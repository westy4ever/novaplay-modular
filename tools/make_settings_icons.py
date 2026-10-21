#!/usr/bin/env python3
"""Draws the line icons used by the Settings screen (images/settings/*.png).

Run on a PC with Pillow:  python3 tools/make_settings_icons.py
Each icon is drawn at 4x and scaled down for smooth edges. Sidebar icons
come in two colours (_on = gold, _off = grey); card icons are gold.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images", "settings")
GOLD = (242, 194, 48, 255)
GREY = (163, 173, 187, 255)
SS = 4
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _canvas(px):
    n = px * SS
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im), n


def _text(d, n, s, col, frac=0.5):
    f = ImageFont.truetype(BOLD, int(n * frac))
    b = d.textbbox((0, 0), s, font=f)
    d.text(((n - (b[2] - b[0])) / 2 - b[0], (n - (b[3] - b[1])) / 2 - b[1]), s, font=f, fill=col)


def draw(name, n, d, c):
    w = max(2, n // 12)                     # stroke
    m = n * 0.14                            # margin
    L, T, R, B = m, m, n - m, n - m
    cx, cy = n / 2, n / 2
    if name == "play":
        d.polygon([(L + n * .08, T), (R, cy), (L + n * .08, B)], fill=c)
    elif name == "cc":
        d.rounded_rectangle([L - n * .04, T + n * .08, R + n * .04, B - n * .08], radius=n * .12, outline=c, width=w)
        _text(d, n, "CC", c, .30)
    elif name == "globe":
        d.ellipse([L, T, R, B], outline=c, width=w)
        d.ellipse([cx - n * .16, T, cx + n * .16, B], outline=c, width=w)
        d.line([L, cy, R, cy], fill=c, width=w)
    elif name == "star":
        pts = []
        for i in range(10):
            r = (R - L) / 2 + n * .04 if i % 2 == 0 else (R - L) / 4.4
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a) + n * .03))
        d.line(pts + [pts[0]], fill=c, width=w, joint="curve")
    elif name == "tools":
        ro = (R - L) / 2
        for i in range(8):
            a = i * math.pi / 4
            d.line([cx + ro * .55 * math.cos(a), cy + ro * .55 * math.sin(a),
                    cx + ro * math.cos(a), cy + ro * math.sin(a)], fill=c, width=int(n * .13))
        d.ellipse([cx - ro * .72, cy - ro * .72, cx + ro * .72, cy + ro * .72], fill=c)
        d.ellipse([cx - ro * .3, cy - ro * .3, cx + ro * .3, cy + ro * .3], fill=(0, 0, 0, 0))
    elif name == "info":
        d.ellipse([L, T, R, B], outline=c, width=w)
        d.line([cx, cy - n * .06, cx, B - n * .16], fill=c, width=w + 1)
        d.ellipse([cx - w, T + n * .12, cx + w, T + n * .12 + 2 * w], fill=c)
    elif name == "hd":
        d.rounded_rectangle([L - n * .04, T + n * .1, R + n * .04, B - n * .1], radius=n * .1, outline=c, width=w)
        _text(d, n, "HD", c, .30)
    elif name == "next":
        d.polygon([(L, T + n * .06), (R - n * .16, cy), (L, B - n * .06)], fill=c)
        d.rectangle([R - n * .12, T + n * .06, R, B - n * .06], fill=c)
    elif name == "ask":
        d.rounded_rectangle([L, T, R, B - n * .16], radius=n * .14, outline=c, width=w)
        d.polygon([(L + n * .16, B - n * .17), (L + n * .16, B), (L + n * .36, B - n * .17)], fill=c)
        _text(d, n, "?", c, .38)
    elif name == "skip":
        for dx in (0, n * .26):
            d.line([L + dx, T + n * .06, cx + dx - n * .04, cy, L + dx, B - n * .06], fill=c, width=w + 1, joint="curve")
    elif name == "pick":
        d.rounded_rectangle([L, T, R, B], radius=n * .12, outline=c, width=w)
        d.line([L + n * .14, cy, cx - n * .04, B - n * .18, R - n * .12, T + n * .2], fill=c, width=w + 1, joint="curve")
    elif name == "studio":
        _text(d, n, "Aa", c, .44)
    elif name == "sliders":
        for i, k in enumerate((.35, .65, .45)):
            y = T + n * .08 + i * (B - T - n * .16) / 2
            d.line([L, y, R, y], fill=c, width=w)
            x = L + (R - L) * k
            d.ellipse([x - n * .08, y - n * .08, x + n * .08, y + n * .08], fill=c)
    elif name == "key":
        d.ellipse([L, cy - n * .2, L + n * .4, cy + n * .2], outline=c, width=w)
        d.line([L + n * .4, cy, R, cy], fill=c, width=w)
        d.line([R - n * .02, cy, R - n * .02, cy + n * .16], fill=c, width=w)
        d.line([R - n * .16, cy, R - n * .16, cy + n * .12], fill=c, width=w)
    elif name == "shield":
        d.polygon([(cx, T), (R, T + n * .12), (R - n * .06, cy + n * .1), (cx, B), (L + n * .06, cy + n * .1), (L, T + n * .12)], outline=c, width=w)
    elif name == "magnet":
        d.arc([L, T + n * .1, R, B + n * .05], 0, 180, fill=c, width=n // 7)
        d.rectangle([L, T, L + n // 7, cy + n * .14], fill=c)
        d.rectangle([R - n // 7, T, R, cy + n * .14], fill=c)
    elif name == "adult":
        d.ellipse([L, T, R, B], outline=c, width=w)
        _text(d, n, "18+", c, .26)
    elif name == "grid":
        g = n * .06
        h = (R - L - g) / 2
        for i in range(2):
            for j in range(2):
                x, y = L + i * (h + g), T + j * (h + g)
                d.rounded_rectangle([x, y, x + h, y + h], radius=n * .04, outline=c, width=w)
    elif name == "contrast":
        d.ellipse([L, T, R, B], outline=c, width=w)
        d.pieslice([L, T, R, B], 90, 270, fill=c)
    elif name == "home":
        d.line([L, cy, cx, T, R, cy], fill=c, width=w + 1, joint="curve")
        d.line([L + n * .12, cy - n * .06, L + n * .12, B, R - n * .12, B, R - n * .12, cy - n * .06], fill=c, width=w, joint="curve")
        d.rectangle([cx - n * .08, B - n * .26, cx + n * .08, B], fill=c)
    elif name == "search":
        d.ellipse([L, T, L + n * .5, T + n * .5], outline=c, width=w + 1)
        d.line([L + n * .44, T + n * .44, R, B], fill=c, width=w + 3)
    elif name == "history":
        d.ellipse([L, T, R, B], outline=c, width=w)
        d.line([cx, T + n * .16, cx, cy, cx + n * .16, cy + n * .1], fill=c, width=w, joint="curve")
    elif name == "download":
        d.line([cx, T, cx, cy + n * .12], fill=c, width=w + 1)
        d.line([cx - n * .18, cy - n * .06, cx, cy + n * .14, cx + n * .18, cy - n * .06], fill=c, width=w + 1, joint="curve")
        d.line([L, cy + n * .14, L, B, R, B, R, cy + n * .14], fill=c, width=w, joint="curve")
    elif name == "torrent":
        d.line([cx - n * .14, T, cx - n * .14, B], fill=c, width=w)
        d.line([cx - n * .3, B - n * .16, cx - n * .14, B, cx + 2, B - n * .16], fill=c, width=w, joint="curve")
        d.line([cx + n * .14, B, cx + n * .14, T], fill=c, width=w)
        d.line([cx - 2, T + n * .16, cx + n * .14, T, cx + n * .3, T + n * .16], fill=c, width=w, joint="curve")
    elif name == "pulse":
        d.rounded_rectangle([L, T, R, B], radius=n * .12, outline=c, width=w)
        d.line([L + n * .06, cy, L + n * .22, cy, cx - n * .06, T + n * .16, cx + n * .06, B - n * .16,
                R - n * .22, cy, R - n * .06, cy], fill=c, width=w, joint="curve")
    elif name == "package":
        d.polygon([(cx, T), (R, T + n * .16), (R, B - n * .16), (cx, B), (L, B - n * .16), (L, T + n * .16)], outline=c, width=w)
        d.line([L, T + n * .16, cx, T + n * .32, R, T + n * .16], fill=c, width=w, joint="curve")
        d.line([cx, T + n * .32, cx, B], fill=c, width=w)
    elif name == "restart":
        d.arc([L, T, R, B], 300, 240, fill=c, width=w + 1)
        a = math.radians(240)
        px, py = cx + (R - L) / 2 * math.cos(a), cy + (B - T) / 2 * math.sin(a)
        d.polygon([(px - n * .14, py - n * .02), (px + n * .06, py - n * .12), (px + n * .04, py + n * .12)], fill=c)
    elif name == "transmission":
        d.ellipse([L, T, R, B], outline=c, width=w)
        d.line([cx - n * .1, T + n * .2, cx - n * .1, B - n * .2], fill=c, width=w)
        d.line([cx - n * .2, B - n * .3, cx - n * .1, B - n * .2, cx, B - n * .3], fill=c, width=w, joint="curve")
        d.line([cx + n * .1, B - n * .2, cx + n * .1, T + n * .2], fill=c, width=w)
        d.line([cx, T + n * .3, cx + n * .1, T + n * .2, cx + n * .2, T + n * .3], fill=c, width=w, joint="curve")


SIDEBAR = ("play", "cc", "globe", "star", "tools", "info", "home", "search", "history", "download")
CARDS = ("home", "hd", "next", "ask", "skip", "pick", "cc", "studio", "sliders", "key", "shield",
         "magnet", "adult", "transmission", "grid", "contrast", "download", "pulse",
         "package", "restart")


def render(name, px, col):
    im, d, n = _canvas(px)
    draw(name, n, d, col)
    return im.resize((px, px), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    for nm in SIDEBAR:
        render(nm, 36, GOLD).save(os.path.join(OUT, "side_%s_on.png" % nm))
        render(nm, 36, GREY).save(os.path.join(OUT, "side_%s_off.png" % nm))
    for nm in CARDS:
        render(nm, 44, GOLD).save(os.path.join(OUT, "card_%s.png" % nm))
    print("icons ->", OUT)


if __name__ == "__main__":
    main()
