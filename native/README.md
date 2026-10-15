# `native` — full BF16 through `diffusers`

The uncompromised route: the complete BF16 model, no quantization and no distillation. It runs
through the `diffusers` pipeline rather than ComfyUI's graph executor.

| Component | Size |
|---|---|
| Transformer | 13.25 GB |
| Qwen3-VL-8B text encoder | 16.33 GB |
| VAE | 0.63 GB |
| **Total on disk** | **~30.2 GB** |

## When to use it

This is the quality ceiling, and it is also the most demanding route:

- **VRAM: more than 16 GB** in practice. On an 8 GB card it will spill into shared memory and
  become extremely slow, or fail outright.
- **Disk: ~31 GB** on top of everything else.
- **Downtime:** the first prompt after a start pays a substantial model-staging cost.

If you are on 8–12 GB, one of the quantized routes will give you a far better experience for a
difference that is often invisible. Reach for `native` when you are chasing maximum fidelity,
or when you want to compare a quantized result against ground truth.

ComfyUI is still required even though inference runs through `diffusers`: the tokenizer and
several graph components come from there.

## Usage

```powershell
.\native\start.bat
.\native\stop.bat
```

Requires the full BF16 weights: `setup_models_native.bat` (~30 GB from ModelScope).
