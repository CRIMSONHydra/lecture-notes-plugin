---
name: scan
description: Inventory everything in the project's resources/ folder (transcripts, Whisper output, recordings, slides, handouts, syllabus, code, images), show which session each file was assigned to and why, list course-wide documents and unassigned files, and optionally write the derived session table into .notes-plugin-internal/profile.md. Use after dropping new files, or when a lecture run picked the wrong inputs.
argument-hint: "[--write-sessions] [--project DIR]"
allowed-tools: Bash(uv run *) Read AskUserQuestion
---

# /notes:scan

Arguments: `$ARGUMENTS`

Commands are `uv run` with quoted paths, one per Bash call, with no `&&`, pipes or `$()`.
`${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are substituted into this text and are NOT shell
environment variables: keep them inside the quoted command. This skill never writes under `resources/`, except that `--fix-layout --confirm` moves stray class files INTO it (see step 3).

## Steps

1. Run (add `--project "<DIR>"` if given; add `--write-sessions` only if the user typed it):
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/scan_resources.py" --json "${CLAUDE_PLUGIN_DATA}/scan.json"`
   Exit 2 with no project: tell the user to run `/notes:init --profile <id>` first.
2. Read `${CLAUDE_PLUGIN_DATA}/scan.json` and explain it in three short tables:
   - **Per session**: session number and title, its speech source (transcript, Whisper, recording, none),
     slides, frames, code, and the reason each file was assigned (stem match, header title or date, session
     number in the name, filename similarity).
   - **Course-wide**: the syllabus and handouts. Every session uses them (expected topics, relevance links).
   - **Unassigned**: files the scanner would not guess about, each with its top candidate sessions and
     scores. For each give the fix: rename the file so it carries the session title or number (keep the
     original name otherwise), or pin it in `.notes-plugin-internal/profile.md` under `paths`.
3. **Files outside `resources/`** (`outside_resources` in the JSON, or the "Files outside resources/" block the
   script printed): discovery only reads `resources/`, so these are ignored. When the list is non-empty:
   - **Interactive:** ask ONCE with `AskUserQuestion` ("N class file(s) are outside resources/ and would be ignored.
     Move them?") with options "Move them into resources/ (Recommended)" and "Leave them". On the first run
     `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/scan_resources.py" --project "<P>" --fix-layout --confirm`, relay what it
     moved or refused (a name clash is refused, never overwritten), then re-run the step so the files are seen.
   - **Headless, `--yes`, or `AskUserQuestion` unavailable or unanswered:** do NOT move anything. List the files in
     the report with the exact move command above.
   Also accepted as arguments: `--fix-layout` (report only) and `--fix-layout --confirm` (move, no question).
4. Mention anything that blocks a run: a session with no speech source (it needs a transcript, a recording or
   a Whisper `.whisper.json`; or `/notes:lecture <n> --allow-no-speech` for documents only), unsupported
   files (`.key` slides: export to PDF), and an unusually large recording.
4. (`/notes:update` appends new sessions by itself; `--write-sessions` is the manual way.) With `--write-sessions`, say which session rows, with their `order`, were appended to `.notes-plugin-internal/profile.md` (existing rows are
   never rewritten) and ask the user to check them.
5. End with one line: the next command (`/notes:update` for everything ready, or `/notes:lecture <n>` for one).
