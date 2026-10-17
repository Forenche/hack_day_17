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
check("digest: zero-count wakelock prints no (0x)", "(0x)" not in d)
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

print("\n%d failed" % len(fails) if fails else "\nall passed")
raise SystemExit(1 if fails else 0)