"""BOM guard for the .ps1 files.

Windows PowerShell 5.1 reads a BOM-less file as ANSI, so every Chinese string in
these scripts turns to mojibake and parsing fails with a bogus
"Missing expression after ','". Editing a .ps1 with a tool that does not preserve
the BOM silently reintroduces that, and pwsh 7 does not complain -- so it ships.

Run this after editing any .ps1:

    python _verify/check_bom.py          # report only
    python _verify/check_bom.py --fix    # repair
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = "--fix" in sys.argv
BOM = b"\xef\xbb\xbf"

files = sorted(list((ROOT / "shared").glob("*.ps1")) + list((ROOT / "setup").glob("*.ps1")))
bad = []
for f in files:
    raw = f.read_bytes()
    non_ascii = any(b > 0x7F for b in raw)
    has_bom = raw.startswith(BOM)
    status = "OK"
    if non_ascii and not has_bom:
        status = "MISSING BOM"
        bad.append(f)
    print(f"  {f.name:<20} bom={has_bom!s:<5} non-ascii={non_ascii!s:<5} {status}")
    if status == "MISSING BOM" and FIX:
        text = raw.decode("utf-8").lstrip("\ufeff")
        f.write_bytes(BOM + text.encode("utf-8"))
        print(f"      -> repaired")

if bad and not FIX:
    print(f"\n  {len(bad)} file(s) need a BOM; rerun with --fix")
    sys.exit(1)
print(f"\n  {len(files)} file(s) checked, " + ("all fine" if not bad else f"{len(bad)} repaired"))
