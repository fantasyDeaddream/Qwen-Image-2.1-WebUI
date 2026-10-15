# `turbo` — W4 + Turbo LoRA, 8-step sampling

Adds the **0.85 GiB Turbo distillation LoRA** on top of the existing 3.91 GiB W4 quantized
model, cutting sampling from 25 steps to 8 (roughly 3× less denoising time) without
downloading a second full model.

```
UNETLoader        qwen_image_2.1_mixed_balanced.safetensors      (W4)
    -> LoraLoaderModelOnly   qwen_image_2.1_turbo_lora_...bf16   strength 1.0
    -> QwenImage21Cache
    -> KSampler      8 steps, CFG 1.0, euler / simple
```

Placement matches `mixed-vram`: the Qwen3-VL text encoder (the only genuinely large component
at 8.7 GiB) stays in RAM, the diffusion model and the BF16 VAE stay resident in VRAM.

---

## The sigma schedule is the part that matters

The Turbo LoRA ships with an **8-step sigma grid**, and ComfyUI cannot read it out of a
checkpoint or a LoRA. It is therefore carried explicitly in `shared/qwen_comfy.py` and fed to
the sampler through a `ManualSigmas` node:

```
1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0
```

Substituting ComfyUI's `simple` scheduler at the same step count gives a visibly different
curve — mean absolute difference 0.148, against 0.031 for `linear_quadratic`:

```
simple           1.0, 0.933, 0.857, 0.769, 0.666, 0.545, 0.399, 0.222, 0
turbo (exact)    1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0
```

### What that costs, measured

On a pocket-watch prompt at 8 steps, plain W4 with `simple` shows degraded detail — the
mechanism turns mushy and the roman numerals garble — while the exact Turbo grid at 8 steps is
hard to distinguish from the 25-step baseline.

Speed, on an RTX 4060 Laptop (8188 MiB), steady state at 1024 × 1024:

| Configuration | Wall time | Per step |
|---|---|---|
| W4, 25 steps | 38.98 s | 1.48 s/it |
| Turbo LoRA, 8 steps, `simple` | 14.04 s | 1.15 s/it |
| Turbo LoRA, 8 steps, exact grid | 13.75 s | 1.48 s/it |
| W4, 8 steps | 13.69 s | 1.46 s/it |

Note what this says: **all 8-step configurations cost the same.** The speed-up comes from the
step count; the LoRA is what keeps quality acceptable at that count. The LoRA is close to free
in both disk (0.85 GiB) and runtime.

---

## Usage

```powershell
.\turbo\start.bat      # starts the backend and opens the browser
.\turbo\stop.bat       # stops it and releases VRAM
```

Requirements: the quantized route installed (`setup_models_mixed.bat -WithGguf`) and ComfyUI
present.

## What this strategy does not change

- The sampling code path, the cache node, or the placement rules — all shared with
  `mixed-vram`.
- Anything outside this folder. `turbo/app.py` only supplies a profile: 8 steps, the LoRA, the
  sigma grid and `--bf16-vae`. That is why the 25-step routes are unaffected.
