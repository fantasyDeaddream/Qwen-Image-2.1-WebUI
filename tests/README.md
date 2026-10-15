# Verification scripts

Ad-hoc checks for the things that are easy to get wrong in this project. They are not a
unit-test suite — they exercise the real PowerShell scripts and the real frozen
executable, which is where the interesting failures live.

Run them from the repository root with the project virtualenv:

```powershell
cd <repo>
.\.venv\Scripts\python.exe tests\check_bom.py
.\.venv\Scripts\python.exe tests\verify.py
.\.venv\Scripts\python.exe tests\verify_path_propagation.py
```

## Why these exist

Almost every bug found in this project was a **host-environment difference**, not a logic
error. Windows PowerShell 5.1 and PowerShell 7 disagree in ways that are invisible on a
developer machine that has both:

| Behaviour | PowerShell 5.1 | PowerShell 7 |
|---|---|---|
| Reads a BOM-less `.ps1` as | ANSI → CJK text becomes mojibake, parsing fails | UTF-8 |
| `$PSScriptRoot` inside a `param()` default | **empty** | populated |
| `Set-Content -Encoding UTF8` writes | UTF-8 **with** BOM | UTF-8 without BOM |
| Native command writes to stderr while `$ErrorActionPreference = 'Stop'` | **terminating error** | ignored |

A clean Windows install only has 5.1. Every row above has caused a real failure here.

## The scripts

| Script | What it checks |
|---|---|
| `check_bom.py` | Every `.ps1` that contains non-ASCII must carry a UTF-8 BOM. `--fix` repairs them. Run this after editing any PowerShell file — an editor that drops the BOM silently breaks 5.1. |
| `verify.py` | Parses all six `.ps1` files under both PowerShell versions; runs `qwen_setup.ps1 -Action probe` and `-Action status` from a path containing a space with **no** `-StartDir`; calls `core.probe()`; launches the GUI offscreen under both versions. |
| `verify_path_propagation.py` | Builds an A/B layout — the executable on one side, the virtualenv and ComfyUI on the other — and asserts that manually chosen paths reach the install steps instead of only the probe. |
| `native_check_test.ps1` | Builds a deliberately partial native-weight tree (one shard present, index promises three, two components missing) and asserts `Test-QwenNativeWeights` rejects it while accepting a complete one. |
| `parse_and_table.ps1` | Parses every `.ps1` under the running PowerShell version and prints the artifact size table, so you can confirm each rule still matches its filename pattern. |
| `make_partial_fixture.py` | Creates the partial tree used by `native_check_test.ps1`. |

## A note on sandbox locations

`verify.py` builds its sandbox **outside** the repository. Putting it inside is a trap: the
model-discovery code walks up parent directories, so a sandbox nested in the checkout finds
the real `.venv` and ComfyUI through that walk and the test stops testing what it claims to.
