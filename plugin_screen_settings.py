# -*- coding: utf-8 -*-
"""NovaPlay — Settings screen (panel style, 2026-10-21).

Layout (1920x1080): header with logo, title and clock; a SECTIONS sidebar
(Playback, Subtitles, Sources & Network, Library & Display, Tools, About)
with box info underneath; a 3-column grid of setting cards showing their
current value; and a colour-key bar at the bottom.

Keys
  ▲ ▼          sidebar: section          cards: move up / down
  ◀ ▶          cards: move; ◀ from the first column goes back to the sidebar
  OK           sidebar: open the cards   cards: change / open the setting
  RED GREEN YELLOW BLUE   Proxy, adult content, TMDb key, TorrServer URL
  INFO         TMDb: back to the built-in key
  MENU         jump to Tools
  EXIT         close

Icons: images/settings/*.png (tools/make_settings_icons.py).
"""
import os
import socket
import sys
import time

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from novaplay_choice import NovaChoiceBox as ChoiceBox
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, gFont


from plugin_common import _PLUGIN_VERSION, PLUGIN_PATH, my_log
from plugin_gridlist import scale_skin_xml, sc, POP_ANIM_FRAMES, POP_ANIM_INTERVAL, anim_frames, anim_reset, anim_light
from novaplay_ui import UiHelpers                       # [ui-helpers] _color / _mr
from plugin_state import (_get_config, _set_config, _PLUGIN_OWNER, _favorite_items,
                          _history_items, _DEFAULT_TMDB_API_KEY)
from plugin_util import _site_label, _site_tagline
from novaplay_diagnostics import NovaDiagnosticsScreen
from install_dependencies import open_installer_with_restart, open_restart_prompt
from plugin_screen_downloads import NovaPlayDownloads

_ICONS = os.path.join(PLUGIN_PATH, "images", "settings")

GOLD = "#F2C230"
WHITE = "#FFFFFF"
TEXT = "#D6DCE5"
MUTED = "#A3ADBB"
CARD = "#161C26"
EDGE = "#262E3B"
ROW_ON = "#222B3A"
PANEL = "#121822"

ROWS = 6            # sidebar sections
CARDS = 9           # 3 x 3 grid
COLS = 3
_SIDE_Y0, _SIDE_STEP = 214, 74
_CX = (430, 921, 1412)
_CY = (222, 474, 726)
_CW, _CH = 468, 230

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _skin():
    p = PLUGIN_PATH
    parts = ["""
    <screen name="NovaPlaySettings" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="#0C1017">
        <eLabel position="0,0" size="1920,1080" backgroundColor="#0C1017" zPosition="0" />
        <ePixmap position="40,30" size="84,84" pixmap="{p}/images/plugin.png" alphatest="blend" scale="1" zPosition="2" />
        <widget name="title" position="146,34" size="1000,52" font="Bold;40" foregroundColor="{gold}" backgroundColor="#0C1017" transparent="1" zPosition="2" />
        <widget name="subtitle" position="146,88" size="1000,32" font="Regular;22" foregroundColor="{muted}" backgroundColor="#0C1017" transparent="1" zPosition="2" />
        <widget name="clock_time" position="1380,22" size="500,64" font="Bold;54" halign="right" foregroundColor="{white}" backgroundColor="#0C1017" transparent="1" zPosition="2" />
        <widget name="clock_date" position="1280,90" size="600,32" font="Regular;22" halign="right" foregroundColor="{muted}" backgroundColor="#0C1017" transparent="1" zPosition="2" />

        <widget name="sideEdge" position="40,150" size="360,810" backgroundColor="{gold}" cornerRadius="16" zPosition="1" />
        <widget name="sidePanel" position="42,152" size="356,806" backgroundColor="{panel}" cornerRadius="14" zPosition="2" />
        <eLabel position="68,174" size="300,30" text="SECTIONS" font="Regular;20" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <eLabel position="68,690" size="304,1" backgroundColor="#2A3240" zPosition="3" />
        <eLabel position="68,704" size="300,30" text="SYSTEM" font="Regular;20" foregroundColor="{muted}" backgroundColor="{panel}" transparent="1" zPosition="3" />
        <widget name="sysinfo" position="68,740" size="316,212" font="Regular;22" foregroundColor="#C9D0DA" backgroundColor="{panel}" transparent="1" zPosition="3" />

        <widget name="secTitle" position="430,148" size="1000,58" font="Bold;42" foregroundColor="{white}" backgroundColor="#0C1017" transparent="1" zPosition="2" />
        <widget name="secCount" position="1380,162" size="500,36" font="Regular;24" halign="right" foregroundColor="{muted}" backgroundColor="#0C1017" transparent="1" zPosition="2" />

        <widget name="aboutBox" position="430,222" size="1450,734" backgroundColor="{card}" cornerRadius="14" zPosition="2" />
        <widget name="aH0" position="470,252" size="760,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB0" position="492,294" size="738,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aH1" position="470,424" size="760,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB1" position="492,466" size="738,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aH2" position="470,596" size="760,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB2" position="492,638" size="738,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aH3" position="470,768" size="760,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB3" position="492,810" size="738,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aH4" position="1270,252" size="580,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB4" position="1292,294" size="558,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aH5" position="1270,424" size="580,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB5" position="1292,466" size="558,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aH6" position="1270,596" size="580,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB6" position="1292,638" size="558,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aH7" position="1270,768" size="580,36" font="Bold;26" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="3" />
        <widget name="aB7" position="1292,810" size="558,118" font="Regular;23" foregroundColor="{text}" backgroundColor="{card}" transparent="1" zPosition="3" />

        <eLabel position="40,976" size="1840,76" backgroundColor="{panel}" cornerRadius="16" zPosition="1" />
        <eLabel position="72,1003" size="22,22" backgroundColor="#E5484D" cornerRadius="11" zPosition="2" />
        <widget name="key_red" position="106,994" size="230,40" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <eLabel position="352,1003" size="22,22" backgroundColor="#30A46C" cornerRadius="11" zPosition="2" />
        <widget name="key_green" position="386,994" size="290,40" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <eLabel position="692,1003" size="22,22" backgroundColor="#F2C230" cornerRadius="11" zPosition="2" />
        <widget name="key_yellow" position="726,994" size="230,40" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <eLabel position="972,1003" size="22,22" backgroundColor="#3E7BFA" cornerRadius="11" zPosition="2" />
        <widget name="key_blue" position="1006,994" size="230,40" font="Regular;26" valign="center" foregroundColor="{white}" backgroundColor="{panel}" transparent="1" zPosition="2" />
        <widget name="hint" position="1250,994" size="600,40" font="Regular;24" halign="right" valign="center" foregroundColor="#C9D0DA" backgroundColor="{panel}" transparent="1" zPosition="2" />
    """.format(p=p, gold=GOLD, muted=MUTED, white=WHITE, panel=PANEL, card=CARD, text=TEXT)]
    for i in range(ROWS):
        y = _SIDE_Y0 + i * _SIDE_STEP
        parts.append("""
        <widget name="rowBg{i}" position="54,{y}" size="332,66" backgroundColor="{panel}" cornerRadius="10" zPosition="3" />
        <widget name="rowBar{i}" position="54,{y}" size="5,66" backgroundColor="{gold}" zPosition="5" />
        <widget name="rowIcon{i}" position="72,{iy}" size="36,36" alphatest="blend" scale="1" zPosition="5" />
        <widget name="rowText{i}" position="124,{y}" size="256,66" font="Regular;28" valign="center" foregroundColor="{text}" backgroundColor="{panel}" transparent="1" zPosition="5" />
        """.format(i=i, y=y, iy=y + 15, panel=PANEL, gold=GOLD, text=TEXT))
    for i in range(CARDS):
        x, y = _CX[i % COLS], _CY[i // COLS]
        parts.append("""
        <widget name="cEdge{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{edge}" cornerRadius="14" zPosition="2" />
        <widget name="cBox{i}" position="{x3},{y3}" size="{w3},{h3}" backgroundColor="{card}" cornerRadius="12" zPosition="3" />
        <widget name="cIconBg{i}" position="{ix},{iy}" size="72,72" backgroundColor="#232B38" cornerRadius="14" zPosition="4" />
        <widget name="cIcon{i}" position="{px},{py}" size="44,44" alphatest="blend" scale="1" zPosition="5" />
        <widget name="cTitle{i}" position="{tx},{ty}" size="336,40" font="Bold;30" foregroundColor="{white}" backgroundColor="{card}" transparent="1" zPosition="5" />
        <widget name="cValue{i}" position="{tx},{vy}" size="336,34" font="Bold;24" foregroundColor="{gold}" backgroundColor="{card}" transparent="1" zPosition="5" />
        <widget name="cDesc{i}" position="{dx},{dy}" size="424,96" font="Regular;22" foregroundColor="#B4BCC8" backgroundColor="{card}" transparent="1" zPosition="5" />
        """.format(i=i, x=x, y=y, w=_CW, h=_CH, x3=x + 3, y3=y + 3, w3=_CW - 6, h3=_CH - 6,
                   ix=x + 22, iy=y + 22, px=x + 36, py=y + 36, tx=x + 112, ty=y + 26, vy=y + 68,
                   dx=x + 24, dy=y + 114, edge=EDGE, card=CARD, white=WHITE, gold=GOLD))
    parts.append("</screen>")
    return "".join(parts)


# ─── small helpers ──────────────────────────────────────────────────────

def _on(key, default="true"):
    return str(_get_config(key, default)).lower() in ("true", "1", "yes", "on")


def _int(key, default):
    try:
        return int(_get_config(key, str(default)) or default)
    except Exception:
        return default


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _trailer_cache_files():
    """[trailers] the saved trailer files (both cache places) -> (paths, bytes)"""
    out, size = [], 0
    for d in ("/media/hdd/NovaPlay/cache/trailers", "/tmp/ap_trailers"):
        try:
            for n in os.listdir(d):
                fp = os.path.join(d, n)
                if os.path.isfile(fp):
                    out.append(fp)
                    size += os.path.getsize(fp)
        except Exception:
            pass
    return out, size


def _trailer_cache_summary():
    files, size = _trailer_cache_files()
    if not files:
        return u"None saved"
    return u"{} · {:.0f} MB".format(len(files), size / 1048576.0)


def _sites_summary():
    try:
        from extractors import get_site_names, get_home_sites
        return u"{} sites  ·  {} on the Home screen".format(len(get_site_names()), len(list(get_home_sites())))
    except Exception:
        return u"—"


def _system_lines():
    model = _read("/proc/stb/info/vumodel")
    model = ("Vu+ " + model.upper()) if model else (_read("/proc/stb/info/model") or _read("/etc/hostname") or "Enigma2")
    image = ""
    iv = _read("/etc/image-version")
    if iv:
        kv = dict(l.split("=", 1) for l in iv.splitlines() if "=" in l)
        image = "{} {}".format(kv.get("distro", ""), kv.get("imageversion", "")).strip()
    if not image:
        image = (_read("/etc/issue").splitlines() or [""])[0].replace("\\n", "").replace("\\l", "").strip()
    ip = "—"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    disk = "—"
    for mnt in ("/media/hdd", "/media/usb", "/"):
        try:
            if mnt != "/" and not os.path.ismount(mnt):
                continue
            st = os.statvfs(mnt)
            disk = "{:.1f} GB free".format(st.f_bavail * st.f_frsize / 1e9)
            break
        except Exception:
            continue
    return "\n".join([
        "Box: " + model,
        "Image: " + (image or "—")[:22],
        "Python: " + sys.version.split()[0],
        "IP: " + ip,
        "Disk: " + disk,
        "Favorites {}  ·  History {}".format(len(_favorite_items()), len(_history_items())),
    ])


class NovaPlaySettings(UiHelpers, Screen):
    skin = scale_skin_xml(_skin())

    SECTIONS = (
        ("playback", "Playback", "play"),
        ("subtitles", "Subtitles", "cc"),
        ("network", "Sources & Network", "globe"),
        ("library", "Library & Display", "star"),
        ("tools", "Tools", "tools"),
        ("about", "About", "info"),
    )

    def __init__(self, session, current_site=None):
        Screen.__init__(self, session)
        self._current_site = current_site
        self._sec = 0
        self._card = 0
        self._zone = "side"          # "side" | "cards"
        self._items = []

        self["title"] = Label("Settings")
        self["subtitle"] = Label("NOVA Media Player {}  |  by {}".format(_PLUGIN_VERSION, _get_config("owner", _PLUGIN_OWNER)))
        self["clock_time"] = Label("")
        self["clock_date"] = Label("")
        self["sideEdge"] = Label("")
        self["sidePanel"] = Label("")
        self["sysinfo"] = Label("")
        self["secTitle"] = Label("")
        self["secCount"] = Label("")
        self["aboutBox"] = Label("")
        for _i in range(8):
            self["aH%d" % _i] = Label("")
            self["aB%d" % _i] = Label("")
        self["key_red"] = Label("Proxy")
        self["key_green"] = Label("Adult content")
        self["key_yellow"] = Label("TMDb key")
        self["key_blue"] = Label("TorrServer")
        self["hint"] = Label("")
        for i in range(ROWS):
            self["rowBg%d" % i] = Label("")
            self["rowBar%d" % i] = Label("")
            self["rowIcon%d" % i] = Pixmap()
            self["rowText%d" % i] = Label(self.SECTIONS[i][1])
        for i in range(CARDS):
            for n in ("cEdge", "cBox", "cIconBg", "cTitle", "cValue", "cDesc"):
                self["%s%d" % (n, i)] = Label("")
            self["cIcon%d" % i] = Pixmap()

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions",
             "InfobarEPGActions", "InfobarMenuActions", "MenuActions"], {
                "ok": self._ok,
                "cancel": self.close,
                "up": lambda: self._move(0, -1),
                "down": lambda: self._move(0, 1),
                "left": lambda: self._move(-1, 0),
                "right": lambda: self._move(1, 0),
                "red": self._edit_proxy,
                "green": self._toggleAdult,
                "yellow": self._edit_tmdb_key,
                "blue": self._edit_torrserver_url,
                "showEventInfo": self._clear_tmdb_key,
                "menu": self._jumpTools,
                "showMenu": self._jumpTools,
                "mainMenu": self._jumpTools,
            }, -1)

        self._clock = eTimer()
        self._clock.callback.append(self._tick)
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._clock.stop)
        # [pop-anim] same smooth zoom as the home / site screens
        self._popT = eTimer()
        self._popT.callback.append(self._popTick)
        self._pop_item = None
        self._pop_f = 0
        self._frame_on = None
        self._frame_f = 0
        self._side_font = {}
        self.onClose.append(self._popT.stop)

    # ─── lifecycle ──────────────────────────────────────────────────────

    def _start(self):
        try:
            self["sysinfo"].setText(_system_lines())
        except Exception as e:
            my_log("settings sysinfo: {}".format(e))
        self._tick()
        self._clock.start(10000, False)
        self._refresh()

    def _tick(self):
        t = time.localtime()
        self["clock_time"].setText("{}:{:02d}".format(t.tm_hour % 12 or 12, t.tm_min))
        self["clock_date"].setText("{} {} {}".format(_DAYS[t.tm_wday], t.tm_mday, _MONTHS[t.tm_mon - 1]))

    # ─── items per section ──────────────────────────────────────────────
    # (key, icon, title, value, description)

    def _build(self, sec):
        if sec == "playback":
            mq = str(_get_config("max_quality", "auto") or "auto").strip().lower()
            return [
                ("maxq", "hd", "Max Quality", {"auto": "Auto"}.get(mq, mq),
                 "Highest stream quality picked automatically"),
                ("nxdelay", "next", "Next Episode", "{} seconds".format(_int("next_episode_delay", 10)),
                 "Countdown before the next episode starts"),
                ("nxprompt", "ask", "End-of-Movie Menu", "On" if _on("next_episode_prompt") else "Off",
                 "Near the end, ask: next episode, mark watched or save position"),
                ("skip", "skip", "Skip Intro", "+{} seconds".format(_int("skip_intro_secs", 90)),
                 "How far the green key jumps at the start"),
                ("qpick", "pick", "Remember Quality", "On" if _get_config("quality_auto_pick", False) else "Off",
                 "Reuse the quality picked last time for a title instead of asking"),
            ]
        if sec == "subtitles":
            return [
                ("autosub", "cc", "Auto Subtitles", "On" if _on("autosub") else "Off",
                 "Load a matching subtitle when playback starts"),
                ("studio", "studio", "Subtitle Studio", "On" if _on("substudio_mode", "false") else "Off",
                 "Styled subtitles: size, font, colour, outline and position"),
                ("subsettings", "sliders", "Subtitle Settings", "Open  ›",
                 "Provider API keys, subtitle folder and cache"),
            ]
        if sec == "network":
            key = (_get_config("tmdb_api_key", "") or "").strip()
            keytxt = ("Built-in key" if key == _DEFAULT_TMDB_API_KEY
                      else ("••••" + key[-4:]) if key else "Not set")
            proxy = (_get_config("browser_proxy", "") or "").strip()
            ts = (_get_config("torrserver_url", "") or "").strip()
            return [
                ("tmdb", "key", "TMDb Key", keytxt,
                 "Posters, plots and ratings. INFO key: back to the built-in key"),
                ("proxy", "shield", "Browser Proxy", proxy.replace("http://", "") or "Off",
                 "Helper for sites behind Cloudflare (http://IP:PORT)"),
                ("torrserver", "magnet", "TorrServer", ts.replace("http://", "") or "Not set",
                 "Plays magnet links (http://IP:PORT)"),
                ("adult", "adult", "Adult Content", "Shown" if _get_config("show_adult", "false") == "true" else "Hidden",
                 "Show or hide adult sites and categories"),
                ("transmission", "transmission", "Transmission", self._trState(),
                 "Starts only for Download to HDD, pauses while you watch"),
            ]
        if sec == "library":
            lib = [
                ("watchedbw", "contrast", "Watched Posters",
                 "Black & white" if _get_config("watched_poster_bw", False) else "Colour",
                 "How posters you already watched are shown"),
                ("downloads", "download", "Downloads", "Open  ›",
                 "Downloads and recordings in progress"),
            ]
            lib.insert(0, ("layout", "grid", "Poster Layout",
                           "Carousel" if _get_config("layout_style", "grid") == "carousel" else "Grid",
                           "Grid with info panel, or carousel over a big backdrop (GREEN on a site switches)"))
            lib.insert(1, ("screenstyle", "grid", "Screen Style",
                           "Modern" if _get_config("screen_style", "classic") == "modern" else "Classic",
                           "Classic, or Modern: 7 x 2 posters without the info panel (site and detail screens)"))
            lib.insert(3, ("trailermode", "grid", "Trailer",
                           "Download" if _get_config("trailer_mode", "stream") == "download" else "Stream first",
                           "Stream first: starts at once (360p) when YouTube allows; Download: sharper 480p, waits"))
            _tq = str(_get_config("torrent_quality", "1080p") or "1080p")
            lib.insert(len(lib) - 1, ("torrquality", "magnet", "Torrent Quality",
                                      {"720p": "720p first", "4K": "4K first", "any": "Most seeders"}.get(_tq, "1080p first"),
                                      "Which torrents are listed first (each quality sorted by seeders)"))
            _am = str(_get_config("anim_mode", "full") or "full")
            lib.insert(2, ("animmode", "grid", "Animations",
                           {"light": "Light", "off": "Off"}.get(_am, "Full"),
                           "Full, Light (fewer steps, pictures don't rescale) or Off -- for a smoother box"))
            return lib
        if sec == "tools":
            return [
                ("diagnostics", "pulse", "Diagnostics", "Run  ›",
                 "Check sites, proxy, TMDb and the player"),
                ("debuglog", "pulse", "Debug Log",
                 "On" if str(_get_config("debug_log", "false")).lower() == "true" else "Off",
                 "Also log every web request and its time (/tmp/novaplay.log) -- on when reporting a problem"),
                ("downloads", "download", "Downloads", "Open  ›",
                 "Downloads and recordings in progress"),
                ("cleartrailers", "download", "Downloaded Trailers", _trailer_cache_summary(),
                 "Delete the trailers saved for instant replay (OK)"),
                ("install", "package", "Install Dependencies", "Run  ›",
                 "Install missing packages the plugin needs"),
                ("restart", "restart", "Restart Enigma2", "Restart  ›",
                 "Restart the box interface"),
            ]
        return []

    def _trState(self):
        try:
            import novaplay_transmission
            if not os.path.exists(novaplay_transmission.DAEMON):
                return "Not installed"
            return "Running" if novaplay_transmission._running_args() is not None else "Off (on demand)"
        except Exception:
            return "—"

    # ─── painting ───────────────────────────────────────────────────────

    def _icon(self, name, file):
        inst = self[name].instance
        if inst is not None:
            path = os.path.join(_ICONS, file)
            if os.path.exists(path):
                inst.setPixmapFromFile(path)

    def _refresh(self):
        key = self.SECTIONS[self._sec][0]
        self._items = self._build(key)
        if self._items:
            self._card = min(self._card, len(self._items) - 1)
        side = self._zone == "side"
        # sidebar
        self._color("sideEdge", bg=GOLD if side else EDGE)
        for i, (_k, _t, ic) in enumerate(self.SECTIONS):
            on = i == self._sec
            self._color("rowBg%d" % i, bg=ROW_ON if on else PANEL)
            self["rowBg%d" % i].show() if on else self["rowBg%d" % i].hide()
            self["rowBar%d" % i].show() if on else self["rowBar%d" % i].hide()
            self._color("rowText%d" % i, fg=WHITE if on else TEXT)
            self._icon("rowIcon%d" % i, "side_%s_%s.png" % (ic, "on" if on else "off"))
        # header of the main area
        self["secTitle"].setText(self.SECTIONS[self._sec][1])
        about = key == "about"
        self["secCount"].setText("" if about else "{} items".format(len(self._items)))
        for n in ["aboutBox"] + ["aH%d" % _i for _i in range(8)] + ["aB%d" % _i for _i in range(8)]:
            self[n].show() if about else self[n].hide()
        if about:
            self._paintAbout()
        # cards
        for i in range(CARDS):
            names = [n % i for n in ("cEdge%d", "cBox%d", "cIconBg%d", "cIcon%d", "cTitle%d", "cValue%d", "cDesc%d")]
            if i >= len(self._items):
                for n in names:
                    self[n].hide()
                continue
            for n in names:
                self[n].show()
            _k, ic, title, value, desc = self._items[i]
            sel = (not side) and i == self._card
            self._color("cEdge%d" % i, bg=GOLD if sel else EDGE)
            self["cTitle%d" % i].setText(title)
            self._color("cTitle%d" % i, fg=GOLD if sel else WHITE)
            self["cValue%d" % i].setText(str(value))
            self["cDesc%d" % i].setText(desc)
            self._icon("cIcon%d" % i, "card_%s.png" % ic)
        self._popFocus()
        if side:
            self["hint"].setText("OK  Open    ▲ ▼  Section    EXIT  Close")
        else:
            self["hint"].setText("OK  Change    ◀  Sections    EXIT  Close")

    # ─── [pop-anim] zoom: section row, card, panel frame ────────────────

    def _placeRow(self, i, p):
        W, H = 332, 66
        dw, dh = 20.0 * p, float(_SIDE_STEP - H) * p
        x0, y0 = 54 - dw / 2.0, _SIDE_Y0 + i * _SIDE_STEP - dh / 2.0
        w, h = W + dw, H + dh
        ic = 36 + 6 * p
        self._mr("rowBg%d" % i, x0, y0, w, h)
        self._mr("rowBar%d" % i, x0, y0, 5, h)
        self._mr("rowIcon%d" % i, x0 + 18, y0 + (h - ic) / 2.0, ic, ic)
        self._mr("rowText%d" % i, x0 + 70 + 6 * p, y0, w - 76, h)
        size = sc(28 + 3 * p)
        inst = self["rowText%d" % i].instance
        if inst is not None and self._side_font.get(i) != size:
            try:
                inst.setFont(gFont("Regular", size))
                self._side_font[i] = size
            except Exception:
                pass

    _CARD_PARTS = (("cEdge", 0, 0, _CW, _CH, True), ("cBox", 3, 3, _CW - 6, _CH - 6, True),
                   ("cIconBg", 22, 22, 72, 72, True), ("cIcon", 36, 36, 44, 44, True),
                   ("cTitle", 112, 26, 336, 40, False), ("cValue", 112, 68, 336, 34, False),
                   ("cDesc", 24, 114, 424, 96, False))

    def _placeCard(self, i, p):
        x0, y0 = _CX[i % COLS], _CY[i // COLS]
        k = 1.0 + 0.08 * p
        cx, cy = x0 - (_CW * k - _CW) / 2.0, y0 - (_CH * k - _CH) / 2.0
        for name, rx, ry, w, h, rs in self._CARD_PARTS:
            grow = rs and not (name == "cIcon" and anim_light())
            self._mr("%s%d" % (name, i), cx + rx * k, cy + ry * k, w * k if grow else w, h * k if grow else h)

    def _placeItem(self, item, p):
        if item[0] == "side":
            self._placeRow(item[1], p)
        else:
            self._placeCard(item[1], p)

    def _placeFrame(self, p):
        d = 7.0 * p
        self._mr("sideEdge", 40 - d, 150 - d, 360 + 2 * d, 810 + 2 * d)
        self._mr("sidePanel", 42 - d, 152 - d, 356 + 2 * d, 806 + 2 * d)

    def _popFocus(self):
        side = self._zone == "side"
        if side:
            item = ("side", self._sec)
        elif self._items:
            item = ("cards", self._card, self._sec)
        else:
            item = None
        restart = False
        if item != self._pop_item:
            if self._pop_item:
                self._placeItem(self._pop_item, 0.0)
            self._pop_item = item
            self._pop_f = 0
            restart = item is not None
        if side != self._frame_on:
            self._frame_on = side
            self._frame_f = 0
            if not side:
                self._placeFrame(0.0)
            restart = restart or side
        if restart:
            try:
                self._popT.start(POP_ANIM_INTERVAL, False)
            except Exception:
                pass

    def _popTick(self):
        import math
        n = float(max(1, anim_frames()))
        busy = False
        if self._pop_item and self._pop_f < n:
            self._pop_f += 1
            self._placeItem(self._pop_item, math.sin(min(1.0, self._pop_f / n) * math.pi / 2))
            busy = busy or self._pop_f < n
        if self._frame_on and self._frame_f < n:
            self._frame_f += 1
            self._placeFrame(math.sin(min(1.0, self._frame_f / n) * math.pi / 2))
            busy = busy or self._frame_f < n
        if not busy:
            self._popT.stop()

    def _paintAbout(self):
        api_key = (_get_config("tmdb_api_key", "") or "").strip()
        site = self._current_site
        blocks = [
            (u"◆  NOVA Media Player {}".format(_PLUGIN_VERSION),
             u"Movies, series and anime from many sites in one place,\nwith TMDb posters and plots, subtitles and resume."),
            (u"◆  Current source" if site else u"◆  Sources",
             u"{}\n{}".format(_site_label(site), _site_tagline(site)[:60]) if site else _sites_summary()),
            (u"◆  Update (PuTTY)",
             u"wget -qO /tmp/install_novaplay.sh https://raw.githubusercontent.com/\n"
             u"westy4ever/novaplay-modular/main/install_novaplay.sh && sh /tmp/install_novaplay.sh"),
            (u"◆  Note", u"Thank you for using NOVA Media Player!"),
            (u"◆  Developer", _get_config("owner", _PLUGIN_OWNER)),
            (u"◆  GitHub", u"github.com/westy4ever/novaplay-modular"),
            (u"◆  TMDb", u"Built-in key" if api_key == _DEFAULT_TMDB_API_KEY else (u"Your key" if api_key else u"Off")),
            (u"◆  Library", u"Favorites: {}\nHistory: {}".format(len(_favorite_items()), len(_history_items()))),
        ]
        for i, (h, b) in enumerate(blocks):
            self["aH%d" % i].setText(h)
            self["aB%d" % i].setText(b)

    # ─── navigation ─────────────────────────────────────────────────────

    def _move(self, dx, dy):
        if self._zone == "side":
            if dy:
                self._sec = (self._sec + dy) % ROWS
                self._card = 0
            elif dx > 0 and self._items:
                self._zone = "cards"
            self._refresh()
            return
        n = len(self._items)
        col, row = self._card % COLS, self._card // COLS
        if dx < 0 and col == 0:
            self._zone = "side"
        elif dx:
            self._card = max(0, min(n - 1, self._card + dx))
        elif dy:
            t = self._card + dy * COLS
            if 0 <= t < n:
                self._card = t
            elif dy > 0 and row < (n - 1) // COLS:
                self._card = n - 1
        self._refresh()

    def _jumpTools(self):
        self._sec = 4
        self._card = 0
        self._zone = "cards"
        self._refresh()

    def _ok(self):
        if self._zone == "side":
            if self._items:
                self._zone = "cards"
                self._refresh()
            return
        if not self._items:
            return
        self._action(self._items[self._card][0])

    def _toggle(self, key, default="true"):
        _set_config(key, "false" if _on(key, default) else "true")
        self._refresh()

    def _action(self, key):
        if key == "maxq":
            self._openMaxQuality()
        elif key == "nxdelay":
            self._openNextDelay()
        elif key == "nxprompt":
            self._toggle("next_episode_prompt")
        elif key == "skip":
            self._choose("Skip Intro", "skip_intro_secs", _int("skip_intro_secs", 90),
                         [("+{} seconds".format(v), v) for v in (30, 45, 60, 75, 90, 120, 150, 180)])
        elif key == "qpick":
            _set_config("quality_auto_pick", not bool(_get_config("quality_auto_pick", False)))
            self._refresh()
        elif key == "autosub":
            self._toggle("autosub")
        elif key == "studio":
            self._toggle("substudio_mode", "false")
        elif key == "subsettings":
            from novaplay_subtitles import NovaSubtitleSettings
            self.session.openWithCallback(lambda *a: self._refresh(), NovaSubtitleSettings)
        elif key == "tmdb":
            self._edit_tmdb_key()
        elif key == "proxy":
            self._edit_proxy()
        elif key == "torrserver":
            self._edit_torrserver_url()
        elif key == "adult":
            self._toggleAdult()
        elif key == "transmission":
            self._transmissionInfo()
        elif key == "debuglog":
            _on = str(_get_config("debug_log", "false")).lower() == "true"
            _set_config("debug_log", "false" if _on else "true")
            try:
                from extractors.net import debug_reset
                debug_reset()
            except Exception:
                pass
            self._refresh()
        elif key == "layout":
            _set_config("layout_style", "grid" if _get_config("layout_style", "grid") == "carousel" else "carousel")
            self._refresh()
        elif key == "trailermode":
            _set_config("trailer_mode", "stream" if _get_config("trailer_mode", "stream") == "download" else "download")
            self._refresh()
        elif key == "torrquality":
            _order = ["1080p", "720p", "4K", "any"]
            _cur = str(_get_config("torrent_quality", "1080p") or "1080p")
            _set_config("torrent_quality", _order[(_order.index(_cur) + 1) % 4 if _cur in _order else 1])
            self._refresh()
        elif key == "animmode":
            _order = ["full", "light", "off"]
            _cur = str(_get_config("anim_mode", "full") or "full")
            _set_config("anim_mode", _order[(_order.index(_cur) + 1) % 3 if _cur in _order else 1])
            anim_reset()
            self._refresh()
        elif key == "screenstyle":
            _set_config("screen_style", "classic" if _get_config("screen_style", "classic") == "modern" else "modern")
            self._refresh()
        elif key == "watchedbw":
            _set_config("watched_poster_bw", not bool(_get_config("watched_poster_bw", False)))
            self._refresh()
        elif key == "downloads":
            self.session.openWithCallback(lambda *a: self._refresh(), NovaPlayDownloads)
        elif key == "diagnostics":
            self.session.open(NovaDiagnosticsScreen)
        elif key == "install":
            open_installer_with_restart(self.session)
        elif key == "cleartrailers":
            files, size = _trailer_cache_files()
            if not files:
                self._refresh()
                return

            def _yes(ok):
                if ok:
                    n = 0
                    for f in files:
                        try:
                            os.remove(f)
                            n += 1
                        except Exception:
                            pass
                    try:
                        from plugin_toast import toast
                        toast(self, u"{} trailer{} deleted".format(n, "" if n == 1 else "s"))
                    except Exception:
                        pass
                self._refresh()
            self.session.openWithCallback(_yes, MessageBox,
                u"Delete {} downloaded trailer{} ({:.0f} MB)?\n\nThey download again the next time you watch them.".format(
                    len(files), "" if len(files) == 1 else "s", size / 1048576.0),
                MessageBox.TYPE_YESNO, default=False)
        elif key == "restart":
            open_restart_prompt(self.session)

    def _choose(self, title, cfg_key, current, options):
        items = [(("● " if v == current else "   ") + lbl, v) for lbl, v in options]

        def _picked(choice):
            if not choice:
                return
            _set_config(cfg_key, str(choice[1]))
            self._refresh()
        self.session.openWithCallback(_picked, ChoiceBox, title=title, list=items)

    def _transmissionInfo(self):
        state = self._trState()
        self.session.open(MessageBox,
                          "Transmission: {}\n\nNovaPlay starts it only when you choose \"Download to HDD\", "
                          "pauses its torrents while you watch and stops it when nothing is left to download.".format(state),
                          MessageBox.TYPE_INFO, timeout=10)

    # ─── existing actions (colour keys) ─────────────────────────────────

    def _toggleAdult(self):
        new_val = "true" if _get_config("show_adult", "false") == "false" else "false"
        _set_config("show_adult", new_val)
        self.session.open(MessageBox, "Adult content shown" if new_val == "true" else "Adult content hidden",
                          MessageBox.TYPE_INFO, timeout=3)
        self._refresh()

    def _edit_proxy(self):
        self.session.openWithCallback(self._on_proxy_entered, VirtualKeyBoard,
                                      title="Proxy: http://IP:PORT  (e.g. http://192.168.1.100:5000)",
                                      text=_get_config("browser_proxy", ""))

    def _on_proxy_entered(self, value):
        if value is None:
            return
        v = value.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            self.session.open(MessageBox, "Format: http://IP:PORT\nExample: http://192.168.1.100:5000",
                              MessageBox.TYPE_WARNING, timeout=6)
            return
        _set_config("browser_proxy", v)
        try:
            from extractors.base import set_browser_proxy
            set_browser_proxy(v)
        except Exception:
            pass
        self._refresh()

    def _edit_tmdb_key(self):
        self.session.openWithCallback(self._on_tmdb_key_entered, VirtualKeyBoard,
                                      title="TMDb API Key (32 characters — from themoviedb.org)",
                                      text=_get_config("tmdb_api_key", ""))

    def _on_tmdb_key_entered(self, value):
        if value is None:
            return
        _set_config("tmdb_api_key", value.strip())
        self._refresh()

    def _clear_tmdb_key(self):
        _set_config("tmdb_api_key", "")
        self._refresh()

    def _edit_torrserver_url(self):
        self.session.openWithCallback(self._on_torrserver_url_entered, VirtualKeyBoard,
                                      title="TorrServer: http://IP:PORT  (e.g. http://127.0.0.1:8090)",
                                      text=_get_config("torrserver_url", ""))

    def _on_torrserver_url_entered(self, value):
        if value is None:
            return
        _set_config("torrserver_url", value.strip())
        self._refresh()

    def _openMaxQuality(self):
        current = str(_get_config("max_quality", "auto") or "auto").strip().lower()
        self._choose("Max Stream Quality", "max_quality", current, [
            ("Auto (best available)", "auto"),
            ("480p — saves the most data", "480p"),
            ("720p — balanced (recommended)", "720p"),
            ("1080p — best quality", "1080p"),
        ])

    def _openNextDelay(self):
        self._choose("Next-Episode Delay", "next_episode_delay", _int("next_episode_delay", 10), [
            ("5 seconds", 5), ("10 seconds (default)", 10), ("15 seconds", 15),
            ("20 seconds", 20), ("30 seconds", 30), ("60 seconds", 60),
        ])

    # kept for callers of the old screen
    def _open_tools_menu(self):
        self._jumpTools()
