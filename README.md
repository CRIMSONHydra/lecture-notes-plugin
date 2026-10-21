# notes: lecture notes for Claude Code

A [Claude Code](https://claude.com/claude-code) plugin that turns a lecture (transcript, optional recording, slides,
the instructor's code) into **two PDF notes per lecture**: a **Basic** note for revising a class you sat in, and an
**Advanced**, web-researched, cited deep dive. It keeps one folder per subject, tracks what was generated from
which inputs, and only regenerates what changed.

**What it does**

- **Basic + Advanced PDF notes** per lecture, from the same lecture digest and research dossier, so both tiers report the same class corrections.
- **Transcripts, recordings, slides, handouts, data and exercises** are discovered in `resources/` under any file names and matched to sessions by title, date or number.
- **Video:** keyframes are extracted and read (board drawings, code, terminal output); GPU Whisper (CUDA or Apple Silicon) transcribes when there is no transcript.
- **Research:** every checkable claim is verified on the web once and cited.
- **Figures:** whiteboard and slide crops from the class, plus clean redraws and generated diagrams.
- **Cross-note sweep:** back-references, recaps, forward links and terminology aliases are added across the finished notes.
- **Term-long workflow:** drop new files in, run `/notes:update`; sessions run in parallel, in course order, with no duplicates.

**Requirements**

- [Claude Code](https://claude.com/claude-code) 2.1.271 or later.
- [`uv`](https://docs.astral.sh/uv/): every Python script runs through it, so there is no pip or virtualenv to manage.
- Chrome, Edge, Chromium or Brave for PDF printing. If none is found, `/notes:doctor install-chromium` installs a Playwright Chromium (about 150 MB).
- Optional, for fast video transcription: an NVIDIA GPU (CUDA) or Apple Silicon. Without one, a transcript you already have is used, or Whisper runs on the CPU (about an hour per 2 h class).
- Windows, macOS and Linux. Text files are written with LF endings and read with CRLF, LF or lone CR alike.

## Install

From GitHub:

```text
/plugin marketplace add CRIMSONHydra/lecture-notes-plugin
/plugin install notes@lecture-notes
/reload-plugins
```

From a local clone (a local marketplace loads in place, so edits apply after `/reload-plugins`):

```text
/plugin marketplace add <path-to-your-clone>
/plugin install notes@lecture-notes
/reload-plugins
```

For a one-off session without installing: `claude --plugin-dir <path-to-your-clone>`.

Check the setup with `/notes:doctor` (uv, browser, GPU and Whisper backend, compilers, cached assets).

## Quick start

```text
mkdir -p ~/courses/<subject> && cd ~/courses/<subject>     # one folder per course; open Claude Code here
/notes:init --from-syllabus <pdf>    # or --from-claude-md <path>, or --profile path/to/profile.md; see Profiles
# drop transcripts / recordings / slides into resources/ (any names), then:
/notes:scan                          # which file went to which session
/notes:update                        # plan, confirm once, generate everything new, in course order
/notes:sweep                         # after the notes exist: cross-note links, recaps, aliases
```

`/notes:lecture <n>` does one session. Run `/notes:update` again whenever more files arrive.

**Environment variable.** `NOTES_NO_VIDEO=1` disables all video processing (keyframes, Whisper, archiving and deleting
recordings): the scripts refuse to run and `/notes:update` treats `--video auto` as `reuse`. Use it on a machine without a
GPU or when you only want the notes from transcripts. `LECTURE_NOTES_CHROME` points at a specific browser binary and
`GITHUB_TOKEN` raises the GitHub rate limit when instructor code is fetched.

## Folder layout

```text
~/courses/<subject>/            <- the project: open Claude Code here
|-- resources/                  <- YOU drop inputs here (read-only to the plugin)
|   |-- <name>.txt              transcripts (.txt .md .vtt .srt)
|   |-- videos/<name>.mp4       recordings (optional)
|   `-- *.pdf *.pptx *.docx     slides, handouts, syllabus
|-- notes/{basic,advanced}/     generated markdown + figures/ (editable)
|-- pdf/{basic,advanced}/       the deliverables
`-- .notes-plugin-internal/     plugin state: profile.md (you edit), state.json (ledger), work/ (scratch, git-ignored)
```

Files dropped **outside** `resources/` (project root, a stray `videos/` or slides folder, `notes/`) are invisible to
discovery. `init`, `scan`, `update` and `doctor` list them as "Files outside resources/" with the exact destination, and
`/notes:scan --fix-layout --confirm` moves them (never overwriting; interactive runs ask once, headless runs only list).

## Commands in short

See the full table under "Commands" below. In short: `init` (once per subject), `scan`, `lecture <n>`, `update`, `sweep`,
`build`, `archive-videos`, `doctor`.

## How the notes look

| Tier | For | Shape |
| --- | --- | --- |
| **Basic** | Revising a class you sat in | **The whole class at class depth**: every concept the class explained or drilled gets its own subsection, every demo with real output, every board drawing, every room question (C++: about 3,500 to 7,000 words for a 2 h class, a floor-first guide, not a cap). TL;DR, traps, cheat sheet, self-check. No research |
| **Advanced** | Learning the topic from first principles | No word cap. Web-researched, cited, redrawn and generated figures, worked examples, interview questions, hands-on. Also shows every class drawing |

Both tiers show **the visualisation that happened in class**: the whiteboard drawings and annotated slides
are catalogued from the recording's keyframes (the digest's *Class visuals* table), cropped and cleaned
(whitened board, no grid) and embedded as figures captioned `From class, hh:mm:ss`. A messy, partial or
wrong drawing is redrawn clean instead (or as well), and a drawing a class correction affects is shown
**corrected**. Terminals and code are transcribed as text, never pictured. ASCII memory diagrams stay in
addition.

Both tiers are written from the same two files, a **lecture digest** (everything the class said, showed and
got wrong) and a **research dossier** (every checkable claim verified on the web once), so they report the
same class corrections. Subject knowledge lives in **profiles** (`cpp`, `fmi`, `cv`, `devops`, or one drafted
from a syllabus for a subject with none); the engine is generic. The plugin only writes inside the subject project folder you opened it in, never to `resources/`.

## License

[MIT](LICENSE). Copyright (c) 2026 CRIMSONHydra.

---

# Details

## Step by step

1. Install the plugin (see Install) and open Claude Code **inside a subject folder** (one folder per course).
2. `/notes:init` scaffolds the project and writes `.notes-plugin-internal/profile.md`. Profiles are user-made:
   `--from-claude-md <path>` drafts one from a subject CLAUDE.md, `--from-syllabus <pdf>` from a syllabus plus
   your resources (see "Subjects without a CLAUDE.md"), `--profile path/to/profile.md` uses a file you wrote, and
   `--profile _template` starts from the blank annotated template.
3. Drop the class files into `resources/` under any names (see "What to drop in resources/" below), then
   `/notes:scan` to see which file went to which session.
4. `/notes:update` generates everything that is new, in course order (or `/notes:lecture 3` for one session).
   Run it again whenever more files arrive: see "Using it all term".

```text
<Subject project>/              <- open Claude Code here
|-- resources/                  <- YOU drop inputs here; read-only to the plugin
|   |-- <stem>.txt              transcripts
|   |-- videos/<stem>.mp4       recordings (optional)
|   `-- *.pdf *.pptx *.docx     slides, handouts
|-- notes/                      <- generated markdown (editable)
|   |-- basic/      NN-<slug>.md  README.md  figures/
|   `-- advanced/   NN[a]-<slug>.md  README.md  figures/
|-- pdf/                        <- the deliverables
|   |-- basic/      <PREFIX>-NN-<slug>-basic.pdf  <PREFIX>-00-index-basic.pdf  <PREFIX>-Compendium-basic.pdf
|   `-- advanced/   ...-advanced.pdf
`-- .notes-plugin-internal/
    |-- README.md               says: plugin-internal, don't read
    |-- profile.md              subject profile (course facts, sessions, style): the one file you edit
    |-- state.json              ledger of what was generated from which inputs: do not edit
    `-- work/                   regenerable intermediates, git-ignored (sessions/NN/, meta/, build/, reports/)
```

**Writes stay inside the project**, and only under `notes/`, `pdf/` and `.notes-plugin-internal/`. `resources/` is protected
by a write guard during a run, so Claude Code's normal permission prompts apply only to those three folders.
The markdown is source; the PDF is what you read.

## What to drop in resources/

Any names, any subfolders; the plugin classifies every file (`/notes:scan` shows how) and never moves or
edits them. **Keep the original filenames**: a Scaler `.txt` header carries the lecture title and date.

| Kind | Examples | Used |
| --- | --- | --- |
| Transcript | `.txt` `.md` `.vtt` `.srt` | per session: primary speech |
| Recording | `.mp4 .mkv .webm .mov` (video), `.m4a .mp3 .wav` (audio) | per session: Whisper speech, plus keyframes for video |
| Existing Whisper / frames output | `*.whisper.json`, `frames/<stem>/` | per session, reused as is |
| Slides, board photos, code | `.pdf .pptx`, `.png .jpg`, `.cpp .py .zip` | per session when the name or first page matches |
| Syllabus, handouts | `Syllabus.pdf`, a capstone brief | course-wide: every session uses them (expected topics, relevance links) |
| Multi-session documents | `FDE_Session_2_3.pdf`, `Class 2-3`, `L2&3`, `sessions 4,5` | assigned to **each** listed session (`2-4` means 2, 3, 4; a date is never a range) |
| Data and exercises | `.csv .json .xlsx .parquet .sql .ipynb .py .zip`, solution PDFs | by `ClassN` number, else course-wide; used as datasets and worked examples when the class used them |
| Reference docs | `links.md`, other `.md`/`.txt` that are not transcripts | course-wide references |

What you get from partial input: **only a transcript** gives a full note from speech, with no screen
content. **Only a recording** runs Whisper (forced; about an hour on CPU for a 2 h class, minutes on an
NVIDIA GPU) and keyframes, and Whisper is read in full as the speech. **Only audio** does the same without
frames. **Only slides or handouts** stop with a message unless you pass `--allow-no-speech`, which writes
notes from documents alone and says so. Files that cannot be matched confidently are listed as
**unassigned** with candidate sessions in the run report: rename the file or pin it in `.notes-plugin-internal/profile.md`.
Sessions need not be listed in the profile; they are derived from `resources/`.

## Commands

| Command | What it does |
| --- | --- |
| `/notes:init [--profile <id\|path> \| --from-claude-md <path> \| --from-syllabus <pdf>] [--id X] [--project DIR] [--force]` | Scaffold the project and write `.notes-plugin-internal/profile.md` |
| `/notes:scan [--write-sessions] [--fix-layout [--confirm]] [--project DIR]` | Inventory `resources/`: which file belongs to which session, course-wide documents, unassigned files, and class files sitting outside `resources/` (`--fix-layout --confirm` moves them in) |
| `/notes:lecture <session> [--tier both\|basic\|advanced] [--video auto\|reuse\|run\|frames-only\|skip] [--redo digest\|research\|basic\|advanced\|all] [--rename-slug <new>] [--keep-work] [--allow-no-speech] [--profile <id\|path>]` | The whole pipeline for one lecture, no check-ins. Says "up to date" and stops when nothing changed (unless `--redo`); revises the existing note when inputs changed |
| `/notes:update [--parallel K] [--tier both\|basic\|advanced] [--sessions 3,5] [--check-code] [--dry-run] [--yes] [--keep-work]` | The term-long command: plan what is new or changed, confirm once, run **K sessions concurrently** (default 4), rebuild index and compendium once |
| `/notes:sweep [--tier both\|basic\|advanced] [--sessions 3,5] [--dry-run] [--yes]` | Cross-note continuity pass: back-references, recaps, forward links, aliases, and links for citations of notes that were not written yet (`pending-ref`), inserted idempotently into existing notes |
| `/notes:build [--tier both\|basic\|advanced] [--notes 02 ...] [--legacy]` | QA, rebuild PDFs and audit from existing markdown (after hand edits) |
| `/notes:archive-videos [--all\|<session>] [--yes]` | After ingest: archive the frames and Whisper JSON into `resources/`, then (on confirmation) delete the large recordings. The notes never need the video again |
| `/notes:doctor [install-chromium] [clear-guard]` | Environment report (uv, Chrome, GPU, compilers, assets) and fixes |

Example: `/notes:lecture 2 --tier both --video auto`.

Pipeline: doctor, resolve inputs, set the write guard, (video: Whisper plus keyframes), screen notes (parallel
`frame-reader` agents; every board drawing gets a crop box), digest (with the Class visuals table), research,
both writers in parallel, **visuals** (`class_figure.py` crops the class frames, then `figure-author` checks
every image and builds the redraws), QA agents, final deterministic `qa.py`, clear guard, **archive and offer
to delete the recording** (`video.delete_after_ingest: ask`; asks you, and never deletes in a headless run),
report. A digest V-row without a figure, a concept below the coverage bar, or a Basic note that is too thin are
QA content failures. `--redo digest` (or `research`, `basic`, `advanced`, `all`) re-runs only that
stage and what depends on it; everything else is reused.

## Parallel update, sweep, subjects without a profile

**`/notes:update --parallel K`** (default 4) runs whole sessions at once: the video ingests of all pending
sessions are started first in the background and queue on a cross-process GPU lock (one GPU job at a time);
then up to K `session-runner` agents run concurrently (each follows `reference/lecture-flow.md` for its one
session and dispatches its own stage agents), refilling as they finish. One failed session never stops the
others: it is recorded `failed` and retried by the next update. Index and compendium are rebuilt once at the
end; the report shows per-session status and timing and the wall clock against the sum of session times. A
note written during a batch only knows the earlier sessions that existed when it started, which is why the
sweep exists.

**`/notes:sweep`** runs after the notes exist (and again after later updates, `--sessions` limits it to new
edges). It builds a course concept map from the digests, an analyst decides the edits per tier (a `Builds on`
back-reference callout, a recap in Basic in place of a re-explanation, a forward link in the earlier note, a
recurring correction, a terminology alias, a recurring-example link), you confirm once (`--yes` or headless
skips it), one editor per note applies them with Edit only, then QA, index and compendium run. Every insertion
sits between `<!-- sweep:ID -->` markers, so a second run replaces rather than duplicates. The sweep never
makes `/notes:update` regard a note as changed.

Writers that cite a session whose note is not written yet wrap the sentence in `<!-- pending-ref:NN -->...<!-- /pending-ref:NN -->`
(never "has no note yet"); the sweep turns each into a link once the note exists (`resolve-pending`), and lint warns
about stale phrases and resolvable markers.

**Subjects without a CLAUDE.md** (for example an elective with transcripts, slides, data and exercises, no
recordings): `/notes:init --from-syllabus <syllabus.pdf>`, or plain `/notes:init` in such a folder, drafts
the profile from the syllabus, the transcript headers and the discovered resources, with generic rules for a
non-programming subject, and lists its assumptions. Review `.notes-plugin-internal/profile.md`, then
`/notes:update`.

**Corrections and the instructor repo.** Basic keeps 🔴 and 🟠 class corrections inline and collects 🟡 slips
in a closing `## Minor slips in class` table; Advanced keeps every correction inline. **The class often will not
match the instructor repo** (files added after class, demos typed differently): that is expected, not an error.
Notes follow the class and mention the repo version in a neutral `> **Repo note:**`, never as a correction or a
gap.

**Basic is judged by reading time and class-level difficulty, not pages.** Large class figures add pages
legitimately; `note_stats` reports reading minutes (figures count 10 s each) and warns only when Basic reads
harder than the class did (undefined-jargon density), never for length alone.

## Running inside an existing repo (for example an existing notes repo)

Open Claude Code in the repo folder that holds `resources/`, `notes/` and `pdf/` and run `/notes:init
--from-claude-md CLAUDE.md` (or `--profile path/to/profile.md`). It only creates `.notes-plugin-internal/`, `notes/basic/`, `notes/advanced/`, `pdf/basic/` and `pdf/advanced/`
and appends `.notes-plugin-internal/work/` to `.gitignore`; it never writes `resources/README.md` into your existing
resources folder. The write guard allows only those tier subfolders and `.notes-plugin-internal/`, so your hand-written
notes, old PDFs, build scripts and `CLAUDE.md` stay untouched (list your hand notes under
`prior_notes_external: ["notes"]` for cross-links; writers never read the hand note of the session they are
writing). Only `archive_video.py` may touch `resources/`, and only to add `frames/<stem>/` and
`<stem>.whisper.json` or remove the one recording you confirmed.

## Profiles

A profile is a markdown file: YAML front matter (session map, budgets, ASR hotwords, branding, optional
non-default `paths`) plus a prose body (persona, reader, house-style deltas, ASR corrections). **No course
profiles ship with the plugin: profiles are user-made.** Only the blank annotated `profiles/_template.md` ships.
`/notes:init` writes your profile (drafted from a CLAUDE.md or syllabus, your own file, or the template) to
`.notes-plugin-internal/profile.md`, which you then own and edit (add sessions as transcripts land). Input paths default to
`resources/` and are relative to `resources_dir`. Legacy notes elsewhere can be listed under
`prior_notes_external` (read-only) for cross-links.

Schema and examples: [`profiles/README.md`](profiles/README.md).

## Headless / CI

For `claude -p` runs, start in the project folder and:

- pass `--permission-mode acceptEdits` (there is nobody to answer prompts);
- send the prompt on **stdin** (`echo "/notes:lecture 2 --tier both" | claude -p ...`), because `--add-dir`
  and `--allowedTools` are variadic and swallow a trailing positional prompt;
- keep the project **outside the plugin's own directory**: writes under the plugin root are denied as
  sensitive, so a test project must live elsewhere (for example a sibling folder).

## Using it all term

Lectures keep arriving. Drop the new files into `resources/` at any time (transcript, recording, slides,
handouts; keep the original names), then run **`/notes:update`**.

- **What it does:** scans `resources/`, lists every session as `new`, `changed`, `incomplete` or `up-to-date`,
  asks once, then runs the lecture flow for only the new and changed ones, **in course order**, and rebuilds
  the index and compendium once. `--dry-run` shows the plan only; `--sessions 3,5` limits it; `--check-code`
  also flags sessions whose instructor repo moved on.
- **What counts as changed:** the transcript, Whisper output, keyframes, that session's slides, classwork, or
  the instructor's code for that lecture. Syllabus and handouts are course-wide: reported, never a trigger.
  **Deleting a recording after `/notes:archive-videos` changes nothing**, because the frames and Whisper it
  produced are what count. A run that failed or was partial is retried by the next update.
- **No duplicates:** each session has one note per tier, `NN-<slug>.md`. The slug is fixed the first time and
  kept in the ledger, so a re-run revises the same file, even if the lecture title is corrected later. Only
  `/notes:lecture N --rename-slug <new>` renames (note, figures and PDF together). Stale PDFs and figures of an
  old slug are pruned. The index and compendium list each session once.
- **Order:** chapters, previous/next links and the "earlier notes" the writers rely on follow the course order,
  not just the number. Each profile session may carry `order` (e.g. a bonus session numbered 14 that
  has `order: 12.5`, so it sits between 12 and 13). Without it: by `date`, then by `n`. A lecture the profile does
  not know yet is appended as a new row (next free number, title and date from the transcript, `order` from the
  date): check the printed title.
- **After a run** the heavy regenerable copies (Chrome build files, slide page PNGs, fetched code) are deleted;
  `--keep-work` keeps them. The digest, research and screen notes stay.

### The `.notes-plugin-internal/` folder

Managed by the plugin; not for reading. The one file you may edit is `profile.md` (course facts, session list,
settings). `state.json` is the ledger (what was generated from which inputs): do not edit it, and keep it, since
it is what prevents regeneration. `work/` is scratch, git-ignored and safe to delete (the ledger survives).

**Migrating from `.notes/`:** projects made by earlier versions used `.notes/`. The first
`/notes:update`, `/notes:lecture`, `/notes:doctor` or `/notes:init` run renames it to
`.notes-plugin-internal/`, fixes the `.gitignore` line and says what it did. If both folders exist it refuses:
merge by hand (keep `profile.md`) and delete the old one. Notes generated before the ledger existed are adopted
automatically on the first update (it fingerprints the current inputs and treats them as up to date).

## OS / GPU matrix

| Stage | Windows | macOS (Apple Silicon) | Linux |
| --- | --- | --- | --- |
| Video decode | CPU (bundled ffmpeg) | same | same |
| OCR of frames | DirectML (any DX12 GPU), else CPU | CoreML, else CPU | CPU |
| Whisper | CUDA fp16 on an NVIDIA GPU (about 70x realtime), else CPU int8 (about 1 h per 2 h) | MLX turbo, else CPU | CUDA fp16, else CPU |
| PDF | Chrome or Edge, else Playwright Chromium | Chrome/Chromium/Edge/Brave, else Playwright | PATH browsers, else Playwright |
| Figures | matplotlib (bundled DejaVu fonts) | same | same |

The mlx, CoreML and Linux-CUDA paths are implemented with CPU fallbacks but unverified on real hardware.
When no fast Whisper backend exists the `auto` policy runs frames-only and uses the existing
transcript; `--video run` forces a CPU transcription.

## First-run downloads

| What | Size | When |
| --- | --- | --- |
| Whisper `large-v3-turbo` | ~1.6 GB | first video transcription |
| CUDA libraries (pip wheels) | ~1 GB | first NVIDIA transcription |
| KaTeX and Mermaid | ~4 MB | first build of a note that uses them; cached, then offline |
| Playwright Chromium | ~150 MB | only if no browser is installed |

## Cost notes

Frames (~100k image tokens per 2 h class), the longer Basic note and web research dominate. A
recording can be deleted after ingest (`/notes:archive-videos`): the extracted frames and Whisper JSON are kept
and reused. Everything is Sonnet except the QA agent
(Haiku, mechanical fixes only). The digest and research are **reused** on re-runs, so changing only the
writers costs a fraction of the first run. `--tier basic` skips deep research (verify-only dossier).

## Privacy

Recordings never leave the machine: decode, Whisper and OCR are local. Only text (transcript excerpts, screen
notes) reaches the model, and web research sends search queries and claim text, not the video.

## Execution policy (what the agents may run)

The agents run on your own machine, which usually holds live cloud, GitHub, registry and cluster
credentials. For a subject like DevOps, "run the snippet to capture real output" would otherwise mean
`terraform apply` against your AWS account. While a run's guard is active, every command an agent wants to
run is judged against two rules, **no cost** and **light load**
([`reference/execution-policy.md`](reference/execution-policy.md)):

- **Floor (code).** Credential and token reads, `sudo`, encoded PowerShell, environment dumps and
  tampering with the plugin's controls are always blocked, also when wrapped in `wsl …` or `bash -c …`.
- **Fast path (code).** Read-only commands (also inside `wsl`) and the plugin's own scripts pass instantly.
- **Course material and toolchain.** Read-only `gh` and anonymous downloads of the repos the course
  references are allowed. The classifier knows how tools resolve on this machine (Python behind uv, Node
  behind fnm, `gh` only in WSL, Git Bash path rewriting), and rewrites commands to the form that works.
- **Classifier (Claude Haiku via `claude -p`).** Everything else from a `notes:*` agent, and every `{run}`
  snippet, is judged by the model: allow, deny, or "run exactly this instead" (for example, a `docker run`
  rewritten with `--rm`, a `notes-` name, 256 MB and half a CPU). Verdicts are cached. Calls run with no tools,
  no settings and a $0.10 cap. If the classifier is unavailable, the answer is deny.
- **Cleanup.** Each approval records what it creates and how to remove it. A `SubagentStop` hook carries out
  those removals: only `notes-*` Docker objects and temp or work paths, in Python, on any OS (inside WSL for
  things created there). Then it sweeps labelled Docker objects. `guard.py clear` sweeps the whole run.
- **Snippets** run with credentials stripped, git confined to local repos, and Git Bash, never the WSL
  launcher, as `bash` on Windows.

The hooks act only during a notes run, and the classifier only on the plugin's own agents, so your other
sessions are unaffected. The floor applies to every caller while a run is active. **Setup:** log in to the
Claude Code CLI once (`claude`). The plugin finds it on PATH, under fnm or npm, or at `LECTURE_NOTES_CLAUDE`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "notes plugin: blocked during a notes run: …" | Intended (see Execution policy). Show the command without `{run}` and give representative output. If the run crashed, `/notes:doctor clear-guard` |
| "classifier unavailable: … is `claude` logged in?" | Run `claude` once and log in, or set `LECTURE_NOTES_CLAUDE` to the CLI path |
| Snippets time out or "cannot open E:/…" on Windows | `bash` resolved to the WSL launcher (Windows searches System32 before PATH); 1.2.0 runs Git Bash by absolute path (install Git for Windows) |
| Whisper says "no CUDA device visible" | Switch the NVIDIA GPU on (laptops), re-run; or let it use the existing transcript |
| CUDA DLLs fail to load on Windows | Run `transcribe.py` inside WSL2 Ubuntu |
| "Chrome not found" | `/notes:doctor install-chromium`, or set `LECTURE_NOTES_CHROME` |
| Audit reports Type 3 fonts | A font fell back to a bitmap-style face; check the font stack and re-run `/notes:build` |
| Offline and a note needs KaTeX/Mermaid | Build once online; assets are cached under `<data dir>/cache/assets` |
| Writes blocked: "notes guard: ... is inside protected ..." | A crashed run left the guard on (expires after 6 h): `/notes:doctor clear-guard` |
| GitHub 403/429 fetching instructor code | Set `GITHUB_TOKEN`; the script falls back to a sparse git clone |
| Files are ignored | They are outside `resources/`: `/notes:scan` lists them, `/notes:scan --fix-layout --confirm` moves them |
| A note is regenerated only because the file was re-checked out on another OS | It is not: hashes ignore CRLF/LF and a BOM; ledgers from before that are upgraded silently |
| `uv` is not found | Install it from https://docs.astral.sh/uv/ and restart the terminal |
| Nothing happens in `claude -p` | See Headless / CI: pass `--permission-mode acceptEdits` and send the prompt on stdin |

## Line endings and git

Everything the plugin writes is LF. Everything it reads accepts CRLF, LF and lone CR (and a UTF-8 BOM), and the
fingerprints in the ledger hash text inputs after normalising line endings and the BOM, so a transcript checked out with
CRLF on Windows and with LF on macOS is the same input and never triggers regeneration. A ledger written before this
(raw-byte hashes) stays valid and is upgraded silently on the next `/notes:update`. This repository ships a
`.gitattributes` (`* text=auto eol=lf`, binaries marked). `/notes:init` writes (or merges, never clobbering your lines) a
project `.gitattributes` with `notes/**/*.md` and `.notes-plugin-internal/**` as LF text and `*.pdf *.png *.jpg *.mp4` as
binary; `/notes:update` adds it to existing projects on their next run.

## Development

```bash
claude plugin validate .
for t in tests/selftest_*.py; do uv run "$t"; done
# optional regression tests against a real subject project (read-only; skipped when unset):
#   NOTES_TEST_CPP_ROOT=<path> NOTES_TEST_FDE_ROOT=<path> uv run tests/selftest_discover.py
```

Layout and design record: [`docs/PLAN.md`](docs/PLAN.md). Video pipeline details: `scripts/video/README.md`.
