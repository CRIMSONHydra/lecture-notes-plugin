---
name: archive-videos
description: Archive the extracted frames and Whisper transcript of ingested class recordings into resources/, then (after confirmation) delete the large video files to free disk space. The notes never need the video again. Manual workflow — run as /notes:archive-videos --all or /notes:archive-videos 4.
argument-hint: "[--all|<session>] [--yes]"
disable-model-invocation: true
allowed-tools: Bash(uv run *) Read Glob AskUserQuestion
---

# /notes:archive-videos

Arguments: `$ARGUMENTS`

Recordings are large (hundreds of MB each) and are only needed to **ingest** a class (keyframes + Whisper).
Once that is done and validated, the notes are built from the extracted outputs, so the video can go. This
skill wraps `archive_video.py`, which is the **only** code allowed to write into `resources/`, and only these
paths: `resources/frames/<stem>/`, `resources/<stem>.whisper.json`, and the deletion of the one recording.

## Rules

- Every command is `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/archive_video.py" ...` with quoted paths, one
  command per Bash call, no `&&`, pipes or `$()`. `${CLAUDE_PLUGIN_ROOT}` is substituted into this text and
  is not a shell variable: keep it inside the quoted command.
- You never delete, copy or move files yourself, and never use Write or Edit under `resources/`. Only the
  script does, and only deletes with `--confirm`.
- Never pass `--force` unless the user typed it in `$ARGUMENTS`. Never pass `--confirm` before a dry run
  has validated the ingest and the user has agreed (or typed `--yes`).
- The project is the working directory (or its first parent holding `.notes-plugin-internal/profile.md`). With no project,
  tell the user to run `/notes:init --profile <id>` and stop.

## Steps

1. **Parse.** `--all` = every session that has a recording; a number (`4`, `04`) = that session; neither =
   print the usage line and stop. `--yes` skips the question in step 3.
2. **Plan (writes nothing).** For `--all` run
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/archive_video.py" --all --dry-run --json "${CLAUDE_PLUGIN_DATA}/archive-plan.json"`
   and for one session `... --session <n> --dry-run --json ...`. Read the JSON and the printed plan. For each
   session it states: whether the ingest validates (keyframes and Whisper belong to this recording), what would
   be archived, the recording's path and size, the bytes freed. Sessions whose frames already live in
   `resources/frames/` qualify directly; a session with an invalid or partial ingest is **refused**: list it
   with the reason, never override it.
3. **Confirm once.** Show a table (session, file, size, status). Unless `--yes` was typed, `AskUserQuestion`
   once: "Delete these N recordings (total X GB)? Frames and Whisper transcripts are archived in resources/ and
   kept; re-ingesting would need the videos again." Options: Yes, delete all listed / No, archive only (keep
   the videos). If `AskUserQuestion` is unavailable (headless), do not delete: print the command instead.
4. **Run.** Yes: the same command as step 2 without `--dry-run` and with `--confirm`
   (`--all --confirm` or `--session <n> --confirm`). No: the command without `--confirm` (archives only).
5. **Report** in at most 10 lines: per session archived or refused, files deleted, total bytes freed, and
   where the extracted frames and Whisper JSON now live (`resources/frames/<stem>/`,
   `resources/<stem>.whisper.json`). Mention that `/notes:lecture` reuses them (`--video reuse`, no
   recording needed) and that deleting a recording never makes `/notes:update` regenerate the session (only the
   frames and Whisper it produced count, and those are kept). If you did not delete, say how to: `/notes:archive-videos <n> --yes`.
