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


### Vaihe 3: TTS-moottori
- [ ] Toteuta edge-tts-integraatio 🟡 ⚡ Sonnet [Claude 1, main]
- [ ] Toteuta tekstin pilkkominen TTS:lle sopiviin osiin (max ~3000 merkkiä per pyyntö) 🟡 ⚡ Sonnet [Claude 1, main]
- [ ] Toteuta äänitiedostojen yhdistäminen pydubilla 🟡 ⚡ Sonnet [Claude 1, main]
- [ ] Toteuta progress-callback (edistymisen seuranta) 🟢 ⚡ Sonnet [Claude 1, main]
- [ ] Lisää kielivaihtoehdot (suomi, englanti) 🟢 ⚡ Sonnet [Claude 1, main]
- [ ] Kirjoita testit TTS-putkelle 🟡 ⚡ Sonnet [Claude 1, main]

### Vaihe 4: GUI
- [ ] Toteuta pääikkuna (tiedoston valinta, kielen valinta, tallennus) 🟡 ⚡ Sonnet [Claude 1, main]
- [ ] Toteuta edistymispalkki ja status-viestit 🟡 ⚡ Sonnet [Claude 1, main]
- [ ] Toteuta asetukset (puhenopeus, äänen valinta) 🟡 ⚡ Sonnet [Claude 1, main]
- [ ] Toteuta virheilmoitukset käyttäjäystävällisesti 🟢 ⚡ Sonnet [Claude 1, main]
- [ ] Aja TTS erillisessä threadissa ettei GUI jäädy 🟡 ⚡ Sonnet [Claude 1, main]

## Backlog

### Vaihe 5: Paketointi (.exe)
- [ ] Konfiguroi PyInstaller (.spec-tiedosto) 🟡 ⚡ Sonnet
- [ ] Sisällytä ffmpeg binääri pakettiin 🟡 ⚡ Sonnet
- [ ] Testaa .exe eri PDF-tiedostoilla 🟡 ⚡ Sonnet
- [ ] Optimoi .exe-koko 🟢 ⚡ Sonnet

### Vaihe 6: Installeri
- [ ] Luo Inno Setup -skripti (installer/setup.iss) 🟡 ⚡ Sonnet
- [ ] Installerin toteutus (tervetuloa, lisenssi, asennuspolku, Start Menu, uninstaller) 🔴 ⚡ Sonnet
- [ ] Lisää sovellukselle ikoni (assets/icon.ico) 🟢 ⚡ Sonnet
- [ ] Testaa installeri puhtaalla Windows-ympäristöllä 🟡 ⚡ Sonnet
- [ ] Varmista että kaikki riippuvuudet sisältyvät installeriin 🟡 ⚡ Sonnet

### Vaihe 7: Dokumentaatio
- [ ] Luo README.md (käyttöohjeet, kuvakaappaukset, tekniset tiedot) 🟡 ⚡ Sonnet
- [ ] Luo BUILDING.md (ohjeet kehittäjälle buildin tekemiseen) 🟡 ⚡ Sonnet
