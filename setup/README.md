# 安装与运行顺序 / Install & run order

本目录是**唯一的安装入口**。原有的 `install.bat` / `fix.bat` /
`Fix_Torchaudio_CUDA130.bat` / `Install_ComfyUI.bat` /
`Patch_ComfyUI_QwenImage21_Master.bat` / `Update_ComfyUI_QwenImage21.bat`
已经合并进这里，全部搬到 `_legacy\` 仅作参考，**不要再运行它们**。

---

## 推荐入口：交互式向导

```
setup_wizard.bat
```

不确定该装什么、或者只想少打几条命令，就先跑这个。它会：

1. **自动检测**——按和其它脚本相同的规则（本目录 → 子目录 → 最多 6 级父目录）
   找到 Python 环境、ComfyUI、模型缓存，并**逐个文件**检查模型是否已在磁盘上；
2. **显示一张状态表**，每项都带实测体积和 `OK` / `MISS`；
3. **让你选**要装什么，然后**调用已有的 `qwen_setup.ps1`** 去真正干活——
   向导自己**不下载、不安装**任何东西；
4. **自动补齐前置条件**并排好顺序：选模型会自动带上 `env` 和 `comfyui`；
   选 Turbo 会自动展开成 `env → comfyui → models-mixed -WithGguf`；
5. **跳过已就绪的部分**，只对缺失的项报出体积并询问一次。

选择示例（本机现状）：

```
   [1] 量化模型三件套（文本编码器 + VAE + W4/int8 扩散）    已就绪
   [2] 三件套 + Turbo 附加（Turbo int8 扩散 + Turbo LoRA）  约   6.76 GiB
   [3] 全量原生 BF16 模型                                   约  30.21 GiB
   [4] Python 环境（venv + torch cu130）                   已就绪
   [5] ComfyUI（含 Qwen-Image-2.1 支持校验）               已就绪
```

（Turbo LoRA 已单独下过，所以 [2] 只算缺的那 6.76 GiB，不重复计算。）

**非交互用法**（脚本化 / 分发时）：

```
setup_wizard.bat --yes                     不带参数=直接进菜单；加 --yes 则跳过下载前的询问
setup_wizard.bat --plan mixed,turbo        直接执行指定选择，不进菜单
setup_wizard.bat --plan env --yes          只补环境，且不询问
```

可用的 `--plan` 项：`env`、`comfyui`、`mixed`、`turbo`、`native`、`fix-torch`。
带参数运行时**不会**在结尾 `pause`，方便被别的脚本调用。

> **不在向导范围内**：Turbo **GGUF**（模型来自 `Abiray/Qwen-Image-2.1-Turbo-GGUF`，
> 且需要一份单独的 ComfyUI 检出装 ComfyUI-GGUF）。那份单独准备，见 `..\gguf\README.md`。

---

## 0. 先选路线：你要用哪套模型？

两套模型**完全独立**，可以分开分发到不同设备。先决定，再照着对应那一列走。

| | **量化路线**（显存 8 GB 左右） | **全量路线**（显存 16 GB+ / 只在 CPU 侧要求高内存） |
|---|---|---|
| 用哪个策略 | `mixed\start.bat` 或 `mixed-vram\start.bat` | `native\start.bat` |
| 后端 | ComfyUI | diffusers |
| 扩散模型 | W4 量化 **3.91 GiB**（已有）或 Comfy-Org int8 **6.76 GiB** | BF16 **13.25 GiB** |
| 文本编码器 | INT8 **8.71 GiB** | BF16 **16.33 GiB** |
| VAE | BF16 0.63 GiB | BF16 0.63 GiB |
| 磁盘占用 | 约 **14 GB** | 约 **31 GB** |
| 安装脚本 | `setup_env.bat` → `setup_comfyui.bat` → `setup_models_mixed.bat` | `setup_env.bat` → `setup_models_native.bat` |

> **量化路线不需要** `setup_models_native.bat`；**全量路线不需要**
> `setup_comfyui.bat` 和 `setup_models_mixed.bat`。分发给别人时只带对应那一套即可。

---

## 1. 运行顺序

### 1.0 先体检（可选但推荐）

```
status.bat
```

只读，不下载、不修改。会在**本目录、子目录、最多 6 级父目录**里查找
Python 环境、模型缓存、ComfyUI，并报告找到了什么、还缺什么。
下面每一步其实也都会做同样的检查，所以这步只是让你先看清楚状况。

---

### 量化路线（mixed / mixed-vram）

```
① setup_env.bat            建 venv + 装 torch cu130 / diffusers / 依赖
② setup_comfyui.bat        克隆 ComfyUI + 装依赖 + 校验支持 Qwen-Image-2.1
③ setup_models_mixed.bat   下载量化模型（已存在则自动跳过）
④ ..\mixed-vram\start.bat  启动（8 GB 显存推荐这个）
   或 ..\mixed\start.bat
```

### 全量路线（native）

```
① setup_env.bat            同上
② setup_models_native.bat  下载原生 BF16 全套（约 30 GB，已存在则自动跳过）
③ ..\native\start.bat      启动
```

---

## 2. 每个脚本做什么

| 脚本 | 作用 | 会联网吗 |
|---|---|---|
| `setup_wizard.bat` | **交互式向导**：检测现状 → 让你选 → 调用下面的脚本。自身不下载 | 不会 |
| `status.bat` | 只读体检，报告找到/缺失的组件 | 不会 |
| `setup_env.bat` | 建 `.venv`（Python 3.11）、装 torch 2.10.0+cu130 / torchvision / torchaudio / diffusers / `requirements.txt` | 会（pip + 可能装 git/Python） |
| `setup_comfyui.bat` | 克隆 ComfyUI、装过滤后的依赖（排除 torch）、**校验 Qwen-Image-2.1 能力**、可选装 ComfyUI-GGUF | 会（git clone + pip） |
| `setup_models_mixed.bat` | 下载量化三件套 | 仅在缺失时 |
| `setup_models_native.bat` | 下载原生 BF16 全套 | 仅在缺失时 |
| `fix_torch.bat` | torch/torchaudio 坏了时才用：重装 cu130 版本并验证 CUDA 可见 | 会（pytorch 源） |

### 自动跳过已存在的内容

每个脚本运行前都会在**当前目录 → 子目录 → 最多 6 级父目录**里搜索，
命中就直接复用，并把位置写进 `QWEN_*` 环境变量缓存（项目根的 `.qwen_env.json`）：

- `setup_env.bat`：找到可用的 `.venv`（含 torch）就不再重建，只问你要不要重装依赖。
- `setup_comfyui.bat`：找到带 `main.py` + `comfy/` + `comfy_extras/nodes_qwen.py` 的
  ComfyUI 就不再克隆。
- `setup_models_mixed.bat`：**逐个文件**检查
  `text_encoders/qwen3vl_8b_int8_convrot.safetensors`、
  `vae/qwen_image_2.1_vae_bf16.safetensors`、以及任意
  `*mixed*balanced*.safetensors`。**全都存在就完全不联网、也不询问**；
  只有缺失的那几个才会下载。
- `setup_models_native.bat`：检查三种形态——HF 风格缓存
  （`models_cache\models--Qwen--Qwen-Image-2.1`）、
  纯目录权重（`models_cache\Qwen-Image-2.1\transformer\*.safetensors`）、
  以及相邻目录里的 `diffusion_pytorch_model*.safetensors`。任一命中即跳过。

**下载前一定会在控制台问你。** 想跳过询问加 `--yes`。

---

## 3. 网络与镜像（本机实测，2026-10-12）

| 资源 | 采用 | 实测速度 | 未采用的原因 |
|---|---|---|---|
| pip | `mirrors.cloud.tencent.com` | **4.03 MB/s** | pypi.org 0.125、阿里 0.261、清华 3.50、中科大 3.97 |
| 模型 | **ModelScope** | **11.53 MB/s** | huggingface.co **不可达**、hf-mirror.com **不可达** |
| Python 安装包 | `mirrors.huaweicloud.com/python` | **10.28 MB/s** | python.org 0.357 |
| PyTorch cu130 | `mirrors.aliyun.com/pytorch-wheels/cu130` | — | 官方源作为回退 |
| git（MinGit） | `registry.npmmirror.com` | — | — |
| ComfyUI 源码 | `github.com/Comfy-Org/ComfyUI` | 可用 | **gitee 镜像禁止使用**，见下 |

> **gitee 的 ComfyUI 镜像是个陷阱。** `gitee.com/mirrors/ComfyUI` 只要 3.3 秒
> 就能克隆完，但它是**过期快照**——缺少 `comfy/ldm/qwen_image21` 与
> `TextEncodeQwenImage21`，**根本跑不了 Qwen-Image-2.1**。
> 所以 `setup_comfyui.bat` 只用 GitHub（备用加速地址 `gh-proxy.com`，实测 6.5 秒），
> 并在克隆后强制校验这三个文件存在，不合格就直接报错停下。

`git` 和 `Python 3.11` **不需要你手动安装**：缺失时会自动下载
MinGit 便携包与 Python 官方安装包（静默按当前用户安装，不改 PATH）。

---

## 4. 常见问题

**Q: 双击脚本闪退。**
用命令行运行，例如在 `setup\` 里执行 `setup_env.bat`，这样能看到完整输出。
脚本结束时都会 `pause`，如果闪退通常是 PowerShell 不可用。

**Q: 提示找不到 Python / git。**
重新运行 `setup_env.bat`，它会自动下载这两样。

**Q: 模型下载中断了。**
重新运行同一个脚本即可。已经下完的文件会被识别并跳过，只会续传缺失的。

**Q: 我把项目复制到了另一台设备，模型在别的地方。**
不用改脚本。把脚本放在新位置直接运行——它会在当前目录、子目录和最多
6 级父目录里找。也可以显式指定：
`set QWEN_MODELS_CACHE=D:\some\models_cache`，
或用 `QWEN_COMFYUI_DIR` / `QWEN_VENV_DIR`。

**Q: 换了机器测速结果不同，想换源。**
改 `..\shared\mirrors.json` 里的地址即可，脚本会读它。
想重新测速：运行 `_bench\mirror_speedtest.ps1`（在项目上一级目录）。

**Q: 8 GB 显存想再省一点。**
设 `QWEN_VAE_IN_VRAM=0` 把 VAE 放回内存（`--cpu-vae`），代价是解码变慢。
量化路线的显存占用见 `..\README.md`。
