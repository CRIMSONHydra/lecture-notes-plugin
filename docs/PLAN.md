# Implementation plan: `notes`, a unified lecture-notes Claude Code plugin

Plugin root: `<plugin>/` (written `P` below). Plugin name: **`notes`**. Local marketplace name: **`lecture-notes`**.
Commands: `/notes:lecture`, `/notes:build`, `/notes:doctor`, `/notes:new-profile`.
Mechanics were checked against code.claude.com docs (plugins-reference, components, skills, sub-agents, hooks, marketplace-reference) on 2026-10-03. The installed CLI is Claude Code **2.1.288**: `~/.vscode/extensions/anthropic.claude-code-2.1.288-win32-x64/resources/native-binary/claude.exe`. `claude` is not on PATH.

---

## 0. Decisions at a glance

- **Profiles hold everything subject-specific.** Each one is a markdown file with YAML front matter (machine fields) and a prose body (persona, course map, house-style deltas, ASR table). `cpp` is complete. `fmi`, `cv` and `devops` are condensed but usable.
- **No subject repo is ever modified.** Three things enforce this:
  - Outputs only go under `out_root`.
  - Every script refuses to write into a protected path.
  - A PreToolUse hook blocks Write/Edit under protected roots while a run is active.
  - All subagents set `omitClaudeMd: true`, so a repo CLAUDE.md that says "write into notes/" never reaches them.
- **Expensive reading happens once.** Frames and transcript are read once into a **lecture digest**, and web research runs once into a **research dossier**. Both tiers' writers work from those two files and do not re-read raw sources.
- **Research runs before both writers.** Both tiers then report the same verified class corrections.
- **One renderer for both tiers.** It combines cpp's two-pass CDP pagination, § numbering and outline with CV's KaTeX, Mermaid, pygments, figures and the Type 3 audit. It is tier- and profile-branded. KaTeX/Mermaid are downloaded once, cached, and inlined into self-contained HTML, so builds work offline after the first run.
- **Figures** use a ported CV style library (`notefig.py`). Each note gets its own deterministic PNG figure script, run by a uv runner. Output is PNG only.
- **Python always runs through `uv run` with PEP-723 headers.** No system Python is assumed; `python` on this machine is the WindowsApps stub.
- **Models:** every agent runs on Sonnet except `note-qa`, which runs on Haiku and only makes mechanical fixes. Profiles can ask for overrides (§3.7).

---

## 1. Context and design principles

### 1.1 What was mined from where

| Source | Tier it resembles | Taken into the plugin |
|---|---|---|
| cpp `Notes/CLAUDE.md` | Basic (strictest: 20–35 min, 1.5–2.5k words) | Revision budget; "earns space" vs Going Deeper; note structure; ASCII memory diagrams; GitHub code as ground truth; source precedence; class-correction severities; video step; audit in the same turn |
| FMI `CLAUDE.md` | Basic (looser: under 60 min, 3–4.5k words) | "cut, don't split"; Further Study structure; units and Indian digit grouping; never restate a transcript number as fact; utf-8 stdout |
| CV `CLAUDE.md` | Advanced (canonical) | Depth Policy; Accessibility Policy and Knowledge Ledger; four-beat pattern; "What you need before" and "New words"; companion `NNa` notes; figure rules; maths three-part rule; paper citations; PDF-only output with markdown kept outside the deliverable dir |
| DevOps `CLAUDE.md` | Advanced | First principles → mechanics → production → failure modes; web research every turn with inline citations and "verified as of"; runnable snippets and dry runs; command tables; common errors; interview Q&A; hands-on |
| cpp `pdf/build.py` + `audit.py` (FMI copy differs only in constants) | — | CDP printing with header/footer; § numbering; two-pass page resolution; outline; compendium; layout audit |
| CV `tools/build_pdfs.py` | — | markdown-it + dollarmath, KaTeX, Mermaid, pygments, figures, callouts, emoji→SVG, Type 3 audit, relative-link fix |
| CV `tools/make_figures.py` | — | Style, registry, title fitting, layout audit, helpers, `--list`/`--audit` |
| CV `tools/verify_numbers.py` | — | The `ok()` claim-check pattern, generalised to per-note check files |
| cpp `video/*` | — | Keyframes and Whisper pipeline, made path-explicit and backend-agnostic |

### 1.2 Principles

1. Generic rules live in `reference/`; course facts live in `profiles/`. Scripts read only profile front matter. Agents read both.
2. Read once. Frames → screen notes → digest happens once per session. Writers may only make targeted greps into the raw transcript or frames.
3. Every agent writes files and returns a summary of at most 150 words. The main context stays small.
4. Deterministic checks guard every LLM output: digest structure, frame coverage, note lint, budget, numbers, snippets, citations, PDF audit.
5. OS/CPU/GPU agnostic:
   - No hardcoded Windows paths in code.
   - Chrome is discovered per OS, with a Playwright-Chromium fallback.
   - Accelerators are chosen at runtime, always with a CPU fallback.
   - All fonts are cross-OS stacks; figures use matplotlib's bundled DejaVu.
6. Never invent output, flags, numbers or URLs. A note is not finished until its PDF is rebuilt, the audit is clean and the numbers check passes, all in the same run.
7. Respect the house style. For example, cpp basic uses ASCII diagrams and no figures, because the cpp CLAUDE.md is explicit about that.

### 1.3 Tier definitions (defaults; each profile overrides)

| | **Basic** | **Advanced** |
|---|---|---|
| Purpose | Re-anchor what was taught, for someone who sat in class | Teach from first principles; usable without attending class |
| Depth | Class level: each concept at the depth an assessment question needs | Full: the concept plus the adjacent concepts a practitioner considers inseparable |
| Budget (prose words¹) | `tiers.basic.words`. Default 1,500–3,000; cpp 1,500–2,500; FMI 3,000–4,500 | No cap. Guidance `tiers.advanced.words`, default 6,000–15,000; only the lower bound warns |
| Time | `tiers.basic.minutes`, computed². cpp 20–35, FMI 30–60 | Not budgeted |
| Splitting | Never split a lecture. Over budget → cut (prose → tables, one example per mechanic, rest to Going Deeper) | Spine plus companion `NNa-…` when over ~12k words or when a derivation deserves its own file; cross-linked both ways |
| Earns space | Every taught concept; minimal code or worked number per mechanic with its **actual output**; tables that settle a confusion; traps; instructor emphasis; answered inaudible room questions; class corrections; **gap fills (≤20% of words)** | Everything in Basic, plus: why/what broke before; mechanics under the hood; a hand-worked example for **every** concept; from-scratch code then idiomatic code; failure modes with real error strings; production reality (cost, latency, security); interview angles; research with citations |
| Pushed out | Pedantry, later-session topics, extra resources → **Going Deeper** (links only, ≤8 bullets) | Nothing is cut for length; Further Study is additive |
| Research | None beyond reading the verified corrections from the dossier | Researcher dossier; inline markdown-link citations; `verified_as_of`; ≥`min_sources` distinct URLs from ≥`min_domains` domains; annotated reading list |
| Accessibility | Profile's reader (for cpp: knows Java/Python and DSA) | **Knowledge Ledger**: assume only what the profile lists plus what earlier notes defined; four-beat pattern; expand acronyms per note; banned phrases ("as you know", "recall that", "obviously", "it is well known", "standard") |
| Required sections | TL;DR (5–8) → body (idea → code → output → gotcha) → Cheat sheet / One-screen summary → Check yourself (5–8 Q, answer plus one-line reason) → Going Deeper / Further Study | What you need before this note → TL;DR/mental model (6–10) → body ladder → Common errors & debugging (where applicable) → One-screen summary → New words in this note → Check yourself (6–10) → Interview questions (model answer plus follow-up) → Hands-on → Further Study → Sources |
| Figures | `tiers.basic.figures`: none / optional (cpp: none, ASCII only) | `expected`, `min_figures` (default 2); ASCII and Mermaid also allowed |

¹ Prose words = words outside fenced code, tables, YAML front matter and the closing sections (Going Deeper / Further Study / Sources).
² `minutes = prose_words/150 + code_lines·8 s + table_rows·15 s`. This was calibrated on the existing cpp notes 01–03: it gives 22.5 / 31.7 / 31.9 min against the hand estimates of 25 / 35 / 30.

**QA thresholds (Basic):**

| Check | Pass | Warn | Fail |
|---|---|---|---|
| Prose words | Within range | Up to 10% over max, or more than 25% under min | More than 10% over max |
| Computed minutes | ≤ max | ≤ max + 10% | > max + 10% |
| Gap-fill share of prose | ≤ 20% | — | > 20% |

### 1.4 Gaps where the instructor fell short: detection and marking

The digest builder fills a **Gaps** table (digest §4). Detection rules:

| Type | Signal |
|---|---|
| `rushed` | Assessable concept (core in the profile course map, or emphasised) with under 3 min of class time, or only "mentioned" |
| `no-example` | Explained with no demo, code or worked number |
| `hand-waved` | "basically", "we'll see later", "don't worry about", "you can google", trailing off |
| `audio-lost` | Junk run not recovered from Whisper or frames |
| `incorrect` | Also listed in §5 corrections |
| `confusion` | Repeated student questions, re-explanation, "does that make sense?" followed by silence |
| `unanswered` | Room question whose answer is inaudible |
| `prerequisite` | Assumed, never taught, and absent from prior notes |
| `deferred` | Instructor said "later session". Never filled in Basic; forward-link only |

**`fill_basic: yes`** applies when either:
- the concept is assessable **and** the gap type is one of `no-example`, `rushed`, `hand-waved`, `audio-lost`, `unanswered`, `confusion`; or
- it is a `prerequisite` needed to follow the class.

**Marking in Basic:** a callout `> **Gap fill:** <≤120 words + at most one short example>`, styled with class `gapfill`, counted by lint and listed in the tier index. The Advanced tier covers gaps fully with no marker. Class errors in both tiers use `> **Class correction (🔴|🟠|🟡 …):** said → actual → why it matters`.

---

## 2. File tree (owner work package in brackets)

```text
lecture-notes-plugin/
├── .claude-plugin/plugin.json          # manifest: name "notes", version, description, author      [WP1]
├── .claude-plugin/marketplace.json     # local marketplace "lecture-notes" → plugin source "./"    [WP1]
├── .gitignore                          # test-output/, __pycache__/, *.pyc, .cache/                [WP1]
├── README.md                           # install, usage, tiers, OS/GPU matrix, troubleshooting      [WP7]
├── hooks/hooks.json                    # PreToolUse(Write|Edit|NotebookEdit) → guard_hook.py        [WP1]
├── skills/
│   ├── lecture/SKILL.md                # /notes:lecture — end-to-end orchestrator                   [WP6]
│   ├── build/SKILL.md                  # /notes:build — QA + rebuild + audit from existing sources  [WP6]
│   ├── doctor/SKILL.md                 # /notes:doctor — env check, chromium install, clear guard   [WP6]
│   └── new-profile/SKILL.md            # /notes:new-profile — draft a profile from a repo CLAUDE.md [WP6]
├── agents/
│   ├── frame-reader.md                 # keyframe shard → verbatim screen notes                     [WP6]
│   ├── digest-builder.md               # transcript + screen notes + code + slides → digest.md      [WP6]
│   ├── researcher.md                   # web verification + deep research → research.md            [WP6]
│   ├── basic-writer.md                 # digest → Basic note (+checks, coverage, figplan)           [WP6]
│   ├── advanced-writer.md              # digest + research → Advanced note(s)                       [WP6]
│   ├── figure-author.md                # figplan → <stem>.figures.py → PNGs                         [WP6]
│   └── note-qa.md                      # run qa.py, mechanical fixes, escalate content issues       [WP6]
├── reference/
│   ├── house-style.md                  # generic rules for all subjects/tiers                       [WP5]
│   ├── tier-basic.md                   # §1.3/§1.4 Basic rules in full                              [WP5]
│   ├── tier-advanced.md                # §1.3 Advanced rules, Knowledge Ledger, four-beat, ladder    [WP5]
│   ├── note-format.md                  # front matter + markdown contract enforced by build/lint    [WP5]
│   ├── figures.md                      # figure rules + notefig API + per-note script convention    [WP5]
│   ├── digest-template.md              # digest schema (§3.5) with an example row per table         [WP5]
│   ├── screen-notes-template.md        # frame-reader output schema                                 [WP5]
│   └── research-template.md            # researcher dossier schema                                  [WP5]
├── profiles/
│   ├── README.md                       # schema, lookup order, repo-provided profile convention     [WP5]
│   ├── _template.md                    # annotated empty profile                                    [WP5]
│   ├── cpp.md                          # High-Performance C++ (complete)                            [WP5]
│   ├── fmi.md  cv.md  devops.md        # condensed, usable                                          [WP5]
├── scripts/
│   ├── notes_lib/__init__.py platform.py chrome.py profile.py notefile.py guard.py   # shared lib    [WP1]
│   ├── doctor.py                       # env report (+JSON)                                         [WP1]
│   ├── install_chromium.py             # Playwright Chromium fallback install                       [WP1]
│   ├── resolve_inputs.py               # profile+session → inputs.json                              [WP1]
│   ├── prep_transcript.py              # header.json, wrapped transcript, junk-run spans            [WP1]
│   ├── fetch_code.py                   # instructor GitHub folder → local copy + manifest           [WP1]
│   ├── extract_pdf.py                  # PDF → per-page text (+PNG) — replaces pdftotext/poppler    [WP1]
│   ├── guard.py  guard_hook.py         # guard CLI / PreToolUse hook                                [WP1]
│   ├── build_pdf.py                    # unified markdown→PDF (per-note + compendium)               [WP2]
│   ├── render/__init__.py markdown.py css.py cdp.py paginate.py assets.py layout.py                 [WP2]
│   ├── audit_pdf.py                    # layout/Type3/figure/math audit                             [WP2]
│   ├── video/pipeline.py transcribe.py keyframes.py README.md                                       [WP3]
│   ├── figures/notefig.py              # figure style library (port of CV)                          [WP4]
│   ├── figures/example.figures.py      # reference per-note figure script                           [WP4]
│   ├── make_figures.py                 # runner for <stem>.figures.py                               [WP4]
│   ├── verify_numbers.py               # runner for <stem>.checks.py                                [WP4]
│   ├── check_snippets.py               # compile/run {run} fences, compare output                   [WP4]
│   ├── note_stats.py  lint_note.py  check_citations.py  check_digest.py  check_screen_notes.py      [WP4]
│   ├── update_index.py                 # generate tier README.md (index chapter)                    [WP4]
│   └── qa.py                           # one-shot QA pipeline per tier → JSON                       [WP4]
└── tests/
    ├── selftest_ingest.py   fixtures/ingest/…    [WP1]
    ├── selftest_render.py   fixtures/render/…    [WP2]
    ├── selftest_video.py                         [WP3]
    ├── selftest_qa.py       fixtures/qa/…        [WP4]
    ├── selftest_profiles.py                      [WP5]
    ├── snapshot.py  e2e/cpp-02-answer-key.md  e2e/check_e2e.py   [WP7]
```

There is deliberately **no `bin/`** (Windows-unfriendly, and claude.ai refuses plugins that have one), no `commands/` (the docs say to prefer skills), and no root `CLAUDE.md` (not loaded; validate warns).

---

## 3. Component specs

### 3.1 Plugin mechanics (from the docs)

- **`plugin.json`:**
  ```json
  {"name":"notes","displayName":"Lecture Notes","version":"0.1.0",
   "description":"Lecture transcript/recording → shared digest → Basic (revision) and Advanced (deep-dive, web-researched) PDF notes with generated figures. OS/CPU/GPU agnostic; Python via uv.",
   "author":{"name":"CRIMSONHydra"},
   "license":"MIT","keywords":["notes","lecture","pdf","whisper","study"]}
  ```
- **`marketplace.json`:**
  ```json
  {"name":"lecture-notes","owner":{"name":"CRIMSONHydra"},
   "description":"Local marketplace for the notes plugin",
   "plugins":[{"name":"notes","source":"./","description":"Unified lecture-notes generator"}]}
  ```
  Put no `version` in the marketplace entry; that avoids a validate warning.
- **Install:** `/plugin marketplace add <plugin>`, then `/plugin install notes@lecture-notes`, then `/reload-plugins`. A local-directory marketplace loads **in place** and is not version-pinned, so edits apply after a reload.
- **Path substitution:** `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` (= `~/.claude/plugins/data/<id>/`, persistent) and `${CLAUDE_SESSION_ID}` are substituted inline in skill and agent **markdown bodies**. They are **not** present in the Bash tool's environment. Every command in a skill or agent must therefore write the substituted path in quotes: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/x.py" --data-dir "${CLAUDE_PLUGIN_DATA}"`. On Windows the substituted path uses forward slashes.
- **Skills:** `skills/<dir>/SKILL.md` becomes `/notes:<dir>`. Frontmatter: `description`, `argument-hint`, `disable-model-invocation: true` (manual workflows), `allowed-tools`. `$ARGUMENTS` holds the raw argument string. Keep each SKILL.md under 500 lines.
- **Agents:** `agents/<name>.md` becomes `notes:<name>`. Supported fields: `name, description, model (sonnet|opus|haiku|inherit|full id), effort, maxTurns, tools, disallowedTools, skills, memory, background, omitClaudeMd, isolation, color`. `permissionMode`, `hooks` and `mcpServers` are ignored for plugin agents. Subagents inherit CLAUDE.md unless `omitClaudeMd: true` (needs 2.1.271 or later; 2.1.288 is installed). Subagents can nest 3 levels deep, with at most 20 concurrent.
- **Hooks:** they fire for subagent tool calls too. A PreToolUse hook blocks a call with exit code 2 and a message on stderr. Use exec form so paths need no quoting:
  ```json
  {"hooks":{"PreToolUse":[{"matcher":"Write|Edit|NotebookEdit","hooks":[{"type":"command","command":"uv","args":["run","--quiet","--script","${CLAUDE_PLUGIN_ROOT}/scripts/guard_hook.py"],"timeout":20}]}]}}
  ```
- **Validation:** `claude.exe plugin validate "<plugin>"` and `claude.exe plugin details notes@lecture-notes` (shows projected token cost).

### 3.2 Output layout

`out_root` is resolved in this order: `--out`, then profile `output.root`, then `${CLAUDE_PLUGIN_DATA}/output/<profile-id>`. It must be outside `repo_root` unless the profile sets `output.inside_repo_ok: true`.

```text
<out_root>/
├── sessions/02/                       # per-session working set (shared by both tiers)
│   ├── inputs.json                    # resolve_inputs output
│   ├── transcript/header.json  transcript.wrapped.txt  junk_runs.json
│   ├── code/Lec-2/…  code/manifest.json
│   ├── slides/<pdf-stem>/pages/NNN.txt|.png
│   ├── video/ (only if the pipeline ran here) whisper.json  frames/…  pipeline.json
│   ├── screen-notes/shard-1.md …
│   ├── digest.md                      # THE lecture digest
│   ├── research.md                    # dossier (verify-only if tier=basic)
│   └── logs/
├── basic/
│   ├── src/02-<slug>.md  README.md  figures/02-*.png      # what the build reads
│   ├── meta/02-<slug>.checks.py .figures.py .figplan.yaml .coverage.json
│   ├── build/ (html, chrome profiles, manifest.json, snippets/)   # under out_root, NOT system temp (snap-Chromium safe)
│   └── pdf/CPP-02-<slug>-basic.pdf  CPP-00-index-basic.pdf  CPP-Compendium-basic.pdf
├── advanced/ (same; files end -advanced.pdf; companions 02a-…)
└── reports/02-report.md  qa-basic-02.json  qa-advanced-02.json
```

The slug comes from the digest's `title_as_taught`. For session 2 this is likely "compilation-and-the-stack-frame", because that is what was actually taught, not the syllabus title. Both tiers share the slug.

### 3.3 Subject profile

**Lookup order** (`notes_lib.profile.find_profile`):
1. An explicit file path.
2. `<cwd>/.lecture-notes/profile.md` (the documented way a repo will later provide its own profile; never created by the plugin).
3. `<data_dir>/profiles/<id>.md` (written by `/notes:new-profile`).
4. `${CLAUDE_PLUGIN_ROOT}/profiles/<id>.md`.
5. If none of those match, both profile directories are scanned for a matching `aliases` entry.

**Front matter schema.** Required keys: `schema, id, title, short, prefix, repo_root, paths.transcripts`. Everything else is optional and takes the `DEFAULTS` in `notes_lib/profile.py`.

```yaml
---
schema: 1
id: cpp                       # [a-z0-9-]+
aliases: [hft-cpp, "c++"]
title: "High-Performance C++ [HFT]"
short: "HP C++ · HFT"         # running header tag
prefix: CPP                   # [A-Z0-9]+, PDF filename prefix
institution: "Scaler School of Technology — SST 2028 batch, Term 9, HFT specialisation"
spelling: en-GB               # en-GB | en-US | any (lint hint only)
repo_root: "<cpp-project>"   # absolute; ~ expanded; read-only
paths:                        # relative to repo_root; placeholders {n} {nn} {stem}; globs ok; lists = alternatives
  transcripts: "resources/{stem}.txt"
  recordings: "resources/videos/{stem}.mp4"
  frames: "resources/frames/{stem}"           # existing keyframes output (reuse read-only)
  whisper: "resources/{stem}.whisper.json"
  slides: ["resources/L{n}.pdf", "resources/L{n}_.pdf"]
  syllabus: "resources/SST 2028 Batch Term 9 Syllabus - High Performance C++.pdf"
  classwork: "../Classwork/{n}"
  prior_notes: "notes"                        # earlier notes: cross-links + knowledge ledger (read-only)
instructor_code: {kind: github, repo: "KnightKnight27/scaler-HFT-C--2028", ref: HEAD, session_dir: "Lec-{n}",
                  ignore: ["*.o","*.out","*.exe","*.class","*.swp",".*.swp"]}   # binaries auto-skipped
sessions:                     # course map; stem = transcript/recording stem; title = syllabus title
  - {n: 1, title: "Getting started with C++", stem: "Getting_started_with_C++_-_Class_Scaler_Academy", date: 2026-08-19}
  - {n: 2, title: "Pointers and References", stem: "Pointers_and_References_-_Class_Scaler_Academy", date: 2026-08-22}
  - {n: 3, title: "Pointers and References 2", stem: "Pointers_and_References_2_-_Class_Scaler_Academy", date: 2026-08-24}
  - {n: 4, title: "Memory Management + STL", stem: "Memory_Management_+_STL_-_Class_Scaler_Academy"}
  - {n: 5, title: "Templates and Generic Programming", stem: "Templates_and_Generic_Programming_-_Class_Scaler_Academy"}
  # 6–13: title only (from the cpp CLAUDE.md session table)
asr: {language: en, hotwords: "C++ std::cout nullptr vtable RBP RSP RIP objdump g++ clang++ HFT order book cache line pointer reference stack heap assembly"}
snippets:                     # check_snippets templates; {src} {exe} {dir}
  cpp: {ext: .cpp, compile: "g++ -std=c++20 -O0 -Wall -Wextra {src} -o {exe}", run: "{exe}"}
tiers:
  basic:    {words: [1500, 2500], minutes: [20, 35], figures: none, diagrams: ascii, math: false,
             check_yourself: [5, 8], required_sections: ["TL;DR","Cheat sheet","Check yourself","Going Deeper"],
             closing_sections: ["Going Deeper"]}
  advanced: {words: [6000, 15000], figures: expected, min_figures: 2, diagrams: mermaid-ok, math: true,
             min_sources: 10, min_domains: 4,
             required_sections: ["What you need before this note","TL;DR","One-screen summary","New words in this note",
                                 "Check yourself","Interview questions","Hands-on","Further Study","Sources"],
             closing_sections: ["Further Study","Sources"]}
research: {preferred_sources: ["en.cppreference.com","eel.is/c++draft","godbolt.org","gitlab.com/x86-psABIs",
           "itanium-cxx-abi.github.io","agner.org","isocpp.github.io/CppCoreGuidelines","gcc.gnu.org/onlinedocs"],
           required_domains: ["en.cppreference.com"]}
callouts: {"HFT reality": reality, "Quiz tip": tip, "UB": ub}   # merged with built-ins
branding: {accent: "#134a7c", accent2: "#a3521a"}
models: {}                    # optional per-agent overrides, honoured only if the Agent tool exposes `model`
output: {root: null, inside_repo_ok: false}
---
```

**Built-in DEFAULTS:**
- basic: words [1500,3000], minutes [20,45], figures optional, diagrams ascii, math false, check_yourself [5,8], going_deeper_max 8, gapfill_max_share 0.20, required ["TL;DR","Check yourself"], closing ["Going Deeper","Further Study"]
- advanced: words [6000,15000], figures expected, min_figures 2, diagrams mermaid-ok, math true, min_sources 8, min_domains 4, companion_split_words 12000, required as in the cpp example minus "Interview questions", closing ["Further Study","Sources"]
- asr.language en; branding accent "#1f4e79" / "#a3521a"

**Profile body sections** (agents read these verbatim): `## Role`, `## Reader`, `## Course`, `## Source handling`, `## Basic tier — subject rules`, `## Advanced tier — subject rules`, `## Domain conventions`, `## ASR corrections`, `## Callouts`, `## Known context`.

**`profiles/cpp.md` body outline** (from the cpp CLAUDE.md; drop repo-path and workflow mechanics, which now belong to the plugin):
- **Role:** senior low-latency C++ engineer (verbatim intent). The basic job is revision; the advanced job is teaching (new).
- **Reader:** 3rd-year SST HFT student; Java/Python and DSA background; new to C++ and systems; revising under time pressure.
- **Course:** course table (3 credits, 13 × ~2 h, instructor pushes `Lec-N/`, assessment with the "column-scrambled syllabus" caveat). Full 13-row session table. Cross-link forward rather than duplicate.
- **Source handling:**
  - Re-check GitHub every lecture; quote the real file and name it.
  - `main.s`/`main.preprocessed` are useful; ignore artefacts.
  - Compare against Classwork and note the delta.
  - Precedence: code = GitHub file > frame > speech; output and diagrams = frame; speech = Scaler transcript, with Whisper for timing and junk runs (Whisper text was heard, so it is not "Reconstructed").
  - Frames are never embedded; redraw them.
  - Verify mechanics with cppreference, the standard and Compiler Explorer.
- **Basic tier — subject rules:** the "Hard Constraint" section (20–35 min, 1.5–2.5k words, never split); "what earns space"/"Going Deeper" lists; note structure (Cheat sheet, Check yourself shapes: what does this print / sizeof / will it compile / which is faster); content requirements:
  - compilable snippets with the command and actual output;
  - ASCII memory boxes with addresses and offsets;
  - the comparison-table list;
  - assembly and `sizeof` only when they teach;
  - latency numbers labelled order-of-magnitude.
- **Advanced tier — subject rules (new):**
  - Knowledge Ledger baseline: knows Java/Python, DSA, basic OS. Does not know C++ beyond earlier notes, assembly, the memory model, or the toolchain.
  - Ladder: problem → mental model → down to bytes/assembly (g++ and clang where they differ) → compiled worked example with output → standard citation (cppreference / [section] of the draft) → UB and failure catalogue (with `-fsanitize=address,undefined` runs) → HFT production reality → interview angles (HRT / Jane Street / Optiver-style questions with model answers) → hands-on build/break/fix → Further Study (C++ Core Guidelines, Meyers *Effective Modern C++*, Agner Fog, Drepper's memory paper, CppCon talks).
  - Generated figures for memory layouts and stack frames.
- **Domain conventions:**
  - Real names: `Order`, `price`, `qty`, `OrderBook`, `TickData`, `NSE`. Reuse the instructor's examples.
  - State the standard version and the compiler/platform.
  - The class demos on macOS/Linux. Note Windows deltas in one line: System V `RDI/RSI` vs Windows x64 `RCX/RDX`; `sizeof(long)` is 4 on LLP64; MinGW vs MSVC vs WSL2.
  - Code and ASCII under 95 columns; tables under 9 columns.
- **ASR corrections:** the full 16-row table, verbatim.
- **Callouts:** Note, Gotcha, UB, Quiz tip, HFT reality, Class correction, Reconstructed, plus the generic Gap fill.
- **Known context:** assessment weightage unconfirmed; slides land as `L<n>.pdf`; class timings are as read off screen.
- **Do NOT include** the known session-2 corrections. They are the hidden E2E answer key.

**Condensed profiles** (complete front matter; body of about 80–150 lines; not yet tested end to end):

- **`fmi.md`**
  - Front matter: `repo_root "<fmi-project>"`; transcripts `resources/{stem}.txt`; sessions 1–2 with stems `Introduction_to_Financial_Accounting_-_Class_Scaler_Academy` and `…_2_-…`. Header dates are dd/mm-ambiguous, so `date:` belongs in the profile. Slides `resources/Session {n}_*.pdf`; `classwork: classwork`; no `instructor_code`; no recordings.
  - Basic tier = the FMI CLAUDE.md: words [3000,4500], minutes [30,60], Further Study ordered go-deeper → practice → real-world reading → coming next, ₹ / lakh / crore unit rules, Indian digit grouping, every metric's failure mode, Exam tip and Market reality callouts. Advanced = the deep-dive pattern plus primary sources (annual reports, NSE/BSE, SEBI, RBI, MCA, IFRS/ICAI), derived TVM and valuation formulas, worked ₹ numericals. Body includes the 17-row ASR table; `math: true` for advanced only.
- **`cv.md`**
  - Front matter: `repo_root "<cv-project>"`; transcripts `["resources/Session {n} — *.txt", "resources/Session_{n}_—_*.txt"]` (groq Whisper, may lack a header); slides `["resources/session_{n}*.pdf","resources/Session{n}*.pdf","resources/Session_{n}_*.pdf"]`; `prior_notes: note-src`; `spelling: en-GB`; `math: true` in both tiers; Mermaid OK.
  - Advanced tier = the CV CLAUDE.md: Depth and Accessibility policies verbatim in substance, the three assumed things, four-beat pattern, maths three-part rule, paper citations with year and venue, compute reality, companion notes, figure rules; `min_figures 3`, `min_sources 10`. Basic tier (derived; tune after first use): words [2500,4000], minutes [30,50], figures optional, one worked example per formula. Body includes the ASR table and the instructor's "internals over API calls" stance.
- **`devops.md`**
  - Front matter: `repo_root "<devops-project>"`; transcripts `resources/*.txt` matched by session title; slides `resources/devops{n}.pdf`; classwork `Classwork/Class {n}`; `math: false`.
  - Advanced tier = the DevOps CLAUDE.md quality bar (taught plus related topics, web research every turn, runnable snippets, dry runs including failing runs, command reference table, common errors, interview Q&A, hands-on, security and cost, AWS default with Azure/GCP table). Basic tier (derived): words [2000,3500], minutes [25,45]. ASR list moved into a table.

### 3.4 Note format contract (`reference/note-format.md`; enforced by `lint_note.py` and `build_pdf.py`)

- **Filename:** `NN[a]-<slug>.md` in `<tier>/src/`. The PDF is `{PREFIX}-{NN}-{slug}-{tier}.pdf`. The index is `{PREFIX}-00-index-{tier}.pdf`; the compendium is `{PREFIX}-Compendium-{tier}.pdf`.
- **YAML front matter:**
  - Basic: `title, session, tier, profile, date, duration_min, sources[], instructor_code, revision_minutes, verified_as_of`
  - Advanced adds `prerequisites[]` and `companion_of` (for `NNa`).
  - The builder renders the visible meta block, including an automatic counterpart link to the other tier's PDF (`../../<other>/pdf/…`) when it exists.
- **Headings:**
  - Exactly one H1, `# NN · Title`.
  - H2/H3 are auto-numbered `§N.k[.m]` by the builder. Never hand-number them; `## 1. ` is stripped with a lint warning.
  - **Never type `§` anywhere.** Refer to sections as "see 2.4".
- **Code:**
  - Fenced with a language. Lines ≤95 columns: warn above 95, error above 120.
  - A `{run}` attribute (```` ```cpp {run} ````) must be followed within 2 lines by a ```` ```text ```` block of real output. Lines starting with `$ ` in the output block are treated as commands.
  - `{run nocheck}` means compile and run but don't compare (platform-dependent output; the caption must name the source platform). `{compile}` means compile only.
- **Tables:** ≤9 columns.
- **Diagrams:** ASCII in ```` ```text ```` fences. ```` ```mermaid ```` only if `diagrams: mermaid-ok`.
- **Math:** `$…$` / `$$…$$` only if the tier's `math: true`; write a literal dollar as `\$`.
- **Figures:** `![alt](figures/NN-name.png "**Lead sentence.** caption…")`. Local PNG only.
- **Callouts:** `> **Label:** …`. Built-in label → class map:
  - note → note
  - gotcha → gotcha
  - ub → ub
  - quiz/exam/interview tip → tip
  - hft/market/production/deployment reality → reality
  - class/transcript correction, class nuance → correction
  - reconstructed → recon
  - maths → maths
  - paper → paper
  - jargon → jargon
  - gap fill, beyond class → gapfill
  - source(s), revision/study time, verified → meta
  - plus profile extras
- **HTML:** raw HTML only `<details><summary>`, `<br>`, `<sub>`, `<sup>`. The builder force-opens details.
- **Links:** links to sibling notes are relative `NN-slug.md`; the builder rewrites them to PDF names or internal anchors. External links are absolute https. No external images.
- **Sidecars in `<tier>/meta/`:**
  - `<stem>.coverage.json`: `{"C01":{"sections":["…"],"worked_examples":1},…}`
  - `<stem>.checks.py` (§3.6, verify_numbers)
  - `<stem>.figplan.yaml`: list of `{name, section, purpose, kind, data, size_in:[7.4,2.8], caption_draft, schematic:false}`
  - `<stem>.figures.py`

### 3.5 Lecture digest, screen notes, research dossier

**`digest.md`** (template in `reference/digest-template.md`; validated by `check_digest.py`).

Front matter: `profile, session, title_syllabus, title_as_taught, slug, date, duration_min, sources{transcript, whisper, frames_total, frames_read, instructor_code[], slides[], classwork}, repo_moved_on, audio_quality (good|patchy|poor), dropped_minutes, verified_as_of`.

Sections (numbered H2s, in this exact order):

1. **Session timeline**: `| hh:mm–hh:mm | topic | concepts | frames |`. Must cover the whole duration; gaps over 10 min must be explained.
2. **Concept inventory**: `| C01 | concept | depth (mentioned/explained/demoed/drilled) | minutes | emphasis | assessable (y/n) | sources |`
3. **Concept detail**: `### C01 — name` → taught (reconstructed account) · code shown (file path(s) plus frame refs) · output shown (verbatim from frames) · board diagrams (redraw spec) · instructor examples and numbers (tag *heard* / *read-from-screen*) · room questions plus answers.
4. **Gaps**: `| G01 | C-ref | type | evidence (ts/quote) | severity | fill_basic | basic fix (1 line) | advanced treatment |`
5. **Class-correction candidates**: `| K01 | said (ts) | likely actual | severity 🔴🟠🟡 | confidence | how to verify |`
6. **Reconstructed passages**: `| ts range | why | reconstructed from |`
7. **Code and output inventory**: `| P01 | file or frame | purpose | compile cmd | output (verbatim, with source) | verified? |`
8. **Claims and numbers ledger**: `| N01 | claim | origin (heard/screen/code) | verify? | computable → checks? |`
9. **Vocabulary**: `| term | first ts | definition as given |`
10. **Course links**: back-links, deferred topics (forward links), overlap with prior notes.
11. **Figure and diagram opportunities**: `| D01 | concept | what to show | tier (basic-ascii / advanced-png) | data source |`
12. **Emphasis and assessment signals**: `| ts | what | signal (emphasised / quizzed room / exam / repeated / skip) |`
13. **Open questions and ambiguities**

Target length: 4–9k words for a 2 h class. Code is referenced by path, not copied, except screen-only outputs, which are copied verbatim.

**`screen-notes/shard-K.md`** (`reference/screen-notes-template.md`; validated by `check_screen_notes.py` against `frames.json`):
- Front matter: `shard: K/N, frames: "001-060", index: <path>, read: [...], skipped: [{n, reason}]`
- One section per read frame: `## F001 · 00:02:17 · full|detail-of-F0xx · relevance high|low`, then *shows* (one line), *verbatim* (fenced exact code or terminal text), *diagram* (enough detail to redraw: boxes, arrows, labels, addresses), *said* (from index), *concepts*.
- Every frame in the range must be either read or skipped with a reason. This is the guard against silently dropping 20 minutes of content.

**`research.md`** (`reference/research-template.md`):
- Front matter: `session, profile, mode (verify|full), verified_as_of, searches, fetched`
- **A.** Verification of digest §5 and §8: `| ID | claim | verdict (correct/incorrect/partly/unverifiable) | evidence ≤25 words | URL | accessed |`
- **B.** Version- and time-sensitive facts
- **C.** Deeper material per concept (C01…), with sources
- **D.** Worked-example seeds
- **E.** Interview angles
- **F.** Annotated reading list (type, title, author/org, year, URL, why, level)
- **G.** Source register: every URL used, with title, domain, primary/secondary, accessed date. **Only URLs actually fetched or returned by WebSearch.**
- `mode: verify` (tier=basic) writes only A, B and G.

### 3.6 Scripts

Every script has a PEP-723 header (`requires-python = ">=3.11"`) and calls `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`. Every script takes absolute paths, supports `--json OUT` where it reports, and exits non-zero on failure. Every script that writes calls `notes_lib.guard.assert_writable()`. Scripts in `scripts/` import `notes_lib` and `render` via `sys.path.insert(0, str(Path(__file__).resolve().parent))`. Video scripts are standalone.

**`notes_lib` API** (stdlib only; `profile.py` and `notefile.py` lazily import `yaml`, so their callers declare `pyyaml`):

```python
# platform.py
utf8_stdio(); os_kind() -> "windows"|"macos"|"linux"; plugin_root() -> Path   # scripts/..
default_data_dir() -> Path   # $LECTURE_NOTES_DATA > $CLAUDE_PLUGIN_DATA > %LOCALAPPDATA%/lecture-notes | ~/Library/Application Support/lecture-notes | ${XDG_DATA_HOME:-~/.local/share}/lecture-notes
resolve_data_dir(arg: str|None) -> Path (mkdir); norm(p) -> Path; is_under(path, root) -> bool  # case-insensitive on Windows
which_any(names) -> str|None; gpu_summary() -> {"nvidia":bool,"nvidia_name":str|None,"apple_silicon":bool}  # nvidia-smi -L / platform.machine; no heavy imports
# chrome.py
class ChromeNotFound(RuntimeError); INSTALL_HINT: str
find_chrome(explicit=None, data_dir=None) -> Path
#   order: explicit → $LECTURE_NOTES_CHROME → <data>/chrome-path.txt → OS candidates → PATH names → Playwright cache scan → raise
chrome_args(chrome, user_data_dir, port) -> list[str]
#   --headless=new (omitted if the binary name contains "headless_shell"/"headless-shell"), --remote-debugging-port,
#   --user-data-dir, --no-first-run, --no-default-browser-check, --disable-gpu, --disable-extensions,
#   --hide-scrollbars, --remote-allow-origins=*, --allow-file-access-from-files, + --no-sandbox if Linux and euid==0
# profile.py
class ProfileError(ValueError); DEFAULTS: dict
split_front_matter(text) -> (dict, body); find_profile(ref, *, data_dir=None, cwd=None) -> Path
load_profile(ref, *, data_dir=None, cwd=None) -> dict   # deep-merged with DEFAULTS; adds "_path", "_body"; validates required keys
session_info(profile, n) -> {"n","nn","title","stem","date"}   # n "2a" -> 2
tier_cfg(profile, tier) -> dict
resolve_repo_paths(profile, key, **fmt) -> list[Path]   # placeholders + globs; list values = alternatives
# notefile.py
@dataclass Note: path, meta, h1, num, title, body, legacy
parse_note(path) -> Note            # legacy = no front matter (H1 regex ^\d+[a-z]?\s*[·\-–—]\s*)
note_id(name) -> (num, slug); pdf_name(prefix, num, slug, tier) -> str
iter_blocks(body) -> Iterator[(kind, text, info)]    # prose|code|table|heading|quote|html; fence-aware
stats(body, closing_sections) -> {"prose_words","code_lines","table_rows","figures","links","gapfill_words","minutes"}
minutes(prose, code, rows) -> float                   # prose/150 + code*8/60 + rows*15/60
# guard.py
class GuardError(PermissionError)
set_guard(data_dir, protect, allow, hours=6, run_id="") -> Path    # <data>/guard.json
clear_guard(data_dir); load_guard(data_dir) -> dict|None (None if missing/expired)
check_path(path, guard) -> str|None; assert_writable(path, data_dir=None)
```

**Chrome candidates:**

| OS | Where to look |
|---|---|
| Windows | `%ProgramFiles%` and `%ProgramFiles(x86)%` Google\Chrome\Application\chrome.exe; `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`; Edge `msedge.exe` under both Program Files; `%LOCALAPPDATA%\Chromium\Application\chrome.exe`; Brave |
| macOS | `/Applications` and `~/Applications`: `Google Chrome.app/Contents/MacOS/Google Chrome`, `Chromium.app/…/Chromium`, `Microsoft Edge.app/…`, `Brave Browser.app/…` |
| Linux | `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`, `microsoft-edge(-stable)`, `brave-browser` |
| All | Playwright cache: `%LOCALAPPDATA%\ms-playwright`, `~/Library/Caches/ms-playwright`, `~/.cache/ms-playwright` |

The `INSTALL_HINT` says: install Chrome/Edge/Chromium, or run `uv run "<plugin>/scripts/install_chromium.py"`. Linux may also need `playwright install-deps`.

**Ingest and platform scripts (WP1):**

| Script | CLI | Output |
|---|---|---|
| `doctor.py` (deps: none) | `[--data-dir D] [--json OUT] [--clear-guard]` | Table plus JSON: `os, arch, uv, chrome{path,ok,hint}, gpu{nvidia,name,apple_silicon}, whisper_fast(cuda\|mlx\|none, heuristic), ocr_accel(directml\|coreml\|cpu), network.github, compilers{g++,clang++}, assets{katex,mermaid}, guard`. Exit 1 only if Chrome is missing. Prints "switch the NVIDIA GPU on for ~70× realtime Whisper" when no NVIDIA GPU is visible on Windows/Linux. |
| `install_chromium.py` (deps: playwright) | `[--data-dir D]` | Runs `python -m playwright install chromium`, gets `sync_playwright().start().chromium.executable_path`, writes `<data>/chrome-path.txt` |
| `resolve_inputs.py` (pyyaml) | `--profile REF --session N --out-root DIR [--tiers both\|basic\|advanced] [--video auto\|reuse\|run\|frames-only\|skip] [--data-dir D] [--dry-run]` | `<out>/sessions/NN/inputs.json`: `profile{id,path}, session{n,nn,title,stem,date,duration_min}, repo_root, out_root, session_dir, transcript{path,bytes}, recording\|null, whisper{path,source:repo\|session}\|null, frames{dir,index,count,transcript_md}\|null, video_action, slides[], classwork{dir,files}\|null, instructor_code{repo,dir,url}\|null, prior_notes{repo[],plugin{basic[],advanced[]}}, tiers[], paths{basic_src,basic_meta,basic_pdf,advanced_*,reports}, protect[], warnings[]`. **Refuses** if out_root is inside repo_root (unless `inside_repo_ok`). `video auto`: reuse repo frames if `index.md` exists; else `run` if a recording exists; else `skip`. |
| `prep_transcript.py` (none) | `<txt> --out DIR [--width 180]` | `header.json`: `Key: Value` lines up to the `====` rule; course, lecture title, start raw plus parsed candidates (m/d vs d/m; unambiguous when a part is over 12), duration_min, generator, model. Missing header is allowed. `transcript.wrapped.txt` (≤180 columns; replaces `fold`). `junk_runs.json`: spans of ≥5 repeated tokens ("you you…"), ≥3× "Thank you.", non-English hallucination markers ("E aí", "Legenda"), each with char offsets and approx time = offset/len × duration. |
| `fetch_code.py` (none) | `--repo O/N --dir Lec-2 --out DIR [--ref HEAD] [--ignore GLOB…] [--max-kb 512]` | One `git/trees/<ref>?recursive=1` API call, then `raw.githubusercontent.com` per file (raw does not count against the rate limit). Uses `GITHUB_TOKEN` if set. Skips ignore globs, binaries (NUL bytes in the first 8 KB) and oversize files. Falls back to `git clone --depth 1 --filter=blob:none --sparse` on 403/429. `manifest.json`: fetched[], skipped[{path,reason}], tree_sha. |
| `extract_pdf.py` (pymupdf) | `<pdf> --out DIR [--png] [--dpi 80] [--pages 1-5,9]` | `pages/NNN.txt`, optionally `NNN.png`, `outline.json`. Also used by note-qa to look at built PDF pages, because Read cannot render PDFs here. |
| `guard.py` (none) | `set --data-dir D --protect P… --allow A… [--hours 6] [--run ID]` / `clear` / `status` | Writes `<data>/guard.json` |
| `guard_hook.py` (none) | stdin = hook JSON | Takes `tool_input.file_path` or `notebook_path`; loads guard via `$CLAUDE_PLUGIN_DATA`, falling back to the default data dir. Blocked → stderr `"notes guard: <path> is inside protected <root> (subject repos are read-only). Clear with /notes:doctor --clear-guard if stale."` and exit 2. Otherwise exit 0. Any exception → exit 0 and log to `<data>/guard-errors.log` (fail-open). |

**Render (WP2):**

`build_pdf.py` deps: markdown-it-py≥3, mdit-py-plugins, pygments, pymupdf, websocket-client, pyyaml.

CLI: `--profile REF --tier basic|advanced --tier-dir DIR [--notes 02 02a…] [--notes-only|--compendium-only] [--html-only] [--chrome PATH] [--data-dir D] [--offline] [--json OUT]`. Source is `DIR/src`, output `DIR/pdf`, work dir `DIR/build`.

Pipeline:
1. Discover `*.md`, with README as chapter "Index", chnum 0.
2. § numbering. Section ids allow companions: `2a.3`.
3. Markdown → HTML with per-tier CSS, branding and cross-OS font stacks:
   - sans: `"Segoe UI","Helvetica Neue",Helvetica,Arial,"DejaVu Sans","Liberation Sans",sans-serif`
   - mono: `Consolas,"Cascadia Mono",Menlo,"DejaVu Sans Mono","Liberation Mono",monospace`
   - serif: `Cambria,Georgia,"DejaVu Serif",serif`
   - **no `system-ui`**, which can resolve to variable fonts
4. Inline KaTeX (CSS with fonts as data URIs) and Mermaid JS **only when the note uses them**.
5. One Chrome per invocation (recommended: a new target per document) → CDP `Page.printToPDF` with header (running title · `{short} · Basic|Advanced`) and footer `pageNumber / totalPages`. Margins ported from cpp.
6. If JS is present, poll `Runtime.evaluate("window.__ready===true")` (timeout 60 s). Collect `document.querySelectorAll('.katex-error').length` and Mermaid error SVG counts.
7. PyMuPDF: resolve pages with `SEC_RE = ^\s*§\s?(\d+[a-z]?(?:\.\d+)*)\s+\S`, write the outline and metadata, relativise `/Launch` links.
8. Compendium: cover + contents (fixed-width `···` placeholders, no `§`) + chapters. Pass 1 → resolve → substitute → pass 2 → verify drift → reprint if needed.
9. Write `build/manifest.json` per PDF: `{src, pdf, pages, bookmarks, images_expected, katex_errors, mermaid_errors, js_used, chrome}`.
10. Exit 1 on a missing figure (`MISSING FIGURE: …`), a JS error, or a Chrome failure.

- **Tier styling:** basic = cpp CSS (sans 9.4pt, compact). Advanced = CV look (serif body around 9.6pt, sans headings), with `accent2` for rules and a cover kicker "BASIC · Revision" / "ADVANCED · Deep dive".
- **Legacy notes** (no front matter) must build; this is the regression target.

`render/` package:
- `layout.py`: `MARGIN_MM=15`, page constants, shared with audit
- `css.py`: template plus `css_for(profile, tier)`
- `markdown.py`: md → html: fences/pygments/`{run}` stripping, math, mermaid, figurise/captions, callouts, glyphs, tables, links, meta block
- `cdp.py`: launch, print, JS ready, error probe
- `paginate.py`: resolve, substitute, outline, link fix
- `assets.py`: KaTeX 0.16.11 from `https://registry.npmjs.org/katex/-/katex-0.16.11.tgz` (extract `package/dist`) and Mermaid 10.9.1 `mermaid.min.js` from jsDelivr, cached under `<data>/cache/assets/`, sha256-pinned (fill the constants after the first fetch). `--offline` with an empty cache and a note that needs them → clear error.

`audit_pdf.py` (pymupdf): `<pdf|dir>… [--manifest M] [--json OUT] [--margin-mm 15]`.
- **Problems** (exit 1): text past the right content edge (+3 pt slack); leftover `···`; Type 3 fonts (list chars via ToUnicode, ported from CV); zero bookmarks on a PDF over 2 pages; embedded image count < `images_expected`; katex/mermaid errors from the manifest.
- **Warnings:** sparse pages (<220 chars and no image covering ≥30% of the page; the cover is exempt); raw-markdown leftovers (```` ``` ````, `](`, more than 3 `**`).

**Video (WP3):** standalone scripts; all paths explicit.

PEP-723 dependency markers:
- `keyframes.py`: `imageio-ffmpeg, numpy, pymupdf, rapidocr>=3`, `onnxruntime-directml; sys_platform=='win32'`, `onnxruntime; sys_platform!='win32'`. macOS wheels include the CoreML EP.
- `transcribe.py`: `faster-whisper>=1.1`; `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*` with marker `(sys_platform=='win32' or sys_platform=='linux') and (platform_machine=='AMD64' or platform_machine=='x86_64')`; `mlx-whisper` and `imageio-ffmpeg` with `sys_platform=='darwin' and platform_machine=='arm64'`.

| Script | CLI | Behaviour |
|---|---|---|
| `transcribe.py` | `<video> --out FILE.json [--backend auto\|faster-whisper\|mlx] [--device auto\|cuda\|cpu] [--model large-v3-turbo] [--hotwords S] [--language en] [--require-fast]` | **auto:** Apple Silicon with `mlx_whisper` importable → mlx; else `cuda_libraries()` (Windows `os.add_dll_directory` + PATH; Linux ctypes RTLD_GLOBAL preload) then `ctranslate2.get_cuda_device_count()>0` → CUDA float16 batch 16; else CPU int8 batch 4 with the message "no CUDA device visible (is the NVIDIA GPU switched on?) — CPU ~1 h for 2 h". A mid-decode CUDA RuntimeError → retry on CPU. `--require-fast`: exit 3 immediately when only CPU is available. **mlx path:** decode audio with the imageio-ffmpeg binary (`-ac 1 -ar 16000 -f f32le -`) into a numpy array; `mlx_whisper.transcribe(audio, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language, word_timestamps=True, initial_prompt=hotwords, condition_on_previous_text=False, hallucination_silence_threshold=2.0)`; regroup with `phrases()` adapted to dict words. Output schema as today plus `"backend"`. Keep VAD, `language`, signature filtering. |
| `keyframes.py` | `<video> --out DIR [--whisper FILE] [--transcript TXT] [--ocr-device auto\|gpu\|cpu] [--destroy 0.02] [--min-dwell 3] [--text-unit 4] [--px-unit 0.0025] [--keep-cache] [--no-pdf]` | Port verbatim: survey 320×180, two-level webcam mask, states, prune, trim, grab with 15 s LEAD (MPEG-TS), OCR cache, strokes, greedy cover, detail crops, scale_for, outputs (index.md, JPGs, frames.json, frames.pdf, transcript.md). **OCR provider:** `avail = onnxruntime.get_available_providers()`; auto order Dml → CUDA → CoreML, using RapidOCR params `EngineConfig.onnxruntime.use_dml` / `use_cuda` / `use_coreml`. If the engine fails to build or the first inference throws, fall back to CPU. Record the provider in frames.json, index.md and stdout. Decode stays on CPU (NVDEC measured 4× slower). `load_speech` takes `--whisper` / `--transcript` instead of RES paths. |
| `pipeline.py` (no deps) | `<video> --out DIR [--transcript TXT] [--hotwords S] [--language en] [--whisper auto\|force\|skip] [--device …] [--ocr-device …] [--reuse] [--keep-cache]` | Runs `uv run transcribe.py --out DIR/whisper.json` (`auto` adds `--require-fast`; exit 3 → frames-only with a notice), then `uv run keyframes.py --out DIR/frames --whisper …`. `--reuse` skips steps whose outputs exist. Writes `pipeline.json` (timings, backend, device, provider, decisions). Line-buffered progress for background runs. |

**Figures and QA toolkit (WP4):**

| Script | CLI / API | Notes |
|---|---|---|
| `figures/notefig.py` (lib; numpy, matplotlib) | Constants `INK MUTED RULE BLUE ORANGE GREEN RED PURPLE TEAL GREY CYCLE W=7.4`; rcParams on import (dpi 180, DejaVu Sans, sizes from CV); `figure(name)` (name regex `^\d{2}[a-z]?-[a-z0-9-]+$`); `save(fig, name)` (fit titles, audit if enabled, PNG only, `metadata={"Software": None}` for byte-determinism); `clean`, `spines`, `show`, `grid_numbers`, `arrow` (ported); new `box(ax,xy,w,h,text,…)`, `memory_strip(ax, cells, addr0, step, …)` (labelled byte/word cells with addresses, for stack frames and struct layouts), `schematic(ax)`; `main(argv=None)` handles `--list`, `--audit`, names; output dir from `$NOTEFIG_OUT` | Port of make_figures.py lines 49–279 plus main(). Drop CV-only helpers (photo, bright_patch, gauss, sobel). |
| `figures/example.figures.py` | Two figures: `02-stack-frame` (memory_strip) and `02-latency-ladder` (log-scale bars labelled SCHEMATIC) | The template figure-author copies. Header: `sys.path.insert(0, os.environ["NOTEFIG_PATH"])` |
| `make_figures.py` (no deps) | `--script F --out DIR [--list] [--audit] [names…] [--json OUT]` | Sets `NOTEFIG_PATH=<plugin>/scripts/figures` and `NOTEFIG_OUT`; runs `uv run --script F …`; checks every registered name produced a PNG; reports sha256s; exit code passes through |
| `verify_numbers.py` (numpy, sympy) | `<meta dir \| *.checks.py>… [--json OUT]` | Runs each file with `runpy` after injecting `ok(cond,label,detail="")`, `close(got,want,tol=1e-6,rel=None,label="")`, `section(t)`, `np`, `math`, `Fraction`, `Decimal`. Prints a CV-style `RESULT:` line. Exit 1 on any failure or exception. |
| `check_snippets.py` (pyyaml) | `<note.md>… --profile REF [--work DIR] [--timeout 10] [--json OUT]` | `{run}` / `{run nocheck}` / `{compile}` fences → `<tier>/build/snippets/<stem>/<k>.<ext>`; compile and run with profile templates; compare against the following text block (normalise CRLF and trailing spaces; drop `$ ` lines). Missing toolchain → `skipped` with a reason (not a failure). |
| `note_stats.py` (pyyaml) | `<note.md>… --profile REF --tier T [--json OUT]` | `notefile.stats` plus budget status per §1.3 |
| `lint_note.py` (pyyaml) | `<note.md>… --profile REF --tier T [--digest D] [--meta-dir M] [--legacy] [--json OUT]` | **Errors:** front matter keys; `§` present; H1 format; required sections (case-insensitive prefix match); code >120 columns; table >9 columns; image not `figures/*.png` or missing; external images; `{run}` without an output block; Mermaid when `diagrams: ascii`; figures when `figures: none`; placeholders (`TODO`, `TBD`, `XXX`, `lorem`, `[citation needed]`); gap-fill share over the cap (basic); coverage sidecar missing a digest concept (advanced: each needs ≥1 worked example; basic: every assessable concept present). **Warnings:** code 96–120 columns; manual heading numbers; disallowed raw HTML; banned phrases (advanced); Going Deeper over 8 bullets or with paragraphs over 40 words; check-yourself count out of range; dead `.md` links; under `min_figures`. |
| `check_citations.py` | `<note.md> --research R --profile REF [--online] [--json OUT]` | Distinct URLs, distinct domains, `required_domains`, `verified_as_of` present, URLs not in research §G → `unknown_urls` (warn; error if over 20%). `--online` HEAD/GET with 8 s timeout, warn-only. |
| `check_digest.py` | `<digest.md> [--duration-min N] [--json OUT]` | Front matter keys including `slug`; 13 sections in order; unique C/G/K/P/N/D ids; Gaps rows reference existing C ids; timeline gaps over 10 min unexplained → error |
| `check_screen_notes.py` | `<dir> --frames-json F [--json OUT]` | Every frame n is either a `## F###` heading or in `skipped` with a reason |
| `update_index.py` (pyyaml) | `--profile REF --tier T --tier-dir DIR` | Writes `src/README.md` deterministically: H1 "Index"; course facts; session → note table over all profile sessions (pending marked); per-note revision minutes (basic) or words and est. reading time (advanced); running class-correction table parsed from callouts (severity emoji, said → actual); gap-fill list (basic); source counts (advanced). Never emits `§`. |
| `qa.py` (pyyaml) | `--profile REF --tier T --tier-dir DIR [--notes 02…] [--digest D] [--research R] [--data-dir X] [--skip-build] [--legacy] --json OUT` | Order: lint → stats → numbers → snippets → citations (advanced) → figure files → update_index → build_pdf → audit_pdf, each via `uv run` subprocess. JSON: `{tier, ok, notes:[{stem, lint, stats, numbers, snippets, citations, figures, coverage}], build{pdfs,compendium}, audit{problems,details}, mechanical_failures[], content_failures[]}`. **Mechanical:** overflow, wide code, table columns, §, missing figure file, placeholders, raw markdown. **Content:** budget, coverage, citations, numbers, snippet mismatch, missing sections. Exit 0 iff no errors. |

### 3.7 Agents

All agents set `omitClaudeMd: true`. Each agent body begins: "Read, in order: `${CLAUDE_PLUGIN_ROOT}/reference/house-style.md`, <tier/template docs>, and the profile path in your task card. Write ONLY the files named in your task card. Never write under any `protect` path. Never Read a `.pdf` (use `extract_pdf.py` PNG/text). Return ≤150 words: status ok|partial|failed, files written, counts, flags."

| Agent | model | tools | maxTurns | Inputs (task card) | Writes |
|---|---|---|---|---|---|
| `frame-reader` | sonnet | Read, Write, Grep, Glob | 60 | frames dir, `index.md`, frame range, screen-notes template | `screen-notes/shard-K.md` |
| `digest-builder` | sonnet | Read, Write, Grep, Glob, Bash, WebFetch | 120 | inputs.json, wrapped transcript + header + junk_runs, screen notes, Whisper `transcript.md` (grep only), code dir, slide pages, classwork, prior notes, profile, digest template | `digest.md` (then runs `check_digest.py` and fixes until clean) |
| `researcher` | sonnet | Read, Write, WebSearch, WebFetch, Grep, Glob | 120 | digest, profile `research.*`, mode verify\|full, research template | `research.md` |
| `basic-writer` | sonnet | Read, Write, Edit, Grep, Glob, Bash | 150 | profile, digest, research (§A/B), code dir, screen notes (grep), prior notes, note path, meta dir, budget | note md, `.coverage.json`, `.checks.py` (when there are computable claims), `.figplan.yaml` (only if figures allowed). Self-runs check_snippets, note_stats, lint_note. |
| `advanced-writer` | sonnet (profile `models.advanced_writer: opus` if supported) | Read, Write, Edit, Grep, Glob, Bash, WebFetch | 200 | Same as basic + full research + prior advanced notes (knowledge ledger) | Note md (and companion if over `companion_split_words`), coverage, checks, figplan. Writes the skeleton first, then fills section by section (no single giant Write). Self-runs check_snippets, lint, check_citations. |
| `figure-author` | sonnet | Read, Write, Edit, Bash, Glob | 80 | figplan, note md, `reference/figures.md`, `example.figures.py` | `meta/<stem>.figures.py`, `src/figures/*.png`; may edit **only** image lines and captions in the note. Runs `make_figures.py --audit`, Reads every PNG, iterates at most 3 times. |
| `note-qa` | haiku | Read, Edit, Bash, Grep, Glob, Write | 60 | tier dir, profile, digest, research | Runs `qa.py`; fixes **mechanical** failures only (wrap code to ≤95 columns, split wide tables, remove §, fix figure paths, placeholders); re-runs until clean or 3 loops; renders 2–4 pages per PDF to PNG (`extract_pdf.py --png --dpi 70`) and inspects them. Returns content failures **unfixed** for the orchestrator. |

Per-agent duties to put in the agent bodies, mined from the four CLAUDE.md per-turn workflows:

- **frame-reader:**
  - Read the `index.md` section for the range.
  - Open the JPGs 10–20 per message in parallel.
  - Skip only frames whose index text shows nothing examinable (desktop, empty browser tab, meeting UI), recording a reason. Never skip code, terminal or diagram frames.
  - Transcribe code and output **exactly**; describe diagrams precisely enough to redraw.
- **digest-builder:**
  - Read the wrapped transcript **in full**.
  - Apply the profile ASR table silently; never reproduce logistics.
  - For each junk run, check Whisper `transcript.md` (grep by time) and screen notes before marking it Reconstructed.
  - Answer inaudible room questions.
  - Tag emphasis signals.
  - Apply source precedence.
  - Detect gaps per §1.4 and correction candidates with confidence.
  - Pick `title_as_taught` and the slug.
  - Note new or amended instructor files ("repo moved on").
  - Never state a transcript-heard number as fact.
- **researcher:**
  - Verify every §5 and §8 item.
  - Prefer the profile's preferred sources and primary sources; cross-check blogs.
  - Cite only fetched or returned URLs, and record them in §G.
  - Flag version-sensitive facts.
  - Full mode adds per-concept depth, example seeds, interview angles and an annotated reading list.
- **basic-writer:**
  - Plan the outline within budget first.
  - Write from the digest; quote real instructor files by name.
  - `{run}` every snippet, compiling locally with the profile templates, and paste this machine's output. Label platform deltas in one line; outputs taken from frames get `{run nocheck}` / "as shown in class (platform)".
  - Include corrections only as verified in research §A.
  - Gap fills only for `fill_basic: yes`, marked `> **Gap fill:**`.
  - Going Deeper is links only and mentions the Advanced note.
  - Cut, don't split.
- **advanced-writer:**
  - Knowledge Ledger check against prior notes.
  - "What you need before this note".
  - Four-beat pattern; the per-concept ladder.
  - Every digest concept gets a thorough worked example; add adjacent concepts.
  - Inline citations only from research §G.
  - Interview questions with model answers and follow-ups; hands-on; annotated Further Study; Sources list.
  - Figure plan for every place a picture is the explanation (≥ `min_figures`).

### 3.8 Skills and orchestrator flow

**`/notes:lecture`** frontmatter:

```yaml
---
name: lecture
description: Generate Basic (revision) and Advanced (deep-dive, web-researched) PDF notes for one lecture of a subject profile. Manual workflow — run as /notes:lecture <profile> <session>.
argument-hint: "<profile> <session> [--tier both|basic|advanced] [--out DIR] [--video auto|reuse|run|frames-only|skip] [--redo digest|research|basic|advanced|all]"
disable-model-invocation: true
allowed-tools: Bash(uv run *) Read Write Edit Glob Grep Agent
---
```

Body: parse `$ARGUMENTS`; let `U = uv run "${CLAUDE_PLUGIN_ROOT}/scripts`; work end to end with no mid-way check-ins; flag scope calls in the report. The project's CLAUDE.md does not govern outputs; only `out_root` does.

1. **Doctor:** `U/doctor.py" --data-dir "${CLAUDE_PLUGIN_DATA}" --json <tmp>`. If Chrome is missing → run `U/install_chromium.py"`, then continue.
2. **Resolve:** `U/resolve_inputs.py" --profile <p> --session <n> --out-root <out> --tiers <t> --video <v> --data-dir "${CLAUDE_PLUGIN_DATA}"`. Read `inputs.json`.
3. **Guard:** `U/guard.py" set --data-dir "${CLAUDE_PLUGIN_DATA}" --protect <inputs.protect…> --allow <out_root> --run <id>`.
4. **Video:**
   - `video_action=run|frames-only` → **background** Bash: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/video/pipeline.py" "<recording>" --out "<session>/video" --transcript "<txt>" --hotwords "<asr.hotwords>" --whisper auto|skip`.
   - `reuse` → nothing to run.
5. **In the foreground meanwhile:**
   - `U/prep_transcript.py" "<txt>" --out "<session>/transcript"`
   - `U/fetch_code.py" --repo … --dir Lec-<n> --out "<session>/code"` (if instructor_code)
   - `U/extract_pdf.py" "<slide>" --out "<session>/slides/<stem>" --png --dpi 80` per slide deck
6. **Screen notes** (skip if no frames; reuse if present and `check_screen_notes` passes and digest not in `--redo`):
   - Split frames into shards of ≤60 frames.
   - Launch `notes:frame-reader` shards **in parallel** (one message).
   - Then `U/check_screen_notes.py" "<session>/screen-notes" --frames-json <frames.json>`. On failure, re-dispatch the missing ranges once.
7. **Digest:** `notes:digest-builder` (reuse an existing `digest.md` that passes `check_digest` unless `--redo digest|all`).
8. **Research:** `notes:researcher` with `mode=full` if Advanced is requested, else `verify` (reuse rule as above).
9. **Writers:** `notes:basic-writer` and `notes:advanced-writer` **in parallel**, one per requested tier. Task card: NN, slug from digest, note/meta paths, budget from `tier_cfg`, today's date, protect list.
10. **Figures:** for each tier with a non-empty figplan → `notes:figure-author` (both tiers in parallel).
11. **QA:** `notes:note-qa` per tier in parallel.
    - If `content_failures` → re-dispatch that tier's writer **once** with the QA JSON, then note-qa again.
    - Still failing → finish and report it honestly.
12. **Final check:** `U/qa.py" --profile <p> --tier <t> --tier-dir <dir> --digest … --research … --json "<out>/reports/qa-<t>-NN.json"` per tier. Deterministic; it also rebuilds the index and compendium.
13. **Clear guard:** `U/guard.py" clear --data-dir "${CLAUDE_PLUGIN_DATA}"`.
14. **Report:** write `<out>/reports/NN-report.md` (inputs used, reuse decisions, devices/backends, corrections table, gap fills, verification summary, QA results, budgets, absolute PDF paths, open questions, suggested ASR-table additions). Reply in chat in ≤15 lines: what was read, what the notes cover, what was corrected or verified, what was ambiguous, scope calls, PDF paths. Do not restate the notes.

**Other skills:**
- **`/notes:build <profile> [--tier …] [--out DIR] [--notes 02 …]`:** runs `qa.py` per tier; if there are mechanical failures, dispatches `notes:note-qa`. `disable-model-invocation: true`.
- **`/notes:doctor [install-chromium] [clear-guard]`:** runs `doctor.py` (plus the install or clear) and explains the table, including GPU-off advice.
- **`/notes:new-profile <repo-or-CLAUDE.md> [--id X] [--into-repo]`:**
  - Reads `profiles/README.md`, `_template.md`, the CLAUDE.md, and the repo's resource listing.
  - Writes `${CLAUDE_PLUGIN_DATA}/profiles/<id>.md`.
  - Validates with `resolve_inputs.py --profile <path> --session 1 --dry-run`.
  - `--into-repo` writes `.lecture-notes/profile.md` only when the user explicitly asks; this is the only path that may touch a repo.

### 3.9 OS / CPU / GPU matrix

| Stage | Windows | macOS Apple Silicon | macOS Intel | Linux |
|---|---|---|---|---|
| Decode | CPU, bundled ffmpeg (imageio-ffmpeg) | same | same | same |
| OCR | DirectML (any DX12 GPU) → CPU | CoreML → CPU | CPU (CoreML attempted) | CPU (OCR GPU saves only ~2–4 min; onnxruntime-gpu deliberately not used) |
| Whisper | CUDA fp16 via pip CUDA wheels + `add_dll_directory` → CPU int8 | MLX turbo → CPU int8 | CPU int8 | CUDA fp16 via pip wheels + ctypes preload → CPU int8 |
| Auto policy | No fast backend → frames-only (Whisper skipped, proportional speech hints); `--whisper force` runs CPU | | | |
| PDF | Chrome/Edge → Playwright Chromium | Chrome/Chromium/Edge/Brave → Playwright | same | PATH browsers → Playwright (snap-safe: build dir under out_root) |
| Figures | matplotlib Agg + DejaVu (bundled) | same | same | same |

---

## 4. Work packages

Conventions for every WP:
- **Owned files only.** Python only via `uv run` with PEP-723 headers.
- **Test outputs** go to `<plugin>/test-output/_wpN/` (gitignored).
- **Never write** under `<courses>/`. Every WP proves this with:
  ```bash
  P=<plugin>; mkdir -p $P/test-output; touch $P/test-output/.stamp-wpN
  # …run tests…
  find "<courses>" -newer $P/test-output/.stamp-wpN -type f | head   # must print nothing
  ```
- **Variables:** `CPP="<cpp-project>"`; `CLAUDE=$(ls -d ~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe | tail -1)`.
- **Git:** `git init` in P only (WP1). No commits unless the user asks.

**Ordering:**
- **Phase 0:** WP1 delivers the scaffold and `notes_lib` first (small).
- **Phase 1, in parallel:** WP1 (rest), WP2, WP3, WP4, WP5, WP6. They code against the interfaces in §3.
- **Phase 2:** WP7.

### WP1: Platform, ingest, guard, manifests
- **Owns:** `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.gitignore`, `hooks/hooks.json`, `scripts/notes_lib/*`, `doctor.py`, `install_chromium.py`, `resolve_inputs.py`, `prep_transcript.py`, `fetch_code.py`, `extract_pdf.py`, `guard.py`, `guard_hook.py`, `tests/selftest_ingest.py`, `tests/fixtures/ingest/**` (including `profile-cpp-min.md`, a minimal front matter pointing at the cpp repo).
- **Port from:**
  - Chrome candidate lists in cpp `pdf/build.py:60-80` and CV `build_pdfs.py:35-39`, generalised per §3.6.
  - Transcript header split regex `\n=+\s*\n` from cpp `video/keyframes.py:load_speech`.
  - The GitHub curl recipe in cpp CLAUDE.md step 3, which becomes `fetch_code.py`.
  - PyMuPDF text extraction plus utf-8 stdout from FMI/CV CLAUDE.md.
- **Acceptance:**
  - `doctor.py --json` finds `C:/Program Files/Google/Chrome/Application/chrome.exe`, reports `g++ 15.2.0`, and reports NVIDIA RTX 4070 when the GPU is on.
  - `resolve_inputs` for cpp session 2: transcript found; recording 438,658,520 bytes; frames reuse with count 149; whisper from repo; slides `[]`; classwork `null`; `video_action: reuse`. It refuses `--out-root "$CPP/x"`.
  - `prep_transcript`: title "Pointers and References", duration 120, date 2026-08-22 (unambiguous m/d).
  - `fetch_code` Lec-2 fetches `main.cpp, main.s, main.preprocessed, kartik.cpp, copy_bracket, fix_pointers.cpp` and skips `main` (binary), `main.o`, `.kartik.cpp.swp`.
  - `extract_pdf` on `L3.pdf` pages 1–2 gives text plus PNG.
  - The guard hook exits 2 inside a protected root and 0 outside.
  - `claude plugin validate` passes on the scaffold.
- **Verify:**
  ```bash
  cd $P
  uv run scripts/doctor.py --json test-output/_wp1/doctor.json
  uv run scripts/resolve_inputs.py --profile tests/fixtures/ingest/profile-cpp-min.md --session 2 --out-root $P/test-output/_wp1/cpp
  uv run scripts/prep_transcript.py "$CPP/resources/Pointers_and_References_-_Class_Scaler_Academy.txt" --out test-output/_wp1/t
  uv run scripts/fetch_code.py --repo KnightKnight27/scaler-HFT-C--2028 --dir Lec-2 --out test-output/_wp1/code
  uv run scripts/extract_pdf.py "$CPP/resources/L3.pdf" --out test-output/_wp1/l3 --png --dpi 60 --pages 1-2
  export CLAUDE_PLUGIN_DATA=$P/test-output/_wp1/data
  uv run scripts/guard.py set --data-dir $CLAUDE_PLUGIN_DATA --protect "$CPP" --allow $P/test-output
  echo '{"tool_name":"Write","tool_input":{"file_path":"<cpp-project>/notes/x.md"}}' | uv run --script scripts/guard_hook.py; echo "exit=$?"   # 2
  echo '{"tool_name":"Write","tool_input":{"file_path":"<plugin>/test-output/x.md"}}' | uv run --script scripts/guard_hook.py; echo "exit=$?"  # 0
  uv run scripts/guard.py clear --data-dir $CLAUDE_PLUGIN_DATA
  uv run tests/selftest_ingest.py
  "$CLAUDE" plugin validate "$P"
  ```

### WP2: PDF render and audit
- **Owns:** `scripts/build_pdf.py`, `scripts/render/*`, `scripts/audit_pdf.py`, `tests/selftest_render.py`, `tests/fixtures/render/**`. The fixtures include their own minimal `profile-cpp.md` and `profile-cv.md`. The fixture generator creates notes plus a PNG via PyMuPDF Pixmap at test time; no binaries are committed.
- **Port from cpp `pdf/build.py`:**
  - `iter_lines`, `gh_anchor`, `strip_md`
  - `number_headings` (+AUTHORED_RE; chnum supports `2a`), `discover`
  - `render_chapter`, `build_cover`, `build_contents` (no § in TOC), `add_heading_ids`, `relink`
  - CSS (basic tier base)
  - `print_pdf` over CDP (port, Page.printToPDF with displayHeaderFooter, ReturnAsStream/IO.read)
  - `resolve_pages` (new SEC_RE), `substitute`, `write_outline` + metadata, `build_compendium` two-pass with drift reprint
  - `classify_blockquotes`, `GLYPHS`
- **Port from cpp `audit.py`:** all checks.
- **Port from CV `tools/build_pdfs.py`:**
  - `make_md` (dollarmath; `\(` `\)` / `\[` `\]` rules), pygments `highlight` + pygments CSS
  - Mermaid fence + init/scale-to-fit JS + KaTeX auto-render + `window.__ready`
  - Wide-code font shrink, `shrink_tables`, `figurise` / `caption_html` / `check_figures` (copying figures into `build/`)
  - `tag_callouts` (lead-strong, strip "(…)"), `EMOJI` / `_svg` / `_blk` / `TICK` / `CROSS` with `_PRE_RE` exclusion
  - `hard_break_front_matter` (legacy), details force-open, `pre.long`
  - `finish_pdf` link relativisation (outline/footer replaced by CDP), `audit_type3`
  - CV CSS (advanced base)
- **New:** front-matter meta block and counterpart link; tier branding; cross-OS font stacks; `notes_lib.chrome`; inlined cached assets; JS-ready and error probe; manifest; output naming; build dir under the tier dir.
- **Acceptance:**
  1. **Synthetic fixtures** (basic: callouts, severity emoji, wide table, ASCII, `{run}` info string, gap fill; advanced: `$`/`$$` math, Mermaid, 2 figures, `<details>`, companion `02a` with cross-links, README) build. Audit reports 0 problems; Type 3 none; `images_expected` met; katex/mermaid errors 0; compendium contents page numbers equal the outline pages.
  2. **Negative fixtures:** a 130-column code line → audit overflow, exit 1. A missing figure → build exit 1 with `MISSING FIGURE`.
  3. **Legacy cpp regression:** copy `$CPP/notes/*.md` to `test-output/_wp2/legacy-cpp/src/` and build with `--tier basic`. Audit is clean. `CPP-02-…` has 7±1 pages and ≥13 bookmarks; the compendium has 24±2 pages and ≥46 bookmarks. These are the current PDFs' numbers.
  4. **Legacy CV regression:** copy `CV/note-src/02-feature-matching-and-classical-cv.md` plus its 8 referenced `figures/*.png` to `test-output/_wp2/legacy-cv/src/` and build with `--tier advanced`. Audit is clean; 8 images; 2 Mermaid diagrams rendered; 0 KaTeX errors; 24±4 pages; ≥40 bookmarks.
  5. **Offline:** after one online build, `--offline` succeeds.
- **Verify:**
  ```bash
  uv run tests/selftest_render.py
  uv run scripts/build_pdf.py --profile tests/fixtures/render/profile-cpp.md --tier basic --tier-dir test-output/_wp2/legacy-cpp --json test-output/_wp2/legacy-cpp.json
  uv run scripts/audit_pdf.py test-output/_wp2/legacy-cpp/pdf --manifest test-output/_wp2/legacy-cpp/build/manifest.json
  # same two lines for legacy-cv with profile-cv.md --tier advanced
  ```

### WP3: Video pipeline
- **Owns:** `scripts/video/pipeline.py`, `transcribe.py`, `keyframes.py`, `README.md`, `tests/selftest_video.py`.
- **Port from:**
  - cpp `video/transcribe.py` in full: `cuda_libraries`, `phrases`, `run` with signature filtering, CPU retry.
  - cpp `video/keyframes.py` in full, constants unchanged.
  - The README measured-numbers tables.
  - Then apply the §3.6 changes: explicit paths, OCR provider abstraction, mlx backend, `--require-fast`, hotwords/language flags, `--no-pdf`, provider/backend recorded.
- **Acceptance:**
  1. **Smoke test** on the short clip `Templates_and_Generic_Programming_-_Class_Scaler_Academy.mp4` (2 MB, reports 291 s): `transcribe --device cpu` produces valid JSON with `backend: faster-whisper, device: cpu, compute_type: int8`. `keyframes --ocr-device cpu` and `--ocr-device auto` (DirectML) both complete and print the provider.
  2. With the GPU off or hidden, `--require-fast` exits 3 and `pipeline --whisper auto` does frames-only.
  3. **Regression** (needs the GPU on, about 6–8 min): `pipeline.py` on the P&R recording gives **149±3 frames** (23±3 detail crops) and **629±5% Whisper segments**, against `$CPP/resources/frames/…/frames.json` and the repo whisper JSON.
  4. Nothing written outside `--out`.
- **Verify:**
  ```bash
  V="$CPP/resources/videos"
  uv run scripts/video/transcribe.py "$V/Templates_and_Generic_Programming_-_Class_Scaler_Academy.mp4" --out test-output/_wp3/tmpl.whisper.json --device cpu
  uv run scripts/video/keyframes.py "$V/Templates_and_Generic_Programming_-_Class_Scaler_Academy.mp4" --out test-output/_wp3/tmpl-frames --whisper test-output/_wp3/tmpl.whisper.json --ocr-device cpu
  uv run scripts/video/pipeline.py "$V/Pointers_and_References_-_Class_Scaler_Academy.mp4" --out test-output/_wp3/pr --transcript "$CPP/resources/Pointers_and_References_-_Class_Scaler_Academy.txt" --hotwords "C++ std::cout nullptr RBP RSP objdump"
  uv run tests/selftest_video.py   # compares counts with the repo outputs (read-only)
  ```

### WP4: Figures and QA toolkit
- **Owns:** `scripts/figures/notefig.py`, `scripts/figures/example.figures.py`, `make_figures.py`, `verify_numbers.py`, `check_snippets.py`, `note_stats.py`, `lint_note.py`, `check_citations.py`, `check_digest.py`, `check_screen_notes.py`, `update_index.py`, `qa.py`, `tests/selftest_qa.py`, `tests/fixtures/qa/**`. Fixtures: mini profile, notes (good and bad), digest, research, screen notes, `frames.json`, checks files, a `{run}` cpp snippet with correct and wrong expected outputs.
- **Port from:**
  - CV `make_figures.py:49-279` (style and helpers) and `:3285-3318` (main).
  - CV `verify_numbers.py` (`ok` / RESULT pattern).
  - The rest is new per §3.6. Note parsing uses `notes_lib.notefile`.
- **Acceptance:**
  - `example.figures.py` writes 2 PNGs; a second run gives identical sha256s; `--list` lists both; `--audit` finds no overlaps.
  - `verify_numbers` passes the good fixture and fails the bad one, naming the label.
  - `check_snippets` compiles the cpp fixture with g++: the correct output passes, the wrong one fails, and with the compiler unavailable it reports skipped.
  - `note_stats` on a copy of `$CPP/notes/02-…md` (legacy): about 1,537 prose words (±5%), about 124 code lines, about 32 minutes.
  - `update_index --legacy` over copies of cpp notes 01–03 yields a corrections table with **7 rows for note 02**, with severities matching the callouts.
  - `lint_note` flags every seeded error in the bad fixture.
  - `check_screen_notes` catches an omitted frame.
  - `check_digest` catches a missing section and an unexplained 15-min timeline gap.
  - `qa.py` on a fixture tier dir runs end to end (build/audit via WP2 once available; `--skip-build` before that) and classifies failures.
- **Verify:**
  ```bash
  uv run scripts/make_figures.py --script scripts/figures/example.figures.py --out test-output/_wp4/fig --audit --json test-output/_wp4/fig1.json
  uv run scripts/make_figures.py --script scripts/figures/example.figures.py --out test-output/_wp4/fig --json test-output/_wp4/fig2.json   # same sha256
  uv run tests/selftest_qa.py
  ```

### WP5: Knowledge (reference docs and profiles)
- **Owns:** `reference/*.md` (8 files), `profiles/README.md`, `profiles/_template.md`, `profiles/cpp.md`, `profiles/fmi.md`, `profiles/cv.md`, `profiles/devops.md`, `tests/selftest_profiles.py`.
- **Port map:**

| Target | Source material |
|---|---|
| `house-style.md` | The shared parts of all four per-turn workflows: read fully; ASR noise and junk runs; reconstruct and mark; answer inaudible questions; emphasis signals; class-correction format and severities (no nitpicking deliberate simplifications); verify before asserting; never invent; never restate transcript numbers; source precedence; jargon bold on first use; real names; comparison tables; frames never embedded; output only under out_root; report-back format |
| `tier-basic.md` | §1.3 Basic + §1.4; cpp "Hard Constraint" / earns-space; FMI "cut, don't split" + Further Study rules |
| `tier-advanced.md` | §1.3 Advanced; CV §1–§2 policies (Knowledge Ledger, four-beat, sentence/paragraph/note level, "the line you do not cross"); CV note structure and content requirements (generalised); DevOps quality bar |
| `note-format.md` | §3.4 verbatim, with examples |
| `figures.md` | CV "Figures" section; make_figures docstring rules (PNG only, seeded, reuse the note's numbers, SCHEMATIC labels, teaching captions, 7.4 in × 2–4 in); notefig API; runner commands |
| `digest-template.md`, `screen-notes-template.md`, `research-template.md` | §3.5, with one example row per table |
| Profiles | §3.3 |

- **Acceptance:**
  - Every profile loads through `notes_lib.profile.load_profile` with no errors.
  - `resolve_inputs --dry-run` for `cpp 2`, `fmi 1`, `cv 2` and `devops 1` finds a transcript.
  - `profiles/cpp.md` contains the 16-row ASR table and the 13-session table, and contains **no** session-2 correction facts (RIP/RSP, `_Z3addii`, etc.).
  - Each reference doc is under 400 lines.
  - Templates pass their own checkers: `check_digest` on the digest template example.
- **Verify:**
  ```bash
  uv run tests/selftest_profiles.py
  for s in "cpp 2" "fmi 1" "cv 2" "devops 1"; do set -- $s; uv run scripts/resolve_inputs.py --profile $1 --session $2 --out-root $P/test-output/_wp5/$1 --dry-run; done
  grep -c "RIP\|_Z3addii" profiles/cpp.md   # 0
  ```

### WP6: Orchestration (skills and agents)
- **Owns:** `skills/lecture/SKILL.md`, `skills/build/SKILL.md`, `skills/doctor/SKILL.md`, `skills/new-profile/SKILL.md`, `agents/frame-reader.md`, `digest-builder.md`, `researcher.md`, `basic-writer.md`, `advanced-writer.md`, `figure-author.md`, `note-qa.md`.
- **Content:** §3.7 and §3.8 exactly, including frontmatter, task-card format and return contract.
  - Every script invocation uses `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/…"` with quoted absolute paths. No `&&`, pipes or `$()` in skill commands, so they work in both PowerShell and Bash.
  - Task cards list: `plugin_root, profile, session, nn, slug, today, session_dir, inputs, digest, research, note_out, meta_dir, prior_notes, protect, budget`.
- **Acceptance:**
  - `claude plugin validate` passes with no errors, and passes `--strict` or with only justified warnings.
  - `claude plugin details notes@lecture-notes` (after a local install) lists 4 skills and 7 agents.
  - Each SKILL.md is under 500 lines.
  - Every agent has `omitClaudeMd: true`, a `model`, and `tools`.
  - Every path or CLI flag used in the prompts exists in §3.6. Check by grepping the flags against each script's `--help`.
- **Verify:**
  ```bash
  "$CLAUDE" plugin validate "$P"
  "$CLAUDE" plugin validate --strict "$P"
  grep -L "omitClaudeMd: true" agents/*.md   # prints nothing
  ```

### WP7: Integration, E2E (C++ session 2), README
- **Owns:** `README.md`, `tests/snapshot.py`, `tests/e2e/cpp-02-answer-key.md`, `tests/e2e/check_e2e.py`.
- **README:** what the plugin does; the two tiers; install (marketplace add/install/reload); commands; profiles (lookup order, `.lecture-notes/profile.md`, `/notes:new-profile`); output layout; OS/GPU matrix (§3.9); requirements (uv, plus Chrome/Edge/Chromium or auto-install); first-run downloads (Whisper ~1.6 GB, CUDA wheels ~1 GB, KaTeX/Mermaid ~4 MB, Playwright ~150 MB if needed); token cost notes (frames and research dominate; digest and research are reused on re-runs); troubleshooting (GPU off, CUDA DLLs → WSL2, Chrome missing, Type 3, offline assets, stale guard); privacy (video stays local); development (validate, selftests).
- **`snapshot.py`:**
  - `take <root> --out F.json`: path, size, mtime_ns, and sha256 for files under 64 MB; size and mtime only for larger files.
  - `diff A.json B.json`: exit 1 on any difference.
- **Acceptance:** §5 passes. The plugin installs through the local marketplace. All selftests pass.

---

## 5. Integration and end-to-end test: C++ session 2

**Inputs (read-only):**
- `<cpp-project>/resources\Pointers_and_References_-_Class_Scaler_Academy.txt` (78 KB, header: 120 min, 8/22/2026)
- `resources\videos\Pointers_and_References_-_Class_Scaler_Academy.mp4` (MPEG-TS, 2:16:04). Not re-processed: `--video reuse`.
- `resources\frames\Pointers_and_References_-_Class_Scaler_Academy\` (149 frames, 23 detail crops, about 89k image tokens, plus `index.md`, `frames.json`, `transcript.md`)
- `resources\Pointers_and_References_-_Class_Scaler_Academy.whisper.json` (629 segments, large-v3-turbo, CUDA)
- GitHub `KnightKnight27/scaler-HFT-C--2028/Lec-2`: `main.cpp, main.s, main.preprocessed, kartik.cpp, copy_bracket`, plus the newer `fix_pointers.cpp`, which the digest should flag as "repo moved on"
- Prior note for cross-links and ledger: `notes/01-why-cpp-and-the-memory-hierarchy.md`
- No slides (there is no `L2.pdf`), no classwork (`../Classwork/2` is absent)
- Writers must **not** read the existing hand-written `notes/02-…md`. That note is only used afterwards as a human quality benchmark.

**Procedure** (cwd = P, so the cpp CLAUDE.md never loads):

```bash
mkdir -p $P/test-output/e2e
uv run tests/snapshot.py take "<cpp-project-parent>" --out test-output/e2e/before.json
"$CLAUDE" plugin validate "$P"
"$CLAUDE" -p --plugin-dir "$P" --output-format json --max-turns 300 \
  --allowedTools "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,Agent,Skill" \
  "/notes:lecture cpp 2 --tier both --out <plugin>/test-output/cpp --video reuse" \
  > test-output/e2e/run.json
uv run tests/snapshot.py take "<cpp-project-parent>" --out test-output/e2e/after.json
uv run tests/snapshot.py diff test-output/e2e/before.json test-output/e2e/after.json   # must exit 0
uv run tests/e2e/check_e2e.py --out test-output/cpp --profile cpp --session 2 --run-json test-output/e2e/run.json
```

Also do the interactive install once:
```bash
"$CLAUDE" plugin marketplace add "<plugin>"
"$CLAUDE" plugin install notes@lecture-notes
"$CLAUDE" plugin details notes@lecture-notes
```

**Expected outputs:**
- `test-output/cpp/sessions/02/{inputs.json, transcript/, code/Lec-2/, screen-notes/shard-1..3.md, digest.md, research.md}`
- `basic/pdf/CPP-02-<slug>-basic.pdf`, `CPP-00-index-basic.pdf`, `CPP-Compendium-basic.pdf`
- The same three under `advanced/pdf/…-advanced.pdf`
- `reports/02-report.md`, `qa-basic-02.json`, `qa-advanced-02.json`

**Hard checks in `check_e2e.py`** (exit 1 on any failure):
1. All expected files exist. `check_screen_notes` reports all 149 frames accounted for. `check_digest` is clean.
2. `audit_pdf` on both pdf dirs: 0 problems (no overflow, no placeholders, no Type 3, figures and math clean).
3. Both `qa-*.json` have `"ok": true`.
4. **Basic:**
   - prose words in [1500, 2500] (warn up to 2750); computed minutes ≤ 35
   - 0 figures (cpp house style); at least one ASCII ```` ```text ```` diagram
   - ≥1 `Gap fill` callout and gap-fill share ≤ 20%
   - check-yourself has 5–8 questions
   - `{run}` snippets pass or are skipped with a reason (g++ 15.2 is present, so expect pass)
5. **Advanced:**
   - ≥10 distinct cited URLs over ≥4 domains, including `en.cppreference.com`
   - all cited URLs are in research §G (≤20% unknown)
   - `verified_as_of` equals the run date
   - ≥2 figures embedded and rendered; coverage sidecar covers every digest concept
   - all required sections present
6. Both notes cite the real instructor file (`Lec-2/main.cpp`) and contain its `int add(int x, int y)` code.
7. The snapshot diff is empty: nothing written into the cpp tree.

**Soft checks** (printed score from `tests/e2e/cpp-02-answer-key.md`, which comes from the hand note and README rows #1, 2, 5, 6, 7, 8, 13, 15 plus the pop/RSP 🟡 one):
- The digest §5 should flag ≥6 of these 9: RSP≠RIP 🔴; uninitialised `100` is UB at -O0 only 🔴; AT&T vs Intel are notations, not ISAs 🟠; `_Z3addii` is Itanium mangling 🟠; `add` is not limited to EAX/EDX 🟠; endianness is a CPU property, not an OS one 🟠; `int` is not 8 bytes on 64-bit 🟡; `pop` does not step one byte 🟡; `cout` is buffered, not a syscall per `<<` 🟡.
- Both tiers include the two 🔴 items.
- Concept coverage includes: no-header main; `#include` as paste (`-E`); the -E/-S/-c/link pipeline; namespaces; reading assembly; calling convention (System V vs Windows); stack frame and prologue/epilogue; RIP/RBP/RSP; stale-stack UB; two's complement; endianness and serialisation; the `kartik.cpp` + `copy_bracket` demo.
- Also record token use and cost from `run.json` in the report.

---

## 6. Risks and open questions

- **Untestable on this machine:** the mlx Whisper and CoreML OCR paths, and Linux CUDA preload. They are implemented with CPU fallbacks and labelled "unverified until run on a Mac/Linux box".
- **KaTeX fonts over file://** can be blocked; this is why assets are inlined as data URIs. If inlining Mermaid (about 3 MB per HTML) slows Chrome, load it once per Chrome instance instead.
- **The guard hook fires on every Write/Edit in all sessions** while the plugin is enabled (about 100–200 ms of uv startup). A crashed run leaves the guard active for up to 6 h; `/notes:doctor clear-guard` clears it. Bash-tool writes are not hooked; scripts self-guard instead.
- **Agent `model` override** via profile `models:` depends on the Agent tool exposing a `model` parameter. Otherwise the frontmatter default applies.
- **Cost estimate:** roughly 1M tokens per session for both tiers, dominated by frames (~100k image tokens) and research. Basic-only is roughly 40% of that. Measure it in E2E.
- **GitHub unauthenticated rate limit** is 60/h (13 were left during planning). Mitigated with raw URLs, `GITHUB_TOKEN`, and a git sparse-clone fallback.
- **Open question: real output location.** Today outputs go to plugin `test-output`, or `${CLAUDE_PLUGIN_DATA}/output`. Should outputs eventually live inside each subject repo (via the profile's `output.root` + `inside_repo_ok`)?
- **Open question: updating earlier notes.** cpp and FMI rules say a later session that extends an earlier topic should update the existing note. v1 only flags this in the report. Should v2 edit earlier plugin-generated notes?
- **Open question: Opus for advanced.** Should `advanced-writer` default to Opus for quality, at higher cost?
- **Open question: figures in cpp basic.** The house style says no; the plan keeps ASCII only.

### Critical Files for Implementation
- <plugin>/scripts/build_pdf.py (with scripts\render\*.py): the unified renderer, ported from <cpp-project>/pdf\build.py and <cv-project>/tools\build_pdfs.py
- <plugin>/skills/lecture/SKILL.md: the orchestrator flow (§3.8)
- <plugin>/scripts/notes_lib/profile.py (with notefile.py, chrome.py, guard.py): the shared interfaces every script depends on
- <plugin>/profiles/cpp.md: subject knowledge derived from <cpp-project>/CLAUDE.md
- <plugin>/scripts/video/keyframes.py and transcribe.py: ported from <cpp-project>/video\

---

## 7. Orchestrator addendum (applies over anything above)

- **Models.** Design: plan with the strongest model, execute with a mid-tier one. Every work package is implemented by a Sonnet agent; plugin agents default to `model: sonnet` (`note-qa` may stay `haiku`). The E2E headless run in §5 adds `--model sonnet` to the `claude -p` invocation. Do not default anything to Opus.
- **Execution order.** WP1 runs first and alone (scaffold + `notes_lib` + `.claude-plugin/*`), because every other package imports `notes_lib`. Then WP2–WP6 run in parallel. Then WP7.
- **Shared workspace.** All packages write into the same `<plugin>` tree. Write **only the files your package owns** (§2/§4). If you need an interface another package owns that isn't there yet, code against the §3 spec and note it in your report — don't create the other package's files.
- **Subject repos are read-only.** Never write anything under `<courses>/`. Test outputs go under `<plugin>/test-output/_wpN/`.
- **No commits.** `git init` (WP1) is fine; don't commit.
- **Report back** in ≤250 words: files written, acceptance criteria met / not met (with the failing output), deviations from the plan, and anything the next package needs to know.

## 8. WP1 as-built notes (read before coding against notes_lib)

- `chrome_args()` returns flags only — launch with `[str(chrome), *chrome_args(chrome, user_data_dir, port)]`.
- `iter_blocks` yields `(kind, text, info)`: `info` is the fence info string for code, an `int` level for headings, `None` otherwise. `iter_blocks_ex` additionally yields line numbers.
- `Note` has an extra `raw` field; `profile["_path"]` is a `Path`.
- `stats()` counts heading words as prose and every fence/table line; cpp notes 01–03 compute to 22.2 / 30.4 / 31.5 min. Use these as the calibration numbers (not 22.5/31.7/31.9).
- `inputs.json` has extra keys: `paths.*` per tier + session dir, `frames.frames_json`, `recording_bytes`. Frame count for cpp session 2 is **149** (from `frames.json`; the dir has 153 entries).
- Extras: `notes_lib.platform.write_json`, `fetch_code --force-clone`, `install_chromium --no-install`. `fetch_code --max-kb` default is 1024 (`main.preprocessed` is 760 KB).
- **Slides are handwritten, image-only PDFs** (`L1.pdf`, `L3.pdf`, `L4_.pdf`, `L5_.pdf`): `extract_pdf.py` yields empty text and warns — agents must read the `--png` page images.
- A Playwright Chromium is installed under `test-output/_wp1/pw` (fallback testing only; the real Chrome is found first).

## 9. Integration notes from WP2–WP6 (read before WP7)

- **Renderer (WP2).** `--notes` limits per-note PDFs only; the compendium always covers all notes unless `--notes-only`. `build/manifest.json` is merged across partial builds; `pdfs[]` entries carry `kind` (note|index|compendium), `name`, `pages`, `bookmarks`, `images_expected`, `katex_errors`, `mermaid_errors`, `js_used`, `overflow_blocks`. Chrome shrinks pages with over-wide code, so overflow is detected at build time and recorded in the manifest — always audit **with** the manifest (default `../build/manifest.json`). Build exit: 0 clean, 1 missing figure / JS / Chrome / asset, 2 usage. Advanced body font is CV's 10.2px root. Legacy regressions match the current PDFs exactly (cpp 02: 7 pages / 13 bookmarks; compendium 24 / 46).
- **Video (WP3).** `--out` is required on all three scripts; pipeline adds `--backend --model --no-pdf`. `frames.json` stays a list (with `ocr_provider` per entry); provider/backend record is in `meta.json`. Audio is decoded via imageio-ffmpeg (faster-whisper 1.2.1 + av 19 breaks). Whisper segment count depends on hotwords (cpp profile list → 617; short list → 788).
- **QA (WP4).** `qa.py` JSON: `{tier, ok, profile, tier_dir, notes:[{stem,num,lint,stats,numbers,snippets,citations,figures,coverage}], index, build, audit, mechanical_failures[], content_failures[], warnings[], steps[], duration_s}`; failures `{check, code, note, line, msg}`. `qa.py` defaults digest/research to `<tier-dir>/../sessions/NN/`. `make_figures --audit` exits 4 on overlaps. `--legacy` accepted by lint_note, update_index, qa.
- **Profiles (WP5).** cpp has all 14 sessions (Fast Orderbooks = n 14, dated between 12 and 13), real stems and dates; `handouts: ["resources/*.docx"]`; slides L2/L6/L7/L8 exist (handwritten, image-only). cpp ASR table is 20 rows.
- **Ingest (WP1).** `extract_pdf.py` also reads .docx/.pptx (text + media, `--png` no-op). `resolve_inputs` emits `slides_detail[]` and `handouts[]` with `kind`.
- **Skills/agents (WP6).** Agents got Edit (incremental fill via `@@FILL@@` markers); task cards add `data_dir, out_root, tier, tier_dir, figures_dir, mode (write|revise), qa_report`; note-qa writes per-loop JSON + content-failures.md under `<session>/logs/`; after the video pipeline, resolve_inputs is re-run with `--video reuse`.
- **Session 2 now has slides** (`resources/L2.pdf`, handwritten) — the E2E digest should read them via `extract_pdf.py --png`.
- The cpp repo's `.git/FETCH_HEAD` / `.git/gk` change from the user's editor background fetch; the E2E snapshot must exclude `.git/`.

## 10. Fixed project layout (supersedes §3.2, `out_root`, `--out`, `repo_root`)

The user opens Claude Code **in a subject project folder** and runs the plugin there. The plugin reads from and writes into that folder only. There is no separate output root.

```text
<Subject project>/                  ← cwd for /notes:*  (found by walking up to .notes/profile.md)
├── resources/                      ← the USER drops inputs here; read-only to the plugin
│   ├── <stem>.txt                  transcripts (Scaler header block optional)
│   ├── videos/<stem>.mp4           recordings (optional)
│   ├── *.pdf  *.pptx  *.docx       slides and handouts
│   └── README.md                   written by /notes:init: what to drop where
├── notes/                          ← generated markdown sources (editable; rebuildable to PDF)
│   ├── basic/     NN-<slug>.md  README.md  figures/*.png
│   └── advanced/  NN[a]-<slug>.md  README.md  figures/*.png
├── pdf/                            ← the deliverables
│   ├── basic/     {PREFIX}-NN-<slug>-basic.pdf  {PREFIX}-00-index-basic.pdf  {PREFIX}-Compendium-basic.pdf
│   └── advanced/  …-advanced.pdf
└── .notes/
    ├── profile.md                  subject profile (course facts, sessions, style)
    └── work/                       regenerable intermediates — safe to delete, git-ignored
        ├── sessions/NN/            inputs.json transcript/ code/ slides/ video/{whisper.json,frames/} screen-notes/ digest.md research.md logs/
        ├── meta/{basic,advanced}/  <stem>.checks.py .figures.py .figplan.yaml .coverage.json
        ├── build/{basic,advanced}/ html, chrome profiles, manifest.json, snippets/
        └── reports/                NN-report.md  qa-<tier>-NN.json
```

**Rules**
- **Project root:** `--project DIR`, else walk up from cwd to the first dir containing `.notes/profile.md`; none → error "run /notes:init". The profile is `<project>/.notes/profile.md` (a `--profile <id|path>` override is still accepted, e.g. for selftests).
- **Profiles:** replace `repo_root` with `resources_dir` (default `"resources"`, relative to the project; an absolute path is allowed for an external, read-only resources tree). All `paths.*` are relative to `resources_dir`, with built-in defaults so a new project needs only course facts + sessions: `transcripts: "{stem}.txt"`, `recordings: "videos/{stem}.mp4"`, `slides: []`, `handouts: []`, `frames: "frames/{stem}"` and `whisper: "{stem}.whisper.json"` (optional, *existing* outputs to reuse read-only), `classwork: null`. Optional `prior_notes_external: [abs dirs]` (read-only legacy notes for cross-links / knowledge ledger). Shipped `profiles/*.md` become **templates**: no absolute paths, no machine-specific roots (OS-agnostic).
- **Writes:** only under `notes/`, `pdf/`, `.notes/`. `resources/` (and any external `resources_dir` / `prior_notes_external`) is protected by the guard during a run. The old "refuse if out_root inside repo_root" check is gone; there is no out_root.
- **Video pipeline output** goes to `.notes/work/sessions/NN/video/`. Existing `resources/frames/<stem>/` + `resources/<stem>.whisper.json` (legacy cpp layout) are reused read-only when `--video reuse|auto` finds them.
- **Prior notes** for the knowledge ledger: the project's own `notes/<tier>/` plus `prior_notes_external`.
- **`notes_lib/layout.py`** (new, stdlib only) is the single source of these paths:
  ```python
  class ProjectError(RuntimeError)
  find_project(start: Path|None=None, explicit: str|Path|None=None) -> Path
  @dataclass class Layout:
      root: Path
      @classmethod def for_project(cls, explicit=None, start=None) -> "Layout"
      profile_path -> root/".notes/profile.md";  work -> root/".notes/work"
      resources(profile) -> Path            # resources_dir resolved (abs or root-relative)
      notes(tier) -> root/"notes"/tier;  figures(tier) -> notes(tier)/"figures"
      pdf(tier) -> root/"pdf"/tier
      session(nn) -> work/"sessions"/nn;  meta(tier) -> work/"meta"/tier
      build(tier) -> work/"build"/tier;   reports -> work/"reports"
      ensure(tiers) -> None                 # mkdir notes/<t>/figures, pdf/<t>, work subdirs
  ```
- **Script CLIs:** every script that took `--out-root`/`--out`/`--tier-dir` now takes `--project DIR` (default: discovered) and derives paths from `Layout`; explicit overrides stay for tests: `build_pdf.py --src DIR --pdf-dir DIR --build-dir DIR`, `qa.py`/others likewise. `resolve_inputs.py --session N [--project DIR] [--profile REF] [--tiers …] [--video …] [--dry-run]`.
- **Skills:**
  - **new** `/notes:init [--profile <id|path> | --from-claude-md <path>] [--id X]` — scaffolds the tree above in cwd (or `--project`), writes `.notes/profile.md` (copy of a shipped template, or drafted from a CLAUDE.md as /notes:new-profile did), `resources/README.md`, and appends `.notes/work/` to the project `.gitignore` (create if absent). Never overwrites an existing profile without `--force`. `/notes:new-profile` folds into this (keep it as a thin alias or remove it).
  - `/notes:lecture <session> [--tier both|basic|advanced] [--video …] [--redo …] [--profile …]` — no `--out`.
  - `/notes:build [--tier …] [--notes NN …]`, `/notes:doctor` unchanged in spirit.
- **Agents:** task-card paths come from `Layout` (session dir, notes/<tier>, figures dir, meta dir, pdf dir); writers write `notes/<tier>/NN-<slug>.md`.
- **Existing subject repos stay untouched** (the user's earlier decision). The E2E uses a fresh test project, not the cpp repo.
- **E2E (supersedes §5 paths):** test project `<e2e-project>/` (outside the plugin dir — writes under the plugin root are denied in headless runs — and outside `<courses>`). Set it up with `/notes:init --profile cpp` semantics (or the init script), then **copy** the session-2 inputs from the cpp repo into its `resources/`: the transcript, `videos/<stem>.mp4`, `L2.pdf`, the capstone `.docx`, `frames/<stem>/` and `<stem>.whisper.json`; set `prior_notes_external` to the cpp repo's `notes/` (read-only) in the test project's profile. Run the headless session **with cwd = the test project** (`--permission-mode acceptEdits`, prompt via stdin, `--model sonnet`), command `/notes:lecture 2 --tier both --video reuse`. Snapshot `<cpp-project-parent>` (excluding `.git/`) before/after — diff must be empty. Expected outputs: `notes/{basic,advanced}/02-<slug>.md`, `pdf/{basic,advanced}/CPP-02-<slug>-{basic,advanced}.pdf` + index + compendium, `.notes/work/...`.

## 11. Work with whatever resources exist (auto-discovery) — supersedes the "transcript required" rule

The user drops files into `resources/` with whatever names they come with. The plugin must find what belongs to each session, use course-wide documents everywhere, and work from any subset of inputs. Measured gaps before this section: a session with a recording + Whisper but no `.txt` failed ("no transcript found"); renamed files (`Lecture 3 - pointers part 2.txt`, `session3_slides.pdf`) failed; the profile's `paths.syllabus` was never read.

**11.1 Inventory** — new `scripts/notes_lib/discover.py` (stdlib only) + CLI `scripts/scan_resources.py [--project D] [--json OUT] [--write-sessions]`. Walk `resources_dir` recursively (skip `README.md`, dotfiles, `frames/*/` internals) and classify every file:

| kind | extensions / signal |
|---|---|
| `transcript` | `.txt` `.md` with a Scaler-style header (`Lecture Title:` … `====`) or long prose; `.vtt` `.srt` (timed — convert to text + timings) |
| `whisper` | `*.whisper.json` (existing pipeline output) |
| `frames` | a directory containing `index.md` + `frames.json` (existing keyframes output) |
| `recording` | `.mp4 .mkv .webm .mov .ts .m4v` (video) · `.m4a .mp3 .wav .aac .ogg .flac` (audio-only → Whisper only, no frames) |
| `slides` | `.pdf .pptx .key→unsupported-warn` whose name/first page looks per-session |
| `handout` | `.pdf .docx .pptx .md` documents |
| `syllabus` | name matches `syllabus|curriculum|course[ _-]?(outline|plan|structure)|schedule` or profile `paths.syllabus` |
| `code` | source files / archives (`.cpp .h .py .java .ipynb .zip …`) |
| `image` | `.png .jpg .jpeg .heic` (board photos — readable by agents) |
| `other` | anything else (listed, never silently dropped) |

Signals per file: normalised filename tokens; transcript header title + date; PDF/Office title and first-page text (via `extract_pdf.py` logic, text only, first page); any explicit session number pattern (`L3`, `Lec-3`, `lecture 3`, `session_03`, `S03`, `class 3`, `day 3`, `week 3`, a leading `03-`); dates in the name.

**11.2 Assignment to sessions** — score each file against every profile session and assign it to the best one when the score clears a threshold and beats the runner-up by a margin; otherwise it is **course-wide** (syllabus, handouts without a session signal) or **unassigned**:
1. exact `stem` match → certain;
2. transcript header title ≈ session title (normalised token similarity ≥ 0.8) or header date == session date → strong;
3. explicit session-number pattern == n → strong, **but** a number that is part of the title ("…References_2…" ≠ session 2) must not override a title match — compare against title tokens first;
4. filename/first-page token similarity to the session title (difflib ratio / token Jaccard) → medium;
5. files sharing a stem with an already-assigned file (e.g. `videos/<stem>.mp4`, `<stem>.whisper.json`, `frames/<stem>/`) follow it.
Explicit profile `paths.*` patterns still win when they match. Ties or low confidence → `unassigned[]` with the top-2 candidates and scores, printed by `resolve_inputs` and in the lecture report (so the user can rename or pin the file in the profile). Never guess silently.

**11.3 Sessions without a profile table** — if the profile has no `sessions` (new subject) or a transcript/recording matches none of them, derive sessions from the inventory: one per transcript/recording cluster, ordered by header date (else filename number, else name), titled from the header/filename. `scan_resources.py --write-sessions` (and `/notes:init` when `resources/` already has files) writes the derived table into `.notes/profile.md` — appending new sessions, never rewriting existing rows — and prints it for the user to check.

**11.4 Speech source — any one of these is enough**:
- Scaler/plain transcript (`.txt`/`.vtt`/`.srt`) → primary speech, read in full (as today).
- No transcript but an existing `whisper.json` → Whisper is the primary speech: the digest-builder reads the Whisper `transcript.md` / segments **in full** (the "grep, don't read" rule applies only when a primary transcript exists).
- No transcript and no Whisper but a recording → run the pipeline with Whisper **forced** (`--whisper force`; if only CPU is available, say how long it will take and proceed — it is the only speech source).
- Audio-only recording → Whisper only (no keyframes).
- No speech at all (only slides/handouts/code) → stop with a clear message unless `--allow-no-speech`, which writes notes from documents only and says so in the note front matter.
`resolve_inputs` therefore requires **at least one** of transcript / whisper / recording, not a transcript. `inputs.json` gains `speech: {primary: transcript|whisper|recording, sources: [...]}`.

**11.5 What every session gets** — `inputs.json` gains `course_docs[]` (syllabus + course-wide handouts, each `{path, kind, reason}`) given to the digest-builder (syllabus → expected topics for this session, deferred-topic links; course handouts like a capstone brief → relevance links) and `unassigned[]`. Prior/matching notes: the project's own `notes/<tier>/` notes for earlier sessions feed the knowledge ledger and cross-links; **an existing note for the same session is detected** and the writers revise it (`mode: revise`) instead of starting over, unless `--redo`; `prior_notes_external` notes are matched to sessions by the same 11.2 scoring (by NN prefix first).

**11.6 Selftests (must pass)** — fixture projects built at test time: (a) transcript only; (b) recording + whisper, no transcript (session 4 case); (c) renamed transcript + slides (`Lecture 3 - pointers part 2.txt`, `session3_slides.pdf`) → both assigned to session 3, not 2; (d) syllabus + course-wide docx → `course_docs`, used by every session; (e) audio-only `.m4a`; (f) an ambiguous file → `unassigned` with candidates; (g) new subject with no `sessions` → derived table; (h) `.vtt` transcript. Plus the existing cpp session-2 resolve result must be unchanged.

**11.7 Pending — do NOT implement until the user confirms the E2E results:** after a successful video ingest (keyframes + Whisper written and validated), offer to delete the recording to save space. Profile setting `video.delete_after_ingest: ask|never|always` (default `ask`). `ask` → the lecture skill asks the user (AskUserQuestion: "Delete resources/videos/<file> (438 MB)? Frames and the Whisper transcript are kept; re-ingesting will need the video again.") and only then calls a dedicated delete script; `always` still prints what it deleted. The guard keeps `resources/` read-only for everything else; only that script, with an explicit `--confirm` flag, may remove a recording, and never before both outputs validate.

## 12. Feedback round 1 (review of the cpp 02 + 04 PDFs) — supersedes conflicting earlier rules

Review verdict: **Basic is too small, and both tiers miss a lot of the visualisation that happened in class.** Also: go ahead with video deletion (keep the extracted frames). And from now on the plugin runs **inside the real cpp repo** `<cpp-project>` (the plugin is used in place on the real subject repo).

### 12.1 Basic tier = the whole class at class depth (not a 30-minute sheet)

- Basic covers **everything the class did**, at the depth the class went to, plus gap fills where the instructor fell short:
  - every concept in digest §2 with depth `explained|demoed|drilled`;
  - every demo (code + real output);
  - every board drawing and annotated slide (§12.2);
  - every room question;
  - every correction.

  It is *not* cut to a reading-time budget. "Cut, don't split" now applies only to material the class did not cover.
- New defaults, also used as the cpp profile values:
  - `tiers.basic.words: [3500, 7000]` for a ~2 h class, scaled ±50% with `duration_min/120`.
  - `minutes` is informational only (no fail).
  - The budget check **warns when under the minimum** ("too thin") and only warns, never fails, when over.
  - `gapfill_max_share: 0.30`.
- Each taught concept gets its own subsection: what was taught → the class's own example/visual → code + output → gotcha. A one-line summary of a taught concept is a lint warning ("thin coverage").
- Coverage check (both tiers, content failure):
  - Every digest concept with depth ≥ explained must have a `.coverage.json` entry **and** at least one example (code, worked number, or figure).
  - Basic must cover ≥ 95% of these concepts; advanced must cover 100%.

### 12.2 Class visuals in both tiers

**Digest §11 is renamed "Class visuals"** (same position). Every board drawing, diagram, annotated slide and pedagogically important screen in the screen notes gets a row:

`| V01 | F067 (00:53:31) | C07 | what it shows | quality clean|messy|partial | crop x0,y0,x1,y1 (fractions of the frame JPG) | treatment embed|redraw|both|text |`

- Terminal and code screens get `text`: transcribe them as code/text blocks, never as images.
- For every diagram it sees, the frame-reader must record a crop box (fractions of the frame image) and whether later frames extend the same drawing. Embed the *last, fullest* version.

**Treatments:**

| Treatment | Use it for |
|---|---|
| `embed` | Crop the class frame and include it as a figure. This is the default for board drawings and annotated slides, and it is the "visualisation that happened in class". |
| `redraw` | A clean generated figure via figure-author/notefig. Use it when the board version is messy, partial or wrong. Whenever a correction applies to the drawing, show the corrected version and caption it as corrected. |
| `both` | The class version plus a clean redraw, side by side or one after the other. |

- Basic: every V-row appears, as `embed` or `both`.
- Advanced: every V-row appears; prefer redraws where they add precision.

**New `scripts/class_figure.py`** (PEP-723: pillow, numpy):

```text
class_figure.py --frame JPG --out FILE [--crop-frac x0,y0,x1,y1] [--whiten|--no-whiten]
                [--max-width 1400] [--format png|jpg] [--json OUT]
```

- Crops the frame and auto-detects a light (whiteboard) or dark background.
- `--whiten` is the default for light boards: flatten the background to white, boost stroke contrast, and remove grid lines where possible.
- Trims uniform margins, caps the width, and produces deterministic output.
- Batch mode: `--from-visuals <stem>.visuals.yaml --frames-dir D --out-dir notes/<tier>/figures` processes every `embed|both` row.
- Output name: `NN-class-<slug>.png|jpg`.

**Renderer and lint:**

- Figures may be `.png` **or `.jpg`**. Class photos as JPEG keep PDFs small; generated figures stay PNG.
- Caption convention for class visuals: `![…](figures/02-class-stack-frame.jpg "**From class, 00:53:31.** …")`. For redraws: `"**Redrawn.** …"`.
- New sidecar `.notes/work/meta/<tier>/<stem>.visuals.json`: `{V01: {files: [...], treatment}}`.
- `lint_note` and `qa.py` content-fail when a digest V-row (treatment ≠ text) has no figure in the note, and warn when an embedded class figure has no timestamp caption.

**House style change:**

- The old cpp rule ("frames are input only, never embed them; redraw as ASCII; no images") is **retired** in the plugin, in both the reference docs and the cpp profile.
- ASCII diagrams stay welcome for memory layouts, in addition to the class visuals, not instead of them.
- cpp `tiers.basic.figures` becomes `class+generated` (was `none`).

**Figure budget:** no cap on class visuals. Keep each one ≤ 1400 px wide at JPEG q≈85, so a basic PDF with about 30 class figures stays under ~15 MB.

### 12.3 Video deletion (implements §11.7)

**New `scripts/archive_video.py`** (PEP-723, no heavy deps):

```text
archive_video.py --project D (--session N | --all) [--confirm] [--dry-run] [--force] [--json OUT]
```

1. **Validate the ingest** for that recording. Required:
   - `frames.json` and `index.md` exist, with at least one frame, or `meta.json` says the video is blank;
   - `whisper.json` is valid with at least one segment, or VAD says the recording has no speech;
   - both belong to *this* recording (stem and size recorded in meta).
2. **Archive** the extracted outputs next to the inputs, where discovery already reuses them:
   - copy the frames dir to `resources/frames/<stem>/` (JPGs, index.md, frames.json, transcript.md, meta.json, frames.pdf);
   - copy whisper to `resources/<stem>.whisper.json`.

   Never overwrite a different existing archive without `--force`; if it is identical, skip it.
3. **Delete** `resources/videos/<file>`, only with `--confirm`, and print the bytes freed. `--dry-run` shows the plan. `--all` covers every session whose ingest is complete and archived; sessions whose frames already live in `resources/frames/` qualify directly.
4. This is the **only** script allowed to write into `resources/`. Its code carries an explicit, narrow exemption: only `resources/frames/<stem>/`, `resources/<stem>.whisper.json`, and deleting the one recording. The guard hook keeps blocking Write/Edit tools in `resources/`.

**Profile setting:** `video: {delete_after_ingest: ask|never|always}`, default `ask`.

**`/notes:lecture` last step** (after PDFs and QA):

| Setting | Behaviour |
|---|---|
| `ask` | AskUserQuestion: "Delete `resources/videos/<file>` (<size>)? Frames and the Whisper transcript are archived in resources/ and kept; the notes don't need the video again." Run archive_video with `--confirm` only on yes. |
| non-interactive / headless | Never delete; report the command to run. |
| `always` | Archive and delete, then report it. |

**New `/notes:archive-videos [--all|<session>] [--yes]` skill** wraps the script. Unless `--yes`, it asks once, listing every file and its size.

**Reuse:** once archived, `resolve_inputs` finds `resources/frames/<stem>/` and `<stem>.whisper.json` (already supported), and `video_action` is `reuse` even with no recording.

### 12.4 Running inside the cpp repo

- The plugin project root is `<cpp-project>`, with `.notes/profile.md` created from the cpp template via `init_project.py`.
- Existing content must stay untouched:
  - `notes/*.md` (hand notes 01–03, `README.md`);
  - `pdf/*` (old PDFs, `build.py`, `audit.py`);
  - `video/`;
  - `CLAUDE.md`.
- **The guard allow-list narrows to the tier subfolders** (`Layout.guard_roots`): `notes/basic/`, `notes/advanced/`, `pdf/basic/`, `pdf/advanced/`, `.notes/`. Everything else in the project is protected, including `resources/` (apart from archive_video's exemption).
- The hand notes in `notes/*.md` are legitimate prior/benchmark material. List them via `prior_notes_external: ["notes"]` (relative to the project) for cross-links and the knowledge ledger. Writers may read earlier sessions' hand notes, but **not** the hand note for the session being written (same rule as the E2E).
- `init_project.py` on an existing folder may only:
  - create `.notes/`, `notes/basic|advanced/` and `pdf/basic|advanced/`;
  - append `.notes/work/` to `.gitignore` (the existing `resources/` ignore stays).

  It must **not** write `resources/README.md` into an existing resources folder, because `resources/` is git-ignored user data. Print the guidance instead.

## 13. Ongoing use: incremental, ordered, no duplicates + internal-folder rename (supersedes conflicting rules)

User asks:
- Rename the plugin's per-project folder so it is obviously plugin-internal, not something to read.
- Make the plugin usable for the **whole term**: as lecture content keeps arriving it generates the new notes **without duplication and in the correct order**, rather than as a one-time batch.

Development happens in the copy `<plugin-dev>/` while live runs use the original. The copy is synced back afterwards, so **work only in the dev copy**.

### 13.1 Rename `.notes/` → `.notes-plugin-internal/`

- `notes_lib/layout.py` constant: internal dir is `.notes-plugin-internal`. Profile path is `.notes-plugin-internal/profile.md`, work dir is `.notes-plugin-internal/work/`.
- `init_project.py` writes `.notes-plugin-internal/README.md`:
  > Managed by the notes plugin. Not meant for reading or editing. The one user-editable file is `profile.md` (course facts, session list, settings). `state.json` is the plugin's ledger — don't edit it. `work/` is regenerable scratch, git-ignored.
- The gitignore line becomes `.notes-plugin-internal/work/`.
- **Legacy migration.** When `find_project` finds `.notes/profile.md` and no new dir:
  - rename `.notes/` → `.notes-plugin-internal/`, fix the `.gitignore` line, write the README, and print what it did;
  - do this from `resolve_inputs`, `doctor`, `init_project` and the new `plan_updates`, through a shared `layout.migrate_legacy(root)`;
  - refuse if both exist.
- Update every script, test, skill, agent, reference doc, profile doc and README mention. The guard roots allow `.notes-plugin-internal/`.

### 13.2 The ledger: `.notes-plugin-internal/state.json`

`state.json` sits at the internal-dir root, not in `work/`, so wiping `work/` keeps the history. Owned by the new `scripts/notes_lib/ledger.py` (stdlib) and the CLI `scripts/ledger.py`.

```json
{"schema": 1, "plugin_version": "…", "updated": "…",
 "sessions": {"02": {"n": 2, "title": "…", "order": 2.0, "slug": "first-program-…",
    "inputs": {"transcript": {"path": "…", "size": 0, "mtime_ns": 0, "sha256": "…"},
               "whisper": {...}, "frames": {"path": "…/frames.json", "sha256": "…", "count": 149},
               "slides": [...], "handouts": [...], "classwork": {...},
               "code": {"repo": "…", "dir": "Lec-2", "tree_sha": "…"}},
    "inputs_hash": "…", "digest_sha256": "…",
    "tiers": {"basic": {"note": "notes/basic/02-….md", "pdf": "pdf/basic/CPP-02-…-basic.pdf",
                        "status": "ok|failed|partial", "qa_ok": true, "generated_at": "…", "mode": "write|revise"},
              "advanced": {...}}}}}
```

- **Fingerprints cover content inputs only:** transcript, whisper.json, frames.json (hash plus count), slides, classwork, and the instructor code `tree_sha` (from the fetch_code manifest).
  - A recording counts only through the frames and Whisper it produced. **Deleting an archived video must never mark a session changed**, because its frames/whisper fingerprints are unchanged.
  - Course-wide docs (syllabus, handouts) are recorded but don't trigger regeneration; `plan_updates` reports them as `course-docs-changed`.
- **`ledger.py` subcommands:**
  - `record --project D --session N --tier T --status ok|failed|partial [--qa-json F]`: snapshots current fingerprints from `inputs.json` plus the note/pdf paths.
  - `adopt --project D`: builds entries for notes already in `notes/<tier>/NN-*.md` that have no entry, fingerprinting the current inputs. This is how sessions generated before the ledger existed are taken over.
  - `show`, and `forget --session N`.

### 13.3 `scripts/plan_updates.py --project D [--tiers both|basic|advanced] [--check-code] [--json OUT]`

Runs discovery (with `--write-sessions` semantics for brand-new sessions, see 13.5), then classifies every session **in course order**:

| Status | When |
|---|---|
| `new` | Inputs exist, no note, no ledger entry. |
| `changed` | Inputs hash differs from the ledger. List exactly which inputs changed, e.g. "slides: L5_.pdf added". |
| `incomplete` | The last run is failed/partial, or a tier is missing. |
| `orphan` | A note exists without a ledger entry. Adopt it, then compare. |
| `up-to-date` | Nothing above applies. |
| `no-inputs` | Profile session with nothing in resources. |
| `course-docs-changed` | Informational only. |

- `--check-code` makes one GitHub tree call and flags `code-changed` sessions ("repo moved on").
- Output: a printed table plus JSON `{sessions: [{n, nn, title, order, status, reasons, action: write|revise|skip}], order: [...]}`.

### 13.4 No duplication

- **One note per (session, tier), file `NN-<slug>.md`:**
  - The slug is fixed at first generation and stored in the ledger.
  - `resolve_inputs` exposes `note_paths: {basic, advanced}` from the ledger or the existing file. Writers must use that exact path, even if `title_as_taught` changes later.
  - A slug changes only with `--rename-slug`, which renames the md, its figures and the PDF, and updates the ledger.
  - Companion notes `NNa` follow the same rule.
- **Pruning (`build_pdf.py --prune`, default on in qa.py):**
  - Delete PDFs in `pdf/<tier>/` that have no corresponding note, e.g. an old slug or a removed note.
  - Delete generated figures `notes/<tier>/figures/NN-*` that no note references. This applies to that session's own prefix only and never touches another session's figures.
- **Lint:** two notes with the same session number in one tier is an error. The index and compendium list each session once.
- **`/notes:lecture N` on an up-to-date session** says "up to date (inputs unchanged since <date>)" and stops, unless `--redo`. A changed session is dispatched with `mode: revise` against the existing note path.

### 13.5 Correct order

- **`order`:** profile sessions get an optional `order` number. If it is absent, sort by `date` when the sessions have dates and by `n` otherwise; ties go by `n`.
  - cpp: Fast Orderbooks (`n: 14`, 2026-10-04) sorts between 12 (10-01) and 13 (10-05).
  - Keep `NN` = session number as the stable file id. Display order uses `order`.
- **Course order is used for:**
  - index and compendium chapter order;
  - previous/next links in each note's meta block;
  - the "prior notes" handed to writers (the knowledge ledger and cross-links mean *sessions earlier in course order*, not smaller `n`);
  - `/notes:update` processing order.
- **New lectures that aren't in the profile:**
  - `plan_updates` appends derived sessions to `profile.md`: next free `n`, title and date from the transcript header or file name, and `order` from the date. It edits the YAML `sessions:` list in place, keeping comments, and never rewrites existing rows.
  - The appended rows are printed so the user can correct a title.

### 13.6 `/notes:update [--tier …] [--sessions 3,5] [--check-code] [--dry-run] [--yes]`

1. Run `plan_updates`.
2. Show the plan table: new / changed (with reasons) / incomplete / up-to-date.
3. Interactive → AskUserQuestion once to confirm the list, unless `--yes`. Headless → proceed.
4. For each session in course order, run the `/notes:lecture` flow:
   - mode `write` for new, `revise` for changed or incomplete;
   - skip the per-session index/compendium rebuild.
5. At the end:
   - rebuild the index and compendium once per tier;
   - video archive/delete per profile;
   - `ledger record` per session/tier (and on failure `status: failed`, so the next update retries it);
   - one combined report.
6. Never regenerate up-to-date sessions.

`/notes:lecture` also calls `ledger record` at its end; status `ok` requires `qa ok`.

### 13.7 Post-run cleanup

At the end of a successful run, delete the regenerable heavy copies:
- `work/build/<tier>/` chrome profiles and HTML;
- `work/sessions/NN/slides/*/pages/*.png` (re-extracted on demand);
- `work/sessions/NN/code/` (re-fetched).

Keep `digest.md`, `research.md`, `screen-notes/`, `inputs.json`, logs and reports. The flag `--keep-work` disables the cleanup.

### 13.8 Tests (dev copy)

Fixture project with three sessions and real small files. Must pass:
1. Generate a stub of session 1 by writing notes directly (no LLM), then `ledger adopt`; `plan_updates` says 1 up-to-date, 2 new.
2. Add a slide for session 1, and session 1 becomes `changed` (reason "slides: … added").
3. Delete session 1's recording after archive, and it stays up-to-date.
4. Drop a transcript for an unknown new lecture, and a session row is appended with the right `order` (between existing dates).
5. Duplicate-number lint error.
6. Prune removes an old-slug PDF and nothing else.
7. Legacy `.notes/` migration.
8. Course order with an out-of-number-order date (the Fast Orderbooks case) in the index/compendium order and the prior-notes list.

All existing selftests stay green.

## 14. Speed — target ≤ 30 min per lecture for each tier (measured bottlenecks)

Measured on the cpp session-4 fresh run (both tiers in parallel), from file timestamps:

| Stage | Duration |
|---|---|
| Startup | 2 min |
| Video keyframes (Whisper reused) | 4 |
| Frame readers (×3 parallel) | 3 |
| Digest | 7 |
| Research | 5 |
| Writers: basic ≈5, advanced ≈12 (parallel) | — |
| Figures | 3 |
| **QA loop** | basic 1, **advanced 12, over 5 iterations** |
| Builds | 0.5 |

Totals: basic ≈ 32 min, advanced ≈ 48 min. Targets: **basic ≤ 25 min, advanced ≤ 30 min**, wall clock from `/notes:lecture` start, both tiers in parallel.

### 14.1 Fast QA loop (biggest win, saves ~9 min)

**`qa.py --fast`:** run only lint + stats + figure-file + visuals/coverage checks. Skip:
- `build_pdf`;
- `audit_pdf`;
- `check_snippets` (cache by snippet hash — unchanged snippets are never recompiled; store results in `work/build/<tier>/snippets/cache.json`);
- `verify_numbers` (unless the checks file changed).

Target: < 10 s.

**Overflow pre-check without Chrome.** Code lines > 95 cols, tables > 9 cols, wide ASCII. Lint already flags these, so `--fast` catches them without building.

**note-qa (agent):**
1. Run `qa.py --fast`.
2. Fix **every** mechanical failure in **one** editing pass. Group by type; wrap all long lines at once.
3. Re-run `--fast`.
4. At most **2 fast iterations**, then **one** full `qa.py` (build + audit).
5. If the full run reports only overflow spans, do one targeted fix and one final full run. Hard cap: 3 full runs.

Write each loop's JSON as before.

### 14.2 Split research so writers start earlier (saves ~3–5 min)

The researcher runs as **two parallel dispatches**, same agent file, `mode` in the task card:

| Mode | What it covers | Output | Time |
|---|---|---|---|
| `verify` | Digest §5 corrections + §8 claims + version-sensitive facts | `research-verify.md` (§A, §B, §G) | ~2 min |
| `deep` | Per-concept depth, worked-example seeds, interview angles, reading list | `research-deep.md` (§C–§F + its own §G) | — |

- Run `deep` only when advanced is requested.
- `check_citations` / qa read **both** files (union of §G).
- Keep accepting a single `research.md` for older sessions.

**Ordering:**
1. digest → `verify` ∥ `deep`.
2. When `verify` returns → start **basic-writer** (it needs only §A/§B).
3. When `deep` returns → start **advanced-writer** (needs both).

The two writers still run concurrently once both are started. Because Agent calls are foreground (`run_in_background: false`), achieve this by issuing the dispatches in the same message where possible:
- Message 1: `verify` + `deep` researchers in parallel.
- Message 2: basic-writer + advanced-writer in parallel.

The basic-writer may start from verify alone; if verify finishes first, dispatch basic-writer **together with** the still-pending deep result handling. If the harness can't express that, accept message-level parallelism (research pair, then writer pair) — that still saves the serial research time because `verify ∥ deep` replaces one long research call.

### 14.3 Per-stage timing in every run

New `scripts/timing.py mark --project D --session N --stage NAME (start|end) [--note TEXT]` appends to `work/sessions/NN/timings.jsonl`. `timing.py report` prints a stage table and totals per tier.

- The lecture skill marks each stage: startup, video, frames, digest, research-verify, research-deep, writer-basic, writer-advanced, figures-basic, figures-advanced, qa-basic, qa-advanced, build, archive.
- The final report (`reports/NN-report.md`) includes the table and flags any stage > 1.5× its budget:

| Stage | Budget |
|---|---|
| video | 6 |
| frames | 4 |
| digest | 8 |
| verify | 3 |
| deep | 6 |
| basic writer | 8 |
| advanced writer | 13 |
| figures | 3 |
| qa | 3 |

### 14.4 Batch throughput in `/notes:update`

Video, frames, digest and research for different sessions are independent. Writers need course order (prior notes = earlier sessions).

**Pipeline the batch:**
- **Phase A** (per session, up to `--parallel K`, default 2): video pipeline → frame readers → digest → research. Throttle the video pipeline to one at a time on a single GPU.
- **Phase B** (strictly course order): writers → figures → QA → ledger record for session k start only when session k−1's notes are written.
- Session k's writers can overlap with session k+1's Phase A.
- **Finish:** index + compendium once.

**Implementation:**
- Phase A for several sessions = multiple orchestrated stage runs. Use `scripts/stage.py` / the existing scripts plus parallel Agent dispatches.
- If nesting limits make cross-session parallel agent dispatch impractical, keep sessions sequential but parallelise within a session. Report which approach was taken.

### 14.5 Advanced writer (optional, only if 14.1–14.2 don't reach ≤ 30 min)

When the digest has ≥ 10 concepts, split advanced writing across two writer dispatches:
- spine `NN` (first half of concepts + framing sections);
- companion `NNa` (second half);

with a shared outline written first, cross-linked. Measure first; implement only if needed.

### 14.6 Acceptance

- `qa.py --fast` < 10 s on the cpp 02 advanced note.
- The note-qa loop on a fixture with 30 seeded mechanical issues converges in ≤ 2 fast + 1 full run.
- `timing.py report` works on a fixture.
- The lecture skill issues the researcher pair and the writer pair as parallel dispatches.
- All selftests green.
- **Real measurement** (orchestrator does it after sync): the next cpp lecture run's timing table.

## 15. Next phase — implement when the project resumes

The user paused after the cpp session-2 notes were generated in the real repo. Resume order:
1. Sync the dev copy (§13–§14) back into the plugin.
2. Migrate the cpp repo `.notes/` → `.notes-plugin-internal/` and `ledger adopt` session 2.
3. User review of session 2.
4. Implement 15.1–15.2.
5. Generate the remaining sessions with `/notes:update`.
6. Run the sweep (15.2).

### 15.1 Maximum parallelism (supersedes §14.4's strict course-order Phase B)

Because the sweep (15.2) reconciles cross-session continuity afterwards, writers no longer have to wait for earlier sessions:

- `/notes:update` runs whole sessions concurrently, `--parallel K` (default 3–4, bounded by API rate limits and the subagent concurrency cap of 20).
- Within a session, everything independent runs in parallel:
  - frame-reader shards;
  - researcher `verify` ∥ `deep`;
  - basic ∥ advanced writers;
  - figure-author per tier.
- **Video pipelines** are the one shared resource. Serialise them, one GPU job at a time (a small lock file in the data dir). Run them first for all pending sessions, overlapped with the LLM stages of sessions that need no video.
- **Shared outputs** (tier index, compendium) are rebuilt once at the end. Per-session `qa.py` uses `--no-rollup`, so parallel sessions never write the same file. Ledger writes take a file lock.
- Prior-notes context for writers is "whatever earlier-in-order notes exist at dispatch time". The sweep fills the gaps.
- **Measured goal:** the 12-session cpp backlog in about the time of 3–4 sequential lectures.

### 15.2 Cross-note sweep: `/notes:sweep [--tier both|basic|advanced] [--sessions …] [--dry-run]`

A separate pass after all (or a batch of) notes exist. It finds what carries over between lectures and fixes continuity in both directions.

**1. Course concept map.** New `scripts/concept_map.py`. From every session's `digest.md` (cheap; no re-reading of transcripts), build `work/course/concepts.json`:
- concept → session where it was first introduced, sessions that reuse it, depth in each;
- corrections per concept;
- terms/notation used.

Concept identity is decided by an agent pass over the concept inventories, since names differ ("stack frame" vs "activation record"). Matching is done once and cached.

**2. Carry-over analysis** (a `sweep-analyst` agent per tier, reading the map plus note headings/summaries, not full notes) finds:
- **Back-references a later note should make** ("the stack frame from session 2"). Shown as a short `> **Builds on:** session 2, §2.4 — …` box, with links, instead of silently re-explaining.
- **Re-explanations to condense** in basic. The later note keeps a 1–2 line recap plus a link. In advanced, keep the depth but link the earlier treatment.
- **Forward links an earlier note should gain** ("revisited with the heap in session 4", "move semantics: session 6").
- **Corrections to propagate.** A misconception corrected in session 2 that recurs later gets a short pointer.
- **Terminology and notation drift.** Same concept, different names or symbols. Pick the earlier one and note the alias.
- **Carry-over examples.** An instructor example that recurs (e.g. `Child` struct, `add(12,13)`) is linked, not duplicated.

**3. Edit plan.** `work/course/sweep-plan.json` holds per-note, small, anchored edits (insert callout after heading X, replace paragraph Y with recap+link, add forward link in section Z). Shown to the user, and confirmed once interactively.

**4. Apply** with targeted edits: Edit, never rewriting whole notes. One edit agent per note, in parallel.

**5. Rebuild and record.**
- Rebuild the affected PDFs, index and compendium; run QA.
- `ledger record` with `mode: sweep`.
- The sweep does not mark notes `changed` for `/notes:update`: ledger inputs are unchanged.

**6. Re-run safely.** The sweep is idempotent. Applied edits are tagged `<!-- sweep:id -->` so a re-run updates rather than duplicates them. It also re-runs incrementally after `/notes:update` adds sessions (only edges touching new or changed sessions).

**Acceptance:**
- On the cpp notes, the sweep adds ≥1 correct back-reference in every note after the first.
- No duplicate callouts on a second run.
- Every link resolves to an existing section anchor in the compendium.
- QA stays green.

## 16. Improvement round before the full generation

Build all of this in the dev copy, sync once, then generate:
- the remaining **cpp** sessions (1, 3–14) with `/notes:update --parallel`;
- the **FDE** subject (`<fde-project>`): a new project with 11 transcripts, slides, data files and a syllabus; no recordings, no CLAUDE.md;
- the **sweep** on both.

### 16.1 Crop quality

User saw crops that were "slightly too tight or too loose". Examples: a board sentence cut mid-line ("When you started a…"), and the drawing app's colour palette column left in. `class_figure.py` becomes content-aware.

**Expand when ink is cut.** On the whitened canvas:
1. Find ink connected components. Use a small dilation so handwriting strokes join into words and shapes.
2. Any component that intersects the requested crop box **and** touches or crosses its edge pulls the box outward to include it fully.
3. Repeat until stable, capped at the canvas.
4. Then pad by ~3% of the box size with blank canvas.

**Trim when the crop is too loose.** Shrink to the ink bounding box of the included components plus padding.

**Remove the palette strip.** Detect a narrow column (< 7% of canvas width) at the right or left edge, separated from the main ink by a blank gutter, containing small saturated round blobs and icons at regular vertical spacing. Exclude it. Never remove anything that is not separated by a blank gutter.

**Determinism:** keep it.

**Report:** `--json` records the requested box vs the final box, and an `expanded_for_cut_ink` / `trimmed` / `palette_removed` flag for each.

**figure-author verifies every class figure.** It Reads each output image and checks:
- no text or drawing cut at an edge;
- no UI chrome;
- not mostly blank.

Then it adjusts `crop` in the visuals yaml and re-runs that row, at most 2 tries per figure.

**Tests:**
- a synthetic canvas with a sentence crossing the crop edge must be fully included;
- a synthetic palette column must be removed;
- real-frame regression: the cpp session-2 frame behind "From class, 00:37:23" must include the whole sentence. Find it from `notes/basic/02-*.md` and the visuals yaml in the cpp repo's plugin work dir, read-only.

### 16.2 Maximum parallelism (implements §15.1)

`/notes:update --parallel K` (default 4) runs whole sessions concurrently.

- **New agent `agents/session-runner.md`** (sonnet) runs the full per-session lecture flow for one session in batch mode (no rollup, no archive prompt), dispatching its own sub-agents.
- **Single flow definition:** move the per-session flow out of `skills/lecture/SKILL.md` into `reference/lecture-flow.md`. Both the lecture skill and the session-runner follow it.
- **Nesting:** update skill → session-runner → stage agents (depth 2). It stays within the 3-level limit.
- **Concurrency:** K sessions × at most 3 parallel stage agents stays under the cap of 20.

Serialise only the true shared resources:
- **GPU video pipeline.** `scripts/video/pipeline.py` takes a cross-process file lock (`<data>/locks/gpu.lock`, OS-agnostic via `msvcrt.locking`/`fcntl` or an atomic lockfile with a stale-PID check) and waits for it.
- **Ingest order.** The update skill launches all pending video ingests first (background, queued by the lock), then starts sessions whose inputs are ready. Sessions without video start immediately.
- **Ledger writes.** Take `<project>/.notes-plugin-internal/state.lock`.
- **Index and compendium.** Rebuild once per tier at the end (`qa.py --no-rollup` per session).

Prior-notes context is "whatever earlier-in-order notes exist at dispatch time". The sweep (16.3) fixes continuity.

**Failure isolation:** one session failing does not stop the others. It is recorded as `failed` in the ledger and retried by the next `/notes:update`.

**Final report** lists:
- per-session status and timing tables (`timing.py report`);
- total wall clock vs the sum of per-session times;
- cost per session where available.

### 16.3 Cross-note sweep (implements §15.2)

**`scripts/concept_map.py --project D [--json OUT]`** extracts every session's concept inventory, corrections, vocabulary and class visuals from the digests into `work/course/inventory.json`. The extraction is deterministic; identity matching is done by an agent (below).

**Agents:**
- **`sweep-analyst`** (sonnet). Reads the inventory and each note's heading outline, and writes:
  - `work/course/concepts.json`: canonical concepts with aliases, first-introduced session, reused in, and depth per session;
  - `work/course/sweep-plan.json`: small anchored edits per note, of these types:
    - `builds-on` back-reference callouts;
    - recap-and-link condensations (basic only);
    - forward links in earlier notes;
    - propagated corrections;
    - terminology aliases;
    - recurring-example links.

  Every edit carries `{id, note, tier, anchor (heading text), type, text, rationale}`.
- **`sweep-editor`** (sonnet). Applies one note's edits with Edit (never rewriting the note). It wraps each insertion in `<!-- sweep:ID -->…<!-- /sweep:ID -->`, so a re-run replaces rather than duplicates.

**`/notes:sweep [--tier both|basic|advanced] [--sessions …] [--dry-run] [--yes]` flow:**
1. concept_map
2. analyst
3. show the plan summary, confirming once (`--yes` / headless → proceed)
4. editors in parallel, one per note
5. `qa.py` per tier (full)
6. index + compendium rebuild
7. `ledger record --mode sweep` (does not change `inputs_hash`, so `/notes:update` ignores it)
8. report

It is incremental after `/notes:update`: only edges touching new or changed sessions are analysed (pass `--sessions`).

**Lint:** sweep markers must be balanced, and a `builds-on` link must resolve to an existing note or section.

### 16.4 Discovery for subjects like FDE

**Multi-session documents.** Filenames like `FDE_Session_2_3_vshare.pdf`, `Class 2-3`, `L2&3`, `sessions 4,5` assign the document to **each** listed session (`sessions: [2,3]` in the inventory). A range `2-4` means 2, 3 and 4. Never interpret a date as a range.

**Data and exercise files.** `.json .csv .xlsx .parquet .sql .ipynb .py .zip` are classified as:
- `data` (datasets);
- `exercise` (notebooks and solutions, e.g. `FlashEats_Class6_Student.ipynb`, `FlashEats_Class5_Solution.pdf`).

They are assigned by number (`Class5`, `Class6`, `class6` in `manifest_class6.json`); otherwise they are course-wide. Agents get them via `inputs.json` `data[]` / `exercises[]`. The digest notes which were used in class.

**`links.md` and other `.md`/`.txt` reference docs** that are not transcripts are classified `reference`, course-wide.

**Profile-less subjects.** `/notes:init --from-syllabus <pdf>`, or automatically when no CLAUDE.md exists, drafts `.notes-plugin-internal/profile.md` from:
- the syllabus text (course title, institution, assessment, session map);
- the transcript headers (title, date, duration);
- the discovered resources;
- `_template.md`.

It picks **generic tier rules** for a non-programming subject:
- no snippets compile step unless code languages are detected;
- `figures: class+generated`;
- research preferred sources chosen from the subject, e.g. for FDE: product and data-engineering primary sources, vendor docs, case studies.

The draft is printed for the user to check. In headless runs it proceeds and lists its assumptions in the report.

### 16.5 Corrections calibration and documentation

**Basic tier:**
- Keep 🔴 and 🟠 class corrections inline as callouts.
- Collect 🟡 slips into one compact table at the end of the note: `## Minor slips in class` with columns `| said | actual |`, one line each, linked from nowhere.
- Gap fills unchanged.

**Advanced tier:** keeps all corrections inline. Depth is the point.

**Instructor-repo drift is expected, not a correction.** Files added or changed after class, or class demos that differ from the repo, get a neutral `> **Repo note:**` callout. Never a Class correction, never a gap. Notes follow what happened in class and cite the repo version where it differs. Document this in `reference/house-style.md` and the README. Example: cpp `Lec-2` gained `fix_pointers.cpp` after class.

**Basic length criterion.** The basic tier is judged by **reading time and difficulty at class level**, not page count: large class figures legitimately add pages. `note_stats` reports:
- reading minutes (prose + code + tables, figures at 10 s each);
- a simple complexity indicator: average sentence length, and jargon terms per 100 words not defined in the note or earlier notes.

Warn only when basic reads harder than class level, e.g. undefined jargon density is high. Document this in `reference/tier-basic.md`.

### 16.6 Acceptance

- All selftests are green, including new ones for 16.1, 16.2 (lock, isolation, ordering), 16.3 (idempotent markers, plan apply on fixture notes), 16.4 (multi-session docs, data/exercise kinds, FDE-like fixture) and 16.5 (minor-slips table lint, repo-note never counted as a correction).
- `claude plugin validate` passes; SKILL.md < 500 lines.
- **Real dry runs:**
  - `scan_resources` on the real FDE folder (read-only, via a scratch project with an absolute `resources_dir`) assigns all 11 transcripts, `FDE_Session_2_3_vshare.pdf` to both 2 and 3, the class-numbered data/exercise files to their sessions, the syllabus and links as course-wide, and leaves nothing unassigned without a reason.
  - `plan_updates` on the real cpp repo, after migration, lists 1 and 3–14 as `new` in course order, with Fast Orderbooks before Capstone, and 2 as up-to-date.

## 17. Figure resolution (class figures were blurry)

**Cause.** Recordings are 1280x720. `keyframes.py scale_for()` saves the frames the agent reads at 0.5-1.0x to cap image
tokens (often 512 px wide), and `class_figure.py` cropped those and enlarged the crop to `--min-width` (bicubic). Slide
pages were rendered at 80 dpi for the same reason.

**Fix: two copies, the agent copy and a native-resolution copy.** The agent copy is unchanged (same names, same selection,
same token cost). Nothing the agents read changes; agents are told never to `Read` `hires/`.
- `keyframes.py` also writes `<frames>/hires/<same name>`: the same region (`frames.json` `region`) at scale 1.0, JPEG `-q:v 2`
  (`notes_lib/hires.py`; frames.json rows get `hires`, meta.json a `hires` block, index.md one line). `--no-hires` skips it.
  `keyframes.py <video> --out <existing frames dir> --hires-only` regrabs only the missing copies from `frames.json` (`t`,
  `region`, `file`) plus the video; idempotent; CPU ffmpeg, no GPU lock; writes `hires/hires.json` and touches nothing else.
- `pipeline.py --reuse`: a reused `frames/` without a complete `hires/` runs `keyframes.py --hires-only` (log `logs/hires.log`).
- `extract_pdf.py --png` also writes `<out>/hires/NNN.png` at `--hires-dpi` (default 200, 0 = off); `pages/NNN.png` stay at `--dpi`.
- `class_figure.py` prefers `<D>/hires/<name>` (frames) and `<out>/hires/NNN.png` (slide `page:` rows) over the low-res file; crop
  fractions are relative so they apply unchanged; all pixel logic scales with the image (ink mask pooled to ~480-960 px). It only
  enlarges (to `--min-width` 700, max 2.5x) when the source really is small. The crop geometry (content-aware box, trims) is
  computed on the low-res frame the agent verified and mapped onto the hires twin (deciding it on the hires pixels gave
  different crops: the ink analysis is resolution dependent), so figures show the approved region, only sharper. Defaults: `--max-width 1600`, JPEG q 90 / 4:4:4.
  `--json` reports per figure `source` (hires|lowres), `source_file`, `source_size`, `enlarged`. `--no-hires` forces low-res.
- `archive_video.py`: before a recording is deleted the archived `resources/frames/<stem>/` must hold `hires/` for every frame
  (regrabbed from the recording if missing); if that fails the recording is kept (`hires-missing`). `--no-hires` waives it.
  `hires/` is not part of archive identity (a re-run over an archive with/without it is still "identical").
- Existing projects: regrab with `--hires-only` for each frames dir whose video still exists, re-run `class_figure.py` from the
  existing visuals yaml into the same figure paths, rebuild the PDFs.
- Tests: `selftest_archive` (`t_class_figure_hires_preferred`, `t_archive_hires`), `selftest_ingest` (`t_extract_pdf` hires dpi),
  `selftest_video` (`test_hires`).

## 18. Pending references (stale "has no note yet" sentences)

**Problem.** Sessions are generated in parallel, so a writer on session N sometimes finds that an earlier session's note
does not exist yet and writes meta commentary: "Session 7 ... has no note in this set yet, so what it supplies is restated
here", "named without links", "session 9's note has not been written at the time of writing". After the batch those notes
exist, and the sweep (16.3) did not look for these sentences, so they stayed false.

**Writers.** For a cited session whose note is missing: cite it normally (session number, title, whatever must be restated)
inside an inline marker `<!-- pending-ref:07 -->...<!-- /pending-ref:07 -->`, with no link and no sentence about the note
set. Rule lives in `reference/house-style.md` ("Pending references"), `reference/note-format.md` (section 7) and both
writers (`agents/basic-writer.md`, `agents/advanced-writer.md`). `prerequisites` lists only existing notes.

**Detection (deterministic).** `scripts/notes_lib/pending.py` `scan(text)` finds, outside fenced code, front matter and table
rows: (a) `pending-ref` markers (with unbalanced-marker problems) and (b) legacy stale phrases (`has no note`, `no note ...
yet`, `note has not been written`, `notes do not exist`, `named ... without links`, `not linked here because`, `named by
session number`, ...; every alternative mentions a note, so "not written to RAM" never matches). Each item carries line,
end line, nearest heading, target sessions (from the marker, or from "session N" in the sentence / the 400 characters
before), the exact source text (`replaces`) and the sentence. `concept_map.py` writes them to `inventory.json` under
`pending` (`id` = `resolve-pending-<NN>[-<NN>]-from-<MM>[-k]`, `kind`, `tier`, `note`, `from`, `targets`, `target_notes`,
`present`, `missing`, `resolvable` = every target has a note in the same tier) and prints a summary line.
`--sessions` keeps the items whose note or target is listed.

**Sweep.** `sweep-analyst` MUST write one `resolve-pending` edit per `resolvable` item (exempt from the 6-per-note cap):
same content, claim about the note set deleted, targets cited as `[session N: <title>](NN-<slug>.md#anchor)` with an
anchor that exists in the target's outline. Items with a missing target get no edit and are listed under `unresolved`
(they stay in the note and are reported). `sweep-editor` swaps the marker span (or the stale sentence in `replaces`) for
an INLINE `<!-- sweep:resolve-pending-07-from-09 -->text<!-- /sweep:resolve-pending-07-from-09 -->`, so a re-run replaces
the text (idempotent) and a later sweep recognises it. `sweep_check.py --apply` is the reference implementation
(`apply_resolve_pending`, whitespace and `> ` tolerant); with `--plan` or `--apply` any resolvable pending item left is a
problem, items waiting for a missing note are printed as `note:` lines.

**Lint.** `lint_note.py`: `pending-marker` (error, unbalanced/nested), `pending-ref-resolvable` (warning, the target note now
exists in the same folder), `stale-note-ref` (warning, any legacy phrase). Link checks inside sweep spans now also cover
inline spans (`builds-on-link`). Inline `sweep` / `pending-ref` markers are invisible to block parsing and word counts
(`notefile._INLINE_MARK_RE`); the renderer already strips HTML comments.

**Tests.** `selftest_improve.t_pending_refs_inventory_resolve_and_lint`: inventory (markers, wrapped and blockquote legacy
sentences, false positives, `--sessions`, byte-stable), lint codes, plan apply twice (byte-identical), re-run replaces,
unresolvable marker survives and is listed, dangling anchor caught in an inline span, inline markers not counted as words.

## 19. Layout check (class files outside `resources/`)

**Problem.** Discovery walks only `resources/`. A transcript in the project root, a `videos/` folder at the root, a
`Downloads`-style subfolder of slides or a stray file in `notes/` was silently ignored.

**Design.** `scripts/notes_lib/layout_check.py` (stdlib only) is the single deterministic check; `check(project,
profile)` returns `[{path, rel, kind, suggested_destination, dest_rel, reason}]`. It reuses the vocabulary of
`notes_lib/discover.py` (extensions, `SLIDE_HINT`, `SYLLABUS_RE`, `explicit_numbers`, header parsing) and flags only on a
positive signal:

| Kind | Flagged when | Destination |
| --- | --- | --- |
| recording | video/audio extension (`.ts` only in a `videos/` folder or >= 1 MB) | `resources/videos/<name>` |
| transcript | `.vtt/.srt`; `.txt/.md` with a transcript header or a Scaler-style name | `resources/<name>` |
| slides, syllabus, handout | discover would call it slides/syllabus, or its name matches a session title | `resources/<name>` |
| data, exercise | a class number in the name (or stray parent folder) or a session-title match | `resources/<name>` |

A stray parent folder that carries a class number or a student/starter-pack word keeps its name
(`resources/Class5_Student_Pack/x.csv`), because discovery reads parent folder names. Recordings anywhere under
`resources/` are never flagged: `discover.inventory` walks it recursively.

**Never walked:** `.git`, `.notes-plugin-internal`, the configured `resources/`, `notes/<tier>`, `pdf/<tier>`, dot-folders,
virtualenvs, caches, build output, well-known code folders (`Classwork`, `src`, ...), `prior_notes_external`, and any folder
with a project marker (`pyproject.toml`, `package.json`, `CMakeLists.txt`, `Makefile`, ...). In the top level of `notes/`
and `pdf/` only recordings and transcripts are flagged.

**Wiring.** `init_project.py`, `scan_resources.py`, `plan_updates.py` print a "Files outside resources/" block and put the list
in their `--json` as `outside_resources`; `doctor.py` adds it to `project.outside_resources` and an advice line. Skills
`init`, `scan`, `update` and `reference/lecture-flow.md` (via the doctor JSON): interactive, ask once with
`AskUserQuestion` ("Move them into resources/ (Recommended)" / "Leave them"); headless, `--yes` or `--batch`: never move, list
the files with the exact command. Moving: `scan_resources.py --project D --fix-layout --confirm` (without `--confirm` it only
reports). It never overwrites: an existing destination, or two files wanting the same one, refuses that file and says so.

**Guard.** The PreToolUse hook (`guard_hook.py`) only intercepts the Write/Edit/NotebookEdit tools; a script run through Bash is
not subject to it, so no exemption entry is needed (unlike `archive_video.py`, which guards its own narrow writes). The move
code applies the same discipline itself (`_assert_exempt`): destination must be inside `resources/`, source a project file
outside it. `guard.assert_writable` is deliberately not called for the destination.

**Tests.** `selftest_discover.t_layout_check`: a fixture with stray transcript, root and `videos/` recordings, slides in a stray
folder, a student pack and a transcript in `notes/` are flagged with the right destinations; correctly placed files, code
folders, marker folders, `CLAUDE.md`, unrelated PDFs/CSVs, tier folders and legacy notes are not; without `--confirm` nothing
moves; `--confirm` moves them and refuses a clash without overwriting.
