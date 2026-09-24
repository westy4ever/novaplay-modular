# -*- coding: utf-8 -*-
"""Diagonal ribbon PNG renderer for Enigma2 (no XML transforms available).

render_ribbon(text, (w, h)) -> path to a cached PNG of a red diagonal
band with the text drawn along it. Cached by sha1 so each unique
string renders once. Thread-safe enough for the grid painter (worst
case two identical misses race; both write the same file).
"""
import os
import hashlib

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:
    _PIL_OK = False

PLUGIN_PATH = os.path.dirname(__file__)
_CACHE_DIR  = os.path.join(PLUGIN_PATH, "cache", "ribbons")

# ── Find an Arabic-capable TTF on the box ─────────────────────────────
# On most Enigma2 images the "Regular" family already resolves to an
# Arabic font, but PIL needs the actual file path. Run
#     find / -iname "*arabic*.ttf" -o -iname "*noto*.ttf" 2>/dev/null
# and add any hits here.
_FONT_CANDIDATES = [
    os.path.join(PLUGIN_PATH, "fonts", "NotoNaskhArabic-Bold.ttf"),
    os.path.join(PLUGIN_PATH, "fonts", "Bahij_TheSansArabic-Black.ttf"),
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]

_font_cache = {}

def _find_font(size):
    key = int(size)
    if key in _font_cache:
        return _font_cache[key]
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _font_cache[key] = f
                return f
            except Exception:
                continue
    _font_cache[key] = None
    return None


def _hex_rgba(h):
    h = (h or "").lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    if len(h) == 8:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
    return (255, 67, 76, 255)


def _text_size(draw, text, font):
    try:
        return draw.textsize(text, font=font)             # Pillow < 10
    except AttributeError:
        bbox = draw.textbbox((0, 0), text, font=font)     # Pillow >= 10
        return bbox[2] - bbox[0], bbox[3] - bbox[1]


def render_ribbon(text, size, bg="#FF434C", fg="#FFFFFF", font_size=None):
    """Return the on-disk path of a cached ribbon PNG for `text`.

    size      — (w, h) of the target corner region on the poster, e.g.
                (POSTER_W*0.55, POSTER_H*0.22).
    Returns   "" if PIL/font unavailable or text is empty.
    """
    if not _PIL_OK or not text:
        return ""
    text = str(text).strip()
    if not text:
        return ""

    w, h = int(size[0]), int(size[1])
    fs = int(font_size) if font_size else max(14, int(h * 0.55))

    cache_key = hashlib.sha1(
        ("%s|%dx%d|%s|%s|%d" % (text, w, h, bg, fg, fs)).encode("utf-8")
    ).hexdigest()[:16]
    out = os.path.join(_CACHE_DIR, "%s.png" % cache_key)
    if os.path.exists(out):
        return out

    font = _find_font(fs)
    if not font:
        return ""

    try:
        try:
            os.makedirs(_CACHE_DIR)
        except OSError:
            pass

        bg_rgba = _hex_rgba(bg)
        fg_rgba = _hex_rgba(fg)

        # 1) horizontal strip, long enough to span the corner diagonally
        diag  = int((w * w + h * h) ** 0.5)
        strip_w = diag + 80
        strip_h = fs + 14
        strip = Image.new("RGBA", (strip_w, strip_h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(strip)
        sd.rectangle([0, 0, strip_w, strip_h], fill=bg_rgba)

        tw, th = _text_size(sd, text, font)
        sd.text(
            ((strip_w - tw) // 2, (strip_h - th) // 2 - 1),
            text, font=font, fill=fg_rgba,
        )

        # 2) rotate CCW 45° → "/" diagonal (matches CSS rotate(-45deg))
        rot = strip.rotate(45, expand=True, resample=Image.BICUBIC)

        # 3) paste so the band crosses the top-left corner of the canvas
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        cx = w // 4
        cy = h // 4
        canvas.paste(rot, (cx - rot.width // 2, cy - rot.height // 2), rot)

        canvas.save(out, "PNG")
        return out
    except Exception:
        return ""