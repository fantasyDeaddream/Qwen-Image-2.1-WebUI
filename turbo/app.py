#!/usr/bin/env python
"""Qwen-Image-2.1 Turbo strategy -- 8-step distillation as a LoRA.

Instead of downloading a second diffusion checkpoint (Turbo BF16 is 13.25 GiB,
Turbo int8 is 6.76 GiB, versus the 3.91 GiB W4 model already on disk), this
strategy applies the **0.85 GiB Turbo LoRA** on top of the existing quantized
checkpoint:

    UNETLoader (qwen_image_2.1_mixed_balanced, W4)
        -> LoraLoaderModelOnly (qwen_image_2.1_turbo_lora_avg_rank_178_bf16)
        -> QwenImage21Cache
        -> KSampler   (8 steps, CFG 1.0, euler/simple -- the shipped Turbo recipe)

Placement is the same as ``mixed-vram``: the Qwen3-VL text encoder stays in
system RAM (it is the only genuinely large piece at 8.7 GiB), while the W4
diffusion model and the BF16 VAE live in VRAM.

Two things worth knowing:

* ComfyUI disables the Qwen prefix KV cache automatically once hook patches
  exist (``model_base.py`` calls ``reset_prefix_cache`` with
  ``len(patcher.hook_patches) == 0``), and a LoRA patch counts as a hook. That
  cache measured as a no-op for text-to-image anyway, so nothing is lost.
* Whether the Turbo LoRA stacks cleanly on the experimental W4 "mixed" format is
  what this folder exists to find out. LoRA patching reaches the quantized ops
  through ``set_weight``/``requantize_from_float`` (``comfy/ops.py:1492``), so it
  should apply rather than be silently dropped -- but requantization error is a
  real possibility and the output has to be compared against the base model.

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
sys.path.insert(0, str(_SHARED))

from qwen_api import JobRunner, build_app, load_images, serve  # noqa: E402
from qwen_comfy_app import Strategy, build_ui, capabilities  # noqa: E402

STRATEGY = "turbo"
HOST = os.environ.get("QWEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_PORT", "7860"))

strategy = Strategy(STRATEGY)

if __name__ == "__main__":
    api_only = "--api-only" in sys.argv
    try:
        strategy.ensure_backend()
        keys = strategy.verify_nodes()
        print(f"[comfy] reference slots: {len(keys)} ({keys[0]} .. {keys[-1]})", flush=True)
        print(f"[turbo] lora = {strategy.models.get('lora')}", flush=True)
        print(f"[turbo] defaults: {strategy.profile.get('steps_default')} steps, "
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
            "comfy_online": strategy.client.alive(timeout=3),
            "models": strategy.models,
            "placement": strategy.profile["placement"],
            "turbo": {
                "lora": strategy.models.get("lora"),
                "lora_strength": strategy.profile.get("lora_strength", 1.0),
                "steps_default": strategy.profile.get("steps_default"),
                "cfg_default": strategy.profile.get("cfg_default"),
            },
        },
        gradio_app=None if api_only else build_ui(strategy),
        title="Qwen-Image-2.1 turbo strategy",
        on_free=lambda: strategy.client.free_cache(unload_models=False),
        on_shutdown=lambda: (
            strategy.client.unload_models(),
            strategy.client.stop(),
        ),
        pid_dir=strategy.paths.output_dir / "run",
    )
    print(f"\nWebUI + API: http://{HOST}:{PORT}/   (API docs at /docs)\n", flush=True)
    serve(app, HOST, PORT, inbrowser=not api_only and "--no-browser" not in sys.argv)
