# -*- coding: utf-8 -*-
"""NovaPlay — Home screen.

The hub: site grid (health dots), Continue-Watching strip, poster grid
and carousel modes. MODULAR EXTRACTION of AdvancedArabicPlayerHome
with the full UX update set baked in:

  * registry-driven site tiles (get_home_sites) + per-site health dots
  * Continue-Watching strip with zone navigation (Fixes A–D applied)
  * static focus — the blink timers are gone entirely
  * poster placeholders (plugin_assets) in grid + carousel
  * resume badge bars (grid layer, mode-gated) + carousel resume marks
  * category-list cache (Bug E) + keybar reset + pager in list mode
  * backdrop = real fanart only (Option A — no poster fallback)
  * search scope honored + 6-worker search cap (Bugs A & H)
  * [PATCH 48/49/52] watched B&W + overlay badges (year/rating/bar)
    at z=4, compact sizes; captions title-only
  * [PATCH 51/52] selected-poster zoom (~8%) in grid; carousel
    rating badge pop (+30% on center)
  * [PATCH 53] carousel year badge (red, top-left) + star in rating;
    grid focus frame removed (zoom is the selection signal)
  * [PATCH 57] continue-watching strip: same selection signal as the
    poster grid — right-edge 6px cyan bar + ~8% zoom + recenter
  * [PATCH 58] poster-grid selection bar hoisted to a dedicated widget
    ("pgridSel", z=5) so the 8% zoom can never paint over it — that
    was why the bar only showed while a poster was still loading
  * [PATCH 59] home site grid: 4x2 → 6x2 (narrower tiles); continue
    strip: 6 @ 240x280 (was 180x200)
  * [PATCH 60] continue-strip ratio was wrong (240:280 ≈ 0.857, near
    square, while posters are 2:3 ≈ 0.667) — resizeCover was squashing
    every poster. Strip is now 220x330 (proper 2:3); home grid anchor
    pushed y=400 → y=440 to clear it
  * [PATCH 61] memoized pixmap paths so setPixmapFromFile only runs
    when a path actually changed; the poll tick is 1s instead of 600ms;
    the continue-strip fallback no longer decodes full-size originals.
    These were the three things that made navigation feel "heavy"
    after the strip got bigger.
"""

import os
import threading
import time

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, ePoint, eSize

from plugin_common import my_log, PLUGIN_PATH, _PLUGIN_VERSION
from extractors import get_extractor, get_home_sites, get_search_site_order
from extractors.base import get_curl_failed_needs_proxy
from plugin_gridlist import (HomeMenuGrid, PosterCardGrid, resolve_icon_path,
    build_pixmap_widgets_xml, build_poster_pixmap_widgets_xml,
    build_poster_badge_widgets_xml,
    TextListGrid,
    build_carousel_xml, build_continue_row_xml,
    HOME_GRID_COLS, HOME_GRID_ROWS, HOME_CELL_W, HOME_CELL_H,
    HOME_CELL_MARGIN, HOME_BORDER_W, HOME_ICON_PAD_TOP, HOME_ICON_W, HOME_ICON_H,
    POSTER_GRID_COLS, POSTER_GRID_ROWS, POSTER_W, POSTER_H,
    _CAROUSEL_GEOMETRY,
    CONT_SLOTS, CONT_W, CONT_H, CONT_GAP, CONT_X0, CONT_Y,
    POSTER_CELL_W, POSTER_CELL_H, POSTER_CELL_MARGIN_H,
    POSTER_CELL_MARGIN_V, POSTER_BADGE_H, sc)
import plugin_imagecache
import plugin_health
from plugin_assets import placeholder_for_item
from plugin_state import (_get_config, _set_config,
    _is_favorite, _get_saved_position,
    _continue_items, _library_search_suggestions,
    _history_items)
from plugin_util import (_site_label, _site_tagline, _site_search_item,
    _wrap_ui_text, _single_line_text, _dedupe_items, _rank_search_items,
    _strip_arabic_from_english_title)
from plugin_tmdb import _tmdb_enabled, _tmdb_search_metadata
from plugin_screen_detail import AdvancedArabicPlayerDetail
from plugin_screen_search import AdvancedArabicPlayerSearch
from novaplay_thread import callInMainThread

_SEARCH_SITE_ORDER = get_search_site_order()


class AdvancedArabicPlayerHome(Screen):
    skin = """
    <screen name="AdvancedArabicPlayerHome" position="center,center" size="1920,1080" title="NovaPlay Media Center" flags="wfNoBorder" backgroundColor="#0D1117">
        <eLabel position="0,0" size="1920,1080" backgroundColor="#0D1117" zPosition="0" />
        <ePixmap position="0,0" size="1920,1080" pixmap="{plugin_path}/images/background.jpg" zPosition="1" alphatest="blend" />
        <widget name="backdropImg" position="0,0" size="1920,1080" zPosition="1" alphatest="blend" scale="1" />
        <widget name="shade_overlay" position="0,0" size="1920,1080" backgroundColor="#0D1117" transparency="150" zPosition="2" />
        <widget name="title_bar"  position="0,0"     size="1920,80" backgroundColor="#0D1117" zPosition="6" />
        <widget name="title_text" position="45,6"    size="1100,36" font="Regular;28" foregroundColor="#00E5FF" transparent="1" zPosition="7" />
        <widget name="status"     position="1150,8"  size="725,30"  font="Regular;22" foregroundColor="#FFD740" transparent="1" halign="right" zPosition="7" />
        <widget name="content_title" position="40,95"  size="1200,50"  font="Bold;38" foregroundColor="#00E5FF" transparent="1" zPosition="5" halign="left" valign="top" />
        <widget name="info_meta"     position="40,150" size="1200,35"  font="Regular;24" foregroundColor="#FFD740" transparent="1" zPosition="5" halign="left" />
        <widget name="info_plot"     position="40,190" size="1200,230" font="Regular;22" foregroundColor="#F0F6FC" transparent="1" zPosition="5" halign="left" valign="top" />
        <widget name="home_grid" position="20,440" size="1880,520" scrollbarMode="showNever" transparent="1" zPosition="3" />
        {home_grid_pics}
        <widget name="poster_grid" position="40,90" size="1840,820" scrollbarMode="showNever" transparent="1" zPosition="3" />
        <widget name="text_list" position="40,95" size="1840,830" scrollbarMode="showNever" transparent="1" zPosition="3" />
        {poster_grid_pics}
        {poster_badge_xml}
        <widget name="pgridSel" position="0,0" size="1,1" backgroundColor="#00E5FF" cornerRadius="3" zPosition="5" transparent="0" />
        {carousel_xml}
        {continue_xml}
        <widget name="grid_status_left"  position="40,965"  size="900,32" font="Regular;22" foregroundColor="#8B949E" transparent="1" halign="left" zPosition="7" />
        <widget name="grid_status_right" position="940,965" size="900,32" font="Regular;22" foregroundColor="#8B949E" transparent="1" halign="right" zPosition="7" />
        <widget name="btn_bar"    position="0,1015"  size="1920,65" backgroundColor="#0D1117" zPosition="6" />
        <widget name="key_red"    position="45,1028" size="420,32" font="Regular;22" foregroundColor="#FF6B6B" transparent="1" halign="center" zPosition="7" />
        <widget name="key_green"  position="510,1028" size="420,32" font="Regular;22" foregroundColor="#39D98A" transparent="1" halign="center" zPosition="7" />
        <widget name="key_yellow" position="975,1028" size="420,32" font="Regular;22" foregroundColor="#FFD740" transparent="1" halign="center" zPosition="7" />
        <widget name="key_blue"   position="1440,1028" size="420,32" font="Regular;22" foregroundColor="#58A6FF" transparent="1" halign="center" zPosition="7" />
    </screen>
    """

    _HOME_GRID_X = 20
    _HOME_GRID_Y = 440          # [PATCH 60] was 400 — pushed down to clear
                                # the taller 2:3 continue strip
    _POSTER_GRID_X = 40
    _POSTER_GRID_Y = 90
    carousel_slots = 7
    carousel_center = 3

    def __init__(self, session):
        self.skin = AdvancedArabicPlayerHome.skin.format(
            plugin_path=PLUGIN_PATH,
            home_grid_pics=build_pixmap_widgets_xml(
                self._HOME_GRID_X, self._HOME_GRID_Y,
                HOME_GRID_COLS, HOME_GRID_ROWS, HOME_CELL_W, HOME_CELL_H,
                HOME_CELL_MARGIN, HOME_BORDER_W, HOME_ICON_PAD_TOP, HOME_ICON_W, HOME_ICON_H,
            ),
            poster_grid_pics=build_poster_pixmap_widgets_xml(self._POSTER_GRID_X, self._POSTER_GRID_Y),
            poster_badge_xml=build_poster_badge_widgets_xml(self._POSTER_GRID_X, self._POSTER_GRID_Y),
            carousel_xml=build_carousel_xml(),
            continue_xml=build_continue_row_xml(),
        )
        Screen.__init__(self, session)
        self.session = session
        self._items  = []
        self._page   = 1
        self._source = "home"
        self._site   = "egydead"
        self._m_type = "movie"
        self._last_query = ""
        self._nav_stack = []
        self._content_title_base = ""
        self._content_subtitle = ""
        self.widget_map = list(range(self.carousel_slots))
        self._layout_style = _get_config("layout_style", "carousel") or "carousel"
        self._next_page_url = None
        self._page_history = []
        self._focus_end = False
        self._tmdb_token = 0
        self._cats_cache = {}
        self._list_return_index = None   # [PATCH 42] Back-position memory

        self["backdropImg"] = Pixmap()
        self["shade_overlay"] = Label("")
        self._current_backdrop_path = ""
        self["title_bar"]  = Label("")
        self["title_text"] = Label("NovaPlay Media Center  v{}".format(_PLUGIN_VERSION))
        self["status"]     = Label("جاري التحميل...")
        self["content_title"] = Label("")
        self["info_meta"]  = Label("")
        self["info_plot"]  = Label("")
        self["btn_bar"]    = Label("")
        self["key_red"]    = Label("خروج")
        self["key_green"]  = Label("المفضلة")
        self["key_yellow"] = Label("بحث")
        self["key_blue"]   = Label("الإعدادات")

        self["home_grid"] = HomeMenuGrid()
        self["home_grid"].onSelectionChanged = self._onGridSelectionChanged
        self["text_list"] = TextListGrid()
        self["text_list"].onSelectionChanged = self._onGridSelectionChanged
        self["text_list"].hide()
        for _r in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                self["pic_%d_%d" % (_r, _c)] = Pixmap()

        self["poster_grid"] = PosterCardGrid()
        self["poster_grid"].onSelectionChanged = self._onPosterGridSelectionChanged
        for _r in range(POSTER_GRID_ROWS):
            for _c in range(POSTER_GRID_COLS):
                self["poster_%d_%d" % (_r, _c)] = Pixmap()
                self["pbadge_%d_%d" % (_r, _c)] = Label("")
                # [PATCH 49] overlay badges above the poster pixmaps
                self["pyear_%d_%d" % (_r, _c)] = Label("")
                self["prat_%d_%d" % (_r, _c)] = Label("")
                self["pbar_%d_%d" % (_r, _c)] = Label("")
        # [PATCH 58] Poster-grid selection bar — its own widget (z=5)
        # so the 8% zoom on the selected cell can't paint over it.
        self["pgridSel"] = Label("")

        for i in range(self.carousel_slots):
            self["cfocus%d" % i] = Label("")
            self["cposter%d" % i] = Label("")
            self["cposterImg%d" % i] = Pixmap()
            self["cfavMark%d" % i] = Label("")
            self["cratingBadge%d" % i] = Label("")
            # [PATCH 53] carousel year badge — red box, top-left
            self["cyearBadge%d" % i] = Label("")
            self["cresumeMark%d" % i] = Label("")

        # ── Continue-watching strip ──
        self["cont_title"] = Label("")
        self["contSel"] = Label("")
        for i in range(CONT_SLOTS):
            # [PATCH 54] badge created BEFORE the pixmap — stacking order
            # follows creation order on this renderer; the pixmap was
            # covering the badge (same lesson as the grid's 49)
            self["contbadge%d" % i] = Label("")
            self["cont%d" % i] = Pixmap()
        self._cont_items = []
        self._cont_index = 0
        self._focus_zone = "grid"
        # [PATCH 61] memoize what's already painted — setPixmapFromFile
        # re-decodes from disk every call, and it was being called on
        # every arrow key and every poll tick with identical data
        self._last_cont_painted = {}      # slot i → path
        self._last_icon_painted = {}      # (row, col) → icon path
        self._last_poster_painted = {}    # (row, col) → poster path
        self.onExecBegin.append(self._paintContinueRow)

        self["grid_status_left"] = Label("")
        self["grid_status_right"] = Label("")

        self._display_mode = "home"
        self.onClose.append(self._onPluginClose)

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions", "InfobarMenuActions"],
            {
                "ok":     self._onOk,
                "cancel": self._onBack,
                "red":    self._onBack,
                "green":  self._onGreen,
                "yellow": self._onSearch,
                "blue":   self._onBlue,
                "up":     self._navUp,
                "down":   self._navDown,
                "left":   self._navLeft,
                "right":  self._navRight,
            }, -1
        )

        self._artworkPollTimer = eTimer()
        self._artworkPollTimer.callback.append(self._pollArtworkCache)

        self.onLayoutFinish.append(self._init)

    def _init(self):
        for _r in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                try: self["pic_%d_%d" % (_r, _c)].instance.setScale(1)
                except: pass
        self._applyCarouselGeometry()
        self._showHome()

    def _moveResize(self, key, x, y, w, h):
        try:
            inst = self[key].instance
            if inst:
                inst.move(ePoint(int(x), int(y)))
                inst.resize(eSize(max(1, int(w)), max(1, int(h))))
        except Exception as e:
            my_log("UI moveResize error for {}: {}".format(key, e))

    def _applyCarouselGeometry(self):
        for logical_slot in range(self.carousel_slots):
            widget_id = self.widget_map[logical_slot]
            x, y, w, h = _CAROUSEL_GEOMETRY.get(logical_slot, (0, 0, 1, 1))
            is_big = (logical_slot == self.carousel_center)
            pad = 12 if is_big else 8
            self._moveResize('cposter%d' % widget_id, x, y, w, h)
            self._moveResize('cposterImg%d' % widget_id, x + pad, y + pad, max(1, w - pad * 2), max(1, h - pad * 2))
            rb_w = 38 if is_big else 34
            rb_h = 40 if is_big else 34
            # [PATCH 51b] rating badge pop: the center (selected) card's
            # badge is ~30% larger than the side cards'
            if is_big:
                rb_w = int(rb_w * 1.3)
                rb_h = int(rb_h * 1.25)
            self._moveResize('cratingBadge%d' % widget_id, x + w - rb_w - 14, y + 22, rb_w, rb_h)
            self._moveResize('cfavMark%d' % widget_id, x + 10, y + 10, 42, 42)
            # [PATCH 53] year badge: top-left, red box (matches grid)
            _yw = 58 if is_big else 52
            self._moveResize('cyearBadge%d' % widget_id, x + 12, y + 22, _yw, 28)
            self._moveResize('cresumeMark%d' % widget_id, x + 10, y + h - 34, w - 20, 30)
            if logical_slot == self.carousel_center:
                focus_extra = 7
                self._moveResize('cfocus%d' % self.carousel_center, x - focus_extra, y - focus_extra, w + (focus_extra * 2), h + (focus_extra * 2))
            else:
                self._moveResize('cfocus%d' % logical_slot, 0, 0, 1, 1)

    def _showHome(self):
        self._source = "home"
        self._display_mode = "home"
        self._page   = 1
        self._nav_stack = []
        self["title_text"].setText("NovaPlay Media Center")
        self["status"].setText("")
        site_items = []
        for key, title, tagline in get_home_sites():
            site_items.append({
                "title": title,
                "tagline": tagline,
                "_action": "site_" + key,
                "_health": plugin_health.get(key),
            })
        blocked = plugin_health.blocked_sites()
        if blocked:
            self["status"].setText("⚠ %d موقع محجوب (Cloudflare) — فعّل بروكسي المتصفح من الإعدادات" % len(blocked))
        self._items = site_items
        self["home_grid"].setList(self._items)
        self._showHomeMode()
        self._onGridSelectionChanged()

    def _showHomeMode(self):
        self._focus_zone = "grid"
        # [PATCH 24] keep the artwork poll running in home mode — it now
        # also refreshes continue-strip posters as downloads land
        try:
            self._artworkPollTimer.start(1000, False)   # [PATCH 61] was 600
        except Exception:
            pass
        self["backdropImg"].hide()
        self["shade_overlay"].hide()
        self["content_title"].hide()
        self["info_meta"].hide()
        self["info_plot"].hide()
        self._current_backdrop_path = ""
        self._tmdb_token += 1
        self["grid_status_left"].hide()
        self["grid_status_right"].hide()
        # [PATCH 58] poster-grid selection bar off in home mode
        try: self["pgridSel"].hide()
        except Exception: pass
        for i in range(self.carousel_slots):
            self["cfocus%d" % i].hide()
            self["cposter%d" % i].hide()
            self["cposterImg%d" % i].hide()
            self["cfavMark%d" % i].hide()
            self["cratingBadge%d" % i].hide()
            self["cyearBadge%d" % i].hide()
            self["cresumeMark%d" % i].hide()
        self["poster_grid"].hide()
        for i in range(POSTER_GRID_ROWS):
            for _c in range(POSTER_GRID_COLS):
                self["poster_%d_%d" % (i, _c)].hide()
                self["pbadge_%d_%d" % (i, _c)].hide()
                self["pyear_%d_%d" % (i, _c)].hide()
                self["prat_%d_%d" % (i, _c)].hide()
                self["pbar_%d_%d" % (i, _c)].hide()
        self["home_grid"].show()
        self["text_list"].hide()
        for i in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                self["pic_%d_%d" % (i, _c)].show()
        self["key_red"].setText("خروج")
        self["key_green"].setText("المفضلة")
        self["key_yellow"].setText("بحث")
        self["key_blue"].setText("الإعدادات")
        self._paintContinueRow()

    def _showPosterMode(self):
        self["home_grid"].hide()
        self["text_list"].hide()
        for i in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                self["pic_%d_%d" % (i, _c)].hide()
        for i in range(CONT_SLOTS):
            self["cont%d" % i].hide()
            self["contbadge%d" % i].hide()
        self["cont_title"].hide()
        try: self["contSel"].hide()
        except Exception: pass
        # [PATCH 58] hide poster-grid selection bar; _updatePosterPixmaps
        # re-shows it in grid mode after layout settles
        try: self["pgridSel"].hide()
        except Exception: pass
        for i in range(self.carousel_slots):
            self["cfocus%d" % i].hide()
            self["cposter%d" % i].hide()
            self["cposterImg%d" % i].hide()
            self["cfavMark%d" % i].hide()
            self["cratingBadge%d" % i].hide()
            self["cyearBadge%d" % i].hide()
            self["cresumeMark%d" % i].hide()
        self["poster_grid"].hide()
        for i in range(POSTER_GRID_ROWS):
            for _c in range(POSTER_GRID_COLS):
                self["poster_%d_%d" % (i, _c)].hide()
                self["pbadge_%d_%d" % (i, _c)].hide()
                self["pyear_%d_%d" % (i, _c)].hide()
                self["prat_%d_%d" % (i, _c)].hide()
                self["pbar_%d_%d" % (i, _c)].hide()
        if self._layout_style == "grid":
            self["poster_grid"].show()
            self["backdropImg"].hide()
            self["shade_overlay"].hide()
            self["content_title"].hide()
            self["info_meta"].hide()
            self["info_plot"].hide()
            self._current_backdrop_path = ""
            self["key_green"].setText("تبديل العرض (Carousel)")
        else:
            self["content_title"].show()
            self["info_meta"].show()
            self["info_plot"].show()
            for i in range(self.carousel_slots):
                self["cposter%d" % i].show()
            self._applyCarouselGeometry()
            self._syncCarouselFocusVisible()
            self["key_green"].setText("تبديل العرض (Grid)")
        self["key_red"].setText("الصفحة السابقة")
        self["key_yellow"].setText("بحث")
        self["key_blue"].setText("الصفحة التالية")
        self["grid_status_left"].show()
        self["grid_status_right"].show()
        self._artworkPollTimer.start(1000, False)   # [PATCH 61] was 600

    def _carouselPositionForSlot(self, slot):
        total = len(self._items)
        if total <= 0: return -1
        if total <= self.carousel_slots:
            pos = self.index + slot - self.carousel_center
            return pos if 0 <= pos < total else -1
        return (self.index - self.carousel_center + slot) % total

    def _updateCarouselSlot(self, widget_id, pos):
        total_items = len(self._items)
        if pos < 0 or pos >= total_items:
            self["cposter%d" % widget_id].hide()
            self["cposterImg%d" % widget_id].hide()
            self["cfavMark%d" % widget_id].hide()
            self["cratingBadge%d" % widget_id].hide()
            self["cyearBadge%d" % widget_id].hide()
            self["cresumeMark%d" % widget_id].hide()
            return
        item = self._items[pos]
        self["cposter%d" % widget_id].show()
        if item.get("_is_next_page"):
            self["cposterImg%d" % widget_id].hide()
            self["cratingBadge%d" % widget_id].hide()
            self["cyearBadge%d" % widget_id].hide()
            self["cfavMark%d" % widget_id].hide()
            self["cresumeMark%d" % widget_id].hide()
            self["cposter%d" % widget_id].setText("الصفحة التالية")
            return
        if item.get("_is_prev_page"):
            self["cposterImg%d" % widget_id].hide()
            self["cratingBadge%d" % widget_id].hide()
            self["cyearBadge%d" % widget_id].hide()
            self["cfavMark%d" % widget_id].hide()
            self["cresumeMark%d" % widget_id].hide()
            self["cposter%d" % widget_id].setText("الصفحة السابقة")
            return
        self["cposter%d" % widget_id].setText("")
        url = item.get("poster") or ""
        path = plugin_imagecache.getCachedImage(url, target_size=(340, 510)) if url else ""
        if path:
            try:
                self["cposterImg%d" % widget_id].instance.setPixmapFromFile(path)
                self["cposterImg%d" % widget_id].show()
            except: self["cposterImg%d" % widget_id].hide()
        else:
            ph = placeholder_for_item(item)
            if ph:
                try:
                    self["cposterImg%d" % widget_id].instance.setScale(1)
                    self["cposterImg%d" % widget_id].instance.setPixmapFromFile(ph)
                    self["cposterImg%d" % widget_id].show()
                except Exception:
                    self["cposterImg%d" % widget_id].hide()
            else:
                self["cposterImg%d" % widget_id].hide()
            if url: plugin_imagecache.requestImageAsync(url, target_size=(340, 510))
        if _is_favorite(item.get("url", "")):
            self["cfavMark%d" % widget_id].setText("★")
            self["cfavMark%d" % widget_id].show()
        else:
            self["cfavMark%d" % widget_id].hide()
        rating = item.get("rating", "")
        if rating:
            # [PATCH 53] star prefix — Labels render ★ (grid does too)
            self["cratingBadge%d" % widget_id].setText(u"★ %s " % rating)
            self["cratingBadge%d" % widget_id].show()
        else:
            self["cratingBadge%d" % widget_id].hide()
        # [PATCH 53] year badge — top-left red box
        _cy = str(item.get("year") or "")[:4]
        if _cy:
            self["cyearBadge%d" % widget_id].setText(_cy)
            self["cyearBadge%d" % widget_id].show()
        else:
            self["cyearBadge%d" % widget_id].hide()
        # v4.5: watched badge — reuse the resumeMark widget
        _watched = False
        try:
            from plugin_watched import is_watched
            _watched = is_watched(item.get("url", ""))
        except Exception:
            pass
        saved_pos = _get_saved_position(item.get("url", ""))
        if _watched:
            self["cresumeMark%d" % widget_id].setText(u"✓ شاهدته")
            self["cresumeMark%d" % widget_id].show()
        elif saved_pos > 30:
            mm, ss = divmod(saved_pos, 60)
            hh, mm = divmod(mm, 60)
            tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
            self["cresumeMark%d" % widget_id].setText("متابعة " + tstr)
            self["cresumeMark%d" % widget_id].show()
        else:
            self["cresumeMark%d" % widget_id].hide()

    def _paintCarousel(self):
        for logical_slot in range(self.carousel_slots):
            widget_id = self.widget_map[logical_slot]
            pos = self._carouselPositionForSlot(logical_slot)
            self._updateCarouselSlot(widget_id, pos)
        self._updateBackdrop()

    def _moveCarousel(self, delta):
        total = len(self._items)
        if total <= 0: return
        self.index += delta
        if self.index < 0: self.index = total - 1
        elif self.index >= total: self.index = 0
        if delta == 1: self.widget_map = self.widget_map[1:] + [self.widget_map[0]]
        elif delta == -1: self.widget_map = [self.widget_map[-1]] + self.widget_map[:-1]
        self._applyCarouselGeometry()
        self._paintCarousel()

    def _syncCarouselFocusVisible(self):
        # Static focus (blink removed): center card's frame simply shows.
        for i in range(self.carousel_slots):
            self["cfocus%d" % i].hide()
        if len(self._items) > 0:
            self["cfocus%d" % self.carousel_center].show()

    def _onPosterGridSelectionChanged(self):
        self._updateGridFooter()
        self._updatePosterPixmaps()
        self._updateBackdrop()

    def _updateGridFooter(self):
        if self._display_mode == "poster":
            grid, cols, rows = self["poster_grid"], POSTER_GRID_COLS, POSTER_GRID_ROWS
        elif self._display_mode == "list":
            # [PATCH 14b] the category list now lives in text_list —
            # geometry read from the widget itself
            grid, cols, rows = self["text_list"], self["text_list"].cols, self["text_list"].rows
        else:
            grid, cols, rows = self["home_grid"], HOME_GRID_COLS, HOME_GRID_ROWS
        page, total_pages = grid.getPageInfo()
        total = len(self._items)
        per_page = cols * rows
        start = (page - 1) * per_page + 1
        end = min(start + per_page - 1, total)
        self["grid_status_left"].setText("Shows {}-{} / {}".format(start, end, total))
        self["grid_status_right"].setText("Page {} / {}".format(page, total_pages))

    def _updatePosterPixmaps(self):
        # [PATCH 58] Selection bar is its own widget above the pixmaps
        # (z=5). The old in-listbox bar was at z=3, same as the poster
        # pixmaps, so the 8% zoom on the selected cell painted over it —
        # which is why it only showed while a poster was still loading.
        try:
            _g = self["poster_grid"]
            if (self._display_mode == "poster" and self._layout_style == "grid"
                    and 0 <= _g.currentIndex < len(self._items)):
                _sel_i = _g.currentIndex - _g._getPageStart()
                _sr = _sel_i // _g.cols
                _scol = _sel_i % _g.cols
                _sx = (self._POSTER_GRID_X + _scol * POSTER_CELL_W
                       + POSTER_CELL_MARGIN_H + POSTER_W + 2)
                _sy = (self._POSTER_GRID_Y + _sr * POSTER_CELL_H
                       + POSTER_CELL_MARGIN_V)
                self._moveResize("pgridSel", _sx, _sy, 6, POSTER_H)
                self["pgridSel"].show()
            else:
                self["pgridSel"].hide()
        except Exception:
            try: self["pgridSel"].hide()
            except Exception: pass

        visible = {}
        for row, col, item in self["poster_grid"].getPageItems():
            visible[(row, col)] = item
        for r in range(POSTER_GRID_ROWS):
            for c in range(POSTER_GRID_COLS):
                widget = self["poster_%d_%d" % (r, c)]
                badge = self["pbadge_%d_%d" % (r, c)]
                item = visible.get((r, c))
                if not item:
                    widget.hide()
                    badge.hide()
                    self["pyear_%d_%d" % (r, c)].hide()
                    self["prat_%d_%d" % (r, c)].hide()
                    self["pbar_%d_%d" % (r, c)].hide()
                    # [PATCH 61] clear the memo so the next paint isn't skipped
                    self._last_poster_painted.pop((r, c), None)
                    continue
                url = item.get("poster") or ""
                path = ""
                if url:
                    path = plugin_imagecache.getCachedImage(url, target_size=(POSTER_W, POSTER_H))
                # [PATCH 48/49-G/52] watched posters paint a black & white
                # variant (fresh "|bw" cache key)
                if path and item.get("url"):
                    try:
                        from plugin_watched import is_watched
                        if is_watched(item.get("url")):
                            _dp = plugin_imagecache.buildCachePath(
                                url + "|bw", target_size=(POSTER_W, POSTER_H))
                            if not os.path.exists(_dp):
                                _data = plugin_imagecache.downloadUrl(url, timeout=8)
                                _dim = None
                                if _data:
                                    try:
                                        from PIL import Image, ImageOps
                                        import io as _io
                                        _img2 = plugin_imagecache.resizeCover(
                                            _data, (POSTER_W, POSTER_H), darken=0.35)
                                        if _img2:
                                            _im = Image.open(_io.BytesIO(_img2)).convert("RGB")
                                            _im = ImageOps.grayscale(_im)
                                            _buf = _io.BytesIO()
                                            _im.save(_buf, format="JPEG", quality=88)
                                            _dim = _buf.getvalue()
                                    except Exception:
                                        _dim = plugin_imagecache.resizeCover(
                                            _data, (POSTER_W, POSTER_H), darken=0.35)
                                if _dim is not None:
                                    plugin_imagecache.writeFileAtomic(_dp, _dim)
                            if os.path.exists(_dp):
                                path = _dp
                    except Exception:
                        pass
                if path:
                    try:
                        # [PATCH 61] only decode when the path changed —
                        # was re-decoding all 8 visible posters on every
                        # arrow key and again every poll tick.
                        if self._last_poster_painted.get((r, c)) != path:
                            widget.instance.setPixmapFromFile(path)
                            self._last_poster_painted[(r, c)] = path
                        widget.show()
                        # [PATCH 51a] selected-poster zoom: the highlighted
                        # card's image grows ~8% and re-centers on its cell.
                        # The else-branch restores normal size when deselected.
                        try:
                            _g = self["poster_grid"]
                            _idx = _g._getPageStart() + r * _g.cols + c
                            _is_sel = (_idx == _g.currentIndex)
                            if _is_sel:
                                _zw, _zh = int(POSTER_W * 1.08), int(POSTER_H * 1.08)
                                _zx = (self._POSTER_GRID_X + c * POSTER_CELL_W + POSTER_CELL_MARGIN_H
                                       + (POSTER_W - _zw) // 2)
                                _zy = (self._POSTER_GRID_Y + r * POSTER_CELL_H + POSTER_CELL_MARGIN_V
                                       + (POSTER_H - _zh) // 2)
                                widget.instance.move(ePoint(_zx, _zy))
                                widget.instance.resize(eSize(_zw, _zh))
                            else:
                                _zx = self._POSTER_GRID_X + c * POSTER_CELL_W + POSTER_CELL_MARGIN_H
                                _zy = self._POSTER_GRID_Y + r * POSTER_CELL_H + POSTER_CELL_MARGIN_V
                                widget.instance.move(ePoint(_zx, _zy))
                                widget.instance.resize(eSize(POSTER_W, POSTER_H))
                        except Exception:
                            pass
                    except Exception:
                        widget.hide()
                else:
                    ph = placeholder_for_item(item)
                    if ph:
                        try:
                            if self._last_poster_painted.get((r, c)) != ph:
                                widget.instance.setScale(1)
                                widget.instance.setPixmapFromFile(ph)
                                self._last_poster_painted[(r, c)] = ph
                            widget.show()
                        except Exception:
                            widget.hide()
                    else:
                        widget.hide()
                    if url:
                        plugin_imagecache.requestImageAsync(url, target_size=(POSTER_W, POSTER_H))
                # [PATCH 48/49] stash watch-state on the item so
                # PosterCardGrid can draw state without re-querying
                try:
                    _wt = False
                    if item.get("url"):
                        try:
                            from plugin_watched import is_watched
                            _wt = is_watched(item.get("url"))
                        except Exception:
                            pass
                    item["_is_watched"] = _wt
                    item["_watch_pos"] = int(_get_saved_position(item.get("url", "")) or 0)
                except Exception:
                    pass
                # [PATCH 49/52] paint the overlay badges — all ABOVE the
                # poster pixmaps (z=4), compact sizes
                _in_grid = (self._display_mode == "poster" and self._layout_style == "grid")
                _watched = bool(item.get("_is_watched"))
                saved_pos = int(item.get("_watch_pos") or 0)

                # year (top-left, red — compact)
                yb = self["pyear_%d_%d" % (r, c)]
                _yr = str(item.get("year") or "")[:4]
                if _yr and _in_grid:
                    yb.setText(_yr)
                    yb.show()
                else:
                    yb.hide()

                # rating (top-right, black/gold — compact, with star)
                rb = self["prat_%d_%d" % (r, c)]
                _rt = str(item.get("rating") or "").strip()
                try:
                    _rtv = float(_rt)
                except Exception:
                    _rtv = 0.0
                if _rtv > 0 and _in_grid:
                    rb.setText(u"★ " + _rt[:3])
                    rb.show()
                else:
                    rb.hide()

                # progress bar (bottom edge): gold fill, width = progress
                bar = self["pbar_%d_%d" % (r, c)]
                if (not _watched) and saved_pos > 30 and _in_grid:
                    try:
                        inst = bar.instance
                        if inst is not None:
                            base_x = (self._POSTER_GRID_X + c * POSTER_CELL_W
                                      + POSTER_CELL_MARGIN_H)
                            base_y = (self._POSTER_GRID_Y + r * POSTER_CELL_H
                                      + POSTER_CELL_MARGIN_V + POSTER_H
                                      - POSTER_BADGE_H - 10)
                            _pct = item.get("_watch_pct") or (35 if saved_pos > 600 else 20)
                            _w = max(12, (POSTER_W * min(100, max(10, int(_pct)))) // 100)
                            inst.move(ePoint(base_x, base_y))
                            inst.resize(eSize(_w, 8))
                        bar.show()
                    except Exception:
                        bar.hide()
                else:
                    bar.hide()

                # bottom gold bar (resume/✓)
                if _watched and _in_grid:
                    badge.setText(u"✓ شاهدته")
                    badge.show()
                elif saved_pos > 30 and _in_grid:
                    mm, ss = divmod(saved_pos, 60)
                    hh, mm = divmod(mm, 60)
                    tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
                    badge.setText("متابعة " + tstr)
                    badge.show()
                else:
                    badge.hide()

    def _updateBackdrop(self, skip_tmdb=False):
        if self._display_mode != "poster" or self._layout_style != "carousel":
            self["backdropImg"].hide()
            self["content_title"].setText("")
            self["info_meta"].setText("")
            self["info_plot"].setText("")
            self._current_backdrop_path = ""
            return
        if not (0 <= getattr(self, 'index', -1) < len(self._items)):
            item = None
        else:
            item = self._items[self.index]
        if not skip_tmdb:
            self._tmdb_token += 1
        if not item or item.get("_is_next_page") or item.get("_is_prev_page"):
            self["backdropImg"].hide()
            self["content_title"].setText("")
            self["info_meta"].setText("")
            self["info_plot"].setText("")
            self._current_backdrop_path = ""
            return
        display_title = _strip_arabic_from_english_title(item.get("title", "") or "")
        year = item.get("year") or ""
        display_with_year = "{} {}".format(display_title, year).strip() if year else display_title
        self["content_title"].setText(_single_line_text(display_with_year, width=42, fallback=""))
        if not skip_tmdb:
            current_token = self._tmdb_token
            threading.Thread(target=self._bgFetchTmdbText, args=(item, current_token)).start()
        target_size = (1920, 1080)
        # Backdrop = real fanart/backdrop only (Option A — poster fallback
        # removed: it cover-cropped a portrait poster into a zoomed strip).
        backdrop_url = item.get("fanart") or item.get("backdrop") or ""
        target_url = backdrop_url
        is_real_backdrop = bool(backdrop_url)
        if not target_url:
            self["backdropImg"].hide()
            self._current_backdrop_path = ""
        else:
            path = plugin_imagecache.getCachedImage(target_url, target_size=target_size)
            if path and path != self._current_backdrop_path:
                try:
                    self["backdropImg"].instance.setPixmapFromFile(path)
                    self["backdropImg"].show()
                    self._current_backdrop_path = path
                    my_log("_updateBackdrop: painted {} ({})".format(path, "real backdrop" if is_real_backdrop else "poster fallback"))
                except Exception as e:
                    my_log("_updateBackdrop: setPixmapFromFile threw for {}: {}".format(path, e))
            elif not path:
                plugin_imagecache.requestImageAsyncPriority(target_url, target_size=target_size)
        # (backdrop fetch merged into _bgFetchTmdbText — no second thread)

    def _bgFetchTmdbText(self, item, token):
        try:
            meta = _tmdb_search_metadata(item.get("title", ""), item.get("year", ""), item.get("type", "movie"))
            if token == self._tmdb_token:
                callInMainThread(self._paintTmdbText, meta, token)
                # Merged: the backdrop image fetch rides the SAME metadata
                # call (the old separate thread re-ran the full search —
                # 2x TMDB traffic per selection).
                if (meta and meta.get("backdrop_url")
                        and not (item.get("fanart") or item.get("backdrop"))):
                    item["fanart"] = meta["backdrop_url"]
                    target_size = (1920, 1080)
                    path = plugin_imagecache.getCachedImage(meta["backdrop_url"], target_size=target_size)
                    if not path:
                        data = plugin_imagecache.downloadUrl(meta["backdrop_url"], timeout=8)
                        if data:
                            processed = plugin_imagecache.resizeCover(data, target_size, darken=0.45)
                            if processed is not None:
                                cache_path = plugin_imagecache.buildCachePath(meta["backdrop_url"], target_size=target_size)
                                if plugin_imagecache.writeFileAtomic(cache_path, processed):
                                    path = cache_path
                    if path:
                        callInMainThread(self._paintTmdbBackdrop, path, token)
        except Exception as e:
            my_log("_bgFetchTmdbText error: {}".format(e))

    def _paintTmdbText(self, meta, token):
        if token != self._tmdb_token: return
        if meta:
            # [PATCH 56] prefer the ITEM's rating/year (same source as
            # the poster badge) so the backdrop info never disagrees
            # with the card; TMDB's fresh lookup only fills gaps
            _item = self._items[self.index] if (0 <= getattr(self, "index", -1) < len(self._items)) else None
            _r = (_item or {}).get("rating") or meta.get("rating", "N/A")
            _y = (_item or {}).get("year") or meta.get("year", "")
            meta_str = "★ %s  |  %s  |  %s" % (_r, _y, meta.get("genres", ""))
            self["info_meta"].setText(meta_str)
            self["info_plot"].setText(meta.get("plot", ""))
        else:
            self["info_meta"].setText("")
            self["info_plot"].setText("")

    def _bgFetchTmdbBackdrop(self, item, token):
        try:
            meta = _tmdb_search_metadata(item.get("title", ""), item.get("year", ""), item.get("type", "movie"))
            if token != self._tmdb_token: return
            if meta and meta.get("backdrop_url"):
                backdrop_url = meta["backdrop_url"]
                item["fanart"] = backdrop_url
                target_size = (1920, 1080)
                path = plugin_imagecache.getCachedImage(backdrop_url, target_size=target_size)
                if not path:
                    data = plugin_imagecache.downloadUrl(backdrop_url, timeout=8)
                    if data:
                        processed = plugin_imagecache.resizeCover(data, target_size, darken=0.45)
                        if processed is not None:
                            cache_path = plugin_imagecache.buildCachePath(backdrop_url, target_size=target_size)
                            ok = plugin_imagecache.writeFileAtomic(cache_path, processed)
                            if ok:
                                path = cache_path
                if path and token == self._tmdb_token:
                    callInMainThread(self._paintTmdbBackdrop, path, token)
        except Exception as e:
            my_log("_bgFetchTmdbBackdrop error: {}".format(e))

    def _paintTmdbBackdrop(self, path, token):
        if token == self._tmdb_token and path:
            try:
                self["backdropImg"].instance.setPixmapFromFile(path)
                self["backdropImg"].show()
                self._current_backdrop_path = path
            except Exception as e:
                my_log("_paintTmdbBackdrop: setPixmapFromFile threw for {}: {}".format(path, e))

    def _refreshContinuePosters(self):
        """[PATCH 24] repaint strip posters from cache only — no re-query,
        no re-request: just swaps placeholders for arrived posters.
        [PATCH 61] memoized: only calls setPixmapFromFile when the path
        actually changed — re-decoding on every poll tick was what made
        home-mode navigation feel heavy after the strip got bigger."""
        try:
            items = getattr(self, "_cont_items", []) or []
            last = self._last_cont_painted
            for i in range(min(CONT_SLOTS, len(items))):
                u = items[i].get("poster") or ""
                if not u:
                    continue
                p = plugin_imagecache.getCachedImage(u, target_size=(CONT_W, CONT_H))
                if not p:
                    # Do NOT fall back to full-size here — decoding a
                    # 500x750 original into a 220x330 widget every tick
                    # was the real hammer. Leave whatever's already
                    # painted (a placeholder) until the right size lands.
                    continue
                if last.get(i) == p:
                    continue                    # already painted
                try:
                    w = self["cont%d" % i]
                    w.instance.setScale(1)
                    w.instance.setPixmapFromFile(p)
                    w.show()
                    last[i] = p
                except Exception:
                    pass
        except Exception:
            pass

    def _pollArtworkCache(self):
        # [PATCH 24] home mode: swap strip placeholders for posters as
        # the async downloads land — without leaving the plugin
        # [PATCH 61] tick is now 1s (was 600ms); the memoized refresh
        # makes each tick near-free when nothing has changed.
        if self._display_mode == "home":
            if getattr(self, "_cont_items", None):
                self._refreshContinuePosters()
            return
        if self._display_mode != "poster": return
        if self._layout_style == "grid":
            self._updatePosterPixmaps()
            self._updateGridFooter()
            self._updateBackdrop(skip_tmdb=True)
        else:
            for logical_slot in range(self.carousel_slots):
                widget_id = self.widget_map[logical_slot]
                pos = self._carouselPositionForSlot(logical_slot)
                self._updateCarouselSlot(widget_id, pos)
            self._updateBackdrop(skip_tmdb=True)

    def _onGridSelectionChanged(self):
        self._updateIcons()
        # Keep the category-list pager in sync while navigating.
        if self._display_mode == "list":
            self._updateGridFooter()

    def _updateIcons(self):
        # [PATCH 61] hide()/show() are cheap; setPixmapFromFile is not.
        # Memoize the icon path per cell so navigating the 6x2 home grid
        # doesn't re-decode all 12 tiles on every arrow key.
        for _r in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                self["pic_%d_%d" % (_r, _c)].hide()
        if self._display_mode != "home":
            return
        last = self._last_icon_painted
        for row, col, item in self["home_grid"].getPageItems():
            icon_path = resolve_icon_path(item, PLUGIN_PATH)
            if not icon_path:
                continue
            key = (row, col)
            widget = self["pic_%d_%d" % (row, col)]
            try:
                if last.get(key) != icon_path:
                    widget.instance.setPixmapFromFile(icon_path)
                    last[key] = icon_path
                widget.show()
            except Exception:
                pass

    # ── Continue-watching row ───────────────────────────────────────────
    def _paintContinueRow(self):
        if self._display_mode != "home":
            return
        try:
            self._cont_items = _continue_items(CONT_SLOTS)
        except Exception:
            self._cont_items = []
        if self._cont_index >= len(self._cont_items):
            self._cont_index = max(0, len(self._cont_items) - 1)
        for i in range(CONT_SLOTS):
            widget = self["cont%d" % i]
            badge = self["contbadge%d" % i]
            if i < len(self._cont_items):
                item = self._cont_items[i]
                url = item.get("poster") or ""
                path = plugin_imagecache.getCachedImage(url, target_size=(CONT_W, CONT_H)) if url else ""
                if not path and url:
                    plugin_imagecache.requestImageAsyncPriority(url, target_size=(CONT_W, CONT_H))
                if not path:
                    path = placeholder_for_item(item)
                if path:
                    try:
                        # [PATCH 61] memoized: only re-decode on change
                        if self._last_cont_painted.get(i) != path:
                            widget.instance.setScale(1)
                            widget.instance.setPixmapFromFile(path)
                            self._last_cont_painted[i] = path
                        widget.show()
                    except Exception:
                        widget.hide()
                else:
                    widget.hide()
                # [PATCH 13] strip items are resumable by definition —
                # show each one's time on its badge
                pos = int(item.get("last_position_sec") or 0)
                mm, ss = divmod(pos, 60)
                hh, mm = divmod(mm, 60)
                tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
                badge.setText(tstr)
                badge.show()
            else:
                widget.hide()
                badge.hide()
        if self._cont_items:
            self["cont_title"].show()
            if self._focus_zone == "row":
                self._moveContinueSel()             # re-asserts bar + zoom
            else:
                self._applyContinueLayout(None)     # keep base sizes
                try: self["contSel"].hide()
                except Exception: pass
            self._updateContinueLabel()
        else:
            self["cont_title"].hide()
            self._resetContinueZoom()

    def _updateContinueLabel(self):
        if not (0 <= self._cont_index < len(self._cont_items)):
            self["cont_title"].setText("متابعة المشاهدة")
            return
        item = self._cont_items[self._cont_index]
        pos = _get_saved_position(item.get("url", ""))
        mm, ss = divmod(max(pos, 0), 60)
        hh, mm = divmod(mm, 60)
        tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
        title = _single_line_text(item.get("title", ""), width=40, fallback="")
        if title:
            self["cont_title"].setText("متابعة المشاهدة  •  {}  •  {}".format(title, tstr))
        else:
            self["cont_title"].setText("متابعة المشاهدة")

    # [PATCH 57] Continue strip now mirrors the poster grid's selection
    # effect: 8% zoom + recenter on the selected card, thin cyan bar on
    # its right edge, no full frame.

    def _applyContinueLayout(self, zoom_index=None):
        """Position all continue-strip posters. zoom_index=None means
        'no selection' (all at base size)."""
        for i in range(CONT_SLOTS):
            base_x = CONT_X0 + i * (CONT_W + CONT_GAP)
            if i == zoom_index:
                zw = int(CONT_W * 1.08)
                zh = int(CONT_H * 1.08)
                zx = base_x + (CONT_W - zw) // 2
                zy = CONT_Y + (CONT_H - zh) // 2
                self._moveResize("cont%d" % i, zx, zy, zw, zh)
            else:
                self._moveResize("cont%d" % i, base_x, CONT_Y, CONT_W, CONT_H)

    def _resetContinueZoom(self):
        """Drop the zoom — call whenever we leave the continue row zone."""
        self._applyContinueLayout(None)
        try: self["contSel"].hide()
        except Exception: pass

    def _moveContinueSel(self):
        if not (0 <= self._cont_index < len(self._cont_items)):
            self._resetContinueZoom()
            return
        # Zoom the selected poster (matches poster-grid 8% pop)
        self._applyContinueLayout(self._cont_index)
        # Right-edge cyan bar (6px) — same signal as the poster grid
        bar_w = 6
        base_x = CONT_X0 + self._cont_index * (CONT_W + CONT_GAP)
        self._moveResize("contSel", base_x + CONT_W + 2, CONT_Y, bar_w, CONT_H)
        self["contSel"].show()
        self._updateContinueLabel()

    # ── Key handlers ────────────────────────────────────────────────────
    def _onOk(self):
        if self._display_mode == "home":
            if self._focus_zone == "row" and self._cont_items:
                item = self._cont_items[min(self._cont_index, len(self._cont_items) - 1)]
                self._focus_zone = "grid"
                self._resetContinueZoom()
                if item: self._openItem(item)
                return
            item = self["home_grid"].getCurrent()
            if not item: return
            a = item.get("_action", "")
            if a.startswith("site_"):
                self._site = a.replace("site_", "")
                self._showSiteCategories()
            return
        if self._display_mode == "list":
            item = self["text_list"].getCurrent()
            if not item: return
            # [PATCH 42] remember the row+page so Back returns here
            try:
                self._list_return_index = self["text_list"].currentIndex
            except Exception:
                self._list_return_index = None
            if item.get("_action") == "search_site":
                self._onSearch(item.get("_site", self._site))
            elif item.get("type") == "category":
                self._loadCategory(item["url"], item["title"], is_new=True)
            return
        if self._display_mode == "poster":
            if self._layout_style == "grid":
                item = self["poster_grid"].getCurrent()
                if item: self._openItem(item)
            else:
                if 0 <= self.index < len(self._items):
                    item = self._items[self.index]
                    if item.get("_is_next_page"):
                        self._nextPage()
                    elif item.get("_is_prev_page"):
                        self._prevPage()
                    else:
                        self._openItem(item)
            return

    def _navUp(self):
        if self._display_mode == "home" and self._focus_zone == "row":
            return                          # row is the topmost zone
        if self._display_mode == "home":
            pg = self["home_grid"]
            if self._cont_items and pg.currentRow == 0 and pg.currentPage == 0:
                self._focus_zone = "row"
                self._moveContinueSel()
                return
            pg.moveUp()
            return
        if self._display_mode == "list":
            self["text_list"].moveUp()
        elif self._display_mode == "poster" and self._layout_style == "grid":
            self["poster_grid"].moveUp()

    def _navDown(self):
        if self._display_mode == "home" and self._focus_zone == "row":
            self._focus_zone = "grid"
            self._resetContinueZoom()
            return
        if self._display_mode == "home":
            self["home_grid"].moveDown()
            return
        if self._display_mode == "list":
            self["text_list"].moveDown()
        elif self._display_mode == "poster" and self._layout_style == "grid":
            self["poster_grid"].moveDown()

    def _navLeft(self):
        if self._display_mode == "home" and self._focus_zone == "row":
            if self._cont_items:
                self._cont_index = (self._cont_index - 1) % len(self._cont_items)
                self._moveContinueSel()
            return
        if self._display_mode == "home":
            self["home_grid"].moveLeft()
        elif self._display_mode == "list":
            pass    # [PATCH 14] single column — no lateral move
        elif self._display_mode == "poster":
            if self._layout_style == "grid": self["poster_grid"].moveLeft()
            else: self._moveCarousel(-1)

    def _navRight(self):
        if self._display_mode == "home" and self._focus_zone == "row":
            if self._cont_items:
                self._cont_index = (self._cont_index + 1) % len(self._cont_items)
                self._moveContinueSel()
            return
        if self._display_mode == "home":
            self["home_grid"].moveRight()
        elif self._display_mode == "list":
            pass    # [PATCH 14] single column — no lateral move
        elif self._display_mode == "poster":
            if self._layout_style == "grid": self["poster_grid"].moveRight()
            else: self._moveCarousel(1)

    def _setList(self, items):
        show_adult = _get_config("show_adult", "false") == "true"
        adult_words = ["18+", "للكبار", "سكس", "xxx", "adult", "إباح", "sex"]
        if not show_adult:
            items = [i for i in items if not any(w in (i.get("title", "") + i.get("category_name", "")).lower() for w in adult_words)]
        content_items = [i for i in items if i.get("type") in ("movie", "series", "episode")]
        # Bug E follow-up: the categories list (source == "categories")
        # must ALWAYS render as a text list even if its entries happen to
        # carry content-like type fields — otherwise Back-to-categories
        # would flip into poster mode and lose the list UI.
        if self._source == "categories":
            content_items = []
        if content_items:
            self._items = content_items
            self._display_mode = "poster"
            if self._layout_style == "carousel":
                if self._page > 1 and self._source == "category":
                    content_items.insert(0, {"title": "Previous Page", "_is_prev_page": True, "type": "movie"})
                if getattr(self, "_next_page_url", None):
                    content_items.append({"title": "Next Page", "_is_next_page": True, "type": "movie"})
                has_prev = self._page > 1 and self._source == "category"
                has_next = getattr(self, "_next_page_url", None) is not None
                if getattr(self, "_focus_end", False):
                    if has_next:
                        self.index = len(content_items) - 2
                    else:
                        self.index = len(content_items) - 1
                    self._focus_end = False
                else:
                    if has_prev:
                        self.index = 1
                    else:
                        self.index = 0
            else:
                self.index = 0
            self.widget_map = list(range(self.carousel_slots))
            self._showPosterMode()
            if self._layout_style == "grid":
                self["poster_grid"].setList(content_items)
                self._onPosterGridSelectionChanged()
            else:
                self._paintCarousel()
        else:
            self._items = items
            self._display_mode = "list"
            self._artworkPollTimer.stop()
            for i in range(CONT_SLOTS):
                self["cont%d" % i].hide()
                self["contbadge%d" % i].hide()
            self["cont_title"].hide()
            try: self["contSel"].hide()
            except Exception: pass
            # [PATCH 58] poster-grid selection bar off in list mode
            try: self["pgridSel"].hide()
            except Exception: pass

            self["backdropImg"].hide()
            self["shade_overlay"].hide()
            self["content_title"].setText("")
            self["info_meta"].setText("")
            self["info_plot"].setText("")
            self._current_backdrop_path = ""

            # List mode: pager ON (Fix 2a), and both grids' widgets hidden.
            self["grid_status_left"].show()
            self["grid_status_right"].show()

            self["poster_grid"].hide()
            for i in range(self.carousel_slots):
                self["cfocus%d" % i].hide()
                self["cposter%d" % i].hide()
                self["cposterImg%d" % i].hide()
                self["cfavMark%d" % i].hide()
                self["cratingBadge%d" % i].hide()
                self["cyearBadge%d" % i].hide()
                self["cresumeMark%d" % i].hide()
            for i in range(POSTER_GRID_ROWS):
                for _c in range(POSTER_GRID_COLS):
                    self["poster_%d_%d" % (i, _c)].hide()
                    self["pbadge_%d_%d" % (i, _c)].hide()
                    self["pyear_%d_%d" % (i, _c)].hide()
                    self["prat_%d_%d" % (i, _c)].hide()
                    self["pbar_%d_%d" % (i, _c)].hide()
            for i in range(HOME_GRID_ROWS):
                for _c in range(HOME_GRID_COLS):
                    self["pic_%d_%d" % (i, _c)].hide()
            self["home_grid"].hide()
            self["text_list"].show()
            self["text_list"].setList(items)
            # [PATCH 42] restore the remembered row/page (Back from a
            # category now returns to where you left, not row 0)
            try:
                _ri = getattr(self, "_list_return_index", None)
                if _ri is not None and self._source == "categories":
                    tl = self["text_list"]
                    tl.currentPage = _ri // tl.itemsPerPage
                    tl.currentRow = (_ri % tl.itemsPerPage) // tl.cols
                    tl.currentCol = (_ri % tl.itemsPerPage) % tl.cols
                    tl._updateIndex()
                    tl._redraw()
                    tl.instance.moveSelectionTo(tl.currentRow)
                    self._updateGridFooter()
            except Exception:
                pass
            self._list_return_index = None
            self["status"].setText("{} عنصر".format(len(items)))
            # Category-list keybar: reset labels left over from
            # _showPosterMode. Red = back to home, yellow = search.
            self["key_red"].setText("رجوع")
            self["key_green"].setText("")
            self["key_yellow"].setText("بحث")
            self["key_blue"].setText("")
            self._updateGridFooter()

    def _showSiteCategories(self):
        # Bug E fix: cache the category list per site — Back from a
        # category must not re-fetch the whole list every time.
        cached = self._cats_cache.get(self._site)
        if cached is not None:
            self._source = "categories"
            self._setList(cached)
            self["title_text"].setText("تصنيفات {}".format(_site_label(self._site)))
            self["status"].setText("اختر القسم")
            return
        try:
            extractor = _get_extractor(self._site)
            get_categories = getattr(extractor, "get_categories", None)
            if not get_categories:
                cats = [{"title": "لا توجد أقسام", "type": "error"}]
            else:
                if self._site in ["egydead", "egydead_coupons"]:
                    movie_cats = get_categories("movie")
                    series_cats = get_categories("series")
                    cats = [_site_search_item(self._site)]
                    for item in movie_cats:
                        updated = dict(item); updated["_m_type"] = "movie"; cats.append(updated)
                    for item in series_cats:
                        updated = dict(item); updated["_m_type"] = "series"; cats.append(updated)
                else:
                    cats = [_site_search_item(self._site)] + (get_categories() or [])
            plugin_health.record(self._site, "ok")
        except Exception as e:
            cats = [{"title": "فشل جلب الأقسام", "type": "error"}]
            plugin_health.record(self._site, "blocked" if get_curl_failed_needs_proxy() else "down", str(e))
        self._cats_cache[self._site] = cats
        self._source = "categories"
        self._setList(cats)
        self["title_text"].setText("تصنيفات {}".format(_site_label(self._site)))
        self["status"].setText("اختر القسم")

    def _loadCategory(self, url, name, is_new=False):
        self._source = "category"
        self._cat_name = name
        if is_new:
            self._cat_url = url
            self._page = 1
            self._page_history = [(url, self._page)]
        else:
            key = (url, self._page)
            if key not in self._page_history:
                self._page_history.append(key)
                if self._site not in ["egydead", "egydead_coupons", "fasel", "faselhdx"]:
                    self._page += 1
        self["status"].setText("جاري تحميل {}...".format(name))
        threading.Thread(target=self._bgLoadCategory, args=(url,), daemon=True).start()

    def _bgLoadCategory(self, url):
        try:
            extractor = _get_extractor(self._site)
            get_category_items = getattr(extractor, "get_category_items", None)
            if not get_category_items: return
            if self._site in ["egydead", "egydead_coupons", "fasel", "faselhdx"]:
                items = get_category_items(url, page=self._page)
            else:
                items = get_category_items(url)
            plugin_health.record(self._site, "ok")
            callInMainThread(self._onCategoryLoaded, items)
        except Exception as e:
            plugin_health.record(self._site, "down", str(e))
            callInMainThread(self["status"].setText, "فشل: {}".format(str(e)[:60]))

    def _onCategoryLoaded(self, items):
        if not items:
            self["status"].setText("لا توجد نتائج")
            return
        next_page_item = next((i for i in items if i.get("_action") == "category" and i.get("url")), None)
        self._next_page_url = next_page_item["url"] if next_page_item else None
        self._setList(_dedupe_items(items))

    def _loadMovies(self):
        self._m_type = "movie"
        self._showSiteCategories()

    def _loadSeries(self):
        self._m_type = "series"
        self._showSiteCategories()

    def _openSettings(self):
        from plugin_screen_settings import AdvancedArabicPlayerSettings
        self.session.open(AdvancedArabicPlayerSettings, self._site)

    def _showLibrary(self, kind):
        if kind == "favorites": items = _get_favorite_items_list()
        else: items = _history_items()
        self._setList(items)
        self["title_text"].setText("المفضلة" if kind == "favorites" else "السجل")
        self["status"].setText("")

    def _onSearch(self, forced_scope=None):
        self.session.openWithCallback(self._onSearchQuery, AdvancedArabicPlayerSearch, current_site=self._site, default_scope=forced_scope or "all", query=self._last_query)

    def _onSearchQuery(self, result=None):
        if not result: return
        # Bug A fix: honor the chosen scope (was: always "all").
        if isinstance(result, str):
            query, scope = result, "all"
        else:
            query = result[0]
            scope = result[1] if len(result) > 1 else "all"
        if not query: return
        self._last_query = query
        self["status"].setText("بحث عن: {}...".format(query))
        threading.Thread(target=self._bgSearch, args=(query, scope or "all"), daemon=True).start()

    def _bgSearch(self, query, scope="all"):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _search_one(name):
            try:
                extractor = _get_extractor(name)
                results = extractor.search(query) or []
                for item in results:
                    item["_site"] = name
                plugin_health.record(name, "ok")
                return name, results
            except Exception as e:
                my_log("Search failed for site {}: {}".format(name, e))
                plugin_health.record(name, "down", str(e))
                return name, []

        items = []
        # Bug H fix: scope honored + 6 workers, not 17 simultaneous
        # curl_cffi TLS sessions (RAM spike + CF bot-burst signature).
        sites = list(_SEARCH_SITE_ORDER) if scope in ("all", "", None) else [scope]
        with ThreadPoolExecutor(max_workers=min(6, len(sites))) as ex:
            futures = [ex.submit(_search_one, name) for name in sites]
            for future in as_completed(futures):
                name, results = future.result()
                items.extend(results)

        callInMainThread(self._onSearchResults, items, query)

    def _onSearchResults(self, items, query):
        if not items:
            self["status"].setText("لا توجد نتائج")
            return
        self._setList(_rank_search_items(items, query))

    def _openItem(self, item):
        self.session.open(AdvancedArabicPlayerDetail, item=item, site=item.get("_site", self._site), m_type=item.get("type", self._m_type))

    def _nextPage(self):
        next_url = getattr(self, "_next_page_url", None)
        cat_url  = getattr(self, "_cat_url",  None)
        cat_name = getattr(self, "_cat_name", "")
        if self._source == "category" and (next_url or cat_url):
            if self._site in ["egydead", "egydead_coupons", "fasel", "faselhdx"]:
                self._page += 1
                fetch_url = cat_url
            else:
                fetch_url = next_url
            if fetch_url:
                self._loadCategory(fetch_url, cat_name)

    def _prevPage(self):
        if len(self._page_history) > 1:
            self._page_history.pop()
            prev_url, prev_page = self._page_history[-1]
            cat_name = getattr(self, "_cat_name", "")
            self._page = prev_page
            if self._layout_style == "carousel":
                self._focus_end = True
            self["status"].setText("جاري تحميل {}...".format(cat_name))
            threading.Thread(target=self._bgLoadCategory, args=(prev_url,), daemon=True).start()

    def _onGreen(self):
        if self._display_mode == "poster":
            current = _get_config("layout_style", "carousel")
            self._layout_style = "grid" if current == "carousel" else "carousel"
            _set_config("layout_style", self._layout_style)
            saved_index = self.index
            if current == "grid":
                saved_index = self["poster_grid"].currentIndex
            self._showPosterMode()
            if self._layout_style == "grid":
                self["poster_grid"].setList(self._items)
                target_idx = saved_index
                pg = self["poster_grid"]
                if 0 <= target_idx < len(self._items):
                    pg.currentPage = target_idx // pg.itemsPerPage
                    remainder = target_idx % pg.itemsPerPage
                    pg.currentRow = remainder // pg.cols
                    pg.currentCol = remainder % pg.cols
                    pg.currentIndex = target_idx
                    pg._redraw()
                    pg._notify()
            else:
                self.index = saved_index
                self._paintCarousel()
        else:
            # List/home: green = favorites (the old _loadSeries re-fetch
            # on categories was a confusing no-op — Bug D fix).
            self._showLibrary("favorites")

    def _onBlue(self):
        if self._source == "home": self._openSettings()
        else: self._nextPage()

    def _onBack(self):
        if self._display_mode == "poster":
            if len(getattr(self, "_page_history", [])) > 1 and self._source == "category":
                self._prevPage()
            else:
                self._showSiteCategories()
        elif self._source != "home":
            self._showHome()
        else:
            if self._focus_zone == "row":
                self._focus_zone = "grid"
                self._resetContinueZoom()
                return
            self.close()

    def _onPluginClose(self):
        try: self._artworkPollTimer.stop()
        except: pass
        try: plugin_imagecache.cancelAsyncImages()
        except: pass


def _get_favorite_items_list():
    # local alias keeps the class body identical to the monolith's
    from plugin_state import _favorite_items as _fi
    return _fi()


def _get_extractor(site):
    return get_extractor(site)