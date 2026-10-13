"""The ledger ``<project>/.notes-plugin-internal/state.json`` (PLAN 13.2). Stdlib only.

It remembers, per session, which content inputs a note was generated from (fingerprints), the fixed note
slug, and per tier the note/pdf paths and the status of the last run. ``plan_updates.py`` compares it with
the current inputs to decide new / changed / incomplete / up-to-date.

Fingerprints cover content inputs only: transcript, whisper.json, frames.json (+ count), slides, session
handouts, classwork, and (recorded, never compared here) the instructor-code ``tree_sha``/``dir_sha``. A
recording counts only through the frames and Whisper it produced, so deleting an archived video changes
nothing. Hashes are of file *content*, so moving or copying a file does not look like a change.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path

from . import notefile, platform as plat
from .lock import FileLock

SCHEMA = 1
TIERS = ("basic", "advanced")


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def plugin_version() -> str:
    try:
        pj = plat.plugin_root() / ".claude-plugin" / "plugin.json"
        return str(json.loads(pj.read_text(encoding="utf-8-sig")).get("version", ""))
    except (OSError, ValueError):
        return ""


def empty_state() -> dict:
    return {"schema": SCHEMA, "plugin_version": plugin_version(), "updated": now_iso(),
            "course_docs": {}, "sessions": {}}


def load(lay) -> dict:
    try:
        st = json.loads(Path(lay.state_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return empty_state()
    if not isinstance(st, dict) or not isinstance(st.get("sessions"), dict):
        return empty_state()
    st.setdefault("course_docs", {})
    return st


def locked(lay, timeout: float | None = 120.0) -> FileLock:
    """``with ledger.locked(lay): st = load(lay); ...; save(lay, st)`` - the cross-process lock
    ``<project>/.notes-plugin-internal/state.lock`` (PLAN 16.2). Parallel sessions serialise their writes."""
    return FileLock(Path(lay.internal) / "state.lock", timeout=timeout, stale_after=300.0)


def save(lay, state: dict) -> Path:
    state["schema"] = SCHEMA
    state["plugin_version"] = plugin_version()
    state["updated"] = now_iso()
    p = Path(lay.state_path)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    plat.write_json(tmp, state)
    os.replace(tmp, p)                                 # atomic: a reader never sees half a file
    return p


# ------------------------------------------------------------------ fingerprints
TEXT_EXT = {".txt", ".md", ".vtt", ".srt", ".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".sql", ".yaml", ".yml",
            ".cpp", ".cc", ".c", ".h", ".hpp", ".py", ".java", ".js", ".ts", ".rs", ".go", ".cs", ".kt", ".sh",
            ".s", ".html", ".css", ".xml", ".toml", ".ini", ".cfg", ".rst", ".tex"}
HASH_VERSION = "norm2"      # marker on a fingerprint whose ``sha256`` is line-ending/BOM-normalised for text files


def is_text(p) -> bool:
    return Path(p).suffix.lower() in TEXT_EXT


def sha256_file(p) -> str:
    """sha256 of the raw bytes (what ledgers written before ``norm1`` stored)."""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_text_bytes(b: bytes) -> bytes:
    """UTF-8 BOM removed, CRLF and lone CR turned into LF: the same text hashes the same on every OS."""
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256_all(p) -> tuple[str, str, str]:
    """(normalised sha256, raw sha256, sha256 of the same text with CRLF endings). Binaries: all three equal the
    raw hash. The CRLF form lets a ledger written on Windows from raw CRLF bytes (old format) still match the
    same file checked out with LF on another OS."""
    if not is_text(p):
        h = sha256_file(p)
        return h, h, h
    data = Path(p).read_bytes()
    n = norm_text_bytes(data)
    return (hashlib.sha256(n).hexdigest(), hashlib.sha256(data).hexdigest(),
            hashlib.sha256(n.replace(b"\n", b"\r\n")).hexdigest())


def sha256_pair(p) -> tuple[str, str]:
    """(normalised sha256, raw sha256). They are equal for binaries and for LF-only, BOM-less text."""
    a = sha256_all(p)
    return a[0], a[1]


def sha256_norm(p) -> str:
    return sha256_pair(p)[0]


def same_hash(old: str | None, new_fp: dict) -> bool:
    """An old stored hash equals the new fingerprint: normalised hash, or (legacy ledgers) the raw-bytes hash."""
    return bool(old) and old in (new_fp.get("sha256"), new_fp.get("raw_sha256"), new_fp.get("crlf_sha256"))


def file_fp(p, cache: dict | None = None) -> dict | None:
    """{path,size,mtime_ns,sha256} or None when the file does not exist. ``cache`` maps a posix path to an
    earlier fp; the hash is reused when size and mtime are unchanged."""
    p = Path(p)
    try:
        st = p.stat()
    except OSError:
        return None
    if not p.is_file():
        return None
    key = p.as_posix()
    old = (cache or {}).get(key)
    fresh = not is_text(p) or (old or {}).get("hash") == HASH_VERSION
    if old and old.get("size") == st.st_size and old.get("mtime_ns") == st.st_mtime_ns and old.get("sha256") and fresh:
        sha, raw, crlf = old["sha256"], old.get("raw_sha256") or old["sha256"], old.get("crlf_sha256")
    else:
        sha, raw, crlf = sha256_all(p)
    fp = {"path": key, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha}
    if is_text(p):
        fp["hash"] = HASH_VERSION
        if raw != sha:
            fp["raw_sha256"] = raw
        if crlf and crlf != sha:
            fp["crlf_sha256"] = crlf
    return fp


def _collect_cache(obj, out: dict) -> dict:
    if isinstance(obj, dict):
        if "sha256" in obj and "path" in obj and "mtime_ns" in obj:
            out[obj["path"]] = obj
        for v in obj.values():
            _collect_cache(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_cache(v, out)
    return out


def fingerprints(inputs: dict, prev_entry: dict | None = None) -> dict:
    """Content fingerprints of one session's resolved ``inputs`` (see resolve_inputs)."""
    cache = _collect_cache(prev_entry or {}, {})
    fp: dict = {}
    t = inputs.get("transcript")
    if t and (x := file_fp(t["path"], cache)):
        fp["transcript"] = x
    w = inputs.get("whisper")
    if w and (x := file_fp(w["path"], cache)):
        fp["whisper"] = x
    fr = inputs.get("frames")
    if fr:
        src = fr.get("frames_json") or fr.get("index")
        if src and (x := file_fp(src, cache)):
            x["count"] = fr.get("count")
            fp["frames"] = x
    for key, items in (("slides", inputs.get("slides") or []),
                       ("handouts", [d["path"] for d in inputs.get("handouts") or []])):
        lst = [x for p in items if (x := file_fp(p, cache))]
        lst.sort(key=lambda d: Path(d["path"]).name.lower())
        if lst:
            fp[key] = lst
    cw = inputs.get("classwork")
    if cw:
        files, raw_files, crlf_files = {}, {}, {}
        for rel in cw.get("files") or []:
            f = Path(cw["dir"]) / rel
            if f.is_file():
                files[rel], raw, crlf_h = sha256_all(f)
                if raw != files[rel]:
                    raw_files[rel] = raw
                if crlf_h != files[rel]:
                    crlf_files[rel] = crlf_h
        fp["classwork"] = {"dir": cw["dir"], "count": len(files), "files": files, "hash": HASH_VERSION,
                           "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
        if raw_files:
            fp["classwork"]["raw_files"] = raw_files
        if crlf_files:
            fp["classwork"]["crlf_files"] = crlf_files
    ic = inputs.get("instructor_code")
    if ic:
        code = {"repo": ic.get("repo"), "dir": ic.get("dir"), "tree_sha": None, "dir_sha": None}
        mf = Path(inputs.get("paths", {}).get("session_code", "")) / "manifest.json"
        try:
            m = json.loads(mf.read_text(encoding="utf-8-sig"))
            code.update(tree_sha=m.get("tree_sha"), dir_sha=m.get("dir_sha"))
        except (OSError, ValueError):
            pass
        fp["code"] = code
    return fp


def comparable(fp: dict) -> dict:
    """The part of fingerprints that decides regeneration: content hashes only (no paths, no code)."""
    out: dict = {}
    for k in ("transcript", "whisper"):
        if k in fp:
            out[k] = fp[k]["sha256"]
    if "frames" in fp:
        out["frames"] = [fp["frames"]["sha256"], fp["frames"].get("count")]
    for k in ("slides", "handouts"):
        if k in fp:
            out[k] = sorted(f"{Path(d['path']).name}:{d['sha256']}" for d in fp[k])
    if "classwork" in fp:
        out["classwork"] = fp["classwork"]["sha256"]
    return out


def inputs_hash(fp: dict) -> str:
    return hashlib.sha256(json.dumps(comparable(fp), sort_keys=True).encode()).hexdigest()


def diff(old: dict, new: dict) -> tuple[list[str], bool]:
    """(reasons, trigger). Added or modified inputs trigger regeneration; so does losing the transcript,
    slides, handouts or classwork. Losing whisper/frames does not (they are derived from a recording that
    may have been deleted after archiving)."""
    reasons: list[str] = []
    trigger = False

    def name(d):
        return Path(d["path"]).name

    for k in ("transcript", "whisper", "frames"):
        o, n = old.get(k), new.get(k)
        if n and not o:
            reasons.append(f"{k}: {name(n)} added")
            trigger = True
        elif o and not n:
            if k == "transcript":
                reasons.append(f"{k}: {name(o)} removed")
                trigger = True
        elif o and n and (not same_hash(o["sha256"], n) or (k == "frames" and o.get("count") != n.get("count"))):
            extra = f" ({o.get('count')} -> {n.get('count')} frames)" if k == "frames" and o.get("count") != n.get("count") else ""
            reasons.append(f"{k}: {name(n)} modified{extra}")
            trigger = True
    for k in ("slides", "handouts"):
        o = {name(d): d["sha256"] for d in old.get(k, [])}
        n = {name(d): d for d in new.get(k, [])}
        for f in sorted(n.keys() - o.keys()):
            reasons.append(f"{k}: {f} added")
            trigger = True
        for f in sorted(o.keys() - n.keys()):
            reasons.append(f"{k}: {f} removed")
            trigger = True
        for f in sorted(o.keys() & n.keys()):
            if not same_hash(o[f], n[f]):
                reasons.append(f"{k}: {f} modified")
                trigger = True
    o = (old.get("classwork") or {}).get("files", {})
    ncw = new.get("classwork") or {}
    n = ncw.get("files", {})
    n_raw = ncw.get("raw_files", {})
    n_crlf = ncw.get("crlf_files", {})
    for f in sorted(n.keys() - o.keys()):
        reasons.append(f"classwork: {f} added")
        trigger = True
    for f in sorted(o.keys() - n.keys()):
        reasons.append(f"classwork: {f} removed")
        trigger = True
    for f in sorted(o.keys() & n.keys()):
        if o[f] not in (n[f], n_raw.get(f), n_crlf.get(f)):
            reasons.append(f"classwork: {f} modified")
            trigger = True
    return reasons, trigger


def upgrade(old: dict, new: dict) -> bool:
    """Silently move a stored fingerprint set to the line-ending-normalised hashes when ``old`` only differs from
    ``new`` by a legacy raw-bytes hash (``same_hash`` true through ``raw_sha256``). Mutates ``old``; True if it
    changed. Call only when :func:`diff` found no trigger."""
    changed = False
    for k in ("transcript", "whisper", "frames"):
        o, n = old.get(k), new.get(k)
        if o and n and o["sha256"] != n["sha256"] and same_hash(o["sha256"], n):
            old[k] = dict(n)
            changed = True
    for k in ("slides", "handouts"):
        ol = old.get(k) or []
        nm = {Path(d["path"]).name: d for d in new.get(k) or []}
        for i, d in enumerate(ol):
            n = nm.get(Path(d["path"]).name)
            if n and d["sha256"] != n["sha256"] and same_hash(d["sha256"], n):
                ol[i] = dict(n)
                changed = True
    o, n = old.get("classwork"), new.get("classwork")
    if o and n and o.get("files") != n.get("files"):
        raw, crl = n.get("raw_files", {}), n.get("crlf_files", {})
        if set(o.get("files", {})) == set(n["files"]) and all(
                o["files"][f] in (n["files"][f], raw.get(f), crl.get(f)) for f in n["files"]):
            old["classwork"] = dict(n)
            changed = True
    return changed


# ------------------------------------------------------------------ notes on disk
def note_files(lay, tier: str) -> dict[str, list[Path]]:
    """``{NN: [paths]}`` for ``notes/<tier>/NN[a]-*.md`` (README excluded), the number without companion letter."""
    out: dict[str, list[Path]] = {}
    d = lay.notes(tier)
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            num, _slug = notefile.note_id(f.name)
            m = re.match(r"(\d+)([a-z]?)$", num or "")
            if m:
                out.setdefault(f"{int(m.group(1)):02d}", []).append(f)
    return out


def primary_notes(files: list[Path]) -> list[Path]:
    return [f for f in files if re.match(r"\d+-", f.name)]


def rel(lay, p) -> str:
    try:
        return Path(p).resolve().relative_to(Path(lay.root).resolve()).as_posix()
    except ValueError:
        return Path(p).as_posix()


def pdf_for(lay, profile: dict, tier: str, note: Path) -> Path | None:
    num, slug = notefile.note_id(note.name)
    p = lay.pdf(tier) / notefile.pdf_name(profile["prefix"], num, slug, tier)
    return p


def file_sha(p) -> str | None:
    try:
        return sha256_norm(p)
    except OSError:
        return None


# ------------------------------------------------------------------ recording
def record(lay, profile: dict, inputs: dict, tier: str, status: str, *, qa_ok=None, mode=None,
           adopted: bool = False, state: dict | None = None, save_it: bool = True) -> dict:
    """Snapshot ``inputs`` as the basis of the tier's note. Returns the session entry.

    With no ``state`` given it takes the ledger lock around its own load-modify-save. ``mode="sweep"`` only
    stamps ``swept_at`` on the tier: inputs, ``inputs_hash``, status and generated_at stay as they were, so
    ``plan_updates`` never sees a sweep as a change (PLAN 16.3)."""
    if state is None and save_it:
        with locked(lay):
            return record(lay, profile, inputs, tier, status, qa_ok=qa_ok, mode=mode, adopted=adopted,
                          state=load(lay), save_it=True)
    if mode == "sweep":
        st = state if state is not None else load(lay)
        ent = st["sessions"].setdefault(inputs["session"]["nn"], {"tiers": {}})
        t = ent.setdefault("tiers", {}).setdefault(tier, {})
        t["swept_at"] = now_iso()
        t["sweep_status"] = status
        if save_it:
            save(lay, st)
        return ent
    if status not in ("ok", "failed", "partial"):
        raise ValueError(f"bad status {status!r}")
    st = state if state is not None else load(lay)
    s = inputs["session"]
    nn = s["nn"]
    ent = st["sessions"].setdefault(nn, {"tiers": {}})
    ent.setdefault("tiers", {})
    fps = inputs.get("fingerprints") or fingerprints(inputs, ent)
    ih = inputs.get("inputs_hash") or inputs_hash(fps)
    if status == "ok" and qa_ok is False:
        status = "partial"
    npaths = inputs.get("note_paths") or {}
    note = npaths.get(tier)
    if not note or not Path(note).is_file():
        prim = primary_notes(note_files(lay, tier).get(nn, []))
        note = prim[0].as_posix() if prim else note
    slug = ent.get("slug")
    if note:
        slug = notefile.note_id(Path(note).name)[1]
        pdf = pdf_for(lay, profile, tier, Path(note))
    else:
        pdf = None
    ent.update({"n": s["n"], "title": s.get("title"), "order": (inputs.get("session") or {}).get("order"),
                "slug": slug, "inputs": fps, "inputs_hash": ih})
    dg = Path(inputs.get("paths", {}).get("digest", ""))
    ent["digest_sha256"] = file_sha(dg) if dg.is_file() else ent.get("digest_sha256")
    t = ent["tiers"].setdefault(tier, {})
    t.update({"note": rel(lay, note) if note else None, "pdf": rel(lay, pdf) if pdf else None,
              "status": status, "qa_ok": qa_ok, "generated_at": now_iso(),
              "mode": mode or ("revise" if (inputs.get("existing_notes") or {}).get(tier) else "write"),
              "inputs": fps, "inputs_hash": ih})
    if adopted:
        t["adopted"] = True
    else:
        t.pop("adopted", None)
    docs = {}
    for d in inputs.get("course_docs") or []:
        if (x := file_fp(d["path"])):
            docs[Path(d["path"]).name] = x["sha256"]       # normalised; plan_updates also accepts the raw hash
    if docs:
        st["course_docs"] = docs
    if save_it:
        save(lay, st)
    return ent


def forget(lay, nn: str) -> bool:
    with locked(lay):
        st = load(lay)
        had = st["sessions"].pop(f"{int(nn):02d}", None) is not None
        if had:
            save(lay, st)
    return had
