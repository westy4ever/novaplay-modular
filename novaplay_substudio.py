# -*- coding: utf-8 -*-
"""
novaplay_substudio.py — Subtitle Studio: custom subtitle renderer.
====================================================================
Renders .srt subtitles as player-screen Labels (not the service layer),
enabling live styling: size, color, background box, position, spacing —
and instant sync offset (no shifted-file rewrites).

Architecture:
  * STUDIO is a module singleton. It is *bound* to the player screen
    (STUDIO.bind(screen)) — the player owns the 250ms tick timer and
    calls STUDIO.update(elapsed_ms) with its own wall-clock position
    (pause-aware, seek-aware — reuses the position tracker's globals).
  * novaplay_subtitles.apply_subtitle() routes here when config
    "substudio_mode" is on; falls back to the service path for non-SRT
    or on any failure.
  * Style lives in memory and is flushed to plugin_state config only
    when the overlay closes (one flash write per session, not per key).

Arabic: rendered through the same eLabel path the whole UI already uses
(Arabic labels work on this image — verified by every screen in it).
"""

import os
import re
import bisect
import json
import logging

try:
    from plugin_state import _get_config, _set_config
except Exception:
    _get_config = lambda k, d="": d
    _set_config = lambda k, v: None

try:
    from enigma import gFont, ePoint, eSize
except Exception:
    gFont = ePoint = eSize = None

try:
    from skin import parseColor
except Exception:
    parseColor = None

# ── Native Shadow Detection (runs at import time inside Enigma2) ──
_NATIVE_SHADOW = False
_FONT_METRICS = False

try:
    from enigma import eLabel as _shadow_test_label
    _test = _shadow_test_label()
    _NATIVE_SHADOW = hasattr(_test, "setShadowColor") and hasattr(_test, "setShadowOffset")
except Exception:
    _NATIVE_SHADOW = False

try:
    from enigma import fontRenderClass as _fontRenderClass
    _FONT_METRICS = True
except Exception:
    _FONT_METRICS = False

# ── Setup logger ──────────────────────────────────────────────────────
logger = logging.getLogger("SubtitleStudio")

_CUE_RE = re.compile(
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')
_MAX_CUES = 5000
_COLORS = [
    ("#00FFFFFF", "White"),      # White
    ("#00FFFF80", "Yellow"),     # Yellow
    ("#00FFD740", "Gold"),       # Gold
    ("#00A7F3FF", "Cyan"),       # Cyan
    ("#00FF6B6B", "Red"),        # Red
    ("#006BFF6B", "Green"),      # Green
]
_FALLBACK_FONT = "Regular"
_OUTLINE_DIRS = [(-2, -2), (-2, 0), (-2, 2),
                 (0, -2),           (0, 2),
                 (2, -2),  (2, 0),  (2, 2)]


def _ts_ms(h, m, s, ms):
    """Convert time components to milliseconds."""
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms))


def parse_srt(path):
    """
    Tolerant SRT parser → sorted [(start_ms, end_ms, [lines], pos, italic)].
    Handles various encoding issues and malformed timestamps.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except Exception as e:
        logger.warning(f"Failed to read subtitle file: {e}")
        return []
    
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            text = None
    
    if text is None:
        logger.warning(f"Could not decode subtitle file: {path}")
        return []
    
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    
    cues = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.split("\n")
        i = 0
        while i < len(lines) and not _CUE_RE.search(lines[i]):
            i += 1
        if i >= len(lines):
            continue
        
        m = _CUE_RE.search(lines[i])
        if not m:
            continue
        
        try:
            start = _ts_ms(*m.groups()[:4])
            end = _ts_ms(*m.groups()[4:])
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping malformed timestamp: {e}")
            continue
        
        # v3: positioning tags (X1:/X2:/Y1:/Y2:) may sit on the timing
        # line or the line right after it
        pos_line = lines[i]
        body_start = i + 1
        if i + 1 < len(lines) and not _CUE_RE.search(lines[i + 1]) \
                and re.search(r"\b[XY]\d\s*:", lines[i + 1]):
            pos_line = lines[i] + " " + lines[i + 1]
            body_start = i + 2
        pos = {}
        for k in ("X1", "X2", "Y1", "Y2"):
            pm = re.search(r"\b%s\s*:\s*(-?\d+)" % k, pos_line, re.I)
            if pm:
                pos[k] = int(pm.group(1))
        raw_body = [l.strip() for l in lines[body_start:] if l.strip()]
        # v3: whole-cue italic detection BEFORE tag stripping
        italic = bool(re.fullmatch(r"\s*<i>.*</i>\s*", " ".join(raw_body), re.S | re.I))
        body = []
        for l in raw_body:
            l = re.sub(r"<[^>]+>", "", l).strip()
            if l:
                body.append(l)
        if body and end > start:
            cues.append((start, end, body, pos, italic))
    
    # Sort by start time
    cues.sort(key=lambda c: c[0])
    
    # Remove duplicates (keep earliest)
    seen = set()
    unique_cues = []
    for cue in cues:
        key = (cue[0], tuple(cue[2]))
        if key not in seen:
            seen.add(key)
            unique_cues.append(cue)
    
    return unique_cues[:_MAX_CUES]


def _estimate_width(text, font_size):
    """
    Arabic-aware character width estimation.
    Uses per-character classes for better monospace approximation.
    """
    text = str(text or "")
    total = 0.0
    
    for ch in text:
        o = ord(ch)
        if ch.isspace():
            total += font_size * 0.35
        elif 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0x08A0 <= o <= 0x08FF:
            # Arabic script
            total += font_size * 0.82
        elif ch in ",.;:!?،؛()[]{}":
            total += font_size * 0.38
        else:
            total += font_size * 0.62
    
    return int(total)


class SubtitleStudio(object):
    """Main subtitle rendering engine."""
    
    LINE_AREA_X = 210          # matches the skin's subLine geometry
    LINE_AREA_W = 1500
    DEFAULT_LINE_HEIGHT = 60
    BG_PADDING = 45
    MIN_BG_WIDTH = 160
    FALLBACK_LINE_HEIGHT = 60
    _SHADOW_KEYS = tuple("subShadow%d_%d" % (li, di) 
                          for li in range(2) for di in range(8))

    def __init__(self):
        self._screen = None
        self._cues = []
        self._starts = []
        self._path = ""
        self._offset_ms = 0
        self._last_rendered = None
        self._active = False
        self._current_cue_index = -1
        self._preview_body = None
        self._font_state = None
        self._shadow_logged = False
        
        self.style = {
            "size": 38,
            "color_idx": 0,
            "bg": True,
            "bg_alpha": 128,
            "offset_y": 0,
            "spacing": 28,
            "font_name": "Regular",
            "font_path": "",
            "outline": True,
            "outline_color_idx": 0,
            "align": 1,            # 0=top, 1=bottom(default), 2=center
            # v3
            "auto_wrap": True,     # reflow long cues into 2 balanced lines
            "use_cue_pos": True,   # honor SRT X/Y cue positions
        }
        self._load_style()
        self._validate_colors()
        
        # Log native shadow status once
        logger.info(f"SubtitleStudio: native shadow = {_NATIVE_SHADOW}, font metrics = {_FONT_METRICS}")

    # ── Validation ──────────────────────────────────────────────────────
    def _validate_colors(self):
        """Ensure color_idx is within valid range."""
        if self.style["color_idx"] >= len(_COLORS):
            self.style["color_idx"] = 0
        if self.style["outline_color_idx"] >= len(_COLORS):
            self.style["outline_color_idx"] = 0

    # ── Style Helper Methods ───────────────────────────────────────────
    def _style_color_hex(self):
        return _COLORS[self.style["color_idx"]][0]

    def _style_outline_hex(self):
        return _COLORS[self.style["outline_color_idx"]][0]

    def _style_bg_hex(self):
        return "#%02X000000" % max(0, min(255, int(self.style["bg_alpha"])))

    def _style_outline_on(self):
        return self.style["outline"] and not self.style["bg"]

    # ── Fix B: Proper line height ──────────────────────────────────────
    def _get_line_height(self, size):
        """Get line height using font metrics if available."""
        if _FONT_METRICS:
            try:
                from enigma import fontRenderClass, gFont as _gFont
                font = _gFont("Regular", size)
                h = fontRenderClass.getInstance().getLineHeight(font)
                if h and h > 0:
                    return max(60, h)
            except Exception:
                pass
        return max(60, size + 26)

    # ── Persistence ────────────────────────────────────────────────────
    def _load_style(self):
        """Load saved style preferences from plugin state."""
        try:
            size = _get_config("substudio_size", 38)
            self.style["size"] = int(size) if size and str(size).isdigit() else 38
            
            color = _get_config("substudio_color", 0)
            self.style["color_idx"] = int(color) if color and str(color).isdigit() else 0
            self.style["color_idx"] %= len(_COLORS)
            
            bg_val = _get_config("substudio_bg", "true")
            self.style["bg"] = str(bg_val).lower() in ("true", "1", "yes", "on")
            
            alpha = _get_config("substudio_alpha", 128)
            self.style["bg_alpha"] = int(alpha) if alpha and str(alpha).isdigit() else 128
            self.style["bg_alpha"] = max(0, min(255, self.style["bg_alpha"]))
            
            offy = _get_config("substudio_offy", 0)
            self.style["offset_y"] = int(offy) if offy and str(offy).lstrip('-').isdigit() else 0
            
            spacing = _get_config("substudio_spacing", 28)
            self.style["spacing"] = int(spacing) if spacing and str(spacing).isdigit() else 28
            
            font_name = _get_config("substudio_fontname", "Regular")
            self.style["font_name"] = str(font_name) if font_name else "Regular"
            
            font_path = _get_config("substudio_fontpath", "")
            self.style["font_path"] = str(font_path) if font_path else ""
            
            outline = _get_config("substudio_outline", "true")
            self.style["outline"] = str(outline).lower() in ("true", "1", "yes", "on")
            
            outline_color = _get_config("substudio_outlinecolor", 0)
            self.style["outline_color_idx"] = int(outline_color) if outline_color and str(outline_color).isdigit() else 0
            self.style["outline_color_idx"] %= len(_COLORS)
            
            align = _get_config("substudio_align", 1)
            self.style["align"] = int(align) if align and str(align).isdigit() else 1
            self.style["align"] %= 3
            
            self.style["auto_wrap"] = str(_get_config("substudio_autowrap", "true")).lower() == "true"
            self.style["use_cue_pos"] = str(_get_config("substudio_cuepos", "true")).lower() == "true"
        except Exception as e:
            logger.warning(f"Error loading style: {e}")

    def flush_style(self):
        """Save current style preferences to plugin state."""
        try:
            _set_config("substudio_size", str(self.style["size"]))
            _set_config("substudio_color", str(self.style["color_idx"]))
            _set_config("substudio_bg", "true" if self.style["bg"] else "false")
            _set_config("substudio_alpha", str(self.style["bg_alpha"]))
            _set_config("substudio_offy", str(self.style["offset_y"]))
            _set_config("substudio_spacing", str(self.style["spacing"]))
            _set_config("substudio_fontname", str(self.style["font_name"]))
            _set_config("substudio_fontpath", str(self.style["font_path"]))
            _set_config("substudio_outline", "true" if self.style["outline"] else "false")
            _set_config("substudio_outlinecolor", str(self.style["outline_color_idx"]))
            _set_config("substudio_align", str(self.style["align"]))
            _set_config("substudio_autowrap", "true" if self.style["auto_wrap"] else "false")
            _set_config("substudio_cuepos", "true" if self.style["use_cue_pos"] else "false")
            logger.debug("Style flushed to config")
        except Exception as e:
            logger.warning(f"Error flushing style: {e}")

    def reset_style(self):
        """Reset all style settings to defaults."""
        self.style = {
            "size": 38,
            "color_idx": 0,
            "bg": True,
            "bg_alpha": 128,
            "offset_y": 0,
            "spacing": 28,
            "font_name": "Regular",
            "font_path": "",
            "outline": True,
            "outline_color_idx": 0,
            "align": 1,
            "auto_wrap": True,
            "use_cue_pos": True
        }
        self._last_rendered = None
        self._preview_body = None
        self._layout_static()
        self.flush_style()
        logger.info("Style reset to defaults")

    # ── v3: style presets ──────────────────────────────────────────────
    def save_preset(self, slot):
        try:
            _set_config("substudio_preset_%s" % str(slot), json.dumps(self.style))
            return True
        except Exception:
            return False

    def load_preset(self, slot):
        raw = ""
        try:
            raw = str(_get_config("substudio_preset_%s" % str(slot), "") or "")
        except Exception:
            raw = ""
        if not raw:
            return False
        try:
            st = json.loads(raw)
            if isinstance(st, dict):
                for k in self.style:
                    if k in st:
                        self.style[k] = st[k]
                self._last_rendered = None
                self._preview_body = None
                self._layout_static()
                return True
        except Exception:
            pass
        return False

    # ── Binding / Attach ───────────────────────────────────────────────
    def bind(self, screen):
        """Bind studio to a player screen instance."""
        self._screen = screen
        self._last_rendered = None
        self._preview_body = None
        self._active = True
        self._layout_static()
        logger.debug("Studio bound to screen")

    def unbind(self):
        """Unbind from screen and clean up."""
        self._hide_lines()
        self._screen = None
        self._active = False
        logger.debug("Studio unbound from screen")

    def attach(self, path):
        """
        Parse and activate subtitle file.
        Returns False for non-SRT / unreadable (caller falls back to service renderer).
        """
        path = str(path or "")
        if not path.lower().endswith(".srt"):
            logger.debug(f"Not an SRT file: {path}")
            return False
        
        cues = parse_srt(path)
        if not cues:
            logger.warning(f"No valid cues found in: {path}")
            return False
        
        self._cues = cues
        self._starts = [c[0] for c in cues]
        self._path = path
        self._last_rendered = None
        self._preview_body = None
        self._current_cue_index = -1
        logger.info(f"Attached {len(cues)} cues from: {path}")
        return True

    def attach_with_offset(self, path, offset_ms=0):
        """Attach subtitle file with initial offset."""
        if self.attach(path):
            self._offset_ms = int(offset_ms or 0)
            return True
        return False

    def set_offset(self, offset_ms):
        """Set subtitle timing offset."""
        new_offset = int(offset_ms or 0)
        if new_offset != self._offset_ms:
            self._offset_ms = new_offset
            self._last_rendered = None   # force re-render
            logger.debug(f"Offset set to {new_offset}ms")

    def get_offset(self):
        """Get current subtitle timing offset."""
        return self._offset_ms

    def detach(self):
        """Detach current subtitle and clean up."""
        self._cues = []
        self._starts = []
        self._path = ""
        self._offset_ms = 0
        self._last_rendered = None
        self._preview_body = None
        self._current_cue_index = -1
        self._hide_lines()
        logger.debug("Subtitle detached")

    def is_attached(self):
        """Check if a subtitle is loaded."""
        return bool(self._cues)

    def get_path(self):
        """Get current subtitle file path."""
        return self._path

    def get_cue_count(self):
        """Get number of loaded cues."""
        return len(self._cues)

    # ── Rendering ──────────────────────────────────────────────────────
    def _w(self, key):
        """Safely get widget by key."""
        try:
            return self._screen[key]
        except Exception:
            return None

    def _hide_lines(self):
        """Hide all subtitle-related widgets."""
        for key in ("subBg1", "subBg2", "subLine1", "subLine2"):
            w = self._w(key)
            if w is not None:
                try:
                    w.hide()
                except Exception as e:
                    logger.debug(f"Error hiding {key}: {e}")
        for key in self._SHADOW_KEYS:
            w = self._w(key)
            if w is not None:
                try:
                    w.hide()
                except Exception:
                    pass

    def _y_base(self):
        """Calculate base Y position based on alignment setting."""
        if self.style["align"] == 0:  # Top
            return 100 + self.style["offset_y"]
        elif self.style["align"] == 2:  # Center
            return 500 + self.style["offset_y"]
        else:  # Bottom (default)
            return 812 + self.style["offset_y"]

    def _fallback_font(self):
        """Return font name (custom font path or default)."""
        if self.style["font_path"] and os.path.exists(self.style["font_path"]):
            return self.style["font_name"]
        return _FALLBACK_FONT

    def _apply_font_all(self, override_name=None):
        """Set the font face on every text widget (lines + shadows).
        Uses native shadow (AJ Panel style) when available, 16-widget fallback when not."""
        if self._screen is None or gFont is None:
            return
        fname = override_name or self._fallback_font()
        size = self.style["size"]
        self._font_state = (fname, size)
        
        try:
            from skin import parseColor as _pc
        except Exception:
            _pc = None

        if _NATIVE_SHADOW:
            # Native shadow mode: apply shadow directly to the 2 line widgets
            for key in ("subLine1", "subLine2"):
                w = self._w(key)
                if w is not None and w.instance is not None:
                    w.instance.setFont(gFont(fname, size))
                    if _pc is not None:
                        w.instance.setForegroundColor(_pc(self._style_color_hex()))
                        w.instance.setShadowColor(_pc(self._style_outline_hex()))
                        w.instance.setShadowOffset((-2, -2))
            
            # Apply background colors
            if _pc is not None:
                bg = self._style_bg_hex()
                for key in ("subBg1", "subBg2"):
                    w = self._w(key)
                    if w is not None and w.instance is not None:
                        w.instance.setBackgroundColor(_pc(bg))
            
            # Permanently hide the 16 shadow widgets
            for key in self._SHADOW_KEYS:
                w = self._w(key)
                if w is not None:
                    try:
                        w.hide()
                    except Exception:
                        pass
        else:
            # Fallback: 16-widget shadow stack
            for key in ("subLine1", "subLine2") + tuple(self._SHADOW_KEYS):
                w = self._w(key)
                if w is not None and w.instance is not None:
                    w.instance.setFont(gFont(fname, size))
                    if _pc is not None:
                        w.instance.setForegroundColor(_pc(self._style_color_hex()))
            
            if _pc is not None:
                bg = self._style_bg_hex()
                for key in ("subBg1", "subBg2"):
                    w = self._w(key)
                    if w is not None and w.instance is not None:
                        w.instance.setBackgroundColor(_pc(bg))

    def _layout_static(self):
        """Apply font, size, and color settings."""
        scr = self._screen
        if scr is None or gFont is None:
            return
        
        try:
            self._apply_font_all(None)
            logger.debug("Static layout applied")
        except Exception as e:
            logger.warning(f"Error applying static layout: {e}")

    def _wrap_body(self, body, size):
        """v3: reflow the cue body into <=2 lines that fit LINE_AREA_W.
        Balanced split minimizes the wider line. auto_wrap off = legacy
        behavior (author's split, 3+ lines merged)."""
        max_w = self.LINE_AREA_W - 90
        if len(body) == 1:
            if _estimate_width(body[0], size) <= max_w or not self.style.get("auto_wrap", True):
                return [body[0], ""]
        elif not self.style.get("auto_wrap", True):
            return [body[0], " ".join(body[1:])]
        elif (_estimate_width(body[0], size) <= max_w
                and _estimate_width(" ".join(body[1:]), size) <= max_w
                and len(body) == 2):
            return [body[0], body[1]]
        # (re)wrap: balanced split across all body text
        text = " ".join(body)
        words = text.split(" ")
        best = None
        for cut in range(1, len(words)):
            a = " ".join(words[:cut])
            b = " ".join(words[cut:])
            wa = _estimate_width(a, size)
            wb = _estimate_width(b, size)
            if wa <= max_w and wb <= max_w:
                if best is None or max(wa, wb) < best[0]:
                    best = (max(wa, wb), a, b)
        if best:
            return [best[1], best[2]]
        mid = max(1, len(words) // 2)
        return [" ".join(words[:mid]), " ".join(words[mid:])]

    def _render_cue(self, body, pos=None, italic=False):
        size = self.style["size"]
        line1, line2 = self._wrap_body(body, size)
        # Fix B: Use _get_line_height instead of hardcoded size+26
        line_h = self._get_line_height(size)
        gap = self.style["spacing"]
        y1 = self._y_base()
        x = self.LINE_AREA_X
        # v3: per-cue SRT positioning — SubRip's reference grid is
        # 384x288; scale to 1920x1080. Override toggle in the overlay.
        if pos and self.style.get("use_cue_pos", True):
            try:
                if "Y1" in pos:
                    y1 = max(20, min(920, int(pos["Y1"] * 1080.0 / 288.0)) + self.style["offset_y"])
                if "X1" in pos and "X2" in pos:
                    cx = (float(pos["X1"]) + float(pos["X2"])) / 2.0 * (1920.0 / 384.0)
                    x = int(max(0, min(1920 - self.LINE_AREA_W, cx - self.LINE_AREA_W / 2.0)))
            except Exception:
                pass
        y2 = y1 + line_h + gap
        # Font pass — ONLY when the font state actually changed.
        want = "Italic" if italic else None
        new_font = (want or self._fallback_font(), size)
        if getattr(self, "_font_state", None) != new_font:
            self._apply_font_all(want)
        outline_on = self._style_outline_on()
        spec = [(line1, "subLine1", "subBg1", y1),
                (line2, "subLine2", "subBg2", y2)]
        for li, (text, lkey, bkey, y) in enumerate(spec):
            lw = self._w(lkey)
            bw = self._w(bkey)
            if lw is None or lw.instance is None:
                continue
            if not text:
                try:
                    lw.hide()
                    if bw is not None:
                        bw.hide()
                    # No text = hide all 8 shadows in one pass
                    for di in range(8):
                        sw = self._w("subShadow%d_%d" % (li, di))
                        if sw is not None:
                            sw.hide()
                except Exception:
                    pass
                continue
            try:
                lw.setText(text)
                lw.instance.move(ePoint(x, y))
                lw.instance.resize(eSize(self.LINE_AREA_W, line_h))
                lw.show()
                # Shadow pass — if native shadow is on, the 16 widgets are already hidden
                # If not, use the fallback shadow stack
                if not _NATIVE_SHADOW:
                    if not outline_on:
                        for di in range(8):
                            sw = self._w("subShadow%d_%d" % (li, di))
                            if sw is not None and sw.instance is not None:
                                sw.hide()
                    else:
                        for di, (dx, dy) in enumerate(_OUTLINE_DIRS):
                            sw = self._w("subShadow%d_%d" % (li, di))
                            if sw is not None and sw.instance is not None:
                                sw.setText(text)
                                sw.instance.move(ePoint(x + dx, y + dy))
                                sw.instance.resize(eSize(self.LINE_AREA_W, line_h))
                                sw.show()
                if bw is not None and bw.instance is not None:
                    if self.style["bg"]:
                        pad = 45
                        w = _estimate_width(text, size) + pad * 2
                        w = max(160, min(self.LINE_AREA_W, w))
                        bx = x + (self.LINE_AREA_W - w) // 2
                        bw.instance.move(ePoint(bx, y))
                        bw.instance.resize(eSize(w, line_h))
                        bw.show()
                    else:
                        bw.hide()
            except Exception:
                pass

    def update(self, pos_ms):
        """
        Called by the player's timer with current position.
        Updates subtitle display based on timing.
        """
        if self._screen is None or not self._cues:
            if self._last_rendered is not None:
                self._last_rendered = None
                self._preview_body = None
                self._hide_lines()
            return
        
        p = pos_ms + self._offset_ms
        idx = bisect.bisect_right(self._starts, p) - 1
        active = None
        j = idx
        while j >= 0 and j > idx - 5:
            s, e, body, pos, italic = self._cues[j]
            if s <= p < e:
                active = (s, e, body, pos, italic)
                break
            if e < p - 8000:
                break
            j -= 1
        
        self._preview_body = active[2] if active else None
        key = None
        if active:
            key = (active[0], active[1], tuple(active[2]),
                   tuple(sorted((active[3] or {}).items())), bool(active[4]))
        
        if key != self._last_rendered:
            self._last_rendered = key
            if active:
                self._render_cue(active[2], active[3], active[4])
                self._current_cue_index = idx if active else -1
            else:
                self._hide_lines()
                self._current_cue_index = -1

    def get_preview(self):
        """Current cue body for the overlay's live preview strip —
        the active cue, or the file's first cue before anything shows."""
        if self._preview_body:
            return self._preview_body
        if self._cues:
            return self._cues[0][2]
        return []

    def get_current_cue(self):
        """Get the currently displayed cue text, if any."""
        if self._last_rendered and len(self._last_rendered) >= 3:
            return self._last_rendered[2]
        return None

    def get_current_cue_index(self):
        """Get the current cue index."""
        return self._current_cue_index

    def jump_to_cue(self, index):
        """
        Jump to a specific cue index.
        Useful for seeking to subtitle positions.
        """
        if 0 <= index < len(self._cues):
            cue = self._cues[index]
            self._last_rendered = None  # Force update
            self._render_cue(cue[2], cue[3], cue[4])
            self._current_cue_index = index
            return True
        return False


# Module singleton instance
STUDIO = SubtitleStudio()