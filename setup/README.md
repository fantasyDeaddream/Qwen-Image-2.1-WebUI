# Installation

This folder is the **only installation entry point**. Every step is a thin wrapper around
one PowerShell script, so the `.bat` files and the GUI launcher always take the same code
path.

---

## Recommended: the interactive wizard

```
setup_wizard.bat
```

It detects any environment that already exists, tells you which route fits your hardware, and
then calls the individual scripts for you. Non-interactive use is supported:

```powershell
.\setup\qwen_wizard.ps1 --plan mixed,turbo --yes
```

Available plans: `env`, `comfyui`, `mixed`, `turbo`, `native`, `fix-torch`. When called with
arguments the wizard does not pause at the end, so it can be scripted. Turbo GGUF is not part
of the wizard — it needs its own isolated ComfyUI checkout, see `gguf/README.md`.

---

## 0. Choose a route first

| Route | Folder(s) | Disk | Fits |
|---|---|---|---|
| Quantized + Turbo (recommended) | `turbo` | ~4.8 GB | 8 GB VRAM |
| Quantized | `mixed-vram`, `mixed` | ~3.9 GB | 8 GB VRAM |
| Full BF16 | `native` | ~30.2 GB | 16 GB VRAM or more |
| GGUF (control / 4–6 GB fallback) | `gguf` | ~3.9 GB | slower and lower quality — see `gguf/README.md` |

You can install more than one; they share the virtualenv and ComfyUI.

### Model sources and sizes (verified against the repository APIs)

Every size is an exact byte count from the ModelScope / HuggingFace file API, not an estimate.

| File | Size | Source |
|---|---|---|
| `text_encoders/qwen3vl_8b_int8_convrot.safetensors` | 9,350,798,360 B (8.709 GiB) | ModelScope `Comfy-Org/Qwen-Image-2.1` |
| `text_encoders/qwen3vl_8b_w4a8.safetensors` (optional) | 6,312,105,364 B (5.879 GiB) | ModelScope `Comfy-Org/Qwen-Image-2.1` |
| `vae/qwen_image_2.1_vae_bf16.safetensors` | 675,509,688 B (0.629 GiB) | ModelScope `Comfy-Org/Qwen-Image-2.1` |
| `diffusion_models/qwen_image_2.1_mixed_balanced.safetensors` (preferred) | 4,203,397,480 B (3.915 GiB) | **HuggingFace only**: `NidAll/Qwen-Image-2.1-Mixed-Balanced` |
| `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` (fallback) | 7,256,783,064 B (6.758 GiB) | ModelScope `Comfy-Org/Qwen-Image-2.1` |
| `diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors` | 7,256,787,829 B (6.758 GiB) | ModelScope `Comfy-Org/Qwen-Image-2.1` |
| `loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors` | 913,314,512 B (0.851 GiB) | ModelScope `Comfy-Org/Qwen-Image-2.1` |
| Full native BF16 set (29 files) | 33,134,789,600 B (30.86 GiB) | ModelScope `Qwen/Qwen-Image-2.1` |
| `qwen_image_2.1_turbo_Q4_K_M.gguf` | 4,189,346,592 B (3.90 GiB) | ModelScope `Abiray/Qwen-Image-2.1-Turbo-GGUF` |

**The W4 transformer has a single source, and that is a documented risk.** The
`NidAll/Qwen-Image-2.1-Mixed-Balanced` repository is not on ModelScope (HTTP 404), and neither
ModelScope nor HuggingFace copies of `Comfy-Org/Qwen-Image-2.1` contain that file. So:

- if the W4 file is already present locally it is **never re-downloaded** (the installer
  matches `*mixed*balanced*` and verifies the byte count);
- if it is missing, the installer **falls back to INT8 ConvRot (6.758 GiB) and warns
  explicitly** — this is a deliberate 2.8 GiB difference, not a silent substitution. On an
  8 GB card, keep the W4 file in `ComfyUI\models\diffusion_models\`;
- HuggingFace reachability is not reliable everywhere (it was unreachable from the reference
  machine on 2026-10-12 and reachable, at 0.37–0.58 MB/s, on 2026-10-17). If you depend on the
  W4 file, keep your own copy.

**Optional smaller text encoder (5.879 GiB).** Set
`QWEN_TEXT_ENCODER=qwen3vl_8b_w4a8.safetensors` before running `setup_models_mixed.bat`: the
download step and the runtime read the same variable, so both use the W4A8 encoder. It saves
2.83 GiB of disk and RAM compared with the INT8 encoder. Its quality difference is not
publicly measured (no LPIPS data is published for this file).

---

## 1. Order of operations

### First, an optional health check

```
status.bat
```

Reports what is present and where: virtualenv, torch version, ComfyUI, model cache, and each
model file. Run it any time something looks wrong.

### Quantized route (`mixed` / `mixed-vram` / `turbo`)

```
setup_env.bat            # virtualenv + torch 2.10.0 / cu130 + app dependencies
setup_comfyui.bat        # ComfyUI, patched for Qwen-Image-2.1
setup_models_mixed.bat   # quantized weights + text encoder + VAE
```

Add the Turbo layer if you want 8-step sampling:

```
setup_models_mixed.bat -WithGguf     # also pulls the Turbo int8 weights and LoRA
```

### Full BF16 route (`native`)

```
setup_env.bat
setup_comfyui.bat        # still required: the tokenizer and graph live there
setup_models_native.bat  # ~30 GB from ModelScope
```

---

## 2. What each script does

| Script | Action |
|---|---|
| `setup_env.bat` | Creates the virtualenv if needed, then installs torch/torchvision/torchaudio from the CUDA 13.0 index plus the app requirements |
| `setup_comfyui.bat` | Clones ComfyUI, verifies it exposes the Qwen-Image-2.1 nodes, and installs its requirements with torch filtered out |
| `setup_models_mixed.bat` | Downloads the quantized transformer, the int8 text encoder and the BF16 VAE |
| `setup_models_native.bat` | Downloads the full BF16 set |
| `fix_torch.bat` | Repairs a broken torch/torchaudio pair (reinstalls both from the cu130 index) |
| `status.bat` | Reports the resolved paths and what is present |

Everything funnels through `qwen_setup.ps1`, which is also usable directly:

```powershell
.\setup\qwen_setup.ps1 -Action probe                  # machine-readable state, for the GUI
.\setup\qwen_setup.ps1 -Action env
.\setup\qwen_setup.ps1 -Action comfyui
.\setup\qwen_setup.ps1 -Action models-mixed -WithGguf
.\setup\qwen_setup.ps1 -Action models-native
.\setup\qwen_setup.ps1 -Action status
.\setup\qwen_setup.ps1 -Action fix-torch
```

Useful switches: `-InstallRoot <dir>` puts the virtualenv, ComfyUI and model cache inside a
folder you choose instead of beside the scripts; `-StartDir <dir>` anchors the search;
`-Yes` skips the download confirmations.

### Existing content is detected, not re-downloaded

Discovery walks the script folder, its subfolders and several parent folders, so a second run
finds what the first one created. Located directories are exported as `QWEN_*` environment
variables and cached in `.qwen_env.json`, which means an install kept in a completely
different tree still gets found on the next run.

Explicit overrides win over the search, in this order:

```
environment variable  >  cache  >  directory search
```

so `QWEN_VENV_DIR`, `QWEN_COMFYUI_DIR` and `QWEN_MODELS_CACHE` are the way to force a
specific location.

### Downloads are size-checked

Every artifact is verified against its known size before it counts as installed. A file whose
transfer was interrupted keeps its name, so a name match alone is not proof: the installers
demand most of the real size, and the sharded native set additionally has every shard in its
index confirmed on disk. Re-running an install therefore resumes rather than skipping.

---

## 3. Network and mirrors

Measured from this project's reference machine (China, 2026-10):

| Purpose | Endpoint | Notes |
|---|---|---|
| pip | `mirrors.cloud.tencent.com/pypi/simple` | fastest measured |
| PyTorch wheels | `download.pytorch.org/whl/cu130` | CUDA 13.0 build |
| Models | `modelscope.cn` | `huggingface.co` and `hf-mirror.com` were unreachable |
| Python installer | `mirrors.huaweicloud.com/python` | only used when 3.11 is absent |
| ComfyUI | GitHub, with `gh-proxy.com` as fallback | a Gitee mirror was rejected: it lagged and lacked the Qwen-Image-2.1 nodes |

These live in `shared/mirrors.json` — edit that file to point at your own mirrors. Python and
Git are bootstrapped automatically if missing; nothing is downloaded without either an
explicit yes or `-Yes`.

---

## 4. Troubleshooting

**"请先配置 Python 环境" / "configure the Python environment first"**
The step needs a virtualenv and could not find one. Run `setup_env.bat`, or point step 2 of
the GUI at an existing environment.

**An install reports a component as missing though the file is there**
It is probably truncated. Re-run the same step; the size check will detect and re-fetch it.

**PowerShell reports `NativeCommandError` with a Python traceback**
On Windows PowerShell 5.1, a native command writing to stderr becomes a *terminating* error
while `$ErrorActionPreference` is `Stop`. The scripts route every interpreter probe through
`Invoke-PyProbe`, which suppresses that. If you see it in your own scripts, do the same.

**Parsing fails with `Missing expression after ','` for no reason**
A `.ps1` file lost its UTF-8 BOM. Windows PowerShell 5.1 then reads it as ANSI, and every CJK
string turns into mojibake that breaks the parser. Run `tests/check_bom.py --fix`.

**A file path contains spaces or non-ASCII characters**
Supported, and covered by `tests/verify.py`.
