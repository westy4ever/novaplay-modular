# -*- coding: utf-8 -*-
"""NovaPlay — Home screen.

FINAL SET of UX features kept from the batch:
  [UX-1]  Cold-open focus lands on the continue strip when populated.
  [UX-2]  Per-continue-card watch-progress bar.
  [UX-3]  Hero fanart backdrop while the strip is focused.
  [UX-4]  Empty-strip hint text.
  [UX-5]  MENU on a site tile → ChoiceBox quick-actions.
  [UX-10] Paging-keys hint in the footer.
  [UX-12] Wrap-around arrow navigation (grid layer).
  [UX-17] Status auto-timeout (6s) for non-warning text.
  [UX-19] Pulsing blocked-site health dot.
  [UX-20] Dim visited tiles.
  [UX-23] Carousel index math (no widget_map shuffling).
  [UX-24] Unified MemoizedPixmapCache.

[Option A] Tile strip layout: title has its own band between the dots
and the site-logo PNG. Tagline removed.

[pop-out] Selected home tile: cyan glow ring behind the tile + enlarged
icon pixmap.

[pop-anim] Eased pop-out animation on selection change.

[zone-slide] Continue strip and site grid occupy mutually-exclusive
vertical zones. Exponential smoothing makes a mid-flight direction
reversal seamless.

[zone-slide-2] Grid at TOP of screen, continue strip at BOTTOM.
Pressing DOWN slides the grid down into place and the strip off the
bottom; pressing UP on grid row 0 / page 0 reverses it. A bouncing
direction-arrow hint tells the user which way to press.

[7x4] Home grid is now 7 columns x 4 rows = 28 slots on ONE page.
The registry (22 sites) fits with no pagination. Tiles shrank
230 -> 215 px tall so 4 rows fit inside the 860-px grid widget.
The nav-hint Y is dynamic: above the strip (y=600) in strip mode,
below the grid (y=980) in grid mode.

[catlist-effects] Category list: mutual slide-in / slide-out,
gold accent bar parked beside the selected row, ▲ / ▼ scroll hints,
horizontal shake on fetch failure.

[tmdb-poster-fallback] Grid/carousel/continue-strip items whose own
site poster is a wrong (landscape) shape (mycima.py / cimawbas_cwb.py
now leave item["poster"] empty for these, keeping the raw image in
item["thumbnail"] instead) get a background TMDB lookup queued the
first time they're drawn, reusing plugin_tmdb's own long-lived cache so
a title only ever costs one real network lookup, not one per page view.
The site's own (wrong-shaped) thumbnail is shown meanwhile rather than a
blank placeholder, and gets swapped for the real TMDB poster the next
time the artwork poll timer redraws once it's ready.
"""

import os
import threading
import time

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from novaplay_choice import NovaChoiceBox as ChoiceBox                     # [UX-5]
from Screens.VirtualKeyBoard import VirtualKeyBoard          # [UX-rename]
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, ePoint, eSize

from plugin_common import my_log, PLUGIN_PATH, _PLUGIN_VERSION
from extractors import get_extractor, get_home_sites, get_search_site_order, get_family_members
from extractors.base import get_curl_failed_needs_proxy

import plugin_imagecache
import plugin_health
from plugin_assets import placeholder_for_item

from plugin_state import (_get_config, _set_config,
    _is_favorite, _get_saved_position,
    _continue_items, _library_search_suggestions,
    _history_items, _clear_continue_item,
    _hide_site, _is_site_hidden,
    _set_site_custom_name,                                    # [UX-rename]
    _set_family_order)                                        # [UX-reorder]

from plugin_gridlist import (HomeMenuGrid, PosterCardGrid, resolve_icon_path,
    build_pixmap_widgets_xml, build_poster_pixmap_widgets_xml,
    build_poster_badge_widgets_xml,
    TextListGrid,
    LIST_CELL_H, LIST_CELL_MARGIN,                       # [catlist-effects]
    build_carousel_xml, build_continue_row_xml,
    HOME_GRID_COLS, HOME_GRID_ROWS, HOME_CELL_W, HOME_CELL_H,
    HOME_CELL_MARGIN, HOME_BORDER_W, HOME_ICON_PAD_TOP, HOME_ICON_W, HOME_ICON_H,
    HOME_CELL_INNER_W, HOME_CELL_INNER_H,                # [PATCH G6][PATCH G7]
    HOME_CENTER_OFFSET_X,
    HOME_POP_OUT, HOME_POP_OUT_ICON,                     # [pop-out]
    POP_ANIM_FRAMES, POP_ANIM_INTERVAL,                  # [pop-anim]
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
from plugin_tmdb import _tmdb_enabled, _tmdb_search_metadata, fill_item_rating_async
from plugin_screen_detail import NovaPlayDetail
from plugin_screen_search import NovaPlaySearch
from novaplay_thread import callInMainThread

from novaplay_dualclock import format_dual_dates

_SEARCH_SITE_ORDER = get_search_site_order()


# ─── [site-label] helpers: render the site's own badge chip ─────────────────
def _hex_to_argb(hexstr, default=0xFFFFD740):
    """'#RRGGBB' (or '#RGB') -> Enigma2's 0xAARRGGBB int. Returns `default`
    on any parse failure so a missing/invalid site colour still renders a
    visible chip instead of an invisible one."""
    h = (hexstr or "").lstrip("#").strip()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return default
    try:
        return 0xFF000000 | int(h, 16)
    except (ValueError, TypeError):
        return default


def _label_chip_width(text):
    """Compact chip width that fits the text plus small padding. Mirrors
    the site's inline .label chip (a small box sized to the text, not a
    full-width bar). ~16px per char at 1080p is a safe middle ground
    across Latin and Arabic glyphs."""
    n = len(text or "")
    return max(sc(64), n * sc(16) + sc(24))


def _label_chip_height(is_big=False):
    return sc(28) if is_big else sc(24)


class NovaPlayHome(Screen):
    skin = """
    <screen name="NovaPlayHome" position="center,center" size="1920,1080" title="NOVA Media Player" flags="wfNoBorder" backgroundColor="#0D1117">
        <eLabel position="0,0" size="1920,1080" backgroundColor="#0D1117" zPosition="0" />
        <widget name="staticBackground" position="0,0" size="1920,1080" zPosition="1" alphatest="blend" />
        <widget name="backdropImg" position="0,0" size="1920,1080" zPosition="1" alphatest="blend" scale="1" />
        <widget name="shade_overlay" position="0,0" size="1920,1080" backgroundColor="#0D1117" transparency="150" zPosition="2" />
        <widget name="title_bar"  position="0,0"     size="1920,80" backgroundColor="#0D1117" zPosition="6" />
        <widget name="title_text" position="45,6"    size="600,36"  font="Regular;28" foregroundColor="#F2C230" transparent="1" zPosition="7" />

        <widget name="clock_time"   position="1440,4"  size="110,34" font="Regular;30" halign="right" valign="center" foregroundColor="#F0F6FC" transparent="1" zPosition="7" />
        <widget name="clock_period" position="1555,10" size="60,26"  font="Regular;18" halign="left"  valign="center" foregroundColor="#8B949E" transparent="1" zPosition="7" />
        <widget name="clock_sep"    position="1625,8"  size="2,34"   backgroundColor="#30363D" zPosition="7" />
        <widget name="clock_greg"   position="1637,4"  size="268,34" font="Regular;20" halign="right" valign="center" foregroundColor="#F0F6FC" transparent="1" zPosition="7" />
        <widget name="clock_hijri"  position="1637,42" size="268,32" font="Regular;20" halign="right" valign="center" foregroundColor="#F2C230" transparent="1" zPosition="7" />

        <widget name="status"     position="660,8"   size="520,30"  font="Regular;22" foregroundColor="#F2C230" transparent="1" halign="right" zPosition="7" />
        <widget name="content_title" position="40,95"  size="1200,50"  font="Bold;38" foregroundColor="#F2C230" transparent="1" zPosition="5" halign="left" valign="top" />
        <widget name="info_meta"     position="40,150" size="1200,35"  font="Regular;24" foregroundColor="#F2C230" transparent="1" zPosition="5" halign="left" />
        <widget name="info_plot"     position="40,190" size="1200,230" font="Regular;22" foregroundColor="#F0F6FC" transparent="1" zPosition="5" halign="left" valign="top" />
        <widget name="homeSel" position="0,0" size="1,1" backgroundColor="#F2C230" cornerRadius="3" zPosition="2" transparent="0" />
        <widget name="home_grid" position="20,100" size="1880,860" scrollbarMode="showNever" transparent="1" zPosition="3" />
        {home_grid_pics}
        <widget name="poster_grid" position="50,90" size="1820,864" scrollbarMode="showNever" transparent="1" zPosition="3" />
        <widget name="text_list" position="40,95" size="1840,830" scrollbarMode="showNever" transparent="1" zPosition="3" />
        {poster_grid_pics}
        {poster_badge_xml}
        <widget name="pgridSel" position="0,0" size="1,1" backgroundColor="#F2C230" cornerRadius="3" zPosition="5" transparent="0" />
        {carousel_xml}
        {continue_xml}
        <widget name="navHint" position="0,600" size="1920,38" font="Regular;26" foregroundColor="#F2C230" transparent="1" halign="center" valign="center" zPosition="8" />
        <widget name="catAccent"   position="34,101"   size="6,71"  backgroundColor="#F2C230" zPosition="6" transparent="0" cornerRadius="3" />
        <widget name="catScrollUp" position="1884,110" size="32,26" font="Regular;20" foregroundColor="#8B949E" transparent="1" halign="center" valign="center" zPosition="6" />
        <widget name="catScrollDn" position="1884,900" size="32,26" font="Regular;20" foregroundColor="#8B949E" transparent="1" halign="center" valign="center" zPosition="6" />
        <widget name="grid_status_left"  position="40,965"  size="900,32" font="Regular;22" foregroundColor="#8B949E" transparent="1" halign="left" zPosition="7" />
        <widget name="grid_status_right" position="940,965" size="900,32" font="Regular;22" foregroundColor="#8B949E" transparent="1" halign="right" zPosition="7" />
        <widget name="btn_bar"    position="0,1015"  size="1920,65" backgroundColor="#0D1117" zPosition="6" />
        <widget name="key_red"    position="45,1028" size="420,32" font="Regular;22" foregroundColor="#FF6B6B" transparent="1" halign="center" zPosition="7" />
        <widget name="key_green"  position="510,1028" size="420,32" font="Regular;22" foregroundColor="#39D98A" transparent="1" halign="center" zPosition="7" />
        <widget name="key_yellow" position="975,1028" size="420,32" font="Regular;22" foregroundColor="#F2C230" transparent="1" halign="center" zPosition="7" />
        <widget name="key_blue"   position="1440,1028" size="420,32" font="Regular;22" foregroundColor="#58A6FF" transparent="1" halign="center" zPosition="7" />
    </screen>
    """

    _HOME_GRID_X = 20
    _HOME_GRID_Y = 100              # [zone-slide-2] grid VISIBLE at top of screen
    _POSTER_GRID_X = 50
    _POSTER_GRID_Y = 90
    carousel_slots = 7
    carousel_center = 3

    # [zone-slide-2 / 7x4] zone geometry: grid at TOP (4 rows * 215 = 860 px),
    # strip at BOTTOM. _GRID_ABOVE_OFF must clear the WHOLE 860-px grid:
    # y=100 + (-960) = -860, fully above the screen.
    _GRID_ABOVE_OFF    = -960
    _STRIP_BOTTOM_OFF  = 580        # strip visible: CONT_Y + 580 = 690
    _STRIP_BELOW_OFF   = 1010       # strip off-screen below: CONT_Y + 1010 = 1120
    _NAV_HINT_Y        = 600        # legacy default; real Y comes from _navHintBaseY()

    def __init__(self, session, start=None):
        # [hub] opened by the panel Home (plugin_screen_hub) for one job:
        # ("site", key) | ("library", "favorites"|"history") | ("search",).
        # Backing out of that job closes this screen instead of showing
        # the classic home grid.
        self._hub_start = start
        self._hub = start is not None
        self._hub_ready = False
        self.skin = NovaPlayHome.skin.format(
            plugin_path=PLUGIN_PATH,
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
        self._cont_backdrop_token = 0
        self._cats_cache = {}
        self._list_return_index = None
        self._picker_rep = None                              # [UX-reorder]
        self._picker_members = []                             # [UX-reorder]
        self._site_pick_origin = None                         # [family-back]
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

        # [zone-slide] transition between continue strip (p=0) and home grid (p=1)
        self._zone_p          = 0.0
        self._zone_target     = 0.0
        self._strip_y_off     = 0
        self._grid_y_off      = 0
        self._zone_anim_timer = eTimer()
        self._zone_anim_timer.callback.append(self._zoneAnimTick)

        # [zone-slide-2] direction hint
        self._navHintDir   = "down"
        self._navHintPhase = 0
        self._navHintTimer = eTimer()
        self._navHintTimer.callback.append(self._navHintTick)

        # [catlist-effects] category-list overlays + slide + shake
        self._listVisible     = False
        self._listSlideTimer  = eTimer()
        self._listSlideTimer.callback.append(self._listSlideTick)
        self._listSlidePhase  = 0
        self._listSlideFrame  = 0
        self._listSlideDoneCb = None
        # [family-back] _onBack()'s "return to family picker" callback
        # fires from INSIDE _listSlideTick()'s own slide-out completion
        # branch. Calling _showSiteFamilyPicker() (which starts a NEW
        # slide-IN via _slideListIn()) synchronously from there is
        # re-entrant: _listSlideTick's still-running slide-out frame
        # unconditionally resets _listSlidePhase=0 and stops
        # _listSlideTimer right after the callback returns, clobbering
        # the slide-in state the callback just started (list stays
        # stuck off-screen; stale catAccent position stays visible).
        # Deferring the picker rebuild by one event-loop tick (a
        # dedicated one-shot timer, started AFTER the slide-out tick's
        # own tail code has already run) avoids that entirely.
        self._hubCloseTimer = eTimer()
        self._hubCloseTimer.callback.append(self.close)
        self._pendingPickerTimer = eTimer()
        self._pendingPickerTimer.callback.append(self._firePendingPicker)
        self._pendingPickerArgs = None
        self._catShakeTimer   = eTimer()
        self._catShakeTimer.callback.append(self._catShakeTick)
        self._catShakeFrames  = 0

        # [pop-anim] home-grid animated pop-out on selection change
        self._pop_timer = eTimer()
        self._pop_timer.callback.append(self._popTick)
        self._pop_frame = 0

        # [pop-anim] continue-strip animated pop-out on selection change
        self._cont_pop_timer = eTimer()
        self._cont_pop_timer.callback.append(self._contPopTick)
        self._cont_pop_frame = 0

        # [pop-anim] poster-grid animated pop-out on selection change
        self._poster_pop_timer = eTimer()
        self._poster_pop_timer.callback.append(self._posterPopTick)
        self._poster_pop_frame = 0
        self._poster_pop_p = 1.0

        self["staticBackground"] = Pixmap()  # [PATCH G9]
        self["backdropImg"] = Pixmap()
        self["shade_overlay"] = Label("")
        self._current_backdrop_path = ""
        self["title_bar"]  = Label("")
        self["title_text"] = Label("NOVA Media Player  v{}".format(_PLUGIN_VERSION))
        self["status"]     = Label("Loading…")
        self["clock_time"]   = Label("")
        self["clock_period"] = Label("")
        self["clock_sep"]    = Label("")
        self["clock_greg"]   = Label("")
        self["clock_hijri"]  = Label("")
        self["content_title"] = Label("")
        self["info_meta"]  = Label("")
        self["info_plot"]  = Label("")
        self["btn_bar"]    = Label("")
        self["key_red"]    = Label("Exit")
        self["key_green"]  = Label("Favorites")
        self["key_yellow"] = Label("Search")
        self["key_blue"]   = Label("Settings")

        # [pop-out] cyan glow ring behind the selected home tile
        self["homeSel"] = Label("")
        self["homeSel"].hide()

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
            self["conttype%d" % i] = Label("")
            self["contbartrack%d" % i] = Label("")
            self["contbar%d" % i] = Label("")
            self["cont%d" % i] = Pixmap()

        # [zone-slide-2] direction-hint label
        self["navHint"] = Label("")
        self["navHint"].hide()

        # [catlist-effects] list overlays
        self["catAccent"]   = Label("")
        self["catScrollUp"] = Label("")
        self["catScrollDn"] = Label("")
        self["catAccent"].hide()
        self["catScrollUp"].hide()
        self["catScrollDn"].hide()

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
        if self._hub:
            self._cold_open = False
            try:
                self._pulse_timer.start(1000, False)
            except Exception:
                pass
            # run the job after this screen is up (opening the search
            # screen from inside onLayoutFinish is not safe)
            self._hubHideHome()
            self._hubStartTimer = eTimer()
            self._hubStartTimer.callback.append(self._hubRunStart)
            self._hubStartTimer.start(30, True)
            return
        if self._cold_open and self._cont_items:
            self._focus_zone = "row"
            self._cont_index = 0
            self._zone_p = 0.0
            self._zone_target = 0.0
            self._applyZoneOffsets()
            self._moveContinueSel()
        else:
            self._focus_zone = "grid"
            self._zone_p = 1.0
            self._zone_target = 1.0
            self._applyZoneOffsets()
        self._cold_open = False
        self._updateNavHint()
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

    def _pulseTick(self):
        self._pulse_on = not self._pulse_on
        if self._display_mode == "home":
            try:
                grid = self["home_grid"]
                grid.pulse_on = self._pulse_on
                grid._redraw()
            except Exception:
                pass

    # ── [zone-slide] slide transition between strip and grid ────────────
    def _startZoneAnim(self, target):
        self._zone_target = target
        try:
            self._zone_anim_timer.start(25, False)
        except Exception as e:
            my_log("zone slide: timer start failed: {}".format(e))

    def _zoneAnimTick(self):
        if self._display_mode != "home":
            try: self._zone_anim_timer.stop()
            except Exception: pass
            return
        diff = self._zone_target - self._zone_p
        if abs(diff) < 0.02:
            self._zone_p = self._zone_target
            self._applyZoneOffsets()
            try: self._zone_anim_timer.stop()
            except Exception: pass
            return
        self._zone_p += diff * 0.35
        self._applyZoneOffsets()

    def _applyZoneOffsets(self):
        p = self._zone_p
        grid_y_off  = int((1.0 - p) * self._GRID_ABOVE_OFF)
        strip_y_off = int((1.0 - p) * self._STRIP_BOTTOM_OFF
                          + p * self._STRIP_BELOW_OFF)
        self._strip_y_off = strip_y_off
        self._grid_y_off  = grid_y_off

        try:
            inst = self["home_grid"].instance
            if inst:
                inst.move(ePoint(self._HOME_GRID_X,
                                 self._HOME_GRID_Y + grid_y_off))
        except Exception as e:
            my_log("zone slide: home_grid move failed: {}".format(e))

        for r in range(HOME_GRID_ROWS):
            for c in range(HOME_GRID_COLS):
                try:
                    inst = self["pic_%d_%d" % (r, c)].instance
                    if inst:
                        bx, by = self._iconBasePos(r, c)
                        inst.move(ePoint(bx, by))
                except Exception:
                    pass

        try:
            self._applyContinueLayout(None, 1.0, y_off=strip_y_off)
        except Exception as e:
            my_log("zone slide: continue layout failed: {}".format(e))

        try:
            inst = self["cont_title"].instance
            if inst:
                inst.move(ePoint(0, 65 + strip_y_off))
        except Exception:
            pass

        if self._display_mode == "home" and p > 0.5:
            try:
                sel_r = self["home_grid"].currentRow
                sel_c = self["home_grid"].currentCol
                if 0 <= sel_r < HOME_GRID_ROWS and 0 <= sel_c < HOME_GRID_COLS:
                    bx = (self._HOME_GRID_X + HOME_CENTER_OFFSET_X
                          + sel_c * HOME_CELL_W + HOME_CELL_MARGIN)
                    by = (self._HOME_GRID_Y + self._grid_y_off
                          + sel_r * HOME_CELL_H + HOME_CELL_MARGIN)
                    self._moveResize("homeSel", bx, by,
                                     HOME_CELL_INNER_W, HOME_CELL_INNER_H)
                    self["homeSel"].show()
            except Exception:
                pass
        else:
            try: self["homeSel"].hide()
            except Exception: pass

    # ── [zone-slide-2] direction-hint arrow ────────────────────────────
    def _navHintBaseY(self):
        """Strip mode → hint above the strip (y=600).  Grid mode → hint
        below the 860-px grid (y=980).  With 4 rows the grid now runs from
        y=100 to y=960, so a fixed Y would collide with one zone or the
        other; the base Y follows the focus."""
        return 600 if self._navHintDir == "down" else 980

    def _updateNavHint(self):
        if self._display_mode != "home":
            self._hideNavHint()
            return
        if self._focus_zone == "row":
            self._navHintDir = "down"
            self["navHint"].setText(u"▼   Press down to show the sites")
        else:
            self._navHintDir = "up"
            self["navHint"].setText(u"▲   Press up to resume watching")
        try:
            self["navHint"].show()
        except Exception:
            pass
        self._navHintPhase = 0
        base_y = self._navHintBaseY()
        try:
            self["navHint"].instance.move(ePoint(0, base_y))
        except Exception:
            pass
        try:
            self._navHintTimer.start(40, False)
        except Exception:
            pass

    def _hideNavHint(self):
        try: self._navHintTimer.stop()
        except Exception: pass
        try: self["navHint"].hide()
        except Exception: pass

    def _navHintTick(self):
        if self._display_mode != "home":
            self._hideNavHint()
            return
        self._navHintPhase = (self._navHintPhase + 1) % 30
        t = self._navHintPhase / 30.0
        if t < 0.5:
            offset = int(t * 2 * 14)
        else:
            offset = 0
        if self._navHintDir != "down":
            offset = -offset
        try:
            inst = self["navHint"].instance
            if inst:
                inst.move(ePoint(0, self._navHintBaseY() + offset))
        except Exception:
            pass

    # ── [catlist-effects] category-list: overlays, slide-in, shake ──────
    def _hideCatOverlays(self):
        for k in ("catAccent", "catScrollUp", "catScrollDn"):
            try: self[k].hide()
            except Exception: pass

    def _slideListIn(self):
        self._listSlidePhase = 1
        self._listSlideFrame = 0
        try:
            inst = self["text_list"].instance
            if inst:
                inst.move(ePoint(1920, 95))
        except Exception:
            pass
        try:
            self._listSlideTimer.start(20, False)
        except Exception as e:
            my_log("list slide-in start failed: {}".format(e))

    def _slideListOut(self, on_done):
        self._listSlidePhase = 2
        self._listSlideFrame = 0
        self._listSlideDoneCb = on_done
        try:
            self._listSlideTimer.start(20, False)
        except Exception as e:
            my_log("list slide-out start failed: {}".format(e))
            if on_done:
                on_done()

    def _listSlideTick(self):
        if self._listSlidePhase == 0:
            try: self._listSlideTimer.stop()
            except Exception: pass
            return
        self._listSlideFrame += 1
        frames = 8
        p = min(1.0, float(self._listSlideFrame) / float(frames))
        if self._listSlidePhase == 1:
            p_eased = 1.0 - (1.0 - p) ** 3
            x = int(1920 * (1.0 - p_eased) - 6 * p_eased)
        else:
            p_eased = p * p
            x = int(40 + 1920 * p_eased)
        try:
            inst = self["text_list"].instance
            if inst:
                inst.move(ePoint(x, 95))
        except Exception:
            pass
        if self._listSlideFrame >= frames:
            if self._listSlidePhase == 1:
                try:
                    inst = self["text_list"].instance
                    if inst:
                        inst.move(ePoint(40, 95))
                except Exception:
                    pass
                self._listVisible = True
                self._updateCatAccent()
                self._updateScrollHints()
            else:
                try: self["text_list"].hide()
                except Exception: pass
                try:
                    inst = self["text_list"].instance
                    if inst:
                        inst.move(ePoint(40, 95))
                except Exception:
                    pass
                cb = self._listSlideDoneCb
                self._listSlideDoneCb = None
                if cb:
                    try: cb()
                    except Exception as e:
                        my_log("list slide-out callback error: {}".format(e))
            self._listSlidePhase = 0
            try: self._listSlideTimer.stop()
            except Exception: pass

    def _updateCatAccent(self):
        if self._display_mode != "list" or not getattr(self, "_listVisible", False):
            try: self["catAccent"].hide()
            except Exception: pass
            return
        try:
            tl = self["text_list"]
            row_y = tl.getRowY(tl.currentRow)
        except Exception:
            row_y = None
        if row_y is None:
            try: self["catAccent"].hide()
            except Exception: pass
            return
        y = 95 + row_y + LIST_CELL_MARGIN
        h = max(10, LIST_CELL_H - 2 * LIST_CELL_MARGIN)
        self._moveResize("catAccent", 34, y, 6, h)
        try: self["catAccent"].show()
        except Exception: pass

    def _updateScrollHints(self):
        if self._display_mode != "list" or not getattr(self, "_listVisible", False):
            try: self["catScrollUp"].hide()
            except Exception: pass
            try: self["catScrollDn"].hide()
            except Exception: pass
            return
        try:
            tl = self["text_list"]
            per  = tl.itemsPerPage
            cur  = tl.currentPage * per + tl.currentRow
            tot  = tl.totalItems
        except Exception:
            return
        try:
            if cur > 0:
                self["catScrollUp"].setText(u"▲")
                self["catScrollUp"].show()
            else:
                self["catScrollUp"].hide()
        except Exception:
            pass
        try:
            if cur + 1 < tot:
                self["catScrollDn"].setText(u"▼")
                self["catScrollDn"].show()
            else:
                self["catScrollDn"].hide()
        except Exception:
            pass

    def _shakeList(self):
        if self._display_mode != "list" or not getattr(self, "_listVisible", False):
            return
        self._catShakeFrames = 8
        try: self._catShakeTimer.start(20, False)
        except Exception as e:
            my_log("list shake start failed: {}".format(e))

    def _catShakeTick(self):
        if self._catShakeFrames <= 0:
            try:
                inst = self["text_list"].instance
                if inst:
                    inst.move(ePoint(40, 95))
            except Exception:
                pass
            try: self._catShakeTimer.stop()
            except Exception: pass
            return
        self._catShakeFrames -= 1
        amps = (6, -6, 4, -4, 2, -2, 1, -1)
        idx = 8 - self._catShakeFrames - 1
        offset = amps[idx] if 0 <= idx < len(amps) else 0
        try:
            inst = self["text_list"].instance
            if inst:
                inst.move(ePoint(40 + offset, 95))
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
            # [site-label] clabel geometry is now set per-slot inside
            # _updateCarouselSlot, where the actual label text (and thus
            # the chip width) is known.
            self._moveResize('cresumeMark%d' % widget_id, x + 10, y + h - 34, w - 20, 30)
            if logical_slot == self.carousel_center:
                focus_extra = 7
                self._moveResize('cfocus%d' % self.carousel_center, x - focus_extra, y - focus_extra, w + (focus_extra * 2), h + (focus_extra * 2))
            else:
                self._moveResize('cfocus%d' % logical_slot, 0, 0, 1, 1)

    def _hubClose(self):
        try:
            self._hubCloseTimer.start(10, True)
        except Exception:
            self.close()

    def _hubHideHome(self):
        """Hide the classic home grid and continue strip (hub jobs that
        load in the background would otherwise show them meanwhile)."""
        names = ["home_grid", "cont_title", "contSel", "homeSel", "navHint"]
        names += ["pic_%d_%d" % (r, c) for r in range(HOME_GRID_ROWS) for c in range(HOME_GRID_COLS)]
        for i in range(CONT_SLOTS):
            names += ["cont%d" % i, "contbadge%d" % i, "conttype%d" % i, "contbartrack%d" % i, "contbar%d" % i]
        for n in names:
            try:
                self[n].hide()
            except Exception:
                pass
        try:
            self._navHintTimer.stop()
        except Exception:
            pass

    def _hubRunStart(self):
        start = self._hub_start or ()
        kind = start[0] if start else ""
        self._hubHideHome()
        try:
            if kind == "site":
                key = start[1]
                members = get_family_members(key)
                if len(members) > 1:
                    self._showSiteFamilyPicker(key, members)
                else:
                    self._site_pick_origin = None
                    self._site = key
                    self._showSiteCategories()
            elif kind == "library":
                self._showLibrary(start[1])
            elif kind == "search":
                self["title_text"].setText("Search")
                self._onSearch()
        except Exception as e:
            my_log("hub start {}: {}".format(start, e))
        self._hub_ready = True

    def _showHome(self):
        if self._hub and self._hub_ready:
            self._hubClose()
            return
        self._source = "home"
        self._display_mode = "home"
        self._page = 1
        self._nav_stack = []
        self._site_pick_origin = None                        # [family-back]
        self["title_text"].setText("NOVA Media Player  v{}".format(_PLUGIN_VERSION))
        self["status"].setText("")

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
        self._startHealthSweep()

        self._showHomeMode()
        self._onGridSelectionChanged()

    def _showHomeMode(self):
        self._focus_zone = "grid"
        self._listVisible = False            # [catlist-effects]
        self._hideCatOverlays()
        try: self._poster_pop_timer.stop()                    # [pop-anim]
        except Exception: pass
        self._poster_pop_p = 1.0
        try:
            self._artworkPollTimer.start(1000, False)
        except Exception:
            pass
        try:  # [PATCH G11]
            self["staticBackground"].instance.setPixmapFromFile(
                os.path.join(PLUGIN_PATH, "images", "background.jpg"))
            self["staticBackground"].show()  # [PATCH G9]
        except Exception as e:
            my_log("staticBackground show (home) failed: {}".format(e))
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
        self["key_red"].setText("Exit")
        self["key_green"].setText("Favorites")
        self["key_yellow"].setText("Search")
        self["key_blue"].setText("Settings")
        self._paintContinueRow()
        self._zone_p = 1.0
        self._zone_target = 1.0
        self._applyZoneOffsets()
        # [zone-slide-2 fix] re-show the direction hint on every return to
        # home. Without this, the hint's own timer hides it on the first
        # tick while a category list is open, and nothing ever brings it
        # back when the user presses BACK — so the ▲ was missing every time
        # you came back from a list to the grid.
        self._updateNavHint()

    def _showPosterMode(self):
        try: self["staticBackground"].hide()  # [PATCH G9]
        except Exception as e: my_log("staticBackground hide (poster) failed: {}".format(e))
        self._hideNavHint()                                    # [zone-slide-2]
        self._listVisible = False                              # [catlist-effects]
        self._hideCatOverlays()
        self["home_grid"].hide()
        self["homeSel"].hide()                                # [pop-out]
        try: self._pop_timer.stop()                           # [pop-anim]
        except Exception: pass
        try: self._cont_pop_timer.stop()                      # [pop-anim]
        except Exception: pass
        try: self._poster_pop_timer.stop()                    # [pop-anim]
        except Exception: pass
        self._poster_pop_p = 1.0
        self["text_list"].hide()
        for i in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                self["pic_%d_%d" % (i, _c)].hide()
        for i in range(CONT_SLOTS):
            self["cont%d" % i].hide()
            self["contbadge%d" % i].hide()
            try:
                self["conttype%d" % i].hide()
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
            self["key_green"].setText("Switch view (Carousel)")
        else:
            self["content_title"].show()
            self["info_meta"].show()
            self["info_plot"].show()
            for i in range(self.carousel_slots):
                self["cposter%d" % i].show()
            self._applyCarouselGeometry()
            self._syncCarouselFocusVisible()
            self["key_green"].setText("Switch view (Grid)")
        self["key_red"].setText("Previous page")
        self["key_yellow"].setText("Search")
        self["key_blue"].setText("Next page")
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

    # ── [tmdb-poster-fallback] shared helper ────────────────────────────
    def _requestTmdbPosterAsync(self, item):
        """For grid/carousel/continue-strip items whose own site poster
        is a wrong (landscape) shape, mycima.py/cimawbas_cwb.py now leave
        item["poster"] empty for exactly this case -- queues a background
        TMDB lookup for a proper, portrait poster, reusing plugin_tmdb's
        own long-lived cache (so a title only ever costs one real network
        lookup, not one per page view, once cached). Matches the existing
        threading.Thread + callInMainThread pattern already used for
        carousel backdrop fetching (_bgFetchTmdbText) -- same
        architecture, different target. _tmdb_poster_tried on the item
        itself ensures this is only ever attempted once per item per
        session, even if TMDB genuinely has no match for it.
        """
        if item.get("poster") or item.get("_tmdb_poster_tried"):
            return
        title = item.get("title", "")
        if not title:
            return
        item["_tmdb_poster_tried"] = True
        year = item.get("year", "")
        m_type = item.get("type", "movie")
        # [DIAG] two prior hypotheses for "grid poster never upgrades to
        # TMDB" were both ruled out by directly reading the real code
        # (setList/getPageItems return the same item references, and
        # MemoizedPixmapCache correctly compares by path content, not just
        # key presence) -- this function previously only logged on
        # failure, so there was no way to tell from a real log whether it
        # even runs for grid items, whether TMDB matches, or whether the
        # redraw callback fires. Logging every step until the actual
        # breakpoint is visible.
        my_log("_requestTmdbPosterAsync: queued for '{}' (year={}, type={})"
               .format(title[:40], year, m_type))

        def _worker():
            try:
                meta = _tmdb_search_metadata(title, year, m_type)
                if meta and meta.get("poster"):
                    item["poster"] = meta["poster"]
                    my_log("_requestTmdbPosterAsync: SUCCESS for '{}' -> {}"
                           .format(title[:40], meta["poster"]))
                    callInMainThread(self._onTmdbPosterReady)
                else:
                    my_log("_requestTmdbPosterAsync: no TMDB poster match for '{}'"
                           .format(title[:40]))
            except Exception as e:
                my_log("_requestTmdbPosterAsync error for {}: {}".format(title[:40], e))

        threading.Thread(target=_worker, daemon=True).start()

    def _onTmdbPosterReady(self):
        """Redraw whichever view is currently showing, so a just-filled-in
        TMDB poster gets picked up on the next paint rather than waiting
        for the next scheduled artwork-poll tick."""
        # [DIAG] see _requestTmdbPosterAsync above -- confirms this callback
        # actually fires and which redraw path it takes.
        my_log("_onTmdbPosterReady: fired, display_mode={} layout_style={}"
               .format(self._display_mode, self._layout_style))
        if self._display_mode == "poster":
            if self._layout_style == "grid":
                try:
                    self._updatePosterPixmaps(getattr(self, "_poster_pop_p", 1.0))
                    my_log("_onTmdbPosterReady: grid redraw completed")
                except Exception as e:
                    my_log("_onTmdbPosterReady grid redraw error: {}".format(e))
            else:
                try:
                    for widget_id in range(self.carousel_slots):
                        pos = self._carouselPositionForSlot(widget_id)
                        self._updateCarouselSlot(widget_id, pos)
                    my_log("_onTmdbPosterReady: carousel redraw completed")
                except Exception as e:
                    my_log("_onTmdbPosterReady carousel redraw error: {}".format(e))
        elif self._display_mode == "home":
            try:
                self._refreshContinuePosters()
                my_log("_onTmdbPosterReady: continue-strip redraw completed")
            except Exception as e:
                my_log("_onTmdbPosterReady continue redraw error: {}".format(e))

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
            self["cposter%d" % widget_id].setText("Next page")
            return
        if item.get("_is_prev_page"):
            self["cposterImg%d" % widget_id].hide()
            self["cratingBadge%d" % widget_id].hide()
            self["cyearBadge%d" % widget_id].hide()
            self["clabel%d" % widget_id].hide()
            self["cfavMark%d" % widget_id].hide()
            self["cresumeMark%d" % widget_id].hide()
            self["cposter%d" % widget_id].setText("Previous page")
            return
        self["cposter%d" % widget_id].setText("")
        # [tmdb-poster-fallback] no real (portrait) poster yet -- kick off
        # a background TMDB lookup, and meanwhile show the site's own
        # (wrong-shaped) thumbnail rather than a blank placeholder.
        url = item.get("poster") or ""
        if not url and item.get("thumbnail"):
            self._requestTmdbPosterAsync(item)
            url = item["thumbnail"]
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
        # [tmdb-rating] one-shot background fill (see poster-grid branch).
        if not item.get("rating"):
            fill_item_rating_async(item, on_done=self._onTmdbPosterReady)

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
            _cl = self["clabel%d" % widget_id]
            _cl.setText(_lb)
            _lc = item.get("label_color") or ""
            if _lc:
                _argb_bg = _hex_to_argb(_lc)
                _argb_fg = 0xFFF0F6FC     # white on the site's own colour
            else:
                _argb_bg = 0xFFFFD740     # fallback gold
                _argb_fg = 0xFF0D1117     # black on gold
            try:
                _inst = _cl.instance
                if _inst is not None:
                    _inst.setBackgroundColor(_argb_bg)
                    _inst.setForegroundColor(_argb_fg)
            except Exception:
                pass
            # [site-label] compact chip positioned directly under the
            # year badge (top-left corner of the card). The year badge
            # in _applyCarouselGeometry lives at (x + 12, y + 22) with
            # size (58|52, 28); place the chip 4px below it, matching
            # the poster-grid layout. Offsets are raw pixels to match
            # the year badge's own raw offsets on top of the already-
            # scaled _CAROUSEL_GEOMETRY base.
            _is_big = (widget_id == self.carousel_center)
            _sx, _sy, _sw, _sh = _CAROUSEL_GEOMETRY.get(widget_id, (0, 0, 1, 1))
            _cw = _label_chip_width(_lb)
            _ch = _label_chip_height(is_big=_is_big)
            _cx = _sx + 12
            _cy = _sy + 22 + 28 + 4
            self._moveResize("clabel%d" % widget_id, _cx, _cy, _cw, _ch)
            _cl.show()
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
            self["cresumeMark%d" % widget_id].setText(u"✓ Watched")
            self["cresumeMark%d" % widget_id].show()
        elif saved_pos > 30:
            mm, ss = divmod(saved_pos, 60)
            hh, mm = divmod(mm, 60)
            tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
            self["cresumeMark%d" % widget_id].setText("Resume " + tstr)
            self["cresumeMark%d" % widget_id].show()
        else:
            self["cresumeMark%d" % widget_id].hide()

    def _paintCarousel(self):
        for widget_id in range(self.carousel_slots):
            pos = self._carouselPositionForSlot(widget_id)
            self._updateCarouselSlot(widget_id, pos)
        self._updateBackdrop()

    def _moveCarousel(self, delta):
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
        # [pop-anim] stop any in-flight animation, snap all posters to base size,
        # then animate the new selection from p=0 upward
        try:
            self._poster_pop_timer.stop()
        except Exception:
            pass
        self._poster_pop_p = 0.0
        try:
            self._updatePosterPixmaps(0.0)
        except Exception as e:
            my_log("poster pop reset error: {}".format(e))
        self._poster_pop_frame = 0
        try:
            self._poster_pop_timer.start(POP_ANIM_INTERVAL, False)
        except Exception as e:
            my_log("poster pop timer start failed: {}".format(e))
        self._updateBackdrop()

    def _posterPopTick(self):
        """[pop-anim] Poster-grid animation frame — same ease-out cubic as the home grid."""
        if self._display_mode != "poster" or self._layout_style != "grid":
            try: self._poster_pop_timer.stop()
            except Exception: pass
            return
        frames = max(1, POP_ANIM_FRAMES)
        self._poster_pop_frame += 1
        p_raw = min(1.0, float(self._poster_pop_frame) / float(frames))
        # ease-out cubic: fast at the start, gentle landing
        p = 1.0 - (1.0 - p_raw) ** 3
        self._poster_pop_p = p
        try:
            self._updatePosterPixmaps(p)
        except Exception as e:
            my_log("_posterPopTick error: {}".format(e))
        if self._poster_pop_frame >= frames:
            try: self._poster_pop_timer.stop()
            except Exception: pass
            self._poster_pop_p = 1.0

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
        self["grid_status_right"].setText("Page {} / {}  •  1–9 to jump".format(page, total_pages))

    def _updatePosterPixmaps(self, pop_p=1.0):
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
                _zoom_s = 0.08 * pop_p
                _zw_s = int(POSTER_W * (1 + _zoom_s))
                _zh_s = int(POSTER_H * (1 + _zoom_s))
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
                    _zoom = 0.08 * pop_p
                    _zw = int(POSTER_W * (1 + _zoom))
                    _zh = int(POSTER_H * (1 + _zoom))
                    _zx = _base_x - (_zw - POSTER_W) // 2
                    _zy = _base_y - (_zh - POSTER_H) // 2
                else:
                    _zw, _zh = POSTER_W, POSTER_H
                    _zx, _zy = _base_x, _base_y

                # [tmdb-poster-fallback] no real (portrait) poster yet --
                # kick off a background TMDB lookup, and meanwhile show
                # the site's own (wrong-shaped) thumbnail rather than a
                # blank placeholder, so something recognizable is visible
                # immediately instead of nothing. mycima.py/cimawbas_cwb.py
                # leave item["poster"] empty specifically for this case,
                # keeping the raw scraped image in item["thumbnail"].
                url = item.get("poster") or ""
                path = ""
                if url:
                    # [DIAG] one-time log the FIRST time this code path sees a
                    # populated item["poster"] (i.e. after the TMDB mutation) --
                    # confirms whether the mutation is actually visible here,
                    # and whether the resulting image resolves to a real cache
                    # hit or falls through to the placeholder/async-queue path.
                    if not item.get("_poster_grid_seen_populated"):
                        item["_poster_grid_seen_populated"] = True
                        my_log("_updatePosterPixmaps: item '{}' poster is now populated: {}"
                               .format(item.get("title", "")[:30], url))
                    path = plugin_imagecache.getCachedImage(url, target_size=(POSTER_W, POSTER_H))
                    if not item.get("_poster_grid_seen_populated_pathlog"):
                        item["_poster_grid_seen_populated_pathlog"] = True
                        my_log("_updatePosterPixmaps: cache lookup for '{}' -> path={!r}"
                               .format(item.get("title", "")[:30], path))
                elif item.get("thumbnail"):
                    self._requestTmdbPosterAsync(item)
                    url = item["thumbnail"]
                    path = plugin_imagecache.getCachedImage(url, target_size=(POSTER_W, POSTER_H))
                # [CHANGE 2026-10-17] watched posters keep their colours by default (the
                # "✓ Watched" badge still marks them); the old black & white, darkened
                # poster is opt-in via the config key "watched_poster_bw".
                if path and item.get("url") and _get_config("watched_poster_bw", False):
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

                # [tmdb-rating] one-shot background fill for items whose
                # source site publishes no rating on its cards (EgyDead,
                # egydead_coupons); the helper's own _tmdb_rating_tried
                # guard makes every later paint a no-op.
                if _in_grid and not item.get("rating"):
                    fill_item_rating_async(item, on_done=self._onTmdbPosterReady)

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
                _compact_chip = False
                if _lb and _in_grid:
                    badge.setText(_lb)
                    _lc = item.get("label_color") or ""
                    if _lc:
                        _argb_bg = _hex_to_argb(_lc)
                        _argb_fg = 0xFFF0F6FC
                    else:
                        _argb_bg = 0xFFFFD740
                        _argb_fg = 0xFF0D1117
                    try:
                        _inst = badge.instance
                        if _inst is not None:
                            _inst.setBackgroundColor(_argb_bg)
                            _inst.setForegroundColor(_argb_fg)
                    except Exception:
                        pass
                    _compact_chip = True
                elif _watched and _in_grid:
                    badge.setText(u"✓ Watched")
                    try:
                        _inst = badge.instance
                        if _inst is not None:
                            _inst.setBackgroundColor(0xFFFFD740)
                            _inst.setForegroundColor(0xFF0D1117)
                    except Exception:
                        pass
                elif saved_pos > 30 and _in_grid:
                    mm, ss = divmod(saved_pos, 60)
                    hh, mm = divmod(mm, 60)
                    tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
                    badge.setText("Resume " + tstr)
                    try:
                        _inst = badge.instance
                        if _inst is not None:
                            _inst.setBackgroundColor(0xFFFFD740)
                            _inst.setForegroundColor(0xFF0D1117)
                    except Exception:
                        pass
                else:
                    badge.hide()

                if _in_grid and _compact_chip:
                    # [site-label] compact chip, left-aligned directly
                    # under the year badge (top-left of the poster).
                    _cw = _label_chip_width(_lb)
                    _cx = _yb_x                              # same left edge as year
                    _cy = _yb_y + POSTER_YEAR_H + sc(4)      # 4px below year
                    try:
                        badge.instance.move(ePoint(_cx, _cy))
                        badge.instance.resize(eSize(_cw, POSTER_BADGE_H))
                    except Exception:
                        pass
                    badge.show()
                elif _in_grid and (_watched or saved_pos > 30):
                    # resume markers keep the old full-width bottom band
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
            self._updatePosterPixmaps(getattr(self, "_poster_pop_p", 1.0))
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
            if getattr(self, "_listVisible", False):
                self._updateCatAccent()
                self._updateScrollHints()

    # ── [pop-anim] home-grid helpers ────────────────────────────────────
    def _iconBasePos(self, row, col):
        """Top-left of the base (unzoomed) icon widget for cell (row, col).
        Mirrors build_pixmap_widgets_xml(), plus the live grid Y offset so
        icons slide together with the home_grid listbox during the
        strip⇄grid transition."""
        bx = (self._HOME_GRID_X + HOME_CENTER_OFFSET_X
              + col * HOME_CELL_W + HOME_CELL_MARGIN + HOME_BORDER_W)
        by = (self._HOME_GRID_Y + self._grid_y_off
              + row * HOME_CELL_H
              + HOME_CELL_MARGIN + HOME_BORDER_W + HOME_ICON_PAD_TOP)
        return bx, by

    def _updateIcons(self):
        # hide all icons + ring, stop any in-flight pop animation
        for _r in range(HOME_GRID_ROWS):
            for _c in range(HOME_GRID_COLS):
                self["pic_%d_%d" % (_r, _c)].hide()
        try:
            self["homeSel"].hide()
        except Exception:
            pass
        try:
            self._pop_timer.stop()
        except Exception:
            pass

        if self._display_mode != "home":
            return

        grid = self["home_grid"]

        # draw every visible icon at its base size
        for row, col, item in grid.getPageItems():
            icon_path = resolve_icon_path(item, PLUGIN_PATH)
            if not icon_path:
                continue
            widget = self["pic_%d_%d" % (row, col)]
            bx, by = self._iconBasePos(row, col)
            try:
                inst = getattr(widget, "instance", None)
                if inst is not None:
                    inst.move(ePoint(int(bx), int(by)))
                    inst.resize(eSize(int(HOME_ICON_W), int(HOME_ICON_H)))
            except Exception as e:
                my_log("_updateIcons base geometry error: {}".format(e))
            try:
                self._pix.set(widget, ("icon", row, col), icon_path)
                widget.show()
            except Exception:
                pass

        # start pop animation on the selected tile
        self._pop_frame = 0
        try:
            self._applyPopGeometry(0.0)
        except Exception:
            pass
        try:
            self._pop_timer.start(POP_ANIM_INTERVAL, False)
        except Exception as e:
            my_log("pop timer start failed: {}".format(e))

    def _applyPopGeometry(self, p):
        """Draw the selected tile's icon and homeSel ring at pop factor p (0..1)."""
        if self._display_mode != "home":
            return
        grid = self["home_grid"]
        sel_r, sel_c = grid.currentRow, grid.currentCol
        if not (0 <= sel_r < HOME_GRID_ROWS and 0 <= sel_c < HOME_GRID_COLS):
            return

        ipop = HOME_POP_OUT_ICON * p
        rpop = HOME_POP_OUT * p

        # selected icon — grow from centre of base position
        sel_item = None
        for row, col, item in grid.getPageItems():
            if row == sel_r and col == sel_c:
                sel_item = item
                break
        if sel_item is not None:
            icon_path = resolve_icon_path(sel_item, PLUGIN_PATH)
            if icon_path:
                widget = self["pic_%d_%d" % (sel_r, sel_c)]
                bx, by = self._iconBasePos(sel_r, sel_c)
                try:
                    inst = getattr(widget, "instance", None)
                    if inst is not None:
                        inst.move(ePoint(int(bx - ipop), int(by - ipop)))
                        inst.resize(eSize(int(HOME_ICON_W + 2 * ipop),
                                          int(HOME_ICON_H + 2 * ipop)))
                    self._pix.set(widget, ("icon", sel_r, sel_c), icon_path)
                    widget.show()
                except Exception as e:
                    my_log("_applyPopGeometry icon error: {}".format(e))

        # cyan glow ring behind the selected tile
        try:
            bx = (self._HOME_GRID_X + HOME_CENTER_OFFSET_X
                  + sel_c * HOME_CELL_W + HOME_CELL_MARGIN)
            by = (self._HOME_GRID_Y + self._grid_y_off
                  + sel_r * HOME_CELL_H + HOME_CELL_MARGIN)
            self._moveResize("homeSel",
                             bx - rpop, by - rpop,
                             HOME_CELL_INNER_W + 2 * rpop,
                             HOME_CELL_INNER_H + 2 * rpop)
            self["homeSel"].show()
        except Exception as e:
            my_log("_applyPopGeometry ring error: {}".format(e))

    def _popTick(self):
        """One animation frame: advance p, apply ease-out cubic, stop at p=1."""
        if self._display_mode != "home":
            try:
                self._pop_timer.stop()
            except Exception:
                pass
            return
        frames = max(1, POP_ANIM_FRAMES)
        self._pop_frame += 1
        p_raw = min(1.0, float(self._pop_frame) / float(frames))
        # ease-out cubic: fast at the start, gentle landing
        p = 1.0 - (1.0 - p_raw) ** 3
        try:
            self._applyPopGeometry(p)
        except Exception as e:
            my_log("_popTick error: {}".format(e))
        if self._pop_frame >= frames:
            try:
                self._pop_timer.stop()
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
            typetag = self["conttype%d" % i]
            track = self["contbartrack%d" % i]
            bar = self["contbar%d" % i]
            if i < len(self._cont_items):
                item = self._cont_items[i]
                # [tmdb-poster-fallback] same fallback as the poster grid --
                # no real poster yet, kick off a background TMDB lookup and
                # show the site's own (wrong-shaped) thumbnail meanwhile.
                url = item.get("poster") or ""
                if not url and item.get("thumbnail"):
                    self._requestTmdbPosterAsync(item)
                    url = item["thumbnail"]
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

                pos = int(item.get("last_position_sec") or 0)
                dur = int(item.get("total_duration")
                          or item.get("duration")
                          or item.get("total_seconds") or 0)
                if dur > 0:
                    # [UX] remaining time is more actionable than elapsed when
                    # deciding whether to continue something -- a leading "-"
                    # marks it as remaining, matching common player convention.
                    remaining = max(0, dur - pos)
                    mm, ss = divmod(remaining, 60)
                    hh, mm = divmod(mm, 60)
                    tstr = "-{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "-{}:{:02d}".format(mm, ss)
                    self._cont_pct[i] = min(99, max(1, int(pos * 100 / dur)))
                else:
                    # duration unknown -- fall back to elapsed (all we actually
                    # know) and don't fabricate a completion percentage.
                    mm, ss = divmod(pos, 60)
                    hh, mm = divmod(mm, 60)
                    tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
                    self._cont_pct[i] = 0
                badge.setText(tstr)
                badge.show()

                _type = item.get("type", "")
                if _type == "episode":
                    typetag.setText("Episode")
                    typetag.show()
                elif _type in ("series", "show"):
                    typetag.setText("Series")
                    typetag.show()
                else:
                    typetag.hide()
            else:
                widget.hide()
                badge.hide()
                typetag.hide()
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
            self["cont_title"].setText("Nothing in progress — start from the grid below")
            self["cont_title"].show()
            self._resetContinueZoom()

    def _updateContinueLabel(self):
        if not (0 <= self._cont_index < len(self._cont_items)):
            self["cont_title"].setText("Continue Watching")
            return
        item = self._cont_items[self._cont_index]
        pos = _get_saved_position(item.get("url", ""))
        mm, ss = divmod(max(pos, 0), 60)
        hh, mm = divmod(mm, 60)
        tstr = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
        title = _single_line_text(item.get("title", ""), width=40, fallback="")
        if title:
            self["cont_title"].setText("Continue Watching  •  {}  •  {}".format(title, tstr))
        else:
            self["cont_title"].setText("Continue Watching")

    # [pop-anim] p in [0,1] interpolates the zoomed card from base size to +8%.
    # Called with zoom_index=None to reset every slot to base.
    # [zone-slide-2] y_off shifts the whole strip vertically during the
    # strip⇄grid transition. Callers that don't pass one get the current
    # live offset, so _paintContinueRow / _moveContinueSel / _resetContinueZoom
    # keep working untouched.
    def _applyContinueLayout(self, zoom_index=None, p=1.0, y_off=None):
        if y_off is None:
            y_off = self._strip_y_off
        _bar_h = sc(6)
        _zoom_factor = 0.08 * p
        for i in range(CONT_SLOTS):
            base_x = CONT_X0 + i * (CONT_W + CONT_GAP)
            if i == zoom_index:
                zw = int(CONT_W * (1 + _zoom_factor))
                zh = int(CONT_H * (1 + _zoom_factor))
                zx = base_x - (zw - CONT_W) // 2
                zy = CONT_Y - (zh - CONT_H) // 2 + y_off
                self._moveResize("cont%d" % i, zx, zy, zw, zh)
                self._moveResize("contbadge%d" % i, zx, zy + zh - sc(28), zw, sc(28))
                self._moveResize("conttype%d" % i, zx + sc(8), zy + sc(8), zw - sc(16), sc(24))
                _bx = zx + sc(6)
                _by = zy + zh - 26 - _bar_h - sc(6)
                _bw = zw - sc(12)
                # cyan selection ring at the right edge
                self._moveResize("contSel", zx + zw + 2, zy, 6, zh)
                try: self["contSel"].show()
                except Exception: pass
            else:
                self._moveResize("cont%d" % i, base_x, CONT_Y + y_off, CONT_W, CONT_H)
                self._moveResize("contbadge%d" % i, base_x,
                                 CONT_Y + y_off + CONT_H - sc(28), CONT_W, sc(28))
                self._moveResize("conttype%d" % i, base_x + sc(8), CONT_Y + y_off + sc(8),
                                 CONT_W - sc(16), sc(24))
                _bx = base_x + sc(6)
                _by = CONT_Y + y_off + CONT_H - 26 - _bar_h - sc(6)
                _bw = CONT_W - sc(12)
            pct = self._cont_pct.get(i, 0) if i < len(self._cont_items) else 0
            try:
                # [UX] a real, known percentage only -- an empty/guessed track
                # for an unknown duration implied a measurement that wasn't
                # real, so it's hidden rather than shown as a fake 0%/35%/20%.
                if i < len(self._cont_items) and pct > 0:
                    self._moveResize("contbartrack%d" % i, _bx, _by, _bw, _bar_h)
                    self["contbartrack%d" % i].show()
                    _fill = max(sc(4), (_bw * pct) // 100)
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
        if zoom_index is None:
            try: self["contSel"].hide()
            except Exception: pass

    def _resetContinueZoom(self):
        try: self._cont_pop_timer.stop()                       # [pop-anim]
        except Exception: pass
        self._applyContinueLayout(None)
        try: self["contSel"].hide()
        except Exception: pass
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
        # [pop-anim] stop any in-flight strip animation, snap all cards to base,
        # then animate the new selection from p=0 upward.
        try:
            self._cont_pop_timer.stop()
        except Exception:
            pass
        self._applyContinueLayout(None)
        self._cont_pop_frame = 0
        try:
            self._cont_pop_timer.start(POP_ANIM_INTERVAL, False)
        except Exception as e:
            my_log("continue pop timer start failed: {}".format(e))
        self._updateContinueLabel()
        self._paintContinueBackdrop()

    def _contPopTick(self):
        """Continue-strip animation frame — same ease-out cubic as the grid."""
        if self._display_mode != "home" or self._focus_zone != "row":
            try:
                self._cont_pop_timer.stop()
            except Exception:
                pass
            return
        frames = max(1, POP_ANIM_FRAMES)
        self._cont_pop_frame += 1
        p_raw = min(1.0, float(self._cont_pop_frame) / float(frames))
        p = 1.0 - (1.0 - p_raw) ** 3
        try:
            self._applyContinueLayout(self._cont_index, p)
        except Exception as e:
            my_log("_contPopTick error: {}".format(e))
        if self._cont_pop_frame >= frames:
            try:
                self._cont_pop_timer.stop()
            except Exception:
                pass

    # ── [UX-3] hero fanart backdrop for the focused continue card ──────
    def _paintContinueBackdrop(self):
        if not (0 <= self._cont_index < len(self._cont_items)):
            return
        self._cont_backdrop_token += 1
        item = self._cont_items[self._cont_index]
        url = item.get("fanart") or item.get("backdrop") or ""
        if not url:
            self["backdropImg"].hide()
            self["shade_overlay"].hide()
            self._current_backdrop_path = ""
            if not item.get("_backdrop_fetch_tried"):
                item["_backdrop_fetch_tried"] = True
                token = self._cont_backdrop_token
                cont_index = self._cont_index
                threading.Thread(
                    target=self._bgFetchContinueBackdrop,
                    args=(item, cont_index, token), daemon=True).start()
            return
        target = (1920, 1080)
        cache_key = self._darkenedBackdropCacheKey(url)  # [PATCH G10]
        path = plugin_imagecache.getCachedImage(cache_key, target_size=target)
        if path and path != self._current_backdrop_path:
            try:
                self["backdropImg"].instance.setPixmapFromFile(path)
                self["backdropImg"].show()
                self._current_backdrop_path = path
            except Exception as e:
                my_log("continue backdrop paint failed: {}".format(e))
        elif not path:
            token = self._cont_backdrop_token
            cont_index = self._cont_index
            threading.Thread(
                target=self._bgFetchContinueBackdrop,
                args=(item, cont_index, token),
                kwargs={"known_url": url}, daemon=True).start()

    def _darkenedBackdropCacheKey(self, backdrop_url):
        # [PATCH G10] a distinct cache key so a darkened backdrop never collides with (or gets
        # silently overwritten by) any other, non-darkened use of the same URL+size elsewhere
        # -- e.g. requestImageAsyncPriority's generic async worker, which never darkens.
        return backdrop_url + "#dk45"

    def _bgFetchContinueBackdrop(self, item, cont_index, token, known_url=None):
        try:
            if known_url:
                backdrop_url = known_url
            else:
                meta = _tmdb_search_metadata(item.get("title", ""), item.get("year", ""),
                                             item.get("type", "movie"))
                if not (meta and meta.get("backdrop_url")):
                    return
                backdrop_url = meta["backdrop_url"]
                item["fanart"] = backdrop_url
            cache_key = self._darkenedBackdropCacheKey(backdrop_url)
            target_size = (1920, 1080)
            path = plugin_imagecache.getCachedImage(cache_key, target_size=target_size)
            if not path:
                data = plugin_imagecache.downloadUrl(backdrop_url, timeout=8)
                if data:
                    processed = plugin_imagecache.resizeCover(data, target_size, darken=0.45)
                    if processed is not None:
                        cache_path = plugin_imagecache.buildCachePath(cache_key, target_size=target_size)
                        if plugin_imagecache.writeFileAtomic(cache_path, processed):
                            path = cache_path
            if path:
                callInMainThread(self._paintContinueTmdbBackdrop, path, cont_index, token)
        except Exception as e:
            my_log("_bgFetchContinueBackdrop error: {}".format(e))

    def _paintContinueTmdbBackdrop(self, path, cont_index, token):
        # [PATCH G8] cont_index alone answers "is the user still on this item" --
        # the token comparison was redundant and could reject a valid, still-current
        # result whenever _paintContinueBackdrop fired twice in quick succession for
        # the same item (confirmed via real device diagnostics: this happened on the
        # very first focus of the strip, silently losing that item's backdrop forever
        # since _backdrop_fetch_tried was already set, so no retry ever happened).
        if self._cont_index != cont_index or not path:
            return
        try:
            self["backdropImg"].instance.setPixmapFromFile(path)
            self["backdropImg"].show()
            self._current_backdrop_path = path
        except Exception as e:
            my_log("_paintContinueTmdbBackdrop: setPixmapFromFile threw for {}: {}".format(path, e))

    # ── [UX-5] MENU handling ────────────────────────────────────────────
    def _onMenu(self):
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
                u"Remove from Continue Watching?\n{}".format(
                    _single_line_text(title, width=40, fallback="Item")),
                MessageBox.TYPE_YESNO, timeout=8, default=False)
            return

        if self._display_mode == "home" and self._focus_zone == "grid":
            item = self["home_grid"].getCurrent()
            if not item:
                return
            a = item.get("_action", "")
            if not a.startswith("site_"):
                return
            site_key = a.replace("site_", "")
            label = item.get("title", site_key)

            _opts = [
                ("Open site",        "open"),
                ("Rename",       "rename"),
                ("Test connection",    "ping"),
                ("Enable proxy",    "proxy"),
                ("Remove from grid",    "hide"),
            ]

            def _choice(ans, _sk=site_key, _lbl=label):
                if ans is None:
                    return
                _key = ans[1] if isinstance(ans, tuple) else None
                if _key == "open":
                    _members = get_family_members(_sk)
                    if len(_members) > 1:
                        self._showSiteFamilyPicker(_sk, _members)
                    else:
                        self._site_pick_origin = None        # [family-back] direct, ungrouped site
                        self._site = _sk
                        self._showSiteCategories()
                elif _key == "rename":
                    self._renameSite(_sk, _lbl, on_done=self._showHome)
                elif _key == "ping":
                    self._quickPingSite(_sk, _lbl)
                elif _key == "proxy":
                    try:
                        _set_config("use_proxy_for_" + _sk, "true")
                        self._setStatus("Proxy enabled for {}".format(_lbl))
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
                title=u"{} — what do you want?".format(label),
                list=_opts)
            return

        if self._display_mode == "list":
            item = self["text_list"].getCurrent()
            if not item or item.get("_action") != "site_pick":
                return
            site_key = item.get("_site")
            if not site_key:
                return
            label = item.get("title", site_key)
            rep_key, members = self._picker_rep, self._picker_members
            idx = members.index(site_key) if site_key in members else -1

            _opts = [("Rename", "rename")]
            if idx > 0:
                _opts.append(("Move up", "up"))
            if 0 <= idx < len(members) - 1:
                _opts.append(("Move down", "down"))

            def _choice(ans, _sk=site_key, _lbl=label, _rep=rep_key, _idx=idx):
                if ans is None:
                    return
                _key = ans[1] if isinstance(ans, tuple) else None
                if _key == "rename":
                    self._renameSite(_sk, _lbl,
                        on_done=lambda: self._showSiteFamilyPicker(_rep, get_family_members(_rep)))
                elif _key in ("up", "down") and _idx >= 0:
                    new_order = list(members)
                    swap_with = _idx - 1 if _key == "up" else _idx + 1
                    if 0 <= swap_with < len(new_order):
                        new_order[_idx], new_order[swap_with] = new_order[swap_with], new_order[_idx]
                        try:
                            _set_family_order(_rep, new_order)
                        except Exception:
                            pass
                        self._showSiteFamilyPicker(_rep, get_family_members(_rep), focus_site=_sk)

            self.session.openWithCallback(
                _choice, ChoiceBox,
                title=u"{} — what do you want?".format(label),
                list=_opts)
            return

    def _quickPingSite(self, site_key, label):
        self._setStatus("Testing {}…".format(label))

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
                callInMainThread(self._setStatus, "{}: failed".format(label), True)
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
            self._setStatus("Page {} / {}".format(target + 1, grid.totalPages))
        except Exception as e:
            my_log("pageJump error: {}".format(e))

    # ── Key handlers ────────────────────────────────────────────────────
    def _onOk(self):
        if self._display_mode == "home":
            if self._focus_zone == "row" and self._cont_items:
                item = self._cont_items[min(self._cont_index, len(self._cont_items) - 1)]
                # [zone-slide] jump to grid endpoint before opening so returning
                # from the detail screen lands the user on the grid, not the strip.
                self._focus_zone = "grid"
                self._zone_p = 1.0
                self._zone_target = 1.0
                self._applyZoneOffsets()
                self._hideNavHint()        # [zone-slide-2]
                if item:
                    self._openItem(item)
                return
            item = self["home_grid"].getCurrent()
            if not item:
                return
            a = item.get("_action", "")
            if a.startswith("site_"):
                site_key = a.replace("site_", "")
                members = get_family_members(site_key)
                if len(members) > 1:
                    self._showSiteFamilyPicker(site_key, members)
                else:
                    self._site_pick_origin = None            # [family-back] direct, ungrouped site
                    self._site = site_key
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
            elif item.get("_action") == "site_pick":
                # [family-back] remember which family this member came
                # from, so _onBack() returns to this picker instead of
                # the home grid once the user leaves the category list.
                self._site_pick_origin = self._picker_rep
                self._site = item.get("_site")
                self._showSiteCategories()
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
                self._startZoneAnim(0.0)   # [zone-slide] grid out, strip in
                self._moveContinueSel()
                self._updateNavHint()      # [zone-slide-2]
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
            self._startZoneAnim(1.0)       # [zone-slide] strip out, grid in
            self._updateNavHint()          # [zone-slide-2]
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
            try: self["staticBackground"].hide()  # [PATCH G9] not shown on category/list screens
            except Exception as e: my_log("staticBackground hide (list) failed: {}".format(e))
            for i in range(CONT_SLOTS):
                self["cont%d" % i].hide()
                self["contbadge%d" % i].hide()
                try:
                    self["conttype%d" % i].hide()
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
            self["homeSel"].hide()                            # [pop-out]
            self._hideNavHint()                               # [zone-slide-2 fix]
            try: self._pop_timer.stop()                       # [pop-anim]
            except Exception: pass
            try: self._cont_pop_timer.stop()                  # [pop-anim]
            except Exception: pass
            try: self._poster_pop_timer.stop()                # [pop-anim]
            except Exception: pass
            self._poster_pop_p = 1.0
            self["text_list"].show()
            self["text_list"].setList(items)
            # [catlist-effects] slide in from the right with a slight overshoot
            self._listVisible = False
            self._slideListIn()
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
            self._setStatus("{} items".format(len(items)))
            self["key_red"].setText("Back")
            self["key_green"].setText("")
            self["key_yellow"].setText("Search")
            self["key_blue"].setText("")
            self._updateGridFooter()

    def _firePendingPicker(self):
        """One-shot timer callback: rebuild the family picker a tick
        after a slide-out animation finished (see _pendingPickerTimer
        in __init__). By now _listSlideTick's own slide-out completion
        code has already reset _listSlidePhase/_listSlideTimer, so the
        new slide-in this triggers (via _showSiteFamilyPicker ->
        _setList -> _slideListIn) runs cleanly instead of being
        clobbered."""
        try:
            self._pendingPickerTimer.stop()
        except Exception:
            pass
        args = self._pendingPickerArgs
        self._pendingPickerArgs = None
        if not args:
            return
        rep_key, focus_site = args
        self._showSiteFamilyPicker(rep_key, get_family_members(rep_key), focus_site=focus_site)

    def _showSiteFamilyPicker(self, rep_key, members, focus_site=None):
        """Grid tile for a grouped site family ("rep_key") was pressed --
        show a small picker (the same plain list used for categories) so
        the user can choose which family member to actually enter.
        Reuses _setList()'s existing list-mode path; items carry
        _action="site_pick" so _onOk()'s list-mode branch routes straight
        into _showSiteCategories() for the chosen site.

        _picker_rep/_picker_members are stashed so _onMenu()'s list-mode
        branch (Rename / Move up / Move down) knows which family and
        full member order it's working with. focus_site re-selects a
        row after a reorder, so the row the user just moved stays
        highlighted instead of the selection jumping back to the top."""
        self._picker_rep = rep_key
        self._picker_members = list(members)
        items = []
        for key in members:
            items.append({
                "title": _site_label(key),
                "plot": _site_tagline(key),
                "type": "tool",
                "_action": "site_pick",
                "_site": key,
            })
        self._source = "site_pick"
        self._setList(items)
        self["title_text"].setText("Choose a site")
        self._setStatus("Pick one of the sites")
        if focus_site and focus_site in members:
            try:
                idx = members.index(focus_site)
                tl = self["text_list"]
                tl.currentPage = idx // tl.itemsPerPage
                tl.currentRow = (idx % tl.itemsPerPage) // tl.cols
                tl.currentCol = (idx % tl.itemsPerPage) % tl.cols
                tl._updateIndex()
                tl._redraw()
                tl.instance.moveSelectionTo(tl.currentRow)
                self._updateGridFooter()
            except Exception:
                pass

    def _renameSite(self, site_key, current_label, on_done=None):
        """Long-press "Rename" on a home-grid tile or a family sub-menu
        row -- types a new display name on the VirtualKeyBoard and
        saves it as that site's override (plugin_state), persisting
        across restarts and applying everywhere _site_label()/
        get_home_sites() show that site's name. An empty entry clears
        the override back to the registry default."""
        def _on_typed(new_name):
            if new_name is None:
                return
            try:
                _set_site_custom_name(site_key, new_name)
            except Exception:
                pass
            if on_done:
                on_done()
        self.session.openWithCallback(
            _on_typed, VirtualKeyBoard,
            title=u"Display name for {}".format(current_label),
            text=current_label)

    def _showSiteCategories(self):
        cached = self._cats_cache.get(self._site)
        if cached is not None:
            self._source = "categories"
            self._setList(cached)
            self["title_text"].setText("{} categories".format(_site_label(self._site)))
            self._setStatus("Choose a section")
            return
        try:
            extractor = _get_extractor(self._site)
            get_categories = getattr(extractor, "get_categories", None)
            if not get_categories:
                cats = [{"title": "No sections", "type": "error"}]
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
            cats = [{"title": "Could not load the sections", "type": "error"}]
            plugin_health.record(self._site, "blocked" if get_curl_failed_needs_proxy() else "down", str(e))
        self._cats_cache[self._site] = cats
        self._source = "categories"
        self._setList(cats)
        self["title_text"].setText("{} categories".format(_site_label(self._site)))
        self._setStatus("Choose a section")

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
        self._setStatus("Loading {}…".format(name))
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
            callInMainThread(self._setStatus, "Failed: {}".format(str(e)[:60]), True)
            callInMainThread(self._shakeList)          # [catlist-effects]

    def _onCategoryLoaded(self, items):
        if not items:
            self._setStatus("No results")
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
        from plugin_screen_settings import NovaPlaySettings
        self.session.openWithCallback(self._onSettingsClosed, NovaPlaySettings, self._site)

    def _onSettingsClosed(self, *args):
        """Apply a Poster Layout change made in Settings straight away."""
        try:
            if not self._hub and _get_config("home_style", "panel") != "classic":
                from plugin_screen_hub import NovaPlayHub
                self.session.open(NovaPlayHub)
                self.close()
                return
        except Exception as e:
            my_log("home style switch: {}".format(e))
        try:
            new = _get_config("layout_style", "carousel") or "carousel"
            if new == self._layout_style:
                return
            if self._display_mode == "poster":
                # _onGreen toggles from the stored value: hand it the old one
                _set_config("layout_style", self._layout_style)
                self._onGreen()
            else:
                self._layout_style = new
        except Exception as e:
            my_log("settings close: {}".format(e))

    def _showLibrary(self, kind):
        if kind == "favorites":
            items = _get_favorite_items_list()
        else:
            items = _history_items()
        self._source = "library"
        self._setList(items)
        self["title_text"].setText("Favorites" if kind == "favorites" else "History")
        self._setStatus("")

    def _onSearch(self, forced_scope=None):
        self.session.openWithCallback(self._onSearchQuery, NovaPlaySearch, current_site=self._site, default_scope=forced_scope or "all", query=self._last_query)

    def _onSearchQuery(self, result=None):
        if not result:
            if self._hub and self._source in ("home", "search_wait"):
                self._hubClose()
            return
        if isinstance(result, str):
            query, scope = result, "all"
        else:
            query = result[0]
            scope = result[1] if len(result) > 1 else "all"
        if not query:
            if self._hub and self._source in ("home", "search_wait"):
                self._hubClose()
            return
        self._last_query = query
        if self._hub and self._source == "home":
            self._source = "search_wait"
            self._hubHideHome()
        self._setStatus("Searching for: {}…".format(query))
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
        from plugin_util import active_search_sites
        sites = active_search_sites() if scope in ("all", "", None) else [scope]
        with ThreadPoolExecutor(max_workers=min(6, len(sites))) as ex:
            futures = [ex.submit(_search_one, name) for name in sites]
            for future in as_completed(futures):
                name, results = future.result()
                items.extend(results)

        callInMainThread(self._onSearchResults, items, query)

    def _onSearchResults(self, items, query):
        if not items:
            self._setStatus("No results")
            if self._hub and self._source == "search_wait":
                self.session.open(MessageBox, u"No results for \"{}\"".format(query), MessageBox.TYPE_INFO, timeout=4)
                self._onSearch()
            return
        self._source = "search"
        self._setList(_rank_search_items(items, query))

    def _openItem(self, item):
        self.session.open(NovaPlayDetail, item=item, site=item.get("_site", self._site), m_type=item.get("type", self._m_type))

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
            self._setStatus("Loading {}…".format(cat_name))
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
                self._updateNavHint()      # [zone-slide-2]
            return
        if self._display_mode == "list":
            self._showLibrary("favorites")

    def _onBlue(self):
        if self._source == "home":
            self._openSettings()
        else:
            self._nextPage()

    def _onBack(self):
        if self._hub and self._source in ("search", "library", "search_wait"):
            self._hubClose()
            return
        if self._source == "library":
            self._showHome()
            return
        if self._display_mode == "poster":
            if len(getattr(self, "_page_history", [])) > 1 and self._source == "category":
                self._prevPage()
            else:
                self._showSiteCategories()
        elif self._source != "home":
            # [family-back] leaving the TOP (categories) level of a site
            # that was entered through a family sub-menu picker (e.g.
            # Arabseed.living via the Arabseed tile) goes back to that
            # picker, not straight to the home grid -- _site_pick_origin
            # is the family's representative key, stashed when the user
            # picked a member from the picker (see _onOk()'s "site_pick"
            # branch below).
            _origin = getattr(self, "_site_pick_origin", None)
            if self._source == "categories" and _origin:
                def _back_to_picker(_rep=_origin, _focus=self._site):
                    self._listVisible = False
                    # [family-back] deferred one tick -- see the
                    # _pendingPickerTimer comment in __init__ for why
                    # this can't call _showSiteFamilyPicker() directly
                    # from inside a slide-out completion callback.
                    self._pendingPickerArgs = (_rep, _focus)
                    try:
                        self._pendingPickerTimer.start(10, True)
                    except Exception:
                        self._showSiteFamilyPicker(_rep, get_family_members(_rep), focus_site=_focus)
                if (self._display_mode == "list"
                        and getattr(self, "_listVisible", False)):
                    self._slideListOut(_back_to_picker)
                else:
                    _back_to_picker()
                return
            # [catlist-effects] slide the list out first, then go home
            if (self._display_mode == "list"
                    and getattr(self, "_listVisible", False)):
                def _after_out():
                    self._listVisible = False
                    self._showHome()
                self._slideListOut(_after_out)
            else:
                self._showHome()
        else:
            if self._focus_zone == "row":
                self._focus_zone = "grid"
                self._resetContinueZoom()
                self._startZoneAnim(1.0)   # [zone-slide] strip out, grid in
                self._updateNavHint()      # [zone-slide-2]
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
        try: self._pop_timer.stop()                          # [pop-anim]
        except: pass
        try: self._cont_pop_timer.stop()                     # [pop-anim]
        except: pass
        try: self._poster_pop_timer.stop()                   # [pop-anim]
        except: pass
        try: self._zone_anim_timer.stop()                    # [zone-slide]
        except: pass
        try: self._navHintTimer.stop()                       # [zone-slide-2]
        except: pass
        try: self._listSlideTimer.stop()                     # [catlist-effects]
        except: pass
        try: self._pendingPickerTimer.stop()                  # [family-back]
        except: pass
        try: self._catShakeTimer.stop()                      # [catlist-effects]
        except: pass
        try: plugin_imagecache.cancelAsyncImages()
        except: pass


def _get_favorite_items_list():
    from plugin_state import _favorite_items as _fi
    return _fi()


def _get_extractor(site):
    return get_extractor(site)
