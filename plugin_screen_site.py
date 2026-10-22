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
import re
import threading
import time

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, ePoint, eSize, gFont

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
from plugin_gridlist import resolve_icon_path, scale_skin_xml, sc, anim_frames, anim_light
from plugin_util import (_site_label, _site_search_item, _single_line_text, _wrap_ui_text,
                         _dedupe_items, _rank_search_items, _strip_arabic_from_english_title)
from plugin_tmdb import _tmdb_search_metadata, fill_item_rating_async
from novaplay_thread import callInMainThread
from plugin_toast import TOAST_XML, toast_init, toast

GOLD = "#F2C230"
WHITE = "#FFFFFF"
TEXT = "#D6DCE5"
MUTED = "#A3ADBB"
CARD = "#161C26"
EDGE = "#262E3B"
ROW_ON = "#222B3A"
PANEL = "#121822"
BG = "#0C1017"

# [ratings] badge colour by score: green 7+, gold 5-7, grey below
def _rate_color(v):
    return "#30C48D" if v >= 7 else (GOLD if v >= 5 else MUTED)


SKELETON = "#1E2633"

# [quality] badge from the site's own label or the title: (text, bg, fg)
_Q_RE = re.compile(r"\b(4k|2160p|uhd|1080p|720p|480p|hdcam|cam|hdts|ts|web[- ]?dl|webrip|blu[- ]?ray|bdrip|hdrip|dvdrip|hdtv)\b", re.I)


def _quality_badge(it):
    raw = " ".join(str(it.get(k) or "") for k in ("quality", "title"))
    m = _Q_RE.search(raw)
    if not m:
        return None
    q = m.group(1).lower().replace(" ", "").replace("-", "")
    if q in ("4k", "2160p", "uhd"):
        return ("4K", GOLD, "#0C1017")
    if q in ("cam", "hdcam", "ts", "hdts"):
        return ("CAM", "#E5484D", "#FFFFFF")
    label = {"webdl": "WEB-DL", "webrip": "WEBRip", "bluray": "BluRay", "bdrip": "BDRip",
             "hdrip": "HDRip", "dvdrip": "DVDRip", "hdtv": "HDTV"}.get(q, q.upper().replace("P", "p"))
    return (label, "#3E7BFA" if q in ("1080p", "bluray", "webdl") else "#566070", "#FFFFFF")             # [loading] grey placeholder cards

CAT_ROWS = 10
_CAT_Y0, _CAT_STEP, _CAT_H = 212, 62, 56

P_COLS, P_ROWS = 5, 2
P_VIEW = P_COLS * P_ROWS
PW, PH = 178, 267
_PX = tuple(430 + c * (PW + 15) for c in range(P_COLS))
_PY = (222, 574)

# [theme] "Modern" screen style (Settings > Screen style): no info panel,
# 7 x 2 posters across the full width, the selected title's name / details
# in a strip under the grid, glow background, gold-tinted category row
M_COLS = 7
M_VIEW = M_COLS * P_ROWS
# [bigger] no info strip under the Modern grid any more (the posters carry
# the name, year, resume band): the posters use that height -- 186 x 279
MPW, MPH = 186, 279
_MPX = tuple(456 + c * (MPW + 16) for c in range(M_COLS))   # [band] room for ‹ ›
_MPY = (222, 582)
ROW_ON_M = "#353124"
# [theme] Modern: the selected poster grows 8 % with an ease-out "pop",
# the same animation as the home screen's poster grid
POP_ZOOM, POP_FRAMES, POP_MS = 0.08, 12, 15     # ~180 ms, small even steps
POP_STEPS, POP_STEP_MS = POP_FRAMES, POP_MS


def _modern_style():
    return _get_config("screen_style", "classic") == "modern"

# [carousel] 7 posters along the bottom, the middle one larger, over a
# full-screen darkened backdrop with the title / details / plot on top
K_SLOTS, K_MID = 7, 3
# [carousel] graduated like the home carousel: the middle poster is the
# biggest, each step out is smaller; all centred on one line (y = 725)
_K_SIZES = ((150, 225), (180, 270), (220, 330), (300, 450), (220, 330), (180, 270), (150, 225))
_K_GAP, _K_LINE = 30, 725


def _k_slots():
    total = sum(w for w, _h in _K_SIZES) + _K_GAP * (len(_K_SIZES) - 1)
    x, out = (1920 - total) // 2, []
    for w, h in _K_SIZES:
        out.append((x, _K_LINE - h // 2, w, h))
        x += w + _K_GAP
    return tuple(out)


_KG = _k_slots()
_KX = tuple(g[0] for g in _KG)
KW, KH = _KG[K_MID - 1][2], _KG[K_MID - 1][3]
KCW, KCH = _KG[K_MID][2], _KG[K_MID][3]
_KY, _KCY = _KG[K_MID - 1][1], _KG[K_MID][1]

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
        <widget name="gridBand" position="405,212" size="1000,692" backgroundColor="{panel}" cornerRadius="18" zPosition="1" />
        <widget name="gridLeft" position="406,520" size="22,64" font="Regular;44" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="6" />
        <widget name="gridRight" position="1382,520" size="22,64" font="Regular;44" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="6" />
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
        <widget name="cBg{i}" position="54,{y}" size="332,{h}" backgroundColor="{panel}" cornerRadius="10" zPosition="4" />
        <widget name="cBar{i}" position="54,{y}" size="5,{h}" backgroundColor="{gold}" zPosition="5" />
        <widget name="cText{i}" position="72,{y}" size="304,{h}" font="Regular;25" halign="left" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        """.format(i=i, y=y, ey=y - 2, eh=_CAT_H + 4, h=_CAT_H, panel=PANEL, gold=GOLD, text=TEXT))
    for i in range(P_VIEW):
        x, y = _PX[i % P_COLS], _PY[i // P_COLS]
        parts.append("""
        <widget name="pEdge{i}" position="{ex},{ey}" size="{ew},{eh}" backgroundColor="{gold}" cornerRadius="10" zPosition="2" />
        <widget name="pBack{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{card}" zPosition="3" />
        <widget name="pImg{i}" position="{x},{y}" size="{w},{h}" alphatest="blend" scale="1" zPosition="4" />
        <widget name="pRate{i}" position="{rx},{ry}" size="70,28" font="Bold;18" halign="center" valign="center" foregroundColor="{gold}" backgroundColor="#200C1017" cornerRadius="6" zPosition="5" />
        <widget name="pQual{i}" position="{qx},{qy}" size="84,24" font="Bold;15" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#3E7BFA" cornerRadius="6" zPosition="5" />
        <widget name="pYear{i}" position="{yx},{ry}" size="64,26" font="Bold;17" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#C0392B" cornerRadius="6" zPosition="5" />
        <widget name="pLabel{i}" position="{yx},{ly}" size="90,24" font="Regular;15" halign="center" valign="center" foregroundColor="#0C1017" backgroundColor="#F2C230" cornerRadius="6" zPosition="5" />
        <widget name="pMark{i}" position="{x},{my}" size="{w},28" font="Regular;18" halign="center" valign="center" foregroundColor="{white}" backgroundColor="#301E7A4A" zPosition="5" />
        <widget name="pName{i}" halign="center" position="{x},{ty}" size="{w},54" font="Regular;19" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="3" />
        """.format(i=i, x=x, y=y, w=PW, h=PH, ex=x - 4, ey=y - 4, ew=PW + 8, eh=PH + 8,
                   rx=x + PW - 78, ry=y + 8, qx=x + PW - 92, qy=y + 40, yx=x + 8, ly=y + 38,
                   my=y + PH - 28, ty=y + PH + 6,
                   gold=GOLD, card=CARD, white=WHITE, bg=BG))
    parts.append("""
        <widget name="kTitle" position="80,160" size="1380,66" halign="left" font="Bold;50" foregroundColor="{gold}" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kMeta" position="80,232" size="1380,36" halign="left" font="Regular;26" foregroundColor="#E6EAF0" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kState" position="80,272" size="1380,32" halign="left" font="Regular;24" foregroundColor="#30C48D" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kPlot" position="80,314" size="1060,170" font="Regular;26" foregroundColor="#E6EAF0" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kCount" position="1480,170" size="400,34" halign="right" font="Regular;24" foregroundColor="#C9D0DA" backgroundColor="{bg}" transparent="1" zPosition="5" />
        <widget name="kLeft" position="112,693" size="44,64" font="Regular;60" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="{bg}" transparent="1" zPosition="8" />
        <widget name="kRight" position="1764,693" size="44,64" font="Regular;60" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="{bg}" transparent="1" zPosition="8" />
    """.format(gold=GOLD, bg=BG))
    for s_ in range(K_SLOTS):
        x, y, w, h = _KG[s_]
        parts.append("""
        <widget name="kEdge{i}" position="{ex},{ey}" size="{ew},{eh}" backgroundColor="{gold}" cornerRadius="10" zPosition="5" />
        <widget name="kBack{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{card}" zPosition="6" />
        <widget name="kImg{i}" position="{x},{y}" size="{w},{h}" alphatest="blend" scale="1" zPosition="7" />
        <widget name="kRate{i}" position="{rx},{ry}" size="70,28" font="Bold;18" halign="center" valign="center" foregroundColor="{gold}" backgroundColor="#200C1017" cornerRadius="6" zPosition="8" />
        <widget name="kQual{i}" position="{qx},{qy}" size="84,24" font="Bold;15" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#3E7BFA" cornerRadius="6" zPosition="8" />
        <widget name="kYear{i}" position="{yx},{ry}" size="64,26" font="Bold;17" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#C0392B" cornerRadius="6" zPosition="8" />
        <widget name="kLabel{i}" position="{yx},{ly}" size="90,24" font="Regular;15" halign="center" valign="center" foregroundColor="#0C1017" backgroundColor="#F2C230" cornerRadius="6" zPosition="8" />
        <widget name="kMark{i}" position="{x},{my}" size="{w},28" font="Regular;18" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#301E7A4A" zPosition="8" />
        """.format(i=s_, x=x, y=y, w=w, h=h, ex=x - 5, ey=y - 5, ew=w + 10, eh=h + 10,
                   rx=x + w - 78, ry=y + 8, qx=x + w - 92, qy=y + 40, yx=x + 8, ly=y + 38, my=y + h - 28,
                   gold=GOLD, card=CARD))
    parts.append(TOAST_XML)
    parts.append("</screen>")
    return "".join(parts)


def _skin_modern():
    parts = ["""
    <screen name="NovaPlaySiteModern" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="{bg}">
        <eLabel position="0,0" size="1920,1080" backgroundColor="{bg}" zPosition="0" />
        <ePixmap position="0,0" size="1920,1080" pixmap="{p}/images/np_bg_modern.jpg" scale="1" zPosition="0" />
        <widget name="kBackdrop" position="0,0" size="1920,1080" alphatest="blend" scale="1" zPosition="1" />
        <widget name="logo" position="40,40" size="180,64" alphatest="blend" scale="1" zPosition="2" />
        <widget name="title" position="240,30" size="1000,54" halign="left" font="Bold;42" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="subtitle" position="240,88" size="1000,32" halign="left" font="Regular;22" foregroundColor="{gold}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="clock_time" position="1380,22" size="500,62" font="Bold;54" halign="right" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="clock_date" position="1280,90" size="600,30" font="Regular;22" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />

        <widget name="catEdge" position="40,150" size="350,810" backgroundColor="{edge}" cornerRadius="22" zPosition="1" />
        <widget name="catPanel" position="42,152" size="346,806" backgroundColor="{panel}" cornerRadius="20" zPosition="2" />
        <widget name="catHead" position="68,168" size="300,30" font="Bold;18" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <widget name="catPage" position="68,912" size="300,30" font="Regular;20" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />

        <widget name="gridTitle" position="430,150" size="620,58" halign="left" font="Bold;40" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="gridMore" position="1060,164" size="380,34" font="Regular;22" halign="right" foregroundColor="{gold}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="gridCount" position="1450,164" size="430,34" font="Regular;22" halign="right" foregroundColor="{muted}" backgroundColor="{bg}" transparent="1" zPosition="2" />
        <widget name="gridMsg" position="430,226" size="1450,602" font="Regular;28" halign="center" valign="center" foregroundColor="{muted}" backgroundColor="{card}" cornerRadius="18" zPosition="2" />
        <widget name="gridBand" position="420,210" size="1470,752" backgroundColor="{panel}" cornerRadius="20" zPosition="1" />
        <widget name="gridLeft" position="422,548" size="30,64" font="Regular;50" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="6" />
        <widget name="gridRight" position="1858,548" size="30,64" font="Regular;50" halign="center" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="6" />

        <widget name="infoPanel" position="0,0" size="2,2" font="Regular;10" backgroundColor="{bg}" transparent="1" zPosition="0" />
        <widget name="infoBar" position="0,0" size="2,2" font="Regular;10" backgroundColor="{bg}" transparent="1" zPosition="0" />
        <widget name="infoTitle" position="0,0" size="2,2" font="Regular;10" backgroundColor="{bg}" transparent="1" zPosition="0" />
        <widget name="infoMeta" position="0,0" size="2,2" font="Regular;10" backgroundColor="{bg}" transparent="1" zPosition="0" />
        <widget name="infoState" position="0,0" size="2,2" font="Regular;10" backgroundColor="{bg}" transparent="1" zPosition="0" />
        <widget name="infoBack" position="0,0" size="2,2" backgroundColor="{bg}" zPosition="0" />
        <widget name="infoImg" position="0,0" size="2,2" alphatest="blend" scale="1" zPosition="0" />
        <widget name="infoPlot" position="0,0" size="2,2" font="Regular;10" backgroundColor="{bg}" transparent="1" zPosition="0" />

        <eLabel position="40,980" size="1840,72" backgroundColor="{panel}" cornerRadius="18" zPosition="3" />
        <eLabel position="64,998" size="34,34" backgroundColor="#E5484D" cornerRadius="9" zPosition="4" />
        <eLabel position="112,994" size="230,42" text="Previous page" font="Regular;24" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <eLabel position="364,998" size="34,34" backgroundColor="#30A46C" cornerRadius="9" zPosition="4" />
        <widget name="key_green" position="412,994" size="230,42" font="Regular;24" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <eLabel position="664,998" size="34,34" backgroundColor="#F2C230" cornerRadius="9" zPosition="4" />
        <eLabel position="712,994" size="200,42" text="Search" font="Regular;24" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <eLabel position="934,998" size="34,34" backgroundColor="#3E7BFA" cornerRadius="9" zPosition="4" />
        <eLabel position="982,994" size="200,42" text="Next page" font="Regular;24" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="4" />
        <widget name="hint" position="1190,994" size="666,42" font="Regular;21" halign="right" valign="center" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="4" />
    """.format(bg=BG, gold=GOLD, muted=MUTED, white=WHITE, panel=PANEL, card=CARD, edge=EDGE, text=TEXT, p=PLUGIN_PATH)]
    for i in range(CAT_ROWS):
        y = _CAT_Y0 + i * _CAT_STEP
        parts.append("""
        <widget name="cBg{i}" position="54,{y}" size="322,{h}" backgroundColor="{panel}" cornerRadius="14" zPosition="4" />
        <widget name="cBar{i}" position="54,{by}" size="5,{bh}" backgroundColor="{gold}" cornerRadius="2" zPosition="5" />
        <widget name="cText{i}" position="74,{y}" size="290,{h}" font="Regular;24" halign="left" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        """.format(i=i, y=y, ey=y - 2, eh=_CAT_H + 4, by=y + 10, bh=_CAT_H - 20, h=_CAT_H, panel=PANEL, gold=GOLD, text=TEXT))
    for i in range(M_VIEW):
        x, y = _MPX[i % M_COLS], _MPY[i // M_COLS]
        parts.append("""
        <widget name="pEdge{i}" position="{ex},{ey}" size="{ew},{eh}" backgroundColor="{gold}" cornerRadius="14" zPosition="2" />
        <widget name="pBack{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{card}" cornerRadius="10" zPosition="3" />
        <widget name="pImg{i}" position="{x},{y}" size="{w},{h}" alphatest="blend" scale="1" zPosition="4" />
        <widget name="pRate{i}" position="{rx},{ry}" size="70,28" font="Bold;18" halign="center" valign="center" foregroundColor="{gold}" backgroundColor="#300C1017" cornerRadius="8" zPosition="5" />
        <widget name="pQual{i}" position="{qx},{qy}" size="84,24" font="Bold;15" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#3E7BFA" cornerRadius="8" zPosition="5" />
        <widget name="pYear{i}" position="{yx},{ry}" size="64,26" font="Bold;17" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#C0392B" cornerRadius="8" zPosition="5" />
        <widget name="pLabel{i}" position="{yx},{ly}" size="90,24" font="Regular;15" halign="center" valign="center" foregroundColor="#0C1017" backgroundColor="#F2C230" cornerRadius="8" zPosition="5" />
        <widget name="pMark{i}" position="{x},{my}" size="{w},28" font="Regular;18" halign="center" valign="center" foregroundColor="{white}" backgroundColor="#301E7A4A" zPosition="5" />
        <widget name="pName{i}" halign="center" position="{nx},{ty}" size="{nw},54" font="Regular;19" foregroundColor="{white}" backgroundColor="{bg}" transparent="1" zPosition="3" />
        """.format(i=i, x=x, y=y, w=MPW, h=MPH, ex=x - 6, ey=y - 6, ew=MPW + 12, eh=MPH + 12,
                   rx=x + MPW - 78, ry=y + 8, qx=x + MPW - 92, qy=y + 40, yx=x + 8, ly=y + 38,
                   my=y + MPH - 28, ty=y + MPH + 8,
                   nx=x - 8, nw=MPW + 16, gold=GOLD, card=CARD, white=WHITE, bg=BG, muted=MUTED))
    # carousel widgets are shared with the classic skin
    classic = _skin()
    car = classic[classic.index('<widget name="kTitle"'):classic.rindex("</screen>")]
    parts.append("        " + car)
    parts.append("</screen>")
    return "".join(parts)


_SKIN_CLASSIC = scale_skin_xml(_skin())
_SKIN_MODERN = scale_skin_xml(_skin_modern())


class NovaPlaySite(Screen):
    skin = _SKIN_CLASSIC

    def __init__(self, session, site, title=None, items=None, loader=None, m_type="movie", library=None):
        # [theme] pick the skin + grid geometry before the skin is applied
        self._modern = _modern_style()
        self._cols = M_COLS if self._modern else P_COLS
        self._view = M_VIEW if self._modern else P_VIEW
        self.skin = _SKIN_MODERN if self._modern else _SKIN_CLASSIC
        self.skinName = "NovaPlaySiteModern" if self._modern else "NovaPlaySite"
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
        self["gridBand"] = Label("")
        for _n, _t in (("gridLeft", u"\u2039"), ("gridRight", u"\u203a"), ("kLeft", u"\u2039"), ("kRight", u"\u203a")):
            self[_n] = Label(_t)
        self["infoBack"] = Label("")
        self["infoImg"] = Pixmap()
        for n in ("infoTitle", "infoMeta", "infoState", "infoPlot"):
            self[n] = Label("")
        self["key_green"] = Label("")
        self["kBackdrop"] = Pixmap()
        self["catPanel"] = Label("")
        self["infoPanel"] = Label("")
        if self._modern:
            self["infoBar"] = Label("")
        for n in ("kTitle", "kMeta", "kState", "kPlot", "kCount"):
            self[n] = Label("")
        for i in range(K_SLOTS):
            for n in ("kEdge", "kBack", "kRate", "kQual", "kYear", "kLabel", "kMark"):
                self["%s%d" % (n, i)] = Label("")
            self["kImg%d" % i] = Pixmap()
        # [carousel] same setting as the classic home's Poster Layout
        self._layout = "carousel" if _get_config("layout_style", "grid") == "carousel" else "grid"
        self._bd_token = 0
        self._bd_path = ""
        self["hint"] = Label("")
        toast_init(self)
        for i in range(CAT_ROWS):
            self["cBg%d" % i] = Label("")
            self["cBar%d" % i] = Label("")
            self["cText%d" % i] = Label("")
        for i in range(self._view):
            for n in ("pEdge", "pBack", "pRate", "pQual", "pLabel", "pMark", "pName", "pYear"):
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
        self._pop = eTimer()
        self._pop.callback.append(self._popTick)
        self._catPop = eTimer()
        self._catPop.callback.append(self._catPopTick)
        self._cat_key = None
        self._cat_slot = -1
        self._cat_frame = 0
        self._carPop = eTimer()
        self._carPop.callback.append(self._carPopTick)
        self._car_frame = 0
        self._pop_key = None         # (view, slot) of the zoomed poster
        self._pop_slot = -1
        self._pop_frame = 0
        self._pop_p = 1.0
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._stop)

    # ─── lifecycle ──────────────────────────────────────────────────────

    # ─── [open-anim] the category panel slides in from the left on open,
    # the first posters glide in from the right ──────────────────────────

    def _openTick(self):
        import math
        self._open_f += 1
        t = min(1.0, self._open_f / float(anim_frames()))
        self._open_dx = -420.0 * (1.0 - math.sin(t * math.pi / 2))
        st = getattr(self, "_frame_st", {}).get("catEdge")
        fp = 0.0
        if st and st["on"]:
            fp = math.sin(min(1.0, st["f"] / float(anim_frames())) * math.pi / 2)
        self._framePlace("catEdge", (40, 150, 350 if self._modern else 360, 810), fp)
        dx = self._open_dx
        self._mr("catHead", 68 + dx, 168 if self._modern else 170)
        self._mr("catPage", 68 + dx, 912 if self._modern else 906)
        for i in range(CAT_ROWS):
            cp = 0.0
            if i == self._cat_slot:
                cp = math.sin(min(1.0, self._cat_frame / float(anim_frames())) * math.pi / 2)
            self._placeCat(i, cp)
        if t >= 1.0:
            self._open_dx = 0.0
            self._openT.stop()

    def _start(self):
        self._first_posters = True
        self._open_f = 0
        self._open_dx = -420.0
        self._openT = eTimer()
        self._openT.callback.append(self._openTick)
        try:
            self._openTick()
            self._openT.start(POP_MS, False)
        except Exception:
            self._open_dx = 0.0
        # nothing of the category rows shows until the categories arrive
        # (the skin draws every row's gold bar / box by default)
        for i in range(CAT_ROWS):
            self._vis(["cBg%d" % i, "cBar%d" % i, "cText%d" % i], False)
        # same for the poster slots (gold focus frames) until posters load
        for i in range(self._view):
            self._vis(["%s%d" % (n, i) for n in ("pEdge", "pBack", "pImg", "pRate", "pQual", "pLabel", "pMark", "pName", "pYear")], False)
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
        for t in (self._clock, self._art, self._infoTimer, self._pop, self._carPop, self._catPop,
                  getattr(self, "_frameT", None), getattr(self, "_openT", None)):
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
    _INFO_W = ["infoPanel", "infoBar", "infoBack", "infoImg", "infoTitle", "infoMeta", "infoState", "infoPlot"]
    _GRID_W = ["gridTitle", "gridCount", "gridMore", "gridBand"]
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
            if self._modern:
                # [bigger] no info strip at all on Modern: the posters say it
                self._vis(self._INFO_W, False)
        self._vis(self._GRID_W, not car)
        if car or not self._posters:
            self._vis(["gridLeft", "gridRight"], False)
        if not (car and self._zone == "posters" and self._posters):
            self._vis(["kLeft", "kRight"], False)
        if car:
            for i in range(self._view):
                self._vis(["%s%d" % (n, i) for n in ("pEdge", "pBack", "pImg", "pRate", "pQual", "pLabel", "pMark", "pName", "pYear")], False)
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
                self._vis(["%s%d" % (_n, i) for _n in ("kEdge", "kBack", "kImg", "kRate", "kQual", "kYear", "kLabel", "kMark")], False)
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
        self._showSkeleton()
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
        self._framePop("catEdge", (40, 150, 350 if self._modern else 360, 810), side)
        self._setCatDirection(items)
        for i in range(CAT_ROWS):
            g = top + i
            if g >= n:
                self["cBg%d" % i].hide(); self["cBar%d" % i].hide(); self["cText%d" % i].hide()
                continue
            c = items[g]
            self["cText%d" % i].show()
            _t = c.get("title", "")
            # Arabic letters are narrower than Latin ones at the same size: the
            # 24-character cut (with '...') was shortening names that fit
            _ar = any(u"\u0600" <= ch <= u"\u06ff" for ch in _t)
            self["cText%d" % i].setText(_single_line_text(_t, width=(31 if _ar else 24), fallback="—"))
            if c.get("type") == "separator":
                self["cBg%d" % i].hide(); self["cBar%d" % i].hide()
                self._color("cText%d" % i, fg=GOLD)
                continue
            on = g == sel
            # only the selected row has a box (same colour as the panel
            # otherwise) -- so the growing row never slides under a neighbour
            self["cBg%d" % i].show() if on else self["cBg%d" % i].hide()
            self._color("cBg%d" % i, bg=(ROW_ON_M if self._modern else ROW_ON) if on else PANEL)
            self["cBar%d" % i].show() if (on and side) else self["cBar%d" % i].hide()
            self._color("cText%d" % i, fg=WHITE if on else (MUTED if c.get("type") == "error" else TEXT))
        self._catPopFocus(sel - top if (side and n and 0 <= sel - top < CAT_ROWS
                                          and items[sel].get("type") != "separator") else -1,
                          (len(self._levels), sel))
        pages = max(1, (n + CAT_ROWS - 1) // CAT_ROWS)
        self["catPage"].setText("{} / {}".format(sel + 1, n) if pages > 1 else "")
        if len(self._levels) > 1:
            self["catHead"].setText(_single_line_text(self._levels[-1]["title"].upper(), width=24, fallback="FOLDER"))
        elif not self._fixed_title and self._loader is None:
            self["catHead"].setText("CATEGORIES")
        self._applyLayout()

    def _setCatDirection(self, items):
        """[rtl] a list of mostly Arabic category names reads right-to-left:
        names right-aligned and the gold bar on the right; English lists
        stay left-to-right (decided per list, so a mixed list doesn't zigzag)"""
        names = [str(c.get("title", "")) for c in items if c.get("type") != "separator"]
        ar = sum(1 for t in names if any(u"\u0600" <= ch <= u"\u06ff" for ch in t))
        rtl = bool(names) and ar * 2 > len(names)
        if rtl == getattr(self, "_cat_rtl", None):
            return
        self._cat_rtl = rtl
        try:
            from enigma import eLabel
            align = eLabel.alignRight if rtl else eLabel.alignLeft
        except Exception:
            align = 2 if rtl else 0
        for i in range(CAT_ROWS):
            inst = self["cText%d" % i].instance
            if inst is not None:
                try:
                    inst.setHAlign(align)
                    inst.invalidate()
                except Exception:
                    pass
            self._placeCat(i, 1.0 if i == getattr(self, "_cat_slot", -1) else 0.0)

    def _openCategory(self, c, focus=True):
        self._slide_dir = 0          # a new category appears in place (no slide)
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
        self._showSkeleton()
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
            self._status(self._crumb())
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
        self._showSkeleton()
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
        if getattr(self, "_skeleton", False):
            self._skeleton = False
            for i in range(self._view):
                self._color("pBack%d" % i, bg=CARD)
            for s_ in range(K_SLOTS):
                self._color("kBack%d" % s_, bg=CARD)
                self["kBack%d" % s_].hide()
        self["gridMsg"].setText(text)
        self["gridMsg"].show()
        for i in range(self._view):
            for n in ("pEdge", "pBack", "pImg", "pRate", "pQual", "pLabel", "pMark", "pName", "pYear"):
                self["%s%d" % (n, i)].hide()
        self["gridCount"].setText("")
        self["gridMore"].setText("")
        self._vis(["gridLeft", "gridRight", "kLeft", "kRight"], False)
        self._posters = []
        self._clearInfo()
        self._applyLayout()

    def _showSkeleton(self):
        """[loading] grey poster-shaped cards where the posters will appear
        (instead of one big 'Loading…' box), so nothing jumps when they come"""
        self["gridMsg"].hide()
        self._posters = []
        self._clearInfo()
        self["gridCount"].setText("")
        self["gridMore"].setText("")
        self._vis(["gridLeft", "gridRight", "kLeft", "kRight"], False)
        car = self._carousel()
        for i in range(self._view):
            for n in ("pEdge", "pImg", "pRate", "pQual", "pLabel", "pMark", "pName", "pYear"):
                self["%s%d" % (n, i)].hide()
            if car:
                self["pBack%d" % i].hide()
            else:
                self._color("pBack%d" % i, bg=SKELETON)
                self["pBack%d" % i].show()
        for s_ in range(K_SLOTS):
            for n in ("kEdge", "kImg", "kRate", "kQual", "kYear", "kLabel", "kMark"):
                self["%s%d" % (n, s_)].hide()
            if car and _KX[s_] >= 420:
                self._color("kBack%d" % s_, bg=SKELETON)
                self["kBack%d" % s_].show()
            else:
                self["kBack%d" % s_].hide()
        self._skeleton = True
        self._applyLayout()

    def _setPosters(self, items):
        self._posters = items
        self._pi = 0 if not getattr(self, "_focus_end", False) else len(items) - 1
        self._focus_end = False
        self["gridMsg"].hide()

    def _crumb(self):
        """[breadcrumb] where you are: folders › category › page N"""
        parts = [l.get("title", "") for l in self._levels[1:]]
        if self._cat_name and (not parts or parts[-1] != self._cat_name):
            parts.append(self._cat_name)
        if self._page > 1 or self._hasPages():
            parts.append(u"page {}".format(self._page))
        return u"  \u203a  ".join(_single_line_text(p, width=30, fallback="") for p in parts if p)

    def _isWatched(self, it):
        try:
            from plugin_watched import is_watched
            return bool(it.get("url")) and is_watched(it.get("url"))
        except Exception:
            return False

    def _paintBadges(self, pre, i, it):
        """[badges] same corners as the Home posters, grid and carousel:
        top-left red year + the site's label chip (or Series / Episode),
        top-right ★ rating + quality, bottom the green Resume band (and
        ✓ Watched only when watched posters aren't black & white).
        -> the band text ('' = none)."""
        def _w(n):
            return "%s%s%d" % (pre, n, i)
        yr = str(it.get("year") or "")[:4]
        if yr.isdigit():
            self[_w("Year")].setText(yr)
            self[_w("Year")].show()
        else:
            self[_w("Year")].hide()
        lb = str(it.get("label") or "").strip()
        kind = {"series": "Series", "episode": "Episode", "season": "Season"}.get(it.get("type", ""), "")
        chip = lb or kind
        if chip:
            lc = str(it.get("label_color") or "").strip()
            if lb and re.match(r"^#?[0-9a-fA-F]{6}$", lc):
                self._color(_w("Label"), bg="#" + lc.lstrip("#"), fg="#FFFFFF")
            elif lb:
                self._color(_w("Label"), bg=GOLD, fg="#0C1017")
            else:
                self._color(_w("Label"), bg="#3A4252", fg="#FFFFFF")
            # room left on that row: the poster width minus the quality badge
            # on the right (when there is one)
            pw = _KG[i][2] if pre == "k" else (MPW if self._modern else PW)
            room = pw - 16 - (90 if _quality_badge(it) else 0)
            cw = max(40, min(room, 22 + 9 * len(chip)))
            self[_w("Label")].setText(_single_line_text(chip, width=max(4, (cw - 12) // 9), fallback=""))
            inst = self[_w("Label")].instance
            if inst is not None:
                try:
                    inst.resize(eSize(sc(cw), sc(24)))
                except Exception:
                    pass
            self[_w("Label")].show()
        else:
            self[_w("Label")].hide()
        r = str(it.get("rating") or "").strip()
        try:
            rv = float(r)
        except Exception:
            rv = 0.0
        if rv > 0:
            self[_w("Rate")].setText(u"★ " + r[:3])
            self._color(_w("Rate"), fg=_rate_color(rv))
            self[_w("Rate")].show()
        else:
            self[_w("Rate")].hide()
            if not it.get("rating"):
                try:
                    fill_item_rating_async(it, on_done=self._repaintSoon)
                except Exception:
                    pass
        qb = _quality_badge(it)
        if qb:
            self[_w("Qual")].setText(qb[0])
            self._color(_w("Qual"), bg=qb[1], fg=qb[2])
            self[_w("Qual")].show()
        else:
            self[_w("Qual")].hide()
        mark = ""
        url = it.get("url", "")
        try:
            from plugin_watched import is_watched
            if url and is_watched(url) and not _get_config("watched_poster_bw", False):
                mark = u"\u2713 Watched"
        except Exception:
            pass
        if not mark:
            try:
                pos = int(_get_saved_position(url) or 0)
                if pos > 30:
                    m, s_ = divmod(pos, 60)
                    mark = u"Resume {}:{:02d}".format(m, s_) if m < 60 else u"Resume {}h{:02d}".format(m // 60, m % 60)
            except Exception:
                pass
        if mark:
            self[_w("Mark")].setText(mark)
            self[_w("Mark")].show()
        else:
            self[_w("Mark")].hide()
        return mark

    def _hasPages(self):
        """the open category has more site pages (so 'page 1' is worth showing)"""
        return bool(self._next_url) or (self._site in _PAGED_SITES and bool(self._cat_url))

    def _paintPosters(self):
        n = len(self._posters)
        if not n:
            return
        if self._carousel():
            self._paintCarousel()
            return
        self["gridMsg"].hide()
        if getattr(self, "_skeleton", False):
            self._skeleton = False
            for i in range(self._view):
                self._color("pBack%d" % i, bg=CARD)
        view = self._pi // self._view
        views = (n + self._view - 1) // self._view
        base = view * self._view
        self["gridCount"].setText("{} titles  ·  {}/{}".format(n, view + 1, views))
        more = []                        # (the › arrow shows there is more)
        if self._page > 1 or self._hasPages():
            more.append("page {}".format(self._page))
        self["gridMore"].setText("   ·   ".join(more))
        # [band] ‹ › beside the grid when there is more that way
        _fwd = view < views - 1 or bool(self._next_url) or (self._site in _PAGED_SITES and bool(self._cat_url))
        self["gridRight"].show() if _fwd else self["gridRight"].hide()
        self["gridLeft"].show() if (view > 0 or self._page > 1) else self["gridLeft"].hide()
        missing = False
        for i in range(self._view):
            g = base + i
            names = ("pEdge", "pBack", "pImg", "pRate", "pQual", "pLabel", "pMark", "pName", "pYear")
            if g >= n:
                for nm in names:
                    self["%s%d" % (nm, i)].hide()
                continue
            it = self._posters[g]
            sel = self._zone == "posters" and g == self._pi
            self["pEdge%d" % i].show() if sel else self["pEdge%d" % i].hide()
            self["pBack%d" % i].show()
            self["pName%d" % i].show()
            # [badges] name only, up to 2 lines (the year is a badge now)
            self["pName%d" % i].setText(_wrap_ui_text(_strip_arabic_from_english_title(it.get("title", "")) or it.get("title", ""),
                                                      width=18 if self._modern else 16, max_lines=2, fallback="—"))
            self._color("pName%d" % i, fg=GOLD if sel else WHITE)
            mark = self._paintBadges("p", i, it)
            # image
            purl = it.get("poster") or ""
            if not purl and it.get("thumbnail"):
                self._tmdbPoster(it)
                purl = it["thumbnail"]
            _ts = (MPW, MPH) if self._modern else (PW, PH)
            path = plugin_imagecache.getCachedImage(purl, target_size=_ts) if purl else ""
            if purl and not path:
                plugin_imagecache.requestImageAsync(purl, target_size=_ts)
                missing = True
            if path and _get_config("watched_poster_bw", False) and self._isWatched(it):
                try:
                    _bw = plugin_imagecache.getBwVariant(purl, _ts)
                    if _bw:
                        path = _bw
                except Exception:
                    pass
            path = path or placeholder_for_item(it)
            inst = self["pImg%d" % i].instance
            if path and inst is not None:
                pix = self.__dict__.setdefault("_pix_path", {})
                if pix.get(("p", i)) != path:          # only a new picture is loaded
                    inst.setPixmapFromFile(path)
                    pix[("p", i)] = path
                self["pImg%d" % i].show()
            else:
                self["pImg%d" % i].hide()
        if missing:
            self._art_tries = 0
            self._art.start(1000, False)
        self._popFocus(view, n)
        self._scheduleInfo()
        self._applyLayout()

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
        x += getattr(self, "_open_dx", 0.0)
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

    # ─── [pop-anim] category rows: the selected one grows 6 % ───────────

    def _placeCat(self, i, p):
        """[pop-anim] like the posters: the row grows in width AND height
        (into the gap to its neighbours), its gold outline thickens and the
        text gets 3 pt bigger."""
        y = _CAT_Y0 + i * _CAT_STEP
        W, H = (322 if self._modern else 332), _CAT_H
        dw, dh = 20.0 * p, float(_CAT_STEP - _CAT_H) * p
        x0, y0 = 54 - dw / 2.0 + getattr(self, "_open_dx", 0.0), y - dh / 2.0
        w, h = W + dw, H + dh
        self._mr("cBg%d" % i, x0, y0, w, h)
        rtl = getattr(self, "_cat_rtl", False)
        bx = x0 + w - 5 if rtl else x0            # [rtl] gold bar on the reading side
        if self._modern:
            self._mr("cBar%d" % i, bx, y0 + 10 * h / H, 5, h - 20 * h / H)
            self._mr("cText%d" % i, x0 + (10 if rtl else 20), y0, w - 30, h)
            base = 24
        else:
            self._mr("cBar%d" % i, bx, y0, 5, h)
            self._mr("cText%d" % i, x0 + (10 if rtl else 18), y0, w - 28, h)
            base = 25
        size = sc(base + 3 * p)
        inst = self["cText%d" % i].instance
        if inst is not None and getattr(self, "_cat_font", {}).get(i) != size:
            try:
                inst.setFont(gFont("Regular", size))
                self.__dict__.setdefault("_cat_font", {})[i] = size
            except Exception:
                pass

    def _catPopFocus(self, slot, key):
        key = (slot,) + tuple(key) if slot >= 0 else None
        if key == self._cat_key:
            return
        try:
            self._catPop.stop()
        except Exception:
            pass
        if 0 <= self._cat_slot < CAT_ROWS:
            self._placeCat(self._cat_slot, 0.0)
        self._cat_key = key
        self._cat_slot = slot
        if slot < 0:
            return
        self._cat_frame = 0
        try:
            self._catPop.start(POP_MS, False)
        except Exception:
            self._placeCat(slot, 1.0)

    def _catPopTick(self):
        if self._cat_slot < 0:
            self._catPop.stop()
            return
        import math
        self._cat_frame += 1
        t = min(1.0, self._cat_frame / float(anim_frames()))
        self._placeCat(self._cat_slot, math.sin(t * math.pi / 2))
        if t >= 1.0:
            self._catPop.stop()

    # ─── [theme] Modern: pop zoom on the selected poster ────────────────

    def _slotGeom(self, i, p):
        x, y = _MPX[i % M_COLS], _MPY[i // M_COLS]
        z = POP_ZOOM * p
        w, h = int(MPW * (1 + z)), int(MPH * (1 + z))
        return x - (w - MPW) // 2, y - (h - MPH) // 2, w, h, (h - MPH) // 2

    def _mr(self, name, x, y, w=None, h=None):
        """move / resize a widget -- skipped when it would not change a pixel
        (each call makes Enigma2 redraw the widget)"""
        inst = self[name].instance
        if inst is None:
            return
        geo = self.__dict__.setdefault("_geo", {})
        pos = (sc(x), sc(y))
        size = (max(1, sc(w)), max(1, sc(h))) if w is not None else None
        old = geo.get(name)
        if old and old[0] == pos and (size is None or old[1] == size):
            return
        try:
            if not old or old[0] != pos:
                inst.move(ePoint(*pos))
            if size is not None and (not old or old[1] != size):
                inst.resize(eSize(*size))
            geo[name] = (pos, size if size is not None else (old[1] if old else None))
        except Exception:
            pass

    def _placeSlot(self, i, p, xoff=0.0, text=True):
        if not self._modern:
            # Classic grid: no zoom, only the page slide moves it
            x, y = _PX[i % P_COLS] + xoff, _PY[i // P_COLS]
            self._mr("pBack%d" % i, x, y, PW, PH)
            self._mr("pImg%d" % i, x, y, PW, PH)
            self._mr("pEdge%d" % i, x - 4, y - 4, PW + 8, PH + 8)
            self._mr("pRate%d" % i, x + PW - 78, y + 8)
            self._mr("pQual%d" % i, x + PW - 92, y + 40)
            self._mr("pYear%d" % i, x + 8, y + 8)
            self._mr("pLabel%d" % i, x + 8, y + 38)
            self._mr("pMark%d" % i, x, y + PH - 28, PW, 28)
            if text:
                self._mr("pName%d" % i, x, y + PH + 6)
            return
        x, y, w, h, dy = self._slotGeom(i, p)
        x += xoff
        bx, by = _MPX[i % M_COLS] + xoff, _MPY[i // M_COLS]
        self._mr("pBack%d" % i, x, y, w, h)
        if anim_light():
            # Light: the picture keeps its size (rescaling it is the costly part)
            self._mr("pImg%d" % i, bx, by, MPW, MPH)
        else:
            self._mr("pImg%d" % i, x, y, w, h)
        self._mr("pEdge%d" % i, x - 6, y - 6, w + 12, h + 12)
        self._mr("pRate%d" % i, x + w - 78, y + 8)
        self._mr("pQual%d" % i, x + w - 92, y + 40)
        self._mr("pYear%d" % i, x + 8, y + 8)
        self._mr("pLabel%d" % i, x + 8, y + 38)
        self._mr("pMark%d" % i, x, y + h - 28, w, 28)
        if text:
            self._mr("pName%d" % i, bx - 8, by + MPH + 8 + dy)

    def _popFocus(self, view, n):
        slot = (self._pi - view * self._view if (self._zone == "posters" and n) else -1) if self._modern else -1
        key = (view, slot, id(self._posters))
        if key == self._pop_key:
            return                   # a repaint (art arrived) -- keep the zoom
        old = self._pop_key
        # [slide-anim] a new view of posters glides in sideways: the next
        # view / page from the right, the previous one from the left
        d = 0
        if getattr(self, "_first_posters", False) and n:
            self._first_posters = False
            d = 1                    # the first posters glide in from the right
        elif old and old[2] == key[2] and abs(view - old[0]) == 1:
            d = view - old[0]
        elif old and old[2] != key[2] and n:
            d = getattr(self, "_slide_dir", 0)
        if old and old[2] != key[2]:
            self._slide_dir = 0
        try:
            self._pop.stop()
        except Exception:
            pass
        if 0 <= self._pop_slot < self._view and self._pop_slot != slot:
            self._placeSlot(self._pop_slot, 0.0)
        self._pop_key = key
        self._pop_slot = slot
        self._grid_slide = d
        if slot < 0 and not d:
            return
        self._pop_frame = 0
        self._pop_p = 0.0
        self._pop_last = None
        self._gridFrame(0.0)
        try:
            self._pop.start(POP_MS, False)
        except Exception:
            self._gridFrame(1.0)

    def _gridFrame(self, p):
        d = getattr(self, "_grid_slide", 0)
        if d:
            off = d * (1920 - 430) * (1.0 - p)
            ends = p <= 0.0 or p >= 1.0          # names / years only at start and end
            for i in range(self._view):
                self._placeSlot(i, p if i == self._pop_slot else 0.0, off, ends)
        elif self._pop_slot >= 0:
            self._placeSlot(self._pop_slot, p)

    # [carousel] the middle poster grows from a side poster's size to its
    # own on every move (same smooth pop as the grid / home carousel)
    def _carGeom(self, p):
        x, y = _KX[K_MID], _KCY
        w, h = int(KW + (KCW - KW) * p), int(KH + (KCH - KH) * p)
        return x + (KCW - w) // 2, y + (KCH - h) // 2, w, h

    def _placeCarSlot(self, s_, x, y, w, h, final=True):
        self._mr("kBack%d" % s_, x, y, w, h)
        if final or not anim_light():
            self._mr("kImg%d" % s_, x, y, w, h)
        else:
            self._mr("kImg%d" % s_, x, y)        # Light: move only, resize at the end
        self._mr("kEdge%d" % s_, x - 5, y - 5, w + 10, h + 10)
        self._mr("kRate%d" % s_, x + w - 78, y + 8)
        self._mr("kQual%d" % s_, x + w - 92, y + 40)
        self._mr("kYear%d" % s_, x + 8, y + 8)
        self._mr("kLabel%d" % s_, x + 8, y + 38)
        self._mr("kMark%d" % s_, x, y + h - 28, w, 28)

    def _kGeom(self, k):
        """slot k's box; off the strip = one more step left / right"""
        if 0 <= k < K_SLOTS:
            return _KG[k]
        if k < 0:
            g0, g1 = _KG[0], _KG[1]
            return (g0[0] + k * (g1[0] - g0[0]), g0[1], g0[2], g0[3])
        gl, g2 = _KG[-1], _KG[-2]
        return (gl[0] + (k - K_SLOTS + 1) * (gl[0] - g2[0]), gl[1], gl[2], gl[3])

    def _placeCarCentre(self, p):
        d = getattr(self, "_car_delta", 0)
        if not d:
            self._placeCarSlot(K_MID, *self._carGeom(p), final=p >= 1.0)
            return
        # [slide-anim] every poster glides from its old slot to the new one
        for s_ in range(K_SLOTS):
            a, b = self._kGeom(s_ + d), self._kGeom(s_)
            self._placeCarSlot(s_, *[int(a[i] + (b[i] - a[i]) * p) for i in range(4)], final=p >= 1.0)

    def _carPopFocus(self):
        key = (self._pi, id(self._posters), self._zone)
        old = getattr(self, "_car_key", None)
        if key == old:
            return
        self._car_key = key
        d = 0
        if old and old[1:] == key[1:] and abs(key[0] - old[0]) == 1:
            d = key[0] - old[0]
        self._car_delta = d
        self._car_frame = 0
        self._placeCarCentre(0.0)
        try:
            self._carPop.start(POP_MS, False)
        except Exception:
            self._placeCarCentre(1.0)

    def _carPopTick(self):
        if not self._carousel():
            self._carPop.stop()
            self._placeCarCentre(1.0)
            return
        import math
        self._car_frame += 1
        t = min(1.0, self._car_frame / float(anim_frames()))
        self._placeCarCentre(math.sin(t * math.pi / 2))
        if t >= 1.0:
            self._carPop.stop()

    def _popTick(self):
        if (self._pop_slot < 0 and not getattr(self, "_grid_slide", 0)) or self._carousel():
            self._pop.stop()
            return
        self._pop_frame += 1
        steps = anim_frames()
        if getattr(self, "_grid_slide", 0):
            steps = min(steps, 8)                # a whole page moves: fewer, bigger steps
        t = min(1.0, self._pop_frame / float(steps))
        # ease-out sine: softer start than cubic, so the first frames
        # don't jump several pixels at once
        import math
        self._pop_p = math.sin(t * math.pi / 2)
        if getattr(self, "_grid_slide", 0):
            self._gridFrame(self._pop_p)
        else:
            # skip frames that wouldn't change a single pixel (less redraw work)
            size = self._slotGeom(self._pop_slot, self._pop_p)[2:4]
            if size != getattr(self, "_pop_last", None) or t >= 1.0:
                self._pop_last = size
                self._placeSlot(self._pop_slot, self._pop_p)
        if t >= 1.0:
            self._pop.stop()
            self._grid_slide = 0

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
        if getattr(self, "_skeleton", False):
            self._skeleton = False
            for s_ in range(K_SLOTS):
                self._color("kBack%d" % s_, bg=CARD)
        self["gridMsg"].hide()
        self._pi = max(0, min(self._pi, n - 1))
        more = u"  ·  page {}".format(self._page) if (self._page > 1 or self._hasPages()) else u""
        # ‹ › at the ends of the strip when there are titles beyond it
        _br = self._zone == "posters"
        self["kLeft"].show() if (_br and (self._pi - K_MID > 0 or self._page > 1)) else self["kLeft"].hide()
        self["kRight"].show() if (_br and (self._pi + K_MID < n - 1 or bool(self._next_url)
                                           or (self._site in _PAGED_SITES and bool(self._cat_url)))) else self["kRight"].hide()
        self["kCount"].setText(u"{} / {}{}".format(self._pi + 1, n, more))
        missing = False
        browsing = self._zone == "posters"
        for s_ in range(K_SLOTS):
            g = self._pi + (s_ - K_MID)
            names = ["%s%d" % (_n, s_) for _n in ("kEdge", "kBack", "kImg", "kRate", "kQual", "kYear", "kLabel", "kMark")]
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
                pix = self.__dict__.setdefault("_pix_path", {})
                if pix.get(("k", s_)) != path:
                    inst.setPixmapFromFile(path)
                    pix[("k", s_)] = path
                self["kImg%d" % s_].show()
            else:
                self["kImg%d" % s_].hide()
            self._paintBadges("k", s_, it)
        if missing:
            self._art_tries = 0
            self._art.start(1000, False)
        self._carPopFocus()
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
            view = self._pi // self._view
            window = self._posters[view * self._view:(view + 1) * self._view]
        done = True
        for it in window:
            u = it.get("poster") or it.get("thumbnail") or ""
            if u and not plugin_imagecache.getCachedImage(
                    u, target_size=(MPW, MPH) if (self._modern and not self._carousel()) else (PW, PH)):
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
        if self._modern and not self._carousel():
            return                    # [bigger] no info strip -> no TMDb lookup per move
        title = _strip_arabic_from_english_title(it.get("title", "")) or it.get("title", "")
        if self._modern:
            self["infoTitle"].setText(_single_line_text(title, width=40, fallback="—"))
        else:
            self["infoTitle"].setText(_wrap_ui_text(title, width=24, max_lines=2, fallback="—"))
        self["kTitle"].setText(_single_line_text(title, width=44, fallback="—"))
        self._paintMeta(it, {})
        self["infoState"].setText(self._stateText(it))
        self["kState"].setText(self._stateText(it))
        _p = (it.get("plot") or "").strip()
        if len(_p) < 60 or _p == (self._cat_name or "").strip():
            _p = ""
        self["infoPlot"].setText(_wrap_ui_text(_p, width=38, max_lines=11, fallback=""))
        self["kPlot"].setText(_wrap_ui_text(_p, width=80, max_lines=5, fallback=""))
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
        self["infoMeta"].setText(_single_line_text("  ·  ".join(b for b in bits if b), width=44 if self._modern else 40, fallback=""))
        self["kMeta"].setText(_single_line_text("  ·  ".join(b for b in bits if b), width=90, fallback=""))

    def _paintBackdrop(self, it):
        if self._modern:
            self._info_img_ok = False
            return
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
            self["kPlot"].setText(_wrap_ui_text(meta["plot"], width=80, max_lines=5, fallback=""))
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
            self["hint"].setText("OK  Open    ◀ ▶  Pages    MENU  Options    EXIT  Sections")

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
        local = self._pi % self._view
        col = local % self._cols
        if dx < 0 and col == 0:
            # ◀ on the first column follows the ‹ arrow: the previous view of
            # posters (same row, last column), then the previous page; only
            # from the very first view does it go to the categories
            view = self._pi // self._view
            if view > 0:
                row = local // self._cols
                self._pi = (view - 1) * self._view + row * self._cols + self._cols - 1
                self._paintPosters()
                return
            if self._page > 1 and len(self._history) > 1 and not self._loading:
                self._prevPage()
                return
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
            t = self._pi + self._cols
            if t < n:
                self._pi = t
            elif self._pi // self._view < (n - 1) // self._view:
                self._pi = n - 1
            else:
                self._nextPage()
                return
        elif dy < 0:
            t = self._pi - self._cols
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
        self._slide_dir = 1
        self._fetch(url)

    def _prevPage(self):
        if self._loading or len(self._history) < 2:
            return
        self._history.pop()
        url, self._page = self._history[-1]
        self._focus_end = False          # the previous page opens on its first title
        self._slide_dir = -1
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
        toast(self, msg)
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
        toast(self, u"\u2605  Added to favorites" if added else u"Removed from favorites")
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
