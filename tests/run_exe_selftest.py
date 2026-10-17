"""Run the frozen EXE's --selftest-image path with a hard timeout.

Used to verify the packaged launcher without a human clicking around. The EXE is
copied to a scratch folder first so the first-run bundle extraction (sync_bundle)
is exercised exactly as a fresh user would experience it.

IMPORTANT (learned the hard way): PyInstaller's onefile bootloader extracts into
%TEMP%\_MEIxxxx and then tightens that folder's ACL. A TEMP directory with a
non-standard ACL -- e.g. the one a sandboxed shell hands to its children -- makes
that extraction fail and the EXE exits with -1 while writing no report at all.
So the child always gets its own plain, writable TEMP/TMP.

    python tests/run_exe_selftest.py [--exe PATH] [--dir PATH] [--chinese] [--gui-alive]

--chinese   put the scratch copy under a path with a space + CJK characters
--gui-alive also check that the GUI event loop boots (offscreen, then terminate)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXE = ROOT / "launcher" / "dist" / "QwenImageLauncher.exe"

# This box's console codepage is GBK; the frozen launcher echoes its report as
# UTF-8 JSON, so printing captured output verbatim would die on U+FFFD.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def make_fixtures(d: Path) -> tuple[Path, Path]:
    from PIL import Image
    png = d / "a.png"
    webp = d / "b.webp"
    Image.new("RGBA", (96, 64), (255, 0, 0, 128)).save(png)
    Image.new("RGB", (64, 96), (0, 255, 0)).save(webp, "WEBP")
    return png, webp


def child_env(tmpdir: Path) -> dict:
    env = dict(os.environ)
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               TEMP=str(tmpdir), TMP=str(tmpdir))
    env.pop("QWEN_PS_EXE", None)
    return env


def main() -> int:
    args = sys.argv[1:]
    exe = Path(args[args.index("--exe") + 1]) if "--exe" in args else DEFAULT_EXE
    if "--dir" in args:
        scratch = Path(args[args.index("--dir") + 1])
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
    elif "--chinese" in args:
        base = Path(tempfile.mkdtemp(prefix="qwen exe selftest "))
        scratch = base / "测试 路径" / "子 目录"
        scratch.mkdir(parents=True, exist_ok=True)
    else:
        scratch = Path(tempfile.mkdtemp(prefix="qwen_exe_selftest_"))
    childtmp = scratch / "childtmp"
    childtmp.mkdir(exist_ok=True)
    exe_copy = scratch / exe.name
    shutil.copy2(exe, exe_copy)
    png, webp = make_fixtures(scratch)
    report = scratch / "report.json"
    print(f"exe     : {exe}  ({exe.stat().st_size} B)")
    print(f"scratch : {scratch}")

    env = child_env(childtmp)
    cmd = [str(exe_copy), "--selftest-image", str(png), str(webp),
           "--report", str(report)]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(scratch), env=env, timeout=180,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as exc:
        rc = "TIMEOUT"
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    elapsed = round(time.time() - t0, 1)
    print(f"exit    : {rc}  ({elapsed}s)")
    if out.strip():
        print("stdout  :", out.strip()[:1000])
    if err.strip():
        print("stderr  :", err.strip()[:1000])

    data = {}
    if report.is_file():
        data = json.loads(report.read_text(encoding="utf-8"))
        print("report  :", json.dumps(data, ensure_ascii=False)[:1600])
    else:
        print("report  : MISSING")

    unpacked = sorted(
        str(f.relative_to(scratch)).replace("\\", "/")
        for f in scratch.rglob("*")
        if f.is_file() and f.name not in (exe.name, "a.png", "b.webp", "report.json")
    )
    print(f"unpacked: {len(unpacked)} files")
    for rel in unpacked:
        print("   ", rel)

    ok = bool(data.get("all_ok")) and rc == 0
    if "--gui-alive" in args:
        p = subprocess.Popen([str(exe_copy)], cwd=str(scratch), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < 10 and p.poll() is None:
            time.sleep(0.2)
        alive = p.poll() is None
        print(f"gui     : {'ALIVE (event loop running)' if alive else f'EXITED rc={p.returncode}'}")
        if alive:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        ok = ok and alive
    print("RESULT  :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
