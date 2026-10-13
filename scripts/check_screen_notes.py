# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Check that screen notes account for every keyframe: the guard against silently dropping content.

    uv run check_screen_notes.py <screen-notes dir> --frames-json F [--json OUT]

Every frame ``n`` of ``frames.json`` must be either a ``## F###`` heading in some ``*.md`` of the directory
(a read frame) or listed in a shard's front matter ``skipped: [{n, reason}]`` with a non-empty reason.
Errors (exit 1): a frame that is neither; a skipped entry without a reason; a heading for a frame that is
not in frames.json. Warnings: a frame both read and skipped; a frame read in two shards; front matter
``read`` disagreeing with the headings; a read section with almost no content; a detail frame not marked
``detail-of-F###``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, notefile, platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()

HEAD_RE = re.compile(r"^F(\d+)\b(.*)$")


def ranges(nums: list[int]) -> str:
    nums = sorted(nums)
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(f"F{nums[i]:03d}" if i == j else f"F{nums[i]:03d}-F{nums[j]:03d}")
        i = j + 1
    return ", ".join(out)


def load_frames(path: Path) -> dict[int, dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("frames", data.get("items", []))
    return {int(f["n"]): f for f in data}


def check(dirpath: Path, frames: dict[int, dict]) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    read: dict[int, list[str]] = {}
    skipped: dict[int, str] = {}
    headings_by_file: dict[str, list[int]] = {}
    thin: list[int] = []
    files = sorted(p for p in dirpath.glob("*.md") if p.is_file())
    if not files:
        errors.append({"code": "no-shards", "msg": f"no *.md screen-note files in {dirpath}"})
    for p in files:
        try:
            meta, body = prof.split_front_matter(p.read_text(encoding="utf-8-sig"))
        except prof.ProfileError as e:
            errors.append({"code": "front-matter", "msg": f"{p.name}: {e}"})
            continue
        for s in meta.get("skipped") or []:
            if not isinstance(s, dict) or "n" not in s:
                errors.append({"code": "skip-format", "msg": f"{p.name}: skipped entry {s!r} needs `n` and `reason`"})
                continue
            n, reason = int(s["n"]), str(s.get("reason") or "").strip()
            if not reason:
                errors.append({"code": "skip-no-reason", "msg": f"{p.name}: F{n:03d} is skipped without a reason"})
            skipped[n] = reason
        blocks = list(notefile.iter_blocks_ex(body))
        heads = [(i, t, ln) for i, (k, t, info, ln) in enumerate(blocks) if k == "heading" and info == 2]
        nums = []
        for idx, (bi, text, ln) in enumerate(heads):
            m = HEAD_RE.match(text.strip())
            if not m:
                continue
            n = int(m.group(1))
            nums.append(n)
            read.setdefault(n, []).append(p.name)
            tail = m.group(2).lower()
            end = heads[idx + 1][0] if idx + 1 < len(heads) else len(blocks)
            content = " ".join(b[1] for b in blocks[bi + 1:end])
            if len(content.strip()) < 20:
                thin.append(n)
            f = frames.get(n)
            if f and f.get("detail_of") and "detail-of" not in tail:
                warnings.append({"code": "detail-unmarked", "msg": f"F{n:03d} is a detail crop of "
                                 f"F{int(f['detail_of']):03d} but its heading does not say `detail-of-F###`"})
        headings_by_file[p.name] = nums
        listed = meta.get("read")
        if isinstance(listed, list) and sorted(int(x) for x in listed) != sorted(nums):
            warnings.append({"code": "read-mismatch", "msg": f"{p.name}: front matter `read` differs from its "
                             f"{len(nums)} F### headings"})

    total = sorted(frames)
    missing = [n for n in total if n not in read and n not in skipped]
    if missing:
        errors.append({"code": "frames-unaccounted", "msg": f"{len(missing)} frame(s) are neither read nor "
                       f"skipped with a reason: {ranges(missing)}"})
    unknown = sorted((set(read) | set(skipped)) - set(frames))
    if unknown:
        errors.append({"code": "unknown-frame", "msg": f"screen notes mention frame(s) not in frames.json: "
                       f"{ranges(unknown)}"})
    both = sorted(set(read) & set(skipped))
    if both:
        warnings.append({"code": "read-and-skipped", "msg": f"frame(s) both read and skipped: {ranges(both)}"})
    dup = sorted(n for n, fs in read.items() if len(fs) > 1)
    if dup:
        warnings.append({"code": "duplicate-read", "msg": f"frame(s) read in more than one place: {ranges(dup)}"})
    if thin:
        warnings.append({"code": "thin-section", "msg": f"read frame(s) with almost no content: {ranges(thin)}"})
    return {"total": len(total), "read": sorted(n for n in read if n in frames),
            "skipped": sorted(n for n in skipped if n in frames), "missing": missing, "unknown": unknown,
            "errors": errors, "warnings": warnings, "files": [p.name for p in files]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--frames-json", required=True)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    d, fj = plat.norm(a.dir), plat.norm(a.frames_json)
    if not d.is_dir() or not fj.is_file():
        print(f"check_screen_notes: need an existing directory and frames.json ({d}, {fj})", file=sys.stderr)
        return 2
    r = check(d, load_frames(fj))
    for tag, issues in (("ERROR", r["errors"]), ("warn ", r["warnings"])):
        for i in issues:
            print(f"  {tag} {i['code']:<18} {i['msg']}")
    print(f"\nscreen notes: {r['total']} frames, {len(r['read'])} read, {len(r['skipped'])} skipped, "
          f"{len(r['missing'])} unaccounted -> {'FAIL' if r['errors'] else 'ok'}")
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": not r["errors"], **r})
    return 1 if r["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
