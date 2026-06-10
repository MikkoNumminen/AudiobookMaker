# Process-kill policy — surface PIDs, never kill on your own

An AI session in this repo must **never kill a process it did not
itself start in this same session** — not GPU compute processes, not a
stuck-looking installer, not "residual" python processes holding VRAM.

## Why

- A process that looks wedged may be another session's (or the user's)
  long synthesis / training run mid-checkpoint. Killing it loses hours
  of GPU time or corrupts a half-written artefact.
- The voice-pack and Chatterbox pipelines hold ~6 GB VRAM per process
  (CLAUDE.md resource discipline); "stuck VRAM" is often a live run,
  not a leak.
- The one observed class of genuinely-stuck processes (driver-level
  hangs) cannot be reliably distinguished from a busy run by inspecting
  process state alone.

## What to do instead

1. Gather the evidence: `nvidia-smi` (or `ps`) output showing PID,
   process name, memory, and runtime.
2. Present the PIDs and your reading of them to the user.
3. **Wait for the user's call.** The user decides what dies. If the
   user is absent, leave the processes alone and work around them
   (e.g. postpone the GPU-needing step).

Processes you started yourself in this session (a test run, a probe
you spawned) are yours to terminate normally.
