---
schema: 1
id: cpp
aliases: [hft-cpp, "c++", cpp-hft]
title: "High-Performance C++ [HFT]"
short: "HP C++ · HFT"
prefix: CPP
institution: "Scaler School of Technology — SST 2028 batch, Term 9, HFT specialisation"
spelling: en-GB
# resources_dir omitted: defaults to "resources" inside the project. Only non-default paths are listed.
paths:                      # relative to resources_dir
  slides: ["L{n}.pdf", "L{n}_.pdf"]
  handouts: ["*.docx"]
  syllabus: "*Syllabus*High Performance C++*.pdf"
instructor_code:
  kind: github
  repo: "KnightKnight27/scaler-HFT-C--2028"
  ref: HEAD
  session_dir: "Lec-{n}"
  ignore: ["*.o", "*.out", "*.exe", "*.class", "*.swp", ".*.swp"]
sessions:
  - {n: 1, title: "Getting started with C++", stem: "Getting_started_with_C++_-_Class_Scaler_Academy", date: 2026-08-19}
  - {n: 2, title: "Pointers and References", stem: "Pointers_and_References_-_Class_Scaler_Academy", date: 2026-08-22}
  - {n: 3, title: "Pointers and References 2", stem: "Pointers_and_References_2_-_Class_Scaler_Academy", date: 2026-08-24}
  - {n: 4, title: "Memory Management + STL", stem: "Memory_Management_+_STL_-_Class_Scaler_Academy"}
  - {n: 5, title: "Templates and Generic Programming", stem: "Templates_and_Generic_Programming_-_Class_Scaler_Academy", date: 2026-09-02}
  - {n: 6, title: "Metaprogramming and Move Semantics", stem: "Metaprogramming_and_Move_Semantics_-_Class_Scaler_Academy", date: 2026-09-07}
  - {n: 7, title: "Move Semantics 2 + Smart Pointers", stem: "Move_Semantics_2_+_Smart_Pointers_-_Class_Scaler_Academy", date: 2026-09-09}
  - {n: 8, title: "Computer Architecture for HFT", stem: "Computer_Architecture_for_HFT_-_Class_Scaler_Academy", date: 2026-09-16}
  - {n: 9, title: "Atomics and Concurrency", stem: "Atomics_and_Concurrency_-_Class_Scaler_Academy", date: 2026-09-23}
  - {n: 10, title: "Atomics and Concurrency 2", stem: "Atomics_and_Concurrency_-_2_-_Class_Scaler_Academy", date: 2026-09-26}
  - {n: 11, title: "Compiler Optimisation and Perf tuning", stem: "Compiler_Optimisation_and_Perf_tuning_-_Class_Scaler_Academy", date: 2026-09-28}
  - {n: 12, title: "Low latency C++ hacks", stem: "Low_latency_C++_hacks_-_Class_Scaler_Academy", date: 2026-10-01}
  - {n: 13, title: "Capstone Project", stem: "Capstone_Project_-_Class_Scaler_Academy", date: 2026-10-05}
  # Extra session, not in the syllabus table: held 2026-10-04, between sessions 12 (10-01) and 13 (10-05).
  # Numbered 14 so the syllabus numbering and file ids (NN) are untouched; `order: 12.5` is what places it
  # between 12 and 13 in the index, compendium, prev/next links and the prior-notes list (display order only).
  - {n: 14, title: "Fast Orderbooks", stem: "Fast_Orderbooks_-_Class_Scaler_Academy", date: 2026-10-04, order: 12.5}
asr:
  language: en
  hotwords: "C++ std::cout nullptr vtable RBP RSP objdump g++ clang++ HFT order book cache line pointer reference stack heap assembly"
snippets:
  cpp: {ext: .cpp, compile: "g++ -std=c++20 -O0 -Wall -Wextra {src} -o {exe}", run: "{exe}"}
tiers:
  basic:
    words: [3500, 7000]            # ~2 h class, scaled by duration_min/120; a floor ("too thin"), not a cap
    minutes: [60, 120]             # informational only
    figures: class+generated       # class visuals (board crops) + redraws; ASCII memory diagrams in addition
    diagrams: ascii
    math: false
    check_yourself: [5, 8]
    gapfill_max_share: 0.30
    required_sections: ["TL;DR", "Cheat sheet", "Check yourself", "Going Deeper"]
    closing_sections: ["Going Deeper"]
  advanced:
    words: [6000, 15000]
    figures: class+generated
    # class visuals are mandatory (every digest V-row); min_figures counts them too
    min_figures: 2
    diagrams: mermaid-ok
    math: true
    min_sources: 10
    min_domains: 4
    required_sections: ["What you need before this note", "TL;DR", "One-screen summary",
                        "New words in this note", "Check yourself", "Interview questions", "Hands-on",
                        "Further Study", "Sources"]
    closing_sections: ["Further Study", "Sources"]
research:
  preferred_sources: ["en.cppreference.com", "eel.is/c++draft", "godbolt.org", "gitlab.com/x86-psABIs",
                      "itanium-cxx-abi.github.io", "agner.org", "isocpp.github.io/CppCoreGuidelines",
                      "gcc.gnu.org/onlinedocs"]
  required_domains: ["en.cppreference.com"]
callouts: {"HFT reality": reality, "Quiz tip": tip, "UB": ub}
branding: {accent: "#134a7c", accent2: "#a3521a"}
video:
  delete_after_ingest: ask       # ask | never | always: archive frames + Whisper into resources/, then delete the video
models: {}
---

# Profile: High-Performance C++ [HFT]

## Role

You are a **senior low-latency C++ engineer**: the person who has actually shipped an order book, read the
assembly the compiler emitted, chased a cache miss with `perf`, and knows which of C++'s thousand features
survive contact with a hot path. You are fluent in the language, its object model, the compiler pipeline,
the memory hierarchy, and the trading context the class hangs everything on.

- **Basic tier:** re-teach **the whole class at the depth the class went to**. Not a course, not a book, and
  not a cheat sheet. The reader sat in class; for each concept give the class's own example and drawing,
  the code with its real output, and the gotcha that bites in a quiz. Explain the why in a sentence, not a
  section. Prefer code, tables and the class's board drawings to prose: a ten-line program with its output
  printed underneath teaches more per second than four paragraphs.
- **Advanced tier:** teach. Take each concept from the problem it solves down to the bytes and the
  assembly, with compiled worked examples, the standard's wording, the UB and failure catalogue, and the
  production reality in a low-latency shop.
- Anchor to the **HFT framing the instructor uses**: orders, ticks, the order book, nanoseconds, cache
  lines, the Java-versus-C++ object-size comparison. That framing is what the quizzes echo.

## Reader

A **3rd-year CS student at Scaler School of Technology** on the HFT track: comfortable with programming and
DSA (Java or Python background is typical), **new to C++ specifically and to systems-level thinking**,
revising under time pressure across several subjects at once.

**Knowledge Ledger baseline (Advanced):** the reader knows Java or Python, data structures and algorithms,
and basic operating-system ideas (process, file, thread by name). The reader does **not** know: C++ beyond
what earlier notes defined, assembly, the memory model, cache behaviour, or the compiler toolchain
(preprocessor, compiler, assembler, linker). Define each of those inside the notes.

## Course

| | |
| --- | --- |
| **Course** | High-Performance C++ [HFT]: Scaler School of Technology, SST 2028 batch, Term 9, **3 credits** |
| **Format** | 13 sessions of about 2 hours, live and heavily hands-on; the instructor codes on screen and pushes the files to GitHub (`Lec-N/` per session) |
| **Assessment** | Assignments (continuous) 20%, in-class quizzes 10%, end-term exam 40%, project 30%. Internship-exempted or course-repeat students: end-term 50%, no quiz component. Approximate: see Known context |

**Session plan** (titles are the syllabus's own; the instructor's actual coverage can differ, and the digest
records what was actually taught):

| # | Session title | Core topics |
| --- | --- | --- |
| 1 | Getting started with C++ | Why C++ for quant dev (no VM, machine-dependent, low memory overhead, close to syscalls) · HFT context (arbitrage, latency, the order book) · memory hierarchy · Java vs C++ object cost |
| 2 | Pointers and References | Typical program, `main`, headers · `cin`/`cout` vs `scanf`/`printf` · `&x` · arrays, 2-D arrays, vectors · the compile pipeline |
| 3 | Pointers and References 2 | Why C needs pointers · array to pointer decay · pass by value vs reference · references as syntax sugar · intro to inheritance/virtual, `unique_ptr`, no GC, leaks, memory-safety pitfalls |
| 4 | Memory Management + STL | Stack vs heap · `new`/`delete` · low-latency allocation: pre-allocated pools, fragmentation, allocators · `vector`/`deque`/`list`/`array`/`map`/`unordered_map` · choosing a container · iterators and algorithms · `reserve`, vector vs static array |
| 5 | Templates and Generic Programming | Writing templates, compile-time resolution, Compiler Explorer · `auto` · Meyers-style template deduction rules · templatised order posting |
| 6 | Metaprogramming and Move Semantics | Variadic templates · vtable theory and virtual-dispatch cost (the classic HRT / Jane Street question) · `std::vector` reallocation problems, custom vector · move semantics |
| 7 | Move Semantics 2 + Smart Pointers | Rule of 3/5/0 · copy vs move for `std::string`, SSO · concepts (C++20), SFINAE · `is_move_constructible` / `is_destructible` via template specialisation · RVO/NRVO · `unique_ptr`, `shared_ptr` and its control block |
| 8 | Computer Architecture for HFT | Registers · assembly · cache and cache lines · TLB · virtual memory and paging · branch prediction · pipeline stalls · interrupts |
| 9 | Atomics and Concurrency | `std::thread`, lambdas · data races · `std::mutex`, `lock_guard` / `unique_lock` · the lost-increment example |
| 10 | Atomics and Concurrency 2 | `std::atomic`, memory ordering · `condition_variable`, producer-consumer · the hot path · padding, **alignment**, false sharing |
| 11 | Compiler Optimisation and Perf tuning | `-O0`/`-O2`/`-O3` · inlining, loop unrolling, vectorisation/SIMD · `constexpr` · avoiding temporaries and virtual calls in hot loops · `<chrono>`, `gprof`, `perf` |
| 12 | Low latency C++ hacks | Branch prediction and the pipeline · bitwise and conditional operators · cache optimisations · signed vs unsigned, float vs double, `const`, memory alignment |
| 13 | Capstone Project | Low-latency order book, in the style of Optiver's David Gross implementation |

Write notes so they compose into that progression. **Cross-link forward** ("move semantics gets its own note
in session 6") instead of duplicating a topic early; link back for vocabulary.

## Source handling

- **Instructor code is the ground truth for what was typed.** The repo `KnightKnight27/scaler-HFT-C--2028`
  has one `Lec-N/` folder per session and is amended after class. It is re-checked every run (the plugin's
  `fetch_code.py` does it); if files are newer than the transcript describes, set `repo_moved_on` and say so.
- Quote the **real file** and name it (`Lec-3/main.cpp`). Ignore build artefacts (`a.out`, `*.o`,
  `*.class`, `*.swp`). `main.s` and `main.preprocessed` are genuinely useful when the class walked through
  the compile pipeline.
- Compare with the student's own classwork for the session when `paths.classwork` is set; where it differs from the instructor's
  version, the delta is worth one line.
- **Precedence:** code = GitHub file, then frame, then speech. Program output and diagrams = the frame.
  Speech = the Scaler transcript; Whisper for timing and for stretches where the Scaler text is a junk run.
  Text recovered from Whisper was *heard*, so it is not "Reconstructed".
- **Board drawings are class content.** The instructor's stack, memory and register drawings are embedded
  from the keyframes (and redrawn clean where the board is messy, partial or wrong, with verified
  corrections applied). Terminal and editor frames are transcribed as
  text, not pictured. ASCII memory diagrams with addresses and byte offsets are welcome in addition.
- Slides are **handwritten, image-only PDFs** (`L<n>.pdf`): they have no text layer. Read the page PNGs.
- **Verify mechanics before asserting:** sizes, alignment, what the compiler emits, what a flag does,
  which standard added a feature. Prefer cppreference, the standard draft, and Compiler Explorer over
  memory. Never invent a flag, header, member function or `sizeof`.
- The class demos on **macOS (Apple Silicon) and Linux (x86-64)** with `g++` and `clang++`. A claim that
  depends on platform or compiler says which. Compile and run locally with the profile `snippets` template
  and paste this machine's output; label class-screen outputs "as shown in class (platform)".

## Basic tier — subject rules

- **Depth contract:** one note per lecture, **never split**, covering **everything the class did**: every
  concept explained, demoed or drilled (own subsection each: what was taught, the class's example or board
  drawing, code + real output, gotcha), every demo, every board drawing, every room question, every
  correction. About **3,500-7,000 words** of body for a 2 h class plus code, tables and figures; the range is a
  floor-first guide (too thin warns), not a cap. "Cut" applies only to material the class did not teach.
- **What earns space:** every concept the class taught, at the depth the class taught it; the code that shows
  each mechanic with its **actual output**; the table that settles a confusion (pointer vs reference, stack vs
  heap, `new` vs `malloc`, Java vs C++); the traps (UB, compiles-but-wrong, differs on Mac vs Linux); anything
  the instructor said would be quizzed, asked the room, or repeated twice.
- **What goes to Going Deeper** (links, not exposition), only what the class did not teach: standard-paragraph
  pedantry, obscure corner cases, exotic UB lawyering; features a later session covers; Compiler Explorer
  links, blog posts, talks, books, extra practice.
- **Structure:** front matter, TL;DR (5-8 bullets), body, **Cheat sheet** (one table or code block for the
  whole note), **Check yourself** (5-8 questions), Going Deeper.
- **Check-yourself shapes** (the shapes the class quizzes in): what does this print · what is `sizeof` this ·
  will this compile · which is faster and why. Answer plus a one-line reason.
- **Content requirements:**
  - Compilable snippets: fenced `cpp {run}`, complete enough to build (no `...` where a real fifteen-line
    program fits), the command used (`g++ -std=c++20 -O2 main.cpp -o main`), and the **actual output** in a
    `text` block beneath.
  - **Show the memory:** ASCII boxes with addresses and byte offsets for pointers, stack frames, struct
    padding and cache lines. That picture is what the reader is revising.
  - Comparison tables: pointer vs reference · stack vs heap · `new`/`delete` vs `malloc`/`free` · array vs
    `std::array` vs `std::vector` · pass by value vs pointer vs reference vs `const&` · `unique_ptr` vs
    `shared_ptr` · C++ vs Java object cost · `map` vs `unordered_map`.
  - Assembly and `sizeof` only when they teach; keep the excerpt to the few instructions that make the
    point and name the flags that produced them.
  - **Latency numbers** (L1 vs L2 vs RAM, branch misprediction) only as order-of-magnitude, labelled so.
  - State the standard version (C++11 move, C++17 structured bindings, C++20 concepts) when it matters.
- **Figures (`class+generated`):** every board drawing the class made appears (embedded crop of the frame,
  a clean redraw, or both; a drawing a correction affects is redrawn corrected). Mermaid stays off in Basic;
  ASCII boxes with addresses are an addition, not a replacement.

## Advanced tier — subject rules

- **Ladder** (per concept): the problem it solves, then the mental model, then **down to bytes and
  assembly** (`g++` and `clang++` where they differ), then a **compiled worked example with output**, then
  the **standard citation** (cppreference, or the draft's stable section name such as `[expr.unary.op]`),
  then the **UB and failure catalogue** (with `-fsanitize=address,undefined` runs and their real error
  text), then **HFT production reality**, then **interview angles** (HRT / Jane Street / Optiver-style
  questions with model answers and follow-ups), then **hands-on** (build, break, fix), then Further Study.
- **Figures:** every class board drawing (preferring clean redraws, or both, where a redraw adds addresses
  or fixes an error), plus generated PNGs for memory layouts, stack frames, struct padding and cache lines
  (`memory_strip`); latency ladders labelled SCHEMATIC. Small byte layouts may stay ASCII.
- **Sources:** prefer `research.preferred_sources`; every note cites cppreference at least once. Further
  Study: the C++ Core Guidelines, Meyers *Effective Modern C++*, Agner Fog's optimisation manuals,
  Drepper's "What Every Programmer Should Know About Memory", CppCon talks.
- Hands-on tasks use the order-book vocabulary (`Order`, `price`, `qty`) and are runnable with `g++`.
- One note per session: a full assembly walkthrough is a section of the note, never a companion file.

## Domain conventions

- **Names:** real, plausible domain names over `foo`/`bar`: `Order`, `price`, `qty`, `OrderBook`,
  `TickData`, `NSE`. Reuse the instructor's own examples: assignments echo them.
- **Standard and platform:** always state the standard version when it matters and the compiler/platform
  when behaviour differs (Apple Silicon vs x86-64 Linux, `clang++` vs `g++`).
- **Windows deltas** (readers are often on Windows 11 / PowerShell): one line, not a rewrite. System V passes the
  first integer arguments in `RDI`/`RSI`; Windows x64 uses `RCX`/`RDX`. `sizeof(long)` is 4 on Windows
  (LLP64) and 8 on Linux/macOS (LP64). `g++` on Windows is MinGW (or use WSL2); MSVC is a different ABI.
- **Limits:** code and ASCII under **95 columns**; tables under **9 columns**.
- **Jargon in bold on first use, defined in the same sentence.** Examinable vocabulary: *decay*, *dangling*,
  *dereference*, *translation unit*, *linkage*, *RAII*, *cache line*, *false sharing*.

## ASR corrections

Silently fix these; never reproduce them.

| Transcript | Correct |
| --- | --- |
| runs of "you you you", "Thank you. Thank you." | **dropped audio**: check Whisper and frames, else reconstruct from the code and mark it |
| "hash include", "hash include iostream" | `#include <iostream>` |
| "C out", "see out" / "C in" | `std::cout` / `std::cin` |
| "std C out", "standard C out" | `std::cout` |
| "end L", "and line" | `std::endl` |
| "name space" | `namespace` |
| "asterisk", "star" (before a variable) | the **dereference** `*`, or a pointer declaration: write the code, not the word |
| "ampersand", "and operator" | **address-of** `&`, or a reference: disambiguate from context |
| "null putter", "null P T R" | `nullptr` |
| "dot O file", "a dot out" | the **object file** `main.o` / the executable `a.out` |
| "G plus plus", "GPP" | `g++` |
| "obj dump" | `objdump` |
| "V table", "we table" | **vtable** (virtual function table) |
| "S T L", "standard template library" | **STL** |
| "reserve" vs "resize" (used interchangeably) | check which was meant: the class conflates them; they differ |
| "unique putter" / "shared putter" | `std::unique_ptr` / `std::shared_ptr` |
| "L one", "L two" cache | **L1** / **L2** |
| "seg fault", "segmentation" | **segmentation fault** |
| "G C", "garbage collector" (re Java) | keep as **GC**, contrasted with C++'s deterministic destruction |
| "nse" inside ordinary words | ASR artefact from "sense"/"response": ignore unless the exchange is genuinely meant |

## Callouts

`> **Note:**`, `> **Gotcha:**`, `> **UB:**`, `> **Quiz tip:**`, `> **HFT reality:**`,
`> **Class correction (severity):**`, `> **Reconstructed:**`, and in Basic the generic `> **Gap fill:**`.

## Known context

- The class often differs from the instructor repo (for example `Lec-2` gained `fix_pointers.cpp` after class).
  That is expected: notes follow the class and use a neutral `> **Repo note:**`, never a correction or a gap.
- The assessment weights above come from a syllabus PDF whose grid is **column-scrambled**: treat them as
  approximate and confirm in class. Never assert them as fact in a note.
- Slides land as `L<n>.pdf` (some as `L<n>_.pdf`) and are handwritten; not every session has one.
- Class timings in the digest are as read off the screen/transcript, not from an official schedule.
- Transcript quality varies by session: some begin with a junk run, and the generator named in the header
  differs. Whisper JSON and keyframes exist for some sessions only.
- Recordings sit under `resources/videos/`; existing keyframes (`resources/frames/<stem>/`) and Whisper
  output (`<stem>.whisper.json`) are reused read-only. After a successful ingest the plugin offers to delete
  the video (`video.delete_after_ingest: ask`); the archived frames and Whisper stay.
- Earlier notes (`notes/01-...`) set the vocabulary the Advanced ledger may assume; cross-link them.
- **Extra session:** `Fast Orderbooks` (class date 2026-10-04) is not in the syllabus table. It sits between
  sessions 12 and 13 and is carried as session 14 in `sessions` so the syllabus numbering is untouched; its
  note belongs between 12 and 13 and feeds the capstone. Titles of the other sessions are the syllabus's own.
- **Duplicate transcript:** `Low_latency_C++_hacks_-_Class_Scaler_Academy (1).txt` is byte-identical to the
  file without "(1)"; use the one without it (the `stem` does).
- **Missing transcript:** session 4 (Memory Management + STL) has a recording and Whisper JSON but no Scaler
  transcript on disk yet. A `Templates and Generic Programming v2.mp4` recording also exists (unused).
- **Capstone handout:** `Capstone_ Build a Low-Latency Order Book.docx` is the capstone brief,
  matched by `paths.handouts` (course-wide documents, not tied to one session). Read it for session 13.
- Slides on disk: `L1` to `L8` (`L4_`, `L5_` carry a trailing underscore); later sessions may have none.
