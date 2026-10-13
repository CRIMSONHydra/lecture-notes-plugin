# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Generate a tier's index chapter ``<project>/notes/<tier>/README.md`` deterministically from its notes.

    uv run update_index.py --tier basic|advanced [--project DIR] [--profile REF] [--src DIR] [--legacy] [--json OUT]

Contents: H1 "Index"; course facts; the session -> note table over every profile session (pending ones
marked); per-note budget numbers (basic: prose words, computed and stated revision minutes; advanced:
words and estimated reading time); the running table of class corrections parsed from the notes' callouts
(`> **Class correction (<severity emoji> ...):** said -> actual -> why`); basic: the list of gap fills;
advanced: source counts. No timestamps, so the file is byte-identical for identical notes. The section
sign is never emitted. ``--legacy`` is accepted for notes written before the front-matter contract (they
are read from their H1 and their ``**Revision time:**`` line either way).
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, layout, notefile, platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LABEL_RE = re.compile(r"^\*\*([^*()]+?)\s*(?:\(([^)]*)\))?\s*:\*\*\s*(.*)$", re.S)
SEV_RE = re.compile(r"[🔴🟠🟡]")
SEV_WORDS = {"high": "🟠", "low": "🟡", "critical": "🔴"}
CORRECTION = ("class correction", "transcript correction", "class nuance")
GAPFILL = ("gap fill", "beyond class")
URL_RE = re.compile(r"https?://[^\s)>\]\"'`<|]+", re.I)
NOTE_FILE_RE = re.compile(r"^(\d+[a-z]?)-.+\.md$")


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def sort_key(num: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([a-z]?)", num)
    return (int(m.group(1)), m.group(2)) if m else (10 ** 6, num)


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("§", "sec ").strip()


def squash(s: str, limit: int) -> str:
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-—") + "…"
    if s.count("`") % 2:
        s += "`"
    if s.count("**") % 2:
        s += "**"
    return esc(s)


def callouts(body: str):
    """Yield (label, paren, first_paragraph, where) for every `**Label (...):**` callout."""
    where = ""
    for kind, text, info, _ln in notefile.iter_blocks_ex(body):
        if kind == "heading" and info == 2:
            where = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", notefile.clean_inline(text)).strip()
        elif kind == "quote":
            cur = None
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                m = LABEL_RE.match(para)
                if m:
                    cur = (m.group(1).strip(), (m.group(2) or "").strip(), m.group(3), where)
                    yield cur


def split_said_actual(text: str) -> tuple[str, str]:
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith("|")]
    flat = " ".join(" ".join(lines).split())
    if re.search(r"\s(?:→|->)\s", flat):
        parts = [p.strip() for p in re.split(r"\s(?:→|->)\s", flat)]
        said, actual = parts[0], parts[1] if len(parts) > 1 else ""
        if len(parts) > 2:
            actual += " — " + " ".join(parts[2:])
        return said, actual
    m = re.search(r"[.!?][\"”')]?(?:\*\*)?(?=\s+[A-Z*`\"(])", flat[15:])
    if m:
        cut = 15 + m.end()
        return flat[:cut].strip(), flat[cut:].strip()
    return flat, ""


def severity(paren: str) -> str:
    m = SEV_RE.search(paren)
    if m:
        return m.group(0)
    for w, e in SEV_WORDS.items():
        if w in paren.lower():
            return e
    return "—"


def minor_slips(body: str) -> list[dict]:
    """Rows of the basic tier's `## Minor slips in class` table (| said | actual |) as yellow corrections."""
    out, on = [], False
    for ln in body.replace("\r\n", "\n").split("\n"):
        if re.match(r"^#{2,3}\s+minor slips in class\b", ln.strip(), re.I):
            on = True
            continue
        if on and re.match(r"^#{1,3}\s", ln):
            break
        if on and ln.strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|?\s*$", ln):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))]
            if len(cells) >= 2 and cells[0].lower() != "said":
                out.append({"sev": "\U0001f7e1", "where": "Minor slips", "said": cells[0], "actual": cells[1]})
    return out


def analyse(path: Path, tier: str, closing: list[str]) -> dict:
    note = notefile.parse_note(path)
    st = notefile.stats(note.body, closing)
    stated = note.meta.get("revision_minutes")
    if stated in (None, ""):
        m = re.search(r"\*\*Revision time:\*\*\s*~?\s*(\d+)", note.raw)
        stated = int(m.group(1)) if m else None
    urls = {u.rstrip(".,;:!?*_)") for kind, t, _i in notefile.iter_blocks(note.body) if kind != "code"
            for u in URL_RE.findall(t)}
    domains = set()
    for u in urls:
        host = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].lower()
        domains.add(host[4:] if host.startswith("www.") else host)
    corr, gaps = [], []
    for label, paren, text, where in callouts(note.body):
        low = label.lower()
        if low.startswith(CORRECTION):
            said, actual = split_said_actual(text.split("\n\n")[0])
            corr.append({"sev": severity(paren), "where": where, "said": said, "actual": actual})
        elif low.startswith(GAPFILL):
            gaps.append({"where": where, "text": " ".join(text.split("\n\n")[0].split())})
    corr += minor_slips(note.body)
    return {"path": path, "num": note.num, "stem": path.stem, "title": note.title or note.meta.get("title") or path.stem,
            "date": note.meta.get("date"), "stats": st, "stated": stated, "corrections": corr,
            "gapfills": gaps, "urls": len(urls), "domains": len(domains),
            "verified": str(note.meta.get("verified_as_of") or "—")}


def render(profile: dict, tier: str, notes: list[dict]) -> str:
    cfg = prof.tier_cfg(profile, tier)
    prefix = profile.get("prefix", "")
    by_session: dict[int, list[dict]] = {}
    for n in notes:
        by_session.setdefault(sort_key(n["num"])[0], []).append(n)
    sessions = {int(s["n"]): s for s in profile.get("sessions", [])}
    for k in by_session:
        sessions.setdefault(k, {"n": k, "title": by_session[k][0]["title"], "date": by_session[k][0]["date"]})
    ic = profile.get("instructor_code") or {}
    link = lambda n: f"[{n['num']} · {esc(n['title'])}]({n['stem']}.md)"  # noqa: E731
    L: list[str] = ["# Index", ""]
    kind = "Basic (revision)" if tier == "basic" else "Advanced (deep dive)"
    L += [f"{kind} notes for **{esc(profile['title'])}**. The PDFs are what you read; this chapter is the "
          "map: which session is which note, how long each takes, and every class correction in one place.", ""]
    L += ["## Course facts", ""]
    L.append(f"- **Course:** {esc(profile['title'])}")
    if profile.get("institution"):
        L.append(f"- **Institution:** {esc(profile['institution'])}")
    if tier == "basic":
        lo, hi = cfg.get("words", [0, 0])
        mlo, mhi = cfg.get("minutes", [0, 0])
        L.append(f"- **Budget per note:** {lo:,} to {hi:,} prose words, {mlo} to {mhi} minutes to revise")
    else:
        L.append(f"- **Depth:** at least {cfg.get('words', [0])[0]:,} prose words per note, no upper cap; "
                 f"cited sources, worked examples and figures")
    if ic.get("repo"):
        L.append(f"- **Instructor code:** <https://github.com/{ic['repo']}> (one folder per session)")
    done = len({k for k in by_session if k in sessions})
    L.append(f"- **Progress:** {done} of {len(sessions)} sessions have a note")
    L.append("")
    L += ["## Session to note map", "", "| # | Date | Session title | Instructor code | Note |",
          "| --- | --- | --- | --- | --- |"]
    omap = prof.session_orders(profile)
    for n in sorted(sessions, key=lambda k: (prof.order_of(omap, k), k)):      # course order (PLAN 13.5)
        s = sessions[n]
        code = ic.get("session_dir", "").format(n=n) + "/" if ic.get("session_dir") else "—"
        cell = "; ".join(link(x) for x in sorted(by_session.get(n, []), key=lambda x: sort_key(x["num"]))) \
            or "*pending*"
        L.append(f"| {n} | {fmt_date(s.get('date'))} | {esc(str(s.get('title') or '—'))} | "
                 f"{'`' + code + '`' if code != '—' else code} | {cell} |")
    L.append("")
    L += ["## The notes", ""]
    if tier == "basic":
        L += ["| # | Note | Prose words | Computed min | Stated min |", "| --- | --- | --- | --- | --- |"]
        for n in notes:
            st = n["stats"]
            L.append(f"| {n['num']} | {link(n)} | {st['prose_words']:,} | ~{st['minutes']:.0f} | "
                     f"{'~' + str(n['stated']) if n['stated'] else '—'} |")
        L += ["", "Computed minutes = prose words / 150 + 8 s per code line + 15 s per table row. "
              "Going Deeper is not counted.", ""]
    else:
        L += ["| # | Note | Prose words | Est. reading time | Figures |", "| --- | --- | --- | --- | --- |"]
        for n in notes:
            st = n["stats"]
            L.append(f"| {n['num']} | {link(n)} | {st['prose_words']:,} | ~{st['minutes']:.0f} min | "
                     f"{st['figures']} |")
        L.append("")
    corr = [(n, c) for n in notes for c in n["corrections"]]
    L += ["## Running list of class corrections", "",
          "Severity: 🔴 changes a quiz answer · 🟠 factually wrong and load-bearing · 🟡 imprecise aside.", ""]
    if corr:
        L += ["| # | Note | Where | Sev | Said in class | Actually |", "| --- | --- | --- | --- | --- | --- |"]
        for i, (n, c) in enumerate(corr, 1):
            L.append(f"| {i} | [{n['num']}]({n['stem']}.md) | {squash(c['where'], 34)} | {c['sev']} | "
                     f"{squash(c['said'], 170)} | {squash(c['actual'], 260)} |")
    else:
        L.append("*No corrections recorded yet.*")
    L.append("")
    if tier == "basic":
        gaps = [(n, g) for n in notes for g in n["gapfills"]]
        L += ["## Gap fills", "", "Places where the class fell short and the note adds a short explanation "
              "(each is a `Gap fill` callout in the note).", ""]
        if gaps:
            L += ["| # | Note | Where | Gap filled |", "| --- | --- | --- | --- |"]
            for i, (n, g) in enumerate(gaps, 1):
                L.append(f"| {i} | [{n['num']}]({n['stem']}.md) | {squash(g['where'], 34)} | "
                         f"{squash(g['text'], 200)} |")
        else:
            L.append("*None.*")
        L.append("")
    else:
        L += ["## Sources", "", "| # | Note | Distinct URLs | Distinct domains | Verified as of |",
              "| --- | --- | --- | --- | --- |"]
        for n in notes:
            L.append(f"| {n['num']} | [{n['num']}]({n['stem']}.md) | {n['urls']} | {n['domains']} | "
                     f"{esc(n['verified'])} |")
        L.append("")
    text = "\n".join(L).rstrip("\n") + "\n"
    assert "§" not in text
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", default=None, help="profile id/path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--tier", required=True, choices=["basic", "advanced"])
    ap.add_argument("--src", default=None, help="notes dir override (default <project>/notes/<tier>)")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    profile = prof.load_or_exit(a.profile, data_dir=a.data_dir, project=a.project)
    cfg = prof.tier_cfg(profile, a.tier)
    if a.src:
        src = plat.norm(a.src)
    else:
        try:
            src = layout.Layout.for_project(a.project).notes(a.tier)
        except layout.ProjectError as e:
            print(f"update_index: {e}", file=sys.stderr)
            return 2
    if not src.is_dir():
        print(f"update_index: {src} does not exist", file=sys.stderr)
        return 2
    omap = prof.session_orders(profile)
    files, seen = [], {}
    for p in sorted(src.glob("*.md")):                       # each session once, in course order
        if not NOTE_FILE_RE.match(p.name):
            continue
        num = notefile.note_id(p.name)[0]
        key = prof.note_sort_key(omap, re.sub(r"[a-z]+$", "", num))     # 10a is session 10: skipped as a duplicate
        if key in seen:
            print(f"update_index: warning: {p.name} has the same session number as {seen[key]}: skipped",
                  file=sys.stderr)
            continue
        seen[key] = p.name
        files.append((key, p))
    files = [p for _k, p in sorted(files, key=lambda kp: kp[0])]
    notes = [analyse(p, a.tier, cfg.get("closing_sections", [])) for p in files]
    out = src / "README.md"
    guard.assert_writable(out)
    text = render(profile, a.tier, notes)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    ncorr = sum(len(n["corrections"]) for n in notes)
    print(f"wrote {out} ({len(notes)} notes, {ncorr} corrections, "
          f"{sum(len(n['gapfills']) for n in notes)} gap fills)")
    for n in notes:
        print(f"  {n['num']}: {len(n['corrections'])} corrections, {len(n['gapfills'])} gap fills")
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {
            "ok": True, "readme": out.as_posix(), "tier": a.tier,
            "notes": [{"num": n["num"], "stem": n["stem"], "title": n["title"],
                       "prose_words": n["stats"]["prose_words"], "minutes": n["stats"]["minutes"],
                       "corrections": n["corrections"], "gapfills": n["gapfills"]} for n in notes]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
