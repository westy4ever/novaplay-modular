# -*- coding: utf-8 -*-
"""
Advanced Arabic Player - Custom grid widgets (resolution-aware)
================================================================

[PATCH 61] navigation performance: plugin_screen_home now memoizes
  pixmap paths so setPixmapFromFile only runs when the path actually
  changed (was re-decoding on every arrow key and every poll tick —
  the size bump in 59/60 made that visible).
[PATCH 60] continue-strip ratio was wrong (240:280 ≈ 0.857, near
  square, while posters are 2:3 ≈ 0.667) — resizeCover was squashing
  every poster. Strip is now 220x330 (proper 2:3).
[PATCH 59] home site grid 4x2 → 6x2 (narrower tiles); continue strip
  6 @ 240x280 (was 180x200).
[PATCH 58] poster-grid selection bar hoisted out of the listbox into
  a dedicated widget (skin-side pgridSel at z=5).
[PATCH 57] continue-strip selection mirrors the poster grid:
  thin right-edge cyan bar + 8% zoom.
[PATCH 56] right-edge cyan selection bar on the poster grid.
[PATCH 54] strip badge z=8 + carousel progress bar.
[PATCH 53] carousel year badge + star in rating; grid frame removed.
[PATCH 46-R2/49/52] overlay badges at z=4, compact sizes.
[PATCH 48] in-poster watch-progress state rides the item dict.
[PATCH 15+16] bigger 3-line captions.
[PATCH 14] TextListGrid (vertical category list).
[PATCH 28] RTL rows for Arabic titles.
[PATCH 34] separator rows.
[PATCH 13] continue-strip per-poster time badges.

Resolution-aware geometry: every constant derived from 1920x1080 by
_SCALE. scale_skin_xml() scales any skin string. resolve_icon_path()
with aliases + default.png fallback.
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
_HEALTH_DOT_COLORS = {"ok": "#39D98A", "down": "#F85149", "blocked": "#FFD740"}

# ─── Icon resolution ────────────────────────────────────────────────────────
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


# --- Home site-menu grid (design: 6x2 cells of 313x260 @1080p) ---
# [PATCH 60] row height 280 → 260 so 2 rows fit the 520px frame the
# screen now reserves (grid anchor moved to y=440 to clear the bigger
# 2:3 continue strip).
HOME_GRID_COLS = 6
HOME_GRID_ROWS = 2
HOME_CELL_W = sc(313)
HOME_CELL_H = sc(260)
HOME_CELL_MARGIN = sc(12)
HOME_BORDER_W = max(2, sc(4))
HOME_CELL_INNER_W = HOME_CELL_W - 2 * HOME_CELL_MARGIN
HOME_CELL_INNER_H = HOME_CELL_H - 2 * HOME_CELL_MARGIN
HOME_LABEL_H = sc(44)
HOME_ICON_PAD_TOP = sc(12)
HOME_ICON_W = HOME_CELL_INNER_W - 2 * HOME_BORDER_W
HOME_ICON_H = max(1, HOME_CELL_INNER_H - 2 * HOME_BORDER_W - HOME_ICON_PAD_TOP - HOME_LABEL_H)
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
            # Health dot (top-right corner)
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
LIST_ROWS = 10
LIST_CELL_W = sc(1840)
LIST_CELL_H = sc(83)
LIST_CELL_MARGIN = sc(6)
LIST_TAG_W = sc(200)


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
        # [PATCH 34] separator rows — dim centered dividers
        if item.get("type") == "separator":
            row.append(MultiContentEntryText(
                pos=(cx, cy), size=(inner_w, inner_h), font=1,
                text=item.get("title", ""), color=_G_CLR["text2"],
                flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER))
            return row
        # frame + surface
        bc = _G_CLR["cyan"] if is_sel else _G_CLR["border"]
        row.append(MultiContentEntryText(pos=(cx, cy), size=(inner_w, inner_h), font=0, text="", color=0, backcolor=bc, flags=0))
        row.append(MultiContentEntryText(pos=(cx + bw, cy + bw), size=(inner_w - 2 * bw, inner_h - 2 * bw), font=0, text="", color=0, backcolor=_G_CLR["surface2"], flags=0))
        # [PATCH 28] RTL rows
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
POSTER_CAPTION_LINE_H = max(14, sc(24))
POSTER_CAPTION_LINES = 3
POSTER_CAPTION_H = POSTER_CAPTION_LINE_H * POSTER_CAPTION_LINES
POSTER_CELL_MARGIN_H = max(4, sc(10))
POSTER_CELL_MARGIN_V = max(4, sc(4))
POSTER_CELL_W = POSTER_W + 2 * POSTER_CELL_MARGIN_H
POSTER_CELL_H = POSTER_H + POSTER_CAPTION_H + 2 * POSTER_CELL_MARGIN_V

POSTER_GRID_COLS = max(4, (SCREEN_W - sc(80)) // POSTER_CELL_W)
POSTER_GRID_ROWS = 2
POSTER_WRAP_CHARS = max(12, int(16 * _SCALE))

class PosterCardGrid(_BaseCardGrid):
    def __init__(self):
        _BaseCardGrid.__init__(self, POSTER_GRID_COLS, POSTER_GRID_ROWS, POSTER_CELL_W, POSTER_CELL_H, font_size=max(14, sc(22)))
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

            # [PATCH 58] The right-edge cyan selection bar was removed from
            # the listbox cell content — it lived at z=3 (same layer as the
            # poster pixmaps), so the 8% zoom on the selected cell painted
            # over it. It's now a dedicated screen-level widget
            # ("pgridSel", z=5) positioned in _updatePosterPixmaps.

            # Solid Pure Opaque Black Background for Poster
            row.append(MultiContentEntryText(pos=(cx, cy), size=(POSTER_W, POSTER_H), font=0, text="", color=0, backcolor="#000000", flags=0))

            title = item.get("title", "")
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

# --- Per-cell overlay labels (z=4, above the poster pixmaps z=3) ----------
POSTER_BADGE_H = max(16, sc(26))


def build_poster_badge_widgets_xml(x0, y0, name_prefix="pbadge"):
    parts = []
    _f = max(11, sc(17))
    _cr = max(3, sc(6))
    for r in range(POSTER_GRID_ROWS):
        for c in range(POSTER_GRID_COLS):
            px = x0 + c * POSTER_CELL_W + POSTER_CELL_MARGIN_H
            py = y0 + r * POSTER_CELL_H + POSTER_CELL_MARGIN_V
            parts.append('\n\t\t<widget name="%s_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#FFD740" transparent="0" zPosition="4" font="Regular;%d" foregroundColor="#0D1117" halign="center" valign="center" cornerRadius="%d" />'
                         % (name_prefix, r, c, px, py + POSTER_H - POSTER_BADGE_H, POSTER_W, POSTER_BADGE_H, _f, _cr))
            # year badge (top-left): red box, white text — compact (52)
            parts.append('\n\t\t<widget name="pyear_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#C0392B" transparent="0" zPosition="4" font="Regular;%d" foregroundColor="#F0F6FC" halign="center" valign="center" cornerRadius="%d" />'
                         % (r, c, px + sc(4), py + sc(4), sc(60), sc(24), max(12, sc(15)), max(2, sc(4))))
            # rating badge (top-right): black box, gold star+number (52)
            parts.append('\n\t\t<widget name="prat_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#000000" transparent="0" zPosition="4" font="Regular;%d" foregroundColor="#FFD740" halign="center" valign="center" cornerRadius="%d" />'
                         % (r, c, px + POSTER_W - sc(74), py + sc(4), sc(70), sc(24), max(12, sc(15)), max(2, sc(4))))
            # progress bar (bottom edge) — gold fill; width scales (52)
            parts.append('\n\t\t<widget name="pbar_%d_%d" position="%d,%d" size="%d,%d" backgroundColor="#FFD740" transparent="0" zPosition="4" cornerRadius="2" />'
                         % (r, c, px, py + POSTER_H - POSTER_BADGE_H - sc(10), POSTER_W, sc(8)))
    return "".join(parts)


# --- Carousel (design geometry @1080p, scaled to screen) ---
_CAROUSEL_DESIGN = {
    0: (55, 680, 145, 215), 1: (250, 630, 180, 270), 2: (485, 570, 230, 345),
    3: (790, 455, 340, 510), 4: (1205, 570, 230, 345), 5: (1490, 630, 180, 270), 6: (1720, 680, 145, 215),
}
_CAROUSEL_GEOMETRY = {k: (sc(x), sc(y), sc(w), sc(h)) for k, (x, y, w, h) in _CAROUSEL_DESIGN.items()}


def build_carousel_xml():
    parts = []
    _cf = max(12, sc(24))
    _bf = max(11, sc(18))
    _cr = max(4, sc(12))
    for i in range(7):
        parts.append('<widget name="cfocus{i}" position="0,0" size="1,1" backgroundColor="#00E5FF" cornerRadius="{cr}" zPosition="3" transparent="0" />'.format(i=i, cr=_cr))
        parts.append('<widget name="cposter{i}" position="0,0" size="1,1" backgroundColor="#161B22" cornerRadius="{cr}" zPosition="4" transparent="0" halign="center" valign="center" font="Regular;{f}" foregroundColor="#00E5FF" />'.format(i=i, cr=_cr, f=_cf))
        parts.append('<widget name="cposterImg{i}" position="0,0" size="1,1" zPosition="5" alphatest="blend" scale="1" />'.format(i=i))
        parts.append('<widget name="cfavMark{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#FFD740" transparent="1" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_cf))
        parts.append('<widget name="cratingBadge{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#FFD740" backgroundColor="#000000" transparent="0" cornerRadius="{cr2}" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_bf, cr2=max(3, sc(8))))
        # [PATCH 53] year badge — red box, top-left (matches grid)
        parts.append('<widget name="cyearBadge{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#F0F6FC" backgroundColor="#C0392B" transparent="0" cornerRadius="{cr2}" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_bf, cr2=max(3, sc(8))))
        parts.append('<widget name="cresumeMark{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#0D1117" backgroundColor="#FFD740" transparent="0" cornerRadius="{cr2}" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_bf, cr2=max(3, sc(8))))
        # [PATCH 54] watch-progress bar — gold fill, bottom edge
        parts.append('<widget name="cbar{i}" position="0,0" size="1,1" backgroundColor="#FFD740" transparent="0" cornerRadius="2" zPosition="7" />'.format(i=i))
    return "\n".join(parts)


# --- Continue-watching strip (design space @1080p) -------------------------
# [PATCH 60] posters were 240x280 (0.857 — nearly square). Poster artwork
# is 2:3 (≈0.667), so resizeCover was cover-cropping every poster into a
# squashed frame — this is why strip posters looked "strange" in size.
# Back to a proper 2:3 shape: 220x330 (bigger than the original 180x200,
# and the same silhouette as the poster grid's 210x330). Width check:
#   6*220 + 5*16 = 1400px  (fits 1920 with X0=45 margin)
CONT_SLOTS = 6
CONT_W = 220
CONT_H = 330
CONT_GAP = 16
CONT_X0 = 45
CONT_Y = 95


def build_continue_row_xml():
    # cont_title stays above the taller strip: 65 + 30 = 95 = strip top.
    parts = ['<widget name="cont_title" position="45,65" size="1300,30" font="Regular;26" foregroundColor="#FFD740" transparent="1" zPosition="7" />']
    # [PATCH 57] contSel is now a thin right-edge bar (matches poster
    # grid's selection strip) and lives ABOVE the poster pixmaps so the
    # zoomed poster can't cover it. Was: cornerRadius="8", zPosition="3".
    parts.append('<widget name="contSel" position="0,0" size="1,1" backgroundColor="#00E5FF" cornerRadius="3" zPosition="6" transparent="0" />')
    for i in range(CONT_SLOTS):
        parts.append('<widget name="cont%d" position="%d,%d" size="%d,%d" zPosition="4" alphatest="blend" scale="1" />'
                      % (i, CONT_X0 + i * (CONT_W + CONT_GAP), CONT_Y, CONT_W, CONT_H))
        # [PATCH 13/54] per-item resume-time badge — gold bar, z=8 so the
        # poster pixmap can never cover it.
        parts.append('<widget name="contbadge%d" position="%d,%d" size="%d,%d" backgroundColor="#FFD740" transparent="0" zPosition="8" font="Regular;16" foregroundColor="#0D1117" halign="center" valign="center" cornerRadius="4" />'
                     % (i, CONT_X0 + i * (CONT_W + CONT_GAP), CONT_Y + CONT_H - 26, CONT_W, 26))
    return "\n".join(parts)