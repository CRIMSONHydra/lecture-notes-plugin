---
title: "Ticks and the order book"
session: 1
tier: basic
profile: cpp
date: 2026-08-19
duration_min: 120
sources: ["Fixture_Ticks_-_Class_Scaler_Academy.txt"]
instructor_code: "Lec-1/main.cpp"
revision_minutes: 25
verified_as_of: 2026-10-03
---

# 01 · Ticks and the Order Book

## TL;DR

- A **tick** is one market-data event; an **order book** keeps resting orders sorted by price.
- Latency is measured in nanoseconds, so the layout of an `Order` in memory matters.
- `sizeof(Order)` is a multiple of its alignment, not the sum of its members.
- Revision links: see [the stack frame note](02-the-stack-frame.md) and the [index](README.md).

## 1. What a tick carries

A **tick** is a single price update for one instrument. The book stores **orders**, each with a
price, a quantity and a side.

```cpp {run}
#include <cstdint>
#include <iostream>

struct Order {
    std::uint64_t id;
    std::int32_t  price;   // in ticks, not rupees
    std::int32_t  qty;
    char          side;    // 'B' or 'S'
};

int main() {
    std::cout << sizeof(Order) << '\n';
    return 0;
}
```

```text
$ g++ -std=c++20 -O0 main.cpp -o main && ./main
24
```

> **Note:** `price` is an integer number of ticks. Floating point prices are a classic trap.

> **Gotcha:** `char side` adds 1 byte but the struct grows by 8, because the **alignment** of
> `std::uint64_t` forces tail padding.

> **UB:** reading an uninitialised `qty` is undefined behaviour, whatever `-O0` happens to print.

> **Quiz tip:** `sizeof` of a struct is never smaller than the sum of its members.

> **HFT reality:** a cache line is 64 bytes, so a 24-byte `Order` fits two and a half per line.

> **Class correction (🔴 changes an answer):** the class said `RSP` is the instruction pointer -> the
> instruction pointer is `RIP`; `RSP` is the stack pointer -> it changes what you read in a listing.

> **Class correction (🟠 high):** AT&T and Intel are assembly *notations*, not instruction sets.

> **Class correction (🟡 low):** `cout` is buffered, not one syscall per `<<`.

> **Reconstructed:** the audio drops here; this follows `Lec-1/main.cpp`.

> **Gap fill:** the class never showed the padding. With `id` at offset 0, `price` at 8, `qty` at 12
> and `side` at 16, the last 7 bytes are padding so the next array element stays 8-aligned.

## 2. The book as a picture

```text
  price   qty     side                           bids          asks
 +-------+-------+------+                        +----+        +----+
 | 10010 |   200 |  S   |   best ask  ------->   |    |        | 10 |
 | 10005 |   500 |  B   |   best bid  ------->   | 05 |        |    |
 +-------+-------+------+                        +----+        +----+
 |<--- 4 B --->|<-- 4 B -->|<-- 1 B (+7 pad) -->|
```

## 3. Severity and the wide table

| Where | What | Severity | Cost | Fix | Owner | Status | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `main.cpp` | uninitialised read | 🔴 | high | initialise | class | open | UB |
| `main.s` | RSP vs RIP | 🟠 | medium | relabel | class | open | register |
| `demo.cpp` | cout buffering | 🟡 | low | footnote | class | closed | aside |

```bash
g++ -std=c++20 -O2 main.cpp -o main
./main
```

## 4. Check yourself

1. What does `sizeof(Order)` print above? **24** -- 17 bytes of members plus 7 bytes of tail padding.
2. Which register holds the next instruction address? **`RIP`** -- `RSP` is the stack pointer.
3. Is a floating point price safe? **No** -- binary fractions cannot represent most decimal prices.
4. Will `side` change `sizeof`? **Yes, by 8** -- alignment rounds the struct up.
5. Why is the book sorted by price? **Matching walks the best price first.**

## Going Deeper

- Drepper, *What Every Programmer Should Know About Memory*.
- cppreference: [`sizeof`](https://en.cppreference.com/w/cpp/language/sizeof).
