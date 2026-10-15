# `gguf` — Turbo GGUF Q4_K_M in an isolated ComfyUI

Runs the Turbo distillation inside **the same VRAM budget as the W4 route**, trading speed for
a smaller footprint on disk and in memory.

```
UNETLoaderGGUF    qwen_image_2.1_turbo_Q4_K_M.gguf              (3.90 GiB)
    -> QwenImage21Cache
    -> KSampler   8 steps, CFG 1.0, euler / simple
```

Placement is identical to `mixed-vram` and `turbo`: **text encoder in RAM**, **diffusion model
in VRAM**, **VAE migrated to VRAM for decoding**.

---

## It works, and it is slower — here is why

Measured on the same ComfyUI build, same prompt, same 8 steps:

| Quantization | Per step |
|---|---|
| Native W4 (fused) | 1.20 s/it |
| GGUF Q4_K_M | 3.53 s/it |

**About 2.9× slower per step.** This is architectural, not a version problem — a control run on
the identical ComfyUI build produced the same gap, which rules out the isolated instance.

The difference is in the execution path:

- **Native W4** dispatches to a fused int8 kernel
  (`comfy/ops.py` → `quant_ops.ck.int8_linear` for the `asym_w4a8_int8` format).
- **GGUF** dequantizes the tensor and then calls `torch.nn.functional.linear`
  (`ComfyUI-GGUF/ops.py`), so every step pays the dequantization cost.

VRAM usage is as expected — roughly 6.9 GB against 6.9 GB for the W4 route — so the GGUF path
buys you nothing in VRAM. Choose it only when disk space or the smaller resident footprint
matters more than sampling speed.

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
