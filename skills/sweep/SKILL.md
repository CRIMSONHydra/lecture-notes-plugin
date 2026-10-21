---
name: sweep
description: Cross-note continuity pass over a finished (or partly finished) subject project. Maps which concepts each lecture introduces and reuses, then inserts small idempotent edits into the existing notes (builds-on back-references, recaps in Basic, forward links, propagated corrections, terminology aliases, recurring-example links, resolution of pending references to notes that did not exist yet, and scrubbing prose that talks about how the notes were made), rebuilds PDFs, index and compendium, and records the run. Safe to re-run. Manual workflow — run as /notes:sweep after /notes:update.
argument-hint: "[--tier both|basic|advanced] [--sessions 3,5] [--dry-run] [--yes]"
disable-model-invocation: true
allowed-tools: Bash(uv run *) Read Glob Grep Agent AskUserQuestion
---

# /notes:sweep

Arguments: `$ARGUMENTS`

Notes are written per lecture, concurrently, so each one knows only the earlier notes that existed when it was
dispatched. The sweep fixes continuity afterwards, **in place, with small anchored edits**. You orchestrate:
one `notes:sweep-analyst` per tier decides the edits, one `notes:sweep-editor` per note applies them. You never
edit a note yourself.

## Rules

- Commands are `uv run` with quoted paths, one per Bash call, no `&&`, pipes or `$()`. Keep
  `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` inside the quoted command.
- Every Agent call is foreground (`run_in_background: false`); parallel dispatches go in ONE message. Never
  end the turn before step 8.
- Edits are **Edit-only and idempotent**: every insertion is wrapped in `<!-- sweep:ID -->` ...
  `<!-- /sweep:ID -->`, so a re-run replaces it and never duplicates it; a `scrub` replaces a passage in place and
  leaves nothing to find, so a re-run has nothing to do. Notes are never rewritten whole.
- The sweep never changes ledger inputs: `/notes:update` does not see a swept note as changed.
- Never Read a `.pdf`. Work only inside the project (`<P>` is the working directory or its first parent
  holding `.notes-plugin-internal/profile.md`).

## Step 1. Parse and check

`--tier` (default both), `--sessions 3,5` (incremental: analyse only edges that touch these sessions, after
`/notes:update` added or changed them), `--dry-run` (stop after the plan), `--yes` (skip the question).
Read the ledger (`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" show --project "<P>" --json`): fewer than 2
sessions with notes means nothing to sweep; say so and stop. A session whose tier is not `ok` is skipped for
that tier and listed in the report.

## Step 2. Concept inventory (deterministic)

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/concept_map.py" --project "<P>" --json "<P>/.notes-plugin-internal/work/course/inventory.json"`

Read the summary lines it prints (sessions, concepts, corrections, and **pending references**). It reads digests
and a plain-text scan of the notes, so it is cheap. `inventory.json` `pending` lists every `pending-ref` marker and
every legacy stale phrase ("has no note yet", "named without links") per note with line, heading, target sessions and
`resolvable` (the target note exists in that tier). Writers cited sessions whose note was not written yet; now it
usually is. `inventory.json` `process_talk` lists every passage that talks about how a note was made instead of the
subject ("representative output, not run on this machine", "the digest", "the execution policy"): a student does not
need it, and each one becomes a `scrub` edit. A sweep with only pending or process-talk items is worth running even
when there are no concept edits.

## Step 3. Analyst (one per tier, in parallel, ONE message)

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
profile: <P>/.notes-plugin-internal/profile.md
project: <P>
tier: basic
inventory: <P>/.notes-plugin-internal/work/course/inventory.json
notes_dir: <P>/notes/basic
concepts_out: <P>/.notes-plugin-internal/work/course/concepts-basic.json
plan_out: <P>/.notes-plugin-internal/work/course/sweep-plan-basic.json
sessions: <list, or all>
previous_plan: <path of the last plan for this tier, if one exists>
today: <YYYY-MM-DD>
```

Each tier keeps its own concepts and plan file. Confirm both files with `Glob`.

## Step 4. Show the plan, confirm once

Summarise each plan: edits per note and per type (builds-on, recap, forward link, propagated correction,
alias, example link, resolve-pending, scrub), and any edit the analyst listed as `unresolved` (including pending
references whose target note is still missing: they stay in place). Check that every `resolvable` pending item of the
inventory has a `resolve-pending` edit and every `process_talk` item a `scrub` edit; if not, say so and send the
analyst back once. With `--dry-run` stop here. Interactive
and no `--yes`: `AskUserQuestion` once ("Apply N edits across M notes?", Apply / Cancel). Headless or `--yes`:
apply. Cancel: stop; nothing has been written to a note.

## Step 5. Editors (one per note, in parallel)

Group the plan edits by note. Send one `notes:sweep-editor` per note, **up to 8 per message**, foreground, as
many messages as needed:

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
project: <P>
tier: basic
note: <P>/notes/basic/03-stack-and-heap.md
edits: <P>/.notes-plugin-internal/work/course/sweep-plan-basic.json
only_note: 03-stack-and-heap.md
protect: [<P>/resources]
```

Collect each return (applied, replaced, skipped with reason). A failed editor leaves its note as it was (it
works with Edit only): list it and continue.

## Step 6. QA and rollup

Per tier, one full QA run on the swept notes (Bash calls for the two tiers may run in parallel), `--notes`
listing every swept note number:
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/qa.py" --project "<P>" --tier <tier> --notes <nn> <nn> --data-dir "${CLAUDE_PLUGIN_DATA}" --no-rollup --json "<P>/.notes-plugin-internal/work/reports/qa-sweep-<tier>.json"`
Then the rollup once per tier:
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/update_index.py" --project "<P>" --tier <tier>` and
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/build_pdf.py" --project "<P>" --tier <tier> --compendium-only --data-dir "${CLAUDE_PLUGIN_DATA}"`.
Lint also checks that sweep markers are balanced and that every `builds-on` and `resolve-pending` link resolves, and
warns (`stale-note-ref`, `pending-ref-resolvable`) about any pending reference left behind. Then
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/sweep_check.py" --project "<P>" --tier <tier> --plan "<plan_out>"` must report
no pending reference whose target exists. A mechanical
failure caused by the sweep (wide line, bad marker): re-dispatch that note editor once with the failure text.
Anything left is reported, never hidden.

## Step 7. Ledger

Per tier and swept session: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record --project "<P>" --session <n> --tier <tier> --status ok --mode sweep`
(it leaves `inputs_hash` alone, so `/notes:update` ignores it). Use `partial` where QA left failures.

## Step 8. Report

`Write` `<P>/.notes-plugin-internal/work/reports/sweep-<today>.md` and reply in at most 15 lines: edits
applied, replaced (a re-run) and skipped per tier with counts by type, notes touched, QA result, PDF paths
(index and compendium), unresolved items, and the pending references still waiting for a note (note, line, target
session), which the next `/notes:sweep` after `/notes:update` will pick up.
