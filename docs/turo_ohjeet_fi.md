# Ohjeet Turolle — äänikirjan teko PDF:stä

Step by step, ihan peruskauraa.

---

**Moi Turo! Tässä ohjeet, miten teet oman äänikirjan PDF:stä omalla tietokoneellasi.
Tämä vie yhteensä noin 2 tuntia (siitä suurin osa on odottelua). Lue nämä läpi ensin.**

---

## Mitä tarvitset

- Windows-tietokoneesi (se NVIDIA-koneesi)
- Internet-yhteys
- PDF-tiedosto, jonka haluat muuttaa äänikirjaksi
- Noin 15 gigatavua vapaata levytilaa

---

## Osa 1 — Asenna kolme ohjelmaa (kertaluonteinen, ~10 minuuttia)

Nämä asennetaan vain kerran. Jos olet jo asentanut ne, hyppää Osaan 2.

### 1.1 Asenna Python 3.11

1. Mene osoitteeseen: https://www.python.org/downloads/release/python-3119/
2. Vieritä alaspäin ja klikkaa: **Windows installer (64-bit)**
3. Avaa ladattu tiedosto
4. **TÄRKEÄÄ:** rastita heti ensimmäisessä ikkunassa alhaalta ruutu
   **"Add python.exe to PATH"**
5. Klikkaa **"Install Now"**
6. Odota, kunnes asennus valmis, sulje ikkuna

### 1.2 Asenna Git

1. Mene osoitteeseen: https://git-scm.com/download/win
2. Lataus alkaa automaattisesti — klikkaa ladattua tiedostoa
3. Klikkaa "Next" joka ikkunassa (oletukset on kunnossa)
4. Sulje asennusohjelma

### 1.3 Tarkista että NVIDIA-ajurit on ajan tasalla

1. Avaa **GeForce Experience** (pitäisi olla jo koneessa)
2. Klikkaa **"Ajurit"** (Drivers) -välilehteä
3. Jos tarjolla on päivitys → asenna se, ja käynnistä kone uudelleen

**Jos Python, Git tai NVIDIA GeForce Experience puuttuu, pysähdy tähän ja soita Mikolle.**

---

## Osa 2 — Lataa projekti ja asenna kaikki (~15 minuuttia, tarvitset internetyhteyden)

### 2.1 Avaa PowerShell

1. Paina **Windows-näppäin**
2. Kirjoita `powershell`
3. Paina **Enter**
4. Eteesi avautuu sinitekstinen ikkuna — tämä on PowerShell

### 2.2 Siirry työkansioon

Kirjoita tämä komento ja paina **Enter**:

```
cd ~\Documents
```

(Tämä siirtää sinut **Tiedostoni/Documents**-kansioon)

### 2.3 Lataa projekti GitHubista

Kirjoita tämä komento ja paina **Enter**:

```
git clone https://github.com/MikkoNumminen/AudiobookMaker.git
```

Odota ~30 sekuntia — kun PowerShell näyttää uuden rivin, valmista.

### 2.4 Avaa kansio Explorer-ikkunassa

Kirjoita:

```
explorer AudiobookMaker\scripts
```

Paina **Enter**. Eteesi avautuu kansio nimeltä **scripts**.

### 2.5 Käynnistä asennus

1. **Kaksoisklikkaa** tiedostoa nimeltä **`setup_chatterbox_windows.bat`**
2. Eteesi avautuu musta ikkuna jossa vierii tekstiä
3. **Tämä vie 10–20 minuuttia.** Se:
   - Asentaa tekoälykirjastot
   - Lataa noin 7 gigatavua mallitiedostoja
   - Tekee pari pikku korjausta
4. **Älä sulje ikkunaa**, vaikka se näyttäisi pysähtyvän — se lataa isoja tiedostoja
5. Kun näet lopussa tekstin "Setup complete" tai "Press Enter to exit" → paina **Enter**
6. Asennus on valmis ✅

**Jos mustassa ikkunassa näkyy PUNAISTA tekstiä tai se sulkeutuu yhtäkkiä:**
ota kuvakaappaus ja lähetä Mikolle.

---

## Osa 3 — Tee äänikirja (~80–110 minuuttia per kirja)

Tämä osa toistetaan aina kun haluat tehdä uuden äänikirjan.

### 3.1 Siirrä PDF-tiedosto johonkin helposti löydettävään paikkaan

Esim. **Työpöydälle** tai **Tiedostoni/Documents**-kansioon.

### 3.2 Avaa scripts-kansio

1. Mene kansioon **Tiedostoni → AudiobookMaker → scripts**
2. Pidä molemmat ikkunat auki: (a) scripts-kansio (b) kansio jossa PDF on

### 3.3 Vedä PDF päälle ja pudota

1. **Vedä PDF-tiedosto** PDF-kansiosta **`run_audiobook.bat`** -tiedoston päälle
   scripts-kansiossa
2. Pudota
3. Eteesi avautuu musta ikkuna jossa lukee:
   - `PDF: ...\sinun-tiedosto.pdf`
   - `Output: ...\dist\audiobook\`
   - `Synthesis is about to start`
4. Paina **Enter** jatkaaksesi
5. Musta ikkuna alkaa vieriä tekstiä: `[chapter 1/8] chunk 1/126 ...`
6. **Tämä vie noin 80–110 minuuttia** (riippuu PDF:n pituudesta)

### 3.4 Mitä teet odotellessa

- Tietokonetta voi käyttää muuhun samaan aikaan, mutta se on vähän hitaampi
- **ÄLÄ** käynnistä pelejä, Photoshoppia tai muita raskaita ohjelmia — ne vievät
  näytönohjaimen muistin ja koko homma voi kaatua
- **ÄLÄ** sulje mustaa ikkunaa
- Voit laittaa koneen viltin alle nukkumaan, mutta **älä** kytke sitä sammuksiin —
  homma jatkuu missä jäi
- Selailu, musiikki, Teamsin soittelu ovat ok

### 3.5 Kun olet valmis

1. Musta ikkuna näyttää tekstin **"DONE. Opening the output folder..."**
2. Windows Explorer avautuu automaattisesti kansioon jossa äänitiedostosi on
3. Löydät sieltä:
   - **`00_full.mp3`** — koko kirja yhtenä tiedostona (tämä on se jonka haluat)
   - `01_*.mp3`, `02_*.mp3`, ... — kirjan luvut erillisinä tiedostoina (varmuuskopiot)
4. Klikkaa `00_full.mp3` → avautuu Windowsin oletusääniohjelmaan → kuuntele

---

## Jos jotain menee vikaan

### "python is not recognized as an internal or external command"

Python ei asentunut kunnolla. Asenna uudelleen (Osa 1.1) ja muista rastittaa
**"Add python.exe to PATH"**.

### Musta ikkuna näyttää "CUDA error" tai "torch.cuda.is_available() returned False"

Näytönohjaimen ajuri on liian vanha. Päivitä GeForce Experiencessä ja käynnistä
kone uudelleen.

### Musta ikkuna vain seisoo ja tuntuu jumiutuneen

Normaalia ensimmäisellä kerralla — se lataa isoa tiedostoa. Anna olla **vähintään
20 minuuttia** ennen kuin huolestut. Jos yli 30 min ilman mitään liikettä → ota
kuvakaappaus ja lähetä Mikolle.

### Keskeytyi kesken / sammutit vahingossa

Ei hätää — aja `run_audiobook.bat` uudelleen samalla PDF:llä. Se jatkaa siitä
mihin jäi (resume on automaattista).

### Mitään muuta outoa tapahtuu

Ota kuvakaappaus koko näytöstä (**Windows + Shift + S**) ja lähetä Mikolle
WhatsAppissa.

---

## Yhteenveto kertaluonteisista asioista

| Asia | Aika | Tulos |
|---|---|---|
| Python + Git + NVIDIA-ajurit | ~10 min | kertaluonteinen |
| `setup_chatterbox_windows.bat` | ~15 min | kertaluonteinen |
| **Per äänikirja:** vedä PDF päälle `run_audiobook.bat` | ~80–110 min | yksi MP3-tiedosto |

---

**Onnea matkaan! Jos tökkää missään vaiheessa, kuvakaappaus + viesti Mikolle.**
