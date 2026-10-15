# turbo 策略 / Turbo LoRA strategy

**目的**：用 **0.85 GiB 的 Turbo 蒸馏 LoRA** 叠加在现有的 3.91 GiB W4 量化模型上，把采样步数从 25 步降到 8 步（约 3 倍去噪提速），而**不下载第二个扩散模型**（Turbo BF16 是 13.25 GiB，Turbo int8 是 6.76 GiB）。

```
UNETLoader (qwen_image_2.1_mixed_balanced, W4)
    -> LoraLoaderModelOnly (qwen_image_2.1_turbo_lora_avg_rank_178_bf16, strength 1.0)
    -> QwenImage21Cache
    -> KSampler   (8 步, CFG 1.0, euler/simple —— Turbo 官方配方)
```

放置与 `mixed-vram` 相同：Qwen3-VL 文本编码器留在内存（唯一真正占地方的 8.7 GiB），W4 扩散模型与 BF16 VAE 常驻显存。

---

## 为什么这个文件夹存在：核心问题已得到回答

**问题**：那个 Turbo LoRA 能不能加载到 NidAll 的**实验性 W4 "mixed" 格式**上？还是会被静默丢弃？

**答案：能，而且确实生效了。** 证据：

| 检查 | 结果 |
|---|---|
| LoRA 文件 | `qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors`，**913,314,512 B**（与 ModelScope 上的字节数完全一致） |
| 张量数 | **665**，`ss_network_dim = Dynamic`（混合秩，所以叫 avg_rank_178） |
| 键名 | `diffusion_model.img_in.lora_down.weight` / `lora_up.weight` —— **与模型键名匹配** |
| 是否有 "key not loaded" 警告 | **没有** |
| 输出是否改变 | **是**（有 LoRA `8617116ba7ff` ≠ 无 LoRA `29d771e79d61`） |
| 速度代价 | **无**（12.92 s vs 13.45 s） |
| 报错 | 无 |

机制上说得通：ComfyUI 的 LoRA patch 通过量化算子上的 `set_weight` → `requantize_from_float` 写回权重（`comfy/ops.py:1492`），所以补丁会真正落到 W4 权重上，而不是被跳过。

> 附带说明：ComfyUI 一旦存在 hook patch（LoRA 就算），会自动关闭 Qwen 前缀 KV cache（`model_base.py` 里 `reset_prefix_cache(len(patcher.hook_patches) == 0)`）。那个缓存我们已实测对文生图是空转，所以没有损失。

---

## 结论（2026-10-13 修订）：Turbo LoRA **确实有意义**

之前用"灯塔日落"这种**简单常见**提示词测，看不出 LoRA 的价值——那是**测试不敏感**，不是 LoRA 没用。换用**难提示词**（画面内文字 / 三物体位置与属性绑定 / 机械精细结构）后差异就出来了：

| 提示词 | base@8 | turbo@8 | base@25 |
|---|---|---|---|
| `NORTHWIND` 木牌文字 | 文字对，但**牌子结构错乱**（多出一块木板） | 干净 | 干净 |
| 三只瓶子（左高蓝 / 中矮绿 / 右圆琥珀） | 都对，瓶形略糙 | 都对 | 都对，最干净 |
| 怀表机芯微距 | **明显劣化**：机芯糊成一团、罗马数字乱码 | 齿轮与摆轮清晰 | 清晰，罗马数字环规整 |

**怀表那一题是判别题**：`base@8` 出现了低步数典型的"糊 + 文字乱码"，而 `turbo@8` 与 `base@25` 处于同一水平。

### 而且它是**免费**的

1024²、热后端、每种配置连跑两次取第二次（消除模型重装载代价）：

| 配置 | 步数 | ComfyUI 总耗时 | 相对 base@25 |
|---|---|---|---|
| base @ 8 步 | 8 | 13.69 s | **2.85×** |
| turbo LoRA @ 8 步（`simple`） | 8 | 14.04 s | 2.78× |
| **turbo LoRA @ 8 步（官方网格）** | 8 | **13.75 s** | **2.83×** |
| base @ 25 步 | 25 | 38.98 s | 1.00× |

**三种 8 步配置耗时完全相同（13.7–14.0 s），单步速率也一样。** 所以 LoRA 不带来任何速度或显存代价，2.85 倍提速来自"用 8 步"本身；**LoRA 买到的是"8 步不掉画质"**。

## 调度已修正

Turbo 是用它自带的 8 步网格（`sample_sigmas`）蒸馏的，而 **ComfyUI 读不到 checkpoint 或 LoRA 里的调度**——`KSampler` 总是按模型采样设置重建网格。我们原先用 `simple`，其网格**近乎均匀**，与官方网格的平均偏差 **0.148**（`linear_quadratic` 只有 0.031）。

现已改为**精确网格**：图里用 `SamplerCustom` + `ManualSigmas` 直接喂官方值
`1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0`

（常量在 `shared\qwen_comfy.py` 的 `TURBO_SIGMAS`，由 profile 的 `sigmas_default` 启用。`SamplerCustom` 内部会调 `fix_empty_latent_channels`，所以 4 通道的 `EmptyLatentImage` 依然正确。）

**效果**：怀表那一题里精确网格版齿轮更锐、罗马数字盘更规整，是四张里最好的；耗时也略低（13.75 vs 14.04 s）。**但这个差异比"base@8 → turbo@8"的差距小得多**，n=3 的情况下只能说"≥，且无任何代价"，保留它是因为它在数学上正确且没有下行风险。

**其它策略不受影响**：`mixed` / `mixed-vram` 用 `simple` + 25 步，与 ComfyUI 官方 2.1 模板（`image_qwen_image_2_1_t2i.json`：steps 25、cfg 1、euler、simple）完全一致。

---

## 旧的（已被推翻的）结论

<details>
<summary>2026-10-12 首次测试：曾认为"收益未被证明"</summary>

用简单提示词、n=1 测出三张图都不错，因此写下"Turbo LoRA 未证明收益"。**该结论已作废**——原因是提示词对蒸馏收益不敏感（简单概念在低步数下本来就画得好），而不是 LoRA 无效。保留此段以记录这次方法论失误。

</details>


## 用法

```
..\start.bat                启动（默认 8 步 / CFG 1.0）
..\stop.bat                 停止并释放模型
```

命令行参数：`--api-only`、`--no-browser`、`--yes`。
WebUI 里步数滑块默认值已按本策略设为 8。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `QWEN_MAX_PIXELS` | `4194304` | 最大像素乘积（唯一的分辨率硬限制） |
| `QWEN_MAX_REFS` | `4` | 参考图上限（ComfyUI 侧上限 16） |
| `QWEN_KV_CACHE_DEVICE` | `gpu` | 有 LoRA patch 时 ComfyUI 会自动关闭前缀缓存，此设置基本被忽略 |

想临时关掉 LoRA 对比：删除或改名 `ComfyUI\models\loras\qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors`
（本策略会报明确的缺文件错误，其他策略不受影响）。

---

## 本策略**没有**改动的东西

- 没有修改 `native` / `mixed` / `mixed-vram` 的任何行为（已逐项验证：默认图的节点仍是 1–9，cache 仍取节点 1，无 LoRA 节点）。
- 没有覆盖任何已有模型文件。LoRA 是**新增文件**，放在 `ComfyUI\models\loras\`。
- 没有安装任何新的 ComfyUI 自定义节点（GGUF 路线才需要，本策略不需要）。
- 四个分发文件夹 `Qwen-Image-2.1-WebUI-*` 未改动。

`shared\qwen_comfy.py` 的 `build_graph()` 新增了 `lora_name` / `lora_strength` / `unet_loader` 三个**可选**参数，默认值保证原有调用完全不变。
