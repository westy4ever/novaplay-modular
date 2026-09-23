# -*- coding: utf-8 -*-
"""NovaPlay — Detail screen (servers / episodes / qualities / chain).
MODULAR EXTRACTION of AdvancedArabicPlayerDetail with:
  * episode chain + auto-server (next-episode support, _playNextEpisode)
  * health hooks on load/extract
  * quality-choices kept populated (variants → Back → servers → grid)
  * watchdog race fix (_done re-check in the timeout callback)
  * OSD poster seeding (P1/P2) for the player
  * magnet prompt + TorrServer + download engine flows
  * [PATCH 46-R2] poster badges: red year box (top-left) + gold-star
    rating box (top-right) — universal TMDB rating source
  * [PATCH 65-B3] per-title quality preference: when a stored quality
    matches an available variant, auto-play it instead of showing the
    quality menu. Saved by plugin_screen_player when the user picks a
    quality from the in-player menu.
"""

import os
import re
import time
import threading
import traceback

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.Console import Console
from Screens.ChoiceBox import ChoiceBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, ePicLoad

from plugin_common import my_log, PLUGIN_PATH, _TYPE_LABELS
from extractors import get_extractor
from extractors.base import get_curl_failed_needs_proxy
from plugin_state import (_get_config, _entry_from_item, _upsert_library_item,
                          _is_favorite, _get_saved_position, _save_position,
                          _toggle_favorite_entry)
from urllib.parse import urlparse
from plugin_util import (_single_line_text, _wrap_ui_text,
                          _strip_arabic_from_english_title,
                          _pick_plot_text, _pick_plot_text_with_source,
                          _poster_cache_path, _normalize_poster_url,
                          _get_cached_poster, _fetch_poster_bytes)
from plugin_tmdb import _tmdb_enabled, _merge_tmdb_data
from plugin_util import _split_episode_label                    # [PATCH 81]
from plugin_screen_player import AdvancedArabicPlayerSimplePlayer, _play
from plugin_widgets import StreamList
import plugin_health
import plugin_imagecache
from novaplay_thread import callInMainThread

_PLUGIN_VERSION = "4.1.0"


# ── [B3] quality-preference key ─────────────────────────────────────
def _quality_pref_key(title):
    """Normalize a title into a config key for its per-title quality
    preference. Series episode titles reduce to the series base so an
    entire show shares one preference."""
    t = str(title or "").strip()
    t = re.sub(r"\bS\d{1,2}E\d{1,2}\b.*$", "", t, flags=re.I)
    t = re.sub(r"\b\d{1,2}x\d{1,2}\b.*$", "", t)
    t = re.sub(r"\s*\[\d+p\]\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -|:")
    t = t.lower()
    t = re.sub(r"[^a-z0-9\u0600-\u06ff]+", "_", t)[:60].strip("_")
    return ("qpref_" + t) if t else ""


class AdvancedArabicPlayerDetail(Screen):
    skin = """
    <screen name="AdvancedArabicPlayerDetail" position="center,center" size="1920,1080" flags="wfNoBorder">
        <widget name="bg" position="0,0" size="1920,1080" backgroundColor="#0D1117" zPosition="1" />

        <widget name="poster_box" position="45,30" size="420,600" backgroundColor="#1C2333" zPosition="2" />
        <widget name="poster" position="68,52" size="375,555" zPosition="4" alphatest="blend" />
        <widget name="posterYear" position="76,56" size="96,36" font="Regular;26" foregroundColor="#F0F6FC" backgroundColor="#C0392B" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="8" />
        <widget name="posterRating" position="325,58" size="104,36" font="Regular;26" foregroundColor="#FFD740" backgroundColor="#000000" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="8" />

        <widget name="info_box" position="495,30" size="1380,405" backgroundColor="#161B22" zPosition="2" />
        <widget name="badge" position="525,52" size="800,33" font="Regular;26" foregroundColor="#E040FB" transparent="1" zPosition="4" />
        <widget name="title" position="525,93" size="1320,90" font="Regular;42" foregroundColor="#00E5FF" transparent="1" zPosition="4" />
        <widget name="meta" position="525,189" size="1320,60" font="Regular;27" foregroundColor="#FFD740" transparent="1" zPosition="4" />
        <widget name="facts" position="525,255" size="1320,42" font="Regular;24" foregroundColor="#8B949E" transparent="1" zPosition="4" />
        <widget name="source" position="525,300" size="1320,80" font="Regular;24" foregroundColor="#58A6FF" transparent="1" zPosition="4" />
        <widget name="proxy_warning" position="1355,52" size="470,33" font="Regular;24" foregroundColor="#FF4444" transparent="1" zPosition="4" halign="right" />

        <widget name="plot_box" position="495,450" size="1380,180" backgroundColor="#1C2333" zPosition="2" />
        <widget name="plot_title" position="525,465" size="600,30" font="Regular;24" foregroundColor="#FFD740" transparent="1" zPosition="4" />
        <widget name="plot" position="525,504" size="1320,150" font="Regular;27" foregroundColor="#F0F6FC" transparent="1" halign="block" valign="top" zPosition="4" />

        <widget name="menu_box" position="45,652" size="1830,390" backgroundColor="#161B22" zPosition="2" />
        <widget name="section" position="75,663" size="1770,36" font="Regular;26" foregroundColor="#FFD740" transparent="1" zPosition="4" />

        <widget name="menu" position="60,708" size="1800,320" zPosition="4"
                scrollbarMode="showOnDemand"
                enableWrapAround="1"
                transparent="1"
                backgroundColor="#161B22"
                backgroundColorSelected="#21262D" />

        <widget name="key_red"    position="45,1042"  size="330,36" font="Regular;24" foregroundColor="#FF6B6B" transparent="1" zPosition="4" />
        <widget name="key_yellow" position="385,1042" size="330,36" font="Regular;24" foregroundColor="#FFD740" transparent="1" zPosition="4" />
        <widget name="key_blue"   position="725,1042" size="330,36" font="Regular;24" foregroundColor="#58A6FF" transparent="1" zPosition="4" />
        <widget name="status"     position="1065,1042" size="795,36" font="Regular;22" foregroundColor="#8B949E" transparent="1" halign="right" zPosition="4" />
    </screen>
    """

    def __init__(self, session, item, site="egydead", m_type="movie",
                 episode_chain=None, episode_index=-1, auto_server_idx=None):
        self.skin = AdvancedArabicPlayerDetail.skin.format(plugin_path=PLUGIN_PATH)
        Screen.__init__(self, session)
        self.session = session
        self._item   = item
        self._site   = site
        self._m_type = m_type
        self._data   = None
        self._servers = []
        self._episodes = []
        self._tmp_posters = []
        self._poster_loaded = False
        self._raw_title = ""
        self._closed = False
        self._proxy_warning_shown = False

        self._extract_lock = threading.Lock()
        self._extract_token = 0
        self._extracting = False
        self._quality_choices = []

        self._episode_chain = list(episode_chain) if episode_chain else []
        self._episode_index = int(episode_index if episode_index is not None else -1)
        self._auto_server_idx = auto_server_idx
        self._last_server_idx = None
        if self._episode_chain and 0 <= self._episode_index < len(self._episode_chain) - 1:
            self._next_episode = self._episode_chain[self._episode_index + 1]
        else:
            self._next_episode = None
        self._auto_menu_timer = None
        self._osd_poster = (item.get("poster") or item.get("image") or "")

        self["bg"]     = Label("")
        self["poster_box"] = Label("")
        self["info_box"] = Label("")
        self["plot_box"] = Label("")
        self["menu_box"] = Label("")
        self["poster"] = Pixmap()
        self["posterYear"] = Label("")
        self["posterRating"] = Label("")
        self["badge"]  = Label("")
        self["title"]  = Label(item.get("title", ""))
        self["meta"]   = Label("")
        self["facts"]  = Label("")
        self["source"] = Label("")
        self["proxy_warning"] = Label("")
        self["plot_title"] = Label("القصة")
        self["plot"]   = Label("")
        self["section"] = Label("جاري التحضير...")
        self["menu"]   = StreamList()
        self["key_red"] = Label("المفضلة")
        self["key_yellow"] = Label("تحديث TMDb")
        self["key_blue"] = Label("تحميل")
        self["status"] = Label("جاري تحميل التفاصيل...")

        self._downloads = []
        self._active_download_task = None

        self.picLoad = ePicLoad()
        self.picLoad.PictureData.get().append(self._paintPoster)

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "ok":     self._onOk,
            "cancel": self._onCancel,
            "red":    self._toggleFavorite,
            "yellow": self._refreshTMDb,
            "blue":   self._openDownloads,
            "up":     lambda: self["menu"].up(),
            "down":   lambda: self["menu"].down(),
            "left":   lambda: self["menu"].pageUp(),
            "right":  lambda: self["menu"].pageDown(),
        }, -1)

        self.onLayoutFinish.append(self._load)
        self.onExecBegin.append(self._refreshPoster)

    def _format_server_item(self, s):
        name = s.get("name", "Server")
        quality = (s.get("quality") or "HD").upper()
        lines = name.split('\n')
        release_name = lines[0].strip()
        meta_line = lines[1] if len(lines) > 1 else ""
        parts = re.split(r'[\U00010000-\U0010ffff]', meta_line)
        parts = [p.strip() for p in parts if p.strip()]
        seeders = ""
        size = ""
        source = ""
        if len(parts) > 0:
            if parts[0].isdigit():
                seeders = "Seeders: " + parts[0]
                if len(parts) > 1:
                    size = "Size: " + parts[1]
                if len(parts) > 2:
                    source = parts[2]
            else:
                source = " | ".join(parts)
        seeders_source = " | ".join([x for x in [seeders, source] if x])
        if not size and not seeders_source:
            fallback = re.sub(r'[\U00010000-\U0010ffff]', '', meta_line).strip()
            fallback = re.sub(r'\s+', ' ', fallback)
            seeders_source = fallback
        return (quality, release_name, size, seeders_source)

    def _format_episode_item(self, ep):
        marker, name = _split_episode_label(ep.get("title", "Episode"))   # [PATCH 81]
        return (marker, name, "", "")

    def _onOk(self):
        idx = self["menu"].getCurrentIndex()
        if idx < 0: return

        if self._quality_choices:
            if idx >= len(self._quality_choices): return
            choice = self._quality_choices[idx]
            self._last_quality_selection = choice
            self._onStreamFound(choice["url"], choice["label"], choice["final_ref"], choice["server"])
            return

        data = self._data or {}
        item_type = data.get("type") or self._item.get("type")
        episode_has_servers = (item_type == "episode" and self._servers)

        if episode_has_servers:
            if idx >= len(self._servers): return
            with self._extract_lock:
                if self._extracting: return
                self._extracting = True
                self._extract_token += 1
                token = self._extract_token
            server = self._servers[idx]
            if server.get("url", "").startswith("magnet:"):
                with self._extract_lock:
                    self._extracting = False
                self._promptMagnetAction(server)
                return
            self["status"].setText("Extracting stream...")
            self["status"].show()
            threading.Thread(target=self._bgExtract, args=(server, token), daemon=True).start()
        elif self._episodes:
            if idx >= len(self._episodes): return
            ep = self._episodes[idx]
            self.session.open(AdvancedArabicPlayerDetail, ep, self._site, ep.get("type", "episode"),
                              episode_chain=self._episodes, episode_index=idx)
        elif self._servers:
            if idx >= len(self._servers): return
            with self._extract_lock:
                if self._extracting: return
                self._extracting = True
                self._extract_token += 1
                token = self._extract_token
            server = self._servers[idx]
            if server.get("url", "").startswith("magnet:"):
                with self._extract_lock:
                    self._extracting = False
                self._promptMagnetAction(server)
                return
            self["status"].setText("Extracting stream...")
            self["status"].show()
            threading.Thread(target=self._bgExtract, args=(server, token), daemon=True).start()

    def _promptMagnetAction(self, server):
        magnet_link = server.get("url", "")
        title = server.get("name", "Magnet Stream")
        choices = [
            ("Play (TorrServer Stream)", "play"),
            ("Download to HDD (Transmission)", "download")
        ]
        self.session.openWithCallback(
            lambda choice: self._onMagnetActionChosen(choice, server, magnet_link, title),
            ChoiceBox,
            title="Choose action for Magnet link",
            list=choices
        )

    def _onMagnetActionChosen(self, choice, server, magnet_link, title):
        if not choice: return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "play":
            with self._extract_lock:
                if self._extracting: return
                self._extracting = True
                self._extract_token += 1
                token = self._extract_token
            self["status"].setText("TorrServer: Starting Stream...")
            self["status"].show()
            threading.Thread(target=self._bgTorrServerMagnet, args=(magnet_link, server, token), daemon=True).start()
        elif action == "download":
            self._initiateTransmissionDownload(magnet_link, title)

    def _initiateTransmissionDownload(self, magnet_link, title):
        try:
            try:
                from Plugins.Extensions.Transmission.torrmgr import config as tconfig
                download_path = '%s/transmission' % tconfig.plugins.torreplayer.poster_path.value
            except:
                download_path = '/media/hdd/transmission'
            rpc_url = 'http://127.0.0.1:9091/transmission/rpc'
            command = 'transmission-remote %s -a "%s" -w "%s" -d 1000000 -u 100' % (rpc_url, magnet_link, download_path)
            self["status"].setText("Sending to Transmission...")
            self.session.open(Console, 'Downloading: %s' % title[:30], [command])
        except Exception as e:
            my_log("Transmission download error: {}".format(e))
            self.session.open(MessageBox, "Failed to send to Transmission.\nIs it installed and running?", MessageBox.TYPE_ERROR, timeout=5)

    def _load(self):
        item_snapshot = self._item
        threading.Thread(target=self._bgLoad, args=(self._site, item_snapshot, self._m_type), daemon=True).start()

    def _bgLoad(self, site, item, m_type):
        url = item["url"]
        _done = [False]
        def _watchdog():
            if not _done[0] and not getattr(self, "_closed", False):
                my_log("_bgLoad watchdog: timeout for {}".format(url[:60]))
                def _show_timeout_msg():
                    if not getattr(self, "_closed", False) and not _done[0]:
                        self.session.open(MessageBox, "Timeout — please try again", MessageBox.TYPE_ERROR, timeout=5)
                callInMainThread(_show_timeout_msg)
        _wt = threading.Timer(30, _watchdog)
        _wt.daemon = True
        _wt.start()
        try:
            extractor = _get_extractor(site)
            get_page = getattr(extractor, "get_page", None)
            if not get_page:
                if not getattr(self, "_closed", False):
                    callInMainThread(self["status"].setText, u"لا توجد بيانات")
                return
            if site in ["egydead", "egydead_coupons", "akwam", "akwams", "wecima"]:
                data = get_page(url, m_type=m_type)
            else:
                data = get_page(url)
            merged_seed = dict(item or {})
            merged_seed.update(data or {})
            data = _merge_tmdb_data(merged_seed)
            _done[0] = True
            plugin_health.record(site, "ok")
            if getattr(self, "_closed", False): return
            callInMainThread(self._onLoaded, data)
        except Exception as e:
            _done[0] = True
            my_log("_bgLoad error: {} -- trying TMDb fallback".format(e))
            plugin_health.record(site, "blocked" if get_curl_failed_needs_proxy() else "down", str(e))
            if getattr(self, "_closed", False): return
            try:
                fallback = _merge_tmdb_data(dict(item or {}))
                if getattr(self, "_closed", False): return
                if fallback and (fallback.get("plot") or fallback.get("poster")):
                    callInMainThread(self._onLoaded, fallback)
                else:
                    callInMainThread(self["status"].setText, u"فشل التحميل — {}".format(str(e)[:40]))
            except Exception as e2:
                my_log("TMDb fallback failed: {}".format(e2))
                if not getattr(self, "_closed", False):
                    callInMainThread(self["status"].setText, u"فشل التحميل — {}".format(str(e)[:40]))
        finally:
            _wt.cancel()

    def _onCancel(self):
        if self._quality_choices:
            self._quality_choices = []
            self["section"].setText(_single_line_text("السيرفرات المتاحة: {}  |  اختر الجودة أو السيرفر".format(len(self._servers)), width=90))
            items = [self._format_server_item(s) for s in self._servers]
            self["menu"].setList(items)
            self["status"].setText(self._status_hint("اختار سيرفر — OK"))
            return
        self._closed = True
        self._proxy_warning_shown = False
        try:
            self.picLoad.PictureData.get().remove(self._paintPoster)
        except Exception: pass
        for p in self._tmp_posters:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception: pass
        self.close()

    def _paintPoster(self, picData=None):
        ptr = self.picLoad.getData()
        if ptr:
            self["poster"].instance.setPixmap(ptr)
            self["poster"].show()
            self._poster_loaded = True
        else:
            my_log("_paintPoster (detail): native decode returned empty picture data")

    def _onLoaded(self, data):
        if getattr(self, "_closed", False): return
        if not data:
            self["status"].setText("تعذر تحميل الصفحة")
            return

        self._data = data
        self._quality_choices = []
        current_title = _strip_arabic_from_english_title(data.get("title") or self._item.get("title", ""))
        self._raw_title = re.sub(r"\s+", " ", current_title).strip()
        self["title"].setText(_wrap_ui_text(current_title, width=30, max_lines=2, fallback="بدون عنوان"))

        meta = []
        if data.get("year"):    meta.append(data["year"])
        if data.get("rating"):  meta.append("{}/10".format(data["rating"]))
        if data.get("type"):    meta.append(_TYPE_LABELS.get(data["type"], "عنصر"))
        if data.get("quality"): meta.append(data["quality"])          # [PATCH 111] print type e.g. HDCAM
        if data.get("genres"):  meta.append(data["genres"])
        self["meta"].setText(_wrap_ui_text("   ".join(meta), width=58, max_lines=2))
        self["badge"].setText("{}  •  {}".format(_site_label(self._site), _TYPE_LABELS.get(data.get("type"), "عنصر")))
        facts = [
            "المفضلة: {}  |  النسخة: {}  |  الوصف: {}".format(
                "محفوظ" if _is_favorite(self._item.get("url")) else "غير محفوظ",
                _PLUGIN_VERSION,
                "موجود" if _pick_plot_text(data, self._item) != "القصة غير متوفرة حالياً لهذا العنصر." else "غير متوفر"
            ),
        ]
        self["facts"].setText(_single_line_text("".join(facts), width=62))
        counts = []
        _nav_items = [e for e in data.get("items", []) if e.get("type") in ("episode", "series", "season")]
        has_episodes = bool(_nav_items)
        has_servers = bool([s for s in data.get("servers", []) if s.get("url")])
        is_series_item = (
            data.get("type") in ("series", "show")
            or self._item.get("type") in ("series", "show")
            or has_episodes
        )
        if has_episodes:
            _nav_label = "المواسم" if all(e.get("type") in ("series", "season") for e in _nav_items) else "الحلقات"
            counts.append("{}: {}".format(_nav_label, len(_nav_items)))
        else:
            counts.append("السيرفرات: {}".format(len([s for s in data.get("servers", []) if s.get("url")])))
        if data.get("year"):
            counts.append("السنة: {}".format(data.get("year")))
        if data.get("country"):                                       # [PATCH 111]
            counts.append("البلد: {}".format(data.get("country")))
        if data.get("runtime"):
            counts.append("المدة: {}".format(data.get("runtime")))
        if data.get("channel"):
            counts.append("القناة: {}".format(data.get("channel")))
        self["source"].setText(_wrap_ui_text("المصدر: {}  |  {}".format(_site_label(self._site), "  |  ".join(counts)), width=58, max_lines=2))
        if has_episodes:
            plot_label = "قصة المسلسل"
        elif has_servers:
            plot_label = "قصة الفيلم"
        elif is_series_item:
            plot_label = "قصة المسلسل"
        else:
            plot_label = "قصة الفيلم"
        if current_title:
            plot_label = "{}: {}".format(plot_label, current_title[:32])
        self["plot_title"].setText(_single_line_text(plot_label, width=46, fallback="القصة"))

        plot_text, plot_source = _pick_plot_text_with_source(data, self._item)
        plot_text = re.sub(r"^\[.*?\]\s*|^المصدر:\s*.*?\|\s*", "", plot_text)
        _MID_SITES = ("EgyDead", "Wecima", "Akoam", "ArabSeed", "TopCinema", "TopCinemaa", "FaselHD", "Shaheed", "Shaheed4u")
        for _ms in _MID_SITES:
            plot_text = re.sub(r"\s*[|\-]\s*" + re.escape(_ms) + r"[^\u0600-\u06ff\n]{0,25}", " ", plot_text, flags=re.I)
            plot_text = re.sub(r"\u0639\u0644\u0649\s+\u0645\u0648\u0642\u0639\s+" + re.escape(_ms) + r"[^\u0600-\u06ff\n]{0,30}", " ", plot_text, flags=re.I)
        plot_text = re.sub(r"  +", " ", plot_text).strip()
        my_log("Detail plot source: {} | len={}".format(plot_source, len(plot_text)))

        _pt = (plot_text or "").strip()
        if len(_pt) > 500:
            _pt = _pt[:500].rsplit(" ", 1)[0] + "…"
        _ar_count = sum(1 for _c in _pt[:80] if "\u0600" <= _c <= "\u06ff")
        if _ar_count > int(len(_pt[:80]) * 0.3):
            _pt = "\u200f" + _pt
        self["plot"].setText(_pt)

        self._servers = _sort_servers([s for s in data.get("servers", []) if s.get("url")])
        self._episodes = [e for e in data.get("items", []) if e.get("type") in ("episode", "series", "season")]
        _chain_poster = data.get("poster") or self._item.get("poster") or ""
        if _chain_poster:
            for _ep in self._episodes:
                if not _ep.get("poster"):
                    _ep["poster"] = _chain_poster

        my_log("Detail _onLoaded: servers={}, items={}".format(len(self._servers), len(self._episodes)))

        item_type = data.get("type") or self._item.get("type")
        episode_has_servers = (item_type == "episode" and self._servers)

        if episode_has_servers:
            self["section"].setText(_single_line_text("السيرفرات المتاحة: {}  |  اختر الجودة أو السيرفر".format(len(self._servers)), width=90))
            items = [self._format_server_item(s) for s in self._servers]
            self["menu"].setList(items)
            self["status"].setText(self._status_hint("اختار سيرفر — OK"))
        elif self._episodes:
            _all_seasons = all(e.get("type") in ("series", "season") for e in self._episodes)
            _list_label = "المواسم المتاحة" if _all_seasons else "الحلقات المتاحة"
            _pick_hint = "اختار الموسم المطلوب" if _all_seasons else "اختار الحلقة المطلوبة"
            _ok_hint = "اختار موسم — OK" if _all_seasons else "اختار حلقة — OK"
            self["section"].setText(_single_line_text("{}: {}  |  {}".format(_list_label, len(self._episodes), _pick_hint), width=90))
            items = [self._format_episode_item(ep) for ep in self._episodes]
            self["menu"].setList(items)
            self["status"].setText(self._status_hint(_ok_hint))
        elif self._servers:
            self["section"].setText(_single_line_text("السيرفرات المتاحة: {}  |  اختر الجودة أو السيرفر".format(len(self._servers)), width=90))
            items = [self._format_server_item(s) for s in self._servers]
            self["menu"].setList(items)
            self["status"].setText(self._status_hint("اختار سيرفر — OK"))
        elif is_series_item:
            self["section"].setText("الحلقات المتاحة: 0")
            self["menu"].setList([("لا توجد حلقات متاحة", "", "", "")])
            self["status"].setText("لا توجد حلقات")
        else:
            self["section"].setText("السيرفرات المتاحة: 0")
            self["menu"].setList([("لا توجد سيرفرات متاحة", "", "", "")])
            self["status"].setText("لا توجد سيرفرات")

        self._downloads = data.get("downloads") or []
        if self._downloads:
            self["key_blue"].setText("تحميل ({})".format(len(self._downloads)))
        else:
            self["key_blue"].setText("")

        try:
            _r = str(data.get("rating") or self._item.get("rating") or "").strip()
            if _r and float(_r) > 0:
                self["posterRating"].setText(u"★ " + _r[:3])
                self["posterRating"].show()
            else:
                self["posterRating"].hide()
        except Exception:
            try: self["posterRating"].hide()
            except Exception: pass
        try:
            _y = str(data.get("year") or self._item.get("year") or "").strip()[:4]
            if _y:
                self["posterYear"].setText(_y)
                self["posterYear"].show()
            else:
                self["posterYear"].hide()
        except Exception:
            try: self["posterYear"].hide()
            except Exception: pass

        poster_url = data.get("poster") or self._item.get("poster", "")
        if poster_url:
            threading.Thread(target=self._downloadPoster, args=(poster_url,), daemon=True).start()
            self._osd_poster = poster_url
        if getattr(self, "_auto_server_idx", None) is not None and self._servers:
            _asi = self._auto_server_idx
            self._auto_server_idx = None
            if 0 <= _asi < len(self._servers):
                try:
                    self._auto_menu_timer = eTimer()
                    self._auto_menu_timer.callback.append(lambda i=_asi: self._autoStartServer(i))
                    self._auto_menu_timer.start(1500, True)
                except Exception:
                    pass

    def _status_hint(self, prefix):
        fav_state = "محفوظ" if _is_favorite(self._item.get("url")) else "غير محفوظ"
        tmdb_state = "TMDb مفعل" if _tmdb_enabled() else "TMDb غير مفعل"
        return "{}  |  {}  |  {}".format(prefix, fav_state, tmdb_state)

    def _refreshPoster(self):
        if getattr(self, "_poster_loaded", False):
            try:
                self["poster"].show()
            except Exception: pass
            return
        poster_url = None
        if self._data and self._data.get("poster"):
            poster_url = self._data["poster"]
        elif self._item.get("poster"):
            poster_url = self._item["poster"]
        if poster_url:
            threading.Thread(target=self._downloadPoster, args=(poster_url,), daemon=True).start()
        else:
            ph = placeholder_for_item(self._item)
            if ph:
                def _paint_ph():
                    try:
                        self["poster"].instance.setScale(1)
                        self["poster"].instance.setPixmapFromFile(ph)
                        self["poster"].show()
                    except Exception:
                        self["poster"].hide()
                callInMainThread(_paint_ph)
            else:
                callInMainThread(self["poster"].hide)

    def _downloadPoster(self, url):
        try:
            if not url: return
            url = _normalize_poster_url(url)
            import urllib.request as _ur
            try:
                w = self["poster"].instance.size().width()
                h = self["poster"].instance.size().height()
            except Exception:
                w, h = 375, 555
            cached = _get_cached_poster(url)
            if cached:
                my_log("_downloadPoster (detail): using cached file for {}".format(url))
                callInMainThread(self.picLoad.setPara, (w, h, 1, 1, 0, 1, "#000000"))
                callInMainThread(self.picLoad.startDecode, cached)
                return
            cache_path = _poster_cache_path(url)
            from urllib.parse import urlparse as _urlparse
            _p = _urlparse(url)
            referer = "{}://{}/".format(_p.scheme, _p.netloc)
            my_log("_downloadPoster (detail): fetching {}".format(url))
            data = _fetch_poster_bytes(url, referer, timeout=10)
            my_log("_downloadPoster (detail): downloaded {} bytes for {}".format(len(data) if data else 0, url))
            save_path = cache_path or "/tmp/ap_detail_{}.jpg".format(int(time.time()))
            with open(save_path, "wb") as f:
                f.write(data)
            if not cache_path:
                self._tmp_posters.append(save_path)
            my_log("_downloadPoster (detail): saved to {}, handing to picLoad".format(save_path))
            callInMainThread(self.picLoad.setPara, (w, h, 1, 1, 0, 1, "#000000"))
            callInMainThread(self.picLoad.startDecode, save_path)
        except Exception as e:
            my_log("_downloadPoster error: {} (URL: {})".format(e, url))

    def _toggleFavorite(self):
        base = self._data or self._item
        entry = _entry_from_item(
            dict(self._item, **(base or {})),
            self._site,
            self._m_type,
            {"type": (base or {}).get("type", self._item.get("type", self._m_type))}
        )
        added = _toggle_favorite_entry(entry)
        self["status"].setText("تمت الإضافة إلى المفضلة" if added else "تم الحذف من المفضلة")
        if self._data:
            self._onLoaded(self._data)

    def _refreshTMDb(self):
        if not _tmdb_enabled():
            self["status"].setText("أضف TMDb API Key من الإعدادات أولاً")
            return
        self["status"].setText("جاري تحديث البيانات من TMDb...")
        threading.Thread(target=self._bgRefreshTMDb, daemon=True).start()

    def _bgRefreshTMDb(self):
        try:
            merged = _merge_tmdb_data(self._data or self._item)
            callInMainThread(self._onLoaded, merged)
        except Exception as e:
            my_log("TMDb refresh failed: {}".format(e))
            callInMainThread(self["status"].setText, "فشل تحديث TMDb")

    def _bgExtract(self, server, token=None):
        try:
            url = server.get("url", "")
            if url.startswith("magnet:"):
                callInMainThread(self["status"].setText, "TorrServer: Starting Stream...")
                threading.Thread(target=self._bgTorrServerMagnet, args=(url, server, token), daemon=True).start()
                return

            extractor = None
            try:
                extractor = _get_extractor(self._site)
            except Exception:
                extractor = None
            if extractor is None or getattr(extractor, "extract_stream", None) is None:
                from extractors import base as extractor

            from novaplay_proxy import cached_extract, attach_cookies
            try:
                result = cached_extract(extractor, url)
                result = attach_cookies(result)
            except Exception:
                result = extractor.extract_stream(url)

            if len(result) >= 4:
                url, qual, final_ref, variants = result[0], result[1], result[2], result[3]
            else:
                url, qual, final_ref = result[0], result[1], result[2]
                variants = []

            if getattr(self, "_closed", False): return
            if token is not None and token != getattr(self, "_extract_token", token): return

            if url:
                if variants:
                    callInMainThread(self._onQualityChoices, url, qual, final_ref, variants, server)
                else:
                    callInMainThread(self._onStreamFound, url, qual, final_ref, server, variants)
            else:
                if get_curl_failed_needs_proxy():
                    plugin_health.record(self._site, "blocked")
                    if not self._proxy_warning_shown:
                        self._proxy_warning_shown = True
                        msg = "⚠️ curl_cffi فشل في تجاوز Cloudflare.\n"
                        msg += "الرجاء تفعيل الـ Proxy من الإعدادات (أزرق ← أحمر)."
                        callInMainThread(self._showProxyWarningPopup, msg)
                    try:
                        callInMainThread(self["proxy_warning"].setText, "⚠️ تفعيل Proxy")
                    except Exception as e:
                        my_log("proxy_warning widget update failed: {}".format(e))
                    try:
                        callInMainThread(self["status"].setText, "⚠️ Proxy مطلوب — راجع الإعدادات")
                    except Exception as e:
                        my_log("status widget update failed: {}".format(e))
                else:
                    callInMainThread(self["status"].setText, "فشل استخراج الرابط — جرب سيرفر تاني")
        except Exception as e:
            my_log("Detail _bgExtract CRITICAL ERROR: {}: {}\n{}".format(type(e).__name__, e, traceback.format_exc()))
            if not getattr(self, "_closed", False):
                callInMainThread(self["status"].setText, "خطأ في النظام: {}".format(str(e)[:30]))
        finally:
            if token is not None:
                with self._extract_lock:
                    if token == self._extract_token:
                        self._extracting = False

    def _bgTorrServerMagnet(self, magnet_link, server, token=None):
        try:
            import json
            import urllib.request as _ur
            import urllib.error
            from urllib.parse import quote

            ts_url = (_get_config("torrserver_url", "http://127.0.0.1:8090") or "").strip().rstrip("/")
            if not ts_url:
                callInMainThread(self["status"].setText, "TorrServer URL is not set in settings!")
                return

            add_url = ts_url + "/torrents"
            payload = json.dumps({
                "action": "add",
                "link": magnet_link,
                "title": server.get("name", "Magnet Stream"),
                "save_to_db": True
            }).encode("utf-8")
            req = _ur.Request(add_url, data=payload, headers={"Content-Type": "application/json"})
            try:
                with _ur.urlopen(req, timeout=20) as resp:
                    add_resp = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                callInMainThread(self["status"].setText, "TorrServer HTTP Error: {}".format(e.code))
                return

            torrent_hash = add_resp.get("hash")
            if not torrent_hash:
                m = re.search(r'btih:([a-zA-Z0-9]+)', magnet_link)
                if m: torrent_hash = m.group(1)

            if not torrent_hash:
                callInMainThread(self["status"].setText, "TorrServer failed to add torrent.")
                return

            get_url = ts_url + "/torrents"
            get_payload = json.dumps({"action": "get", "hash": torrent_hash}).encode("utf-8")
            file_index = 0
            file_path = "stream"
            video_exts = ('.mpg', '.vob', '.m4v', '.mkv', '.avi', '.divx', '.dat', '.flv', '.mp4', '.mov', '.wmv', '.asf', '.3gp', '.3g2', '.mpeg', '.mpe', '.rm', '.rmvb', '.ogm', '.ogv', '.m2ts', '.mts', '.webm', '.ts')

            for attempt in range(20):
                try:
                    req = _ur.Request(get_url, data=get_payload, headers={"Content-Type": "application/json"})
                    with _ur.urlopen(req, timeout=15) as resp:
                        info = json.loads(resp.read().decode("utf-8"))
                except Exception:
                    info = None

                tor_obj = None
                if isinstance(info, list) and len(info) > 0:
                    tor_obj = info[0]
                elif isinstance(info, dict) and info:
                    tor_obj = info

                if tor_obj:
                    files = tor_obj.get("file_stats") or []
                    if not files:
                        raw_data = tor_obj.get("data")
                        if raw_data:
                            try:
                                parsed_data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                                files = ((parsed_data or {}).get("TorrServer") or {}).get("Files", [])
                            except Exception as parse_err:
                                my_log("TorrServer: failed to parse nested data field: {}".format(parse_err))
                                files = []

                    if files:
                        largest_file = None
                        max_size = 0
                        for f in files:
                            path = (f.get("path") or "").lower()
                            size = f.get("length") or f.get("size") or 0
                            if path.endswith(video_exts):
                                if size > max_size:
                                    max_size = size
                                    largest_file = f

                        if largest_file:
                            file_index = largest_file.get("id", 0)
                            file_path = largest_file.get("path", "stream")
                            break

                callInMainThread(self["status"].setText, "TorrServer: Fetching metadata ({}s)...".format((attempt+1)*2))
                time.sleep(2)
            else:
                callInMainThread(self["status"].setText, "TorrServer: Timeout fetching metadata.")
                return

            encoded_path = quote(file_path, safe='/')
            stream_url = "{}/stream/{}?link={}&index={}&play".format(
                ts_url, encoded_path, torrent_hash, file_index)
            my_log("TorrServer Stream URL: {}".format(stream_url))

            callInMainThread(self._onStreamFound, stream_url, server.get("quality", "HD"), stream_url, server)

        except Exception as e:
            my_log("TorrServer Error: {}".format(e))
            callInMainThread(self["status"].setText, "TorrServer Error: {}".format(str(e)[:30]))
        finally:
            # [PATCH 80] every early return above used to leave _extracting stuck True
            if token is not None:
                with self._extract_lock:
                    if token == self._extract_token:
                        self._extracting = False

    def _showProxyWarningPopup(self, msg):
        self.session.open(MessageBox, msg, MessageBox.TYPE_WARNING, timeout=8)

    def _onQualityChoices(self, url, qual, final_ref, variants, server):
        if getattr(self, "_closed", False): return
        # [B3] if a stored quality preference matches an available variant,
        # auto-play it directly instead of showing the menu
        try:
            _pk = _quality_pref_key(self._raw_title or self._item.get("title", ""))
            if _pk:
                _pl = str(_get_config(_pk, "") or "").strip()
                if _pl:
                    for _lbl, _vurl in (variants or []):
                        if str(_lbl).strip().lower() == _pl.lower():
                            # [PATCH 96] same viability rule as the menu filter below: with a
                            # tokened master, tokenless / cross-host variants are dead links
                            try:
                                _mu0 = urlparse(url)
                                if _mu0.query and not (urlparse(_vurl).query
                                                       and urlparse(_vurl).netloc == _mu0.netloc):
                                    continue
                            except Exception:
                                pass
                            my_log("quality pref: auto-selecting '{}' for key '{}'".format(_pl, _pk))
                            self._onStreamFound(_vurl, _pl, final_ref, server)
                            return
        except Exception as e:
            my_log("quality pref check failed: {}".format(e))

        choices = [{
            "label": qual or "افتراضي",
            "url": url,
            "final_ref": final_ref,
            "server": server,
        }]
        seen_urls = {url}
        try:
            _mu = urlparse(url)
            if _mu.query:
                variants = [(lbl, v) for lbl, v in (variants or [])
                            if urlparse(v).query and urlparse(v).netloc == _mu.netloc]
        except Exception:
            pass
        for lbl, vurl in variants:
            if vurl in seen_urls: continue
            seen_urls.add(vurl)
            choices.append({
                "label": lbl,
                "url": vurl,
                "final_ref": final_ref,
                "server": server,
            })
        self._quality_choices = choices

        quality_labels = []
        for i, c in enumerate(choices):
            label = c["label"]
            if "1080" in label or "Original" in label:
                badge = "🔵"
            elif "720" in label:
                badge = "🟢"
            elif "480" in label:
                badge = "🟡"
            elif "360" in label:
                badge = "⚪"
            else:
                badge = "🟣"
            quality_labels.append(("{}. {} {}".format(i + 1, badge, label), "", ""))

        self["section"].setText(_single_line_text("الجودات المتاحة: {}  |  اختر الجودة المطلوبة".format(len(choices)), width=90))
        self["menu"].setList(quality_labels)
        self["status"].setText(self._status_hint("اختار جودة — OK"))

    def _playNextEpisode(self, next_ep):
        if getattr(self, "_closed", False) or not next_ep:
            return
        self._closed = True
        self.session.open(
            AdvancedArabicPlayerDetail, next_ep, self._site,
            next_ep.get("type", "episode"),
            episode_chain=self._episode_chain,
            episode_index=self._episode_index + 1,
            auto_server_idx=self._last_server_idx)
        try:
            self.picLoad.PictureData.get().remove(self._paintPoster)
        except Exception:
            pass
        for p in self._tmp_posters:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass
        self.close()

    def _autoStartServer(self, idx):
        if getattr(self, "_closed", False) or self._quality_choices:
            return
        if not (0 <= idx < len(self._servers)):
            return
        server = self._servers[idx]
        if server.get("url", "").startswith("magnet:"):
            return
        with self._extract_lock:
            if self._extracting:
                return
            self._extracting = True
            self._extract_token += 1
            token = self._extract_token
        self["status"].setText("جاري تشغيل الحلقة التالية...")
        self["status"].show()
        threading.Thread(target=self._bgExtract, args=(server, token), daemon=True).start()

    def _applyQualityCap(self, url):
        try:
            cap = str(_get_config("max_quality", "auto") or "auto").strip().lower()
            if cap in ("", "auto"):
                return url
            digits = "".join(c for c in cap if c.isdigit())
            cap_h = int(digits) if digits else 0
            if not cap_h:
                return url
            base, frag = (url.split("#", 1) + [""])[:2]
            if ".m3u8" not in base.lower():
                return url
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            if frag:
                for p in frag.split("&"):
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if k not in headers:
                            headers[k] = v
            from extractors.base import fetch
            text, _final = fetch(base, extra_headers=headers)
            if not text or "#EXT-X-STREAM-INF" not in text:
                return url
            from urllib.parse import urljoin
            lines = [l.strip() for l in text.splitlines()]
            best = None
            smallest = None
            i = 0
            while i < len(lines):
                if lines[i].startswith("#EXT-X-STREAM-INF"):
                    j = i + 1
                    while j < len(lines) and not lines[j]:
                        j += 1
                    if j < len(lines) and not lines[j].startswith("#"):
                        uri = lines[j]
                        m = re.search(r"RESOLUTION=(\d+)[xX](\d+)", lines[i])
                        h = int(m.group(2)) if m else 0
                        if h and h <= cap_h and (best is None or h > best[0]):
                            best = (h, uri)
                        if h and (smallest is None or h < smallest[0]):
                            smallest = (h, uri)
                        i = j + 1
                        continue
                i += 1
            pick = best or smallest
            if not pick:
                return url
            variant = urljoin(base, pick[1])
            if not variant or variant == base:
                return url
            my_log("quality cap: {}p master → {}p rendition".format(cap_h, pick[0]))
            return variant + ("#" + frag if frag else "")
        except Exception as e:
            my_log("quality cap error: {} — playing original URL".format(e))
            return url

    def _onStreamFound(self, stream_url, quality, final_ref, server, variants=None):
        if getattr(self, "_closed", False): return
        try:
            if server in self._servers:
                self._last_server_idx = self._servers.index(server)
        except Exception:
            pass
        if not stream_url:
            self["status"].setText("{} — غير متاح، جرب سيرفر آخر".format(server["name"]))
            return
        my_log("Stream found: {} [{}]".format(stream_url, quality))
        history_entry = _entry_from_item(
            dict(self._item, **(self._data or {})),
            self._site,
            self._m_type,
            {
                "server_name": server.get("name", ""),
                "quality": quality or "",
                "last_stream_url": stream_url,
            }
        )
        _upsert_library_item("history", history_entry, limit=120)

        title = getattr(self, "_raw_title", None) or re.sub(r"\s+", " ", self["title"].getText()).strip()

        try:
            from plugin_screen_player import _build_remote_play_candidates
            raw_url = stream_url.strip()
            if "|" in raw_url:
                main_url, old_params = raw_url.split("|", 1)
            else:
                main_url, old_params = raw_url, ""

            lower_main_url = main_url.lower()
            is_media_url = any(marker in lower_main_url for marker in (
                ".m3u8", ".mp4", ".mkv", ".mp3", ".ts", ".avi", "master.txt", "/hls", "/stream", "/playlist"
            ))
            is_embed_page = any(marker in lower_main_url for marker in (
                "/embed-", "/embed/", "/e/", "/watch/"
            ))
            if is_embed_page and not is_media_url:
                self["status"].setText("الرابط صفحة تشغيل وليس ملف فيديو — جرب سيرفر آخر")
                return

            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            if final_ref:
                headers["Referer"] = final_ref
            if old_params:
                for p in old_params.split("&"):
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if k not in headers: headers[k] = v

            header_str = "&".join(["{}={}".format(k, v) for k, v in headers.items()])
            pure_url = main_url.split("|")[0].strip()
            url = pure_url + "#" + header_str if header_str else pure_url
            url = self._applyQualityCap(url)

            if variants is not None:
                _qv = variants or None
                if _qv:
                    my_log("quality: passing {} variant(s) to player".format(len(_qv)))
            else:
                try:
                    from extractors.base import get_last_quality_variants
                    _qv = get_last_quality_variants() or []
                except Exception:
                    _qv = []
                if _qv:
                    my_log("quality: passing {} variant(s) to player".format(len(_qv)))
                else:
                    _qv = None

            _item_url = self._item.get("url", "")
            _saved_pos = _get_saved_position(_item_url)
            if _saved_pos > 30:
                if _saved_pos >= 3600:
                    _hours_r = _saved_pos // 3600
                    _mins_r = (_saved_pos % 3600) // 60
                    _secs_r = _saved_pos % 60
                    resume_text = "Resume from {:02d}:{:02d}:{:02d}?".format(_hours_r, _mins_r, _secs_r)
                else:
                    _mins_r = _saved_pos // 60
                    _secs_r = _saved_pos % 60
                    resume_text = "Resume from {}:{:02d}?".format(_mins_r, _secs_r)

                def _on_resume(_ans, _u=url, _t=title, _iu=_item_url, _sp=_saved_pos,
                               _ne=self._next_episode, _cb=self._playNextEpisode,
                               _po=self._osd_poster, _qv=_qv):
                    if not _ans:
                        _save_position(_iu, 0)
                    _play(self.session, _u, _t, resume_pos=_sp if _ans else 0, item_url=_iu,
                          next_episode=_ne, on_next=_cb, poster_url=_po,
                          quality_variants=_qv)
                self["status"].setText("جاري فتح المشغل...")
                self.session.openWithCallback(_on_resume, MessageBox, resume_text, MessageBox.TYPE_YESNO, timeout=8, default=True)
            else:
                self["status"].setText("Opening player...")
                _play(self.session, url, title, resume_pos=0, item_url=_item_url,
                      next_episode=self._next_episode, on_next=self._playNextEpisode,
                      poster_url=self._osd_poster,
                      quality_variants=_qv)
            from extractors.base import get_proxy_used
            if get_proxy_used():
                self["status"].setText("✓ Proxy  " + self["status"].getText())
                self["status"].show()
                self["proxy_warning"].setText("")
            else:
                self["status"].hide()
        except Exception as e:
            my_log("Error opening player: {}".format(e))
            self["status"].setText("خطأ في المشغل: {}".format(str(e)[:60]))

    def _openDownloads(self):
        if self._active_download_task and self._active_download_task.status == "downloading":
            pct = self._active_download_task.progress_pct()
            msg = "جاري التحميل: {:.0f}%\nإلغاء؟".format(pct) if pct else "جاري التحميل...\nإلغاء؟"
            self.session.openWithCallback(self._onCancelDownloadPrompt, MessageBox,
                                        msg, MessageBox.TYPE_YESNO, timeout=8, default=False)
            return
        if not self._downloads:
            self["status"].setText("لا توجد روابط تحميل متاحة لهذا العنصر")
            return
        entries = []
        for d in self._downloads:
            label = d.get("resolution") or "جودة غير معروفة"
            extra = " | ".join([x for x in (d.get("size"), d.get("quality")) if x])
            if extra:
                label = "{} ({})".format(label, extra)
            entries.append((label, d))
        self.session.openWithCallback(self._onDownloadChoiceMade, ChoiceBox,
                                    title="اختر الجودة للتحميل", list=entries)

    def _onCancelDownloadPrompt(self, confirmed):
        if confirmed and self._active_download_task:
            self._active_download_task.cancel()
            self["status"].setText("تم إلغاء التحميل")

    def _onDownloadChoiceMade(self, choice):
        if not choice:
            return
        entry = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else None
        if not entry:
            return
        self.session.openWithCallback(
            lambda c: self._onDownloadActionChosen(c, entry),
            ChoiceBox,
            title="شاهد أم حمّل؟",
            list=[("▶ شاهد الآن", "watch"), ("⬇ حمّل على القرص", "download")]
        )

    def _onDownloadActionChosen(self, choice, entry):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "watch":
            self["status"].setText("جاري تجهيز الرابط للمشاهدة...")
            self["status"].show()
            threading.Thread(target=self._bgResolveAndWatch, args=(entry,), daemon=True).start()
        elif action == "download":
            self["status"].setText("جاري تجهيز رابط التحميل...")
            threading.Thread(target=self._bgResolveAndDownload, args=(entry,), daemon=True).start()

    def _bgResolveAndWatch(self, entry):
        try:
            from plugin_downloads import resolve_download_link
            resolved_url = resolve_download_link(entry["url"])
            referer = entry["url"]

            if not resolved_url:
                extract_fn = None
                try:
                    extractor = _get_extractor(self._site)
                    extract_fn = getattr(extractor, "extract_stream", None)
                except Exception:
                    extract_fn = None
                if extract_fn is None:
                    from extractors.base import extract_stream as extract_fn

                result = extract_fn(entry["url"])
                resolved_url = result[0] if result else None
                referer = result[2] if result and len(result) >= 3 and result[2] else entry["url"]
                variants = result[3] if result and len(result) >= 4 and result[3] else []
            else:
                variants = []

            if not resolved_url:
                callInMainThread(self["status"].setText, "تعذر تجهيز الرابط للمشاهدة - جرب جودة أخرى")
                return

            label = entry.get("resolution") or entry.get("quality") or "HD"
            server = {"name": entry.get("resolution") or "رابط تحميل",
                      "url": entry["url"],
                      "type": "embed",
                      "quality": label}
            callInMainThread(self._onStreamFound, resolved_url, label, referer, server, variants)
        except Exception as e:
            my_log("resolve-and-watch error: {}".format(e))
            callInMainThread(self["status"].setText, "فشل تجهيز الرابط للمشاهدة")

    def _bgResolveAndDownload(self, entry):
        try:
            from plugin_downloads import resolve_download_link, DownloadTask, download_manager
            resolved_url = resolve_download_link(entry["url"])
            referer = entry["url"]

            if not resolved_url:
                extract_fn = None
                try:
                    extractor = _get_extractor(self._site)
                    extract_fn = getattr(extractor, "extract_stream", None)
                except Exception:
                    extract_fn = None
                if extract_fn is None:
                    from extractors.base import extract_stream as extract_fn

                result = extract_fn(entry["url"])
                resolved_url = result[0] if result else None
                referer = result[2] if result and len(result) >= 3 and result[2] else entry["url"]

            if not resolved_url:
                callInMainThread(self["status"].setText, "تعذر تجهيز رابط التحميل - جرب جودة أخرى")
                return

            title_hint = getattr(self, "_raw_title", None) or self._item.get("title", "video")
            task = DownloadTask(title=title_hint, url=resolved_url, referer=referer,
                                item_url=self._item.get("url", ""))
            self._active_download_task = task
            progress_timer_stop = threading.Event()

            def _progress_loop():
                while not progress_timer_stop.is_set():
                    if task.status == "downloading":
                        pct = task.progress_pct()
                        msg = ("جاري التحميل: {:.0f}%".format(pct) if pct
                            else "جاري التحميل: {:.1f} MB".format(task.bytes_done / 1048576.0))
                        callInMainThread(self["status"].setText, msg)
                    elif task.status in ("done", "error", "cancelled"):
                        break
                    time.sleep(1.5)

            threading.Thread(target=_progress_loop, daemon=True).start()

            try:
                dest = download_manager(task, title_hint=title_hint)
                progress_timer_stop.set()
                callInMainThread(self["status"].setText, "تم الحفظ: {}".format(os.path.basename(dest)))
            except Exception as e:
                progress_timer_stop.set()
                if task.status == "cancelled":
                    return
                my_log("Download failed: {}".format(e))
                callInMainThread(self["status"].setText, "فشل التحميل: {}".format(str(e)[:40]))
        except Exception as e:
            my_log("Download setup error: {}".format(e))
            callInMainThread(self["status"].setText, "خطأ في التحميل: {}".format(str(e)[:40]))
        finally:
            self._active_download_task = None


from plugin_util import _site_label, _sort_servers            # noqa: E402
from plugin_assets import placeholder_for_item                # noqa: E402


def _get_extractor(site):
    return get_extractor(site)