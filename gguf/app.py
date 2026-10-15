#!/usr/bin/env python
"""Qwen-Image-2.1 **Turbo GGUF** strategy -- same placement, different quant family.

    text encoder (Qwen3-VL-8B, 8.7 GiB) -> system RAM / CPU
    diffusion model (GGUF Q4_K_M, 3.90 GiB) -> VRAM, resident
    VAE (BF16, 0.63 GiB) -> starts in RAM, migrated into VRAM for the decode

That is exactly the ``mixed-vram`` placement. The differences are only *which*
diffusion weights are used and *where they come from*:

* ``UnetLoaderGGUF`` (ComfyUI-GGUF) instead of ``UNETLoader``, reading
  ``qwen_image_2.1_turbo_Q4_K_M.gguf`` -- 3.90 GiB, i.e. the same footprint as
  the 3.91 GiB W4 "mixed" checkpoint, so the VRAM budget is unchanged.
* 8 steps at CFG 1.0, which is the Turbo distillation recipe (versus 25 steps
  for the base model).

Isolation -- this strategy deliberately does **not** touch the shared runtime:

* ComfyUI itself lives in ``gguf\\ComfyUI`` (v0.39.0), a separate checkout that
  belongs to this folder. The primary ``ComfyUI\\`` that serves ``mixed`` /
  ``mixed-vram`` / ``turbo`` is never written to.
* ``ComfyUI-GGUF`` is installed only into that isolated checkout's
  ``custom_nodes``, so a problem there cannot break the other strategies.
* The text encoder, VAE and LoRAs are **not duplicated**: the isolated
  checkout's ``extra_model_paths.yaml`` references the primary models directory
  read-only.
* The backend runs on port 8189 so it can coexist with the primary one.

Run:  ..\\start.bat   (or)  python app.py [--api-only] [--no-browser]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = next((p / "shared" for p in [_HERE, *_HERE.parents]
                if (p / "shared" / "qwen_comfy_app.py").is_file()), None)
if _SHARED is None:
    raise SystemExit("找不到 shared\\qwen_comfy_app.py，请把脚本放回项目目录树内。")

# Pin the isolated checkout explicitly. Without this the project-wide env cache
# would hand back the PRIMARY ComfyUI, which has no CustomNode-GGUF installed.
_ISOLATED = _HERE / "ComfyUI"
if _ISOLATED.is_dir():
    os.environ["QWEN_COMFYUI_DIR"] = str(_ISOLATED)

sys.path.insert(0, str(_SHARED))

from qwen_api import JobRunner, build_app, load_images, serve  # noqa: E402
from qwen_comfy_app import Strategy, build_ui, capabilities  # noqa: E402

STRATEGY = "gguf"
HOST = os.environ.get("QWEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_PORT", "7860"))

strategy = Strategy(STRATEGY)

if __name__ == "__main__":
    api_only = "--api-only" in sys.argv
    try:
        strategy.ensure_backend()
        keys = strategy.verify_nodes()
        print(f"[comfy] isolated dir : {strategy.paths.comfy_dir}", flush=True)
        print(f"[comfy] port         : {strategy.client.port}", flush=True)
        print(f"[gguf] diffusion     : {strategy.models.get('unet')}", flush=True)
        print(f"[gguf] reference slots: {len(keys)} ({keys[0]} .. {keys[-1]})", flush=True)
        print(f"[gguf] defaults      : {strategy.profile.get('steps_default')} steps, "
              f"cfg {strategy.profile.get('cfg_default')}", flush=True)
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
            "comfy_port": strategy.client.port,
            "comfy_online": strategy.client.alive(timeout=3),
            "models": strategy.models,
            "placement": strategy.profile["placement"],
            "gguf": {
                "file": strategy.models.get("unet"),
                "loader": strategy.profile.get("unet_loader"),
                "steps_default": strategy.profile.get("steps_default"),
                "cfg_default": strategy.profile.get("cfg_default"),
            },
        },
        gradio_app=None if api_only else build_ui(strategy),
        title="Qwen-Image-2.1 turbo-gguf strategy",
        on_free=lambda: strategy.client.free_cache(unload_models=False),
        on_shutdown=lambda: (
            strategy.client.unload_models(),
            strategy.client.stop(),
        ),
        pid_dir=strategy.paths.output_dir / "run",
    )
    print(f"\nWebUI + API: http://{HOST}:{PORT}/   (API docs at /docs)\n", flush=True)
    serve(app, HOST, PORT, inbrowser=not api_only and "--no-browser" not in sys.argv)
