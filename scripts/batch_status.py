# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Final report of a parallel `/notes:update` batch (PLAN 16.2): per-session status and timing, wall clock vs sum.

    uv run batch_status.py [--project DIR] [--sessions 2,3,5] [--since ISO] [--json OUT]

Reads the ledger (status and generation time per session and tier) and every session's ``timings.jsonl``
(``timing.py``) and prints

  * one row per session: ledger status per tier (ok / partial / failed / not generated), minutes spent
    (sum of its stage minutes), its own wall clock (first to last mark), flagged stages;
  * ``sum of per-session wall`` (what a sequential run would have cost) against ``batch wall clock`` (first mark of
    any session to the last of any session) and the speed-up; and the sessions to retry (failed or partial).

``--since`` limits the batch to timing marks at or after that ISO time (default: everything recorded).
Cost per session is shown when a stage mark carries a ``cost`` note such as ``cost=0.42``; otherwise ``-``.
Exit 1 when some session failed (so a wrapper can say "retry with /notes:update").
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import layout, ledger, platform as plat  # noqa: E402
import timing  # noqa: E402

plat.utf8_stdio()


def parse_ts(x: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(x)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def session_rows(lay, only: set[int] | None, since: dt.datetime | None) -> list[dict]:
    state = ledger.load(lay)
    sdir = lay.work / "sessions"
    nns = set(state["sessions"]) | ({p.name for p in sdir.iterdir() if p.is_dir()} if sdir.is_dir() else set())
    rows = []
    for nn in sorted(nns):
        if not nn.isdigit() or (only is not None and int(nn) not in only):
            continue
        ev = timing.read(lay.session(nn) / "timings.jsonl")
        if since:
            ev = [e for e in ev if parse_ts(e["ts"]) >= since]
        ent = state["sessions"].get(nn) or {}
        tiers = {t: ((ent.get("tiers") or {}).get(t) or {}).get("status", "-") for t in layout.TIERS}
        rep = timing.build_report(ev) if ev else None
        cost = None
        for e in ev:
            m = re.search(r"cost=\$?([0-9.]+)", e.get("note") or "")
            if m:
                cost = (cost or 0.0) + float(m.group(1))
        stamps = [parse_ts(e["ts"]) for e in ev]
        rows.append({"nn": nn, "title": ent.get("title"), "tiers": tiers,
                     "stage_min": round(sum(s["minutes"] for s in rep["stages"]), 1) if rep else None,
                     "wall_min": rep["wall_min"] if rep else None, "flagged": rep["flagged"] if rep else [],
                     "first": min(stamps).isoformat() if stamps else None,
                     "last": max(stamps).isoformat() if stamps else None, "cost": cost,
                     "retry": any(v in ("failed", "partial") for v in tiers.values())})
    return rows


def summarise(rows: list[dict]) -> dict:
    timed = [r for r in rows if r["first"]]
    wall = 0.0
    if timed:
        wall = (max(parse_ts(r["last"]) for r in timed) - min(parse_ts(r["first"]) for r in timed)).total_seconds() / 60
    total = sum(r["wall_min"] or 0.0 for r in timed)
    return {"sessions": len(rows), "timed": len(timed), "batch_wall_min": round(wall, 1),
            "sum_session_wall_min": round(total, 1),
            "speedup": round(total / wall, 2) if wall > 0 else None,
            "retry": [r["nn"] for r in rows if r["retry"]],
            "failed": [r["nn"] for r in rows if "failed" in r["tiers"].values()],
            "cost_total": round(sum(r["cost"] or 0.0 for r in rows), 2) if any(r["cost"] for r in rows) else None}


def format_table(rows: list[dict], sm: dict) -> str:
    L = [f"{'session':<8}{'basic':<10}{'advanced':<10}{'stage min':>10}{'wall min':>10}{'cost':>8}  notes", "-" * 66]
    for r in rows:
        L.append(f"{r['nn']:<8}{r['tiers']['basic']:<10}{r['tiers']['advanced']:<10}"
                 f"{(r['stage_min'] if r['stage_min'] is not None else '-')!s:>10}"
                 f"{(r['wall_min'] if r['wall_min'] is not None else '-')!s:>10}"
                 f"{('%.2f' % r['cost']) if r['cost'] is not None else '-':>8}  "
                 + ("retry; " if r["retry"] else "") + ("over budget: " + ", ".join(r["flagged"]) if r["flagged"] else ""))
    L.append("-" * 66)
    L.append(f"batch wall clock {sm['batch_wall_min']} min; sum of per-session wall {sm['sum_session_wall_min']} min"
             + (f"; speed-up x{sm['speedup']}" if sm["speedup"] else ""))
    if sm["cost_total"] is not None:
        L.append(f"cost total {sm['cost_total']}")
    if sm["retry"]:
        L.append("to retry with /notes:update: " + ", ".join(sm["retry"]))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project")
    ap.add_argument("--sessions")
    ap.add_argument("--since")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        lay = layout.Layout.for_project(a.project)
    except layout.ProjectError as e:
        print(f"batch_status: {e}", file=sys.stderr)
        return 2
    only = {int(x) for x in a.sessions.split(",") if x.strip().isdigit()} if a.sessions else None
    rows = session_rows(lay, only, parse_ts(a.since) if a.since else None)
    sm = summarise(rows)
    print(format_table(rows, sm))
    if a.json:
        plat.write_json(plat.norm(a.json), {"rows": rows, "summary": sm})
    return 1 if sm["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
