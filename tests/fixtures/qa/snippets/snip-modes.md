---
title: Snippet fixture (modes)
session: 2
tier: basic
---

# 02 · Snippet fixture, modes

`{run nocheck}` compiles and runs but never compares (platform-dependent output):

```cpp {run nocheck}
#include <cstdio>
int main() { std::printf("%zu\n", sizeof(long)); return 0; }
```

```text
8
```

`{compile}` only has to build, and needs no output block:

```cpp {compile}
int add(int x, int y) { return x + y; }
int main() { return add(1, 2) == 3 ? 0 : 1; }
```
