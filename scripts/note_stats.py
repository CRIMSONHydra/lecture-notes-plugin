# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Word/minute/figure statistics of notes and their budget status (PLAN 12.1).

    uv run note_stats.py <note.md>... --profile REF --tier basic|advanced [--json OUT]

``notefile.stats`` supplies the numbers (prose words exclude fenced code, tables, front matter and the
closing sections). Basic-tier status per check:

    prose words   the profile range is for a ~2 h class and is scaled by clamp(duration_min / 120, 0.5, 1.5)
                  (duration_min from the note's front matter). Under the minimum -> warn, "too thin";
                  over the maximum -> warn. Never a failure: coverage and visuals are the hard checks.
    minutes       informational only (never warns or fails)
    gap-fill      pass <= gapfill_max_share (default 0.30) of prose words; fail above

Reading minutes (PLAN 16.5): prose/150 + code lines x 8 s + table rows x 15 s + figures x 10 s. The basic tier is
judged by reading time and difficulty, not page count (large class figures legitimately add pages).
Complexity indicator: average sentence length (prose words per sentence) and *undefined jargon density* - acronyms
and CamelCase terms per 100 prose words that are neither bold-defined in the note nor in the bold-defined
vocabulary of earlier notes in the same folder (or --vocab-from DIR). Basic warns above ~24 words/sentence or
~2.5 undefined terms per 100 words ("reads harder than class level"); it is a heuristic, never a failure.

Advanced has no cap: only prose words below the profile minimum warn; minutes are not budgeted.
Exit 1 only if the gap-fill share fails (warnings do not fail).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, layout, notefile, platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()

import re  # noqa: E402

SENT_WARN = 24.0           # average prose words per sentence
JARGON_WARN = 2.5          # undefined jargon terms per 100 prose words
COMMON = {"cpu", "ram", "os", "io", "pdf", "ascii", "utf", "usb", "gb", "mb", "kb", "ok", "api", "url", "id", "ui",
          "gpu", "ide", "faq", "etc", "vs", "tbd", "todo", "http", "https", "json", "csv", "html", "css", "sql",
          "yaml", "uv", "gcc", "wsl", "gui", "cli", "pc", "ssd", "hdd", "ns", "us", "ms", "hz", "ghz", "mhz",
          "ai", "ml", "llm", "dsa", "cs", "it", "q", "a"}
BOLD_RE = re.compile(r"\*\*([^*\n]{2,60})\*\*")
ACRO_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,6}\b")
CAMEL_RE = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]+)+\b|\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+\b")
RANK = {"pass": 0, "n/a": 0, "warn": 1, "fail": 2}


def duration_scale(duration_min) -> float:
    try:
        return min(1.5, max(0.5, float(duration_min) / 120.0))
    except (TypeError, ValueError):
        return 1.0


def words_status(v: float, lo: float, hi: float, tier: str) -> str:
    """warn when under the minimum (too thin) or, basic only, over the maximum; never fail."""
    if v < lo or (tier == "basic" and v > hi):
        return "warn"
    return "pass"


def reading_minutes(st: dict) -> float:
    """Prose + code + tables, figures at 10 s each."""
    return round(st["minutes"] + st.get("figures", 0) * 10 / 60.0, 1)


def bold_terms(body: str) -> set[str]:
    out: set[str] = set()
    for kind, text, _i in notefile.iter_blocks(body):
        if kind in ("prose", "quote", "table"):
            for m in BOLD_RE.finditer(text):
                t = m.group(1).strip().rstrip(":.").lower()
                out.add(t)
                out.update(w for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", t))
                out.update(re.findall(r"\(([A-Za-z0-9]{2,8})\)", m.group(0)))
    return out


def defined_acronyms(body: str) -> set[str]:
    """Acronyms introduced as `Full name (ACR)` anywhere in the prose."""
    return {m.lower() for m in re.findall(r"\w[\w -]{3,}\(([A-Z][A-Z0-9]{1,6})\)", body)}


def complexity(body: str, known: set[str] | None = None) -> dict:
    """Average sentence length + undefined jargon density of the prose of ``body`` (a heuristic)."""
    prose = []
    for kind, text, _i in notefile.iter_blocks(body):
        if kind in ("prose", "quote"):
            prose.append(text)
    txt = "\n".join(prose)
    plain = notefile.clean_inline(re.sub(r"`[^`\n]*`", " ", txt))
    plain = re.sub(r"\bi\.e\.|\be\.g\.|\bvs\.|\betc\.", " ", plain)
    sentences = [x for x in re.split(r"(?<=[.!?])\s+|\n\s*\n", plain) if re.search(r"\w", x)]
    words = sum(notefile.count_words(x) for x in sentences)
    avg = round(words / len(sentences), 1) if sentences else 0.0
    defined = (known or set()) | bold_terms(body) | defined_acronyms(body) | COMMON
    cand: dict[str, int] = {}
    for tok in ACRO_RE.findall(plain) + CAMEL_RE.findall(plain):
        if tok.lower() not in defined:
            cand[tok] = cand.get(tok, 0) + 1
    n_und = sum(cand.values())
    per100 = round(100.0 * n_und / words, 2) if words else 0.0
    top = sorted(cand.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {"avg_sentence_words": avg, "sentences": len(sentences), "words": words,
            "undefined_jargon": n_und, "jargon_per_100w": per100, "top_undefined": [k for k, _ in top]}


def earlier_vocab(note_path: Path) -> set[str]:
    """Bold-defined vocabulary of the notes numbered below this one in the same folder."""
    num = notefile.note_id(note_path.name)[0] or ""
    m = re.match(r"(\d+)", num)
    if not m:
        return set()
    me, out = int(m.group(1)), set()
    for f in sorted(note_path.parent.glob("[0-9]*.md")):
        fm = re.match(r"(\d+)", f.name)
        if fm and int(fm.group(1)) < me:
            try:
                out |= bold_terms(notefile.parse_note(f).body) | defined_acronyms(f.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError):
                pass
    return out


def budget(st: dict, cfg: dict, tier: str, duration_min=None, cx: dict | None = None) -> dict:
    """Budget block for one stats dict (importable by other scripts). ``cx`` = complexity() of the note."""
    lo, hi = cfg.get("words", [0, 10 ** 9])
    scale = duration_scale(duration_min) if tier == "basic" else 1.0
    lo, hi = int(round(lo * scale)), int(round(hi * scale))
    w = st["prose_words"]
    out = {"words": {"value": w, "range": [lo, hi], "scale": round(scale, 2),
                     "status": words_status(w, lo, hi, tier), "too_thin": w < lo,
                     "over": tier == "basic" and w > hi}}
    if tier == "basic":
        mlo, mhi = cfg.get("minutes", [0, 10 ** 9])
        out["minutes"] = {"value": st["minutes"], "range": [mlo, mhi], "status": "pass",   # informational
                          "reading": reading_minutes(st)}
        if cx is not None:
            hard = cx["avg_sentence_words"] > SENT_WARN or cx["jargon_per_100w"] > JARGON_WARN
            out["complexity"] = {**cx, "status": "warn" if hard else "pass", "harder_than_class": hard}
        cap = float(cfg.get("gapfill_max_share", 0.30))
        share = st["gapfill_words"] / st["prose_words"] if st["prose_words"] else 0.0
        out["gapfill"] = {"value": round(share, 3), "cap": cap, "words": st["gapfill_words"],
                          "status": "pass" if share <= cap else "fail"}
    else:
        out["minutes"] = {"value": st["minutes"], "range": None, "status": "n/a"}
        out["gapfill"] = {"value": None, "cap": None, "words": st["gapfill_words"], "status": "n/a"}
    out["status"] = max((b["status"] for b in out.values() if isinstance(b, dict)), key=RANK.get)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("notes", nargs="+")
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", default=None, help="profile id/path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--tier", required=True, choices=["basic", "advanced"])
    ap.add_argument("--vocab-from", action="append", default=None,
                    help="extra folder of earlier notes whose bold-defined terms count as known (repeatable)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    profile = prof.load_or_exit(a.profile, data_dir=a.data_dir, project=a.project, notes=a.notes)
    cfg = prof.tier_cfg(profile, a.tier)
    rows = []
    for npath in a.notes:
        note = notefile.parse_note(plat.norm(npath))
        st = notefile.stats(note.body, cfg.get("closing_sections", []))
        known = earlier_vocab(note.path) | {t for v in (a.vocab_from or []) for t in earlier_vocab(Path(v) / "99-x.md")}
        cx = complexity(note.body, known) if a.tier == "basic" else None
        b = budget(st, cfg, a.tier, note.meta.get("duration_min"), cx)
        rows.append({"path": note.path.as_posix(), "stem": note.path.stem, "num": note.num,
                     "legacy": note.legacy, "stats": st, "budget": b, "status": b["status"]})
        w, m, g = b["words"], b["minutes"], b["gapfill"]
        print(f"{note.path.name}: {st['prose_words']} prose words [{w['status']}"
              + (", too thin" if w["too_thin"] else ", over range" if w["over"] else "")
              + f" of {w['range'][0]}-{w['range'][1]}], "
              f"{st['code_lines']} code lines, {st['table_rows']} table rows, {st['figures']} figures, "
              f"~{st['minutes']} min [{m['status']}]"
              + (f", gap-fill {st['gapfill_words']}w = {g['value']:.0%} [{g['status']}]"
                 if a.tier == "basic" else "")
              + (f", reads {m['reading']} min, {cx['avg_sentence_words']} words/sentence, "
                 f"{cx['jargon_per_100w']} undefined jargon/100w"
                 + (" [harder than class level: " + ", ".join(cx["top_undefined"][:5]) + "]"
                    if b["complexity"]["harder_than_class"] else "") if cx else "")
              + f"  => {b['status'].upper()}")
    worst = max((r["status"] for r in rows), key=RANK.get)
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": worst != "fail", "tier": a.tier, "status": worst, "notes": rows})
    return 1 if worst == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
