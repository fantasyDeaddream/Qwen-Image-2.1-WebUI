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

---

## 0. Choose a route first

| Route | Folder(s) | Disk | Fits |
|---|---|---|---|
| Quantized + Turbo (recommended) | `turbo` | ~4.8 GB | 8 GB VRAM |
| Quantized | `mixed-vram`, `mixed` | ~3.9 GB | 8 GB VRAM |
| Full BF16 | `native` | ~30.2 GB | 16 GB VRAM or more |
| GGUF | `gguf` | ~3.9 GB | 8 GB VRAM, slower per step |

You can install more than one; they share the virtualenv and ComfyUI.

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
