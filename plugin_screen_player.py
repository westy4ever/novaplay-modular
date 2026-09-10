# -*- coding: utf-8 -*-
"""NovaPlay — playback screen + service orchestration.

MODULAR EXTRACTION of AdvancedArabicPlayerSimplePlayer plus _play /
_build_remote_play_candidates / service capture-restore. Full feature
set with ALL session patches baked in:

  * tracker via novaplay_tracker module attributes; Fix X: tracker
    stops BEFORE the save/clear block (the EOF clear stays cleared)
  * Fix Y: _next_open_timer NOT stopped in __stop (the deferred
    next-episode open survives player teardown)
  * Fix Z: double evEOF does not exit from under a visible card
  * Fix W: single TMDB fetch per backdrop selection (merge lives in
    plugin_screen_home.py)
  * OSD poster: 132x198 floating ABOVE the panel (no seekbar overlap),
    paints from imagecache → util-cache bridge → raw cache → placeholder
  * auto-next card: portrait 100x150 poster slot; poster fallback
    chain (episode → series/player poster → util cache → async) PLUS
    a cache poll in the countdown tick so a late download replaces
    the placeholder mid-countdown
  * number keys 0-9 = proportional seeks (KEY_9 was unbound → red X)
  * record: decodes the proxy URL back to the upstream stream so the
    HLS/direct dispatch sees the real extension
  * start_proxy() guard; '#' header merge + value unquoting
  * real seekbar; pillarbox aspect; REC blink; sleep timer; player
    MENU on menu/showMenu/mainMenu; Subtitle Studio v3
  * Fix 1-4: OSD video info badge, subtitle indicator, clock, colored elapsed
  * Fix C: Pick line to sync in Subtitle Studio
"""

import os
import re
import sys
import time
import threading

from urllib.parse import quote, unquote, urlparse

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Components.ServiceEventTracker import ServiceEventTracker
from enigma import (eTimer, eServiceReference, iPlayableService,
                    ePoint, eSize, gFont)

import novaplay_proxy
from novaplay_proxy import start_proxy, _PROXY_PORT
import plugin_imagecache
from plugin_common import my_log
from plugin_state import (_get_config, _set_config, _get_saved_position,
                          _save_position)
from plugin_util import SAFE_UA, _single_line_text
from novaplay_subtitles import (maybe_resume_subtitle, remember_subtitle,
                                apply_subtitle, get_subtitle_state,
                                SYNC_STEP_MS, adjust_sync, reset_sync,
                                disable_subtitle, _cfg,
                                NovaSubtitleBrowser, NovaOnlineSubsScreen)
from plugin_assets import placeholder_for_item
import novaplay_tracker
from novaplay_tracker import (start_pos_tracker, stop_pos_tracker,
                              current_play_secs)
from novaplay_thread import callInMainThread
from plugin_gridlist import scale_skin_xml

try:
    from Screens.AudioSelection import AudioSelection
except Exception:
    AudioSelection = None

try:
    from enigma import eAVSwitch
except Exception:
    eAVSwitch = None

try:
    from enigma import iServiceInformation
except Exception:
    iServiceInformation = None

try:
    from skin import parseColor
except Exception:
    parseColor = None


# ─── Remote-play candidate builder ──────────────────────────────────────────

def _build_remote_play_candidates(url):
    url = str(url).strip()
    # _onStreamFound builds "url#Key=Value&..." (sref-header convention);
    # resolvers append "|Key=Value&...". Accept BOTH, merge if both appear.
    plain_url, pipe = (url.split("|", 1) + [""])[:2]
    if "#" in plain_url:
        plain_url, frag = plain_url.split("#", 1)
        pipe = frag if not pipe else (frag + "&" + pipe)
    headers = {}
    for part in pipe.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            # '#' fragment header values arrive URL-encoded
            # (Referer=C2%2Fpath style) — unquote or they hit the CDN
            # mangled. Harmless on plain values.
            try:
                v = unquote(v.strip())
            except Exception:
                v = v.strip()
            if k and k not in headers:
                headers[k] = v
    candidates = []
    seen = set()

    def add_candidate(p_type, svc_url, label, uses_proxy=False):
        key = (p_type, svc_url)
        if not svc_url or key in seen: return
        seen.add(key)
        candidates.append((p_type, svc_url, label, uses_proxy))

    proxied = ""
    legacy_proxied = ""
    if plain_url.startswith("https://") or plain_url.startswith("http://"):
        # no proxy -> no proxied candidates, direct only
        if start_proxy():
            q = "url=" + quote(plain_url, safe="")
            if headers.get("Referer"):
                q += "&referer=" + quote(headers["Referer"], safe="")
            if headers.get("User-Agent"):
                q += "&ua=" + quote(headers["User-Agent"], safe="")
            if headers.get("Cookie"):
                q += "&cookie=" + quote(headers["Cookie"], safe="")
            proxied = "http://127.0.0.1:{}/stream?{}".format(_PROXY_PORT, q)
            legacy_raw = url.replace("#", "|") if "#" in url else url
            legacy_proxied = "http://127.0.0.1:{}/{}".format(_PROXY_PORT, legacy_raw)

    is_hls = any(x in plain_url.lower() for x in (".m3u8", "master.txt", "/hls", "/playlist"))
    is_mkv = ".mkv" in plain_url.lower()
    has_exteplayer = os.path.exists("/usr/bin/exteplayer3")

    if is_hls:
        add_candidate(4097, plain_url, "4097 مباشر HLS")
        add_candidate(4097, url, "4097 + headers HLS")
        add_candidate(8193, plain_url, "8193 مباشر")
        add_candidate(5001, plain_url, "5001 مباشر")
        if has_exteplayer:
            add_candidate(5002, plain_url, "5002 exteplayer3")
            add_candidate(5002, url, "5002 exteplayer3 + headers")
        if proxied:
            add_candidate(4097, proxied, "4097 + proxy HLS", True)
            add_candidate(5001, proxied, "5001 + proxy", True)
            if has_exteplayer:
                add_candidate(5002, proxied, "5002 exteplayer3 + proxy", True)
        if legacy_proxied:
            add_candidate(4097, legacy_proxied, "4097 + proxy قديم", True)
    elif is_mkv and has_exteplayer:
        add_candidate(5002, url, "5002 exteplayer3 + headers")
        add_candidate(5002, plain_url, "5002 exteplayer3")
        add_candidate(5001, plain_url, "5001 مباشر")
        add_candidate(4097, plain_url, "4097 مباشر")
        add_candidate(8193, plain_url, "8193 مباشر")
        add_candidate(4097, url, "4097 + headers")
        if proxied:
            add_candidate(5002, proxied, "5002 exteplayer3 + proxy", True)
            add_candidate(5001, proxied, "5001 + proxy", True)
            add_candidate(4097, proxied, "4097 + proxy", True)
        if legacy_proxied:
            add_candidate(4097, legacy_proxied, "4097 + proxy قديم", True)
    else:
        add_candidate(5001, plain_url, "5001 مباشر")
        add_candidate(4097, plain_url, "4097 مباشر")
        add_candidate(8193, plain_url, "8193 مباشر")
        add_candidate(4097, url, "4097 + headers")
        if has_exteplayer:
            add_candidate(5002, plain_url, "5002 exteplayer3")
            add_candidate(5002, url, "5002 exteplayer3 + headers")
        if proxied:
            add_candidate(5001, proxied, "5001 + proxy", True)
            add_candidate(4097, proxied, "4097 + proxy", True)
            if has_exteplayer:
                add_candidate(5002, proxied, "5002 exteplayer3 + proxy", True)
        if legacy_proxied:
            add_candidate(4097, legacy_proxied, "4097 + proxy قديم", True)

    return candidates


def _copy_service_ref(sref):
    if not sref: return None
    try:
        return eServiceReference(sref.toString())
    except Exception:
        try:
            return eServiceReference(str(sref.toString()))
        except Exception:
            return sref


def _capture_previous_service(session):
    try:
        return _copy_service_ref(session.nav.getCurrentlyPlayingServiceReference())
    except Exception as e:
        my_log("Capture previous service failed: {}".format(e))
        return None


def _restore_previous_service(session, previous_service):
    if not previous_service: return
    try:
        session.nav.stopService()
    except Exception: pass
    try:
        session.nav.playService(previous_service)
        my_log("Previous service restored")
    except Exception as e:
        my_log("Restore previous service failed: {}".format(e))


# ─── The player screen ───────────────────────────────────────────────────────

class AdvancedArabicPlayerSimplePlayer(Screen):
    skin = scale_skin_xml("""
    <screen name="AdvancedArabicPlayerSimplePlayer" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="transparent">
        <widget name="aspect_bar_l" position="0,0" size="1,1080" backgroundColor="#000000" zPosition="5" />
        <widget name="aspect_bar_r" position="1919,0" size="1,1080" backgroundColor="#000000" zPosition="5" />
        <widget name="recBlink" position="1660,42" size="190,54" font="Regular;36" halign="center" valign="center" foregroundColor="#00FF0000" backgroundColor="#AA000000" transparent="0" zPosition="95" cornerRadius="16" />
        <widget name="osd_shadow"   position="148,856" size="1624,230" backgroundColor="#000000" zPosition="9" />
        <widget name="overlay_bg"   position="160,860" size="1600,210" backgroundColor="#0A0E14" zPosition="10" />
        <widget name="osd_topline"  position="160,860" size="1600,3" backgroundColor="#00E5FF" zPosition="11" />
        <widget name="osd_titlebar" position="160,860" size="1600,52" backgroundColor="#0D1520" zPosition="11" />
        <!-- Poster floats ABOVE the OSD panel, over the video's left edge:
             132x198 (2:3), clear of the seekbar entirely, hides with the OSD -->
        <widget name="osdPosterBox" position="160,606" size="168,248" backgroundColor="#161B22" cornerRadius="12" zPosition="12" transparent="0" />
        <widget name="osdPoster" position="164,610" size="160,240" zPosition="12" alphatest="blend" scale="1" />
        <widget name="osd_title"    position="180,868" size="1180,38" font="Regular;30" foregroundColor="#00E5FF" transparent="1" zPosition="12" halign="left" />
        <widget name="osd_videinfo" position="860,868" size="420,38" font="Regular;26" foregroundColor="#39D1D4D9" transparent="1" zPosition="12" halign="left" />
        <widget name="osd_subinfo" position="1290,870" size="36,34" font="Regular;28" foregroundColor="#39FFD740" transparent="1" zPosition="12" halign="center" valign="center" />
        <widget name="osd_clock" position="1630,868" size="110,38" font="Regular;28" foregroundColor="#8B49E4B9" transparent="1" zPosition="12" halign="right" />
        <widget name="osd_durtext"  position="1330,868" size="290,38" font="Regular;26" foregroundColor="#8B949E" transparent="1" zPosition="12" halign="right" />
        <widget name="seekbar" position="180,912" size="1480,12" zPosition="12" backgroundColor="#1C2333" foregroundColor="#00E5FF" cornerRadius="6" />
        <widget name="prog_bar"  position="1660,906" size="100,26" font="Regular;20" foregroundColor="#00E5FF" transparent="1" zPosition="12" halign="right" />
        <widget name="osd_elapsed"  position="180,938" size="320,44" font="Regular;36" foregroundColor="#FFD740" transparent="1" zPosition="12" />
        <widget name="status"       position="640,938" size="640,44" font="Regular;36" foregroundColor="#39D98A" transparent="1" zPosition="12" halign="center" />
        <widget name="osd_hints"    position="1220,938" size="520,44" font="Regular;26" foregroundColor="#8B949E" transparent="1" zPosition="12" halign="right" />
        <widget name="osd_divider"  position="160,982" size="1600,2" backgroundColor="#1C2333" zPosition="11" />
        <widget name="osd_keybar"   position="160,984" size="1600,46" backgroundColor="#0D1520" zPosition="11" />
        <widget name="osd_keys"     position="180,992" size="1560,34" font="Regular;26" foregroundColor="#C9D1D9" transparent="1" zPosition="12" halign="center" />
        <widget name="osd_botline"  position="160,1027" size="1600,3" backgroundColor="#0A2040" zPosition="11" />
        <widget name="autoNextPanel" position="1110,92" size="750,255" zPosition="20" backgroundColor="#20000000" transparent="0" cornerRadius="24" />
        <widget name="autoNextAccent" position="1110,92" size="750,7" zPosition="21" backgroundColor="#00E5FF" transparent="0" />
        <widget name="autoNextPoster" position="1140,105" size="100,150" zPosition="22" alphatest="blend" scale="1" />
        <widget name="autoNextTitle" position="1370,122" size="455,40" font="Regular;30" halign="left" foregroundColor="#F0F6FC" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextEpisode" position="1370,170" size="455,54" font="Regular;27" halign="left" foregroundColor="#FFD740" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextText" position="1370,235" size="455,32" font="Regular;24" halign="left" foregroundColor="#E8E8E8" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextProgress" position="1370,282" size="455,16" zPosition="22" cornerRadius="8" backgroundColor="#1C2333" foregroundColor="#00E5FF" />
        <widget name="autoNextHelp" position="1370,310" size="455,24" font="Regular;20" halign="left" foregroundColor="#CFCFCF" backgroundColor="transparent" zPosition="22" transparent="1" />
        <!-- Studio-rendered subtitles: above video & aspect bars (z5), below OSD (z9+) -->
        <widget name="subBg1"   position="660,812" size="600,100" zPosition="7" backgroundColor="#80000000" transparent="0" cornerRadius="14" />
        <widget name="subBg2"   position="660,916" size="600,100" zPosition="7" backgroundColor="#80000000" transparent="0" cornerRadius="14" />
        <widget name="subLine1" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00FFFFFF" backgroundColor="transparent" zPosition="8" transparent="1" />
        <widget name="subLine2" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00FFFFFF" backgroundColor="transparent" zPosition="8" transparent="1" />
        <widget name="subShadow0_0" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_1" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_2" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_3" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_4" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_5" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_6" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow0_7" position="210,812" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_0" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_1" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_2" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_3" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_4" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_5" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_6" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <widget name="subShadow1_7" position="210,916" size="1500,100" font="Regular;38" halign="center" valign="center" foregroundColor="#00000000" backgroundColor="transparent" zPosition="7" transparent="1" />
        <!-- Subtitle Studio overlay (v3) -->
        <widget name="studioPanel" position="0,36" size="1920,262" zPosition="85" backgroundColor="#0A0E14" transparent="0" />
        <widget name="studioAccent" position="0,36" size="1920,8" zPosition="86" backgroundColor="#00E5FF" transparent="0" />
        <widget name="studioTitle" position="45,52" size="1145,32" font="Regular;30" foregroundColor="#00E5FF" transparent="1" zPosition="87" />
        <widget name="studioStatus" position="1270,52" size="600,30" font="Regular;24" halign="right" foregroundColor="#8B949E" transparent="1" zPosition="87" />
        <widget name="studioHelp" position="45,178" size="1830,31" font="Regular;24" halign="center" foregroundColor="#8B949E" transparent="1" zPosition="87" />
        <widget name="studioCard0" position="60,94" size="330,68" font="Regular;22" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#18181818" transparent="0" cornerRadius="14" zPosition="87" />
        <widget name="studioCard1" position="420,94" size="330,68" font="Regular;22" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#18181818" transparent="0" cornerRadius="14" zPosition="87" />
        <widget name="studioCard2" position="790,90" size="340,76" font="Regular;24" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#402A82FF" transparent="0" cornerRadius="18" zPosition="88" />
        <widget name="studioCard3" position="1170,94" size="330,68" font="Regular;22" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#18181818" transparent="0" cornerRadius="14" zPosition="87" />
        <widget name="studioCard4" position="1530,94" size="330,68" font="Regular;22" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#18181818" transparent="0" cornerRadius="14" zPosition="87" />
        <widget name="studioPreviewBg" position="460,212" size="1000,74" zPosition="87" backgroundColor="#18181818" transparent="0" cornerRadius="12" />
        <widget name="studioPreview" position="470,216" size="980,66" font="Regular;30" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="transparent" zPosition="88" transparent="1" />
        <widget name="safeTop" position="0,90" size="1920,2" backgroundColor="#00E5FF" zPosition="86" transparent="0" />
        <widget name="safeBottom" position="0,990" size="1920,2" backgroundColor="#00E5FF" zPosition="86" transparent="0" />
        <widget name="safeLeft" position="96,90" size="2,900" backgroundColor="#00E5FF" zPosition="86" transparent="0" />
        <widget name="safeRight" position="1822,90" size="2,900" backgroundColor="#00E5FF" zPosition="86" transparent="0" />
    </screen>
    """)

    _OSD_WIDGETS = [
        "osd_shadow","overlay_bg","osd_topline","osd_botline",
        "osd_titlebar","osd_title","osd_durtext",
        "osdPosterBox","osdPoster",
        "prog_bar","seekbar","osd_elapsed",
        "status","osd_hints","osd_divider",
        "osd_keybar","osd_keys",
        # Fix 1-3: Add new widgets to OSD display list
        "osd_videinfo","osd_subinfo","osd_clock",
    ]
    _AUTONEXT_WIDGETS = ("autoNextPanel","autoNextAccent","autoNextPoster","autoNextTitle",
                          "autoNextEpisode","autoNextText","autoNextProgress","autoNextHelp")
    _STUDIO_KEYS = ("studioPanel","studioAccent","studioTitle","studioStatus",
                    "studioHelp","studioCard0","studioCard1","studioCard2",
                    "studioCard3","studioCard4",
                    "studioPreviewBg","studioPreview")
    _STUDIO_CARDS = ("studioCard0","studioCard1","studioCard2","studioCard3","studioCard4")
    _AUTONEXT_SECONDS = 10
    _ASPECT_MODES = [("Full", 0), ("14:9", 120), ("4:3", 240)]

    def __init__(self, session, title, candidates, previous_service=None, resume_pos=0, item_url="",
                 next_episode=None, on_next=None, poster_url=""):
        Screen.__init__(self, session)
        self["overlay_bg"]   = Label("")
        self["status"]       = Label("جاري التشغيل...")
        self["osd_shadow"]   = Label("")
        self["osd_titlebar"] = Label("")
        self["osd_title"]    = Label("")
        self["osd_durtext"]  = Label("")
        self["osd_topline"]  = Label("")
        self["seekbar"]      = ProgressBar()
        self["prog_bar"]     = Label("")
        self["osd_elapsed"]  = Label("")
        self["osd_hints"]    = Label("")
        self["osd_divider"]  = Label("")
        self["osd_keybar"]   = Label("")
        self["osd_keys"]     = Label("")
        self["osd_botline"]  = Label("")
        self["aspect_bar_l"] = Label("")
        self["aspect_bar_r"] = Label("")
        self["osdPosterBox"] = Label("")
        self["osdPoster"]    = Pixmap()
        # Fix 1: Video info badge
        self["osd_videinfo"] = Label("")
        # Fix 2: Subtitle indicator
        self["osd_subinfo"] = Label("")
        # Fix 3: Current time
        self["osd_clock"] = Label("")
        self["recBlink"]     = Label("● REC")
        for k in self._AUTONEXT_WIDGETS:
            self[k] = Pixmap() if "Poster" in k else Label("")
        self["subBg1"] = Label("")
        self["subBg2"] = Label("")
        self["subLine1"] = Label("")
        self["subLine2"] = Label("")
        for _li in range(2):
            for _di in range(8):
                self["subShadow%d_%d" % (_li, _di)] = Label("")
        self["studioPanel"] = Label("")
        self["studioAccent"] = Label("")
        self["studioTitle"] = Label("Subtitle Studio")
        self["studioStatus"] = Label("")
        self["studioHelp"] = Label("◀ ▶ اختيار   ▲ ▼ تعديل   OK قائمة   EXIT إغلاق")
        for c in self._STUDIO_CARDS:
            self[c] = Label("")
        self["studioPreviewBg"] = Label("")
        self["studioPreview"] = Label("")
        self["safeTop"] = Label("")
        self["safeBottom"] = Label("")
        self["safeLeft"] = Label("")
        self["safeRight"] = Label("")

        _raw = (title or "").strip()
        _qtag_m = re.search(r'\s*(\[\d+p\])\s*$', _raw)
        _qtag = _qtag_m.group(1) if _qtag_m else ""
        _bare = _raw[:_qtag_m.start()].strip() if _qtag_m else _raw
        if len(_bare) > 34:
            _bare = _bare[:32].rstrip() + u"\u2026"
        self.title = (_bare + " " + _qtag).strip() if _qtag else _bare
        self.candidates = candidates or []
        self.previous_service = _copy_service_ref(previous_service)
        self.sref = None
        self._play_confirmed = False
        self._candidate_idx = -1
        self._candidate_start_ts = 0
        self._candidate_uses_proxy = False
        self._candidate_label = ""
        self._handoff = False
        self._restored_previous = False
        self._resume_pos = int(resume_pos or 0)
        self._item_url  = item_url or ""
        self._poster_url = poster_url or ""
        self._poster_painted = False
        # Fix 1: cache video info
        self._osd_video_info = ""
        self._next_episode = next_episode
        self._on_next = on_next
        self._next_prompt_enabled = str(_get_config("next_episode_prompt", "true")).lower() == "true"
        self._skip_restore = False
        self._next_open_timer = None
        self._eof_prompted = False
        self._sleep_timer = None
        self._sleep_minutes = 0
        self._aspect_idx = 0
        self._record_task = None
        self._rec_blink_on = True
        self._autonext_active = False
        self._autonext_nxt = None
        self._autonext_secs = 0
        self._autonext_poster_url = ""
        self._autonext_poster_polls = 0
        self._studioOverlayActive = False
        self._studioIdx = 0
        self._studioWin = 0
        self._studioItems = []
        self._studioChoiceKey = ""

        self._seek_timer = eTimer()
        self._seek_timer.callback.append(self.__doSeek)
        self._seek_retry_count = 0
        self._seek_verify_timer = eTimer()
        self._seek_verify_timer.callback.append(self.__verifySeek)
        self._hide_timer = eTimer()
        self._hide_timer.callback.append(self.__hideOSD)
        self._osd_update_timer = eTimer()
        self._osd_update_timer.callback.append(self.__updateOSD)
        self._osd_visible = False
        self._total_secs  = 0
        self._osd_auto_hide_secs = 4
        self._paused = False
        self._paused_elapsed = 0
        self._force_confirmation_timer = eTimer()
        self._force_confirmation_timer.callback.append(self.__forceConfirm)
        self._retry_timer = eTimer()
        self._retry_timer.callback.append(self.__onTimeout)
        self._autonext_timer = eTimer()
        self._autonext_timer.callback.append(self.__autonextTick)
        self._studioTimer = eTimer()
        self._studioTimer.callback.append(self.__studioTick)
        self._rec_blink_timer = eTimer()
        self._rec_blink_timer.callback.append(self.__tickRecBlink)

        for k in self._STUDIO_KEYS + self._AUTONEXT_WIDGETS:
            try: self[k].hide()
            except Exception: pass
        for k in ("safeTop","safeBottom","safeLeft","safeRight",
                  "aspect_bar_l","aspect_bar_r","recBlink",
                  "subBg1","subBg2","subLine1","subLine2") + \
                 tuple("subShadow%d_%d" % (li, di) for li in range(2) for di in range(8)):
            try: self[k].hide()
            except Exception: pass

        try:
            from novaplay_substudio import STUDIO
            STUDIO.bind(self)
        except Exception:
            pass

        self["actions"] = ActionMap(["OkCancelActions", "MediaPlayerActions", "NumberActions", "InfobarSeekActions", "DirectionActions", "ColorActions", "InfobarSubtitleActions", "InfobarSubtitleSelectionActions", "MenuActions", "InfoBarMenuActions", "ButtonSetupActions", "InfoActions"], {
            "cancel":           self.__onCancelKey,
            "stop":             self.__onExit,
            "ok":               self.__onOkKey,
            "playpauseService": self.__togglePause,
            "right":            self.__navRightKey,
            "left":             self.__navLeftKey,
            "up":               self.__navUpKey,
            "down":             self.__navDownKey,
            # KEY_LEFT/KEY_RIGHT ALSO arrive via InfobarSeekActions as
            # seekBack/seekFwd — gate those through the same router or
            # the studio overlay is bypassed on some images.
            "seekFwd":          self.__navSeekFwdKey,
            "seekBack":         self.__navSeekBackKey,
            # Number keys = proportional jumps (KEY_9 was unbound → red X)
            "0": lambda: self.__seekPct(0),
            "1": lambda: self.__seekPct(10),
            "2": lambda: self.__seekPct(20),
            "3": lambda: self.__seekPct(30),
            "4": lambda: self.__seekPct(40),
            "5": lambda: self.__seekPct(50),
            "6": lambda: self.__seekPct(60),
            "7": lambda: self.__seekPct(70),
            "8": lambda: self.__seekPct(80),
            "9": lambda: self.__seekPct(90),
            # InfobarSeekActions (already in this ActionMap's context list)
            # maps KEY_1/3/4/6/7/9 to "seekdef:N" — the LITERAL action name
            # with the colon — and within an ActionMap the later-listed
            # context wins the dispatch. Without these handlers the action
            # arrives unhandled → the red "unhandled key" indicator.
            "seekdef:1": lambda: self.__seekPct(10),
            "seekdef:3": lambda: self.__seekPct(30),
            "seekdef:4": lambda: self.__seekPct(40),
            "seekdef:6": lambda: self.__seekPct(60),
            "seekdef:7": lambda: self.__seekPct(70),
            "seekdef:9": lambda: self.__seekPct(90),            
            "green":            self.__onRestart,
            "red":              self.__subtitleDelayBack,
            "blue":             self._onSubtitles,
            "yellow":           self.__cycleAspect,
            "subtitles":        self._onSubtitles,
            "subtitleSelection": self._onSubtitles,
            "menu":             self._playerMenu,
            "showMenu":         self._playerMenu,
            "mainMenu":         self._playerMenu,
            "info":             self.__streamInfo,
        }, -1)
        eventmap = {
            iPlayableService.evTuneFailed: self.__onFailed,
            iPlayableService.evEOF: self.__onEOF,
        }
        ev_video = getattr(iPlayableService, "evVideoSizeChanged", None)
        if ev_video is not None:
            eventmap[ev_video] = self.__onConfirmed
        self._events = ServiceEventTracker(screen=self, eventmap=eventmap)
        self.onLayoutFinish.append(self.__initOSD)
        self.onLayoutFinish.append(self.__playNext)
        self.onClose.append(self.__stop)

    # ─── OSD ────────────────────────────────────────────────────────────
    def __initOSD(self):
        for w in self._OSD_WIDGETS:
            try: self[w].hide()
            except: pass
        for w in self._AUTONEXT_WIDGETS:
            try: self[w].hide()
            except: pass

    def __hideOSD(self):
        self._osd_visible = False
        try: self._osd_update_timer.stop()
        except: pass
        for w in self._OSD_WIDGETS:
            try: self[w].hide()
            except: pass

    def __showOSD(self, auto_hide=True):
        self._osd_visible = True
        for w in self._OSD_WIDGETS:
            try: self[w].show()
            except: pass
        try:
            self._paintOsdPoster()
        except Exception:
            pass
        self.__updateOSD()
        try:
            self._osd_update_timer.start(1000, False)
        except: pass
        if auto_hide:
            try:
                self._hide_timer.stop()
                self._hide_timer.start(self._osd_auto_hide_secs * 1000, True)
            except: pass

    def _paintOsdPoster(self):
        """Fill the OSD's poster slot once. imagecache (sized) → util-cache
        bridge (detail's ePicLoad path) → raw imagecache → placeholder →
        hide. Never blocks."""
        if getattr(self, "_poster_painted", False):
            return
        url = getattr(self, "_poster_url", "") or ""
        if not url:
            return
        self._poster_painted = True
        path = ""
        try:
            path = plugin_imagecache.getCachedImage(url, target_size=(160, 240))
        except Exception:
            path = ""
        if not path:
            try:
                plugin_imagecache.requestImageAsyncPriority(url, target_size=(160, 240))
            except Exception:
                pass
            try:
                path = plugin_imagecache.getCachedImage(url)
            except Exception:
                path = ""
        if not path:
            try:
                from plugin_util import _get_cached_poster
                path = _get_cached_poster(url) or ""
            except Exception:
                path = ""
        if not path:
            path = placeholder_for_item({"type": "movie"}) or ""
        if path:
            try:
                self["osdPoster"].instance.setScale(1)
                self["osdPoster"].instance.setPixmapFromFile(path)
                self["osdPoster"].show()
                self["osdPosterBox"].show()
            except Exception:
                try:
                    self["osdPoster"].hide()
                    self["osdPosterBox"].hide()
                except Exception:
                    pass

    def __updateOSD(self):
        if not self._osd_visible:
            try: self._osd_update_timer.stop()
            except: pass
            return
        try:
            if self._paused:
                elapsed = self._paused_elapsed
            else:
                elapsed = current_play_secs()
            he = elapsed // 3600; me = (elapsed % 3600) // 60; se = elapsed % 60
            
            # Fix 4: Colored elapsed time (AJ Panel style — cyan for active
            # progress, gold when paused)
            color = "#39FFD740" if self._paused else "#39E5D1A6"
            try:
                if parseColor is not None:
                    self["osd_elapsed"].instance.setForegroundColor(parseColor(color))
            except Exception:
                pass
            self["osd_elapsed"].setText("{:02d}:{:02d}:{:02d}".format(he, me, se))
            
            # Fix 1: Video info badge (resolution/FPS/aspect)
            if self._total_secs > 0 and not self._osd_video_info:
                try:
                    service = self.session.nav.getCurrentService()
                    info = service.info() if service else None
                    parts = []
                    if info is not None and iServiceInformation is not None:
                        try:
                            x = info.getInfo(iServiceInformation.sWidth)
                            y = info.getInfo(iServiceInformation.sHeight)
                            if x > 0 and y > 0:
                                parts.append("{}×{}".format(x, y))
                        except Exception:
                            pass
                        try:
                            fr = info.getInfo(iServiceInformation.sFrameRate)
                            if fr > 0:
                                parts.append("· {}fps".format(fr))
                        except Exception:
                            pass
                    self._osd_video_info = "  ".join(parts)
                except Exception:
                    self._osd_video_info = ""
            self["osd_videinfo"].setText(self._osd_video_info)
            
            # Fix 2: Subtitle indicator — bright gold CC when active,
            # dim grey cc when none (case alone was easy to miss)
            try:
                from novaplay_subtitles import get_subtitle_state
                _cc_on = bool(get_subtitle_state().get("path"))
                self["osd_subinfo"].setText("CC" if _cc_on else "cc")
                if parseColor is not None:
                    try:
                        self["osd_subinfo"].instance.setForegroundColor(
                            parseColor("#39FFD740" if _cc_on else "#396E7681"))
                    except Exception:
                        pass
            except Exception:
                pass
            
            # Fix 3: Current time
            now = time.strftime("%H:%M")
            self["osd_clock"].setText(now)
            
            total = self._total_secs
            if not total:
                try:
                    svc = self.session.nav.getCurrentService()
                    seek = svc and svc.seek()
                    if seek:
                        r = seek.getLength()
                        if r and r[0] == 0 and r[1] > 0:
                            total = r[1] // 90000
                            self._total_secs = total
                except: pass
            if total > 0:
                rem = max(0, total - elapsed)
                pct = min(1.0, float(elapsed) / float(total))
                hr = rem // 3600
                mr = (rem % 3600) // 60
                sr = rem % 60
                ht = total // 3600
                mt = (total % 3600) // 60
                st = total % 60
                self["osd_durtext"].setText("-{:02d}:{:02d}:{:02d}  {:02d}:{:02d}:{:02d}".format(hr, mr, sr, ht, mt, st))
                try:
                    self["seekbar"].setRange((0, 100))
                    self["seekbar"].setValue(int(pct * 100))
                except Exception:
                    pass
                self["prog_bar"].setText("{:.0f}%".format(pct * 100))
            else:
                self["osd_durtext"].setText("")
                self["prog_bar"].setText("")
                try:
                    self["seekbar"].setValue(0)
                except Exception:
                    pass
            self["osd_keys"].setText("OK=إيقاف  0-9=قفز  ‹›±10ث  أحمر:ترجمة-  أصفر:نسبة  أخضر:إعادة  أزرق:ترجمة  Stop=خروج")
        except Exception as e:
            my_log("updateOSD error: {}".format(e))

    # ─── Studio tick ─────────────────────────────────────────────────────
    def __studioTick(self):
        try:
            from novaplay_substudio import STUDIO
            if STUDIO.is_attached():
                STUDIO.update(int(current_play_secs() * 1000))
        except Exception:
            pass

    # ─── play-next / confirmation ───────────────────────────────────────
    def __playNext(self):
        if getattr(self, "_is_advancing", False): return
        self._is_advancing = True
        self._candidate_idx += 1
        if self._candidate_idx >= len(self.candidates):
            if not getattr(self, "_exhausted", False):
                self._exhausted = True
                self["status"].setText("تعذر تشغيل الرابط على كل المحاولات")
                def _fail_callback(*args):
                    self.__onExit(clear_position=False)
                self.session.openWithCallback(_fail_callback, MessageBox, "تعذر تشغيل الرابط على كل المحاولات", MessageBox.TYPE_ERROR, timeout=5)
            self._is_advancing = False
            return

        p_type, svc_url, label, uses_proxy = self.candidates[self._candidate_idx]
        self._play_confirmed = False
        self._candidate_start_ts = time.time()
        self._candidate_uses_proxy = uses_proxy
        self._candidate_label = label
        if uses_proxy:
            novaplay_proxy._PROXY_LAST_HIT = 0
            novaplay_proxy._PROXY_LAST_BYTES = 0
        self.sref = eServiceReference(p_type, 0, svc_url)
        if sys.version_info[0] == 3:
            self.sref.setName(str(self.title))
        else:
            self.sref.setName(self.title.encode("utf-8", "ignore"))

        self["status"].setText("جاري التشغيل... {}".format(label))
        self.__showOSD(False)
        my_log("Play attempt: {}".format(label))
        try:
            self.session.nav.stopService()
        except: pass
        try:
            self.session.nav.playService(self.sref)
            self._retry_timer.start(12000, True)
            self._force_confirmation_timer.start(3000, True)
        except Exception as e:
            my_log("SimplePlayer fallback error: {}".format(e))
            self._is_advancing = False
            self.__playNext()
            return
        self._is_advancing = False

    def __onConfirmed(self):
        if self._play_confirmed: return
        self._play_confirmed = True
        try:
            self._retry_timer.stop()
            self._force_confirmation_timer.stop()
        except: pass
        my_log("Play confirmed: {}".format(self._candidate_label))
        start_pos_tracker(self.session, self._item_url, start_pos=self._resume_pos)
        try:
            maybe_resume_subtitle(self.session, self.title, self._item_url)
        except Exception:
            pass
        try:
            self._studioTimer.start(100, False)
        except Exception:
            pass
        if self._resume_pos > 30:
            self._seek_retry_count = 0
            self._seek_timer.start(6000, True)
        self["osd_title"].setText(self.title)
        self["status"].setText(u"▶ Playing")
        self._total_secs = 0
        self.__showOSD(True)

    def __onFailed(self):
        if self._play_confirmed: return
        try:
            self._retry_timer.stop()
            self._force_confirmation_timer.stop()
        except: pass
        my_log("Play failed event: {}".format(self._candidate_label))
        self.__playNext()

    def __onEOF(self):
        if self._play_confirmed:
            if (self._next_episode and self._on_next
                    and self._next_prompt_enabled
                    and not getattr(self, "_eof_prompted", False)):
                self._eof_prompted = True
                my_log("EOF: next episode available — showing auto-next card")
                self._showAutoNextCard(self._next_episode)
                return
            # Fix Z: GStreamer fires evEOF twice after a seek near the end —
            # the second event must not exit from under the visible card;
            # the card's countdown/OK/EXIT own the exit.
            if getattr(self, "_autonext_active", False):
                return
            my_log("Playback EOF reached, clearing resume position.")
            self.__onExit(clear_position=True)
            return
        self.__onFailed()

    def __onTimeout(self):
        if self._play_confirmed: return
        if self._candidate_uses_proxy and novaplay_proxy._PROXY_LAST_HIT >= self._candidate_start_ts and novaplay_proxy._PROXY_LAST_BYTES > 0:
            my_log("Play proxy confirmed by traffic: {} bytes".format(novaplay_proxy._PROXY_LAST_BYTES))
            self.__onConfirmed()
            return
        my_log("Play timeout: {}".format(self._candidate_label))
        self.__playNext()

    def __forceConfirm(self):
        if self._play_confirmed: return
        if self._candidate_uses_proxy and novaplay_proxy._PROXY_LAST_HIT >= self._candidate_start_ts and novaplay_proxy._PROXY_LAST_BYTES > 0:
            my_log("Play proxy confirmed early by traffic: {} bytes".format(novaplay_proxy._PROXY_LAST_BYTES))
            self.__onConfirmed()

    # ─── auto-next card ──────────────────────────────────────────────────
    def _showAutoNextCard(self, nxt):
        self._autonext_nxt = nxt
        self._autonext_secs = self._AUTONEXT_SECONDS
        try:
            # Poster fallback chain: episode poster → series poster the
            # player holds → util cache (detail's ePicLoad path) → raw
            # imagecache → queue async → placeholder (replaced by the
            # poll in __autonextTick when the download lands).
            poster = (nxt or {}).get("poster") or getattr(self, "_poster_url", "") or ""
            path = ""
            if poster:
                path = plugin_imagecache.getCachedImage(poster, target_size=(100, 150))
                if not path:
                    try:
                        from plugin_util import _get_cached_poster
                        path = _get_cached_poster(poster) or ""
                    except Exception:
                        path = ""
                if not path:
                    plugin_imagecache.requestImageAsyncPriority(poster, target_size=(100, 150))
                    try:
                        path = plugin_imagecache.getCachedImage(poster) or ""
                    except Exception:
                        path = ""
            if not path:
                path = placeholder_for_item(nxt) or ""
            if path:
                self["autoNextPoster"].instance.setScale(1)
                self["autoNextPoster"].instance.setPixmapFromFile(path)
                self["autoNextPoster"].show()
            else:
                self["autoNextPoster"].hide()
        except Exception:
            self["autoNextPoster"].hide()
        self["autoNextTitle"].setText(_single_line_text(
            (nxt or {}).get("title") or "", width=30, fallback="الحلقة التالية"))
        self["autoNextEpisode"].setText("الحلقة التالية")
        self["autoNextText"].setText("التشغيل تلقائياً بعد %d ثوانٍ" % self._AUTONEXT_SECONDS)
        self["autoNextHelp"].setText("OK = تشغيل الآن    EXIT = إلغاء")
        try:
            self["autoNextProgress"].setRange((0, self._AUTONEXT_SECONDS))
            self["autoNextProgress"].setValue(self._AUTONEXT_SECONDS)
        except Exception:
            pass
        for w in self._AUTONEXT_WIDGETS:
            try: self[w].show()
            except Exception: pass
        self._autonext_active = True
        self._autonext_poster_url = poster if poster else ""
        self._autonext_poster_polls = 8
        try: self._autonext_timer.stop()
        except Exception: pass
        self._autonext_timer.start(1000, False)

    def _hideAutoNextCard(self):
        self._autonext_active = False
        self._autonext_poster_polls = 0
        try: self._autonext_timer.stop()
        except Exception: pass
        for w in self._AUTONEXT_WIDGETS:
            try: self[w].hide()
            except Exception: pass

    def __autonextTick(self):
        if not getattr(self, "_autonext_active", False):
            return
        # poster poll: replace placeholder with the real poster as soon
        # as the async download lands
        if getattr(self, "_autonext_poster_polls", 0) > 0 and getattr(self, "_autonext_poster_url", ""):
            self._autonext_poster_polls -= 1
            try:
                p = plugin_imagecache.getCachedImage(self._autonext_poster_url, target_size=(100, 150)) \
                    or plugin_imagecache.getCachedImage(self._autonext_poster_url)
                if p:
                    self._autonext_poster_polls = 0
                    self["autoNextPoster"].instance.setScale(1)
                    self["autoNextPoster"].instance.setPixmapFromFile(p)
                    self["autoNextPoster"].show()
            except Exception:
                pass
        self._autonext_secs -= 1
        if self._autonext_secs <= 0:
            self._hideAutoNextCard()
            self.__nextAnswer(True, self._autonext_nxt)
            return
        try:
            self["autoNextText"].setText("التشغيل تلقائياً بعد %d ثوانٍ" % self._autonext_secs)
            self["autoNextProgress"].setValue(self._autonext_secs)
        except Exception:
            pass

    def __nextAnswer(self, ans, nxt):
        if ans and self._on_next:
            self._skip_restore = True
            self.__onExit(clear_position=True)
            try:
                t = eTimer()
                t.callback.append(lambda: self._on_next(nxt))
                t.start(200, True)
                self._next_open_timer = t
            except Exception:
                self._on_next(nxt)
        else:
            self.__onExit(clear_position=True)

    # ─── key routing (studio → autonext → normal) ───────────────────────
    def __onOkKey(self):
        if getattr(self, "_studioOverlayActive", False):
            self._studioOk()
            return
        if getattr(self, "_autonext_active", False):
            self._hideAutoNextCard()
            self.__nextAnswer(True, self._autonext_nxt)
            return
        self.__togglePause()

    def __onCancelKey(self):
        if getattr(self, "_studioOverlayActive", False):
            self._hideStudioOverlay()
            # Back-track: Studio was opened FROM the subtitle menu —
            # return there, not to bare playback.
            self._onSubtitles()
            return
        if getattr(self, "_autonext_active", False):
            self._hideAutoNextCard()
            self.__onExit(clear_position=True)
            return
        self.__onExit()

    # ── Key routing: studio → autonext → normal playback (v4.1) ────────
    # One physical LEFT/RIGHT press is dispatched TWICE on this image
    # — as DirectionActions ("left"/"right") AND InfobarSeekActions
    # ("seekBack"/"seekFwd"). Proven by the log: two +150s position
    # jumps = 2 presses × (+10 +60). __studioDedup() swallows the
    # duplicate (same direction twice within 150 ms).
    #   Studio:  ◀ ▶ select card    ▲ ▼ adjust value
    #   Player:  ◀ ▶ ±10s          ▲ ▼ ±60s
    def __studioDedup(self, direction):
        """True = duplicate dispatch of the same keypress within 150 ms."""
        try:
            now = time.time()
        except Exception:
            return False
        if direction == getattr(self, "_studioNavDir", 0) and \
                (now - getattr(self, "_studioNavT", 0.0)) < 0.15:
            return True
        self._studioNavDir = direction
        self._studioNavT = now
        return False

    def __studioAdjustVKey(self, direction):
        """▲/▼ in studio: adjust the selected value one step. Cards that
        open sub-screens or act destructively are OK-only."""
        if not getattr(self, "_studioOverlayActive", False):
            return
        if not (0 <= self._studioIdx < len(self._studioItems)):
            return
        key = self._studioItems[self._studioIdx][2]
        if key in ("font", "presets", "pickline", "reset", "remove"):
            return
        self._studioAdjust(direction)

    def __navLeftKey(self):
        if self._studioOverlayActive:
            if not self.__studioDedup(-1):
                self._studioMove(-1)
            return
        if not self.__studioDedup(-1):
            self.__seek(-10)

    def __navRightKey(self):
        if self._studioOverlayActive:
            if not self.__studioDedup(+1):
                self._studioMove(+1)
            return
        if not self.__studioDedup(+1):
            self.__seek(+10)

    def __navUpKey(self):
        if self._studioOverlayActive:
            if not self.__studioDedup(-2):
                self.__studioAdjustVKey(+1)
            return
        if not self.__studioDedup(-2):
            self.__seek(-60)

    def __navDownKey(self):
        if self._studioOverlayActive:
            if not self.__studioDedup(+2):
                self.__studioAdjustVKey(-1)
            return
        if not self.__studioDedup(+2):
            self.__seek(+60)

    def __navSeekFwdKey(self):
        if getattr(self, "_studioOverlayActive", False):
            if not self.__studioDedup(+1):
                self._studioMove(+1)
            return
        if getattr(self, "_autonext_active", False):
            return
        if not self.__studioDedup(+1):
            self.__seek(+10)

    def __navSeekBackKey(self):
        if getattr(self, "_studioOverlayActive", False):
            if not self.__studioDedup(-1):
                self._studioMove(-1)
            return
        if getattr(self, "_autonext_active", False):
            return
        if not self.__studioDedup(-1):
            self.__seek(-10)

    # ─── pause / seek / restart / exit ───────────────────────────────────
    def __togglePause(self):
        try:
            svc = self.session.nav.getCurrentService()
            if not svc:
                self.__showOSD(True); return
            p = svc.pause()
            if not p:
                self.__showOSD(True); return
            if self._paused:
                p.unpause()
                self._paused = False
                with novaplay_tracker._GLOBAL_POS_LOCK:
                    novaplay_tracker._GLOBAL_IS_PAUSED = False
                    novaplay_tracker._GLOBAL_PLAY_START_POS = self._paused_elapsed
                    novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
                self["status"].setText(u"▶ Playing")
            else:
                with novaplay_tracker._GLOBAL_POS_LOCK:
                    wall = novaplay_tracker._GLOBAL_PLAY_START_WALL
                    base = novaplay_tracker._GLOBAL_PLAY_START_POS
                    if wall:
                        elapsed = int((time.time() - wall) + base)
                    else:
                        elapsed = 0
                    self._paused_elapsed = max(0, elapsed)
                p.pause()
                self._paused = True
                with novaplay_tracker._GLOBAL_POS_LOCK:
                    novaplay_tracker._GLOBAL_PLAY_START_POS = self._paused_elapsed
                    novaplay_tracker._GLOBAL_IS_PAUSED = True
                self["status"].setText(u"⏸ Paused")
            self.__showOSD(True)
        except Exception as e:
            my_log("togglePause error: {}".format(e))
            self.__showOSD(True)

    def __seek(self, delta_secs):
        try:
            svc = self.session.nav.getCurrentService()
            if not svc: return
            sk = svc.seek()
            if not sk: return
            with novaplay_tracker._GLOBAL_POS_LOCK:
                _wall = novaplay_tracker._GLOBAL_PLAY_START_WALL
                _base = novaplay_tracker._GLOBAL_PLAY_START_POS
                if _wall:
                    elapsed = time.time() - _wall
                else:
                    elapsed = 0
                current_est = int(_base + elapsed)
                target = max(0, current_est + int(delta_secs))
                _tot = self._total_secs
                if _tot > 0:
                    target = min(target, _tot - 3)
                target = max(0, target)
                sk.seekTo(target * 90000)
                novaplay_tracker._GLOBAL_LAST_SEEK_TARGET = target
                novaplay_tracker._GLOBAL_PLAY_START_POS = max(0, target - 2)
                novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
                if self._paused:
                    self._paused_elapsed = target
            # v4.1: do NOT zero _total_secs here — the resume-seek at
            # startup ran through __seek and wiped the duration before
            # the OSD could learn it, leaving number-key jumps dead.
            # (__onConfirmed already resets it properly on restart.)
            my_log("seek: delta={}s → target={}s".format(int(delta_secs), target))
            _th = target // 3600; _tm = (target % 3600) // 60; _ts = target % 60
            _arr = u"➡" if delta_secs > 0 else u"⬅"
            self["status"].setText(u"{} {:02d}:{:02d}:{:02d}".format(_arr, _th, _tm, _ts))
            self.__showOSD(True)
            self._hide_timer.start(2500, True)
        except Exception as e:
            my_log("seek error: {}".format(e))

    def __seekPct(self, pct):
        """Number keys: jump to a % of total duration (needs total; asks
        GStreamer for it when the OSD hasn't learned it yet).
        v4.1: self-contained dedup (keys 1/3/4/6/7/9 can dispatch BOTH
        as "N" and "seekdef:N") + telemetry log for diagnostics."""
        try:
            now = time.time()
            if pct == getattr(self, "_seekPctLastPct", None) and \
                    (now - getattr(self, "_seekPctLastT", 0.0)) < 0.30:
                return
            self._seekPctLastPct = pct
            self._seekPctLastT = now

            total = self._total_secs
            if not total:
                try:
                    svc = self.session.nav.getCurrentService()
                    seek = svc and svc.seek()
                    if seek:
                        r = seek.getLength()
                        if r and r[0] == 0 and r[1] > 0:
                            total = r[1] // 90000
                            self._total_secs = total
                except Exception:
                    pass
            cur = current_play_secs()
            my_log("seekPct: key={}%, total={}s, current={}s".format(pct, total, cur))
            if not total:
                self["status"].setText("مدة غير معروفة بعد…")
                self.__showOSD(True)
                return
            self.__seek(int(total * pct / 100) - cur)
        except Exception as e:
            my_log("seekPct error: {}".format(e))

    def __onRestart(self):
        # Gated: don't fire while Studio overlay or auto-next card is open
        if getattr(self, "_studioOverlayActive", False):
            return
        if getattr(self, "_autonext_active", False):
            return
        my_log("Restart+Resume requested by green button")
        if self._item_url:
            try:
                if self._paused:
                    secs = self._paused_elapsed
                else:
                    secs = current_play_secs()
                if secs > 30:
                    _save_position(self._item_url, secs, force=True)
                    self._resume_pos = secs
                    my_log("Restart: saved pos={}s, will re-seek after restart".format(secs))
            except Exception as e:
                my_log("Restart pos-save error: {}".format(e))
        try:
            self._seek_timer.stop()
            self._seek_verify_timer.stop()
        except: pass
        self._play_confirmed = False
        self._seek_retry_count = 0
        try:
            self.session.nav.stopService()
        except: pass
        self._candidate_idx = -1
        self["status"].setText(u"إعادة التشغيل + استئناف من {}:{:02d}...".format(
            self._resume_pos // 60, self._resume_pos % 60) if self._resume_pos > 30 else u"إعادة التشغيل...")
        self.__showOSD(True)
        self._restart_timer = eTimer()
        self._restart_timer.callback.append(self.__playNext)
        self._restart_timer.start(500, True)

    def __onExit(self, clear_position=False):
        try:
            self._hideAutoNextCard()
        except Exception:
            pass
        try:
            self._hideStudioOverlay()
        except Exception:
            pass
        # Fix X: stop the tracker BEFORE the save/clear block — its final
        # force-flush used to run AFTER the clear, resurrecting the EOF
        # position the clear just zeroed.
        stop_pos_tracker()
        try:
            if self._item_url:
                if clear_position:
                    _save_position(self._item_url, 0, force=True)
                    my_log("Exit save: cleared position (EOF)")
                else:
                    if self._paused:
                        secs = self._paused_elapsed
                    else:
                        secs = current_play_secs()
                    _tot = self._total_secs
                    if _tot > 0:
                        secs = min(secs, _tot - 5)
                    secs = max(0, secs)
                    if secs > 30:
                        _save_position(self._item_url, secs, force=True)
                        my_log("Exit save: {}s".format(secs))
        except Exception as e:
            my_log("Exit save error: {}".format(e))
        try:
            self.session.nav.stopService()
        except: pass
        try:
            from novaplay_substudio import STUDIO
            STUDIO.unbind()
        except Exception:
            pass
        # (tracker already stopped at the top)
        if not getattr(self, "_skip_restore", False):
            _restore_previous_service(self.session, self.previous_service)
        self.close()

    def __stop(self):
        self.__hideOSD()
        # Fix Y: _next_open_timer is NOT stopped here — its callback only
        # opens the next episode's Detail screen and never touches a
        # player widget; stopping it killed the auto-next chain during
        # teardown (log: card shown, next episode never opened).
        for t in ("_seek_timer","_seek_verify_timer","_retry_timer","_hide_timer",
                  "_osd_update_timer","_force_confirmation_timer","_restart_timer",
                  "_sleep_timer","_autonext_timer",
                  "_studioTimer","_rec_blink_timer"):
            try:
                timer = getattr(self, t, None)
                if timer: timer.stop()
            except Exception: pass

    # ─── resume seek (tracker-attribute form) ───────────────────────────
    def __doSeek(self):
        if not self._resume_pos or self._resume_pos <= 30:
            my_log("Seek skipped: resume_pos={}".format(self._resume_pos))
            return
        try:
            svc = self.session.nav.getCurrentService()
            seek = svc and svc.seek()
            if not seek:
                self._seek_retry_count += 1
                if self._seek_retry_count <= 3:
                    my_log("doSeek: no seek interface, retry {}/3 in 4s".format(self._seek_retry_count))
                    self._seek_timer.start(4000, True)
                else:
                    my_log("doSeek: giving up after 3 retries")
                return
            seek.seekTo(self._resume_pos * 90000)
            my_log("Resume seekTo: {}s (attempt {})".format(self._resume_pos, self._seek_retry_count + 1))
            self._total_secs = 0
            self._seek_verify_timer.start(4000, True)
            if self._osd_visible:
                self.__updateOSD()
        except Exception as e:
            my_log("doSeek failed: {} — retry {}/3".format(e, self._seek_retry_count))
            self._seek_retry_count += 1
            if self._seek_retry_count <= 3:
                self._seek_timer.start(4000, True)

    def __verifySeek(self):
        if not self._resume_pos or self._resume_pos <= 30: return
        try:
            svc = self.session.nav.getCurrentService()
            seek = svc and svc.seek()
            actual_pos = -1
            if seek:
                try:
                    r = seek.getPlayPosition()
                    if r and r[0] == 0 and r[1] > 0:
                        actual_pos = int(r[1] // 90000)
                except Exception: pass
            if actual_pos >= 0:
                if actual_pos >= max(0, self._resume_pos - 60):
                    with novaplay_tracker._GLOBAL_POS_LOCK:
                        novaplay_tracker._GLOBAL_PLAY_START_POS = actual_pos
                        novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
                        novaplay_tracker._GLOBAL_LAST_SEEK_TARGET = actual_pos
                    if self._paused:
                        self._paused_elapsed = actual_pos
                    my_log("verifySeek OK via PTS: actual={}s target={}s".format(actual_pos, self._resume_pos))
                else:
                    if seek and self._seek_retry_count <= 3:
                        self._seek_retry_count += 1
                        seek.seekTo(self._resume_pos * 90000)
                        my_log("verifySeek double-tap {}/3: actual={}s target={}s".format(self._seek_retry_count, actual_pos, self._resume_pos))
                        self._seek_verify_timer.start(3000, True)
                    else:
                        with novaplay_tracker._GLOBAL_POS_LOCK:
                            novaplay_tracker._GLOBAL_PLAY_START_POS = max(0, self._resume_pos - 2)
                            novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
                        my_log("verifySeek giving up, setting display to target {}s".format(self._resume_pos))
            else:
                if self._seek_retry_count <= 2:
                    if seek:
                        seek.seekTo(self._resume_pos * 90000)
                    self._seek_retry_count += 1
                    with novaplay_tracker._GLOBAL_POS_LOCK:
                        novaplay_tracker._GLOBAL_PLAY_START_POS = max(0, self._resume_pos - 2)
                        novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
                        novaplay_tracker._GLOBAL_LAST_SEEK_TARGET = self._resume_pos
                    if self._paused:
                        self._paused_elapsed = self._resume_pos
                    my_log("verifySeek double-tap {}/3 (no PTS), target={}s".format(self._seek_retry_count, self._resume_pos))
                    self._seek_verify_timer.start(3000, True)
                else:
                    my_log("verifySeek: max retries reached, target={}s".format(self._resume_pos))
        except Exception as e:
            my_log("verifySeek error: {}".format(e))

    # ─── player menu ─────────────────────────────────────────────────────
    def _playerMenu(self):
        rec_on = (getattr(self, "_record_task", None) is not None
                  and self._record_task.status == "downloading")
        items = [
            ("Subtitle — الترجمة", "subs"),
            ("Audio track — الصوت", "audio"),
            ("Aspect ratio — نسبة العرض: %s" % self._aspectLabel(), "aspect"),
            ("Stream info — معلومات", "info"),
            ("Next-episode prompt — التالي تلقائياً: %s" % ("ON" if self._next_prompt_enabled else "OFF"), "nxtep"),
            ("Sleep timer — مؤقت النوم: %s" % self._sleepLabel(), "sleep"),
            ("%s Record stream — تسجيل البث" % ("● REC" if rec_on else "○"), "record"),
            ("Stop recording — إيقاف التسجيل", "stoprec"),
        ]
        self.session.openWithCallback(self._onPlayerMenuChoice, ChoiceBox, "Player Menu", items)

    def _onPlayerMenuChoice(self, choice):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "subs":
            self._onSubtitles()
        elif action == "audio":
            self.__audioSelect()
        elif action == "aspect":
            self.__cycleAspect()
        elif action == "info":
            self.__streamInfo()
        elif action == "nxtep":
            self._next_prompt_enabled = not self._next_prompt_enabled
            _set_config("next_episode_prompt", "true" if self._next_prompt_enabled else "false")
            self["status"].setText("Next-episode: %s" % ("ON" if self._next_prompt_enabled else "OFF"))
            self.__showOSD(True)
        elif action == "sleep":
            self.__sleepMenu()
        elif action == "record":
            self.__recordCurrentStream()
        elif action == "stoprec":
            self.__stopRecording()

    # ─── aspect (pillarbox) ──────────────────────────────────────────────
    def _aspectLabel(self):
        try:
            return self._ASPECT_MODES[getattr(self, "_aspect_idx", 0)][0]
        except Exception:
            return "Full"

    def __cycleAspect(self):
        # Gated: don't fire while Studio overlay or auto-next card is open
        if getattr(self, "_studioOverlayActive", False):
            return
        if getattr(self, "_autonext_active", False):
            return
        self._aspect_idx = (getattr(self, "_aspect_idx", 0) + 1) % len(self._ASPECT_MODES)
        label, bar = self._ASPECT_MODES[self._aspect_idx]
        try:
            self["aspect_bar_l"].instance.resize(eSize(bar if bar else 1, 1080))
            if bar:
                self["aspect_bar_l"].instance.move(ePoint(0, 0))
                self["aspect_bar_r"].instance.resize(eSize(bar, 1080))
                self["aspect_bar_r"].instance.move(ePoint(1920 - bar, 0))
            else:
                self["aspect_bar_r"].instance.resize(eSize(1, 1080))
                self["aspect_bar_r"].instance.move(ePoint(1919, 0))
        except Exception as e:
            my_log("aspect error: {}".format(e))
        self["status"].setText("Aspect: %s" % label)
        self.__showOSD(True)

    # ─── audio ───────────────────────────────────────────────────────────
    def __audioSelect(self):
        try:
            if AudioSelection is not None:
                self.session.open(AudioSelection, self)
                return
        except Exception:
            pass
        try:
            service = self.session.nav.getCurrentService()
            if not service:
                return
            tracks = service.audioTracks()
            if not tracks:
                self["status"].setText("Audio: N/A")
                return
            n = tracks.getNumberOfTracks()
            if n <= 1:
                self["status"].setText("Audio: 1 track only")
                return
            cur = tracks.getCurrentTrack()
            entries = []
            for i in range(n):
                try:
                    name = tracks.getTrackName(i) or ""
                except Exception:
                    name = ""
                entries.append(("Track %d: %s%s" % (i + 1, name, " ▶" if i == cur else ""), i))
            self.session.openWithCallback(self.__audioPicked, ChoiceBox, "Audio", entries)
        except Exception as e:
            my_log("audioSelect error: {}".format(e))

    def __audioPicked(self, choice):
        if not choice:
            return
        idx = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        try:
            service = self.session.nav.getCurrentService()
            if service:
                tracks = service.audioTracks()
                if tracks:
                    tracks.selectTrack(int(idx))
                    self["status"].setText("Audio: track %d" % (int(idx) + 1))
                    self.__showOSD(True)
        except Exception as e:
            my_log("audioPicked error: {}".format(e))

    # ─── stream info ─────────────────────────────────────────────────────
    def __streamInfo(self):
        try:
            service = self.session.nav.getCurrentService()
            info = service.info() if service else None
            lines = [u"معلومات البث — Stream info"]
            if info is not None and iServiceInformation is not None:
                try:
                    x = info.getInfo(iServiceInformation.sWidth)
                    y = info.getInfo(iServiceInformation.sHeight)
                    if x > 0 and y > 0:
                        lines.append(u"Resolution: {}x{}".format(x, y))
                except Exception:
                    pass
                try:
                    fr = info.getInfo(iServiceInformation.sFrameRate)
                    if fr > 0:
                        lines.append(u"Frame rate: {} fps".format(fr))
                except Exception:
                    pass
            lines.append(u"Candidate: {} ({})".format(
                self._candidate_label or "-",
                (self._candidate_idx + 1) if self._candidate_idx >= 0 else "-"))
            try:
                st = novaplay_proxy.get_stats()
                lines.append(u"Proxy: {} req / {} errors / {} manifests".format(
                    st.get("requests", 0), st.get("upstream_errors", 0), st.get("manifests_rewritten", 0)))
            except Exception:
                pass
            self.session.open(MessageBox, u"\n".join(lines), MessageBox.TYPE_INFO, timeout=10)
        except Exception as e:
            my_log("streamInfo error: {}".format(e))

    # ─── sleep timer ─────────────────────────────────────────────────────
    def _sleepLabel(self):
        return "off" if not self._sleep_minutes else "%d min" % self._sleep_minutes

    def __sleepMenu(self):
        entries = [("Off", 0), ("15 min", 15), ("30 min", 30), ("60 min", 60), ("90 min", 90)]
        self.session.openWithCallback(self.__sleepPicked, ChoiceBox, "Sleep timer — مؤقت النوم", entries)

    def __sleepPicked(self, choice):
        if not choice:
            return
        mins = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        self._sleep_minutes = int(mins or 0)
        try:
            if self._sleep_timer is not None:
                self._sleep_timer.stop()
        except Exception:
            pass
        self._sleep_timer = None
        if self._sleep_minutes > 0:
            self._sleep_timer = eTimer()
            self._sleep_timer.callback.append(self.__sleepExpired)
            self._sleep_timer.start(self._sleep_minutes * 60000, True)
            self["status"].setText("Sleep: %d min" % self._sleep_minutes)
        else:
            self["status"].setText("Sleep: off")
        self.__showOSD(True)

    def __sleepExpired(self):
        my_log("Sleep timer expired — exiting playback")
        self.__onExit()

    # ─── record while watching ───────────────────────────────────────────
    def __recordCurrentStream(self):
        if getattr(self, "_record_task", None) and self._record_task.status == "downloading":
            self["status"].setText("Recording already running")
            self.__showOSD(True)
            return
        url = ""
        referer = ""
        for cand in (self.candidates or []):
            if len(cand) >= 4 and cand[3]:
                # Decode the proxy URL back to the upstream stream URL —
                # download_manager's extension dispatch (HLS vs direct)
                # must see the real .m3u8, not the /stream wrapper whose
                # only '?' is the proxy's own query delimiter.
                try:
                    from urllib.parse import urlparse, parse_qs, unquote
                    pp = urlparse(str(cand[1]))
                    if pp.path == "/stream":
                        qs = parse_qs(pp.query or "")
                        url = unquote((qs.get("url") or [""])[0]).strip()
                        referer = unquote((qs.get("referer") or [""])[0]).strip()
                except Exception:
                    url = str(cand[1])
                if url:
                    break
        if not url:
            try:
                url = str(self.sref.getPath() or "").strip()
            except Exception:
                url = ""
        if not url or not url.startswith("http"):
            self["status"].setText("Recording: no stream URL")
            self.__showOSD(True)
            return
        try:
            from plugin_downloads import DownloadTask, download_manager
            task = DownloadTask(title=self.title, url=url, referer=referer, item_url=self._item_url)
            self._record_task = task
            self["status"].setText("Recording started...")
            self._updateRecBlink()
            self.__showOSD(True)

            def _bg():
                try:
                    dest = download_manager(task, title_hint=self.title)
                    callInMainThread(self.__recDone, os.path.basename(dest))
                except Exception as e:
                    if task.status != "cancelled":
                        callInMainThread(self.__recDone, "failed: {}".format(str(e)[:40]))
                finally:
                    callInMainThread(self._updateRecBlink)

            threading.Thread(target=_bg, daemon=True).start()
        except Exception as e:
            my_log("record error: {}".format(e))
            self["status"].setText("Recording unavailable")
            self.__showOSD(True)

    def __stopRecording(self):
        task = getattr(self, "_record_task", None)
        if task and task.status == "downloading":
            task.cancel()
            self["status"].setText("Recording stopped")
        else:
            self["status"].setText("Not recording")
        self._updateRecBlink()
        self.__showOSD(True)

    def __recDone(self, msg):
        try:
            self["status"].setText("Recorded: {}".format(msg))
            self.__showOSD(True)
        except Exception:
            pass

    # ─── REC blink ───────────────────────────────────────────────────────
    def _updateRecBlink(self):
        try:
            task = getattr(self, "_record_task", None)
            if task is not None and task.status == "downloading":
                self._rec_blink_on = True
                self["recBlink"].setText("● REC")
                self["recBlink"].show()
                try: self._rec_blink_timer.start(650, True)
                except Exception: pass
            else:
                try: self._rec_blink_timer.stop()
                except Exception: pass
                self["recBlink"].hide()
        except Exception:
            pass

    def __tickRecBlink(self):
        try:
            task = getattr(self, "_record_task", None)
            if task is None or task.status != "downloading":
                self._updateRecBlink()
                return
            self._rec_blink_on = not self._rec_blink_on
            if self._rec_blink_on:
                self["recBlink"].show()
            else:
                self["recBlink"].hide()
            self._rec_blink_timer.start(650, True)
        except Exception:
            pass

    # ─── subtitles: menu + quick-delay ───────────────────────────────────
    def __subtitleDelayBack(self):
        # RED during playback: quick subtitle sync nudge (the full
        # +/-/reset set stays in the subtitle menu).
        # Gated: don't fire while Studio overlay or auto-next card is open
        if getattr(self, "_studioOverlayActive", False):
            return
        if getattr(self, "_autonext_active", False):
            return
        try:
            adjust_sync(self.session, -SYNC_STEP_MS)
            state = get_subtitle_state()
            off = int(state.get("offset_ms") or 0)
            self["status"].setText("ترجمة: %+d ms" % off)
        except Exception as e:
            my_log("subtitle delay error: {}".format(e))
        self.__showOSD(True)

    def _onSubtitles(self):
        studio_on = str(_get_config("substudio_mode", "false")).lower() == "true"
        items = [
            ("Subtitle Studio — تنسيق الترجمة (%s)" % ("ON" if studio_on else "OFF"), "studio_toggle"),
        ]
        if studio_on:
            items.append(("Studio settings — إعدادات التنسيق", "studio_open"))
        items.append(("Browse local subtitles", "browse"))
        items.append(("Search online (SubSource)", "subsource"))
        items.append(("Search online (OpenSubtitles)", "opensubtitles"))
        state = get_subtitle_state()
        if state.get("path"):
            items.append(("Sync +%d ms" % SYNC_STEP_MS, "sync+"))
            items.append(("Sync -%d ms" % SYNC_STEP_MS, "sync-"))
            items.append(("Reset sync", "sync0"))
            items.append(("Disable subtitles", "off"))
        self.session.openWithCallback(self._onSubtitleAction, ChoiceBox, "Subtitles", items)

    def _onSubtitleAction(self, result):
        if not result:
            return
        choice = result[1]
        if choice == "studio_toggle":
            from novaplay_substudio import STUDIO
            was_on = str(_get_config("substudio_mode", "false")).lower() == "true"
            _set_config("substudio_mode", "false" if was_on else "true")
            if not was_on:
                state = get_subtitle_state()
                path = state.get("_orig_path") or state.get("path") or ""
                if path and os.path.exists(path):
                    apply_subtitle(self.session, path)
                self._showStudioOverlay()
            else:
                STUDIO.detach()
                state = get_subtitle_state()
                path = state.get("_orig_path") or state.get("path") or ""
                if path and os.path.exists(path):
                    from novaplay_subtitles import _apply_path_with_offset
                    _apply_path_with_offset(self.session, self._item_url, path,
                                            int(state.get("offset_ms") or 0))
            return
        if choice == "studio_open":
            if not getattr(self, "_studioOverlayActive", False):
                self._showStudioOverlay()
            return
        if choice == "browse":
            self.session.openWithCallback(self._onSubtitlePicked,
                                          NovaSubtitleBrowser, "", self.title)
        elif choice in ("subsource", "opensubtitles"):
            self.session.openWithCallback(self._onSubtitlePicked,
                                          NovaOnlineSubsScreen, self.title,
                                          self._item_url, choice,
                                          _cfg(choice + "_api_key", ""))
        elif choice == "sync+":
            my_log(adjust_sync(self.session, 250))
        elif choice == "sync-":
            my_log(adjust_sync(self.session, -250))
        elif choice == "sync0":
            my_log(reset_sync(self.session))
        elif choice == "off":
            disable_subtitle(self.session)

    def _onSubtitlePicked(self, path):
        if path and os.path.exists(path):
            remember_subtitle(self._item_url, path, 0)
            apply_subtitle(self.session, path)

    # ── Subtitle Studio overlay (v3) ─────────────────────────────────────
    def _studioItemsList(self):
        from novaplay_substudio import STUDIO
        s = STUDIO.style
        align_lbl = ["Top — أعلى", "Bottom — أسفل", "Center — وسط"][s["align"]]
        font_lbl = os.path.basename(s.get("font_path") or "") or s.get("font_name") or "Default"
        return [
            ("Delay — التأخير", "%+d ms" % STUDIO.get_offset(), "delay", 250),
            ("Size — الحجم", str(s["size"]), "size", 2),
            ("Font — الخط", font_lbl, "font", 1),
            ("Color — اللون", ["White","Yellow","Gold","Cyan"][s["color_idx"]], "color", 1),
            ("Outline — الحدود", "On" if s["outline"] else "Off", "outline", 1),
            ("Outline color — لون الحدود", ["White","Yellow","Gold","Cyan"][s["outline_color_idx"]], "ocolor", 1),
            ("Background — الخلفية", "On" if s["bg"] else "Off", "bg", 1),
            ("BG Alpha — الشفافية", str(s["bg_alpha"]), "bg_alpha", 16),
            ("Position — الموضع", str(s["offset_y"]), "offset_y", 10),
            ("Align — المحاذاة", align_lbl, "align", 1),
            ("Spacing — التباعد", str(s["spacing"]), "spacing", 2),
            ("Auto-wrap — التفاف تلقائي", "On" if s.get("auto_wrap", True) else "Off", "autowrap", 1),
            ("Cue position — موضع الملف", "On" if s.get("use_cue_pos", True) else "Off", "cuepos", 1),
            ("Presets — قوالب", "A / B / C", "presets", 0),
            # Fix C: Pick line to sync
            ("Pick line to sync — مزامنة بالسطر", "", "pickline", 0),
            ("Reset — افتراضي", "", "reset", 0),
            ("Remove — إيقاف", "", "remove", 0),
        ]

    def _showStudioOverlay(self):
        self._studioOverlayActive = True
        self._studioItems = self._studioItemsList()
        if self._studioIdx >= len(self._studioItems):
            self._studioIdx = 0
        # Pause the subtitle render tick while the settings overlay is
        # open — both compete for the main thread and key presses feel
        # laggy when they interleave.
        try:
            self._studioTimer.stop()
        except Exception:
            pass
        for k in self._STUDIO_KEYS:
            try:
                self[k].show()
            except Exception:
                pass
        self._studioRefresh()

    def _hideStudioOverlay(self):
        self._studioOverlayActive = False
        # Resume the subtitle render tick
        try:
            self._studioTimer.start(100, False)
        except Exception:
            pass
        for k in self._STUDIO_KEYS:
            try:
                self[k].hide()
            except Exception:
                pass
        try:
            from novaplay_substudio import STUDIO
            STUDIO.flush_style()
        except Exception:
            pass
        for k in ("safeTop", "safeBottom", "safeLeft", "safeRight"):
            try:
                self[k].hide()
            except Exception:
                pass
        try:
            from novaplay_substudio import STUDIO
            STUDIO.flush_style()
        except Exception:
            pass

    def _studioRefresh(self):
        total = len(self._studioItems)
        if not total:
            return
        idx = max(0, min(self._studioIdx, total - 1))
        # True carousel: the selected item ALWAYS sits in the center
        # card (studioCard2 — the blue one). Items beyond the list
        # edges render as hidden cards, so at Delay / at Remove the
        # selection is still centered.
        self._studioWin = idx - 2
        for slot, card in enumerate(self._STUDIO_CARDS):
            item_i = self._studioWin + slot
            txt = ""
            if 0 <= item_i < total:
                label, value, _k, _s = self._studioItems[item_i]
                txt = "%s\n%s" % (label, value) if value else label
            try:
                self[card].setText(txt)
                if txt:
                    self[card].show()
                else:
                    self[card].hide()
            except Exception:
                pass
        try:
            self["studioStatus"].setText("بث مباشر للتعديلات")
        except Exception:
            pass
        try:
            from novaplay_substudio import STUDIO
            body = STUDIO.get_preview()
            text = body[0] if body else ""
            if len(body) > 1 and body[1]:
                text = text + "\n" + body[1][:70]
            self["studioPreview"].setText(text or "لا توجد ترجمة نشطة")
            try:
                self["studioPreview"].instance.setFont(
                    gFont("Regular", max(22, min(int(STUDIO.style["size"]), 34))))
            except Exception:
                pass
        except Exception:
            pass
        cur_key = self._studioItems[idx][2] if self._studioItems else ""
        show_safe = cur_key in ("offset_y", "align")
        for k in ("safeTop", "safeBottom", "safeLeft", "safeRight"):
            try:
                if show_safe:
                    self[k].show()
                else:
                    self[k].hide()
            except Exception:
                pass
            item_i = self._studioWin + slot
            txt = ""
            if 0 <= item_i < total:
                label, value, _k, _s = self._studioItems[item_i]
                txt = "%s\n%s" % (label, value) if value else label
            try:
                self[card].setText(txt)
                if txt:
                    self[card].show()
                else:
                    self[card].hide()
            except Exception:
                pass
        try:
            self["studioStatus"].setText("بث مباشر للتعديلات")
        except Exception:
            pass
        try:
            from novaplay_substudio import STUDIO
            body = STUDIO.get_preview()
            text = body[0] if body else ""
            if len(body) > 1 and body[1]:
                text = text + "\n" + body[1][:70]
            self["studioPreview"].setText(text or "لا توجد ترجمة نشطة")
            try:
                self["studioPreview"].instance.setFont(
                    gFont("Regular", max(22, min(int(STUDIO.style["size"]), 34))))
            except Exception:
                pass
        except Exception:
            pass
        cur_key = self._studioItems[idx][2] if self._studioItems else ""
        show_safe = cur_key in ("offset_y", "align")
        for k in ("safeTop", "safeBottom", "safeLeft", "safeRight"):
            try:
                if show_safe:
                    self[k].show()
                else:
                    self[k].hide()
            except Exception:
                pass

    def _studioMove(self, step):
        total = len(self._studioItems)
        if not total:
            return
        self._studioIdx = max(0, min(self._studioIdx + step, total - 1))
        self._studioRefresh()

    def _studioAdjust(self, step):
        if not (0 <= self._studioIdx < len(self._studioItems)):
            return
        from novaplay_substudio import STUDIO
        label, value, key, stp = self._studioItems[self._studioIdx]
        s = STUDIO.style
        if key == "delay":
            adjust_sync(self.session, stp * step)
        elif key == "size":
            s["size"] = max(22, min(80, s["size"] + stp * step))
            STUDIO._layout_static()
        elif key == "color":
            s["color_idx"] = (s["color_idx"] + step) % 4
            STUDIO._layout_static()
        elif key == "bg":
            s["bg"] = not s["bg"]
        elif key == "bg_alpha":
            s["bg_alpha"] = max(0, min(224, s["bg_alpha"] + stp * step))
            STUDIO._layout_static()
        elif key == "offset_y":
            s["offset_y"] = max(-250, min(120, s["offset_y"] + stp * step))
        elif key == "spacing":
            s["spacing"] = max(0, min(120, s["spacing"] + stp * step))
        elif key == "outline":
            s["outline"] = not s["outline"]
        elif key == "ocolor":
            s["outline_color_idx"] = (s["outline_color_idx"] + step) % 4
            STUDIO._layout_static()
        elif key == "align":
            s["align"] = (s["align"] + step) % 3
        elif key == "autowrap":
            s["auto_wrap"] = not s.get("auto_wrap", True)
        elif key == "cuepos":
            s["use_cue_pos"] = not s.get("use_cue_pos", True)
            STUDIO._last_rendered = None
        elif key == "presets":
            self._studioPresetMenu()
            return
        elif key == "font":
            self._studioFontBrowser()
            return
        elif key == "reset":
            STUDIO.reset_style()
        elif key == "remove":
            disable_subtitle(self.session)
        # Fix C: Handle pickline key
        elif key == "pickline":
            self._studioPickLine()
            return
        self._studioItems = self._studioItemsList()
        self._studioRefresh()

    # Fix C: "Pick Line to Sync" methods
    def _studioPickLine(self):
        from novaplay_substudio import STUDIO
        if not STUDIO.is_attached():
            return
        self._hideStudioOverlay()
        entries = []
        for i, (start, end, body, _, _) in enumerate(STUDIO._cues):
            lines_preview = " / ".join(body[:2])
            entries.append((lines_preview[:60], i))
        self.session.openWithCallback(
            self._onStudioPickLine, ChoiceBox,
            "اختر السطر المطابق لما يظهر الآن", entries)

    def _onStudioPickLine(self, choice):
        from novaplay_substudio import STUDIO
        from novaplay_subtitles import get_subtitle_state, remember_subtitle
        if not choice:
            self._showStudioOverlay()
            return
        idx = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        cue = STUDIO._cues[idx]
        line_time = cue[0] // 1000  # ms → sec
        current = current_play_secs()
        target_delay = current - line_time
        STUDIO.set_offset(int(target_delay * 1000))
        STUDIO._last_rendered = None
        state = get_subtitle_state()
        # Store the offset in the subtitle state
        state["offset_ms"] = int(target_delay * 1000)
        if state.get("item_url"):
            remember_subtitle(state["item_url"], STUDIO.get_path(), int(target_delay * 1000))
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()

    def _studioOk(self):
        if not (0 <= self._studioIdx < len(self._studioItems)):
            return
        label, value, key, stp = self._studioItems[self._studioIdx]
        if key == "presets":
            self._studioPresetMenu()
            return
        if key == "font":
            self._studioFontBrowser()
            return
        # Fix C: Handle pickline key in _studioOk
        if key == "pickline":
            self._studioPickLine()
            return
        if key in ("delay", "size", "bg_alpha", "offset_y", "spacing", "color", "bg",
                   "outline", "ocolor", "align"):
            choices = []
            if key == "delay":
                choices = [("%+d ms" % v, v) for v in range(-5000, 5001, 250)]
            elif key == "size":
                choices = [(str(v), v) for v in range(22, 81, 2)]
            elif key == "color":
                choices = [("White", 0), ("Yellow", 1), ("Gold", 2), ("Cyan", 3)]
            elif key == "bg":
                choices = [("On", 1), ("Off", 0)]
            elif key == "bg_alpha":
                choices = [(str(v), v) for v in range(0, 225, 16)]
            elif key == "offset_y":
                choices = [(str(v), v) for v in range(-250, 121, 10)]
            elif key == "spacing":
                choices = [(str(v), v) for v in range(0, 121, 2)]
            elif key == "outline":
                choices = [("On", 1), ("Off", 0)]
            elif key == "ocolor":
                choices = [("White", 0), ("Yellow", 1), ("Gold", 2), ("Cyan", 3)]
            elif key == "align":
                choices = [("Top — أعلى", 0), ("Bottom — أسفل", 1), ("Center — وسط", 2)]
            self._studioChoiceKey = key
            self._hideStudioOverlay()
            self.session.openWithCallback(self._onStudioChoice, ChoiceBox,
                                          title=label, list=choices)
            return
        self._studioAdjust(+1 if key in ("reset", "remove") else 1)

    def _onStudioChoice(self, choice):
        if not choice:
            self._showStudioOverlay()
            return
        value = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        key = getattr(self, "_studioChoiceKey", "")
        from novaplay_substudio import STUDIO
        s = STUDIO.style
        try:
            if key == "delay":
                adjust_sync(self.session, int(value) - STUDIO.get_offset())
            elif key == "size":
                s["size"] = int(value)
                STUDIO._layout_static()
            elif key == "color":
                s["color_idx"] = int(value) % 4
                STUDIO._layout_static()
            elif key == "bg":
                s["bg"] = bool(value)
            elif key == "bg_alpha":
                s["bg_alpha"] = int(value)
                STUDIO._layout_static()
            elif key == "offset_y":
                s["offset_y"] = int(value)
            elif key == "spacing":
                s["spacing"] = int(value)
            elif key == "outline":
                s["outline"] = bool(value)
            elif key == "ocolor":
                s["outline_color_idx"] = int(value) % 4
                STUDIO._layout_static()
            elif key == "align":
                s["align"] = int(value) % 3
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()

    def _studioPresetMenu(self):
        items = []
        for slot in ("A", "B", "C"):
            items.append(("Load %s — تحميل" % slot, "load_" + slot))
            items.append(("Save %s — حفظ" % slot, "save_" + slot))
        self._hideStudioOverlay()
        self.session.openWithCallback(self._onStudioPreset, ChoiceBox,
                                      "Style Presets — القوالب", items)

    def _onStudioPreset(self, choice):
        from novaplay_substudio import STUDIO
        if not choice:
            self._showStudioOverlay()
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        slot = str(action)[-1] if action else "A"
        if str(action).startswith("save_"):
            STUDIO.save_preset(slot)
            STUDIO.flush_style()
        elif str(action).startswith("load_"):
            STUDIO.load_preset(slot)
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()

    def _studioFontBrowser(self):
        try:
            from Components.FileList import FileList
        except Exception:
            FileList = None
        if FileList is None:
            self["status"].setText("Font browser unavailable")
            self.__showOSD(True)
            return
        self._hideStudioOverlay()

        class _FontPick(Screen):
            skin = """
            <screen name="NovaFontPick" position="center,center" size="1500,860" title="Select Font" flags="wfNoBorder">
                <eLabel position="0,0" size="1500,860" backgroundColor="#0D1117" zPosition="0" />
                <eLabel position="0,0" size="1500,80" backgroundColor="#161B22" zPosition="1" />
                <widget name="path" position="40,18" size="1420,44" font="Regular;30" foregroundColor="#00E5FF" transparent="1" zPosition="2" />
                <widget name="list" position="30,100" size="1440,680" scrollbarMode="showOnDemand" zPosition="2" />
                <widget name="hint" position="40,800" size="1420,36" font="Regular;24" foregroundColor="#8B949E" transparent="1" zPosition="2" />
            </screen>"""
            def __init__(self2, session, start_dir):
                Screen.__init__(self2, session)
                self2["path"] = Label(start_dir)
                self2["list"] = FileList(start_dir, showDirectories=True,
                                         showFiles=True,
                                         matchingPattern=r"^.*\.(ttf|otf|ttc)$")
                self2["hint"] = Label("OK اختيار  |  EXIT رجوع  |  YELLOW الخط الافتراضي")
                self2["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
                    "ok": self2._ok,
                    # always close WITH an argument — a bare close() fires
                    # the callback with none → processDelay TypeError
                    "cancel": lambda: self2.close(""),
                    "yellow": lambda: self2.close(""),
                    "up": lambda: self2["list"].up(),
                    "down": lambda: self2["list"].down(),
                    "left": lambda: self2["list"].pageUp(),
                    "right": lambda: self2["list"].pageDown(),
                }, -1)
            def _ok(self2):
                try:
                    if self2["list"].canDescent():
                        self2["list"].descent()
                        try:
                            self2["path"].setText(str(self2["list"].getCurrentDirectory() or ""))
                        except Exception:
                            pass
                        return
                except Exception:
                    pass
                try:
                    sel = self2["list"].getFilename()
                    if sel:
                        self2.close(sel)
                except Exception:
                    pass

        start = "/media/hdd" if os.path.exists("/media/hdd") else "/"
        self.session.openWithCallback(self._onStudioFontPicked, _FontPick, start)

    def _onStudioFontPicked(self, path=None):
        """Callback from the font browser (_FontPick).

        Called via session.openWithCallback. On PICK: path = selected
        .ttf/.otf/.ttc. On EXIT/cancel: Enigma2's deferred processDelay
        may deliver NO argument at all (bare close) — hence path=None.
        Empty/None → revert to the default font.
        """
        from novaplay_substudio import STUDIO
        try:
            if path:
                STUDIO.register_font(path)
            else:
                STUDIO.use_default_font()
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()


# ─── Global play function ──────────────────────────────────────────────────

def _play(session, url, title, resume_pos=0, item_url="",
          next_episode=None, on_next=None, poster_url=""):
    try:
        svc_url = str(url).strip()
        is_remote = svc_url.startswith("http://") or svc_url.startswith("https://")
        previous_service = _capture_previous_service(session)
        if is_remote:
            session.open(AdvancedArabicPlayerSimplePlayer, title,
                         _build_remote_play_candidates(svc_url), previous_service,
                         resume_pos=resume_pos, item_url=item_url,
                         next_episode=next_episode, on_next=on_next,
                         poster_url=poster_url)
            return
        sref = eServiceReference(4097, 0, svc_url)
        if sys.version_info[0] == 3:
            sref.setName(str(title))
        else:
            sref.setName(title.encode("utf-8", "ignore"))
        try:
            from Screens.InfoBar import MoviePlayer
            callback = lambda *args: _restore_previous_service(session, previous_service)
            try:
                session.openWithCallback(callback, MoviePlayer, sref, askBeforeLeaving=False)
            except TypeError:
                session.openWithCallback(callback, MoviePlayer, sref)
        except Exception as e:
            my_log("[PLAY_INFOBAR_FALLBACK] " + str(e))
            session.open(AdvancedArabicPlayerSimplePlayer, title,
                         _build_remote_play_candidates(svc_url), previous_service,
                         next_episode=next_episode, on_next=on_next,
                         poster_url=poster_url)
    except Exception as e:
        my_log("[PLAY_ERROR] " + str(e))