# -*- coding: utf-8 -*-
"""NovaPlay — settings screen + tools & diagnostics menu.

Adds (this revision):
  * [A2] "📥 Downloads Manager" entry in the tools menu, opening
    the new NovaPlayDownloads screen.
  * [A3] "⏭️ Next-Episode Delay" entry — a ChoiceBox for the seconds
    the auto-next card counts down before auto-starting.
"""

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel

from plugin_common import _PLUGIN_VERSION
from plugin_gridlist import scale_skin_xml
from plugin_state import _get_config, _set_config, _PLUGIN_OWNER, _favorite_items, _history_items, _DEFAULT_TMDB_API_KEY
from plugin_util import _site_label, _site_tagline
from novaplay_diagnostics import NovaDiagnosticsScreen
from install_dependencies import open_installer_with_restart, open_restart_prompt
from plugin_screen_downloads import NovaPlayDownloads


class NovaPlaySettings(Screen):
    skin = scale_skin_xml("""
    <screen name="NovaPlaySettings" position="center,center" size="1920,1080" flags="wfNoBorder">
        <widget name="bg" position="0,0" size="1920,1080" backgroundColor="#0D1117" zPosition="1" />
        <widget name="title" position="60,30" size="900,57" font="Regular;45" foregroundColor="#00E5FF" transparent="1" zPosition="3" />
        <widget name="owner" position="60,96" size="600,36" font="Regular;27" foregroundColor="#FFD740" transparent="1" zPosition="3" />
        <widget name="site" position="60,138" size="1800,36" font="Regular;24" foregroundColor="#8B949E" transparent="1" zPosition="3" />
        <widget name="body_box" position="60,195" size="1800,720" backgroundColor="#161B22" zPosition="2" />
        <widget name="body" position="90,218" size="1740,675" font="Regular;28" foregroundColor="#F0F6FC" transparent="1" zPosition="3" />
        <widget name="hint" position="60,939" size="1800,36" font="Regular;22" foregroundColor="#8B949E" transparent="1" zPosition="3" halign="center" />
        <widget name="key_red_label" position="60,987" size="300,36" font="Regular;24" foregroundColor="#FF6B6B" transparent="1" zPosition="3" halign="center" />
        <widget name="key_green_label" position="450,987" size="450,36" font="Regular;24" foregroundColor="#39D98A" transparent="1" zPosition="3" halign="center" />
        <widget name="key_yellow_label" position="990,987" size="450,36" font="Regular;24" foregroundColor="#FFD740" transparent="1" zPosition="3" halign="center" />
        <widget name="key_blue_label" position="1500,987" size="360,36" font="Regular;24" foregroundColor="#58A6FF" transparent="1" zPosition="3" halign="center" />
    </screen>
    """)

    def __init__(self, session, current_site):
        Screen.__init__(self, session)
        self._current_site = current_site
        self["bg"] = Label("")
        self["title"] = Label("Settings & About")
        self["owner"] = Label("")
        self["site"] = Label("")
        self["body_box"] = Label("")
        self["body"] = ScrollLabel("")
        self["hint"] = Label("OK/Back close  |  Red: Proxy  |  Green: Adult  |  Yellow: TMDb  |  Blue: TorrServer  |  Menu: Tools & diagnostics")
        self["key_red_label"] = Label("Set Proxy")
        self["key_green_label"] = Label("Show adult content")
        self["key_yellow_label"] = Label("Edit TMDb key")
        self["key_blue_label"] = Label("Edit TorrServer URL")
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions",
             "InfobarEPGActions", "InfobarMenuActions", "MenuActions",
             "ButtonSetupActions"], {
                "ok": self.close,
                "cancel": self.close,
                "up": self["body"].pageUp,
                "down": self["body"].pageDown,
                "left": self["body"].pageUp,
                "right": self["body"].pageDown,
                "red": self._edit_proxy,
                "green": self._toggleAdult,
                "yellow": self._edit_tmdb_key,
                "blue": self._edit_torrserver_url,
                "showEventInfo": self._clear_tmdb_key,
                "menu": self._open_tools_menu,
                "showMenu": self._open_tools_menu,
                "mainMenu": self._open_tools_menu,
            }, -1)
        self._refresh()

    def _toggleAdult(self):
        current = _get_config("show_adult", "false")
        new_val = "true" if current == "false" else "false"
        _set_config("show_adult", new_val)
        msg = "Adult content enabled" if new_val == "true" else "Adult content hidden"
        self.session.open(MessageBox, msg, MessageBox.TYPE_INFO, timeout=3)
        self._refresh()

    def _refresh(self):
        self["owner"].setText("Owner: {}".format(_get_config("owner", _PLUGIN_OWNER)))
        self["site"].setText("Current source: {}  |  {}".format(_site_label(self._current_site), _site_tagline(self._current_site)))
        api_key = (_get_config("tmdb_api_key", "") or "").strip()
        ts_url = (_get_config("torrserver_url", "") or "").strip()
        proxy = (_get_config("browser_proxy", "") or "").strip()
        layout = _get_config("layout_style", "carousel")
        adult = _get_config("show_adult", "false")
        self["key_green_label"].setText("Disable adult content" if adult == "true" else "Enable adult content")
        maxq = str(_get_config("max_quality", "auto") or "auto").strip().upper()
        if maxq not in ("AUTO", "480P", "720P", "1080P"):
            maxq = "AUTO"
        try:
            _nxdelay = int(_get_config("next_episode_delay", "10") or 10)
        except Exception:
            _nxdelay = 10
        body = (
            "NovaPlay Media Center v{version}\n\n"
            "Layout: {layout_style}\n"
            "Adult content: {adult_status}\n"
            "Max stream quality: {maxq}\n"
            "Next-episode countdown: {nxdelay} seconds\n\n"
            "TMDb:\n• Status: {tmdb_status}\n• Key: {tmdb_key}\n\n"
            "TorrServer:\n• Status: {ts_status}\n• Address: {ts_url}\n\n"
            "Browser Proxy:\n• Status: {proxy_status}\n• Address: {proxy_addr}\n\n"
            "Library:\n• Favorites: {fav_count}\n• History: {hist_count}\n\n"
            "How to use:\n• Green: toggle adult content\n"
            "• Red: Proxy\n• Yellow: TMDb API Key\n• Blue: TorrServer URL\n"
            "• Menu: Tools & diagnostics (subtitles, diagnostics, install, restart, downloads)\n"
            "• Info: delete your key and use the built-in one"
        ).format(
            version=_PLUGIN_VERSION,
            layout_style=layout.upper(),
            adult_status="On (shown)" if adult == "true" else "Hidden (family safe)",
            maxq=maxq,
            nxdelay=_nxdelay,
            tmdb_status="On" if api_key else "Off",
            # [FIX] after "clear" (Info) the plugin falls back to its built-in key;
            # say so, instead of showing a key as if clearing did nothing
            tmdb_key=("Built-in key (default)" if api_key == _DEFAULT_TMDB_API_KEY
                      else ("********" + api_key[-4:]) if api_key else "Not set"),
            ts_status="On" if ts_url else "Off",
            ts_url=ts_url or "Not set",
            proxy_status="On" if proxy else "Off",
            proxy_addr=proxy or "Not set",
            fav_count=len(_favorite_items()),
            hist_count=len(_history_items()),
        )
        self["body"].setText(body)

    def _edit_proxy(self):
        self.session.openWithCallback(self._on_proxy_entered, VirtualKeyBoard, title="Proxy: http://IP:PORT  (e.g. http://192.168.1.100:5000)", text=_get_config("browser_proxy", ""))
    def _on_proxy_entered(self, value):
        if value is None: return
        v = value.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            self.session.open(MessageBox, "Format: http://IP:PORT\nExample: http://192.168.1.100:5000", MessageBox.TYPE_WARNING, timeout=6)
            return
        _set_config("browser_proxy", v)
        try:
            from extractors.base import set_browser_proxy
            set_browser_proxy(v)
        except Exception: pass
        self._refresh()

    def _edit_tmdb_key(self):
        self.session.openWithCallback(self._on_tmdb_key_entered, VirtualKeyBoard, title="TMDb API Key (32 characters — from themoviedb.org)", text=_get_config("tmdb_api_key", ""))
    def _on_tmdb_key_entered(self, value):
        if value is None: return
        _set_config("tmdb_api_key", value.strip())
        self._refresh()

    def _clear_tmdb_key(self):
        _set_config("tmdb_api_key", "")
        self._refresh()

    def _edit_torrserver_url(self):
        self.session.openWithCallback(self._on_torrserver_url_entered, VirtualKeyBoard, title="TorrServer: http://IP:PORT  (e.g. http://127.0.0.1:8090)", text=_get_config("torrserver_url", ""))
    def _on_torrserver_url_entered(self, value):
        if value is None: return
        _set_config("torrserver_url", value.strip())
        self._refresh()

    def _open_tools_menu(self):
        _mq = str(_get_config("max_quality", "auto") or "auto").strip().upper()
        if _mq not in ("AUTO", "480P", "720P", "1080P"):
            _mq = "AUTO"
        try:
            _nxdelay = int(_get_config("next_episode_delay", "10") or 10)
        except Exception:
            _nxdelay = 10
        choices = [
            ("🛠️ System Diagnostics", "diagnostics"),
            ("📥 Downloads Manager", "downloads"),
            ("📦 Install Missing Dependencies", "install_deps"),
            ("🎬 Subtitle Settings", "subtitles"),
            ("🎚️ Max Stream Quality ({})".format(_mq), "maxq"),
            ("⏭️ Next-Episode Delay ({}s)".format(_nxdelay), "nxdelay"),
            ("🔄 Restart Enigma2", "restart"),
        ]
        self.session.openWithCallback(
            self._on_tools_choice,
            ChoiceBox,
            title="Tools & Diagnostics",
            list=choices
        )

    def _on_tools_choice(self, choice):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "diagnostics":
            self.session.open(NovaDiagnosticsScreen)
        elif action == "downloads":
            self.session.open(NovaPlayDownloads)
        elif action == "install_deps":
            open_installer_with_restart(self.session)
        elif action == "subtitles":
            from novaplay_subtitles import NovaSubtitleSettings
            self.session.open(NovaSubtitleSettings)
        elif action == "maxq":
            self._openMaxQuality()
        elif action == "nxdelay":
            self._openNextDelay()
        elif action == "restart":
            open_restart_prompt(self.session)

    def _openMaxQuality(self):
        current = str(_get_config("max_quality", "auto") or "auto").strip().lower()
        options = [
            ("Auto (best available)", "auto"),
            ("480p — saves the most data", "480p"),
            ("720p — balanced (recommended)", "720p"),
            ("1080p — best quality", "1080p"),
        ]
        items = []
        for label, value in options:
            mark = "● " if value == current else "   "
            items.append(("{}{}".format(mark, label), value))

        def _picked(choice):
            if not choice:
                return
            value = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
            _set_config("max_quality", str(value))
            self._refresh()

        self.session.openWithCallback(_picked, ChoiceBox,
                                      title="Max Stream Quality",
                                      list=items)

    def _openNextDelay(self):
        try:
            current = int(_get_config("next_episode_delay", "10") or 10)
        except Exception:
            current = 10
        options = [
            ("5 seconds", 5), ("10 seconds (default)", 10), ("15 seconds", 15),
            ("20 seconds", 20), ("30 seconds", 30), ("60 seconds", 60),
        ]
        items = []
        for label, value in options:
            mark = "● " if value == current else "   "
            items.append(("{}{}".format(mark, label), value))

        def _picked(choice):
            if not choice:
                return
            value = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
            try:
                _set_config("next_episode_delay", str(int(value)))
            except Exception:
                pass
            self._refresh()

        self.session.openWithCallback(_picked, ChoiceBox,
                                      title="Next-Episode Delay",
                                      list=items)