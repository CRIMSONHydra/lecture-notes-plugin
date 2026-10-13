# Per-session lecture flow (single source)

Followed by `skills/lecture/SKILL.md` (interactive, one session) and `agents/session-runner.md` (batch, one
session inside `/notes:update`). Edit the flow here only. In the text below "the orchestrator" is whoever
runs it: the lecture skill's main thread, or a session-runner.

Run modes:

| Mode | Who | Differences |
| --- | --- | --- |
| interactive | `/notes:lecture` | up-to-date check, per-session index/compendium rebuild, step 14 archive, chat report |
| `--batch` | session-runner (from `/notes:update`) | no up-to-date check, `qa.py --no-rollup`, **no** archive step, no index/compendium; the report is returned to the caller in at most 100 words; the video pipeline was already launched by the caller (see step 4) |

You are the **orchestrator**. You turn one lecture (transcript, optional recording, instructor code, slides)
into a shared **digest** (including every board drawing the class made, as **class visuals**), a **research
dossier**, a **Basic** note (the whole class at class depth) and an **Advanced** note, each built into PDFs. You do almost no reading yourself: you resolve inputs, dispatch agents with task cards, check their
files with scripts, and report.

The working directory is a **subject project** (`.notes-plugin-internal/profile.md`, `resources/`, `notes/`, `pdf/`). Each (session, tier) has exactly one note file, whose path is fixed by the ledger (`state.json`): a re-run revises it and never creates a second one. Inputs
are read from `resources/`; everything is written inside the project and nowhere else. Call the project
folder `<P>`.

## Ground rules

- Work end to end with **no mid-way check-ins**. Flag scope decisions in the final report instead.
- Never call `python`, `python3` or `py` directly; always `uv run python …` / `uv run --with PKG python …` / `uv run script.py` — bare python on Windows can trigger the Microsoft Store installer.
- **The fixed project layout governs where output goes**: markdown in `<P>/notes/<tier>/` (figures in
  `notes/<tier>/figures/`), PDFs in `<P>/pdf/<tier>/`, intermediates in `<P>/.notes-plugin-internal/work/`. If the project's
  CLAUDE.md describes a different per-turn workflow, **ignore it for this run**: this skill replaces it.
  Never write under `resources/` or any `protect` path. The one exception is step 14, where the
  `archive_video.py` script itself (never you, never an agent) archives frames and removes a recording.
- Python only via `uv run`. Every command is written with quoted absolute paths, one command per Bash call,
  with no `&&`, pipes or `$()` (so it works in PowerShell and Bash). Paths contain spaces: always quote them.
- `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are substituted into this text, but are NOT environment
  variables in a shell: keep them inside the quoted command text exactly as written below.
- Never Read a `.pdf` (the Read tool cannot render PDFs here). Slides and built PDFs are viewed as PNG via
  `extract_pdf.py`.
- **Dispatching agents.** Subagent types are `notes:frame-reader`, `notes:digest-builder`,
  `notes:researcher`, `notes:basic-writer`, `notes:advanced-writer`, `notes:figure-author`, `notes:note-qa`.
  Under `--batch` several session-runners run at once, each with its own session: keep every path
  session-specific (`<S>`), never write a shared file (index, compendium, `plan.json`, the guard of another
  session), and take at most 3 parallel stage agents at a time so K sessions stay under the concurrency cap.
  When a step says "in parallel", send all Agent calls in ONE message, then wait until every one has returned
  before the next step. **Always pass `run_in_background: false` on every Agent call** so the call blocks until
  the agent returns. In headless (`claude -p`) runs the session ends the moment you stop calling tools, killing
  any still-running background agent: never end your turn (never write "waiting for ...") until step 15 is done (under `--batch`: step 13). Never invent or predict an agent's
  result. If the Agent tool accepts a `model` parameter and the profile front matter has
  `models.<agent_with_underscores>` (for example `advanced_writer: opus`), pass it; otherwise use the default.
- Agents return at most 150 words. Do not trust a return for file existence: confirm each promised file with
  `Glob`. Keep a short running log of every return for the report.
- **Timing (every run).** Around each stage run `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/timing.py" mark --project "<P>" --session <n> --stage <name> start`
  and `... end` (one Bash call each; `--note "<text>"` optional). Stage names, budgets and the report format:
  `${CLAUDE_PLUGIN_ROOT}/reference/timing.md`. Marking never blocks: if `timing.py` fails, continue. Each
  tier targets 25 min (Basic) and 30 min (Advanced) wall clock, so never serialise what the steps say to send
  in one message, and never add steps.
- `today` = the current date from your context, as `YYYY-MM-DD` (if you cannot see it: PowerShell
  `Get-Date -Format yyyy-MM-dd`, or Bash `date +%F`).

## Step 0. Parse `$ARGUMENTS`

First token = session (`2`, `02`, `2a` all mean session 2). Defaults: `--tier both`, `--video auto`, no
`--redo`, no `--rename-slug`, cleanup on, profile = `<P>/.notes-plugin-internal/profile.md` (`--profile <id|path>`
overrides it, mainly for tests). `--keep-work` disables the post-run cleanup (step 13). `--rename-slug <new>`
renames the session's note files (step 9b). `--batch` is set only by `/notes:update`: it means "the caller already
decided this session needs work and will rebuild the index and compendium and archive videos once at the end"
(skips the up-to-date check, the per-session index/compendium rebuild and step 14). `--allow-no-speech`
is passed through to `resolve_inputs`. `--redo`
takes a comma list from `digest, research, basic, advanced, all`. If the session is missing, print the usage
line and stop. The skill's `--tier` is the script's `--tiers`. If the scripts report no project, tell the user
to run `/notes:init --profile <id>` in their subject folder and stop.

## Step 1. Doctor

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --data-dir "${CLAUDE_PLUGIN_DATA}" --json "${CLAUDE_PLUGIN_DATA}/doctor.json"`

Read the JSON. Exit 1 means no Chrome-family browser. In that case run
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/install_chromium.py" --data-dir "${CLAUDE_PLUGIN_DATA}"`, then run the
doctor again. If a browser is still missing, stop and tell the user (PDFs cannot be built). Keep `gpu`,
`whisper_fast`, `ocr_accel` and `advice[]` for the report.

**Files outside `resources/`** (`project.outside_resources` in the doctor JSON): discovery only reads `resources/`, so these are ignored. When the list is non-empty:
- **Interactive:** ask ONCE with `AskUserQuestion` ("N class file(s) are outside resources/ and would be ignored.
  Move them?") with options "Move them into resources/ (Recommended)" and "Leave them". On the first run
  `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/scan_resources.py" --project "<P>" --fix-layout --confirm`, relay what it
  moved or refused (a name clash is refused, never overwritten), then re-run the step so the files are seen.
- **Under `--batch` (`/notes:update` already asked once), headless, `--yes`, or `AskUserQuestion` unavailable or unanswered:** do NOT move anything. List the files in
  the report with the exact move command above.
The doctor reports it only when the working directory is inside the project.


## Step 2. Resolve inputs and decide reuse

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_inputs.py" --session <n> --tiers <tier> --video <video> --data-dir "${CLAUDE_PLUGIN_DATA}"`

Add `--profile "<ref>"` only when the user gave one. The project is discovered from the working directory.
Exit 2 is a refusal (no project, or **no speech source at all**: no transcript, Whisper output or recording
matched this session): print stderr and stop. If the user wants notes from slides/handouts only, they re-run
with `--allow-no-speech` (add it to the command and pass it through; say in the report that the notes rest on
documents only). Do not rename or move the user's files. The last stdout
line is `inputs.json -> <path>`: Read that file. Keep its `warnings[]` for the report. From now on use
`inputs.profile.path` as the profile for every command and card, call `inputs.session_dir` `<S>`
(`<P>/.notes-plugin-internal/work/sessions/<nn>`), `inputs.project` `<P>`, and `inputs.session.nn` `<nn>`.

**Up-to-date check (skip under `--batch` or `--redo`).** Run
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/plan_updates.py" --project "<P>" --tiers <tier> --json "<P>/.notes-plugin-internal/work/reports/plan.json"`
and Read it: find this session's row. Status `up-to-date` (inputs unchanged since the ledger's last run, every
requested tier `ok`): print `Session <nn> is up to date (inputs unchanged since <date>). Use --redo to force.`
and stop (nothing to guard or clean). `new` means `mode: write`; `changed`, `incomplete` or `orphan` means
`mode: revise` against `inputs.note_paths`, and the row's `reasons` go in the report. A recording deleted after
archive never makes a session changed (only transcript, Whisper, frames, slides, classwork and instructor code
count).

**Print the session's resource map now** (a short block, and repeat it in the final report): speech source
(`speech.primary` and every `speech.sources[]` path), recording, existing Whisper/frames, slides, instructor
code, **course docs** (`course_docs[]`: path, kind, reason), and **unassigned files** (`unassigned[]`: path,
kind, top candidates as `session n (score)`). Unassigned files are never used as this session's input.
Do not ask the user about them mid-run: report them at the end with the fix (rename the file so it carries
the session title or number, or pin it under `paths` in `.notes-plugin-internal/profile.md`).

Read the first 150 lines of the profile file (the YAML front matter). You need `asr.hotwords`, `asr.language`,
`tiers.<tier>` (the budget), `models`, `research`. If `inputs.frames` is null and `video_action` is `skip`,
the digest rests on transcript, code and slides only: say so in the report.

**Reuse decisions** (record them for the report):

- *Digest:* if `<S>/digest.md` exists and
  `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/check_digest.py" "<S>/digest.md" --duration-min <inputs.session.duration_min>`
  exits 0, and `--redo` has neither `digest` nor `all`, **reuse it**: skip steps 4, 6, 7 and the transcript and
  slide parts of step 5. Otherwise rebuild.
- *Research:* reuse `<S>/research-verify.md` (and `<S>/research-deep.md` when Advanced is requested) only if
  they exist, the digest was reused and `--redo` has neither `research` nor `all`. An older session with one
  `<S>/research.md` of `mode: full` counts as both files. Reuse each file independently (verify may exist
  while deep is missing: run just the missing one).
- *Visuals:* class figures are cheap and deterministic: step 10 always re-runs the cropper for a tier whose
  note is (re)written, and never needs the recording (the keyframes are enough).
- *Writers:* `inputs.note_paths.<tier>` is the one path this session's note lives at (from the ledger, else an
  existing file; null for a brand-new session). If the file exists, dispatch that tier's writer with
  `mode: revise` and `existing_note: <path>` (it edits in place, keeping what is still good), unless `--redo`
  names that tier or `all` (then `mode: write`, overwrite **the same path**). No existing note: `mode: write`
  and the path is `<notes dir>/<nn>-<slug>.md` with the digest's slug. A tier whose note exists, whose ledger entry for that tier is `ok`, whose PDF exists in `pdf/<tier>/`,
  whose digest was **reused**, and whose `--redo` does not name it is skipped entirely. A note without an `ok`
  ledger entry or without its PDF is an **interrupted run**: dispatch that tier's writer with `mode: revise` (it
  completes a truncated note and keeps finished sections), then run figures, QA and the PDF build as usual. A rebuilt digest means every requested tier runs (revise if a note exists).

## Step 3. Guard

**Under `--batch` skip this step**: `/notes:update` sets one guard for the whole batch (the guard is a single
file per data dir, so parallel sessions must not set or clear it themselves).

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/guard.py" set --data-dir "${CLAUDE_PLUGIN_DATA}" --protect "<protect 1>" "<protect 2>" --allow "<allow 1>" "<allow 2>" "<allow 3>" --run "<profile id>-<nn>-${CLAUDE_SESSION_ID}"`

One quoted argument per entry of `inputs.protect` (`resources/` and any external prior-notes directory) and of
`inputs.allow` (`notes/`, `pdf/`, `.notes-plugin-internal/`). A hook now blocks Write and Edit under those paths for every
agent. **Step 13 must run on every exit path**, including failures.

## Step 4. Video (only if the digest is rebuilt)

**Under `--batch`** the caller (`/notes:update`) may already have started this pipeline in the background
(queued by the cross-process GPU lock, `<data>/locks/gpu.lock`). Check `<S>/video/pipeline.json` and the
frames dir: if the ingest is finished, re-run step 2's resolve with `--video reuse` and move on; if the
caller's ingest is still running, wait by re-issuing the same command with `--reuse` (it blocks on the lock and
then resumes or finds everything done). Never start a second GPU job by another route.

If `inputs.video_action` is `run`, `run-force-whisper`, `frames-only` or `whisper-only`, run this with the Bash
tool in the **foreground** with `timeout: 600000` (do NOT use `run_in_background`: a background pipeline gets
ignored and the digest starts without frames). If the call times out, re-issue the identical command: `--reuse`
resumes finished steps. Do step 5 first or after it, but never start step 6 or 7 before this command has returned.

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/video/pipeline.py" "<inputs.recording>" --out "<S>/video" --transcript "<inputs.transcript.path>" --existing-whisper "<inputs.whisper.path>" --hotwords "<asr.hotwords>" --language "<asr.language>" --whisper <auto, force or skip> --reuse`

Omit `--transcript` when `inputs.transcript` is null, and `--existing-whisper` when `inputs.whisper` is null (an existing Whisper JSON of this video is adopted, not recomputed). `--reuse` lets an interrupted run resume.

| `video_action` | `--whisper` | Notes |
| --- | --- | --- |
| `run` | `auto` | falls back to frames-only by itself when no fast backend exists |
| `frames-only` | `skip` | Whisper not wanted |
| `run-force-whisper` | `force` | the recording is the **only** speech source. If `doctor` says no fast backend (`whisper_fast` false, CPU only), warn the user NOW that it will take roughly the recording length or more (about an hour for a 2 h class; GPU off advice from `advice[]`), then proceed anyway |
| `whisper-only` | `force` | audio-only file: no keyframes, so there are no screen notes (skip step 6); say the digest has speech only |
| `reuse`, `skip` | none | run nothing |

When it returns, Read `<S>/video/pipeline.json` (backend, device, OCR provider, decisions) for the report, then
re-run step 2's resolve command with `--video reuse` and Read the refreshed `inputs.json`: it now points at
`<S>/video/frames` and `<S>/video/whisper.json`. If the pipeline failed, continue with a transcript-only
digest and say so in the report; if it failed and there is **no transcript**, there is no speech: stop (step 13
first) and report. After a successful run with no transcript, `speech.primary` is now `whisper`.

## Step 5. Prepare sources (foreground, meanwhile)

Run these as parallel Bash calls in one message. Skip an item whose inputs are absent.

- `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/prep_transcript.py" "<inputs.transcript.path>" --out "<S>/transcript"`
  (digest rebuild only, only when `inputs.transcript` exists; `.txt`, `.md`, `.vtt` and `.srt` are accepted).
  With no transcript the Whisper `transcript.md` is the speech: skip this call.
- `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_code.py" --repo "<ic.repo>" --dir "<ic.dir>" --out "<S>/code" --ref "<ic.ref>" --ignore "<glob 1>" "<glob 2>"`
  with `ic` = `inputs.instructor_code`; `--ignore` goes last. Always run it (the instructor's repo is re-checked
  every lecture). If GitHub is unreachable, continue without code and flag it.
- Per entry of `inputs.slides` (digest rebuild only):
  `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/extract_pdf.py" "<slide.pdf>" --out "<S>/slides/<pdf file name without .pdf>" --png --dpi 80 --hires-dpi 200`
  The 80 dpi `pages/NNN.png` are what agents read (token budget). The same call also writes `hires/NNN.png`
  at 200 dpi: figure crops are taken from it, so **never `Read` `hires/`** (see PLAN 17).
  Slides are often handwritten image-only PDFs: empty `.txt` files plus a warning are normal; the digest-builder
  reads the PNGs.

The video pipeline (step 4) must have returned before step 6.

## Step 6. Screen notes (only if the digest is rebuilt and `inputs.frames` exists)

With `count = inputs.frames.count`: shards = `ceil(count / 60)`, split into near-equal consecutive ranges of at most
60 frames (149 frames become `001-050`, `051-100`, `101-149`). Do not read `frames.json` yourself (it is large).
Launch one `notes:frame-reader` per shard **in parallel**, each with this card:

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
profile: <inputs.profile.path>
session: <n>
nn: <nn>
frames_dir: <inputs.frames.dir>
index: <inputs.frames.index>
range: 051-100
shard: 2/3
out: <S>/screen-notes/shard-2.md
protect: [<inputs.protect>]
```

Then `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/check_screen_notes.py" "<S>/screen-notes" --frames-json "<inputs.frames.frames_json>"`.
On failure, re-dispatch the affected shards **once** with an extra `missing: [<frame numbers>]` line (the
checker names them), then re-check. Frames still unaccounted for: continue, and list those ranges as unread in
the report and in the digest card (`unread_frames`).

## Step 7. Digest (only if rebuilding)

One `notes:digest-builder` with:

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
data_dir: ${CLAUDE_PLUGIN_DATA}
profile: <inputs.profile.path>
session: <n>
nn: <nn>
today: <today>
inputs: <S>/inputs.json
session_dir: <S>
digest_out: <S>/digest.md
mode: write
speech_primary: <inputs.speech.primary: transcript | whisper | recording | none>
course_docs: [<inputs.course_docs: path (kind) per entry>]
unassigned: [<inputs.unassigned paths: do NOT use these>]
data: [<inputs.data[]: path per entry>]            # datasets assigned to this session or course-wide
exercises: [<inputs.exercises[]: path per entry>]  # notebooks, solutions
references: [<inputs.references[]: path per entry>]  # links.md and other reference docs
protect: [<inputs.protect>]
unread_frames: [<ranges or empty>]
```

`data`, `exercises` and `references` may be empty or absent (programming courses without them are normal).
The digest records which of them the class actually used, so the writers turn exercises into worked
examples and hands-on sections.

When `speech_primary` is not `transcript`, add the line `note: the Whisper transcript is the PRIMARY speech
source; read it in FULL` to the card. When `--allow-no-speech` produced no speech, `speech_primary: none`.

Afterwards run `check_digest.py` as in step 2, adding `--json "<S>/logs/digest-check.json"`. If it fails,
re-dispatch **once** with `mode: revise` and `check_report: <S>/logs/digest-check.json`. Still failing: stop and report
if the failure is structural (missing or misordered sections, duplicate IDs, missing `slug`); if it is only a
timeline-gap explanation, continue and list it as an open question. Then Read the digest's first 40 lines for
`slug`, `title_as_taught`, `audio_quality`, `repo_moved_on`. **Visuals sanity check:** `Grep` the screen notes
for `^- \*\*Diagram:\*\* \(none\)` (count) against `^## F` (count) to see how many frames carry a drawing, and
`Grep` the digest for `^\| V[0-9]+ ` (count). Zero V-rows although many frames carry drawings (or fewer V-rows
than there are distinct drawing chains) means the catalogue was skipped: re-dispatch the digest-builder
**once** in `mode: revise` with the instruction "build digest section 11 (Class visuals) from the screen
notes; every drawing chain is one V-row". Report the V-row count. **The slug names both tiers' notes.**

## Step 8. Research (two parallel dispatches, ONE message)

Send **in one message** (both foreground, `run_in_background: false`): one `notes:researcher` with
`mode: verify` (always: about 2 minutes, tight scope) and, only when Advanced is requested, one with
`mode: deep`. Wait for both before step 9. Skip any whose reuse rule (step 2) says reuse.

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
profile: <inputs.profile.path>
session: <n>
nn: <nn>
today: <today>
digest: <S>/digest.md
research_out: <S>/research-verify.md      # deep: <S>/research-deep.md
mode: verify                              # or deep
min_sources: <tiers.advanced.min_sources, deep only>
min_domains: <tiers.advanced.min_domains, deep only>
protect: [<inputs.protect>]
```

Confirm each promised file with `Glob`. Later steps call them `research_verify` and `research_deep`.

## Step 9. Writers (in parallel, one per requested tier, ONE message)

Both writers get this card (change `tier` and the `basic_*` paths to `advanced_*` for the other tier):

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
data_dir: ${CLAUDE_PLUGIN_DATA}
profile: <inputs.profile.path>
session: <n>
nn: <nn>
slug: <slug from the digest>
today: <today>
tier: basic
project: <P>
digest: <S>/digest.md
research_verify: <S>/research-verify.md
research_deep: <S>/research-deep.md   # advanced writer only
note_out: <inputs.note_paths.basic, else <inputs.paths.basic_notes>/<nn>-<slug>.md>
meta_dir: <inputs.paths.basic_meta>
figures_dir: <inputs.paths.basic_figures>
build_dir: <inputs.paths.basic_build>
session_dir: <S>
prior_notes: <inputs.prior_notes.project.basic and inputs.prior_notes.external>   # sessions EARLIER IN COURSE ORDER only
protect: [<inputs.protect>]
budget: <the profile's tiers.basic mapping, copied verbatim from the front matter>
duration_min: <inputs.session.duration_min>   # the word range scales with duration_min / 120
mode: write            # or revise, with existing_note: <inputs.existing_notes.basic> (see step 2)
```

Tell the Basic writer in one line: "cover the whole class at class depth; every digest concept, demo and
V-row; the word range is guidance, under the minimum means material was skipped". Both writers write the
visuals sidecars (`<stem>.visuals.yaml`, `<stem>.visuals.json`) next to the coverage file.

For Advanced use `advanced` paths, `prior_notes` = `inputs.prior_notes.project.advanced` plus
`inputs.prior_notes.external`, `existing_note: <inputs.existing_notes.advanced>`, and the `tiers.advanced`
mapping. `prior_notes` lists the only sibling notes that exist: the writers link to those and nowhere else.
Add `data`, `exercises` and `references` lines to both cards as in step 7 (when non-empty).
**Corrections calibration (both writers):** Basic keeps only 🔴/🟠 class corrections inline and collects 🟡
slips in a closing `## Minor slips in class` table (`| said | actual |`); Advanced keeps every correction
inline. Instructor-repo drift (files added or changed after class, demos that differ from the repo) is a
neutral `> **Repo note:**` callout, never a correction and never a gap: notes follow the class
(`house-style.md`, `tier-basic.md`).
Writers write **exactly** to `note_out`; the file name (slug) is never re-derived from the digest, even if
`title_as_taught` changed. Prev/next links come from the builder: tell writers not to hand-write them. After they return, `Glob` each tier's
`<P>/notes/<tier>/` for the note (exactly one file per session and tier: a companion `<nn>a-*.md` is a failure, merge it). A writer that returned `failed` or wrote no note is
re-dispatched once with the same card.

## Step 9b. `--rename-slug <new-slug>` (only when asked; run it before step 9, when the note exists)

The slug is fixed at first generation. Only this flag changes it: run
`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" rename-slug --project "<P>" --session <n> --slug "<new-slug>"`
(renames the note, its figures and PDF in both tiers and updates the ledger), then re-run step 2's resolve so
`note_paths` shows the new names. Without the flag never rename.

## Step 10. Visuals and figures (between the writers and QA)

For each tier whose note was written (a `<tier meta>/<stem>.visuals.yaml` and/or `<stem>.figplan.yaml` exists;
`Glob` `<tier meta>/*.visuals.yaml` and `*.figplan.yaml`):

**10a. Class crops (deterministic, cheap).** Per `<stem>.visuals.yaml`, with `frames_dir` =
`inputs.frames.dir` (the keyframe JPGs, read-only), run in the foreground, one Bash call per tier (parallel
calls fine):

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/class_figure.py" --from-visuals "<meta>/<stem>.visuals.yaml" --frames-dir "<frames_dir>" --session-dir "<S>" --out-dir "<inputs.paths.<tier>_figures>" --nn <nn> --sidecar "<meta>/<stem>.visuals.json"`

`class_figure.py` crops from the native-resolution twins `<frames_dir>/hires/<same name>` and `<S>/slides/<deck>/hires/NNN.png`
when they exist (same crop fractions), and reports `source: hires|lowres` per figure in `--json`. A frames dir from an older run
without `hires/` still works (blurrier): regrab it with `keyframes.py <video> --out <frames_dir> --hires-only` while the
recording exists. `archive_video.py` does this itself before deleting a recording and refuses to delete without `hires/`.

Rows citing a slide page (`page:`) need only `--session-dir`. With no `inputs.frames` (no keyframes), keyframe rows cannot be cropped: skip them in 10a, tell the figure-author the crops cannot be
made (every embed becomes a redraw) and flag it in the report.

**10b. Figure-author (reads every image).** Dispatch one `notes:figure-author` per tier that has a
visuals yaml **or** a non-empty figplan (different tiers in parallel). It opens every class crop with the
Read tool and checks it (no text or drawing cut at an edge, no UI chrome such as a colour palette column,
not mostly blank), re-crops at most twice per figure, and builds the redraws and generated figures:

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
data_dir: ${CLAUDE_PLUGIN_DATA}
profile: <inputs.profile.path>
tier: advanced
project: <P>
note: <path of the note md>
stem: <note file name without .md>
figplan: <meta>/<stem>.figplan.yaml          # omit when there is none
visuals_yaml: <meta>/<stem>.visuals.yaml     # omit when there is none
visuals_json: <meta>/<stem>.visuals.json
frames_dir: <inputs.frames.dir>
session_dir: <S>
nn: <nn>
meta_dir: <inputs.paths.advanced_meta>
figures_dir: <inputs.paths.advanced_figures>
today: <today>
protect: [<inputs.protect>]
```

Both tiers normally have class visuals (the profile's `tiers.<tier>.figures` is `class+generated`); with
`figures: none` skip this whole step. Afterwards `Glob` the figures dir: every file named in
`<stem>.visuals.json` must exist (the QA checks it too).

## Step 11. QA (per tier, in parallel)

One `notes:note-qa` per tier:

```text
TASK CARD
plugin_root: ${CLAUDE_PLUGIN_ROOT}
data_dir: ${CLAUDE_PLUGIN_DATA}
profile: <inputs.profile.path>
tier: basic
project: <P>
notes_dir: <inputs.paths.basic_notes>
digest: <S>/digest.md
research_verify: <S>/research-verify.md
research_deep: <S>/research-deep.md   # advanced only
notes: <nn> [<nn>a]
logs_dir: <inputs.paths.session_logs>
session: <n>
nn: <nn>
protect: [<inputs.protect>]
```

It fixes mechanical failures and writes `<logs_dir>/qa-<tier>-content-failures.md`. If a tier still has
`content_failures`, re-dispatch **that tier's writer once** with `mode: revise` and
`qa_report: <the last <logs_dir>/qa-<tier>-iter<k>.json>`; re-run `notes:figure-author` for that tier only if the
writer said its figplan or visuals changed (then repeat step 10 for that tier); then run `notes:note-qa`
for that tier again. New failure classes are content failures: a digest V-row with no figure, a class
figure without a `From class` caption, Basic too thin, a concept below the coverage threshold. Failures that remain after that
are reported honestly, never hidden.

## Step 12. Final deterministic check
(The `notes:note-qa` agent of step 11 already ran the fast loop; this is the full `qa.py` run that the ledger
and report rely on. Mark stages `qa-<tier>` around step 11 and `build` around this step.) (both tiers; Bash calls may run in parallel)

`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/qa.py" --project "<P>" --tier <tier> --notes <nn> --digest "<S>/digest.md" --research "<S>/research-verify.md" "<S>/research-deep.md" --data-dir "${CLAUDE_PLUGIN_DATA}" --json "<inputs.paths.reports>/qa-<tier>-<nn>.json"`

Under `--batch` also add `--no-rollup` (builds this
note's PDF but not the index and compendium: `/notes:update` builds those once). Drop `--research` for the Basic tier (the paths are the files that exist; a legacy `research.md` alone is fine). Read each
JSON: `ok`, `mechanical_failures`, `content_failures`, `build.pdfs`, `build.compendium`, `audit.problems`.

## Step 13. Clear the guard, record the ledger, clean up (always, even if an earlier step failed)

1. Interactive only: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/guard.py" clear --data-dir "${CLAUDE_PLUGIN_DATA}"`
   (under `--batch` the caller clears it).
2. **Ledger.** Per requested tier that was dispatched (also after an abort, as `failed`):
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record --project "<P>" --session <n> --tier <tier> --status <ok|failed|partial> --mode <write|revise> --qa-json "<inputs.paths.reports>/qa-<tier>-<nn>.json"`
   `ok` only when that tier's `qa.py` JSON has `ok: true`; content failures left: `partial`; no note produced
   or the run aborted: `failed` (the next `/notes:update` retries it). Skip a tier that was reused untouched.
3. **Cleanup** (only if every dispatched tier is `ok`, and not with `--keep-work`):
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" clean --project "<P>" --session <n>` deletes the regenerable
   heavy copies (`work/build/<tier>/`, `work/sessions/<nn>/slides/*/pages/*.png`, `work/sessions/<nn>/code/`) and
   keeps `digest.md`, `research*.md`, `screen-notes/`, `inputs.json`, logs and reports. Report what was freed.

## Step 14. Archive and delete the recording (after PDFs and QA; the guard is already cleared)

**Skipped under `--batch`** (`/notes:update` archives once at the end; it never deletes headless unless the
profile says `always`). Only when `inputs.recording` is a video file (not audio-only) and the profile's `video.delete_after_ingest`
(default `ask`) is not `never`. The ingest outputs (keyframes, Whisper JSON) are what the notes were built
from; the recording is not needed again. All the validation lives in the script.

1. `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/archive_video.py" --session <n> --dry-run --json "<S>/logs/archive-plan.json"`
   Read the JSON/stdout: it says whether the ingest validates (frames and Whisper belong to this recording),
   what would be copied into `resources/frames/<stem>/` and `resources/<stem>.whisper.json`, and the bytes
   that deletion frees. If it refuses (invalid or partial ingest): do nothing, report why.
2. Decide by setting:

   | Setting | Action |
   | --- | --- |
   | `never` | skip; report "recording kept" |
   | `always` | `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/archive_video.py" --session <n> --confirm`, then report what was archived and deleted |
   | `ask` (default), interactive | `AskUserQuestion`: "Delete resources/videos/<file> (<size>)? Frames and the Whisper transcript are archived in resources/ and kept; the notes do not need the video again." Options: Yes, delete / No, keep it. Run the same command with `--confirm` **only on Yes**; on No run nothing (the user can run `/notes:archive-videos` later) |
   | `ask`, non-interactive (headless `claude -p`, `AskUserQuestion` unavailable, errors or returns no answer) | **never delete.** Report the exact command: `uv run "<plugin>/scripts/archive_video.py" --project "<P>" --session <n> --confirm` |

3. Never delete before the dry run validated, never pass `--confirm` without a Yes (or `always`), never pass
   `--force` yourself. Keep the printed result (copied, deleted, bytes freed) for the report.

## Step 15. Report

Run `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/timing.py" report --project "<P>" --session <n>` and paste its stage
table into the report, flagging every stage above 1.5 times its budget (reference/timing.md).
`Write` `<P>/.notes-plugin-internal/work/reports/<nn>-report.md` with these sections: **run mode** (write or
revise, the plan row's `reasons`, ledger status recorded, cleanup done or kept); **resource map** (speech source and
primary, recording, frames and whisper source, code files, slides, classwork, course docs, and the
**unassigned files with their candidate sessions and how to fix them**); reuse decisions (including writers
that revised an existing note versus wrote fresh); devices and backends (doctor plus
`pipeline.json`: Whisper backend, OCR provider, the GPU-off advice if it applied); the class-corrections table
(digest K rows, research verdict, which tier included each); gap fills added; verification summary (research
counts, claims left unverifiable); QA results per tier with any remaining failures; budgets (Basic prose
words and minutes against the profile, Advanced word count, sources and domains); absolute paths of every PDF
(per-note, index, compendium, both tiers, under `<P>/pdf/<tier>/`); **visuals** (V-rows in the digest, class
crops made, redraws, any crop that was illegible and became a redraw); **video** (archived and/or deleted, bytes
freed, or the command to run); `repo_moved_on` files; **earlier notes this session extends**
(digest section 10: candidates for an update, which this version only flags); open questions (digest
section 13); suggested ASR-table additions; warnings from `inputs.json`.

Under `--batch`: write the report file and return to the caller at most 100 words (`status: ok|partial|failed` per
tier, note and PDF paths, QA failures left, total minutes, flags). Interactively: reply in chat in **at most 15 lines**: what was read (one line of the resource map, plus any
unassigned files), what the notes cover, what was corrected or verified, what stayed ambiguous, scope calls you
made, and the PDF paths. Do not restate the notes.

## Failure policy

| Failure | Action |
| --- | --- |
| Refusal from `resolve_inputs` (exit 2: no project, no speech source) | Print the message (it lists the inventory), stop. Nothing was written or guarded yet |
| Chrome missing after install attempt | Stop (no PDFs possible) before any agent runs |
| Video pipeline fails | Continue transcript-only, flag in the report |
| GitHub unreachable | Continue without code, flag; the digest cites frames |
| Screen notes incomplete after one re-dispatch | Continue, list unread frame ranges |
| Digest structurally invalid after one re-dispatch | Clear the guard, stop, report |
| Writer failed twice | Report that tier as failed, still finish the other tier |
| QA content failures after one revision | Finish, report them honestly in report and chat |
| No keyframes (no recording) | No class crops: V-rows are redrawn or flagged; say so |
| `archive_video` refuses or fails | Keep the recording, report the reason |
| Headless run, setting `ask` | Never delete; print the command |
| Session up to date (plan row) | Say so and stop; `--redo` forces it |
| Any abort | Run step 13 first (ledger `failed`, guard cleared; skip cleanup) |
