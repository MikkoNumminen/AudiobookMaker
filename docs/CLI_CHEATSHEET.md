# AudiobookMaker CLI — cheatsheet

Convert books to audiobooks from the command line. Same engines and voices as the desktop app, no window required.

## Install

```bash
git clone https://github.com/MikkoNumminen/AudiobookMaker
cd AudiobookMaker
pip install -e .
```

After this the `audiobookmaker` command works from any directory.

Windows users without Python: grab `AudiobookMaker-CLI-X.Y.Z-windows-x64.zip` from the [releases page](https://github.com/MikkoNumminen/AudiobookMaker/releases), unzip, add the folder to `PATH`. ffmpeg is bundled.

## First time — check everything works

```bash
audiobookmaker doctor
```

Reports ffmpeg presence, which engines are ready, free disk space, and Python version. Green across the board means you're ready to convert.

## Convert a book

```bash
audiobookmaker convert book.pdf
```

Works with **PDF**, **EPUB**, and **TXT**. The output MP3 lands in `~/.audiobookmaker/` by default. The final path is printed when synthesis finishes.

Pick a specific engine, voice, language for one run:

```bash
audiobookmaker convert book.pdf --engine edge --language fi --voice fi-FI-NooraNeural
```

Choose where the file goes:

```bash
audiobookmaker convert book.pdf --output ~/Audiobooks/my-book.mp3
```

## Hear what an engine sounds like before a long run

```bash
audiobookmaker sample book.pdf
```

Synthesises the first ~500 characters and writes `book_sample.mp3`. Compare two engines:

```bash
audiobookmaker sample book.pdf --engine edge   --output book_edge.mp3
audiobookmaker sample book.pdf --engine piper  --output book_piper.mp3
```

## Quick voice test, no file involved

```bash
audiobookmaker preview "Hello, this is a test."
```

Speaks the text through your speakers. Add `--engine`, `--voice` to test combinations. Use `--no-play` to write the audio to a tempfile path instead of playing it.

## See what voices and engines you have

```bash
audiobookmaker voices list
audiobookmaker voices list --engine edge --language fi
audiobookmaker engines list
```

## Install or remove an engine

```bash
audiobookmaker engines install piper
audiobookmaker engines install chatterbox_fi
audiobookmaker engines remove piper
audiobookmaker engines check chatterbox_fi
```

`engines check` exits 0 if available, 2 if not — useful in scripts:

```bash
audiobookmaker engines check piper || audiobookmaker engines install piper --yes
```

## Set your defaults so you don't repeat flags

```bash
audiobookmaker config set engine_id piper
audiobookmaker config set language fi
audiobookmaker config show
```

From this point `audiobookmaker convert book.epub` uses Piper without any `--engine` flag.

Other config commands:

```bash
audiobookmaker config show engine_id   # show one field
audiobookmaker config reset engine_id  # reset one field to default
audiobookmaker config reset            # reset everything
audiobookmaker config path             # print the config file path
```

## Voice packs (custom voices you imported)

```bash
audiobookmaker packs list
audiobookmaker packs import ~/Downloads/my_voice_pack
audiobookmaker packs info my_voice_pack
audiobookmaker packs remove my_voice_pack
```

## Update to a new version

```bash
audiobookmaker update check
audiobookmaker update apply
```

`update apply` downloads, verifies SHA-256, and runs the installer. Only meaningful for the standalone Windows binary — `pip install` users update with `git pull && pip install -e .`.

## Batch convert a folder

```bash
# bash / zsh
for f in ~/books/*.epub; do
    audiobookmaker convert "$f" --quiet
done
```

```powershell
# PowerShell
Get-ChildItem ~/books -Filter *.epub | ForEach-Object {
    audiobookmaker convert $_.FullName --quiet
}
```

`--quiet` suppresses per-chunk progress and prints only the final output path — keeps batch output readable.

## Pipe progress into another tool

Every synthesis subcommand supports `--json` for NDJSON (one JSON object per line):

```bash
audiobookmaker convert book.pdf --json | jq -r 'select(.kind=="chunk") | "\(.total_done)/\(.total_chunks)"'
```

Grab the final output path in a script:

```bash
out=$(audiobookmaker convert book.pdf --json | jq -r 'select(.kind=="done") | .output_path')
echo "Written to: $out"
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Bad input or validation failure |
| 2 | Missing dependency (engine not installed, ffmpeg absent, SHA-256 mismatch) |
| 3 | User cancelled (Ctrl-C or "N" answer) |
| 4 | Runtime failure (network, GPU, synthesis error) |
| 5 | Unexpected internal error |

## When in doubt

```bash
audiobookmaker --help
audiobookmaker <command> --help
audiobookmaker doctor
```

Full reference and worked examples in [docs/CLI.md](CLI.md).
