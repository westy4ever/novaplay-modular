# -*- coding: utf-8 -*-
"""NovaPlay — Home (panel style, 2026-10-21).

The first screen: a sidebar (Home, Search, Favorites, History, Downloads,
Settings), a Continue Watching row and the site cards (logo, name,
tagline, health dot; 4 x 3 per page). It hands the browsing itself to the
existing NovaPlayHome screen, opened "for one job" (a site, a library list
or search results) -- when the user backs out of that job NovaPlayHome
closes and this screen is shown again.

The classic home grid stays available: Settings > Library & Display >
Home Style (config "home_style": "panel" | "classic").

Keys
  ▲ ▼ ◀ ▶   move (◀ from the first column goes to the sidebar)
  OK         open
  MENU       site: open / rename / test / proxy / remove — continue: remove
  RED Exit   GREEN Favorites   YELLOW Search   BLUE Settings
  EXIT       close NovaPlay
"""
import os
import threading
import time

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from novaplay_choice import NovaChoiceBox as ChoiceBox
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from enigma import eTimer, ePoint, eSize, gFont

try:
    from skin import parseColor
except Exception:
    parseColor = None

from plugin_common import my_log, PLUGIN_PATH, _PLUGIN_VERSION
from extractors import get_extractor, get_home_sites, get_family_members
import plugin_imagecache
import plugin_health
from plugin_assets import placeholder_for_item
from plugin_state import (_get_config, _set_config, _continue_items, _history_items,
                          _favorite_items, _clear_continue_item, _hide_site, _is_site_hidden,
                          _set_site_custom_name, _DEFAULT_TMDB_API_KEY)
from plugin_gridlist import resolve_icon_path, scale_skin_xml, sc, POP_ANIM_FRAMES, POP_ANIM_INTERVAL, anim_frames, anim_light
from plugin_util import _single_line_text
from novaplay_thread import callInMainThread
from plugin_toast import TOAST_XML, toast_init, toast
from novaplay_dualclock import format_dual_dates

GOLD = "#F2C230"
WHITE = "#FFFFFF"
TEXT = "#D6DCE5"
MUTED = "#A3ADBB"
CARD = "#161C26"
EDGE = "#262E3B"
ROW_ON = "#222B3A"
PANEL = "#121822"
BG = "#0C1017"
DOT = {"ok": "#30C48D", "down": "#E5484D", "blocked": "#F2A33A"}

_ICONS = os.path.join(PLUGIN_PATH, "images", "settings")

SECTIONS = (
    ("home", "Home", "home"),
    ("search", "Search", "search"),
    ("favorites", "Favorites", "star"),
    ("history", "History", "history"),
    ("downloads", "Downloads", "download"),
    ("settings", "Settings", "tools"),
)
ROWS = len(SECTIONS)
_SIDE_Y0, _SIDE_STEP = 214, 74

CONT_N = 3          # cards on screen
CONT_MAX = 20       # titles the row scrolls through
# [band] the row sits in a dark band with ‹ › arrows, like the detail
# screen's server cards
_CONT_X = (465, 935, 1405)
_CONT_Y, _CONT_W, _CONT_H = 222, 440, 132
_CONT_TW = _CONT_W - 120          # title / details / bar width

S_COLS, S_ROWS = 4, 3
S_PAGE = S_COLS * S_ROWS
_S_X = (466, 816, 1166, 1516)     # [band] inside a dark band with ‹ ›
_S_Y = (440, 618, 796)
_S_W, _S_H = 328, 158

# [pop-anim] the selected card grows 8 % (same smooth pop as the poster
# grids): (widget, x, y, w, h, resize) relative to the card's corner
POP_ZOOM = 0.08
POP_STEPS, POP_STEP_MS = POP_ANIM_FRAMES, POP_ANIM_INTERVAL
_CONT_PARTS = (("kEdge", 0, 0, _CONT_W, _CONT_H, True), ("kBox", 3, 3, _CONT_W - 6, _CONT_H - 6, True),
               ("kPoster", 16, 16, 72, 100, True), ("kTitle", 106, 18, _CONT_TW, 36, False),
               ("kSub", 106, 58, _CONT_TW, 30, False), ("kBar", 106, 100, _CONT_TW, 6, False))
_SIDE_PARTS = (("rowBg", 0, 0, 332, 66, True), ("rowBar", 0, 0, 5, 66, True),
               ("rowIcon", 18, 15, 36, 36, True), ("rowText", 70, 0, 256, 66, False))
_SITE_PARTS = (("sEdge", 0, 0, _S_W, _S_H, True), ("sBox", 3, 3, _S_W - 6, _S_H - 6, True),
               ("sLogo", 18, 18, 132, 47, True), ("sDot", _S_W - 34, 34, 14, 14, False),
               ("sName", 18, 74, _S_W - 36, 36, False), ("sTag", 18, 112, _S_W - 36, 30, False))


def _skin():
    parts = ["""
    <screen name="NovaPlayHub" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="{bg}">
        <eLabel position="0,0" size="1920,1080" backgroundColor="{bg}" zPosition="0" />
        <ePixmap position="40,30" size="84,84" pixmap="{p}/images/plugin.png" alphatest="blend" scale="1" zPosition="2" />
        <eLabel position="146,30" size="600,52" text="NOVA Media Player" font="Bold;40" foregroundColor="{gold}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="subtitle" position="146,86" size="1100,32" font="Regular;22" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="clock_time" position="1380,18" size="500,62" font="Bold;54" halign="right" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="clock_greg" position="1280,84" size="600,30" font="Regular;22" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="clock_hijri" position="1280,114" size="600,30" font="Regular;22" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />

        <widget name="sideEdge" position="40,150" size="360,810" backgroundColor="{gold}" cornerRadius="16" zPosition="1" />
        <widget name="sidePanel" position="42,152" size="356,806" backgroundColor="{panel}" cornerRadius="14" zPosition="2" />
        <eLabel position="68,174" size="300,30" text="SECTIONS" font="Regular;20" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <eLabel position="68,690" size="304,1" backgroundColor="#2A3240" zPosition="3" />
        <eLabel position="68,704" size="300,30" text="LIBRARY" font="Regular;20" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <widget name="libinfo" position="68,740" size="316,212" font="Regular;22" foregroundColor="#C9D0DA" backgroundColor="{panel}" transparent="1" zPosition="3" />

        <eLabel position="430,148" size="900,58" text="Continue Watching" font="Bold;42" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="contCount" position="1380,162" size="500,36" font="Regular;24" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <eLabel position="420,208" size="1470,160" backgroundColor="{panel}" cornerRadius="20" zPosition="1" />
        <eLabel position="420,430" size="1470,536" backgroundColor="{panel}" cornerRadius="20" zPosition="1" />
        <widget name="siteLeft" position="424,665" size="34,64" font="Regular;52" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        <widget name="siteRight" position="1852,665" size="34,64" font="Regular;52" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        <widget name="contLeft" position="424,256" size="34,64" font="Regular;52" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        <widget name="contRight" position="1852,256" size="34,64" font="Regular;52" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        <widget name="contEmpty" position="465,222" size="1380,132" font="Regular;26" valign="center" foregroundColor="{muted}" backgroundColor="{card}" cornerRadius="14" zPosition="2" />

        <eLabel position="430,376" size="900,58" text="Sites" font="Bold;42" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="siteCount" position="1380,390" size="500,36" font="Regular;24" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />

        <eLabel position="40,976" size="1840,76" backgroundColor="{panel}" cornerRadius="16" zPosition="1" />
        <eLabel position="72,1003" size="22,22" backgroundColor="#E5484D" cornerRadius="11" zPosition="2" />
        <eLabel position="106,994" size="200,40" text="Exit" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <eLabel position="312,1003" size="22,22" backgroundColor="#30A46C" cornerRadius="11" zPosition="2" />
        <eLabel position="346,994" size="220,40" text="Favorites" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <eLabel position="572,1003" size="22,22" backgroundColor="#F2C230" cornerRadius="11" zPosition="2" />
        <eLabel position="606,994" size="200,40" text="Search" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <eLabel position="812,1003" size="22,22" backgroundColor="#3E7BFA" cornerRadius="11" zPosition="2" />
        <widget name="key_blue" position="846,994" size="200,40" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <widget name="hint" position="1060,994" size="790,40" font="Regular;24" halign="right" valign="center" foregroundColor="#C9D0DA" backgroundColor="{panel}" transparent="1" zPosition="2" />
    """.format(p=PLUGIN_PATH, bg=BG, gold=GOLD, muted=MUTED, white=WHITE, panel=PANEL, card=CARD)]
    for i in range(ROWS):
        y = _SIDE_Y0 + i * _SIDE_STEP
        parts.append("""
        <widget name="rowBg{i}" position="54,{y}" size="332,66" backgroundColor="{panel}" cornerRadius="10" zPosition="4" />
        <widget name="rowBar{i}" position="54,{y}" size="5,66" backgroundColor="{gold}" zPosition="5" />
        <widget name="rowIcon{i}" position="72,{iy}" size="36,36" alphatest="blend" scale="1" zPosition="5" />
        <widget name="rowText{i}" position="124,{y}" size="256,66" font="Regular;28" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        """.format(i=i, y=y, ey=y - 2, iy=y + 15, panel=PANEL, gold=GOLD, text=TEXT))
    for i in range(CONT_N):
        x, y = _CONT_X[i], _CONT_Y
        parts.append("""
        <widget name="kEdge{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{edge}" cornerRadius="14" zPosition="2" />
        <widget name="kBox{i}" position="{x3},{y3}" size="{w3},{h3}" backgroundColor="{card}" cornerRadius="12" zPosition="3" />
        <widget name="kPoster{i}" position="{px},{py}" size="72,100" alphatest="blend" scale="1" zPosition="4" />
        <widget name="kTitle{i}" halign="left" position="{tx},{ty}" size="{tw},36" font="Bold;26" foregroundColor="{white}" backgroundColor="{card}" transparent="1" zPosition="4" />
        <widget name="kSub{i}" position="{tx},{sy}" size="{tw},30" font="Regular;20" foregroundColor="{muted}" backgroundColor="{card}" transparent="1" zPosition="4" />
        <widget name="kBar{i}" position="{tx},{by}" size="{tw},6" backgroundColor="#2A3240" foregroundColor="{gold}" borderWidth="0" cornerRadius="3" zPosition="4" />
        """.format(i=i, x=x, y=y, w=_CONT_W, h=_CONT_H, x3=x + 3, y3=y + 3, w3=_CONT_W - 6, h3=_CONT_H - 6,
                   px=x + 16, py=y + 16, tx=x + 106, ty=y + 18, sy=y + 58, by=y + 100, tw=_CONT_TW,
                   edge=EDGE, card=CARD, white=WHITE, muted=MUTED, gold=GOLD))
    for i in range(S_PAGE):
        x, y = _S_X[i % S_COLS], _S_Y[i // S_COLS]
        parts.append("""
        <widget name="sEdge{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{edge}" cornerRadius="14" zPosition="2" />
        <widget name="sBox{i}" position="{x3},{y3}" size="{w3},{h3}" backgroundColor="{card}" cornerRadius="12" zPosition="3" />
        <widget name="sLogo{i}" position="{lx},{ly}" size="132,47" alphatest="blend" scale="1" zPosition="4" />
        <widget name="sDot{i}" position="{dx},{dy}" size="14,14" backgroundColor="{card}" cornerRadius="7" zPosition="4" />
        <widget name="sName{i}" halign="left" position="{nx},{ny}" size="{tw},36" font="Bold;26" foregroundColor="{white}" backgroundColor="{card}" transparent="1" zPosition="4" />
        <widget name="sTag{i}" position="{nx},{gy}" size="{tw},30" font="Regular;20" foregroundColor="#B4BCC8" backgroundColor="{card}" transparent="1" zPosition="4" />
        """.format(i=i, x=x, y=y, w=_S_W, h=_S_H, x3=x + 3, y3=y + 3, w3=_S_W - 6, h3=_S_H - 6,
                   lx=x + 18, ly=y + 18, dx=x + _S_W - 34, dy=y + 34, nx=x + 18, ny=y + 74, gy=y + 112, tw=_S_W - 36,
                   edge=EDGE, card=CARD, white=WHITE))
    parts.append(TOAST_XML)
    parts.append("</screen>")
    return "".join(parts)


def _norm_title(t):
    import re as _re
    t = _re.sub(r"\(?\b(19|20)\d{2}\b\)?", " ", str(t or "").lower())
    return _re.sub(r"[^0-9a-z\u0600-\u06ff]+", "", t)


def _remaining(item):
    pos = int(item.get("last_position_sec") or 0)
    dur = int(item.get("total_duration") or item.get("duration") or item.get("total_seconds") or 0)
    if dur > 0:
        left = max(0, dur - pos)
        h, m = divmod(left // 60, 60)
        txt = ("{}h {:02d}m left".format(h, m) if h else "{} min left".format(max(1, m)))
        return txt, min(99, max(1, int(pos * 100 / dur)))
    m, s = divmod(pos, 60)
    h, m = divmod(m, 60)
    return ("at {}:{:02d}:{:02d}".format(h, m, s) if h else "at {}:{:02d}".format(m, s)), 0


class NovaPlayHub(Screen):
    skin = scale_skin_xml(_skin())

    def __init__(self, session):
        Screen.__init__(self, session)
        self._zone = "sites"          # side | cont | sites
        self._sec = 0
        self._ci = 0                  # continue index
        self._cbase = 0               # first title shown in the row
        self._cont_slide = 0
        self._site_slide = 0          # +1 / -1 while a new sites page glides in
        self._si = 0                  # site index (global)
        self._sites = []
        self._cont = []
        self._swept = False

        self["subtitle"] = Label("Version {}  |  Immersive Media Experience".format(_PLUGIN_VERSION))
        self["clock_time"] = Label("")
        self["clock_greg"] = Label("")
        self["clock_hijri"] = Label("")
        self["sideEdge"] = Label("")
        self["sidePanel"] = Label("")
        self["libinfo"] = Label("")
        self["contCount"] = Label("")
        self["contEmpty"] = Label("")
        self["contLeft"] = Label(u"\u2039")
        self["siteLeft"] = Label(u"\u2039")
        self["siteRight"] = Label(u"\u203a")
        toast_init(self)
        self["contRight"] = Label(u"\u203a")
        self["siteCount"] = Label("")
        self["key_blue"] = Label("Settings")
        self["hint"] = Label("")
        for i in range(ROWS):
            self["rowBg%d" % i] = Label("")
            self["rowBar%d" % i] = Label("")
            self["rowIcon%d" % i] = Pixmap()
            self["rowText%d" % i] = Label(SECTIONS[i][1])
        for i in range(CONT_N):
            for n in ("kEdge", "kBox", "kTitle", "kSub"):
                self["%s%d" % (n, i)] = Label("")
            self["kPoster%d" % i] = Pixmap()
            self["kBar%d" % i] = ProgressBar()
        for i in range(S_PAGE):
            for n in ("sEdge", "sBox", "sDot", "sName", "sTag"):
                self["%s%d" % (n, i)] = Label("")
            self["sLogo%d" % i] = Pixmap()

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions",
             "InfobarMenuActions"], {
                "ok": self._ok,
                "cancel": self.close,
                "up": lambda: self._move(0, -1),
                "down": lambda: self._move(0, 1),
                "left": lambda: self._move(-1, 0),
                "right": lambda: self._move(1, 0),
                "red": self.close,
                "green": lambda: self._openSection("favorites"),
                "yellow": lambda: self._openSection("search"),
                "blue": lambda: self._openSection("settings"),
                "menu": self._menu,
                "showMenu": self._menu,
                "mainMenu": self._menu,
            }, -1)

        self._clock = eTimer()
        self._clock.callback.append(self._tick)
        self._art = eTimer()
        self._art.callback.append(self._artPoll)
        self._art_tries = 0
        self._pop = eTimer()
        self._pop.callback.append(self._popTick)
        self._pop_card = None        # ("cont"|"sites", slot) being zoomed
        self._pop_frame = 0
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._stopTimers)
        self.onClose.append(self._restoreFonts)

    # ─── lifecycle ──────────────────────────────────────────────────────

    def _restoreFonts(self):
        """[fonts] leaving NovaPlay: the skin's own fallback font again"""
        try:
            import novaplay_fonts
            novaplay_fonts.restore_skin()
        except Exception:
            pass

    def _start(self):
        try:
            import novaplay_fonts               # [fonts] Arabic always drawable here
            novaplay_fonts.ensure_arabic()
        except Exception:
            pass
        self._tick()
        self._clock.start(10000, False)
        self._reload()
        self._zone = "cont" if self._cont else "sites"
        self._paint()
        self._healthSweep()

    def _stopTimers(self):
        for t in (self._clock, self._art, self._pop, getattr(self, "_frameT", None)):
            try:
                t.stop()
            except Exception:
                pass

    def _tick(self):
        try:
            greg, hijri, t, period = format_dual_dates()
            self["clock_time"].setText("{} {}".format(t, period))
            self["clock_greg"].setText(greg)
            self["clock_hijri"].setText(hijri)
        except Exception as e:
            my_log("hub clock: {}".format(e))

    def _reload(self):
        try:
            # one card per title (the same film watched from two sites)
            seen, self._cont = set(), []
            _all = _continue_items(500)
            self._cont_total = len(set(_norm_title(i.get("title", "")) for i in _all))
            for it in _all:
                k = _norm_title(it.get("title", ""))
                if k in seen:
                    continue
                seen.add(k)
                self._cont.append(it)
                if len(self._cont) >= CONT_MAX:
                    break
        except Exception:
            self._cont = []
        self._ci = min(self._ci, max(0, len(self._cont) - 1))
        self._cbase = max(0, min(self._cbase, self._ci, len(self._cont) - CONT_N))
        recent = {}
        now = time.time()
        try:
            for h in (_history_items() or [])[:200]:
                s = h.get("_site")
                try:
                    ts = float(h.get("_ts") or 0)
                except Exception:
                    ts = 0
                if s and ts and now - ts < 86400:
                    recent[s] = recent.get(s, 0) + 1
        except Exception:
            pass
        rank = {"blocked": 1, "down": 2}
        rows = [(k, t, g) for k, t, g in get_home_sites() if not _is_site_hidden(k)]
        rows.sort(key=lambda r: (rank.get(plugin_health.get(r[0]), 0), -recent.get(r[0], 0)))
        self._sites = [{"key": k, "title": t, "tagline": g, "_action": "site_" + k} for k, t, g in rows]
        self._si = min(self._si, max(0, len(self._sites) - 1))
        try:
            fav, hist = len(_favorite_items()), len(_history_items())
        except Exception:
            fav = hist = 0
        key = (_get_config("tmdb_api_key", "") or "").strip()
        proxy = (_get_config("browser_proxy", "") or "").strip()
        dl = 0
        try:
            import plugin_downloads
            dl = len([t for t in plugin_downloads.get_all_tasks() if t.status == "downloading"])
        except Exception:
            pass
        self["libinfo"].setText("\n".join([
            "Favorites: {}".format(fav),
            "History: {}".format(hist),
            "Downloads: {} active".format(dl),
            "TMDb: {}".format("Built-in key" if key == _DEFAULT_TMDB_API_KEY else ("Your key" if key else "Off")),
            "Proxy: {}".format("On" if proxy else "Off"),
        ]))

    # ─── painting ───────────────────────────────────────────────────────

    def _color(self, name, bg=None, fg=None):
        inst = self[name].instance
        if inst is None or parseColor is None:
            return
        if bg:
            inst.setBackgroundColor(parseColor(bg))
        if fg:
            inst.setForegroundColor(parseColor(fg))
        inst.invalidate()

    def _pix(self, name, path):
        inst = self[name].instance
        if inst is not None and path and os.path.exists(path):
            inst.setPixmapFromFile(path)
            self[name].show()
        else:
            self[name].hide()

    def _paint(self):
        side = self._zone == "side"
        self._color("sideEdge", bg=GOLD if side else EDGE)
        self._framePop("sideEdge", (40, 150, 360, 810), side)
        for i, (_k, _t, ic) in enumerate(SECTIONS):
            on = i == self._sec
            self._color("rowBg%d" % i, bg=ROW_ON if on else PANEL)
            self["rowBg%d" % i].show() if on else self["rowBg%d" % i].hide()
            self["rowBar%d" % i].show() if on else self["rowBar%d" % i].hide()
            self._color("rowText%d" % i, fg=WHITE if on else TEXT)
            self._pix("rowIcon%d" % i, os.path.join(_ICONS, "side_%s_%s.png" % (ic, "on" if on else "off")))
        self._paintCont()
        self._paintSites()
        self._popFocus()
        if side:
            self["hint"].setText("OK  Open    ▶  Back to the list    EXIT  Close")
        elif self._zone == "cont":
            self["hint"].setText("OK  Resume    MENU  Remove    ◀  Sections")
        else:
            self["hint"].setText("OK  Open site    MENU  Site options    ◀  Sections")

    # ─── [pop-anim] the panel's gold frame pops out while it has focus ──

    def _framePop(self, name, box, on):
        """box = (x, y, w, h) of the frame at rest; grows up to 7 px all
        round, smoothly, when on; snaps back when off."""
        st = getattr(self, "_frame_st", None)
        if st is None:
            st = self._frame_st = {}
            self._frameT = eTimer()
            self._frameT.callback.append(self._frameTick)
        cur = st.get(name)
        if cur and cur["on"] == on:
            return
        st[name] = {"on": on, "box": box, "f": 0}
        if not on:
            self._framePlace(name, box, 0.0)
            return
        try:
            self._frameT.start(POP_STEP_MS, False)
        except Exception:
            self._framePlace(name, box, 1.0)

    def _framePlace(self, name, box, p):
        x, y, w, h = box
        d = 7.0 * p
        inst = self[name].instance
        if inst is None:
            return
        try:
            inst.move(ePoint(sc(x - d), sc(y - d)))
            inst.resize(eSize(max(1, sc(w + 2 * d)), max(1, sc(h + 2 * d))))
        except Exception:
            pass
        # the panel inside grows with it, so the frame stays a thin line and
        # the whole box pops out (like a poster) instead of a thicker border
        inner = {"sideEdge": "sidePanel", "catEdge": "catPanel"}.get(name)
        try:
            ii = self[inner].instance if inner else None
            if ii is not None:
                ii.move(ePoint(sc(x + 2 - d), sc(y + 2 - d)))
                ii.resize(eSize(max(1, sc(w - 4 + 2 * d)), max(1, sc(h - 4 + 2 * d))))
        except Exception:
            pass

    def _frameTick(self):
        import math
        busy = False
        for name, st in self._frame_st.items():
            if not st["on"] or st["f"] >= anim_frames():
                continue
            st["f"] += 1
            t = min(1.0, st["f"] / float(anim_frames()))
            self._framePlace(name, st["box"], math.sin(t * math.pi / 2))
            busy = busy or t < 1.0
        if not busy:
            self._frameT.stop()

    # ─── [pop-anim] zoom on the selected card ───────────────────────────

    def _placeCard(self, card, p, xoff=0.0):
        zone, i = card[0], card[1]
        if zone == "side":
            # like the cards: wider AND taller (into the gap), thicker gold
            # outline, icon and text grow too
            W, H = 332, 66
            dw, dh = 20.0 * p, float(_SIDE_STEP - H) * p
            x0, y0 = 54 - dw / 2.0, _SIDE_Y0 + i * _SIDE_STEP - dh / 2.0
            w, h = W + dw, H + dh
            ic = 36 if anim_light() else 36 + 6 * p
            for name, x, y, ww, hh in (("rowBg", x0, y0, w, h), ("rowBar", x0, y0, 5, h),
                                       ("rowIcon", x0 + 18, y0 + (h - ic) / 2.0, ic, ic),
                                       ("rowText", x0 + 70 + 6 * p, y0, w - 76, h)):
                self._mrc("%s%d" % (name, i), x, y, ww, hh)
            size = sc(28 + 3 * p)
            inst = self["rowText%d" % i].instance
            fonts = self.__dict__.setdefault("_side_font", {})
            if inst is not None and fonts.get(i) != size:
                try:
                    inst.setFont(gFont("Regular", size))
                    fonts[i] = size
                except Exception:
                    pass
            return
        if zone == "cont":
            x0, y0, W, H, parts = _CONT_X[0] + i * (_CONT_X[1] - _CONT_X[0]) + xoff, _CONT_Y, _CONT_W, _CONT_H, _CONT_PARTS
        else:
            x0, y0, W, H, parts = _S_X[i % S_COLS] + xoff, _S_Y[i // S_COLS], _S_W, _S_H, _SITE_PARTS
        k = 1.0 + POP_ZOOM * p
        cx, cy = x0 - (W * k - W) / 2.0, y0 - (H * k - H) / 2.0
        light = anim_light()
        for name, rx, ry, w, h, rs in parts:
            if rs and light and name in ("kPoster", "sLogo"):
                # Light: pictures keep their size, they only move with the card
                self._mrc("%s%d" % (name, i), cx + rx * k, cy + ry * k, w, h)
                continue
            if rs:
                self._mrc("%s%d" % (name, i), cx + rx * k, cy + ry * k, w * k, h * k)
            else:
                self._mrc("%s%d" % (name, i), cx + rx * k, cy + ry * k)

    def _mrc(self, name, x, y, w=None, h=None):
        """[perf] move / resize, skipped when it would not change a pixel"""
        geo = self.__dict__.setdefault("_geo", {})
        pos = (sc(x), sc(y))
        size = (max(1, sc(w)), max(1, sc(h))) if w is not None else None
        old = geo.get(name)
        if old and old[0] == pos and (size is None or old[1] == size):
            return
        inst = self[name].instance
        if inst is None:
            return
        try:
            if not old or old[0] != pos:
                inst.move(ePoint(*pos))
            if size is not None and (not old or old[1] != size):
                inst.resize(eSize(*size))
            geo[name] = (pos, size if size is not None else (old[1] if old else None))
        except Exception:
            pass

    def _popFocus(self):
        if self._zone == "side":
            card = ("side", self._sec, 0)
        elif self._zone == "cont" and self._cont:
            card = ("cont", self._ci - self._cbase, self._cbase)
        elif self._zone == "sites" and self._sites:
            card = ("sites", self._si % S_PAGE, self._si // S_PAGE)
        else:
            card = None
        if card == self._pop_card:
            return
        try:
            self._pop.stop()
        except Exception:
            pass
        if self._pop_card:
            self._placeCard(self._pop_card, 0.0)
        self._pop_card = card
        if not card:
            self._cont_slide = 0
            self._site_slide = 0
            return
        self._pop_frame = 0
        self._popFrame(0.0)
        try:
            self._pop.start(POP_ANIM_INTERVAL, False)
        except Exception:
            self._popFrame(1.0)

    def _popFrame(self, p):
        """[slide-anim] Continue Watching scrolled by one: all its cards glide
        one card sideways while the selected one grows; else just the zoom."""
        if self._pop_card and self._pop_card[0] == "sites" and self._site_slide:
            # a new page of sites glides in from the side it was asked from
            off = self._site_slide * (1920 - _S_X[0]) * (1.0 - p)
            for i in range(S_PAGE):
                self._placeCard(("sites", i, 0), p if i == self._pop_card[1] else 0.0, off)
            return
        d = self._cont_slide if self._pop_card and self._pop_card[0] == "cont" else 0
        if d:
            off = d * (_CONT_X[1] - _CONT_X[0]) * (1.0 - p)
            for i in range(CONT_N):
                self._placeCard(("cont", i, 0), p if i == self._pop_card[1] else 0.0, off)
        else:
            self._placeCard(self._pop_card, p)

    def _popTick(self):
        if not self._pop_card:
            self._pop.stop()
            return
        import math
        self._pop_frame += 1
        t = min(1.0, self._pop_frame / float(max(1, anim_frames())))
        self._popFrame(math.sin(t * math.pi / 2))
        if t >= 1.0:
            self._pop.stop()
            self._cont_slide = 0
            self._site_slide = 0

    def _paintCont(self):
        n = len(self._cont)
        tot = max(n, getattr(self, "_cont_total", n))
        if n > CONT_N:
            self["contCount"].setText(u"{} / {}  ·  \u25c0 \u25b6 scroll  ·  MENU to clear".format(self._ci + 1, tot if tot > n else n))
        else:
            self["contCount"].setText(("{} of {}  ·  MENU to clear".format(n, tot) if tot > n else "{} items".format(n)) if n else "")
        if not n:
            self["contEmpty"].setText("      Nothing in progress — pick a site below")
            self["contEmpty"].show()
        else:
            self["contEmpty"].hide()
        # ‹ › only when there are more titles that way
        self["contLeft"].show() if self._cbase > 0 else self["contLeft"].hide()
        self["contRight"].show() if self._cbase + CONT_N < n else self["contRight"].hide()
        missing = False
        for i in range(CONT_N):
            names = ["kEdge%d", "kBox%d", "kPoster%d", "kTitle%d", "kSub%d", "kBar%d"]
            g = self._cbase + i
            if g >= n:
                for nm in names:
                    self[nm % i].hide()
                continue
            for nm in names:
                self[nm % i].show()
            it = self._cont[g]
            sel = self._zone == "cont" and g == self._ci
            self._color("kEdge%d" % i, bg=GOLD if sel else EDGE)
            self["kTitle%d" % i].setText(_single_line_text(it.get("title", ""), width=24, fallback="—"))
            self._color("kTitle%d" % i, fg=GOLD if sel else WHITE)
            left, pct = _remaining(it)
            kind = {"episode": "Episode", "series": "Series"}.get(it.get("type", ""), "Movie")
            self["kSub%d" % i].setText("{}  ·  {}".format(kind, left))
            self["kBar%d" % i].setValue(pct)
            url = it.get("poster") or it.get("thumbnail") or ""
            path = plugin_imagecache.getCachedImage(url, target_size=(72, 100)) if url else ""
            if url and not path:
                plugin_imagecache.requestImageAsyncPriority(url, target_size=(72, 100))
                missing = True
            self._pix("kPoster%d" % i, path or placeholder_for_item(it))
        if missing:
            self._art_tries = 0
            self._art.start(1000, False)

    def _artPoll(self):
        self._art_tries += 1
        done = True
        for it in self._cont[self._cbase:self._cbase + CONT_N]:
            url = it.get("poster") or it.get("thumbnail") or ""
            if url and not plugin_imagecache.getCachedImage(url, target_size=(72, 100)):
                done = False
        if done or self._art_tries > 15:
            self._art.stop()
            self._paintCont()

    def _paintSites(self):
        n = len(self._sites)
        page = self._si // S_PAGE
        pages = max(1, (n + S_PAGE - 1) // S_PAGE)
        self["siteCount"].setText("{} sites  ·  Page {}/{}".format(n, page + 1, pages))
        self["siteLeft"].show() if page > 0 else self["siteLeft"].hide()
        self["siteRight"].show() if page + 1 < pages else self["siteRight"].hide()
        base = page * S_PAGE
        for i in range(S_PAGE):
            g = base + i
            names = ["sEdge%d", "sBox%d", "sLogo%d", "sDot%d", "sName%d", "sTag%d"]
            if g >= n:
                for nm in names:
                    self[nm % i].hide()
                continue
            for nm in names:
                self[nm % i].show()
            s = self._sites[g]
            sel = self._zone == "sites" and g == self._si
            self._color("sEdge%d" % i, bg=GOLD if sel else EDGE)
            self["sName%d" % i].setText(_single_line_text(s["title"], width=20, fallback=s["key"]))
            self._color("sName%d" % i, fg=GOLD if sel else WHITE)
            self["sTag%d" % i].setText(_single_line_text(s.get("tagline") or "", width=30, fallback=""))
            self._color("sDot%d" % i, bg=DOT.get(plugin_health.get(s["key"]), CARD))
            self._pix("sLogo%d" % i, resolve_icon_path(s))

    # ─── health dots (same check the classic home runs) ─────────────────

    def _healthSweep(self):
        if self._swept:
            return
        self._swept = True
        pending = [s["key"] for s in self._sites if plugin_health.get(s["key"]) == "unknown"]
        if not pending:
            return

        def _worker():
            for key in pending:
                try:
                    ex = get_extractor(key)
                    cats = getattr(ex, "get_categories", None)
                    if cats:
                        cats("movie") if key in ("egydead", "egydead_coupons") else cats()
                    plugin_health.record(key, "ok")
                except Exception as e:
                    plugin_health.record(key, "down", str(e))
                callInMainThread(self._paintSites)
        threading.Thread(target=_worker, daemon=True).start()

    # ─── navigation ─────────────────────────────────────────────────────

    def _move(self, dx, dy):
        if self._zone == "side":
            if dy:
                self._sec = (self._sec + dy) % ROWS
            elif dx > 0:
                self._zone = "cont" if self._cont else "sites"
                self._sec = 0
        elif self._zone == "cont":
            n = len(self._cont)
            if dx < 0 and self._ci == 0:
                self._zone = "side"
            elif dx:
                self._ci = max(0, min(n - 1, self._ci + dx))
                old = self._cbase
                if self._ci < self._cbase:
                    self._cbase = self._ci
                elif self._ci >= self._cbase + CONT_N:
                    self._cbase = self._ci - CONT_N + 1
                if abs(self._cbase - old) == 1:
                    self._cont_slide = self._cbase - old
            elif dy > 0 and self._sites:
                self._zone = "sites"
                page = self._si // S_PAGE
                self._si = min(len(self._sites) - 1, page * S_PAGE + min(self._ci - self._cbase, S_COLS - 1))
        else:
            n = len(self._sites)
            _page0 = self._si // S_PAGE
            local = self._si % S_PAGE
            col, row = local % S_COLS, local // S_COLS
            page = self._si // S_PAGE
            if dx < 0 and col == 0:
                if page > 0:
                    self._si = max(0, self._si - S_PAGE + S_COLS - 1)
                else:
                    self._zone = "side"
            elif dx > 0 and (col == S_COLS - 1 or self._si == n - 1):
                nxt = (page + 1) * S_PAGE + row * S_COLS
                if (page + 1) * S_PAGE < n:
                    self._si = min(n - 1, nxt)
            elif dx:
                self._si = max(0, min(n - 1, self._si + dx))
            elif dy < 0:
                if row > 0:
                    self._si -= S_COLS
                elif self._cont:
                    self._zone = "cont"
                    self._ci = min(self._cbase + min(col, CONT_N - 1), len(self._cont) - 1)
                elif page > 0:
                    self._si = max(0, self._si - S_COLS)
            elif dy > 0:
                t = self._si + S_COLS
                if t < n:
                    self._si = t
                elif (page + 1) * S_PAGE < n:
                    self._si = n - 1
        self._paint()

    def _ok(self):
        if self._zone == "side":
            self._openSection(SECTIONS[self._sec][0])
        elif self._zone == "cont":
            if self._cont:
                self._openItem(self._cont[self._ci])
        elif self._sites:
            self._openSite(self._sites[self._si]["key"])

    # ─── opening things ─────────────────────────────────────────────────

    def _back(self, *args):
        """Returned from another screen: refresh what may have changed."""
        try:
            import novaplay_fonts               # a plugin opened meanwhile may have
            novaplay_fonts.ensure_arabic()      # swapped the fallback font again
        except Exception:
            pass
        try:
            self._reload()
            self._paint()
        except Exception as e:
            my_log("hub refresh: {}".format(e))

    def _openSite(self, key):
        members = get_family_members(key)
        if len(members) > 1:
            from plugin_screen_family import NovaFamilyPicker

            def _picked(choice):
                if choice:
                    self._openSiteScreen(choice)
                else:
                    self._back()          # names / order may have changed
            self.session.openWithCallback(_picked, NovaFamilyPicker, key)
            return
        self._openSiteScreen(key)

    def _openSiteScreen(self, key):
        from plugin_screen_site import NovaPlaySite
        self.session.openWithCallback(self._back, NovaPlaySite, key)

    def _openItem(self, item):
        from plugin_screen_detail import NovaPlayDetail
        self.session.openWithCallback(self._back, NovaPlayDetail, item=item,
                                      site=item.get("_site", ""), m_type=item.get("type", "movie"),
                                      resume=True)

    def _openSection(self, key):
        if key == "home":
            self._zone = "cont" if self._cont else "sites"
            self._paint()
        elif key == "search":
            from plugin_screen_search import NovaPlaySearch
            self.session.openWithCallback(self._onQuery, NovaPlaySearch, current_site="",
                                          default_scope="all", query=getattr(self, "_last_query", ""))
        elif key in ("favorites", "history"):
            from plugin_screen_site import NovaPlaySite
            items = _favorite_items() if key == "favorites" else _history_items()
            self.session.openWithCallback(self._back, NovaPlaySite, None,
                                          title="Favorites" if key == "favorites" else "History",
                                          items=[dict(i) for i in (items or [])], library=key)
        elif key == "downloads":
            from plugin_screen_downloads import NovaPlayDownloads
            self.session.openWithCallback(self._back, NovaPlayDownloads)
        elif key == "settings":
            from plugin_screen_settings import NovaPlaySettings
            self.session.openWithCallback(self._afterSettings, NovaPlaySettings, None)

    def _onQuery(self, result=None):
        if not result:
            return
        query, scope = (result, "all") if isinstance(result, str) else (result[0], result[1] if len(result) > 1 else "all")
        if not query:
            return
        self._last_query = query
        from plugin_screen_site import NovaPlaySite, search_items
        self.session.openWithCallback(self._back, NovaPlaySite, None, title=u"Search: {}".format(query),
                                      loader=lambda: search_items(query, scope or "all"))

    def _afterSettings(self, *args):
        if _get_config("home_style", "panel") == "classic":
            from plugin_screen_home import NovaPlayHome
            self.session.open(NovaPlayHome)
            self.close()
            return
        self._back()

    # ─── MENU ───────────────────────────────────────────────────────────

    def _menu(self):
        if self._zone == "cont" and self._cont:
            it = self._cont[self._ci]
            try:
                from plugin_state import _continue_items as _ci
                total = len(_ci(500))
            except Exception:
                total = len(self._cont)

            def _do(ans, _url=it.get("url", "")):
                act = ans[1] if ans else None
                if act == "one":
                    try:
                        _clear_continue_item(_url)
                    except Exception as e:
                        my_log("clear continue: {}".format(e))
                    self._back()
                    toast(self, u"Removed from Continue Watching")
                elif act == "all":
                    def _yes(ok):
                        if ok:
                            try:
                                from plugin_state import _clear_all_continue
                                _clear_all_continue()
                            except Exception as e:
                                my_log("clear all continue: {}".format(e))
                            self._back()
                            toast(self, u"Continue Watching cleared")
                    self.session.openWithCallback(_yes, MessageBox,
                        u"Clear Continue Watching?\n\n{} unfinished titles will be removed from the list "
                        u"(they stay in History).".format(total), MessageBox.TYPE_YESNO, default=False)
            self.session.openWithCallback(_do, ChoiceBox, title=u"Continue Watching", list=[
                (u"Remove \"{}\"".format(_single_line_text(it.get("title", ""), width=40, fallback="this title")), "one"),
                (u"Clear all ({} titles)".format(total), "all")])
            return
        if self._zone != "sites" or not self._sites:
            return
        s = self._sites[self._si]
        key, label = s["key"], s["title"]

        def _choice(ans):
            act = ans[1] if isinstance(ans, tuple) else None
            if act == "open":
                self._openSite(key)
            elif act == "rename":
                def _typed(name):
                    if name is not None:
                        try:
                            _set_site_custom_name(key, name)
                        except Exception:
                            pass
                        self._back()
                self.session.openWithCallback(_typed, VirtualKeyBoard,
                                              title=u"Display name for {}".format(label), text=label)
            elif act == "ping":
                self["subtitle"].setText("Testing {}…".format(label))

                def _worker():
                    try:
                        ex = get_extractor(key)
                        cats = getattr(ex, "get_categories", None)
                        if cats:
                            cats("movie") if key in ("egydead", "egydead_coupons") else cats()
                        plugin_health.record(key, "ok")
                        msg = "{}: OK".format(label)
                    except Exception as e:
                        plugin_health.record(key, "down", str(e))
                        msg = "{}: failed".format(label)
                    callInMainThread(self["subtitle"].setText, msg)
                    callInMainThread(self._paintSites)
                threading.Thread(target=_worker, daemon=True).start()
            elif act == "proxy":
                _set_config("use_proxy_for_" + key, "true")
                self["subtitle"].setText("Proxy enabled for {}".format(label))
            elif act == "hide":
                try:
                    _hide_site(key)
                except Exception:
                    pass
                self._back()
        self.session.openWithCallback(_choice, ChoiceBox, title=u"{} — what do you want?".format(label), list=[
            ("Open site", "open"), ("Rename", "rename"), ("Test connection", "ping"),
            ("Enable proxy", "proxy"), ("Remove from home", "hide")])
