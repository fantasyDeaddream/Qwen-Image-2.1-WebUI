# `mixed` — W4 quantization, scheduler-managed placement

The same W4 weights as `mixed-vram`, but placement is left to ComfyUI's own memory scheduler
instead of being pinned by hand.

```
UNETLoader        qwen_image_2.1_mixed_balanced.safetensors     (W4, 3.91 GiB)
CLIPLoader        qwen3vl_8b_int8_convrot.safetensors           (int8, 8.71 GiB)
VAELoader         qwen_image_2.1_vae_bf16.safetensors           (BF16, 0.63 GiB)
    -> QwenImage21Cache
    -> KSampler   25 steps, CFG 1.0, euler / simple
```

## When to use it

- **12 GB VRAM or more.** With room to spare, letting ComfyUI decide is simpler and adapts to
  whatever else is using the GPU.
- **On 8 GB** prefer `mixed-vram`, which pins the 8.7 GiB text encoder into RAM explicitly.
  The automatic path can reach the same arrangement, but it is a heuristic rather than a
  guarantee.

Everything else — weights, graph, sampler settings, number of steps — is identical to
`mixed-vram`. Only the `--bf16-vae` / placement profile differs.

## Usage

```powershell
.\mixed\start.bat
.\mixed\stop.bat
```

Requires the quantized route: `setup_models_mixed.bat`.
