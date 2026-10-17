"""digest.py - turn raw dumpsys/logcat output into short, decoded, honest digests.

No adb, no network and no LLM in this file, so all of it can be tested offline against
saved dumps. The model only ever sees what these functions print, so every rule about
"what may be claimed" lives here in code instead of in a prompt.
"""
import re
from collections import Counter


# ------------------------------------------------------------------ helpers
def read_text_auto(path):
    """Read a file saved by PowerShell '>' (often UTF-16) or by anything else (UTF-8)."""
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    if b"\x00" in raw[:200]:  # UTF-16 without a BOM
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


_UNIT = {"ms": 0.001, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_dur(s):
    """'1h 50m 47s 477ms' -> seconds. Unknown/empty -> 0."""
    if not s:
        return 0.0
    return sum(int(n) * _UNIT[u] for n, u in re.findall(r"(\d+)(ms|d|h|m|s)\b", s))


def fmt_dur(sec):
    sec = int(round(sec))
    if sec < 60:
        return f"{sec}s"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    parts = ([f"{d}d"] if d else []) + ([f"{h}h"] if h else []) + ([f"{m}m"] if m or not (d or h) else [])
    return " ".join(parts)


def _kv(text):
    d = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z _/.-]*?):\s*(\S.*?)\s*$", line)
        if m:
            d.setdefault(m.group(1).strip().lower(), m.group(2))
    return d


# ------------------------------------------------------------------ battery now
BATT_STATUS = {1: "unknown", 2: "charging", 3: "discharging", 4: "not charging", 5: "full"}
BATT_HEALTH = {1: "unknown", 2: "good", 3: "overheat", 4: "dead", 5: "over voltage",
               6: "unspecified failure", 7: "cold"}


def capacity_line(text):
    """Three numbers read with root: charge_full_design, charge_full (uAh) and cycle_count. '' if unreadable."""
    nums = re.findall(r"^\s*(\d+)\s*$", text or "", re.M)
    if len(nums) != 3:
        return ""
    design, full, cycles = (int(n) for n in nums)
    if design <= 0 or full <= 0:
        return ""
    s = (f"Battery capacity (read with root): design {design / 1000:.0f} mAh; the battery gauge's reported full-charge "
         f"capacity {full / 1000:.0f} mAh; charge cycles {cycles}.")
    if full == design:
        s += " Reported full capacity equals design capacity, which this phone may simply be copying: wear is NOT established."
    else:
        s += f" Reported full capacity is {100 * full / design:.0f}% of design (as reported by the gauge)."
    return s


def battery_digest(text, capacity_text=None):
    d = _kv(text)

    def num(k):
        try:
            return float(d[k])
        except (KeyError, ValueError):
            return None

    out = ["BATTERY NOW (codes decoded by script)"]
    if (v := num("level")) is not None:
        out.append(f"Level: {v:.0f}%")
    if (v := num("status")) is not None:
        out.append(f"Status: {BATT_STATUS.get(int(v), f'unknown code {int(v)}')}")
    src = [(n, k) for n, k in (("AC", "ac powered"), ("USB", "usb powered"),
                               ("wireless", "wireless powered"), ("dock", "dock powered")) if k in d]
    if src:
        on = [n for n, k in src if d[k].lower() == "true"]
        out.append("Plugged in: " + (", ".join(on) if on else "no (running on battery)"))
    else:
        out.append("Plugged in: unknown")
    if (v := num("health")) is not None:
        out.append(f"Health: {BATT_HEALTH.get(int(v), f'unknown code {int(v)}')}")
    if (v := num("temperature")) is not None:
        out.append(f"Battery temperature: {v / 10:.1f} C")
    if (v := num("voltage")) is not None:
        out.append(f"Voltage: {v:.0f} mV")
    if capacity_text is not None and (cl := capacity_line(capacity_text)):
        out.append(cl)
    out.append("This is a snapshot: it cannot show why the battery drained earlier.")
    return "\n".join(out)


# ------------------------------------------------------------------ thermal now
TEMP_RE = re.compile(r"Temperature\{mValue=([-\d.]+), mType=(\d+), mName=([^,]+), mStatus=(\d+)\}")
TEMP_TYPE = {0: "CPU", 1: "GPU", 2: "battery", 3: "skin"}
THROTTLE = {0: "none", 1: "light", 2: "moderate", 3: "severe", 4: "critical", 5: "emergency", 6: "shutdown"}


def thermal_digest(text):
    lines = text.splitlines()
    head = next((i for i, l in enumerate(lines) if "Current temperatures from HAL" in l), None)
    entries, note = [], ""
    if head is not None:
        for l in lines[head + 1:]:
            m = TEMP_RE.search(l)
            if m:
                entries.append(m.groups())
            elif entries:
                break
    else:
        entries = [m.groups() for l in lines if (m := TEMP_RE.search(l))]
        note = "No 'current' section found; values may be stale cached readings."
    out = ["THERMAL NOW (current readings only; cached readings dropped)"]
    if (m := re.search(r"Thermal Status:\s*(\d+)", text)):
        out.append(f"Android thermal status: {THROTTLE.get(int(m.group(1)), 'code ' + m.group(1))}")
    temps = [(float(v), int(t), n.strip(), int(s)) for v, t, n, s in entries]
    other = [n for v, t, n, s in temps if t not in TEMP_TYPE]
    for t, label in TEMP_TYPE.items():
        vals = [(v, n) for v, tt, n, s in temps if tt == t]
        if not vals:
            continue
        lo, hi = min(vals)[0], max(vals)[0]
        out.append(f"{label}: {lo:.1f} C" if lo == hi else
                   f"{label}: {lo:.1f} to {hi:.1f} C across {len(vals)} sensors")
    hot = [f"{n} (severity {THROTTLE.get(s, s)})" for v, t, n, s in temps if s != 0 and t in TEMP_TYPE]
    out.append("Device-reported throttling: " +
               (", ".join(hot) if hot else f"none on all {sum(1 for x in temps if x[1] in TEMP_TYPE)} sensors"))
    if other:
        out.append("Excluded (not clearly temperatures): " + ", ".join(other))
    if note:
        out.append(note)
    out.append("No temperature limits are given, so do not call any reading hot or normal; "
               "the device's own throttling status above is the only baseline.")
    return "\n".join(out)


# ------------------------------------------------------------------ cpu now
CPU_RE = re.compile(r"^\s*[+-]?([\d.]+)%\s+(\d+)/(.+?):\s+([\d.]+)%\s+user\s+\+\s+([\d.]+)%\s+kernel")
APP_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[A-Za-z0-9_]+)+(:[\w.]+)?$")


def cpu_digest(text, cores=None):
    out = ["CPU NOW (short recent window)"]
    if (m := re.search(r"^CPU usage from .*$", text, re.M)):
        out.append(f"Window: {m.group(0).strip()}")
    if (m := re.search(r"Load:\s*([\d.]+)\s*/\s*([\d.]+)\s*/\s*([\d.]+)", text)):
        l1, l5, l15 = (float(x) for x in m.groups())
        s = f"Load average (1/5/15 min): {l1} / {l5} / {l15}"
        if cores:
            s += f" on {cores} cores = {l1 / cores:.2f} per core (rough; Linux load also counts waiting tasks)"
        out.append(s)
    rows = []
    for l in text.splitlines():
        if (m := CPU_RE.match(l)):
            rows.append((float(m.group(1)), m.group(3).strip(), float(m.group(4)), float(m.group(5))))
    apps = [r for r in rows if APP_RE.match(r[1]) and not r[1].startswith(("android.hardware", "vendor.", "android.system"))]
    sysr = [r for r in rows if r not in apps]
    fmt = lambda r: f"  {r[0]:g}% {r[1]} ({r[2]:g}% user + {r[3]:g}% kernel)"
    out.append("Top apps (package-named processes):" if apps else "No package-named app processes in the window.")
    out += [fmt(r) for r in sorted(apps, reverse=True)[:3]]
    out.append("Top system/kernel processes (NOT apps; kworker etc. are kernel threads):")
    out += [fmt(r) for r in sorted(sysr, reverse=True)[:3]]
    return "\n".join(out)


# ------------------------------------------------------------------ errors
LOG_RE = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+\d+\s+\d+\s+([VDIWEF])\s+(.+?)\s*:\s(.*)$")
TS_RE = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d)")
NATIVE_CRASH_RE = re.compile(r"\*\*\* \*\*\* \*\*\*")
CRASH_PROC_RE = re.compile(r">>> (.+?) <<<|Process: ([^\s,]+)")


def _crash_events(lines):
    """Group crash-buffer lines into events. A Java 'FATAL EXCEPTION' line or a native
    '*** *** ***' header starts one; the process name comes from the next '>>> x <<<' or 'Process: x'."""
    events = []
    for l in lines:
        if "FATAL EXCEPTION" in l or NATIVE_CRASH_RE.search(l):
            events.append({"ts": TS_RE.match(l).group(1), "proc": None})
        elif events and events[-1]["proc"] is None and (m := CRASH_PROC_RE.search(l)):
            events[-1]["proc"] = m.group(1) or m.group(2)
    return events


def errors_digest(crash_text, error_text):
    crash_lines = [l for l in crash_text.splitlines() if LOG_RE.match(l)]
    errs = [l for l in error_text.splitlines() if LOG_RE.match(l)]
    events = _crash_events(crash_lines)
    anrs = [l for l in errs if "ANR in " in l]
    newest = max((TS_RE.match(l).group(1) for l in errs), default=None)
    head = f"ERRORS (crash buffer: {len(crash_lines)} lines; recent error-level log: {len(errs)} lines"
    out = [head + (f"; newest entry {newest}" if newest else "") + ")"]
    if events:
        last = events[-1]
        out.append(f"Crash events in the crash buffer: {len(events)}; most recent at {last['ts']}" +
                   (f" in {last['proc']}" if last["proc"] else "") +
                   " (timestamps carry no year; compare with the newest entry above to judge how old it is)")
    else:
        out.append("Crash events in the crash buffer: none")
    out.append(f"ANRs in the recent error log: {len(anrs)}")
    out += ["  " + l[:170] for l in anrs[:3]]
    tags = Counter(LOG_RE.match(l).group(2) for l in errs)
    if tags:
        out.append("Most frequent error tags: " + "; ".join(f"{t} x{c}" for t, c in tags.most_common(5)))
        seen = set()
        for l in reversed(errs):
            t = LOG_RE.match(l).group(2)
            if t in dict(tags.most_common(3)) and t not in seen:
                seen.add(t)
                out.append("  e.g. " + l[:170])
    out.append("Error-level lines are often harmless vendor noise; frequency alone is not proof of a fault.")
    return "\n".join(out)


# ------------------------------------------------------------------ battery history (batterystats)
SYSTEM_UIDS = {0: "kernel/root", 1000: "Android system", 1001: "phone/radio", 1002: "Bluetooth service",
               1027: "NFC service", 1073: "network stack", 2000: "adb shell"}
PRETTY = {"com.google.android.gms": "Google Play services", "com.whatsapp": "WhatsApp",
          "com.instagram.android": "Instagram", "com.google.android.youtube": "YouTube",
          "com.apple.android.music": "Apple Music", "com.spotify.music": "Spotify",
          "org.telegram.messenger.web": "Telegram", "com.discord": "Discord",
          "com.android.chrome": "Chrome", "com.android.systemui": "System UI",
          "com.supercell.clashroyale": "Clash Royale", "com.twitter.android": "X (Twitter)", "com.ubercab": "Uber"}
JUNK = ("overlay", ".rro", "auto_generated", "resources", "frameworkres", "cutout")
WAKE_SHOW_SECS = 15 * 60
GOOGLE_WAKELOCK_LIMIT = 2 * 3600  # Android vitals: >2h cumulative non-exempt wake lock time per 24h


def uid_to_int(tok):
    if tok.isdigit():
        return int(tok)
    m = re.fullmatch(r"u(\d+)a(\d+)", tok)
    return int(m.group(1)) * 100000 + 10000 + int(m.group(2)) if m else None


def parse_packages(text):
    by = {}
    for m in re.finditer(r"package:(\S+)\s+uid:(\d+)", text):
        by.setdefault(int(m.group(2)), []).append(m.group(1))
    return by


def app_name(uid, pkgs):
    if uid in SYSTEM_UIDS:
        return SYSTEM_UIDS[uid]
    if uid is None:
        return "unknown uid"
    if uid < 10000:
        return f"system uid {uid}"
    cands = [p for p in pkgs.get(uid % 100000, []) if not any(j in p for j in JUNK)]
    for p in cands:
        if p in PRETTY:
            return PRETTY[p]
    return sorted(cands, key=lambda p: (len(p), p))[0] if cands else f"uid {uid}"


_WAKE = re.compile(r"^\s+Wake lock (\S+) (.*): ((?:\d+d )?(?:\d+h )?(?:\d+m )?(?:\d+s )?(?:\d+ms)?) \((\d+) times\)")
_COMP = re.compile(r"^\s+([a-z_]+): ([\d.]+)(?: apps: [\d.]+)?(?: duration: (.+?))?\s*$")
_UIDLINE = re.compile(r"^ {2}UID (\S+?): ([\d.]+)(.*)$")


def parse_history(dump):
    lines = dump.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "Statistics since last charge:"), None)
    if start is None:
        return None
    h = {"on_battery": None, "screen_off": None, "start": None, "capacity": None,
         "components": {}, "uids": [], "wake": []}
    for l in lines[start + 1:start + 80]:
        if (m := re.match(r"\s*Time on battery:\s*(.+?) \(", l)):
            h["on_battery"] = parse_dur(m.group(1))
        elif (m := re.match(r"\s*Time on battery screen off:\s*(.+?) \(", l)):
            h["screen_off"] = parse_dur(m.group(1))
        elif (m := re.match(r"\s*(Discharge|Screen on discharge|Screen off discharge|Screen doze discharge|"
                            r"Device light doze discharge|Device deep doze discharge):\s*([\d.]+) mAh", l)):
            h.setdefault("disch", {})[m.group(1)] = float(m.group(2))
        elif (m := re.match(r"\s*Start clock time:\s*(\d{4})-(\d\d)-(\d\d)-(\d\d)-(\d\d)-(\d\d)", l)):
            h["start"] = "%s-%s-%s %s:%s:%s" % m.groups()
    p = next((i for i in range(start, len(lines)) if "Estimated power use (mAh):" in lines[i]), None)
    if p is not None:
        cap_line = lines[p + 1] if p + 1 < len(lines) else ""
        if (m := re.search(r"Capacity:\s*(\d+)", cap_line)):
            h["capacity"] = int(m.group(1))
        if (m := re.search(r"Computed drain:\s*([\d.]+),\s*actual drain:\s*([\d.\-]+)", cap_line)):
            h["computed"], h["actual"] = m.group(1), m.group(2)
        in_global, seen = True, set()
        for l in lines[p + 2:p + 6000]:
            if "All kernel wake locks" in l:
                break
            if in_global:
                if l.strip().startswith("(") or _UIDLINE.match(l):
                    in_global = False
                elif (m := _COMP.match(l)):
                    h["components"].setdefault(m.group(1), float(m.group(2)))
            if (m := _UIDLINE.match(l)) and m.group(1) not in seen:
                seen.add(m.group(1))
                u = {"tok": m.group(1), "uid": uid_to_int(m.group(1)), "total": float(m.group(2))}
                for k, val, dur in re.findall(r"\b(fg|bg|fgs|cached): ([\d.]+(?:e[-+]?\d+)?)(?: \(([^)]*)\))?", m.group(3)):
                    u[k] = (float(val), parse_dur(dur) if dur else None)
                h["uids"].append(u)
    k = next((i for i in range(start, len(lines)) if lines[i].strip() == "All kernel wake locks:"), None)
    h["kernel"] = []
    if k is not None:
        for l in lines[k + 1:k + 400]:
            if (m := re.match(r"\s+Kernel Wake lock (.+?): (\d.*?)(?: \((\d+) times\))? realtime", l)):
                h["kernel"].append({"name": m.group(1).strip(), "secs": parse_dur(m.group(2)),
                                    "times": int(m.group(3) or 0)})
            elif l.strip() == "":
                break
    w = next((i for i in range(start, len(lines)) if lines[i].strip() == "All partial wake locks:"), None)
    if w is not None:
        for l in lines[w + 1:w + 2000]:
            if (m := _WAKE.match(l)):
                h["wake"].append({"uid": uid_to_int(m.group(1)), "name": m.group(2).strip(),
                                  "secs": parse_dur(m.group(3)), "times": int(m.group(4))})
            elif l.startswith("  Wake lock") or l.strip() == "":
                if l.strip() == "":
                    break
            else:
                break
    return h


KERNEL_HINTS = [("rmnet", "mobile data"), ("ipa", "mobile data"), ("qcom_rx", "mobile data"),
                ("bluetooth", "Bluetooth"), ("uart", "Bluetooth/serial"), ("display", "display"),
                ("alarmtimer", "alarms"), ("gesture", "gesture/proximity sensor")]
KERNEL_SHOW_SECS = 10 * 60


def kernel_hint(name):
    n = name.lower()
    return next((lab for key, lab in KERNEL_HINTS if key in n), None)


def history_digest(dump, pkgs=None):
    pkgs = pkgs or {}
    h = parse_history(dump)
    if h is None:
        return ("BATTERY HISTORY: could not find 'Statistics since last charge:' in the batterystats output "
                "(different Android version, or the dump is truncated).")
    out = ["BATTERY HISTORY since last charge" + (f" (started {h['start']})" if h["start"] else "")]
    if h["on_battery"]:
        s = f"On battery: {fmt_dur(h['on_battery'])}"
        if h["screen_off"] is not None:
            s += (f"; screen on about {fmt_dur(h['on_battery'] - h['screen_off'])}, "
                  f"screen off {fmt_dur(h['screen_off'])}")
        out.append(s)
    if h.get("disch", {}).get("Discharge"):
        out.append("Per-app and per-component mAh values below are RELATIVE modelled estimates (Android's power model); "
                   "only the lines marked 'Measured' come from the battery level. Use shares, do not convert estimates to mAh. "
                   "These shares cover the WHOLE window, screen on and off together; they cannot be split by screen state, "
                   "so do not present them as the cause of the screen-off figure.")
    else:
        out.append("mAh values are RELATIVE estimates from Android's power model" +
                   (f" (battery capacity {h['capacity']} mAh)" if h["capacity"] else "") +
                   ": compare them, never add them up or treat them as exact drain.")
    uids = h["uids"]
    total = sum(u["total"] for u in uids) or 1.0
    dis = h.get("disch", {})
    if dis.get("Discharge"):
        tot = dis["Discharge"]
        s = f"Measured discharge (from the battery level, since last charge): {tot:.0f} mAh"
        if h["capacity"]:
            s += f" of {h['capacity']} mAh capacity ({100 * tot / h['capacity']:.0f}%)"
        if "Screen on discharge" in dis and "Screen off discharge" in dis:
            s += (f"; during screen-on {dis['Screen on discharge']:.0f} mAh ({100 * dis['Screen on discharge'] / tot:.0f}%)"
                  f", during screen-off {dis['Screen off discharge']:.0f} mAh ({100 * dis['Screen off discharge'] / tot:.0f}%)")
        if "Device light doze discharge" in dis and "Device deep doze discharge" in dis:
            s += (f"; device idle (doze) discharge: light {dis['Device light doze discharge']:.0f} mAh"
                  f", deep {dis['Device deep doze discharge']:.0f} mAh")
        out.append(s + ".")
        ratio = total / tot
        out.append(f"The per-app estimates below add up to {total:.0f} mAh, {100 * ratio:.0f}% of the measured figure: " +
                   ("consistent with it, so the shares below are consistent with the measured drain." if 0.7 <= ratio <= 1.3 else
                    "they do not match the measurement, so treat the shares with caution."))
    elif h.get("computed"):
        try:
            comp = float(h["computed"])
        except ValueError:
            comp = 0.0
        if comp > 0 and 0.5 <= total / comp <= 2:
            out.append(f"Android's own totals: computed drain {h['computed']} mAh, actual drain {h['actual']} mAh"
                       + (f", battery capacity {h['capacity']} mAh" if h["capacity"] else "")
                       + " (report them as given; do not convert them to percent)")
        elif comp > 0:
            out.append("Absolute drain figures in this dump disagree with each other, so none are reported; "
                       "only relative shares below can be used.")
    comp = {k: v for k, v in h["components"].items() if v > 0}
    if comp:
        ct = sum(comp.values())
        big = [(k, 100 * v / ct) for k, v in sorted(comp.items(), key=lambda kv: -kv[1]) if 100 * v / ct >= 1][:5]
        out.append("Largest component shares (relative): " + ", ".join(f"{k} {p:.0f}%" for k, p in big))
    if not uids:
        out.append("Per-app estimates: not found in this dump.")
    else:
        sysu = [u for u in uids if u["uid"] is not None and u["uid"] < 10000]
        apps = [u for u in uids if u not in sysu]
        if sysu:
            out.append("System components (NOT apps), share of attributed estimate: " +
                       ", ".join(f"{app_name(u['uid'], pkgs)} {100 * u['total'] / total:.0f}%"
                                 for u in sorted(sysu, key=lambda u: -u["total"])[:4]))
            sp, ap = (100 * sum(u["total"] for u in g) / total for g in (sysu, apps))
            out.append(f"Split of attributed estimate: system components {sp:.0f}% | apps {ap:.0f}%"
                       f" (the larger share is {'system components' if sp > ap else 'apps'})")
        out.append("Top apps by estimated use (share | foreground | background | foreground-service time; "
                   "times are wall-clock, so judge by share):")
        for i, u in enumerate(sorted(apps, key=lambda u: -u["total"])[:5], 1):
            t = lambda k: (fmt_dur(u[k][1]) if k in u and u[k][1] else "-")
            out.append(f"  {i}. {app_name(u['uid'], pkgs)}: {100 * u['total'] / total:.0f}% | fg {t('fg')} | bg {t('bg')} | fgs {t('fgs')}")
    if uids:
        def off(u):
            return sum(u[k][0] for k in ("bg", "fgs", "cached") if k in u)
        offs = sorted([u for u in apps if 100 * off(u) / total >= 1], key=lambda u: -off(u))[:3]
        out.append("Apps with the most energy used while NOT in the foreground (background + foreground-service + cached; "
                   "share of attributed estimate): " +
                   ("; ".join(f"{app_name(u['uid'], pkgs)} {100 * off(u) / total:.0f}%" for u in offs) if offs else "none >= 1%") +
                   ". Not-in-foreground is not the same as not-used: the app may have been opened earlier.")
    wl = sorted([x for x in h["wake"] if x["secs"] >= WAKE_SHOW_SECS], key=lambda x: -x["secs"])[:5]
    if wl:
        out.append("Longest partial wakelocks of 15m or more (can overlap, do not add up):")
        out += [f"  {app_name(x['uid'], pkgs)}: {x['name'][:60]} {fmt_dur(x['secs'])}" +
                (f" ({x['times']}x)" if x["times"] else "") for x in wl]
    elif h["wake"]:
        out.append("Partial wakelocks: none of 15m or longer (shorter ones omitted as too small to matter)")
    kl = sorted([x for x in h["kernel"] if x["secs"] >= KERNEL_SHOW_SECS and x["name"] not in ("PowerManagerService.WakeLocks", "PowerManagerService.Display")],
                key=lambda x: -x["secs"])[:6]
    if kl:
        out.append("Longest kernel wakelocks of 10m or more, excluding the PowerManagerService.WakeLocks total and the "
                   "screen-on lock PowerManagerService.Display (can overlap, do not add up; the label after the arrow is only a guess from the name):")
        for x in kl:
            hint = kernel_hint(x["name"])
            out.append(f"  {x['name'][:50]} {fmt_dur(x['secs'])}" + (f" ({x['times']}x)" if x["times"] else "") +
                       (f" -> name suggests {hint}" if hint else ""))
    elif h["kernel"]:
        out.append("Kernel wakelocks: none of 10m or longer")
    out.append("Rule checks (thresholds from Google's Android vitals / Battery Historian docs):")
    per = Counter()
    for x in h["wake"]:
        per[x["uid"]] += x["secs"]
    over = [(u, s) for u, s in per.items() if s >= GOOGLE_WAKELOCK_LIMIT]
    out.append("  - Wakelocks >= 2h per app: " + ("; ".join(f"{app_name(u, pkgs)} {fmt_dur(s)} (audio playback is exempt in Google's metric)"
                                                      for u, s in over) if over else "none"))
    fgs = sorted([u for u in uids if u.get("fgs") and (u["uid"] or 0) >= 10000 and 100 * u["fgs"][0] / total >= 1],
                 key=lambda u: -u["fgs"][0])[:3]
    out.append("  - Apps whose foreground-service energy is >= 1% of the estimate: " +
               ("; ".join(f"{app_name(u['uid'], pkgs)} {100 * u['fgs'][0] / total:.0f}%" +
                          (f" ({fmt_dur(u['fgs'][1])} of service time)" if u["fgs"][1] else "") for u in fgs)
                if fgs else "none") + ". Service hours are wall-clock and say nothing about energy.")
    out.append("NOT included here: alarms/job counts, per-app network use. "
               "A cause may only be called 'possible' and must point at a line above.")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python digest.py batterystats.txt [packages_uid.txt]")
    pk = parse_packages(read_text_auto(sys.argv[2])) if len(sys.argv) > 2 else {}
    print(history_digest(read_text_auto(sys.argv[1]), pk))