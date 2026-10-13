# Basic tier — the whole class, at class depth

Purpose: **re-teach what the class taught, at the depth the class went to**, for a reader who sat in the
session and is revising it. Not a course, not a book, and **not a cut-down cheat sheet**: if the instructor
spent ten minutes and a whiteboard on a mechanic, the note gives that mechanic its own subsection with the
same example and the same drawing. One lecture in, one note out. Budget numbers below are defaults; the
profile overrides them.

## 1. Depth contract

> **One note per lecture. Never split. Cover everything the class did, at the depth the class went to.**

The note must contain, without exception:

- every concept in digest section 2 with depth `explained`, `demoed` or `drilled` (and `mentioned` ones that
  are assessable, in a line or two);
- every demo: the code **and** its real output;
- every class visual: each row of digest section 11 appears as a figure (see `figures.md`, "Class visuals");
- every room question, with its answer;
- every verified class correction;
- gap fills where the instructor fell short (section 4), marked.

"Cut, don't split" now applies **only to material the class did not cover** (extras, later-session topics,
pedantry): those go to the closing section. Never trim a concept the class taught in order to hit a number.
Never "solve" length by telegraphic bullet soup either: the note must teach.

**Each taught concept gets its own subsection**, in this order: what was taught (two or three lines) -> the
class's own example or visual -> code + real output -> the gotcha or trap. A one-line summary of a concept the
class spent minutes on is a lint warning ("thin coverage").

| Quantity | Default | Notes |
| --- | --- | --- |
| Prose words | `tiers.basic.words`, default 3,500-7,000 for a ~2 h class | prose = outside code fences, tables, front matter and closing sections; scaled by `duration_min / 120` (clamped to 0.5x-1.5x) |
| Minutes | `tiers.basic.minutes`, informational | reading minutes by `note_stats`: prose + code + tables, each figure 10 s; never fails |
| Gap-fill share | at most 30% of prose | `gapfill_max_share` |
| Class visuals | every digest V-row, no cap | each is a figure (`figures: class+generated`) |
| Check yourself | `check_yourself`, default 5-8 questions | |
| Going Deeper | at most 8 bullets, links only | each bullet at most 40 words |

Budget check: **under the minimum is a warning ("too thin")**; over the maximum is only a warning, never a
failure. The word range says how big a faithful note usually is; coverage and visuals are the hard checks:

- every digest concept with depth `explained` or higher needs a `.coverage.json` entry **and** at least one
  example (code, worked number or figure); Basic must cover at least **95%** of them (Advanced 100%);
- every digest V-row whose treatment is not `text` needs a figure in the note (content failure).

**Basic is judged by reading time and class-level difficulty, not by page count.** Large class figures
legitimately add pages. `note_stats` reports reading minutes and a simple complexity indicator (average
sentence length, and undefined jargon terms per 100 words, where "defined" means in this note or an earlier
one). It warns only when Basic reads harder than the class did, for example a high undefined-jargon density;
never because the PDF is long. Define a term on first use or link the earlier note that did.

**Corrections in Basic.** 🔴 and 🟠 stay inline as callouts. 🟡 slips are collected into one closing table
`## Minor slips in class` (`| said | actual |`, one line each, linked from nowhere), placed before Going
Deeper. Gap fills are unchanged. **Instructor-repo drift** (the class differs from the repo, or the repo
gained files after class) is a neutral `> **Repo note:**`, never a correction and never a gap.

## 2. What earns space in the body

- Every concept the class taught, at the depth the class taught it (section 1).
- Every class visual (board drawing, annotated slide): embedded from the frame, redrawn, or both.
- The code (or worked number) that shows each mechanic, with its **actual output** underneath: every demo
  the class ran, not one per mechanic.
- The table that settles a confusion (pointer vs reference, current vs non-current, SLM vs WDV).
- The traps: UB, the thing that compiles and is still wrong, the thing that differs by platform, what a
  number hides, what an examiner will bait you with.
- Anything the instructor emphasised, quizzed, or repeated; answered inaudible room questions.
- Verified class corrections (callouts) and **gap fills** (section 4), within caps.

## 3. What moves to the closing section

Only what the class did **not** teach: standard-paragraph pedantry, obscure corner cases, features a
**later** session covers (forward-link it), anything the instructor said was not examined, extra practice,
blog posts, talks, books, tool links. Format: a short list of links with a few words each. No exposition.
Mention the Advanced counterpart of this note when it exists. Subject profiles may fix a closing-section
order (for example finance: go deeper, extra practice, real-world reading, coming next).

## 4. Gaps and gap fills

The digest (section 4) lists where the instructor fell short. You act only on rows with `fill_basic: yes`:

| Gap type | Signal | Fill in Basic? |
| --- | --- | --- |
| `rushed` | assessable concept with under 3 min of class time, or only "mentioned" | yes, if assessable |
| `no-example` | explained with no demo, code or worked number | yes, if assessable |
| `hand-waved` | "basically", "we'll see later", "you can google", trailing off | yes, if assessable |
| `audio-lost` | junk run not recovered from Whisper or frames | yes: reconstruct and also mark `Reconstructed` |
| `unanswered` | room question whose answer is inaudible | yes: answer it |
| `confusion` | repeated student questions, re-explanation, silence after "does that make sense?" | yes, if assessable |
| `prerequisite` | assumed, never taught, absent from prior notes | yes, if needed to follow the class |
| `incorrect` | wrong in class | no: a Class correction callout instead (🟡 in the Minor slips table) |
| `deferred` | "later session" | **never**: forward-link only |

Marking: `> **Gap fill:** <at most 120 words and at most one short example>`. Lint counts them; the tier
index lists them. Do not blend a fill into class content. Do not exceed 30% of prose words in total
(`gapfill_max_share`). A gap is only worth a fill if the reader could be asked about it.

## 5. Required structure (in this order)

1. **Front matter** (see `note-format.md`) and the one-line meta block the builder renders.
2. **TL;DR**: 5-8 bullets. If the reader only reads this on the morning of the quiz, it must still be worth
   something. Concrete facts, not topic names.
3. **Body**: one subsection per taught concept, in teaching order (section 1). Each runs: what was taught,
   the class's example or visual, code + output, the gotcha. The "why" is one sentence, not a section.
4. **Cheat sheet** (or One-screen summary): one table or code block that consolidates the whole note.
5. **Check yourself**: 5-8 questions in the shape the class quizzes in (profile lists the shapes), each
   with the answer **and** a one-line reason. Put the inaudible-room-question answers here.
6. **Going Deeper** (or **Further Study**): the optional list from section 3.

`required_sections` in the profile is the checklist `lint_note.py` enforces (case-insensitive prefix match).

## 6. Content requirements

- **Compilable/runnable snippets** with the exact command and the **actual output** in a `text` block
  directly beneath. Mark runnable fences `{run}` (see `note-format.md`); you compile and run them with the
  profile's `snippets` template and paste the real output. No `...` fragments where a ten-line program
  fits. Outputs that only exist on a class screen: `{run nocheck}` and say "as shown in class (platform)".
- **Class visuals** as figures (section 1; `figures.md`): the board drawing the class made is part of the
  class. ASCII diagrams (memory layouts with addresses and byte offsets, under 95 columns) are **in addition
  to** the class visuals, never instead of them.
- **Show the arithmetic** (quantitative subjects): worked numbers laid out line by line with units, the
  naive version beside the right one where the contrast teaches.
- **Comparison tables** for every alternative the profile lists as a standing confusion.
- Assembly, `sizeof`, formulas and derivations as the class showed them: keep to the lines that make the
  point, and name the flags/assumptions that produced them.
- **Latency numbers** only as order-of-magnitude, labelled so.
- Real names, real numbers (profile conventions). State the standard version and the platform.

## 7. Check-yourself shapes

Use the shapes the class quizzes (profile `Basic tier` rules list them). Examples of shapes: "what does
this print", "will this compile", "what is `sizeof` this", "which is faster and why", "identify the effect
of this transaction", "work out this ratio". Each question is answerable from the note alone. Mix easy
recall with at least one trap question built from a class correction or a gotcha.

## 8. Writer workflow

1. Read the profile, this file, `house-style.md`, `note-format.md`, `figures.md`, then the digest (all 13
   sections, especially 2, 3 and 11) and research part A/B.
2. **Plan the outline from the digest**: one subsection per concept with depth `explained` or higher, each
   V-row attached to its concept, each demo (P-row) attached to its concept. Word targets follow class
   minutes (digest section 2). The plan is complete when nothing in digest sections 2, 3, 7 and 11 is
   unplaced.
3. Write from the digest. Quote real instructor files by name. Compile every `{run}` snippet locally.
4. Place the class visuals: image lines with `From class` captions, and write the `<stem>.visuals.json`
   sidecar (see `figures.md`).
5. Add verified corrections (research part A). Add gap fills only for `fill_basic: yes`.
6. Write the coverage sidecar: every digest concept with depth `explained` or higher maps to a section.
7. Self-run `check_snippets.py`, `note_stats.py`, `lint_note.py`; fix; re-run until clean. Too thin
   (under the word minimum) means you skipped material: go back to the digest. If a later session extends
   an earlier topic, **flag it in the report**; do not edit earlier notes in v1.

## 9. Common failure modes

| Failure | Fix |
| --- | --- |
| Reads like a transcript summary ("then the instructor showed...") | state the mechanic, not the narration |
| A concept the class drilled gets one line | its own subsection: example, visual, code, gotcha |
| A V-row of the digest has no figure | embed or redraw it; every V-row appears |
| Board drawing replaced by an ASCII sketch only | embed the class frame; ASCII may follow, not replace |
| Concept list without examples | add the program and its output |
| Output typed from memory | run it; if it cannot run, mark `nocheck` and name the platform |
| Gap fill disguised as class content | wrap in the `Gap fill` callout or delete |
| Cutting taught material to look short | the cut rule covers only what the class did not teach |
| Going Deeper with paragraphs | links only, at most 8, at most 40 words each |
| Re-teaching a later session | forward link, one line |
