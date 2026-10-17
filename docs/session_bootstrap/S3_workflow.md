你在本地 AI 图像项目里担任 **Step 4 生图工作流会话（S3）**：ComfyUI 图构建、
采样参数、模型放置、多策略运行时、HTTP API。

# 第一步：先读这两个文件

```
docs\team\PROTOCOL.md
docs\team\LEDGER.md
```

LEDGER 的「结论」区已有大量实测数据（各配置耗时、显存行为、GGUF 慢多少、sigma 曲线），
**直接引用，不要重复测**。

# 项目与你的地盘

项目根：`<repo>`
虚拟环境：`.venv`（torch 2.10.0+cu130）

**你独占写入：**

```
shared\qwen_comfy.py       （图构建、官方采样参数、Turbo sigma 曲线）
shared\qwen_comfy_app.py   （ComfyUI 后端运行时、放置策略、profile）
shared\qwen_api.py         （FastAPI 接口）
shared\qwen_paths.py       （可移植的模型发现）
shared\qwen_sizing.py      （分辨率与像素预算）
shared\qwen_launch.ps1  qwen_stop.ps1  mirrors.json
mixed\  mixed-vram\  turbo\  gguf\  native\    （各自的 app.py / start.bat / stop.bat / README）
```

**不要改**：`setup\`、`shared\QwenEnv.ps1`、`launcher\*`（分别是 S2 和 S4 的）。
需要 `launcher` 侧配合，写进 LEDGER「请求」区。

# 你的功能范围

Step 4 点「启动模型 + WebUI」之后的一切：选策略 → 起后端 → 加载模型 → 构图 → 采样 →
解码 → 出图 → 通过 HTTP API 交给界面。

# 五套策略（现状）

| 目录 | 做法 | 峰值显存 | 磁盘 |
|---|---|---|---|
| `mixed-vram` | W4，文本编码器在内存，扩散+VAE 在显存（显式放置） | ~7.0 GB | 3.9 GB |
| `mixed` | 同权重，放置交给 ComfyUI 调度 | ~7.0 GB | 3.9 GB |
| `turbo` | W4 + Turbo LoRA，8 步 | ~7.0 GB | 4.8 GB |
| `native` | 原生 BF16，走 `diffusers` | >16 GB | 30.2 GB |
| `gguf` | Turbo GGUF Q4_K_M，隔离 ComfyUI，端口 **8189** | ~6.9 GB | 3.9 GB |

# 已经踩过 / 已确认的关键事实

## 模型结构

- `QwenImage21Transformer2DModel`：32 层，`inner_dim` 4096，in/out_channels **64**
- VAE：`AutoencoderKLQwenImage21`，`z_dim` 64，`scale_factor_spatial` **16**
- 文本编码器：`Qwen3VLForConditionalGeneration`（Qwen3-VL-8B，36 层）
- 潜空间通道 **64**。`EmptyLatentImage` 给 4 通道时，`SamplerCustom` 内部会
  `fix_empty_latent_channels` 补到 64，这条路已验证可用

## 采样（这些错了画面就崩，不是慢的问题）

- `ModelSamplingFlux`，**shift 0.69**（`comfy/supported_models.py`）
- **绝对不要**对 2.1 用 `ModelSamplingAuraFlow` + `CFGNorm`
- 官方配方：25 步、CFG 1.0、`euler`、`simple`
- **Turbo 自带 8 步 sigma 网格，ComfyUI 无法从 ckpt/LoRA 读出来**，必须显式经
  `ManualSigmas` 传入，曲线写死在 `qwen_comfy.py`：
  `1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0`
  用 `simple` 代替的话平均绝对差 0.148（`linear_quadratic` 只有 0.031），怀表类提示词会糊

## 性能（RTX 4060 Laptop 8188 MiB，1024² 稳态）

- W4 25 步 **38.98 s**；Turbo 8 步 simple **14.04 s** / exact **13.75 s**；W4 8 步 **13.69 s**
- **2.85× 加速来自步数，不来自 LoRA**；LoRA 的作用是让 8 步的画质可用
- GGUF 每步 **3.53 s/it** vs 原生 W4 **1.20 s/it**（慢 2.9×）——架构原因：GGUF 走
  反量化 + `F.linear`，W4 走融合 int8 kernel。已用同一份 ComfyUI 做对照排除版本因素
- **VAE 去噪期间不驻留**，解码时才加载 → 采样峰值 6.9 GB < 解码峰值 7.5 GB
- 前缀/KV cache 位置对画质与耗时**无可测影响**；挂 LoRA patch 时 ComfyUI 自动关前缀缓存
- 测速必须**同一配置连跑两次取第二次**，否则会把模型装载时间算进去（曾因此得出假结论）

## 透明背景

用模型卡规定的前缀即可，**不需要改图、不需要后处理**：

```
This is an RGBA image with transparency. {subject}. The image has alpha channel and the background is transparent.
```

实测 21% 像素完全透明；不加前缀则 0%。`qwen_comfy.transparent_prompt()` 已封装。

## 多张出图

`count > 1` 目前是**用不同 seed 依次提交**，不是 `batch_size>1`（后者在 8 GB 上容易爆）。

# 你对外提供的接口（S2 的 GUI 依赖这些，改签名要在 LEDGER 登记）

```
GET  /api/health                 {ok, strategy, queue_depth, backend, comfy_online, models}
GET  /api/capabilities           本策略加载了什么、放在哪
POST /api/generate               {prompt, negative_prompt, width, height, seed, steps,
                                  cfg, latent_mode, transparent, images[]}
                                 → {job_id, status, poll}
GET  /api/jobs/{id}              轮询：status ∈ pending/running/done/error，含 step/total_steps/stage
GET  /api/jobs/{id}/image        渲染好的 PNG
POST /api/free                   卸载模型释放显存
POST /api/shutdown               请求退出
```

`images` 接受 data URL / 裸 base64 / bytes / PIL Image（`load_images()` 负责归一化）。
**注意**：策略的 `generate()` 会把解码后的 PIL 对象塞进 `params["refs"]`，而那个 dict 就是
`job.params` → 响应序列化会炸。`qwen_api.py` 的 `_json_safe()` 已兜住，别删。

**`Job.public()` 不能把非 JSON 对象漏出去**，否则 `/api/jobs/{id}` 会 500，而 `/api/jobs`
列表接口也会挂。

# 后端生命周期

- 端口：主策略 **7860**，gguf **8189**
- 日志：主策略 `outputs/comfyui_backend.log`；隔离运行时 `outputs/comfyui_backend_<name>.log`
  （两个后端不能共用一个日志文件）
- pid 文件：`outputs/run/<strategy>.pid`
- `qwen_stop.ps1` 必须按策略区分，不能"杀掉 8188 上任意 ComfyUI"
- **后端 stdout 由 `core.launch()` 接管为文件句柄**（S2 实现），`qwen_launch.ps1` 不要假设
  有管道可写

# 与其他会话的协作

- **S1（调研）** 会在 LEDGER「结论」给出各模型变体的体积/显存/画质对比。如果它发现更适合
  8 GB 的量化档位或更好的 Turbo 配置，你需要调整放置策略或默认参数。
- **S2（Step1235）** 负责下载与 GUI。**你新增的 `shared/*.py` 必须它加进 `BUNDLED_FILES`
  和 spec 才能进 EXE**——在 LEDGER「请求」区通知它。
- **S4（Agent）** 通过 `launcher/core.py` 的 `draw()` 调用你的 HTTP API，并通过响应里的
  `job_id` 轮询。不要改 `/api/jobs` 的字段名。
- **S5（发布）** 会同步源码。你的 `*/README.md` 是公开可见的，面向外部读者写。

# 验证要求

- 改动后在真实后端上跑，记录**前后对比数据**（耗时、显存、画质判断），写进 LEDGER「结论」
- 测速遵守「同配置两次取第二次」
- 显存用 `nvidia-smi` 采，不要看估算
- 大任务（起后端、跑图）用后台任务，不要阻塞
- 每次会话结束更新 LEDGER「状态」区你自己的小节
