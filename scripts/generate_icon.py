#!/usr/bin/env python3
"""Generate the AI Director app icon (clapperboard + AI sparkle).

Renders at high resolution with Pillow and writes every size the project
needs:

    assets/icon/icon.png                    512  README / general use
    desktop/tauri/src-tauri/icons/icon.png  512  Tauri shell + AppImage
    installer/windows/aidirector.ico        multi-size Windows icon
    src/aidirector/web/static/favicon.png   64   web UI / app window

Run from the repo root:  uv run python scripts/generate_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
S = 1024  # master canvas
SS = 4    # supersampling for crisp edges


def _linear_gradient(size: int, start: tuple, end: tuple) -> Image.Image:
    """Diagonal (top-left -> bottom-right) RGB gradient."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(
                int(s + (e - s) * t) for s, e in zip(start, end)
            )
    return img


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size * SS, size * SS), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle(
        (0, 0, size * SS - 1, size * SS - 1), radius=radius * SS, fill=255
    )
    return mask.resize((size, size), Image.LANCZOS)


def _sparkle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
             fill: tuple) -> None:
    """Four-point star with slightly pinched waist."""
    pts = []
    for i in range(8):
        angle = math.pi / 4 * i - math.pi / 2
        radius = r if i % 2 == 0 else r * 0.22
        pts.append((cx + radius * math.cos(angle),
                    cy + radius * math.sin(angle)))
    draw.polygon(pts, fill=fill)


def build_master() -> Image.Image:
    # --- background: indigo -> violet rounded square -------------------
    bg = _linear_gradient(S, (43, 38, 126), (139, 63, 216)).convert("RGBA")
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(bg, (0, 0), _rounded_mask(S, 224))

    # --- clapperboard glyph (drawn supersampled) ------------------------
    hi = Image.new("RGBA", (S * SS, S * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(hi)
    z = SS
    white = (247, 247, 252, 255)
    ink = (43, 38, 126, 255)

    # soft drop shadow under the board
    shadow = Image.new("RGBA", (S * SS, S * SS), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((212 * z, 452 * z, 812 * z, 816 * z),
                         radius=44 * z, fill=(20, 12, 60, 140))

    # board body
    d.rounded_rectangle((200 * z, 440 * z, 800 * z, 804 * z),
                        radius=44 * z, fill=white)
    # "edit plan" lines on the slate
    d.rounded_rectangle((268 * z, 548 * z, 656 * z, 596 * z),
                        radius=24 * z, fill=ink)
    d.rounded_rectangle((268 * z, 664 * z, 540 * z, 712 * z),
                        radius=24 * z, fill=(139, 63, 216, 255))

    # hinged top bar, rotated open, striped
    bar_w, bar_h = 620, 132
    bar = Image.new("RGBA", (bar_w * z, bar_h * z), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    bd.rounded_rectangle((0, 0, bar_w * z - 1, bar_h * z - 1),
                         radius=36 * z, fill=white)
    stripe_mask = Image.new("L", (bar_w * z, bar_h * z), 0)
    smd = ImageDraw.Draw(stripe_mask)
    smd.rounded_rectangle((0, 0, bar_w * z - 1, bar_h * z - 1),
                          radius=36 * z, fill=255)
    stripes = Image.new("RGBA", (bar_w * z, bar_h * z), (0, 0, 0, 0))
    st = ImageDraw.Draw(stripes)
    for i in range(0, bar_w + bar_h, 124):
        st.polygon([(i * z, bar_h * z), ((i + 62) * z, bar_h * z),
                    ((i + 62 - bar_h) * z, 0), ((i - bar_h) * z, 0)],
                   fill=ink)
    stripes.putalpha(
        ImageChops.multiply(stripes.getchannel("A"), stripe_mask))
    bar.alpha_composite(stripes)
    bar = bar.rotate(14, resample=Image.BICUBIC, expand=True,
                     center=(36 * z, bar_h * z - 36 * z))
    hi.alpha_composite(bar, (196 * z, (440 - bar_h + 14) * z))

    # AI sparkles, cyan
    _sparkle(d, 812 * z, 236 * z, 92 * z, (129, 236, 255, 255))
    _sparkle(d, 700 * z, 348 * z, 40 * z, (129, 236, 255, 230))

    shadow = shadow.resize((S, S), Image.LANCZOS).filter(
        ImageFilter.GaussianBlur(14))
    glyph = hi.resize((S, S), Image.LANCZOS)
    icon.alpha_composite(shadow)
    icon.alpha_composite(glyph)
    return icon


def main() -> None:
    icon = build_master()
    targets = {
        ROOT / "assets" / "icon" / "icon.png": 512,
        ROOT / "desktop" / "tauri" / "src-tauri" / "icons" / "icon.png": 512,
        ROOT / "src" / "aidirector" / "web" / "static" / "favicon.png": 64,
    }
    for path, size in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        icon.resize((size, size), Image.LANCZOS).save(path)
        print(f"wrote {path.relative_to(ROOT)} ({size}px)")

    ico_path = ROOT / "installer" / "windows" / "aidirector.ico"
    icon.resize((256, 256), Image.LANCZOS).save(
        ico_path, sizes=[(256, 256), (128, 128), (64, 64),
                         (48, 48), (32, 32), (16, 16)])
    print(f"wrote {ico_path.relative_to(ROOT)} (multi-size .ico)")


if __name__ == "__main__":
    main()
