# Per-stage timing and budgets

Targets, wall clock from the start of `/notes:lecture`, both tiers in parallel: **Basic 25 min, Advanced 30 min**.

## Marking

`uv run "<plugin>/scripts/timing.py" mark --project "<P>" --session <n> --stage <name> (start|end) [--note TEXT]`
appends to `work/sessions/NN/timings.jsonl`. `timing.py report --project "<P>" --session <n>` prints the stage
table and per-tier totals. The orchestrator marks `start` before and `end` after each stage (one call each);
the stages that run in parallel (research pair, writer pair, per-tier QA) are marked with their own names.

| Stage name | Covers | Budget (min) |
| --- | --- | --- |
| `startup` | doctor, resolve, guard | 2 |
| `video` | Whisper + keyframes pipeline | 6 |
| `frames` | frame-reader shards | 4 |
| `digest` | digest-builder | 8 |
| `research-verify` | researcher `verify` | 3 |
| `research-deep` | researcher `deep` | 6 |
| `writer-basic` | basic-writer | 8 |
| `writer-advanced` | advanced-writer | 13 |
| `figures-basic`, `figures-advanced` | class crops + figure-author | 3 each |
| `qa-basic`, `qa-advanced` | note-qa fast loop + one full run | 3 each |
| `build` | final `qa.py` | 1 |
| `archive` | `archive_video.py` | 1 |

The final report includes the table and flags any stage over **1.5 times** its budget with the likely cause
(a long loop, a missing GPU, a slow search).

## Where the time goes (and what the design does about it)

| Bottleneck | Fix in the flow |
| --- | --- |
| QA loop (Advanced, many iterations) | `qa.py --fast` (no Chrome), all mechanical fixes in one pass, at most 2 fast iterations then 1 full run, hard cap 3 full runs |
| One long research call | `verify` (about 2 min) and `deep` dispatched in ONE message |
| Writers waiting on each other | both writers in ONE message, after the research pair |
| Re-compiling unchanged snippets | snippet cache in `work/build/<tier>/snippets/cache.json` |

## `/notes:update` batch mode

`--parallel K` (default 2). Phase A (video, frames, digest, research) for up to K sessions at a time, the video
pipeline one at a time; Phase B (writers, figures, QA, ledger) strictly in course order. See
`skills/update/SKILL.md` for which parts the harness can actually overlap.
