# Benchmark — `work-session` (iteration 1)

| Config | n | Pass rate | Duration (s) | Tokens |
|---|---:|---:|---:|---:|
| **with_skill** | 3 | 0.94 ± 0.08 | 77.0 ± 15.4 | 26,762 ± 3,143 |
| **without_skill** | 3 | 0.80 ± 0.18 | 100.2 ± 41.8 | 33,098 ± 3,347 |

## Deltas (with_skill − without_skill)

- **Pass rate**: +0.14
- **Tokens saved**: +6,336 (+19.1%)
- **Duration saved**: +23.2 s (+23.2%)

## Per-eval breakdown

### `with_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| claim-named-backlog-item | 7 / 7 | 1.00 | 31,183 | 97.9 |
| finish-and-clean | 5 / 6 | 0.83 | 24,945 | 71.8 |
| block-on-external | 5 / 5 | 1.00 | 24,157 | 61.4 |

### `without_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| claim-named-backlog-item | 4 / 7 | 0.57 | 37,728 | 156.2 |
| finish-and-clean | 5 / 6 | 0.83 | 31,632 | 88.5 |
| block-on-external | 5 / 5 | 1.00 | 29,933 | 55.8 |
