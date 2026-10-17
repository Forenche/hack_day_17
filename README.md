# adb-agent: a local Gemma that explains what is wrong with an Android phone

A local open-weight model (Gemma via Ollama) calls five read-only tools and explains
battery drain, heat, CPU use and errors, quoting the lines it relied on and saying what
it could not check. Nothing leaves the machine.

How it stays honest: the model never sees raw `dumpsys` output. `digest.py` decodes codes
(`temperature: 351` -> 35.1 C), separates kernel threads from apps, drops stale cached
temperatures, maps `u0aN` UIDs to package names, and applies published thresholds (Google
Android vitals: more than 2h of wakelock per day is "excessive"). The model only explains.

## Run order (each step tells you if the next is worth doing)

```powershell
pip install requests
python selftest.py                       # 10 s, offline: parsers + agent loop vs a mock model

python agent.py --fake --digests         # what the model would see, synthetic data
python agent.py --digests                # same, LIVE phone (adb devices must list it)

# your saved dump (UTF-8 or the UTF-16 that PowerShell '>' writes both work)
python agent.py --dump batterystats_sample.txt --packages packages_uid.txt --digests

ollama pull gemma4:e4b
python agent.py --ask "Why did my battery drain since the last charge?"
python agent.py --eval                   # 6 questions: tool choice + number-grounding check
python agent.py --dump batterystats_sample.txt --packages packages_uid.txt --ask "What drained my battery?"
```

If `--digests` output looks wrong, fix `digest.py` before touching any prompt: the model
cannot be better than its input.

## What is and is not proven

- `selftest.py` proves the parsing and tool plumbing on SYNTHETIC dumps shaped like real ones.
- It says nothing about how well Gemma reasons. `--eval` reports tool choice and flags numbers
  in the answer that are not in the tool output; read the answers yourself.
- Not covered: kernel wakelocks, alarm/job counts, per-app network use. mAh values are Android's
  relative estimates and are never added up.

## Known limits (measured, not guessed)

- On a real OnePlus 11R dump the model reliably picked the right tool (6/6 questions, ~7 s each on an
  RTX 4060 laptop with gemma4:e4b), and its numbers matched the tool output. Its *reasoning* is shallow:
  the ranking and splits are computed in code (`digest.py`), the model mostly quotes and summarises them.
- About 45% of the attributed estimate on that dump is system components (kernel/root, Android system).
  The tools cannot say why. The agent says so instead of guessing.
- Error-log counts (e.g. `lowmemorykiller`) change between runs because the log rolls over, and are often
  vendor noise. Core counts from `nproc` are online cores only, so per-core load is approximate.
- Tested on one phone model. Other vendors print the same `dumpsys` sections but with differences.

## Privacy

Everything runs locally (Ollama on localhost, adb over USB). Do not commit your own `dumpsys` dumps or
package lists: they list every app you have installed.

## Files
`digest.py` parsers and rules | `agent.py` tool loop + CLI | `samples.py` synthetic data |
`selftest.py`, `mockollama.py` offline tests