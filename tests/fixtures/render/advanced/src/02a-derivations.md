---
title: "Homography derivations"
session: 2
tier: advanced
profile: cv
date: 2026-08-22
verified_as_of: 2026-10-03
companion_of: "02"
---

# 02a · Homography Derivations

## 1. Setting up the system

Back to the spine note: [02](02-matching-basics.md#1-the-ratio-test). Each pair $(x, y) \to (x', y')$ gives

$$ x' = \frac{h_{11}x + h_{12}y + h_{13}}{h_{31}x + h_{32}y + h_{33}} $$

### 1.1 Stacking four pairs

Eight equations, eight unknowns once $h_{33}=1$.

## 2. Why four

> **Gotcha:** three collinear points are not enough; the system becomes singular.

## Further Study

- Hartley and Zisserman, chapter 4.
