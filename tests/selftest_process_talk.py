# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Self-test for notes_lib/process_talk.py (prose about how a note was made) and the sweep's ``scrub`` edit.

    uv run tests/selftest_process_talk.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from notes_lib import process_talk as pt  # noqa: E402

spec = importlib.util.spec_from_file_location("sweep_check", ROOT / "scripts" / "sweep_check.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

bad: list[tuple] = []

NOTE = """---
title: x
---
# 12 · Pods

## Pod states

Representative output (from the class demo; not run on this machine, `kubectl` is outside the execution policy):

```text
NAME   READY
web    1/1
```

The numbers are this laptop's, not the class server's.

> **Note:** all terminal output in this note is representative. Nothing in this note was run against a live daemon.

> **Reconstructed:** about 1h26 to 1h33 the audio is lost while the instructor walked around the room.

> **Repo note:** the repo has since moved on.

- On every push, the pipeline runs the tests.
- The digest also saw it on coreutils 8.32.

The instructor's laptop had many images. A `{run}` snippet was also checked on Git Bash.

```bash {run}
echo "representative output on this machine"
```

| command | effect |
| --- | --- |
| `sh x.sh` | whatever `sh` is on this machine |
"""

found = pt.scan(NOTE)
lines = {it["line"]: it for it in found}
texts = " | ".join(it["text"][:40] for it in found)
want_hits = ["Representative output (from", "The numbers are this laptop", "> **Note:** all terminal",
             "- The digest also saw", "The instructor's laptop had", "| `sh x.sh` |"]
for w in want_hits:
    if not any(it["text"].startswith(w) for it in found):
        bad.append(("should be flagged", w, texts))
for w in ("Reconstructed", "Repo note", "the pipeline runs the tests", "echo"):
    if any(w in it["text"] for it in found):
        bad.append(("should not be flagged", w))
if len(found) != len(want_hits):
    bad.append(("flag count", len(found), texts))

# scrub: rewrite in place, delete with its blank line, and a re-run is a no-op
item = next(it for it in found if it["text"].startswith("Representative output"))
out, st = sc.apply_scrub(NOTE, {"replaces": item["replaces"], "text": "Output (from the class demo):"})
if st != "scrubbed" or "Output (from the class demo):\n\n```text" not in out or "Representative" in out.split("```text")[0]:
    bad.append(("scrub rewrite", st))
again, st2 = sc.apply_scrub(out, {"replaces": item["replaces"], "text": "Output (from the class demo):"})
if st2 != "already-clean" or again != out:
    bad.append(("scrub idempotent", st2))
note_item = next(it for it in found if it["text"].startswith("> **Note:**"))
out2, st3 = sc.apply_scrub(NOTE, {"replaces": note_item["replaces"], "text": ""})
if st3 != "scrubbed" or "\n\n\n" in out2 or "Nothing in this note" in out2:
    bad.append(("scrub delete keeps one blank line", st3))
out3, st4 = sc.apply_scrub(NOTE, {"replaces": "a passage that is not there", "text": "x"})
if st4 != "replaces-not-found" or out3 != NOTE:
    bad.append(("scrub not found", st4))
# a wrapped passage still matches when the line breaks moved (whitespace-tolerant)
wrapped = NOTE.replace("The numbers are this laptop's, not the class server's.",
                       "The numbers are this laptop's,\nnot the class server's.")
item2 = next(it for it in found if it["text"].startswith("The numbers"))
out4, st5 = sc.apply_scrub(wrapped, {"replaces": item2["replaces"], "text": "Example numbers; yours will differ."})
if st5 != "scrubbed" or "Example numbers" not in out4:
    bad.append(("scrub flexible whitespace", st5))

for b in bad:
    print(*b, sep=" | ")
print("FAIL" if bad else "OK", f"{len(bad)} failures" if bad else "scan and scrub")
sys.exit(1 if bad else 0)
