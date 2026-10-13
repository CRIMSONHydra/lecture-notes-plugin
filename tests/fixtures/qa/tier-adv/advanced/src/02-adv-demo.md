---
title: "Stack Frames, Deeply"
session: 2
tier: advanced
profile: cpp
date: 2026-08-22
duration_min: 120
sources: ["Memory_Layout_-_Class_Scaler_Academy.txt"]
instructor_code: ["Lec-2/main.cpp"]
revision_minutes: 25
verified_as_of: 2026-10-03
prerequisites: ["01-why-cpp"]
companion_of: null
---

# 02 · Stack Frames, Deeply

## What you need before this note

You need to know what a register is and that memory has addresses. Nothing else.

## TL;DR

- A call builds a **stack frame** below the caller's frame, as the C++ reference describes for
  [function calls](https://en.cppreference.com/w/cpp/language/main_function).
- `RBP` anchors the frame; `RSP` is the live top of the stack.
- Reading an uninitialised local is **UB** by the language rules on
  [indeterminate values](https://en.cppreference.com/w/cpp/language/pointer).

## The frame, byte by byte

The figure shows `add(12, 13)` at `-O0`. The caller's `RBP` sits above the two `int` locals.

![frame of add](figures/02-stack-frame.png "**The frame.** return address, saved RBP, x and y, with illustrative addresses.")

![latency ladder](figures/02-latency-ladder.png "**Latency.** each level is roughly 4 to 10 times slower; schematic.")

```cpp {run}
#include <cstdio>
int add(int x, int y) { return x + y; }
int main() { std::printf("%d\n", add(12, 13)); return 0; }
```

```text
25
```

The ABI that fixes the argument registers is published by the
[x86-64 psABI](https://gitlab.com/x86-psABIs/x86-64-ABI) project, and Compiler Explorer at
[godbolt](https://godbolt.org) shows the assembly. Agner Fog's
[optimisation manuals](https://agner.org/optimize/) cover the cost side.

## One-screen summary

| Thing | Rule |
| --- | --- |
| Direction | down |
| Result | `eax` |

## New words in this note

- **stack frame**: the region a call uses for locals.

## Check yourself

1. **Which register is the stack pointer?** `RSP`.
2. **Where is the return address?** At `rbp+8`.
3. **Is a stale read defined?** No, UB.

## Hands-on

Compile the snippet at `-O0` and `-O2` and compare the assembly.

## Further Study

- Drepper, *What Every Programmer Should Know About Memory*.

## Sources

- <https://en.cppreference.com/w/cpp/language/main_function>
- <https://en.cppreference.com/w/cpp/language/pointer>
- <https://gitlab.com/x86-psABIs/x86-64-ABI>
- <https://godbolt.org>
- <https://agner.org/optimize/>
