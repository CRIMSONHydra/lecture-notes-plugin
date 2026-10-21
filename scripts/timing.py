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
import re
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


INFERRED = "inferred from file times"
_CORE = ("digest", "writer-basic", "writer-advanced", "qa-basic", "qa-advanced")


def _mtime(p: Path):
    try:
        return dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)
    except OSError:
        return None


def _newest(paths):
    ts = [t for t in (_mtime(p) for p in paths) if t]
    return max(ts) if ts else None


def infer_events(root: Path, session) -> list[dict]:
    """Stage start/end events reconstructed from the files a run leaves behind: ``inputs.json`` (start),
    ``digest.md``, the research files, each tier's sidecars (end of writing), figures, QA logs and PDFs.
    Agents often skip the marks; the artifacts are always there. Writers and QA of the two tiers run in
    parallel, so their stages overlap and tier totals are upper bounds, as for marked runs."""
    lay = layout.Layout(root)
    sdir = lay.session(session)
    m = re.match(r"\s*(\d+)", str(session))
    nn = f"{int(m.group(1)):02d}" if m else str(session)
    ev: list[dict] = []

    def add(stage, start, end):
        if start and end and end >= start:
            for event, ts in (("start", start), ("end", end)):
                ev.append({"stage": stage, "event": event, "ts": ts.isoformat(timespec="milliseconds"), "note": INFERRED})

    t0 = _mtime(sdir / "inputs.json")
    digest = _mtime(sdir / "digest.md")
    add("digest", t0, digest)
    rv, rd = _mtime(sdir / "research-verify.md"), _mtime(sdir / "research-deep.md")
    add("research-verify", digest or t0, rv)
    add("research-deep", digest or t0, rd)
    research_end = max((t for t in (rv, rd, digest, t0) if t), default=None)
    for tier in ("basic", "advanced"):
        sidecars = list(lay.meta(tier).glob(f"{nn}-*.coverage.json")) + list(lay.meta(tier).glob(f"{nn}-*.visuals.json"))
        w_end = _newest(sidecars) or _newest(lay.notes(tier).glob(f"{nn}-*.md"))
        add(f"writer-{tier}", research_end, w_end)
        f_end = _newest(lay.figures(tier).glob(f"{nn}-*"))
        if f_end and w_end and f_end > w_end:
            add(f"figures-{tier}", w_end, f_end)
        q_start = max((t for t in (w_end, f_end) if t), default=None)
        q_end = _newest(list(sdir.glob(f"logs/qa-{tier}-iter*.json")) + list(lay.pdf(tier).glob(f"*-{nn}-*.pdf")))
        add(f"qa-{tier}", q_start, q_end)
    return ev


def complete(events: list[dict], root: Path, session) -> tuple[list[dict], list[str]]:
    """Marked events plus inferred ones for every core stage that was never marked or never ended.
    Returns the merged events and the names of the inferred stages."""
    marked: dict[str, dict] = {}
    for e in events:
        s = marked.setdefault(e["stage"], {"start": False, "end": False})
        s[e["event"]] = True
    def marked_fully(st):
        return bool(marked.get(st, {}).get("start") and marked.get(st, {}).get("end"))

    if all(marked_fully(st) for st in _CORE) and not any(s["start"] and not s["end"] for s in marked.values()):
        return events, []
    inferred = [e for e in infer_events(root, session) if not marked_fully(e["stage"])]
    replaced = {e["stage"] for e in inferred}
    return [e for e in events if e["stage"] not in replaced] + inferred, sorted(replaced)


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
        if r["notes"] and all(n == INFERRED for n in r["notes"]):
            mark += "  (inferred)"
        L.append(f"{r['stage']:<18} {r['minutes']:>7.1f} {str(r['budget'] or '-'):>7}{mark}")
    L.append("-" * 44)
    for t, v in rep["tier_totals_min"].items():
        L.append(f"{'total ' + t:<18} {v:>7.1f}")
    L.append(f"{'wall clock':<18} {rep['wall_min']:>7.1f}")
    if rep["flagged"]:
        L.append("FLAGGED (> 1.5x budget): " + ", ".join(rep["flagged"]))
    if rep.get("inferred"):
        L.append("(inferred) = the stage was not marked (or never ended); its time comes from the files it left behind")
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
    ev, inferred = complete(read(p), layout.find_project(explicit=a.project), a.session)
    if not ev:
        print(f"no timings recorded at {p} and no session files to infer them from", file=sys.stderr)
        return 1
    rep = build_report(ev)
    rep["inferred"] = inferred
    print(format_report(rep))
    if a.json:
        guard.assert_writable(a.json)
        plat.write_json(a.json, rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
