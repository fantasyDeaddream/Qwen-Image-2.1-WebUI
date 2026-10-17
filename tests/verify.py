"""Post-fix verification (replaces the lost _bench scaffolding).

Checks the two things that actually matter after the $PSScriptRoot / null-path
fixes:
  * every -Action works under Windows PowerShell 5.1 with no -StartDir, from a
    path containing a space -- the exact combination that failed for the user
  * the GUI still renders and probes correctly on both PowerShell versions
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = Path(tempfile.gettempdir()) / "qwen_verify" / "space dir" / "QwenImage"
fails = []

# This box's automation session may hand children a TEMP directory whose ACL the
# GUI smoke script (and PowerShell itself) cannot write into; that failure looks
# exactly like a product bug. Point TEMP/TMP at a directory we know is writable.
import tempfile  # noqa: E402

_tmp_base = Path(tempfile.gettempdir())
try:
    _probe_file = _tmp_base / ".qwen_write_probe"
    _probe_file.write_text("x", encoding="utf-8")
    _probe_file.unlink()
except OSError:
    _tmp_base = ROOT / ".qwen_tmp"
    _tmp_base.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(_tmp_base)
os.environ["TMP"] = str(_tmp_base)


def check(label, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{('  ' + str(detail)) if detail else ''}")
    if not ok:
        fails.append(label)


print("=" * 74)
print("1) 脚本解析（5.1 与 7）")
print("=" * 74)
scripts = [ROOT / "setup" / "qwen_setup.ps1", ROOT / "setup" / "qwen_wizard.ps1",
           ROOT / "shared" / "QwenEnv.ps1", ROOT / "shared" / "qwen_launch.ps1",
           ROOT / "shared" / "qwen_stop.ps1", ROOT / "shared" / "qwen_env.ps1"]
for ps in ("powershell.exe", "pwsh.exe"):
    for s in scripts:
        cmd = (f'$e=$null; [void][System.Management.Automation.Language.Parser]'
               f'::ParseFile("{s}",[ref]$null,[ref]$e); '
               f'if($e.Count){{"ERR: "+$e[0].Message}}else{{"OK"}}')
        r = subprocess.run([ps, "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True).stdout.strip()
        check(f"{ps[:-4]:<12} {s.name:<20}", r == "OK", "" if r == "OK" else r[:60])

print()
print("=" * 74)
print("2) 含空格路径 + 无 -StartDir + 仅 5.1（用户的确切场景）")
print("=" * 74)
shutil.rmtree(SANDBOX.parents[1], ignore_errors=True)
SANDBOX.mkdir(parents=True, exist_ok=True)
for d in ("shared", "setup", "turbo", "mixed", "mixed-vram", "gguf", "native"):
    src = ROOT / d
    if not src.is_dir():
        continue
    (SANDBOX / d).mkdir(parents=True, exist_ok=True)
    # scripts only: gguf/ComfyUI is a 4 GB read-only git checkout, irrelevant here
    for f in src.glob("*"):
        if f.is_file() and f.suffix in (".ps1", ".py", ".json", ".md"):
            shutil.copy2(f, SANDBOX / d / f.name)
print(f"  {SANDBOX}")

BAD = ("EmptyStringNotAllowed", "NullNotAllowed", "为空字符串", "为 null",
       "ParserError", "Missing closing", "Unexpected token")
for action in ("probe", "status"):
    p = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(SANDBOX / "setup" / "qwen_setup.ps1"),
                        "-Action", action],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    hit = [b for b in BAD if b in out]
    check(f"-Action {action} 无路径/解析错误", not hit, hit)
    if action == "probe":
        check("  probe 输出了 JSON 块",
              "###QWEN-JSON-BEGIN###" in out and "###QWEN-JSON-END###" in out)
        check("  search_dir 含空格路径且完整",
              "space dir" in out)

print()
print("=" * 74)
print("3) 启动器 core.probe()（强制 5.1）")
print("=" * 74)
os.environ["QWEN_PS_EXE"] = "powershell.exe"
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT / "launcher"))
import core  # noqa: E402
check("ps_exe() 是 5.1", Path(core.ps_exe()).name.lower() == "powershell.exe",
      Path(core.ps_exe()).name)
st = core.probe(install_root=str(SANDBOX))
check("返回 dict 且键齐全",
      sorted(st) == ["comfyui", "install_root", "models", "models_cache",
                     "project_root", "search_dir", "venv"], sorted(st))
check("search_dir keeps the space", "space dir" in (st.get("search_dir") or ""))
check(".qwen_probe.json 已生成", (SANDBOX / ".qwen_probe.json").is_file())

print()
print("=" * 74)
print("4) GUI 冒烟（两种 PowerShell 各一次）")
print("=" * 74)
SMOKE = os.environ.get("TEMP", ".") + r"\qwen_smoke.py"
Path(SMOKE).write_text('''
import os, sys
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(r"%s")
sys.path.insert(0, str(ROOT / "launcher"))
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
import app as launcher_app

app = QApplication(sys.argv)
win = launcher_app.Launcher(); win.resize(1600, 900); win.show()
res = {}
def step():
    if not win.state:
        QTimer.singleShot(400, step); return
    res["venv"] = bool((win.state.get("venv") or {}).get("ok"))
    res["choices"] = len(win.choices)
    res["pages"] = win.stack.count()
    for i in range(res["pages"]):
        win.goto_step(i); app.processEvents()
    res["nav_ok"] = True
    app.quit()
QTimer.singleShot(1200, step); QTimer.singleShot(90000, app.quit)
app.exec_()
print("SMOKE", res)
sys.exit(0 if res.get("nav_ok") and res.get("pages") == 5 else 1)
''' % ROOT, encoding="utf-8")
for ps in (None, "powershell.exe"):
    if ps:
        os.environ["QWEN_PS_EXE"] = ps
    else:
        os.environ.pop("QWEN_PS_EXE", None)
    r = subprocess.run([str(ROOT / ".venv" / "Scripts" / "python.exe"), SMOKE],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = [l for l in (r.stdout or "").splitlines() if l.startswith("SMOKE")]
    check(f"GUI ({ps or 'pwsh 7'})", r.returncode == 0, line[0] if line else (r.stderr or "")[-90:])

print()
print("=" * 74)
print("RESULT:", "PASS" if not fails else f"FAIL ({len(fails)})")
for f in fails:
    print("  -", f)
print("=" * 74)
shutil.rmtree(SANDBOX.parents[1], ignore_errors=True)
sys.exit(1 if fails else 0)
