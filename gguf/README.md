# gguf 策略 / Turbo GGUF strategy

**目的**：把 Turbo 蒸馏放进与现有 W4 量化**完全相同的显存预算**里，用 8 步换速度。

```
UNETLoaderGGUF (qwen_image_2.1_turbo_Q4_K_M.gguf, 3.90 GiB)
    -> QwenImage21Cache
    -> KSampler   (8 步, CFG 1.0, euler/simple)
```

放置与 `mixed-vram` / `turbo` 完全一致：**文本编码器在内存/CPU**、**扩散模型在显存**、
**VAE 从内存迁移到显存做解码**。

---

## 结论先行：能用，但**比现有方案慢 2.4 倍**

实测（1024×1024，8 步，CFG 1.0，热后端，同一台机器）：

| 方案 | 单步耗时 | 整轮耗时 | 峰值显存 |
|---|---|---|---|
| **W4 + Turbo LoRA**（`turbo` 策略） | ~1.4 s/it | **12.92 s** | 6919 MiB |
| W4 基础模型 @ 8 步 | ~1.5 s/it | 13.45 s | 6919 MiB |
| **GGUF Q4_K_M**（本策略） | **3.53 s/it** | **31.13 – 31.73 s** | 6939 MiB |

**显存完全符合预期**（6939 vs 6919 MiB，因为 3.90 vs 3.91 GiB），
**但速度只有 W4 方案的 41%。**

原因很明确：**GGUF 的重量保持量化状态，每一步都要逐算子反量化**（`dequant.py` →
`GGMLTensor`）。而现有的 W4 "mixed" 走的是 ComfyUI 原生 `MixedPrecisionOps`
路径，反量化是融合进内核的。**逐算子反量化的开销压倒了它带来的一切好处。**

所以：**为了"更快"而选 GGUF 是走反了方向。** 同样 3.90 GiB、同样 8 步，
`turbo` 策略（W4 + 0.85 GiB Turbo LoRA）快 2.4 倍。

本策略保留下来作为**对照与备用**（例如将来想试 Q5/Q6 质量档，或 W4 方案出问题时），
但**不建议作为日常使用**。

---

## 首次准备（隔离运行时）

本策略**刻意不碰共享运行时**，所以需要手动准备一次：

1. **隔离的 ComfyUI 检出**放在 `gguf\ComfyUI\`（v0.39.0）。它不是 `setup_*.bat` 建的，
   所以 `start.bat` 会检查并在缺失时打印指引。
2. **ComfyUI-GGUF 只装在这个隔离检出里**（`gguf\ComfyUI\custom_nodes\ComfyUI-GGUF`），
   这样它出问题也不会影响 `mixed` / `mixed-vram` / `turbo`。
3. **GGUF 模型**（3.90 GiB）：
   ```
   ModelScope  Abiray/Qwen-Image-2.1-Turbo-GGUF
   qwen_image_2.1_turbo_Q4_K_M.gguf  ->  gguf\ComfyUI\models\unet\
   ```
4. **`extra_model_paths.yaml`** 已写好，指向主 `ComfyUI\models` 的
   `text_encoders` / `vae` / `loras`，**只读引用，不重复占 9.34 GiB**。

---

## 隔离是如何保证的

| 项 | 位置 |
|---|---|
| ComfyUI 检出 | `gguf\ComfyUI\`（**独立**，v0.39.0） |
| ComfyUI-GGUF | 只在 `gguf\ComfyUI\custom_nodes\` |
| GGUF 模型 | 只在 `gguf\ComfyUI\models\unet\` |
| 文本编码器 / VAE / LoRA | 只读引用主 `ComfyUI\models`（未复制、未修改） |
| 后端端口 | **8189**（主实例是 8188，可共存） |
| 日志 | `outputs\comfyui_backend_gguf.log`（与主实例分开） |

已逐项验证：主 `ComfyUI\custom_nodes\` 仍只有示例文件，主 `models\diffusion_models\`
与 `models\unet\` 里只有原来的 `qwen_image_2.1_mixed_balanced.safetensors`。

## 用法

```
..\start.bat                启动（默认 8 步 / CFG 1.0）
..\stop.bat                 停止并释放模型（会停 7860 与 8189）
```

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `QWEN_MAX_PIXELS` | `4194304` | 最大像素乘积（唯一的分辨率硬限制） |
| `QWEN_MAX_REFS` | `4` | 参考图上限（ComfyUI 上限 16） |
| `QWEN_COMFYUI_DIR` | 由 profile 固定为 `gguf\ComfyUI` | 手动设置会覆盖 profile |

安装/运行顺序见主文档 `README.md` 与 `setup\README.md`。
