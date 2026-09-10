# -*- coding: utf-8 -*-
"""NovaPlay — settings screen + tools & diagnostics menu.
Includes the showMenu/mainMenu MENU-key fix and the Subtitle Settings
entry (both were missing from the monolith's live version)."""

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel

from plugin_common import PLUGIN_PATH, _PLUGIN_VERSION
from plugin_gridlist import scale_skin_xml
from plugin_state import _get_config, _set_config, _PLUGIN_OWNER, _favorite_items, _history_items
from plugin_util import _site_label, _site_tagline
from novaplay_diagnostics import NovaDiagnosticsScreen
from install_dependencies import open_installer_with_restart, open_restart_prompt


class AdvancedArabicPlayerSettings(Screen):
    skin = scale_skin_xml("""
    <screen name="AdvancedArabicPlayerSettings" position="center,center" size="1920,1080" flags="wfNoBorder">
        <ePixmap position="0,0" size="1920,1080" pixmap="{}/images/bg_settings.png" zPosition="0" alphatest="blend" />
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
    """.format(PLUGIN_PATH))

    def __init__(self, session, current_site):
        Screen.__init__(self, session)
        self._current_site = current_site
        self["bg"] = Label("")
        self["title"] = Label("الإعدادات وحول النسخة")
        self["owner"] = Label("")
        self["site"] = Label("")
        self["body_box"] = Label("")
        self["body"] = ScrollLabel("")
        self["hint"] = Label("OK/Back للإغلاق  |  أحمر: Proxy  |  أخضر: الكبار  |  أصفر: TMDb  |  أزرق: TorrServer  |  Menu: الأدوات والتشخيص")
        self["key_red_label"] = Label("تعيين Proxy")
        self["key_green_label"] = Label("عرض محتوى الكبار")
        self["key_yellow_label"] = Label("تعديل مفتاح TMDb")
        self["key_blue_label"] = Label("تعديل TorrServer URL")
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
                # MENU → tools on every image: "showMenu" is what
                # InfobarMenuActions actually delivers on this keymap;
                # "menu" (MenuActions) and "mainMenu" (ButtonSetupActions,
                # openPLi/DreamOS) cover the rest.
                "menu": self._open_tools_menu,
                "showMenu": self._open_tools_menu,
                "mainMenu": self._open_tools_menu,
            }, -1)
        self._refresh()

    def _toggleAdult(self):
        current = _get_config("show_adult", "false")
        new_val = "true" if current == "false" else "false"
        _set_config("show_adult", new_val)
        msg = "تم تفعيل محتوى الكبار" if new_val == "true" else "تم إخفاء محتوى الكبار"
        self.session.open(MessageBox, msg, MessageBox.TYPE_INFO, timeout=3)
        self._refresh()

    def _refresh(self):
        self["owner"].setText("المالك: {}".format(_get_config("owner", _PLUGIN_OWNER)))
        self["site"].setText("المصدر الحالي: {}  |  {}".format(_site_label(self._current_site), _site_tagline(self._current_site)))
        api_key = (_get_config("tmdb_api_key", "") or "").strip()
        ts_url = (_get_config("torrserver_url", "") or "").strip()
        proxy = (_get_config("browser_proxy", "") or "").strip()
        layout = _get_config("layout_style", "carousel")
        adult = _get_config("show_adult", "false")
        self["key_green_label"].setText("تعطيل محتوى الكبار" if adult == "true" else "تفعيل محتوى الكبار")
        body = (
            "NovaPlay Media Center v{version}\n\n"
            "واجهة العرض: {layout_style}\n"
            "محتوى الكبار: {adult_status}\n\n"
            "TMDb:\n• الحالة: {tmdb_status}\n• المفتاح: {tmdb_key}\n\n"
            "TorrServer:\n• الحالة: {ts_status}\n• العنوان: {ts_url}\n\n"
            "Browser Proxy:\n• الحالة: {proxy_status}\n• العنوان: {proxy_addr}\n\n"
            "المكتبة:\n• المفضلة: {fav_count}\n• السجل: {hist_count}\n\n"
            "طريقة الاستخدام:\n• أخضر: تبديل عرض محتوى الكبار\n"
            "• أحمر: Proxy\n• أصفر: TMDb API Key\n• أزرق: TorrServer URL\n"
            "• Menu: الأدوات والتشخيص (ترجمة، تشخيص، تثبيت، إعادة تشغيل)\n"
            "• Info: حذف مفتاح TMDb"
        ).format(
            version=_PLUGIN_VERSION,
            layout_style=layout.upper(),
            adult_status="مفعل (ظهر)" if adult == "true" else "مخفي (آمن للعائلة)",
            tmdb_status="مفعل" if api_key else "غير مفعل",
            tmdb_key=("********" + api_key[-4:]) if api_key else "غير مضبوط",
            ts_status="مفعل" if ts_url else "غير مفعل",
            ts_url=ts_url or "غير مضبوط",
            proxy_status="مفعل" if proxy else "غير مفعل",
            proxy_addr=proxy or "غير مضبوط",
            fav_count=len(_favorite_items()),
            hist_count=len(_history_items()),
        )
        self["body"].setText(body)

    def _edit_proxy(self):
        self.session.openWithCallback(self._on_proxy_entered, VirtualKeyBoard, title="Proxy: http://IP:PORT  (مثال: http://192.168.1.100:5000)", text=_get_config("browser_proxy", ""))
    def _on_proxy_entered(self, value):
        if value is None: return
        v = value.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            self.session.open(MessageBox, "الصيغة: http://IP:PORT\nمثال: http://192.168.1.100:5000", MessageBox.TYPE_WARNING, timeout=6)
            return
        _set_config("browser_proxy", v)
        try:
            from extractors.base import set_browser_proxy
            set_browser_proxy(v)          # same validated value that was saved
        except Exception: pass
        self._refresh()

    def _edit_tmdb_key(self):
        self.session.openWithCallback(self._on_tmdb_key_entered, VirtualKeyBoard, title="TMDb API Key (32 حرف — من themoviedb.org)", text=_get_config("tmdb_api_key", ""))
    def _on_tmdb_key_entered(self, value):
        if value is None: return
        _set_config("tmdb_api_key", value.strip())
        self._refresh()

    def _clear_tmdb_key(self):
        _set_config("tmdb_api_key", "")
        self._refresh()

    def _edit_torrserver_url(self):
        self.session.openWithCallback(self._on_torrserver_url_entered, VirtualKeyBoard, title="TorrServer: http://IP:PORT  (مثال: http://127.0.0.1:8090)", text=_get_config("torrserver_url", ""))
    def _on_torrserver_url_entered(self, value):
        if value is None: return
        _set_config("torrserver_url", value.strip())
        self._refresh()

    def _open_tools_menu(self):
        """Tools & diagnostics: diagnostics, dependency install,
        subtitle settings (API keys / folder / auto-attach / cache),
        Enigma2 restart."""
        choices = [
            ("🛠️ System Diagnostics", "diagnostics"),
            ("📦 Install Missing Dependencies", "install_deps"),
            ("🎬 Subtitle Settings — ترجمة", "subtitles"),
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
        elif action == "install_deps":
            open_installer_with_restart(self.session)
        elif action == "subtitles":
            from novaplay_subtitles import NovaSubtitleSettings
            self.session.open(NovaSubtitleSettings)
        elif action == "restart":
            open_restart_prompt(self.session)