---
title: Snippet fixture (correct output)
session: 2
tier: basic
---

# 02 · Snippet fixture, correct

Compile, run, compare. The `$` line is a command and is dropped; the rest must match.

```cpp {run}
#include <cstdio>
int add(int x, int y) { return x + y; }
int main() {
    std::printf("add(12, 13) = %d\n", add(12, 13));
    std::printf("sizeof(int) = %zu\n", sizeof(int));
    return 0;
}
```

```text
$ g++ -std=c++20 main.cpp -o main && ./main
add(12, 13) = 25
sizeof(int) = 4
```

A non-zero exit status is not a mismatch (main returns x in the class example).

```cpp {run}
#include <cstdio>
int main() { std::puts("exit 7"); return 7; }
```

```text
exit 7
```

Plain fences are never compiled.

```cpp
this is not valid C++ and must be ignored
```
