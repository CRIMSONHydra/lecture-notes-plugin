# House style — rules for every subject and both tiers

Read this first, every run. Subject facts (persona, reader, ASR table, course map) are in the **profile**
named in your task card; tier rules are in `tier-basic.md` / `tier-advanced.md`; the file contract is in
`note-format.md`. Where a profile rule and this file disagree on a subject fact, the profile wins. Where
they disagree on safety (read-only repos, no invention), this file wins.

## 1. Authority and scope

- Your task card is the only authority on what to write and where. Write **only** the files it names.
- `resources/` (and any external prior-notes dir) is **read-only**. Never write under any `protect` path. Never run a tool that writes there.
- A subject project's own `CLAUDE.md` does not govern you (it is excluded on purpose). Its instructions to
  "write into notes/" or "rebuild pdf/" do not apply. Only the project layout is writable: `notes/<tier>/`,
  `.notes-plugin-internal/work/` and `pdf/`.
- Never `Read` a `.pdf` (the tool cannot render PDFs here). Use `extract_pdf.py` (text, and `--png` page
  images that you can `Read`). Handwritten slide decks have no text layer; read their PNGs.
- Python only through `uv run "<plugin>/scripts/x.py"` with quoted absolute paths. Never `pip`, never a venv.
- Never call `python`, `python3` or `py` directly; always `uv run python …` / `uv run --with PKG python …` / `uv run script.py` — bare python on Windows can trigger the Microsoft Store installer.
- No commits, no network writes. Web access only where your agent definition grants it.

## 2. Reading the sources

- **Read the transcript fully**, end to end, in chunks. The best asides are scattered, not front-loaded.
- Use the wrapped copy (`transcript.wrapped.txt`), never the raw single-line file. The header block gives
  course, title, date, duration: use it for front matter, and prefer the profile's `date` when ambiguous.
- Note what the instructor **emphasised**, **quizzed the room on**, **said is examinable**, **repeated**
  and **said to skip**. Tag these (see section 5). They decide depth and ordering.
- Transcripts are ASR output of a live room: Hinglish, filler, mic trouble, inaudible student answers.
  Extract the technical intent. Fix manglings silently using the profile's ASR table. **Never reproduce**
  logistics: attendance, mic checks, screen-share fights, jokes about handwriting, "can you hear me".
- **Junk runs** (`you you you`, `Thank you. Thank you.`, spurious non-English such as "E ai ... Legenda")
  are **dropped audio**, not content. Check, in order: Whisper `transcript.md` (grep by timestamp), screen
  notes, slides, instructor code. Only then reconstruct.
- Numbers heard in a transcript are unreliable (ASR mangles digits, sizes, versions, prices). **Never
  state a transcript-heard number as fact.** Verify it, or label it "read from screen in class" /
  "as heard".

## 3. Source precedence

| Need | Best source, then fallback |
| --- | --- |
| Code as typed | instructor's file (GitHub/classwork) > frame (screen notes) > speech |
| Program output, terminal text | frame (screen notes) > re-run it locally > speech |
| Diagrams, board drawings | frame (screen notes) > slides > speech |
| What was said, who asked what | Scaler transcript (read in full) |
| Timing; garbled stretches of the Scaler text | Whisper `transcript.md` (it was *heard*, so not "Reconstructed") |
| Definitions, standards, versions | primary source via research dossier > your own knowledge (mark it) |
| What was said, **no transcript** (Whisper primary) | Whisper `transcript.md`, read in full (it was *heard*, so not "Reconstructed"; its digits and terms are `heard`, never fact) |
| What was said, **no speech at all** (`--allow-no-speech`) | slides and handouts: say the note rests on documents only |
| Expected topics, deferred-topic owners | syllabus / profile course map; never present them as taught |

- Quote the **real file** and name it (`Lec-2/main.c`). Never reconstruct code that exists.
- When the instructor's repo has files newer than the transcript describes, say so ("repo moved on").
- When your local rebuild of a snippet differs from a class screen, show both and label the platform.
- **Class visuals are part of the class.** A board drawing or annotated slide is evidence *and* content:
  each digest "Class visuals" row (V01...) appears in the note as an **embedded crop of the class frame**
  (`embed`), a **clean redraw** (`redraw`), or both. Terminal and code screens are never images: transcribe
  them (`text` treatment). Mechanics, captions and the sidecar are in `figures.md`; `tier-basic.md` and
  `tier-advanced.md` say how each tier uses them. Redraw and label a drawing the class got wrong, as
  corrected. ASCII memory diagrams stay welcome **in addition to** class visuals, never instead of them.

## 4. Reconstruct, mark, answer

- Where audio is lost and nothing recovers it, reconstruct from code + slides + your knowledge, write it
  properly (no hole, no pretending the transcript said it) and mark it:
  `> **Reconstructed:** the audio drops here; this follows Lec-3/main.cpp.`
- Where the instructor asked the room something and the answer is inaudible, **answer it in the note**
  (usually as a Check-yourself question or a one-line aside). These are the best quiz material.
- Never leave a topic as a heading with nothing under it because "the audio was bad".

## 5. Emphasis signals

Tag during reading, carry into the digest (section 12), and act on in the note:

| Signal | Treatment |
| --- | --- |
| emphasised, repeated twice, "remember this" | depth, TL;DR bullet, cheat-sheet row |
| quizzed the room, "this will come in the exam/interview" | a Check-yourself question in that shape |
| said to skip / "not for exam" | one line, or Going Deeper / Further Study with "he said skip, here is why it exists" |
| rushed, hand-waved, no example | candidate gap (see `tier-basic.md` for gap fills) |

## 6. Class corrections

Never propagate an error; never nitpick a deliberate, flagged simplification. Format, exactly:

`> **Class correction (🟠 high):** what was said → what is actually the case → why it matters.`

| Severity | Meaning |
| --- | --- |
| 🔴 | changes the answer to a quiz or exam question |
| 🟠 | factually wrong and load-bearing |
| 🟡 | imprecise, dated, or an unverified aside |

- Include a correction only as **verified** in the research dossier part A (or by you, running the code).
  Unverifiable suspicions go to the report's open questions, not into the note body.
- State the correction where the error would otherwise be absorbed, in both tiers. Keep the said-part
  short and neutral ("the class said X"), never about the instructor.
- **Basic collects 🟡 slips; Advanced keeps everything inline.** In the Basic tier 🔴 and 🟠 corrections stay
  inline callouts; every 🟡 goes in one closing table `## Minor slips in class` with columns `| said | actual |`,
  one line each. Advanced keeps all corrections inline.
- **Instructor-repo drift is expected, not a correction.** The class frequently differs from the repo (files
  added or changed after class, demos typed differently). Notes follow what happened in class and cite the
  repo version where it differs, in a neutral `> **Repo note:** ...` callout: never a Class correction, never
  a gap. Example: `Lec-3` gained `extra_demo.c` after the class.
- The index (`update_index.py`) harvests these callouts into the running corrections table. Do not keep
  a second list.
- When the instructor flags something as a simplification ("roughly", "for now"), do not correct it.
  If the simplification matters, explain what production does instead.

## 7. Verify before asserting

- Check mechanics before writing them: sizes, alignment, flags, what a compiler or tool prints, which
  standard added a feature, API signatures, defaults. **Run it** when you can. Prefer the primary source
  (the profile's `research.preferred_sources`) over memory.
- **Never invent** a flag, header, member function, API, `sizeof`, benchmark number, version, URL, paper,
  figure or date. If you did not run it and are not certain, say what it depends on, or leave it out.
- Platform-specific claims name the platform (OS, CPU, compiler, version). The class's own machines are
  in the profile; a common machine is Windows 11 / PowerShell: add a one-line Windows delta where a
  command or result differs, rather than rewriting the example.
- Time-sensitive claims (versions, defaults, prices, "current best practice") carry `verified_as_of`.
- Latency and cost numbers are **order-of-magnitude**, labelled so, never precise figures for a machine.
- State the standard/language version when it matters (for example C++11 move, C++20 concepts).

## 8. Writing style

- Markdown, hierarchical, scannable. **Short paragraphs** (three sentences is usually two too many).
  Prefer a table or a code block to prose. Padding is a failure in both tiers, but **omitting taught
  material is worse**: Basic covers the whole class at class depth.
- **Why in a sentence, not a section.**
- **Jargon in bold on first use, defined in the same sentence.** Keep the jargon (it is examinable);
  define it. Expand acronyms on first use per note (Advanced: always).
- Real domain names and numbers from the profile (`Order`, `price`, `qty`, rupees, `payments-svc`), never
  `foo` / `bar` / `X Ltd`. Reuse the instructor's own examples: assessments echo them.
- **Comparison table** for anything with alternatives. A table that settles a confusion earns its space.
- Callouts: `> **Note:**`, `> **Gotcha:**`, `> **Class correction (...):**`, `> **Repo note:**`, `> **Reconstructed:**`,
  plus the profile's extras. Full label-to-class map in `note-format.md`.
- Comment non-obvious lines in code, not obvious ones. Comment the why, not the syntax.
- Spelling follows the profile's `spelling`. Never write the section sign; refer to "section 2.4" in words.
- Cross-link **forward** ("move semantics gets its own note in session 6") instead of duplicating a topic
  taught later; link **back** to earlier notes for vocabulary.
- Never write em-dash chains or rhetorical filler. Say the thing.
- **Write for the student, never about how the note was made.** The reader does not know or care that agents,
  a digest, an execution policy or a particular laptop produced this. Never write "representative output",
  "not run on this machine/here", "this laptop", "the digest", "the inputs", "no frames", "the classifier",
  "`{run}`" or any plugin step in the prose. Introduce example output as "Output:" or "Output (from the class
  demo):"; state a platform difference as a fact about the platform ("`ip` is Linux-only; Windows has
  `ipconfig`"); if output varies, say what varies ("pod name suffixes are random"). Provenance about the
  *class* is fine and stays in its callouts (`Reconstructed`, `Repo note`, `Class correction`). `lint_note`
  fails such prose as `process-talk`; `/notes:sweep` rewrites any that slipped through.

### Pending references (earlier session, note not written yet)

Sessions are generated in parallel, so a note may cite an earlier session whose note does not exist at writing
time. Cite it like any other reference (session number and title, plus whatever you need to restate), wrapped in an
inline pending marker, with **no link and no comment about the note set**:

```text
<!-- pending-ref:07 -->Session 7 (modelling the business workflow with data) built the order journey, one row
per order.<!-- /pending-ref:07 -->
```

- One marker per cited session, inside the paragraph (never nested, never around a heading or a table).
- Forbidden: "has no note yet", "no note exists", "note has not been written", "not linked here", "named without a
  link", "in plain text only" and any variant. The set changes after the batch; those sentences go stale.
- `lint_note.py` flags the stale phrases and any marker whose note now exists. `/notes:sweep` replaces each marker
  with a link `[session 7: <title>](07-<slug>.md#<anchor>)` inside `<!-- sweep:resolve-pending-07-from-09 -->` markers,
  keeping the content. Markers whose note is still missing are left and listed in the sweep report.

## 9. Gaps in the class

Instructors skip, rush and hand-wave. The digest records these (digest section 4); each tier handles them
differently: Basic fills only assessable gaps, marked `Gap fill`, within a 30% prose cap; Advanced covers
all gaps fully, unmarked. `deferred` topics ("later session") are never filled early: forward-link only.
Never bury a gap fill inside a sentence of class content: the reader must be able to tell what the class
said from what was added.

## 10. Done means checked

A note is not finished until, **in the same run**:

1. its snippets were compiled/run (`check_snippets.py`) and output pasted from the real run,
2. `lint_note.py` and `note_stats.py` are clean for the tier,
3. computable claims have a `.checks.py` that passes (`verify_numbers.py`),
4. Advanced: `check_citations.py` is clean and every cited URL is in research part G,
5. the PDF is rebuilt and `audit_pdf.py` reports zero problems (`qa.py` runs all of this).

A stale PDF is worse than no PDF. Content failures you cannot fix (budget, coverage, wrong output) are
returned honestly in your report, never papered over.

## 11. Return contract (every agent)

Return **at most 150 words**, plain text:

```text
status: ok | partial | failed
files: <absolute paths written>
counts: <what is measurable: words, frames, concepts, snippets, sources>
flags: <scope calls, ambiguities, unverified claims, anything the caller must decide>
```

Write detail into files, not into the reply. The orchestrator's closing chat report (at most 15 lines):
what was read, what the notes cover, what was corrected or verified, what the source left ambiguous,
scope calls, absolute PDF paths. It never restates the notes.

## 12. Hygiene checklist before you return

- [ ] Nothing written outside the files named in the task card.
- [ ] No transcript logistics, no ASR mangling, no empty headings.
- [ ] Every number is verified, computed, or labelled heard/screen.
- [ ] Every correction is verified and in the callout format with a severity.
- [ ] Every reconstruction is marked; every inaudible room question is answered.
- [ ] No section sign, no `TODO`/`TBD`/`XXX`/lorem.
- [ ] Every digest V-row (treatment other than `text`) appears as a figure, with a `From class` or `Redrawn` caption.
- [ ] Code lines at most 95 columns, tables at most 9 columns, ASCII diagrams at most 95 wide.
