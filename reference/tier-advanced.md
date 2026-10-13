# Advanced tier — the deep-dive note

Purpose: **teach the topic from first principles** so a reader who missed the class (or sat through it
confused) can learn it from this note alone, and be interview- and exam-ready. Everything the class
covered is mandatory at full depth; then go outward to the adjacent concepts a practitioner considers
inseparable. The profile's `Advanced tier - subject rules` add the subject's ladder, baseline and sources.

## 1. Depth policy

There is **no time budget and no length cap** (guidance: `tiers.advanced.words`, default 6,000-15,000; only
the lower bound warns). Thorough is not bloated: repetition, restatement and filler are still failures.
If a section teaches nothing, cut it.

| Do | Not |
| --- | --- |
| Derive it, then instantiate it on an input small enough to check by hand | State the rule and move on |
| Hand-work **every** concept (a number, a trace, a dry run) | Hand-work only the easy ones |
| From scratch first, then the idiomatic/library way, then what the library does differently | Show only the one-liner |
| Cover adjacent concepts (what a practitioner treats as inseparable) | Cover exactly what the slide said |
| Explain what broke before this existed | Present the current approach as obvious |
| Failure modes with **real error strings**; compute/latency/cost/security reality | Give the happy path only |
| Show the failing run too, then the fix | Show only success |

**One note per session.** Never split a session into a companion `NNa-<slug>.md` (lint and qa fail on it); a long
derivation or a family of examples goes in the one note as its own major section, and what does not fit is cut.

## 2. Accessibility policy

> A note has failed if a smart reader with only the baseline below has to leave it to understand it.

Length is free; confusion is not. Depth and accessibility are not in tension: a deep explanation starts
from something the reader already has. Do **not** simplify the content (the maths, derivations, standards
stay); make sure **nothing arrives unexplained**.

### 2.1 The Knowledge Ledger (hard rule)

You may assume the reader knows exactly:

1. the **baseline** in the profile's `Reader` section, and
2. anything a **previously generated Advanced note** (task card `prior_notes`) has already defined, and
3. nothing else.

Everything else is built inside the notes. Before writing, list the prior notes' defined terms, then list
the terms this session needs. Any term not in the ledger is one of: **prerequisite groundwork** (define it
in "What you need before this note", or extend the earlier note and link it, if the profile says so);
**this session's subject** (teach it from zero); **a small aside** (one or two sentences inline). Never a
fourth option. Every acronym is unknown until defined, every time, in every note.

**Banned phrases:** "as you know", "recall that", "obviously", "it is well known", "standard" (as an
appeal to authority). If it were obvious the reader would not be reading the note. Lint warns on them.

### 2.2 How to write it

**Sentence level.** Short, one idea each (three commas and a semicolon means two sentences). Concrete noun
over abstract noun. Active voice, present tense, "you" where it helps. Expand each acronym on first use in
each note. No unexplained metaphors from other fields. Spelling per the profile.

**Paragraph level: the four-beat pattern.** For every genuinely new idea, in this order:

1. **The problem.** What goes wrong without it; concrete, ideally with a number.
2. **The everyday version.** One or two sentences of plain analogy, clearly flagged as an analogy, with the
   place it breaks stated. Unfenced analogies become misconceptions.
3. **The precise version.** Definition, equation, algorithm, spec. The jargon is **introduced here, in
   bold**, having been earned.
4. **The check.** A tiny worked example, checkable by hand, using the words just defined.

Do not skip beat 2 because the idea seems easy; do not skip beat 3 because beat 2 felt satisfying.

**Note level.** Open with "What you need before this note": specific ideas and the specific earlier notes
that teach them, each linked ("you need *receptive field* and *stride*, both from [note 03](03-x.md)");
if nothing beyond the baseline, say so. Close the body with "New words in this note": every term
introduced, one plain sentence each, in order of appearance.

**Maths** (when `math: true`): every equation gets three things: the equation, a symbol-by-symbol plain
reading, and a numeric instantiation small enough to verify by hand. Derive; do not assert. Long
derivation: the full working in its own section of the note. Every worked number goes in `.checks.py`.

### 2.3 The line you do not cross

Accessible does not mean vague. Do not: drop the derivation ("the maths shows that"); replace a number with
"quite large"; skip failure modes because they are complicated; omit a source because its notation is
heavy; write a chatty preamble instead of an explanation. Test: **can the reader follow every step without
leaving the page.**

## 3. Required structure (in this order)

| # | Section | Rule |
| --- | --- | --- |
| 1 | Front matter | `note-format.md`; includes `prerequisites[]`, `verified_as_of` |
| 2 | What you need before this note | linked earlier notes + ideas; or "nothing beyond the baseline" |
| 3 | TL;DR (mental model) | 6-10 lines in the plainest language in the note |
| 4 | Body ladder | one subsection per concept, section 4 below |
| 5 | Common errors and debugging | where applicable: real error strings, root cause, fix |
| 6 | One-screen summary | formulas/commands/defaults/shapes in one table or block |
| 7 | New words in this note | each term, one plain sentence |
| 8 | Check yourself | 6-10 questions in the assessment's shapes, answer + one-line reason; at least one trap and one "why does this fail" |
| 9 | Interview questions | model answer **and** the follow-up a strong candidate expects |
| 10 | Hands-on | build / break / fix on the reader's machine or a free tier |
| 11 | Further Study | annotated, additive (not a dumping ground), ends with what comes next in the course |
| 12 | Sources | every URL cited, with title and domain; only from research part G |

The profile's `required_sections` is what lint enforces; subjects may add (for example Command reference).

## 4. The per-concept ladder

Default ladder (the profile's `Advanced tier - subject rules` replaces or extends the middle rungs):

1. **Why it exists**: the problem and what people did before; what broke.
2. **Mental model**: four-beat pattern, one analogy with its failure point.
3. **Mechanics under the hood**: the real implementation level the subject calls for.
4. **Hand-worked example**: tiny input, every step shown, verified in `.checks.py`.
5. **From-scratch code, then idiomatic/library code**: runnable, with output; what the library does
   differently and better.
6. **Dry run / trace**: stage by stage, including the **failing** run and the fix.
7. **Standard / primary-source citation**: inline link from research part G.
8. **Failure modes**: real error strings, the input that breaks it, the metric that lies.
9. **Production reality**: cost, latency, memory/compute, security, what is legacy vs current.
10. **Interview angle**: how it is asked and what a strong answer contains.

Comparison tables for every set of alternatives. Command/flag reference table for every tool introduced
(flag, meaning, typical use). Callouts per `note-format.md`.

## 5. Research and citations

- Work from the research dossier (`research-verify.md` and `research-deep.md`), not from memory, for any version-, time- or
  standard-sensitive claim. Prefer primary sources (profile `research.preferred_sources`); cross-check
  secondary ones.
- **Cite inline as markdown links** to URLs that appear in research part G (fetched or returned by search).
  Never invent or "remember" a URL. Name papers/standards with year (and venue/section where they apply).
- Gates: at least `min_sources` distinct URLs from at least `min_domains` distinct domains, including each
  `research.required_domains`. Stamp `verified_as_of` in front matter. Flag version-sensitive facts so a
  later reader knows to re-check.
- Class corrections: only as verified in research part A (callout format in `house-style.md`); **every severity stays
  inline** (Basic collects 🟡 in a table, Advanced does not). Repo drift is a `> **Repo note:**`, never a correction.
- The Sources section lists every cited URL with a plain-words description; Further Study adds the annotated
  reading list (type, title, author/org, year, why, level).

## 6. Figures

**Class visuals first.** Every row of digest section 11 (Class visuals) appears in the note, preferring
`redraw` or `both` where a clean redraw adds precision (addresses, axes, corrected values); a drawing the
class got wrong is shown **corrected** and captioned as such (`figures.md`, section 8). The writer also
writes `<stem>.visuals.json` / `<stem>.visuals.yaml`. Then, beyond the class's drawings:
draw a figure when **the picture is the explanation**, not as decoration. Plan at least `min_figures` where
`tiers.advanced.figures: expected` (class visuals count toward it). Write a figure plan (`.notes-plugin-internal/work/meta/<tier>/<stem>.figplan.yaml`); the `figure-author`
agent produces PNGs. Rules and API: `figures.md`. ASCII (text fences) is still right for small grids,
byte layouts and call stacks. Mermaid only if `diagrams: mermaid-ok`, kept shallow (about 8 nodes).

## 7. Content requirements recap

- Every digest concept is covered (coverage sidecar, 100%) with at least one worked example.
- Every digest V-row appears as a figure (embed, redraw or both) with its caption convention.
- Snippets run: `{run}` fences with real output; complete programs, no `...`.
- State shapes/units/versions every time they matter; give formulas each time they are used.
- Failure modes always; compute/cost/security reality wherever it applies.
- Hands-on tied to the course assignment or project where one exists.
- Gaps from the class are covered fully, unmarked (class errors still use the Class correction callout).

## 8. Writer workflow

1. Read profile, this file, `house-style.md`, `note-format.md`, `figures.md`, digest, research (full), prior
   Advanced notes (ledger), instructor code.
2. Build the **ledger check** and the outline; write the **skeleton first** (all H2/H3, coverage mapped),
   then fill section by section. Never one giant write.
3. Per concept apply the ladder; hand-work it; put each computable claim in `.checks.py`.
4. Add the figure plan and the visuals sidecars; `figure-author` runs later; leave image lines with draft
   captions.
5. Self-run `check_snippets.py`, `lint_note.py`, `check_citations.py`; fix; re-run until clean.
6. Return honest content failures (coverage, citations, numbers) in the report.

## 9. Common failure modes

| Failure | Fix |
| --- | --- |
| Concept stops at the definition or at the library call | climb the ladder: hand-worked example, internals, failure mode |
| A word used before it is defined, or an undefined acronym | ledger check; define at first use, in bold |
| Citation of a URL not in research part G | fetch it through the researcher or drop the claim |
| Worked number not in `.checks.py` | add it; run `verify_numbers.py` |
| Decorative figure | cut it, or make it show the note's own numbers |
| Vague quantity ("quite large", "very fast") | give the number, or label it order-of-magnitude |
| One huge note that nobody can navigate | clear sections, a TL;DR, the cheat sheet; cut, never a second file |
