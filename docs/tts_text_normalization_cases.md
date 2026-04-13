# Finnish TTS Text Normalization Inventory

Source corpus: `source.pdf` (Finnish legal-history text, 371 123 chars),
extracted via `src.pdf_parser.parse_pdf`. Used to scope a Finnish normalizer for
Chatterbox-TTS.

## Severity ranking (worst audio impact first)

| # | Category | Matches | Severity | Tool |
|---|----------|---------|----------|------|
| 1 | Century expressions `NNNN-luvun` | 341 | CRITICAL | custom regex + num2words |
| 2 | Year numbers | 822 | CRITICAL | num2words (ordinal/cardinal) |
| 3 | Numeric ranges `NNNN-NNNN` | 114 | HIGH | custom regex |
| 4 | Elided hyphen compounds `keski-ja` | ~15 | HIGH | custom expansion |
| 5 | Latin / German legal terms | ~100 | HIGH | phoneme lexicon |
| 6 | Hyphenated compounds | 135 | MEDIUM | often OK, audit |
| 7 | Roman numerals | 62 | MEDIUM | roman-to-int + num2words |
| 8 | Page refs `s. 139` | 141 | MEDIUM | custom regex |
| 9 | Parenthetical citations | 25 | MEDIUM | strip or reword |
| 10 | Abbreviations `eKr./jKr.` | 16 | MEDIUM | lookup table |
| 11 | Percentages `22 %` | 6 | LOW | num2words + "prosenttia" |
| 12 | Curly quotes `”` | 121 | LOW | strip/normalize |
| 13 | URLs / emails | 2 | LOW | spell out or skip |
| 14 | Section marker `27 §` | 1 | LOW | "pykälä" |
| 15 | OCR garbage `■` | 3 | LOW | strip |

## Category details

### 1. Century expressions (341)
Examples: `1100-luvun`, `1800-luvulla`, `1500-luvulle`, `1300-luvulla`, `1200-luvulta`.
Naive TTS reads "yksi-yksi-nolla-nolla viiva luvun". Must convert to
`tuhatsadan luvun`, preserving Finnish case endings (`-luku/-luvun/-luvulla/-luvulta/-luvulle`).
num2words cannot do this — needs custom regex that strips the hyphen, passes the
number through num2words in genitive, and reattaches the declined `luku` form.
**68 unique surface forms** in this text alone.

### 2. Year numbers (822)
Examples: `1456`, `1500`, `1809`, `1680`, `1918`.
num2words(lang='fi') handles cardinals. For years Finnish speakers usually read
them as cardinals (`tuhat neljäsataa viisikymmentäkuusi`), so direct num2words
works — but must NOT fire inside a range or century expression, so ordering of
normalization passes matters.

### 3. Numeric ranges (114)
Examples: `1500-1800`, `400-1500`, `1100-1300`, `1618-1648`, `1630-1789`.
The hyphen here means "to" (Finnish: `...sta ...een`). Naive TTS says "miinus".
Plus ISBN-like forms `978-951-51-4999-2` and `1456-842` must be detected
separately or they'll be mangled. **Critical: disambiguate from `NNNN-luvun`**
— the century regex must run first.

### 4. Elided hyphen compounds (surprise finding)
Examples: `Yhteiskunta-ja`, `keski-ja`, `1100-ja`, `1630-ja`, `paikallis-ja`.
Finnish orthographic convention: `keski- ja uuden ajan` means "middle and new
age". The trailing hyphen marks an omitted shared suffix. Chatterbox will read
"keski viiva ja". Needs expansion or hyphen stripping with pause. **This is the
highest-impact case that plain regex libraries won't cover.**

### 5. Latin / German legal terms
Latin probes: `usus modernus` x7, `ius commune` x9, `ius publicum` x1,
`corpus iuris` x38, `ius proprium`, `ius gentium`. German: `Landrecht`,
`Reichskammergericht`, `Rechtsgeschichte`, `Gerichtsordnung`.
A Finnish TTS will apply Finnish phonotactics ("korpus iuris" → "korpus i-u-ris"
which is actually OK; but `Reichskammergericht` becomes unintelligible).
num2words irrelevant — needs a **domain lexicon** or SSML phoneme hints (Chatterbox
doesn't support SSML, so best-effort = respell phonetically: `Reichskammergericht` →
`Raihskammergericht`).

### 6. Hyphenated compounds (135, 97 unique)
Examples: `sääty-yhteiskunnan`, `sääty-yhteiskunnaksi`, `tasa-arvoa`, `e-mail`,
`forum-iuris`. Most are pronounced fine by Finnish TTS because the hyphen just
signals a compound boundary. Audit needed; low priority.

### 7. Roman numerals (62)
Examples: `I`, `II`, `III`, `V`, `VI`, `VII`, `XI`, `XII`, `XIV`, `XV`, `XVI`,
`XIX`. Used for centuries (`XVII vuosisata`) and regnal numbers (`Kaarle XI`).
TTS reads them as letters. Convert with `roman` package + num2words(to='ordinal').
Ambiguity: `I` and `V` collide with common words — must require uppercase
and word-boundary context.

### 8. Page references (141)
`s.` x139, `ss.` x2. Examples: `s. 139`, `ss. 12-15`. The period terminates a
sentence for a naive sentence-splitter → premature pause + wrong prosody. Expand
to `sivu 139`, `sivut 12–15`.

### 9. Parenthetical citations (25)
Examples: `(De jure helli ac pacis, 1625)`, `(Chicago: The University of Chicago
Press, 1987)`, `(Munchen: Beck, 1985)`. Recommendation: **drop entirely** in
audiobook mode — they add no listening value and poison prosody. Needs a
user-toggle.

### 10. Abbreviations
Only `eKr.` and `jKr.` appear (16 matches) — no `esim./ks./mm./vrt./ns./tri/prof.`
at all in this text. **Surprising**: the expected "Finnish abbreviation zoo" is
almost absent. Still need the lookup table for general corpora, but this PDF
won't stress-test it.

### 11. Percentages (6)
`22 %`, `28 %`, `95 %`, `60 %`, `33 %`, `7 %`. Finnish style uses the space.
Expand to `N prosenttia` (partitive). num2words handles the number.

### 12. Quotation marks
121 curly `”` (U+201D) and 7 curly singles, **zero** guillemets/French quotes,
zero en/em dashes. Normalize curly → straight or strip; no cross-language
quote handling needed for this corpus.

### 13. URLs / emails
`http://www.helsinki.fi/oik/tdk`, `forum-iuris@helsinki.fi`. Spell-out-letter
mode or skip. Low frequency.

### 14. Section marker
One `27 §` — read as `pykälä`. Trivial regex.

### 15. OCR garbage
Three `■` (U+25A0) in one corrupted line: `'7-3. He & ■ t < L S K'`. Plus one
stray `©`. Strip control/symbol chars before normalization.

### 16. Decimals
Only 5 unique — mostly DOI/section numbers (`10.31885`, `1.1`, `1.2`, `4.0`) and
one range mis-detected as `1655,1680`. Finnish decimal comma is effectively
unused in this corpus. Still worth a regex because other texts will have them.

## Ordering constraints for normalizer passes

1. Strip OCR junk and normalize quotes.
2. Expand `NNNN-luku*` (before ranges, before year).
3. Expand numeric ranges `NNNN-NNNN` → `NNNN–NNNN` spoken as `...sta ...een`.
4. Expand elided hyphen compounds `X-ja Y` → `X ja Y` (or `X- ja Y` with pause).
5. Expand Roman numerals in regnal/chapter context.
6. Expand `s./ss./§/%/eKr./jKr.`.
7. Expand standalone years last (cardinals).
8. Optionally strip parenthetical citations.
9. Apply Latin/German respelling lexicon.
10. Pass remaining bare digits through num2words.

## Surprising findings

- **Elided hyphens** (`keski-ja`) are the single most damaging and least-covered
  case — not on the original spec list.
- **The classic Finnish abbreviation zoo is absent** from this text. The normalizer
  should still support it, but this PDF is a weak test for it.
- **Zero en-dashes / em-dashes**; the PDF extractor flattens everything to ASCII
  hyphen, which means range detection must work on `-` alone and cannot rely on
  `–` as a disambiguator.
- **Parenthetical bibliographic citations** (publisher, city, year) are too noisy
  to pronounce and should probably be dropped, not normalized.
- **No kpl/art./luku N** chapter markers — chapter boundaries are plain digits
  followed by a period and a capital (`1. Yleistä`).
