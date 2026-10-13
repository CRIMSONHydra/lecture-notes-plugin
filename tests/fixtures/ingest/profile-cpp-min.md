---
schema: 1
id: cpp
aliases: [hft-cpp, "c++"]
title: "High-Performance C++ [HFT]"
short: "HP C++ · HFT"
prefix: CPP
institution: "Scaler School of Technology — SST 2028 batch, Term 9, HFT specialisation"
spelling: en-GB
paths:
  transcripts: "{stem}.txt"
  recordings: "videos/{stem}.mp4"
  frames: "frames/{stem}"
  whisper: "{stem}.whisper.json"
  slides: ["L{n}.pdf", "L{n}_.pdf"]
  syllabus: "SST 2028 Batch Term 9 Syllabus - High Performance C++.pdf"
  classwork: "Classwork/{n}"
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
  - {n: 5, title: "Templates and Generic Programming", stem: "Templates_and_Generic_Programming_-_Class_Scaler_Academy"}
  - {n: 6, title: "Metaprogramming and Move Semantics"}
asr:
  language: en
  hotwords: "C++ std::cout nullptr vtable RBP RSP RIP objdump g++ clang++ HFT order book cache line pointer reference stack heap assembly"
output: {root: null, inside_repo_ok: false}
---

# Minimal cpp profile (ingest fixture)

Front matter only, pointing at the real cpp subject repo (read-only). The full profile with the
persona, course map and ASR table is `profiles/cpp.md` (a different work package).
