"""AI Agent SDK for the launcher: turn a loose user request into a draw plan.

Two providers, both OpenAI-compatible (verified reachable from this machine):

    DeepSeek   https://api.deepseek.com            GET /models   POST /chat/completions
    GLM 智谱    https://open.bigmodel.cn/api/paas/v4  GET /models   POST /chat/completions

The model list is always fetched live with the user's own key -- nothing is
hardcoded -- and then filtered down to models that can actually see images,
because the agent's whole job is reading the user's pictures.

The agent returns one JSON object; `Plan.from_agent()` validates and clamps it
before the launcher is allowed to use it.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

TIMEOUT = 120


# ---------------------------------------------------------------------------
#  providers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    base: str
    signup: str
    # GLM wants bare base64 in image_url.url; DeepSeek takes a data: URL.
    bare_base64: bool = False


PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider(
        key="deepseek", name="DeepSeek",
        base="https://api.deepseek.com",
        signup="https://platform.deepseek.com/api_keys",
    ),
    "glm": Provider(
        key="glm", name="智谱 GLM (BigModel)",
        base="https://open.bigmodel.cn/api/paas/v4",
        signup="https://open.bigmodel.cn/usercenter/apikeys",
        bare_base64=True,
    ),
}


class AgentError(RuntimeError):
    pass


def _post(url: str, api_key: str, payload: dict, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:600]
        except Exception:  # noqa: BLE001
            pass
        raise AgentError(f"HTTP {e.code}：{body or e.reason}") from e
    except Exception as e:  # noqa: BLE001
        raise AgentError(f"{type(e).__name__}: {e}") from e


def _get(url: str, api_key: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:400]
        except Exception:  # noqa: BLE001
            pass
        raise AgentError(f"HTTP {e.code}：{body or e.reason}") from e
    except Exception as e:  # noqa: BLE001
        raise AgentError(f"{type(e).__name__}: {e}") from e


# ---------------------------------------------------------------------------
#  live model list + capability probing
#
#  The provider answers the capability question itself. GLM replies to a
#  text-only model with:
#      HTTP 400 {"error":{"code":"1210","message":"messages.content.type 参数非法，
#                       取值范围 ['text']"}}
#  so sending a 1x1 image costs one token and returns the truth.
#
#  Name heuristics were measured wrong in BOTH directions against the live APIs:
#      deepseek-flash, glm-5.3-flash  -> accept images, no "v" in the name
#      glm-4.6v, glm-5v-turbo         -> accept images, absent from GET /models
#
#  So: probe. Three outcomes, and only "text" is evidence of absence.
#      vision  -- accepts image input, selectable
#      text    -- provider says text only, hidden with the provider's own reason
#      unknown -- no verdict (quota, network, odd error): STILL SELECTABLE
# ---------------------------------------------------------------------------
TEXT_ONLY_SIGNATURE = "取值范围 ['text']"

# 1x1 transparent PNG -- enough to trigger the verdict, cheap enough to ignore
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg==")

# Names the providers' own docs list as multimodal but that GET /models omits.
# They are probed like everything else, so a stale entry simply drops out.
DOCUMENTED_VISION_CANDIDATES = {
    "glm": ["glm-4v-flash", "glm-4.6v", "glm-4.6v-flash", "glm-5v-turbo",
            "glm-4.1v-thinking-flash"],
    "deepseek": [],
}

# Used only to order the probe queue, so likely candidates answer first.
_VISION_HINT = ("vision", "multimodal", "omni", "-vl", "vl-", "_vl", "vl2", "vlmax",
                "-4v", "-5v", "-6v", "4v-", "5v-", "6v-", "4.1v", "4.5v", "4.6v",
                "5.1v", "v-plus", "v-flash", "v-turbo", "v-pro")

# Not conversational endpoints at all -- nothing to probe.
_NOT_AN_AGENT = (
    ("ocr", "OCR / 固定格式识别，不接受 Agent 指令"),
    ("embedding", "向量化模型"),
    ("rerank", "重排序模型"),
    ("tts", "语音合成"),
    ("asr", "语音识别"),
    ("whisper", "语音识别"),
    ("-voice", "语音模型"),
    ("realtime", "实时语音对话"),
    ("moderation", "内容审核"),
    ("cogview", "图像生成模型"),
    ("cogvideo", "视频生成模型"),
    ("glm-image", "图像生成模型"),
    ("glm-phone", "手机端智能代理框架，非通用对话模型"),
    ("codegeex", "代码补全模型"),
    ("-image", "疑似图像生成/编辑模型"),
    ("dall-e", "图像生成模型"),
    ("stable-diffusion", "图像生成模型"),
    ("flux", "图像生成模型"),
)


def is_conversational(model_id: str) -> tuple:
    """(ok, reason). False only for models that are plainly not chat LLMs."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return False, "空名称"
    for token, why in _NOT_AN_AGENT:
        if token in mid:
            return False, f"{why}（名称含 “{token}”）"
    return True, ""


def looks_multimodal(model_id: str) -> bool:
    """A hint only -- used to order the probe queue, never to decide."""
    mid = (model_id or "").lower()
    return any(t in mid for t in _VISION_HINT)


def list_models(provider_key: str, api_key: str) -> list:
    """Ask the provider what it currently serves. Nothing is hardcoded."""
    p = PROVIDERS[provider_key]
    data = _get(f"{p.base}/models", api_key)
    ids = []
    if isinstance(data, dict):
        for key in ("data", "models", "result"):
            v = data.get(key)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        ids.append(item)
                    elif isinstance(item, dict):
                        ids.append(item.get("id") or item.get("name") or item.get("model"))
                break
    elif isinstance(data, list):
        ids = [x if isinstance(x, str) else (x.get("id") or x.get("name") or x.get("model"))
               for x in data]
    return sorted({i for i in ids if isinstance(i, str) and i.strip()})


def probe_vision(provider_key: str, api_key: str, model: str, timeout: int = 45) -> tuple:
    """Ask the provider whether this model takes images.

    Returns (tier, reason) with tier in {'vision', 'text', 'unknown'}.
    'unknown' means no verdict was obtained -- an API error, a quota problem, an
    unfamiliar message shape. Unknown is not evidence of absence, so callers must
    keep those models selectable.
    """
    p = PROVIDERS[provider_key]
    payload = {"model": model, "max_tokens": 1, "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "ok"},
            {"type": "image_url", "image_url": {"url": _data_url(_TINY_PNG, p)}},
        ]}]}
    try:
        _post(f"{p.base}/chat/completions", api_key, payload, timeout=timeout)
        return "vision", "实测：接受图片输入"
    except AgentError as e:
        msg = str(e)
        if TEXT_ONLY_SIGNATURE in msg:
            return "text", "实测：提供方明确回复只接受 text"
        low = msg.lower()
        if "content.type" in low or "1210" in msg:
            return "text", f"实测：拒绝图片输入（{msg[:70]}）"
        if ("不存在" in msg or "not exist" in low or "not found" in low
                or "1220" in msg or "invalid model" in low):
            return "unknown", f"模型不存在或不可用（{msg[:70]}）"
        return "unknown", f"无法判定：{msg[:90]}"


def refresh(provider_key: str, api_key: str, on_progress=None) -> dict:
    """List then probe. Returns which models can actually see, with evidence."""
    say = on_progress or (lambda _s: None)
    listed = list_models(provider_key, api_key)
    say(f"GET /models 返回 {len(listed)} 个模型")

    candidates, dropped = [], []
    for m in listed:
        ok, why = is_conversational(m)
        (candidates if ok else dropped).append(m if ok else (m, why))
    extras = [m for m in DOCUMENTED_VISION_CANDIDATES.get(provider_key, [])
              if m not in listed and is_conversational(m)[0]]
    candidates += extras
    # likely candidates first so the useful answers arrive soonest
    candidates.sort(key=lambda m: (not looks_multimodal(m), m))

    vision, text_only, unknown = [], [], []
    for i, m in enumerate(candidates, 1):
        tier, why = probe_vision(provider_key, api_key, m)
        say(f"  [{i}/{len(candidates)}] {m}: {why}")
        if tier == "vision":
            vision.append(m)
        elif tier == "text":
            text_only.append((m, why))
        else:
            unknown.append((m, why))
    return {"listed": listed, "vision": vision, "text_only": text_only,
            "unknown": unknown, "excluded": dropped, "extras_tried": extras}


def filter_vision(models: list) -> tuple:
    """Offline fallback, used only before any probe has run.

    (likely_vision, not_obviously_vision, excluded) -- a name-based guess that
    must never be used to hide a model from the user.
    """
    yes, no, bad = [], [], []
    for m in models:
        ok, why = is_conversational(m)
        if not ok:
            bad.append((m, why))
        elif looks_multimodal(m):
            yes.append(m)
        else:
            no.append(m)
    return yes, no, bad


# ---------------------------------------------------------------------------
#  the contract the agent must fulfil
# ---------------------------------------------------------------------------
MODES = ("text2image", "edit", "reference")

SYSTEM_PROMPT = """You are the planning brain of a local image generator (Qwen-Image-2.1).
You read the user's request -- text and any attached pictures -- and return ONE JSON object
that tells the local model exactly what to draw. You never chat; you only return JSON.

Return exactly these keys:

{"mode": "text2image" | "edit" | "reference",
 "prompt": "<rewritten, vivid, concrete English prompt for the image generator>",
 "negative_prompt": "<usually empty string>",
 "width": <int>, "height": <int>,
 "transparent": <true|false>,
 "latent_mode": "custom" | "reference",
 "count": <int 1-8>,
 "edit_image_index": <int or -1>,
 "reference_indices": [<int>, ...],
 "reasoning": "<one short sentence in the USER'S OWN LANGUAGE explaining your choices>"}

How to choose:

- mode "text2image": no image is being edited or referenced, or images are irrelevant.
- mode "edit": the user wants a picture CHANGED (remove background, restyle, recolour,
  add/remove something, "make this..."). Put that picture's index in edit_image_index.
- mode "reference": the user wants a NEW picture that borrows subject/style/composition
  from the attached pictures. List them in reference_indices.

Rules:
- width/height must be multiples of 32. Keep the product under 4194304 pixels (2048x2048).
  Respect the aspect ratio the user asks for; if they give none, use 1024x1024.
- transparent: true when the user wants a cut-out, sticker, PNG with no background,
  or the background removed.
- latent_mode: use "reference" only when mode is "edit" or "reference" AND the output
  should inherit the size of the first attached picture; otherwise "custom".
- count: how many variations to draw. Default 1. If the user explicitly says a number
  (e.g. "give me 4"), use it, capped at 8.
- edit_image_index / reference_indices are 0-based into the attached pictures, in the
  order given. Use -1 / [] when there are no pictures.
- Rewrite `prompt` into a strong, specific image prompt: subject, composition, lighting,
  style, materials. Keep any text the user wants rendered INSIDE the image in quotes.
  Do not add content the user did not ask for.
"""


def _data_url(png_bytes: bytes, provider: Provider) -> str:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return b64 if provider.bare_base64 else f"data:image/png;base64,{b64}"


def build_messages(provider: Provider, user_prompt: str, images: list, ui_count: int,
                   ui_transparent: bool = False) -> list:
    """images: list of (name, png_bytes)."""
    content = [{"type": "text", "text":
                f"USER REQUEST:\n{user_prompt.strip() or '(the user only attached pictures)'}\n\n"
                f"UI currently suggests count={ui_count}, transparent={str(ui_transparent).lower()}. "
                f"The number stated in the user's request always wins."}]
    for i, (_name, raw) in enumerate(images):
        content.append({"type": "text", "text": f"[picture {i}]"})
        content.append({"type": "image_url",
                        "image_url": {"url": _data_url(raw, provider)}})
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}]


def _extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or fences; dig it out."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:  # noqa: BLE001
            pass
    raise AgentError("模型没有返回可解析的 JSON：\n" + text[:400])


def ask(provider_key: str, api_key: str, model: str, user_prompt: str,
        images: list, ui_count: int = 1, ui_transparent: bool = False) -> dict:
    """One round-trip: request in, validated Plan out."""
    p = PROVIDERS[provider_key]
    messages = build_messages(p, user_prompt, images, ui_count, ui_transparent)
    payload = {"model": model, "messages": messages, "temperature": 0.2,
               "response_format": {"type": "json_object"}}
    try:
        data = _post(f"{p.base}/chat/completions", api_key, payload)
    except AgentError as first:
        # Some models reject response_format; retry once without it.
        if "response_format" not in str(first) and "400" not in str(first):
            raise
        payload.pop("response_format", None)
        data = _post(f"{p.base}/chat/completions", api_key, payload)

    try:
        text = data["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise AgentError(f"响应结构异常：{json.dumps(data)[:400]}") from e
    return _extract_json(text)


# ---------------------------------------------------------------------------
#  validated plan
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    mode: str = "text2image"
    prompt: str = ""
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    transparent: bool = False
    latent_mode: str = "custom"
    count: int = 1
    edit_image_index: int = -1
    reference_indices: list = field(default_factory=list)
    reasoning: str = ""
    raw: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @classmethod
    def from_agent(cls, data: dict, n_images: int, max_pixels: int) -> "Plan":
        p = cls(raw=dict(data))
        p.mode = str(data.get("mode", "text2image")).strip().lower()
        if p.mode not in MODES:
            p.warnings.append(f"未知 mode “{p.mode}”，按 text2image 处理")
            p.mode = "text2image"
        p.prompt = str(data.get("prompt") or "").strip()
        p.negative_prompt = str(data.get("negative_prompt") or "").strip()
        p.reasoning = str(data.get("reasoning") or "").strip()

        def _int(key, default):
            try:
                return int(float(data.get(key, default)))
            except (TypeError, ValueError):
                p.warnings.append(f"{key} 不是数字，用 {default}")
                return default

        # Models occasionally answer booleans as strings ("false" is the
        # dangerous one: plain bool("false") is True).
        def _flag(key, default=False):
            v = data.get(key, default)
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            s = str(v).strip().lower()
            if s in ("1", "true", "yes", "y", "on", "是"):
                return True
            if s in ("0", "false", "no", "n", "off", "否", ""):
                return False
            p.warnings.append(f"{key} 不是布尔值（{v!r}），按 {default} 处理")
            return default

        # size: clamp, snap to /32, then shrink proportionally until under the cap
        def _axis(key, default=1024):
            raw = _int(key, default)
            value = max(256, min(4096, raw))
            if value != raw:
                p.warnings.append(f"{key}={raw} 超出 256–4096，已夹到 {value}")
            snapped = (value // 32) * 32
            if snapped != value:
                p.warnings.append(f"{key}={value} 不是 32 的倍数，已对齐到 {snapped}")
            return snapped

        p.width = _axis("width")
        p.height = _axis("height")
        if p.width * p.height > max_pixels:
            scale = (max_pixels / (p.width * p.height)) ** 0.5
            p.width = max(256, int(p.width * scale) // 32 * 32)
            p.height = max(256, int(p.height * scale) // 32 * 32)
            p.warnings.append(f"尺寸超上限，已缩到 {p.width}x{p.height}")

        p.transparent = _flag("transparent", False)
        p.latent_mode = "reference" if str(data.get("latent_mode", "custom")).lower() == "reference" else "custom"

        raw_count = _int("count", 1)
        p.count = max(1, min(8, raw_count))
        if p.count != raw_count:
            p.warnings.append(f"张数 {raw_count} 超出 1–8，已夹到 {p.count}")

        p.edit_image_index = _int("edit_image_index", -1)
        refs = data.get("reference_indices") or []
        if isinstance(refs, (int, str)):
            refs = [refs]
        p.reference_indices, not_int = [], []
        for r in refs:
            try:
                p.reference_indices.append(int(r))
            except (TypeError, ValueError):
                not_int.append(r)
        if not_int:
            p.warnings.append(f"参考图索引 {not_int} 不是整数，已忽略")

        # reconcile with how many pictures actually exist
        if n_images == 0:
            if p.mode != "text2image":
                p.warnings.append("没有附加图片，已退回 text2image")
            p.mode, p.edit_image_index, p.reference_indices = "text2image", -1, []
            p.latent_mode = "custom"
        else:
            if p.mode == "edit" and not (0 <= p.edit_image_index < n_images):
                p.warnings.append(f"edit_image_index={p.edit_image_index} 越界"
                                  f"（共 {n_images} 张），改用第 0 张")
                p.edit_image_index = 0
            kept = [i for i in p.reference_indices if 0 <= i < n_images]
            dropped = [i for i in p.reference_indices if not (0 <= i < n_images)]
            if dropped:
                p.warnings.append(f"参考图索引 {dropped} 越界"
                                  f"（共 {n_images} 张），已忽略")
            p.reference_indices = kept
            if p.mode == "reference" and not p.reference_indices:
                p.warnings.append("reference 模式没有有效参考图索引，改用第 0 张")
                p.reference_indices = [0]
        if not p.prompt:
            p.warnings.append("agent 未给出提示词")
        return p

    def ordered_images(self, images: list) -> list:
        """Attached pictures in the order the local model should receive them.

        The edited picture must come first (the ComfyUI graph sizes its latent
        from the first reference when latent_mode is "reference").
        """
        if not images:
            return []
        n = len(images)
        if self.mode == "edit":
            idx = self.edit_image_index if 0 <= self.edit_image_index < n else 0
            return [images[idx]] + [im for i, im in enumerate(images) if i != idx]
        if self.mode == "reference":
            picked_idx = [i for i in self.reference_indices if 0 <= i < n] or [0]
            picked = [images[i] for i in picked_idx]
            others = [im for i, im in enumerate(images) if i not in picked_idx]
            return picked + others
        return list(images)

    def summary(self) -> str:
        lines = [
            f"模式        : {self.mode}",
            f"提示词      : {self.prompt[:110]}{'…' if len(self.prompt) > 110 else ''}",
            f"尺寸        : {self.width} x {self.height}",
            f"透明背景    : {'是' if self.transparent else '否'}",
            f"latent 模式 : {self.latent_mode}",
            f"张数        : {self.count}",
        ]
        if self.mode == "edit":
            lines.append(f"被编辑图片  : 第 {self.edit_image_index} 张")
        if self.reference_indices:
            lines.append(f"参考图索引  : {self.reference_indices}")
        if self.reasoning:
            lines.append(f"agent 说明  : {self.reasoning}")
        for w in self.warnings:
            lines.append(f"⚠ {w}")
        return "\n".join(lines)
