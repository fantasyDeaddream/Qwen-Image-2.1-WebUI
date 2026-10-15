#!/usr/bin/env python
"""Qwen-Image-2.1 native (diffusers) strategy -- WebUI + HTTP API.

Deployment recipe ("32+8G" placement, tuned for 8 GB VRAM / 32 GB RAM):

    Qwen3-VL text encoder -> CPU RAM, BF16
    Qwen Image transformer -> CUDA, FP8-E4M3 storage / BF16 compute,
                              last N blocks cycled through host RAM per forward
    Qwen Image VAE        -> CPU during denoising, CUDA only for the final decode

What this version adds over the previous single-file app:

* arbitrary output resolution -- only a maximum pixel product is enforced, and
  both sides are snapped to the model's 32-pixel grid (the pipeline requires
  ``vae_scale_factor * 2``);
* multiple reference images in one call, together with the prompt, producing a
  single image, while the original single-image edit path still works;
* an HTTP API with the same features as the WebUI, served by one FastAPI app
  that also hosts the Gradio UI.

Environment overrides: QWEN_FP8_TRANSFORMER, QWEN_USE_KV_CACHE,
QWEN_OFFLOAD_BLOCKS, QWEN_OFFLOAD_EMPTY_CACHE, QWEN_MAX_PIXELS, QWEN_MAX_REFS,
QWEN_HOST, QWEN_PORT, QWEN_DEVICE_INDEX.
"""

from __future__ import annotations

import gc
import inspect
import os
import random
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. locate the shared library: own folder, then parents
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_SHARED = None
for _cand in [_HERE, *_HERE.parents]:
    if (_cand / "shared" / "qwen_paths.py").is_file():
        _SHARED = _cand / "shared"
        break
if _SHARED is None:
    raise SystemExit(
        "找不到 shared\\qwen_paths.py。请把本脚本放回项目目录树内再运行。\n"
        "shared\\qwen_paths.py not found in this folder or any parent folder."
    )
sys.path.insert(0, str(_SHARED))

from qwen_comfy import transparent_prompt  # noqa: E402

# ---------------------------------------------------------------------------
# 2. environment hardening -- must happen before torch is imported
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "PYTORCH_CUDA_ALLOC_CONF",
    "expandable_segments:True,garbage_collection_threshold:0.8",
)
os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Offline by default: only the local cache is used. Set QWEN_ALLOW_DOWNLOAD=1
# to let the apps resolve missing files from the hub.
if os.environ.get("QWEN_ALLOW_DOWNLOAD", "0") != "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import gradio as gr  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

from qwen_api import JobRunner, build_app, load_images, serve  # noqa: E402
from qwen_paths import describe, initialize  # noqa: E402
import qwen_sizing  # noqa: E402

STRATEGY = "native"

# ---------------------------------------------------------------------------
# 3. resolve the shared assets (no downloads, ever, in this step)
# ---------------------------------------------------------------------------
PATHS = initialize(
    _HERE,
    need_venv=True,
    need_diffusers=True,
    need_models=True,
    need_weights=True,
)
print(describe(PATHS), flush=True)

MODEL_ID = os.environ.get("QWEN_MODEL_ID", "Qwen/Qwen-Image-2.1")
# Pinned revision: the cache's `main` ref points at a metadata-only snapshot,
# so following `main` would fail offline. See README.
MODEL_REVISION = os.environ.get(
    "QWEN_MODEL_REVISION", "b3179ad355be050328e483a9dfdd9e60cd62adfa"
)
CACHE_DIR = PATHS.cache_dir
OUTPUT_DIR = PATHS.output_dir

USE_FP8_TRANSFORMER = os.environ.get("QWEN_FP8_TRANSFORMER", "1").strip() != "0"
USE_KV_CACHE = os.environ.get("QWEN_USE_KV_CACHE", "0").strip() == "1"
OFFLOAD_BLOCKS = max(0, int(os.environ.get("QWEN_OFFLOAD_BLOCKS", "6")))
OFFLOAD_EMPTY_CACHE = os.environ.get("QWEN_OFFLOAD_EMPTY_CACHE", "0").strip() == "1"
# Reference images may also be encoded by the VAE and spliced into the denoiser
# as latent tokens. That is the high-fidelity edit path; when it is unavailable
# we fall back to text-encoder vision context only.
USE_REF_LATENTS = os.environ.get("QWEN_REF_LATENTS", "1").strip() != "0"
MAX_REFS = max(0, int(os.environ.get("QWEN_MAX_REFS", "4")))
HOST = os.environ.get("QWEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_PORT", "7860"))

CPU = torch.device("cpu")


def human_gib(value: float) -> str:
    return f"{value / (1024 ** 3):.2f} GiB"


def model_bytes(module) -> int:
    total = 0
    for p in module.parameters():
        total += p.numel() * p.element_size()
    for b in module.buffers():
        total += b.numel() * b.element_size()
    return total


def cuda_mem(prefix: str):
    if not torch.cuda.is_available():
        return
    try:
        print(
            f"[MEM] {prefix}: allocated={human_gib(torch.cuda.memory_allocated(DEVICE_INDEX))} "
            f"reserved={human_gib(torch.cuda.memory_reserved(DEVICE_INDEX))} "
            f"peak={human_gib(torch.cuda.max_memory_allocated(DEVICE_INDEX))}",
            flush=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 4. device + pipeline
# ---------------------------------------------------------------------------
def select_device():
    if not torch.cuda.is_available():
        raise RuntimeError(
            f"未检测到可用 CUDA GPU。PyTorch {torch.__version__} / CUDA {torch.version.cuda}"
        )
    wanted = os.environ.get("QWEN_DEVICE_INDEX", "").strip()
    count = torch.cuda.device_count()
    if wanted:
        index = int(wanted)
        if not 0 <= index < count:
            raise RuntimeError(f"QWEN_DEVICE_INDEX={index} 不存在，可见 GPU 数：{count}")
    else:
        index = max(range(count), key=lambda i: torch.cuda.get_device_properties(i).total_memory)
    torch.cuda.set_device(index)
    props = torch.cuda.get_device_properties(index)
    print(f"[GPU] [{index}] {props.name} | VRAM {human_gib(props.total_memory)}", flush=True)
    return torch.device(f"cuda:{index}"), index


DEVICE, DEVICE_INDEX = select_device()
DTYPE = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16

print("=" * 72, flush=True)
print("Qwen-Image-2.1 native strategy", flush=True)
print(f"  text encoder -> CPU ({DTYPE})", flush=True)
print(f"  transformer  -> {DEVICE} (FP8 storage={USE_FP8_TRANSFORMER})", flush=True)
print("  VAE          -> CPU except final decode", flush=True)
print(f"  model cache  -> {CACHE_DIR}", flush=True)
print(f"  revision     -> {MODEL_REVISION}", flush=True)
print("=" * 72, flush=True)

from diffusers import QwenImage21Pipeline  # noqa: E402

_load_kwargs = dict(
    dtype=DTYPE,
    cache_dir=str(CACHE_DIR),
    low_cpu_mem_usage=True,
)
if MODEL_REVISION:
    _load_kwargs["revision"] = MODEL_REVISION
if os.environ.get("QWEN_ALLOW_DOWNLOAD", "0") != "1":
    _load_kwargs["local_files_only"] = True

try:
    pipe = QwenImage21Pipeline.from_pretrained(MODEL_ID, **_load_kwargs)
except Exception as exc:  # noqa: BLE001
    raise SystemExit(
        f"加载模型失败 / failed to load {MODEL_ID}@{MODEL_REVISION}\n"
        f"  cache: {CACHE_DIR}\n"
        f"  error: {type(exc).__name__}: {exc}\n"
        "提示：如果模型尚未下载，请先运行 setup\\setup_models.bat（会先征求确认）。"
    ) from exc

pipe.text_encoder.to(CPU)
pipe.text_encoder.eval()
pipe.text_encoder.requires_grad_(False)

if USE_FP8_TRANSFORMER:
    if not hasattr(pipe.transformer, "enable_layerwise_casting"):
        raise SystemExit("当前 diffusers 不支持 enable_layerwise_casting()，请升级 diffusers。")
    print("[TRANSFORMER] FP8-E4M3 layerwise storage / BF16 compute", flush=True)
    pipe.transformer.enable_layerwise_casting(
        storage_dtype=torch.float8_e4m3fn,
        compute_dtype=DTYPE,
        non_blocking=False,
    )

pipe.transformer.to(DEVICE)
pipe.transformer.eval()
pipe.transformer.requires_grad_(False)

# ---- partial block offload: keep a VRAM margin without moving everything ----
_OFFLOAD_HANDLES = []
_OFFLOADED_BYTES = 0


def prepare_offload():
    global _OFFLOADED_BYTES
    if OFFLOAD_BLOCKS <= 0:
        print("[OFFLOAD] disabled", flush=True)
        return
    blocks = getattr(pipe.transformer, "transformer_blocks", None)
    if blocks is None:
        print("[OFFLOAD] transformer has no transformer_blocks; skipped", flush=True)
        return
    total = len(blocks)
    n = min(OFFLOAD_BLOCKS, total)
    for idx, block in enumerate(list(blocks)[-n:], start=total - n):
        size = model_bytes(block)
        _OFFLOADED_BYTES += size
        print(f"[OFFLOAD] block {idx}/{total - 1}: {human_gib(size)} storage", flush=True)
        block.to(CPU)

        def pre_hook(module, args):
            if next(module.parameters()).device != DEVICE:
                module.to(DEVICE)

        def post_hook(module, args, output):
            module.to(CPU)
            if OFFLOAD_EMPTY_CACHE:
                torch.cuda.empty_cache()
            return output

        _OFFLOAD_HANDLES.append(block.register_forward_pre_hook(pre_hook))
        _OFFLOAD_HANDLES.append(block.register_forward_hook(post_hook))
    print(
        f"[OFFLOAD] {n}/{total} blocks cycled via RAM, "
        f"released {human_gib(_OFFLOADED_BYTES)}",
        flush=True,
    )


prepare_offload()

pipe.vae.to(CPU)
pipe.vae.eval()
pipe.vae.requires_grad_(False)

gc.collect()
torch.cuda.empty_cache()
torch.cuda.reset_peak_memory_stats(DEVICE_INDEX)

print(f"[PLACEMENT] text_encoder={next(pipe.text_encoder.parameters()).device}", flush=True)
print(f"[PLACEMENT] transformer={next(pipe.transformer.parameters()).device}", flush=True)
print(f"[PLACEMENT] vae={next(pipe.vae.parameters()).device}", flush=True)
print(f"[SIZE] transformer={human_gib(model_bytes(pipe.transformer))}", flush=True)
print(f"[SIZE] vae={human_gib(model_bytes(pipe.vae))}", flush=True)
cuda_mem("after load")

_SIG = inspect.signature(pipe.__call__).parameters
SUPPORTS_KV_CACHE = "use_kv_cache" in _SIG
SUPPORTS_SIGMAS = "sigmas" in _SIG
TRANSFORMER_LAYERS = len(getattr(pipe.transformer, "transformer_blocks", []) or [])
print(
    f"[PIPELINE] use_kv_cache={SUPPORTS_KV_CACHE} sigmas={SUPPORTS_SIGMAS} "
    f"transformer_layers={TRANSFORMER_LAYERS}",
    flush=True,
)


# ---------------------------------------------------------------------------
# 5. request handling
# ---------------------------------------------------------------------------
def _prepare_references(images) -> list:
    """Cap the number of references and normalise them to RGB PIL images."""
    refs = []
    for img in images or []:
        if img is None:
            continue
        if not isinstance(img, Image.Image):
            img = Image.fromarray(img)
        refs.append(img.convert("RGB"))
    if MAX_REFS and len(refs) > MAX_REFS:
        print(f"[REF] {len(refs)} given, keeping the first {MAX_REFS}", flush=True)
        refs = refs[:MAX_REFS]
    return refs


def _encode_on_cpu(prompt: str, refs: list):
    """Run Qwen3-VL on CPU and return GPU-ready conditioning."""
    started = time.perf_counter()
    encoded = pipe.encode_prompt(prompt=prompt, image=refs or None, device=CPU)
    if not isinstance(encoded, tuple) or len(encoded) < 2:
        raise RuntimeError(f"encode_prompt() 返回结构异常：{type(encoded)}")
    embeds, mask = encoded[0], encoded[1]
    pad_mask = encoded[2] if len(encoded) > 2 else None
    elapsed = time.perf_counter() - started
    print(
        f"[TEXT] CPU encode {elapsed:.2f}s | embeds={tuple(embeds.shape)} "
        f"{embeds.dtype} | refs={len(refs)}",
        flush=True,
    )
    embeds = embeds.to(DEVICE, dtype=DTYPE)
    if mask is not None:
        mask = mask.to(DEVICE)
    if pad_mask is not None:
        pad_mask = pad_mask.to(DEVICE)
    return embeds, mask, pad_mask, elapsed


def _decode_latent(latents, width: int, height: int) -> Image.Image:
    """Move the VAE onto the GPU only for the final decode."""
    print("[VAE] onload for decode", flush=True)
    pipe.vae.to(DEVICE)
    pipe.vae.eval()
    torch.cuda.empty_cache()
    try:
        with torch.inference_mode():
            latents = pipe._unpack_latents(latents, height, width, pipe.vae_scale_factor)
            latents = latents.to(device=DEVICE, dtype=pipe.vae.dtype)
            mean = torch.tensor(
                pipe.vae.config.latents_mean, device=DEVICE, dtype=latents.dtype
            ).view(1, pipe.vae.config.z_dim, 1, 1, 1)
            std = 1.0 / torch.tensor(
                pipe.vae.config.latents_std, device=DEVICE, dtype=latents.dtype
            ).view(1, pipe.vae.config.z_dim, 1, 1, 1)
            latents = latents / std + mean
            image = pipe.vae.decode(latents, return_dict=False)[0][:, :, 0]
            image = pipe.image_processor.postprocess(image, output_type="pil")[0]
    finally:
        pipe.vae.to(CPU)
        gc.collect()
        torch.cuda.empty_cache()
    return image


def _make_callback(total_steps, timer, progress):
    last = time.perf_counter()

    def callback(_pipe, step, timestep, callback_kwargs):
        nonlocal last
        torch.cuda.synchronize(DEVICE)
        now = time.perf_counter()
        delta, last = now - last, now
        print(
            f"[STEP] {step + 1}/{total_steps} step={delta:.2f}s "
            f"elapsed={now - timer[0]:.2f}s",
            flush=True,
        )
        if progress:
            progress(step=step + 1, total=total_steps)
        return callback_kwargs

    return callback


def run_generation(params: dict, progress=None):
    """Shared by the WebUI and the API. Returns (PIL image, meta dict)."""
    prompt = str(params.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空 / prompt is required")

    budget = qwen_sizing.max_pixels()
    if params.get("max_pixels"):
        try:
            budget = min(int(params["max_pixels"]), qwen_sizing.ABSOLUTE_MAX_PIXELS)
        except (TypeError, ValueError):
            pass

    width, height = qwen_sizing.resolve(
        params.get("width"),
        params.get("height"),
        aspect=params.get("aspect"),
        budget=budget,
        spec=params.get("size"),
    )

    steps = max(1, min(150, int(params.get("steps") or 8)))
    seed = int(params.get("seed") if params.get("seed") is not None else 42)
    transparent = bool(params.get("transparent"))
    refs = _prepare_references(params.get("refs"))

    if transparent:
        # Same wording as the ComfyUI strategies, and the format the Qwen model
        # card prescribes for RGBA output.
        prompt = transparent_prompt(prompt)

    print("\n" + "=" * 72, flush=True)
    print(f"NEW GENERATION | {width}x{height} | {steps} steps | seed={seed}", flush=True)
    print(f"  references: {len(refs)} | ref-latents: {USE_REF_LATENTS}", flush=True)
    print("=" * 72, flush=True)

    generator = torch.Generator(device=DEVICE).manual_seed(seed)

    t0 = time.perf_counter()
    embeds, mask, pad_mask, text_elapsed = _encode_on_cpu(prompt, refs)

    kwargs = dict(
        prompt=None,
        prompt_embeds=embeds,
        prompt_embeds_mask=mask,
        height=height,
        width=width,
        num_inference_steps=steps,
        generator=generator,
        true_cfg_scale=1.0,
        output_type="latent",
        return_dict=True,
        callback_on_step_end=_make_callback(steps, [time.perf_counter()], progress),
        callback_on_step_end_tensor_inputs=[],
    )
    if SUPPORTS_KV_CACHE:
        kwargs["use_kv_cache"] = USE_KV_CACHE

    # Reference latents: the pipeline refuses `image` together with
    # `prompt_embeds` unless it also gets `image_pad_mask`, which its own
    # signature does not forward. Supplying the mask we already computed on CPU
    # is enough, so patch it in for the duration of the call and fall back to
    # vision-context-only conditioning if anything about that is rejected.
    used_ref_latents = False
    if refs and USE_REF_LATENTS and pad_mask is not None:
        original = pipe.encode_prompt

        def encode_with_mask(*args, **kw):
            if kw.get("prompt_embeds") is not None and kw.get("image") is not None \
                    and kw.get("image_pad_mask") is None:
                kw["image_pad_mask"] = pad_mask
            return original(*args, **kw)

        pipe.encode_prompt = encode_with_mask
        kwargs["image"] = refs
        used_ref_latents = True

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats(DEVICE_INDEX)
        torch.cuda.synchronize(DEVICE)
    timer = time.perf_counter()
    started = time.perf_counter()
    try:
        with torch.inference_mode():
            result = pipe(**kwargs)
    except (ValueError, TypeError, RuntimeError) as exc:
        if not used_ref_latents:
            raise
        print(
            f"[REF] reference-latent path rejected ({type(exc).__name__}: {exc}); "
            "retrying with text-encoder vision context only",
            flush=True,
        )
        pipe.encode_prompt = original
        kwargs.pop("image", None)
        used_ref_latents = False
        torch.cuda.empty_cache()
        started = time.perf_counter()
        with torch.inference_mode():
            result = pipe(**kwargs)
    finally:
        if used_ref_latents:
            pipe.encode_prompt = original

    torch.cuda.synchronize(DEVICE)
    denoise_elapsed = time.perf_counter() - started
    print(f"[GPU] denoising done in {denoise_elapsed:.2f}s", flush=True)
    cuda_mem("after denoising")

    latents = result.images
    if not torch.is_tensor(latents):
        raise RuntimeError(f"output_type='latent' returned {type(latents)}")

    decode_started = time.perf_counter()
    image = _decode_latent(latents, width, height)
    decode_elapsed = time.perf_counter() - decode_started

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"qwen_native_{stamp}_{seed}.png"
    image.save(out)

    total = time.perf_counter() - t0
    mode = f"多图参考({len(refs)})" if refs else "文生图"
    meta = {
        "image_path": str(out),
        "width": width,
        "height": height,
        "megapixels": round(width * height / 1e6, 3),
        "steps": steps,
        "seed": seed,
        "mode": mode,
        "reference_count": len(refs),
        "reference_latents": used_ref_latents,
        "text_encode_seconds": round(text_elapsed, 2),
        "denoise_seconds": round(denoise_elapsed, 2),
        "decode_seconds": round(decode_elapsed, 2),
        "total_seconds": round(total, 2),
        "fp8_transformer": USE_FP8_TRANSFORMER,
        "kv_cache": USE_KV_CACHE,
        "offload_blocks": OFFLOAD_BLOCKS,
        "gpu": torch.cuda.get_device_name(DEVICE_INDEX),
    }
    status = (
        f"完成 | {width}x{height} | {steps} steps | seed={seed} | {mode}\n"
        f"Qwen3-VL CPU {text_elapsed:.1f}s | 去噪 {denoise_elapsed:.1f}s | "
        f"VAE 解码 {decode_elapsed:.1f}s | 合计 {total:.1f}s\n"
        f"FP8={USE_FP8_TRANSFORMER} | KV cache={USE_KV_CACHE} | "
        f"offload={OFFLOAD_BLOCKS} blocks | 参考潜变量={used_ref_latents}\n"
        f"输出：{out}"
    )
    return image, {**meta, "status": status}


# ---------------------------------------------------------------------------
# 6. Gradio UI
# ---------------------------------------------------------------------------
def _preset_to_wh(name: str):
    return qwen_sizing.PRESETS.get(name, (1024, 1024))


def _on_preset(name):
    w, h = _preset_to_wh(name)
    return w, h


def _ui_generate(prompt, edit_image, ref_files, preset, width, height,
                 max_pixels, steps, seed, transparent, progress=gr.Progress()):
    progress(0.0, desc="准备")
    refs = []
    if edit_image is not None:
        refs.append(edit_image)
    for f in (ref_files or []):
        try:
            refs.append(Image.open(f if isinstance(f, str) else f.name).convert("RGB"))
        except Exception as exc:  # noqa: BLE001
            print(f"[REF] 跳过无法读取的文件 {f}: {exc}", flush=True)
    params = dict(
        prompt=prompt,
        refs=refs,
        width=width,
        height=height,
        max_pixels=max_pixels,
        steps=steps,
        seed=seed,
        transparent=transparent,
    )
    if preset and preset != "自定义 / custom":
        params["aspect"] = None
    try:
        image, meta = run_generation(params)
    except Exception as exc:  # noqa: BLE001
        raise gr.Error(f"生成失败：{type(exc).__name__}: {exc}") from exc
    progress(1.0, desc="完成")
    return image, meta["status"]


def build_ui():
    preset_names = ["自定义 / custom"] + list(qwen_sizing.PRESETS.keys())
    caps = qwen_sizing.max_pixels()
    with gr.Blocks(title="Qwen-Image-2.1 原生策略 / native") as demo:
        gr.Markdown(
            "# Qwen-Image-2.1 原生策略（diffusers）\n"
            "**Qwen3-VL → CPU RAM ｜ Transformer → FP8 GPU ｜ VAE → 仅解码时进 GPU**\n\n"
            f"分辨率可任意指定，只限制最大像素乘积（当前上限 **{caps/1e6:.2f} MP**，"
            "可用 `QWEN_MAX_PIXELS` 调整）；两边会自动对齐到 32 的倍数。\n\n"
            "参考图支持**多张**：以「编辑图」+「附加参考图」的形式一起参与，"
            "配合提示词生成一张新图。"
        )
        with gr.Row():
            with gr.Column(scale=1):
                prompt = gr.Textbox(
                    label="提示词", lines=7,
                    placeholder="例如：把这几张参考图里的角色合成到同一张海报里，统一光照与色调",
                )
                edit_image = gr.Image(label="编辑图（单张，沿用原有编辑方式）", type="pil")
                ref_files = gr.Files(
                    label="附加参考图（可多选，参与多图参考）",
                    file_count="multiple",
                    file_types=["image"],
                )
                with gr.Row():
                    preset = gr.Dropdown(
                        preset_names, value="1:1   1024x1024",
                        label="画幅预设（仅用于填充宽高，可改）",
                    )
                with gr.Row():
                    width = gr.Number(value=1024, precision=0, label="宽 width")
                    height = gr.Number(value=1024, precision=0, label="高 height")
                with gr.Row():
                    max_pixels = gr.Number(
                        value=int(caps), precision=0,
                        label="最大像素乘积 max pixels (只用这个限制分辨率)",
                    )
                with gr.Row():
                    steps = gr.Slider(1, 60, value=8, step=1, label="推理步数")
                    seed = gr.Number(value=42, precision=0, label="Seed")
                with gr.Row():
                    seed_btn = gr.Button("随机 Seed")
                    clear_btn = gr.Button("清空参考图")
                transparent = gr.Checkbox(label="透明背景提示词", value=False)
                generate_btn = gr.Button("生成图片", variant="primary")
            with gr.Column(scale=1):
                output = gr.Image(label="生成结果", type="pil")
                status = gr.Textbox(label="状态 / 分阶段耗时", interactive=False, lines=8)

        preset.change(_on_preset, inputs=preset, outputs=[width, height])
        generate_btn.click(
            _ui_generate,
            inputs=[prompt, edit_image, ref_files, preset, width, height,
                    max_pixels, steps, seed, transparent],
            outputs=[output, status],
        )
        seed_btn.click(lambda: random.randint(0, 2**31 - 1), outputs=seed)
        clear_btn.click(lambda: (None, None), outputs=[edit_image, ref_files])
    return demo


# ---------------------------------------------------------------------------
# 7. API + serve
# ---------------------------------------------------------------------------
def api_generate(params: dict, progress):
    refs = load_images(params.pop("images", None))
    refs += load_images(params.pop("image", None))
    params["refs"] = refs
    return run_generation(params, progress)


def health():
    return {
        "device": torch.cuda.get_device_name(DEVICE_INDEX) if torch.cuda.is_available() else "cpu",
        "backend": "diffusers",
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "offline": os.environ.get("HF_HUB_OFFLINE") == "1",
    }


CAPABILITIES = {
    "backend": "diffusers",
    "endpoints": {
        "generate": "POST /api/generate  {prompt, images?, width?, height?, aspect?, max_pixels?, steps?, seed?, transparent?}",
        "poll": "GET /api/jobs/{job_id}",
        "image": "GET /api/jobs/{job_id}/image",
        "sync": "POST /api/generate/image (returns PNG)",
        "health": "GET /api/health",
        "capabilities": "GET /api/capabilities",
    },
    "resolution": {
        "mode": "arbitrary width/height, only a maximum pixel product is enforced",
        "multiple": qwen_sizing.SIZE_MULTIPLE,
        "min_side": qwen_sizing.MIN_SIDE,
        "max_side": qwen_sizing.MAX_SIDE,
        "default_max_pixels": qwen_sizing.DEFAULT_MAX_PIXELS,
        "current_max_pixels": qwen_sizing.max_pixels(),
        "presets": qwen_sizing.PRESETS,
    },
    "references": {
        "multiple": True,
        "max_count": MAX_REFS,
        "reference_latents": USE_REF_LATENTS,
        "accepted": "PIL image, data URL, bare base64, or bytes; list for several",
        "note": "images are passed as text-encoder vision context and, when supported, as VAE reference latents",
    },
    "defaults": {"steps": 8, "seed": 42, "true_cfg_scale": 1.0},
}

if __name__ == "__main__":
    # `--api-only` skips the Gradio UI, for headless/API use
    api_only = "--api-only" in sys.argv
    runner = JobRunner(api_generate)
    app = build_app(
        strategy=STRATEGY,
        runner=runner,
        capabilities=CAPABILITIES,
        health=health,
        gradio_app=None if api_only else build_ui(),
        title="Qwen-Image-2.1 native strategy",
        # Models stay loaded; only the CUDA allocator cache is returned.
        on_free=lambda: (gc.collect(), torch.cuda.empty_cache()),
        on_shutdown=lambda: (gc.collect(), torch.cuda.empty_cache()),
        pid_dir=PATHS.output_dir / "run",
    )
    print(f"\nWebUI + API: http://{HOST}:{PORT}/   (API docs at /docs)\n", flush=True)
    serve(app, HOST, PORT, inbrowser=not api_only and "--no-browser" not in sys.argv)
