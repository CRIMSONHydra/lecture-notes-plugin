# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Per-stage timing of a lecture run (PLAN 14.3).

    uv run timing.py mark   --project D --session N --stage NAME start|end [--note TEXT]
    uv run timing.py report --project D --session N [--json OUT]

``mark`` appends one JSON line ``{"stage","event","ts","note"}`` to
``<project>/.notes-plugin-internal/work/sessions/NN/timings.jsonl``. Stage names used by the lecture skill:
startup, video, frames, digest, research-verify, research-deep, writer-basic, writer-advanced, figures-basic,
figures-advanced, qa-basic, qa-advanced, build, archive (a repeated stage, e.g. a second QA iteration, adds
up). ``report`` prints a stage table (minutes), per-tier totals (shared stages count toward both tiers: a
tier's total is the sum of its own stages plus the shared ones, so it is an upper bound when stages overlap)
and the wall clock, and flags every stage over 1.5x its budget (minutes): video 6, frames 4, digest 8,
research-verify 3, research-deep 6, writer-basic 8, writer-advanced 13, figures 3, qa 3.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import guard, layout, platform as plat  # noqa: E402

BUDGET = {"video": 6, "frames": 4, "digest": 8, "research-verify": 3, "research-deep": 6, "writer-basic": 8,
          "writer-advanced": 13, "figures-basic": 3, "figures-advanced": 3, "qa-basic": 3, "qa-advanced": 3}
FLAG_FACTOR = 1.5


def path_for(a) -> Path:
    root = layout.find_project(explicit=a.project)
    return layout.Layout(root).session(a.session) / "timings.jsonl"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def read(p: Path) -> list[dict]:
    out = []
    if p.is_file():
        for ln in p.read_text(encoding="utf-8-sig").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def build_report(events: list[dict]) -> dict:
    open_: dict[str, dt.datetime] = {}
    stages: dict[str, dict] = {}
    first = last = None
    for e in events:
        ts = dt.datetime.fromisoformat(e["ts"])
        first = ts if first is None or ts < first else first
        last = ts if last is None or ts > last else last
        s = stages.setdefault(e["stage"], {"stage": e["stage"], "minutes": 0.0, "runs": 0, "open": False, "notes": []})
        if e.get("note"):
            s["notes"].append(e["note"])
        if e["event"] == "start":
            open_[e["stage"]] = ts
        elif e["event"] == "end" and e["stage"] in open_:
            s["minutes"] += (ts - open_.pop(e["stage"])).total_seconds() / 60
            s["runs"] += 1
    for st in open_:
        stages[st]["open"] = True
    rows = []
    for s in stages.values():
        b = BUDGET.get(s["stage"])
        s["minutes"] = round(s["minutes"], 2)
        s["budget"] = b
        s["flag"] = bool(b and s["minutes"] > FLAG_FACTOR * b)
        rows.append(s)
    tiers = {}
    for t in ("basic", "advanced"):
        own = sum(r["minutes"] for r in rows if r["stage"].endswith("-" + t))
        shared = sum(r["minutes"] for r in rows if not r["stage"].endswith(("-basic", "-advanced")))
        tiers[t] = round(own + shared, 2)
    wall = round((last - first).total_seconds() / 60, 2) if first and last else 0.0
    return {"stages": rows, "tier_totals_min": tiers, "wall_min": wall,
            "flagged": [r["stage"] for r in rows if r["flag"]]}


def format_report(rep: dict) -> str:
    L = [f"{'stage':<18} {'min':>7} {'budget':>7}  note", "-" * 44]
    for r in rep["stages"]:
        mark = "  <-- over 1.5x budget" if r["flag"] else ("  (not ended)" if r["open"] else "")
        L.append(f"{r['stage']:<18} {r['minutes']:>7.1f} {str(r['budget'] or '-'):>7}{mark}")
    L.append("-" * 44)
    for t, v in rep["tier_totals_min"].items():
        L.append(f"{'total ' + t:<18} {v:>7.1f}")
    L.append(f"{'wall clock':<18} {rep['wall_min']:>7.1f}")
    if rep["flagged"]:
        L.append("FLAGGED (> 1.5x budget): " + ", ".join(rep["flagged"]))
    return "\n".join(L)


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mark")
    m.add_argument("--project")
    m.add_argument("--session", required=True)
    m.add_argument("--stage", required=True)
    m.add_argument("event", choices=["start", "end"])
    m.add_argument("--note", default="")
    m.add_argument("--at", help="ISO timestamp (tests)")
    r = sub.add_parser("report")
    r.add_argument("--project")
    r.add_argument("--session", required=True)
    r.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        p = path_for(a)
    except layout.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.cmd == "mark":
        guard.assert_writable(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"stage": a.stage, "event": a.event, "ts": a.at or now(), "note": a.note}) + "\n")
        return 0
    ev = read(p)
    if not ev:
        print(f"no timings recorded at {p}", file=sys.stderr)
        return 1
    rep = build_report(ev)
    print(format_report(rep))
    if a.json:
        guard.assert_writable(a.json)
        plat.write_json(a.json, rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
