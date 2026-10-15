# `mixed-vram` — W4 quantization, explicit placement

The default route for an 8 GB NVIDIA card. Quantized weights, with each component placed by
hand into the memory pool where it belongs.

```
UNETLoader        qwen_image_2.1_mixed_balanced.safetensors     (W4, 3.91 GiB)
CLIPLoader        qwen3vl_8b_int8_convrot.safetensors           (int8, 8.71 GiB)
VAELoader         qwen_image_2.1_vae_bf16.safetensors           (BF16, 0.63 GiB)
    -> QwenImage21Cache
    -> KSampler   25 steps, CFG 1.0, euler / simple
```

## Placement

| Component | Where | Why |
|---|---|---|
| Qwen3-VL text encoder | **RAM / CPU** | 8.7 GiB, used once per prompt, then idle |
| Diffusion transformer (W4) | **VRAM** | touched on every step |
| VAE | **VRAM, BF16** | loaded at decode time only |

The text encoder is the single largest component and the least frequently used, so moving it
off the GPU is what makes the whole thing fit. Measured peak on an 8 GiB card: about 6.9 GB
during sampling and 7.5 GB during decoding, because the VAE is **not** resident while
denoising.

## Why explicit placement rather than automatic

`mixed` lets ComfyUI's scheduler decide. That works, but leaves the outcome to a heuristic;
this route pins each component so the memory profile is predictable and reproducible. On
12 GB or more, `mixed` is the more convenient choice.

## Usage

```powershell
.\mixed-vram\start.bat
.\mixed-vram\stop.bat
```

Requires the quantized route: `setup_models_mixed.bat`.
