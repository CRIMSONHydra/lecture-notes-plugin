---
schema: 1
id: cpp
aliases: [hft-cpp, "c++"]
title: "Systems Programming 101"
short: "SysProg 101"
prefix: CPP
institution: "Example University — Batch 2030, Term 1"
spelling: en-GB
paths:
  transcripts: "{stem}.txt"
  recordings: "videos/{stem}.mp4"
  frames: "frames/{stem}"
  whisper: "{stem}.whisper.json"
  slides: ["L{n}.pdf", "L{n}_.pdf"]
  syllabus: "Batch 2030 Term 1 Syllabus - Systems Programming 101.pdf"
  classwork: "Classwork/{n}"
instructor_code:
  kind: github
  repo: "example-org/course-code"
  ref: HEAD
  session_dir: "Lec-{n}"
  ignore: ["*.o", "*.out", "*.exe", "*.class", "*.swp", ".*.swp"]
sessions:
  - {n: 1, title: "Intro to Systems", stem: "Intro_to_Systems_-_Class_Scaler_Academy", date: 2026-08-19}
  - {n: 2, title: "Memory Layout", stem: "Memory_Layout_-_Class_Scaler_Academy", date: 2026-08-22}
  - {n: 3, title: "Memory Layout 2", stem: "Memory_Layout_2_-_Class_Scaler_Academy", date: 2026-08-24}
  - {n: 4, title: "Heaps and Allocators", stem: "Heaps_and_Allocators_-_Class_Scaler_Academy"}
  - {n: 5, title: "Generic Containers", stem: "Generic_Containers_-_Class_Scaler_Academy"}
  - {n: 6, title: "Metaprogramming and Move Semantics"}
asr:
  language: en
  hotwords: "C++ std::cout nullptr vtable RBP RSP RIP objdump g++ clang++ order book cache line pointer reference stack heap assembly"
output: {root: null, inside_repo_ok: false}
---

# Minimal cpp profile (ingest fixture)

Front matter only, pointing at the real cpp subject repo (read-only). The full profile with the
persona, course map and ASR table is the subject profile (a different work package).
