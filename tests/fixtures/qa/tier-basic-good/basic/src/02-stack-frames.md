---
title: "Stack Frames, Briefly"
session: 2
tier: basic
profile: cpp
date: 2026-08-22
duration_min: 120
sources: ["Memory_Layout_-_Class_Scaler_Academy.txt"]
instructor_code: ["Lec-2/main.cpp"]
revision_minutes: 5
verified_as_of: 2026-10-03
---

# 02 · Stack Frames, Briefly

## TL;DR

- A function call pushes a **stack frame**: the caller's `RBP`, the return address, then the locals.
- The stack grows toward **lower** addresses; `RBP` marks the base of the current frame.
- Arguments travel in registers, the result comes back in `EAX`.
- Nothing is erased on return, so a stale value can reappear in the next call: that is **UB**.
- `RSP` is the stack pointer; `RIP` is the instruction pointer. They are not the same register.

## The frame of add

The class compiled `Lec-2/main.cpp` at `-O0` and read the frame off the assembly.

```cpp {run}
#include <cstdio>
int add(int x, int y) { return x + y; }
int main() {
    std::printf("add(12, 13) = %d\n", add(12, 13));
    return 0;
}
```

```text
$ g++ -std=c++20 main.cpp -o main && ./main
add(12, 13) = 25
```

```text
 higher addresses
 +--------------------+  rbp+8
 | return address     |
 +--------------------+  rbp
 | saved RBP          |
 +--------------------+  rbp-4
 | x = 12             |
 +--------------------+  rbp-8
 | y = 13             |
 +--------------------+
 lower addresses
```

| Register | Holds | Moves on `call`? |
| --- | --- | --- |
| `RIP` | next instruction | yes |
| `RBP` | frame base | after `push rbp` |
| `RSP` | stack top | yes |

> **Class correction (🔴 changes an answer):** the class said `RSP` holds the line of code being
> executed. That is `RIP`. `RSP` is the stack pointer, the address of the top of the stack.

> **Gap fill:** the class never showed why the saved `RBP` exists. It lets the epilogue restore the
> caller's frame base, so the caller finds its own locals again after the call returns.

## Cheat sheet

| Thing | Rule |
| --- | --- |
| Stack direction | down, toward lower addresses |
| Arguments | registers (`edi`, `esi` on Linux) |
| Result | `eax` |

## Check yourself

1. **Which register holds the next instruction?** `RIP`, not `RSP`.
2. **Where does `x` live relative to `RBP`?** At `rbp-4` for the first `int` argument.
3. **Is reading a stale local defined?** No, it is UB.
4. **How many bytes does the return address take on x86-64?** Eight.

## Going Deeper

- [Compiler Explorer](https://godbolt.org) to watch the assembly change with `-O`.
- The System V AMD64 ABI for the real calling convention.
- Advanced note 02 covers the same frame with figures.
