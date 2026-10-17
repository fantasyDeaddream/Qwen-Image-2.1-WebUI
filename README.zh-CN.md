# Qwen-Image-2.1 WebUI

在 Windows 本地运行 **Qwen-Image-2.1** 的启动器与多策略运行时——目标是让 **8 GB 显存的笔记本
显卡也能出 24 GB 显卡那样的图**。

> [English README](README.md)

---

## 这是什么

Qwen-Image-2.1 是个很大的模型：BF16 全量权重约 **30 GB**，由 13.25 GB 的 transformer、
16.33 GB 的 Qwen3-VL 文本编码器和 0.63 GB 的 VAE 组成。纸面上这远超普通游戏本的承受范围。

本项目让它跑得起来，做了四件事：

1. **把模型拆到两块你真正拥有的内存池里。** 文本编码器每个提示词只用一次，之后一直闲置，
   所以放进内存；扩散 transformer 和 VAE 跑在显存里。这样既保住了原生模型的画质，
   又不用为你没有的显存买单。
2. **提供五套可互换的运行策略**（量化 / 原生 / 蒸馏 Turbo / GGUF），
   让你在磁盘、显存和速度之间自由取舍。
3. **提供一个单文件启动器**：自动检测硬件、给出策略建议、下载模型、拉起 WebUI。全程不用命令行。
4. **内置 AI Agent**：把一句自然语言加上几张参考图，翻译成本地模型能精确执行的绘图任务。

图形界面和命令行**驱动的是同一套 PowerShell 脚本**，所以两者不会出现行为漂移。

---

## 环境要求

| | |
|---|---|
| 系统 | Windows 10 / 11 64 位 |
| 显卡 | NVIDIA，显存 8 GB 以上（CUDA 13.0 wheel） |
| 内存 | 最低 16 GB，32 GB 更从容 |
| 磁盘 | 量化路线约 15 GB，原生 BF16 约 31 GB |
| 网络 | 首次运行需要（会下载 Python、ComfyUI 与模型） |

**不需要预先安装 Python 和 Git**，安装脚本会自动准备。

---

## 快速开始

### 方式一：直接用打包好的启动器（推荐）

1. 从 [最新 Release](../../releases/latest) 下载 `QwenImageLauncher.exe`。
2. 放进一个单独的文件夹后运行。Windows SmartScreen 会提示未签名——选择
   **更多信息 → 仍要运行**。
3. 按左侧五步走。第 3 步会用 ★ 标出**针对你硬件推荐**的路线。
4. 后端就绪后浏览器会自动打开。标题栏的 **使用说明** 按钮里有内置指引。

首次运行时，EXE 会在自己旁边**展开脚本**：PowerShell 和项目虚拟环境需要真实的文件路径，
没法塞进二进制里。之后替换 `.exe` 只会刷新这些脚本，
**虚拟环境、ComfyUI、模型和 outputs 都不会被动**。

### 方式二：从源码运行

```powershell
git clone https://github.com/fantasyDeaddream/Qwen-Image-2.1-WebUI.git
cd Qwen-Image-2.1-WebUI

# 一次性安装，按顺序执行
.\setup\setup_env.bat            # 虚拟环境 + torch 2.10.0 / cu130 + 应用依赖
.\setup\setup_comfyui.bat        # ComfyUI，并为 Qwen-Image-2.1 打补丁
.\setup\setup_models_mixed.bat   # 约 14 GB 量化权重 + Turbo LoRA

# 启动某个策略
.\turbo\start.bat                # 或 mixed\ 、mixed-vram\ 、native\ 、gguf\
```

也有一个可交互的安装向导，会检测已有环境并让你选路线：

```powershell
.\setup\setup_wizard.bat
```

---

## 运行策略

每个文件夹都是一套自包含的运行方式。它们的区别在于**什么放在哪里**，
而不在于出图的代码。

| 目录 | 做什么 | 峰值显存 | 磁盘 | 说明 |
|---|---|---|---|---|
| `mixed-vram` | W4 量化，显式放置：编码器在内存，扩散 + VAE 在显存 | ~7.0 GB | 3.9 GB | **8 GB 显卡的最佳默认** |
| `mixed` | 同样的 W4 权重，放置交给 ComfyUI 调度 | ~7.0 GB | 3.9 GB | 12 GB 以上更从容 |
| `turbo` | W4 + Turbo LoRA，8 步采样 | ~7.0 GB | 4.8 GB | **推荐**，见下 |
| `native` | 原生 BF16，走 `diffusers` | >16 GB | 30.2 GB | 画质上限最高 |
| `gguf` | Turbo GGUF Q4_K_M，独立 ComfyUI 实例 | ~6.9 GB | 3.9 GB | 慢 2.9× **且画质更差**——仅作 4–6 GB 兜底，见 `gguf/README.md` |

### 关于 Turbo

Turbo LoRA 自带一条 8 步 sigma 曲线，而 ComfyUI 无法从 checkpoint 或 LoRA 里读到它，
所以 `shared/qwen_comfy.py` 把它显式写死：

```
1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0
```

不用这条曲线的话，ComfyUI 的 `simple` 调度器在 8 步下走的是明显不同的曲线
（平均绝对差 0.148，而 `linear_quadratic` 是 0.031）。在怀表这类提示词上，
纯 W4 跑 8 步会出现机芯糊成一团、罗马数字乱码；而 Turbo 跑 8 步与完整 25 步基线几乎看不出差别。

---

## 性能实测

测试平台：**RTX 4060 Laptop（8188 MiB）、31.7 GB 内存、i9-13900HX**，
耗时取自 ComfyUI 自身的统计。"稳态"指连续两次相同配置中的第二次，因此不计模型装载时间。

### 1024 × 1024

| 配置 | 总耗时 | 每步 |
|---|---|---|
| W4，25 步（基线） | 38.98 s | 1.48 s/it |
| Turbo LoRA，8 步，`simple` | 14.04 s | 1.15 s/it |
| Turbo LoRA，8 步，精确曲线 | 13.75 s | 1.48 s/it |
| W4，8 步 | 13.69 s | 1.46 s/it |

所有 8 步配置的耗时相同。**约 2.85 倍的加速来自步数，不是来自 LoRA**——
LoRA 的作用是让 8 步的画质仍然可用。

### 显存行为

- **去噪期间 VAE 并不驻留**。它在解码时才加载，所以采样峰值（约 6.9 GB）
  低于解码峰值（约 7.5 GB）。
- `mixed-vram` 路线下**文本编码器留在内存**，不占显存。
- **前缀/KV cache 的位置对画质和耗时都没有可测影响**。
  注意：只要挂了 LoRA patch，ComfyUI 会自动关闭前缀缓存。

### GGUF 每步更慢，而且画质更差

在同一份 ComfyUI 上对比两种量化：**W4 为 1.20 s/it，GGUF 为 3.53 s/it**，慢约 2.9 倍。
这是架构差异而非版本问题：原生 W4 路径直接派发到融合的 int8 kernel，
而 GGUF 路径要先反量化再调用 `torch.nn.functional.linear`。

它牺牲的不只是速度。unsloth 公布的 Turbo Q4_K_M LPIPS 为 **0.153**，INT8 为 **0.037**——
大约差 **4 倍**（AlexNet，8 步）。Q5_K_M（0.074）与 Q6_K（0.055）能追回一部分，但仍落后；
只有 Q8_0（0.036）追平 INT8，而此时文件已经不再更小。GGUF 的显存占用也与 W4 路线相当，
既不省显存也不省磁盘。

把 `gguf` 当作对照路线、以及 4–6 GB 显卡的兜底即可——6–8 GB 机器上，
`mixed-vram` / `turbo` 严格更好。细节见 [`gguf/README.md`](gguf/README.md)。

---

## 透明背景

Qwen-Image-2.1 能输出真正的 RGBA 图像，既不需要改图，也不需要后处理——
靠模型卡里规定的措辞即可：

> `This is an RGBA image with transparency. {subject}. The image has alpha channel and the background is transparent.`

加上这句前缀，"去掉背景"类提示词会产出真正的透明通道（实测某测试主体有 21% 像素完全透明）；
不加则完全没有透明。WebUI 和 HTTP API 都提供了 `transparent` 开关。

---

## AI Agent

启动器第 4 步内置 Agent 面板，把模糊意图变成精确任务。它通过 OpenAI 兼容接口对接
**DeepSeek** 或 **智谱 GLM**。

- **模型列表联网实时拉取**（`GET /models`），不写死任何模型名。
- **读图能力靠实测，不靠猜。** 发一张 1×1 图片、`max_tokens=1`，
  GLM 对纯文本模型会明确回 `messages.content.type 参数非法，取值范围 ['text']`。
  靠名字猜在两个方向上都会错：`deepseek-flash` 和 `glm-5.3-flash` 名字里没有 `v` 却能读图；
  `glm-4.6v` 和 `glm-5v-turbo` 能读图却根本不在 `GET /models` 里。
- 模型分三档展示：**实测可读图**、**未确认但可选**、**已排除并说明理由**
  （OCR 等固定格式接口、向量化、语音、图像生成）。**只有拿到确凿反证才会排除**；
  无法确认从不被当作排除依据。
- Agent 只返回一个 JSON：模式（`text2image` / `edit` / `reference`）、改写后的提示词、
  宽高、是否透明、latent 模式、张数、以及哪张附图是被编辑对象。
  返回结果会先校验并夹紧再使用：尺寸对齐 32 的倍数、像素总量封顶、张数限幅、
  图片索引越界则回退到第一张。

API Key 用 **Windows DPAPI** 加密存储（绑定当前机器的当前用户），
另存一份 SHA-256 指纹用于标识。密钥本身绝不明文落盘；若系统不支持 DPAPI，则干脆不保存。

---

## HTTP API

每套策略在 Gradio WebUI 之外都提供一套 FastAPI：

```
GET  /api/health                 存活 + 队列深度
GET  /api/capabilities           本策略加载了什么、放在哪
POST /api/generate               提交任务，返回 {"job_id": ...}
GET  /api/jobs/{job_id}          轮询状态、进度与结果元数据
GET  /api/jobs/{job_id}/image    取回渲染好的 PNG
POST /api/free                   卸载模型并释放显存
POST /api/shutdown               请求后端退出
```

交互式文档在 `http://127.0.0.1:7860/docs`。
启动器驱动的正是这套 API，所以打包后的 EXE 不需要内置 torch。

---

## 目录结构

```
launcher/          PyQt5 前端：检测、安装、运行、AI Agent、画廊
  app.py           窗口、五个页面、控制台
  core.py          发现、进程控制、密钥、HTTP 出图
  agent.py         提供方 SDK、能力探测、计划校验
  agent_panel.py   Agent 界面与结果画廊
  tests/           离线单元测试（Agent 层，104 项，不联网、不需要 Key）
shared/            所有策略共用的库
  QwenEnv.ps1      路径发现、环境缓存、镜像配置
  qwen_comfy.py    图构建、官方采样参数、Turbo sigma 曲线
  qwen_comfy_app.py  ComfyUI 运行时
  qwen_api.py      FastAPI 接口
  qwen_paths.py    可移植的模型发现
  qwen_sizing.py   分辨率与像素预算规则
setup/             安装：环境、ComfyUI、模型、交互式向导
mixed/ mixed-vram/ turbo/ gguf/ native/    各一套运行策略
tests/             验证脚本（见 tests/README.md）
```

---

## 常见问题

**SmartScreen 拦截了 EXE。** 它没有代码签名。*更多信息 → 仍要运行*。

**双击没反应。** 从终端运行（`cmd` 后输入 `QwenImageLauncher.exe`），这样才能看到报错。

**下载中途断了。** 重新执行同一个安装步骤即可。安装脚本会**按已知体积校验每个文件**，
所以被截断的文件会被识别出来并重新下载，而不会被误认为已完成。

**显存不够 / 太慢。** 用第 3 步里带 ★ 的路线，分辨率控制在 1024×1024 附近。
参考图开销很大：高分辨率多图任务会显著拉长耗时。

**移动文件夹后全部显示"缺失"。** 在第 2 步指向新位置，
或删掉 EXE 旁边的 `launcher_state.json` 重新开始。

---

## 致谢

- [Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) —— 模型，来自 Qwen 团队
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) —— 推理引擎
- Qwen-Image-2.1-Turbo 蒸馏来自 [Unsloth](https://huggingface.co/unsloth) 与
  [Abiray](https://huggingface.co/Abiray)
- 模型权重在安装时从 [ModelScope](https://www.modelscope.cn) 下载，**本仓库不重新分发**

## 许可

[MIT](LICENSE)。模型权重有其自己的许可——商用前请先阅读 Qwen-Image-2.1 的模型卡。
