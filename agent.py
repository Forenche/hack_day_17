"""agent.py - a local Gemma (via Ollama) that explains what is going on with an Android phone.

The model can only pick a tool NAME. Every command is a fixed read-only argument list, and the
model only sees short digests produced by digest.py (decoded codes, no raw dumps).

Examples (PowerShell):
    python agent.py --digests                          # no LLM: look at what the model would see (live phone)
    python agent.py --fake --digests                   # same, synthetic data, no phone
    python agent.py --ask "Why did my battery drain since the last charge?"
    python agent.py --dump batterystats.txt --packages packages_uid.txt --ask "What drained my battery?"
    python agent.py --eval                             # tool-choice + number-grounding check on 6 questions
    python agent.py                                    # interactive
"""
import argparse
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

import requests

import digest
import samples

ADB_CMDS = {  # fixed, read-only. Nothing the model writes is ever put into a command.
    "battery": ["shell", "dumpsys", "battery"],
    "thermal": ["shell", "dumpsys", "thermalservice"],
    "cpu": ["shell", "dumpsys", "cpuinfo"],
    "nproc": ["shell", "nproc"],
    "crash": ["logcat", "-b", "crash", "-d", "-t", "200"],
    "errors": ["logcat", "-d", "-t", "1500", "*:E"],
    "batterystats": ["shell", "dumpsys", "batterystats"],
    "packages": ["shell", "pm", "list", "packages", "-U"],
}

TOOL_DESC = {
    "get_battery_history": "Battery drain since the last full charge: screen-on time, which apps and system components used the most estimated power, wakelocks, foreground services. Use for ANY question about why or what drained the battery.",
    "get_battery_now": "Current battery level, charging state, health and temperature (decoded).",
    "get_thermal_now": "Current temperatures and the device's own throttling status.",
    "get_top_cpu_now": "Which processes used the most CPU in a short recent window, split into apps and system/kernel.",
    "get_recent_errors": "Crash/ANR markers and the most frequent recent error log tags.",
}

SYSTEM = """You diagnose Android phones. Before answering, call the tools you need. For a general health check, call every available tool. For any question about battery drain (why or what drained it), call get_battery_history first.

RULES
- Only cite numbers that appear in tool output. Quote the line you rely on.
- mAh figures are relative estimates: compare them, never add or convert them. Percent shares are of the attributed estimate.
- Kernel threads, system components and system services are not apps; say so.
- Do not call anything "normal", "high" or "overheating" unless the tool output gives a baseline or the device's own status for it. Otherwise describe the ranking.
- If the data cannot answer the question, say so and say what is missing.
- Only suggest a possible cause if a quoted line supports it, and label it "possible".
- For drain questions, first say how the estimate splits between system components and apps, then name the top apps.

FORMAT
Evidence: at most 4 bullets, each one short quoted line; never paste whole sections.
What it suggests: only conclusions the quoted lines support, else "nothing obvious".
Not checked: what these tools cannot tell you."""


class Backend:
    """mode: live (adb), fake (synthetic), dump (saved batterystats file, history tool only)."""

    def __init__(self, mode, dump=None, packages=None):
        self.mode, self.dump, self.packages = mode, dump, packages
        self._cache = {}

    def tools(self):
        return ["get_battery_history"] if self.mode == "dump" else list(TOOL_DESC)

    def _adb(self, key, timeout=60):
        exe = shutil.which("adb")
        if not exe:
            raise RuntimeError("adb not found on PATH (install Android platform-tools) - or use --fake / --dump")
        r = subprocess.run([exe] + ADB_CMDS[key], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout if r.stdout.strip() else (r.stderr or "")

    def _raw(self, key):
        if self.mode == "fake":
            return {"battery": samples.BATTERY, "thermal": samples.THERMAL, "cpu": samples.CPU,
                    "nproc": samples.NPROC, "crash": samples.CRASH, "errors": samples.ERRORS,
                    "batterystats": samples.batterystats(), "packages": samples.PACKAGES}[key]
        return self._adb(key, 180 if key == "batterystats" else 60)

    def call(self, name):
        if name not in self.tools():
            return f"error: unknown tool {name}"
        if name == "get_battery_history":
            if "hist" not in self._cache or time.time() - self._cache["hist"][0] > 120:
                if self.mode == "dump":
                    text = digest.read_text_auto(self.dump)
                    pk = digest.parse_packages(digest.read_text_auto(self.packages)) if self.packages else {}
                else:
                    text, pk = self._raw("batterystats"), digest.parse_packages(self._raw("packages"))
                self._cache["hist"] = (time.time(), digest.history_digest(text, pk))
            return self._cache["hist"][1]
        if name == "get_battery_now":
            return digest.battery_digest(self._raw("battery"))
        if name == "get_thermal_now":
            return digest.thermal_digest(self._raw("thermal"))
        if name == "get_top_cpu_now":
            try:
                cores = int(self._raw("nproc").strip().split()[0])
            except (ValueError, IndexError):
                cores = None
            return digest.cpu_digest(self._raw("cpu"), cores)
        return digest.errors_digest(self._raw("crash"), self._raw("errors"))


def tool_specs(names):
    return [{"type": "function", "function": {"name": n, "description": TOOL_DESC[n],
                                              "parameters": {"type": "object", "properties": {}}}} for n in names]


def chat(host, model, messages, tools, think):
    body = {"model": model, "stream": False, "messages": messages, "tools": tools,
            "options": {"temperature": 0, "num_ctx": 8192}, "think": think}
    r = requests.post(f"{host}/api/chat", json=body, timeout=300)
    if r.status_code == 400:  # model without a 'think' switch
        body.pop("think")
        r = requests.post(f"{host}/api/chat", json=body, timeout=300)
    r.raise_for_status()
    return r.json()["message"]


def run_question(host, model, question, backend, think=False):
    names = backend.tools()
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    called, bad, tool_text, answer = [], [], [], ""
    t0 = time.time()
    for _ in range(6):
        m = chat(host, model, msgs, tool_specs(names), think)
        calls = m.get("tool_calls") or []
        if not calls:
            answer = (m.get("content") or "").strip()
            break
        msgs.append(m)
        for c in calls:
            n = c["function"]["name"]
            out = backend.call(n)
            (called if n in names else bad).append(n)
            tool_text.append(out)
            msgs.append({"role": "tool", "tool_name": n, "content": out})
    return {"called": called, "bad": bad, "answer": answer, "secs": time.time() - t0,
            "tool_text": "\n".join(tool_text)}


def ungrounded_numbers(answer, tool_text):
    """Numbers in the answer that appear nowhere in the tool output. A hint, not proof:
    derived numbers (sums, ratios) show up here too, which is exactly what we want to see."""
    have = set(re.findall(r"\d+(?:\.\d+)?", tool_text))
    return sorted({n for n in re.findall(r"\d+(?:\.\d+)?", answer) if len(n.replace(".", "")) >= 2 and n not in have})


QUESTIONS = [
    ("Why did my battery drain since the last charge?", {"get_battery_history"}, 1),
    ("What drained my battery that I didn't actively use?", {"get_battery_history"}, 1),
    ("Is my phone overheating?", {"get_thermal_now"}, 1),
    ("Which app is using the most CPU right now?", {"get_top_cpu_now"}, 1),
    ("Did anything crash or fail recently?", {"get_recent_errors"}, 1),
    ("Give me a quick overall health check of this phone.", set(), 3),
]


def show(res):
    print(f"[tools: {res['called'] or 'NONE'}{'  INVALID: ' + str(res['bad']) if res['bad'] else ''}  {res['secs']:.1f}s]")
    print(res["answer"] or "(empty answer)")
    bad = ungrounded_numbers(res["answer"], res["tool_text"])
    if bad:
        print(f"\n!! numbers in the answer not found in tool output: {bad}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemma4:e4b")
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--fake", action="store_true", help="synthetic data, no phone")
    ap.add_argument("--dump", help="saved `adb shell dumpsys batterystats` output (history tool only)")
    ap.add_argument("--packages", help="saved `adb shell pm list packages -U` output")
    ap.add_argument("--ask")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--digests", action="store_true", help="print what each tool returns; no LLM")
    ap.add_argument("--think", action="store_true")
    a = ap.parse_args()
    if not a.host.startswith("http"):
        a.host = "http://" + a.host
    backend = Backend("dump" if a.dump else "fake" if a.fake else "live", a.dump, a.packages)

    if a.digests:
        for n in backend.tools():
            print(f"===== {n} =====\n{backend.call(n)}\n")
        return
    if a.eval:
        ok, lats = 0, []
        qs = QUESTIONS if backend.mode != "dump" else QUESTIONS[:2]
        for q, must, k in qs:
            r = run_question(a.host, a.model, q, backend, a.think)
            good = must <= set(r["called"]) and len(set(r["called"])) >= k and not r["bad"] and bool(r["answer"])
            ok += good
            lats.append(r["secs"])
            print(f"\n{'PASS' if good else 'FAIL'}  {q}")
            show(r)
        print(f"\n{a.model}: {ok}/{len(qs)} tool-choice passes, median {statistics.median(lats):.1f}s. "
              "PASS says nothing about answer quality: read the answers and the '!!' lines.")
        return
    if a.ask:
        show(run_question(a.host, a.model, a.ask, backend, a.think))
        return
    print("Ask about the phone (empty line to quit).")
    while (q := input("> ").strip()):
        show(run_question(a.host, a.model, q, backend, a.think))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"error: {e}")