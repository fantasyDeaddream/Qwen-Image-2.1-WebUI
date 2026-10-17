# `gguf` — Turbo GGUF Q4_K_M in an isolated ComfyUI

> **This is not a general-purpose option.** It is kept as a comparison/control route and as a
> last resort for machines that cannot fit anything else (roughly 4–6 GB VRAM). It is about
> **2.9× slower per step** than the W4 route *and* it is **visibly lower quality**. On a
> 6–8 GB card use `mixed-vram` or `turbo` instead.

Runs the Turbo distillation in a separate ComfyUI instance on port 8189, inside roughly the
same VRAM budget as `mixed-vram`:

```text
UNETLoaderGGUF    qwen_image_2.1_turbo_Q4_K_M.gguf              (3.90 GiB)
    -> QwenImage21Cache
    -> KSampler   8 steps, CFG 1.0, euler / simple
```

Placement is identical to `mixed-vram` and `turbo`: **text encoder in RAM**, **diffusion model
in VRAM**, **VAE migrated to VRAM for decoding**.

---

## The quality cost

Quantization quality is measured as LPIPS (lower is better). unsloth publishes the following
for Turbo GGUF (AlexNet, same seed, 8 steps, CFG 1), next to the INT8 ConvRot build of the same
model:

| Weights | LPIPS ↓ |
|---|---|
| GGUF Q4_K_M (this route) | **0.153** |
| GGUF Q5_K_M | 0.074 |
| GGUF Q6_K | 0.055 |
| GGUF Q8_0 | 0.036 |
| INT8 ConvRot | **0.037** |

Q4_K_M — the file used here — is roughly **4× worse** than INT8. Q5/Q6 improve steadily but
stay behind; only Q8_0 catches up with INT8, and by then it is no smaller on disk. GGUF
compression is not "the same quality, a bit slower": it is a different point on the quality
curve.

Source: <https://huggingface.co/unsloth/Qwen-Image-2.1-Turbo-GGUF>. The quantized file this
project downloads is Abiray's Q4_K_M (4,189,346,592 B), a file of the same quantization family;
the LPIPS table is unsloth's published measurement.

## The speed cost

Measured on the same ComfyUI build, same prompt, same 8 steps:

| Quantization | Per step |
|---|---|
| Native W4 (fused) | 1.20 s/it |
| GGUF Q4_K_M | 3.53 s/it |

**About 2.9× slower per step.** This is architectural, not a version problem — a control run on
the identical ComfyUI build produced the same gap, which rules out the isolated instance.

- **Native W4** dispatches to a fused int8 kernel
  (`comfy/ops.py` → `quant_ops.ck.int8_linear` for the `asym_w4a8_int8` format).
- **GGUF** dequantizes the tensor and then calls `torch.nn.functional.linear`
  (`ComfyUI-GGUF/ops.py`), so every step pays the dequantization cost.

VRAM use is not lower either: roughly 6.9 GB, the same as the W4 route. The download is
3.9 GB, also the same. The GGUF path therefore buys neither VRAM nor disk over the default
route — only a different (worse) trade-off.

## Where it can still be justified

- **4–6 GB cards.** Q4_K_S / Q4_K_M plus a CPU VAE is the only way to keep 1024 × 1024 inside
  6 GB, and the margin is a few tens of megabytes at best; Q3_K_M is the safe choice. A 4 GB
  card is limited to Q2_K at 768² or below.
- **As a control.** It isolates the effect of the quantization format on quality and speed
  when comparing the other routes.

---

## First-time setup: the isolated runtime

```powershell
setup_models_mixed.bat -WithGguf
```

This clones the GGUF weights and prepares a **separate ComfyUI 0.39.0 checkout** under
`gguf/ComfyUI` with `ComfyUI-GGUF` installed. It is kept apart from the main ComfyUI so the
two never interfere:

- the main `ComfyUI/custom_nodes` keeps only its example files
- only the isolated instance carries `ComfyUI-GGUF`
- the main `models/diffusion_models` and `models/unet` keep only the original W4 file

Nothing above is modified inside `gguf/ComfyUI`, so you can delete that folder at any time and
re-create it.

## Usage

```powershell
.\gguf\start.bat      # starts on port 8189, not 7860
.\gguf\stop.bat
```

Port **8189** is deliberate: the isolated instance can run alongside a main-strategy backend
without a conflict. Logs go to `outputs/comfyui_backend_gguf.log` so two backends never share
one file.

## Requirements

The quantized route (`setup_models_mixed.bat -WithGguf`). The isolated runtime is created by
that script; if it is missing, the launcher reports the component as not installed and points
here.
