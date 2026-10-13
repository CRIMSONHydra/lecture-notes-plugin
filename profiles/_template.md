---
# Annotated empty profile. /notes:init copies it to <project>/.notes-plugin-internal/profile.md.
# Change the values, delete the comments you do not need. Schema and rules: profiles/README.md.
schema: 1                        # required; always 1
id: example                      # required; [a-z0-9-]+  (the name used on the command line)
aliases: []                      # other accepted names, e.g. [ex, example-course]
title: "Example Course Title"    # required; full title for covers
short: "Example · Track"         # required; running header tag in the PDF
prefix: EXA                      # required; [A-Z0-9]+, PDF filename prefix (EXA-02-slug-basic.pdf)
institution: "Institution, batch, term, specialisation"
spelling: en-GB                  # en-GB | en-US | any  (lint hint only)
# resources_dir: "resources"     # optional; default "resources" inside the project. Absolute = external, read-only
# prior_notes_external: []       # optional absolute dirs of legacy notes (read-only): cross-links, knowledge ledger
paths:                           # relative to resources_dir; {n} {nn} {stem} {title}; globs ok; list = alternatives
                                 # list ONLY what differs from the defaults:
                                 #   transcripts "{stem}.txt" · recordings "videos/{stem}.mp4"
                                 #   frames "frames/{stem}" · whisper "{stem}.whisper.json"
                                 #   slides [] · handouts [] · classwork null
  slides: ["L{n}.pdf", "L{n}_.pdf"]
  handouts: ["*.docx"]                         # optional course-wide documents
  syllabus: "Syllabus*.pdf"
# instructor_code: {kind: github, repo: "owner/name", ref: HEAD, session_dir: "Lec-{n}",
#                   ignore: ["*.o", "*.out", "*.exe", "*.class", "*.swp"]}   # omit when there is none
sessions:                        # OPTIONAL (delete the whole block to derive sessions from resources/); the course map; stem = transcript stem; date wins over the transcript header
  - {n: 1, title: "First session title", stem: "First_session_title_-_Class_Scaler_Academy", date: 2026-01-05}
  - {n: 2, title: "Second session title"}      # title only until its transcript lands
  # optional per row: order: 1.5  (display position; default = sort by date, else n). /notes:update appends new lectures here itself
asr: {language: en, hotwords: "domain terms the Whisper prompt should know"}
snippets:                        # templates for check_snippets: {src} {exe} {dir}
  python: {ext: .py, compile: null, run: "uv run {src}"}
tiers:
  basic:
    words: [3500, 7000]          # prose words for a ~2 h class (scaled by duration_min/120); closing sections
                                 # excluded. A floor-first guide: under it warns "too thin", over never fails
    minutes: [60, 120]           # informational only: prose/150 + code_lines*8s + table_rows*15s
    figures: class+generated     # none | optional | expected | class | class+generated
    gapfill_max_share: 0.30
    diagrams: ascii              # ascii | mermaid-ok
    math: false
    check_yourself: [5, 8]
    required_sections: ["TL;DR", "Cheat sheet", "Check yourself", "Going Deeper"]
    closing_sections: ["Going Deeper"]
  advanced:
    words: [6000, 15000]
    figures: class+generated     # none | optional | expected | class | class+generated
    min_figures: 2
    diagrams: mermaid-ok
    math: true
    min_sources: 8
    min_domains: 4
    required_sections: ["What you need before this note", "TL;DR", "One-screen summary",
                        "New words in this note", "Check yourself", "Hands-on", "Further Study", "Sources"]
    closing_sections: ["Further Study", "Sources"]
research:
  preferred_sources: ["docs.example.org"]      # domains to prefer, primary first
  required_domains: []                         # domains every Advanced note must cite at least once
callouts: {}                     # extra labels, e.g. {"Exam tip": tip}; merged with the built-ins
branding: {accent: "#1f4e79", accent2: "#a3521a"}
video: {delete_after_ingest: ask}   # ask | never | always: after ingest, archive frames + Whisper into
                                     # resources/ and delete the recording (asks first; never when headless)
models: {}                       # optional per-agent overrides, e.g. {advanced_writer: opus}
---

# Profile: Example Course Title

> Template. Keep these H2 titles and this order; agents look for them. Replace every line of guidance.

## Role

Who writes these notes: the persona (senior practitioner of what), what the job is in the Basic tier
(the whole class re-taught at class depth, with its board drawings) and in the Advanced tier (teaching from
first principles).

## Reader

Who reads them (year, track, prior knowledge). **List what may be assumed (the Knowledge Ledger
baseline) and what must not be.** Everything else gets built inside the notes.

## Course

| | |
| --- | --- |
| **Course** | title, institution, credits |
| **Format** | number of sessions, length, live/hybrid, instructor style |
| **Assessment** | weights (mark unconfirmed ones as unconfirmed) |

| # | Session title | Core topics |
| --- | --- | --- |
| 1 | First session title | topics from the syllabus |

Cross-link forward ("this gets its own note in session N") instead of duplicating a later topic.

## Source handling

Where instructor code comes from and how to quote it; extra source-precedence rules; what to verify
with (named primary sources); platforms the class uses.

## Basic tier — subject rules

Depth contract (the whole class at class depth, every board drawing) and the word guide for this subject;
the "what earns space" list; Check-yourself shapes the class quizzes in; the
comparison tables to keep; what always goes to the closing section.

## Advanced tier — subject rules

Knowledge Ledger baseline; the per-concept ladder for this subject; figures expected; primary sources;
interview angles.

## Domain conventions

Real names and numbers to use, units and formatting, platform deltas, width limits.

## ASR corrections

| Transcript | Correct |
| --- | --- |
| runs of "you you you", "Thank you. Thank you." | **dropped audio**: check Whisper and frames, else reconstruct and mark it |

## Callouts

The callout labels this subject uses beyond the built-ins (see `reference/note-format.md`).

## Known context

Unconfirmed facts, resource naming patterns, caveats about the sources.
