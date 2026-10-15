"""Backend for the Qwen-Image-2.1 launcher GUI.

Everything here is a thin, honest wrapper around the scripts that already exist:
detection goes through ``qwen_setup.ps1 -Action probe`` (same code path as the
CLI), installation through ``qwen_setup.ps1 -Action ...``, start/stop through
``qwen_launch.ps1`` / ``qwen_stop.ps1``. The GUI adds a face and hardware advice,
not a second implementation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.request
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BEGIN = "###QWEN-JSON-BEGIN###"
END = "###QWEN-JSON-END###"


def launcher_dir() -> Path:
    """Where this component lives.

    Under PyInstaller ``__file__`` points into the temporary _MEIPASS extraction
    directory, so the frozen build has to resolve from the executable instead --
    otherwise it would look for the project root inside a temp folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# Scripts the launcher shells out to. In the distribution build every one of
# these is packed into the EXE and unpacked next to it on first run, so the user
# only ever handles a single .exe. In the development checkout they are used
# straight from the working tree and nothing is copied.
BUNDLED_FILES = (
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
)


def _bundle_root() -> Path | None:
    """Where PyInstaller unpacked the bundled scripts, or None when unfrozen."""
    mp = getattr(sys, "_MEIPASS", None)
    if not mp:
        return None
    root = Path(mp) / "bundle"
    return root if root.is_dir() else None


def sync_bundle() -> list:
    """Materialise the bundled scripts beside the EXE. Returns what changed.

    Copies file by file, only when the content differs. It never deletes
    anything, so a running install (``.venv``, ``ComfyUI``, ``models_cache``,
    ``outputs``, the isolated ``gguf/ComfyUI``) is untouched when the EXE is
    replaced with a newer build -- only the launcher's own scripts refresh.
    """
    src_root = _bundle_root()
    if src_root is None:
        return []                       # development checkout: nothing to do
    written = []
    for rel in BUNDLED_FILES:
        src = src_root / rel
        if not src.is_file():
            continue
        dst = launcher_dir() / rel
        try:
            if dst.is_file() and dst.read_bytes() == src.read_bytes():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            written.append(rel)
        except OSError:
            pass                        # read-only location: fall through
    return written


def user_guide() -> str:
    """The bundled user guide, for the in-app help window."""
    p = launcher_dir() / "USER_GUIDE.md"
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            pass
    return "使用说明文件缺失。请重新运行 QwenImageLauncher.exe 以释放脚本。"


def bundling() -> bool:
    """True when this build carries its own scripts."""
    return bool(getattr(sys, "frozen", False) and _bundle_root() is not None)


def find_project_root(start: Path | None = None) -> Path:
    """The directory that owns shared/QwenEnv.ps1.

    Frozen build: the EXE's own folder, after unpacking the bundled scripts there.
    Development: walk up to the checkout, exactly as before.
    """
    if getattr(sys, "frozen", False):
        sync_bundle()
        here = launcher_dir()
        if (here / "shared" / "QwenEnv.ps1").is_file():
            return here
    here = (start or launcher_dir()).resolve()
    for cand in [here, *here.parents]:
        if (cand / "shared" / "QwenEnv.ps1").is_file():
            return cand
    raise SystemExit(
        "找不到运行脚本（shared\\QwenEnv.ps1）。\n"
        "开发版请把 launcher 放在 Qwen-Image-2.1-WebUI 目录内；\n"
        "分发版请确认 EXE 所在目录可写——脚本会在首次运行时释放到那里。"
    )


PROJECT = find_project_root()
SETUP = PROJECT / "setup" / "qwen_setup.ps1"
LAUNCH = PROJECT / "shared" / "qwen_launch.ps1"
STOP = PROJECT / "shared" / "qwen_stop.ps1"


def ps_exe() -> str:
    """Prefer PowerShell 7, fall back to Windows PowerShell 5.1.

    Both are supported: a clean Windows install only has 5.1, which reads
    BOM-less UTF-8 as ANSI and mangles every Chinese string in these scripts
    ("Missing expression after ','" out of nowhere), so the .ps1 files carry an
    explicit UTF-8 BOM. Set QWEN_PS_EXE to force one, e.g. to reproduce a
    machine that has no pwsh.
    """
    forced = (os.environ.get("QWEN_PS_EXE") or "").strip()
    if forced:
        found = shutil.which(forced)
        if not found and Path(forced).is_file():
            found = forced
        if found:
            return found
    for name in ("pwsh.exe", "powershell.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("找不到 PowerShell（pwsh 或 powershell 均可）。")


# ---------------------------------------------------------------------------
#  process helpers
# ---------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000


def _popen(args, env=None, cwd=None):
    return subprocess.Popen(
        args, cwd=str(cwd or PROJECT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=CREATE_NO_WINDOW,
    )


def stream(args, on_line=None, env=None, cwd=None):
    """Run to completion, forwarding each output line. Returns the exit code."""
    proc = _popen(args, env=env, cwd=cwd)
    for line in proc.stdout:
        if on_line:
            on_line(line.rstrip("\r\n"))
    return proc.wait()


def capture(args, env=None):
    proc = _popen(args, env=env)
    out, _ = proc.communicate()
    # PowerShell 5.1 prefixes redirected output with a UTF-8 BOM; json.loads
    # rejects it outright ("Unexpected UTF-8 BOM"), so drop it here.
    return proc.returncode, (out or "").lstrip("\ufeff")


def path_overrides(state: dict | None = None, manual: dict | None = None) -> dict:
    """Build the QWEN_* override set from a probe state plus manual entries.

    These are what let a script find an install that lives in a different tree
    from the executable: QwenEnv.ps1 gives a valid QWEN_VENV_DIR/QWEN_COMFYUI_DIR
    top priority over its own directory search.
    """
    out: dict = {}
    for key, path in (("QWEN_VENV_DIR", (state or {}).get("venv")),
                      ("QWEN_COMFYUI_DIR", (state or {}).get("comfyui")),
                      ("QWEN_MODELS_CACHE", (state or {}).get("models_cache"))):
        val = (path or {}).get("path") if isinstance(path, dict) else path
        if val:
            out[key] = str(val)
    for key in ("QWEN_VENV_DIR", "QWEN_COMFYUI_DIR", "QWEN_MODELS_CACHE"):
        val = (manual or {}).get(key)
        if val:
            out[key] = str(val)
    return out


def build_env(state: dict | None = None, install_root: str | None = None,
              manual: dict | None = None) -> dict:
    """Child environment with the resolved paths pinned.

    QwenEnv.ps1 honours QWEN_VENV_DIR / QWEN_COMFYUI_DIR / QWEN_MODELS_CACHE as
    explicit overrides, which is how an install placed in a user-chosen folder is
    found again from the project tree. `manual` wins over what the probe found,
    because it is what the user actually typed.
    """
    env = dict(os.environ)
    root = install_root or (state or {}).get("install_root") or (state or {}).get("project_root")
    env.update(path_overrides(state, manual))
    if root:
        env["QWEN_PROJECT_ROOT"] = root
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------------------
#  detection
# ---------------------------------------------------------------------------
def probe(install_root: str | None = None, on_line=None, manual: dict | None = None) -> dict:
    """Run -Action probe and return its state dict.

    The result is read from ``.qwen_probe.json`` when possible. That file is
    written with an explicit encoding, so a machine whose console codepage turns
    CJK paths into undecodable bytes (Windows PowerShell 5.1 on a Chinese
    Windows) still reports correct paths. Stdout stays as a fallback.
    """
    search = install_root or str(PROJECT)
    probe_file = Path(search) / ".qwen_probe.json"
    try:
        probe_file.unlink()
    except OSError:
        pass

    args = [ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SETUP),
            "-Action", "probe", "-StartDir", search]
    if install_root:
        args += ["-InstallRoot", install_root]
    rc, out = capture(args, env=build_env(install_root=install_root, manual=manual))

    if on_line:
        # surface the human-readable part so the user can see what was searched
        cut = out.find(BEGIN)
        for line in out[:cut if cut >= 0 else len(out)].splitlines():
            if line.strip():
                on_line(line.strip())

    if probe_file.is_file():
        try:
            # utf-8-sig: PowerShell 5.1 writes -Encoding UTF8 as UTF-8 *with* BOM,
            # PowerShell 7 without. Accepting both is the whole point.
            data = json.loads(probe_file.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and data:
                return data
        except Exception as exc:  # noqa: BLE001
            if on_line:
                on_line(f"[WARN] 读取 .qwen_probe.json 失败（{exc}），改用 stdout")

    begin = out.find(BEGIN)
    end = out.find(END)
    if begin < 0 or end < 0:
        raise RuntimeError(
            f"探测失败（退出码 {rc}）。原始输出尾部：\n" + "\n".join(out.splitlines()[-12:])
        )
    payload = out[begin + len(BEGIN):end].strip().lstrip("\ufeff")
    return json.loads(payload)


def hardware(on_line=None) -> dict:
    """GPU / RAM / disk. Everything optional -- missing pieces just report None."""
    hw: dict = {"gpu": None, "vram_gib": None, "ram_gib": None, "free_disk_gib": None,
                "cuda": False, "notes": []}

    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            rc, out = capture([smi, "--query-gpu=name,memory.total,driver_version",
                               "--format=csv,noheader,nounits"])
            if rc == 0 and out.strip():
                first = out.strip().splitlines()[0]
                name, total, driver = [p.strip() for p in first.split(",")[:3]]
                hw["gpu"] = name
                hw["vram_gib"] = round(int(total) / 1024, 1)
                hw["driver"] = driver
                hw["cuda"] = True
        except Exception as exc:  # noqa: BLE001
            hw["notes"].append(f"nvidia-smi 调用失败：{exc}")
    if not hw["cuda"]:
        hw["notes"].append("未检测到 NVIDIA GPU（或驱动不可用）——只能用 CPU，速度会非常慢。")

    try:
        import psutil
        hw["ram_gib"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass

    try:
        hw["free_disk_gib"] = round(shutil.disk_usage(str(PROJECT.anchor)).free / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass

    if on_line:
        on_line(f"GPU : {hw['gpu'] or '未检测到'}  显存 {hw['vram_gib'] or '?'} GiB")
        on_line(f"内存: {hw['ram_gib'] or '?'} GiB    C 盘可用 {hw['free_disk_gib'] or '?'} GiB")
    return hw


# ---------------------------------------------------------------------------
#  what to install, and why
# ---------------------------------------------------------------------------
@dataclass
class Choice:
    key: str                 # 'mixed' | 'turbo' | 'native' | 'gguf'
    title: str
    detail: str
    gib: float               # size of what is still missing
    install_action: str      # qwen_setup.ps1 -Action
    with_gguf: bool = False
    ready: bool = False
    recommend: bool = False
    strategies: list = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.ready:
            return "已就绪"
        return f"约 {self.gib:.2f} GiB"


def build_choices(hw: dict, state: dict) -> list:
    """Turn hardware + detected state into an advised shortlist, not a bare list."""
    models = state.get("models", {})
    vram = hw.get("vram_gib") or 0.0
    ram = hw.get("ram_gib") or 0.0
    disk = hw.get("free_disk_gib") or 0.0

    have_core = bool(models.get("encoder", {}).get("present")
                     and models.get("vae", {}).get("present")
                     and models.get("diffusion", {}).get("present"))
    turbo_ready = have_core and bool(models.get("turbo_diffusion", {}).get("present") or True) \
        and bool(models.get("turbo_lora", {}).get("present"))
    native_ready = bool((state.get("models_cache") or {}).get("native"))
    gguf_ready = bool(models.get("turbo_gguf", {}).get("present"))

    missing_core = 0.0
    for k in ("encoder", "vae", "diffusion"):
        m = models.get(k, {})
        if not m.get("present"):
            missing_core += float(m.get("gib") or 0)
    missing_turbo = 0.0
    for k in ("turbo_diffusion", "turbo_lora"):
        m = models.get(k, {})
        if not m.get("present"):
            missing_turbo += float(m.get("gib") or 0)

    mixed = Choice(
        key="mixed", title="量化模型（W4 / INT8 + INT8 文本编码器）",
        detail="ComfyUI 后端。文本编码器常驻内存，扩散模型与 VAE 在显存。8 GB 显存的首选。",
        gib=missing_core, install_action="models-mixed", ready=(missing_core <= 0),
        strategies=["mixed-vram", "mixed"],
    )
    turbo = Choice(
        key="turbo", title="量化模型 + Turbo 蒸馏（8 步，快约 2.8×）",
        detail="在量化三件套之上加 Turbo int8 扩散 + Turbo LoRA，8 步出图，实测与 25 步同档画质。",
        gib=missing_core + missing_turbo, install_action="models-mixed", with_gguf=True,
        ready=(missing_core <= 0 and missing_turbo <= 0), strategies=["turbo"],
    )
    native = Choice(
        key="native", title="全量原生 BF16（diffusers）",
        detail="不量化、画质上限最高；文本编码器 16.3 GiB 需要足够内存，磁盘约 30 GiB。",
        gib=0.0 if native_ready else 30.21, install_action="models-native",
        ready=native_ready, strategies=["native"],
    )
    gguf = Choice(
        key="gguf", title="Turbo GGUF Q4_K_M（隔离运行时）",
        detail="3.90 GiB，需要单独的 ComfyUI 检出装 ComfyUI-GGUF。实测比 W4 慢约 2.9 倍（逐算子反量化），"
               "只建议作为对照或 W4 出问题时的备用。",
        gib=0.0 if gguf_ready else 3.90, install_action="", ready=gguf_ready, strategies=["gguf"],
    )

    # ---- advice -----------------------------------------------------------
    if vram >= 16:
        native.recommend = True
        mixed.detail += " 你的显存足够大，也可以直接用全量原生。"
    elif vram >= 8:
        turbo.recommend = True
    elif vram > 0:
        mixed.recommend = True
        mixed.detail += f" 显存只有 {vram:.0f} GiB，建议把分辨率控制在 1024×1024 附近。"
    else:
        mixed.recommend = True

    if ram and ram < 24:
        native.detail += f" 当前内存 {ram:.0f} GiB，跑原生 BF16 会很吃紧。"
        if native.recommend:
            native.recommend = False
            turbo.recommend = True

    need = turbo.gib if turbo.recommend else (native.gib if native.recommend else mixed.gib)
    if disk and need and disk < need + 5:
        for c in (mixed, turbo, native):
            c.detail += "  ⚠ 磁盘剩余空间可能不足。"
        gguf.detail += "  ⚠ 磁盘剩余空间可能不足。"

    if vram and vram < 8:
        gguf.detail += " 你的显存偏小，GGUF Q4_K_M（3.90 GiB）在显存上更宽松。"
    return [mixed, turbo, native, gguf]


# ---------------------------------------------------------------------------
#  install / run / stop
# ---------------------------------------------------------------------------
def install(choice: Choice, install_root: str | None, on_line,
            state: dict | None = None, manual: dict | None = None) -> int:
    if not choice.install_action:
        on_line("该组件没有对应的安装脚本（GGUF 需要手动准备隔离运行时，见 gguf\\README.md）。")
        return 1
    args = [ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SETUP),
            "-Action", choice.install_action, "-Yes"]
    # The probe state and the manual entries must reach the script, otherwise it
    # searches from its own folder and cannot find an install kept elsewhere.
    env = build_env(state=state, install_root=install_root, manual=manual)
    args += ["-StartDir", install_root or str(PROJECT)]
    if install_root:
        args += ["-InstallRoot", install_root]
        env["QWEN_INSTALL_ROOT"] = install_root
    if choice.with_gguf:
        args += ["-WithGguf"]
    on_line(f"$ {' '.join(Path(a).name if a.endswith('.ps1') else a for a in args[4:])}")
    return stream(args, on_line=on_line, env=env)


def install_env(install_root: str | None, on_line, state: dict | None = None,
                manual: dict | None = None) -> int:
    args = [ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SETUP),
            "-Action", "env", "-Yes", "-StartDir", install_root or str(PROJECT)]
    if install_root:
        args += ["-InstallRoot", install_root]
    return stream(args, on_line=on_line,
                  env=build_env(state=state, install_root=install_root, manual=manual))


def install_comfyui(install_root: str | None, on_line, state: dict | None = None,
                    manual: dict | None = None) -> int:
    args = [ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SETUP),
            "-Action", "comfyui", "-Yes", "-StartDir", install_root or str(PROJECT)]
    if install_root:
        args += ["-InstallRoot", install_root]
    return stream(args, on_line=on_line,
                  env=build_env(state=state, install_root=install_root, manual=manual))


def strategy_log(strategy: str) -> Path:
    p = PROJECT / "outputs" / f"launcher_{strategy}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def launch(strategy: str, state: dict, on_line=None, manual: dict | None = None):
    """Start the chosen strategy as ``(proc, log_path)``.

    The child's stdout goes to a FILE, never a pipe. A pipe belongs to this
    process, so if the GUI exits while the model is still loaded, every print()
    in the backend starts raising ``OSError: [Errno 22]`` -- the port stays open,
    the model stays in VRAM, and every generation fails instantly. A file handle
    keeps the backend healthy whatever happens to the GUI, and the GUI just tails
    the file for its console.
    """
    log = strategy_log(strategy)
    args = [ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LAUNCH),
            "-Strategy", strategy, "-NoBrowser", "-Yes"]
    fh = open(log, "wb", buffering=0)
    try:
        proc = subprocess.Popen(
            args, cwd=str(PROJECT), env=build_env(state, manual=manual),
            stdout=fh, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW, close_fds=True)
    finally:
        fh.close()          # the child keeps its own inherited handle
    return proc, str(log)


def follow_log(path, on_line, should_stop, poll: float = 0.4):
    """Tail a log file until should_stop() reports the writer is gone."""
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    try:
        while True:
            line = f.readline()
            if line:
                on_line(line.rstrip("\r\n"))
                continue
            if should_stop():
                for rest in f:      # final drain
                    on_line(rest.rstrip("\r\n"))
                return
            time.sleep(poll)
    finally:
        f.close()


def stop(strategy: str, on_line, state: dict | None = None,
         manual: dict | None = None) -> int:
    args = [ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(STOP),
            "-Strategy", strategy]
    return stream(args, on_line=on_line,
                  env=build_env(state=state, manual=manual))


def health_url(port: int = 7860) -> str:
    return f"http://127.0.0.1:{port}/api/health"


def webui_url(port: int = 7860) -> str:
    return f"http://127.0.0.1:{port}/"


# ---------------------------------------------------------------------------
#  uninstall
# ---------------------------------------------------------------------------
def uninstall_targets(state: dict) -> dict:
    """What the three uninstall buttons would remove, with the real paths."""
    venv = (state.get("venv") or {}).get("path")
    comfy = (state.get("comfyui") or {}).get("path")
    cache = (state.get("models_cache") or {}).get("path")
    models = state.get("models") or {}
    files = {}
    for key, m in models.items():
        if m.get("present") and m.get("path"):
            files[key] = m["path"]
    return {
        "env": {"venv": venv, "comfyui": comfy, "models_cache": cache},
        "models": files,
        "launcher": str(launcher_dir()),
        "project": str(PROJECT),
    }


def _rm(path: str, log) -> bool:
    p = Path(path)
    if not p.exists():
        log(f"  跳过（不存在）：{path}")
        return False
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=False)
        else:
            p.unlink()
        log(f"  已删除：{path}")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠ 删除失败 {path}：{type(exc).__name__}: {exc}")
        return False


def uninstall_env(state: dict, log) -> int:
    t = uninstall_targets(state)["env"]
    removed = 0
    for key in ("venv", "comfyui"):
        if t.get(key):
            log(f"删除 {key}：{t[key]}")
            removed += _rm(t[key], log)
    if not removed:
        log("没有找到可删除的环境。")
    return removed


def uninstall_model(state: dict, model_key: str, log) -> int:
    files = uninstall_targets(state)["models"]
    path = files.get(model_key)
    if not path:
        log(f"没有找到已安装的 {model_key}。")
        return 0
    log(f"删除模型文件 {model_key}：{path}")
    return _rm(path, log)


def uninstall_launcher(keep_assets: bool, state: dict, log) -> int:
    """Remove this EXE component; optionally also every downloaded env/model."""
    mine = launcher_dir()
    frozen = getattr(sys, "frozen", False)
    removed = 0
    if not keep_assets:
        removed += uninstall_env(state, log)
        files = uninstall_targets(state)["models"]
        for key, path in files.items():
            log(f"删除模型文件 {key}：{path}")
            removed += _rm(path, log)
        cache = (state.get("models_cache") or {}).get("path")
        if cache:
            log(f"删除模型缓存：{cache}")
            removed += _rm(cache, log)
    log(f"删除启动器组件：{mine}")
    log("（正在运行的 EXE 自身可能无法立刻删除全部文件；如果残留，请手动删除该目录。）")
    removed += _rm(mine, log)
    return removed


# ---------------------------------------------------------------------------
#  remembered state
# ---------------------------------------------------------------------------
STATE_FILE = "launcher_state.json"


def memory_path() -> Path:
    """Sits next to the EXE so the memory travels with the installation."""
    return launcher_dir() / STATE_FILE


def load_memory() -> dict:
    p = memory_path()
    if not p.is_file():
        return {}
    try:
        # utf-8-sig: the file survives being opened and saved in Notepad, which
        # would otherwise prepend a BOM and make this throw.
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def save_memory(data: dict) -> Path:
    p = memory_path()
    payload = dict(data)
    payload["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def clear_memory() -> bool:
    p = memory_path()
    if p.is_file():
        p.unlink()
        return True
    return False


def memory_is_usable(mem: dict) -> tuple:
    """(ok, reason). Only checks that the remembered paths still exist."""
    venv, comfy = mem.get("venv"), mem.get("comfyui")
    if venv and not (Path(venv) / "Scripts" / "python.exe").is_file():
        return False, f"记忆里的 Python 环境已经不存在：{venv}"
    if comfy and not (Path(comfy) / "main.py").is_file():
        return False, f"记忆里的 ComfyUI 已经不存在：{comfy}"
    if not venv and not comfy:
        return False, "记忆文件里没有可用的路径"
    return True, "ok"


def path_env(venv=None, comfy=None, models_cache=None, install_root=None) -> dict:
    env = dict(os.environ)
    for key, val in (("QWEN_VENV_DIR", venv), ("QWEN_COMFYUI_DIR", comfy),
                     ("QWEN_MODELS_CACHE", models_cache)):
        if val:
            env[key] = val
        else:
            env.pop(key, None)
    if install_root:
        env["QWEN_PROJECT_ROOT"] = install_root
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------------------
#  secrets
#
#  API keys are never written in the clear. Two things are stored per key:
#
#    secrets[key]       Windows DPAPI ciphertext (base64). CryptProtectData ties
#                       it to the current Windows user on this machine, so the
#                       file is useless if copied elsewhere. Reversible by
#                       design -- otherwise "remember my key" is impossible.
#    secret_sha256[key] a plain SHA-256 fingerprint, used only to show WHICH key
#                       is saved. SHA-256 is one-way, so a hash alone could never
#                       be used to call the API: it proves identity, it is not
#                       storage.
#
#  If DPAPI is unavailable the key is simply not persisted and the caller is told
#  -- falling back to something weaker would defeat the point.
# ---------------------------------------------------------------------------
_BLOB_CLS = None


def _dpapi(data: bytes, protect: bool) -> bytes | None:
    """Windows CryptProtectData / CryptUnprotectData. None when unavailable."""
    global _BLOB_CLS
    try:
        import ctypes
        from ctypes import wintypes

        if _BLOB_CLS is None:
            class _Blob(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD),
                            ("pbData", ctypes.POINTER(ctypes.c_char))]
            _BLOB_CLS = _Blob

        buf = ctypes.create_string_buffer(data, len(data))
        src = _BLOB_CLS(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        dst = _BLOB_CLS()
        fn = (ctypes.windll.crypt32.CryptProtectData if protect
              else ctypes.windll.crypt32.CryptUnprotectData)
        if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
            return None
        out = ctypes.string_at(dst.pbData, dst.cbData)
        ctypes.windll.kernel32.LocalFree(dst.pbData)
        return out
    except Exception:  # noqa: BLE001
        return None


def secret_fingerprint(value: str) -> str:
    """Short SHA-256 fingerprint -- identifies a key without revealing it."""
    return hashlib.sha256((value or "").encode()).hexdigest()[:16]


def save_secret(name: str, value: str) -> dict:
    """Persist an API key protected by DPAPI, plus its SHA-256 fingerprint."""
    value = (value or "").strip()
    mem = load_memory()
    secrets = dict(mem.get("secrets") or {})
    prints = dict(mem.get("secret_sha256") or {})
    if not value:
        secrets.pop(name, None)
        prints.pop(name, None)
        stored, fp = False, ""
    else:
        fp = secret_fingerprint(value)
        blob = _dpapi(value.encode(), True)
        stored = bool(blob)
        if stored:
            secrets[name] = base64.b64encode(blob).decode("ascii")
        else:
            secrets.pop(name, None)
        prints[name] = fp
    mem["secrets"] = secrets
    mem["secret_sha256"] = prints
    save_memory(mem)
    return {"stored": stored, "sha256": fp}


def load_secret(name: str) -> str:
    """Decrypt a stored API key, or '' when there is none / it will not open."""
    blob = (load_memory().get("secrets") or {}).get(name)
    if not blob:
        return ""
    try:
        raw = _dpapi(base64.b64decode(blob), False)
    except Exception:  # noqa: BLE001
        return ""
    return raw.decode("utf-8", "replace") if raw else ""


def secret_info(name: str) -> dict:
    """{'saved': bool, 'sha256': str, 'has_ciphertext': bool} for the UI."""
    mem = load_memory()
    prints = mem.get("secret_sha256") or {}
    return {"saved": bool(prints.get(name)),
            "sha256": prints.get(name, ""),
            "has_ciphertext": bool((mem.get("secrets") or {}).get(name))}


def clear_secret(name: str) -> None:
    mem = load_memory()
    for field in ("secrets", "secret_sha256"):
        d = dict(mem.get(field) or {})
        d.pop(name, None)
        mem[field] = d
    save_memory(mem)


# ---------------------------------------------------------------------------
#  drawing over HTTP
#
#  The frozen launcher deliberately does not bundle torch/gradio, so it drives a
#  running strategy through its documented API instead of importing it.
# ---------------------------------------------------------------------------
def _api(port: int, path: str, payload=None, timeout=60):
    url = f"http://127.0.0.1:{port}{path}"
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
    return raw if "image" in ctype else json.loads(raw.decode())


def strategy_alive(port: int = 7860) -> bool:
    try:
        _api(port, "/api/health", timeout=4)
        return True
    except Exception:  # noqa: BLE001
        return False


def draw(plan, images: list, port: int = 7860, seed: int = 42,
         steps: int | None = None, on_line=None, on_image=None) -> list:
    """Submit `plan.count` jobs and hand each result to on_image(bytes, info).

    `images` is a list of PNG byte strings, already ordered by
    Plan.ordered_images(). Returns the list of (bytes, info) produced.
    """
    say = on_line or (lambda _s: None)
    refs = [base64.b64encode(b).decode("ascii") for b in images]
    results = []
    for i in range(plan.count):
        payload = {
            "prompt": plan.prompt,
            "negative_prompt": plan.negative_prompt,
            "width": plan.width, "height": plan.height,
            "seed": int(seed) + i,
            "latent_mode": plan.latent_mode,
            "transparent": bool(plan.transparent),
        }
        if steps:
            payload["steps"] = int(steps)
        if refs:
            payload["images"] = refs
        say(f"提交第 {i + 1}/{plan.count} 张 …")
        job = _api(port, "/api/generate", payload, timeout=600)
        jid = job.get("job_id") or job.get("id")
        if not jid:
            raise RuntimeError(f"提交失败：{json.dumps(job)[:200]}")

        deadline = time.time() + 1800
        while True:
            st = _api(port, f"/api/jobs/{jid}", timeout=30)
            status = str(st.get("status", "")).lower()
            if status in ("done", "completed", "succeeded", "success"):
                break
            if status in ("failed", "error"):
                raise RuntimeError(str(st.get("error") or st)[:400])
            if time.time() > deadline:
                raise RuntimeError("等待出图超时（30 分钟）")
            time.sleep(2)

        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/jobs/{jid}/image", timeout=120) as r:
            raw = r.read()
        info = {"index": i + 1, "seed": payload["seed"], "job_id": jid,
                "width": plan.width, "height": plan.height}
        results.append((raw, info))
        say(f"第 {i + 1}/{plan.count} 张完成（{len(raw) // 1024} KB）")
        if on_image:
            on_image(raw, info)
    return results


# ---------------------------------------------------------------------------
#  self test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import pprint

    print(f"project : {PROJECT}")
    print(f"pwsh    : {ps_exe()}")
    st = probe(on_line=lambda s: None)
    print("state   :")
    pprint.pprint(st)
    hw = hardware(on_line=print)
    print("\nchoices :")
    for c in build_choices(hw, st):
        flag = "RECOMMEND" if c.recommend else "         "
        print(f"  [{flag}] {c.key:7} {c.verdict:>14}  {c.title}")
        print(f"             {c.detail}")
    print("\nuninstall targets:")
    pprint.pprint(uninstall_targets(st))
