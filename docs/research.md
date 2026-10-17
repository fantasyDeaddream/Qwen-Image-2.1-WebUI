# Qwen-Image-2.1 模型变体调研与硬件适配评估

> 会话：S1（调研）｜采集时间：**2026-10-17 11:30–11:55（本机时间）**
> 权威来源：ModelScope 文件 API、HuggingFace 文件树 API、本机已落盘文件字节数、第三方模型卡；
> 项目基线数据（LEDGER「结论」区、根 `README.md`、`turbo/README.md`、`gguf/README.md`）**不重复验证**，直接引用。
> 体积单位统一为 **GiB**（1 GiB = 2³⁰ B），字节数保留原始值以便核对。
> **显存峰值统一用 MiB**；显存容量按 1 GB = 1024 MiB 计（8 GB 卡 = 8188/8192 MiB，12 GB = 12288，16 GB = 16384，24 GB = 24576，32 GB = 32768）。
> 带 `~` 的是估算值，带 `?` 的是未验证值，见 §6。

---

## 0. 结论摘要（先给答案）

1. **8 GB 显存的现有默认（`mixed-vram` = W4 mixed_balanced 3.91 GiB + BF16 VAE，编码器留在内存）依然是最优解**，没有找到"更小且画质更好"的替代。实测 1024² 峰值 7294 MiB、2048² 峰值 7563 MiB，都在 8188 MiB 以内（基线实测）。
2. **本次最大的可执行发现：存在 W4A8 文本编码器 `qwen3vl_8b_w4a8.safetensors`（5.879 GiB）**，比正在用的 int8 编码器（8.709 GiB）**小 2.83 GiB**。这是 16 GB 内存笔记本、以及 12–16 GB 显存机器"把编码器塞进显存"的关键文件，ModelScope 可直接下载。代价：编码器精度损失（公开数据里没有它的 LPIPS，标 `?`）。
3. **GGUF 路线应降级为"对照/极端小显存备用"**，不是 6–8 GB 的推荐：同体积下慢 **2.9×**（本机实测 3.53 s/it vs W4 1.20 s/it），而且画质有公开证据明显更差——unsloth 对 Turbo GGUF 的 LPIPS：Q4_K_M **0.153**、Q6_K 0.055、Q8_0 0.036；同尺寸的 INT8 只有 0.037。**"更小"不等于"够用"**。
4. **8 GB 以上是当前项目的真实缺口**：12/16/24 GB 显存现在仍按"编码器在 CPU"的 8 GB 方式跑。分档见 §3：12 GB 可上 INT8 DiT；16 GB 可让 W4A8 编码器与 DiT **同时常驻**；24 GB 可跑 **BF16 DiT + W4A8 编码器**（不损失 DiT 精度）。
5. **4–6 GB 显存只能算"极限可用"**：受限于工作区（1024² 需 ~2.3–2.7 GiB）+ VAE，任何组合在 1024² 都会溢出；需要用 CPU VAE + 降分辨率，或接受共享显存换速度。4 GB 实际只有 Q2_K/Q3_K 档 + ≤768² 勉强成立（估算，未实测）。
6. **官方/社区变体的完整枚举**（体积全部实测/API 核对）见 §1；其中真正可被本项目 ComfyUI 0.37.0 加载的格式已核实：`int8_tensorwise(+convrot)`、`asym_w4a8_int8`、`convrot_w4a4`、`float8_e4m3fn/e5m2`、`mxfp8`、`nvfp4`（后两者需相应硬件，4060 Ada **不可用** NVFP4）。
7. **模型来源需注意**：本机在用的 W4 `mixed_balanced` 与 NidAll 的 Turbo INT8、toxicdog 的 INT4 **只有 HuggingFace 有，ModelScope 无镜像**；本次实测 HF 现在**可达**（HTTP 206 续传，0.37–0.58 MB/s），但基线曾记录不可达——S2 需要把它登记为"备用源"。

---

## 1. 组件级变体清单

### 1.1 扩散 Transformer（DiT，单文件 / 分片）

| # | 变体 | 来源仓库（MS = ModelScope 有镜像） | 体积 | 分片 | 显存需求 | 相对速度 | 画质证据 | 适用 |
|---|---|---|---|---|---|---|---|---|
| D1 | **BF16（ComfyUI 单文件）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 14,230,280,616 B / **13.253 GiB** | 1 | 13.25 + VAE + 工作区 | 基准（原生） | 质量基准（无损） | ≥24 GB；质量上限 |
| D2 | **BF16（官方 diffusers）** | `Qwen/Qwen-Image-2.1` ※MS | 9,968,332,504 + 4,261,951,904 = 14,230,284,408 B / **13.253 GiB** | 2 | 同上 | 同上 | 同上 | `native` 策略 |
| D3 | **Turbo BF16** | `Comfy-Org/Qwen-Image-2.1`、`Qwen/Qwen-Image-2.1-Turbo` ※MS | **13.253 GiB**（与 D2 逐字节同尺寸） | 1（Comfy）/ 2（diffusers） | 同上 | 8 步而非 25/40 步 | 无损（官方蒸馏） | ≥24 GB 的 8 步画质上限 |
| D4 | **INT8 ConvRot（官方 ComfyUI 重打包）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 7,256,783,064 B / **6.758 GiB** | 1 | 6.76 + VAE + 工作区 = **10,206 MiB**（编码器在 CPU）| ~1.08× 慢于 W4A8（按 3090 数据换算，见 §5.3） | **LPIPS 0.047（项目 README 声称，无法溯源 `?`）**；unsloth 自家 INT8-ConvRot 测 0.064 | ≥12 GB；质量优先 |
| D5 | **Turbo INT8 ConvRot（官方重打包）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 7,256,787,829 B / **6.758 GiB** | 1 | 同 D4 | 8 步 | 未单独公布 `?` | ≥12 GB 的 8 步方案 |
| D6 | **W4 mixed balanced（当前在用）** | `NidAll/Qwen-Image-2.1-Mixed-Balanced`（**仅 HF**） | 4,203,397,480 B / **3.915 GiB** | 1 | 3.92 + VAE + 工作区 = 实测 7294 MiB(1024²) / 7563 MiB(2048²) | **1.00×（本机实测 1.48 s/it）** | 无 LPIPS；作者给转换误差 mean 0.0731、65 项校验通过；定性称"接近 INT8" | **8 GB 最优（当前）** |
| D7 | **W4A8 混合（jameriles）** | `jameriles/Qwen-Image-2.1-Hybrid-W4A8-INT8-ConvRot`（仅 HF） | 5,142,880,624 B / **4.790 GiB** | 1 | ~4.79 + VAE + 工作区 | ~W4A8 级 `~` | 无 LPIPS；作者只做 1 次 2×12 GB 卡的编辑验证 | 8–12 GB（需实测） |
| D8 | **INT8 混合（JoaoZaokk p010，2.1 专用）** | `JoaoZaokk/ConvRot-Mixed-INT8-for-Qwen-Image-2.1`（仅 HF） | 6,552,125,872 B / **6.102 GiB** | 1 | ~6.1 + VAE + 工作区 | 3090@1024²/25 步：11.79 s（全 INT8 12.70 s） | **24 场景盲评 0/24 差**；p008 0.0、p012/p015 6/24 差、纯 W4A4 18/24 差 | 10–12 GB 质量/速度折中（需实测） |
| D9 | **Turbo INT8 ConvRot（社区，NidAll）** | `NidAll/Turbo-INT8-ConvRot-for-Qwen-Image-2.1`（仅 HF） | 7,256,828,688 B / **6.758 GiB** | 1 | 同 D5 | 8 步 | 无受控对比 `?` | 与 D5 重复，无需下载 |
| D10 | **W4A4 INT4 ConvRot（base）** | `toxicdog/Qwen-Image-2.1-INT4ConvRot-ComfyUI`（仅 HF） | 3,666,512,184 B / **3.415 GiB** | 1 | ~3.42 + VAE + 工作区 | 3090 参考：8.41 s / 25 步（比 INT8 快 1.5×） | **盲评证据差**（同格式 W4A4 在 24 场景里 18 个"差"） | 仅 4–6 GB 极限档、且需实测 |
| D11 | **Turbo W4A4 INT4 ConvRot** | `toxicdog/Qwen-Image-2.1-Turbo-INT4-ConvRot`（仅 HF） | 3,666,512,176 B / **3.415 GiB** | 1 | 同 D10 | 8 步 × 更快的 W4A4 内核 | 作者自述"测试性转换，请自查画质" `?` | 4–6 GB 实验档 |
| D12 | **FP8 E4M3（unsloth，diffusers 路线）** | `unsloth/Qwen-Image-2.1-FP8` ※MS | 7,122,877,560 B / **6.634 GiB** | 1 | ~6.63 + VAE + 工作区 | 无 ComfyUI 实测 `?` | **LPIPS 0.112**（unsloth 同 seed 对 BF16） | diffusers/native 路线；ComfyUI 兼容性未验证 |
| D13 | **Turbo FP8 E4M3（unsloth）** | `unsloth/Qwen-Image-2.1-Turbo-FP8` ※MS | 7,122,899,992 B / **6.634 GiB** | 1 | 同上 | 8 步 | **LPIPS 0.125** | 同上 |
| D14 | **Calibrated W8A8 FP8（ProCreations）** | `ProCreations/Image-2.1-Calibrated-FP8` ※MS | 3 分片 3,494,954,232+3,463,633,328+302,911,200 = 7,261,498,760 B / **6.763 GiB** | **3** | 6.76 + BF16 attention/encoder/VAE | 对比 BF16：**1.40×（1024²）/1.25×（2048²）**（RTX PRO 6000 实测） | **LPIPS 0.0345、SSIM 0.9713**（对 BF16，16 提示 + 2 编辑） | **仅自定义 diffusers 运行时**；声明非 ComfyUI/通用格式 |
| D15 | **NVFP4（BennyDaBall）** | `BennyDaBall/Qwen-Image-2.1-NVFP4`（仅 HF） | 4,197,574,664 B / **3.909 GiB** | 1 | 3.91 + 编码器 7.03 + VAE = 实测 **13,249 MiB** 峰值（5090） | 5090@1024²/40 步 **6.746 s**（INT8 7.553、BF16 15.629） | 无 LPIPS；24 组同 seed 对比 + 视觉评审 | **仅 Blackwell（sm120）**；4060 Ada 会禁用 |
| D16 | **Turbo NVFP4** | `BennyDaBall/Qwen-Image-2.1-Turbo-NVFP4`（仅 HF） | 5,692,827,176 B / **5.302 GiB** | 1 | 5.30 + 编码器 8.693 + VAE 0.629 | 8 步 | 无 LPIPS `?` | 仅 Blackwell；比 base NVFP4 更大，无优势 |

> 另见 §1.6 的 Viggle 6 步 Turbo（自带 fp8/int8/GGUF 各档，社区蒸馏，非官方）。

### 1.2 文本编码器（Qwen3-VL 8B；决定内存与"能否上显存"）

| # | 变体 | 来源 | 体积 | 分片 | 处理位置建议 | 画质证据 | 备注 |
|---|---|---|---|---|---|---|---|
| E1 | **BF16（ComfyUI 单文件）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 17,534,334,616 B / **16.330 GiB** | 1 | 只能内存（CPU） | 基准 | ComfyUI 路线一般不用 |
| E2 | **BF16（官方 diffusers）** | `Qwen/Qwen-Image-2.1` ※MS | 4 shards = 17,534,339,488 B / **16.330 GiB** | **4** | `native` 放内存 | 基准 | README 里"16.33 GiB 编码器"即此 |
| E3 | **Turbo BF16** | `Qwen/Qwen-Image-2.1-Turbo` ※MS | 17,534,340,584 B / **16.330 GiB** | 1 | 内存 | 基准 | 与 E1 等价 |
| E4 | **INT8 ConvRot（当前在用）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 9,350,798,360 B / **8.709 GiB** | 1 | 内存/CPU（可拔到显存，见 §3） | 无独立 LPIPS `?` | `text_encoders/qwen3vl_8b_int8_convrot.safetensors` |
| E5 | **W4A8（★新发现）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 6,312,105,364 B / **5.879 GiB** | 1 | 内存；**12–16 GB 显存可常驻 GPU** | 无 LPIPS `?` | 省 **2.83 GiB**；NidAll 官方推荐搭配；CPU 加载需实测 |
| E6 | **INT8 tensorwise（unsloth）** | `unsloth/Qwen-Image-2.1-FP8` ※MS | 9,349,769,248 B / **8.708 GiB** | 1 | 内存 | 与 E4 同级 `?` | diffusers 路线 |
| E7 | **FP8 E4M3（unsloth）** | `unsloth/Qwen-Image-2.1-FP8` ※MS | 9,394,530,592 B / **8.749 GiB** | 1 | 内存 | 无独立 LPIPS `?`（整模型 FP8 0.112） | **不省显存**，无优势 |
| E8 | **INT4 ConvRot（toxicdog）** | `toxicdog/Qwen-Image-2.1-INT4ConvRot-ComfyUI`（仅 HF） | 6,880,836,952 B / **6.408 GiB** | 1 | 内存 | 无 `?` | 比 W4A8 更大更差，无理由用 |
| E9 | **Hybrid W4A8+INT8（jameriles）** | `jameriles/Qwen-Image-2.1-Hybrid-W4A8-INT8-ConvRot`（仅 HF） | 6,741,467,072 B / **6.279 GiB** | 1 | 内存 | 无 `?` | 与 D7 配套 |
| E10 | **NVFP4（BennyDaBall）** | `BennyDaBall/Qwen-Image-2.1-NVFP4`（仅 HF） | 7,549,882,768 B / **7.031 GiB**（Turbo 版 9,334,536,360 B / **8.693 GiB**） | 1 | 显存（Blackwell） | 无 `?` | 仅 sm120；Turbo 版编码器更大 |
| E11 | Prompt Enhancer INT8（T2I/I2I） | `Comfy-Org/Qwen-Image-2.1` ※MS | 各 9,471,072,252 B / **8.821 GiB** | 1（每个模型） | 内存/按需 | 官方配套 | **可选增强件**，不是生成必需 |
| E12 | Prompt Enhancer W4A8（T2I/I2I） | `Comfy-Org/Qwen-Image-2.1` ※MS | 各 6,724,090,764 B / **6.262 GiB** | 1（每个模型） | 内存/按需 | 官方配套 | 同上，体积减半 |
| E13 | Prompt Enhancer BF16 源 | `Qwen/Qwen-Image-2.1-PE-T2I`、`-PE-I2I` ※MS | 各 17.55 GiB（4 shards） | 4 | 内存 | 官方 | 只在 diffusers 路线用 |

> **编码器才是真正的显存/内存大头**：它比 DiT 大 2–4 倍。8 GB 卡上它必须离开显存（`mixed-vram` 的核心设计），这也是为什么 E5（省 2.83 GiB）比换 DiT 更关键。

### 1.3 VAE

| # | 变体 | 来源 | 体积 | 说明 |
|---|---|---|---|---|
| V1 | **BF16 ComfyUI 版（在用）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 675,509,688 B / **0.629 GiB** | `vae/qwen_image_2.1_vae_bf16.safetensors`；实测放显存比放内存快 2.5×（基线） |
| V2 | **官方 diffusers base VAE 是 fp32** | `Qwen/Qwen-Image-2.1` ※MS | 1,350,989,512 B / **1.258 GiB** | 注意：README 写的 0.63 GiB 是 ComfyUI 转 bf16 后的版本；`native` 路线磁盘要多算 0.63 GiB |
| V3 | Turbo VAE（bf16） | `Qwen/Qwen-Image-2.1-Turbo` ※MS | 675,508,656 B / **0.629 GiB** | 与 V1 等价 |

### 1.4 加速件：Turbo LoRA / 步数蒸馏

| # | 变体 | 来源 | 体积 | 步数 | 速度 | 画质证据 | 结论 |
|---|---|---|---|---|---|---|---|
| L1 | **官方 Turbo LoRA（在用）** | `Comfy-Org/Qwen-Image-2.1` ※MS | 913,314,512 B / **0.851 GiB** | 8（CFG 1） | 本机实测 13.75–14.04 s @1024²（比 25 步 2.83×） | 灰度验证：难提示词下 8 步≈25 步；n=3 定性 | **保留，已验证可叠在 W4 上** |
| L2 | Lightning 4 步 LoRA | `lightx2v/Qwen-Image-2.1-Lightning` ※MS | 67,144,280 B / **0.063 GiB** | 4 | 理论再快 ~1.8×（去噪 4/8 步）`~` | 仅官方展示图，无 LPIPS `?` | 潜在实验；调度与 ComfyUI 兼容性未验证 |
| L3 | Turbo8 8 步 LoRA | `chriswritescode/Turbo8-LoRA-Qwen-Image-2.1`（仅 HF） | 1,359,148,744 B / **1.266 GiB** | 8 | 与 L1 同级 | 192 held-out：PickScore 21.94 vs 教师 22.15；CLIPScore 26.89=26.89；**文字精确率 75% vs 95%** | 有评测但文字退步、比 L1 大 0.4 GiB；不换 |
| L4 | Pruna 5/8 步适配器 | `PrunaAI/Pruna-Qwen-Image-2.1`（仅 HF） | 各 335,606,144 B / **0.313 GiB** | 5 / 8 | 5 步更快、8 步质量优先 | 官方称 5 步"明显画质下降" | diffusers 路线；不适用 ComfyUI |

### 1.5 GGUF 档位（base 与 Turbo）

**权威体积**：`Abiray/Qwen-Image-2.1-GGUF` / `Abiray/Qwen-Image-2.1-Turbo-GGUF`（※MS 两者都有，S2 可直接下）；
**画质权威**：`unsloth/Qwen-Image-2.1-Turbo-GGUF` 公布的 LPIPS（AlexNet，同 seed，8 步 CFG 1；注意其 Dynamic 2.0 配方体积与 Abiray 略有差异）。

| 档位 | Abiray base（GiB） | Abiray Turbo（GiB） | unsloth Turbo（GiB） | **unsloth Turbo LPIPS↓** | 说明 |
|---|---|---|---|---|---|
| Q2_K | — | 2.278 | 2.297 | **0.374** | 4060 以下极低端；画质漂移明显 |
| Q3_K_S | — | 2.897 | 2.538 | 0.303 | |
| Q3_K_M | 2.967 | 2.967 | 2.951 | 0.261 | ≤4–6 GB 才考虑 |
| Q3_K_XL | — | — | 3.364 | **0.237** | unsloth 建议：必须低于 4 GB 时选它而非 Q3_K_M/S |
| Q4_K_S | 3.781 | 3.781 | 3.638 | 0.201 | |
| Q4_K_M | 3.902 | 3.902 | 3.911 | **0.153** | **6–8 GB 的"标准答案"，但画质明显不如 INT8** |
| Q5_K_S | — | 4.597 | 4.193 | 0.103 | 8 GB 可塞下（含显存 VAE：4294+644+2300=7238 MiB） |
| Q5_K_M | 4.664 | 4.664 | 5.020 | 0.074 | 8–10 GB |
| Q6_K | 5.473 | 5.473 | 5.841 | 0.055 | 10–12 GB；接近 INT8 画质 |
| Q6_K_XL | — | — | 6.257 | 0.050 | unsloth 独有 |
| Q8_0 | 7.070 | 7.070 | 7.116 | **0.036** | 12 GB+；几乎无损 |
| F16 | — | — | 13.253 | 0.012 | 仅作基准 |
| 参考 | 另：`leejet/Qwen-Image-2.1-GGUF`（仅 HF）Q2_K 2.386 / Q3_K 3.046 / Q4_K 3.909 / Q5_0 4.722 / Q6_K 5.585 / Q8_0 7.159 | | | | 命名非 K-quant 标准，不推荐 |

**速度**：本机实测 GGUF Q4_K_M 每步 **3.53 s/it**，W4 native 路 **1.20 s/it** → 慢 **2.9×**（架构原因：GGUF 每算子反量化，W4 走融合 int8 kernel）。**任何为"更快"选 GGUF 的动机都不成立。**

### 1.6 社区/平台限定变体（了解即可，不建议纳入策略）

| 变体 | 来源 | 体积 | 为什么不用 |
|---|---|---|---|
| Viggle Turbo（6 步，多精度） | `Viggle/Qwen-Image-2.1-viggle-turbo`（仅 HF） | fp8 7.251 GB / int8 7.256 GB / GGUF Q8 7.687 GB 等 34 个文件 | 第三方蒸馏，偏离官方调度；无画质数据 |
| Nunchaku W4A4（ModelsLab） | `ModelsLab/Qwen-Image-2.1-W4A4-int4`（仅 HF） | 4,655,346,432 B / 4.336 GiB | 需 nunchaku 后端，不是 ComfyUI 原生 |
| MLX / OpenVINO / SYCL / MNN 系 | `ddalcu/…MLX-Serve-4bit`、`HelloSun/…OpenVINO-INT4` 等 | — | 非 CUDA/Windows 路线，本项目不适用 |
| **Qwen-Image-2512 / 1.0 家族的量化** | `Comfy-Org/Qwen-Image_ComfyUI`（fp8/nvfp4 是 1.0 的）、`JoaoZaokk/Qwen-Image-2512-W4A8-ConvRot` | 38.05/19.03 GiB 等 | **不是 2.1**；base_model 写的是 `Qwen/Qwen-Image`，别混用 |

### 1.7 官方新增件（对后续功能有价值，非必需）

| 件 | 来源 | 体积 | 用途 |
|---|---|---|---|
| Fun-Union ControlNet | `Comfy-Org/Qwen-Image-2.1` ※MS / `alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union` | BF16 7,550,977,992 B / **7.032 GiB**；INT8 3,779,298,944 B / **3.520 GiB** | 结构/姿态控制（S4 可考虑做 Agent 能力） |
| Fun-Acc LoRAs | `Comfy-Org/Qwen-Image-2.1`、`alibaba-pai` | 未逐一核 `?` | 官方加速 LoRA 集 |
| Prompt Enhancer（E11–E13） | 见 §1.2 | 8.8 / 6.3 GiB per 模型 | 把短提示词扩写；多占 2 个模型的内存 |

---

## 2. 整套组合：磁盘 / 显存 / 内存

> 所有数字 = 上表字节数相加，**GiB**。显存为"1024² 单图峰值"（**MiB**，推算模型见 §3.1）；内存为"编码器在 CPU 时的 RAM 需求估算"。

| 组合 | DiT + 编码器 + VAE | 磁盘 | 显存峰值 1024² / 2048²（MiB） | RAM（编码器在 CPU） | 备注 |
|---|---|---|---|---|---|
| **当前 W4 kit** | 3.915 + 8.709 + 0.629 | **13.25 GiB**（+LoRA 0.851 = 14.10） | **7294 / 7563（实测）** | ~13–14 GiB（16 GB 紧、24 GB 舒适） | 8 GB 现役 |
| **W4 + W4A8 编码器** | 3.915 + 5.879 + 0.629 | **10.42 GiB**（+LoRA = 11.27） | 7294（编码器在 CPU 时不占显存；两者都在显存时 13,314） | ~10–11 GiB（16 GB 舒适） | ★推荐新增组合 |
| W4 + INT8 DiT kit | 6.758 + 8.709 + 0.629 | 16.10 GiB | 10,206 / 10,476（编码器在 CPU） | ~13–14 GiB | 12 GB 档质量优先 |
| INT8 DiT + W4A8 编码器 | 6.758 + 5.879 + 0.629 | 13.27 GiB | 10,206（编码器在 CPU）/ 16,226（两者常驻） | ~10–11 GiB | 12–16 GB |
| Turbo INT8 kit | 6.758 + 8.709 + 0.629 | 16.10 GiB | 10,206（8 步） | ~13–14 GiB | 12 GB 的 8 步 |
| Turbo GGUF Q4_K_M kit | 3.902 + 8.709 + 0.629 | 13.24 GiB | **6939（实测，8 步）** | ~13–14 GiB | 慢 2.9× |
| Turbo GGUF Q4_K_M + W4A8 编码器 | 3.902 + 5.879 + 0.629 | 10.41 GiB | 6939 | ~10–11 GiB | 4–6 GB 才看 |
| Q2_K kit（极限） | 2.278 + 8.709 + 0.629 | 11.62 GiB | ~5277 | ~13–14 GiB | 4 GB 降分辨率用 |
| W4A4 INT4 kit | 3.415 + 6.408 + 0.629 | 10.45 GiB | ~6782 | ~11–12 GiB | 画质风险 |
| W4A8 hybrid kit | 4.790 + 6.279 + 0.629 | 11.70 GiB | ~8190 | ~11–12 GiB | 需实测 |
| 纯 INT8 混合 p010 kit | 6.102 + 8.709 + 0.629 | 15.44 GiB | ~9534 | ~13–14 GiB | 需实测 |
| NVFP4 kit | 3.909 + 7.031 + 0.629 | 11.57 GiB | **13,249（5090 实测，含编码器）** | 编码器在 GPU | 仅 Blackwell |
| BF16 ComfyUI kit | 13.253 + 16.330 + 0.629 | **30.21 GiB** | ~33,578 | 编码器 16.33 GiB 在 CPU → RAM ≥ 24–32 GiB | 32 GB 显存边 |
| **官方 native 全套** | 13.253 + 16.330 + **1.258(fp32 VAE)** | **30.86 GiB**（29 文件） | DiT 以 FP8 存储 ≈ 6793 + 工作区 `?` | 编码器 16.33 GiB 在 RAM | `native` 策略 |
| 官方 Turbo 全套（diffusers） | 13.253 + 16.330 + 0.629 | 30.26 GiB（33 文件） | 同上 | 同上 | 8 步的 BF16 路线 |

---

## 3. 硬件分档建议（4 / 6 / 8 / 12 / 16 / 24 GB）

### 3.1 估算模型（从本机实测标定，不是拍脑袋）

本机（RTX 4060 Laptop 8188 MiB）实测给出：

```
显存峰值 ≈ DiT权重 + 编码器(若在显存) + VAE + 工作区
工作区(ComfyUI/W4 路径)：1024² ≈ 2641 MiB；2048² ≈ 2910 MiB
工作区(GGUF 路径)：1024² ≈ 2300 MiB
VAE(bf16, 显存) = 644 MiB；VAE(CPU) = 0（但解码慢 2.5×）
标定：4009(W4) + 644 + 2641 = 7294 MiB = 实测峰值 7294 MiB ✔
```

> 说明：工作区随 `宽×高`（token 数）增长；分辨率减半到 768²（0.59 MP）时工作区约降到 **1500 MiB** 量级 `~`。
> RAM 估算 = 编码器权重 + ComfyUI/Python ≈1.5–2 GiB + Windows ≈3 GiB；CPU VAE 再 +1–2 GiB 瞬时。

### 3.2 分档建议

| 显存 | 推荐组合 | 理由 | 牺牲 | 速度预估 |
|---|---|---|---|---|
| **4 GB** | **Turbo Q2_K GGUF（2.28 GiB）+ CPU VAE + ≤768²**；或 Q3_K_M ≤640² | 1024² 任何组合都装不下（Q2_K 2333+2300=4633 > 4096 MiB） | 画质差（LPIPS 0.374）、分辨率低、CPU VAE 解码慢 | GGUF 2.9× 慢 × 小图；~40–90 s/张 `~` |
| **6 GB** | **A：W4 mixed + Turbo LoRA + CPU VAE + ≤896²**（1024² 时 4009+2641=6650 MiB，溢出 ~0.5 GiB）；**B：Turbo Q4_K_S GGUF + CPU VAE @1024²**（3871+2300=**6171 MiB**，只差 27 MiB，可容忍；更稳是 Q3_K_M=5338 MiB） | A 画质最好、速度最快；B 能保住 1024² | A 需降分辨率或容忍共享显存；B 画质 LPIPS 0.201、慢 2.9×；两者 VAE 在 CPU 解码明显变慢 | A：8 步去噪 ≈12 s + CPU VAE 解码（`?`）；B：≈31 s/张 + CPU VAE |
| **8 GB（本机）** | **保持现状：W4 mixed_balanced + BF16 VAE + int8 编码器在 CPU；需要速度就加官方 Turbo LoRA（8 步）** | 实测 7294/7563 MiB 都在卡内；无更优替代 | 编码器 8.7 GiB 占内存；25 步慢（38.98 s） | 25 步 38.98 s / Turbo 8 步 13.75–14.04 s |
| **12 GB** | **首选：INT8 ConvRot DiT（6.758）+ W4A8 编码器（5.879，可放显存，ComfyUI 会自动换出）+ BF16 VAE** | 编码器在 CPU 时峰值 **10,206 MiB**（留 2.0 GiB 余量）；质量比 W4 更接近 BF16（LPIPS 0.037–0.064 vs W4 未测） | 比 W4 大 2.8 GiB 磁盘；速度与 W4 同级或慢 ~8% | 1024² 25 步 ≈ 42 s `~`；Turbo 8 步 ≈ 15 s `~` |
| **16 GB** | **W4 mixed + W4A8 编码器两者常驻显存**（4009+6020+644+2641=**13,314 MiB**；2048² 13,583 MiB）。若换 INT8 DiT 则 16,226 MiB——只剩 158 MiB，不建议 | 编码器上 GPU 后不再吃 CPU 内存、编码更快；2048² 也能跑 | 编码器量化损失（LPIPS 未知）；需要改代码让它可配置（见 LEDGER 请求） | 1024² 8 步 ≈ 13 s、25 步 ≈ 37 s `~` |
| **24 GB** | **两选一（按质量偏好实测）：A：BF16 DiT（13,571）+ W4A8 编码器 + VAE = 22,876 MiB，2048² 23,145 MiB；B：BF16 编码器（16,722）+ W4 DiT = 24,016 MiB（更紧）** | A 不损失 DiT 精度，是全 ComfyUI 路线画质上限；B 不损失编码器精度但 DiT 量化、且只剩 0.55 GiB 余量 | A 编码器仍有量化；B 只能 1024²、易溢出；两者 2048² 都紧 | A：BF16 DiT 比 W4 每步慢约 2–3×，但 24 GB 卡本身快；8 步 Turbo BF16 更实际 |
| **32 GB** | **BF16 编码器 + INT8 DiT**（16,722+6921+644+2641 = **26,928 MiB**，舒适，可 2048²）、或 `native` 策略 | 全 BF16 需要 33,578 MiB → 会溢出 ~810 MiB，不推荐当默认 | INT8 DiT 有量化损失 | 取决于卡 |
| **48 GB+** | 全 BF16（ComfyUI 30.21 GiB，峰值 33,578 MiB ≈ 32.8 GiB）+ 2048² + 多参考图；或 `native` diffusers | 画质上限、无溢出 | 磁盘 30 GiB、需要 32 GB+ RAM（native） | — |

> **本项目的分发命名与上表吻合**：`32G+` = 32 GB 显存、`32+8G` / `32+12G` = 32 GB 内存 + 8/12 GB 显存、`8-12G` = 8–12 GB 显存档。

---

## 4. 与现有五套策略的对照（复核）

| 策略 | 当前实现（基线） | 复核结论 | 建议 |
|---|---|---|---|
| `native`（diffusers BF16） | 编码器 CPU BF16 16.33 GiB；DiT FP8-E4M3 存储/BF16 计算进显存，尾部 N block 往返内存；VAE 内存 | **定位正确**：这是"质量上限 + 高内存"路线，8 GB 卡上能跑但代价大。磁盘 30.86 GiB（比 README 的 30.9 一致） | 保留；文档明确"需要 ≥24–32 GB RAM"，8 GB 显存只是能跑 |
| `mixed`（ComfyUI 自适应） | 与 mixed-vram 同文件，放置交给 ComfyUI | 在 8 GB 上会抖动（编码器被换出换入）；没有独有价值 | 保留作对照，不作为推荐默认 |
| `mixed-vram` | 编码器 CPU；W4 DiT + BF16 VAE 常驻显存；KV cache 在显存 | **8 GB 最优，维持**。实测 7294/7563 MiB 都在卡内 | 保留为 8 GB 默认 |
| `turbo` | W4 + 官方 Turbo LoRA，8 步 CFG 1，ManualSigmas 官方网格 | 有效：8 步 ≈ 25 步画质（n=3 定性），2.83× 加速，LoRA 零速度/显存代价 | 保留；建议在 UI 里标注"画质敏感场景仍用 25 步" |
| `gguf` | Turbo GGUF Q4_K_M 3.902 GiB，隔离 ComfyUI 8189 | **应降级**：同体积慢 2.9×，且 Q4_K_M LPIPS 0.153（unsloth）明显差于 INT8 0.037。它唯一的价值是"极端小显存 + 隔离环境" | 保留但不推荐日常；README 里"2.4×"的结论可以补一句"画质也差于 W4/INT8" |

### 漏掉的、值得新增的替代方案

1. **W4A8 文本编码器（E5，5.879 GiB）** —— 最小改动、最大收益：磁盘 −2.83 GiB、内存 −2.83 GiB、使 12–16 GB 显存放得下编码器。**建议 S3 把它做成可配置项并实测（含 CPU 加载是否正常）。**
2. **12–16 GB 显存的"编码器上 GPU"配置** —— 当前五套策略都默认编码器在 CPU，12 GB 以上的机器白扔显存。
3. **INT8 ConvRot DiT（D4）作为 ≥12 GB 的质量档** —— 与 W4 同源、有第三方 LPIPS（0.037–0.064），磁盘多 2.8 GiB。
4. **BF16 DiT + W4A8 编码器（24 GB）** —— 全 ComfyUI 路线里唯一能在 24 GB 内不损失 DiT 精度的组合。
5. **Lightning 4 步 LoRA（L2，仅 0.063 GiB）** —— 若实测可用，可在 8 步基础上再 ~1.8×；**成本极低（下载 67 MB）**，值得 S3 做一次对照实验。
6. **不建议引入**：INT4/W4A4（有盲评证据画质差）、NVFP4（需 Blackwell）、GGUF 大档位（慢 2.9×）、unsloth FP8（diffusers-only 且 LPIPS 更差）。

---

## 5. 数据来源与采集时间

### 5.1 API 来源（2026-10-17 11:30–11:55）

| 标记 | 来源 | 说明 |
|---|---|---|
| **[MS]** | `https://www.modelscope.cn/api/v1/models/<owner>/<repo>/repo/files?Revision=master&Recursive=true` | 体积、分片数权威来源；返回 `Data.Files[]`，已过滤 `Type=='tree'` |
| **[HF]** | `https://huggingface.co/api/models/<id>/tree/main?recursive=true` | 本次实测 HF **可达**（HTTP 200；range 下载 HTTP 206，0.37–0.58 MB/s）；基线曾说不可达，属环境变化 |
| **[L]** | 本机 `ComfyUI\models\...` 文件系统字节数（`Get-ChildItem`） | 已在盘资产的权威值 |
| **[B]** | LEDGER「基线」区、根 `README.md`、`turbo/README.md`、`gguf/README.md` | 项目既有实测，不重复验证 |
| **[X]** | 第三方模型卡/评测（unsloth、BennyDaBall、ProCreations、JoaoZaokk） | 画质与跨卡速度；均为第三方自述 |
| **[C]** | 官方模型卡（Qwen / Comfy-Org / Abiray） | 仅用于"是否存在、如何配套"，不作为性能依据 |

### 5.2 已核对的仓库清单

- ModelScope **有**：`Qwen/Qwen-Image-2.1`(29 文件/30.86 GiB)、`Qwen/Qwen-Image-2.1-Turbo`(33/30.26 GiB)、`Qwen/Qwen-Image-2.1-PE-T2I`、`-PE-I2I`、`Comfy-Org/Qwen-Image-2.1`(18 文件/113.14 GiB，含全部单文件变体)、`Abiray/Qwen-Image-2.1-GGUF`、`Abiray/Qwen-Image-2.1-Turbo-GGUF`、`unsloth/Qwen-Image-2.1-GGUF`、`unsloth/Qwen-Image-2.1-Turbo-GGUF`、`unsloth/Qwen-Image-2.1-FP8`、`unsloth/Qwen-Image-2.1-Turbo-FP8`、`lightx2v/Qwen-Image-2.1-Lightning`、`ProCreations/Image-2.1-Calibrated-FP8`。
- **仅 HuggingFace 有**（ModelScope 404 实测）：`NidAll/Qwen-Image-2.1-Mixed-Balanced`（本机在用 W4 的唯一来源）、`NidAll/Turbo-INT8-ConvRot-…`、`toxicdog/Qwen-Image-2.1-{INT4ConvRot-ComfyUI,Turbo-INT4-ConvRot}`、`jameriles/Qwen-Image-2.1-Hybrid-W4A8-INT8-ConvRot`、`JoaoZaokk/ConvRot-Mixed-INT8-for-Qwen-Image-2.1`、`chriswritescode/Turbo8-LoRA-…`、`BennyDaBall/Qwen-Image-2.1-NVFP4`、`Viggle/…`。
- 官方 native 全套：transformer 2 分片 + text_encoder 4 分片 + VAE 1 文件 = 29 个文件 / **30.86 GiB**（与基线 30.9 GiB 一致）。

### 5.3 速度数据来源明细

| 数据 | 硬件 | 条件 | 来源 |
|---|---|---|---|
| 25 步 38.98 s / 1.48 s/it；8 步 13.69 s；Turbo LoRA 8 步 13.75–14.04 s | RTX 4060 Laptop 8188 MiB | 1024²，热后端 | **[B]** 基线 + `turbo/README.md` |
| GGUF Q4_K_M 3.53 s/it，8 步 31.13–31.73 s | 同上 | 1024²，8 步 | **[B]** `gguf/README.md` |
| 全 INT8 12.70 s / p010 11.79 s / p008 12.96 s / W4A4 8.41 s | RTX 3090 280 W | 1024²，25 步，24 次中位数，SageAttention | **[X]** JoaoZaokk 卡 |
| INT8 ConvRot 7.553 s、BF16 15.629 s、NVFP4 6.746 s；峰值 18433 / 31677 / 13249 MiB | RTX 5090 32 GB | 1024²，40 步，全节点执行、3 次热跑中位数 | **[X]** BennyDaBall VALIDATION.md |
| FP8 7.01 s vs BF16 9.79 s（1024²，40 步）；FP8 44.17 vs 55.17 s（2048²） | RTX PRO 6000 96 GB | 40 步，CUDA 同步端到端 | **[X]** ProCreations 卡 |

### 5.4 关键原始链接（画质/速度数字的出处）

- unsloth Turbo GGUF LPIPS 全表：<https://huggingface.co/unsloth/Qwen-Image-2.1-Turbo-GGUF>
- unsloth Turbo FP8/INT8 LPIPS：<https://huggingface.co/unsloth/Qwen-Image-2.1-Turbo-FP8>
- unsloth Base FP8/INT8 LPIPS：<https://huggingface.co/unsloth/Qwen-Image-2.1-FP8>
- BennyDaBall NVFP4 实测（速度/峰值显存）：<https://huggingface.co/BennyDaBall/Qwen-Image-2.1-NVFP4/blob/main/VALIDATION.md>
- ProCreations Calibrated FP8（LPIPS/SSIM/速度）：<https://huggingface.co/ProCreations/Image-2.1-Calibrated-FP8>
- JoaoZaokk 2.1 混合 INT8 盲评与 3090 速度：<https://huggingface.co/JoaoZaokk/ConvRot-Mixed-INT8-for-Qwen-Image-2.1>
- NidAll W4 mixed_balanced（当前在用）：<https://huggingface.co/NidAll/Qwen-Image-2.1-Mixed-Balanced>
- Abiray GGUF（MS 可下的权威体积）：<https://huggingface.co/Abiray/Qwen-Image-2.1-GGUF> / <https://huggingface.co/Abiray/Qwen-Image-2.1-Turbo-GGUF>
- Comfy-Org 单文件变体集合：<https://huggingface.co/Comfy-Org/Qwen-Image-2.1>（ModelScope 镜像：<https://www.modelscope.cn/models/Comfy-Org/Qwen-Image-2.1>）
- 官方模型卡：<https://www.modelscope.cn/models/Qwen/Qwen-Image-2.1> / <https://www.modelscope.cn/models/Qwen/Qwen-Image-2.1-Turbo>

---

## 6. 未验证 / 存疑项（不要当结论引用）

1. **`README.md` §9 的 "Comfy-Org turbo int8_convrot LPIPS 0.047" 无法溯源**。公开可查的是 unsloth 对**自己**的 Turbo INT8-ConvRot（7.26 GB，group-256 Hadamard）测的 **0.037**、Base INT8 0.064；Comfy-Org 的 6.758 GiB 文件**没有任何公开 LPIPS**。两者不是同一文件，不能互换引用。
2. **W4 `mixed_balanced`（当前在用）没有任何公开 LPIPS/CLIP**。作者只给"mean error 0.0731、65 项校验通过"和定性声明。它的画质只能靠本机对比实验确认（建议 S3 用同 seed 与 INT8 对比）。
3. **W4A8 编码器（E5）在 CPU 上的行为未实测**。已知 int8_convrot 编码器在 CPU 可用（项目现役）；ComfyUI 的 `get_disabled_quant_formats` 只在 MPS/XPU/DirectML/IXUCA 上禁用 int8 系格式，**CPU 不在禁用列表**，所以 W4A8 大概率同样可用——但"内存是否按 5.88 GiB 保留、精度是否等同、编码耗时是否可接受"都必须实测（这是 S3 的请求项）。
4. **INT4/W4A4（D10/D11）在 Turbo 8 步下的画质未知**。作者自称"测试性转换"；同格式的 W4A4（非 Turbo）在 2.1 家族盲评里 24 场景 18 个被判"差"。默认不要用。
5. **unsloth 的 FP8/INT8 是 diffusers 格式**（`library_name: diffusers`，transformer-only 单文件），能否被 ComfyUI 原生加载未验证。ComfyUI 0.37.0 本身已支持 `float8_e4m3fn/e5m2/int8_tensorwise/convrot_w4a4/asym_w4a8_int8/mxfp8/nvfp4` 格式（已读 `comfy/quant_ops.py`），但**文件是否带 `comfy_quant` 元数据**是另一回事。
6. **GGUF 文本编码器路线未验证**：unsloth 推荐 `Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf`（5.15 GB，LPIPS 0.029/SSIM 0.959 vs Q4_K_M 5.03 GB），但本机 GGUF 实例（ComfyUI-GGUF）目前仍用 safetensors int8 编码器；qwen3vl 架构能否走 `CLIPLoaderGGUF` 未验证。
7. **4/6 GB 档位全部是估算**：依据 §3.1 公式 + 工作区线性外推，没有真机。Windows 共享显存会让"装不下"变成"能跑但慢"。
8. **跨卡速度换算**：本文"~"值由 3090/5090 数据按已知比例外推到 4060，只用于排序，不要当绝对耗时。
9. **NVFP4/mxfp8 需要相应硬件**（Blackwell sm120）；本机 4060（Ada sm89）会被 ComfyUI 禁用 `nvfp4`/`mxfp8`。FP8 在 Ada 上受支持但本项目未实测其速度。
10. **环境变化**：基线记录 HF 不可达，本次 2026-10-17 实测可达（206 range、0.37–0.58 MB/s，较慢）。若 HF 再次不可达，§5.2「仅 HF」里的变体（含当前在用的 W4 mixed_balanced）**无法重新下载**——这是分发风险，需 S2 决策是否在本地归档/换源。
11. **`README.md` §9 的 "W4 = W4 + FP8 组缩放 + FP32 通道缩放 + Lloyd-Max codebook"** 与 NidAll 卡自述（ComfyUI-native mixed-precision，tags 含 `w4a8/int8/convrot`）不完全一致；格式细节未在公开卡里给出，标 `?`。不影响"它能跑、省显存"的事实。
12. **Turbo 官方 8 步 schedule 的可移植性已由 `turbo/README.md` 验证（ManualSigmas）**，但这套 sigma 是否适用于 Lightning 4 步 / Turbo8 LoRA 未验证。
