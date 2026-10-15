"""Resolution handling for the Qwen-Image-2.1 strategies.

Requirement: the WebUI and the API must accept *arbitrary* width/height (or an
arbitrary aspect ratio plus a megapixel budget) instead of a fixed list of
presets, and must only enforce a maximum total pixel count.

Hard constraints of Qwen-Image-2.1 that make this safe:

* the VAE compresses 16x spatially (``scale_factor_spatial: 16``);
* the diffusers pipeline additionally requires a multiple of
  ``vae_scale_factor * 2`` = **32** (``check_inputs`` in
  ``pipeline_qwenimage21.py``) and silently floors to it otherwise;
* ComfyUI's empty-latent nodes only need 16, so 32 is the safe common
  denominator for every strategy in this project;
* attention cost grows with the sequence length, which is
  ``(w/16) * (h/16)`` tokens, i.e. linear in the pixel product -- so a maximum
  pixel product is the correct single knob.

Presets are still offered, but purely as convenience shortcuts that fill in
the width/height fields -- they are not an allow list.
"""

from __future__ import annotations

import math
import os
import re

# Granularity required by the model: the VAE compresses 16x and the diffusers
# pipeline demands a multiple of vae_scale_factor * 2 = 32. ComfyUI only needs
# 16, so 32 is the common denominator and is always safe.
SIZE_MULTIPLE = 32

# Sensible hard rails. The pixel budget is what actually protects the GPU.
MIN_SIDE = 256
MAX_SIDE = 4096
DEFAULT_MAX_PIXELS = 2048 * 2048          # 4.19 MP
ABSOLUTE_MAX_PIXELS = 4096 * 4096         # never allow more than this

# Convenience presets -> (w, h). Used to prefill the W/H inputs.
PRESETS = {
    "1:1   1024x1024": (1024, 1024),
    "1:1   1280x1280": (1280, 1280),
    "1:1   1536x1536": (1536, 1536),
    "1:1   2048x2048": (2048, 2048),
    "4:3   1344x1024": (1344, 1024),
    "3:4   1024x1344": (1024, 1344),
    "3:2   1536x1024": (1536, 1024),
    "2:3   1024x1536": (1024, 1536),
    "16:9  1536x864": (1536, 864),
    "9:16  864x1536": (864, 1536),
    "16:9  2048x1152": (2048, 1152),
    "9:16  1152x2048": (1152, 2048),
    "21:9  2016x864": (2016, 864),
    "9:21  864x2016": (864, 2016),
}

_SIZE_RE = re.compile(r"^\s*(\d{2,5})\s*[xX*×,\s]\s*(\d{2,5})\s*$")
_ASPECT_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*[:/]\s*(\d{1,3}(?:\.\d+)?)\s*$")


def max_pixels() -> int:
    """Pixel budget, overridable with QWEN_MAX_PIXELS (in pixels)."""
    raw = os.environ.get("QWEN_MAX_PIXELS", "").strip()
    if raw:
        try:
            value = int(float(raw))
            if value > 0:
                return min(value, ABSOLUTE_MAX_PIXELS)
        except ValueError:
            pass
    return DEFAULT_MAX_PIXELS


def snap(value: float, multiple: int = SIZE_MULTIPLE) -> int:
    """Round to the nearest multiple, never below one multiple."""
    return max(multiple, int(round(value / multiple)) * multiple)


def snap_up(value: float, multiple: int = SIZE_MULTIPLE) -> int:
    return max(multiple, int(math.ceil(value / multiple)) * multiple)


def parse_size(text: str):
    """'1536x1024' -> (1536, 1024). Returns None when it is not a size."""
    if not text:
        return None
    m = _SIZE_RE.match(str(text))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_aspect(text: str):
    """'16:9' -> 16/9. Returns None when it is not an aspect ratio."""
    if not text:
        return None
    m = _ASPECT_RE.match(str(text))
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    if a <= 0 or b <= 0:
        return None
    return a / b


def fit(width: int, height: int, budget: int | None = None) -> tuple[int, int]:
    """Snap to the model granularity and shrink to fit the pixel budget.

    Aspect ratio is preserved as closely as the 16-pixel grid allows. Shrinking
    prefers the smaller of the two candidate roundings at each step so the
    budget is never exceeded.
    """
    budget = budget or max_pixels()
    w = max(MIN_SIDE, min(MAX_SIDE, int(width)))
    h = max(MIN_SIDE, min(MAX_SIDE, int(height)))
    w, h = snap(w), snap(h)

    # Shrink while over budget. The loop is bounded, and each iteration either
    # shrinks the area or keeps it equal, so it always terminates.
    for _ in range(64):
        if w * h <= budget:
            break
        scale = math.sqrt(budget / float(w * h))
        nw = max(SIZE_MULTIPLE, int(math.floor(w * scale / SIZE_MULTIPLE)) * SIZE_MULTIPLE)
        nh = max(SIZE_MULTIPLE, int(math.floor(h * scale / SIZE_MULTIPLE)) * SIZE_MULTIPLE)
        if (nw, nh) == (w, h):
            # rounding stalled: shave one step off the longer side
            if w >= h and w > SIZE_MULTIPLE:
                w -= SIZE_MULTIPLE
            elif h > SIZE_MULTIPLE:
                h -= SIZE_MULTIPLE
            else:
                break
        else:
            w, h = nw, nh

    # clamp the sides but keep the product legal
    w = max(MIN_SIDE, min(MAX_SIDE, w))
    h = max(MIN_SIDE, min(MAX_SIDE, h))
    while w * h > budget and (w > SIZE_MULTIPLE or h > SIZE_MULTIPLE):
        if w >= h and w > SIZE_MULTIPLE:
            w -= SIZE_MULTIPLE
        elif h > SIZE_MULTIPLE:
            h -= SIZE_MULTIPLE
        else:
            break
    return w, h


def from_aspect(ratio: float, budget: int | None = None) -> tuple[int, int]:
    """Largest 16-pixel-grid size with this aspect ratio inside the budget."""
    budget = budget or max_pixels()
    w = math.sqrt(budget * ratio)
    h = w / ratio
    return fit(int(w), int(h), budget)


def resolve(
    width=None,
    height=None,
    *,
    aspect=None,
    budget: int | None = None,
    spec: str | None = None,
) -> tuple[int, int]:
    """Turn any of the accepted user inputs into a legal (width, height).

    Precedence: an explicit ``WxH`` string, then numeric width/height, then an
    aspect ratio, then square at the budget.
    """
    budget = budget or max_pixels()

    parsed = parse_size(spec) if spec else None
    if parsed:
        return fit(parsed[0], parsed[1], budget)

    try:
        w = int(width) if width not in (None, "") else None
        h = int(height) if height not in (None, "") else None
    except (TypeError, ValueError):
        w = h = None

    if w and h:
        return fit(w, h, budget)
    if w and not h:
        return fit(w, w, budget)
    if h and not w:
        return fit(h, h, budget)

    if aspect:
        ratio = parse_aspect(aspect) if isinstance(aspect, str) else float(aspect)
        if ratio:
            return from_aspect(ratio, budget)

    side = snap(math.sqrt(budget))
    return fit(side, side, budget)


def describe(w: int, h: int, budget: int | None = None) -> str:
    budget = budget or max_pixels()
    mp = w * h / 1e6
    return (
        f"{w}x{h} ({w/h:.3f} ratio, {mp:.2f} MP, "
        f"budget {budget/1e6:.2f} MP, multiple of {SIZE_MULTIPLE})"
    )


def _selftest() -> int:
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got}, want {want}")

    check("parse_size", parse_size("1536x1024"), (1536, 1024))
    check("parse_size x", parse_size(" 2048 x 1152 "), (2048, 1152))
    check("parse_size bad", parse_size("abc"), None)
    check("parse_aspect", round(parse_aspect("16:9"), 6), round(16 / 9, 6))
    check("parse_aspect bad", parse_aspect("16"), None)

    # already legal
    check("fit keep", fit(1536, 1024), (1536, 1024))
    # snap to the model grid
    w, h = fit(1000, 1000)
    check("fit snap w", w % SIZE_MULTIPLE, 0)
    check("fit snap h", h % SIZE_MULTIPLE, 0)
    # every preset must already sit on the grid
    for name, (pw, ph) in PRESETS.items():
        if pw % SIZE_MULTIPLE or ph % SIZE_MULTIPLE:
            failures.append(f"preset off-grid: {name} -> {pw}x{ph}")
    # over budget -> never exceeds
    for bw, bh in [(4096, 4096), (3000, 2000), (4096, 512), (512, 4096)]:
        w, h = fit(bw, bh)
        if w * h > DEFAULT_MAX_PIXELS:
            failures.append(f"budget violated for {bw}x{bh}: {w}x{h}={w*h}")
        if w % SIZE_MULTIPLE or h % SIZE_MULTIPLE:
            failures.append(f"granularity violated for {bw}x{bh}: {w}x{h}")
        if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
            failures.append(f"side rail violated for {bw}x{bh}: {w}x{h}")
    # aspect preservation within tolerance after downscale
    w, h = fit(4096, 2304)          # 16:9 over budget
    if abs((w / h) - (4096 / 2304)) > 0.05:
        failures.append(f"aspect drifted: {w}x{h} ratio {w/h:.4f}")
    # aspect-only path
    w, h = from_aspect(16 / 9)
    if w * h > DEFAULT_MAX_PIXELS or w % SIZE_MULTIPLE or h % SIZE_MULTIPLE:
        failures.append(f"from_aspect illegal: {w}x{h}")
    if abs((w / h) - 16 / 9) > 0.05:
        failures.append(f"from_aspect ratio: {w}x{h} ratio {w/h:.4f}")
    # resolve precedence
    check("resolve spec", resolve(spec="1536x1024"), (1536, 1024))
    check("resolve wh", resolve(1024, 1536), (1024, 1536))
    check("resolve square", resolve(width=1024), (1024, 1024))
    w, h = resolve(aspect="9:16")
    if abs((w / h) - 9 / 16) > 0.05:
        failures.append(f"resolve aspect: {w}x{h}")
    # single side over the rail
    w, h = fit(99999, 512)
    if w > MAX_SIDE:
        failures.append(f"max side not clamped: {w}")

    print(f"qwen_sizing self-test: {len(failures)} failure(s)")
    for f in failures:
        print("  FAIL", f)
    if not failures:
        print("  all checks passed")
        print("  examples:")
        for spec in ["1:1", "16:9", "9:16", "21:9", "4:3"]:
            w, h = resolve(aspect=spec)
            print(f"    {spec:5} -> {describe(w, h)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
