# Qwen-Image-2.1 启动器 / launcher

赛博朋克风格的图形前端（PyQt5）。它**不重新实现任何安装逻辑**——检测、安装、启动、
停止、卸载全部委托给已有的 PowerShell 脚本，自己只负责界面、引导和硬件建议。

```
launcher\
├─ app.py              PyQt5 主程序（5 步引导界面）
├─ core.py             后端：探测 / 安装 / 启动 / 停止 / 卸载，全部包一层调用
├─ theme.py            赛博朋克配色与 QSS（蓝 / 黑 / 绿 / 荧光）
├─ icon.ico            应用图标（多尺寸 16–256，圆角透明）
├─ qwen_launcher.spec  PyInstaller 配置（单文件、无控制台窗口、内嵌图标）
├─ build_exe.bat       一键构建 dist\QwenImageLauncher.exe
└─ README.md
```

### 图标是自己画的

`icon.ico` **由本项目的 Qwen-Image-2.1 自己生成**（`turbo` 策略、8 步、1024²）：
提示词要求"霓虹青色电路走线构成的字母 Q、深色圆角方形徽章"。生成 3 个候选后选用了
笔画最粗的那个（小尺寸下最清晰），再用 PIL 裁成正方形、切圆角加透明、导出
16/24/32/48/64/128/256 七个尺寸。

重新生成：`python _bench\make_icon.py`（会重新调用 Qwen-Image 画候选），
或 `python _bench\make_icon.py --pick 2`（换用第 2 个候选直接重建 .ico）。

---

## 运行

```bat
:: 直接用 Python（开发 / 调试）
..\.venv\Scripts\python.exe app.py

:: 或构建成单个 EXE
build_exe.bat        ->  launcher\dist\QwenImageLauncher.exe
```

**EXE 放在哪里**：项目根目录或它的任意子目录里都行——程序会向上查找
`shared\QwenEnv.ps1` 来定位项目。找不到会给出明确提示。

窗口默认 **1600×900**，可最小化、可拖拽边框缩放（最小 1180×660），
**F11** 或右上角按钮切换全屏，**Esc** 退出全屏。

正文按 15px 排版（标题 32px、小节 15px、提示 14px），底部终端输出保持 12px 不变。

---

## 两种形态：开发版 / 分发版

同一份源码，两种用法，互不干扰。

### 开发版（现在这样）

直接跑 `launcher/app.py`。`shared\*.ps1`、`setup\qwen_setup.ps1`、各策略的 `app.py`
都是**散装文件**，改完立刻生效，不用重新打包。

```powershell
cd launcher
..\.venv\Scripts\python.exe app.py
```

`core.bundling()` 返回 `False`，`core.PROJECT` 就是仓库根目录，不会往磁盘上复制任何东西。

### 分发版（单个 EXE）

```powershell
cd launcher
..\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm qwen_launcher.spec
# 产物：launcher\dist\QwenImageLauncher.exe
```

`qwen_launcher.spec` 把 **`core.BUNDLED_FILES` 里列出的每一个脚本**都打进 EXE
（`shared\` 9 个 + `setup\` 1 个 + 5 个策略 `app.py` + `USER_GUIDE.md`），
所以对外**只需要分发这一个 .exe**。

**为什么首次运行会释放出一堆文件夹**：PowerShell 和项目 venv 需要**真实的文件路径**
才能执行脚本，EXE 没法把内存里的模块交给它们。所以第一次运行时，
`core.sync_bundle()` 会把打包的脚本释放到 EXE 同目录。

**升级为什么不会丢数据**：`sync_bundle()` 是**逐文件比对内容**复制的，
而且**只增不删**。所以替换 EXE 只会刷新启动器自己的脚本，
`.venv\`、`ComfyUI\`、`models_cache\`、`outputs\`、以及隔离的 `gguf\ComfyUI\` 全都原样保留。

两份清单必须同步：`core.BUNDLED_FILES` 与 spec 里的 `BUNDLED`。
spec 在缺文件时会直接报错，不会悄悄打出个残缺的 EXE。

### 打包产物自检

分发版没有控制台，所以验证结论写进 JSON：

```powershell
QwenImageLauncher.exe --selftest-image "一张图.png" "另一张.webp" --report out.json
```

它会**真的跑一遍"添加图片"按钮路径**（打桩文件对话框）并解码每个文件，
输出 `{frozen, qt, results[], button_click_ok, loaded_images, all_ok}`。
当初的闪退是按钮信号把 `checked` 传进了 `paths`，只有走按钮才复现得出来——
所以这个自检必须点按钮，不能只调解码函数。

## 五步引导

| 步骤 | 做什么 |
|---|---|
| **1 检测** | 显卡型号 / 显存 / 内存 / C 盘可用 / 驱动版本；再用 `qwen_setup.ps1 -Action probe` 逐项检查 venv、torch、ComfyUI、模型缓存与每个模型文件 |
| **2 定位 / 安装路径** | 指定已有的 **Python 环境 / ComfyUI / 模型缓存** 三个目录并重新检测；确实没有 → 指定**安装根目录**。勾选底部"记住以上路径"后，下次启动直接进 STEP 4 |
| **3 环境配置与模型安装** | 先补 `env`（venv + torch cu130）与 `ComfyUI`，再选模型。每张卡片都带**针对你硬件的建议**（见下） |
| **4 启动与出图** | 选运行策略 → 启动 → 自动轮询 `/api/health`，就绪后自动打开浏览器；下方是 **AI Agent 交互面板**（见下） |
| **5 卸载** | 三个独立入口：① 环境 ② 某个模型 ③ 本启动器组件 |

### 记忆：第二次打开就跳过向导

STEP 2 勾选"记住以上路径"后，解析出的三个目录会写进 **`launcher_state.json`**（就在 EXE 旁边）。
下次启动时如果该文件存在且路径仍然有效，程序会：

1. 直接载入全部路径（并预填 STEP 2 的三个输入框，方便随时改）
2. 仍然做一次快速探测（确认模型真的还在）
3. **直接跳到 STEP 4**，不再走路径设定与模型安装

想回到常规流程：点 STEP 4 右下角的 **"忘记记忆并重新检测"**，或删掉 `launcher_state.json`。
如果记忆里的目录被删了，程序会打印警告并自动回退到常规检测。

### AI Agent：用自然语言驱动本地模型

STEP 4 下半部分。它把"一句话 + 几张图"翻译成本地模型能吃的精确参数。

**两家 API（都实测可达，都是 OpenAI 兼容）**

| 提供方 | Base URL | 模型列表 | 申请入口 |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `GET /models` | platform.deepseek.com/api_keys |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `GET /models` | open.bigmodel.cn/usercenter/apikeys |

**模型列表联网实时拉取**（用你自己的 Key），不写死任何模型名。
拉回来后**只保留支持读图的模型**——因为 Agent 的核心工作就是看你给的图片：

- 命中 `vision / vl / 4v / 5v / v-plus / v-flash / omni` 等特征 → 可选
- 已知纯文本家族（`deepseek-chat`、`glm-4-flash`、`glm-4.5` …）→ 过滤
- **无法确认的一律过滤**，并统计告诉你过滤了多少个
- 需要时可用"显示全部模型（不推荐）"把被过滤的也列出来（默认关闭）

> API Key 默认只存内存。要它下次还在，得勾选"记住 API Key"——
> 那会**明文**写进 `launcher_state.json`，界面上写明了这一点。

**Agent 契约**（`agent.py` 的 `SYSTEM_PROMPT`）要求模型只返回一个 JSON：

```json
{"mode": "text2image | edit | reference",
 "prompt": "改写后的英文提示词",
 "width": 1024, "height": 1024,
 "transparent": false,
 "latent_mode": "custom | reference",
 "count": 1,
 "edit_image_index": -1,
 "reference_indices": [],
 "reasoning": "用用户的语言说明为什么这么选"}
```

`Plan.from_agent()` 会**校验并夹紧**：尺寸对齐 32 的倍数、像素乘积不超上限、
`count` 夹到 1–8、图片索引越界就改用第 0 张、没有图却要 edit 就退回 text2image。
每条纠正都作为 `⚠` 显示，不静默改用户意图。

**绘制流程**：① 让 AI 规划 → 显示规划结果 → ② 按规划绘图（也可"跳过 AI，直接绘图"）。
多张图以 `count` 个不同 seed 依次提交；出图后按缩略图排在画廊里，
**点击缩略图放大看原图**，弹窗里可**另存为**或**删除**。

**为什么走 HTTP 而不是直接调用**：冻结后的 EXE 刻意不打包 torch/gradio（体积 2 GB → 45 MB），
所以它通过 7860 端口的 `/api/generate` 驱动正在运行的策略——也就是对外公开的那套 API。


### 硬件建议（不是只列清单）

`core.build_choices()` 按显存 / 内存 / 磁盘把推荐项标成 **★ 推荐**，并在卡片上写明理由：

| 条件 | 建议 |
|---|---|
| 显存 ≥ 16 GiB | 全量原生 BF16 |
| 显存 8–16 GiB | **量化 + Turbo**（8 步，实测快约 2.8×，画质与 25 步同档） |
| 显存 < 8 GiB | 量化三件套，并提示控制分辨率；另提示 GGUF 占用更小 |
| 内存 < 24 GiB | 即使显存够也**不推荐**原生 BF16（文本编码器 16.3 GiB 在内存里） |
| 磁盘 < 所需 + 5 GiB | 在所有大件上标 ⚠ |

策略下拉框也会给出逐条提示，例如选 `native` 时会提示"显存低于 16 GiB 可能溢出到共享显存"。

**实测示例**（RTX 4060 Laptop 8 GiB / 31.7 GiB 内存 / 61.5 GiB 可用）：

```
★ 推荐  量化模型 + Turbo 蒸馏（8 步，快约 2.8×）      约 6.76 GiB
        全量原生 BF16（diffusers）                    已就绪
        量化模型（W4 / INT8 + INT8 文本编码器）        已就绪
        Turbo GGUF Q4_K_M（隔离运行时）               已就绪
```

---

## 自定义安装路径（为此改动了两个已有脚本）

先前的脚本只会把东西装在自己所在目录。现在支持指定**安装根目录**，
选好之后环境与模型都装在那里，后续安装复用同一位置：

```
<根目录>\ .venv               Python 环境
<根目录>\ ComfyUI             ComfyUI 检出
<根目录>\ ComfyUI\models      量化模型
<根目录>\ models_cache        原生 BF16 缓存
```

为此做了两处**向后兼容**的改动：

**1. `shared\QwenEnv.ps1`**：新增显式环境变量覆盖，优先级
`环境变量 > 缓存文件 > 目录搜索`，且每一项都会先验证再采用：

| 变量 | 作用 |
|---|---|
| `QWEN_VENV_DIR` | 直接指定 Python 环境 |
| `QWEN_COMFYUI_DIR` | 直接指定 ComfyUI 检出 |
| `QWEN_MODELS_CACHE` | 直接指定原生模型缓存 |

这是让"装在别处"能被重新找到的关键——Python 侧的 `qwen_paths.py` 本来就认这些变量，
而 PowerShell 侧以前只认缓存文件和目录搜索，两边不一致。

**2. `setup\qwen_setup.ps1`**：新增 `-InstallRoot` 参数与 `probe` 动作。

- `-InstallRoot <dir>`：在上面列出的位置创建，并让发现过程也从这里开始（重跑能找到上次装的东西）
- `-Action probe`：输出一段带哨兵标记的 JSON，供 GUI 读取

**不带这两个参数时行为与改动前完全一致**（已回归验证）。

> `probe` 是 GUI 与 CLI 不跑偏的关键：GUI **不自己复制一份检测规则**，
> 而是走和其它动作完全相同的 `Initialize-QwenEnv` / `Resolve-QwenModelTargets`。

---

## 卸载

| 入口 | 删除内容 | 保留 |
|---|---|---|
| ① 卸载环境 | `.venv` + `ComfyUI` | 模型文件 |
| ② 卸载某个模型 | 只删选中的那一个文件 | 环境与其它模型 |
| ③ 卸载本启动器 | launcher 目录 / EXE | 勾选"保留"时环境与模型都保留；取消勾选则连环境、全部模型、模型缓存一起删 |

每个入口都会**先列出具体路径再二次确认**；"彻底卸载"还会再确认一次。

> 正在运行的 EXE 无法删除自身全部文件，程序会明确提示手动删除残留目录。

---

## 不在本启动器范围内

**Turbo GGUF 的隔离运行时**需要单独准备（模型来自 `Abiray/Qwen-Image-2.1-Turbo-GGUF`，
且需要一份独立 ComfyUI 检出装 ComfyUI-GGUF）。GUI 会检测它是否已存在、可启动，
但不会替你搭建。步骤见 `..\gguf\README.md`。

---

## 已验证 / 未验证

**已验证**（离屏渲染 + 真实检测）：

- 五页界面全部渲染正常，配色与布局正确（截图见 `_bench\shots\`）
- 真实硬件检测：RTX 4060 Laptop / 8.0 GiB / 31.7 GiB / 61.5 GiB 可用
- 真实状态探测：venv + torch 2.10.0+cu130、ComfyUI、模型缓存、6 类模型文件逐项判定正确
- 建议逻辑：8 GiB 显存正确推荐 `turbo`
- 五个策略的提示文案全部生成
- 卸载页正确列出 5 个已安装模型

**未验证**：

- 从 GUI 实际触发一次完整安装（会真的下载十几 GB，没有执行）
- 从 GUI 启动 / 停止真实策略（会占满显存，没有执行）
- 卸载的实际删除效果（**没有执行任何删除**）
- 在有独立显卡驱动的真实桌面上运行（测试用的是 Qt offscreen 平台）
