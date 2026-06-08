# Finnish governor cases for TTS number normalization

Research note for the `num2words`-based Finnish text normalizer. Every entry
cites the authoritative source. Rule-of-thumb: **a cardinal numeral agrees in
case with its head noun in oblique cases** (VISK §772). The digit form is
usually written bare because the governor noun already carries the case
(Kielitoimiston ohjepankki, Kielikello), but when read aloud the number
itself must inflect.

## num2words API

`num2words.lang_FI`: `to_cardinal(value, case='nominative', plural=False, prefer=None)`
and `to_ordinal(...)`. Parameter name is **`case=`** (not `to=`). Allowed
values: `nominative, genitive, accusative, partitive, inessive, elative,
illative, adessive, ablative, allative, essive, translative, instructive,
abessive, comitative`. Source: `num2words/lang_FI.py` `NAME_TO_CASE`.

## Q1 — vuonna 1500

VISK §772: numeral congruence applies. Correct reading aloud is **inessive**
(`vuonna tuhannessaviidessäsadassa`). The "nominative sounds right on the
radio" intuition is the common colloquial shortening where only the last
component inflects, which Kielikello explicitly permits for 3+ digit numbers
("vain viimeinen osa taipuu"). For TTS, emit inessive to be safe.

Sister constructions: `vuoteen 1900` → **illative**;
`vuodesta 1917` → **elative**; `vuosina 1914–1918` → **essive (plural=True)**
on both endpoints. Refs: VISK §772,
https://kielikello.fi/lukusanojen-taivuttaminen/

## Q2 — klo 14

`kello` itself is nominative (it's a frozen adverbial), so the hour is
**nominative**: `kello neljätoista`. But:
- `klo 14 alkaen` → elative: `kello neljästätoista alkaen`
  (Kielikello example "kello 8.30:stä alkaen").
- `klo 14–16` range with `alkaen/asti/välillä` → elative…illative.
  Bare range: both nominative.
- `klo 14.30`: Kielitoimisto allows inflection or no inflection when minutes
  are present. Safest: nominative `neljätoista kolmekymmentä`.

Ref: https://jkorpela.fi/kielenopas/5.2.html, Kielikello perusluvut.

## Q3 — viisi kertaa

**Nominative on the numeral, partitive on `kertaa`** — this is the standard
"numeraali + partitiivi" rule (VISK §772). Generalises to `kolme kertaa,
sata kertaa, tuhat kertaa`. `monta` already is a partitive-like form and
governs partitive (`monta kertaa`). `muutama` behaves like an adjective:
`muutaman kerran` (genitive-adverbial) or `muutamia kertoja` (pl.part.) —
both are current. Ref:
https://kielitoimistonohjepankki.fi/ohje/ajan-ja-maaran-ilmauksia-…

## Q4 — sivulta 42

Numeral agrees: **ablative** `sivulta neljältäkymmeneltäkahdelta`.
- `sivulla 42` → adessive `neljässäkymmenessäkahdessa`
- `sivulle 42` → allative `neljällekymmenellekahdelle`
- `sivut 42–45` → nominative plural on the number (here the noun is nom.pl.)
- `sivuilla 42–45` → adessive on both endpoints
- `s. 42` → expand to `sivulla neljäkymmentäkaksi` unless preceding
  preposition dictates otherwise.

## Extra governors worth handling

| Trigger | Case to pass to num2words | Notes |
|---|---|---|
| `aikoihin 1800` | illative (plural) | "tienoilla" synonym |
| `tienoilla 1800` | adessive (plural) | |
| `luokkaa 50` | partitive (`luokkaa` governs part.) | idiom, keep 50 nom. |
| `noin 50` | nominative | `noin` is adverbial, no case change |
| `yli 50` / `alle 50` | nominative or part. | both attested |
| `kello 8:sta 16:een` | elative → illative | explicit endings |
| `eaa.` / `jaa.` | nominative year | `vuonna 500 eaa.` → inessive |
| `pykälä 5` | nominative | `pykälässä 5` → inessive |
| `luku 3` (book chapter) | nominative | `luvussa 3` → inessive |
| `rivillä 12` | adessive | same rule as sivulla |
| `kohdassa 4` | inessive | |

## Unverified

- Whether VISK explicitly blesses the "last-part-only" shortening for years
  in broadcast style — Kielikello mentions it generally but not year-specific.
- `klo 14.30` inflected form in formal speech: sources disagree.
- Double partitive `montaa kertaa` is colloquially widespread but Kielikello
  ("Montaa-partitiivi") treats it as nonstandard.

### Sources
- VISK §772 https://scripta.kotus.fi/visk/sisallys.php?p=772
- Kielikello "Lukusanojen taivuttaminen" https://kielikello.fi/lukusanojen-taivuttaminen/
- Kielikello "Perusluvut ja sijapäätteiden merkitseminen" https://kielikello.fi/perusluvut-ja-sijapaatteiden-merkitseminen/
- Kielitoimiston ohjepankki "Peruslukujen taivuttaminen" https://kielitoimistonohjepankki.fi/ohje/luvut-ja-numerot-peruslukujen-taivuttaminen/
- Kielitoimiston ohjepankki "Ajan ja määrän ilmauksia" https://kielitoimistonohjepankki.fi/ohje/ajan-ja-maaran-ilmauksia-toisen-kerran-vai-toista-kertaa-viikko-vai-viikon-kerrallaan/
- Jukka Korpela, Nykyajan kielenopas §5.2 https://jkorpela.fi/kielenopas/5.2.html
- num2words source https://github.com/savoirfairelinux/num2words/blob/master/num2words/lang_FI.py
