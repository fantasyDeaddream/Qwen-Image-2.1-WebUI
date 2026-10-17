# 协作账本 / Ledger

规则见 [PROTOCOL.md](PROTOCOL.md)。本文件**追加式**：只在对应的节里追加条目，
不删除、不重写别人的内容。每条都带会话标签和时间戳。

---

## 状态

<!-- 每个会话在开启时建自己的小节，结束时更新。一句话说清现状与阻塞。 -->

### [S1] 2026-10-17 11:55 — 调研完成（v1）
产出 `docs/research.md`（变体清单 + 硬件分档 4/6/8/12/16/24 GB + 五套策略复核）。
结论见本文件「结论」区 5 条、请求 3 条（S2/S3/S5 各 1 条）。无阻塞。
下一步（可选）：等 S3 实测 W4A8 编码器 / INT4 ConvRot / Lightning 4 步后，我按结果更新分档表。

### [S2] 2026-10-17 12:20 — 核验完成；EXE 已用最新源码重建，冻结自检 PASS

- **已读**：任务书、`PROTOCOL.md`、本账本（含 S1 全部结论、S4 请求）。
- **核验（全部有实跑输出）**：
  ① `_verify\check_bom.py`：6 个 `.ps1` 全 BOM OK（我改过 2 个后自动修复并复核）；`_verify\verify.py`：
     **PASS**（12 项 5.1+7 解析、含空格路径无 `-StartDir` 的 probe/status、`core.probe()` 强制 5.1、
     GUI 5 页离屏冒烟 ×2 版本）。
  ② `-Action probe/status/manifest` 在真实仓库与纯沙箱目录均正常；manifest 与我实测的 ModelScope API
     逐字节一致（native 29 文件 33,134,789,600 B；mixed 25,453,193,453 B；GGUF 4,189,346,592 B）。
  ③ 冻结 EXE：首启释放 16 个脚本、`--selftest-image` 在全新目录与「中文+空格」路径均
     `all_ok=true` / rc=0，GUI 事件循环可起；5 页截图已留证（`_verify\_gui_shots\`）。
- **EXE（响应 S4 请求，最新源码含 `agent.py` 11:53 改动）**：
  `launcher\dist\QwenImageLauncher.exe` = 45,547,059 B / 构建 37.0 s /
  SHA256 `2EEB86FD5CBBCDF781EC1C31C559D003C436757ABA00367B070F92F79FDCB264`。
  构建前机器校验 `BUNDLED_FILES`(16) == spec `BUNDLED`(16)，16 个源文件齐全。
- **本轮改动（都在我的地盘）**：`shared\QwenEnv.ps1`（登记 W4A8 编码器 artifact 门槛）、
  `setup\qwen_setup.ps1`（下载侧支持 `QWEN_TEXT_ENCODER`；W4 缺失时显式告警回退 INT8）、
  `setup\README.md`（模型来源/体积表 + W4 单源风险 + W4A8 用法）、
  `_verify\{run_exe_selftest,diag_exe,gui_shots}.py` 新增 + `verify.py` 的 TEMP 兜底（可复用验证工具）。
- **阻塞**：无。**下一步**：若 S3/S4 之后再有改动，按请求重建一次 EXE 并复测；S5 需要发布时直接取
  `launcher\dist\QwenImageLauncher.exe` + 上面的 SHA256。

### [S3] 2026-10-17 11:47 — 上下文已装载，就绪待命（未开工）

- **已读**：`docs/session_bootstrap/S3_workflow.md`、`docs/team/PROTOCOL.md`、本账本（含 S1 全部「结论」+ 3 条请求）。
- **现状**：我独占的五套策略源码已在树上且成熟（`shared/qwen_*`、`mirrors.json`、`mixed/`、`mixed-vram/`、`turbo/`、`gguf/`、`native/`，版本 10-12～10-15）。本轮**尚未修改任何文件、未起后端、未产出实测数据**。
- **阻塞**：无技术阻塞；按用户指示本轮暂不开工（不做自检/验证），等调起后再动。
- **已接收待办（开工第一优先，来自 S1 请求）**：① `TEXT_ENCODER_FILE` 加 env/profile 覆盖（如 `QWEN_TEXT_ENCODER`）；② mixed-vram/turbo 实测 W4A8 编码器（5.879 GiB）的 CPU 加载、编码耗时与画质。
- **下一步**：收到调起指令 → 先核对采样参数/构图与任务书一致 → 真后端实测（同配置两次取第二次、`nvidia-smi` 采峰值）→ 回填「结论」并更新本节。

### [S4] 2026-10-17 11:56 — Step 4 Agent 已核验（单测 104 项 + 冻结 EXE 自检通过）

- **已读**：`docs/session_bootstrap/S4_agent.md`、`PROTOCOL.md`、本账本（含 S1 结论与 3 条请求）。
- **现状**：`launcher/agent.py` + `launcher/agent_panel.py` 的既有实现（Provider SDK、联网模型列表、
  读图实测三分类、Plan 校验夹紧、参考图双尺寸解码、出图画廊）**已按任务书核对通过**，未发现结构性缺陷。
  本轮只做了「补强 + 留证」：`Plan.from_agent` 的每一处纠正现在都会产生 `⚠` 告警、`ordered_images`
  对越界索引防御、`list_models` 兼容裸数组里的 `model` 键；**对外签名一个都没改**（详见「契约」）。
- **留证**：新增 `launcher/tests/`（不进包）— `test_agent_logic.py` 74 项、`test_agent_panel.py` 30 项，
  全部通过、不联网、不需要 Key（含 127.0.0.1 回环假提供方，覆盖 urllib 与 HTTP 错误映射）。
  冻结 EXE `--selftest-image` **all_ok=true / exit 0**（6 种图片格式 + 按钮崩溃路径，Pillow 12.3.0 在包内）。
- **未做（按用户指示本轮不做全量验证）**：真 Key 链路（本会话无 Key）与真出图→画廊链路
  （7860/8189 后端都没在跑）。两条链路的操作步骤与期望结果已写在「结论」[S4] 条，随时可补跑。
- **给 S2 的请求**：用最新源码重建 EXE（本轮改了 `agent.py`，11:26 那版 dist EXE 不含这些改动）——
  见「请求」区。**无其它阻塞**。

### [S5] 2026-10-17 11:51 — 发布会话已就绪，本轮不发新版本（用户指示）

- **已读**：`docs/session_bootstrap/S5_publish.md`、`PROTOCOL.md`、本账本（含 S1 全部「结论」与 3 条请求）。
- **发布基线已核验**（与任务书一致）：仓库 public、默认分支 `main`；`v1.0.0` = commit `9cc0348`，release 资产 `QwenImageLauncher.exe` 45,534,395 B + `.sha256` 在线完整；`_publish\Qwen-Image-2.1-WebUI` 工作副本干净、与 `origin/main` 同步（62 个跟踪文件）。
- **本轮按用户指示不发布新版本**：不打 tag、不建 release、不推送；等 S2 重建 EXE、S3/S4 提交改动并更新状态后，再走「同步 → 提交 → v1.1.0 → release」。
- **S1 请求（「请求」区 L52–56）核实结果**：对外 README（v1.0.0 / `9cc0348`）重写后**不含**「LPIPS 0.047」表述，公开侧无需更正；该数字只存在于**源树开发版** `README.md` §9 表格第 412 行（内部文档，不在我独占范围，未擅自改）。GGUF 公开 README 已写实测 2.9× 慢，但未提 LPIPS 明显更差——列入下次文档更新待办。
- **发现（待决，下次同步前需定）**：公开仓库当前**无 `docs/` 目录**（v1.0.0 快照 62 个文件不含）；任务书「必须保留」清单要求收录 `docs/`，但 `docs/session_bootstrap/` 含本机绝对路径，直接公开违反脱敏规则。建议：清洗（去本地路径）后收录，或继续排除——请用户/后续会话确认。
- **阻塞**：无；S2、S4 未启动，S3 就绪未开工，源树暂无待发布改动，暂无可同步内容。
- **下一步**：① 跟进 S2 用最新代码重建 EXE → 复制到 `_publish\release\` 并生成 SHA256；② S3/S4 完成后同步源码（先跑脱敏扫描 + 人工核 `git status`）并更新文档；③ 打 `v1.1.0`、发 release；④ 收尾时再更新本节。

---

## 请求

<!-- 格式：  ### [S3] 2026-10-18 14:32 → S2  /  然后写要什么、为什么 -->

### [S1] 2026-10-17 11:55 → S3
**把文本编码器文件名/类型做成可配置项，并实测 W4A8 编码器。**
`shared/qwen_comfy.py` 的 `TEXT_ENCODER_FILE` 目前写死 `qwen3vl_8b_int8_convrot.safetensors`（8.709 GiB）。
Comfy-Org 同仓库有 `qwen3vl_8b_w4a8.safetensors`，**5.879 GiB**（ModelScope 可下，路径 `text_encoders/qwen3vl_8b_w4a8.safetensors`）。
请求：① 加环境变量/profile 覆盖（如 `QWEN_TEXT_ENCODER`）；② 在 mixed-vram/turbo 上实测 CPU 加载是否正常、编码耗时与画质。
理由：省 2.83 GiB 磁盘与内存，是 16 GB 内存笔记本、以及 12–16 GB 显存放编码器进显存的关键。细节见 `docs/research.md` §1.2 / §4。

### [S1] 2026-10-17 11:55 → S2
**模型来源与镜像：有两个变体只有 HuggingFace 有，且 HF 本次实测可达。**
实测：`NidAll/Qwen-Image-2.1-Mixed-Balanced`（当前在用的 W4 mixed_balanced 3.915 GiB 的唯一来源）、`NidAll/Turbo-INT8-ConvRot-…`、`toxicdog/*INT4ConvRot*` 在 ModelScope **404**；HF 本次 HTTP 200 / range 206（0.37–0.58 MB/s）。
请求：① 在 manifest/artifact 表里登记这些文件的来源，并把可选 `qwen3vl_8b_w4a8.safetensors`（5.879 GiB）登记为编码器替代项；② 决策是否给 NidAll W4 文件做本地归档/备用源（HF 若再次不通，重新安装会缺文件）。

### [S1] 2026-10-17 11:55 → S5
**README §9 的 Turbo INT8 LPIPS 0.047 无法溯源，建议更正并注明出处。**
公开可查的是 unsloth 针对**自家** Turbo INT8-ConvRot（7.26 GB）测的 0.037、Base INT8 0.064、FP8 0.112；Comfy-Org 的 6.758 GiB `turbo_int8_convrot` 没有公开 LPIPS，不能互换引用。
另：`gguf` 策略"慢 2.4×"可补一句"Q4_K_M 的 LPIPS 0.153 也明显差于 INT8 0.037"，避免读者误以为 GGUF 只是慢。
数据出处见 `docs/research.md` §1.1 / §1.5 / §5。

### [S4] 2026-10-17 11:56 → S2
**请用最新源码重建 EXE（并跑一次冻结版图片自检）。**

- 本轮我修改了 `launcher/agent.py`（Plan 校验补齐 `⚠` 告警、`ordered_images` 越界防御、
  `list_models` 兼容裸数组 `model` 键）。对外函数签名**未改**，`agent_panel.py` **未改**，
  所以 `app.py`/`core.py` 无需任何配合。`launcher\dist\QwenImageLauncher.exe`（11:26 构建）
  不含这些改动，重建即可。
- **打包清单不用动**：新增的 `launcher/tests/` 是开发期单测（74 + 30 项），
  **请不要**加进 `core.BUNDLED_FILES` / `qwen_launcher.spec` 的 `BUNDLED`。
- 建议重建后跑：`QwenImageLauncher.exe --selftest-image a.png b.jpg`，期望
  `selftest_image.json` 里 `"all_ok": true`、退出码 0。本机跑法有坑（`%TEMP%` 不可写、
  `Start-Process -Wait` 会假挂起、`--report` 别带引号），已写进「结论」[S4] 条，可直接照抄。

### [S2] 2026-10-17 12:20 → S4
**EXE 已重建，可直接验收。** `launcher\dist\QwenImageLauncher.exe` = 45,547,059 B（2026-10-17 12:05 构建，
含你 11:53 的 `agent.py`），SHA256 `2EEB86FD5CBBCDF781EC1C31C559D003C436757ABA00367B070F92F79FDCB264`。
冻结版 `--selftest-image` 在全新目录与「中文+空格」路径各跑一次：`all_ok=true` / rc=0 / 16 个 bundle
文件释放正常；`--gui-alive` 亦通过（offscreen 事件循环存活）。`launcher/tests/` 未进包（已确认清单 16→16 未变）。
补充一点你那条跑法：除了 `%TEMP%` 要可写，**直接改子进程 TEMP/TMP 最稳**；`--report` 用绝对路径没问题
（Python `subprocess.run` 方式下带空格/中文也 OK），见「结论」[S2] 条。

### [S2] 2026-10-17 12:20 → S1
**W4A8 编码器已做成端到端可选项；W4 单源风险已登记，本轮不做 3.9 GiB 本地归档。**
1. 编码器替代项：`shared\QwenEnv.ps1` 增加 `qwen3vl.*w4a8` 的 artifact 门槛（6,312,105,364×0.9）；
   `setup\qwen_setup.ps1` 的 models-mixed / probe / manifest 现在读 **`QWEN_TEXT_ENCODER`**
   （运行时 `shared/qwen_comfy_app.py` 本来就优先它——两边现在一致）。设
   `QWEN_TEXT_ENCODER=qwen3vl_8b_w4a8.safetensors` 即下 5.879 GiB 那个；未设置时行为与之前完全一致
   （实测对照：default manifest 仍是 5 文件 / 25,453,193,453 B）。S3 若要改成 profile 配置请先登记契约。
2. 来源登记：Comfy-Org 的 ModelScope 仓（18 个文件，121,480,804,931 B）与 HF 仓**都没有**
   `diffusion_models/qwen_image_2.1_mixed_balanced.safetensors`；`NidAll/Qwen-Image-2.1-Mixed-Balanced`
   在 ModelScope **404**、HF 有（4,203,397,480 B）。→ 决定：**不做本地归档**（本机 C 盘只剩 49.0 GiB，
   且该文件已在 `ComfyUI\models\diffusion_models\`，只有用户手动删掉才会缺），改为文档化 + 缺失时
   **显式告警**并回退 Comfy-Org INT8 ConvRot（6.758 GiB，比 W4 大 2.8 GiB）。详见 `setup\README.md`
   「模型来源与体积」。若要更强保障，可行的下一步是 HF 断点续传下载器（0.37–0.58 MB/s，约 2–3 小时），
   本轮按「不发布新版本」的口径没有开工。

---

## 契约

<!-- 任何被其他组件依赖的接口变更都登记在这里：
     PowerShell 函数名、Python 函数签名、环境变量、打包文件清单、JSON 字段名。 -->

### 基线（2026-10-17，当前代码状态）

以下是现存的、跨组件依赖的接口。**改动它们必须在此节追加新条目。**

**`shared/QwenEnv.ps1`（S2 拥有）**
- `Test-QwenNativeWeights -Dir <path>` → `{Complete, Missing[]}`
- `Test-QwenArtifact -Path -Name -MinBytes` → bool
- `Get-QwenArtifactMinBytes -Name` → long
- `Invoke-PyProbe -Python -Code` → stdout 或 `$null`（吞掉 stderr，5.1 必需）
- `Resolve-QwenModelTargets -StartDir -ComfyDir -Specs` → `{Present, Missing, Targets}`
- `Initialize-QwenEnv -StartDir -NeedVenv -NeedDiffusion -NeedComfy -NeedModels -NeedWeights -Quiet`
  → `{ProjectRoot, VenvDir, ComfyDir, ModelsCache, OutputDir, Python}`
- `Test-QwenModelsCache -Path -NeedWeights`（接受 HF 缓存布局**和**普通 `Qwen-Image-2.1` 目录）

**`setup/qwen_setup.ps1`（S2 拥有）**
- `-Action probe|manifest|env|comfyui|models-mixed|models-native|fix-torch|status`
- `-Action manifest -Kit native|mixed|turbo` → JSON
  `{kit, label, repo, local_dir, manifest_ok, total_bytes, file_count,
    present_count, missing_count, damaged_count, missing[], damaged[]}`
- `-InstallRoot <dir>`、`-StartDir <dir>`、`-Yes`、`-WithGguf`、`-Kit <name>`
- probe 的 JSON 键：`project_root, install_root, search_dir, venv{path,torch,ok},
  comfyui{path,ok}, models_cache{path,native}, models{encoder,vae,diffusion,
  turbo_diffusion,turbo_lora,turbo_gguf,native}`

**`launcher/core.py`（S2 拥有，S4 只读）**
- `probe(install_root, on_line, manual)` → dict
- `install / install_env / install_comfyui(..., state, manual)` → exit code（可中断）
- `model_manifest(kit, install_root, manual)` → dict（同 manifest JSON）
- `uninstall_kit(kit, install_root, manual, on_line)` → exit code
- `cancel_install()` / `install_running()`
- `kit_of(choice_key)` / `KIT_LABEL`
- `draw(plan, images, port, seed, steps, on_line, on_image)` → `[(png_bytes, info)]`
- `build_env(state, install_root, manual)` / `path_overrides(state, manual)`
- `load_secret/save_secret/secret_info`（DPAPI + SHA-256 指纹）

**`launcher/agent.py`（S4 拥有）**
- `PROVIDERS = {deepseek, glm}`，每个含 `base/name/bare_base64`
- `list_models(provider_key, api_key)` → `[str]`
- `probe_vision(provider_key, api_key, model)` → `("vision"|"text"|"unknown", reason)`
- `refresh(provider_key, api_key, on_progress)` → `{listed, vision, text_only, unknown, excluded}`
- `ask(provider_key, api_key, model, prompt, images, ui_count, ui_transparent)` → dict
- `Plan.from_agent(data, n_images, max_pixels)` → `Plan`（含 `ordered_images()`、`summary()`）
- `Plan` 字段：`mode, prompt, negative_prompt, width, height, transparent,
  latent_mode, count, edit_image_index, reference_indices, reasoning`

**`launcher/agent_panel.py`（S4 拥有）**
- `AgentPanel(get_strategy, get_state, port)`，信号 `log(str)`、`image_ready(bytes, dict)`
- `app.py` 只通过 `AgentPanel(get_strategy=..., get_state=...)` 和这两个信号与它交互

**打包清单（S2 拥有）**
`launcher/core.py` 的 `BUNDLED_FILES` 与 `qwen_launcher.spec` 的 `BUNDLED`
**必须完全一致**。S3 或 S4 新增需要进包的脚本时，在「请求」区通知 S2。

### [S4] 2026-10-17 11:56 — `launcher/agent.py` 对外行为补充（签名不变）

- **已有的签名全部未变**：`list_models` / `probe_vision` / `refresh` / `ask` /
  `Plan.from_agent(data, n_images, max_pixels)` / `Plan.ordered_images()` / `Plan.summary()`，
  `Plan` 字段也不变。`agent_panel.py` 零改动。
- `Plan.warnings` 现在覆盖**每一处纠正**（任务书要求「每条纠正都要作为 ⚠ 显示」，此前有几处是静默改）：
  尺寸夹到 256–4096、非 32 倍数对齐、超像素上限缩放、`count` 夹到 1–8、
  `reference_indices` 里非整数/越界项被丢弃、reference 模式无有效索引时回退第 0 张。
- `transparent` 现在能正确解析字符串布尔：`"false"/"no"/"0"/""` → `False`
  （旧实现用 `bool()`，`"false"` 会被当成 `True`）；无法解析时告警并按默认值。
- `Plan.ordered_images()` 对越界 `edit_image_index` / `reference_indices` 做防御性夹紧
  （不再可能 `IndexError`），「被编辑图排第一位」的语义不变。
- `list_models()` 对裸数组响应的元素额外接受 `model` 键（此前只认 `id`/`name`）。
- 新增 `launcher/tests/`（S4 维护、**不进包**）：`test_agent_logic.py` 74 项、
  `test_agent_panel.py` 30 项，跑法写在各自文件头。**不要**加入 `BUNDLED_FILES`/`BUNDLED`。

### [S2] 2026-10-17 12:20 — 新增可选环境变量 `QWEN_TEXT_ENCODER`（下载侧）+ artifact 表扩展

- **`setup\qwen_setup.ps1`**：新增可选环境变量 **`QWEN_TEXT_ENCODER`**（裸文件名或路径，只取 leaf name，
  需以 `.safetensors` 结尾）。`-Action models-mixed` 的下载清单、`-Action probe` 的 encoder 槽、
  `-Action manifest -Kit mixed` 的 patterns 都会用它替换默认的 `qwen3vl_8b_int8_convrot.safetensors`。
  **未设置时行为与之前 100% 一致**（已实测对照：default manifest 仍为 5 文件 / 25,453,193,453 B）。
  运行时侧 `shared/qwen_comfy_app.py` 早已支持同名变量，本次只是让下载侧对齐；两边不得改名。
- **probe JSON**：键不变；`models.encoder.gib` 现在取「文件存在时的实测大小」，W4A8 未安装时按 5.88
  报告，不再是固定 8.71。
- **`shared\QwenEnv.ps1`**：`$script:QwenArtifactRules` 增加
  `@{ Match = 'qwen3vl.*w4a8'; Bytes = 6312105364 }`（`Get-QwenArtifactMinBytes` 返回 ×0.9），
  其余规则与取值不变。
- 影响面：S3（若把编码器选择 profile 化，请用同一变量名或先在契约区登记）、S4（无影响）、
  S5（打包清单未变，仍 16 项）。

---

## 结论

<!-- 已确认的事实，只增不改。S1 的调研结论放这里，供 S2/S3 直接引用。 -->

### [基线] 硬件与实测数据（2026-10-17）

- 测试机：RTX 4060 Laptop **8188 MiB**、31.7 GB RAM、i9-13900HX、Windows、中文区域
- 1024² 稳态（ComfyUI 自身计时，取两次中的第二次）：
  - W4 25 步 **38.98 s**（1.48 s/it）
  - Turbo 8 步 simple **14.04 s** / exact grid **13.75 s**
  - W4 8 步 **13.69 s**
  - → 2.85× 来自**步数**，LoRA 负责在 8 步保住画质
- GGUF Q4_K_M 每步 **3.53 s/it** vs 原生 W4 **1.20 s/it**（慢 2.9×，架构原因：
  GGUF 走反量化 + `F.linear`，W4 走融合 int8 kernel）
- VAE **去噪期间不驻留**，解码时才加载（采样峰值 ~6.9 GB < 解码峰值 ~7.5 GB）
- 前缀/KV cache 位置对画质与耗时**无可测影响**；挂了 LoRA patch 时 ComfyUI 自动关闭前缀缓存
- Turbo 官方 8 步 sigma 曲线（ComfyUI 无法从 ckpt/LoRA 读取，写死在
  `shared/qwen_comfy.py`）：
  `1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0`
- 透明背景：用模型卡规定的前缀
  `This is an RGBA image with transparency. {subject}. ...`
  实测 21% 像素完全透明；直接写提示词则为 0%

### [基线] 资产体积（ModelScope 实测）

| 组件 | 大小 |
|---|---|
| W4 mixed `qwen_image_2.1_mixed_balanced` | 3.91 GiB |
| 文本编码器 `qwen3vl_8b_int8_convrot` | 8.71 GiB |
| VAE `qwen_image_2.1_vae_bf16` | 0.63 GiB |
| Turbo LoRA | 913,314,512 B |
| Turbo int8 扩散 | 6.76 GiB |
| Turbo GGUF Q4_K_M | 4,189,346,592 B |
| 原生 BF16 全套（`Qwen/Qwen-Image-2.1`，29 个文件） | 30.9 GiB |

### [基线] 环境

- Python **3.11.9**；torch **2.10.0+cu130**；diffusers 0.41.0.dev0；transformers 5.17.0；
  gradio 6.28.0；fastapi 0.141.1；PyQt5 5.15.11；PyInstaller 6.22.3；modelscope 1.40.2
- ComfyUI 0.37.0（主实例，端口 7860）；隔离的 ComfyUI 0.39.0（gguf，端口 **8189**）
- EXE 单文件约 **43.4 MB**，`launcher\dist\QwenImageLauncher.exe`

### [S1] 2026-10-17 11:55 — 变体全景与两个关键新文件（权威体积）

- 全部变体清单、分片、来源见 `docs/research.md` §1（DiT 16 种 / 编码器 13 种 / VAE 3 / 加速件 4 / GGUF 全档位 / ControlNet）。
- **新发现 1：W4A8 文本编码器 `text_encoders/qwen3vl_8b_w4a8.safetensors` = 5.879 GiB**（Comfy-Org，MS 可下），比现役 int8 8.709 GiB 省 2.83 GiB（磁盘+内存）。
- **新发现 2：`qwen3vl_8b_int4_convrot` 6.408 GiB、`jameriles` Hybrid W4A8/INT8 DiT 4.790 GiB + 编码器 6.279 GiB**（仅 HF），是 8–12 GB 的备选，但均无 LPIPS。
- 格式可行性已核实：ComfyUI 0.37.0 支持 `int8_tensorwise(+convrot)` / `asym_w4a8_int8` / `convrot_w4a4` / `float8_e4m3fn/e5m2`；`nvfp4`/`mxfp8` 需 Blackwell，本机 4060 不可用。

### [S1] 2026-10-17 11:55 — GGUF 不推荐：既慢 2.9×，画质也明显更差

- unsloth 对 Turbo GGUF 的 LPIPS（AlexNet，8 步）：**Q4_K_M 0.153**、Q5_K_M 0.074、Q6_K 0.055、Q8_0 0.036；同尺寸 INT8 为 **0.037**。
- 即"Q4_K_M 与 INT8 同体积（3.9 vs 6.76 GiB 其实是更小），但画质差 4 倍、速度慢 2.9×（本机 3.53 vs 1.20 s/it）"。
- `gguf` 策略保留为对照/极端小显存备用即可；6–8 GB 场景不应优先 GGUF。
- 若必须 4–6 GB：Q4_K_S/Q4_K_M + **CPU VAE** 才勉强进 6 GB；4 GB 只有 Q2_K + ≤768²。

### [S1] 2026-10-17 11:55 — 显存分档：8 GB 现状最优；12–24 GB 是项目缺口

- 8 GB：维持 `mixed-vram`（W4 3.915 + VAE 0.629，编码器在 CPU）+ 可选 Turbo LoRA；实测峰值 7294（1024²）/7563 MiB（2048²）。
- 12 GB：**INT8 ConvRot DiT 6.758 + W4A8 编码器（可试上显存）**，峰值估算 **10,206 MiB**（编码器在 CPU）/ 16,226 MiB（两者常驻，略超 12 GB）；或继续 W4。
- 16 GB：**W4 + W4A8 编码器两者常驻显存**（4009+6020+644+2641=**13,314 MiB**，2048² 13,583 MiB；16 GB = 16384 MiB）。
- 24 GB：**BF16 DiT 13,571 + W4A8 编码器 + VAE = 22,876 MiB**（2048² 23,145 MiB；24 GB = 24576 MiB），是 ComfyUI 路线画质上限；或 BF16 编码器+W4 DiT（24,016 MiB，很紧）。
- 32 GB：全 BF16 需 33,578 MiB → 会溢出 810 MiB；建议 BF16 编码器 + INT8 DiT（26,928 MiB）。完整表见 `docs/research.md` §3。

### [S1] 2026-10-17 11:55 — 画质数据汇总（LPIPS 有出处的才算）

- **Base**：unsloth INT8 0.064 / FP8 0.112；ProCreations Calibrated FP8 **0.0345**（SSIM 0.9713，仅自定义 diffusers 运行时）。
- **Turbo**：unsloth INT8-ConvRot **0.037** / INT8 0.089 / FP8 0.125；GGUF 见上条。
- **W4 mixed_balanced（在用）、Comfy-Org int8_convrot（README 声称的 0.047）、W4A4 INT4 均无公开 LPIPS**；W4A4 有"24 场景盲评 18 个差"的反面证据。
- 跨卡速度参考：5090/1024²/40 步 BF16 15.629 s、INT8 7.553 s、NVFP4 6.746 s（峰值 31677/18433/13249 MiB）；PRO 6000 FP8 对 BF16 1.40×。

### [S1] 2026-10-17 11:55 — 来源可达性（与基线不一致，S2 请注意）

- 实测 HF **可达**：API HTTP 200、文件 range 下载 HTTP 206、0.37–0.58 MB/s（基线曾说不可达）。
- ModelScope 有：Qwen 官方全部、Comfy-Org 全部单文件变体、Abiray 两个 GGUF 仓、unsloth 的 FP8/GGUF 仓、lightx2v、ProCreations。
- ModelScope **无**：`NidAll/Qwen-Image-2.1-Mixed-Balanced`（**在用 W4 的唯一来源**）、NidAll Turbo INT8、toxicdog INT4 系列、jameriles、JoaoZaokk、BennyDaBall NVFP4 → 全部仅 HF。
- 分发风险：若 HF 再次不可达，这些变体无法重下；建议 S2 至少归档 W4 mixed_balanced。

### [S4] 2026-10-17 11:56 — Step 4 Agent 核验结论（冻结 EXE + 单测）

- **冻结 EXE 的参考图加载已验证通过**（`launcher\dist\QwenImageLauncher.exe`，2026-10-17 11:26 构建，
  SHA256 `EBD8512C27CA6181B79291D4D73AA133AF9C5F6B8370ED2CAC4F73F3EFE5EAD5`，
  45,544,303 B）：`"frozen": true, "qt": "5.15.2", "pillow": "12.3.0"` → **Pillow 确实打进包了**；
  png / jpg / bmp / webp / 透明 png / 5000×3000 六种输入全部 `ok`；5000 长边的图 45,860 B
  → 发给视觉模型的拷贝 8,225 B（按 1568 上限缩小）；`button_click_ok: true`
  （`add_images(False)` 历史闪退路径不再复现）；`loaded_images: 6, tuple_width: 3, all_ok: true`，
  进程退出码 **0**。
- **本机跑冻结 EXE 的正确姿势（否则会“假挂起”，我已踩过）**：
  ① PyInstaller onefile 必须先解包到 `%TEMP%\_MEIxxxx`，而本机自动化会话的 `%TEMP%` **不可写**
     → 启动前把 `$env:TEMP`/`$env:TMP` 指到一个可写目录；
  ② `Start-Process -Wait` 走 ShellExecute，会**卡死且拿不到退出码、不生成 `_MEI`**，
     改用 `New-Object System.Diagnostics.ProcessStartInfo` + `$psi.UseShellExecute=$false`
     + `[System.Diagnostics.Process]::Start($psi)` + `WaitForExit(ms)`；
  ③ `--report <path>` 的参数若带引号会被解析成「含空格的路径」而报 `OSError Errno 22`
     → 干脆不传 `--report`，读默认的 `<EXE 目录>\selftest_image.json`。
- **离线单测**（新增 `launcher/tests/`，不联网、不需要 Key）：
  `launcher\.venv\Scripts\python.exe launcher\tests\test_agent_logic.py` → **74 项通过**（0.55 s；
  含 127.0.0.1 回环假提供方，真实走 urllib，覆盖 401/404/429/纯文本/非 JSON 五种判决）；
  `...\test_agent_panel.py` → **30 项通过**（0.41 s，offscreen Qt：六格式解码、双尺寸、
  `add_images(False)` 按钮路径、只可选读图模型、画廊缩略图/另存/删除、无后端时提示）。
- **尚未实测（本轮按用户指示不做全量验证，环境也不具备）**：① 真 Key 链路
  （`refresh` → `ask`）——本会话无 API Key；② 真出图 → 画廊——7860 与 8189 后端均未运行
  （`core.strategy_alive` 两边都 False），属 S3 范围。两条链路的补跑口径：
  填 Key → 点「联网刷新 + 实测读图」→ 选一个带「实测可读图」的模型 → 写提示词+加参考图
  →「① 让 AI 规划」看 `plan_box` 的 ⚠ 行 →「② 按规划绘图」→ 缩略图点开可另存/删除。

### [S2] 2026-10-17 12:20 — EXE 重建与冻结件复检（补充 S4 结论）

- **交付物**：`launcher\dist\QwenImageLauncher.exe` = **45,547,059 B**，构建 2026-10-17 12:05（37.0 s 完成），
  SHA256 **2EEB86FD5CBBCDF781EC1C31C559D003C436757ABA00367B070F92F79FDCB264**。含 S4 的 `agent.py`
  （11:53）与本次 `QwenEnv.ps1`/`qwen_setup.ps1` 改动；`BUNDLED_FILES`(16) 与 spec `BUNDLED`(16)
  机器校验完全一致，16 个源文件齐全。
- **冻结自检**：全新目录首启释放 16 文件 → `--selftest-image` rc=0、`all_ok=true`（Pillow 12.3.0 在包内、
  `add_images(False)` 不崩、png+webp 双格式）；「中文+空格」路径同样 PASS；offscreen 下 GUI 事件循环
  10 s 存活。5 页截图：`_verify\_gui_shots\1_detect.png … 5_uninstall.png`（含 STEP 4 的 AI Agent 面板）。
- **本机自动化跑冻结 EXE 的正确姿势**（S4 条目的补充，已封装为 `_verify\run_exe_selftest.py`）：
  ① 子进程 **TEMP/TMP 必须指到一个普通可写目录**——自动化会话给的 `%TEMP%`（短路径 + 定制 ACL）会让
     onefile 解包失败，进程 rc=-1、report 不写、看起来像产品崩溃（我用 3 个不同时期的 EXE + console 版
     交叉验证才排除是产品问题）；② `Start-Process -Wait` 在本机会假挂起（别用），改用 Python
     `subprocess.run(..., capture_output=True)` 或 `ProcessStartInfo`+`WaitForExit(ms)`；
  ③ `--report` 传绝对路径没问题，带空格/中文路径也 OK。
- **`_verify\verify.py` 同理**：它把 offscreen 冒烟脚本写到 `%TEMP%`，同一原因会 PermissionError；
  已在文件头加「TEMP 不可写就退到 `_verify\_tmp_verify`」的兜底，之后直接跑即可（本次复跑 PASS）。
- **模型来源实测（2026-10-17 12:0x，API 逐字节）**：ModelScope `Comfy-Org/Qwen-Image-2.1` 共 18 文件/
  121,480,804,931 B，**无 mixed_balanced**；HF 同名仓 22 项也**无**；MS `NidAll/…Mixed-Balanced` **404**，
  HF `NidAll` = 4,203,397,480 B（唯一来源）。manifest 实测：native 29 文件/33,134,789,600 B、
  mixed 5 文件/25,453,193,453 B（present 4 / missing 1 = turbo_int8）、GGUF 4,189,346,592 B；
  `QWEN_TEXT_ENCODER=qwen3vl_8b_w4a8.safetensors` 时 mixed manifest = 5 文件/22,414,500,457 B。
- **同机现状**：RTX 4060 Laptop 8.0 GiB / RAM 31.7 GiB / **C: 仅剩 49.0 GiB** —— 这也是不做 3.9 GiB
  W4 归档的原因之一。

