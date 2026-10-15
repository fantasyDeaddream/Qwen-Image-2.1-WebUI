"""Shared path discovery for the Qwen-Image-2.1 WebUI strategies.

Every strategy lives in its own sub folder, so it can no longer rely on
``Path(__file__).parent`` to find the shared assets. This module implements the
search policy used by both the Python apps and the ``.bat`` launchers:

    own folder  ->  sub folders (depth <= 2)  ->  up to 6 parent folders

A hit is validated by a precise marker (a real torch install, real transformer
weights, a real ComfyUI checkout), never by folder name alone. Results are
exported as ``QWEN_*`` environment variables and reused by later calls.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------
MAX_PARENT_HOPS = 6
MAX_SUB_DEPTH = 2

SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "models_cache", "site-packages", ".cache", "outputs", "temp",
    "blobs", "snapshots", "ComfyUI", ".mypy_cache", ".pytest_cache",
}

VENV_DIR_NAMES = (".venv", "venv", "env", ".env")
MODELS_DIR_NAMES = ("models_cache", "hf_cache", os.path.join("models", "hf"), "models")
COMFY_DIR_NAMES = ("ComfyUI", "comfyui", "Comfy")

HF_REPO_DIR = "models--Qwen--Qwen-Image-2.1"
MIN_WEIGHT_BYTES = 100 * 1024 * 1024


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _ancestors(start: Path, max_up: int = MAX_PARENT_HOPS):
    cur = start.resolve()
    for _ in range(max_up):
        parent = cur.parent
        if parent == cur:
            break
        yield parent
        cur = parent


def _shallow_subdirs(start: Path, max_depth: int = MAX_SUB_DEPTH):
    frontier = [start.resolve()]
    for _ in range(max_depth):
        nxt = []
        for d in frontier:
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if e.name in SKIP_DIR_NAMES:
                    continue
                p = Path(e.path)
                yield p
                nxt.append(p)
        frontier = nxt


def search_space(start: Path):
    """Yield candidate roots: own folder, then sub folders, then parents."""
    start = Path(start).resolve()
    yield start
    yield from _shallow_subdirs(start)
    yield from _ancestors(start)


# --------------------------------------------------------------------------
# Validators
# --------------------------------------------------------------------------
def is_venv(path: Path) -> bool:
    return (path / "Scripts" / "python.exe").is_file() and (
        path / "Lib" / "site-packages" / "torch" / "__init__.py"
    ).is_file()


def is_venv_with(path: Path, *, diffusers: bool = False, comfy: bool = False) -> bool:
    if not is_venv(path):
        return False
    sp = path / "Lib" / "site-packages"
    if diffusers and not (sp / "diffusers" / "__init__.py").is_file():
        return False
    if comfy and not any(sp.glob("comfyui_frontend_package*")):
        return False
    return True


def is_models_cache(path: Path, *, need_weights: bool = False) -> bool:
    repo = path / HF_REPO_DIR
    if not _is_dir(repo):
        return False
    if not need_weights:
        return True
    snaps = repo / "snapshots"
    if not _is_dir(snaps):
        return False
    for snap in snaps.iterdir():
        tf = snap / "transformer"
        if not _is_dir(tf):
            continue
        for f in tf.glob("*.safetensors"):
            try:
                if f.stat().st_size > MIN_WEIGHT_BYTES:
                    return True
            except OSError:
                continue
    return False


def is_comfy_dir(path: Path) -> bool:
    return (
        (path / "main.py").is_file()
        and (path / "comfy" / "model_management.py").is_file()
        and (path / "comfy_extras" / "nodes_qwen.py").is_file()
    )


def has_mixed_checkpoint(comfy_dir: Path) -> bool:
    d = comfy_dir / "models" / "diffusion_models"
    if not _is_dir(d):
        return False
    for f in d.glob("*.safetensors"):
        if ("mixed" in f.name.lower() or "balanced" in f.name.lower()):
            try:
                if f.stat().st_size > MIN_WEIGHT_BYTES:
                    return True
            except OSError:
                continue
    return False


# --------------------------------------------------------------------------
# Finders
# --------------------------------------------------------------------------
def _find(start, dir_names, validator, self_ok=True):
    for root in search_space(start):
        for name in dir_names:
            cand = root / name
            if _is_dir(cand) and validator(cand):
                return cand.resolve()
        if self_ok and _is_dir(root) and validator(root):
            return root.resolve()
    return None


def find_venv(start, *, diffusers: bool = False, comfy: bool = False):
    return _find(
        start,
        VENV_DIR_NAMES,
        lambda p: is_venv_with(p, diffusers=diffusers, comfy=comfy),
    )


def find_models_cache(start, *, need_weights: bool = False):
    return _find(
        start,
        MODELS_DIR_NAMES,
        lambda p: is_models_cache(p, need_weights=need_weights),
    )


def find_comfy_dir(start):
    return _find(start, COMFY_DIR_NAMES, is_comfy_dir)


def find_project_root(start):
    """Shallowest candidate that owns at least two of the shared assets."""
    for root in search_space(start):
        score = 0
        if _is_dir(root / "models_cache"):
            score += 1
        if (root / "ComfyUI" / "main.py").is_file():
            score += 1
        if (root / ".venv" / "Scripts" / "python.exe").is_file():
            score += 1
        if (root / "shared" / "qwen_paths.py").is_file():
            score += 1
        if score >= 2:
            return root.resolve()
    return None


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
@dataclass
class Paths:
    start: Path
    project_root: Path | None = None
    venv_dir: Path | None = None
    python: Path | None = None
    models_cache: Path | None = None
    comfy_dir: Path | None = None
    output_dir: Path = field(default_factory=Path)
    notes: list = field(default_factory=list)

    @property
    def cache_dir(self) -> Path:
        return self.models_cache or (self.project_root or self.start) / "models_cache"

    @property
    def comfy(self) -> Path:
        return self.comfy_dir or (self.project_root or self.start) / "ComfyUI"

    def as_dict(self):
        return {
            "QWEN_PROJECT_ROOT": self.project_root,
            "QWEN_VENV_DIR": self.venv_dir,
            "QWEN_PYTHON": self.python,
            "QWEN_MODELS_CACHE": self.models_cache,
            "QWEN_COMFYUI_DIR": self.comfy_dir,
            "QWEN_OUTPUT_DIR": self.output_dir,
        }


def initialize(
    start=None,
    *,
    need_venv: bool = False,
    need_diffusers: bool = False,
    need_comfy_lib: bool = False,
    need_models: bool = False,
    need_weights: bool = False,
    need_comfy: bool = False,
    verbose: bool = True,
) -> Paths:
    """Resolve and export every shared path. Never downloads anything."""
    start = Path(start or Path(__file__).resolve().parent).resolve()
    p = Paths(start=start)

    def say(msg):
        if verbose:
            print(f"[INFO] {msg}", flush=True)

    p.project_root = find_project_root(start)
    if p.project_root:
        say(f"project root : {p.project_root}")

    if need_venv or need_diffusers or need_comfy_lib:
        # an explicit env var always wins, so wrappers can pin a location
        override = os.environ.get("QWEN_VENV_DIR")
        if override and is_venv_with(
            Path(override), diffusers=need_diffusers, comfy=need_comfy_lib
        ):
            p.venv_dir = Path(override)
            say(f"venv (from QWEN_VENV_DIR): {p.venv_dir}")
        else:
            p.venv_dir = find_venv(
                start, diffusers=need_diffusers, comfy=need_comfy_lib
            )
        if p.venv_dir:
            p.python = p.venv_dir / "Scripts" / "python.exe"
            say(f"python env   : {p.venv_dir}")
        else:
            p.notes.append("no suitable python environment found")

    if need_models:
        override = os.environ.get("QWEN_MODELS_CACHE")
        if override and is_models_cache(Path(override), need_weights=need_weights):
            p.models_cache = Path(override)
            say(f"model cache (from QWEN_MODELS_CACHE): {p.models_cache}")
        else:
            p.models_cache = find_models_cache(start, need_weights=need_weights)
        if p.models_cache:
            say(f"model cache  : {p.models_cache}")
        else:
            p.notes.append("no offline model cache found")

    if need_comfy:
        override = os.environ.get("QWEN_COMFYUI_DIR")
        if override and is_comfy_dir(Path(override)):
            p.comfy_dir = Path(override)
            say(f"ComfyUI (from QWEN_COMFYUI_DIR): {p.comfy_dir}")
        else:
            p.comfy_dir = find_comfy_dir(start)
        if p.comfy_dir:
            say(f"ComfyUI      : {p.comfy_dir}")
        else:
            p.notes.append("no ComfyUI checkout found")

    base = p.project_root or start
    p.output_dir = base / "outputs"
    p.output_dir.mkdir(parents=True, exist_ok=True)

    for key, value in p.as_dict().items():
        if value:
            os.environ.setdefault(key, str(value))

    return p


def describe(p: Paths) -> str:
    lines = ["resolved paths:"]
    for key, value in p.as_dict().items():
        lines.append(f"  {key:20} {value if value else '<not found>'}")
    for n in p.notes:
        lines.append(f"  note: {n}")
    return "\n".join(lines)


if __name__ == "__main__":
    here = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    res = initialize(
        here,
        need_venv=True,
        need_models=True,
        need_weights=True,
        need_comfy=True,
    )
    print(describe(res))
