---
schema: 1
id: cpp
title: "Systems Programming 101 (QA fixture)"
short: "HP C++ - QA fixture"
prefix: CPP
institution: "Example University - fixture"
spelling: en-GB
instructor_code: {kind: github, repo: "example-org/course-code", ref: HEAD, session_dir: "Lec-{n}"}
sessions:
  - {n: 1, title: "Intro to Systems", stem: "S1", date: 2026-08-19}
  - {n: 2, title: "Memory Layout", stem: "S2", date: 2026-08-22}
  - {n: 3, title: "Memory Layout 2", stem: "S3", date: 2026-08-24}
  - {n: 4, title: "Heaps and Allocators"}
snippets:
  cpp: {ext: .cpp, compile: "definitely-not-a-compiler-xyz {src} -o {exe}", run: "{exe}"}
tiers:
  basic:
    words: [120, 400]
    minutes: [2, 8]
    figures: none
    diagrams: ascii
    math: false
    check_yourself: [3, 5]
    required_sections: ["TL;DR", "Cheat sheet", "Check yourself", "Going Deeper"]
    closing_sections: ["Going Deeper"]
  advanced:
    words: [150, 600]
    figures: expected
    min_figures: 2
    diagrams: mermaid-ok
    math: true
    min_sources: 4
    min_domains: 3
    check_yourself: [3, 5]
    required_sections: ["What you need before this note", "TL;DR", "One-screen summary", "New words in this note", "Check yourself", "Hands-on", "Further Study", "Sources"]
    closing_sections: ["Further Study", "Sources"]
research:
  preferred_sources: ["en.cppreference.com"]
  required_domains: ["en.cppreference.com"]
---

# QA fixture profile

Minimal profile for `tests/selftest_qa.py`: tiny budgets so fixture notes stay short. Not a real subject.
