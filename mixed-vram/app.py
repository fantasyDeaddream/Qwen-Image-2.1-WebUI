#!/usr/bin/env python
"""Qwen-Image-2.1 Mixed-VRAM strategy -- quantized W4 model, explicit placement.

This is the quantized counterpart of the native "32+8G" recipe. It reuses the
already-downloaded ``qwen_image_2.1_mixed_balanced`` checkpoint but stops
letting ComfyUI guess where things live:

    Qwen3-VL text encoder (INT8, 8.71 GiB) -> system RAM, encoded on CPU
                                              (``CLIPLoader.device = "cpu"``)
    diffusion model (W4, 3.91 GiB)        -> VRAM, resident and not evicted
    Qwen Image VAE (0.63 GiB)             -> VRAM, in BF16 (``--bf16-vae``)
    prefix KV cache                       -> pinned to the GPU
                                              (``QwenImage21Cache(device="gpu")``)

Why this is worth having: the quantized checkpoint is only ~3.9 GiB, so it fits
an 8 GB card with room for the latents and attention workspace. ComfyUI's
default heuristics would still page it out whenever the encoder wants the card,
which costs a full weight reload per step group. Pinning the encoder -- the one
thing that is genuinely 8.7 GiB -- to RAM removes that churn entirely.

The VAE deliberately stays on the **GPU**, in bf16 rather than ComfyUI's
default fp32: 0.63 GiB instead of 1.29 GiB, and GPU decoding instead of the CPU
decode that dominated an earlier measurement. Resident total is
3.91 + 0.63 = 4.54 GiB. Set ``QWEN_VAE_IN_VRAM=0`` to move it back to the CPU.

Run:  ..\\start.bat   (or)  python app.py [--api-only] [--no-browser]
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = next((p / "shared" for p in [_HERE, *_HERE.parents]
                if (p / "shared" / "qwen_comfy_app.py").is_file()), None)
if _SHARED is None:
    raise SystemExit("找不到 shared\\qwen_comfy_app.py，请把脚本放回项目目录树内。")
sys.path.insert(0, str(_SHARED))

import os  # noqa: E402

from qwen_api import JobRunner, build_app, load_images, serve  # noqa: E402
from qwen_comfy_app import Strategy, build_ui, capabilities  # noqa: E402

STRATEGY = "mixed-vram"
HOST = os.environ.get("QWEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_PORT", "7860"))

strategy = Strategy(STRATEGY)

if __name__ == "__main__":
    api_only = "--api-only" in sys.argv
    try:
        strategy.ensure_backend()
        keys = strategy.verify_nodes()
        print(f"[comfy] reference slots: {len(keys)} ({keys[0]} .. {keys[-1]})", flush=True)
        total, free = strategy.client.vram()
        if total:
            print(f"[comfy] VRAM {total/1024**3:.2f} GiB total, "
                  f"{free/1024**3:.2f} GiB free", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] ComfyUI 后端尚未就绪：{type(exc).__name__}: {exc}", flush=True)
        print("[WARN] 首次生成时会再试一次。", flush=True)

    def generate(params: dict, progress):
        refs = load_images(params.pop("images", None)) + load_images(params.pop("image", None))
        params["refs"] = list(params.get("refs") or []) + refs
        return strategy.run(params, progress)

    runner = JobRunner(generate)
    app = build_app(
        strategy=STRATEGY,
        runner=runner,
        capabilities=capabilities(strategy),
        health=lambda: {
            "backend": "comfyui",
            "comfy_dir": str(strategy.paths.comfy_dir),
            "comfy_online": strategy.client.alive(timeout=3),
            "models": strategy.models,
            "placement": strategy.profile["placement"],
        },
        gradio_app=None if api_only else build_ui(strategy),
        title="Qwen-Image-2.1 mixed-vram strategy",
        # Models stay loaded between requests; only transient buffers are freed.
        on_free=lambda: strategy.client.free_cache(unload_models=False),
        # Stop scripts call /api/shutdown, which lands here and tears down the
        # ComfyUI child process so no VRAM is left held by an orphan.
        on_shutdown=lambda: (
            strategy.client.unload_models(),
            strategy.client.stop(),
        ),
        pid_dir=strategy.paths.output_dir / "run",
    )
    print(f"\nWebUI + API: http://{HOST}:{PORT}/   (API docs at /docs)\n", flush=True)
    serve(app, HOST, PORT, inbrowser=not api_only and "--no-browser" not in sys.argv)
