# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Verify (and optionally apply) a sweep plan idempotently (PLAN 16.3).

    uv run sweep_check.py [--project DIR] [--tier basic|advanced|both] [--plan JSON] [--apply] [--json OUT]

Check mode (default): scans every note of the tier(s) for ``<!-- sweep:ID -->`` ... ``<!-- /sweep:ID -->`` blocks and reports

  * unbalanced, nested or unclosed markers,
  * an ID that occurs more than once (in one note or across the tier): a re-run would duplicate it,
  * with ``--plan``: plan edits whose ID is missing from the target note (not applied yet), blocks in a note
    that the plan does not know (orphans), and an ID found in a note other than the plan's.

``--apply`` is the reference implementation of what a sweep-editor does, handy for tests and as a fallback:
each plan edit ``{id, note, tier, anchor, type, text}`` becomes a block

    <!-- sweep:ID -->
    text
    <!-- /sweep:ID -->

that REPLACES the existing block with that ID, or is inserted right after the ``anchor`` heading line (an anchor
not found = the edit is skipped and reported). Applying the same plan twice gives byte-identical notes.
``--plan`` is ``{"edits": [...]}`` or a bare list; ``note`` is a file name or a path relative to the project.
``resolve-pending`` edits (PLAN 18) are different: ``{id, note, tier, type: "resolve-pending", replaces, text, [pending]}``
swap the ``pending-ref`` span (or the stale sentence quoted in ``replaces``, whitespace-tolerant) for an INLINE span
``<!-- sweep:ID -->text<!-- /sweep:ID -->`` inside the paragraph; re-applying replaces the span's text.
With ``--plan`` or ``--apply`` a pending reference whose target note exists and that is still unresolved is a problem;
references to notes that do not exist yet are only listed (``note:`` lines).
Exit 1 when problems are found (apply: when an edit could not be placed).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import guard, layout, notefile, pending as pending_lib, platform as plat  # noqa: E402

plat.utf8_stdio()
M = notefile.SWEEP_ANY_RE
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def scan_text(text: str) -> tuple[dict[str, list[int]], list[str]]:
    """({id: [line, ...]}, problems) for one note."""
    seen: dict[str, list[int]] = {}
    problems: list[str] = []
    open_id, open_line, fenced = None, 0, False
    for i, ln in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", ln):
            fenced = not fenced
        if fenced:
            continue
        for m in M.finditer(ln):
            closing, sid = bool(m.group(1)), m.group(2)
            if not closing:
                if open_id:
                    problems.append(f"line {i}: sweep:{sid} opens inside sweep:{open_id}")
                seen.setdefault(sid, []).append(i)
                open_id, open_line = sid, i
            else:
                if open_id != sid:
                    problems.append(f"line {i}: /sweep:{sid} closes {open_id!r}" if open_id
                                    else f"line {i}: /sweep:{sid} has no opening marker")
                open_id = None
    if open_id:
        problems.append(f"line {open_line}: sweep:{open_id} is never closed")
    for sid, lines in seen.items():
        if len(lines) > 1:
            problems.append(f"sweep id {sid} appears {len(lines)} times (lines {', '.join(map(str, lines))})")
    return seen, problems


def block(edit: dict) -> str:
    sid = edit["id"]
    body = str(edit["text"]).strip("\n")
    return f"<!-- sweep:{sid} -->\n{body}\n<!-- /sweep:{sid} -->"


def inline_block(edit: dict) -> str:
    """Inline marker span (a ``resolve-pending`` edit lives inside a paragraph, so no own-line markers)."""
    sid = edit["id"]
    return f"<!-- sweep:{sid} -->{' '.join(str(edit['text']).split())}<!-- /sweep:{sid} -->"


def _flex(s: str) -> re.Pattern:
    """Regex matching ``s`` with any whitespace run (also a line break plus a blockquote ``>``) in place of a space."""
    parts = [re.escape(x) for x in s.split()]
    return re.compile(r"(?:\s|(?<=\n)>)+".join(parts)) if parts else re.compile(r"(?!x)x")


def apply_resolve_pending(text: str, edit: dict) -> tuple[str, str]:
    """``resolve-pending`` (PLAN 18): swap a ``pending-ref`` span (or the stale sentence in ``replaces``) for an inline
    sweep span holding the corrected sentence with its links. status resolved | replaced | replaces-not-found."""
    nl = "\r\n" if "\r\n" in text else "\n"
    t = text.replace("\r\n", "\n")
    sid = re.escape(edit["id"])
    rx = re.compile(rf"<!--\s*sweep:{sid}\s*-->.*?<!--\s*/sweep:{sid}\s*-->", re.S)
    if rx.search(t):
        return rx.sub(lambda _m: inline_block(edit), t, count=1).replace("\n", nl), "replaced"
    old = str(edit.get("replaces") or "")
    span = None
    if old:
        i = t.find(old)
        span = (i, i + len(old)) if i >= 0 else None
        if span is None:
            m = _flex(old).search(t)
            span = m.span() if m else None
    if span is None and edit.get("pending"):
        m = re.search(rf"<!--\s*pending-ref:{re.escape(str(edit['pending']))}\s*-->.*?"
                      rf"<!--\s*/pending-ref:{re.escape(str(edit['pending']))}\s*-->", t, re.S)
        span = m.span() if m else None
    if span is None:
        return text, "replaces-not-found"
    out = t[:span[0]] + inline_block(edit) + t[span[1]:]
    return out.replace("\n", nl), "resolved"


def apply_edit(text: str, edit: dict) -> tuple[str, str]:
    """(new text, status) with status replaced | inserted | anchor-not-found. Idempotent."""
    if edit.get("type") == "resolve-pending":
        return apply_resolve_pending(text, edit)
    nl = "\r\n" if "\r\n" in text else "\n"
    t = text.replace("\r\n", "\n")
    sid = re.escape(edit["id"])
    rx = re.compile(rf"<!--\s*sweep:{sid}\s*-->.*?<!--\s*/sweep:{sid}\s*-->", re.S)
    if rx.search(t):
        out = rx.sub(lambda _m: block(edit), t, count=1)
        return out.replace("\n", nl), "replaced"
    anchor = notefile._heading_key(str(edit.get("anchor", "")))
    lines = t.split("\n")
    fenced = False
    for i, ln in enumerate(lines):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", ln):
            fenced = not fenced
        m = None if fenced else HEAD_RE.match(ln)
        if m and notefile._heading_key(m.group(2)) == anchor:
            blk = block(edit).split("\n")
            lines[i + 1:i + 1] = ["", *blk]
            after = i + 2 + len(blk)
            if after < len(lines) and lines[after].strip():
                lines.insert(after, "")
            return "\n".join(lines).replace("\n", nl), "inserted"
    return text, "anchor-not-found"


def load_plan(path: Path) -> list[dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    edits = doc.get("edits") if isinstance(doc, dict) else doc
    if not isinstance(edits, list):
        raise ValueError("plan must be a list of edits or {'edits': [...]}")
    return [e for e in edits if isinstance(e, dict) and e.get("id") and e.get("note")]


def note_path(lay, edit: dict, tier: str) -> Path:
    n = Path(str(edit["note"]))
    for cand in (lay.root / n, lay.notes(edit.get("tier") or tier) / n.name):
        if cand.is_file():
            return cand
    return lay.notes(edit.get("tier") or tier) / n.name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project")
    ap.add_argument("--tier", default="both", choices=["basic", "advanced", "both"])
    ap.add_argument("--plan")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--data-dir")
    a = ap.parse_args(argv)
    try:
        lay = layout.Layout.for_project(a.project)
    except layout.ProjectError as e:
        print(f"sweep_check: {e}", file=sys.stderr)
        return 2
    tiers = list(layout.TIERS) if a.tier == "both" else [a.tier]
    problems: list[str] = []
    edits = load_plan(plat.norm(a.plan)) if a.plan else []
    applied: list[dict] = []
    if a.apply:
        if not edits:
            print("sweep_check: --apply needs --plan", file=sys.stderr)
            return 2
        ddir = plat.resolve_data_dir(a.data_dir)
        for e in edits:
            if e.get("tier") and e["tier"] not in tiers:
                continue
            p = note_path(lay, e, tiers[0])
            if not p.is_file():
                applied.append({"id": e["id"], "status": "note-missing"})
                problems.append(f"{e['id']}: note {p.name} not found")
                continue
            text = p.read_text(encoding="utf-8-sig")        # LF-normalised, BOM stripped
            new, status = apply_edit(text, e)
            if new != text:
                guard.assert_writable(p, ddir)
                p.write_bytes(new.encode("utf-8"))
            applied.append({"id": e["id"], "note": p.name, "status": status})
            if status == "replaces-not-found":
                problems.append(f"{e['id']}: neither the stale sentence nor a pending-ref marker was found in {p.name}")
            if status == "anchor-not-found":
                problems.append(f"{e['id']}: anchor {e.get('anchor')!r} not found in {p.name}")
    # check pass (after any apply)
    owner: dict[str, list[str]] = {}
    for t in tiers:
        d = lay.notes(t)
        for f in sorted(d.glob("[0-9]*.md")) if d.is_dir() else []:
            seen, probs = scan_text(f.read_text(encoding="utf-8-sig"))
            problems += [f"{t}/{f.name}: {x}" for x in probs]
            for sid in seen:
                owner.setdefault(f"{t}:{sid}", []).append(f.name)
    for key, files in owner.items():
        if len(set(files)) > 1:
            problems.append(f"sweep id {key} is in several notes: {', '.join(sorted(set(files)))}")
    if edits and not a.apply:
        want = {f"{e.get('tier') or tiers[0]}:{e['id']}": Path(str(e["note"])).name for e in edits
                if (e.get("tier") or tiers[0]) in tiers}
        for key, fname in want.items():
            if key not in owner:
                problems.append(f"plan edit {key} is not applied in {fname} yet")
            elif fname not in owner[key]:
                problems.append(f"plan edit {key} was found in {owner[key]}, not in {fname}")
        known = {k for k in want}
        for key in owner:
            if key not in known:
                problems.append(f"block {key} in {owner[key][0]} is not in the plan (orphan)")
    # pending references (PLAN 18): with a plan, every one whose target note exists must be resolved by now
    waiting: list[str] = []
    for t in tiers:
        d = lay.notes(t)
        for f in sorted(d.glob("[0-9]*.md")) if d.is_dir() else []:
            res = pending_lib.scan(f.read_text(encoding="utf-8-sig"))
            for it in res["markers"] + res["legacy"]:
                have = bool(it["targets"]) and all(any(d.glob(f"{x:02d}-*.md")) for x in it["targets"])
                msg = f"{t}/{f.name}:{it['line']}: {it['kind']} pending reference to session " \
                      f"{', '.join(map(str, it['targets'])) or '?'}"
                if have:
                    if a.plan or a.apply:
                        problems.append(msg + " can be resolved (target note exists) but was not")
                    else:
                        waiting.append(msg)
                else:
                    waiting.append(msg + " (target note still missing: left in place)")
    n_blocks = len(owner)
    for w in waiting:
        print(f"  note: {w}")
    print(f"sweep_check: {n_blocks} sweep block(s) in {', '.join(tiers)}; "
          + (f"applied {sum(1 for x in applied if x['status'] in ('replaced', 'inserted', 'resolved'))}/{len(applied)}; " if a.apply else "")
          + f"{len(problems)} problem(s)")
    for p in problems:
        print(f"  - {p}")
    if a.json:
        plat.write_json(plat.norm(a.json), {"ok": not problems, "blocks": n_blocks, "applied": applied,
                                            "problems": problems})
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
