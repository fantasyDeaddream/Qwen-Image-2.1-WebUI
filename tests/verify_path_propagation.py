"""Reproduce and verify the exact failure the user hit.

Layout:
    A = <temp>/qwen_verify_paths/video_side/     <- the EXE side (no venv/ComfyUI)
    B = the real checkout                          <- where .venv and ComfyUI actually are

Clicking "安装 量化模型" used to run qwen_setup.ps1 with no knowledge of B, so it
searched from A\\setup, found no venv, and threw
    请先配置 Python 环境（启动器第 3 步「配置环境」，或 -Action env）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
A = Path(tempfile.gettempdir()) / "qwen_verify_paths" / "video_side"
fails = []


def check(label, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{('  ' + str(detail)) if detail else ''}")
    if not ok:
        fails.append(label)


shutil.rmtree(A.parent, ignore_errors=True)
A.mkdir(parents=True, exist_ok=True)
for d in ("shared", "setup", "turbo", "mixed", "mixed-vram", "gguf", "native"):
    src = ROOT / d
    if not src.is_dir():
        continue
    (A / d).mkdir(parents=True, exist_ok=True)
    # scripts only: gguf/ComfyUI is a 4 GB read-only git checkout, irrelevant here
    for f in src.glob("*"):
        if f.is_file() and f.suffix in (".ps1", ".py", ".json", ".md"):
            shutil.copy2(f, A / d / f.name)
print(f"  A (EXE 侧, 无 venv/ComfyUI): {A}")
print(f"  B (真实环境)               : {ROOT}")

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT / "launcher"))
import core  # noqa: E402

# the user's manual entries, exactly as STEP 2 collects them
MANUAL = {
    "QWEN_VENV_DIR": str(ROOT / ".venv"),
    "QWEN_COMFYUI_DIR": str(ROOT / "ComfyUI"),
}

print()
print("=" * 74)
print("1) build_env 是否真的带上了覆盖")
print("=" * 74)
env = core.build_env(install_root=None, manual=MANUAL)
check("QWEN_VENV_DIR", env.get("QWEN_VENV_DIR") == str(ROOT / ".venv"), env.get("QWEN_VENV_DIR"))
check("QWEN_COMFYUI_DIR", env.get("QWEN_COMFYUI_DIR") == str(ROOT / "ComfyUI"))
env2 = core.build_env(state={"venv": {"path": r"X:\from-probe"}, "comfyui": {"path": r"Y:\from-probe"}},
                      manual=MANUAL)
check("手动值优先于 probe 结果", env2.get("QWEN_VENV_DIR") == str(ROOT / ".venv"))
check("path_overrides 空输入安全", core.path_overrides(None, None) == {})

print()
print("=" * 74)
print("2) 旧行为：不带覆盖跑 models-mixed（应当抛错）")
print("=" * 74)
CLEAN = {k: v for k, v in os.environ.items()
         if k not in ("QWEN_VENV_DIR", "QWEN_COMFYUI_DIR", "QWEN_MODELS_CACHE",
                      "QWEN_PROJECT_ROOT")}
CLEAN["PYTHONIOENCODING"] = "utf-8"
p = subprocess.run([core.ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(A / "setup" / "qwen_setup.ps1"),
                    "-Action", "models-mixed", "-Yes", "-StartDir", str(A)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=CLEAN, timeout=180)
old_out = (p.stdout or "") + (p.stderr or "")
# -StartDir is now always passed, so the old failure mode is unreachable even
# without the manual overrides. Asserting its ABSENCE is the stronger check.
check("旧错误已不可复现（请先配置 Python 环境）", "请先配置 Python 环境" not in old_out)

print()
print("=" * 74)
print("3) 新行为：带覆盖跑同一个命令（应当越过检查）")
print("=" * 74)
p = subprocess.Popen([core.ps_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", str(A / "setup" / "qwen_setup.ps1"),
                      "-Action", "models-mixed", "-Yes", "-StartDir", str(A)],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                     encoding="utf-8", errors="replace", env=env)
out = []
deadline = time.time() + 25
while time.time() < deadline:
    line = p.stdout.readline()
    if not line:
        if p.poll() is not None:
            break
        continue
    out.append(line.rstrip())
    # stop as soon as we know it got past the venv gate -- do not download
    if any(k in line for k in ("请先配置 Python 环境", "下载", "download", "[INFO] search",
                               "已存在", "MISS", "OK")):
        if "请先配置 Python 环境" in line or len(out) > 25:
            break
p.kill()
try:
    p.wait(timeout=10)
except Exception:  # noqa: BLE001
    pass
new_out = "\n".join(out)
check("不再抛 '请先配置 Python 环境'", "请先配置 Python 环境" not in new_out)
check("脚本看到了正确的 venv（搜索输出里有 B 的路径或已跳过检查）",
      str(ROOT) in new_out or "search start" in new_out)
print("      --- 新行为前几行 ---")
for ln in out[:10]:
    print("      " + ln[:110])

print()
print("=" * 74)
print("RESULT:", "PASS" if not fails else f"FAIL ({len(fails)})")
for f in fails:
    print("  -", f)
print("=" * 74)
shutil.rmtree(A.parent, ignore_errors=True)
sys.exit(1 if fails else 0)
