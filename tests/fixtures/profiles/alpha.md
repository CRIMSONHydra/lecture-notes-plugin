---
schema: 1
id: alpha
aliases: [alpha-course]
title: "Example Systems Programming"
short: "Example SP"
prefix: ESP
institution: "Example University, synthetic fixture"
spelling: en-GB
paths:
  slides: ["L{n}.pdf"]
  syllabus: "Syllabus*.pdf"
instructor_code: {kind: github, repo: "example-org/example-course-code", ref: HEAD, session_dir: "Lec-{n}"}
sessions:
  - {n: 1, title: "Introduction", stem: "Introduction_-_Lecture", date: 2026-01-05}
  - {n: 2, title: "Memory and Pointers", stem: "Memory_and_Pointers_-_Lecture", date: 2026-01-08}
  - {n: 3, title: "Templates", stem: "Templates_-_Lecture", date: 2026-01-12}
  - {n: 4, title: "Extra Workshop", order: 2.5}
asr: {language: en, hotwords: "pointer, vtable, nullptr"}
snippets:
  cpp: {ext: .cpp, compile: "g++ -std=c++20 {src} -o {exe}", run: "{exe}"}
tiers:
  basic: {words: [3500, 7000], minutes: [60, 120], figures: class+generated, diagrams: ascii, math: false}
  advanced: {words: [6000, 15000], math: true}
callouts: {"Exam tip": tip}
video: {delete_after_ingest: ask}
---

# Profile: Example Systems Programming

Synthetic fixture for the profile self-test. Not a real course.

## Role

A senior practitioner writing revision notes.

## Reader

A third-year student. May assume: loops, functions. Must not assume: pointers.

## Course

| # | Session title | Core topics |
| --- | --- | --- |
| 1 | Introduction | course overview, toolchain basics |
| 2 | Memory and Pointers | stack, heap, pointers |
| 3 | Templates | generic functions, deduction |

## Source handling

Quote instructor code from the linked repository.

## Basic tier — subject rules

The whole class at class depth.

## Advanced tier — subject rules

Teach from first principles with primary sources.

## Domain conventions

Use realistic names; state the compiler and platform.

## ASR corrections

| Transcript | Correct |
| --- | --- |
| "hash include" | `#include` |
| "null putter" | `nullptr` |
| "V table" | vtable |
| runs of "you you you" | **dropped audio**: check Whisper and frames, else reconstruct and mark it |

## Callouts

`Exam tip` plus the built-ins.

## Known context

Fixture only.
