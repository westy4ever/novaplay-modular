# -*- coding: utf-8 -*-
"""NovaPlay — Home screen.

FINAL SET of UX features kept from the batch:
  [UX-1]  Cold-open focus lands on the continue strip when populated.
  [UX-2]  Per-continue-card watch-progress bar.
  [UX-3]  Hero fanart backdrop while the strip is focused.
  [UX-4]  Empty-strip hint text.
  [UX-5]  MENU on a site tile → ChoiceBox quick-actions.  ← FIXED
  [UX-10] Paging-keys hint in the footer.
  [UX-12] Wrap-around arrow navigation (grid layer).
  [UX-17] Status auto-timeout (6s) for non-warning text.
  [UX-19] Pulsing blocked-site health dot.  ← FIXED
  [UX-20] Dim visited tiles.
  [UX-23] Carousel index math (no widget_map shuffling).
  [UX-24] Unified MemoizedPixmapCache.

Everything else from the draft batch (theme system, section label,
hide-unused config, usage reorder, double-BACK reset, clear-all
continue) has been removed.
"""

import os
import threading
import time

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox                     # [UX-5] fix
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, ePoint, eSize

from plugin_common import my_log, PLUGIN_PATH, _PLUGIN_VERSION
from extractors import get_extractor, get_home_sites, get_search_site_order
from extractors.base import get_curl_failed_needs_proxy

import plugin_imagecache
import plugin_health
from plugin_assets import placeholder_for_item

from plugin_state import (_get_config, _set_config,
    _is_favorite, _get_saved_position,
    _continue_items, _library_search_suggestions,
    _history_items, _clear_continue_item,
    _hide_site, _is_site_hidden)

from plugin_gridlist import (HomeMenuGrid, PosterCardGrid, resolve_icon_path,
    build_pixmap_widgets_xml, build_poster_pixmap_widgets_xml,
    build_poster_badge_widgets_xml,
    TextListGrid,
    build_carousel_xml, build_continue_row_xml,
    HOME_GRID_COLS, HOME_GRID_ROWS, HOME_CELL_W, HOME_CELL_H,
    HOME_CELL_MARGIN, HOME_BORDER_W, HOME_ICON_PAD_TOP, HOME_ICON_W, HOME_ICON_H,
    HOME_CENTER_OFFSET_X,  # [PATCH G3 HOTFIX] was missing -- caused a NameError crash on launch
    POSTER_GRID_COLS, POSTER_GRID_ROWS, POSTER_W, POSTER_H,
    _CAROUSEL_GEOMETRY,
    CONT_SLOTS, CONT_W, CONT_H, CONT_GAP, CONT_X0, CONT_Y,
    POSTER_CELL_W, POSTER_CELL_H, POSTER_CELL_MARGIN_H,
    POSTER_CELL_MARGIN_V, POSTER_BADGE_H,
    POSTER_YEAR_W, POSTER_YEAR_H, POSTER_RATE_W, POSTER_RATE_H,
    MemoizedPixmapCache, sc)

from plugin_util import (_site_label, _site_tagline, _site_search_item,
    _wrap_ui_text, _single_line_text, _dedupe_items, _rank_search_items,
    _strip_arabic_from_english_title)
from plugin_tmdb import _tmdb_enabled, _tmdb_search_metadata
from plugin_screen_detail import AdvancedArabicPlayerDetail
from plugin_screen_search import AdvancedArabicPlayerSearch
from novaplay_thread import callInMainThread

from novaplay_dualclock import format_dual_dates

_SEARCH_SITE_ORDER = get_search_site_order()


class AdvancedArabicPlayerHome(Screen):
    skin = """
    <screen name="AdvancedArabicPlayerHome" position="center,center" size="1920,1080" title="NovaPlay Media Center" flags="wfNoBorder" backgroundColor="#0D1117">
        <eLabel position="0,0" size="1920,1080" backgroundColor="#0D1117" zPosition="0" />
        <ePixmap position="0,0" size="1920,1080" pixmap="{plugin_path}/images/background.jpg" zPosition="1" alphatest="blend" />
        <widget name="backdropImg" position="0,0" size="1920,1080" zPosition="1" alphatest="blend" scale="1" />
        <widget name="shade_overlay" position="0,0" size="1920,1080" backgroundColor="#0D1117" transparency="150" zPosition="2" />
        <widget name="title_bar"  position="0,0"     size="1920,80" backgroundColor="#0D1117" zPosition="6" />
        <widget name="title_text" position="45,6"    size="600,36"  font="Regular;28" foregroundColor="#00E5FF" transparent="1" zPosition="7" />

        <widget name="clock_time"   position="1440,4"  size="110,34" font="Regular;30" halign="right" valign="center" foregroundColor="#F0F6FC" transparent="1" zPosition="7" />
        <widget name="clock_period" position="1555,10" size="60,26"  font="Regular;18" halign="left"  valign="center" foregroundColor="#8B949E" transparent="1" zPosition="7" />
        <widget name="clock_sep"    position="1625,8"  size="2,34"   backgroundColor="#30363D" zPosition="7" />
        <widget name="clock_greg"   position="1637,4"  size="268,34" font="Regular;20" halign="right" valign="center" foregroundColor="#F0F6FC" transparent="1" zPosition="7" />
        <widget name="clock_hijri"  position="1637,42" size="268,32" font="Regular;20" halign="right" valign="center" foregroundColor="#FFD740" transparent="1" zPosition="7" />

        <widget name="status"     position="660,8"   size="520,30"  font="Regular;22" foregroundColor="#FFD740" transparent="1" halign="right" zPosition="7" />
        <widget name="content_title" position="40,95"  size="1200,50"  font="Bold;38" foregroundColor="#00E5FF" transparent="1" zPosition="5" halign="left" valign="top" />
        <widget name="info_meta"     position="40,150" size="1200,35"  font="Regular;24" foregroundColor="#FFD740" transparent="1" zPosition="5" halign="left" />
        <widget name="info_plot"     position="40,190" size="1200,230" font="Regular;22" foregroundColor="#F0F6FC" transparent="1" zPosition="5" halign="left" valign="top" />
        <widget name="home_grid" position="20,425" size="1880,520" scrollbarMode="showNever" transparent="1" zPosition="3" />
        {home_grid_pics}
        <widget name="poster_grid" position="50,90" size="1820,864" scrollbarMode="showNever" transparent="1" zPosition="3" />
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
    _HOME_GRID_Y = 425
    _POSTER_GRID_X = 50
    _POSTER_GRID_Y = 90
    carousel_slots = 7
    carousel_center = 3

    def __init__(self, session):
        self.skin = AdvancedArabicPlayerHome.skin.format(
            plugin_path=PLUGIN_PATH,
            # [PATCH G3] + HOME_CENTER_OFFSET_X so icon widgets line up with the now-
            # centered tile backgrounds drawn by HomeMenuGrid._buildRow.
            home_grid_pics=build_pixmap_widgets_xml(
                self._HOME_GRID_X + HOME_CENTER_OFFSET_X, self._HOME_GRID_Y,
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
        self._items = []
        self._page = 1
        self._source = "home"
        self._site = "egydead"
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
        self._cont_backdrop_token = 0  # [PATCH G5]
        self._cats_cache = {}
        self._list_return_index = None
        self.index = 0

        # [UX batch] state
        self._cold_open = True
        self._cont_items = []
        self._cont_index = 0
        self._cont_pct = {}
        self._focus_zone = "grid"
        self._pix = MemoizedPixmapCache()
        self._pulse_on = True

        self._status_timer = eTimer()
        self._status_timer.callback.append(self._clearStatusIfTransient)
        self._status_is_warning = False

        self._pulse_timer = eTimer()
        self._pulse_timer.callback.append(self._pulseTick)

        self["backdropImg"] = Pixmap()
        self["shade_overlay"] = Label("")
        self._current_backdrop_path = ""
        self["title_bar"]  = Label("")
        self["title_text"] = Label("NovaPlay Media Center  v{}".format(_PLUGIN_VERSION))
        self["status"]     = Label("جاري التحميل…")
        self["clock_time"]   = Label("")
        self["clock_period"] = Label("")
        self["clock_sep"]    = Label("")
        self["clock_greg"]   = Label("")
        self["clock_hijri"]  = Label("")
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
                self["pyear_%d_%d" % (_r, _c)] = Label("")
                self["prat_%d_%d" % (_r, _c)] = Label("")
                self["pbar_%d_%d" % (_r, _c)] = Label("")
        self["pgridSel"] = Label("")

        for i in range(self.carousel_slots):
            self["cfocus%d" % i] = Label("")
            self["cposter%d" % i] = Label("")
            self["cposterImg%d" % i] = Pixmap()
            self["cfavMark%d" % i] = Label("")
            self["cratingBadge%d" % i] = Label("")
            self["cyearBadge%d" % i] = Label("")
            self["clabel%d" % i] = Label("")
            self["cresumeMark%d" % i] = Label("")

        self["cont_title"] = Label("")
        self["contSel"] = Label("")
        for i in range(CONT_SLOTS):
            self["contbadge%d" % i] = Label("")
            self["contbartrack%d" % i] = Label("")
            self["contbar%d" % i] = Label("")
            self["cont%d" % i] = Pixmap()

        self.onExecBegin.append(self._paintContinueRow)

        self["grid_status_left"] = Label("")
        self["grid_status_right"] = Label("")

        self._display_mode = "home"
        self.onClose.append(self._onPluginClose)

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions",
             "InfobarMenuActions", "NumberActions", "MenuActions"],
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
                "menu":     self._onMenu,
                "showMenu": self._onMenu,
                "1": lambda: self._pageJump(1),
                "2": lambda: self._pageJump(2),
                "3": lambda: self._pageJump(3),
                "4": lambda: self._pageJump(4),
                "5": lambda: self._pageJump(5),
                "6": lambda: self._pageJump(6),
                "7": lambda: self._pageJump(7),
                "8": lambda: self._pageJump(8),
                "9": lambda: self._pageJump(9),
            }, -1
        )

        self._artworkPollTimer = eTimer()
        self._artworkPollTimer.callback.append(self._pollArtworkCache)

        self._clockTimer = eTimer()
        self._clockTimer.callback.append(self._tickClock)
        self.onLayoutFinish.append(self._startClock)

        self.onLayoutFinish.append(self._init)

    def _init(self):
        for _r in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                try:
                    self["pic_%d_%d" % (_r, _c)].instance.setScale(1)
                except Exception:
                    pass
        self._applyCarouselGeometry()
        self._showHome()
        # [UX-1] cold-open focus on the continue strip when populated
        if self._cold_open and self._cont_items:
            self._focus_zone = "row"
            self._cont_index = 0
            self._moveContinueSel()
        self._cold_open = False
        # [UX-19] start the health-dot pulse
        try:
            self._pulse_timer.start(1000, False)
        except Exception:
            pass

    def _startClock(self):
        self._tickClock()
        try:
            self._clockTimer.start(1000, False)
        except Exception as e:
            my_log("clock timer start failed: {}".format(e))

    def _tickClock(self):
        try:
            greg, hijri, t, period = format_dual_dates()
            self["clock_time"].setText(t)
            self["clock_period"].setText(period)
            self["clock_greg"].setText(greg)
            self["clock_hijri"].setText(hijri)
        except Exception as e:
            my_log("clock tick error: {}".format(e))

    # ── [UX-17] status auto-timeout ─────────────────────────────────────
    def _setStatus(self, text, warning=False):
        try:
            self["status"].setText(text)
        except Exception:
            return
        self._status_is_warning = bool(warning)
        try:
            self._status_timer.stop()
        except Exception:
            pass
        if text and not warning:
            try:
                self._status_timer.start(6000, True)
            except Exception:
                pass

    def _clearStatusIfTransient(self):
        if not self._status_is_warning:
            try:
                self["status"].setText("")
            except Exception:
                pass

    # ── [PATCH G4] proactive health sweep ────────────────────────────────
    def _startHealthSweep(self):
        if getattr(self, "_health_swept", False):
            return
        self._health_swept = True
        try:
            pending = [it["_action"][5:] for it in self._items
                       if it.get("_action", "").startswith("site_")
                       and plugin_health.get(it["_action"][5:]) == "unknown"]
        except Exception:
            pending = []
        if not pending:
            return

        def _worker():
            for site_key in pending:
                try:
                    ex = _get_extractor(site_key)
                    cats = getattr(ex, "get_categories", None)
                    if cats:
                        if site_key in ("egydead", "egydead_coupons"):
                            cats("movie")
                        else:
                            cats()
                    plugin_health.record(site_key, "ok")
                except Exception as e:
                    plugin_health.record(site_key, "down", str(e))
                callInMainThread(self._onHealthSweepResult, site_key)

        threading.Thread(target=_worker, daemon=True).start()

    def _onHealthSweepResult(self, site_key):
        if self._display_mode != "home":
            return
        try:
            changed = False
            for it in self._items:
                if it.get("_action") == "site_" + site_key:
                    it["_health"] = plugin_health.get(site_key)
                    changed = True
                    break
            if changed:
                self["home_grid"]._redraw()
        except Exception:
            pass

    # ── [UX-19] pulse blocked-site health dots ──────────────────────────
    def _pulseTick(self):
        self._pulse_on = not self._pulse_on
        if self._display_mode == "home":
            try:
                grid = self["home_grid"]
                grid.pulse_on = self._pulse_on
                grid._redraw()
            except Exception:
                pass

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
            widget_id = logical_slot
            x, y, w, h = _CAROUSEL_GEOMETRY.get(logical_slot, (0, 0, 1, 1))
            is_big = (logical_slot == self.carousel_center)
            pad = 12 if is_big else 8
            self._moveResize('cposter%d' % widget_id, x, y, w, h)
            self._moveResize('cposterImg%d' % widget_id, x + pad, y + pad, max(1, w - pad * 2), max(1, h - pad * 2))
            rb_w = 38 if is_big else 34
            rb_h = 40 if is_big else 34
            if is_big:
                rb_w = int(rb_w * 1.3)
                rb_h = int(rb_h * 1.25)
            self._moveResize('cratingBadge%d' % widget_id, x + w - rb_w - 14, y + 22, rb_w, rb_h)
            self._moveResize('cfavMark%d' % widget_id, x + 10, y + 10, 42, 42)
            _yw = 58 if is_big else 52
            self._moveResize('cyearBadge%d' % widget_id, x + 12, y + 22, _yw, 28)
            _lh = 26 if is_big else 22
            self._moveResize('clabel%d' % widget_id, x + 10, y + h - 34 - _lh - 2, w - 20, _lh)
            self._moveResize('cresumeMark%d' % widget_id, x + 10, y + h - 34, w - 20, 30)
            if logical_slot == self.carousel_center:
                focus_extra = 7
                self._moveResize('cfocus%d' % self.carousel_center, x - focus_extra, y - focus_extra, w + (focus_extra * 2), h + (focus_extra * 2))
            else:
                self._moveResize('cfocus%d' % logical_slot, 0, 0, 1, 1)

    # ── Home ───────────────────────────────────────────────────────────
    def _showHome(self):
        self._source = "home"
        self._display_mode = "home"
        self._page = 1
        self._nav_stack = []
        self["title_text"].setText("NovaPlay Media Center  v{}".format(_PLUGIN_VERSION))
        self["status"].setText("")

        # [UX-9/20] decorate site tiles with recent-activity markers
        raw_sites = list(get_home_sites())
        try:
            hist = _history_items() or []
        except Exception:
            hist = []
        recent_sites = {}
        _now = time.time()
        for h in hist[:200]:
            s = h.get("_site")
            if not s:
                continue
            try:
                ts = float(h.get("_ts") or 0)
            except Exception:
                ts = 0
            if ts and (_now - ts) < 86400:
                recent_sites[s] = recent_sites.get(s, 0) + 1

        # [PATCH G4] health (ok/unknown > blocked > down) then 24h recency, stable sort
        # so equal-rank sites keep their existing relative order.
        _HEALTH_SORT_RANK = {"blocked": 1, "down": 2}
        visible_sites = [(key, title, tagline) for key, title, tagline in raw_sites
                          if not _is_site_hidden(key)]
        visible_sites.sort(key=lambda row: (
            _HEALTH_SORT_RANK.get(plugin_health.get(row[0]), 0),
            -recent_sites.get(row[0], 0),
        ))

        site_items = []
        for key, title, tagline in visible_sites:
            site_items.append({
                "title": title,
                "tagline": tagline,
                "_action": "site_" + key,
                "_health": plugin_health.get(key),
                "_visited": key in recent_sites,
                "_fresh": recent_sites.get(key, 0) >= 3,
            })

        self._items = site_items
        self["home_grid"].setList(self._items)
        self._startHealthSweep()  # [PATCH G4]

        self._showHomeMode()
        self._onGridSelectionChanged()

        blocked = plugin_health.blocked_sites()
        if blocked:
            self._setStatus("⚠ %d/%d محجوب — فعّل البروكسي" % (
                len(blocked), len(site_items)), warning=True)

    def _showHomeMode(self):
        self._focus_zone = "grid"
        try:
            self._artworkPollTimer.start(1000, False)
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
        try: self["pgridSel"].hide()
        except Exception: pass
        for i in range(self.carousel_slots):
            self["cfocus%d" % i].hide()
            self["cposter%d" % i].hide()
            self["cposterImg%d" % i].hide()
            self["cfavMark%d" % i].hide()
            self["cratingBadge%d" % i].hide()
            self["cyearBadge%d" % i].hide()
            self["clabel%d" % i].hide()
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
            try:
                self["contbartrack%d" % i].hide()
                self["contbar%d" % i].hide()
            except Exception:
                pass
        self["cont_title"].hide()
        try: self["contSel"].hide()
        except Exception: pass
        try: self["pgridSel"].hide()
        except Exception: pass
        for i in range(self.carousel_slots):
            self["cfocus%d" % i].hide()
            self["cposter%d" % i].hide()
            self["cposterImg%d" % i].hide()
            self["cfavMark%d" % i].hide()
            self["cratingBadge%d" % i].hide()
            self["cyearBadge%d" % i].hide()
            self["clabel%d" % i].hide()
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
        self._artworkPollTimer.start(1000, False)

    def _carouselPositionForSlot(self, slot):
        # [UX-23] direct index math, no widget_map shuffling
        total = len(self._items)
        if total <= 0:
            return -1
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
            self["clabel%d" % widget_id].hide()
            self["cresumeMark%d" % widget_id].hide()
            return
        item = self._items[pos]
        self["cposter%d" % widget_id].show()
        if item.get("_is_next_page"):
            self["cposterImg%d" % widget_id].hide()
            self["cratingBadge%d" % widget_id].hide()
            self["cyearBadge%d" % widget_id].hide()
            self["clabel%d" % widget_id].hide()
            self["cfavMark%d" % widget_id].hide()
            self["cresumeMark%d" % widget_id].hide()
            self["cposter%d" % widget_id].setText("الصفحة التالية")
            return
        if item.get("_is_prev_page"):
            self["cposterImg%d" % widget_id].hide()
            self["cratingBadge%d" % widget_id].hide()
            self["cyearBadge%d" % widget_id].hide()
            self["clabel%d" % widget_id].hide()
            self["cfavMark%d" % widget_id].hide()
            self["cresumeMark%d" % widget_id].hide()
            self["cposter%d" % widget_id].setText("الصفحة السابقة")
            return
        self["cposter%d" % widget_id].setText("")
        url = item.get("poster") or ""
        path = plugin_imagecache.getCachedImage(url, target_size=(340, 510)) if url else ""
        if path:
            try:
                self._pix.set(self["cposterImg%d" % widget_id], ("car", widget_id), path)
                self["cposterImg%d" % widget_id].show()
            except Exception:
                self["cposterImg%d" % widget_id].hide()
        else:
            ph = placeholder_for_item(item)
            if ph:
                try:
                    self._pix.setScaled(self["cposterImg%d" % widget_id], ("car", widget_id), ph, scale=1)
                    self["cposterImg%d" % widget_id].show()
                except Exception:
                    self["cposterImg%d" % widget_id].hide()
            else:
                self["cposterImg%d" % widget_id].hide()
            if url:
                plugin_imagecache.requestImageAsync(url, target_size=(340, 510))
        if _is_favorite(item.get("url", "")):
            self["cfavMark%d" % widget_id].setText("★")
            self["cfavMark%d" % widget_id].show()
        else:
            self["cfavMark%d" % widget_id].hide()
        rating = item.get("rating", "")
        if rating:
            self["cratingBadge%d" % widget_id].setText(u"★ %s " % rating)
            self["cratingBadge%d" % widget_id].show()
        else:
            self["cratingBadge%d" % widget_id].hide()
        _cy = str(item.get("year") or "")[:4]
        if _cy:
            self["cyearBadge%d" % widget_id].setText(_cy)
            self["cyearBadge%d" % widget_id].show()
        else:
            self["cyearBadge%d" % widget_id].hide()
        _lb = str(item.get("label") or "").strip()
        if _lb:
            self["clabel%d" % widget_id].setText(_lb)
            self["clabel%d" % widget_id].show()
        else:
            self["clabel%d" % widget_id].hide()
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
        for widget_id in range(self.carousel_slots):
            pos = self._carouselPositionForSlot(widget_id)
            self._updateCarouselSlot(widget_id, pos)
        self._updateBackdrop()

    def _moveCarousel(self, delta):
        # [UX-23] simple index bump; no widget_map shuffling
        total = len(self._items)
        if total <= 0:
            return
        self.index = (self.index + delta) % total
        self._applyCarouselGeometry()
        self._paintCarousel()

    def _syncCarouselFocusVisible(self):
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
            grid, cols, rows = self["text_list"], self["text_list"].cols, self["text_list"].rows
        else:
            grid, cols, rows = self["home_grid"], HOME_GRID_COLS, HOME_GRID_ROWS
        page, total_pages = grid.getPageInfo()
        total = len(self._items)
        per_page = cols * rows
        start = (page - 1) * per_page + 1
        end = min(start + per_page - 1, total)
        self["grid_status_left"].setText("Shows {}-{} / {}".format(start, end, total))
        # [UX-10] paging-keys hint
        self["grid_status_right"].setText("Page {} / {}  •  1–9 للتنقل".format(page, total_pages))

    def _updatePosterPixmaps(self):
        try:
            _g = self["poster_grid"]
            if (self._display_mode == "poster" and self._layout_style == "grid"
                    and 0 <= _g.currentIndex < len(self._items)):
                _sel_i = _g.currentIndex - _g._getPageStart()
                _sr = _sel_i // _g.cols
                _scol = _sel_i % _g.cols
                _sx_base = (self._POSTER_GRID_X + _scol * POSTER_CELL_W
                            + POSTER_CELL_MARGIN_H)
                _sy_base = (self._POSTER_GRID_Y + _sr * POSTER_CELL_H
                            + POSTER_CELL_MARGIN_V)
                _zw_s = int(POSTER_W * 1.08)
                _zh_s = int(POSTER_H * 1.08)
                _zx_s = _sx_base - (_zw_s - POSTER_W) // 2
                _zy_s = _sy_base - (_zh_s - POSTER_H) // 2
                self._moveResize("pgridSel", _zx_s + _zw_s + 2, _zy_s, 6, _zh_s)
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
                    self._pix.forget(("poster", r, c))
                    continue

                _base_x = (self._POSTER_GRID_X + c * POSTER_CELL_W
                           + POSTER_CELL_MARGIN_H)
                _base_y = (self._POSTER_GRID_Y + r * POSTER_CELL_H
                           + POSTER_CELL_MARGIN_V)
                try:
                    _g = self["poster_grid"]
                    _idx = _g._getPageStart() + r * _g.cols + c
                    _is_sel = (_idx == _g.currentIndex)
                except Exception:
                    _is_sel = False
                if _is_sel:
                    _zw = int(POSTER_W * 1.08)
                    _zh = int(POSTER_H * 1.08)
                    _zx = _base_x - (_zw - POSTER_W) // 2
                    _zy = _base_y - (_zh - POSTER_H) // 2
                else:
                    _zw, _zh = POSTER_W, POSTER_H
                    _zx, _zy = _base_x, _base_y

                url = item.get("poster") or ""
                path = ""
                if url:
                    path = plugin_imagecache.getCachedImage(url, target_size=(POSTER_W, POSTER_H))
                if path and item.get("url"):
                    try:
                        from plugin_watched import is_watched
                        if is_watched(item.get("url")):
                            _bw = plugin_imagecache.getBwVariant(url, (POSTER_W, POSTER_H))
                            if _bw:
                                path = _bw
                    except Exception:
                        pass
                if path:
                    try:
                        self._pix.set(widget, ("poster", r, c), path)
                        widget.show()
                        widget.instance.move(ePoint(_zx, _zy))
                        widget.instance.resize(eSize(_zw, _zh))
                    except Exception:
                        widget.hide()
                else:
                    ph = placeholder_for_item(item)
                    if ph:
                        try:
                            self._pix.setScaled(widget, ("poster", r, c), ph, scale=1)
                            widget.show()
                            widget.instance.move(ePoint(_zx, _zy))
                            widget.instance.resize(eSize(_zw, _zh))
                        except Exception:
                            widget.hide()
                    else:
                        widget.hide()
                    if url:
                        plugin_imagecache.requestImageAsync(url, target_size=(POSTER_W, POSTER_H))

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

                _in_grid = (self._display_mode == "poster" and self._layout_style == "grid")
                _watched = bool(item.get("_is_watched"))
                saved_pos = int(item.get("_watch_pos") or 0)
                _pad = sc(6)

                _yb_x = _zx + _pad
                _yb_y = _zy + _pad
                _rb_x = _zx + _zw - POSTER_RATE_W - _pad
                _rb_y = _zy + _pad
                _bot_x = _zx
                _bot_y = _zy + _zh - POSTER_BADGE_H
                _bot_w = _zw
                _bar_x = _zx + sc(4)
                _bar_y = _zy + _zh - POSTER_BADGE_H - sc(12)
                _bar_max_w = _zw - sc(8)

                yb = self["pyear_%d_%d" % (r, c)]
                _yr = str(item.get("year") or "")[:4]
                if _yr and _in_grid:
                    yb.setText(_yr)
                    try:
                        yb.instance.move(ePoint(_yb_x, _yb_y))
                        yb.instance.resize(eSize(POSTER_YEAR_W, POSTER_YEAR_H))
                    except Exception:
                        pass
                    yb.show()
                else:
                    yb.hide()

                rb = self["prat_%d_%d" % (r, c)]
                _rt = str(item.get("rating") or "").strip()
                try:
                    _rtv = float(_rt)
                except Exception:
                    _rtv = 0.0
                if _rtv > 0 and _in_grid:
                    rb.setText(u"★ " + _rt[:3])
                    try:
                        rb.instance.move(ePoint(_rb_x, _rb_y))
                        rb.instance.resize(eSize(POSTER_RATE_W, POSTER_RATE_H))
                    except Exception:
                        pass
                    rb.show()
                else:
                    rb.hide()

                _lb = str(item.get("label") or "").strip()
                if _lb and _in_grid:
                    badge.setText(_lb)
                elif _watched and _in_grid:
                    badge.setText(u"✓ شاهدته")
                elif saved_pos > 30 and _in_grid:
                    mm, ss = divmod(saved_pos, 60)
                    hh, mm = divmod(mm, 60)
                    tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
                    badge.setText("متابعة " + tstr)
                else:
                    badge.hide()

                if _in_grid and (_lb or _watched or saved_pos > 30):
                    try:
                        badge.instance.move(ePoint(_bot_x, _bot_y))
                        badge.instance.resize(eSize(_bot_w, POSTER_BADGE_H))
                    except Exception:
                        pass
                    badge.show()
                else:
                    badge.hide()

                bar = self["pbar_%d_%d" % (r, c)]
                if (not _watched) and saved_pos > 30 and _in_grid:
                    try:
                        inst = bar.instance
                        if inst is not None:
                            _pct = item.get("_watch_pct") or (35 if saved_pos > 600 else 20)
                            _pct = min(100, max(10, int(_pct)))
                            _w = max(sc(12), (_bar_max_w * _pct) // 100)
                            inst.move(ePoint(_bar_x, _bar_y))
                            inst.resize(eSize(_w, sc(10)))
                        bar.show()
                    except Exception:
                        bar.hide()
                else:
                    bar.hide()

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
                except Exception as e:
                    my_log("_updateBackdrop: setPixmapFromFile threw for {}: {}".format(path, e))
            elif not path:
                plugin_imagecache.requestImageAsyncPriority(target_url, target_size=target_size)

    def _bgFetchTmdbText(self, item, token):
        try:
            meta = _tmdb_search_metadata(item.get("title", ""), item.get("year", ""), item.get("type", "movie"))
            if token == self._tmdb_token:
                callInMainThread(self._paintTmdbText, meta, token)
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
        if token != self._tmdb_token:
            return
        if meta:
            _item = self._items[self.index] if (0 <= getattr(self, "index", -1) < len(self._items)) else None
            _r = (_item or {}).get("rating") or meta.get("rating", "N/A")
            _y = (_item or {}).get("year") or meta.get("year", "")
            meta_str = "★ %s  |  %s  |  %s" % (_r, _y, meta.get("genres", ""))
            self["info_meta"].setText(meta_str)
            self["info_plot"].setText(meta.get("plot", ""))
        else:
            self["info_meta"].setText("")
            self["info_plot"].setText("")

    def _paintTmdbBackdrop(self, path, token):
        if token == self._tmdb_token and path:
            try:
                self["backdropImg"].instance.setPixmapFromFile(path)
                self["backdropImg"].show()
                self._current_backdrop_path = path
            except Exception as e:
                my_log("_paintTmdbBackdrop: setPixmapFromFile threw for {}: {}".format(path, e))

    # ── [UX-24] continue-strip repaint (memoized) ──────────────────────
    def _refreshContinuePosters(self):
        try:
            items = getattr(self, "_cont_items", []) or []
            for i in range(min(CONT_SLOTS, len(items))):
                u = items[i].get("poster") or ""
                if not u:
                    continue
                p = plugin_imagecache.getCachedImage(u, target_size=(CONT_W, CONT_H))
                if not p:
                    continue
                self._pix.setScaled(self["cont%d" % i], ("cont", i), p, scale=1)
        except Exception:
            pass

    def _pollArtworkCache(self):
        if self._display_mode == "home":
            if getattr(self, "_cont_items", None):
                self._refreshContinuePosters()
            return
        if self._display_mode != "poster":
            return
        if self._layout_style == "grid":
            self._updatePosterPixmaps()
            self._updateGridFooter()
            self._updateBackdrop(skip_tmdb=True)
        else:
            for widget_id in range(self.carousel_slots):
                pos = self._carouselPositionForSlot(widget_id)
                self._updateCarouselSlot(widget_id, pos)
            self._updateBackdrop(skip_tmdb=True)

    def _onGridSelectionChanged(self):
        self._updateIcons()
        if self._display_mode == "list":
            self._updateGridFooter()

    def _updateIcons(self):
        for _r in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                self["pic_%d_%d" % (_r, _c)].hide()
        if self._display_mode != "home":
            return
        for row, col, item in self["home_grid"].getPageItems():
            icon_path = resolve_icon_path(item, PLUGIN_PATH)
            if not icon_path:
                continue
            widget = self["pic_%d_%d" % (row, col)]
            try:
                self._pix.set(widget, ("icon", row, col), icon_path)
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
            track = self["contbartrack%d" % i]
            bar = self["contbar%d" % i]
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
                        self._pix.setScaled(widget, ("cont", i), path, scale=1)
                        widget.show()
                    except Exception:
                        widget.hide()
                else:
                    widget.hide()

                # [PATCH 13] resume-time badge
                pos = int(item.get("last_position_sec") or 0)
                mm, ss = divmod(pos, 60)
                hh, mm = divmod(mm, 60)
                tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
                badge.setText(tstr)
                badge.show()

                # [UX-2] watch-progress percentage
                dur = int(item.get("total_duration")
                          or item.get("duration")
                          or item.get("total_seconds") or 0)
                if dur > 0:
                    self._cont_pct[i] = min(99, max(1, int(pos * 100 / dur)))
                else:
                    self._cont_pct[i] = 35 if pos > 600 else (20 if pos > 30 else 0)
            else:
                widget.hide()
                badge.hide()
                track.hide()
                bar.hide()
                self._cont_pct[i] = 0
        if self._cont_items:
            self["cont_title"].show()
            if self._focus_zone == "row":
                self._moveContinueSel()
            else:
                self._applyContinueLayout(None)
                try: self["contSel"].hide()
                except Exception: pass
            self._updateContinueLabel()
        else:
            # [UX-4] empty-strip hint
            self["cont_title"].setText("لا يوجد محتوى قيد المشاهدة — ابدأ من الشبكة أدناه")
            self["cont_title"].show()
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

    def _applyContinueLayout(self, zoom_index=None):
        # [UX-2] also places the progress track + fill per slot
        _bar_h = sc(6)
        for i in range(CONT_SLOTS):
            base_x = CONT_X0 + i * (CONT_W + CONT_GAP)
            if i == zoom_index:
                zw = int(CONT_W * 1.08)
                zh = int(CONT_H * 1.08)
                zx = base_x - (zw - CONT_W) // 2
                zy = CONT_Y - (zh - CONT_H) // 2
                self._moveResize("cont%d" % i, zx, zy, zw, zh)
                self._moveResize("contbadge%d" % i, zx, zy + zh - sc(28), zw, sc(28))
                _bx = zx + sc(6)
                _by = zy + zh - 26 - _bar_h - sc(6)
                _bw = zw - sc(12)
            else:
                self._moveResize("cont%d" % i, base_x, CONT_Y, CONT_W, CONT_H)
                self._moveResize("contbadge%d" % i, base_x,
                                 CONT_Y + CONT_H - sc(28), CONT_W, sc(28))
                _bx = base_x + sc(6)
                _by = CONT_Y + CONT_H - 26 - _bar_h - sc(6)
                _bw = CONT_W - sc(12)
            pct = self._cont_pct.get(i, 0) if i < len(self._cont_items) else 0
            try:
                if i < len(self._cont_items):
                    self._moveResize("contbartrack%d" % i, _bx, _by, _bw, _bar_h)
                    self["contbartrack%d" % i].show()
                    _fill = max(sc(4), (_bw * pct) // 100) if pct > 0 else 0
                    if _fill > 0:
                        self._moveResize("contbar%d" % i, _bx, _by, _fill, _bar_h)
                        self["contbar%d" % i].show()
                    else:
                        self["contbar%d" % i].hide()
                else:
                    self["contbartrack%d" % i].hide()
                    self["contbar%d" % i].hide()
            except Exception:
                pass

    def _resetContinueZoom(self):
        self._applyContinueLayout(None)
        try: self["contSel"].hide()
        except Exception: pass
        # [UX-3] leaving the row clears the hero backdrop
        try:
            self["backdropImg"].hide()
            self["shade_overlay"].hide()
            self._current_backdrop_path = ""
        except Exception:
            pass

    def _moveContinueSel(self):
        if not (0 <= self._cont_index < len(self._cont_items)):
            self._resetContinueZoom()
            return
        self._applyContinueLayout(self._cont_index)
        bar_w = 6
        zw = int(CONT_W * 1.08)
        zh = int(CONT_H * 1.08)
        base_x = CONT_X0 + self._cont_index * (CONT_W + CONT_GAP)
        zx = base_x - (zw - CONT_W) // 2
        zy = CONT_Y - (zh - CONT_H) // 2
        self._moveResize("contSel", zx + zw + 2, zy, bar_w, zh)
        self["contSel"].show()
        self._updateContinueLabel()
        self._paintContinueBackdrop()

    # ── [UX-3] hero fanart backdrop for the focused continue card ──────
    def _paintContinueBackdrop(self):
        if not (0 <= self._cont_index < len(self._cont_items)):
            return
        self._cont_backdrop_token += 1  # [PATCH G5] invalidate any in-flight fetch for a prior card
        item = self._cont_items[self._cont_index]
        url = item.get("fanart") or item.get("backdrop") or ""
        if not url:
            self["backdropImg"].hide()
            self["shade_overlay"].hide()
            self._current_backdrop_path = ""
            # [PATCH G5] no stored backdrop URL at all -- continue entries never carry one
            # (see plugin_state._entry_from_item). Fall back to a TMDB lookup, same source
            # _updateBackdrop already uses for the separate site-detail view, so the hero
            # backdrop actually has something to show instead of staying permanently blank.
            if not item.get("_backdrop_fetch_tried"):
                item["_backdrop_fetch_tried"] = True
                token = self._cont_backdrop_token
                cont_index = self._cont_index
                threading.Thread(
                    target=self._bgFetchContinueBackdrop,
                    args=(item, cont_index, token), daemon=True).start()
            return
        target = (1920, 1080)
        path = plugin_imagecache.getCachedImage(url, target_size=target)
        if path and path != self._current_backdrop_path:
            try:
                self["backdropImg"].instance.setPixmapFromFile(path)
                self["backdropImg"].show()
                self["shade_overlay"].show()
                self._current_backdrop_path = path
            except Exception as e:
                my_log("continue backdrop paint failed: {}".format(e))
        elif not path:
            plugin_imagecache.requestImageAsyncPriority(url, target_size=target)

    def _bgFetchContinueBackdrop(self, item, cont_index, token):
        # [PATCH G5] mirrors _bgFetchTmdbText's fetch/cache sequence, scoped to the
        # continue strip via its own token instead of self._tmdb_token (a different,
        # unrelated selection context -- the site-detail poster grid).
        try:
            meta = _tmdb_search_metadata(item.get("title", ""), item.get("year", ""),
                                         item.get("type", "movie"))
            if not (meta and meta.get("backdrop_url")):
                return
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
                        if plugin_imagecache.writeFileAtomic(cache_path, processed):
                            path = cache_path
            if path:
                callInMainThread(self._paintContinueTmdbBackdrop, path, cont_index, token)
        except Exception as e:
            my_log("_bgFetchContinueBackdrop error: {}".format(e))

    def _paintContinueTmdbBackdrop(self, path, cont_index, token):
        # [PATCH G5] only paint if the user is still on the same continue card and no
        # newer fetch has started since (e.g. they arrowed to a different card while
        # this one was still loading).
        if token != self._cont_backdrop_token or self._cont_index != cont_index or not path:
            return
        try:
            self["backdropImg"].instance.setPixmapFromFile(path)
            self["backdropImg"].show()
            self["shade_overlay"].show()
            self._current_backdrop_path = path
        except Exception as e:
            my_log("_paintContinueTmdbBackdrop: setPixmapFromFile threw for {}: {}".format(path, e))

    # ── [UX-5] MENU handling — FIXED with ChoiceBox ─────────────────────
    def _onMenu(self):
        # Continue-row: MENU on a card → remove from continue
        if (self._display_mode == "home" and self._focus_zone == "row"
                and 0 <= self._cont_index < len(self._cont_items)):
            item = self._cont_items[self._cont_index]
            url = item.get("url", "")
            title = item.get("title", "")
            if not url:
                return

            def _confirm(ans, _url=url, _title=title):
                if not ans:
                    return
                try:
                    _clear_continue_item(_url)
                except Exception as e:
                    my_log("clear continue item failed: {}".format(e))
                self._paintContinueRow()
            self.session.openWithCallback(
                _confirm, MessageBox,
                u"إزالة من متابعة المشاهدة؟\n{}".format(
                    _single_line_text(title, width=40, fallback="عنصر")),
                MessageBox.TYPE_YESNO, timeout=8, default=False)
            return

        # Site tile: MENU on a grid tile → quick actions
        if self._display_mode == "home" and self._focus_zone == "grid":
            item = self["home_grid"].getCurrent()
            if not item:
                return
            a = item.get("_action", "")
            if not a.startswith("site_"):
                return
            site_key = a.replace("site_", "")
            label = item.get("title", site_key)

            # [UX-5 FIX] ChoiceBox returns the selected tuple, not True/False
            _opts = [
                ("فتح الموقع",        "open"),
                ("اختبار الاتصال",    "ping"),
                ("تفعيل البروكسي",    "proxy"),
                ("مسح من الشبكة",    "hide"),
            ]

            def _choice(ans, _sk=site_key, _lbl=label):
                if ans is None:
                    return
                _key = ans[1] if isinstance(ans, tuple) else None
                if _key == "open":
                    self._site = _sk
                    self._showSiteCategories()
                elif _key == "ping":
                    self._quickPingSite(_sk, _lbl)
                elif _key == "proxy":
                    try:
                        _set_config("use_proxy_for_" + _sk, "true")
                        self._setStatus("فُعّل البروكسي لـ {}".format(_lbl))
                    except Exception:
                        pass
                elif _key == "hide":
                    try:
                        _hide_site(_sk)
                    except Exception:
                        pass
                    self._showHome()

            self.session.openWithCallback(
                _choice, ChoiceBox,
                title=u"{} — ماذا تريد؟".format(label),
                list=_opts)
            return

    def _quickPingSite(self, site_key, label):
        self._setStatus("جاري اختبار {}…".format(label))

        def _worker():
            try:
                ex = _get_extractor(site_key)
                cats = getattr(ex, "get_categories", None)
                if cats:
                    if site_key in ("egydead", "egydead_coupons"):
                        cats("movie")
                    else:
                        cats()
                plugin_health.record(site_key, "ok")
                callInMainThread(self._setStatus, "{}: OK".format(label))
            except Exception as e:
                plugin_health.record(site_key, "down", str(e))
                callInMainThread(self._setStatus, "{}: فشل".format(label), True)
        threading.Thread(target=_worker, daemon=True).start()

    def _pageJump(self, page_num):
        if self._display_mode == "poster":
            if self._layout_style != "grid":
                return
            grid = self["poster_grid"]
        elif self._display_mode == "list":
            grid = self["text_list"]
        else:
            return
        try:
            target = max(0, min(int(page_num) - 1, grid.totalPages - 1))
            if target == grid.currentPage:
                return
            grid.currentPage = target
            grid.currentRow = 0
            grid.currentCol = 0
            grid._updateIndex()
            grid._redraw()
            grid._notify()
            self._updateGridFooter()
            self._setStatus("صفحة {} / {}".format(target + 1, grid.totalPages))
        except Exception as e:
            my_log("pageJump error: {}".format(e))

    # ── Key handlers ────────────────────────────────────────────────────
    def _onOk(self):
        if self._display_mode == "home":
            if self._focus_zone == "row" and self._cont_items:
                item = self._cont_items[min(self._cont_index, len(self._cont_items) - 1)]
                self._focus_zone = "grid"
                self._resetContinueZoom()
                if item:
                    self._openItem(item)
                return
            item = self["home_grid"].getCurrent()
            if not item:
                return
            a = item.get("_action", "")
            if a.startswith("site_"):
                self._site = a.replace("site_", "")
                self._showSiteCategories()
            return
        if self._display_mode == "list":
            item = self["text_list"].getCurrent()
            if not item:
                return
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
                if item:
                    self._openItem(item)
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
            return
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
            pass
        elif self._display_mode == "poster":
            if self._layout_style == "grid":
                self["poster_grid"].moveLeft()
            else:
                self._moveCarousel(-1)

    def _navRight(self):
        if self._display_mode == "home" and self._focus_zone == "row":
            if self._cont_items:
                self._cont_index = (self._cont_index + 1) % len(self._cont_items)
                self._moveContinueSel()
            return
        if self._display_mode == "home":
            self["home_grid"].moveRight()
        elif self._display_mode == "list":
            pass
        elif self._display_mode == "poster":
            if self._layout_style == "grid":
                self["poster_grid"].moveRight()
            else:
                self._moveCarousel(1)

    def _setList(self, items):
        show_adult = _get_config("show_adult", "false") == "true"
        adult_words = ["18+", "للكبار", "سكس", "xxx", "adult", "إباح", "sex"]
        if not show_adult:
            items = [i for i in items if not any(w in (i.get("title", "") + i.get("category_name", "")).lower() for w in adult_words)]
        content_items = [i for i in items if i.get("type") in ("movie", "series", "episode")]
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
                try:
                    self["contbartrack%d" % i].hide()
                    self["contbar%d" % i].hide()
                except Exception:
                    pass
            self["cont_title"].hide()
            try: self["contSel"].hide()
            except Exception: pass
            try: self["pgridSel"].hide()
            except Exception: pass

            self["backdropImg"].hide()
            self["shade_overlay"].hide()
            self["content_title"].setText("")
            self["info_meta"].setText("")
            self["info_plot"].setText("")
            self._current_backdrop_path = ""

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
                self["clabel%d" % i].hide()
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
            self._setStatus("{} عنصر".format(len(items)))
            self["key_red"].setText("رجوع")
            self["key_green"].setText("")
            self["key_yellow"].setText("بحث")
            self["key_blue"].setText("")
            self._updateGridFooter()

    def _showSiteCategories(self):
        cached = self._cats_cache.get(self._site)
        if cached is not None:
            self._source = "categories"
            self._setList(cached)
            self["title_text"].setText("تصنيفات {}".format(_site_label(self._site)))
            self._setStatus("اختر القسم")
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
        self._setStatus("اختر القسم")

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
        self._setStatus("جاري تحميل {}…".format(name))
        threading.Thread(target=self._bgLoadCategory, args=(url,), daemon=True).start()

    def _bgLoadCategory(self, url):
        try:
            extractor = _get_extractor(self._site)
            get_category_items = getattr(extractor, "get_category_items", None)
            if not get_category_items:
                return
            if self._site in ["egydead", "egydead_coupons", "fasel", "faselhdx"]:
                items = get_category_items(url, page=self._page)
            else:
                items = get_category_items(url)
            plugin_health.record(self._site, "ok")
            callInMainThread(self._onCategoryLoaded, items)
        except Exception as e:
            plugin_health.record(self._site, "down", str(e))
            callInMainThread(self._setStatus, "فشل: {}".format(str(e)[:60]), True)

    def _onCategoryLoaded(self, items):
        if not items:
            self._setStatus("لا توجد نتائج")
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
        if kind == "favorites":
            items = _get_favorite_items_list()
        else:
            items = _history_items()
        self._source = "library"
        self._setList(items)
        self["title_text"].setText("المفضلة" if kind == "favorites" else "السجل")
        self._setStatus("")

    def _onSearch(self, forced_scope=None):
        self.session.openWithCallback(self._onSearchQuery, AdvancedArabicPlayerSearch, current_site=self._site, default_scope=forced_scope or "all", query=self._last_query)

    def _onSearchQuery(self, result=None):
        if not result:
            return
        if isinstance(result, str):
            query, scope = result, "all"
        else:
            query = result[0]
            scope = result[1] if len(result) > 1 else "all"
        if not query:
            return
        self._last_query = query
        self._setStatus("بحث عن: {}…".format(query))
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
        sites = list(_SEARCH_SITE_ORDER) if scope in ("all", "", None) else [scope]
        with ThreadPoolExecutor(max_workers=min(6, len(sites))) as ex:
            futures = [ex.submit(_search_one, name) for name in sites]
            for future in as_completed(futures):
                name, results = future.result()
                items.extend(results)

        callInMainThread(self._onSearchResults, items, query)

    def _onSearchResults(self, items, query):
        if not items:
            self._setStatus("لا توجد نتائج")
            return
        self._source = "search"
        self._setList(_rank_search_items(items, query))

    def _openItem(self, item):
        self.session.open(AdvancedArabicPlayerDetail, item=item, site=item.get("_site", self._site), m_type=item.get("type", self._m_type))

    def _nextPage(self):
        next_url = getattr(self, "_next_page_url", None)
        cat_url = getattr(self, "_cat_url", None)
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
            self._setStatus("جاري تحميل {}…".format(cat_name))
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
            return
        if self._display_mode == "home":
            if self._cont_items:
                self._focus_zone = "row"
                self._moveContinueSel()
            return
        if self._display_mode == "list":
            self._showLibrary("favorites")

    def _onBlue(self):
        if self._source == "home":
            self._openSettings()
        else:
            self._nextPage()

    def _onBack(self):
        if self._source == "library":
            self._showHome()
            return
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
        try: self._clockTimer.stop()
        except: pass
        try: self._status_timer.stop()
        except: pass
        try: self._pulse_timer.stop()
        except: pass
        try: plugin_imagecache.cancelAsyncImages()
        except: pass


def _get_favorite_items_list():
    from plugin_state import _favorite_items as _fi
    return _fi()


def _get_extractor(site):
    return get_extractor(site)