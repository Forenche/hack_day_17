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
    # root only, opt-in with --root: three fixed read-only sysfs files
    "capacity": ["shell", "su -c 'cat /sys/class/power_supply/battery/charge_full_design "
                          "/sys/class/power_supply/battery/charge_full /sys/class/power_supply/battery/cycle_count'"],
    "crash": ["logcat", "-b", "crash", "-d", "-t", "200"],
    "errors": ["logcat", "-d", "-t", "1500", "*:E"],
    "batterystats": ["shell", "dumpsys", "batterystats"],
    "packages": ["shell", "pm", "list", "packages", "-U"],
}

TOOL_DESC = {
    "get_battery_history": "Battery drain since the last full charge: screen-on time, which apps and system components used the most estimated power, wakelocks, foreground services. Use for ANY question about why or what drained the battery.",
    "get_battery_now": "Current battery level, charging state, health and temperature (decoded), plus design capacity and charge cycles when available.",
    "get_thermal_now": "Current temperatures and the device's own throttling status.",
    "get_top_cpu_now": "Which processes used the most CPU in a short recent window, split into apps and system/kernel.",
    "get_recent_errors": "Crash/ANR markers and the most frequent recent error log tags.",
}

SYSTEM = """You are a diagnostician for Android phones. You can learn about the phone only by calling tools. Their output is your only evidence.

PROCESS
1. Choose the tools the question needs. For a general health check call every tool. For anything about battery drain over time, call get_battery_history first; the other tools only show the present moment.
2. Read the whole tool output. Work out exactly what the question asks: which things, over what period, and whether it separates two categories. Answer from the line that was built for that purpose, and prefer lines the tool already computed (rankings, splits, rule checks) over raw lists.
3. Weigh size. A quantity that is tiny next to the period or total it belongs to is not a cause.

RULES
- Every claim must rest on a line you quote. If no line supports it, leave it out.
- Only cite numbers that appear in tool output. Never add, convert or scale them. mAh figures and percentage shares are relative estimates.
- Kernel threads, system components and system services are not apps; say so when they matter.
- Do not call anything normal, high, hot or a problem unless the output gives a baseline or the device's own status for it. Otherwise describe the ranking.
- Label anything inferred as "possible" and name the quoted line it comes from.
- A claim about a category (for example Bluetooth or mobile data) may only be made from a line you have quoted, and a label the tool marks as a guess stays a guess: say "the name suggests".
- Do not compare numbers yourself. Where the tool states a ranking or which side is larger, copy it; otherwise list the figures without words like higher, lower or most.
- Say plainly what the data cannot show.

FORMAT
Evidence: at most 4 bullets, each one short quoted line; never paste whole sections.
What it suggests: start with the single most important finding in one sentence, then at most two supporting ones; only conclusions the quoted lines support, else "nothing obvious".
Not checked: what these tools cannot tell you."""


class Backend:
    """mode: live (adb), fake (synthetic), dump (saved batterystats file, history tool only)."""

    def __init__(self, mode, dump=None, packages=None, root=False):
        self.mode, self.dump, self.packages, self.root = mode, dump, packages, root
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
                    "batterystats": samples.batterystats(), "packages": samples.PACKAGES,
                    "capacity": samples.CAPACITY}[key]
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
            cap = None
            if self.root or self.mode == "fake":
                try:
                    cap = self._raw("capacity") if self.mode == "fake" else self._adb("capacity", 15)
                except (RuntimeError, subprocess.TimeoutExpired):
                    cap = None
            return digest.battery_digest(self._raw("battery"), cap)
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


def chat(host, model, messages, tools, think, temp=0.0):
    body = {"model": model, "stream": False, "messages": messages, "tools": tools,
            "options": {"temperature": temp, "num_ctx": 16384 if think else 8192}, "think": think}
    r = requests.post(f"{host}/api/chat", json=body, timeout=300)
    if r.status_code == 400:  # model without a 'think' switch
        body.pop("think")
        if think:
            print("warning: model rejected the think option; running WITHOUT thinking", file=sys.stderr)
        r = requests.post(f"{host}/api/chat", json=body, timeout=300)
    r.raise_for_status()
    return r.json()["message"]


def run_question(host, model, question, backend, think=False, temp=0.0):
    names = backend.tools()
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    called, bad, tool_text, answer = [], [], [], ""
    t0 = time.time()
    for _ in range(6):
        m = chat(host, model, msgs, tool_specs(names), think, temp)
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


COMPARE_RE = re.compile(r"\b(higher|lower|larger|smaller|greater|bigger|more than|less than|most|least)\b", re.I)


def comparison_claims(answer):
    """Comparison words in the 'What it suggests' part only. Small models are unreliable at comparing
    numbers, and the grounding check cannot see that, so these are flagged for a human to verify."""
    if "What it suggests" not in answer:
        return []
    part = answer.split("What it suggests", 1)[1].split("Not checked", 1)[0]
    return sorted({m.lower() for m in COMPARE_RE.findall(part)})


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
    cmp = comparison_claims(res["answer"])
    if cmp:
        print(f"\n?? comparison words in the answer {cmp}: small models often get these wrong; check them against the digest.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemma4:e4b")
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--fake", action="store_true", help="synthetic data, no phone")
    ap.add_argument("--dump", help="saved `adb shell dumpsys batterystats` output (history tool only)")
    ap.add_argument("--packages", help="saved `adb shell pm list packages -U` output")
    ap.add_argument("--root", action="store_true", help="also read battery design/full capacity and cycles via su (rooted phone, opt-in)")
    ap.add_argument("--ask")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--digests", action="store_true", help="print what each tool returns; no LLM")
    ap.add_argument("--think", action="store_true", help="enable the model's reasoning mode (slower)")
    ap.add_argument("--temp", type=float, default=0.0, help="Google suggests 1.0 for Gemma 4; default 0 for repeatability")
    a = ap.parse_args()
    if not a.host.startswith("http"):
        a.host = "http://" + a.host
    backend = Backend("dump" if a.dump else "fake" if a.fake else "live", a.dump, a.packages, a.root)

    if a.digests:
        for n in backend.tools():
            print(f"===== {n} =====\n{backend.call(n)}\n")
        return
    if a.eval:
        ok, lats = 0, []
        qs = QUESTIONS if backend.mode != "dump" else QUESTIONS[:2]
        for q, must, k in qs:
            r = run_question(a.host, a.model, q, backend, a.think, a.temp)
            good = must <= set(r["called"]) and len(set(r["called"])) >= k and not r["bad"] and bool(r["answer"])
            ok += good
            lats.append(r["secs"])
            print(f"\n{'PASS' if good else 'FAIL'}  {q}")
            show(r)
        print(f"\n{a.model}: {ok}/{len(qs)} tool-choice passes, median {statistics.median(lats):.1f}s. "
              "PASS says nothing about answer quality: read the answers and the '!!' lines.")
        return
    if a.ask:
        show(run_question(a.host, a.model, a.ask, backend, a.think, a.temp))
        return
    print("Ask about the phone (empty line to quit).")
    while (q := input("> ").strip()):
        show(run_question(a.host, a.model, q, backend, a.think, a.temp))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"error: {e}")