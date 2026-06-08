# Comprehensive Finnish TTS Normalizer — Design Document

Status: DESIGN ONLY. No code in this document. This is the north star we will
implement against in subsequent sessions. Reviewer: user. Implementer: future
implementation sessions working against this spec.

The goal is to raise Finnish TTS output (any engine: Edge-TTS Noora,
Chatterbox, Piper, Qwen-TTS) to the level where Edge-TTS Noora reads
general Finnish prose — including dense nonfiction PDFs — without
mispronunciations, wrong cases on number words, mangled loanwords, or
letter-by-letter Roman numerals.

The current normalizer (`src/tts_engine.py::normalize_finnish_text`) has eight
passes A–H. It handles century expressions, numeric ranges, bare years, page
refs, decimals, and elided hyphens. It does **not** handle grammatical case on
number words, loanword respelling, abbreviation expansion, Roman numerals, or
acronyms. The design below extends it to a full pipeline.

---

## 1. Governor-word case detection

`num2words(n, lang='fi', case=X)` supports all 15 Finnish grammatical cases.
The case of a number word is determined by the *governing word* — the
preposition, postposition, noun, or verb form that sits immediately before
or after the number and demands a specific case on its argument.

The normalizer must look at ±3 words of context around each bare integer,
match against a table of known governors, and pass the detected case to
num2words. When no governor matches, fall back to nominative (current
behaviour).

### Case name mapping (num2words)

num2words uses these case names for `lang='fi'`:

| Case | num2words name | Typical question |
|------|----------------|------------------|
| Nominative | `nominative` | kuka / mikä |
| Genitive | `genitive` | kenen / minkä |
| Partitive | `partitive` | ketä / mitä |
| Accusative | `accusative` | (rare, == genitive or nominative) |
| Inessive | `inessive` | missä |
| Elative | `elative` | mistä |
| Illative | `illative` | mihin |
| Adessive | `adessive` | millä / milloin |
| Ablative | `ablative` | miltä |
| Allative | `allative` | mille |
| Essive | `essive` | minä |
| Translative | `translative` | miksi |
| Abessive | `abessive` | (rare) |
| Instructive | `instructive` | (rare) |
| Comitative | `comitative` | (rare) |

(If num2words uses different identifiers, the lookup table below is the
source of truth for semantics; the string constants get adjusted once during
implementation and pinned with unit tests.)

### Governor table (≥30 entries)

The table is `(trigger regex or lemma, direction, case, category, notes)`.
"Direction" = B (before the number) or A (after the number). Governors
"before" the number mean the number is the governor's complement: `vuonna
1905` → `vuonna` is before, demands essive/nominative form of the year
(*spoken* as cardinal in nominative because Finnish year expressions use a
special construction — see notes). Governors "after" the number are case
clues from the following head noun: `1905 vuoden tapaus` (rare) or
`5 prosenttia` (partitive triggered by the following noun in partitive).

Grouped by semantic cluster.

#### Year governors

| Governor | Dir | Case (num2words) | Notes |
|----------|-----|------------------|-------|
| `vuonna` | B | nominative | "in the year X" — Finnish uses essive on `vuonna` but the year itself stays nominative. Keep as nominative. |
| `vuoden` | B | nominative | "of the year X". Year stays nominative. |
| `vuodelta` | B | nominative | "from the year X". |
| `vuoteen` | B | illative | "into / up to year X". Year goes illative. ⚠ needs verification. |
| `vuodesta` | B | elative | "from year X onward". Year goes elative. ⚠ needs verification. |
| `vuosina` | B | nominative | plural — "in years X and Y". |

⚠ The year cases are the single biggest source of uncertainty. Finnish
native convention is that years are usually read in nominative regardless of
the governing preposition (`vuodesta 1905` is spoken "vuodesta tuhatyhdeksänsataaviisi",
nominative form of the numeral). **Flag all year illative/elative as
uncertain and verify with a native speaker or libvoikko before shipping.**

#### Page governors

| Governor | Dir | Case | Notes |
|----------|-----|------|-------|
| `sivu` | B | nominative | "page X" |
| `sivulla` | B | adessive | "on page X" |
| `sivulta` | B | ablative | "from page X" |
| `sivulle` | B | allative | "onto page X" |
| `sivuilla` | B | adessive | plural |
| `sivusta` | B | elative | "from page X" |
| `sivuun` | B | illative | "to page X" |
| `s.` | B | nominative | abbrev — expand to `sivu` in Pass J, then case resolves |
| `ss.` | B | nominative | plural abbrev — expand to `sivut` |

#### Chapter / section governors

| Governor | Dir | Case | Notes |
|----------|-----|------|-------|
| `luku` | B | nominative | "chapter X" (as title) |
| `luvussa` | B | inessive | "in chapter X" |
| `lukuun` | B | illative | "to chapter X" |
| `luvun` | B | genitive | "of chapter X" |
| `luvusta` | B | elative | "from chapter X" |
| `pykälä` | B | nominative | legal "section X" |
| `pykälässä` | B | inessive | |
| `kappale` | B | nominative | "paragraph/chapter" |
| `kappaleessa` | B | inessive | |
| `osa` | B | nominative | "part X" |
| `osassa` | B | inessive | |

#### Percentage / ratio governors

| Governor | Dir | Case | Notes |
|----------|-----|------|-------|
| `prosenttia` | A | partitive | "X prosenttia" — the number is in nominative, but when the phrase is itself in partitive (rare) we may need to change it. Default: nominative on number, insert `prosenttia` as partitive suffix. |
| `prosentin` | A | genitive | |
| `promillea` | A | partitive | |

#### Measurement / counting governors

| Governor | Dir | Case | Notes |
|----------|-----|------|-------|
| `kertaa` | A | partitive | "X kertaa" → number in nominative; `kertaa` is already partitive. Default nominative on number. |
| `kerran` | A | genitive | "once" — but also "of the time". |
| `kappaletta` | A | partitive | "X kpl" |
| `vuotta` | A | partitive | "X vuotta" → number nominative. |
| `kuukautta` | A | partitive | |
| `päivää` | A | partitive | |
| `tuntia` | A | partitive | |
| `minuuttia` | A | partitive | |

Rule of thumb for partitive-head constructions: the *number* stays
**nominative** (`viisi kertaa`, not `viittä kertaa`). This means the
partitive head noun is a governor for *the head noun's form*, not for the
number. The normalizer should therefore NOT rewrite the number case here —
it should only confirm the head noun is rendered in partitive. For
num2words this means `case='nominative'` is correct.

#### Location / position governors

| Governor | Dir | Case | Notes |
|----------|-----|------|-------|
| `kohdalla` | B | adessive | "at point X" — number goes adessive |
| `kohdasta` | B | elative | |
| `kohtaan` | B | illative | |
| `rivillä` | B | adessive | "on line X" |
| `riviltä` | B | ablative | |
| `riville` | B | allative | |

#### Temporal (clock / date)

| Governor | Dir | Case | Notes |
|----------|-----|------|-------|
| `klo` | B | nominative | "klo 15" → "kello viisitoista" — clock cardinal, nominative. |
| `kello` | B | nominative | same |
| `päivänä` | B | essive | "Xnnen päivänä" — date essive. Usually requires ordinal form, not cardinal → special path. |
| `kuussa` | B | inessive | "in the Xth month" |

#### Ordinal contexts (should produce ordinals, not cardinals)

These do not translate to a `case=` argument; they flip num2words to
`to='ordinal'` mode and then apply a case on top.

| Governor | Form | Notes |
|----------|------|-------|
| `Kustaa` / `Kaarle` / `Adolf` (any regnal first name) | ordinal, nominative | "Kustaa II Aadolf" |
| `paavi` | ordinal | "paavi Pius IX" |
| `luku` in chapter title context | ordinal | "2. luku" → "toinen luku" |

#### Fallback

No governor matched within ±3 tokens → `case='nominative'`, `to='cardinal'`.
This preserves current behaviour.

### Uncertainty flags

Marked ⚠ above. Before shipping:

1. Verify year cases (`vuoteen`, `vuodesta`) with native speaker or
   libvoikko. The simplest safe default is "years are always nominative";
   this is what current Finnish radio announcers actually do.
2. Verify that partitive-head counting constructions leave the number in
   nominative (they do, but worth a test).
3. Decide the default for `klo` — cardinal nominative works for "kello
   viisitoista" (24h clock).

---

## 2. Loanword lexicon (Pass I)

A single lookup table covering `-ismi`, non-`valtio` `-tio`, Latin legal
phrases, foreign place names, and person names. This is where most of the
unhandled high-severity categories from the failure inventory live.

### Data format

**Choice: YAML file at `data/fi_loanwords.yaml`, loaded once at import time
into a Python dict.**

Rationale:

- YAML is diffable, reviewable, and native-speaker-editable without touching
  Python.
- A separate file keeps the table out of code review noise.
- Load-once-into-dict gives O(1) lookup at runtime.
- TSV was considered but YAML allows nested metadata (variants, notes,
  category) per entry without column explosion.

Schema per entry:

```yaml
- form: humanismi
  respelling: humanis-mi
  category: ismi
  variants: [humanismin, humanismia, humanismissa, humanismiksi, humanismille,
             humanismilla, humanismilta, humanismilta]
  notes: "force morpheme boundary for chatterbox"
```

### Smart vs dumb entries

**Smart.** Each entry is `(canonical surface form, phonetic respelling,
category, variant list)`. The respelling is not an IPA hint — it is an
actual respelling in Finnish orthography that we know the target TTS engine
will pronounce correctly. Example: `humanismi` → `humanis-mi` works because
Chatterbox tokenizes the hyphenated form into two clean syllables.

For Latin entries, the respelling is a Finnish-orthography approximation:

- `ius` → `jus`
- `iuris` → `juuris`
- `commune` → `kommune`
- `pandectarum` → `pandektarum`
- `Geschwindigkeitsbegrenzung` → `Geshvindigkaitsbegrentsung`

(These are not phonetically perfect. They are *better than what the TTS
produces unassisted*. Perfection requires SSML phoneme tags, which
Chatterbox and most open-source engines do not support.)

### Integration with regex passes

Runs as **Pass I**, **after** num2words expansion (Pass G) and **before**
morpheme-boundary splitting (Pass H). Rationale:

1. Loanwords never contain digits, so num2words and the loanword table
   don't collide.
2. Running after num2words means num2words can't accidentally consume part
   of a respelling (`humanis-mi` has no digits; safe).
3. Running before Pass H prevents double-splitting a respelled form.

Implementation: build a single compiled regex alternation from all keys in
the lexicon, word-boundary anchored, case-insensitive. For matched forms,
substitute with the `respelling` field.

### Declined forms

**Choice: list all declensions explicitly in `variants`.**

Rationale: stripping Finnish suffixes requires morphological analysis
(libvoikko). Until we introduce libvoikko, the lexicon enumerates declensions
manually. Each `-ismi` stem has ~12 common forms; this is tractable because
the failure inventory shows only ~45 unique `-ismi` surface forms total.

For `-tio` loanwords (228 occurrences), expect ~40–60 unique surface forms
after collapsing; still tractable.

Alternative: a "suffix stripper" that normalizes the word to nominative
singular before looking up the lexicon. Reject for v1 — too much risk of
false positives on native Finnish words that happen to end in `-ismi-like`
sequences (e.g. `mekanismi` is a loanword but `suomismi` does not exist,
`runnomismi`-style constructions don't, etc.). v2 can add libvoikko-based
stemming.

### Test corpus (20–30 representative words)

From the failure inventory:

```
humanismi, humanismin, humanismia, absolutismin, Absolutismia,
merkantilismia, merkantilismiksi, Merkantilismissa, Merkantilismin,
Merkantilismille, positivismin, liberalismi, nationalismi, feodalismi,
Oikeuspositivismi, konsiliarismissa, protestantismia, rationalismin,
oikeuspluralismin,
rationaalisen, Rationaalinen, instituutio, kodifikaatiot,
conductio, Rationalistinen, locatio, reseptio, Constitutio,
modernisaation, Actio, stipulatio,
ius commune, ius proprium, usus modernus pandectarum, corpus iuris,
ratio scripta, modus vivendi,
München, Berliini, Tukholma,
H. Esimerkki, A. B. Sukunimi
```

Every entry here becomes a unit test: `normalize_finnish_text("... <form> ...")`
must produce the respelling.

---

## 3. Abbreviation expansion (Pass J)

Expansion happens **before** number normalization so that `s. 42` can be
rewritten to `sivu 42` and then fed into the case-aware number pass.

### Data format

Same YAML approach, separate file `data/fi_abbreviations.yaml`. Entries:

```yaml
- abbr: "s."
  expansion: "sivu"
  trigger: "\\bs\\.\\s*(?=\\d)"
  category: page
  notes: "only when followed by a digit"
```

### Table

Grouped by category. Trigger regex in each row; the expansion is plain text.

#### Time

| Abbr | Trigger (regex) | Expansion | Notes |
|------|-----------------|-----------|-------|
| `klo` | `\bklo\.?\s+` | `kello ` | followed by digit |
| `kello` | — | — | already a word |

#### Reference

| Abbr | Trigger | Expansion | Notes |
|------|---------|-----------|-------|
| `ks.` | `\bks\.` | `katso` | |
| `vrt.` | `\bvrt\.` | `vertaa` | |
| `ts.` | `\bts\.` | `toisin sanoen` | |
| `eli` | — | — | already word |
| `esim.` | `\besim\.` | `esimerkiksi` | |
| `mm.` | `\bmm\.` | `muun muassa` | ambiguous with mm (millimetre); only expand when NOT preceded by a number |
| `jne.` | `\bjne\.` | `ja niin edelleen` | |
| `yms.` | `\byms\.` | `ynnä muuta sellaista` | |
| `ym.` | `\bym\.` | `ynnä muuta` | |
| `nk.` | `\bnk\.` | `niin kutsuttu` | |
| `ns.` | `\bns\.` | `niin sanottu` | |

#### Page / chapter

| Abbr | Trigger | Expansion | Notes |
|------|---------|-----------|-------|
| `s.` | `\bs\.\s*(?=\d)` | `sivu ` | digit follows |
| `ss.` | `\bss\.\s*(?=\d)` | `sivut ` | |
| `luku` | — | — | word |
| `lukuun` | — | — | word |

#### Titles

| Abbr | Trigger | Expansion |
|------|---------|-----------|
| `tri` | `\btri\.?\s+(?=[A-ZÄÖÅ])` | `tohtori ` |
| `prof.` | `\bprof\.\s+` | `professori ` |
| `dos.` | `\bdos\.\s+` | `dosentti ` |
| `fil.` | `\bfil\.\s+` | `filosofian ` |
| `maist.` | `\bmaist\.\s+` | `maisteri ` |
| `kand.` | `\bkand\.\s+` | `kandidaatti ` |
| `toim.` | `\btoim\.\s+` | `toimittaja ` |

#### Dates / eras

| Abbr | Trigger | Expansion |
|------|---------|-----------|
| `eKr.` | `\beKr\.` | `ennen Kristusta` |
| `jKr.` | `\bjKr\.` | `jälkeen Kristuksen` |
| `eaa.` | `\beaa\.` | `ennen ajanlaskun alkua` |
| `jaa.` | `\bjaa\.` | `jälkeen ajanlaskun alun` |

#### Organizations

| Abbr | Trigger | Expansion | Notes |
|------|---------|-----------|-------|
| `YK` | `\bYK\b` | `Yhdistyneet kansakunnat` | |
| `EU` | `\bEU\b` | `Euroopan unioni` | |
| `NATO` | `\bNATO\b` | `Nato` | lowercase reading |
| `USA` | `\bUSA\b` | `Yhdysvallat` | |
| `Yhdysvallat` | — | — | word |

#### Distance

| Abbr | Trigger | Expansion | Notes |
|------|---------|-----------|-------|
| `km` | `(\d+)\s*km\b` | `N kilometriä` | partitive — head noun partitive, number nominative |
| `m` | `(\d+)\s*m\b` | `N metriä` | |
| `cm` | `(\d+)\s*cm\b` | `N senttimetriä` | |
| `mm` | `(\d+)\s*mm\b` | `N millimetriä` | NOT `mm.` (abbreviation) — must be preceded by a digit |
| `km/h` | `(\d+)\s*km/h\b` | `N kilometriä tunnissa` | |
| `m/s` | `(\d+)\s*m/s\b` | `N metriä sekunnissa` | |

#### Weight

| Abbr | Trigger | Expansion |
|------|---------|-----------|
| `kg` | `(\d+)\s*kg\b` | `N kiloa` |
| `g` | `(\d+)\s*g\b` | `N grammaa` |
| `mg` | `(\d+)\s*mg\b` | `N milligrammaa` |
| `t` | `(\d+)\s*t\b` | `N tonnia` |

#### Volume

| Abbr | Trigger | Expansion |
|------|---------|-----------|
| `l` | `(\d+)\s*l\b` | `N litraa` |
| `dl` | `(\d+)\s*dl\b` | `N desilitraa` |
| `cl` | `(\d+)\s*cl\b` | `N senttilitraa` |
| `ml` | `(\d+)\s*ml\b` | `N millilitraa` |

#### Temperature

| Abbr | Trigger | Expansion |
|------|---------|-----------|
| `°C` | `(-?\d+)\s*°C` | `N celsiusastetta` |
| `°F` | `(-?\d+)\s*°F` | `N fahrenheitastetta` |

#### Currency

| Abbr | Trigger | Expansion |
|------|---------|-----------|
| `€` | `(\d+)\s*€` | `N euroa` |
| `$` | `\$\s*(\d+)` | `N dollaria` |
| `mk` | `(\d+)\s*mk\b` | `N markkaa` |
| `FIM` | `(\d+)\s*FIM\b` | `N Suomen markkaa` |
| `USD` | `(\d+)\s*USD\b` | `N Yhdysvaltain dollaria` |
| `EUR` | `(\d+)\s*EUR\b` | `N euroa` |

#### Special

| Abbr | Trigger | Expansion | Notes |
|------|---------|-----------|-------|
| `N:o` / `n:o` / `nro` | `\b[Nn]:?o\.?\s*(\d+)` | `numero N` | integrates with num2words |
| `§` | `(\d+)\s*§` | `pykälä N` | |
| `%` | `(\d+)\s*%` | `N prosenttia` | |
| `‰` | `(\d+)\s*‰` | `N promillea` | |

---

## 4. Roman numeral expansion (Pass K)

Roman numerals appear in regnal names, century numbering, chapter headings,
and legal-document article numbers. The failure inventory shows 47 occurrences
in the source corpus.

### Lookup strategy

Two-step:

1. **Detect** the Roman token with a conservative regex: `\b(?=[MDCLXVI]{2,})M*(C[MD]|D?C*)(X[CL]|L?X*)(I[XV]|V?I*)\b`. This requires at least 2 characters, avoiding false positives on standalone `I`, `V`, `L`, `C`, `D`, `M`.
2. **Classify context** to decide cardinal vs ordinal and pick the case:
   - Preceded by a regnal first name → ordinal, nominative: `Kustaa II` → `Kustaa toinen`.
   - Followed by `vuosisata`/`luku`/`luvulla` → ordinal declined by the luku-pass: `XX vuosisata` → `kahdeskymmenes vuosisata`.
   - Preceded by `luku` → ordinal: `luku IV` → `luku neljäs`.
   - Otherwise cardinal nominative.

### Regnal name list (for context detection)

`Kustaa`, `Kaarle`, `Juhana`, `Eerik`, `Henrik`, `Pius`, `Leo`, `Pyhä
Henrik`, `Aleksanteri`, `Nikolai`, `Katariina`, `Elisabet`, `Yrjö`, `Fredrik`,
`Adolf`, `Oskar`, `Erik`, plus ecclesial titles `paavi`, `kuningas`,
`keisari`, `tsaari`, `sulttaani`.

### Single-letter guard

Never expand standalone `I`, `V`, `X`, `L`, `C`, `D`, `M` unless **all** of:

1. Uppercase.
2. Surrounded by whitespace / punctuation.
3. Preceded by a known regnal first name OR followed by a century/chapter head.

### False-positive blacklist

- `DC` — direct current, power electronics. Detect with context "DC power",
  "DC-" prefix.
- `LCD`, `MVP`, `CV`, `CI` — modern acronyms. The "at least 2 characters"
  rule already catches `DC` as a valid Roman (600). Add an override list of
  known modern acronyms to skip.
- English text fragments — detect with a language-id pre-check, OR trust that
  the main text is Finnish and the occasional English stray is acceptable.

### Proposed regex + logic

```
ROMAN = r'\b(?=[MDCLXVI])(M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3}))\b'

def _roman_sub(m):
    roman = m.group(0)
    if roman in MODERN_ACRONYM_BLACKLIST:
        return roman
    if len(roman) == 1:
        # additional context guard
        ...
    n = roman_to_int(roman)
    ctx_before = tokens_before(m.start(), 2)
    ctx_after = tokens_after(m.end(), 2)
    if any(tok in REGNAL_NAMES for tok in ctx_before):
        return num2words(n, lang='fi', to='ordinal')
    if any(tok.startswith('vuosisa') or tok.startswith('luvu') for tok in ctx_after):
        return num2words(n, lang='fi', to='ordinal') + ' ' + ctx_after[0]
    # fallback: cardinal nominative
    return num2words(n, lang='fi')
```

---

## 5. Number case integration

### The wrap

Pass G currently does `num2words(n, lang='fi')` (nominative cardinal). The
new Pass G is:

```
for each bare-integer match:
  context = look_back_3_tokens + look_ahead_3_tokens
  governor = find_governor(context)  # GOVERNOR_TABLE lookup
  case = governor.case if governor else 'nominative'
  to   = governor.form if governor else 'cardinal'
  return num2words(n, lang='fi', case=case, to=to)
```

### Look-back / look-ahead implementation

Regex-only look-behind of variable length is not supported in Python's `re`.
Two options:

#### Option A — Token-based pre-pass (preferred)

1. Tokenize the text into words + their start/end offsets.
2. Walk the token list left-to-right.
3. For each token that is a pure integer, grab `tokens[i-3:i]` and
   `tokens[i+1:i+4]` as context.
4. Look up the context against `GOVERNOR_TABLE` (a dict keyed on lemma).
5. Replace the token with the num2words output.
6. Reassemble the text, preserving whitespace and punctuation.

**Cost:** one tokenization pass over the text. For 370 KB of Finnish prose,
this is ~50 ms in pure Python. Negligible compared to num2words itself.

**Complexity:** moderate — need a robust tokenizer that preserves offsets.
We already have `split_text_into_chunks` logic that walks char-by-char; this
can share code.

#### Option B — Regex with explicit governor patterns

For each known governor, write a regex that matches `governor + digits` or
`digits + governor` and substitute with the cased form. Everything unmatched
falls through to the current Pass G.

**Cost:** cheaper per-pass but O(N_governors * text_size) total. With ~50
governors this is comparable.

**Complexity:** lower — each governor is its own regex rule. Easier to
test, easier to debug. Downside: governors that appear *between* the bare
integer and its head noun (e.g. intervening adjectives) get missed.

### Decision

**Use Option A (token-based) for Pass G.** The extra complexity is worth it
because:

1. Handles variable distance between governor and number.
2. Makes the ±3 window explicit and tunable.
3. Shares tokenization infra with the abbreviation pass (Pass J) and the
   Roman numeral pass (Pass K), which also need context.

We introduce a small internal `_tokenize_with_offsets(text) -> list[Token]`
utility. Token = `(text, start, end, kind)` where kind ∈ `{word, digit,
punct, space}`.

---

## 6. Integration order (Pass A–Z)

Ordering is load-bearing. Each pass consumes certain patterns, leaving the
rest for later passes. The new order is:

| Pass | Name | One-line reason |
|------|------|-----------------|
| A | Strip OCR junk (`■`, `©`, stray control chars) | Must run first — downstream regexes assume clean input. |
| B | Normalize quotes and dashes (curly → straight, en/em → `-`) | Range detection needs consistent hyphens. |
| C | Collapse TOC dot-leaders (`.............` → space) | Prevents millions of "dot dot dot" from ellipsis pass. |
| D | Drop bibliographic citations `(Author Year)` | Remove noise before anything else touches parens. |
| E | Elided-hyphen compounds `keski-ja` → `keski- ja` | Must precede range/century because the hyphen is ambiguous. |
| F | Century expressions `NNNN-luvulla` | Must run before numeric ranges so `1500-luvulla` isn't seen as `1500-18` + `00-luvulla`. |
| G | ISBN detection (e.g. `978-XXX-XX-XXXX-X`) | Strip or spell one-by-one so Pass H doesn't see it as a range. |
| H | Numeric ranges `NNNN-NNNN` | Before bare-year. |
| I | Date patterns `D.M.YYYY`, `D.M.` | Must run before decimal pass or they'll be parsed as decimals. |
| J | Time patterns `HH:MM`, `HH.MM klo` | Same reason. |
| K | Abbreviation expansion (`s.`, `klo`, `eKr.`, units, currency) | Produces bare integers that Pass N will then case-resolve. |
| L | Roman numerals | Before ordinal/cardinal number expansion so regnal `II` becomes "toinen", not literal. |
| M | Decimals `N,N` / `N.N` | Must run before bare-int pass. |
| N | Bare integers with **governor-word case detection** | The core new pass. Requires tokenization. |
| O | Loanword lexicon respelling (`-ismi`, `-tio`, Latin, German, place names) | After number expansion so respellings can't collide with digits. |
| P | Long-compound seam splitting (optional, future) | After everything else — purely cosmetic word-break insertion. |
| Q | Glued compound-number morpheme split (current Pass H) | After num2words, after respelling. |
| R | Acronym letter-by-letter expansion (legal-code acronyms, etc.) | Late — must not consume known words. |
| S | Final whitespace/punctuation cleanup | Last. Collapse multi-space, fix space-before-punct. |

The old Pass A–H map into the new pipeline as follows:

- old A (citations) → new D
- old B (elided hyphen) → new E
- old C (century) → new F
- old D (ranges) → new H
- old E (page abbr) → new K (as part of the abbreviation table)
- old F (decimals) → new M
- old G (bare int) → new N (now case-aware)
- old H (morpheme split) → new Q

---

## 7. Test harness

### Structure

Three test files:

1. `tests/test_normalizer_unit.py` — per-pass unit tests, one fixture per
   category. Fast, run on every save.
2. `tests/test_normalizer_golden.py` — 50+ golden sentences from real Finnish
   prose with exact expected output. Run on every commit.
3. `tests/test_normalizer_regression.py` — the exact failure sentences from
   `docs/finnish_tts_failure_inventory.md`. Run on every commit.

### Golden corpus (≥50 sentences)

Mix sources:

- 20 synthetic sentences written to mirror the failure classes from the source
  corpus, covering every category (years, centuries, ranges, -ismi, -tio,
  Latin, Roman, abbreviations, page refs, parenthetical citations). Fixtures
  use synthetic / public-domain text only — never verbatim source.
- 10 sentences synthesized to hit edge cases (partitive head nouns, elided
  hyphens, decimals, ISBN, dates, time, currency, temperature, percent).
- 10 sentences with multiple overlapping patterns (e.g. `vuoteen 1905 s. 42
  luku IV humanismista`).
- 10 sentences that should pass through **unchanged** (plain modern Finnish
  prose with no numbers or loanwords) to catch regressions.

Each entry is a `(input, expected)` tuple in a YAML file
`tests/data/golden_corpus.yaml`. The test loads the file and asserts per
entry. Failing assertions print a unified diff.

### Per-category unit tests

- Years: `vuonna 1905`, `vuodesta 1900 alkaen`, `1900–1950`.
- Decimals: `3,14`, `10.5`, `1.1.1900` (date, not decimal).
- Centuries: `1500-luvulla`, `XV vuosisadalla`, `1900-luvun puolivälissä`.
- Abbreviations: every row in the abbreviation table.
- Loanwords: every entry in the lexicon (20–30 minimum; target: all 45 `-ismi`
  surface forms).
- Romans: `Kustaa II`, `XX vuosisata`, `luku IV`, `DC` (must NOT expand).

### Regression tests

One `@pytest.mark.parametrize` case per failure line from the inventory.
Total ≈ 100 cases. Run on every commit.

### Automation

- `pytest tests/test_normalizer_*.py` runs all three in <2 seconds (no audio).
- CI hook: block merging if any regression or golden fails.
- Manual CLI tool: `python -m src.tools.normalize_fi < input.txt` for ad-hoc
  testing on new corpora. Prints the normalized text to stdout.

---

## 8. Libvoikko integration (future)

### When libvoikko becomes necessary

num2words alone covers:

- Producing any of the 15 cases for any integer.
- Cardinal / ordinal forms.

num2words **cannot**:

1. **Choose** the right case from context. That's the governor-table job in
   Pass N above.
2. Analyze arbitrary Finnish words to find their stem and case. Needed for:
   - Detecting the governor when the governor itself is in a case form we
     didn't predict (e.g. `vuotena` instead of `vuonna`).
   - Stemming `-ismi` and `-tio` loanwords so the lexicon can store one entry
     per stem instead of one per surface form.
3. Lemmatize unknown loanwords. If we encounter a `-ismi` word not in the
   lexicon, libvoikko can confirm it matches the `-ismi` morphology pattern
   and we can apply a generic fallback.

### Minimum viable "voikko mode"

Ship when libvoikko is installed and skip otherwise. Integration points:

1. **Governor detection:** before lookup in `GOVERNOR_TABLE`, call
   `voikko.analyze(token)` to get the base form. Match on base form. Removes
   the need to enumerate `vuonna/vuoteen/vuodesta/vuoden/vuosina` as separate
   keys.
2. **Loanword stemming:** for any word matching `\w+(?:ismi|tio)\w*`, stem
   via voikko, look up the stem in the lexicon, and apply the respelling
   mapped through the detected suffix.
3. **Unknown-word fallback:** if a word looks like a loanword morphologically
   and isn't in the lexicon, apply a generic insertion of soft syllable breaks.

### Implementation shape

- New module `src/fi_morph.py` wrapping libvoikko with lazy import and a
  no-op fallback.
- `voikko_analyze(word) -> dict | None` — returns base form + case, or None
  if voikko is unavailable.
- Normalizer passes call `voikko_analyze` first, fall back to string match.

### Packaging note

libvoikko is a C library. It adds a PyInstaller bundling burden. Keep it
optional (`extras_require={'morph': ['libvoikko']}`) and document that the
fallback is still good enough for 90% of cases. Do NOT make it a hard
dependency for the Windows installer.

---

## 9. Honest limits

What will STILL be broken after all of the above is in place. Stop when
these are reached — going further requires full SSML phoneme tags or a
custom acoustic model.

### 1. Engine-level phonotactic failures

Chatterbox-TTS and Piper will still mispronounce some Finnish phoneme
sequences no matter what respelling we give them. Examples: word-final
consonant clusters (`lskt`), geminate vowels followed by a liquid
(`piilla`-like sequences), some loan diphthongs. Only switching to a
better acoustic model (Edge-TTS Noora, Qwen-TTS Fi) actually fixes these.

### 2. Proper name pronunciation for unknown names

Any foreign place name or person name not in the lexicon will be read with
Finnish phonotactics. We can grow the lexicon forever, but a new name in
every new document is out of reach without a name-detection model.

### 3. Code-switched sentences

A Finnish sentence containing a full English quote
(`hän siteerasi englanniksi "the point speaks for itself"`) gets read entirely with
Finnish phonotactics and sounds broken. Fixing this requires per-span
language detection plus multi-voice synthesis — far beyond the normalizer.

### 4. Prosody and emphasis

The normalizer is a text transformer. It cannot control where the TTS puts
stress, pitch, or emphasis. For legal/academic prose with nested clauses,
the TTS will sometimes read with wrong emphasis. SSML `<emphasis>` would
help but Chatterbox and Piper don't support it.

### 5. Homograph disambiguation

Finnish has few true homographs but plenty of context-sensitive readings
(`voi` = "butter" vs "can"; `tuli` = "came" vs "fire"). Number words have
the same issue: `yksi` = "one" vs "a certain". Only a full language model
pass can disambiguate; rule-based normalization cannot.

### 6. Sarcasm, irony, intonation

Unfixable. Not the normalizer's job.

---

## Appendix A — glossary

- **Governor**: the word that syntactically demands a specific case on its
  argument. In Finnish, most prepositions, postpositions, and many verbs
  govern a case.
- **Respelling**: rewriting a word in a different (usually Finnish)
  orthography so the TTS engine produces a closer phonetic output.
- **Pass**: a single left-to-right transformation of the text, named A–S in
  the current design.
- **Lexicon**: the YAML files `data/fi_loanwords.yaml` and
  `data/fi_abbreviations.yaml`.

## Appendix B — implementation phasing

Recommended order for the implementation sessions, each shippable on its own:

1. **Phase 1** — refactor current normalizer into the token-based pipeline
   (new passes map to old ones). No behaviour change. Full test coverage
   pinned as regression.
2. **Phase 2** — abbreviation expansion (Pass K). Low risk, high visibility.
3. **Phase 3** — loanword lexicon (Pass O) with `-ismi` + Latin entries.
   Covers highest-severity acoustic failures.
4. **Phase 4** — Roman numeral pass (Pass L).
5. **Phase 5** — governor-word case detection for Pass N. Biggest quality
   jump, biggest implementation risk. Requires the native-speaker case
   verification from §1.
6. **Phase 6** — (optional) libvoikko mode.
