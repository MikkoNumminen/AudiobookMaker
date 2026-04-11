# TODO

Shared task list across all Claude Code sessions. Remove completed tasks immediately — no "Recently Completed" section.
Every item must have a size estimate: 🟢 small, 🟡 medium, 🔴 large. LLM marker: ⚡ Sonnet, 🧠 Opus.
In Progress items must show the owner: `[Claude 1, main]`, `[Claude 2, worktree-name]`, etc.
4 permanent Claude instances: **Claude 1, Claude 2, Claude 3, Claude 4.**

**🚨 MANDATORY RULES — NO EXCEPTIONS:**

1. **Re-read this file BEFORE starting ANY work** — every single session, every single task.
2. **NEVER start a task without FIRST moving it to "In Progress" with your name tag.** If you skip this, you are causing collisions.
3. **If you pause or stop mid-task, your entry MUST stay in "In Progress" until the work is committed and pushed.** Do not remove it just because you stopped — other Claudes need to see it.
4. **If a task already has an owner tag, do NOT touch it.** Pick something else or wait.
5. **Violating these rules breaks the shared workflow for all instances.**

## In Progress

## Backlog

### VoxCPM2 — GPU-testaus (vaatii NVIDIA-koneen)
- [ ] Aja `pip install voxcpm` GPU-koneella ja varmista että GUI näkee moottorin saatavilla olevana 🟢 ⚡ Sonnet
- [ ] Aja Turo-kirjan ensimmäiset ~5000 merkkiä VoxCPM2:lla suomeksi, vertaa v3-versioon (Noora Edge-TTS) 🟡 🧠 Opus
- [ ] Kokeile `voice_description="warm baritone elderly male"` englanninkielisellä näytetekstillä ja arvioi kuulostaako oikealta 🟡 🧠 Opus
- [ ] Kokeile `reference_audio` -kloonausta 10 s näytteellä (esim. Morgan Freeman-tyylinen klippi) ja arvioi 🟡 🧠 Opus
- [ ] Jos VoxCPM2:n suomi ei ole selkeästi parempi kuin Noora: päätä jääkö engine koodiin vai poistetaanko 🟢 🧠 Opus

### Qwen3-TTS (kokeellinen, todennäköisesti droppaus)
- [ ] Tutki uudestaan WebFetchillä onko QwenLM julkaissut virallisen PyPI-paketin `qwen3-tts` vuonna 2026 (aiempi tutkimus: ei ollut — vendoroitu HF Spaceen) 🟢 ⚡ Sonnet
- [ ] Jos PyPI-paketti on olemassa: tarkista tukeeko se suomea (aiempi kuvaus mainitsi vain "10 major languages") 🟢 ⚡ Sonnet
- [ ] Jos suomi + PyPI + CPU-fallback kaikki täyttyvät: luo `src/tts_qwen.py` adapter samalla mallilla kuin `tts_voxcpm.py` (kloonaus + voice description laiskana tuontina) 🟡 ⚡ Sonnet
- [ ] Jos yksikin edellä olevista ei täyty: lisää Qwen3 READMEen "Not supported — use VoxCPM2 instead" -huomautuksella ja sulje tämä tehtävärivistö 🟢 ⚡ Sonnet
- [ ] GPU-testaus Qwen3:lle jos adapter saadaan tehtyä 🟡 🧠 Opus

### Vaatii Windows-koneen
- [ ] Lisää sovellukselle ikoni (assets/icon.ico) 🟢 ⚡ Sonnet
- [ ] Testaa .exe eri PDF-tiedostoilla 🟡 ⚡ Sonnet
- [ ] Testaa installeri puhtaalla Windows-ympäristöllä 🟡 ⚡ Sonnet
