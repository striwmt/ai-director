"""Caption rendering for the preview renderer.

Captions are plan data (user editable); this module renders them to a
transparent PNG with Pillow and hands the preview renderer an ffmpeg
overlay+fade snippet. Core filters only — many ffmpeg builds (including
this machine's) ship without drawtext/freetype, so text is never rasterized
by ffmpeg itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..director.schemas import ClipCaption
from ..logging import get_logger
from ..process import run_command, tool_available

log = get_logger("timeline.captions")

FADE_IN = 0.4
FADE_OUT = 0.6
START_DELAY = 0.15
_MIN_SHOW = 1.0


# Well-known font locations for hosts without fontconfig (Windows, minimal
# Linux). CJK-capable fonts first; order matters.
FALLBACK_FONT_FILES: tuple[str, ...] = (
    # Windows
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\yugothm.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    # Linux without fontconfig
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/NotoSansCJKjp-VF.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # macOS
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
)


@lru_cache(maxsize=4)
def find_caption_font(needs_cjk: bool = True) -> str | None:
    """Resolve a usable font file; None disables captions.

    fontconfig (Linux) is authoritative when present; otherwise fall back
    to well-known font paths so Windows/macOS work without extra tooling.
    """
    if tool_available("fc-match"):
        pattern = "sans-serif:lang=ja" if needs_cjk else "sans-serif"
        try:
            result = run_command(["fc-match", "-f", "%{file}", pattern], timeout=10.0)
            font = result.stdout.strip()
            if font:
                return font
        except Exception as exc:
            log.warning("fc-match lookup failed (%s); trying fallbacks", exc)
    for candidate in FALLBACK_FONT_FILES:
        if Path(candidate).is_file():
            return candidate
    log.warning("no caption font found; captions disabled")
    return None


@dataclass(frozen=True)
class CaptionOverlay:
    png_path: Path
    show_seconds: float  # visible duration including fades, from clip start

    @property
    def filter_snippet(self) -> str:
        """Filter applied to the looped PNG input before overlaying."""
        fade_out_start = max(START_DELAY + FADE_IN, START_DELAY + self.show_seconds - FADE_OUT)
        return (
            "format=rgba,"
            f"fade=t=in:st={START_DELAY}:d={FADE_IN}:alpha=1,"
            f"fade=t=out:st={fade_out_start:.3f}:d={FADE_OUT}:alpha=1"
        )

    @property
    def enable_expr(self) -> str:
        return f"between(t,{START_DELAY},{START_DELAY + self.show_seconds:.3f})"


def render_caption_png(
    caption: ClipCaption,
    canvas_w: int,
    canvas_h: int,
    out_path: Path,
    *,
    font_file: str,
) -> bool:
    """Draw the caption centered on a transparent canvas-sized PNG."""
    from PIL import Image, ImageDraw, ImageFont

    lines: list[tuple[str, int]] = []
    main = caption.text.strip()
    secondary = caption.secondary.strip()
    main_size = max(18, round(canvas_h * 0.055))
    sub_size = max(13, round(canvas_h * 0.034))
    if main:
        lines.append((main, main_size))
    if secondary:
        lines.append((secondary, sub_size))
    if not lines:
        return False

    image = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    rendered: list[tuple[str, "ImageFont.FreeTypeFont", int, int]] = []
    gap = round(main_size * 0.35)
    total_h = 0
    for text, size in lines:
        try:
            font = ImageFont.truetype(font_file, size)
        except OSError as exc:
            log.warning("cannot load font %s (%s); caption skipped", font_file, exc)
            return False
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        rendered.append((text, font, right - left, bottom - top))
        total_h += bottom - top
    total_h += gap * (len(rendered) - 1)

    stroke = max(2, main_size // 18)
    y = (canvas_h - total_h) // 2
    for text, font, _w, h in rendered:
        draw.text(
            (canvas_w // 2, y),
            text,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke,
            stroke_fill=(0, 0, 0, 150),
            anchor="ma",  # centered horizontally, top-anchored vertically
        )
        y += h + gap

    image.save(out_path)
    return True


def build_caption_overlay(
    caption: ClipCaption,
    canvas_w: int,
    canvas_h: int,
    clip_duration: float,
    work_dir: Path,
    clip_index: int,
    *,
    font_file: str | None = None,
) -> CaptionOverlay | None:
    """Prepare the PNG + timing for one clip's caption; None = don't render."""
    show = min(caption.duration, clip_duration - 0.3)
    if show < _MIN_SHOW:
        return None
    text_all = caption.text.strip() + caption.secondary.strip()
    if not text_all:
        return None
    needs_cjk = any(ord(ch) > 0x2E80 for ch in text_all)
    font = font_file or find_caption_font(needs_cjk)
    if font is None:
        return None
    png_path = work_dir / f"caption_{clip_index}.png"
    if not render_caption_png(caption, canvas_w, canvas_h, png_path, font_file=font):
        return None
    return CaptionOverlay(png_path=png_path, show_seconds=show)
