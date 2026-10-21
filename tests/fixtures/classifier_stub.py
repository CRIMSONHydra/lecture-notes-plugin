"""Deterministic stand-in for the LLM classifier (LECTURE_NOTES_CLASSIFIER_CMD) used by the self-tests.

Same contract as `claude -p --output-format json --json-schema …`: reads {"context", "items"} on stdin and
prints {"structured_output": {"verdicts": [...]}}. Rules: text mentioning terraform, aws or kubectl is
denied; `docker run` without --rm is rewritten; `mktemp-notes` creates a temp dir that needs cleanup;
everything else is allowed.
"""
import json
import sys
import tempfile
from pathlib import Path

req = json.load(sys.stdin)
out = []
for it in req["items"]:
    t = it["text"]
    v = {"id": it["id"], "decision": "allow", "reason": "stub: harmless", "command": t, "creates": [], "cleanup": []}
    if any(w in t for w in ("terraform", "aws ", "kubectl")):
        v.update(decision="deny", reason="stub: touches cloud or cluster resources")
    elif "docker run" in t and "--rm" not in t and it.get("kind") == "command":
        v.update(decision="rewrite", reason="stub: needs limits",
                 command=t.replace("docker run", "docker run --rm --name notes-web -m 256m --cpus 0.5", 1),
                 creates=["container notes-web"], cleanup=["docker rm -f notes-web"])
    elif "mktemp-notes" in t:
        d = Path(tempfile.gettempdir()) / "notes-selftest-leftover"
        v.update(creates=[str(d)], cleanup=[f"rm -rf {d.as_posix()}"])
    out.append(v)
print(json.dumps({"type": "result", "subtype": "success", "structured_output": {"verdicts": out}}))
