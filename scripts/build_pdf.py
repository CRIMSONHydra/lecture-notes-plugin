# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "mdit-py-plugins", "pygments", "pymupdf", "websocket-client", "pyyaml"]
# ///
"""Unified markdown -> PDF renderer for both tiers of the `notes` plugin.

    uv run build_pdf.py --tier basic [--project DIR] [--profile REF]
    uv run build_pdf.py --tier advanced --notes 02 02a
    uv run build_pdf.py --tier basic --compendium-only --prune --offline --json OUT
    uv run build_pdf.py --profile P --tier basic --src DIR --pdf-dir DIR --build-dir DIR   (explicit, for tests)

Source is ``<project>/notes/<tier>/`` (``NN[a]-slug.md`` plus ``README.md`` as the Index), output
``<project>/pdf/<tier>/``, work dir ``<project>/.notes-plugin-internal/work/build/<tier>/`` (HTML, Chrome profile,
``manifest.json``).  ``--project`` defaults to the project found above cwd; ``--src/--pdf-dir/--build-dir``
override the three directories.  Outputs::

    {PREFIX}-{NN}-{slug}-{tier}.pdf      one per note (companions: 02a)
    {PREFIX}-00-index-{tier}.pdf         the README
    {PREFIX}-Compendium-{tier}.pdf       cover + contents + every chapter, continuously paginated

Pipeline: discover -> number headings (section sign N.k[.m]) -> markdown -> HTML (tier CSS, KaTeX and
Mermaid inlined only when used) -> headless Chrome over CDP (header, ``page / total`` footer) ->
PyMuPDF (resolve pages, outline, metadata, relative links).  The compendium prints twice (placeholder
contents, then real page numbers) and reprints if pagination drifted.

Chapters (index, compendium, prev/next links in each note's meta block) follow COURSE order: a profile
session's ``order`` (else date, else number), not the file number alone. ``--prune`` deletes PDFs in
``pdf/<tier>/`` that no note produces any more (an old slug, a removed note) and generated figures
``figures/NN-*`` that no note references (only for the sessions being built when ``--notes`` is given);
index/compendium PDFs and files that do not follow the PDF naming are never touched.

Exit 0 clean; 1 on a missing figure, a JS error, a Chrome failure or a missing asset; 2 on usage errors.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notes_lib import chrome as chromelib, guard, layout as projlayout, platform as plat, profile as profmod  # noqa: E402
from render import assets, css, layout, markdown as rmd, paginate  # noqa: E402
from render.cdp import Browser, ChromeError  # noqa: E402

plat.utf8_stdio()

TIERS = ("basic", "advanced")


class Fail(Exception):
    """Fatal build problem: message is printed, exit code is ``code``."""

    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


def log(msg: str = "") -> None:
    print(msg, flush=True)


def norm_selector(s: str) -> str:
    s = s.strip().lower()
    if s in ("00", "0", "index", "readme"):
        return "00"
    m = re.match(r"^(\d+)([a-z]?)$", s)
    return (m.group(1).zfill(2) + m.group(2)) if m else s


class Builder:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.tier: str = args.tier
        self.data_dir = plat.resolve_data_dir(args.data_dir)
        explicit = bool(args.src and args.pdf_dir and args.build_dir)
        try:
            lay = None if explicit and not args.project else projlayout.Layout.for_project(args.project)
            self.profile = profmod.load_profile(args.profile, data_dir=self.data_dir, project=lay.root if lay else None)
        except projlayout.ProjectError as e:
            raise Fail(str(e), 2)
        self.layout = lay
        self.tier_cfg = profmod.tier_cfg(self.profile, self.tier)
        self.src = plat.norm(args.src) if args.src else lay.notes(self.tier)
        self.pdf_dir = plat.norm(args.pdf_dir) if args.pdf_dir else lay.pdf(self.tier)
        self.build = plat.norm(args.build_dir) if args.build_dir else lay.build(self.tier)
        self.prefix: str = self.profile["prefix"]
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.entries: list[dict] = []
        self.pruned: list[str] = []
        self.chrome_path: Path | None = None
        self._katex = None
        self._mermaid = None
        self.css = css.css_for(self.profile, self.tier)
        self.md = rmd.make_md(self.tier, bool(self.tier_cfg.get("math")), self.profile.get("callouts"))
        self.tag = f"{self.profile['short']} · {css.TIER_LABEL[self.tier]}"

    # -- helpers ---------------------------------------------------------
    def error(self, msg: str) -> None:
        if msg in self.errors:
            return
        self.errors.append(msg)
        log(f"  ERROR: {msg}")

    def warn(self, msg: str) -> None:
        if msg in self.warnings:
            return
        self.warnings.append(msg)
        log(f"  warning: {msg}")

    def katex(self) -> dict:
        if self._katex is None:
            try:
                self._katex = assets.get_katex(self.data_dir, self.args.offline)
            except assets.AssetError as e:
                raise Fail(str(e))
        return self._katex

    def mermaid(self) -> str:
        if self._mermaid is None:
            try:
                self._mermaid = assets.get_mermaid(self.data_dir, self.args.offline)
            except assets.AssetError as e:
                raise Fail(str(e))
        return self._mermaid

    def counterpart(self, ch: rmd.Chapter):
        other = "basic" if self.tier == "advanced" else "advanced"
        name = ch.pdf_stem.rsplit("-", 1)[0] + f"-{other}.pdf"
        if (self.pdf_dir.parent / other / name).is_file():
            return f"../{other}/{name}", f"{css.TIER_LABEL[other]} version of this note (PDF)"
        return None

    def check_figures(self, ch: rmd.Chapter, figures: list[str]) -> int:
        """Stage local figures into build/ (HTML references them relative to itself). -> count expected."""
        n = 0
        for src in figures:
            if re.match(r"^[a-z]+://", src, re.I) or src.startswith("data:"):
                self.warn(f"{ch.path.name}: external image {src[:60]} (notes must use local figures/*.png or *.jpg)")
                continue
            n += 1
            rel = Path(src.replace("\\", "/"))
            f = (self.src / rel).resolve()
            if ".." in rel.parts or not plat.is_under(f, self.src):
                self.error(f"MISSING FIGURE: {ch.path.name}: {src} (path escapes src/)")
                continue
            if not f.is_file():
                self.error(f"MISSING FIGURE: {ch.path.name}: {src} (expected {f})")
                continue
            dst = self.build / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_mtime < f.stat().st_mtime or dst.stat().st_size != f.stat().st_size:
                shutil.copy2(f, dst)
        return n

    def document(self, title: str, body: str, math: int, mermaid: int) -> str:
        return rmd.wrap_document(
            title, body, self.css,
            katex=self.katex() if math else None,
            mermaid_js=self.mermaid() if mermaid else None,
        )

    def metadata(self) -> dict:
        p = self.profile
        return {
            "author": f"{p['title']} class notes",
            "subject": p.get("institution") or p["title"],
            "keywords": f"{p['short']}, {css.TIER_LABEL[self.tier]}",
            "creator": "lecture-notes plugin (markdown-it-py + Chrome)",
        }

    def record(self, kind: str, src, pdf: Path, html: Path, pages: int, bookmarks: int,
               images_expected: int, res: dict, js_used: bool) -> dict:
        e = {
            "kind": kind,
            "src": src if isinstance(src, list) else str(src),
            "pdf": pdf.resolve().as_posix(),
            "name": pdf.name,
            "html": html.resolve().as_posix(),
            "pages": pages,
            "bookmarks": bookmarks,
            "images_expected": images_expected,
            "katex_errors": res.get("katex_errors", 0),
            "mermaid_errors": res.get("mermaid_errors", 0),
            "js_used": js_used,
            "overflow_blocks": res.get("overflow_blocks", []),
            "chrome": str(self.chrome_path) if self.chrome_path else None,
            "built": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }
        self.entries.append(e)
        if e["katex_errors"]:
            self.warn(f"{pdf.name}: {e['katex_errors']} KaTeX error(s) (shown red in the PDF; the audit fails on this)")
        if e["mermaid_errors"]:
            self.warn(f"{pdf.name}: {e['mermaid_errors']} Mermaid diagram(s) failed to render")
        for m in res.get("js_errors", []):
            self.error(f"JS ERROR while printing {pdf.name}: {m}")
        log(f"  {pdf.name}  ({pages} pages, {bookmarks} bookmarks, {pdf.stat().st_size / 1024:.0f} KB"
            f"{', js' if js_used else ''})")
        return e

    # -- per note --------------------------------------------------------
    def build_note(self, br: Browser | None, ch: rmd.Chapter, by_file: dict) -> None:
        ctx = {"mode": "note", "by_file": by_file, "math": bool(self.tier_cfg.get("math"))}
        body, info = rmd.render_chapter(ch, self.md, ctx, self.tier, self.counterpart(ch))
        for u in info["unmapped"]:
            self.warn(f"{ch.path.name}: emoji {u} has no vector stand-in (may embed a Type 3 font)")
        expected = self.check_figures(ch, info["figures"])
        html = self.document(f"{ch.label} {ch.title}", body, info["math"], info["mermaid"])
        hp = self.build / f"{ch.pdf_stem}.html"
        hp.write_text(html, encoding="utf-8", newline="\n")
        if br is None:
            log(f"  {hp.name}")
            return
        out = self.pdf_dir / f"{ch.pdf_stem}.pdf"
        running = ch.title if ch.is_index else f"{ch.label} · {ch.title}"
        js = bool(info["math"] or info["mermaid"])
        res = br.print_html(hp, out, header=css.header_template(running, self.tag),
                            footer=css.footer_template(), js=js)
        pages, ypos, starts = paginate.resolve_pages(out, [ch], 0)
        n, nb = paginate.finalize_pdf(out, [ch], pages, ypos, starts, title=f"{self.prefix} {ch.num} — {ch.title}",
                                      metadata=self.metadata(), contents_page=None, build_dir=self.build)
        self.record("index" if ch.is_index else "note", ch.path, out, hp, n, nb, expected, res, js)

    # -- compendium ------------------------------------------------------
    def build_compendium(self, br: Browser | None, chapters: list[rmd.Chapter], by_file: dict) -> None:
        ctx = {"mode": "compendium", "by_file": by_file, "math": bool(self.tier_cfg.get("math"))}
        parts = [rmd.build_cover(self.profile, self.tier, chapters), rmd.build_contents(chapters)]
        math = mermaid = expected = 0
        for ch in chapters:
            body, info = rmd.render_chapter(ch, self.md, ctx, self.tier, None)
            parts.append(body)
            math += info["math"]
            mermaid += info["mermaid"]
            for u in info["unmapped"]:
                self.warn(f"{ch.path.name}: emoji {u} has no vector stand-in (may embed a Type 3 font)")
            expected += self.check_figures_quiet(ch, info["figures"])
        sub = "Class revision notes" if self.tier == "basic" else "Deep-dive notes"
        sub = (self.profile.get("cover") or {}).get("subtitle") or sub
        title = f"{self.profile['title']} — {sub}"
        html = self.document(title, "".join(parts), math, mermaid)
        hp = self.build / f"{self.prefix}-Compendium-{self.tier}.html"
        hp.write_text(html, encoding="utf-8", newline="\n")
        if br is None:
            log(f"  {hp.name}")
            return
        out = self.pdf_dir / f"{self.prefix}-Compendium-{self.tier}.pdf"
        js = bool(math or mermaid)
        header = css.header_template(title, self.tag)
        footer = css.footer_template()

        res = br.print_html(hp, out, header=header, footer=footer, js=js)            # pass 1: placeholders
        pages, ypos, starts = paginate.resolve_pages(out, chapters, 1)
        hp.write_text(paginate.substitute(html, pages), encoding="utf-8", newline="\n")
        res = br.print_html(hp, out, header=header, footer=footer, js=js)            # pass 2: real numbers
        pages2, ypos, starts = paginate.resolve_pages(out, chapters, 1)
        if pages2 != pages:
            drift = [k for k in set(pages) | set(pages2) if pages.get(k) != pages2.get(k)]
            log(f"  ! pagination drifted on {len(drift)} anchors; reprinting with pass-2 numbers")
            hp.write_text(paginate.substitute(html, pages2), encoding="utf-8", newline="\n")
            res = br.print_html(hp, out, header=header, footer=footer, js=js)
            pages2, ypos, starts = paginate.resolve_pages(out, chapters, 1)
        n, nb = paginate.finalize_pdf(out, chapters, pages2, ypos, starts, title=title,
                                      metadata=self.metadata(), contents_page=2, build_dir=self.build)
        self.record("compendium", [str(c.path) for c in chapters], out, hp, n, nb, expected, res, js)

    def check_figures_quiet(self, ch, figures) -> int:
        """Figures were already checked/staged by the per-note pass; in --compendium-only runs do it here."""
        return self.check_figures(ch, figures)

    # -- manifest --------------------------------------------------------
    def write_manifest(self) -> Path:
        """Merge this build's entries into the shared ``manifest.json``. Concurrent per-note builds (``/notes:update``
        in parallel) each read-modify-write it, so the merge runs under a lock file and lands via an atomic replace."""
        mp = self.build / "manifest.json"
        lock = self.build / "manifest.json.lock"
        t0 = time.time()
        while True:
            try:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 60:      # a crashed holder
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > 90:
                    lock.unlink(missing_ok=True)
                time.sleep(0.2)
        try:
            old: dict = {}
            if mp.is_file():
                try:
                    old = json.loads(mp.read_text(encoding="utf-8-sig"))
                except Exception:
                    old = {}
            keep = [e for e in old.get("pdfs", []) if e.get("name") not in {x["name"] for x in self.entries}
                    and (self.pdf_dir / e.get("name", "")).is_file()]
            doc = {
                "tier": self.tier, "profile": self.profile["id"], "src": self.src.as_posix(),
                "pdf_dir": self.pdf_dir.as_posix(),
                "updated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "chrome": str(self.chrome_path) if self.chrome_path else old.get("chrome"),
                "pdfs": keep + self.entries,
            }
            tmp = self.build / f"manifest.{os.getpid()}.tmp"
            plat.write_json(tmp, doc)
            os.replace(tmp, mp)
        finally:
            lock.unlink(missing_ok=True)
        return mp

    # -- run -------------------------------------------------------------
    def run(self) -> int:
        a = self.args
        if not self.src.is_dir():
            raise Fail(f"source dir not found: {self.src}", 2)
        for d in (self.pdf_dir, self.build):
            try:
                guard.assert_writable(d, self.data_dir)
            except guard.GuardError as e:
                raise Fail(str(e), 2)
        chapters = rmd.discover(self.src, self.prefix, self.tier, warn=self.warn,
                                order=profmod.session_orders(self.profile))
        if not chapters:
            raise Fail(f"no notes found in {self.src}", 2)
        by_file = {c.path.name.lower(): c for c in chapters}
        sel = {norm_selector(x) for x in a.notes} if a.notes else None
        per_note = [c for c in chapters if sel is None or c.num in sel]
        if sel is not None:
            missing = sorted(sel - {c.num for c in chapters})
            if missing:
                raise Fail(f"--notes: no such note(s) {missing}; have {[c.num for c in chapters]}", 2)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.build.mkdir(parents=True, exist_ok=True)

        br: Browser | None = None
        if not a.html_only:
            try:
                self.chrome_path = chromelib.find_chrome(a.chrome, self.data_dir)
            except chromelib.ChromeNotFound as e:
                raise Fail(str(e))
        log(f"profile: {self.profile['id']}  tier: {self.tier}  chrome: {self.chrome_path or '(html only)'}")
        log(f"src: {self.src}  ->  {self.pdf_dir}")
        try:
            if not a.html_only:
                br = Browser(self.chrome_path, self.build)
                br.start()
            if not a.compendium_only:
                log("per-note PDFs:" if br else "html:")
                for ch in per_note:
                    self.build_note(br, ch, by_file)
            if not a.notes_only:
                log("compendium:")
                self.build_compendium(br, chapters, by_file)
        except ChromeError as e:
            raise Fail(f"Chrome failure: {e}")
        finally:
            if br:
                br.close()
        if a.prune:
            self.prune(chapters, sel)
        if not a.html_only:
            self.write_manifest()
        return 1 if self.errors else 0

    # -- prune (PLAN 13.4) -----------------------------------------------
    def prune(self, chapters, sel) -> None:
        """Delete stale per-note PDFs and unreferenced ``NN-*`` figures. Scoped to ``sel`` when given."""
        keep = {c.pdf_stem + ".pdf" for c in chapters}
        pat = re.compile(rf"^{re.escape(self.prefix)}-(\d+[a-z]?)-.+-{self.tier}\.pdf$")
        scope = None if sel is None else {c.num for c in chapters if c.num in sel}
        if self.pdf_dir.is_dir():
            for f in sorted(self.pdf_dir.glob("*.pdf")):
                m = pat.match(f.name)
                if not m or f.name in keep or "-00-index-" in f.name:
                    continue
                num = m.group(1)
                num = str(int(re.match(r"\d+", num).group(0))).zfill(2) + re.sub(r"^\d+", "", num)
                if scope is not None and num not in scope:
                    continue
                try:
                    guard.assert_writable(f, self.data_dir)
                    f.unlink()
                    log(f"  pruned stale PDF {f.name}")
                    self.pruned.append(f.name)
                except (OSError, guard.GuardError) as e:
                    self.warn(f"could not prune {f.name}: {e}")
        figs = self.src / "figures"
        if figs.is_dir():
            used: set[str] = set()
            for c in chapters:
                used.update(Path(x).name for x in re.findall(r"figures/([^\s)\"']+)", c.path.read_text(encoding="utf-8-sig")))
            nums = {c.num for c in chapters if not c.is_index} if scope is None else scope   # sessions with a note
            for f in sorted(figs.iterdir()):
                m = re.match(r"^(\d+[a-z]?)-", f.name)
                if not f.is_file() or not m or f.name in used:
                    continue
                num = str(int(re.match(r"\d+", m.group(1)).group(0))).zfill(2) + re.sub(r"^\d+", "", m.group(1))
                if num not in nums:
                    continue
                try:
                    guard.assert_writable(f, self.data_dir)
                    f.unlink()
                    log(f"  pruned unreferenced figure {f.name}")
                    self.pruned.append(f"figures/{f.name}")
                except (OSError, guard.GuardError) as e:
                    self.warn(f"could not prune {f.name}: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a tier's markdown notes to PDF (per note + compendium).")
    ap.add_argument("--project", help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", help="profile id, alias or path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--tier", required=True, choices=TIERS)
    ap.add_argument("--src", help="markdown dir (default: <project>/notes/<tier>)")
    ap.add_argument("--pdf-dir", help="PDF output dir (default: <project>/pdf/<tier>)")
    ap.add_argument("--build-dir", help="work dir (default: <project>/.notes-plugin-internal/work/build/<tier>)")
    ap.add_argument("--notes", nargs="+", metavar="NN", help="per-note PDFs only for these notes (02 02a ...); "
                    "the compendium always covers every note unless --notes-only")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--notes-only", action="store_true", help="skip the compendium")
    g.add_argument("--compendium-only", action="store_true", help="skip the per-note PDFs")
    ap.add_argument("--prune", action="store_true",
                    help="delete stale PDFs (no note produces them) and unreferenced NN-* figures")
    ap.add_argument("--html-only", action="store_true", help="write build/*.html only (no Chrome)")
    ap.add_argument("--chrome", help="path to Chrome/Edge/Chromium")
    ap.add_argument("--data-dir", help="plugin data dir (KaTeX/Mermaid cache lives in <data>/cache/assets)")
    ap.add_argument("--offline", action="store_true", help="never download: fail clearly if KaTeX/Mermaid are needed but not cached")
    ap.add_argument("--json", metavar="OUT", help="write a JSON report here")
    args = ap.parse_args(argv)

    b = None
    code = 0
    msg = ""
    try:
        b = Builder(args)
        code = b.run()
    except Fail as e:
        code, msg = e.code, str(e)
        print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    except (profmod.ProfileError, projlayout.ProjectError) as e:
        code, msg = 2, str(e)
        print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    if b is not None:
        log(f"{'FAIL' if code else 'OK'}: {len(b.entries)} PDF(s), {len(b.errors)} error(s), {len(b.warnings)} warning(s)")
    if args.json:
        try:
            guard.assert_writable(args.json, args.data_dir)
        except guard.GuardError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        doc = {
            "ok": code == 0, "exit": code, "error": msg or None,
            "tier": args.tier, "src": b.src.as_posix() if b else None, "pdf_dir": b.pdf_dir.as_posix() if b else None,
            "errors": (b.errors if b else []) + ([msg] if msg else []),
            "warnings": b.warnings if b else [],
            "pdfs": b.entries if b else [],
            "pruned": b.pruned if b else [],
            "manifest": (b.build / "manifest.json").as_posix() if b else None,
        }
        plat.write_json(args.json, doc)
    return code


if __name__ == "__main__":
    sys.exit(main())
