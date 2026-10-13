# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Check an advanced note's citations against the research dossier and the profile's source rules.

    uv run check_citations.py <note.md>... --research R [R2 ...] --profile REF [--online] [--json OUT]

Per note (URLs are taken from prose, lists, tables and headings, never from code fences):

  errors   fewer distinct URLs than ``min_sources`` or distinct domains than ``min_domains``; a
           ``research.required_domains`` entry never cited; ``verified_as_of`` missing from the front
           matter; more than 20% of the cited URLs absent from the research source register (section G)
  warnings some (<= 20%) cited URLs absent from section G (``unknown_urls``); with --online, URLs that do
           not answer (HEAD, then GET, 8 s timeout) - network trouble is never an error

URL identity ignores scheme case, ``www.``, a trailing slash and the #fragment. Exit 1 on any error.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, layout, notefile, platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()

URL_RE = re.compile(r"https?://[^\s)>\]\"'`<|]+", re.I)
TRAIL = ".,;:!?*_"


def clean_url(u: str) -> str:
    u = u.rstrip(TRAIL)
    while u.endswith(")") and u.count(")") > u.count("("):
        u = u[:-1]
    return u


def norm_url(u: str) -> str:
    p = urlsplit(clean_url(u))
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")


def domain_of(u: str) -> str:
    host = (urlsplit(clean_url(u)).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def note_urls(note: notefile.Note) -> list[str]:
    seen, out = set(), []
    for kind, text, _info in notefile.iter_blocks(note.body):
        if kind == "code":
            continue
        for m in URL_RE.finditer(text):
            u = clean_url(m.group(0))
            k = norm_url(u)
            if k not in seen:
                seen.add(k)
                out.append(u)
    return out


def source_register_many(files: list[Path]) -> tuple[set[str], bool]:
    """Union of section G over several research files (research-verify.md + research-deep.md, PLAN 14.2;
    a single research.md is the one-file case). has_g is True if any file has a section G."""
    urls: set[str] = set()
    found = False
    for f in files:
        u, g = source_register(f)
        urls |= u
        found = found or g
    return urls, found


def source_register(research: Path) -> tuple[set[str], bool]:
    """(normalised URLs of section G, whether a section G was found)."""
    text = research.read_text(encoding="utf-8-sig")
    _, body = prof.split_front_matter(text)
    cur_in_g, found, urls = False, False, set()
    for kind, t, info, _ln in notefile.iter_blocks_ex(body):
        if kind == "heading" and info == 2:
            cur_in_g = bool(re.match(r"^\s*G[\s.:)\-–—]", t) or "source register" in t.lower())
            found = found or cur_in_g
            continue
        if cur_in_g:
            urls.update(norm_url(m.group(0)) for m in URL_RE.finditer(t))
    if not found:                                    # fall back to every URL in the file
        urls = {norm_url(m.group(0)) for m in URL_RE.finditer(body)}
    return urls, found


def probe(url: str) -> str | None:
    """None if the URL answers, else a short reason."""
    hdr = {"User-Agent": "Mozilla/5.0 (lecture-notes check_citations)"}
    last = "no answer"
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=hdr)
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status < 400:
                    return None
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:                       # noqa: BLE001 - offline, DNS, TLS, timeout
            last = type(e).__name__
    return last


def required_hit(req: str, urls: list[str]) -> bool:
    r = req.lower().strip().strip("/")
    for u in urls:
        if "/" in r:
            if norm_url(u).startswith(r):
                return True
        else:
            d = domain_of(u)
            if d == r or d.endswith("." + r):
                return True
    return False


def check_note(path: Path, cfg: dict, required: list[str], register: set[str], has_g: bool,
               online: bool) -> dict:
    note = notefile.parse_note(path)
    urls = note_urls(note)
    domains = sorted({domain_of(u) for u in urls})
    errors, warnings = [], []

    def E(code, msg):
        errors.append({"code": code, "msg": msg, "kind": "content"})

    def Wn(code, msg):
        warnings.append({"code": code, "msg": msg, "kind": "content"})

    lo_s, lo_d = int(cfg.get("min_sources", 0)), int(cfg.get("min_domains", 0))
    if len(urls) < lo_s:
        E("sources-low", f"{len(urls)} distinct URLs cited, profile needs at least {lo_s}")
    if len(domains) < lo_d:
        E("domains-low", f"{len(domains)} distinct domains cited ({', '.join(domains) or 'none'}), "
          f"profile needs at least {lo_d}")
    for req in required:
        if not required_hit(req, urls):
            E("required-domain-missing", f"required source domain `{req}` is never cited")
    if not str(note.meta.get("verified_as_of") or "").strip():
        E("verified-missing", "front matter has no `verified_as_of` date")
    unknown = [u for u in urls if norm_url(u) not in register]
    if not has_g:
        Wn("no-source-register", "research file has no section G (source register); compared against "
           "every URL in the file")
    if unknown:
        share = len(unknown) / len(urls)
        (E if share > 0.20 else Wn)("unknown-urls", f"{len(unknown)} of {len(urls)} cited URLs ({share:.0%}) "
                                     "are not in the research source register: " + ", ".join(unknown[:5])
                                     + (" ..." if len(unknown) > 5 else ""))
    dead = []
    if online:
        for u in urls:
            why = probe(u)
            if why:
                dead.append({"url": u, "why": why})
                Wn("url-unreachable", f"{u} -> {why}")
    return {"path": path.as_posix(), "stem": path.stem, "urls": len(urls), "domains": len(domains),
            "domain_list": domains, "url_list": urls, "unknown_urls": unknown, "unreachable": dead,
            "errors": errors, "warnings": warnings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("notes", nargs="+")
    ap.add_argument("--research", required=True, nargs="+",
                    help="research file(s); several = union of their section G (verify + deep)")
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", default=None, help="profile id/path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    profile = prof.load_or_exit(a.profile, data_dir=a.data_dir, project=a.project, notes=a.notes)
    cfg = prof.tier_cfg(profile, "advanced")
    required = list((profile.get("research") or {}).get("required_domains") or [])
    rpaths = [plat.norm(x) for x in a.research]
    for rpath in rpaths:
        if not rpath.is_file():
            print(f"check_citations: research file not found: {rpath}", file=sys.stderr)
            return 2
    register, has_g = source_register_many(rpaths)
    results = [check_note(plat.norm(n), cfg, required, register, has_g, a.online) for n in a.notes]
    ne = sum(len(r["errors"]) for r in results)
    for r in results:
        print(f"{Path(r['path']).name}: {r['urls']} URLs, {r['domains']} domains, "
              f"{len(r['unknown_urls'])} not in section G, {len(r['errors'])} error(s), "
              f"{len(r['warnings'])} warning(s)")
        for tag, issues in (("ERROR", r["errors"]), ("warn ", r["warnings"])):
            for i in issues:
                print(f"  {tag} {i['code']:<24} {i['msg']}")
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": ne == 0, "error_count": ne, "research": [r.as_posix() for r in rpaths],
                                 "register_urls": len(register), "notes": results})
    return 1 if ne else 0


if __name__ == "__main__":
    raise SystemExit(main())
