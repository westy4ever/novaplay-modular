# -*- coding: utf-8 -*-
"""NovaPlay — site screen (panel style, 2026-10-21).

  left    the site's categories (separators shown as headings,
          sub-folders open in place, EXIT goes back up)
  middle  posters of the open category, 5 x 2 per view; moving past the
          last row pages through the loaded titles and then loads the
          site's next page
  right   the selected title: backdrop, name, type/year/rating, plot
          (from the site, completed by TMDb in the background)

Also used for a fixed list (Favorites, History, search results):
NovaPlaySite(session, None, title=..., items=[...]) or loader=callable.

Keys
  ▲ ▼ ◀ ▶  move (◀ from the first poster column goes to the categories)
  OK        categories: open — posters: open the title
  RED  previous page   GREEN  favorite on/off   YELLOW  search this site
  BLUE next page       EXIT   posters -> categories -> up a folder -> close
"""
import os
import threading
import time

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer

try:
    from skin import parseColor
except Exception:
    parseColor = None

from plugin_common import my_log, PLUGIN_PATH
from extractors import get_extractor, get_search_site_order
from extractors.base import get_curl_failed_needs_proxy
import plugin_imagecache
import plugin_health
from plugin_assets import placeholder_for_item
from plugin_state import (_get_config, _get_saved_position, _is_favorite,
                          _entry_from_item, _toggle_favorite_entry)
from plugin_gridlist import resolve_icon_path, scale_skin_xml
from plugin_util import (_site_label, _site_search_item, _single_line_text, _wrap_ui_text,
                         _dedupe_items, _rank_search_items, _strip_arabic_from_english_title)
from plugin_tmdb import _tmdb_search_metadata, fill_item_rating_async
from novaplay_thread import callInMainThread

GOLD = "#F2C230"
WHITE = "#FFFFFF"
TEXT = "#D6DCE5"
MUTED = "#A3ADBB"
CARD = "#161C26"
EDGE = "#262E3B"
ROW_ON = "#222B3A"
PANEL = "#121822"
BG = "#0C1017"

CAT_ROWS = 10
_CAT_Y0, _CAT_STEP, _CAT_H = 212, 62, 56

P_COLS, P_ROWS = 5, 2
P_VIEW = P_COLS * P_ROWS
PW, PH = 178, 267
_PX = tuple(430 + c * (PW + 15) for c in range(P_COLS))
_PY = (222, 574)

# [carousel] 7 posters along the bottom, the middle one larger, over a
# full-screen darkened backdrop with the title / details / plot on top
K_SLOTS, K_MID = 7, 3
KW, KH = 190, 285
KCW, KCH = 232, 348
_KX = (215, 425, 635, 845, 1097, 1307, 1517)
_KY, _KCY = 620, 590

_PAGED_SITES = ("egydead", "egydead_coupons", "fasel", "faselhdx")
_ADULT = ["18+", "للكبار", "سكس", "xxx", "adult", "إباح", "sex"]
_CONTENT = ("movie", "series", "episode", "season")
_KIND = {"movie": "Movie", "series": "Series", "episode": "Episode", "season": "Season"}


def _skin():
    parts = ["""
    <screen name="NovaPlaySite" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="{bg}">
        <eLabel position="0,0" size="1920,1080" backgroundColor="{bg}" zPosition="0" />
        <widget name="kBackdrop" position="0,0" size="1920,1080" alphatest="blend" scale="1" zPosition="1" />
        <widget name="logo" position="40,40" size="180,64" alphatest="blend" scale="1" zPosition="2" />
        <widget name="title" position="240,32" size="1100,52" halign="left" font="Bold;40" foregroundColor="{gold}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="subtitle" position="240,86" size="1100,32" halign="left" font="Regular;22" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="clock_time" position="1380,22" size="500,62" font="Bold;54" halign="right" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="clock_date" position="1280,90" size="600,30" font="Regular;22" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />

        <widget name="catEdge" position="40,150" size="360,810" backgroundColor="{edge}" cornerRadius="16" zPosition="1" />
        <widget name="catPanel" position="42,152" size="356,806" backgroundColor="{panel}" cornerRadius="14" zPosition="2" />
        <widget name="catHead" position="68,170" size="300,30" font="Regular;20" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <widget name="catPage" position="68,906" size="300,30" font="Regular;20" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />

        <widget name="gridTitle" position="430,148" size="700,58" halign="left" font="Bold;42" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="gridCount" position="1080,162" size="300,36" font="Regular;24" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="gridMsg" position="430,226" size="950,602" font="Regular;28" halign="center" valign="center" foregroundColor="{muted}" backgroundColor="{card}" cornerRadius="14" zPosition="2" />
        <widget name="gridMore" position="430,906" size="950,34" font="Regular;22" halign="center" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />

        <widget name="infoPanel" position="1410,150" size="470,810" backgroundColor="{panel}" cornerRadius="16" zPosition="1" />
        <widget name="infoBack" position="1430,170" size="430,242" backgroundColor="{card}" cornerRadius="12" zPosition="2" />
        <widget name="infoImg" position="1430,170" size="430,242" alphatest="blend" scale="1" zPosition="3" />
        <widget name="infoTitle" position="1430,428" size="430,84" halign="left" font="Bold;32" foregroundColor="{gold}" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <widget name="infoMeta" position="1430,516" size="430,32" font="Regular;22" foregroundColor="#C9D0DA" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <widget name="infoState" position="1430,552" size="430,32" font="Regular;22" foregroundColor="#30C48D" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <widget name="infoPlot" position="1430,596" size="430,346" font="Regular;22" foregroundColor="#B4BCC8" backgroundColor="{panel}" transparent="1" zPosition="3" />

        <eLabel position="40,976" size="1840,76" backgroundColor="{panel}" cornerRadius="16" zPosition="3" />
        <eLabel position="72,1003" size="22,22" backgroundColor="#E5484D" cornerRadius="11" zPosition="4" />
        <eLabel position="106,994" size="230,40" text="Previous page" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <eLabel position="352,1003" size="22,22" backgroundColor="#30A46C" cornerRadius="11" zPosition="4" />
        <widget name="key_green" position="386,994" size="230,40" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <eLabel position="632,1003" size="22,22" backgroundColor="#F2C230" cornerRadius="11" zPosition="4" />
        <eLabel position="666,994" size="160,40" text="Search" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <eLabel position="842,1003" size="22,22" backgroundColor="#3E7BFA" cornerRadius="11" zPosition="4" />
        <eLabel position="876,994" size="200,40" text="Next page" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <widget name="hint" position="1090,994" size="760,40" font="Regular;24" halign="right" valign="center" foregroundColor="#C9D0DA" backgroundColor="{panel}" transparent="1" zPosition="4" />
    """.format(bg=BG, gold=GOLD, muted=MUTED, white=WHITE, panel=PANEL, card=CARD, edge=EDGE)]
    for i in range(CAT_ROWS):
        y = _CAT_Y0 + i * _CAT_STEP
        parts.append("""
        <widget name="cBg{i}" position="54,{y}" size="332,{h}" backgroundColor="{panel}" cornerRadius="10" zPosition="3" />
        <widget name="cBar{i}" position="54,{y}" size="5,{h}" backgroundColor="{gold}" zPosition="5" />
        <widget name="cText{i}" position="72,{y}" size="304,{h}" font="Regular;25" halign="left" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        """.format(i=i, y=y, h=_CAT_H, panel=PANEL, gold=GOLD, text=TEXT))
    for i in range(P_VIEW):
        x, y = _PX[i % P_COLS], _PY[i // P_COLS]
        parts.append("""
        <widget name="pEdge{i}" position="{ex},{ey}" size="{ew},{eh}" backgroundColor="{gold}" cornerRadius="10" zPosition="2" />
        <widget name="pBack{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{card}" zPosition="3" />
        <widget name="pImg{i}" position="{x},{y}" size="{w},{h}" alphatest="blend" scale="1" zPosition="4" />
        <widget name="pRate{i}" position="{rx},{ry}" size="78,30" font="Bold;19" halign="center" valign="center" foregroundColor="{gold}" backgroundColor="#200C1017" cornerRadius="6" zPosition="5" />
        <widget name="pMark{i}" position="{x},{my}" size="{w},28" font="Regular;18" halign="center" valign="center" foregroundColor="{white}" backgroundColor="#301E7A4A" zPosition="5" />
        <widget name="pName{i}" halign="left" position="{x},{ty}" size="{w},32" font="Regular;22" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="3" />
        <widget name="pYear{i}" position="{x},{yy}" size="{w},26" font="Regular;18" foregroundColor="#A3ADBB" backgroundColor="{bg}" transparent="1" zPosition="3" />
        """.format(i=i, x=x, y=y, w=PW, h=PH, ex=x - 4, ey=y - 4, ew=PW + 8, eh=PH + 8,
                   rx=x + 8, ry=y + 8, my=y + PH - 28, ty=y + PH + 6, yy=y + PH + 36,
                   gold=GOLD, card=CARD, white=WHITE, bg=BG))
    parts.append("""
        <widget name="kTitle" position="80,160" size="1380,66" halign="left" font="Bold;50" foregroundColor="{gold}" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kMeta" position="80,232" size="1380,36" halign="left" font="Regular;26" foregroundColor="#E6EAF0" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kState" position="80,272" size="1380,32" halign="left" font="Regular;24" foregroundColor="#30C48D" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kPlot" position="80,314" size="1060,250" font="Regular;26" foregroundColor="#E6EAF0" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kCount" position="1480,170" size="400,34" halign="right" font="Regular;24" foregroundColor="#C9D0DA" backgroundColor="{bg}" transparent="1" zPosition="5" />
    """.format(gold=GOLD, bg=BG))
    for s_ in range(K_SLOTS):
        mid = s_ == K_MID
        w, h, y = (KCW, KCH, _KCY) if mid else (KW, KH, _KY)
        x = _KX[s_]
        parts.append("""
        <widget name="kEdge{i}" position="{ex},{ey}" size="{ew},{eh}" backgroundColor="{gold}" cornerRadius="10" zPosition="5" />
        <widget name="kBack{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{card}" zPosition="6" />
        <widget name="kImg{i}" position="{x},{y}" size="{w},{h}" alphatest="blend" scale="1" zPosition="7" />
        <widget name="kRate{i}" position="{rx},{ry}" size="78,30" font="Bold;19" halign="center" valign="center" foregroundColor="{gold}" backgroundColor="#200C1017" cornerRadius="6" zPosition="8" />
        """.format(i=s_, x=x, y=y, w=w, h=h, ex=x - 5, ey=y - 5, ew=w + 10, eh=h + 10,
                   rx=x + 8, ry=y + 8, gold=GOLD, card=CARD))
    parts.append("</screen>")
    return "".join(parts)


class NovaPlaySite(Screen):
    skin = scale_skin_xml(_skin())

    def __init__(self, session, site, title=None, items=None, loader=None, m_type="movie", library=None):
        Screen.__init__(self, session)
        self._site = site
        self._m_type = m_type
        self._fixed_title = title
        self._loader = loader
        self._init_items = items
        self._library = library          # "favorites" | "history" | None
        self._zone = "cats"
        # category levels: [{"title", "items", "sel"}]; sub-folders push a level
        self._levels = []
        self._posters = []
        self._pi = 0
        self._cat_name = ""
        self._cat_url = None
        self._next_url = None
        self._page = 1
        self._history = []           # [(url, page)] of loaded site pages
        self._loading = False
        self._token = 0
        self._info_token = 0
        self._info_item = None

        self["logo"] = Pixmap()
        self["title"] = Label(title or (_site_label(site) if site else ""))
        self["subtitle"] = Label("")
        self["clock_time"] = Label("")
        self["clock_date"] = Label("")
        self["catEdge"] = Label("")
        self["catHead"] = Label("CATEGORIES")
        self["catPage"] = Label("")
        self["gridTitle"] = Label("")
        self["gridCount"] = Label("")
        self["gridMsg"] = Label("")
        self["gridMore"] = Label("")
        self["infoBack"] = Label("")
        self["infoImg"] = Pixmap()
        for n in ("infoTitle", "infoMeta", "infoState", "infoPlot"):
            self[n] = Label("")
        self["key_green"] = Label("")
        self["kBackdrop"] = Pixmap()
        self["catPanel"] = Label("")
        self["infoPanel"] = Label("")
        for n in ("kTitle", "kMeta", "kState", "kPlot", "kCount"):
            self[n] = Label("")
        for i in range(K_SLOTS):
            for n in ("kEdge", "kBack", "kRate"):
                self["%s%d" % (n, i)] = Label("")
            self["kImg%d" % i] = Pixmap()
        # [carousel] same setting as the classic home's Poster Layout
        self._layout = "carousel" if _get_config("layout_style", "grid") == "carousel" else "grid"
        self._bd_token = 0
        self._bd_path = ""
        self["hint"] = Label("")
        for i in range(CAT_ROWS):
            self["cBg%d" % i] = Label("")
            self["cBar%d" % i] = Label("")
            self["cText%d" % i] = Label("")
        for i in range(P_VIEW):
            for n in ("pEdge", "pBack", "pRate", "pMark", "pName", "pYear"):
                self["%s%d" % (n, i)] = Label("")
            self["pImg%d" % i] = Pixmap()

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions"], {
                "menu": self._menu,
                "ok": self._ok,
                "cancel": self._exit,
                "up": lambda: self._move(0, -1),
                "down": lambda: self._move(0, 1),
                "left": lambda: self._move(-1, 0),
                "right": lambda: self._move(1, 0),
                "red": self._prevPage,
                "green": self._toggleLayout,
                "yellow": self._search,
                "blue": self._nextPage,
            }, -1)

        self._clock = eTimer()
        self._clock.callback.append(self._tick)
        self._art = eTimer()
        self._art.callback.append(self._artPoll)
        self._art_tries = 0
        self._infoTimer = eTimer()
        self._infoTimer.callback.append(self._loadInfo)
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._stop)

    # ─── lifecycle ──────────────────────────────────────────────────────

    def _start(self):
        self._applyLayout()
        self._tick()
        self._clock.start(10000, False)
        if self._site:
            path = resolve_icon_path({"_action": "site_" + self._site})
            if path and self["logo"].instance:
                self["logo"].instance.setPixmapFromFile(path)
        else:
            path = os.path.join(PLUGIN_PATH, "images", "plugin.png")
            if self["logo"].instance:
                self["logo"].instance.setPixmapFromFile(path)
        if self._loader is not None or self._fixed_title:
            self._levels = [{"title": self._fixed_title or "Results",
                             "items": [{"title": self._fixed_title or "Results", "type": "fixed"}], "sel": 0}]
            self["catHead"].setText("LIST")
            self._paintCats()
            if self._loader is not None:
                self._runLoader()
            else:
                self._cat_name = self._fixed_title or ""
                self["gridTitle"].setText(_single_line_text(self._cat_name, width=34, fallback=""))
                self._focus_after = True
                self.setItems(self._init_items or [])
            return
        self._loadCategories()

    def _stop(self):
        for t in (self._clock, self._art, self._infoTimer):
            try:
                t.stop()
            except Exception:
                pass
        self._token += 1

    def _tick(self):
        t = time.localtime()
        self["clock_time"].setText("{}:{:02d}".format(t.tm_hour % 12 or 12, t.tm_min))
        self["clock_date"].setText(time.strftime("%A %d %B", t))

    def _status(self, text):
        self["subtitle"].setText(text)

    # ─── helpers ────────────────────────────────────────────────────────

    def _color(self, name, bg=None, fg=None):
        inst = self[name].instance
        if inst is None or parseColor is None:
            return
        if bg:
            inst.setBackgroundColor(parseColor(bg))
        if fg:
            inst.setForegroundColor(parseColor(fg))
        inst.invalidate()

    def _ex(self):
        return get_extractor(self._site)

    def _filterAdult(self, items):
        if _get_config("show_adult", "false") == "true":
            return items
        return [i for i in items if not any(w in (i.get("title", "") + i.get("category_name", "")).lower()
                                             for w in _ADULT)]

    # ─── [carousel] layout switching ───────────────────────────────────

    _CAT_W = ["catEdge", "catPanel", "catHead", "catPage"]
    _INFO_W = ["infoPanel", "infoBack", "infoImg", "infoTitle", "infoMeta", "infoState", "infoPlot"]
    _GRID_W = ["gridTitle", "gridCount", "gridMore"]
    _CAR_W = ["kTitle", "kMeta", "kState", "kPlot", "kCount"]

    def _vis(self, names, on):
        for n in names:
            try:
                self[n].show() if on else self[n].hide()
            except Exception:
                pass

    def _carousel(self):
        return self._layout == "carousel"

    def _applyLayout(self):
        """Show / hide the widget groups for the current layout and zone."""
        car = self._carousel()
        browsing = car and self._zone == "posters" and bool(self._posters)
        self._vis(self._CAT_W, not browsing)
        if browsing:
            for i in range(CAT_ROWS):
                self._vis(["cBg%d" % i, "cBar%d" % i, "cText%d" % i], False)
        self._vis(self._INFO_W, not car)
        if not car:
            # infoImg is shown by _paintBackdrop only when it has a picture
            if not getattr(self, "_info_img_ok", False):
                self["infoImg"].hide()
        self._vis(self._GRID_W, not car)
        if car:
            for i in range(P_VIEW):
                self._vis(["%s%d" % (n, i) for n in ("pEdge", "pBack", "pImg", "pRate", "pMark", "pName", "pYear")], False)
        # carousel + category list open: a "peek" -- the section name, the
        # counter and the poster strip stay in view beside the panel (was a
        # large empty hint card in the middle of the screen)
        peek = car and self._zone == "cats" and bool(self._posters)
        self._vis(self._CAR_W, browsing)
        if peek:
            self["kCount"].show()
            self["gridTitle"].setText(_single_line_text(self._cat_name, width=34, fallback=""))
            self["gridTitle"].show()
        if not (browsing or peek):
            for i in range(K_SLOTS):
                self._vis(["kEdge%d" % i, "kBack%d" % i, "kImg%d" % i, "kRate%d" % i], False)
        if not car or not self._bd_path:
            self["kBackdrop"].hide()
        if car and self._posters:
            self["gridMsg"].hide()
        self["key_green"].setText("Grid view" if car else "Carousel view")

    def _toggleLayout(self):
        self._layout = "grid" if self._carousel() else "carousel"
        try:
            from plugin_state import _set_config
            _set_config("layout_style", self._layout)
        except Exception:
            pass
        if self._carousel() and self._posters:
            self._zone = "posters"
        self._info_item = None
        self._paintCats()
        self._paintPosters()
        self._applyLayout()
        self._paintHint()

    # ─── categories ─────────────────────────────────────────────────────

    def _loadCategories(self):
        self._status("Loading sections…")
        self._showMsg("Loading…")
        tok = self._token

        def _worker():
            try:
                ex = self._ex()
                gc = getattr(ex, "get_categories", None)
                if not gc:
                    cats = []
                elif self._site in ("egydead", "egydead_coupons"):
                    cats = [_site_search_item(self._site)]
                    for it in gc("movie") or []:
                        d = dict(it); d["_m_type"] = "movie"; cats.append(d)
                    for it in gc("series") or []:
                        d = dict(it); d["_m_type"] = "series"; cats.append(d)
                else:
                    cats = [_site_search_item(self._site)] + list(gc() or [])
                plugin_health.record(self._site, "ok")
                err = None
            except Exception as e:
                cats, err = [], str(e)
                plugin_health.record(self._site, "blocked" if get_curl_failed_needs_proxy() else "down", err)
            callInMainThread(self._onCategories, tok, cats, err)
        threading.Thread(target=_worker, daemon=True).start()

    def _onCategories(self, tok, cats, err):
        if tok != self._token:
            return
        cats = self._filterAdult(cats)
        if not cats or len(cats) <= 1 and err:
            self._levels = [{"title": "", "items": [{"title": "Could not load the sections", "type": "error"}], "sel": 0}]
            self._status("Could not load the sections" + (" — try the proxy (Settings)" if err else ""))
            self._showMsg("This site did not answer.\nTry again later, or enable the proxy for it.")
        else:
            sel = self._firstSelectable(cats, 0, 1)
            self._levels = [{"title": "", "items": cats, "sel": sel}]
            self._status("{} sections".format(len([c for c in cats if self._selectable(c)])))
            self._showMsg("Choose a section on the left")
            # open the first real category right away so posters show
            first = next((i for i, c in enumerate(cats)
                          if c.get("type") == "category" and c.get("url") and c.get("_action") != "search_site"), None)
            if first is not None:
                self._levels[0]["sel"] = first
                self._openCategory(cats[first], focus=self._carousel())
        self._paintCats()
        self._paintHint()

    @staticmethod
    def _selectable(c):
        return c.get("type") not in ("separator", "error") and (c.get("url") or c.get("_action") or c.get("type") == "fixed")

    def _firstSelectable(self, items, start, step):
        i = start
        while 0 <= i < len(items):
            if self._selectable(items[i]):
                return i
            i += step
        return max(0, min(start, len(items) - 1))

    def _paintCats(self):
        lvl = self._levels[-1] if self._levels else {"items": [], "sel": 0}
        items, sel = lvl["items"], lvl["sel"]
        n = len(items)
        top = max(0, min(sel - CAT_ROWS // 2, n - CAT_ROWS)) if n > CAT_ROWS else 0
        side = self._zone == "cats"
        self._color("catEdge", bg=GOLD if side else EDGE)
        for i in range(CAT_ROWS):
            g = top + i
            if g >= n:
                self["cBg%d" % i].hide(); self["cBar%d" % i].hide(); self["cText%d" % i].hide()
                continue
            c = items[g]
            self["cText%d" % i].show()
            self["cText%d" % i].setText(_single_line_text(c.get("title", ""), width=24, fallback="—"))
            if c.get("type") == "separator":
                self["cBg%d" % i].hide(); self["cBar%d" % i].hide()
                self._color("cText%d" % i, fg=GOLD)
                continue
            on = g == sel
            self["cBg%d" % i].show()
            self._color("cBg%d" % i, bg=ROW_ON if on else PANEL)
            self["cBar%d" % i].show() if (on and side) else self["cBar%d" % i].hide()
            self._color("cText%d" % i, fg=WHITE if on else (MUTED if c.get("type") == "error" else TEXT))
        pages = max(1, (n + CAT_ROWS - 1) // CAT_ROWS)
        self["catPage"].setText("{} / {}".format(sel + 1, n) if pages > 1 else "")
        if len(self._levels) > 1:
            self["catHead"].setText(_single_line_text(self._levels[-1]["title"].upper(), width=24, fallback="FOLDER"))
        elif not self._fixed_title and self._loader is None:
            self["catHead"].setText("CATEGORIES")
        self._applyLayout()

    def _openCategory(self, c, focus=True):
        if c.get("_action") == "search_site":
            self._search()
            return
        if c.get("type") in ("separator", "error", "fixed"):
            return
        url = c.get("url")
        if not url:
            return
        self._cat_name = c.get("title", "")
        self._cat_url = url
        self._m_type = c.get("_m_type", self._m_type)
        self._page = 1
        self._history = [(url, 1)]
        self._next_url = None
        self._focus_after = focus
        self._fetch(url)

    def _fetch(self, url):
        self._loading = True
        self._token += 1
        tok = self._token
        self["gridTitle"].setText(_single_line_text(self._cat_name, width=34, fallback=""))
        self._status("Loading {}…".format(self._cat_name))
        page = self._page

        def _worker():
            try:
                gi = getattr(self._ex(), "get_category_items", None)
                items = (gi(url, page=page) if self._site in _PAGED_SITES else gi(url)) if gi else []
                plugin_health.record(self._site, "ok")
                err = None
            except Exception as e:
                items, err = [], str(e)
                plugin_health.record(self._site, "down", err)
            callInMainThread(self._onItems, tok, items or [], err)
        threading.Thread(target=_worker, daemon=True).start()

    def _onItems(self, tok, items, err):
        if tok != self._token:
            return
        self._loading = False
        nxt = next((i for i in items if i.get("_action") == "category" and i.get("url")), None)
        self._next_url = nxt["url"] if nxt else None
        items = self._filterAdult(_dedupe_items(items))
        content = [i for i in items if i.get("type") in _CONTENT]
        if content:
            self._setPosters(content)
            if getattr(self, "_focus_after", True):
                self._zone = "posters"
            self._status(u"Page {}  ·  {}".format(self._page, self._cat_name))
        elif items and any(self._selectable(i) for i in items):
            # a folder of sub-categories: open it in the left panel
            self._levels.append({"title": self._cat_name, "items": items,
                                 "sel": self._firstSelectable(items, 0, 1)})
            self._zone = "cats"
            self._status("{}: choose a section".format(self._cat_name))
            self._showMsg("Choose a section on the left")
        else:
            self._status("Failed: {}".format(err[:60]) if err else "No titles here")
            self._showMsg("Nothing to show here" if not err else "This page did not load.\nPress OK to try again.")
        self._paintCats()
        self._paintPosters()
        self._paintHint()

    def _runLoader(self):
        self._loading = True
        self._token += 1
        tok = self._token
        self._showMsg("Loading…")
        self._status("Loading…")

        def _worker():
            try:
                items = self._loader() or []
                err = None
            except Exception as e:
                items, err = [], str(e)
            callInMainThread(self._onItems, tok, items, err)
        threading.Thread(target=_worker, daemon=True).start()
        self._cat_name = self._fixed_title or ""
        self["gridTitle"].setText(_single_line_text(self._cat_name, width=34, fallback=""))

    def setItems(self, items):
        """Fixed list given up front (favorites / history)."""
        self._token += 1
        self._onItems(self._token, list(items or []), None)

    # ─── posters ────────────────────────────────────────────────────────

    def _showMsg(self, text):
        self["gridMsg"].setText(text)
        self["gridMsg"].show()
        for i in range(P_VIEW):
            for n in ("pEdge", "pBack", "pImg", "pRate", "pMark", "pName", "pYear"):
                self["%s%d" % (n, i)].hide()
        self["gridCount"].setText("")
        self["gridMore"].setText("")
        self._posters = []
        self._clearInfo()
        self._applyLayout()

    def _setPosters(self, items):
        self._posters = items
        self._pi = 0 if not getattr(self, "_focus_end", False) else len(items) - 1
        self._focus_end = False
        self["gridMsg"].hide()

    def _paintPosters(self):
        n = len(self._posters)
        if not n:
            return
        if self._carousel():
            self._paintCarousel()
            return
        self["gridMsg"].hide()
        view = self._pi // P_VIEW
        views = (n + P_VIEW - 1) // P_VIEW
        base = view * P_VIEW
        self["gridCount"].setText("{} titles  ·  {}/{}".format(n, view + 1, views))
        more = []
        if view < views - 1 or self._next_url or self._site in _PAGED_SITES and self._cat_url:
            more.append("▼ more")
        if self._page > 1:
            more.append("page {}".format(self._page))
        self["gridMore"].setText("   ·   ".join(more))
        missing = False
        for i in range(P_VIEW):
            g = base + i
            names = ("pEdge", "pBack", "pImg", "pRate", "pMark", "pName", "pYear")
            if g >= n:
                for nm in names:
                    self["%s%d" % (nm, i)].hide()
                continue
            it = self._posters[g]
            sel = self._zone == "posters" and g == self._pi
            self["pEdge%d" % i].show() if sel else self["pEdge%d" % i].hide()
            self["pBack%d" % i].show()
            self["pName%d" % i].show()
            self["pName%d" % i].setText(_single_line_text(_strip_arabic_from_english_title(it.get("title", "")) or it.get("title", ""),
                                                          width=16, fallback="—"))
            self._color("pName%d" % i, fg=GOLD if sel else WHITE)
            _yr = str(it.get("year") or "")[:4]
            _kd = _KIND.get(it.get("type", ""), "")
            self["pYear%d" % i].setText("  ·  ".join(b for b in (_yr, _kd) if b))
            self["pYear%d" % i].show()
            # rating
            r = str(it.get("rating") or "").strip()
            try:
                rv = float(r)
            except Exception:
                rv = 0.0
            if rv > 0:
                self["pRate%d" % i].setText(u"★ " + r[:3])
                self["pRate%d" % i].show()
            else:
                self["pRate%d" % i].hide()
                if not it.get("rating"):
                    try:
                        fill_item_rating_async(it, on_done=self._repaintSoon)
                    except Exception:
                        pass
            # watched / resume band
            mark = ""
            url = it.get("url", "")
            try:
                from plugin_watched import is_watched
                if url and is_watched(url):
                    mark = u"✓ Watched"
            except Exception:
                pass
            if not mark:
                try:
                    pos = int(_get_saved_position(url) or 0)
                    if pos > 30:
                        m, s = divmod(pos, 60)
                        mark = u"Resume {}:{:02d}".format(m, s) if m < 60 else u"Resume {}h{:02d}".format(m // 60, m % 60)
                except Exception:
                    pass
            if mark:
                self["pMark%d" % i].setText(mark)
                self["pMark%d" % i].show()
            else:
                self["pMark%d" % i].hide()
            # image
            purl = it.get("poster") or ""
            if not purl and it.get("thumbnail"):
                self._tmdbPoster(it)
                purl = it["thumbnail"]
            path = plugin_imagecache.getCachedImage(purl, target_size=(PW, PH)) if purl else ""
            if purl and not path:
                plugin_imagecache.requestImageAsync(purl, target_size=(PW, PH))
                missing = True
            if path and mark == u"✓ Watched" and _get_config("watched_poster_bw", False):
                try:
                    _bw = plugin_imagecache.getBwVariant(purl, (PW, PH))
                    if _bw:
                        path = _bw
                except Exception:
                    pass
            path = path or placeholder_for_item(it)
            inst = self["pImg%d" % i].instance
            if path and inst is not None:
                inst.setPixmapFromFile(path)
                self["pImg%d" % i].show()
            else:
                self["pImg%d" % i].hide()
        if missing:
            self._art_tries = 0
            self._art.start(1000, False)
        self._scheduleInfo()
        self._applyLayout()

    # ─── [carousel] painting ────────────────────────────────────────────

    def _posterPath(self, it):
        """-> (path, still_loading) for a poster at the grid's cached size."""
        purl = it.get("poster") or ""
        if not purl and it.get("thumbnail"):
            self._tmdbPoster(it)
            purl = it["thumbnail"]
        path = plugin_imagecache.getCachedImage(purl, target_size=(PW, PH)) if purl else ""
        loading = False
        if purl and not path:
            plugin_imagecache.requestImageAsync(purl, target_size=(PW, PH))
            loading = True
        return (path or placeholder_for_item(it)), loading

    def _paintCarousel(self):
        n = len(self._posters)
        self["gridMsg"].hide()
        self._pi = max(0, min(self._pi, n - 1))
        more = u"  ·  page {}".format(self._page) if self._page > 1 else u""
        self["kCount"].setText(u"{} / {}{}".format(self._pi + 1, n, more))
        missing = False
        browsing = self._zone == "posters"
        for s_ in range(K_SLOTS):
            g = self._pi + (s_ - K_MID)
            names = ["kEdge%d" % s_, "kBack%d" % s_, "kImg%d" % s_, "kRate%d" % s_]
            # categories open: keep the strip in view right of the panel (slot 0
            # sits under it), without the gold focus frame
            if not (0 <= g < n) or (not browsing and _KX[s_] < 420):
                self._vis(names, False)
                continue
            it = self._posters[g]
            self["kBack%d" % s_].show()
            self["kEdge%d" % s_].show() if (browsing and s_ == K_MID) else self["kEdge%d" % s_].hide()
            path, loading = self._posterPath(it)
            missing = missing or loading
            inst = self["kImg%d" % s_].instance
            if path and inst is not None:
                inst.setPixmapFromFile(path)
                self["kImg%d" % s_].show()
            else:
                self["kImg%d" % s_].hide()
            r_ = str(it.get("rating") or "").strip()
            try:
                rv = float(r_)
            except Exception:
                rv = 0.0
            if rv > 0:
                self["kRate%d" % s_].setText(u"★ " + r_[:3])
                self["kRate%d" % s_].show()
            else:
                self["kRate%d" % s_].hide()
                if not it.get("rating"):
                    try:
                        fill_item_rating_async(it, on_done=self._repaintSoon)
                    except Exception:
                        pass
        if missing:
            self._art_tries = 0
            self._art.start(1000, False)
        self._scheduleInfo()
        self._applyLayout()

    def _loadCarouselBackdrop(self, it):
        """Full-screen darkened backdrop (same cache the classic carousel uses)."""
        url = it.get("fanart") or it.get("backdrop") or ""
        self._bd_token += 1
        tok = self._bd_token
        if not url:
            self._bd_path = ""
            self["kBackdrop"].hide()
            return
        size = (1920, 1080)
        path = plugin_imagecache.getCachedImage(url, target_size=size)
        if path:
            self._showCarouselBackdrop(tok, path)
            return

        def _worker():
            try:
                data = plugin_imagecache.downloadUrl(url, timeout=8)
                if not data:
                    return
                out = plugin_imagecache.resizeCover(data, size, darken=0.45)
                if out is None:
                    return
                cp = plugin_imagecache.buildCachePath(url, target_size=size)
                if plugin_imagecache.writeFileAtomic(cp, out):
                    callInMainThread(self._showCarouselBackdrop, tok, cp)
            except Exception as e:
                my_log("carousel backdrop: {}".format(e))
        threading.Thread(target=_worker, daemon=True).start()

    def _showCarouselBackdrop(self, tok, path):
        if tok != self._bd_token or not self._carousel():
            return
        inst = self["kBackdrop"].instance
        if inst is not None and path:
            inst.setPixmapFromFile(path)
            self._bd_path = path
            self["kBackdrop"].show()

    def _repaintSoon(self, *a):
        if self._posters:
            self._paintPosters()

    def _artPoll(self):
        self._art_tries += 1
        if self._carousel():
            window = self._posters[max(0, self._pi - K_MID):self._pi + K_MID + 1]
        else:
            view = self._pi // P_VIEW
            window = self._posters[view * P_VIEW:(view + 1) * P_VIEW]
        done = True
        for it in window:
            u = it.get("poster") or it.get("thumbnail") or ""
            if u and not plugin_imagecache.getCachedImage(u, target_size=(PW, PH)):
                done = False
                break
        if done or self._art_tries > 20:
            self._art.stop()
        self._paintPosters()

    def _tmdbPoster(self, it):
        if it.get("poster") or it.get("_tmdb_poster_tried") or not it.get("title"):
            return
        it["_tmdb_poster_tried"] = True

        def _worker():
            try:
                meta = _tmdb_search_metadata(it.get("title", ""), it.get("year", ""), it.get("type", "movie"))
                if meta and meta.get("poster"):
                    it["poster"] = meta["poster"]
                    callInMainThread(self._repaintSoon)
            except Exception as e:
                my_log("site tmdb poster: {}".format(e))
        threading.Thread(target=_worker, daemon=True).start()

    # ─── info panel ─────────────────────────────────────────────────────

    def _clearInfo(self):
        self._info_item = None
        for n in ("infoTitle", "infoMeta", "infoState", "infoPlot", "kTitle", "kMeta", "kState", "kPlot", "kCount"):
            self[n].setText("")
        self["infoImg"].hide()
        self._info_img_ok = False

    def _current(self):
        if self._zone == "posters" and 0 <= self._pi < len(self._posters):
            return self._posters[self._pi]
        if self._posters and 0 <= self._pi < len(self._posters):
            return self._posters[self._pi]
        return None

    def _scheduleInfo(self):
        it = self._current()
        if it is self._info_item:
            return
        self._info_item = it
        if not it:
            self._clearInfo()
            return
        title = _strip_arabic_from_english_title(it.get("title", "")) or it.get("title", "")
        self["infoTitle"].setText(_wrap_ui_text(title, width=24, max_lines=2, fallback="—"))
        self["kTitle"].setText(_single_line_text(title, width=44, fallback="—"))
        self._paintMeta(it, {})
        self["infoState"].setText(self._stateText(it))
        self["kState"].setText(self._stateText(it))
        _p = (it.get("plot") or "").strip()
        if len(_p) < 60 or _p == (self._cat_name or "").strip():
            _p = ""
        self["infoPlot"].setText(_wrap_ui_text(_p, width=38, max_lines=11, fallback=""))
        self["kPlot"].setText(_wrap_ui_text(_p, width=80, max_lines=7, fallback=""))
        self._paintBackdrop(it)
        if self._carousel():
            self._loadCarouselBackdrop(it)
        self._info_token += 1
        self._infoTimer.start(450, True)

    def _stateText(self, it):
        url = it.get("url", "")
        bits = []
        try:
            if url and _is_favorite(url):
                bits.append(u"★ In favorites")
        except Exception:
            pass
        try:
            pos = int(_get_saved_position(url) or 0)
            if pos > 30:
                bits.append(u"Resume at {}:{:02d}".format(pos // 60, pos % 60))
        except Exception:
            pass
        return "   ".join(bits)

    def _paintMeta(self, it, meta):
        bits = [_KIND.get(it.get("type", ""), "")]
        y = it.get("year") or meta.get("year")
        if y:
            bits.append(str(y)[:4])
        r = it.get("rating") or meta.get("rating")
        if r and str(r) not in ("N/A", "0", "0.0"):
            bits.append(u"★ " + str(r)[:3])
        if meta.get("genres"):
            bits.append(meta["genres"])
        self["infoMeta"].setText(_single_line_text("  ·  ".join(b for b in bits if b), width=40, fallback=""))
        self["kMeta"].setText(_single_line_text("  ·  ".join(b for b in bits if b), width=90, fallback=""))

    def _paintBackdrop(self, it):
        url = it.get("fanart") or it.get("backdrop") or ""
        if not url:
            self["infoImg"].hide()
            self._info_img_ok = False
            return
        path = plugin_imagecache.getCachedImage(url, target_size=(430, 242))
        if path and self["infoImg"].instance is not None:
            self["infoImg"].instance.setPixmapFromFile(path)
            self._info_img_ok = True
            if not self._carousel():
                self["infoImg"].show()
        else:
            self["infoImg"].hide()
            plugin_imagecache.requestImageAsyncPriority(url, target_size=(430, 242))
            tok = self._info_token

            def _later():
                if tok == self._info_token and self._info_item is it:
                    self._paintBackdrop(it)
            self._backdropRetry = eTimer()
            self._backdropRetry.callback.append(_later)
            self._backdropRetry.start(1500, True)

    def _loadInfo(self):
        it = self._info_item
        if not it:
            return
        tok = self._info_token

        def _worker():
            try:
                meta = _tmdb_search_metadata(it.get("title", ""), it.get("year", ""), it.get("type", "movie")) or {}
            except Exception:
                meta = {}
            callInMainThread(self._onInfo, tok, it, meta)
        threading.Thread(target=_worker, daemon=True).start()

    def _onInfo(self, tok, it, meta):
        if tok != self._info_token or it is not self._info_item:
            return
        self._paintMeta(it, meta)
        _own = (it.get("plot") or "").strip()
        if meta.get("plot") and (len(_own) < 60 or _own == (self._cat_name or "").strip()):
            self["infoPlot"].setText(_wrap_ui_text(meta["plot"], width=38, max_lines=11, fallback=""))
            self["kPlot"].setText(_wrap_ui_text(meta["plot"], width=80, max_lines=7, fallback=""))
        if meta.get("backdrop_url") and not (it.get("fanart") or it.get("backdrop")):
            it["fanart"] = meta["backdrop_url"]
            self._paintBackdrop(it)
            if self._carousel():
                self._loadCarouselBackdrop(it)

    # ─── navigation ─────────────────────────────────────────────────────

    def _paintHint(self):
        if self._carousel() and self._zone == "posters":
            self["hint"].setText("OK  Open    ◀ ▶  Titles    MENU  Options    EXIT  Sections")
            return
        if self._zone == "cats":
            self["hint"].setText("OK  Open section    ▶  Titles    EXIT  Back")
        elif self._library:
            self["hint"].setText("OK  Open    MENU  Options    EXIT  Back")
        else:
            self["hint"].setText("OK  Open    MENU  Options    ◀  Sections")

    def _move(self, dx, dy):
        if self._zone == "cats":
            if not self._levels:
                return
            lvl = self._levels[-1]
            if dy:
                items = lvl["items"]
                i = lvl["sel"] + dy
                while 0 <= i < len(items) and not self._selectable(items[i]):
                    i += dy
                if 0 <= i < len(items):
                    lvl["sel"] = i
                self._paintCats()
            elif dx > 0 and self._posters:
                self._zone = "posters"
                self._paintCats()
                self._paintPosters()
                self._paintHint()
            return
        n = len(self._posters)
        if not n:
            return
        if self._carousel():
            if dx < 0:
                if self._pi > 0:
                    self._pi -= 1
                elif self._page > 1 and len(self._history) > 1:
                    self._prevPage()
                    return
                else:
                    self._zone = "cats"
                    self._paintCats()
                    self._paintPosters()
                    self._paintHint()
                    return
            elif dx > 0:
                if self._pi + 1 >= n:
                    self._nextPage()
                    return
                self._pi += 1
            else:
                return
            self._paintPosters()
            return
        local = self._pi % P_VIEW
        col = local % P_COLS
        if dx < 0 and col == 0:
            self._zone = "cats"
            self._paintCats()
            self._paintPosters()
            self._paintHint()
            return
        if dx:
            t = self._pi + dx
            if t >= n:
                self._nextPage()
                return
            self._pi = max(0, t)
        elif dy > 0:
            t = self._pi + P_COLS
            if t < n:
                self._pi = t
            elif self._pi // P_VIEW < (n - 1) // P_VIEW:
                self._pi = n - 1
            else:
                self._nextPage()
                return
        elif dy < 0:
            t = self._pi - P_COLS
            if t >= 0:
                self._pi = t
            elif self._page > 1 and len(self._history) > 1:
                self._prevPage()
                return
        self._paintPosters()

    def _ok(self):
        if self._zone == "cats":
            if not self._levels:
                return
            lvl = self._levels[-1]
            if 0 <= lvl["sel"] < len(lvl["items"]):
                self._openCategory(lvl["items"][lvl["sel"]])
            return
        it = self._current()
        if it:
            self._openItem(it)

    def _exit(self):
        if self._zone == "posters":
            self._zone = "cats"
            self._paintCats()
            self._paintPosters()
            self._paintHint()
            return
        if len(self._levels) > 1:
            self._levels.pop()
            self._paintCats()
            return
        self.close()

    def _openItem(self, it):
        from plugin_screen_detail import NovaPlayDetail
        self.session.openWithCallback(self._afterDetail, NovaPlayDetail, item=it,
                                      site=it.get("_site", self._site),
                                      m_type=it.get("type", self._m_type))

    def _afterDetail(self, *a):
        self._info_item = None
        self._paintPosters()

    # ─── pages ──────────────────────────────────────────────────────────

    def _nextPage(self):
        if self._loading or not self._cat_url or self._loader is not None:
            return
        if self._site in _PAGED_SITES:
            url = self._cat_url
        else:
            url = self._next_url
        if not url:
            self._status("Last page")
            return
        self._page += 1
        self._history.append((url, self._page))
        self._fetch(url)

    def _prevPage(self):
        if self._loading or len(self._history) < 2:
            return
        self._history.pop()
        url, self._page = self._history[-1]
        self._focus_end = True
        self._fetch(url)

    # ─── MENU: remove from Favorites / History ──────────────────────────

    def _menu(self):
        it = self._current() if self._zone == "posters" or self._carousel() else None
        name = u"Favorites" if self._library == "favorites" else u"History"
        opts = []
        if it and it.get("url") and self._library != "favorites":
            fav = _is_favorite(it.get("url"))
            opts.append((u"Remove from Favorites" if fav else u"Add to Favorites", "fav"))
        if self._library and it and it.get("url"):
            opts.append((u"Remove \"{}\" from {}".format(
                _single_line_text(it.get("title", ""), width=40, fallback="this title"), name), "one"))
        if self._library and self._posters:
            opts.append((u"Clear all of {} ({} titles)".format(name, len(self._posters)), "all"))
        opts.append((u"Grid view" if self._carousel() else u"Carousel view", "layout"))
        if not opts:
            return

        def _chosen(ans):
            act = ans[1] if ans else None
            if act == "fav":
                self._toggleFavorite()
            elif act == "layout":
                self._toggleLayout()
            elif act == "one":
                from plugin_state import _remove_library_item
                _remove_library_item(self._library, it.get("url"))
                self._posters = [p for p in self._posters if p.get("url") != it.get("url")]
                self._afterRemove(u"Removed from {}".format(name))
            elif act == "all":
                def _yes(ok):
                    if ok:
                        from plugin_state import _clear_library
                        _clear_library(self._library)
                        self._posters = []
                        self._afterRemove(u"{} cleared".format(name))
                self.session.openWithCallback(_yes, MessageBox, u"Remove all {} titles from {}?".format(
                    len(self._posters), name), MessageBox.TYPE_YESNO, default=False)
        from novaplay_choice import NovaChoiceBox
        self.session.openWithCallback(_chosen, NovaChoiceBox,
                                      title=name if self._library else u"Options", list=opts)

    def _afterRemove(self, msg):
        self._status(msg)
        self._info_item = None
        if not self._posters:
            self._zone = "cats"
            self._showMsg(u"Nothing here yet")
        else:
            self._pi = min(self._pi, len(self._posters) - 1)
            self._paintPosters()
        self._paintHint()

    # ─── colour keys ────────────────────────────────────────────────────

    def _toggleFavorite(self):
        it = self._current()
        if not it or not it.get("url"):
            return
        entry = _entry_from_item(dict(it), it.get("_site", self._site), it.get("type", self._m_type))
        added = _toggle_favorite_entry(entry)
        self._status("Added to favorites" if added else "Removed from favorites")
        self["infoState"].setText(self._stateText(it))
        self["kState"].setText(self._stateText(it))

    def _search(self):
        from plugin_screen_search import NovaPlaySearch
        self.session.openWithCallback(self._onQuery, NovaPlaySearch,
                                      current_site=self._site or "", default_scope=self._site or "all",
                                      query=getattr(self, "_last_query", ""))

    def _onQuery(self, result=None):
        if not result:
            return
        query, scope = (result, "all") if isinstance(result, str) else (result[0], result[1] if len(result) > 1 else "all")
        if not query:
            return
        self._last_query = query
        scope = scope or "all"
        if not self._site or scope in ("all", "") or scope != self._site:
            # another site or everything: results in their own screen
            self.session.open(NovaPlaySite, None, title=u"Search: {}".format(query),
                              loader=lambda: search_items(query, scope))
            return
        self._cat_name = u"Search: {}".format(query)
        self._cat_url = None
        self._next_url = None
        self._page = 1
        self._history = []
        self._loader_once(lambda: search_items(query, self._site))

    def _loader_once(self, fn):
        self._loading = True
        self._token += 1
        tok = self._token
        self["gridTitle"].setText(_single_line_text(self._cat_name, width=34, fallback=""))
        self._status("Searching…")

        def _worker():
            try:
                items, err = fn() or [], None
            except Exception as e:
                items, err = [], str(e)
            callInMainThread(self._onItems, tok, items, err)
        threading.Thread(target=_worker, daemon=True).start()


def search_items(query, scope="all"):
    """Same search the classic home runs: one site or every search site."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(name):
        try:
            res = get_extractor(name).search(query) or []
            for it in res:
                it["_site"] = name
            plugin_health.record(name, "ok")
            return res
        except Exception as e:
            my_log("Search failed for site {}: {}".format(name, e))
            plugin_health.record(name, "down", str(e))
            return []

    from plugin_util import active_search_sites
    sites = active_search_sites() if scope in ("all", "", None) else [scope]
    out = []
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(sites)))) as ex:
        for f in as_completed([ex.submit(_one, s) for s in sites]):
            out.extend(f.result())
    return _rank_search_items(out, query)
