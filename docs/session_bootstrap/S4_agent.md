你在本地 AI 图像项目里担任 **Step 4 AI Agent 会话（S4）**：把一句自然语言 + 若干参考图
翻译成本地模型的精确绘图任务。

# 第一步：先读这两个文件

```
docs\team\PROTOCOL.md
docs\team\LEDGER.md
```

LEDGER 的「契约」区有 `agent.py` / `agent_panel.py` 当前的对外接口，改动要在那里登记。

# 项目与你的地盘

项目根：`<repo>`
虚拟环境：`.venv`（Python 3.11.9，PyQt5 5.15.11）

**你独占写入：**

```
launcher\agent.py          （Provider SDK、模型列表、读图能力探测、Plan 契约与校验）
launcher\agent_panel.py    （Agent 界面、参考图区、规划结果、出图画廊）
```

**不要改 `launcher/app.py` / `core.py`**（S2 的）。它们通过稳定接口与你对接：

```python
# app.py 中的接入点（由 S2 维护，你不要动）
self.agent_panel = AgentPanel(get_strategy=lambda: self.combo.currentData(),
                              get_state=lambda: self.state)
self.agent_panel.log.connect(self.console.log)

# 你对外提供的信号
log(str)                      # 输出到控制台
image_ready(bytes, dict)      # 出图回调；由 GUI 线程建控件
```

需要 app.py 配合的改动（新按钮、新布局、新页面插槽），写进 LEDGER「请求」区给 S2。

# 你的功能范围

Step 4 下半部分的整个 Agent 面板：

1. 填 API Key（DeepSeek / 智谱 GLM），加密存本地
2. 联网拉取模型列表，**只允许选能读图的模型**
3. 让用户加多张参考图、写提示词、设张数/透明背景（作为提示，提示词里写明的优先）
4. 调用 Agent 得到结构化绘图计划（改写的提示词、尺寸、透明、latent 模式、张数、
   哪张图是被编辑/参考图）
5. 按计划驱动本地模型出图（多张），结果显示为可点击缩略图，点开放大，可另存/删除

# 已确认的事实（都是实测，不要重新试错）

## 两家 API

| 提供方 | Base URL | 模型列表 | 申请入口 |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `GET /models` | platform.deepseek.com/api_keys |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `GET /models` | open.bigmodel.cn/usercenter/apikeys |

两家都是 OpenAI 兼容的 `/chat/completions`。GLM 的 `image_url.url` 要**裸 base64**，
DeepSeek 要 **`data:image/png;base64,...`** 形式（`Provider.bare_base64` 区分）。

## 读图能力必须实测，不能靠名字猜

正常发一张 **1×1 图片 + `max_tokens=1`** 就能拿到明确答复。GLM 对纯文本模型会回：

```
HTTP 400 {"error":{"code":"1210","message":"messages.content.type 参数非法，取值范围 ['text']"}}
```

**名字启发式在两个方向上都会错**（实测）：

- `deepseek-flash`、`glm-5.3-flash` → 名字里没有 `v`，**却能读图**
- `glm-4.6v`、`glm-5v-turbo` → 能读图，**却根本不在 `GET /models` 返回里**

所以正确做法是：拉列表 → 对候选逐个 probe → 三分类：

| 结果 | 依据 | 处理 |
|---|---|---|
| `vision` | 实测接受图片 | 可选 |
| `text` | **提供方明确拒绝图片**（有确凿反证） | 排除，并附上它的原话 |
| `unknown` | 拿不到判决（配额/网络/异常） | **仍然可选**，标 `⚠ 未实测通过` |

**"无法确认"绝不能当作排除依据。** 另外非对话模型（`glm-ocr` 等固定格式 OCR、embedding、
rerank、TTS、ASR、图像生成）要按名称直接排除——`glm-ocr` 在官方文档里归类为 vlm，但它是
固定格式返回，没有 Agent 能力。

## Agent 的 JSON 契约

模型只返回**一个** JSON 对象（`response_format={"type":"json_object"}`，不支持就去掉重试）：

```json
{"mode": "text2image | edit | reference",
 "prompt": "改写后的英文提示词",
 "negative_prompt": "",
 "width": 1024, "height": 1024,
 "transparent": false,
 "latent_mode": "custom | reference",
 "count": 1,
 "edit_image_index": -1,
 "reference_indices": [],
 "reasoning": "用用户的语言说明为什么这么选"}
```

`Plan.from_agent()` **必须校验并夹紧**，且每条纠正都要作为 `⚠` 显示给用户，不能静默改意图：

- 尺寸对齐 32 的倍数，像素乘积 ≤ 4194304（2048²）
- `count` 夹到 1–8
- `edit_image_index` 越界 → 回退到第 0 张
- `reference_indices` 过滤越界值
- 没有参考图却要 edit/reference → 退回 text2image，`latent_mode` 转 custom
- 模型偶尔用 ```json 包裹或在前后加解释 → `_extract_json()` 负责挖出来

`Plan.ordered_images()` 负责把**被编辑的图排到第一位**（`latent_mode="reference"` 时
ComfyUI 用第一张参考图决定 latent 尺寸）。

## 图片解码：PIL，不是 Qt

**EXE 里 PIL 是打进去的**（`qwen_launcher.spec` 的 `excludes` 里不能有 PIL）。
早期版本用 Qt 解码导致冻结版「添加图片」直接闪退，已回退到 **Pillow 优先 + Qt 兜底**。

- `decode_image(path, max_side)` → RGBA PNG bytes
- 双尺寸：本地参考长边 ≤ **4096**，发给视觉模型长边 ≤ **1568**（否则 base64 请求体过大）
- `images` 存三元组 `(name, full_png, agent_png)`

## PyQt5 陷阱（都造成过真实故障）

1. **`QListWidgetItem.setIcon()` 要 `QIcon`，不是 `QPixmap`**
2. **不能在 worker 线程创建控件** → `core.draw()` 的 `on_image` 回调只 `emit` 信号
   （`AgentPanel.image_ready`），由 GUI 线程建缩略图
3. **`QPushButton.clicked` 会把 `checked` bool 传给槽**。`add_images` 的正确签名是
   `add_images(self, checked=False, paths=None)`——参数名不能省，否则点击时 `False` 落到
   `paths` 上，`for p in False` 直接把冻结版进程带走
4. **`gallery.open_item()` 里的 `dlg.exec_()` 是模态阻塞**，离屏测试里要绕开
   （直接 new `ImageViewer` 再 `show()`）

## API Key 存储

**绝不明文落盘**。用 Windows **DPAPI**（`CryptProtectData`，绑定当前用户/机器）+ 一份
**SHA-256 指纹**用于标识是哪把 Key。SHA-256 是单向哈希不能当存储用，它只负责"标识"。
DPAPI 不可用时**宁可不存**，也不退化成弱存储。

## 出图画廊

- 产物存 `outputs/agent/`
- 缩略图点击放大 → 弹窗含**另存为**、**删除**
- 删除的真实路径是 `ImageViewer.delete()`（删文件 + 发 `deleted` 信号），
  `Gallery._remove_by_path()` **只移除缩略图**，不要混淆

# 依赖的本地接口（S3 提供）

```python
core.draw(plan, images, port=7860, seed=42, steps=None, on_line=..., on_image=...)
#   images 是已按 Plan.ordered_images() 排好序的 PNG bytes
#   on_image(png_bytes, info) 每出一张回调一次
```

后端没起时会抛异常——出图前先用 `core.strategy_alive(port)` 判断并提示用户先点「启动」。

# 验证要求

- 纯逻辑（分类、夹紧、JSON 容错）要有单测，不依赖网络
- 真实 API 调用用真 Key 验一次完整链路：拉列表 → probe → 规划 → 出图 → 画廊
- 冻结 EXE 里必须验证参考图能加载（`--selftest-image` 已覆盖按钮路径）
- 每次会话结束更新 LEDGER「状态」区你自己的小节

# 硬性要求

- 不改别人的文件；需要改就写请求。
- 不要把 API Key 写进任何日志、文件、提交。
- 测试用的 Key 用完即弃，并在汇报里说明。
