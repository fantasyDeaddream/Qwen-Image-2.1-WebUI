# Qwen-Image-2.1 WebUI

A Windows launcher and multi-strategy runtime for running **Qwen-Image-2.1** locally —
built so that an 8 GB laptop GPU can produce the same images a 24 GB card would.

> [中文说明 / Chinese README](README.zh-CN.md)

---

## What this is

Qwen-Image-2.1 is a large image model: the full BF16 weights are about **30 GB** across a
13.25 GB transformer, a 16.33 GB Qwen3-VL text encoder and a 0.63 GB VAE. On paper that
puts it well out of reach of a typical gaming laptop.

This project makes it fit. It does four things:

1. **Splits the model across the two memory pools you actually have.** The text encoder is
   used once per prompt and then sits idle, so it lives in RAM; the diffusion transformer
   and the VAE run on the GPU. You keep the quality of the native model without paying for
   VRAM you do not own.
2. **Offers five interchangeable run strategies** — quantized, native, distilled (Turbo) or
   GGUF — so you can trade disk, VRAM and speed against each other.
3. **Ships a single-file launcher** that detects your hardware, tells you which strategy
   suits it, downloads the models and starts the WebUI. No command line required.
4. **Adds an AI agent** that turns a plain-language request plus a few reference images
   into a precise, structured drawing job for the local model.

The GUI and the command line drive the same PowerShell scripts, so the two cannot drift
apart.

---

## Requirements

| | |
|---|---|
| OS | Windows 10 / 11, 64-bit |
| GPU | NVIDIA, 8 GB VRAM or more (CUDA 13.0 wheels) |
| RAM | 16 GB minimum, 32 GB comfortable |
| Disk | ~15 GB for the quantized route, ~31 GB for native BF16 |
| Network | required on first run (Python, ComfyUI and models are downloaded) |

Python and Git do **not** need to be installed beforehand — the setup scripts bootstrap
both.

---

## Quick start

### Option A — use the prebuilt launcher (recommended)

1. Download `QwenImageLauncher.exe` from the [latest release](../../releases/latest).
2. Put it in a folder of its own and run it. Windows SmartScreen will warn about an
   unsigned binary — choose **More info → Run anyway**.
3. Work through the five steps in the sidebar. Step 3 marks the route it recommends for
   your hardware with a ★.
4. When the backend is up the browser opens automatically. Press **使用说明** in the title
   bar for the built-in guide.

On first run the executable unpacks its own scripts next to itself: PowerShell and the
project virtualenv need real file paths, so they cannot live inside the binary. Replacing
the `.exe` later refreshes only those scripts — your virtualenv, ComfyUI, models and
outputs are left untouched.

### Option B — run from source

```powershell
git clone https://github.com/fantasyDeaddream/Qwen-Image-2.1-WebUI.git
cd Qwen-Image-2.1-WebUI

# one-time setup, in this order
.\setup\setup_env.bat            # virtualenv + torch 2.10.0 / cu130 + app deps
.\setup\setup_comfyui.bat        # ComfyUI, patched for Qwen-Image-2.1
.\setup\setup_models_mixed.bat   # ~14 GB of quantized weights + Turbo LoRA

# start a strategy
.\turbo\start.bat                # or mixed\ , mixed-vram\ , native\ , gguf\
```

There is also an interactive installer that detects an existing environment and lets you
pick a route:

```powershell
.\setup\setup_wizard.bat
```

---

## Run strategies

Each folder is one self-contained way of running the model. They differ in what lives
where, not in the code that draws the picture.

| Folder | What it does | Peak VRAM | Disk | Notes |
|---|---|---|---|---|
| `mixed-vram` | W4 quantization, explicit placement: encoder in RAM, diffusion + VAE in VRAM | ~7.0 GB | 3.9 GB | **best default for 8 GB cards** |
| `mixed` | Same W4 weights, placement left to ComfyUI's scheduler | ~7.0 GB | 3.9 GB | more headroom on 12 GB+ |
| `turbo` | W4 + the Turbo LoRA, 8-step sampling | ~7.0 GB | 4.8 GB | **recommended** — see below |
| `native` | Full BF16 through `diffusers` | >16 GB | 30.2 GB | highest quality ceiling |
| `gguf` | Turbo GGUF Q4_K_M in an isolated ComfyUI instance | ~6.9 GB | 3.9 GB | 2.9× slower **and lower quality** — 4–6 GB fallback only, see `gguf/README.md` |

### About Turbo

The Turbo LoRA ships with an 8-step sigma schedule that ComfyUI cannot read from a
checkpoint or a LoRA, so `shared/qwen_comfy.py` carries it explicitly:

```
1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0
```

Without it, ComfyUI's `simple` scheduler at 8 steps uses a noticeably different curve (mean
absolute difference 0.148 versus 0.031 for `linear_quadratic`). At 8 steps on a
pocket-watch prompt, plain W4 shows mushy mechanisms and garbled roman numerals, while
Turbo at 8 steps is hard to tell apart from the full 25-step baseline.

---

## Performance

Measured on an **RTX 4060 Laptop (8188 MiB), 31.7 GB RAM, i9-13900HX**, using ComfyUI's own
per-run timings. "Steady state" means the second of two consecutive identical runs, so
model staging is not counted.

### 1024 × 1024

| Configuration | Wall time | Per step |
|---|---|---|
| W4, 25 steps (baseline) | 38.98 s | 1.48 s/it |
| Turbo LoRA, 8 steps, `simple` | 14.04 s | 1.15 s/it |
| Turbo LoRA, 8 steps, exact grid | 13.75 s | 1.48 s/it |
| W4, 8 steps | 13.69 s | 1.46 s/it |

All 8-step configurations cost the same. **The ~2.85× speed-up comes from the step count,
not from the LoRA** — the LoRA is what keeps quality acceptable at that step count.

### Memory behaviour

- The **VAE is not resident during denoising**. It is loaded at decode time, so peak VRAM
  during sampling (~6.9 GB) is lower than during decoding (~7.5 GB).
- The **text encoder stays in RAM**, not VRAM, in the `mixed-vram` route.
- **Prefix/KV cache placement has no measurable effect** on either the image or the
  runtime. Note that ComfyUI disables the prefix cache automatically whenever LoRA patches
  are attached.

### GGUF is slower per step — and lower quality

Running the same ComfyUI build with both quantizations, W4 executes at **1.20 s/it** and
GGUF at **3.53 s/it** — about 2.9× slower. This is architectural rather than a version
problem: the native W4 path dispatches to a fused int8 kernel, whereas the GGUF path
dequantizes the tensor and then calls `torch.nn.functional.linear`.

It is not only a speed trade-off. unsloth's published LPIPS for Turbo Q4_K_M is **0.153**
against **0.037** for the INT8 build — roughly **4× worse** (AlexNet, 8 steps). Q5_K_M (0.074)
and Q6_K (0.055) close part of the gap but stay behind; only Q8_0 (0.036) matches INT8, at
which point the file is no longer smaller. GGUF also uses about the same VRAM as the W4 route,
so it saves neither VRAM nor disk.

Treat `gguf` as a control route and as a fallback for 4–6 GB cards only — not as an option for
6–8 GB machines, where `mixed-vram` or `turbo` is strictly better. Details in
[`gguf/README.md`](gguf/README.md).

---

## Transparency

Qwen-Image-2.1 can emit a real RGBA image. No graph changes and no post-processing are
needed — the model card's phrasing does the work:

> `This is an RGBA image with transparency. {subject}. The image has alpha channel and the background is transparent.`

With that prefix a "remove the background" prompt produces genuine transparency (a measured
21% of pixels fully transparent on a test subject); with a bare prompt it produces none.
Both the WebUI and the HTTP API expose this as a `transparent` flag.

---

## The AI agent

Step 4 of the launcher includes an agent panel that turns loose intent into an exact job.
It talks to **DeepSeek** or **Zhipu GLM** over their OpenAI-compatible APIs.

- **The model list is fetched live** from `GET /models` — nothing is hard-coded.
- **Vision capability is probed, not guessed.** A 1×1 image is sent with `max_tokens=1`;
  GLM answers a text-only model with `messages.content.type 参数非法，取值范围 ['text']`.
  Name heuristics are unreliable in both directions — `deepseek-flash` and `glm-5.3-flash`
  accept images despite carrying no `v` in their names, while `glm-4.6v` and `glm-5v-turbo`
  accept them yet do not appear in `GET /models` at all.
- Models are shown in three tiers: **visually verified**, **unverified but selectable**, and
  **excluded with a stated reason** (OCR and other fixed-output endpoints, embeddings,
  speech, image generators). A model is only excluded when there is positive evidence
  against it; inability to confirm is never treated as evidence.
- The agent returns one JSON object — mode (`text2image` / `edit` / `reference`), a
  rewritten prompt, width and height, transparency, latent mode, image count, and which
  attached picture is the edit target. The result is validated and clamped before use:
  dimensions snap to multiples of 32, the pixel budget is capped, count is bounded, and
  out-of-range image indices fall back to the first picture.

API keys are stored with Windows DPAPI (tied to your user account on that machine) plus a
SHA-256 fingerprint for identification. The key itself is never written in the clear, and
if DPAPI is unavailable it is simply not persisted.

---

## HTTP API

Every strategy serves a FastAPI app alongside the Gradio WebUI:

```
GET  /api/health                 liveness + queue depth
GET  /api/capabilities           what this strategy loaded and where
POST /api/generate               queue a job, returns {"job_id": ...}
GET  /api/jobs/{job_id}          poll status, progress and result metadata
GET  /api/jobs/{job_id}/image    the rendered PNG
POST /api/free                   drop models and release VRAM
POST /api/shutdown               ask the backend to exit
```

Interactive docs at `http://127.0.0.1:7860/docs`. The launcher drives this same API, which
is why the frozen executable does not need to bundle torch.

---

## Project layout

```
launcher/          PyQt5 front-end: detection, install, run, AI agent, gallery
  app.py           window, five pages, console
  core.py          discovery, process control, secrets, HTTP drawing
  agent.py         provider SDK, capability probing, plan validation
  agent_panel.py   agent UI and the result gallery
  tests/           offline unit tests for the agent layer (104 checks, no network, no API key)
shared/            the library every strategy shares
  QwenEnv.ps1      path discovery, environment cache, mirror config
  qwen_comfy.py    graph builder, official samplers, Turbo sigma grid
  qwen_comfy_app.py  the ComfyUI-backed runtime
  qwen_api.py      FastAPI surface
  qwen_paths.py    portable model discovery
  qwen_sizing.py   resolution and pixel-budget rules
setup/             installation: environment, ComfyUI, models, interactive wizard
mixed/ mixed-vram/ turbo/ gguf/ native/    one run strategy each
tests/             verification scripts (see tests/README.md)
```

---

## Troubleshooting

**SmartScreen blocks the executable.** It is not code-signed. *More info → Run anyway.*

**Double-clicking does nothing.** Run it from a terminal (`cmd`, then
`QwenImageLauncher.exe`) so the traceback is visible.

**A download stopped partway.** Re-run the same install step. The installers verify every
artifact against its known size, so a truncated file is detected and re-fetched instead of
being mistaken for a finished one.

**Not enough VRAM, or very slow.** Use the ★-marked route in step 3 and keep the resolution
near 1024×1024. Reference images are expensive: high-resolution multi-image jobs
substantially extend runtime.

**Everything reports "missing" after moving the folder.** Point step 2 at the new
locations, or delete `launcher_state.json` next to the executable to start clean.

---

## Credits

- [Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) — the model, by the Qwen team
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — the inference engine
- Qwen-Image-2.1-Turbo distillation by the [Unsloth](https://huggingface.co/unsloth) and
  [Abiray](https://huggingface.co/Abiray) projects
- Model weights are downloaded from [ModelScope](https://www.modelscope.cn) at install time
  and are **not** redistributed here

## License

[MIT](LICENSE). The model weights carry their own license — check the Qwen-Image-2.1 model
card before any commercial use.
