---
name: init
description: Set up the current folder as a lecture-notes subject project. Scaffolds resources/, notes/, pdf/ and .notes-plugin-internal/, and writes .notes-plugin-internal/profile.md from a shipped template (--profile), drafts one from a subject CLAUDE.md (--from-claude-md), or drafts one from a syllabus plus the discovered resources when the subject has no CLAUDE.md (--from-syllabus, also automatic). Manual workflow — run once per subject folder as /notes:init --profile cpp.
argument-hint: "[--profile <id|path> | --from-claude-md <path> | --from-syllabus <pdf>] [--id X] [--project DIR] [--force]"
disable-model-invocation: true
allowed-tools: Bash(uv run *) Read Write Glob Grep AskUserQuestion
---

# /notes:init

Arguments: `$ARGUMENTS`

Create the **fixed project layout** in the current folder (or `--project DIR`), so `/notes:lecture` has
somewhere to read from and write to:

```text
<project>/
├── resources/        you drop transcripts, videos/, slides, handouts here (read-only to the plugin)
├── notes/{basic,advanced}/   generated markdown (+ figures/)
├── pdf/{basic,advanced}/     the PDFs
└── .notes-plugin-internal/   plugin-internal (README.md inside says so): profile.md is the one file you edit,
                              state.json is the ledger (do not edit), work/ is git-ignored scratch
```

## Rules

- **Legacy projects.** A project scaffolded by an older plugin has `.notes/profile.md`. `init_project.py` (and
  `resolve_inputs`, `doctor`, `plan_updates`) migrate it to `.notes-plugin-internal/`, fix the `.gitignore` line
  and write the folder README, printing what they did; if both folders exist they refuse and say why. Relay
  that output to the user; never move the folder by hand.

- **Line endings.** `init_project.py` also writes (or merges into) the project's `.gitattributes`: `notes/**/*.md` and
  `.notes-plugin-internal/**` as `text=auto eol=lf`, and `*.pdf *.png *.jpg *.mp4` as `binary`. It adds only the
  lines that are missing and never touches your own lines, so re-running it changes nothing. `plan_updates.py` and the
  legacy migration do the same for existing projects. Everything the plugin writes is LF; reading accepts CRLF, LF or
  lone CR. Relay the `wrote .gitattributes` line if it appears.

- Commands are `uv run` with quoted paths, one per Bash call, with no `&&`, pipes or `$()`.
  `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are substituted into this text and are not shell
  environment variables: keep them inside the quoted command.
- Write only inside the project. The scaffolding itself is done by `init_project.py`; you never create the
  tree by hand. An existing `.notes-plugin-internal/profile.md` is never overwritten unless the user typed `--force`.
- Never Read a `.pdf`. If a syllabus is a PDF, convert it with
  `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/extract_pdf.py" "<pdf>" --out "${CLAUDE_PLUGIN_DATA}/tmp/<name>" --pages 1-3`
  and read the `.txt` files (empty means image-only: add `--png`).

## Mode A: `--profile <id|path>` (a shipped template or your own file)

Run, with `<dir>` = `--project` or `.`:

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --project "<dir>" --profile "<id|path>"`

(add `--force` and `--id X` only if the user gave them). Available template ids: `cpp`, `fmi`, `cv`, `devops`. No `--profile` and no `CLAUDE.md` in the folder: go to Mode C. Exit 2 means
an unknown profile or an existing profile without `--force`: print the message and stop.

## Mode B: `--from-claude-md <path>` (draft a profile for a new subject)

A **profile** holds everything subject-specific: YAML front matter (machine fields) and a prose body
(persona, reader, course map, house-style deltas, ASR corrections). Draft one from a subject's CLAUDE.md.

1. **Read the references:** `${CLAUDE_PLUGIN_ROOT}/profiles/README.md` (schema, defaults),
   `${CLAUDE_PLUGIN_ROOT}/profiles/_template.md`, and one finished profile as a model
   (`${CLAUDE_PLUGIN_ROOT}/profiles/cpp.md`, or the closest condensed one). Skim the headings of
   `${CLAUDE_PLUGIN_ROOT}/reference/tier-basic.md` and `tier-advanced.md`.
2. **Read the CLAUDE.md** in full, then `Glob` the project's `resources/` (if it already holds files) to learn
   the naming of transcripts, slides, recordings. Read the first 12 lines of two or three transcripts: the
   header gives course, title, start time and duration.
3. **Derive the front matter** with only schema keys: `schema: 1`, `id` (from `--id`, else a short
   `[a-z0-9-]+` name), `aliases`, `title`, `short`, `prefix`, `institution`, `spelling`, `paths` (only what
   differs from the defaults, relative to `resources/`; **no absolute paths**), `instructor_code` if a code
   repo is linked, `sessions` (optional: number, title; `stem` where a transcript exists; `date` when ambiguous; omit it when
the CLAUDE.md has no course map, the scanner derives it from `resources/`), `asr`,
   `snippets`, `tiers.basic` / `tiers.advanced` (budgets and sections from the CLAUDE.md where stated),
   `research.preferred_sources`, `callouts`, `branding`, `models: {}`. Use `prior_notes_external` only for a
   legacy notes folder the user names.
4. **Write the body** with the fixed sections in order: `## Role`, `## Reader`, `## Course`,
   `## Source handling`, `## Basic tier — subject rules`, `## Advanced tier — subject rules`,
   `## Domain conventions`, `## ASR corrections` (a table, verbatim), `## Callouts`, `## Known context`. Keep the
   persona and pedagogy; **drop** workflow mechanics (where to write, how to build PDFs). Do not copy
   per-session corrections or findings from existing notes (they would leak answers into future runs). List
   guesses under a "Needs confirmation" bullet in `## Known context`.
5. **Write the draft** to `${CLAUDE_PLUGIN_DATA}/tmp/<id>.md` (outside the project), then scaffold with it:
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --project "<dir>" --profile "${CLAUDE_PLUGIN_DATA}/tmp/<id>.md"`
   This copies it to `<dir>/.notes-plugin-internal/profile.md` and creates the tree.

## Mode C: `--from-syllabus <pdf>` (a subject with no profile and no CLAUDE.md)

Chosen explicitly, **or automatically** when neither `--profile` nor `--from-claude-md` is given and the folder
has no `CLAUDE.md` (a syllabus found in `resources/` is used; with none, the transcript headers alone). Typical
case: a non-programming elective such as FDE (transcripts, slides, data files, exercises, no recordings).

1. **Skeleton and facts (deterministic).** `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --project "<dir>" --from-syllabus "<pdf>"`
   scaffolds the tree and writes a skeleton profile plus extracted facts (syllabus text, transcript headers,
   the discovered resources with their sessions, data, exercises and references).
   Run it with `--help` first if a flag is rejected, then Read what it printed and wrote. Syllabus PDFs: convert with
   `extract_pdf.py` as in the rules above and read the text.
2. **Draft the profile** over that skeleton, reading `${CLAUDE_PLUGIN_ROOT}/profiles/README.md`,
   `_template.md` and one finished profile. Fill the front matter and the ten body sections from the syllabus
   (course title, institution, assessment, session map), the transcript headers (title, date, duration) and the
   resources. Use **generic tier rules for a non-programming subject**:
   - no `snippets` compile step unless code languages are detected in the resources (SQL, Python notebooks
     only get a `snippets` entry when the class runs them); otherwise omit it;
   - `tiers.basic.figures` and `tiers.advanced.figures` = `class+generated`;
   - Basic: the whole class at class depth, worked examples from the exercises and datasets the class used;
     Advanced: cited depth, case studies, hands-on tied to the exercises;
   - `research.preferred_sources` chosen **from the subject**, not copied from a programming profile (for a
     data-engineering or product subject: product and engineering primary sources, vendor documentation,
     standards, well-known case studies and company engineering blogs; for finance: regulators, exchanges,
     textbooks; and so on); `required_domains` only when the syllabus names one;
   - a short `## Domain conventions` (units, names, notation the subject uses) and an `## ASR corrections`
     table seeded from the hotwords you can see in the transcripts (proper nouns, product names).
3. Write the draft to `${CLAUDE_PLUGIN_DATA}/tmp/<id>.md`, then
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" --project "<dir>" --profile "${CLAUDE_PLUGIN_DATA}/tmp/<id>.md" --force`
   (the skeleton already exists; `--force` replaces it with your draft).
4. **Print the assumptions** (what the draft inferred from which source, what was guessed: subject type, tier
   rules, research sources, session titles) and **print the draft's front matter** for the user to check.
   Interactive: say they can edit `.notes-plugin-internal/profile.md` and re-run. Headless: proceed and keep the
   assumptions list for the report; never block on it.

## Finish

1. Validate: pick a session with a transcript in `resources/` (else skip this step if `resources/` is empty) and run
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_inputs.py" --project "<dir>" --session <n> --dry-run --data-dir "${CLAUDE_PLUGIN_DATA}"`.
   In Mode B fix the profile (`.notes-plugin-internal/profile.md`) and re-run, at most three rounds.
2. **Existing resources.** If `resources/` already holds files, `init_project.py` has scanned them and written
   the derived session map (sessions the profile lacked, titled from transcript headers or file names) into
   `.notes-plugin-internal/profile.md`. Show that table (Read the `sessions:` block of the profile) and the scan summary the
   script printed (course docs, unassigned files). Tell the user: **to fix a mis-assigned file, rename it so
   the session title or number is in the name, or pin it under `paths` in `.notes-plugin-internal/profile.md`**; to re-check
   at any time run `/notes:scan`.
3. **Files outside `resources/`** (`outside_resources` in the JSON, or the "Files outside resources/" block the
   script printed): discovery only reads `resources/`, so these are ignored. When the list is non-empty:
   - **Interactive:** ask ONCE with `AskUserQuestion` ("N class file(s) are outside resources/ and would be ignored.
     Move them?") with options "Move them into resources/ (Recommended)" and "Leave them". On the first run
     `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/scan_resources.py" --project "<P>" --fix-layout --confirm`, relay what it
     moved or refused (a name clash is refused, never overwritten), then re-run the step so the files are seen.
   - **Headless, `--yes`, or `AskUserQuestion` unavailable or unanswered:** do NOT move anything. List the files in
     the report with the exact move command above.
   (`init_project.py` prints the block and lists it as `outside_resources` in its `--json`.)
4. **Report in at most 10 lines:** the project path, the profile id and where it came from, the tree created,
   and the next step: drop transcripts, recordings, slides, handouts into `resources/` under any names (any
   one speech source per session is enough), then `/notes:update` (plans and generates everything new, in course order) or `/notes:lecture <session>` for one lecture. In Modes B and C list what was inferred versus guessed (Mode C: the assumptions printed above).
