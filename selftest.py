"""selftest.py - offline checks: parsers against synthetic dumps, UTF-16 files, and the agent
loop against a mock Ollama server. Run:  python selftest.py   (no phone, no GPU, no model needed)
This proves the plumbing. It does NOT say anything about how well Gemma reasons."""
import os
import tempfile

import agent
import digest
import samples

fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# ---- parsers
pk = digest.parse_packages(samples.PACKAGES)
h = digest.parse_history(samples.batterystats())
check("history: took the 'since last charge' block, not the decoy before it", h["on_battery"] == 10 * 3600 + 5 * 60)
check("history: screen-off parsed", h["screen_off"] == 6 * 3600)
check("history: start time", h["start"] == "2026-01-02 08:00:00")
check("history: capacity", h["capacity"] == 5000)
check("history: 10 distinct UID rows", len(h["uids"]) == 10)
check("history: wakelock names containing colons parse", any(w["name"] == "Playback:Session" for w in h["wake"]))
check("history: wakelock block stops at blank line", len(h["wake"]) == 6)
d = digest.history_digest(samples.batterystats(), pk)
check("digest: system UIDs not listed as apps", "1. Android system" not in d and "1. kernel" not in d)
check("digest: shared UID resolves to a pretty name", "Google Play services" in d)
check("digest: overlay package ignored", "overlay" not in d)
check("digest: 2h wakelock rule fires with the audio caveat", "com.example.music 2h 10m (audio playback is exempt" in d)
check("digest: fgs rule ranks by energy, not hours", "foreground-service energy is >= 1% of the estimate: com.example.music 8% (3h 20m of service time)" in d)
check("digest: wall-clock caveat present", "say nothing about energy" in d)
check("digest: system-vs-apps split computed in code", "Split of attributed estimate: system components" in d)
check("digest: split states which side is larger", "(the larger share is apps)" in d)
check("digest: off-foreground energy ranking", "NOT in the foreground (background + foreground-service + cached; share of attributed estimate): com.example.music 9%" in d)
check("digest: zero-count wakelock prints no (0x)", "(0x)" not in d)
check("digest: measured discharge with screen-on/off split", "Measured discharge (from the battery level, since last charge): 2900 mAh of 5000 mAh capacity (58%)" in d and "during screen-off 800 mAh (28%)" in d)
check("digest: shares labelled as whole-window, not screen-off", "cover the WHOLE window" in d)
check("digest: per-app sum reconciled against measured discharge", "add up to 3000 mAh, 103% of the measured figure: consistent" in d)
check("digest: mismatch with the measurement is flagged", "do not match the measurement" in digest.history_digest(samples.batterystats().replace("Discharge: 2900 mAh", "Discharge: 400 mAh"), pk))
nodis = samples.batterystats().replace("  Discharge: 2900 mAh\n", "")
dn = digest.history_digest(nodis, pk)
check("digest: without a measured line, matching computed totals are shown as given", "computed drain 3000 mAh, actual drain 3000 mAh" in dn and "do not convert" in dn)
bad = digest.history_digest(nodis.replace("Computed drain: 3000, actual drain: 3000", "Computed drain: 600, actual drain: 600"), pk)
check("digest: inconsistent drain totals are withheld", "computed drain" not in bad and "disagree with each other" in bad)
check("digest: kernel wakelocks listed with name-based hint", "hal_bluetooth_lock" in d and "name suggests Bluetooth" in d and "rmnet_ipa%d" in d and "name suggests mobile data" in d)
check("digest: kernel aggregate, screen-on lock and short locks excluded", "\n  PowerManagerService.WakeLocks" not in d and "\n  PowerManagerService.Display" not in d and "alarmtimer" not in d)
cl = digest.capacity_line("5000000\n5000000\n5\n")
check("capacity: equal full/design does not claim wear status", "wear is NOT established" in cl and "charge cycles 5" in cl)
check("capacity: worn battery reported as percent of design", "82% of design" in digest.capacity_line(samples.CAPACITY))
check("capacity: garbage or permission error yields nothing", digest.capacity_line("cat: x: Permission denied\n") == "" and digest.capacity_line("") == "")
check("capacity: battery_now includes it only when given", "design 5000 mAh" in digest.battery_digest(samples.BATTERY, samples.CAPACITY) and "design" not in digest.battery_digest(samples.BATTERY))
check("digest: wakelocks under 15m are omitted", "ProximitySensor" not in d and "NetworkStats" not in d and "AudioMix 2h 10m" in d)
check("digest: unknown low UID labelled as system", "system uid 1082" in d)
check("digest: unlabelled mAh warning present", "RELATIVE" in d)
check("digest: truncated dump handled", "could not find" in digest.history_digest("garbage\n", {}))
t = digest.thermal_digest(samples.THERMAL)
check("thermal: stale cached 62.4 dropped", "62.4" not in t)
check("thermal: non-temperature sensor excluded", "Excluded" in t and "socd" in t)
check("thermal: throttling baseline stated", "none on all 6 sensors" in t)
b = digest.battery_digest(samples.BATTERY)
check("battery: 351 -> 35.1 C, codes decoded", "35.1 C" in b and "Health: good" in b and "Status: charging" in b)
c = digest.cpu_digest(samples.CPU, 8)
check("cpu: kworker classed as system, chat as app", "com.example.chat" in c.split("Top system")[0] and "kworker/0:2-events" in c.split("Top system")[1])
check("cpu: unit stated (percent of one core, can exceed 100)", "of ONE core" in c and "exceed 100%" in c)
check("cpu: load per core", "0.50 per core" in c)
e = digest.errors_digest(samples.CRASH, samples.ERRORS)
check("errors: crash lines grouped into events (native + Java)", "Crash events in the crash buffer: 2; most recent at 01-02 09:58:11 in com.example.chat" in e)
check("errors: ANR counted", "ANRs in the recent error log: 1" in e)
check("errors: no raw crash-buffer line count posing as markers", "Crash/ANR markers" not in e)
check("errors: tag counts", "ChargerInfo x10" in e)

with tempfile.TemporaryDirectory() as td:
    p16 = os.path.join(td, "u16.txt")
    open(p16, "wb").write(b"\xff\xfe" + samples.PACKAGES.encode("utf-16-le"))
    check("utf-16 (PowerShell '>') file reads", "com.example.chat" in digest.read_text_auto(p16))

check("comparison: flagged only in the suggestion part", agent.comparison_claims("Evidence: the most energy\nWhat it suggests: 45% is higher than 55%.\nNot checked: the least") == ["higher", "than"] or agent.comparison_claims("Evidence: the most energy\nWhat it suggests: 45% is higher than 55%.\nNot checked: the least") == ["higher"])
check("comparison: nothing flagged without a suggestion section", agent.comparison_claims("the most energy") == [])
check("grounding: flags an invented number", agent.ungrounded_numbers("battery is 99% and 41%", "Level: 41%") == ["99"])

# ---- agent loop against a mock Ollama
import mockollama

host = mockollama.start()

fake = agent.Backend("fake")
r = agent.run_question(host, "mock", "Why did my battery drain since the last charge?", fake)
check("agent: drain question -> history tool", r["called"] == ["get_battery_history"])
check("agent: invented number caught", agent.ungrounded_numbers(r["answer"], r["tool_text"]) == ["99"])
r = agent.run_question(host, "mock", "Give me a quick overall health check of this phone.", fake)
check("agent: health check uses 4 snapshot tools", len(set(r["called"])) == 4)

with tempfile.TemporaryDirectory() as td:
    dp, pp = os.path.join(td, "bs.txt"), os.path.join(td, "pk.txt")
    open(dp, "wb").write(b"\xff\xfe" + samples.batterystats().encode("utf-16-le"))
    open(pp, "w").write(samples.PACKAGES)
    dump = agent.Backend("dump", dp, pp)
    check("dump mode exposes only the history tool", dump.tools() == ["get_battery_history"])
    r = agent.run_question(host, "mock", "Is my phone overheating?", dump)
    check("dump mode: model cannot reach snapshot tools", r["called"] == [])
    r = agent.run_question(host, "mock", "What drained my battery?", dump)
    check("dump mode: history works from a UTF-16 file", r["called"] == ["get_battery_history"] and "Google Play services" in r["tool_text"])

# retry-on-comparison, with the model call replaced by a script
_orig_chat = agent.chat
_seq = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_battery_history", "arguments": {}}}]},
        {"role": "assistant", "content": "Evidence: x\nWhat it suggests: 45% is higher than 55%.\nNot checked: y"},
        {"role": "assistant", "content": "Evidence: x\nWhat it suggests: system 45%, apps 55%.\nNot checked: y"}]
_seen = []


def _fake_chat(host, model, messages, tools, think, temp=0.0):
    _seen.append(messages[-1]["content"])
    return _seq.pop(0)


agent.chat = _fake_chat
r = agent.run_question("h", "m", "Why did my battery drain?", agent.Backend("fake"))
check("retry: comparison words trigger one rewrite that replaces the answer", r["retried"] and "system 45%, apps 55%" in r["answer"] and len(_seen) == 3)
_seq[:] = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_battery_history", "arguments": {}}}]},
           {"role": "assistant", "content": "Evidence: x\nWhat it suggests: 45% is higher than 55%.\nNot checked: y"}]
_seen.clear()
r = agent.run_question("h", "m", "Why did my battery drain?", agent.Backend("fake"), retry=False)
check("retry: --no-retry leaves the answer alone", not r["retried"] and "higher" in r["answer"] and len(_seen) == 2)
agent.chat = _orig_chat

print("\n%d failed" % len(fails) if fails else "\nall passed")
raise SystemExit(1 if fails else 0)