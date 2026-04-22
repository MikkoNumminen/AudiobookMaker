# Release history

Every release worth talking about, in reverse-chronological order. The
latest entry is mirrored in the top-level [README.md](../README.md) so
first-time readers see what changed most recently without scrolling
past a wall of history.

Anything older than the top-of-list entry lives here.

---

**v3.11.0** -- GUI polish for the main workflow and voice-pack plumbing:

- **Buttons light up when they are ready** -- Convert and Make sample
  stay greyed out until you pick both an input and a voice. Preview
  and Open folder stay greyed out until there is actually a finished
  audiobook to open. Fewer buttons you can click that do nothing
- **Progress bar only when something is happening** -- the thin blue
  bar at the bottom now hides itself when nothing is running. No more
  static zero-percent bar staring back at you from an idle app
- **Chatterbox chunk-size dial hides when you are not using Chatterbox**
  -- the "chunk size" spinbox only matters for the Finnish Chatterbox
  engine. Pick Edge-TTS or Piper and the control disappears, so the
  Settings panel shows just the knobs that actually affect your run
- **Workflow grouping in the action row** -- a thin vertical divider
  splits the action buttons into two honest halves: on the left, the
  things that *produce* output (Convert, Make sample); on the right,
  the things that let you *review* output (Preview, Open folder).
  Convert stays visually dominant
- **Voice packs now load their LoRA adapter at synthesis time** -- if
  you imported or cloned a voice pack that was trained on more data
  (reduced or full LoRA tier), the synthesis subprocess now actually
  wires the adapter into the Chatterbox model instead of falling
  back to the bare reference clip. Few-shot packs keep working the
  same as before
- **Voice Cloner survives the PyTorch 2.6 upgrade** -- the diarizer
  (the part that figures out who is speaking in your audio file) was
  crashing on checkpoint load after PyTorch's security defaults
  changed. Two compatibility shims now make pyannote load cleanly
  again and sidestep a separate speechbrain stack-walk bug that was
  aborting mid-pipeline on some machines
- **1884 tests passing** -- up from 1878

**v3.10.0** -- Clone a voice from any audio file, right inside the app:

- **Clone voice from file button** -- a new button in Settings opens a
  file picker. Drop in a clean recording of a single voice (or two
  voices talking), the app listens to it, figures out who is speaking,
  asks you what to name each voice, and adds them to the Voice
  dropdown. No command line, no scripts. About ten minutes from drop
  to voice-in-dropdown for a five-minute recording
- **Drag and drop audio onto the window** -- same flow, no button
  needed. Drop a `.wav` / `.mp3` / `.m4a` / `.flac` / `.ogg` / `.m4b`
  on the main window and the clone-voice flow fires. Silently falls
  back to the file picker if the drop library is missing
- **Voice Cloner capability in Engine Manager** -- a new "Extras" row
  installs the two helper libraries (one that listens to your file,
  one that figures out who is speaking) into the same Python folder
  as Chatterbox, so the main app stays lean. Install / Remove works
  the same as the existing engine rows
- **Guided Hugging Face setup** -- the listener component needs a
  free one-time Hugging Face key. A dedicated setup window walks you
  through it in three clicks with browser buttons that take you to
  the right pages. If the key is wrong or the network is down you
  get a plain-language message, not a Python traceback
- **Pre-analyze modal asks what you want** -- before the app starts
  listening, it asks: how many voices is the recording (1, 2, 3-8,
  auto), and which language (Finnish or English). Finnish cloning in
  v1 is biased toward the stock Finnish voice; English clones more
  cleanly
- **Copyright-safe by default** -- the raw filename never leaks into
  the log panel. Scratch files land in `.local/clone_scratch/` so
  nothing you feed the app ends up in a diff or a PR
- **1872 tests passing** -- up from 1598. The clone-voice flow,
  drag-drop parser, Engine Manager Extras row, and Hugging Face
  setup modal each have regression coverage

**v3.9.1** -- Bug reporting, a voice pack builder, and small polish:

- **Report a bug button** -- a new link in Settings opens a pre-filled
  GitHub issue with your app version, OS, and engine info. So fixes
  don't wait on you remembering which build you were running
- **Engine Manager follows the Language toggle** -- flipping Language
  between Finnish and English now also relocalises the Engine Manager
  window, Back button included
- **Voice pack builder pipeline** -- five command-line tools under
  `scripts/voice_pack_*.py` turn a clean sample recording into an
  installable voice pack. The stages are: analyze (ASR + diarization),
  cluster characters, export per-speaker clips, train a LoRA adapter,
  package for the app. Dev-only for now; the app still imports
  finished packs through the **Import voice pack** button
- **Inline audio sample player** -- the Listen and Make Sample
  buttons now play through an in-app player instead of shelling out
  to the OS default app. Faster, quieter, no orphaned windows
- **Finnish acronym fallback** -- unknown uppercase acronyms now
  fall back to a clean letter-by-letter read (`NSA` → "en-es-aa")
  instead of getting mangled by the AI
- **1598 tests passing** -- up from 1565

**v3.9.0** -- Import voice packs, Cold Forge redesign, and a much
bigger test suite keeping it all honest:

- **Import voice pack button** -- a new button in Settings opens a
  folder picker, copies the pack to `~/.audiobookmaker/voice_packs/`,
  and the voice shows up next to Grandmom in the Voice dropdown. Picks
  from a pack auto-wire the reference audio so Chatterbox clones from
  it without you having to point at a file manually. Voice packs are a
  bundle of a reference clip and metadata; the pipeline to build them
  from source recordings lives under `scripts/voice_pack_*`
- **Cold Forge design system** -- a new theme module (`gui_style.py`)
  centralises fonts, spacing, colours, and icons so the whole window
  follows one consistent visual language. Replaces the old mix of
  hardcoded colour literals
- **Chatterbox registered like every other engine** -- Chatterbox now
  plugs into the shared engine registry via a subprocess-aware bridge
  class (`tts_chatterbox_bridge.py`). The GUI picks Chatterbox the same
  way it picks Edge-TTS or Piper; the subprocess split is an
  implementation detail behind a `uses_subprocess = True` flag
- **GUI builders split out of `gui_unified.py`** -- header bar, engine
  bar, settings panel, and action row each live in their own module
  under `src/gui_builders/`, so the main window file reads like
  glue-code instead of a 3000-line god class
- **Stress-tested Chatterbox long-run** -- the 500-call Tier 1 validator
  exercises the same engine handle across hundreds of synthesis calls
  and now holds memory flat through the whole run. Fixes an upstream
  EOS-suppression bug that caused occasional swallowed sentences on
  multi-hour books
- **1565 tests passing** -- pre-commit hooks and CI enforce the full
  suite before any commit. Test count grew from 618 → 1565 over the
  recent audit pass

**v3.7.0** -- Sample button, language picker up front, and English
audiobooks that finally sound English:

- **Make Sample button** -- a new button sits next to Convert. Click
  it and the app generates a ~30-second sample from the start of your
  book and saves it to `<book>_sample.mp3` next to the planned output.
  Lets you A/B two engines or voices in seconds before committing to
  a multi-hour full run
- **Language picker moved to the main bar** -- "Language" now sits
  next to Engine and Voice (was buried in Settings). Picking a
  language filters the Engine and Voice dropdowns so you only see
  what actually works in that language. The setting sticks across
  restarts; first launch defaults to Finnish if Windows is in
  Finnish, English otherwise
- **English audiobooks read like English audiobooks** -- Chatterbox
  in English mode no longer quietly applies Finnish rules (Roman
  numerals as Finnish ordinals, Finnish case inflection on numbers,
  and so on). The normalizer now dispatches by language
- **English text normalizer** -- full rules for English currency,
  units, time, dates, telephone numbers, URLs and emails, and
  acronyms. Numbers, money, and dates in English books finally sound
  natural
- **41 Edge-TTS voices, 25 Piper voices** -- large voice catalogue
  expansion across the supported languages
- **Chatterbox Grandmom per language** -- one voice entry per
  language (Grandmom (Finnish), Grandmom (English)) so the dropdown
  matches what you actually get

**v3.6** -- Live ETA and an auto-updater that can recover on its own:

- **Sticky status strip with live ETA** -- a status line pinned under
  the toolbar shows current progress and remaining time, and gives
  you a pre-synthesis estimate so you know roughly how long a book
  will take BEFORE you start
- **Self-healing SHA-256 fallback** -- if the release notes are
  missing the security hash, the app falls back to a sidecar
  `.exe.sha256` file. No manual intervention needed
- **Open in browser fallback** -- when an auto-update is blocked
  (antivirus, permissions, network hiccup), the update banner now
  includes an "Open in browser" button so you can always grab the
  installer manually
- **Foreground after update** -- the app now pops itself to the
  front after a successful auto-install instead of silently opening
  behind your browser
- **Periodic re-check + real errors** -- the app re-checks for
  updates every 4 hours, and download failures now show a proper
  error message instead of failing silently
- **Chatterbox subprocess hotfix** -- ffmpeg path is now correctly
  wired into the Chatterbox subprocess, and the audio/chunking/
  normalizer modules are bundled into the installer so Chatterbox
  no longer crashes on first run

**v3.5** -- Grandmom speaks English, plus a lot of quiet polish:

- **Grandmom speaks English** -- the default Chatterbox voice now
  works natively in English via voice-cloning, not just Finnish
- **Open in browser for every update** -- the update banner always
  includes a browser-download link as a safety net, no matter what
  the auto-updater is doing
- **Launcher help link works again** -- the in-app "Help" link from
  the launcher now points at the README instead of a dead URL, and
  stray old-branding references were scrubbed
- **Hardened cleanup paths** -- Piper and Chatterbox setup no longer
  leave half-downloaded files behind when something goes wrong
  mid-download

**v3.4** -- Read EPUB files too:

- **EPUB and TXT input** -- the "Book" tab now accepts EPUB and plain
  `.txt` files alongside PDF. Same flow: pick a file, pick a voice,
  press Convert

**v3.3** -- Naming, reliability, and a rescue when you reinstall:

- **Default Chatterbox voice is "Grandmom"** -- the stock cloning
  voice has a proper name instead of an opaque file id, so you can
  tell voices apart in the dropdown
- **Your MP3s survive a reinstall** -- uninstalling or updating no
  longer wipes MP3s sitting in the install folder. The cleanup step
  rescues user audio before removing old app files
- **Piper setup actually finishes** -- the `espeakbridge` native
  component is now bundled correctly, so Piper no longer fails to
  load with a cryptic import error on first run
- **Update button stops double-firing** -- the update banner's
  click handler no longer runs twice on some clicks, which had
  occasionally stalled the install

**v3.2** -- Polish pass on the install and update experience:

- **Goat splash on startup and during updates** -- the goat icon
  appears the moment the app starts, and stays on screen through
  the 10-15 second update gap so you never wonder if the app crashed
- **Running version shown in the title bar** -- so you can confirm
  which build is actually running after an update
- **Progress bar reaches 100%** -- "Done!" no longer appears while
  the bar is still at 85%. Every gain counts, visibly
- **Chunk progress lines are green** -- successful `[chapter N/N]
  chunk M/K` steps render green so you can watch progress at a glance
- **Warning-free log panel** -- upstream cosmetic warnings from torch,
  diffusers, transformers, and HuggingFace Hub are suppressed at the
  source. Real warnings still show up yellow
- **Chatterbox alignment fix shown as info** -- when our EOS loop-break
  kicks in, the log shows one calm `[info] alignment fix applied...`
  line instead of two scary red warnings
- **Generated files save to the install folder root** -- no more
  burrowing into an `audiobooks\` subdirectory. MP3s land next to the
  app .exe and survive uninstall/reinstall
- **Auto-bump output filenames** -- `Convert` never overwrites a
  previous `texttospeech_N.mp3`; the next free number is picked
  automatically
- **Self-healing auto-updater** -- if the silent install fails for
  any reason, the next launch detects it and offers a visible
  installer fallback

**v2.3** -- Major update. Modern UI, more voices, auto-updates, and
a lot of fixes to make everything actually work reliably:

- **Modern look** -- the app uses CustomTkinter with dark/light mode
  that follows your Windows theme automatically
- **Listen button** -- type text, click Listen, hear it spoken right
  away. No need to save a file first. Great for trying out voices
- **30+ voices in 6 languages** -- Finnish, English, German, Swedish,
  French, and Spanish voices from Edge-TTS. Offline Piper voices for
  Finnish, English, and German
- **Auto-updates** -- the app checks for new versions every 5 minutes.
  When one is found, a banner appears at the top. Click it and the app
  downloads, installs, and restarts itself. No manual downloads needed
  after the first install
- **Voice recording** -- record your own voice directly from the app
  and use it for voice cloning with Chatterbox
- **Chatterbox works with text** -- you can type or paste text and
  synthesize it with Chatterbox. Previously only PDF input was
  supported
- **Smart language detection** -- the app detects your Windows language
  and picks Finnish or English UI automatically on first run
- **Single-instance guard** -- prevents accidentally opening two copies
  of the app, which could cause file conflicts or GPU crashes. If you
  need two windows (e.g. different engines on different files), the app
  asks you to confirm
- **Automatic output paths** -- no more file-picker dialogs before you
  start. Every generated MP3 lands next to the installed app (same
  folder as the .exe), with auto-incrementing filenames so nothing is
  overwritten
- **500+ tests** -- pre-commit hooks and CI enforce that all tests pass
  before any code ships

**v2.0.0** -- Unified app:

- One download replaces both old installers (Main and Launcher)
- Finnish/English UI toggle
- Plain text input alongside PDF
- In-app engine installer for Chatterbox
