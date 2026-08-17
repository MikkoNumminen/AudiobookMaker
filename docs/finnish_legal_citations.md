# Reading Finnish legal citations aloud

Finnish legal writing is full of shorthand that looks fine on paper and turns
to mush in a text-to-speech engine. `MK 2:1` is a perfectly clear reference to
a chapter and a section if you are a lawyer. To a synthesis model it is two
numbers with a colon between them, and the colon is not a sound.

This page explains what AudiobookMaker does about that, and what it
deliberately does not do.

The code lives in `src/tts_normalizer_fi_legal.py`. It runs as Pass Z, near the
start of the Finnish normalizer, so everything it produces is plain Finnish
words plus bare digits that the later number passes can read.

## What gets converted

| Written | Spoken as | Why |
| --- | --- | --- |
| `10 §` | `pykälä 10` | The section sign has no pronunciation |
| `4 §:n 2 momentti` | `pykälä 4 momentti 2` | Colon inside a word is unreadable |
| `32.1 §` | `pykälä 32 momentti 1` | Compact form of "section 32, moment 1" |
| `MK 2:1` | `maakaaren luku 2 pykälä 1` | Chapter and section shorthand |
| `MK 13:4.1` | `maakaaren luku 13 pykälä 4 momentti 1` | Same, with a moment |
| `MK 2 luvun 1 §` | `maakaaren luku 2 pykälä 1` | Chapter word spelled out |
| `8 art.` | `artikla 8` | Article reference |
| `417/2007` | `417 kautta 2007` | A slash is read as "kautta" |
| `18.10.2024/552` | `18.10.2024 kautta 552` | Amendment form, date first |
| `KKO 2010:23` | `KKO 2010 numero 23` | Court decision citation |
| `s. 56-77` | `sivut 56-77` | Page range, plural |
| `OikTL 36 §` | `oikeustoimilain pykälä 36` | Law abbreviation expanded |

Law abbreviations are expanded from a table at the top of the module. It covers
contract, property, tenancy, company, insolvency, family, inheritance,
criminal, and employment law, plus the constitutional and administrative
entries that were there first. Add to that table when a source document turns
up something it does not know.

## Two design decisions worth knowing about

### The noun comes before the number

We say `pykälä 10`, not `10. pykälä`. We say `luku 2`, not `2 luvun`. A Finnish
lawyer writing by hand would produce "maakaaren 2 luvun 1 pykälä", so our
version reads a little stiffly.

There are two reasons for it. The first is that "2 luvun" needs the number in
the genitive, "kahden luvun", and num2words cannot be relied on to agree case
across a phrase like that. The second is that the governor table in
`data/fi_governors.yaml` decides a number's case by looking at the word to its
**left**. Put the noun first and the machinery that already exists does the
right thing for free. Put the number first and there is nothing to read the
case off.

### A court abbreviation is left exactly as written

`KKO 2010:23` becomes `KKO 2010 numero 23`. The letters stay. That is on
purpose: a Finnish reader says "koo-koo-oo" out loud, and the acronym pass
further down the pipeline produces exactly that from the letters. Expanding it
to "korkeimman oikeuden ratkaisu" would be rewriting the citation, not reading
it.

Only the colon needs help. Left alone it reached a later pass that turns a
colon between two letters into a hyphen, and the citation came out as
"kaksituhatta kymmenen-kaksikymmentä kolme".

## What this pass will not do

It only ever rewrites things whose meaning is fixed by their shape. Anything
that needs a judgement call about meaning is left for a human, because a regex
that guesses wrong deletes content silently.

So it does not:

- **Delete editorial metadata.** Lines like "author updated the text on
  <date>" are junk in one document and content in another. There is no shape
  that distinguishes them.
- **Delete amendment and repeal notes.** "Section 3 amended by act
  18.10.2024/552" is throwaway metadata when a publisher stamped it onto the
  page, and it is the actual subject of the sentence when the author is
  discussing how the law changed. Same words, opposite verdicts. The pass
  makes such a line *readable* and leaves the decision to delete it alone.
- **Delete internal navigation.** "see section 4 above" is sometimes scaffolding
  and sometimes load-bearing grammar.

If a source document needs that kind of cleanup, do it to the text before
handing it to AudiobookMaker.

## Known limitations

- **Grammatical case on a citation is dropped, not moved.** `2 luvussa 3 §`
  means "in chapter 2, section 3" and is read as "luku 2 pykälä 3". Same for
  `MK 2:1:ssä` and `4 §:ssä`. Once the reference has been reordered noun-first
  there is nowhere to put the ending, so it is consumed. The reference
  survives, the "in" does not. What matters is that it is consumed rather than
  left to land on the number: `pykälä yhdessä` would be heard as "section
  together", which is a different word rather than a missing one.
- **A postposition after a citation does not inflect the noun.** Finnish
  "nojalla", "mukaan" and "perusteella" all govern the genitive, so
  `OikTL 36 §:n nojalla` should read "oikeustoimilain pykälän 36 nojalla" and
  instead reads "pykälä 36 nojalla". The citation is still correct and
  understandable; the grammar around it is not. Teaching the pass which words
  govern which case is a much larger job than reading the citation itself.
- **Chapter shorthand needs a known abbreviation in front.** A bare `2:1` is
  left alone and read as a ratio, "kaksi yhteen", because a bare `2:1` really
  is much more often a ratio or a score than a statute reference. If the law
  name is spelled out in prose rather than abbreviated, the shorthand after it
  will not be recognised.
- **Three abbreviations opt out of the chapter form.** `KKO` and `KHO` are
  courts, whose own citations are the same shape, and `UK` is far more often
  United Kingdom than ulosottokaari. All three still work in every other form,
  so `UK 4 §` is the ulosottokaari as normal.
- **A clock time after a law abbreviation would be misread.** `AL 12:30` comes
  out as "avioliittolain luku 12 pykälä 30". Nobody writes a time directly
  after a law abbreviation, so this is accepted rather than guarded.
- **Only one slash per run of numbers.** `18.10.2024/552` is handled;
  `2024/12/25` keeps its second slash. A general digit-slash-digit rule would
  swallow fractions such as "1/2 annoksesta", which is a worse trade.
- **`vp` is not expanded.** In `HE 120/1994 vp` it stays as two letters and is
  read "vee pee", which is what a Finnish reader says anyway. Expanding it
  would mean adding a two-letter key to a case-insensitive abbreviation table
  that matches on prefixes, and that is a bad trade for no gain.

## Checking your own text

Run a sample through the normalizer and read the output before you commit to a
long conversion:

```python
from src.tts_normalizer import normalize_text
print(normalize_text("Kauppakirjasta säädetään MK 2 luvun 1 §:ssä.", "fi"))
```

Three things in the output mean something got missed: a surviving `§`, a
surviving `:` between digits, and a period with letters on both sides such as
`32.pykälä`. The test file `tests/test_tts_normalizer_fi_legal.py` asserts all
three are absent for a set of representative sentences.

Remember that clean normalizer output is not proof of a good audiobook. Per
`docs/tts_symbol_handling.md`, the only check that catches a dropped clause is
transcribing the finished audio and reading it against the source.

## See also

- `src/tts_normalizer_fi_legal.py` for the pass itself and its ordering notes
- `docs/tts_symbol_handling.md` for the symbol gate that catches stray glyphs
- `data/fi_governors.yaml` for the case-agreement table
