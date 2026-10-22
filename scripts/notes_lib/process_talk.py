"""Prose that talks about how a note was made instead of about the subject.

A student reading the notes does not care which commands an agent could run, what the execution policy allowed,
what the digest said or which machine built the PDF. Sentences such as "Representative output (not run on this
machine; kubectl is outside the execution policy)" are noise to them. ``scan(text)`` finds every paragraph (or
callout, list item or table row) that contains such talk, with its exact source text, so ``lint_note`` can fail it
and the sweep can rewrite it in place.

Provenance about the *class* is fine and is not flagged: ``> **Reconstructed:**`` (lost audio), ``> **Repo note:**``,
``> **Class correction:**``, "the instructor's laptop", "the class did not show the output".
"""
from __future__ import annotations

import re

from . import pending

PATTERNS: list[tuple[str, re.Pattern]] = [(name, re.compile(rx, re.I)) for name, rx in (
    ("representative output", r"\brepresentative\b"),
    ("not run here", r"\bnot (?:been )?(?:run|executed|captured|tested)(?: live)? (?:on this|here|live|against|locally|in this)\b"
                     r"|\bnot run here\b|\bnothing in this note was run\b"),
    ("this machine", r"\bon this (?:machine|laptop|computer|pc|host)\b|\bthis (?:laptop|machine)(?:'s)?\b"),
    ("execution policy", r"\bexecution[- ]policy\b|\bnever[- ]execute\b|\bexec(?:ution)?[- ]guard\b|\bthe classifier\b"),
    ("the digest", r"\bthe digest\b|\bdigest (?:row|section|says|also|lists|records)\b"),
    ("the inputs", r"\b(?:not|isn't|aren't) in the inputs\b|\b(?:available|visible) to this note\b"
                   r"|\bthis note's (?:inputs|sources|transcript)\b"),
    ("the pipeline's agents", r"\btask card\b|\b(?:writer|stage|session[- ]runner|qa) agent\b|\bsnippet (?:sandbox|checker)\b"
                              r"|\bcheck_snippets\b|\{run(?: nocheck)?\}"),
    ("what the generator saw", r"\bno (?:frames|keyframes|screen captures|screen recording)\b|\b(?:transcript|recording) (?:only|alone)\b"
                               r"|\bthe transcript shows no\b|\bwas (?:also )?(?:checked|re-?run) (?:on|with) (?:git bash|coreutils|this)\b"),
)]

# "the digest" is also a container image digest (`nginx@sha256:...`, "pin by digest", "update the digest"): a unit
# that talks about image digests is about the subject, not about the lecture digest.
_IMAGE_DIGEST = re.compile(r"sha256|@sha|@DIGEST|\bimage digests?\b|\bby digest\b|\bdigest pin|\bpinn(?:ed|ing)\b", re.I)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_KEEP_INLINE = re.compile(r"`\{run[^`]*\}`")                 # "`{run}` snippet" in prose is process talk too


def _blocks(masked: str) -> list[tuple[int, int]]:
    """``(start_line, end_line)`` (1-based, inclusive) of each prose unit: a paragraph, a callout, a list item or a
    table row. Units are separated by blank lines; list items and table rows are units of their own."""
    lines = masked.split("\n")
    out: list[tuple[int, int]] = []
    start = None
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        new_unit = bool(re.match(r"^\s*([-*+]|\d+[.)])\s+|^\s*\|", ln)) and start is not None and not s.startswith(">")
        if not s:
            if start is not None:
                out.append((start, i - 1))
                start = None
            continue
        if new_unit:
            out.append((start, i - 1))
            start = i
        elif start is None:
            start = i
    if start is not None:
        out.append((start, len(lines)))
    return out


def scan(text: str) -> list[dict]:
    """``[{line, end_line, heading, phrase, replaces, text}]``: every prose unit that talks about the pipeline.
    ``replaces`` is the unit's exact source text (lines joined with ``\\n``), ``text`` the same on one line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    masked = pending.mask_code(text)
    heads = pending._headings(masked)
    src_lines = text.split("\n")
    m_lines = masked.split("\n")
    found = []
    for a, b in _blocks(masked):
        chunk = "\n".join(m_lines[a - 1:b])
        if re.match(r"^\s*#{1,6}\s", chunk) or re.match(r"^\s*>\s*\*\*(Reconstructed|Repo note)\b", chunk, re.I):
            continue
        probe = _KEEP_INLINE.sub(" {run} ", chunk)
        probe = _INLINE_CODE.sub(" ", probe)
        hit = next((name for name, rx in PATTERNS if rx.search(probe)
                    and not (name == "the digest" and _IMAGE_DIGEST.search(chunk))), None)
        if not hit:
            continue
        replaces = "\n".join(src_lines[a - 1:b])
        found.append({"line": a, "end_line": b, "heading": pending._heading_at(heads, a), "phrase": hit,
                      "replaces": replaces, "text": " ".join(replaces.split())})
    return found
