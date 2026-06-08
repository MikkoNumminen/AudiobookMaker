# Finnish TTS Landscape for Audiobook Production — April 2026

Research snapshot for deciding what should power AudiobookMaker's Finnish narration in the next 6–12 months. Constraint: user will not pay for any hosted service, so commercial APIs appear only as quality reference points.

## Current baseline (what we ship today)

- **Chatterbox-TTS** (ResembleAI) multilingual + **Finnish-NLP/Chatterbox-Finnish** T3 finetune
- torch 2.6.0 cu124, chatterbox-tts 0.1.7, silero-vad, num2words — ~5 GB deps, ~7 GB weights
- CUDA 12.4+ required for realistic throughput; Mac CPU runs at ~6× slower than realtime
- Quality: user-rated "v7 sounds excellent" but weak on `-ismi` / `-tio` loanword classes; gemination hurt by upstream token_repetition bug we patched

---

## 1. Candidates researched

### 1a. Chatterbox Multilingual (ResembleAI, Sept 2025) — **officially lists Finnish**

- URL: https://github.com/resemble-ai/chatterbox • https://www.resemble.ai/introducing-chatterbox-multilingual-open-source-tts-for-23-languages/
- License: **MIT** (code and weights)
- Finnish: one of 23 official languages (`fi`). No public Finnish-specific benchmark, no samples in the launch post.
- Hardware: same GPU envelope as current Chatterbox; ~350M params for Turbo, bigger for multilingual.
- Quality guess: **8/10** — claimed 63.75% preference vs ElevenLabs in internal blind tests (English). Unknown for Finnish but architecturally identical to the path we're already running; it is the *upstream* of our current fork.
- Voice cloning: yes, zero-shot. Active dev (11k+ stars, 1M HF downloads).
- **Important**: this is the upstream of Finnish-NLP/Chatterbox-Finnish, which means we could potentially drop the custom T3 finetune and just track upstream if upstream Finnish is already "good enough". Needs an A/B listening test.

### 1b. F5-TTS + AsmoKoskinen Finnish finetune — **the strongest non-Chatterbox option**

- URL: https://github.com/SWivid/F5-TTS • https://huggingface.co/AsmoKoskinen/F5-TTS_Finnish_Model
- License: F5-TTS code MIT/CC-BY; Finnish weights **CC-BY-NC-4.0** (non-commercial only — fine for personal audiobooks, blocks any paid product).
- Finnish: **dedicated finetune**, three rounds on Common Voice + VoxPopuli + LibriVox Finnish. Latest checkpoint 2025-03-23, recommended v1. Listed in F5-TTS official `SHARED.md`.
- Hardware: GPU strongly recommended (~6 GB VRAM); CPU inference possible but slow. ~336M params.
- Deps: flow-matching stack, torch + torchaudio + vocos. Comparable weight to ours.
- Quality: F5-TTS base scores **MOS ~4.1** on English; Finnish finetune not independently benchmarked but community samples are coherent. Honest guess: **7/10** for Finnish — likely better than our Chatterbox on loanwords (larger Finnish-specific data) but possibly less expressive prosody.
- Voice cloning: **yes**, 5–15 s reference, this is F5's headline feature.
- Known limitation: "numbers cannot be understood — convert to words" (matches our existing num2words pipeline).
- Active development, SHARED.md registry, hungarian & other community finetunes prove the finetune workflow is reproducible.

### 1c. Piper TTS + Finnish Harri (and AsmoKoskinen Piper Finnish) — **the CPU fallback**

- URL: https://github.com/rhasspy/piper • https://huggingface.co/rhasspy/piper-voices • https://huggingface.co/AsmoKoskinen/Piper_Finnish_Model
- License: **MIT** (Piper). Harri voice CC-BY. AsmoKoskinen's Piper-Finnish is CC-BY-NC (trained via F5-TTS Finnish voice cloning).
- Finnish: Harri (low + medium) officially in Piper VOICES.md. AsmoKoskinen also published a Piper Finnish trained from scratch.
- Hardware: **CPU-only, real-time**. <100 MB weights. No torch required (onnxruntime).
- Quality: **5–6/10**. Clearly robotic vs modern neural models; intelligible, consistent, no hallucinations. We already ship it as the CPU fallback.
- Voice cloning: **no** (each voice is a separate trained checkpoint).
- Verdict: keep exactly as-is.

### 1d. XTTS v2 (Coqui, dead upstream, IDIAP fork) — **not recommended for Finnish**

- URL: https://github.com/idiap/coqui-ai-TTS (maintained fork) • https://huggingface.co/coqui/XTTS-v2
- License: **CPML (non-commercial only)**. Coqui shut down Dec 2023; no commercial license path.
- Finnish: **not in the 17 officially supported languages**. Community finetuning for new languages is documented but no public Finnish checkpoint of quality was found.
- Verdict: skip — worse Finnish support than F5 or Chatterbox and a dead-ended license.

### 1e. Kokoro-82M, MeloTTS, Fish-Speech, Orpheus-TTS, Sesame CSM-1B — **all ruled out for Finnish**

| Model | Finnish? | License | Note |
|---|---|---|---|
| Kokoro-82M | No (en/es/fr/hi/it/pt/ja/zh) | Apache 2.0 | Would be perfect for CPU fallback if Finnish ever added |
| MeloTTS | No (en/es/fr/zh/ja/ko) | MIT | Fast CPU but no `fi` |
| Fish-Speech / OpenAudio S1 | No (en/ja/ko/zh/fr/de/ar/es) | CC-BY-NC-SA weights | Non-commercial and no `fi` |
| Orpheus-TTS (Canopy) | No (en/es/fr/de/it/pt/zh, research preview) | Apache 2.0 | Promising but no Finnish plan announced |
| Sesame CSM-1B | **English only** (20+ langs "coming") | Apache 2.0 | Watch for updates |
| StyleTTS 2 | Requires custom PL-BERT + from-scratch Finnish training | MIT | Real engineering project, no community Finnish checkpoint exists |
| MARS5/6, Dia, Spark-TTS | No Finnish | varied | Skip |
| eSpeak-NG / Festival | Yes, rule-based | GPL/BSD | Quality floor ~2/10 |

### 1f. Commercial reference points (flagged, not recommended)

Azure Neural "Noora"/"Harri" is the gold-standard Finnish baseline (~9/10). Google WaveNet Finnish, ElevenLabs Finnish, Amazon Polly Suvi all deliver 8–9/10 but require paid accounts. **Off-limits per user constraint.**

---

## 2. Top-5 assessments

**1) Stay on Chatterbox, but retest against upstream Chatterbox-Multilingual.** Our finetune predates ResembleAI's own `fi` release (Sept 2025). We may be carrying a custom T3 patch that upstream already subsumes. Switching cost: one A/B listening test, maybe zero code changes (the chatterbox-tts package already handles both). Realistic quality delta: 0–15% either direction; could remove ~3 GB of redundant weights and the forward-hook patch burden. Killer feature: MIT license, no fork maintenance.

**2) F5-TTS + AsmoKoskinen Finnish as secondary engine.** The most credible *different* architecture with real Finnish data. Switching cost: medium — new deps (vocos, new tokenizer, different inference loop), ~1–2 days engineering, installer grows by ~2 GB. Realistic quality: likely +5–20% on loanwords and rare phonemes due to the bigger Finnish training set, but flow-matching can hallucinate on long chunks. Dealbreaker: **CC-BY-NC weights** — fine for personal use, blocks any future paid product.

**3) Piper Finnish as permanent CPU fallback.** Already in place. No change needed. Killer feature: runs on any laptop, 100 MB, zero GPU. Quality ceiling is low (~6/10), so it's a fallback not an upgrade.

**4) Train our own F5-TTS or Chatterbox finetune on a curated Finnish audiobook corpus.** Cost: ~$200–500 GPU rental (A100×48h) + 20–50 h audio cleanup work. Realistic upside: the biggest single quality jump available, since our pain points (`-ismi`/`-tio`, gemination) are data-distribution problems. Dealbreaker: time investment, not money.

**5) Monitor Sesame CSM and Orpheus multilingual.** Both are 2025 Apache-2.0 releases with stated intent to cover 20+ languages. Neither supports Finnish yet. Check quarterly.

---

## 3. Ranked recommendation

1. **Primary engine in 6 months: Chatterbox-Multilingual upstream** (verify vs our custom finetune first; if upstream ties or wins, drop the fork). Fall back to **F5-TTS Finnish** as the architectural hedge for personal-use builds where CC-BY-NC is acceptable.
2. **CPU-only fallback: Piper Harri / AsmoKoskinen Piper Finnish.** Already shipping.
3. **Monitor for 2026:** Sesame CSM multilingual rollout, Orpheus Finnish, Kokoro language expansion, any new Chatterbox Turbo multilingual release.
4. **Do NOT use:** XTTS v2 (dead upstream, no Finnish), MeloTTS/Kokoro/Fish-Speech/Orpheus/CSM for Finnish *today*, any commercial API (user constraint), eSpeak/Festival (quality floor).

---

## Sources

- https://github.com/resemble-ai/chatterbox
- https://www.resemble.ai/introducing-chatterbox-multilingual-open-source-tts-for-23-languages/
- https://huggingface.co/ResembleAI/chatterbox
- https://huggingface.co/AsmoKoskinen/F5-TTS_Finnish_Model
- https://github.com/SWivid/F5-TTS/blob/main/src/f5_tts/infer/SHARED.md
- https://huggingface.co/AsmoKoskinen/Piper_Finnish_Model
- https://github.com/rhasspy/piper/blob/master/VOICES.md
- https://github.com/rhasspy/piper/discussions/735
- https://huggingface.co/coqui/XTTS-v2
- https://github.com/idiap/coqui-ai-TTS
- https://huggingface.co/hexgrad/Kokoro-82M
- https://github.com/myshell-ai/MeloTTS
- https://github.com/fishaudio/fish-speech
- https://github.com/canopyai/Orpheus-TTS
- https://github.com/SesameAILabs/csm
- https://github.com/yl4579/StyleTTS2
- https://www.digitalocean.com/community/tutorials/best-text-to-speech-models
