# Launcher

The PyQt5 front-end. It **reimplements nothing** — detection, installation, start, stop and
uninstall are all delegated to the PowerShell scripts in `shared/` and `setup/`. The launcher
adds an interface, guidance and hardware advice on top of them.

```
launcher/
├─ app.py              PyQt5 main window (the five-step wizard)
├─ core.py             back end: probe / install / start / stop / uninstall
├─ agent.py            AI agent SDK: providers, capability probing, plan validation
├─ agent_panel.py      agent UI and the result gallery
├─ theme.py            colour scheme and QSS
├─ icon.ico            application icon, 16–256 px, rounded with transparency
├─ qwen_launcher.spec  PyInstaller configuration (single file, no console, embedded icon)
├─ USER_GUIDE.md       the end-user guide, bundled into the executable
└─ build_exe.bat       one-click build
```

## Running from source

```powershell
cd launcher
..\.venv\Scripts\python.exe app.py
```

The launcher expects to live inside the project tree, or in a subfolder of it — it walks up
until it finds `shared/QwenEnv.ps1`.

## Two build modes

The same source produces both, and they do not interfere with each other.

### Development

Run `app.py` directly, as above. The PowerShell scripts and the per-strategy `app.py` files
stay as loose files you can edit and re-run without rebuilding anything.
`core.bundling()` returns `False` and nothing is ever copied to disk.

### Distribution (single executable)

```powershell
cd launcher
..\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm qwen_launcher.spec
# output: launcher\dist\QwenImageLauncher.exe
```

`qwen_launcher.spec` packs **every script listed in `core.BUNDLED_FILES`** into the
executable — the nine files under `shared/`, `setup/qwen_setup.ps1`, the five strategy
`app.py` files and `USER_GUIDE.md` — so the whole product ships as one `.exe`.

**Why the first run unpacks folders.** PowerShell and the project virtualenv need real file
paths; an executable cannot hand them an in-memory module. So on first run
`core.sync_bundle()` writes the bundled scripts next to the binary.

**Why upgrading never loses data.** `sync_bundle()` compares content file by file and only
ever adds or overwrites — it never deletes. Replacing the executable therefore refreshes the
launcher's own scripts while leaving `.venv/`, `ComfyUI/`, `models_cache/`, `outputs/` and
the isolated `gguf/ComfyUI/` untouched.

Keep the two lists in step: `core.BUNDLED_FILES` and `BUNDLED` in the spec. The spec fails
the build when a source file is missing, so a partial executable cannot be produced silently.

## Verifying a build

The packaged executable has no console, so `--selftest-image` writes its verdict to JSON:

```powershell
QwenImageLauncher.exe --selftest-image "a.png" "b.webp" --report out.json
```

It drives the **actual 添加图片 button path** (with the file dialog stubbed) and decodes each
file, reporting `{frozen, qt, results[], button_click_ok, loaded_images, all_ok}`. The button
path matters: a crash once came from `QPushButton.clicked` passing its `checked` flag into a
slot parameter, which only reproduces when you click rather than call the decoder directly.

## The five steps

| Step | What it does |
|---|---|
| 1 Detect | GPU, VRAM, RAM and free disk; then the same environment checks the CLI performs |
| 2 Locate / install path | Point at existing directories, or choose an install root. Can be remembered |
| 3 Environment and models | Create the virtualenv, install ComfyUI, then pick a model route — each card carries a recommendation for the detected hardware |
| 4 Run | Start a strategy, wait for health, open the browser; the AI agent sits below |
| 5 Uninstall | Environment, a specific model, or the launcher component |

### Remembered paths

Step 2 can persist the resolved paths to `launcher_state.json` beside the executable. On the
next launch the program loads them, still runs a quick probe to confirm the models are really
there, and then jumps straight to step 4. Delete that file, or use **忘记记忆并重新检测** in
step 4, to return to the normal flow.

### Hardware advice

`core.build_choices()` marks the route that suits the detected VRAM, RAM and disk with a ★ and
shows the reasoning on the card, rather than presenting an undifferentiated list.

### AI agent

Step 4 hosts an agent panel that turns a plain-language request plus reference images into a
structured drawing job. It talks to DeepSeek or Zhipu GLM over their OpenAI-compatible APIs,
fetches the model list live, and **probes** which models can actually read images instead of
inferring it from names. See the main README for details.

### Secrets

API keys are stored with Windows DPAPI plus a SHA-256 fingerprint. The key is never written
in the clear; if DPAPI is unavailable it is not persisted at all.

## Why the GUI drives HTTP instead of importing

The frozen executable deliberately does not bundle torch or gradio (2 GB → 43 MB). It
therefore drives a running strategy through the documented API on port 7860, which is the
same surface any other client would use.
