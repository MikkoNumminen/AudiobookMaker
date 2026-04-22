# Benchmark — `fi-normalizer-pass` (iteration 1)

| Config | n | Pass rate | Duration (s) | Tokens |
|---|---:|---:|---:|---:|
| **with_skill** | 3 | 1.00 ± 0.00 | 169.5 ± 44.8 | 48,496 ± 9,513 |
| **without_skill** | 3 | 0.83 ± 0.14 | 142.9 ± 40.3 | 43,941 ± 11,389 |

## Deltas (with_skill − without_skill)

- **Pass rate**: +0.17
- **Tokens saved**: -4,555 (-10.4%)
- **Duration saved**: -26.6 s (-18.6%)

## Per-eval breakdown

### `with_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| new-pass-p-compound-splitter | 7 / 7 | 1.00 | 51,049 | 155.5 |
| extend-pass-i-lexicon | 6 / 6 | 1.00 | 35,781 | 123.1 |
| debug-page-range | 6 / 6 | 1.00 | 58,659 | 230.0 |

### `without_skill`

| Eval | Passed | Pass rate | Tokens | Duration (s) |
|---|---:|---:|---:|---:|
| new-pass-p-compound-splitter | 7 / 7 | 1.00 | 53,572 | 157.3 |
| extend-pass-i-lexicon | 4 / 6 | 0.67 | 27,945 | 87.9 |
| debug-page-range | 5 / 6 | 0.83 | 50,305 | 183.4 |
