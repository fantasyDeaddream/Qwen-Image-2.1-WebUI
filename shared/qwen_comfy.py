"""ComfyUI client + Qwen-Image-2.1 workflow builder, shared by the mixed
strategies.

The graph here follows the *official* Qwen-Image-2.1 templates
(``comfyui_workflow_templates_json/templates/image_qwen_image_2_1_*.json``),
not the legacy Qwen-Image (1.0) recipes. Two differences matter a lot:

1. **No ``ModelSamplingAuraFlow`` and no ``CFGNorm``.** Qwen-Image-2.1's own
   ``sampling_settings`` already carry ``shift = 0.69`` and are realised by
   ``ModelSamplingFlux``. ``ModelSamplingAuraFlow(shift=3.0)`` replaces the
   sampling object with a *different* shift function (``time_snr_shift`` instead
   of ``flux_time_shift``) at ~4.3x the intended shift, which corrupts the
   schedule. ``CFGNorm`` is an exact identity at the official ``cfg = 1``.
   Both appear only in the Qwen-Image (1.0) templates, where the shift is 1.15.

2. **Reference images are supplied through dotted Autogrow keys**
   (``"images.image_1"`` ... ``"images.image_16"``). A bare ``"image_1"`` is
   silently dropped when it holds a literal and raises a TypeError when it holds
   a link, so the dotted form is the only correct spelling.

Official sampler settings are steps 25 / cfg 1 / euler / simple / denoise 1.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

# --------------------------------------------------------------------------
# Node ids in the built graph (stable, so callers can reason about them)
# --------------------------------------------------------------------------
N_UNET = "1"
N_CLIP = "2"
N_VAE = "3"
N_CACHE = "4"
N_TEXT = "5"
N_LATENT = "6"
N_SAMPLER = "7"
N_DECODE = "8"
N_SAVE = "9"
N_COMPILE = "10"            # optional TorchCompileModel
N_LORA = "11"               # optional LoraLoaderModelOnly
N_SAMPLER_SEL = "12"        # KSamplerSelect   (explicit-sigmas path)
N_SIGMAS = "13"             # ManualSigmas     (explicit-sigmas path)
N_LOAD_FIRST = 100          # 100, 101, ... one per reference image

# TorchCompileModel backends this ComfyUI build offers. NOTE: "inductor" needs
# a working triton, which is NOT installed in this environment (it fails with
# TritonMissing), so "cudagraphs" is the only usable one here.
COMPILE_BACKENDS = ("inductor", "cudagraphs")

# Official Qwen-Image-2.1 defaults.
OFFICIAL_STEPS = 25
OFFICIAL_CFG = 1.0
OFFICIAL_SAMPLER = "euler"
OFFICIAL_SCHEDULER = "simple"

# Qwen-Image-2.1-Turbo ships this exact 8-step schedule (sample_sigmas in
# model_index.json; also quoted in unsloth's README). ComfyUI cannot read a
# schedule out of a checkpoint OR a LoRA -- KSampler always rebuilds one from
# the model's sampling settings -- so a step-distilled model has to be handed
# the grid explicitly or it silently denoises off-distribution.
# Compare: ComfyUI's own `simple` grid at 8 steps is 1.0/0.933/0.857/0.769/
# 0.666/0.545/0.399/0.222/0, i.e. nearly uniform, mean |diff| 0.148 from this.
TURBO_SIGMAS = "1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0"
TURBO_STEPS = 8

# Qwen-Image-2.1 generates transparent (RGBA) images natively, and the model card
# prescribes this exact prompt format for it. No graph change and no alpha
# post-processing is involved: the VAE decodes a 4th channel and ComfyUI's
# SaveImage keeps it (it does Image.fromarray on the raw HxWx4 tensor). Measured
# at 8 steps, 1024x1024: the bare prompt gives 0% transparent pixels, this
# format gives ~44%.
TRANSPARENT_PREFIX = "This is an RGBA image with transparency. "
TRANSPARENT_SUFFIX = ". The image has alpha channel and the background is transparent."


def transparent_prompt(prompt: str) -> str:
    """Wrap a prompt in the RGBA format Qwen-Image-2.1 was trained for."""
    return f"{TRANSPARENT_PREFIX}{prompt.strip().rstrip('.')}{TRANSPARENT_SUFFIX}"

TEXT_ENCODER_FILE = "qwen3vl_8b_int8_convrot.safetensors"
VAE_FILE = "qwen_image_2.1_vae_bf16.safetensors"


class ComfyError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------
class ComfyClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8188, *, comfy_dir=None):
        self.host = host
        self.port = int(port)
        self.comfy_dir = Path(comfy_dir) if comfy_dir else None
        self.process: subprocess.Popen | None = None

    # ---- plumbing --------------------------------------------------------
    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _request(self, path, data=None, method=None, timeout=60, raw=False):
        url = self.base + path
        body = None
        headers = {}
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=body, headers=headers,
            method=method or ("POST" if data is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ComfyError(f"ComfyUI HTTP {exc.code} on {path}: {detail[:800]}") from exc
        if raw:
            return payload
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            return {"raw": payload.decode("utf-8", errors="replace")}

    # ---- lifecycle -------------------------------------------------------
    def alive(self, timeout=4) -> bool:
        try:
            self._request("/system_stats", timeout=timeout)
            return True
        except Exception:
            return False

    def start(self, *, python: str, extra_args=(), env_extra=None, log_path=None,
              timeout=300, verbose=True) -> bool:
        """Launch ComfyUI if it is not already answering. Returns True if we
        started it ourselves."""
        if self.alive():
            if verbose:
                print("[comfy] reusing the running backend", flush=True)
            return False
        if not self.comfy_dir or not (self.comfy_dir / "main.py").is_file():
            raise ComfyError(
                f"找不到 ComfyUI/main.py：{self.comfy_dir}\n"
                "请先运行 setup\\setup_comfyui.bat"
            )
        cmd = [python, str(self.comfy_dir / "main.py"),
               "--listen", self.host, "--port", str(self.port), *extra_args]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if env_extra:
            env.update(env_extra)
        if verbose:
            print(f"[comfy] starting: {' '.join(cmd)}", flush=True)
        out = open(log_path, "ab") if log_path else None
        self.process = subprocess.Popen(
            cmd, cwd=str(self.comfy_dir), env=env,
            stdout=out if out else None, stderr=subprocess.STDOUT if out else None,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise ComfyError(
                    f"ComfyUI 启动后立即退出 (exit {self.process.returncode})。"
                    + (f" 日志：{log_path}" if log_path else "")
                )
            if self.alive(timeout=3):
                if verbose:
                    print("[comfy] backend ready", flush=True)
                return True
            time.sleep(1.0)
        raise ComfyError(f"ComfyUI 在 {timeout}s 内未就绪")

    def stop(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=8)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None

    # ---- discovery -------------------------------------------------------
    def object_info(self, node: str | None = None):
        path = f"/object_info/{node}" if node else "/object_info"
        return self._request(path, timeout=60)

    def available(self, folder: str):
        try:
            return self._request(f"/models/{folder}", timeout=30)
        except Exception:
            return []

    def autogrow_keys(self, node: str, field: str) -> list:
        """Read the dotted keys an Autogrow input accepts.

        ``/object_info`` publishes the *template*, not the expanded keys, so a
        client has to join the field name with ``template.names``.
        """
        info = self.object_info(node).get(node, {})
        spec = (info.get("input", {}).get("required", {}) or {}).get(field)
        if not spec or not isinstance(spec, list) or len(spec) < 2:
            return []
        template = (spec[1] or {}).get("template") or {}
        names = template.get("names") or []
        return [f"{field}.{n}" for n in names]

    def system_stats(self):
        return self._request("/system_stats", timeout=20)

    def vram(self):
        try:
            dev = (self.system_stats().get("devices") or [])[0]
            return int(dev.get("vram_total", 0)), int(dev.get("vram_free", 0))
        except Exception:
            return 0, 0

    # ---- image io --------------------------------------------------------
    def upload_image(self, image: Image.Image, filename: str | None = None) -> str:
        buf = io.BytesIO()
        image.convert("RGBA").save(buf, format="PNG")
        raw = buf.getvalue()
        boundary = ("----QwenComfy" + uuid.uuid4().hex).encode("ascii")
        name = filename or f"qwen_ref_{uuid.uuid4().hex[:10]}.png"
        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="image"; filename="' + name.encode() + b'"\r\n'
            b"Content-Type: image/png\r\n\r\n" + raw + b"\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="type"\r\n\r\ninput\r\n'
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
            b"--" + boundary + b"--\r\n"
        )
        req = urllib.request.Request(
            self.base + "/upload/image", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ComfyError(
                f"上传参考图失败：HTTP {exc.code} {exc.read().decode(errors='replace')[:400]}"
            ) from exc
        if "name" not in result:
            raise ComfyError(f"上传接口返回异常：{result}")
        sub = result.get("subfolder") or ""
        return f"{sub}/{result['name']}" if sub else result["name"]

    # ---- execution -------------------------------------------------------
    def queue(self, graph: dict, client_id: str | None = None) -> str:
        payload = {"prompt": graph, "client_id": client_id or uuid.uuid4().hex}
        result = self._request("/prompt", data=payload, method="POST", timeout=120)
        if "error" in result:
            raise ComfyError("ComfyUI 拒绝该工作流：\n"
                             + json.dumps(result, ensure_ascii=False, indent=2)[:2000])
        pid = result.get("prompt_id")
        if not pid:
            raise ComfyError(f"/prompt 未返回 prompt_id：{result}")
        return pid

    def wait_result(self, prompt_id: str, *, timeout=3600, on_progress=None):
        """Poll until the prompt finishes. Returns (PIL image, info dict)."""
        deadline = time.time() + timeout
        last_step = -1
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise ComfyError("ComfyUI 进程已退出，请查看日志")
            history = self._request(
                f"/history/{urllib.parse.quote(prompt_id)}", timeout=30
            )
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {}) or {}
                if status.get("status_str") == "error":
                    raise ComfyError(
                        "ComfyUI 生成失败：\n"
                        + json.dumps(status.get("messages", []), ensure_ascii=False)[:2000]
                    )
                for node_out in (entry.get("outputs") or {}).values():
                    for item in node_out.get("images", []) or []:
                        params = urllib.parse.urlencode({
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        })
                        raw = self._request("/view?" + params, timeout=180, raw=True)
                        img = Image.open(io.BytesIO(raw))
                        img.load()
                        return img.convert("RGBA"), {
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "comfy_status": status,
                        }
                if status.get("completed"):
                    raise ComfyError("ComfyUI 已完成但没有输出图片")
            if on_progress:
                try:
                    q = self._request("/prompt", timeout=15)
                    on_progress(stage=f"queue_remaining={q.get('exec_info', {}).get('queue_remaining')}")
                except Exception:
                    pass
            time.sleep(0.7)
            last_step += 1
        raise ComfyError(f"生成超时（{timeout}s）")

    def interrupt(self):
        try:
            self._request("/interrupt", data={}, method="POST", timeout=15)
        except Exception:
            pass

    def free_cache(self, *, unload_models: bool = False):
        """Release ComfyUI's allocator cache while keeping models resident.

        ``unload_models=False`` is the important part: the text encoder,
        diffusion model and VAE stay loaded, so the next request skips the
        ~70-100 s reload. Only transient buffers are returned.
        """
        return self._request(
            "/free",
            data={"unload_models": bool(unload_models), "free_memory": True},
            method="POST",
            timeout=60,
        )

    def unload_models(self):
        """Hard release of every model -- used by the stop scripts only."""
        try:
            return self.free_cache(unload_models=True)
        except Exception:
            return None


# --------------------------------------------------------------------------
# workflow builder
# --------------------------------------------------------------------------
def build_graph(
    *,
    prompt: str,
    negative_prompt: str = "",
    width: int,
    height: int,
    steps: int = OFFICIAL_STEPS,
    cfg: float = OFFICIAL_CFG,
    seed: int = 0,
    unet_name: str,
    clip_name: str = TEXT_ENCODER_FILE,
    vae_name: str = VAE_FILE,
    reference_names: list | None = None,
    latent_mode: str = "custom",
    clip_device: str = "default",
    cache_device: str = "auto",
    cache_dtype: str = "default",
    reference_resolution: int = 0,
    filename_prefix: str = "QwenImage21",
    sampler: str = OFFICIAL_SAMPLER,
    scheduler: str = OFFICIAL_SCHEDULER,
    denoise: float = 1.0,
    torch_compile: str | None = None,
    lora_name: str | None = None,
    lora_strength: float = 1.0,
    unet_loader: str = "UNETLoader",
    sigmas: str | None = None,
) -> dict:
    """Build an API-format Qwen-Image-2.1 graph.

    ``latent_mode``:
        ``"custom"``    -> ``EmptyLatentImage(width, height)``; the caller owns
                           the output size completely. This is what makes an
                           arbitrary pixel ratio possible, including when
                           reference images are attached.
        ``"reference"`` -> the ``TextEncodeQwenImage21`` latent output, which is
                           an empty latent on the *first reference image's*
                           size. That is the anchored-edit behaviour (output
                           size follows the reference).

    ``torch_compile``: ``None`` to skip, or a ``COMPILE_BACKENDS`` value to
        insert a ``TorchCompileModel`` between the cache node and the sampler.
        Requires a warm-up run: the first execution pays the compile cost.

    ``lora_name``: optional LoRA applied with ``LoraLoaderModelOnly`` between
        the loader and the cache node. Used by the turbo strategy to put the
        Turbo distillation on top of the existing quantized checkpoint instead
        of downloading a second 4-14 GiB model.

    ``unet_loader``: ``"UNETLoader"`` (default) or ``"UnetLoaderGGUF"``, which
        comes from the ComfyUI-GGUF custom node and takes no ``weight_dtype``.
    """
    reference_names = list(reference_names or [])
    if unet_loader == "UnetLoaderGGUF":
        unet_node = {"class_type": "UnetLoaderGGUF",
                     "inputs": {"unet_name": unet_name}}
    else:
        unet_node = {"class_type": "UNETLoader",
                     "inputs": {"unet_name": unet_name, "weight_dtype": "default"}}
    pre_cache = N_LORA if lora_name else N_UNET
    graph: dict = {
        N_UNET: unet_node,
        N_CLIP: {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": clip_name, "type": "qwen_image",
                            "device": clip_device}},
        N_VAE: {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        # Explicit is better than implicit: an absent QwenImage21Cache node is
        # equivalent to auto+default, but stating it makes the intent auditable.
        N_CACHE: {"class_type": "QwenImage21Cache",
                  "inputs": {"model": [pre_cache, 0], "device": cache_device,
                             "dtype": cache_dtype}},
        # Either the stock KSampler, or -- when an explicit sigma grid is given
        # -- SamplerCustom fed by ManualSigmas. Both live on node N_SAMPLER so
        # everything downstream is identical.
        N_SAMPLER: (
            {"class_type": "SamplerCustom",
             "inputs": {"model": [N_CACHE, 0], "add_noise": True,
                        "noise_seed": int(seed), "cfg": float(cfg),
                        "positive": [N_TEXT, 0], "negative": [N_TEXT, 1],
                        "sampler": [N_SAMPLER_SEL, 0], "sigmas": [N_SIGMAS, 0],
                        "latent_image": [N_LATENT, 0]}}
            if sigmas else
            {"class_type": "KSampler",
             "inputs": {"model": [N_CACHE, 0], "seed": int(seed),
                        "steps": int(steps), "cfg": float(cfg),
                        "sampler_name": sampler, "scheduler": scheduler,
                        "denoise": float(denoise)}}
        ),
        N_DECODE: {"class_type": "VAEDecode",
                   "inputs": {"samples": [N_SAMPLER, 0], "vae": [N_VAE, 0]}},
        N_SAVE: {"class_type": "SaveImage",
                 "inputs": {"images": [N_DECODE, 0], "filename_prefix": filename_prefix}},
        # No ModelSamplingAuraFlow, no CFGNorm -- see the module docstring.
    }

    # ---- optional LoRA between the loader and the cache node --------------
    if lora_name:
        graph[N_LORA] = {"class_type": "LoraLoaderModelOnly",
                         "inputs": {"model": [N_UNET, 0], "lora_name": lora_name,
                                    "strength_model": float(lora_strength)}}

    # ---- exact sigma grid (distilled checkpoints) -------------------------
    # A distilled model is trained for one specific noise schedule. ComfyUI
    # cannot read that schedule out of a checkpoint or a LoRA, and KSampler
    # always rebuilds a grid from the model's sampling settings, so a step
    # distillation can silently run off-distribution. ManualSigmas lets us feed
    # the shipped grid verbatim. SamplerCustom also calls
    # fix_empty_latent_channels() itself, so a plain EmptyLatentImage is fine.
    if sigmas:
        graph[N_SAMPLER_SEL] = {"class_type": "KSamplerSelect",
                                "inputs": {"sampler_name": sampler}}
        graph[N_SIGMAS] = {"class_type": "ManualSigmas",
                           "inputs": {"sigmas": sigmas}}

    # ---- optional torch.compile of the diffusion model -------------------
    # Sits after the cache node so the cache's transformer_options survive.
    model_source = N_CACHE
    if torch_compile:
        if torch_compile not in COMPILE_BACKENDS:
            raise ValueError(
                f"torch_compile must be one of {COMPILE_BACKENDS}, got {torch_compile!r}"
            )
        graph[N_COMPILE] = {"class_type": "TorchCompileModel",
                            "inputs": {"model": [N_CACHE, 0],
                                       "backend": torch_compile}}
        model_source = N_COMPILE
    graph[N_SAMPLER]["inputs"]["model"] = [model_source, 0]

    # ---- reference images ------------------------------------------------
    text_inputs: dict = {
        "clip": [N_CLIP, 0],
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "resolution": int(reference_resolution),
    }
    for index, name in enumerate(reference_names):
        nid = str(N_LOAD_FIRST + index)
        graph[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
        if index == 0:
            # the VAE is what turns references into reference latents
            text_inputs["vae"] = [N_VAE, 0]
        text_inputs[f"images.image_{index + 1}"] = [nid, 0]
    graph[N_TEXT] = {"class_type": "TextEncodeQwenImage21", "inputs": text_inputs}
    graph[N_SAMPLER]["inputs"]["positive"] = [N_TEXT, 0]
    graph[N_SAMPLER]["inputs"]["negative"] = [N_TEXT, 1]

    # ---- target latent ---------------------------------------------------
    if latent_mode == "reference":
        if not reference_names:
            raise ValueError("latent_mode='reference' 需要至少一张参考图")
        graph[N_SAMPLER]["inputs"]["latent_image"] = [N_TEXT, 2]
    else:
        graph[N_LATENT] = {"class_type": "EmptyLatentImage",
                           "inputs": {"width": int(width), "height": int(height),
                                      "batch_size": 1}}
        graph[N_SAMPLER]["inputs"]["latent_image"] = [N_LATENT, 0]

    return graph


def describe_graph(graph: dict) -> str:
    lines = []
    for nid in sorted(graph, key=lambda x: (len(x), x)):
        node = graph[nid]
        ins = {k: v for k, v in node["inputs"].items()}
        lines.append(f"  [{nid}] {node['class_type']}  {json.dumps(ins, ensure_ascii=False)}")
    return "\n".join(lines)


def extra_model_dirs(comfy_dir) -> dict:
    """Parse ComfyUI's ``extra_model_paths.yaml`` -> ``{folder: [Path, ...]}``.

    ComfyUI honours this file itself, but ``local_checkpoint`` scans the
    filesystem directly. Without this, an isolated checkout that references
    another models directory (as the gguf strategy does, to avoid duplicating
    the 8.7 GiB text encoder) looks empty to us even though ComfyUI finds
    everything. Returns ``{}`` when the file is absent or unreadable.
    """
    cfg = Path(comfy_dir) / "extra_model_paths.yaml"
    if not cfg.is_file():
        return {}
    try:
        import yaml  # PyYAML ships with ComfyUI
        data = yaml.safe_load(cfg.read_text(encoding="utf-8-sig")) or {}  # Notepad adds a BOM
    except Exception:
        return {}
    out: dict = {}
    for spec in data.values():
        if not isinstance(spec, dict):
            continue
        base = spec.get("base_path")
        if not base:
            continue
        for folder, sub in spec.items():
            if folder in ("base_path", "is_default") or not isinstance(sub, str):
                continue
            p = Path(sub)
            if not p.is_absolute():
                p = Path(base) / p
            if p.is_dir():
                out.setdefault(folder, []).append(p)
    return out


def local_checkpoint(comfy_dir, folder: str, *, prefer=("mixed", "balanced"),
                     extra_roots=()) -> str | None:
    """Pick a checkpoint from a ComfyUI models folder, preferring mixed/balanced.

    ``extra_roots`` are additional directories to search, which is how the
    isolated gguf checkout reaches the primary checkout's text encoder and VAE.
    """
    dirs = [Path(comfy_dir) / "models" / folder] + [Path(r) for r in extra_roots]
    files = []
    for d in dirs:
        if d.is_dir():
            files += [f for f in sorted(d.glob("*.safetensors"))
                      if f.stat().st_size > 100 * 1024 * 1024]
    for token in prefer:
        for f in files:
            if token and token in f.name.lower():
                return f.name
    return files[0].name if files else None
