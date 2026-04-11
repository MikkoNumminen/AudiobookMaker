# Chatterbox-TTS on Cloud GPU — Runbook

Practical guide for generating Finnish audiobooks with the
[Finnish-NLP Chatterbox finetune](https://huggingface.co/Finnish-NLP) on a
rented GPU. One-shot synthesis, no training, no serving.

## Why cloud

- **CPU is too slow.** Chatterbox multilingual on an M-series Mac runs at
  ~6–7x realtime. A 6-hour audiobook = 36–42 hours of CPU grinding.
- **No local GPU.** None of us own an RTX 4090.
- **Rented 4090 is cheap.** ~0.1 RTF on a 4090 (10x faster than realtime)
  means a 6-hour book finishes in ~35–40 minutes of GPU time for under $1.
- **No subscription.** Pay-per-second, tear down when done.

## TL;DR recommendation

| Rank | Provider      | GPU       | Price (Apr 2026) | Why |
|------|---------------|-----------|------------------|-----|
| 1    | **RunPod Community Cloud** | RTX 4090 | **$0.34/hr** | Pre-built PyTorch images, 60-second boot, web terminal, persistent volumes, no Docker gymnastics. |
| 2    | Vast.ai        | RTX 4090 | from **$0.29/hr** | Cheapest marketplace rate, but host quality varies and bandwidth/disk are extra line items. |

Runner-up if a 4090 isn't available: **RunPod A40 ($0.35/hr)** or **L40S
($0.86/hr secure)** — both have 48 GB VRAM and comparable speed for TTS.

Pricing verified April 2026:
- [RunPod RTX 4090 page](https://www.runpod.io/gpu-models/rtx-4090)
- [Vast.ai RTX 4090 page](https://vast.ai/pricing/gpu/RTX-4090)
- [Modal pricing](https://modal.com/pricing) — L4 $0.80/hr, A10 $1.10/hr,
  A100-40GB $2.10/hr (serverless, more convenient but 3x the cost)
- [Lambda Labs](https://lambda.ai/pricing) — A100 from $1.29/hr, no 4090

## Estimated cost + time for a 6-hour audiobook

Assumptions: ~500k chars, ~1000 chunks, RTF ~0.1 on a 4090, model/weights
download once (~4 GB, 2 min), input PDF ~20 MB, output MP3 ~500 MB.

| Step                            | Time        | RunPod 4090 cost |
|---------------------------------|-------------|------------------|
| Pod boot + pip install          | 4 min       | $0.02            |
| Model download (first run)      | 2 min       | $0.01            |
| Synthesis (1000 chunks @ 0.1 RTF) | ~36 min   | $0.20            |
| MP3 encoding + download         | 3 min       | $0.02            |
| Buffer (typos, restart, etc.)   | 10 min      | $0.06            |
| **Total**                       | **~55 min** | **~$0.35**       |

Vast.ai at $0.29/hr comes out to ~$0.27 for the same run, but add ~$0.05
for storage/egress line items — call it a wash. Pick RunPod for sanity.

Modal (serverless) is ~$1.25 for the same job (L4) with **zero** idle
cost — worth it if you synthesize one book every few months and don't
want to babysit a pod.

## Step-by-step runbook (RunPod)

### 1. One-time account setup

1. Sign up at https://runpod.io (GitHub/Google OAuth works).
2. Add $10 credit (minimum). Credit card or crypto.
3. Generate an SSH key locally if you haven't:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/runpod -N ""
   ```
4. Paste `~/.ssh/runpod.pub` into RunPod → Settings → SSH Public Keys.

### 2. Launch a pod

1. Go to **Pods → Deploy**.
2. Filter: **Community Cloud**, GPU = **RTX 4090**, 1x.
3. Template: **RunPod PyTorch 2.4** (CUDA 12.4, Python 3.11, pre-installed).
4. Volume disk: **20 GB** (model weights ~4 GB, output MP3 ~500 MB, buffer).
5. Expose HTTP ports: leave default (we only need SSH).
6. Click **Deploy On-Demand**. Wait ~60 seconds.
7. Copy the SSH command from the pod's **Connect** panel. It looks like:
   ```
   ssh root@<pod-ip> -p <port> -i ~/.ssh/runpod
   ```

### 3. Upload the input PDF

From your Mac:
```bash
scp -P <port> -i ~/.ssh/runpod \
    /path/to/book.pdf root@<pod-ip>:/workspace/book.pdf
```

### 4. Install Chatterbox and dependencies

SSH in, then:
```bash
cd /workspace
pip install --upgrade pip
pip install chatterbox-tts pypdf soundfile pydub
apt-get update && apt-get install -y ffmpeg
```

### 5. Pull the Finnish finetune

```bash
python -c "
from huggingface_hub import snapshot_download
snapshot_download('Finnish-NLP/chatterbox-multilingual-fi',
                  local_dir='/workspace/chatterbox-fi')
"
```
(Replace with the exact HF repo once the finetune is published. Estimate
based on typical Chatterbox finetune size: ~4 GB, ~2 minutes on RunPod's
network.)

### 6. Run synthesis

Upload `scripts/generate_audiobook_parallel.py` (or a Chatterbox variant)
from your Mac:
```bash
scp -P <port> -i ~/.ssh/runpod \
    scripts/generate_audiobook_chatterbox.py \
    root@<pod-ip>:/workspace/
```

On the pod:
```bash
cd /workspace
python generate_audiobook_chatterbox.py book.pdf book.mp3 \
    --model /workspace/chatterbox-fi \
    --device cuda
```

Expect ~35–40 minutes of wall-clock for a 6-hour book. Watch `nvidia-smi`
in another SSH session to confirm the GPU is actually pegged (>90% util).

### 7. Download the MP3

From your Mac:
```bash
scp -P <port> -i ~/.ssh/runpod \
    root@<pod-ip>:/workspace/book.mp3 ./book.mp3
```

### 8. **TEAR DOWN** (critical — don't skip)

1. Back in the RunPod web UI → **Pods**.
2. Click the **Stop** button on your pod — this halts billing for GPU time.
3. Click **Terminate** — this deletes the volume. **Do this** unless you
   plan to reuse the pod within 24h. Stopped pods still bill for disk
   (~$0.10/GB/month).
4. Verify on the dashboard that no pods show "Running" or "Stopped".

**Estimated total spend**: $0.35–$0.50.

## Safety checklist — avoid the $200 surprise

- [ ] Set a **spending limit** in RunPod → Billing (e.g. $5/month).
- [ ] Set a **pod auto-stop timer** when deploying (e.g. 2 hours).
- [ ] After download, immediately **Terminate** the pod, not just Stop.
- [ ] Turn off auto-recharge on your payment method.
- [ ] Bookmark the Pods page; check it before you close your laptop.
- [ ] If you see >24h of unattended runtime: something is wrong.

## Gotchas

- **Community Cloud = interruptible-ish.** Hosts are individuals. Rare,
  but a pod can disappear. For a 1-hour job, acceptable risk. Use Secure
  Cloud ($0.59/hr) if you're paranoid.
- **First model download is slow.** Budget 2–5 extra minutes.
- **PDF parsing still runs on CPU** (trivial cost, but your script must
  not block the GPU waiting on pypdf).
- **Vast.ai hosts vary wildly.** Filter by DLPerf score and reliability
  >99%. Check disk I/O — some hosts have terrible SSDs.
- **Don't pip install in /workspace on RunPod** if using a persistent
  volume you plan to reuse — pip caches go to `~/.cache`, not the volume.

## Alternative: Modal serverless (for infrequent use)

If you'll generate <1 book per month, Modal's serverless model avoids
babysitting. Decorate a Python function with `@app.function(gpu="L4")`
and it spins up on demand, ~$1.25 per 6-hour book. Higher per-job cost,
zero idle cost, zero teardown risk. See
[modal.com/docs/examples](https://modal.com/docs/examples) for TTS
templates.

## Sources

- RunPod RTX 4090 pricing: https://www.runpod.io/gpu-models/rtx-4090
- Vast.ai RTX 4090 pricing: https://vast.ai/pricing/gpu/RTX-4090
- Modal pricing: https://modal.com/pricing
- Lambda pricing: https://lambda.ai/pricing
- Chatterbox RTF benchmark:
  https://github.com/devnen/Chatterbox-TTS-Server
