# Benchmark — `pronunciation-corpus-add` (iteration 1)

| Config | n | Pass rate | Duration (s) | Tokens |
|---|---:|---:|---:|---:|
| **with_skill** | 3 | 1.00 ± 0.00 | 83.8 ± 6.1 | 27,170 ± 2,324 |
| **without_skill** | 3 | 0.89 ± 0.08 | 113.2 ± 13.8 | 29,661 ± 2,055 |

## Deltas (with_skill − without_skill)

- **Pass rate**: +0.11
- **Tokens saved**: +2,491 (+8.4%)
- **Duration saved**: +29.4 s (+26.0%)

## Per-eval breakdown

### `with_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| single-turo-report | 6 / 6 | 1.00 | 25,057 | 75.1 |
| batch-five-reports | 7 / 7 | 1.00 | 26,047 | 88.6 |
| ambiguous-category | 6 / 6 | 1.00 | 30,406 | 87.6 |

### `without_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| single-turo-report | 5 / 6 | 0.83 | 26,774 | 93.7 |
| batch-five-reports | 7 / 7 | 1.00 | 30,813 | 122.1 |
| ambiguous-category | 5 / 6 | 0.83 | 31,396 | 123.7 |
