# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Qwen-Image-2.1 launcher.

Two builds come out of this one spec:

  development  -- run launcher/app.py straight from the checkout; the PowerShell
                  scripts and strategy apps stay as loose files that are easy to
                  edit. Nothing below the datas list matters there.

  distribution -- app.py is frozen together with EVERY script the launcher needs
                  (see core.BUNDLED_FILES), so the whole product ships as one
                  .exe. On first run those files are unpacked next to the
                  executable, because PowerShell and the project venv need real
                  paths -- an EXE cannot hand them an in-memory module. The
                  unpacker copies per file and never deletes, so replacing the
                  EXE refreshes only the launcher's own scripts and leaves
                  .venv / ComfyUI / models_cache / outputs alone.

      pyinstaller --clean --noconfirm launcher\\qwen_launcher.spec
"""
from pathlib import Path

HERE = Path(SPECPATH).resolve()          # .../Qwen-Image-2.1-WebUI/launcher
PROJECT = HERE.parent                    # .../Qwen-Image-2.1-WebUI
ICON = HERE / "icon.ico"

# must stay in step with core.BUNDLED_FILES
BUNDLED = [
    "shared/QwenEnv.ps1",
    "shared/mirrors.json",
    "shared/qwen_launch.ps1",
    "shared/qwen_stop.ps1",
    "shared/qwen_api.py",
    "shared/qwen_comfy.py",
    "shared/qwen_comfy_app.py",
    "shared/qwen_paths.py",
    "shared/qwen_sizing.py",
    "setup/qwen_setup.ps1",
    "mixed/app.py",
    "mixed-vram/app.py",
    "turbo/app.py",
    "gguf/app.py",
    "native/app.py",
    "USER_GUIDE.md",
]

datas = []
if ICON.is_file():
    datas.append((str(ICON), "."))
missing = []
for rel in BUNDLED:
    src = (HERE / rel) if rel == "USER_GUIDE.md" else (PROJECT / rel)
    if src.is_file():
        datas.append((str(src), "bundle/" + str(Path(rel).parent).replace("\\", "/")))
    else:
        missing.append(rel)
if missing:
    raise SystemExit("bundle source missing: " + ", ".join(missing))


a = Analysis(
    [str(HERE / "app.py")],
    pathex=[str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=["core", "theme", "agent", "agent_panel"],
    hookspath=[],
    runtime_hooks=[],
    # torch/diffusers live in the project venv and are used by the strategy apps,
    # never by this launcher; excluding them keeps the EXE around 45 MB.
    # PIL is deliberately NOT excluded: the launcher decodes reference images.
    excludes=[
        "torch", "torchvision", "torchaudio", "diffusers", "transformers",
        "numpy", "scipy", "safetensors", "gradio", "fastapi", "uvicorn",
        "matplotlib", "tkinter", "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtQml", "PyQt5.QtQuick", "PyQt5.Qt3DCore",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="QwenImageLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI only; all output goes to the in-app console pane
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.is_file() else None,
)
