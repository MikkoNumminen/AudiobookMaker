# Benchmark — `release-cut` (iteration 1)

| Config | n | Pass rate | Duration (s) | Tokens |
|---|---:|---:|---:|---:|
| **with_skill** | 3 | 1.00 ± 0.00 | 90.7 ± 2.3 | 28,834 ± 2,716 |
| **without_skill** | 3 | 1.00 ± 0.00 | 144.3 ± 13.4 | 46,853 ± 2,879 |

## Deltas (with_skill − without_skill)

- **Pass rate**: +0.00
- **Tokens saved**: +18,019 (+38.5%)
- **Duration saved**: +53.6 s (+37.1%)

## Per-eval breakdown

### `with_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| casual-patch-bump | 8 / 8 | 1.00 | 32,627 | 93.3 |
| vague-ship-request | 4 / 4 | 1.00 | 27,462 | 91.0 |
| explicit-minor-bump | 7 / 7 | 1.00 | 26,414 | 87.8 |

### `without_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| casual-patch-bump | 8 / 8 | 1.00 | 50,008 | 163.2 |
| vague-ship-request | 4 / 4 | 1.00 | 43,047 | 133.1 |
| explicit-minor-bump | 7 / 7 | 1.00 | 47,503 | 136.7 |
