# Lecture digest — schema and worked example

`digest.md` is the **single shared reading** of one session. It is built once by `digest-builder` from the
transcript, screen notes (frames), instructor code, slides and classwork, then used by **both** tiers'
writers and by the researcher, who do not re-read the raw sources (except targeted greps). Validated by
`check_digest.py`: fix until it exits clean.

Location: `<session_dir>/digest.md`. Target length 4,000-9,000 words for a 2-hour class. **Code is referenced
by path, not copied**, except outputs that exist only on screen, which are copied verbatim. Never reproduce
logistics. Apply the profile's ASR table silently. Tag every heard number as *heard*, every screen number as
*read-from-screen*; never state either as verified fact.

## 1. Front matter (all keys required)

```yaml
profile: cpp                      # profile id
session: 3                        # int
title_syllabus: "Memory Layout 2"     # profile title
title_as_taught: "Arrays, decay and reference parameters"   # what was actually taught
slug: arrays-decay-and-reference-parameters     # kebab-case, from title_as_taught; both tiers use it
date: 2026-08-24                  # ISO; profile date wins when the transcript header is ambiguous
duration_min: 120
sources:
  transcript: "Memory_Layout_2_-_Class_Scaler_Academy.txt"
  whisper: "Memory_Layout_2_-_Class_Scaler_Academy.whisper.json"   # or null
  frames_total: 140
  frames_read: 138                # frames_total minus skipped (with reasons in screen notes)
  instructor_code: ["Lec-3/main.cpp"]   # [] if none
  slides: ["L3.pdf"]                    # [] if none
  classwork: ["Classwork/3/swap.cpp"]   # [] / null if none
speech_primary: transcript       # transcript | whisper | recording | none (inputs.speech.primary)
repo_moved_on: false              # true if instructor files are newer than the transcript describes
audio_quality: good               # good | patchy | poor
dropped_minutes: 0                # minutes lost to unrecovered junk runs (sum of audio-lost spans)
verified_as_of: 2026-10-03        # the run date
```

## 2. The thirteen sections (numbered H2s, exact order, exact titles)

| # | Title | Row / content shape | Rules |
| --- | --- | --- | --- |
| 1 | Session timeline | `hh:mm–hh:mm`, topic, concepts, frames | cover the whole duration; a gap over 10 min must be explained (break, lab, audio lost) |
| 2 | Concept inventory | `C01`, concept, depth, minutes, emphasis, assessable (y/n), sources | depth is one of mentioned / explained / demoed / drilled; minutes is class time |
| 3 | Concept detail | one `### C01 — name` per concept | six labelled bullets, see below |
| 4 | Gaps | `G01`, C-ref, type, evidence, severity, fill_basic, basic fix, advanced treatment | types and the fill rule are in `tier-basic.md`; C-ref must exist in section 2 |
| 5 | Class-correction candidates | `K01`, said (ts), likely actual, severity, confidence, how to verify | feeds the researcher; severity is 🔴 🟠 🟡 |
| 6 | Reconstructed passages | ts range, why, reconstructed from | each junk run, and what recovered it or did not |
| 7 | Code and output inventory | `P01`, file or frame, purpose, compile cmd, output verbatim, verified? | verified = you ran it here. Also one row per `data`, `exercise` or `reference` file with `used in class: yes/no` (no compile or output) |
| 8 | Claims and numbers ledger | `N01`, claim, origin (heard/screen/code), verify?, computable | computable claims become `.checks.py` entries |
| 9 | Vocabulary | term, first ts, definition as given | terms introduced or used; seeds the Advanced "New words" |
| 10 | Course links | back-links, deferred topics (forward links), overlap with prior notes | `deferred` gaps are listed here, never filled in Basic |
| 11 | Class visuals | `V01`, frame (`F067 (00:53:31)`), concept, what it shows, quality, crop `x0,y0,x1,y1`, treatment | **every** board drawing, diagram, annotated slide and pedagogically important screen the class showed; see "Class visuals" below. Optional second table of `D` rows: extra figure opportunities with no class drawing |
| 12 | Emphasis and assessment signals | ts, what, signal | signal is one of emphasised / quizzed room / exam / repeated / skip |
| 13 | Open questions and ambiguities | bullets | anything you could not resolve; the report relays these |

Section 3, per concept (`### C01 — name`), labelled bullets in this order:

1. **Taught** — your reconstructed account of what the class established (not a transcript summary).
2. **Code shown** — file path(s) plus frame refs (`F031`).
3. **Output shown** — verbatim from frames, with the frame ref.
4. **Board diagrams** — the V-ids that belong to this concept (`V03, V04`) plus enough to redraw each:
   boxes, arrows, labels, addresses. Every V-row of section 11 is cited by exactly one concept.
5. **Instructor examples and numbers** — each tagged *heard* or *read-from-screen*.
6. **Room questions and answers** — and the answer you supply where it was inaudible.

IDs are unique within their letter (`C`, `G`, `K`, `P`, `N`, `V`, and `D` when used) and never reused. Gap
rows must point at existing concepts. Both tiers must cover every concept with depth `explained` or higher
(Basic at least 95%, Advanced 100%), and every V-row.

**Class visuals (section 11).** One row per distinct drawing or annotated screen, built from the screen notes
(each `Diagram` field carries a crop box and any "extended later by" link):

`| V01 | F067 (00:53:31) | C07 | what it shows | quality | crop x0,y0,x1,y1 | treatment |`

- **Frame:** the **last, fullest** frame of that drawing (follow "extended later by" chains to the end), with
  its time. Several frames of one drawing are one row, not several.
- **Quality:** `clean` (readable as is), `messy` (readable with effort), `partial` (cut off or half-drawn).
- **Crop:** fractions of the frame JPG, `x0,y0,x1,y1` in 0 to 1, copied or tightened from the screen notes.
- **Treatment:** `embed` (default for board drawings and annotated slides), `redraw` (board version messy,
  partial or wrong), `both`, or `text` (terminals, editors and code screens: transcribe, never an image).
  A drawing that a section 5 correction affects is `redraw` or `both` with the corrected content stated in
  "what it shows".
- **Slide pages:** a V-row's source may be a slide page instead of a keyframe: frame column `slide L3 p2`, plus `page: slides/<pdf-stem>/pages/NNN.png` in the cropper row; treatment defaults to `embed` like a board drawing, caption `**From the slides, p.N.**`.
- Nothing visual is left out: when in doubt, add the row. A board drawing with no row is a defect.

## 3. Detection duties

- Apply source precedence from `house-style.md`. Check each junk run against Whisper and screen notes
  before marking it unrecovered. Answer inaudible room questions in section 3.
- Gaps (section 4): `rushed`, `no-example`, `hand-waved`, `audio-lost`, `incorrect`, `confusion`,
  `unanswered`, `prerequisite`, `deferred` (signals in `tier-basic.md`). Set `fill_basic: yes` per the rule
  there; `deferred` is never `yes`.
- Class-correction candidates: say what you think is wrong, how sure you are (low/med/high), and **how it
  can be verified** (a command to run, a standard to look up). Do not assert it as fact here.
- Pick `title_as_taught` and `slug` from what the class actually covered. Compare instructor files against
  the transcript and set `repo_moved_on`.

## 4. Worked example (synthetic, for format only)

The block below is a complete minimal digest. Its content is invented to show the shape (a 30-minute
session); real digests are far longer. `check_digest.py` must accept it unchanged.

````markdown
---
profile: cpp
session: 3
title_syllabus: "Memory Layout 2"
title_as_taught: "Arrays, decay and reference parameters"
slug: arrays-decay-and-reference-parameters
date: 2026-08-24
duration_min: 30
sources:
  transcript: "Example_Session_-_Class_Scaler_Academy.txt"
  whisper: null
  frames_total: 12
  frames_read: 11
  instructor_code: ["Lec-3/array_decay.cpp"]
  slides: []
  classwork: []
speech_primary: transcript
repo_moved_on: false
audio_quality: patchy
dropped_minutes: 2
verified_as_of: 2026-10-03
---

# Digest — Session 3: Arrays, decay and reference parameters

## 1. Session timeline

| Time | Topic | Concepts | Frames |
| --- | --- | --- | --- |
| 00:00–00:04 | Recap of pointers and addresses | C01 | F001–F002 |
| 00:04–00:16 | Array decay when passed to a function | C02 | F003–F008 |
| 00:16–00:28 | Pass by value vs by reference, swap | C03 | F009–F011 |
| 00:28–00:30 | Wrap-up and homework | | F012 |

## 2. Concept inventory

| ID | Concept | Depth | Minutes | Emphasis | Assessable | Sources |
| --- | --- | --- | --- | --- | --- | --- |
| C01 | Pointer holds an address | explained | 4 | low | y | transcript |
| C02 | Array-to-pointer decay | demoed | 12 | high (repeated) | y | transcript, Lec-3/array_decay.cpp, F005 |
| C03 | Pass by value vs reference | drilled | 12 | high (quizzed room) | y | transcript, F010 |

## 3. Concept detail

### C01 — Pointer holds an address

- **Taught:** a pointer variable stores the address of another object; `*p` reads that object.
- **Code shown:** none (recap).
- **Output shown:** none.
- **Board diagrams:** V01: two boxes, `x` (value 7) and `p` (value = address of x), arrow from `p` to `x`.
- **Instructor examples and numbers:** `x = 7` (*heard*).
- **Room questions and answers:** none.

### C02 — Array-to-pointer decay

- **Taught:** an array name in most expressions becomes a pointer to its first element, so a function that
  receives an array parameter actually receives a pointer and loses the length.
- **Code shown:** `Lec-3/array_decay.cpp`; frame F005.
- **Output shown:** `main: 40` then `f: 8` (F006, read-from-screen).
- **Board diagrams:** V02: a row of 10 int cells, an arrow from the name `arr` to cell 0, label "address only".
- **Instructor examples and numbers:** `int arr[10]` (*read-from-screen*).
- **Room questions and answers:** asked why `f` prints 8; answer inaudible. Supplied: `sizeof` of a pointer
  on a 64-bit machine is 8 bytes.

### C03 — Pass by value vs reference

- **Taught:** by value copies the argument, so a swap on copies changes nothing; by reference binds to the
  caller's object.
- **Code shown:** `Lec-3/array_decay.cpp` (swap section); frames F009-F011.
- **Output shown:** `before 1 2` / `after 2 1` (F011, read-from-screen).
- **Board diagrams:** V03: caller frame and callee frame boxes; copy arrow vs alias arrow.
- **Instructor examples and numbers:** swap of 1 and 2 (*read-from-screen*).
- **Room questions and answers:** "does a reference take memory?" Answer supplied: it is implementation
  defined; the language gives no guarantee either way.

## 4. Gaps

| ID | Concept | Type | Evidence | Severity | fill_basic | Basic fix | Advanced treatment |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G01 | C02 | no-example | "just remember it decays" (00:09) | medium | yes | one 8-line sizeof demo | full decay rules incl. exceptions |
| G02 | C03 | deferred | "references in depth next class" (00:27) | low | no | forward link | covered where relevant |

## 5. Class-correction candidates

| ID | Said | Likely actual | Severity | Confidence | How to verify |
| --- | --- | --- | --- | --- | --- |
| K01 | "arrays and pointers are the same thing" (00:07) | distinct types; an array decays to a pointer in most expressions | 🟠 | high | compile `sizeof(arr)` in main vs in a callee |

## 6. Reconstructed passages

| Time range | Why | Reconstructed from |
| --- | --- | --- |
| 00:12–00:14 | junk run, audio lost | Lec-3/array_decay.cpp and frame F007; Whisper had it partly |

## 7. Code and output inventory

| ID | File or frame | Purpose | Compile command | Output | Verified |
| --- | --- | --- | --- | --- | --- |
| P01 | Lec-3/array_decay.cpp | show sizeof array vs parameter | g++ -std=c++20 -O0 array_decay.cpp | main: 40 / f: 8 (F006) | yes, rerun locally |

## 8. Claims and numbers ledger

| ID | Claim | Origin | Verify? | Computable |
| --- | --- | --- | --- | --- |
| N01 | sizeof(int[10]) is 40 | screen (F006) | yes | yes: 10 * sizeof(int), check on x86-64 |
| N02 | sizeof(pointer) is 8 | screen (F006) | yes | yes |

## 9. Vocabulary

| Term | First ts | Definition as given |
| --- | --- | --- |
| decay | 00:05 | array name turning into a pointer to its first element |
| pass by reference | 00:17 | the callee works on the caller's object, not a copy |

## 10. Course links

- Back: session 2 introduced addresses and `&`.
- Deferred: references in depth, `const&` (said "next class").
- Overlap with prior notes: pointer basics repeat note 02; link, do not re-teach.

## 11. Class visuals

| ID | Frame | Concept | What it shows | Quality | Crop | Treatment |
| --- | --- | --- | --- | --- | --- | --- |
| V01 | F001 (00:01:30) | C01 | board: boxes `x` (7) and `p` (address of `x`), arrow from `p` to `x` | clean | 0.10,0.15,0.90,0.70 | embed |
| V02 | F003 (00:05:20) | C02 | board: ten int cells, `arr` pointing at cell 0, "40 bytes" under the row | clean | 0.05,0.10,0.95,0.80 | embed |
| V03 | F009 (00:17:40) | C03 | board: caller and callee boxes; copy arrow (by value) vs alias arrow (by reference) | messy | 0.00,0.05,1.00,0.90 | both |
| V04 | F002 (00:05:40) | C02 | editor with `array_decay.cpp` | clean | 0.00,0.00,1.00,1.00 | text |

Extra figure opportunities with no class drawing (optional):

| ID | Concept | What to show | Data source |
| --- | --- | --- | --- |
| D01 | C02 | sizeof 40 vs 8 as two bars next to the cells | N01, N02 |

## 12. Emphasis and assessment signals

| Time | What | Signal |
| --- | --- | --- |
| 00:09 | decay "will come in the quiz" | exam |
| 00:20 | swap by value vs reference | quizzed room |

## 13. Open questions and ambiguities

- The 00:12-00:14 stretch was recovered only partly; the exact wording of the decay definition is unclear.
````
