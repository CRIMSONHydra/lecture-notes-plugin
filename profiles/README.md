# Subject profiles

A **profile** holds everything subject-specific; the plugin's scripts and agents hold nothing else. It is one
markdown file: **YAML front matter** (machine fields, read by scripts through `notes_lib.profile`) and a
**prose body** (read verbatim by agents). Generic rules live in `../reference/`; course facts live here.

| File | What |
| --- | --- |
| `_template.md` | the blank, annotated profile: a starting point for your own subject (loads, but points at nothing) |
| `README.md` | this schema reference |

**No course profiles ship with the plugin: every profile is user-made.** Three ways to get one:

| You have | Run | Result |
| --- | --- | --- |
| a subject `CLAUDE.md` | `/notes:init --from-claude-md <path>` | a drafted profile |
| a syllabus and/or resources | `/notes:init --from-syllabus <pdf>` (or just `/notes:init`) | a drafted profile, assumptions printed |
| a profile file you wrote | `/notes:init --profile path/to/profile.md` | your file, copied |
| nothing yet | `/notes:init --profile _template` | the blank template, to edit by hand |

## The project copy is the profile

`/notes:init` copies the chosen profile into the subject project as `.notes-plugin-internal/profile.md`; from
then on that copy is the profile (edit it freely: add sessions as transcripts land). The template carries no
absolute or machine-specific paths, so it works on any OS.

## Lookup order

1. In a project: `<project>/.notes-plugin-internal/profile.md` (the project is found by walking up from the working
   directory, or `--project DIR`).
2. `--profile <path>` overrides that: an explicit file path, or an id/alias resolved against
   `<data_dir>/profiles/` (your own profiles) and then `<plugin>/profiles/` (only the template). `/notes:init`
   itself accepts only a file path, or `_template`.

Files whose name starts with `_` and `README.md` are never matched by the alias scan.

## Front matter schema

**Required:** `schema` (must be `1`), `id` (`[a-z0-9-]+`), `title`, `short`, `prefix` (`[A-Z0-9]+`),
Everything else is optional and takes the defaults in
`scripts/notes_lib/profile.py` (`DEFAULTS`), deep-merged with what you give.

| Key | Type | Meaning |
| --- | --- | --- |
| `schema` | int | always `1` |
| `id`, `aliases` | string, list | machine name, and alternative names accepted on the command line |
| `title`, `short`, `prefix` | string | full course title; running-header tag; PDF filename prefix |
| `institution` | string | one line for the cover and agents |
| `spelling` | `en-GB`, `en-US`, `any` | lint hint only |
| `resources_dir` | path | where the user drops inputs, **read-only**. Omit it: the default is `"resources"` inside the project. An absolute path is allowed for an external tree |
| `paths.*` | pattern or list | where inputs live, relative to `resources_dir` (below) |
| `prior_notes_external` | list of absolute dirs | optional legacy notes, read-only, used for cross-links and the knowledge ledger (the project's own `notes/<tier>/` is always included) |
| `instructor_code` | map or null | `{kind: github, repo, ref, session_dir, ignore[]}` |
| `sessions[]` | list, **optional** | `{n, title, stem, date, order}` rows (`order` optional): the course map. Omit it and the sessions are derived from what is in `resources/` |
| `asr` | map | `language`, `hotwords` (a string for the Whisper prompt) |
| `snippets` | map | per-language `{ext, compile, run}` templates with `{src} {exe} {dir}` |
| `tiers.basic`, `tiers.advanced` | map | budgets and required sections (below) |
| `research` | map | `preferred_sources[]`, `required_domains[]` |
| `callouts` | map | extra callout label to class, merged with the built-ins |
| `branding` | map | `accent`, `accent2` colours for the PDF |
| `models` | map | optional per-agent model overrides (honoured only when the tool exposes `model`) |

### `paths`

All relative to `resources_dir`, and **every key has a default**, so a profile lists only what differs:

```yaml
transcripts: "{stem}.txt"          recordings: "videos/{stem}.mp4"
frames: "frames/{stem}"            whisper: "{stem}.whisper.json"      # existing outputs, reused read-only
slides: []                         handouts: []                        classwork: null
```

Placeholders: `{n}` session number, `{nn}` zero-padded, `{stem}` and `{title}`
from the matching `sessions[]` row. Globs are allowed. A list means **alternatives, tried in order**, and all
matches are merged. A pattern whose placeholder value is missing is skipped, so a session absent from the
course map falls through to the next alternative.

| Key | Used for |
| --- | --- |
| `transcripts` | the class transcript (a missing match is a fatal error for that session) |
| `recordings`, `frames`, `whisper` | the video, its existing keyframes dir, its Whisper JSON (reused read-only) |
| `slides` | slide PDFs for the session |
| `handouts` | course-wide documents (.pdf/.docx/.pptx), same pattern rules as `slides`, not tied to one session |
| `syllabus` | syllabus PDF (course map, assessment) |
| `classwork` | the student's own code folder for the session (default none) |

Escape glob characters in names: a file called `Syllabus - [] Term 1.pdf` needs a pattern that avoids the
brackets (`Syllabus*Term 1.pdf`).

### `sessions`

`stem` is the transcript's file stem (also names the recording, frames and Whisper files). `date` is
`YYYY-MM-DD` and **wins over the transcript header**, which is often ambiguous between day/month and
month/day. Sessions without a transcript yet may carry only `{n, title}`. Add `stem` and `date` when the
transcript lands.

**`order` (optional number per row)** is the *display* position in the course: index and compendium chapter
order, prev/next links, the "prior notes" handed to writers, and `/notes:update` processing order. `n` stays
the stable file id (`NN-<slug>.md`). Without `order`, sessions sort by `date` when they have dates, else by
`n`; ties go by `n`. Use it for an extra lecture that falls between two numbered ones (for example an extra
workshop held after session 12 and before session 13 is `n: 14, order: 12.5`).

**Auto-appended sessions.** When `/notes:update` (via `plan_updates.py`) finds a lecture that is not in the
table, it appends a row in place (comments kept, existing rows never rewritten): next free `n`, title and
`date` from the transcript header or file name, `order` from the date. The rows are printed; correct a title
by editing the file.

**`state.json`.** Next to `profile.md`, `.notes-plugin-internal/state.json` is the plugin's ledger (per session
and tier: input fingerprints, the note slug and paths, QA status). It is how `/notes:update` knows what
changed. Do not edit it; `profile.md` is the only file there you edit.

**`sessions` is optional.** `scripts/scan_resources.py` (run by `/notes:init` and `/notes:scan`) inventories
`resources/`, assigns each file to a session, and with `--write-sessions` **appends** derived rows to this
list (existing rows are never rewritten). A new subject can therefore start with no table at all. Files are
matched by `stem`, then by transcript header title or date, then by a session number in the name, then by
name similarity; weak matches land in `unassigned`, never guessed. What makes discovery work best:

- **Keep the original filenames.** A Scaler export such as `Lecture_Title_-_Class_Scaler_Academy.txt` is
  matched by title; renaming it to something unrelated removes the signal.
- **`.txt` transcript headers carry the title and date** (`Lecture Title:`, start time); those are used when
  the filename says nothing.
- Put a session number or title in slide and handout names (`session3_slides.pdf`), or pin a file with
  `paths.*`. Course-wide files (syllabus, handouts without a session signal) need no pinning.
- Recordings, `.whisper.json` and `frames/<stem>/` that share a transcript's stem follow it automatically.

### `tiers`

```yaml
tiers:
  basic:    {words: [3500, 7000], minutes: [60, 120], figures: class+generated, gapfill_max_share: 0.30,
             diagrams: ascii, math: false, check_yourself: [5, 8], required_sections: [...],
             closing_sections: [...]}
  advanced: {words: [6000, 15000], figures: class+generated, min_figures: 2, diagrams: mermaid-ok,
             math: true, min_sources: 10, min_domains: 4, required_sections: [...], closing_sections: [...]}
video:      {delete_after_ingest: ask}      # ask | never | always
```

`words` for Basic is a guide for a ~2 h class (scaled by `duration_min / 120`): under the minimum warns "too
thin", over the maximum never fails; `minutes` is informational only. Basic re-teaches the **whole class** at
class depth, so the numbers are a floor-first estimate, not a cap. `figures`: `none` | `optional` |
`expected` | `class` | `class+generated`; `class` means class visuals only (board crops from the keyframes,
every digest V-row appears), `class+generated` adds redraws and generated figures. `video.delete_after_ingest`:
after a successful ingest `/notes:lecture` archives the frames and Whisper JSON into `resources/` and, per the
setting, asks (`ask`, never when headless), never deletes (`never`) or deletes (`always`) the recording. `diagrams`: `ascii` | `mermaid-ok`. `closing_sections` are excluded
from the prose count. `required_sections` are matched as case-insensitive heading prefixes.

## Body sections (agents read these verbatim)

Use exactly these H2 titles, in this order:

| Section | What goes in it |
| --- | --- |
| `## Role` | who the author is: persona and the job for each tier |
| `## Reader` | who reads the notes; the **Knowledge Ledger baseline** (what may be assumed) |
| `## Course` | course table, session plan, assessment, format; cross-link forward rule |
| `## Source handling` | where code comes from, source precedence deltas, what to verify with |
| `## Basic tier — subject rules` | budget, what earns space, structure, content requirements, quiz shapes |
| `## Advanced tier — subject rules` | baseline, the concept ladder, figures, sources, interview angles |
| `## Domain conventions` | names, units, platform deltas, formatting limits |
| `## ASR corrections` | the transcript-to-correct table (agents apply it silently) |
| `## Callouts` | which callouts the subject uses |
| `## Known context` | unconfirmed facts, resource naming, caveats |

Do **not** put absolute paths, build commands or workflow mechanics in a profile: those belong to the plugin.
Do not put per-session correction facts in a profile: corrections come from research, per session.

## Schema change from the repo-rooted layout

`repo_root` is gone (it made profiles machine-specific). Replace it with `resources_dir` (or omit it),
strip the `resources/` prefix from every `paths.*` value, delete `paths.prior_notes` (the project's own
notes are always the ledger; use `prior_notes_external` for legacy ones) and delete `output:`.

## Subjects with no profile (and no CLAUDE.md)

`/notes:init --from-syllabus <pdf>` (or just `/notes:init` in a folder with no `CLAUDE.md`) drafts
`.notes-plugin-internal/profile.md` from the syllabus, the transcript headers and the discovered resources, with
generic tier rules for a non-programming subject (no compile step unless code is detected, `figures:
class+generated`, research sources chosen from the subject). It prints its assumptions; check them, then edit.
Multi-session documents (`Session_2_3`, `Class 2-3`, `L2&3`) belong to every listed session; datasets and
notebooks are `data` and `exercises` (by `ClassN` number, else course-wide); `links.md` and other reference
text are course-wide `references`. The agents receive them as `data[]`, `exercises[]`, `references[]`.

## Writing or changing a profile

1. In the project: `/notes:init --from-claude-md <path>` (drafts one from a subject `CLAUDE.md`),
   `--from-syllabus <pdf>`, or `--profile _template` (the blank template), then edit
   `.notes-plugin-internal/profile.md`.
2. To keep a profile outside any project, copy `_template.md`, fill it in and pass it with `--profile <path>`.
3. Check it loads and finds a transcript for a session you have in `resources/`:

```bash
uv run "<plugin>/scripts/resolve_inputs.py" --project <project> --session 1 --dry-run
```

4. Run `uv run "<plugin>/tests/selftest_profiles.py"` (loads the template and the synthetic fixture profiles in
   `tests/fixtures/profiles/`, checks the body sections and the tables, scaffolds a test project and resolves
   session 2).
