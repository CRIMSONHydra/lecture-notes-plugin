---
name: build
description: Run QA, rebuild and audit the PDF notes already in the current subject project (notes/ to pdf/), without reading or rewriting lectures. Manual workflow — run as /notes:build [--tier both|basic|advanced] [--notes 02 02a] [--legacy].
argument-hint: "[--tier both|basic|advanced] [--notes 02 02a ...] [--legacy]"
disable-model-invocation: true
allowed-tools: Bash(uv run *) Read Glob Grep Agent
---

# /notes:build

Arguments: `$ARGUMENTS`

Rebuild and audit the PDFs of notes that already exist, and fix purely **mechanical** problems (wide code,
wide tables, `§`, figure paths, stray placeholders). Content problems are reported, never rewritten here. A legacy `.notes/` folder is migrated to
`.notes-plugin-internal/` by the scripts (relay what they print). `qa.py` prunes PDFs and generated figures that
no note references (an old slug); mention what it removed.

## Rules

- Every command is `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/..."` with quoted paths, one command per Bash call,
  no `&&`, pipes or `$()`. `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are substituted into this text:
  keep them inside the quoted command exactly as written.
- Never Read a `.pdf`. Built PDFs are looked at as PNG via `extract_pdf.py` (the `notes:note-qa` agent does it).
- Write nothing yourself. The only writer is `notes:note-qa`, and only inside `notes/<tier>/`.

## Steps

1. **Parse.** Flags: `--tier both|basic|advanced` (default both), `--notes 02 02a ...` (note numbers; default
   all notes in the tier), `--legacy` (notes without front matter, such as hand-written ones). The project `<P>`
   is the working directory (or the first parent holding `.notes-plugin-internal/profile.md`); if there is none, tell the user to
   run `/notes:init --profile <id>` and stop. The profile is `<P>/.notes-plugin-internal/profile.md`.
2. **Check there is something to build.** For each requested tier, `Glob` `<P>/notes/<tier>/*.md` (ignore
   `README.md`). A tier with no notes is skipped and reported. Stop if none has any.
3. **Digest and research.** If `--notes` names exactly one session (for example `02` and `02a`) and
   `<P>/.notes-plugin-internal/work/sessions/<nn>/digest.md` exists, pass `--digest` (and `--research` for the Advanced tier when
   `research.md` exists). Otherwise pass neither: coverage and citation checks then run without them, which you
   state in the report.
4. **QA per tier** (parallel Bash calls are fine):
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/qa.py" --project "<P>" --tier <tier> --notes <nn> [<nn>a] --digest "<digest>" --research "<research>" --data-dir "${CLAUDE_PLUGIN_DATA}" --json "<P>/.notes-plugin-internal/work/reports/qa-<tier>-build.json"`
   Omit `--notes` for all notes, `--digest` and `--research` as decided in step 3, and add `--legacy` when
   given. Read the JSON: `ok`, `mechanical_failures`, `content_failures`, `build.pdfs`, `build.compendium`,
   `audit.problems`. If the error text says no Chrome browser was found, stop and tell the user to run
   `/notes:doctor install-chromium`.
5. **Mechanical failures.** For each tier that has any, dispatch `notes:note-qa` (tiers in parallel) with:

   ```text
   TASK CARD
   plugin_root: ${CLAUDE_PLUGIN_ROOT}
   data_dir: ${CLAUDE_PLUGIN_DATA}
   profile: <P>/.notes-plugin-internal/profile.md
   project: <P>
   tier: basic
   notes_dir: <P>/notes/basic
   digest: <digest path or none>
   research: <research path or none>
   notes: 02 02a
   logs_dir: <P>/.notes-plugin-internal/work/reports
   protect: [<P>/resources]
   ```

   Then re-run step 4 for that tier to confirm.
6. **Content failures** are not fixed here. List them (note, check, message) and tell the user which command
   regenerates the part: `/notes:lecture <n> --redo basic` or `--redo advanced` (a figure file
   missing means the figure step must run again, which `--redo` of that tier does).
7. **Report in at most 10 lines:** per tier `ok` or not, PDFs rebuilt (absolute paths under `<P>/pdf/<tier>/`:
   per-note, index, compendium), mechanical fixes made, content failures left, audit problems.
