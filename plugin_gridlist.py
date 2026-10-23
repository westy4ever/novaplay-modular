# -*- coding: utf-8 -*-
"""NovaPlay -- screen scaling, skin scaler, site icons, animation settings.

Shared by every screen:
  sc(v) / scale_skin_xml(xml)   1080p design values -> the box's resolution
  resolve_icon_path(item)       a site's logo PNG
  POP_ANIM_* / anim_*()         Settings > Animations (Full / Light / Off)
  _has_arabic(text)

(The Classic home's grid widgets that lived here were removed with that
screen on 2026-10-23.)
"""

import os
import re

PLUGIN_PATH = os.path.dirname(__file__)

# ─── Screen metrics (design space = 1920x1080) ─────────────────────────────
try:
    from enigma import getDesktop
    _dsize = getDesktop(0).size()
    SCREEN_W, SCREEN_H = int(_dsize.width()), int(_dsize.height())
except Exception:
    SCREEN_W, SCREEN_H = 1920, 1080
if SCREEN_W < 400:
    SCREEN_W, SCREEN_H = 1920, 1080

_SCALE = max(0.5, min(2.0, SCREEN_W / 1920.0))


def sc(v):
    """Scale a design-space (1080p) pixel value to the current screen."""
    return int(round(v * _SCALE))


# ─── Generic skin scaler ────────────────────────────────────────────────────
_SKIN_NUM_RE = re.compile(r"-?\d+")
_SKIN_SCALE_ATTRS = ("position", "size", "cornerRadius", "itemHeight")


def scale_skin_xml(xml):
    if _SCALE == 1.0:
        return xml

    def _scale_attr(m):
        name, q, val = m.group(1), m.group(2), m.group(3)
        if name in _SKIN_SCALE_ATTRS:
            val = _SKIN_NUM_RE.sub(
                lambda n: str(int(round(int(n.group()) * _SCALE))), val)
        elif name == "font":
            parts = val.split(";")
            if len(parts) == 2 and parts[1].strip().isdigit():
                parts[1] = str(max(12, int(round(int(parts[1]) * _SCALE))))
                val = ";".join(parts)
        return "%s=%s%s%s" % (name, q, val, q)

    return re.sub(r'(\w+)=("|\')([^"\']*)\2', _scale_attr, xml)


# ─── Icon resolution ────────────────────────────────────────────────────────
_ICON_ALIASES = {"wecima_sarl": "wecima"}


def resolve_icon_path(item, plugin_path=PLUGIN_PATH):
    action = item.get("_action", "")
    icon_path = None
    if action.startswith("site_"):
        site_key = action.replace("site_", "")
        for cand in (site_key, _ICON_ALIASES.get(site_key, "")):
            if not cand:
                continue
            icon_file = os.path.join(plugin_path, "images", "%s.png" % cand)
            if os.path.exists(icon_file):
                icon_path = icon_file
                break
    if not icon_path:
        for fallback in (os.path.join(plugin_path, "plugin.png"),
                         os.path.join(plugin_path, "images", "default.png")):
            if os.path.exists(fallback):
                icon_path = fallback
                break
    return icon_path


# [pop-anim] eased pop-out animation on selection change
POP_ANIM_FRAMES    = 12                 # frames per pop-out animation (Full)
POP_ANIM_INTERVAL  = 15                 # ms between frames (12 * 15 = 180ms, small even steps)

# [anim] Settings > Animations: Full (12 steps) / Light (8 steps, poster
# pictures don't rescale while zooming) / Off (straight to the end).
# Read through a 3 s cache so animation frames don't hit the state file.
_ANIM_STEPS = {"full": POP_ANIM_FRAMES, "light": 8, "off": 1}
_anim_cache = [0.0, "full"]


def anim_mode():
    import time as _t
    now = _t.time()
    if now - _anim_cache[0] > 3.0:
        try:
            from plugin_state import _get_config
            m = str(_get_config("anim_mode", "full") or "full").lower()
        except Exception:
            m = "full"
        _anim_cache[0], _anim_cache[1] = now, (m if m in _ANIM_STEPS else "full")
    return _anim_cache[1]


def anim_frames():
    """steps per animation for the current mode (1 = no animation)"""
    return _ANIM_STEPS[anim_mode()]


def anim_light():
    """True when pictures should not be rescaled while they zoom"""
    return anim_mode() != "full"


def anim_reset():
    """forget the cached mode (Settings just changed it)"""
    _anim_cache[0] = 0.0


def _has_arabic(text):
    try:
        for ch in str(text or ""):
            if u"\u0600" <= ch <= u"\u06FF" or u"\u0750" <= ch <= u"\u077F":
                return True
    except Exception:
        pass
    return False
