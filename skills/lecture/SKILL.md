---
name: lecture
description: Generate Basic (revision) and Advanced (deep-dive, web-researched) PDF notes for one lecture, inside the current subject project (run /notes:init first). Stops with "up to date" when nothing changed since the last run, and revises (never duplicates) an existing note when inputs changed. Manual workflow — run as /notes:lecture <session>; for a whole term use /notes:update.
argument-hint: "<session> [--tier both|basic|advanced] [--video auto|reuse|run|frames-only|skip] [--redo digest|research|basic|advanced|all] [--rename-slug <new-slug>] [--keep-work] [--batch] [--allow-no-speech] [--profile <id|path>]"
disable-model-invocation: true
allowed-tools: Bash(uv run *) Read Write Edit Glob Grep Agent AskUserQuestion
---

# /notes:lecture

Arguments: `$ARGUMENTS`

You are the **orchestrator** for ONE lecture. The whole per-session flow (steps 0 to 15: doctor, resolve, guard,
video, screen notes, digest, research, writers, figures, QA, ledger, archive, report) lives in a single file
that the batch runner (`agents/session-runner.md`) follows too:

**Read `${CLAUDE_PLUGIN_ROOT}/reference/lecture-flow.md` in full now, then follow it step by step in
interactive mode** with the arguments above. Do not work from memory of an older version of the flow.

Reminders that must survive:

- No mid-way check-ins; flag scope decisions in the final report.
- Commands are `uv run` with quoted paths, one per Bash call, no `&&`, pipes or `$()`.
  `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are substituted into this text, not shell variables.
- Every Agent call is foreground (`run_in_background: false`); parallel dispatches go in ONE message.
- Never end the turn before step 15; step 13 (guard, ledger, cleanup) runs on every exit path.
- Never Read a `.pdf`. Never delete a recording headless unless the profile says `video.delete_after_ingest: always`.
- `--batch` is for `/notes:update` only (the session-runner agent passes it). If it is set here, follow the
  `--batch` rows of the flow: no up-to-date check, `--no-rollup`, no guard, no archive.
