---
name: update
description: Bring the whole subject project up to date after new lecture files land in resources/ (transcripts, recordings, slides, handouts, data, exercises, instructor code). Plans what is new, changed or incomplete, in course order, shows the plan, then generates only that work with several sessions running concurrently (--parallel K, default 4), never duplicates a note, never touches up-to-date sessions, and rebuilds the index and compendium once. Manual workflow — run as /notes:update.
argument-hint: "[--parallel K] [--video auto|reuse|skip] [--tier both|basic|advanced] [--sessions 3,5] [--check-code] [--dry-run] [--yes] [--keep-work]"
disable-model-invocation: true
allowed-tools: Bash(uv run *) Read Glob Grep Agent AskUserQuestion
---

# /notes:update

Arguments: `$ARGUMENTS`

The term-long entry point. The user drops new files in `resources/` whenever a lecture happens, then runs
this. You plan with a script, confirm once, then run **whole sessions concurrently**: each session is one
`notes:session-runner` agent that follows `${CLAUDE_PLUGIN_ROOT}/reference/lecture-flow.md` in batch mode and
dispatches its own stage agents (update, then session-runner, then stage agents: depth 2). You write no notes
yourself and you do not run the per-session flow in your own thread.

## Rules

- Commands are `uv run` with quoted paths, one per Bash call, no `&&`, pipes or `$()`. `${CLAUDE_PLUGIN_ROOT}`
  and `${CLAUDE_PLUGIN_DATA}` are substituted into this text, not shell variables: keep them in the quoted command.
- Work end to end after the confirmation; no mid-way check-ins. **Every Agent call is foreground
  (`run_in_background: false`)**; never end the turn until step 7 is done. In headless runs the session ends
  the moment you stop calling tools and kills anything still running.
- **Never regenerate an up-to-date session.** A deleted, archived recording never makes a session changed.
- **Never delete a recording headless** unless the profile says `video.delete_after_ingest: always`.
- **Failure isolation.** One session failing never stops the others: it is recorded `failed` in the ledger
  (the runner does this; you do it if the runner died) and the next `/notes:update` retries it.
- The project is the working directory (or its first parent holding `.notes-plugin-internal/profile.md`). A
  legacy `.notes/` folder is migrated automatically by the scripts (they print what they did); report it.
  No project: tell the user to run `/notes:init --profile <id>` (or `/notes:init --from-syllabus <pdf>` for a
  subject with no profile) and stop.
- Shared files are written only by you, once, at the end: tier indexes, compendium, `plan.json`, the guard.
  Runners use `qa.py --no-rollup`, the ledger lock and session-specific paths.

## Step 1. Parse

`--parallel K` (default 4; K sessions run at once, each with at most 3 stage agents, so 4 stays far under the
cap of 20), `--tier` (default both), `--sessions 3,5`, `--check-code` (one GitHub call: flag sessions whose
instructor code moved on), `--dry-run` (plan only), `--yes` (skip the question), `--keep-work` (pass to runners), `--video auto|reuse|skip`
(default `auto`; `reuse` or `skip` = no video processing at all: step 4 launches nothing and every task card
carries that value). When the environment has `NOTES_NO_VIDEO` set, treat `auto` as `reuse`: the video scripts
refuse to run (exit 4) and `archive_video.py` keeps every recording.

## Step 2. Plan

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/plan_updates.py" --project "<P>" --tiers <tier> [--check-code] --json "<P>/.notes-plugin-internal/work/reports/plan.json"`

It scans `resources/`, **appends** rows to the profile for lectures it does not know yet (title and date from
the transcript header or file name, `order` from the date; existing rows are never rewritten), adopts notes
that pre-date the ledger, and classifies every session in course order. Read the JSON.

| Status | Action |
| --- | --- |
| `new` | `write` |
| `changed` (reasons name the inputs, e.g. "slides: L5_.pdf added") | `revise` |
| `incomplete` (last run failed or partial, or a tier missing) | `revise` |
| `orphan` (a note with no ledger entry) | adopted by the script, then compared; usually `up-to-date` or `changed` |
| `code-changed` (`--check-code`) | `revise` |
| `up-to-date`, `no-inputs` | skip |
| `course-docs-changed` | informational: syllabus, handouts, data changed, nothing regenerates |

Apply `--sessions` as a filter on the rows.

## Step 3. Show the plan and confirm

**Files outside `resources/`** (`outside_resources` in `plan.json`, or the "Files outside resources/" block the
script printed): discovery only reads `resources/`, so these are ignored. When the list is non-empty:
- **Interactive:** ask ONCE with `AskUserQuestion` ("N class file(s) are outside resources/ and would be ignored.
  Move them?") with options "Move them into resources/ (Recommended)" and "Leave them". On the first run
  `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/scan_resources.py" --project "<P>" --fix-layout --confirm`, relay what it
  moved or refused (a name clash is refused, never overwritten), then re-run the step so the files are seen.
- **Headless, `--yes`, or `AskUserQuestion` unavailable or unanswered:** do NOT move anything. List the files in
  the report with the exact move command above.
Do this before the plan question; if files were moved, re-run step 2 first. `--dry-run` only lists them.

Print one table in course order: session, title, status, reasons, action, whether it has a recording to ingest.
Print any profile rows the script appended ("check the title") and the migration note. Nothing to do: say
"everything is up to date" and stop. With `--dry-run` stop here.

Interactive and no `--yes`: `AskUserQuestion` **once** ("Generate N new and M changed sessions, K at a time:
...?", options Go ahead / Cancel). Headless or `--yes`: proceed.

Then, once: the doctor (`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --data-dir "${CLAUDE_PLUGIN_DATA}" --json
"${CLAUDE_PLUGIN_DATA}/doctor.json"`; exit 1 means no browser: run `install_chromium.py`, re-run, and stop if
still none) and the guard once for the whole batch:
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/guard.py" set --project "<P>" --data-dir "${CLAUDE_PLUGIN_DATA}" --run "update-${CLAUDE_SESSION_ID}"`.
The guard is cleared in step 6 on every exit path.

## Step 4. Launch all video ingests first (background, serialised by the GPU lock)

For every planned session whose resolved inputs hold a recording that still needs ingesting (no finished
`<S>/video/pipeline.json`, `video_action` `run`, `run-force-whisper`, `frames-only` or `whisper-only`; get it
from `resolve_inputs.py --session <n> --tiers <tier> --video auto --dry-run --data-dir "${CLAUDE_PLUGIN_DATA}"`
and its `inputs.json`), start the pipeline command from step 4 of `reference/lecture-flow.md` with the Bash
tool and **`run_in_background: true`**. This is the only background work you start. `pipeline.py` takes
`<data>/locks/gpu.lock`, so the jobs queue and run one GPU job at a time, in course order of launch. Sessions
without a recording need none of this and start at once. Mark `video` timings per session as the flow does.
A session-runner whose ingest is still running simply waits on the same lock (its `--reuse` run resumes or
finds the work done); you never block step 5 on the ingests.

## Step 5. Run sessions K at a time

Keep a queue of the sessions to run in course order. Sessions that need no video and sessions whose ingest has
finished go first; sessions still waiting for the GPU go last (they would just wait inside the runner).

Dispatch **up to K `notes:session-runner` Agent calls in ONE message** (foreground, `run_in_background:
false`), so they run concurrently, each with this card:

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
data_dir: ${CLAUDE_PLUGIN_DATA}
project: <P>
session: <n>
tiers: <tier>
video: <--video value, default auto>
keep_work: <true|false>
today: <YYYY-MM-DD>
```

If the profile has `models.session_runner`, pass it as the Agent `model`. When the message returns, every
runner in it has finished (the call blocks). Record each return (status, paths, minutes). Then `Glob` that
session's notes; confirm the ledger shows it (`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" show --project
"<P>" --session <n> --json`); a runner that died or returned nothing is recorded by you as `failed`
(`ledger.py record --status failed --mode <write|revise>` per tier). Refill: send the next message with the
next up to K queued sessions. Repeat until the queue is empty. A message holds fewer than K calls only for the
last wave. Prior-notes context for a runner is whatever earlier-in-order notes exist at its dispatch time;
`/notes:sweep` reconciles continuity afterwards (say so in the report).

Background ingests that are still running when the queue is empty: start any session left waiting on them now
(foreground); a failed ingest means that session runs transcript-only or fails by the flow policy.

## Step 6. Roll up once, then archive

Clear the guard first: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/guard.py" clear --data-dir "${CLAUDE_PLUGIN_DATA}"`
(also on every earlier exit path). Then per requested tier, after the last session:

- `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/update_index.py" --project "<P>" --tier <tier>`
- `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/build_pdf.py" --project "<P>" --tier <tier> --compendium-only --prune --data-dir "${CLAUDE_PLUGIN_DATA}"`

(`--prune` removes PDFs and generated figures that no note references; it never touches another session files.)
Index and compendium list each session once, in course order, with prev/next links from the builder.
Then archive recordings once, per the profile `video.delete_after_ingest`:
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/archive_video.py" --all --dry-run --json "${CLAUDE_PLUGIN_DATA}/archive-plan.json"`,
and act: `never` keep; `always` run with `--all --confirm`; `ask` interactive `AskUserQuestion` once, `--confirm`
only on Yes; `ask` headless **never delete**, print the command. Only sessions whose notes are `ok` are archived.

## Step 7. Report

`Write` `<P>/.notes-plugin-internal/work/reports/update-<today>.md` and reply in at most 20 lines:

- the plan table with the final status per session (`ok`, `partial`, `failed`) and per-session minutes
  (`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/timing.py" report --project "<P>" --session <n>`, flag stages above
  1.5 times budget; cost per session where available);
- **total wall clock vs the sum of per-session times** (the concurrency gain);
- the PDFs built (index, compendium per tier, absolute paths), appended profile rows to check, video archive
  result, failures with the retry (`/notes:update` retries `failed` sessions by itself), and `unassigned`
  files from the scan with the fix (rename the file or pin it under `paths`);
- the reminder to run `/notes:sweep` now that new sessions exist (it also resolves the `pending-ref` markers that writers
  left for notes that were not written yet at the time: say how many `concept_map.py` counts).
