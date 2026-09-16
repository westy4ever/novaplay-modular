# -*- coding: utf-8 -*-
"""
Advanced Arabic Player - Custom grid widgets (resolution-aware)
================================================================

Changes in this revision (UX refresh):
  * HOME_GRID_ROWS 3 -> 2: the home screen now hosts a Continue-
    Watching strip above the site grid (build_continue_row_xml +
    wiring in plugin.py). 17 sites now page as 8+8+1.
  * Blinking focus removed: PosterCardGrid no longer carries
    blink_state; the selected cell's frame is ALWAYS cyan. plugin.py
    deletes the two 450ms blink timers in the same revision — no
    more show/hide churn or focus-state repair code.
  * HomeMenuGrid draws per-site health dots in the top-right corner
    of each tile from item["_health"] (stamped by plugin_health.get
    in plugin.py): "ok" green / "down" red / "blocked" yellow;
    "unknown" or missing draws nothing, so a fresh install shows a
    clean grid. Dots are code-drawn rectangles using the same
    MultiContentEntryText backcolor trick as the cell borders — no
    new assets. They render in the listbox layer, under the site
    icon pixmaps, so they show through the logos' transparent
    corners.
  * NEW: build_continue_row_xml() + CONT_* constants — skin XML for
    the continue-watching strip. These are DESIGN-SPACE @1080p
    numbers matching the home skin's fixed (non-scaled) coordinates;
    see the note at the bottom of this file.

Changes in the previous revision (kept for reference):
  * Resolution-aware geometry: every constant is derived from the
    1920x1080 design by _SCALE = desktop_width / 1920. On 1280x720
    boxes the grids fit the screen; on 4K they grow. On a 1080p box
    _SCALE == 1.0 and every value is identical to the old file.
  * POSTER_GRID_COLS is computed from available width (8 at 1080p by
    design, 7 at 720p, 8 at 4K) instead of overflowing narrow screens.
  * scale_skin_xml(): generic skin-string scaler — wrap any screen's
    skin with it and its position/size/font numbers scale too.
  * resolve_icon_path(): site-key aliases (wecima_sarl -> wecima.png)
    + images/default.png fallback. Previously the fallback was
    plugin.png, which is not shipped — so 5 home tiles (yts, torrentio,
    vidsrc, imdb_su, wecima_sarl) rendered icon-less. default.png is
    shipped in this revision.
  * build_carousel_xml(): f-strings replaced with .format() — an
    f-string crashes at import on Python-2 images, while the rest of
    the codebase is deliberately py2-compatible.
  * Caption wrap width and carousel font sizes scale with the screen.

[PATCH 46-R2] web-card overlay badges on poster cards: red year box
  (top-left) + star rating box (top-right, yellow star on black).
  Rating source is universal — TMDB's _merge_tmdb_data fills "rating"
  for every site; YTS items carry it directly from TMDB listings.
  Carousel rating badge restyled gold to match.

[PATCH 48] in-poster watch-progress bar on grid cards (replaces the
  old RESUME text chip): dark track across the poster's bottom edge
  + gold fill proportional to watch state. Finished items show no
  bar — their poster itself is dimmed (home screen's _updatePosterPixmaps
  bakes a darkened variant). Position data arrives on the item dict
  (item["_watch_pos"] / item["_is_watched"], stamped by the home
  screen's paint loop).
"""

import os
import re
from plugin_util import _wrap_ui_text
from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText
from enigma import (eListboxPythonMultiContent, eListbox, gFont,
                    RT_HALIGN_CENTER, RT_HALIGN_LEFT, RT_HALIGN_RIGHT,
                    RT_VALIGN_CENTER)

# Import state to check for saved resume positions
from plugin_state import _get_saved_position

PLUGIN_PATH = os.path.dirname(__file__)

# ─── Screen metrics (design space = 1920x1080) ─────────────────────────────
try:
    from enigma import getDesktop
    _dsize = getDesktop(0).size()
    SCREEN_W, SCREEN_H = int(_dsize.width()), int(_dsize.height())
except Exception:
    SCREEN_W, SCREEN_H = 1920, 1080
if SCREEN_W < 400:  # defensive: desktop not ready at import time
    SCREEN_W, SCREEN_H = 1920, 1080

_SCALE = max(0.5, min(2.0, SCREEN_W / 1920.0))


def sc(v):
    """Scale a design-space (1080p) pixel value to the current screen."""
    return int(round(v * _SCALE))


# ─── Generic skin scaler ────────────────────────────────────────────────────
_SKIN_NUM_RE = re.compile(r"-?\d+")
_SKIN_SCALE_ATTRS = ("position", "size", "cornerRadius", "itemHeight")


def scale_skin_xml(xml):
    """Scale a skin string's geometry to the current screen.
    Scales numbers inside position="…", size="…", cornerRadius="…",
    itemHeight="…" attributes, and the size in font="Name;NN".
    zPosition / transparency / colors are left untouched. At _SCALE==1.0
    the string is returned unchanged."""
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


_G_CLR = {
    "surface2": "#1C2333", "border": "#30363D", "cyan": "#00E5FF",
    "text": "#F0F6FC", "text2": "#8B949E", "gold": "#FFD740", "badge_bg": "#000000",
}

# ─── Per-site health dot colors (home grid) ────────────────────────────────
# item["_health"] is stamped by plugin_health.get(key) in plugin.py.
# "unknown" is intentionally absent — a fresh/untested site draws nothing.
_HEALTH_DOT_COLORS = {"ok": "#39D98A", "down": "#F85149", "blocked": "#FFD740"}

# ─── Icon resolution ────────────────────────────────────────────────────────
# wecima_sarl ships no PNG of its own — alias it to the wecima icon.
# Add further aliases here as sites fork (e.g. "shahid4u": "shaheed").
_ICON_ALIASES = {
    "wecima_sarl": "wecima",
}


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
        # plugin.png was the old fallback but is not shipped; try it
        # anyway (harmless), then images/default.png.
        for fallback in (os.path.join(plugin_path, "plugin.png"),
                         os.path.join(plugin_path, "images", "default.png")):
            if os.path.exists(fallback):
                icon_path = fallback
                break
    return icon_path


def build_pixmap_widgets_xml(x0, y0, cols, rows, cell_w, cell_h, margin, border_w, icon_pad_top, icon_w, icon_h, name_prefix="pic"):
    parts = []
    for r in range(rows):
        for c in range(cols):
            px = x0 + c * cell_w + margin + border_w
            py = y0 + r * cell_h + margin + border_w + icon_pad_top
            parts.append('\n\t\t<widget name="%s_%d_%d" position="%d,%d" size="%d,%d" transparent="1" alphatest="blend" zPosition="3" />' % (name_prefix, r, c, px, py, icon_w, icon_h))
    return "".join(parts)


# --- Shared Engine ---
class _BaseCardGrid(GUIComponent):
    GUI_WIDGET = eListbox
    def __init__(self, cols, rows, cell_w, cell_h, font_size=24):
        GUIComponent.__init__(self)
        self.cols, self.rows, self.cell_w, self.cell_h = cols, rows, cell_w, cell_h
        self.itemsPerPage = cols * rows
        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("Regular", font_size))
        self.l.setFont(1, gFont("Regular", max(12, font_size - 6)))
        self._items = []
        self.currentPage = 0
        self.totalPages = 1
        self.currentRow = 0
        self.currentCol = 0
        self.currentIndex = 0
        self.totalItems = 0
        self.onSelectionChanged = None

    def _updatePageInfo(self):
        self.totalPages = max(1, (self.totalItems + self.itemsPerPage - 1) // self.itemsPerPage)
        if self.currentPage >= self.totalPages: self.currentPage = max(0, self.totalPages - 1)

    def _getPageStart(self): return self.currentPage * self.itemsPerPage
    def _getPageEnd(self): return min(self._getPageStart() + self.itemsPerPage, self.totalItems)
    def _getMaxRow(self):
        n = self._getPageEnd() - self._getPageStart()
        return 0 if n == 0 else (n - 1) // self.cols

    def _getMaxCol(self, row):
        n = self._getPageEnd() - self._getPageStart()
        rs = row * self.cols
        return -1 if rs >= n else min(self.cols - 1, n - rs - 1)

    def _updateIndex(self):
        if self.totalItems == 0:
            self.currentIndex = self.currentRow = self.currentCol = 0; return
        self.currentIndex = self.currentPage * self.itemsPerPage + self.currentRow * self.cols + self.currentCol
        if self.currentIndex >= self.totalItems:
            self.currentIndex = max(0, self.totalItems - 1)
            self.currentPage = self.currentIndex // self.itemsPerPage
            self.currentRow = (self.currentIndex % self.itemsPerPage) // self.cols
            self.currentCol = (self.currentIndex % self.itemsPerPage) % self.cols

    def _notify(self):
        if self.onSelectionChanged: self.onSelectionChanged()

    def moveUp(self):
        if self.currentRow > 0: self.currentRow -= 1
        elif self.currentPage > 0:
            self.currentPage -= 1; self.currentRow = self._getMaxRow()
            self.currentCol = min(self.currentCol, self._getMaxCol(self.currentRow))
        self._updateIndex(); self._redraw(); self._notify()

    def moveDown(self):
        mr = self._getMaxRow()
        if self.currentRow < mr:
            self.currentRow += 1; self.currentCol = min(self.currentCol, self._getMaxCol(self.currentRow))
        elif self.currentPage < self.totalPages - 1:
            self.currentPage += 1; self.currentRow = 0; self.currentCol = min(self.currentCol, self._getMaxCol(0))
        self._updateIndex(); self._redraw(); self._notify()

    def moveLeft(self):
        if self.currentCol > 0: self.currentCol -= 1
        elif self.currentPage > 0:
            self.currentPage -= 1; self.currentRow = min(self.currentRow, self._getMaxRow())
            self.currentCol = self._getMaxCol(self.currentRow)
        self._updateIndex(); self._redraw(); self._notify()

    def moveRight(self):
        mc = self._getMaxCol(self.currentRow)
        if self.currentCol < mc: self.currentCol += 1
        elif self.currentPage < self.totalPages - 1:
            self.currentPage += 1; self.currentRow = min(self.currentRow, self._getMaxRow())
            self.currentCol = 0
        self._updateIndex(); self._redraw(); self._notify()

    def setList(self, items):
        self._items = items or []
        self.totalItems = len(self._items)
        self._updatePageInfo()
        self.currentPage = self.currentRow = self.currentCol = 0
        self._updateIndex(); self._redraw(); self._notify()

    def getCurrent(self):
        if self._items and 0 <= self.currentIndex < len(self._items): return self._items[self.currentIndex]
        return None

    def getPageInfo(self): return self.currentPage + 1, self.totalPages
    def getPageItems(self):
        s = self._getPageStart(); e = self._getPageEnd()
        return [((i - s) // self.cols, (i - s) % self.cols, self._items[i]) for i in range(s, e)]

    def _buildRow(self, row_idx): raise NotImplementedError

    def _redraw(self):
        n = self._getPageEnd() - self._getPageStart()
        num_rows = max(1, (n + self.cols - 1) // self.cols) if self.totalItems > 0 else 0
        num_rows = max(1, num_rows)
        entries = [self._buildRow(r) for r in range(num_rows)]
        while len(entries) < self.rows: entries.append([None])
        self.l.setList(entries)
        if self.instance:
            try: self.instance.setSelectionEnable(False)
            except: pass
            if self.currentRow < len(entries): self.instance.moveSelectionTo(self.currentRow)

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setItemHeight(self.cell_h)
        try: instance.setSelectionEnable(False)
        except: pass
        try: instance.setScrollbarMode(eListbox.showOnDemand)
        except: instance.setScrollbarMode(1)

    def preWidgetDelete(self, instance): instance.setContent(None)


# --- Home site-menu grid (design: 4x3 cells of 470x300 @1080p) ---
# NOTE: rows reduced 3 -> 2 in the UX-refresh revision — the home screen
# now hosts the Continue-Watching strip above the grid.
HOME_GRID_COLS = 4
HOME_GRID_ROWS = 2
HOME_CELL_W = sc(470)
HOME_CELL_H = sc(300)
HOME_CELL_MARGIN = sc(16)
HOME_BORDER_W = max(2, sc(4))
HOME_CELL_INNER_W = HOME_CELL_W - 2 * HOME_CELL_MARGIN
HOME_CELL_INNER_H = HOME_CELL_H - 2 * HOME_CELL_MARGIN
HOME_LABEL_H = sc(44)
HOME_ICON_PAD_TOP = sc(12)
HOME_ICON_W = HOME_CELL_INNER_W - 2 * HOME_BORDER_W
HOME_ICON_H = max(1, HOME_CELL_INNER_H - 2 * HOME_BORDER_W - HOME_ICON_PAD_TOP - HOME_LABEL_H)
# text-box metrics used by HomeMenuGrid._buildRow
HOME_TITLE_H = sc(40)
HOME_TAG_STEP = sc(44)
HOME_TAG_H = sc(32)
HOME_TITLE_PAD = sc(8)

class HomeMenuGrid(_BaseCardGrid):
    def __init__(self):
        _BaseCardGrid.__init__(self, HOME_GRID_COLS, HOME_GRID_ROWS, HOME_CELL_W, HOME_CELL_H, font_size=max(16, sc(24)))

    def _buildRow(self, row_idx):
        start = self._getPageStart()
        is_sr = (row_idx == self.currentRow)
        row = [None]
        for col_idx in range(self.cols):
            item_idx = start + row_idx * self.cols + col_idx
            if item_idx >= self._getPageEnd(): continue
            cx = col_idx * self.cell_w + HOME_CELL_MARGIN
            cy = HOME_CELL_MARGIN
            item = self._items[item_idx]
            is_sel = is_sr and col_idx == self.currentCol
            bc = _G_CLR["cyan"] if is_sel else _G_CLR["border"]
            row.append(MultiContentEntryText(pos=(cx, cy), size=(HOME_CELL_INNER_W, HOME_CELL_INNER_H), font=0, text="", color=0, backcolor=bc, flags=0))
            row.append(MultiContentEntryText(pos=(cx + HOME_BORDER_W, cy + HOME_BORDER_W), size=(HOME_CELL_INNER_W - 2 * HOME_BORDER_W, HOME_CELL_INNER_H - 2 * HOME_BORDER_W), font=0, text="", color=0, backcolor=_G_CLR["surface2"], flags=0))
            # Health dot (top-right corner): green=ok, red=down,
            # yellow=blocked; "unknown" draws nothing. Rendered in the
            # listbox layer — the site icon pixmap paints on top, so the
            # dot shows through the logo's transparent corners.
            hstate = item.get("_health")
            if hstate in _HEALTH_DOT_COLORS:
                _ds = max(6, sc(14))
                _dx = cx + HOME_CELL_INNER_W - HOME_BORDER_W - _ds - sc(10)
                _dy = cy + HOME_BORDER_W + sc(10)
                row.append(MultiContentEntryText(pos=(_dx, _dy), size=(_ds, _ds), font=0, text="", color=0, backcolor=_HEALTH_DOT_COLORS[hstate], flags=0))
            title = item.get("title", "")
            tagline = item.get("tagline", "")
            ty = cy + HOME_BORDER_W + HOME_TITLE_PAD
            row.append(MultiContentEntryText(pos=(cx + HOME_BORDER_W + HOME_TITLE_PAD, ty), size=(HOME_CELL_INNER_W - 2 * HOME_BORDER_W - 2 * HOME_TITLE_PAD, HOME_TITLE_H), font=0, text=title, color=_G_CLR["text"], backcolor=_G_CLR["surface2"], flags=RT_HALIGN_CENTER))
            if tagline:
                tag_y = ty + HOME_TAG_STEP
                row.append(MultiContentEntryText(pos=(cx + HOME_BORDER_W + HOME_TITLE_PAD, tag_y), size=(HOME_CELL_INNER_W - 2 * HOME_BORDER_W - 2 * HOME_TITLE_PAD, HOME_TAG_H), font=0, text=tagline, color=_G_CLR["text2"], backcolor=_G_CLR["surface2"], flags=RT_HALIGN_CENTER))
        return row


# --- Plain vertical text list (categories / text-only listings) ---------
# The home screen's "list" mode used to render through home_grid (4x2
# tiles) — a grid wearing a list's name. This is a real single-column
# scroller on the same _BaseCardGrid engine (paging, cursor,
# onSelectionChanged come free). Skin entry name: "text_list".
# [PATCH 14]
LIST_ROWS = 10                        # visible rows (design @1080p)
LIST_CELL_W = sc(1840)
LIST_CELL_H = sc(83)
LIST_CELL_MARGIN = sc(6)
LIST_TAG_W = sc(200)                 # right-aligned tag/count column


def _has_arabic(text):
    """[PATCH 28] True if the string contains Arabic-script characters.
    Arabic rows right-align (RTL reading); Latin rows stay LTR."""
    try:
        for ch in str(text or ""):
            if u"\u0600" <= ch <= u"\u06FF" or u"\u0750" <= ch <= u"\u077F":
                return True
    except Exception:
        pass
    return False


class TextListGrid(_BaseCardGrid):
    def __init__(self):
        _BaseCardGrid.__init__(self, 1, LIST_ROWS,
                               LIST_CELL_W, LIST_CELL_H,
                               font_size=max(16, sc(28)))

    def _buildRow(self, row_idx):
        start = self._getPageStart()
        row = [None]
        item_idx = start + row_idx              # single column: row == index
        if item_idx >= self._getPageEnd():
            return row
        item = self._items[item_idx]
        is_sel = (row_idx == self.currentRow)
        cx, cy = LIST_CELL_MARGIN, LIST_CELL_MARGIN
        inner_w = LIST_CELL_W - 2 * LIST_CELL_MARGIN
        inner_h = LIST_CELL_H - 2 * LIST_CELL_MARGIN
        bw = HOME_BORDER_W
        # [PATCH 34] separator rows (YTS's "🎬 Movies" headers etc.) render
        # as dim centered dividers — visually distinct from selectable cards
        if item.get("type") == "separator":
            row.append(MultiContentEntryText(
                pos=(cx, cy), size=(inner_w, inner_h), font=1,
                text=item.get("title", ""), color=_G_CLR["text2"],
                flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER))
            return row
        # frame + surface (same card look as the site tiles)
        bc = _G_CLR["cyan"] if is_sel else _G_CLR["border"]
        row.append(MultiContentEntryText(pos=(cx, cy), size=(inner_w, inner_h), font=0, text="", color=0, backcolor=bc, flags=0))
        row.append(MultiContentEntryText(pos=(cx + bw, cy + bw), size=(inner_w - 2 * bw, inner_h - 2 * bw), font=0, text="", color=0, backcolor=_G_CLR["surface2"], flags=0))
        # [PATCH 28] RTL rows: Arabic titles pin to the RIGHT edge with
        # the tag on the LEFT (Arabic reads right-to-left); Latin rows
        # keep the LTR layout. Detected per row — mixed lists handle
        # themselves.
        title = item.get("title", "")
        tag = item.get("tagline") or item.get("count") or ""
        if _has_arabic(title):
            row.append(MultiContentEntryText(pos=(cx + bw + sc(20), cy + bw),
                                              size=(inner_w - 2 * bw - LIST_TAG_W - sc(40), inner_h - 2 * bw),
                                              font=0, text=title,
                                              color=_G_CLR["cyan"] if is_sel else _G_CLR["text"],
                                              backcolor=_G_CLR["surface2"],
                                              flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER))
            if tag:
                row.append(MultiContentEntryText(pos=(cx + bw + sc(10), cy + bw),
                                                  size=(LIST_TAG_W - sc(10), inner_h - 2 * bw),
                                                  font=1, text=tag, color=_G_CLR["text2"],
                                                  backcolor=_G_CLR["surface2"],
                                                  flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER))
        else:
            row.append(MultiContentEntryText(pos=(cx + bw + sc(20), cy + bw),
                                              size=(inner_w - 2 * bw - LIST_TAG_W - sc(40), inner_h - 2 * bw),
                                              font=0, text=title,
                                              color=_G_CLR["cyan"] if is_sel else _G_CLR["text"],
                                              backcolor=_G_CLR["surface2"], flags=RT_VALIGN_CENTER))
            if tag:
                row.append(MultiContentEntryText(pos=(cx + inner_w - bw - LIST_TAG_W, cy + bw),
                                                  size=(LIST_TAG_W - sc(10), inner_h - 2 * bw),
                                                  font=1, text=tag, color=_G_CLR["text2"],
                                                  backcolor=_G_CLR["surface2"],
                                                  flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER))
        return row


# --- Poster grid (design: 8x2, posters 210x330 @1080p; cols auto-fit) ---
POSTER_W = sc(210)
POSTER_H = sc(330)
# [PATCH 15+16] bigger caption font (20 @1080p, was 16) + 3-line wrap.
# Budget check: 3x24 lines + 2x4 margins = 80px — identical to the old
# 2x26 + 2x14, so POSTER_CELL_H stays 410 and grid geometry, pixmap/
# badge XML and the 2-row 820px fit are all unchanged.
POSTER_CAPTION_LINE_H = max(14, sc(24))
POSTER_CAPTION_LINES = 3
POSTER_CAPTION_H = POSTER_CAPTION_LINE_H * POSTER_CAPTION_LINES
POSTER_CELL_MARGIN_H = max(4, sc(10))
POSTER_CELL_MARGIN_V = max(4, sc(4))
POSTER_CELL_W = POSTER_W + 2 * POSTER_CELL_MARGIN_H
POSTER_CELL_H = POSTER_H + POSTER_CAPTION_H + 2 * POSTER_CELL_MARGIN_V

# Auto-fit columns to the screen width (plugin.py places the grid at
# x=sc(40)): 8 at 1080p (matches the old design exactly), 7 at 720p,
# 8 at 4K with larger cells.
POSTER_GRID_COLS = max(4, (SCREEN_W - sc(80)) // POSTER_CELL_W)
POSTER_GRID_ROWS = 2
POSTER_WRAP_CHARS = max(12, int(16 * _SCALE))

class PosterCardGrid(_BaseCardGrid):
    def __init__(self):
        _BaseCardGrid.__init__(self, POSTER_GRID_COLS, POSTER_GRID_ROWS, POSTER_CELL_W, POSTER_CELL_H, font_size=max(14, sc(22)))
        # [PATCH 15+16] captions use font 1 (was font_size-6 = 16 @1080p);
        # raise it without touching font 0 or the other grids
        self.l.setFont(1, gFont("Regular", max(14, sc(20))))

    def _buildRow(self, row_idx):
        start = self._getPageStart()
        is_sr = (row_idx == self.currentRow)
        row = [None]
        cy = POSTER_CELL_MARGIN_V
        for col_idx in range(self.cols):
            item_idx = start + row_idx * self.cols + col_idx
            if item_idx >= self._getPageEnd(): continue
            cx = col_idx * self.cell_w + POSTER_CELL_MARGIN_H
            item = self._items[item_idx]
            is_sel = is_sr and col_idx == self.currentCol

            # 1. Static focus frame (drawn underneath the poster) —
            # always cyan on the selected cell; the blink timer is gone.
            frame_color = _G_CLR["cyan"] if is_sel else _G_CLR["border"]
            _bp = max(2, sc(6))
            row.append(MultiContentEntryText(pos=(cx - _bp, cy - _bp), size=(POSTER_W + 2 * _bp, POSTER_H + 2 * _bp), font=0, text="", color=0, backcolor=frame_color, flags=0))

            # 2. Solid Pure Opaque Black Background for Poster
            row.append(MultiContentEntryText(pos=(cx, cy), size=(POSTER_W, POSTER_H), font=0, text="", color=0, backcolor="#000000", flags=0))


            title = item.get("title", "")
            # [PATCH 49] year lives in the red badge box now — captions
            # are title-only
            if item.get("_is_next_page"):
                caption = "الصفحة التالية"
            else:
                caption = title

            cap_y = cy + POSTER_H + 2

            row.append(MultiContentEntryText(pos=(cx, cap_y), size=(POSTER_W, POSTER_CAPTION_H), font=0, text="", color=0, backcolor="#000000", flags=0))
            wrapped = _wrap_ui_text(caption, width=POSTER_WRAP_CHARS, max_lines=POSTER_CAPTION_LINES, fallback=caption)
            for line_idx, line in enumerate(wrapped.split("\n")):
                line_y = cap_y + line_idx * POSTER_CAPTION_LINE_H
                row.append(MultiContentEntryText(pos=(cx, line_y), size=(POSTER_W, POSTER_CAPTION_LINE_H), font=1, text=line, color=_G_CLR["text"], backcolor="#000000", flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER))
        return row


def build_poster_pixmap_widgets_xml(x0, y0, name_prefix="poster"):
    parts = []
    for r in range(POSTER_GRID_ROWS):
        for c in range(POSTER_GRID_COLS):
            px = x0 + c * POSTER_CELL_W + POSTER_CELL_MARGIN_H
            py = y0 + r * POSTER_CELL_H + POSTER_CELL_MARGIN_V
            parts.append('\n\t\t<widget name="%s_%d_%d" position="%d,%d" size="%d,%d" transparent="1" alphatest="blend" zPosition="3" />' % (name_prefix, r, c, px, py, POSTER_W, POSTER_H))
    return "".join(parts)

# --- Resume badge bars (grid mode) -----------------------------------------
# Gold bar with the resume time at the poster's bottom edge. Label widgets
# at zPosition=4 — ABOVE the poster pixmaps (z=3). The old badge drawn
# inside PosterCardGrid lived in the listbox layer and was always covered
# by the poster pixmap, i.e. it never actually rendered.
POSTER_BADGE_H = max(16, sc(26))


def build_poster_badge_widgets_xml(x0, y0, name_prefix="pbadge"):
    """[PATCH 49] per-cell overlay labels, all at zPosition=4 — ABOVE the
    poster pixmaps (z=3). The listbox layer cannot paint card overlays
    (pixmaps cover it — the reason the in-layer badges/bars never showed):
      pbadge_{r,c}   — bottom gold bar (resume time / ✓)
      pyear_{r,c}    — top-left red year box
      prat_{r,c}     — top-right black rating box
      pbar_{r,c}     — watch-progress bar (track+fill, scaled at runtime)
    """
    parts = []
    _f = max(11, sc(17))
    _cr = max(3, sc(6))
    for r in range(POSTER_GRID_ROWS):
        for c in range(POSTER_GRID_COLS):
            px = x0 + c * POSTER_CELL_W + POSTER_CELL_MARGIN_H
            py = y0 + r * POSTER_CELL_H + POSTER_CELL_MARGIN_V
            parts.append('\n\t\t<widget name="%s_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#FFD740" transparent="0" zPosition="4" font="Regular;%d" foregroundColor="#0D1117" halign="center" valign="center" cornerRadius="%d" />'
                         % (name_prefix, r, c, px, py + POSTER_H - POSTER_BADGE_H, POSTER_W, POSTER_BADGE_H, _f, _cr))
            # year badge (top-left): red box, white text
            parts.append('\n\t\t<widget name="pyear_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#C0392B" transparent="0" zPosition="4" font="Regular;%d" foregroundColor="#F0F6FC" halign="center" valign="center" cornerRadius="%d" />'
                         % (r, c, px + sc(6), py + sc(6), sc(92), sc(30), _f, _cr))
            # rating badge (top-right): black box, gold text (no ★ glyph —
            # the skin font drops it; "7.8" alone reads clean)
            parts.append('\n\t\t<widget name="prat_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#000000" transparent="0" zPosition="4" font="Regular;%d" foregroundColor="#FFD740" halign="center" valign="center" cornerRadius="%d" />'
                         % (r, c, px + POSTER_W - sc(106), py + sc(6), sc(100), sc(30), _f, _cr))
            # progress bar (bottom edge, above the gold badge bar)
            parts.append('\n\t\t<widget name="pbar_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#0D1117" transparent="0" zPosition="4" cornerRadius="2" />'
                         % (r, c, px, py + POSTER_H - POSTER_BADGE_H - sc(12), POSTER_W, sc(8)))
    return "".join(parts)


# --- Carousel (design geometry @1080p, scaled to screen) ---
_CAROUSEL_DESIGN = {
    0: (55, 680, 145, 215), 1: (250, 630, 180, 270), 2: (485, 570, 230, 345),
    3: (790, 455, 340, 510), 4: (1205, 570, 230, 345), 5: (1490, 630, 180, 270), 6: (1720, 680, 145, 215),
}
_CAROUSEL_GEOMETRY = {k: (sc(x), sc(y), sc(w), sc(h)) for k, (x, y, w, h) in _CAROUSEL_DESIGN.items()}


def build_carousel_xml():
    parts = []
    _cf = max(12, sc(24))   # cposter font
    _bf = max(11, sc(18))   # badge/fav/resume font
    _cr = max(4, sc(12))    # corner radius
    for i in range(7):
        # zPosition=3 ensures focus is above the shade_overlay (z=2) but below poster (z=4)
        parts.append('<widget name="cfocus{i}" position="0,0" size="1,1" backgroundColor="#00E5FF" cornerRadius="{cr}" zPosition="3" transparent="0" />'.format(i=i, cr=_cr))
        parts.append('<widget name="cposter{i}" position="0,0" size="1,1" backgroundColor="#161B22" cornerRadius="{cr}" zPosition="4" transparent="0" halign="center" valign="center" font="Regular;{f}" foregroundColor="#00E5FF" />'.format(i=i, cr=_cr, f=_cf))
        parts.append('<widget name="cposterImg{i}" position="0,0" size="1,1" zPosition="5" alphatest="blend" scale="1" />'.format(i=i))
        parts.append('<widget name="cfavMark{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#FFD740" transparent="1" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_cf))
        # [PATCH 46-R2] rating badge restyled gold — yellow star on
        # black box, matching the grid card design
        parts.append('<widget name="cratingBadge{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#FFD740" backgroundColor="#000000" transparent="0" cornerRadius="{cr2}" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_bf, cr2=max(3, sc(8))))
        parts.append('<widget name="cresumeMark{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#0D1117" backgroundColor="#FFD740" transparent="0" cornerRadius="{cr2}" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_bf, cr2=max(3, sc(8))))
    return "\n".join(parts)


# --- Continue-watching strip (design space @1080p) -------------------------
# NOTE: these are DESIGN-SPACE numbers, deliberately NOT sc()-scaled.
# The home screen's skin in plugin.py is a raw fixed 1920x1080 string
# (not wrapped in scale_skin_xml), so the strip follows the same
# convention to stay aligned with it. If the home skin ever gets
# scale_skin_xml()-wrapped and its geometry sc()-scaled, wrap these
# the same way (CONT_W = sc(140), etc.).
#
# Layout: title/label line at y=88, posters at y=120..320, the site
# grid starts at y=330. The strip hosts CONT_SLOTS posters of
# 140x200 at 16px gaps; plugin.py moves a contSel frame (peeks 6px
# around the selected poster, same pattern as the carousel cfocus).
CONT_SLOTS = 7
CONT_W = 140
CONT_H = 200
CONT_GAP = 16
CONT_X0 = 45
CONT_Y = 120


def build_continue_row_xml():
    """Skin XML for the Continue-Watching strip widgets.

    One label (cont_title — row caption + selected-item info), one
    selection frame (contSel — sized/moved at runtime by plugin.py's
    _moveContinueSel), and CONT_SLOTS poster pixmaps (cont0..cont6).
    Injected into the home screen's skin via the {continue_xml}
    placeholder in plugin.py."""
    parts = ['<widget name="cont_title" position="45,88" size="1300,30" font="Regular;26" foregroundColor="#FFD740" transparent="1" zPosition="7" />']
    parts.append('<widget name="contSel" position="0,0" size="1,1" backgroundColor="#00E5FF" cornerRadius="8" zPosition="3" transparent="0" />')
    for i in range(CONT_SLOTS):
        parts.append('<widget name="cont%d" position="%d,%d" size="%d,%d" zPosition="4" alphatest="blend" scale="1" />'
                      % (i, CONT_X0 + i * (CONT_W + CONT_GAP), CONT_Y, CONT_W, CONT_H))
        # [PATCH 13] per-item resume-time badge — gold bar at the poster's
        # bottom edge (grid pbadge style), above the pixmap (z=6)
        parts.append('<widget name="contbadge%d" position="%d,%d" size="%d,%d" backgroundColor="#FFD740" transparent="0" zPosition="6" font="Regular;15" foregroundColor="#0D1117" halign="center" valign="center" cornerRadius="4" />'
                     % (i, CONT_X0 + i * (CONT_W + CONT_GAP), CONT_Y + CONT_H - 22, CONT_W, 22))
    return "\n".join(parts)