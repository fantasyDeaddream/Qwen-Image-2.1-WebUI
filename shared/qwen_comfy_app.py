"""Shared application body for the ComfyUI-backed (quantized) strategies.

A strategy is just a *placement profile* over the same graph, so the app logic
lives here once and each ``<strategy>/app.py`` is a thin file that picks a
profile. Two profiles ship:

``mixed``
    Let ComfyUI decide where the text encoder lives. Biggest speed, least
    control: on a small card ComfyUI will page the encoder in and out.

``mixed-vram``
    Explicit placement, mirroring the native 32+8G recipe:
      * Qwen3-VL text encoder -> system RAM (``CLIPLoader.device = "cpu"``),
        so the ~8.7 GiB INT8 encoder never competes with the diffusion model;
      * VAE -> CPU (ComfyUI's ``--cpu-vae``);
      * diffusion model -> VRAM, resident (only ~3.9 GiB for the W4 mixed
        checkpoint), with the prefix KV cache pinned to the GPU
        (``QwenImage21Cache(device="gpu")``).
    The point is that the 3.9 GiB model *stays* on the card instead of being
    evicted whenever the encoder needs room.
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

import qwen_comfy
from qwen_comfy import TURBO_SIGMAS

from PIL import Image

# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------
def _compile_args() -> list:
    """Extra ComfyUI flags that TorchCompileModel needs.

    ``cudagraphs`` builds CUDA-graph trees, which call
    ``checkPoolLiveAllocations``. That raises ``RuntimeError: cudaMallocAsync
    does not yet support checkPoolLiveAllocations`` while the streaming
    allocator is active, and ComfyUI turns cudaMallocAsync ON by default
    (``cuda_malloc.py:106-112``). So enabling compilation must also pass
    ``--disable-cuda-malloc`` or the first forward pass dies.
    """
    if not _compile_backend():
        return []
    return ["--disable-cuda-malloc"]

# Marks a QWEN_COMFYUI_DIR value that we set ourselves, so switching profiles
# inside one process can undo it while a user-supplied value stays untouched.
_ISOLATED_MARK = "_QWEN_ISOLATED_COMFY"
# Captured at import time, i.e. BEFORE any qwen_paths.initialize() call, because
# initialize() exports QWEN_COMFYUI_DIR itself -- so a value read later cannot be
# told apart from a genuine user override.
_USER_COMFY_DIR = os.environ.get("QWEN_COMFYUI_DIR")


def _apply_isolated_comfy(profile: dict) -> None:
    """Let a profile pin its own ComfyUI checkout inside the strategy folder.

    The project-wide env cache hands back the primary ComfyUI, which is right
    for most strategies but wrong for one that needs a custom node the primary
    does not have. A profile carrying ``comfy_dir_rel`` (relative to the project
    root) overrides that, unless the user set QWEN_COMFYUI_DIR themselves.
    """
    rel = profile.get("comfy_dir_rel")
    if rel:
        cand = Path(__file__).resolve().parent.parent / rel
        if (cand / "main.py").is_file():
            os.environ["QWEN_COMFYUI_DIR"] = str(cand)
            os.environ[_ISOLATED_MARK] = "1"
            return
    # This profile wants no isolation: put the user's value back, dropping
    # anything we set earlier or that a previous initialize() exported.
    if os.environ.get(_ISOLATED_MARK) == "1" or os.environ.get("QWEN_COMFYUI_DIR") != _USER_COMFY_DIR:
        if _USER_COMFY_DIR is None:
            os.environ.pop("QWEN_COMFYUI_DIR", None)
        else:
            os.environ["QWEN_COMFYUI_DIR"] = _USER_COMFY_DIR
        os.environ.pop(_ISOLATED_MARK, None)


def _extra_args() -> list:
    """Escape hatch for additional ComfyUI CLI flags.

    The useful one here is --reserve-vram <GiB>: ComfyUI already reserves
    600 MiB on Windows ("because of the shared vram issue", model_management.py
    :879) but that budget is computed from what it can see, so a background
    application that grows later can still push the card into WDDM's blind
    shared-memory paging. Raising the reserve makes ComfyUI do its own managed,
    prefetched offloading instead. Example: QWEN_COMFY_EXTRA_ARGS="--reserve-vram 1.2"
    """
    raw = os.environ.get("QWEN_COMFY_EXTRA_ARGS", "").strip()
    return raw.split() if raw else []


def _ck_args() -> list:
    """Comfy Kitchen attention for the diffusion model.

    ``comfy_kitchen`` is already installed (it is a ComfyUI requirement), so
    this needs no new dependency. It selects an optimised attention kernel
    instead of the generic PyTorch path, which matters because a DiT spends
    most of its time in attention.

    Measured here at 1024x1024 / 12 steps: bit-identical output but NO
    speedup (15.0 s -> 15.1 s denoise), so it is OFF by default. Set
    QWEN_CK_ATTENTION=1 to enable it; it may help at other shapes.
    """
    if os.environ.get("QWEN_CK_ATTENTION", "0").strip() == "0":
        return []
    return ["--use-ck-attention"]


def _compile_backend():
    """TorchCompileModel backend, or None to leave compilation off.

    Only ``cudagraphs`` is usable here: the ``inductor`` backend that this
    ComfyUI build offers needs a working triton, and triton is not installed in
    this venv -- ``torch.compile(backend="inductor")`` fails with
    ``TritonMissing``. Set QWEN_TORCH_COMPILE=inductor after installing
    triton-windows, or leave it empty for no compilation.
    """
    value = os.environ.get("QWEN_TORCH_COMPILE", "").strip()
    if not value or value.lower() in ("0", "off", "none", "false"):
        return None
    return value


def _vae_args() -> list:
    """Where the VAE runs.

    Measured on the target machine: ComfyUI loads the VAE as **fp32** by
    default, which is 1.29 GiB for this 0.63 GiB bf16 checkpoint, and decoding
    on the CPU was the single largest cost in a generation (denoising itself
    was ~2.3 s of a 132 s run).

    Budget for an 8 GB card (8188 MiB, ~1.9-2.1 GiB already used by other
    applications):
        diffusion model (W4)   3.91 GiB
        VAE bf16               0.63 GiB
        ------------------------------
        resident total         4.54 GiB      -> ~1.5 GiB left for workspace

    So the VAE goes on the GPU, but in **bf16** rather than fp32. Setting
    QWEN_VAE_IN_VRAM=0 restores the previous --cpu-vae behaviour.
    """
    if os.environ.get("QWEN_VAE_IN_VRAM", "1").strip() == "0":
        return ["--cpu-vae"]
    return ["--bf16-vae"]


PROFILES = {
    "mixed": {
        "title": "Qwen-Image-2.1 Mixed（量化模型，ComfyUI 自适应放置）",
        "clip_device": "default",
        "cache_device": os.environ.get("QWEN_KV_CACHE_DEVICE", "auto"),
        "cache_dtype": os.environ.get("QWEN_KV_CACHE_DTYPE", "default"),
        "comfy_args": _ck_args() + _compile_args() + _extra_args(),
        "placement": "text encoder: ComfyUI 决定 | diffusion: 显存 | VAE: 显存 | KV cache: auto",
    },
    "turbo": {
        "title": "Qwen-Image-2.1 Turbo（8 步蒸馏 LoRA 叠加在现有 W4 量化模型上）",
        "clip_device": "cpu",
        "cache_device": "gpu",
        "cache_dtype": "default",
        "comfy_args": _vae_args() + _extra_args(),
        "placement": (
            "text encoder: 内存/CPU | VAE: 显存 BF16 | diffusion: 常驻显存 | "
            "Turbo LoRA: 叠加在 W4 mixed 上 | KV cache: GPU（有 LoRA patch 时 ComfyUI 会自动关闭前缀缓存）"
        ),
        # Turbo is an 8-step distillation: 8 steps at CFG 1 is the shipped recipe.
        "steps_default": 8,
        "cfg_default": 1.0,
        # Turbo is trained for its shipped grid, not ComfyUI's `simple`.
        # An exact grid switches the graph to SamplerCustom + ManualSigmas.
        "sigmas_default": TURBO_SIGMAS,
        "lora": "qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors",
        "lora_strength": 1.0,
    },    "gguf": {
        "title": "Qwen-Image-2.1 Turbo GGUF（Q4_K_M，隔离的 ComfyUI 实例）",
        "clip_device": "cpu",
        "cache_device": "gpu",
        "cache_dtype": "default",
        "comfy_args": _vae_args() + _extra_args(),
        "placement": (
            "text encoder: 内存/CPU | VAE: 显存 BF16 | diffusion: 显存 3.90 GiB (GGUF Q4_K_M) | "
            "KV cache: GPU"
        ),
        # Turbo is an 8-step distillation.
        "steps_default": 8,
        "cfg_default": 1.0,
        # GGUF has to go through the ComfyUI-GGUF custom node, and that node is
        # installed into an isolated checkout so the primary ComfyUI (which
        # serves mixed / mixed-vram / turbo) is never modified. The isolated
        # instance therefore runs on its own port so both can coexist.
        "unet_loader": "UnetLoaderGGUF",
        "unet_folder": "unet",
        "comfy_port": 8189,
        "comfy_dir_rel": "gguf/ComfyUI",
    },    "mixed-vram": {
        "title": "Qwen-Image-2.1 Mixed-VRAM（量化模型，显式放置）",
        "clip_device": "cpu",
        # Where the step-independent prefix K/V lives. "gpu" is fastest but
        # costs VRAM; "cpu" keeps it in pinned RAM (ComfyUI prefetches it behind
        # compute); "off" recomputes the prefix every step. dtype "default" is
        # lossless -- int8/int4 trade precision for size and are opt-in only.
        "cache_device": os.environ.get("QWEN_KV_CACHE_DEVICE", "gpu"),
        "cache_dtype": os.environ.get("QWEN_KV_CACHE_DTYPE", "default"),
        "comfy_args": _vae_args() + _ck_args() + _compile_args() + _extra_args(),
        "placement": (
            "text encoder: 内存/CPU (8.7 GiB 不进显存) | VAE: 显存 BF16 (0.63 GiB) | "
            "diffusion: 常驻显存 (3.91 GiB) | KV cache: GPU"
        ),
    },
}


class Strategy:
    """Holds the resolved paths, the client and the model file names."""

    def __init__(self, name: str):
        if name not in PROFILES:
            raise SystemExit(f"unknown strategy profile: {name}")
        self.name = name
        self.profile = PROFILES[name]
        # A profile may pin its own ComfyUI checkout (the gguf strategy does);
        # this must happen before the path discovery below reads the env cache.
        _apply_isolated_comfy(self.profile)

        # locate the shared library and the project assets
        here = Path(__file__).resolve().parent
        project = here.parent
        sys.path.insert(0, str(here))

        import qwen_paths
        import qwen_sizing

        self.sizing = qwen_sizing
        self.paths = qwen_paths.initialize(
            project, need_models=True, need_comfy=True, verbose=True
        )
        if not self.paths.comfy_dir:
            raise SystemExit(
                "找不到 ComfyUI 目录。请先运行 setup\\setup_comfyui.bat\n"
                "ComfyUI not found -- run setup\\setup_comfyui.bat first."
            )

        import qwen_comfy

        self.comfy = qwen_comfy
        self.client = qwen_comfy.ComfyClient(
            host=os.environ.get("QWEN_COMFY_HOST", "127.0.0.1"),
            port=int(os.environ.get("QWEN_COMFY_PORT",
                         str(self.profile.get("comfy_port", 8188)))),
            comfy_dir=self.paths.comfy_dir,
        )
        self.models = self._resolve_models()

    # ---- model discovery -------------------------------------------------
    def _resolve_models(self) -> dict:
        comfy_dir = self.paths.comfy_dir
        comfy = self.comfy
        # extra_model_paths.yaml lets an isolated checkout borrow another models
        # directory instead of duplicating the 8.7 GiB text encoder. ComfyUI
        # reads that file itself; our own filesystem scan needs to be told.
        extra = comfy.extra_model_dirs(comfy_dir)
        if self.profile.get("unet_loader") == "UnetLoaderGGUF":
            unet = None
            for folder in (self.profile.get("unet_folder", "unet"), "diffusion_models"):
                d = Path(comfy_dir) / "models" / folder
                hits = sorted(d.glob("*.gguf")) if d.is_dir() else []
                if hits:
                    unet = hits[0].name
                    break
            if not unet:
                folder = self.profile.get("unet_folder", "unet")
                raise SystemExit(
                    f"找不到 GGUF 扩散模型：{comfy_dir}\\models\\{folder}\\*.gguf\n"
                    "请下载（约 3.90 GiB，ModelScope Abiray/Qwen-Image-2.1-Turbo-GGUF）：\n"
                    "  qwen_image_2.1_turbo_Q4_K_M.gguf"
                )
        else:
            unet = comfy.local_checkpoint(comfy_dir, "diffusion_models",
                                          prefer=("mixed", "balanced"),
                                          extra_roots=extra.get("diffusion_models", ()))
        clip = comfy.local_checkpoint(
            comfy_dir, "text_encoders",
            prefer=(os.environ.get("QWEN_TEXT_ENCODER", ""), "qwen3vl", "int8"),
            extra_roots=extra.get("text_encoders", ()),
        )
        vae = comfy.local_checkpoint(comfy_dir, "vae", prefer=("qwen", "vae"),
                                     extra_roots=extra.get("vae", ()))
        if not unet:
            raise SystemExit(
                f"找不到 mixed 量化扩散模型：{comfy_dir}\\models\\diffusion_models\n"
                "请运行 setup\\setup_models.bat 或手动放入模型。"
            )
        if not clip:
            raise SystemExit(
                f"找不到文本编码器：{comfy_dir}\\models\\text_encoders\n"
                f"（extra_model_paths.yaml 提供的备用目录：{extra.get('text_encoders', [])}）"
            )
        if not vae:
            raise SystemExit(f"找不到 VAE：{comfy_dir}\\models\\vae")

        lora = None
        want_lora = self.profile.get("lora")
        if want_lora:
            ldir = Path(comfy_dir) / "models" / "loras"
            exact = ldir / want_lora
            if exact.is_file() and exact.stat().st_size > 10 * 1024 * 1024:
                lora = exact.name
            elif ldir.is_dir():
                cands = [f for f in sorted(ldir.glob("*.safetensors"))
                         if "turbo" in f.name.lower() and f.stat().st_size > 10 * 1024 * 1024]
                lora = cands[0].name if cands else None
            if not lora:
                raise SystemExit(
                    "turbo 策略需要 Turbo LoRA，但没有找到：\n"
                    f"  {ldir}\\{want_lora}\n"
                    "请下载（0.85 GiB，ModelScope）：\n"
                    "  setup\\setup_models_mixed.bat --with-gguf\n"
                    "或手动放到 ComfyUI\\models\\loras\\ 下。"
                )
        return {"unet": unet, "clip": clip, "vae": vae, "lora": lora}

    # ---- lifecycle -------------------------------------------------------
    def ensure_backend(self):
        if self.client.alive():
            return False
        python = os.environ.get("QWEN_PYTHON") or sys.executable
        # When a CPU text encoder is requested, make sure the encoder is not
        # also pulled onto the GPU by the dynamic-VRAM heuristics.
        args = list(self.profile["comfy_args"])
        # Backends are started with the log opened in append mode, so two
        # checkouts sharing one filename interleave their output and make
        # measurements unattributable. An isolated runtime gets its own log;
        # the primary keeps the existing name.
        log = self.paths.output_dir / "comfyui_backend.log"
        if self.profile.get("comfy_dir_rel"):
            log = self.paths.output_dir / f"comfyui_backend_{self.name}.log"
        self.client.start(
            python=python, extra_args=args, log_path=str(log), timeout=420
        )
        return True

    def verify_nodes(self):
        """Fail early and clearly if the backend lacks what the graph needs."""
        loader = self.profile.get("unet_loader", "UNETLoader")
        for node in (loader, "CLIPLoader", "VAELoader",
                     "TextEncodeQwenImage21", "KSampler"):
            if node not in self.client.object_info(node):
                raise self.comfy.ComfyError(f"ComfyUI 缺少节点 {node}")
        # learn the dotted Autogrow keys instead of hard-coding them
        keys = self.client.autogrow_keys("TextEncodeQwenImage21", "images")
        if not keys:
            raise self.comfy.ComfyError(
                "无法从 /object_info 解析 TextEncodeQwenImage21 的 images 槽位"
            )
        return keys

    # ---- generation ------------------------------------------------------
    def run(self, params: dict, progress=None):
        comfy = self.comfy
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt 不能为空 / prompt is required")
        if params.get("transparent"):
            prompt = comfy.transparent_prompt(prompt)

        budget = self.sizing.max_pixels()
        if params.get("max_pixels"):
            try:
                budget = min(int(params["max_pixels"]), self.sizing.ABSOLUTE_MAX_PIXELS)
            except (TypeError, ValueError):
                pass
        width, height = self.sizing.resolve(
            params.get("width"), params.get("height"),
            aspect=params.get("aspect"), budget=budget, spec=params.get("size"),
        )

        steps = max(1, min(150, int(params.get("steps") or self.profile.get("steps_default") or comfy.OFFICIAL_STEPS)))
        cfg = float(params.get("cfg", self.profile.get("cfg_default", comfy.OFFICIAL_CFG)))
        seed = int(params.get("seed") if params.get("seed") is not None else 42)
        negative = str(params.get("negative_prompt") or "")
        refs = [r for r in (params.get("refs") or []) if r is not None]
        latent_mode = str(params.get("latent_mode") or "custom")
        if latent_mode not in ("custom", "reference"):
            latent_mode = "custom"
        if latent_mode == "reference" and not refs:
            latent_mode = "custom"

        self.ensure_backend()
        ref_names = []
        for image in refs:
            if not isinstance(image, Image.Image):
                image = Image.fromarray(image)
            ref_names.append(self.client.upload_image(image.convert("RGB")))

        compile_backend = _compile_backend()
        graph = comfy.build_graph(
            prompt=prompt,
            negative_prompt=negative,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            seed=seed,
            unet_name=self.models["unet"],
            clip_name=self.models["clip"],
            vae_name=self.models["vae"],
            reference_names=ref_names,
            latent_mode=latent_mode,
            clip_device=self.profile["clip_device"],
            cache_device=self.profile["cache_device"],
            cache_dtype=self.profile["cache_dtype"],
            filename_prefix=f"QwenImage21_{self.name}",
            torch_compile=compile_backend,
            lora_name=self.models.get("lora"),
            lora_strength=float(self.profile.get("lora_strength", 1.0)),
            unet_loader=self.profile.get("unet_loader", "UNETLoader"),
            sigmas=params.get("sigmas") or self.profile.get("sigmas_default"),
        )
        print("=" * 72, flush=True)
        print(f"NEW GENERATION | {self.name} | {width}x{height} | {steps} steps | "
              f"cfg={cfg} | seed={seed} | refs={len(ref_names)} | latent={latent_mode}",
              flush=True)
        print(comfy.describe_graph(graph), flush=True)
        print("=" * 72, flush=True)

        started = time.perf_counter()
        if progress:
            progress(stage="queued", step=0, total=steps)
        prompt_id = self.client.queue(graph)
        image, info = self.client.wait_result(
            prompt_id, on_progress=(lambda **kw: progress(**kw)) if progress else None
        )
        elapsed = time.perf_counter() - started

        stamp = time.strftime("%Y%m%d_%H%M%S")
        out = self.paths.output_dir / f"qwen_{self.name}_{stamp}_{seed}.png"
        image.save(out)

        meta = {
            "image_path": str(out),
            "strategy": self.name,
            "width": width,
            "height": height,
            "megapixels": round(width * height / 1e6, 3),
            "steps": steps,
            "cfg": cfg,
            "seed": seed,
            "reference_count": len(ref_names),
            "latent_mode": latent_mode,
            "placement": self.profile["placement"],
            "models": self.models,
            "seconds": round(elapsed, 2),
            "comfy_filename": info.get("filename"),
        }
        status = (
            f"完成 | {self.name} | {width}x{height} | {steps} steps | cfg={cfg} | "
            f"seed={seed} | 参考图 {len(ref_names)} 张 | latent={latent_mode}\n"
            f"放置：{self.profile['placement']}\n"
            f"耗时 {elapsed:.1f}s | 模型 {self.models['unet']}\n"
            f"输出：{out}"
        )
        return image, {**meta, "status": status}


# ---------------------------------------------------------------------------
# Gradio UI (identical shape for every ComfyUI strategy)
# ---------------------------------------------------------------------------
def build_ui(strategy: Strategy):
    import gradio as gr

    sizing = strategy.sizing
    caps = sizing.max_pixels()
    preset_names = ["自定义 / custom"] + list(sizing.PRESETS.keys())

    def on_preset(name):
        return sizing.PRESETS.get(name, (1024, 1024))

    def ui_generate(prompt, edit_image, ref_files, preset, width, height,
                    max_pixels, steps, cfg, seed, negative, latent_mode, transparent,
                    progress=gr.Progress()):
        progress(0.0, desc="准备")
        refs = []
        if edit_image is not None:
            refs.append(edit_image)
        for f in (ref_files or []):
            try:
                refs.append(Image.open(f if isinstance(f, str) else f.name).convert("RGB"))
            except Exception as exc:  # noqa: BLE001
                print(f"[REF] skip unreadable file {f}: {exc}", flush=True)
        try:
            image, meta = strategy.run({
                "prompt": prompt, "refs": refs, "width": width, "height": height,
                "max_pixels": max_pixels, "steps": steps, "cfg": cfg, "seed": seed,
                "negative_prompt": negative, "latent_mode": latent_mode,
                "transparent": transparent,
            })
        except Exception as exc:  # noqa: BLE001
            raise gr.Error(f"生成失败：{type(exc).__name__}: {exc}") from exc
        progress(1.0, desc="完成")
        return image, meta["status"]

    with gr.Blocks(title=strategy.profile["title"]) as demo:
        gr.Markdown(
            f"# {strategy.profile['title']}\n"
            f"**放置策略：{strategy.profile['placement']}**\n\n"
            f"分辨率任意指定，只限制最大像素乘积（当前 **{caps/1e6:.2f} MP**，"
            "`QWEN_MAX_PIXELS` 可调），两边自动对齐到 32 的倍数。\n\n"
            "参考图可多张：`编辑图` + `附加参考图` 一起参与，"
            "`latent 模式` 决定输出尺寸是跟随第一张参考图（锚定编辑）还是由宽高决定。\n\n"
            f"模型：`{strategy.models['unet']}`"
        )
        with gr.Row():
            with gr.Column(scale=1):
                prompt = gr.Textbox(label="提示词", lines=7)
                negative = gr.Textbox(label="负面提示词（官方推荐留空）", lines=2, value="")
                edit_image = gr.Image(label="编辑图（单张）", type="pil")
                ref_files = gr.Files(label="附加参考图（可多选）",
                                     file_count="multiple", file_types=["image"])
                preset = gr.Dropdown(preset_names, value="1:1   1024x1024",
                                     label="画幅预设（仅填充宽高）")
                with gr.Row():
                    width = gr.Number(value=1024, precision=0, label="宽 width")
                    height = gr.Number(value=1024, precision=0, label="高 height")
                max_pixels = gr.Number(value=int(caps), precision=0,
                                       label="最大像素乘积 max pixels")
                with gr.Row():
                    steps = gr.Slider(1, 60, value=int(strategy.profile.get("steps_default", 25)), step=1, label="推理步数")
                    cfg = gr.Slider(0.0, 6.0, value=1.0, step=0.1, label="CFG")
                with gr.Row():
                    seed = gr.Number(value=42, precision=0, label="Seed")
                    latent_mode = gr.Dropdown(
                        ["custom", "reference"], value="custom",
                        label="latent 模式（custom=用宽高 / reference=锚定首张参考图）",
                    )
                with gr.Row():
                    seed_btn = gr.Button("随机 Seed")
                    clear_btn = gr.Button("清空参考图")
                transparent = gr.Checkbox(
                    label="透明背景（RGBA）",
                    value=False,
                    info="在提示词外套用 Qwen 官方 RGBA 格式，输出带 alpha 通道的 PNG",
                )
                generate_btn = gr.Button("生成图片", variant="primary")
            with gr.Column(scale=1):
                output = gr.Image(label="生成结果", type="pil")
                status = gr.Textbox(label="状态", interactive=False, lines=9)

        preset.change(on_preset, inputs=preset, outputs=[width, height])
        generate_btn.click(
            ui_generate,
            inputs=[prompt, edit_image, ref_files, preset, width, height,
                    max_pixels, steps, cfg, seed, negative, latent_mode, transparent],
            outputs=[output, status],
        )
        seed_btn.click(lambda: random.randint(0, 2**31 - 1), outputs=seed)
        clear_btn.click(lambda: (None, None), outputs=[edit_image, ref_files])
    return demo


def capabilities(strategy: Strategy) -> dict:
    sizing = strategy.sizing
    return {
        "backend": "comfyui",
        "strategy": strategy.name,
        "placement": strategy.profile["placement"],
        "models": strategy.models,
        "endpoints": {
            "generate": "POST /api/generate  {prompt, negative_prompt?, images?, width?, height?, aspect?, max_pixels?, steps?, cfg?, seed?, latent_mode?, transparent?}",
            "poll": "GET /api/jobs/{job_id}",
            "image": "GET /api/jobs/{job_id}/image",
            "sync": "POST /api/generate/image (returns PNG)",
            "health": "GET /api/health",
            "capabilities": "GET /api/capabilities",
        },
        "resolution": {
            "mode": "arbitrary width/height, only a maximum pixel product is enforced",
            "multiple": sizing.SIZE_MULTIPLE,
            "min_side": sizing.MIN_SIDE,
            "max_side": sizing.MAX_SIDE,
            "default_max_pixels": sizing.DEFAULT_MAX_PIXELS,
            "current_max_pixels": sizing.max_pixels(),
            "presets": sizing.PRESETS,
        },
        "references": {
            "multiple": True,
            "max_count": 16,
            "slot_keys": "images.image_1 .. images.image_16 (dotted Autogrow keys)",
            "reference_latents": True,
            "latent_mode": {
                "custom": "output size comes from width/height",
                "reference": "output size anchored to the first reference image",
            },
        },
        "sampler": {
            "steps_default": strategy.comfy.OFFICIAL_STEPS,
            "cfg_default": strategy.comfy.OFFICIAL_CFG,
            "sampler": strategy.comfy.OFFICIAL_SAMPLER,
            "scheduler": strategy.comfy.OFFICIAL_SCHEDULER,
            "note": "official Qwen-Image-2.1 settings; no ModelSamplingAuraFlow/CFGNorm",
        },
    }
