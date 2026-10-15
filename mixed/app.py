#!/usr/bin/env python
"""Qwen-Image-2.1 Mixed strategy -- quantized W4 model, WebUI + HTTP API.

The diffusion model is the already-downloaded ``qwen_image_2.1_mixed_balanced``
checkpoint (3.91 GiB). Placement is left to ComfyUI, which pages the text
encoder in and out as VRAM allows. Use the sibling ``mixed-vram`` strategy when
you would rather pin the encoder to RAM and keep the diffusion model resident.

Features (identical across all strategies):
  * arbitrary output resolution, limited only by a maximum pixel product;
  * several reference images plus a prompt producing one image, in both the
    WebUI and the API;
  * ``latent_mode`` chooses between a custom output size and an edit anchored
    to the first reference image.

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

STRATEGY = "mixed"
HOST = os.environ.get("QWEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_PORT", "7860"))

strategy = Strategy(STRATEGY)

if __name__ == "__main__":
    api_only = "--api-only" in sys.argv
    # Starting the backend up front turns a misconfiguration into a clear
    # startup error instead of a failure on the first click.
    try:
        strategy.ensure_backend()
        keys = strategy.verify_nodes()
        print(f"[comfy] reference slots: {len(keys)} ({keys[0]} .. {keys[-1]})", flush=True)
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
        title="Qwen-Image-2.1 mixed strategy",
        on_free=lambda: strategy.client.free_cache(unload_models=False),
        on_shutdown=lambda: (
            strategy.client.unload_models(),
            strategy.client.stop(),
        ),
        pid_dir=strategy.paths.output_dir / "run",
    )
    print(f"\nWebUI + API: http://{HOST}:{PORT}/   (API docs at /docs)\n", flush=True)
    serve(app, HOST, PORT, inbrowser=not api_only and "--no-browser" not in sys.argv)
