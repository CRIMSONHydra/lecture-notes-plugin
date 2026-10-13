---
title: "The stack frame"
session: 2
tier: basic
profile: cpp
date: 2026-08-22
duration_min: 120
sources: ["Fixture_Stack_-_Class_Scaler_Academy.txt"]
instructor_code: "Lec-2/main.cpp"
revision_minutes: 30
verified_as_of: 2026-10-03
---

# 02 · The Stack Frame

## TL;DR

- The stack grows from high addresses to low; a call pushes a **frame**.
- Marks that must not become Type 3 fonts: ✓ done, ✗ wrong, ⇒ implies, ⭐ starred, ✅ passed.
- Earlier ideas live in [note 01](01-ticks-and-the-order-book.md#1-what-a-tick-carries).

## 1. Calling `add`

```cpp {run}
#include <iostream>

int add(int x, int y) { return x + y; }

int main() {
    int a = 12, b = 13;
    std::cout << add(a, b) << '\n';
}
```

```text
25
```

```cpp {run nocheck}
#include <cstdio>
int main() { std::printf("%zu\n", sizeof(long)); }
```

```text
8
```

> **Gotcha (platform):** on Windows (LLP64) `sizeof(long)` is 4; the output above is from Linux.

```text
## this line looks like a heading but sits inside a fence and must not be numbered
        high addresses
   +----------------------+
   |  return address      |  <- pushed by CALL      ✓ saved
   +----------------------+
   |  saved RBP           |  <- pushed by prologue  ⇒ RBP now points here
   +----------------------+
   |  local: int a        |  [rbp-4]
   +----------------------+
        low addresses
```

### 1.1 Prologue and epilogue

The prologue is `push rbp; mov rbp, rsp`; the epilogue undoes it.

### 1.2 Arguments in registers

System V passes the first integer arguments in `RDI` and `RSI`; Windows x64 uses `RCX` and `RDX`.

## 2. Wide code that still fits

```cpp
std::vector<std::pair<std::uint64_t, std::int32_t>> book_levels = load_levels("NSE", "RELIANCE", 5);
```

## 3. Check yourself

1. Which way does the stack grow? **Down** -- toward lower addresses.
2. What does `CALL` push? **The return address.**
3. Will `int a;` print `0`? **Not guaranteed** -- reading it is UB.
4. Which register holds the frame base? **`RBP`.**
5. Which is faster, a register argument or a stack argument? **Register** -- no memory access.

## Going Deeper

- System V AMD64 ABI, section 3.2.
