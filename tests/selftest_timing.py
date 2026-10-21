# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Self-test for scripts/timing.py: marked stages, and stages inferred from file times when marks are missing.

    uv run tests/selftest_timing.py
"""
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("timing", ROOT / "scripts" / "timing.py")
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)
from notes_lib import layout  # noqa: E402

bad: list[tuple] = []
root = Path(tempfile.mkdtemp(prefix="notes-timing-"))
(root / ".notes-plugin-internal").mkdir()
(root / ".notes-plugin-internal" / "profile.md").write_text("---\nschema: 1\nid: t\ntitle: T\nshort: T\nprefix: T\n---\n", encoding="utf-8")
lay = layout.Layout(root)
sdir = lay.session("03")
T0 = time.time() - 3600


def touch(p: Path, minutes: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    os.utime(p, (T0 + minutes * 60, T0 + minutes * 60))


touch(sdir / "inputs.json", 0)
touch(sdir / "digest.md", 4)
touch(sdir / "research-verify.md", 6)
touch(sdir / "research-deep.md", 9)
touch(lay.meta("basic") / "03-x.coverage.json", 17)
touch(lay.notes("basic") / "03-x.md", 30)                      # edited again by QA later: not the writer's end
touch(lay.figures("basic") / "03-x-flow.png", 20)
touch(sdir / "logs" / "qa-basic-iter1.json", 24)
touch(lay.pdf("basic") / "T-03-x-basic.pdf", 26)
touch(lay.meta("advanced") / "03-x.coverage.json", 22)
touch(lay.pdf("advanced") / "T-03-x-advanced.pdf", 31)

# no marks at all: every core stage is inferred
events, inferred = timing.complete([], root, "3")
rep = timing.build_report(events)
mins = {r["stage"]: r["minutes"] for r in rep["stages"]}
want = {"digest": 4.0, "research-verify": 2.0, "research-deep": 5.0, "writer-basic": 8.0, "figures-basic": 3.0,
        "qa-basic": 6.0, "writer-advanced": 13.0, "qa-advanced": 9.0}
for st, m in want.items():
    if abs(mins.get(st, -1) - m) > 0.05:
        bad.append(("inferred minutes", st, mins.get(st), "want", m))
if sorted(inferred) != sorted(want):
    bad.append(("inferred stages", inferred))
if abs(rep["wall_min"] - 31.0) > 0.05:
    bad.append(("wall clock", rep["wall_min"]))
text = timing.format_report(dict(rep, inferred=inferred))
if "(inferred)" not in text or "digest" not in text:
    bad.append(("format", text))

# a marked, ended stage is kept as marked; an unended one is replaced by the inferred stage
iso = lambda m: timing.dt.datetime.fromtimestamp(T0 + m * 60, timing.dt.timezone.utc).isoformat(timespec="milliseconds")  # noqa: E731
marked = [{"stage": "digest", "event": "start", "ts": iso(0), "note": ""},
          {"stage": "digest", "event": "end", "ts": iso(3), "note": ""},
          {"stage": "writer-basic", "event": "start", "ts": iso(9), "note": ""}]
events, inferred = timing.complete(marked, root, "3")
rep = timing.build_report(events)
mins = {r["stage"]: r["minutes"] for r in rep["stages"]}
if abs(mins["digest"] - 3.0) > 0.05 or "digest" in inferred:
    bad.append(("marked digest must win", mins.get("digest"), inferred))
if abs(mins["writer-basic"] - 8.0) > 0.05 or "writer-basic" not in inferred:
    bad.append(("unended writer-basic must be inferred", mins.get("writer-basic"), inferred))

# nothing to infer from: no events, no crash
empty = Path(tempfile.mkdtemp(prefix="notes-timing-empty-"))
(empty / ".notes-plugin-internal").mkdir()
events, inferred = timing.complete([], empty, "1")
if events or inferred:
    bad.append(("empty project", events, inferred))

for b in bad:
    print(*b, sep=" | ")
print("FAIL" if bad else "OK", f"{len(bad)} failures" if bad else "3 scenarios")
sys.exit(1 if bad else 0)
