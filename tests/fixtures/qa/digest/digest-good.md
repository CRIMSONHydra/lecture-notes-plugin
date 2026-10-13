---
profile: cpp
session: 2
title_syllabus: "Memory Layout"
title_as_taught: "Stack Frames, Briefly"
slug: stack-frames-briefly
date: 2026-08-22
duration_min: 120
sources:
  transcript: "resources/Memory_Layout_-_Class_Scaler_Academy.txt"
  whisper: "resources/Memory_Layout_-_Class_Scaler_Academy.whisper.json"
  frames_total: 6
  frames_read: 6
  instructor_code: ["Lec-2/main.cpp"]
  slides: []
  classwork: null
repo_moved_on: false
audio_quality: good
dropped_minutes: 0
verified_as_of: 2026-10-03
---

# Lecture digest: Stack Frames, Briefly

## 1. Session timeline

| hh:mm–hh:mm | topic | concepts | frames |
| --- | --- | --- | --- |
| 00:00–00:20 | recap of session 1 and plan | C03 | F001 |
| 00:20–00:45 | writing and compiling add | C01 | F002–F003 |
| 00:45–01:00 | reading the assembly | C01 | F004 |
| 01:00–01:26 | the stack frame drawn on the board | C01, C02 | F005 |
| 01:30–02:00 | stale stack and UB | C02 | F006 |

## 2. Concept inventory

| ID | concept | depth | minutes | emphasis | assessable | sources |
| --- | --- | --- | --- | --- | --- | --- |
| C01 | stack frame layout | demoed | 40 | emphasised | y | Lec-2/main.cpp, F005 |
| C02 | stale stack is UB | explained | 30 | repeated | y | F006 |
| C03 | session 1 recap | mentioned | 20 | none | n | speech |

## 3. Concept detail

### C01 — stack frame layout

- **taught:** each call pushes the caller's RBP and the return address, then the locals below.
- **code shown:** `Lec-2/main.cpp` (F002).
- **output shown:** `25` (F003, read-from-screen).

### C02 — stale stack is UB

- **taught:** `ret` does not clear the frame; the next call may see old bytes.
- **room question:** "is it always 100?" Answer: no, it is undefined behaviour.

### C03 — session 1 recap

- **taught:** bids and asks, the memory hierarchy.

## 4. Gaps

| ID | C-ref | type | evidence | severity | fill_basic | basic fix | advanced treatment |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G01 | C01 | no-example | 01:10 "you can see it in the assembly" | med | yes | one-line example | full walk-through |
| G02 | C02 | hand-waved | 01:40 "don't worry about -O2" | low | yes | name the flag | compare -O0 and -O2 |

## 5. Class-correction candidates

| ID | said (ts) | likely actual | severity | confidence | how to verify |
| --- | --- | --- | --- | --- | --- |
| K01 | RSP holds the current line (01:15) | that is RIP | 🔴 | high | Intel SDM, register names |

## 6. Reconstructed passages

| ts range | why | reconstructed from |
| --- | --- | --- |
| 01:26–01:30 | audio dropped | Lec-2/main.cpp |

## 7. Code and output inventory

| ID | file or frame | purpose | compile cmd | output | verified? |
| --- | --- | --- | --- | --- | --- |
| P01 | Lec-2/main.cpp | add(12, 13) | g++ -std=c++20 main.cpp | 25 | yes |

## 8. Claims and numbers ledger

| ID | claim | origin | verify? | computable |
| --- | --- | --- | --- | --- |
| N01 | add(12, 13) = 25 | screen | yes | yes: checks.py |

## 9. Vocabulary

| term | first ts | definition as given |
| --- | --- | --- |
| stack frame | 01:00 | the region a call uses for its locals |

## 10. Course links

- Back: session 1 memory hierarchy.
- Forward: pointers (session 3).

## 11. Class visuals

| ID | Frame | Concept | What it shows | Quality | Crop | Treatment |
| --- | --- | --- | --- | --- | --- | --- |
| V01 | F002 (00:25:10) | C01 | editor with `main.cpp` and the add function | clean | 0.00,0.00,1.00,1.00 | text |

Extra figure opportunities with no class drawing (optional):

| ID | Concept | What to show | Data source |
| --- | --- | --- | --- |
| D01 | C01 | frame of add with addresses | P01 |

## 12. Emphasis and assessment signals

| ts | what | signal |
| --- | --- | --- |
| 01:15 | RIP vs RSP | emphasised |

## 13. Open questions and ambiguities

- None.
