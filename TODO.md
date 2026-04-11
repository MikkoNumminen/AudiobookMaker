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

### Local disk cleanup (deferred — Mac still in use)
- [ ] After Turo's fast-track audiobook run succeeds AND no more local Chatterbox dev is needed, delete `.venv-chatterbox/` (symlink) + `.venv-qwen/` (1.4 GB real venv — originally named `.venv-qwen` from the dead Qwen investigation, later repurposed for Chatterbox), `~/.cache/huggingface/hub/models--ResembleAI--chatterbox` (6.0 GB), `~/.cache/huggingface/hub/models--Finnish-NLP--Chatterbox-Finnish` (2.0 GB). Total ~9.4 GB reclaimable. Do NOT delete while the Mac is still being used for dev. 🟢 ⚡ Sonnet

### Chatterbox-Finnish — upstream contribution (ready to ship)
- [ ] Submit the bug report + patch in `docs/upstream/chatterbox/BUG_REPORT.md` and `docs/upstream/chatterbox/hook_leak_fix.patch` as a GitHub issue + PR to `resemble-ai/chatterbox`. Reproducer is at `docs/upstream/chatterbox/repro_hook_leak.py`. 🟡 🧠 Opus

### Chatterbox-Finnish — Turo feedback loop
- [ ] After Turo runs the fast-track bundle on his PC, collect any errors/friction and fix them in `scripts/setup_chatterbox_windows.ps1` or `scripts/run_audiobook.bat` 🟡 ⚡ Sonnet
- [ ] If Turo hits the "open PowerShell" wall: write a `setup.bat` wrapper that launches the .ps1 with `ExecutionPolicy Bypass` already (check whether current `setup_chatterbox_windows.bat` already does this) 🟢 ⚡ Sonnet

### VoxCPM2 — GPU testing (requires NVIDIA machine)
- [ ] Run `pip install voxcpm` on a GPU machine and verify the GUI sees the engine as available 🟢 ⚡ Sonnet
- [ ] Synthesize the first ~5000 characters of the Turo book with VoxCPM2 in Finnish, compare against v3 (Noora Edge-TTS) 🟡 🧠 Opus
- [ ] Try `voice_description="warm baritone elderly male"` on an English sample and judge whether the output sounds right 🟡 🧠 Opus
- [ ] Try `reference_audio` cloning with a 10 s clip (e.g. a Morgan Freeman-style sample) and evaluate the result 🟡 🧠 Opus
- [ ] If VoxCPM2's Finnish is not clearly better than Noora: decide whether to keep the engine in the codebase or remove it 🟢 🧠 Opus

### Qwen3-TTS — DROPPED
Investigated and ruled out. Qwen3-TTS officially supports only 10 languages (Finnish not
included), is CUDA-only (flash-attn3), hits a hard `Output channels > 65536` convolution
limit on MPS, and CPU inference is slower than realtime even on an RTX 4090. The
`dev_qwen_tts.py` script stays as a feasibility probe for any future developer who wonders
"why not Qwen" but is not a viable engine for this project. No further action planned.

### Requires a Windows machine
- [ ] Add an application icon (assets/icon.ico) 🟢 ⚡ Sonnet
- [ ] Test the .exe against multiple PDF files 🟡 ⚡ Sonnet
- [ ] Test the installer on a clean Windows environment 🟡 ⚡ Sonnet
