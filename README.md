# AudiobookMaker

Muuntaa PDF-tiedostot äänikirjoiksi. Lataa PDF, paina nappia, saat MP3:n.

## Ominaisuudet

- PDF-tekstin automaattinen tunnistus ja siivous (sivunumerot, otsikot, alaviitteet)
- Lukujen automaattinen tunnistus
- Tekstistä puheeksi edge-tts:llä (suomi ja englanti)
- MP3-tiedosto per luku tai yksi yhdistetty tiedosto
- Yksinkertainen Tkinter-GUI
- Windows-installer — ei vaadi Pythonia tai muita asennuksia

## Asennus (loppukäyttäjä)

1. Lataa `AudiobookMaker-Setup.exe` Releases-sivulta
2. Tuplaklikkaa ja seuraa ohjeita
3. Löydät sovelluksen Start-valikosta

## Käyttö

1. Avaa sovellus
2. Valitse PDF-tiedosto
3. Valitse kieli (suomi / englanti)
4. Säädä puhenopeus tarvittaessa
5. Paina **Muunna** — edistyspalkki näyttää tilanteen
6. Tallenna MP3

## Kehitysympäristö

Vaatii Python 3.11+, ffmpeg järjestelmässä tai dist/-kansiossa.

```bash
git clone <repo>
cd AudiobookMaker
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

Testit:

```bash
pytest tests/
```

## Projektin rakenne

```
AudiobookMaker/
├── src/
│   ├── pdf_parser.py    # PDF-parsinta ja tekstin siivous
│   ├── tts_engine.py    # edge-tts-integraatio ja äänitiedostojen yhdistäminen
│   ├── gui.py           # Tkinter-käyttöliittymä
│   └── main.py          # Sovelluksen käynnistyspiste
├── tests/               # Yksikkötestit
├── assets/              # Ikoni ja muut resurssit
├── installer/           # Inno Setup -skripti
├── dist/                # Käännetyt binäärit (ei versiohallinnassa)
└── requirements.txt
```

## Teknologiat

| Komponentti | Kirjasto |
|-------------|---------|
| PDF-parsinta | PyMuPDF (fitz) |
| Puhesynteesi | edge-tts |
| Äänen käsittely | pydub + ffmpeg |
| GUI | Tkinter |
| Windows-paketointi | PyInstaller |
| Installer | Inno Setup |

## Rajoitukset

- edge-tts käyttää Microsoftin palvelimia — vaatii internet-yhteyden
- Skannatut PDF:t (kuva-PDF) eivät toimi — tekstin pitää olla kopioitavissa
- PDF-tekstin siivous ei ole täydellinen kaikille formaateille

## Lisenssi

MIT
