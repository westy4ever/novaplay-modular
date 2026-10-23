# -*- coding: utf-8 -*-
"""NovaPlay — playback screen + service orchestration.

MODULAR EXTRACTION of NovaPlaySimplePlayer plus _play /
_build_remote_play_candidates / service capture-restore. Full feature
set with ALL session patches baked in:

  * tracker via novaplay_tracker module attributes
  * Fix Y: _next_open_timer NOT stopped in __stop
  * Fix Z: double evEOF does not exit from under a visible card
  * OSD poster: 132x198 floating ABOVE the panel
  * auto-next card: portrait 100x150 with fallback chain
  * number keys 0-9 = proportional seeks
  * record: decodes the proxy URL back to the upstream stream
  * [PATCH 29] eAVSwitch aspect probes with restore-on-exit
  * [PATCH 32] per-site "candidate that worked" memory
  * [PATCH 33] verifySeek trusts the demuxer's PTS
  * [PATCH 65-B3] per-title quality preference
  * [PATCH 65-A3] next-episode countdown from config

[player-anim]
  * OSD slide-in/out (eased, exponential smoothing)
  * Seek-arrow flash overlay
  * Auto-next card slide-in/out from the right
  * Quality-switch wipe (mask the service restart)
  * Aspect-bar slide-in on 4:3 / 14:9 cycle
  * Studio-panel drop-down
  * Sync-offset toast on the right edge

[player-ux]
  * state glyph ▶/⏸/⏳/⚠ beside elapsed
  * watched at 90%
  * "✓ Saved at hh:mm" toast on exit
  * hold-to-accelerate seek
  * MENU = skip hint 3s into an extraction
  * retry dialog when all candidates fail
  * near-end exit prompt (next / mark / save / cancel)
  * disk-space guard before recording
"""

import os
import re
import sys
import time
import threading

from urllib.parse import quote, unquote, urlparse, parse_qs

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from novaplay_choice import NovaChoiceBox as ChoiceBox
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


def _tr_call(name, *a):
    try:
        import novaplay_transmission
        return getattr(novaplay_transmission, name)(*a)
    except Exception as e:
        my_log("transmission {}: {}".format(name, e))
from plugin_state import (_get_config, _set_config, _get_saved_position,
                          _save_position)
from plugin_util import SAFE_UA, _single_line_text
from novaplay_subtitles import (maybe_resume_subtitle, remember_subtitle,
                                apply_subtitle, get_subtitle_state,
                                SYNC_STEP_MS, adjust_sync, reset_sync,
                                disable_subtitle, turn_off_subtitle, _cfg,
                                has_subtitle_for, update_subtitle_state,
                                NovaSubtitleBrowser, NovaOnlineSubsScreen)
from plugin_assets import placeholder_for_item
import novaplay_tracker
from novaplay_tracker import (start_pos_tracker, stop_pos_tracker,
                              current_play_secs, current_play_ms)
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


# ── [B3] quality-preference key ─────────────────────────────────────────
def _quality_pref_key(title):
    """Normalize a title into a config key for its per-title quality
    preference. Series episode titles reduce to the series base."""
    t = str(title or "").strip()
    t = re.sub(r"\bS\d{1,2}E\d{1,2}\b.*$", "", t, flags=re.I)
    t = re.sub(r"\b\d{1,2}x\d{1,2}\b.*$", "", t)
    t = re.sub(r"\s*\[\d+p\]\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -|:")
    t = t.lower()
    t = re.sub(r"[^a-z0-9\u0600-\u06ff]+", "_", t)[:60].strip("_")
    return ("qpref_" + t) if t else ""


# ─── Remote-play candidate builder ──────────────────────────────────────
_DEFERRED_OPENS = []


def _defer_open_next(cb, nxt):
    """Fire cb(nxt) ~200ms after the player has fully closed."""
    t = eTimer()

    def _run():
        try:
            _DEFERRED_OPENS.remove(t)
        except Exception:
            pass
        my_log("autoNext: opening next episode")
        try:
            cb(nxt)
        except Exception as e:
            my_log("auto-next open error: {}".format(e))

    t.callback.append(_run)
    t.start(200, True)
    _DEFERRED_OPENS.append(t)


def _build_remote_play_candidates(url):
    url = str(url).strip()
    plain_url, pipe = (url.split("|", 1) + [""])[:2]
    if "#" in plain_url:
        plain_url, frag = plain_url.split("#", 1)
        pipe = frag if not pipe else (frag + "&" + pipe)
    headers = {}
    for part in pipe.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            try:
                v = unquote(v.strip())
            except Exception:
                v = v.strip()
            if k and k not in headers:
                headers[k] = v
    candidates = []
    seen = set()
    # XOR-obfuscated playlists (extractors/hlsobf.py): only the local proxy can decode them
    xor_key = next((v for k, v in headers.items() if k.lower() == "x-np-xor"), "")
    # [2026-10-21] a resolver can mark a stream proxy-only (X-NP-Proxy=1): e.g.
    # vscdn.xyz's hls4 playlist lists its video pieces as tiktokcdn "images" --
    # only the proxy strips that disguise, and a direct player just spins.
    proxy_only = any(k.lower() == "x-np-proxy" for k in headers)
    for _k in [k for k in headers if k.lower() == "x-np-proxy"]:
        headers.pop(_k, None)
    # [2026-10-21] X-NP-Strict: the stream's own Referer is required -- leave out
    # every header-less method (one hung Enigma2 on TopCinema's VideoTube)
    strict = any(k.lower() == "x-np-strict" for k in headers)
    for _k in [k for k in headers if k.lower() == "x-np-strict"]:
        headers.pop(_k, None)
    if strict:
        # drop the marker from the "+ headers" URL, same separator and format
        url = re.sub(r'(?i)(^|[#|&])X-NP-Strict=[^&]*&?', r'\1', url).rstrip("&#|")

    def add_candidate(p_type, svc_url, label, uses_proxy=False):
        key = (p_type, svc_url)
        if not svc_url or key in seen: return
        seen.add(key)
        candidates.append((p_type, svc_url, label, uses_proxy))

    # [2026-10-23] TorrServer stream (".../stream/<file>?link=<hash>&index=N&play"):
    # it's already a local HTTP server that reads ahead -- straight to the
    # player, never through NovaPlay's proxy (an extra hop on the box's CPU
    # that made torrents buffer). exteplayer3 first: it asks for byte ranges
    # (seek/resume work) and plays every container a torrent comes in.
    _pl = plain_url.lower()
    if "/stream/" in _pl and "link=" in _pl and ("&play" in _pl or "?play" in _pl):
        if os.path.exists("/usr/bin/exteplayer3"):
            add_candidate(5002, plain_url, "5002 exteplayer3 (TorrServer)")
        add_candidate(5001, plain_url, "5001 direct (TorrServer)")
        add_candidate(4097, plain_url, "4097 direct (TorrServer)")
        return candidates

    proxied = ""
    legacy_proxied = ""
    if plain_url.startswith("https://") or plain_url.startswith("http://"):
        if start_proxy():
            q = "url=" + quote(plain_url, safe="")
            if headers.get("Referer"):
                q += "&referer=" + quote(headers["Referer"], safe="")
            if headers.get("User-Agent"):
                q += "&ua=" + quote(headers["User-Agent"], safe="")
            if headers.get("Cookie"):
                q += "&cookie=" + quote(headers["Cookie"], safe="")
            if headers.get("Origin"):
                q += "&origin=" + quote(headers["Origin"], safe="")
            if xor_key:
                q += "&xor=" + quote(xor_key, safe="")
            if _wants_ahead_cache(plain_url):
                q += "&cache=1"
            proxied = "http://127.0.0.1:{}/stream?{}".format(_PROXY_PORT, q)
            # start downloading the playlists now (slow CDNs: 30-75 s on the box), so they
            # are ready by the time a proxy candidate asks for them
            if xor_key or any(x in plain_url.lower() for x in (".m3u8", "master.txt")):
                try:
                    _ph = {"User-Agent": headers.get("User-Agent") or novaplay_proxy.SAFE_UA}
                    for _k in ("Referer", "Origin", "Cookie"):
                        if headers.get(_k):
                            _ph[_k] = headers[_k]
                    # all qualities, downloaded in parallel: the box's player switched
                    # quality right after starting and gave up waiting for the second one
                    novaplay_proxy.prefetch_hls(plain_url, _ph, xor_key, max_variants=3)
                except Exception:
                    pass
            legacy_raw = url.replace("#", "|") if "#" in url else url
            legacy_proxied = "http://127.0.0.1:{}/{}".format(_PROXY_PORT, legacy_raw)

    if xor_key or proxy_only:
        _tag = "XOR" if xor_key else "proxy only"
        if proxied:
            add_candidate(4097, proxied, "4097 + proxy HLS ({})".format(_tag), True)
            add_candidate(5001, proxied, "5001 + proxy ({})".format(_tag), True)
            if os.path.exists("/usr/bin/exteplayer3"):
                add_candidate(5002, proxied, "5002 exteplayer3 + proxy ({})".format(_tag), True)
        return candidates

    is_hls = any(x in plain_url.lower() for x in (".m3u8", "master.txt", "/hls", "/playlist"))
    is_mkv = ".mkv" in plain_url.lower()
    has_exteplayer = os.path.exists("/usr/bin/exteplayer3")

    # fMP4 HLS (#EXT-X-MAP) only plays on the box through the proxy, which repackages
    # it as MPEG-TS (novaplay_proxy remux): try that first instead of after ~20 s of
    # direct attempts that always fail. The resolver hands the playlist to the proxy
    # (VidCore/VidFast), so it's already in the proxy's cache here.
    if is_hls and proxied:
        try:
            _hit = novaplay_proxy._cached_playlist(plain_url)
            if _hit and "#EXT-X-MAP" in _hit[0] and novaplay_proxy.remux_available():
                add_candidate(4097, proxied, "4097 + proxy HLS (fMP4->TS)", True)
                if has_exteplayer:
                    add_candidate(5002, proxied, "5002 exteplayer3 + proxy (fMP4->TS)", True)
        except Exception:
            pass

    if is_hls:
        add_candidate(4097, plain_url, "4097 direct HLS")
        add_candidate(4097, url, "4097 + headers HLS")
        add_candidate(8193, plain_url, "8193 direct")
        add_candidate(5001, plain_url, "5001 direct")
        if has_exteplayer:
            add_candidate(5002, plain_url, "5002 exteplayer3")
            add_candidate(5002, url, "5002 exteplayer3 + headers")
        if proxied:
            add_candidate(4097, proxied, "4097 + proxy HLS", True)
            add_candidate(5001, proxied, "5001 + proxy", True)
            if has_exteplayer:
                add_candidate(5002, proxied, "5002 exteplayer3 + proxy", True)
        if legacy_proxied:
            add_candidate(4097, legacy_proxied, "4097 + legacy proxy", True)
    elif is_mkv and has_exteplayer:
        add_candidate(5002, url, "5002 exteplayer3 + headers")
        add_candidate(5002, plain_url, "5002 exteplayer3")
        add_candidate(5001, plain_url, "5001 direct")
        add_candidate(4097, plain_url, "4097 direct")
        add_candidate(8193, plain_url, "8193 direct")
        add_candidate(4097, url, "4097 + headers")
        if proxied:
            add_candidate(5002, proxied, "5002 exteplayer3 + proxy", True)
            add_candidate(5001, proxied, "5001 + proxy", True)
            add_candidate(4097, proxied, "4097 + proxy", True)
        if legacy_proxied:
            add_candidate(4097, legacy_proxied, "4097 + legacy proxy", True)
    else:
        add_candidate(5001, plain_url, "5001 direct")
        add_candidate(4097, plain_url, "4097 direct")
        add_candidate(8193, plain_url, "8193 direct")
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
            add_candidate(4097, legacy_proxied, "4097 + legacy proxy", True)

    if strict:
        kept = [c for c in candidates if c[3] or "headers" in c[2]]
        if kept and len(kept) < len(candidates):
            my_log("candidates: stream needs its headers -- skipping {} header-less method(s)".format(
                len(candidates) - len(kept)))
            candidates = kept
        # the box's players send the headers for the playlist but not reliably
        # for every piece (VideoTube: "4097 + headers HLS" timed out); the proxy
        # sends them on every request, so it goes first here
        candidates = [c for c in candidates if c[3]] + [c for c in candidates if not c[3]]
    return candidates


# [2026-10-22] CDNs whose plain video files ffmpeg (exteplayer3) can't read
# smoothly in its many short requests (Mixdrop: 0.36x real time, while one
# long connection gets ~960 KB/s). For these the proxy downloads ahead to
# the hard disk over one connection (novaplay_proxy read-ahead cache).
_AHEAD_CACHE_HOSTS = ("mxcontent.",)


def _wants_ahead_cache(plain_url):
    try:
        u = str(plain_url or "").lower()
        if not any(h in urlparse(u).netloc for h in _AHEAD_CACHE_HOSTS):
            return False
        if any(x in u for x in (".m3u8", "/hls", "master.txt")):
            return False
        return novaplay_proxy.ahead_available()
    except Exception:
        return False


def _upstream_of(svc_url):
    """The real stream address behind a play candidate (proxy /stream?url=,
    legacy proxy /<url>|hdrs, or a direct url with |/# headers)."""
    u = str(svc_url or "")
    try:
        if "127.0.0.1:{}".format(_PROXY_PORT) in u:
            p = urlparse(u)
            if p.path == "/stream":
                return (parse_qs(p.query).get("url") or [""])[0].split("|")[0]
            u = u.split("127.0.0.1:{}/".format(_PROXY_PORT), 1)[1]
        return u.split("|")[0].split("#")[0]
    except Exception:
        return ""


def _nav_play_unhooked(nav, sref):
    """[2026-10-21] play NOVA's own stream with Enigma2's real playService.
    Other plugins replace session.nav.playService on the instance and reroute
    every http address (StreamProxy's ServiceMonitor: 127.0.0.1:7860/proxy/m3u
    -- fine for HLS, but a plain MP4 such as Mixdrop's failed at once with
    FF_ERROR and never reached NOVA's proxy). NOVA's addresses are already
    final, so call the class method and skip such instance-level hooks."""
    try:
        if "playService" in getattr(nav, "__dict__", {}):
            orig = getattr(type(nav), "playService", None)
            if orig is not None:
                hook = nav.__dict__["playService"]
                my_log("play: bypassing a playService hook ({})".format(
                    getattr(getattr(hook, "__func__", hook), "__qualname__", hook)))
                return orig(nav, sref)
    except Exception as e:
        my_log("play: unhooked call failed ({}), using the normal one".format(e))
    return nav.playService(sref)


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


_restore_timer = [None]


def _restore_previous_service(session, previous_service):
    """[2026-10-21] the TV channel came back with the film's last frame frozen
    on screen (zapping fixed it). With Fast Channel Change the channel is kept
    tuned in the background, and asking for it again right away was treated as
    "already playing", so its picture was never re-attached. Stop the film,
    then a moment later (the film's player fully shut down) play the channel
    with forceRestart, the full switch a zap does."""
    if not previous_service: return
    try:
        session.nav.stopService()
    except Exception: pass

    def _play():
        _restore_timer[0] = None
        try:
            try:
                session.nav.playService(previous_service, forceRestart=True)
            except TypeError:
                session.nav.playService(previous_service)
            my_log("Previous service restored")
        except Exception as e:
            my_log("Restore previous service failed: {}".format(e))

    try:
        t = eTimer()
        try:
            t.callback.append(_play)
        except AttributeError:
            t.timeout.get().append(_play)
        _restore_timer[0] = t      # keep a reference until it fires
        t.start(300, True)
    except Exception:
        _play()


# ─── The player screen ───────────────────────────────────────────────────

class NovaPlaySimplePlayer(Screen):
    skin = scale_skin_xml("""
    <screen name="NovaPlaySimplePlayer" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="transparent">
        <widget name="aspect_bar_l" position="0,0" size="1,1080" backgroundColor="#000000" zPosition="5" />
        <widget name="aspect_bar_r" position="1919,0" size="1,1080" backgroundColor="#000000" zPosition="5" />
        <widget name="recBlink" position="1660,42" size="190,54" font="Regular;36" halign="center" valign="center" foregroundColor="#00FF0000" backgroundColor="#AA000000" transparent="0" zPosition="95" cornerRadius="16" />
        <!-- [osd-ui 2026-10-23] three rows inside the TV safe area (ends at y 1000):
             title / episode / video / subtitles / clock -- seek bar with elapsed and
             remaining + "Ends 23:14" -- colour keys + one help line -->
        <widget name="overlay_bg"   position="160,800" size="1600,200" backgroundColor="#121822" cornerRadius="20" zPosition="10" />
        <widget name="osdPosterBox" position="184,506" size="188,278" backgroundColor="#121822" cornerRadius="12" zPosition="12" transparent="0" />
        <widget name="osdPoster" position="188,510" size="180,270" zPosition="12" alphatest="blend" scale="1" />
        <widget name="osd_title"    position="184,812" size="576,40" font="Regular;30" foregroundColor="#F2C230" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_episode"  position="770,812" size="180,40" font="Regular;24" foregroundColor="#F2C230" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_videinfo" position="960,812" size="260,40" font="Regular;22" foregroundColor="#A3ADBB" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_subdetail" position="1230,812" size="370,40" font="Regular;20" foregroundColor="#A3ADBB" transparent="1" zPosition="12" halign="left" valign="center" />
        <widget name="osd_clock"    position="1610,812" size="130,40" font="Regular;28" foregroundColor="#FFFFFF" transparent="1" zPosition="12" halign="right" valign="center" />
        <widget name="seekFrame" position="182,870" size="1556,10" zPosition="11" backgroundColor="#2A3240" cornerRadius="5" />
        <widget name="seekbar" position="184,872" size="1552,6" zPosition="12" backgroundColor="#1C2333" foregroundColor="#F2C230" cornerRadius="3" />
        <widget name="seekMarker" position="184,866" size="18,18" zPosition="13" backgroundColor="#00FFFFFF" cornerRadius="9" />
        <widget name="osd_elapsed"  position="184,890" size="330,40" font="Regular;28" foregroundColor="#E5D1A6" transparent="1" zPosition="12" valign="center" />
        <widget name="osd_state"    position="520,890" size="60,40" font="Regular;30" foregroundColor="#39D98A" transparent="1" zPosition="12" halign="center" valign="center" />
        <widget name="status"       position="590,890" size="640,40" font="Regular;24" foregroundColor="#FFFFFF" transparent="1" zPosition="12" halign="center" valign="center" />
        <widget name="osd_durtext"  position="1240,890" size="500,40" font="Regular;26" foregroundColor="#FFFFFF" transparent="1" zPosition="12" halign="right" valign="center" />
        <widget name="osd_divider"  position="184,936" size="1552,1" backgroundColor="#2A3240" zPosition="11" />
        <widget name="keyRed"    position="184,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#681418" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="keyGreen"  position="399,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#18521F" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="keyYellow" position="614,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#695B0C" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="keyBlue"   position="829,948" size="205,38" font="Regular;23" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="#141763" transparent="0" cornerRadius="10" zPosition="12" />
        <widget name="osd_keys"     position="1050,948" size="690,38" font="Regular;22" foregroundColor="#A3ADBB" transparent="1" zPosition="12" halign="right" valign="center" />
        <widget name="seekBubble"   position="184,818" size="150,40" font="Bold;24" foregroundColor="#0C1017" backgroundColor="#F2C230" cornerRadius="10" halign="center" valign="center" transparent="0" zPosition="14" />
        <widget name="pauseBadge"   position="900,480" size="120,120" font="Regular;52" foregroundColor="#FFFFFF" backgroundColor="#400C1017" cornerRadius="60" halign="center" valign="center" transparent="0" zPosition="91" />
        <!-- old OSD parts, no longer shown (kept so nothing that still sets them breaks) -->
        <widget name="osd_shadow" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_topline" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_titlebar" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_subinfo" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="progBack" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="prog_bar" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_hints" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_keybar" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_botline" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="osd_jump" position="-300,-300" size="1,1" font="Regular;10" transparent="1" zPosition="0" />
        <widget name="autoNextPanel" position="1110,92" size="750,255" zPosition="20" backgroundColor="#20000000" transparent="0" cornerRadius="24" />
        <widget name="autoNextAccent" position="1110,92" size="750,7" zPosition="21" backgroundColor="#F2C230" transparent="0" />
        <widget name="autoNextPoster" position="1140,105" size="100,150" zPosition="22" alphatest="blend" scale="1" />
        <widget name="autoNextTitle" position="1370,122" size="455,40" font="Regular;30" halign="left" foregroundColor="#F0F6FC" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextEpisode" position="1370,170" size="455,54" font="Regular;27" halign="left" foregroundColor="#F2C230" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextText" position="1370,235" size="455,32" font="Regular;24" halign="left" foregroundColor="#E8E8E8" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="autoNextProgress" position="1370,282" size="455,16" zPosition="22" cornerRadius="8" backgroundColor="#1C2333" foregroundColor="#F2C230" />
        <widget name="autoNextHelp" position="1370,310" size="455,24" font="Regular;20" halign="left" foregroundColor="#CFCFCF" backgroundColor="transparent" zPosition="22" transparent="1" />
        <widget name="seekFlash"    position="660,430" size="600,140" font="Regular;64" foregroundColor="#F0F6FC" backgroundColor="#E60D1117" transparent="0" cornerRadius="20" halign="center" valign="center" zPosition="90" />
        <widget name="qualityWipe"  position="0,0"   size="1920,0"  backgroundColor="#F2C230" transparent="0" zPosition="89" />
        <widget name="syncToast"    position="1550,760" size="280,64" font="Regular;34" foregroundColor="#0C1017" backgroundColor="#F2C230" transparent="0" cornerRadius="14" halign="center" valign="center" zPosition="88" />
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
        <widget name="studioPanel" position="0,36" size="1920,262" zPosition="85" backgroundColor="#0A0E14" transparent="0" />
        <widget name="studioAccent" position="0,36" size="1920,8" zPosition="86" backgroundColor="#F2C230" transparent="0" />
        <widget name="studioTitle" position="45,52" size="480,34" font="Regular;30" foregroundColor="#F2C230" transparent="1" zPosition="87" />
        <widget name="studioTab0" position="560,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioTab1" position="790,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioTab2" position="1020,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioTab3" position="1250,48" size="210,40" font="Regular;24" halign="center" valign="center" foregroundColor="#8B949E" backgroundColor="#171718" transparent="0" cornerRadius="20" zPosition="87" />
        <widget name="studioStatus" position="1490,54" size="380,32" font="Regular;26" halign="right" foregroundColor="#8B949E" transparent="1" zPosition="87" />
        <widget name="studioHelp" position="45,252" size="1830,30" font="Regular;22" halign="center" foregroundColor="#8B949E" transparent="1" zPosition="87" />
        <widget name="studioCard0" position="60,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard1" position="425,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard2" position="790,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard3" position="1155,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioCard4" position="1520,100" size="340,82" font="Regular;26" halign="center" valign="center" foregroundColor="#D0D0D0" backgroundColor="#171718" transparent="0" cornerRadius="16" zPosition="87" />
        <widget name="studioPreviewBg" position="460,192" size="1000,1" zPosition="86" backgroundColor="#0A0E14" transparent="0" />
        <widget name="studioPreview" position="45,194" size="1830,50" font="Regular;28" halign="center" valign="center" foregroundColor="#F0F6FC" backgroundColor="transparent" zPosition="88" transparent="1" />
        <widget name="safeTop" position="0,90" size="1920,2" backgroundColor="#F2C230" zPosition="86" transparent="0" />
        <widget name="safeBottom" position="0,990" size="1920,2" backgroundColor="#F2C230" zPosition="86" transparent="0" />
        <widget name="safeLeft" position="96,90" size="2,900" backgroundColor="#F2C230" zPosition="86" transparent="0" />
        <widget name="safeRight" position="1822,90" size="2,900" backgroundColor="#F2C230" zPosition="86" transparent="0" />
    </screen>
    """)

    _OSD_WIDGETS = [
        "overlay_bg", "osdPosterBox", "osdPoster",
        "osd_title", "osd_episode", "osd_videinfo", "osd_subdetail", "osd_clock",
        "seekFrame", "seekbar", "seekMarker",
        "osd_elapsed", "osd_state", "status", "osd_durtext",
        "osd_divider", "keyRed", "keyGreen", "keyYellow", "keyBlue", "osd_keys",
    ]
    # [osd-ui] parts of the old layout: always hidden
    _OSD_RETIRED = ("osd_shadow", "osd_topline", "osd_titlebar", "osd_subinfo", "progBack",
                    "prog_bar", "osd_hints", "osd_keybar", "osd_botline", "osd_jump")

    _OSD_DESIGN_Y = {
        "overlay_bg": 800, "osdPosterBox": 506, "osdPoster": 510,
        "osd_title": 812, "osd_episode": 812, "osd_videinfo": 812, "osd_subdetail": 812, "osd_clock": 812,
        "seekFrame": 870, "seekbar": 872, "seekMarker": 866,
        "osd_elapsed": 890, "osd_state": 890, "status": 890, "osd_durtext": 890,
        "osd_divider": 936, "keyRed": 948, "keyGreen": 948, "keyYellow": 948, "keyBlue": 948,
        "osd_keys": 948,
    }
    _OSD_SLIDE_OFFSET = 500

    _AUTONEXT_WIDGETS = ("autoNextPanel","autoNextAccent","autoNextPoster","autoNextTitle",
                          "autoNextEpisode","autoNextText","autoNextProgress","autoNextHelp")
    _AUTONEXT_DESIGN_X = {
        "autoNextPanel": 1110, "autoNextAccent": 1110,
        "autoNextPoster": 1140, "autoNextTitle": 1370,
        "autoNextEpisode": 1370, "autoNextText": 1370,
        "autoNextProgress": 1370, "autoNextHelp": 1370,
    }
    _AUTONEXT_SLIDE_OFFSET = 850

    _STUDIO_KEYS = ("studioPanel","studioAccent","studioTitle","studioStatus",
                    "studioHelp","studioCard0","studioCard1","studioCard2",
                    "studioCard3","studioCard4",
                    "studioTab0","studioTab1","studioTab2","studioTab3",
                    "studioPreviewBg","studioPreview")
    _STUDIO_TABS = ("studioTab0","studioTab1","studioTab2","studioTab3")
    _STUDIO_SLOTS_X = (60, 425, 790, 1155, 1520)
    # [studio-ui] settings grouped into 4 tabs (1-4 switch tabs)
    _STUDIO_GROUPS = (
        (u"TEXT", ("size", "font", "color", "spacing", "autowrap")),
        (u"LOOK", ("outline", "ocolor", "bg", "bg_alpha")),
        (u"POSITION", ("offset_y", "align", "cuepos")),
        (u"SYNC", ("delay", "pickline", "presets", "reset", "remove")),
    )
    _STUDIO_HINTS = {
        "size": u"▲ bigger   ▼ smaller",
        "font": u"OK — choose a font",
        "color": u"▲ ▼ change the text colour",
        "spacing": u"▲ ▼ space between the two lines",
        "autowrap": u"▲ ▼ on / off — split long lines automatically",
        "outline": u"▲ ▼ on / off — dark edge around the letters",
        "ocolor": u"▲ ▼ change the edge colour",
        "bg": u"▲ ▼ on / off — box behind the text",
        "bg_alpha": u"▲ more see-through   ▼ more solid",
        "offset_y": u"▲ ▼ move the subtitle up / down",
        "align": u"▲ ▼ top / bottom / centre of the screen",
        "cuepos": u"▲ ▼ on / off — use positions written in the subtitle file",
        "delay": u"▲ +250 ms   ▼ −250 ms   (text too early → +, too late → −)",
        "pickline": u"OK — pick the line you hear now to sync",
        "presets": u"OK — save / load styles A, B, C",
        "reset": u"OK — back to the default style",
        "remove": u"OK — turn the subtitles off",
    }
    _STUDIO_CARDS = ("studioCard0","studioCard1","studioCard2","studioCard3","studioCard4")
    _STUDIO_DESIGN_Y = {
        "studioPanel": 36, "studioAccent": 36,
        "studioTitle": 52, "studioStatus": 54, "studioHelp": 252,
        "studioCard0": 100, "studioCard1": 100, "studioCard2": 100,
        "studioCard3": 100, "studioCard4": 100,
        "studioTab0": 48, "studioTab1": 48, "studioTab2": 48, "studioTab3": 48,
        "studioPreviewBg": 192, "studioPreview": 194,
    }
    _STUDIO_SLIDE_PX = 320

    _AUTONEXT_SECONDS_DEFAULT = 10
    _ASPECT_MODES = [("Full", 0), ("14:9", 120), ("4:3", 240)]
    _AV_PROBE_MODES = [
        ("System 4:3 — probe v0", 0),
        ("System 16:9 — probe v1", 1),
        ("System 16:10 — probe v2", 2),
        ("System probe v3", 3),
        ("System probe v4", 4),
        ("System probe v5", 5),
    ]

    _OSD_ANIM_MS       = 20
    _OSD_ANIM_EASE     = 0.35
    _AUTONEXT_ANIM_MS  = 20
    _AUTONEXT_ANIM_EASE= 0.35
    _SEEK_FLASH_MS     = 800
    _EXTRACT_HINT_MS   = 3000
    _QUALITY_WIPE_MS   = 25
    _QUALITY_WIPE_FRAMES = 10

    def __init__(self, session, title, candidates, previous_service=None, resume_pos=0, item_url="",
                 next_episode=None, on_next=None, poster_url="", quality_variants=None, server_url="",
                 is_preview=False, servers=None, site="", server_name=""):
        Screen.__init__(self, session)
        self["overlay_bg"]   = Label("")
        self["status"]       = Label("Playing...")
        self["osd_shadow"]   = Label("")
        self["osd_titlebar"] = Label("")
        self["osd_title"]    = Label("")
        self["osd_durtext"]  = Label("")
        self["osd_topline"]  = Label("")
        self["seekbar"]      = ProgressBar()
        self["prog_bar"]     = Label("")
        self["osd_elapsed"]  = Label("")
        self["osd_state"]    = Label("")
        self["osd_hints"]    = Label("")
        self["osd_divider"]  = Label("")
        self["osd_keybar"]   = Label("")
        self["osd_keys"]     = Label("")
        self["osd_botline"]  = Label("")
        self["aspect_bar_l"] = Label("")
        self["aspect_bar_r"] = Label("")
        self["osdPosterBox"] = Label("")
        self["osdPoster"]    = Pixmap()
        self["osd_videinfo"] = Label("")
        self["osd_subinfo"] = Label("")
        self["osd_clock"] = Label("")
        self["recBlink"]     = Label("● REC")
        # [POLISH v1] seek-bar polish + colour key chips + second info row
        self["seekFrame"]    = Label("")
        self["seekMarker"]   = Label("")
        self["progBack"]     = Label("")
        self["keyRed"]       = Label(u"Next server ⏭")
        self["keyGreen"]     = Label(u"Skip intro")
        self["keyYellow"]    = Label(u"Quality · Server")
        self["keyBlue"]      = Label(u"Audio · Subs")
        self["osd_subdetail"] = Label("")
        self["osd_episode"]  = Label("")
        self["osd_jump"]     = Label("")
        self["seekFlash"]    = Label("")
        self["seekFlash"].hide()
        self["seekBubble"]   = Label("")      # [osd-ui] target time above the marker
        self["seekBubble"].hide()
        self["pauseBadge"]   = Label(u"\u275a\u275a")   # [osd-ui] big pause sign
        self["pauseBadge"].hide()
        self["qualityWipe"]  = Label("")
        self["qualityWipe"].hide()
        self["syncToast"]    = Label("")
        self["syncToast"].hide()
        for k in self._AUTONEXT_WIDGETS:
            if k == "autoNextProgress":
                self[k] = ProgressBar()
            elif "Poster" in k:
                self[k] = Pixmap()
            else:
                self[k] = Label("")
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
        self["studioHelp"] = Label(u"◀ ▶ setting   ▲ ▼ change   1 2 3 4 tab   OK menu   EXIT close")
        for _i in range(4):
            self["studioTab%d" % _i] = Label("")
        for c in self._STUDIO_CARDS:
            self[c] = Label("")
        self["studioPreviewBg"] = Label("")
        self["studioPreview"] = Label("")
        self["safeTop"] = Label("")
        self["safeBottom"] = Label("")
        self["safeLeft"] = Label("")
        self["safeRight"] = Label("")

        self._raw_title = (title or "").strip()

        _raw = self._raw_title
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
        # [server-rank] host of the ORIGINAL server/embed link this stream
        # came from -- credited/blamed once playback is actually confirmed
        # or exhausted, not merely extracted.
        self._server_url = server_url or ""
        # [panel] the title's server list, for switching servers inside the player
        self._servers = [sv for sv in (servers or []) if isinstance(sv, dict) and sv.get("url")
                         and not str(sv.get("url")).startswith("magnet:")]
        self._site = site or ""
        self._server_name = server_name or next(
            (sv.get("name", "") for sv in self._servers if sv.get("url") == self._server_url), "")
        self._speed = 1
        self._closed_flag = False
        self._switch_kind = "quality"
        self._server_prev = None
        # [detail-trailer] a trailer has nothing to mark watched, save a
        # position for, or queue a next episode after -- so the near-end
        # exit prompt in __onCancelKey (meant for "you're about to finish
        # a real episode, what do you want to do?") is just noise here.
        # Opt-in only: default False keeps the prompt exactly as-is for
        # every other caller/stream.
        self._is_preview = bool(is_preview)
        try:
            self.candidates = self._reorderCandidatesByPref(self.candidates)
        except Exception:
            pass
        self._poster_url = poster_url or ""
        self._quality_variants = self._normalizeQualityVariants(quality_variants)
        self._quality_current_url = (candidates[0][1] if candidates else "")
        _qlbl = ""
        try:
            from extractors.base import _label_quality_variant
            _qlbl = _label_quality_variant(self._quality_current_url) or ""
        except Exception:
            _qlbl = ""
        self._quality_current_label = _qlbl or (_qtag.strip("[]") if _qtag else "AUTO")
        self._quality_dead_urls = set()
        self._quality_switch_target_url = ""
        self._quality_prev_url = ""
        self._quality_prev_label = ""
        self._quality_prev_candidates = []
        self._quality_switch_in_flight = False
        self._quality_pending_pref = ""
        self._poster_painted = False
        self._poster_final = False
        self._poster_requested = False
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
        self._pic_extra = None
        self._pic_extra_list = []
        self._pic_pep_rows = []
        self._saved_av_aspect = None
        self._av_aspect_dirty = False
        try:
            from Components.config import config
            self._saved_av_aspect = int(config.av.aspect.value)
        except Exception:
            pass
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

        # [player-anim] OSD slide state
        self._osd_slide_p = 0.0
        self._osd_slide_target = 0.0
        self._osd_slide_timer = eTimer()
        self._osd_slide_timer.callback.append(self._osdSlideTick)

        # [player-anim] auto-next slide state
        self._autonext_slide_p = 0.0
        self._autonext_slide_target = 0.0
        self._autonext_slide_timer = eTimer()
        self._autonext_slide_timer.callback.append(self._autonextSlideTick)

        # [player-anim] seek flash
        self._seek_flash_timer = eTimer()
        self._seek_flash_timer.callback.append(self._hideSeekFlash)
        self._pause_badge_timer = eTimer()
        self._pause_badge_timer.callback.append(self._hidePauseBadge)

        # [player-anim] quality wipe
        self._quality_wipe_frame = 0
        self._quality_wipe_target = 0
        self._quality_wipe_cb = None
        self._quality_wipe_timer = eTimer()
        self._quality_wipe_timer.callback.append(self._qualityWipeTick)

        # [player-ux] extraction skip hint
        self._skip_hint_timer = eTimer()
        self._skip_hint_timer.callback.append(self._showSkipHint)
        self._skip_hint_shown = False

        # [player-anim] aspect-bar slide state
        self._aspect_anim_from = 0
        self._aspect_anim_to = 0
        self._aspect_anim_p = 1.0
        self._aspect_anim_timer = eTimer()
        self._aspect_anim_timer.callback.append(self._aspectAnimTick)

        # [player-anim] studio-panel slide state
        self._studio_slide_p = 1.0
        self._studio_slide_target = 1.0
        self._studio_slide_timer = eTimer()
        self._studio_slide_timer.callback.append(self._studioSlideTick)

        # [player-anim] sync toast
        self._sync_toast_x_from = 1550
        self._sync_toast_x_target = 1550
        self._sync_toast_p = 0.0
        self._sync_toast_frame = 0
        self._sync_toast_slide_timer = eTimer()
        self._sync_toast_slide_timer.callback.append(self._syncToastSlideTick)
        self._sync_toast_timer = eTimer()
        self._sync_toast_timer.callback.append(self._hideSyncToast)

        # [player-ux] hold-to-accelerate seek
        self._hold_dir = 0
        self._last_seek_step = 10   # [POLISH v1] last seek step, shown in osd_jump
        self._hold_count = 0
        self._hold_last_t = 0.0

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
        self._stall_last_pts = -1
        self._stall_count = 0
        self._stall_fail_count = 0
        self._stall_recovering = False
        self._stall_recover_target = 0
        self._stall_timer = eTimer()
        self._stall_timer.callback.append(self.__stallWatchdog)
        self._stall_kick_timer = eTimer()
        self._stall_kick_timer.callback.append(self.__stallKickBack)
        self._autosub_gen = 0
        self._watched_at_90_fired = False

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

        self["actions"] = ActionMap(["OkCancelActions", "MediaPlayerActions", "NumberActions", "InfobarSeekActions", "DirectionActions", "ColorActions", "InfobarSubtitleActions", "InfobarSubtitleSelectionActions", "InfobarAudioSelectionActions", "MenuActions", "InfoBarMenuActions", "ButtonSetupActions", "InfoActions"], {
            "cancel":           self.__onCancelKey,
            "stop":             self.__onExit,
            "ok":               self.__onOkKey,
            "playpauseService": self.__togglePause,
            "right":            self.__navRightKey,
            "left":             self.__navLeftKey,
            "up":               self.__navUpKey,
            "down":             self.__navDownKey,
            "seekFwd":          self.__navSeekFwdKey,
            "seekBack":         self.__navSeekBackKey,
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
            "seekdef:1": lambda: self.__seekPct(10),
            "seekdef:3": lambda: self.__seekPct(30),
            "seekdef:4": lambda: self.__seekPct(40),
            "seekdef:6": lambda: self.__seekPct(60),
            "seekdef:7": lambda: self.__seekPct(70),
            "seekdef:9": lambda: self.__seekPct(90),
            "green":            self.__greenKey,
            "red":              self.__nextServer,
            "blue":             self._subsPanel,
            "yellow":           self.__panelMenu,
            "subtitles":        self._subsPanel,
            "subtitleSelection": self._subsPanel,
            "audioSelection":   self.__audioSelect,
            "menu":             self._playerMenu,
            "showMenu":         self._playerMenu,
            "mainMenu":         self._playerMenu,
            "info":             self.__streamInfo,
        }, -1)
        # [studio-ui] no CH+/CH- binding: in many keymaps ◀ ▶ also send the
        # zap actions, which made one press move a setting AND switch tab.
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
        # Transmission on demand: torrents pause while something plays (they
        # share the HDD with playback and swap), resumed after exit.
        threading.Thread(target=_tr_call, args=("pause_all",), daemon=True).start()

    # ── [A3] next-episode delay, from config ────────────────────────────
    def _nextDelaySecs(self):
        """[A3] read next_episode_delay from config; clamped 5-60s."""
        try:
            v = int(_get_config("next_episode_delay", str(self._AUTONEXT_SECONDS_DEFAULT)) or self._AUTONEXT_SECONDS_DEFAULT)
        except Exception:
            v = self._AUTONEXT_SECONDS_DEFAULT
        return max(5, min(60, v))

    # ── [player-anim] OSD slide helpers ─────────────────────────────────
    def _designX(self, key):
        try:
            inst = self[key].instance
            if inst:
                return inst.position().x()
        except Exception:
            pass
        return 0

    def _moveResizeY(self, key, design_y, offset, w=None, h=None):
        try:
            inst = self[key].instance
            if not inst:
                return
            inst.move(ePoint(int(self._designX(key)),
                             int(design_y + offset)))
            if w is not None and h is not None:
                inst.resize(eSize(int(w), int(h)))
        except Exception as e:
            my_log("moveResizeY error for {}: {}".format(key, e))

    def _startOSDSlide(self, target):
        self._osd_slide_target = target
        try:
            self._osd_slide_timer.start(self._OSD_ANIM_MS, False)
        except Exception as e:
            my_log("OSD slide start failed: {}".format(e))

    def _osdSlideTick(self):
        diff = self._osd_slide_target - self._osd_slide_p
        if abs(diff) < 0.02:
            self._osd_slide_p = self._osd_slide_target
            self._applyOSDSlide()
            try: self._osd_slide_timer.stop()
            except Exception: pass
            if self._osd_slide_p <= 0.01 and not self._osd_visible:
                for w in self._OSD_WIDGETS:
                    try: self[w].hide()
                    except Exception: pass
            return
        self._osd_slide_p += diff * self._OSD_ANIM_EASE
        self._applyOSDSlide()

    def _applyOSDSlide(self):
        offset = int((1.0 - self._osd_slide_p) * self._OSD_SLIDE_OFFSET)
        for w in self._OSD_WIDGETS:
            dy = self._OSD_DESIGN_Y.get(w)
            if dy is None:
                continue
            self._moveResizeY(w, dy, offset)

    # ── [player-anim] auto-next card slide helpers ──────────────────────
    def _startAutoNextSlide(self, target):
        self._autonext_slide_target = target
        try:
            self._autonext_slide_timer.start(self._AUTONEXT_ANIM_MS, False)
        except Exception as e:
            my_log("auto-next slide start failed: {}".format(e))

    def _autonextSlideTick(self):
        diff = self._autonext_slide_target - self._autonext_slide_p
        if abs(diff) < 0.02:
            self._autonext_slide_p = self._autonext_slide_target
            self._applyAutoNextSlide()
            try: self._autonext_slide_timer.stop()
            except Exception: pass
            if self._autonext_slide_p <= 0.01:
                for w in self._AUTONEXT_WIDGETS:
                    try: self[w].hide()
                    except Exception: pass
            return
        self._autonext_slide_p += diff * self._AUTONEXT_ANIM_EASE
        self._applyAutoNextSlide()

    def _applyAutoNextSlide(self):
        offset = int((1.0 - self._autonext_slide_p) * self._AUTONEXT_SLIDE_OFFSET)
        for w in self._AUTONEXT_WIDGETS:
            dx = self._AUTONEXT_DESIGN_X.get(w)
            if dx is None:
                continue
            try:
                inst = self[w].instance
                if inst:
                    pos = inst.position()
                    inst.move(ePoint(int(dx + offset), int(pos.y())))
            except Exception:
                pass

    # ── [player-anim] seek flash ────────────────────────────────────────
    def _showSeekFlash(self, direction, delta_secs, target_secs):
        try:
            if direction > 0:
                arrow = u"▶▶"
            else:
                arrow = u"◀◀"
            _th = target_secs // 3600
            _tm = (target_secs % 3600) // 60
            _ts = target_secs % 60
            tstr = u"{:02d}:{:02d}:{:02d}".format(_th, _tm, _ts)
            self["seekFlash"].setText(u"{} {}s\n{}".format(arrow, int(abs(delta_secs)), tstr))
            self["seekFlash"].show()
            try:
                self._seek_flash_timer.stop()
            except Exception:
                pass
            self._seek_flash_timer.start(self._SEEK_FLASH_MS, True)
        except Exception as e:
            my_log("seekFlash error: {}".format(e))

    def _hideSeekFlash(self):
        try: self["seekFlash"].hide()
        except Exception: pass
        try: self["seekBubble"].hide()
        except Exception: pass

    @staticmethod
    def _clockText(secs):
        """[osd-ui] 42:10 / 1:02:10"""
        secs = max(0, int(secs))
        h, rem = divmod(secs, 3600)
        m, s_ = divmod(rem, 60)
        return u"{}:{:02d}:{:02d}".format(h, m, s_) if h else u"{}:{:02d}".format(m, s_)

    def _showSeekBubble(self, target_secs):
        """[osd-ui] small gold bubble with the target time over the seek bar,
        right above where the marker will be"""
        try:
            total = self._total_secs
            if total <= 0:
                return
            pct = min(1.0, max(0.0, float(target_secs) / float(total)))
            x = 184 + int(1552 * pct) - 75
            x = max(170, min(1750 - 150, x))
            inst = self["seekBubble"].instance
            if inst is not None:
                inst.move(ePoint(x, 818))
            self["seekBubble"].setText(self._clockText(target_secs))
            self["seekBubble"].show()
        except Exception as e:
            my_log("seek bubble: {}".format(e))

    def _showPauseBadge(self):
        try:
            self["pauseBadge"].show()
            self._pause_badge_timer.stop()
            self._pause_badge_timer.start(1500, True)
        except Exception:
            pass

    def _hidePauseBadge(self):
        try:
            self._pause_badge_timer.stop()
        except Exception:
            pass
        try:
            self["pauseBadge"].hide()
        except Exception:
            pass

    # ── [player-anim] quality switch wipe ───────────────────────────────
    def _startQualityWipe(self, on_midpoint=None):
        self._quality_wipe_cb = on_midpoint
        self._quality_wipe_frame = 0
        self._quality_wipe_target = 1
        try:
            self["qualityWipe"].show()
            self._moveResizeBar("qualityWipe", 0, 0, 1920, 0)
            self._quality_wipe_timer.start(self._QUALITY_WIPE_MS, False)
        except Exception as e:
            my_log("quality wipe start failed: {}".format(e))
            if on_midpoint:
                try: on_midpoint()
                except Exception: pass

    def _moveResizeBar(self, key, x, y, w, h):
        try:
            inst = self[key].instance
            if inst:
                inst.move(ePoint(int(x), int(y)))
                inst.resize(eSize(int(w), int(h)))
        except Exception:
            pass

    def _qualityWipeTick(self):
        self._quality_wipe_frame += 1
        frames = self._QUALITY_WIPE_FRAMES
        p = min(1.0, float(self._quality_wipe_frame) / float(frames))
        if self._quality_wipe_target == 1:
            h = int(1080 * p)
            self._moveResizeBar("qualityWipe", 0, 0, 1920, h)
            if p >= 1.0:
                if self._quality_wipe_cb:
                    cb = self._quality_wipe_cb
                    self._quality_wipe_cb = None
                    try: cb()
                    except Exception as e:
                        my_log("quality wipe midpoint error: {}".format(e))
                self._quality_wipe_frame = 0
                self._quality_wipe_target = 0
        else:
            y = int(-1080 * p)
            h = int(1080 * (1.0 - p))
            self._moveResizeBar("qualityWipe", 0, y + 1080, 1920, h)
            if p >= 1.0:
                try:
                    self._quality_wipe_timer.stop()
                    self["qualityWipe"].hide()
                    self._moveResizeBar("qualityWipe", 0, 0, 1920, 0)
                except Exception:
                    pass

    # ── [player-anim] aspect-bar slide ─────────────────────────────────
    def _aspectAnimTick(self):
        diff = 1.0 - self._aspect_anim_p
        if diff < 0.02:
            self._aspect_anim_p = 1.0
            self._applyAspectBarWidth(self._aspect_anim_to)
            try: self._aspect_anim_timer.stop()
            except Exception: pass
            if self._aspect_anim_to == 0:
                try:
                    self["aspect_bar_l"].hide()
                    self["aspect_bar_r"].hide()
                except Exception:
                    pass
            return
        self._aspect_anim_p += 0.25
        p_eased = 1.0 - (1.0 - min(1.0, self._aspect_anim_p)) ** 3
        cur = int(self._aspect_anim_from
                  + (self._aspect_anim_to - self._aspect_anim_from) * p_eased)
        self._applyAspectBarWidth(cur)

    def _applyAspectBarWidth(self, w):
        try:
            inst_l = self["aspect_bar_l"].instance
            inst_r = self["aspect_bar_r"].instance
            w = max(1, int(w))
            if inst_l:
                inst_l.resize(eSize(w, 1080))
                inst_l.move(ePoint(0, 0))
            if inst_r:
                inst_r.resize(eSize(w, 1080))
                inst_r.move(ePoint(1920 - w, 0))
        except Exception:
            pass

    # ── [player-anim] studio-panel slide ───────────────────────────────
    def _applyStudioSlide(self):
        offset = int((1.0 - self._studio_slide_p) * -self._STUDIO_SLIDE_PX)
        for k in self._STUDIO_KEYS:
            dy = self._STUDIO_DESIGN_Y.get(k)
            if dy is None:
                continue
            try:
                inst = self[k].instance
                if inst:
                    pos = inst.position()
                    inst.move(ePoint(int(pos.x()), int(dy + offset)))
            except Exception:
                pass

    def _studioSlideTick(self):
        diff = self._studio_slide_target - self._studio_slide_p
        if abs(diff) < 0.02:
            self._studio_slide_p = self._studio_slide_target
            self._applyStudioSlide()
            try: self._studio_slide_timer.stop()
            except Exception: pass
            if self._studio_slide_p <= 0.01:
                for k in self._STUDIO_KEYS:
                    try: self[k].hide()
                    except Exception: pass
            return
        self._studio_slide_p += diff * 0.35
        self._applyStudioSlide()

    # ── [player-anim] sync-offset toast ────────────────────────────────
    def _showSyncToast(self, text):
        try:
            self["syncToast"].setText(text)
            self["syncToast"].show()
            self._sync_toast_x_from = 1920 + 20
            self._sync_toast_x_target = 1550
            self._sync_toast_p = 0.0
            try:
                self["syncToast"].instance.move(ePoint(self._sync_toast_x_from, 760))
            except Exception:
                pass
            self._sync_toast_frame = 0
            try:
                self._sync_toast_slide_timer.stop()
                self._sync_toast_slide_timer.start(20, False)
            except Exception:
                pass
            try:
                self._sync_toast_timer.stop()
                self._sync_toast_timer.start(1500, True)
            except Exception:
                pass
        except Exception as e:
            my_log("showSyncToast error: {}".format(e))

    def _syncToastSlideTick(self):
        self._sync_toast_frame += 1
        p = min(1.0, self._sync_toast_frame / 6.0)
        p_eased = 1.0 - (1.0 - p) ** 3
        x = int(self._sync_toast_x_from
                + (self._sync_toast_x_target - self._sync_toast_x_from) * p_eased)
        try:
            inst = self["syncToast"].instance
            if inst:
                inst.move(ePoint(x, 760))
        except Exception:
            pass
        if p >= 1.0:
            try: self._sync_toast_slide_timer.stop()
            except Exception: pass

    def _hideSyncToast(self):
        try:
            self["syncToast"].hide()
        except Exception:
            pass

    # ── [player-ux] extraction skip hint ────────────────────────────────
    def _armSkipHint(self):
        self._skip_hint_shown = False
        try:
            self._skip_hint_timer.stop()
            self._skip_hint_timer.start(self._EXTRACT_HINT_MS, True)
        except Exception:
            pass

    def _disarmSkipHint(self):
        try: self._skip_hint_timer.stop()
        except Exception: pass
        self._skip_hint_shown = False

    def _showSkipHint(self):
        if getattr(self, "_closed", False) or self._play_confirmed:
            return
        self._skip_hint_shown = True
        try:
            cur = self["status"].getText() or "Playing..."
            self["status"].setText(u"{}   —   MENU = skip".format(cur))
            self.__showOSD(True)
        except Exception:
            pass

    # ─── OSD ────────────────────────────────────────────────────────────
    def __initOSD(self):
        for w in self._OSD_WIDGETS + list(self._OSD_RETIRED) + ["seekBubble", "pauseBadge"]:
            try: self[w].hide()
            except: pass
        for w in self._AUTONEXT_WIDGETS:
            try: self[w].hide()
            except: pass

    def __hideOSD(self):
        if self._osd_visible:
            self._osd_visible = False
            try: self["seekBubble"].hide()
            except Exception: pass
            try: self._osd_update_timer.stop()
            except: pass
            self._startOSDSlide(0.0)

    def __showOSD(self, auto_hide=True):
        self._osd_visible = True
        if self._osd_slide_p <= 0.01:
            self._osd_slide_p = 0.0
        for w in self._OSD_WIDGETS:
            try: self[w].show()
            except: pass
        self._startOSDSlide(1.0)
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
        if getattr(self, "_poster_final", False):
            return
        url = getattr(self, "_poster_url", "") or ""
        if not url:
            self._poster_final = True
            if not getattr(self, "_poster_painted", False):
                self.__setOsdPosterPixmap(placeholder_for_item({"type": "movie"}) or "")
                self._poster_painted = True
            return
        path = ""
        try:
            from plugin_util import _get_cached_poster
            path = _get_cached_poster(url) or ""
        except Exception:
            path = ""
        if not path:
            try:
                path = plugin_imagecache.getCachedImage(url, target_size=(180, 270)) or ""
            except Exception:
                path = ""
        if not path and not getattr(self, "_poster_requested", False):
            try:
                plugin_imagecache.requestImageAsyncPriority(url, target_size=(180, 270))
                self._poster_requested = True
            except Exception:
                pass
        if not path:
            try:
                path = plugin_imagecache.getCachedImage(url) or ""
            except Exception:
                path = ""
        if path:
            self._poster_final = True
            self._poster_painted = True
            self.__setOsdPosterPixmap(path)
            my_log("OSD poster painted: {}".format(url[:80]))
            return
        if not getattr(self, "_poster_painted", False):
            self.__setOsdPosterPixmap(placeholder_for_item({"type": "movie"}) or "")
            self._poster_painted = True
            my_log("OSD poster placeholder shown (waiting for download)")

    def __setOsdPosterPixmap(self, path):
        if not path:
            return
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

    # ── [player-ux] state glyph ─────────────────────────────────────────
    def _stateGlyph(self):
        """▶ playing · ⏸ paused · ⏳ buffering · ⚠ stalled/recovering"""
        if getattr(self, "_stall_recovering", False):
            return u"⚠", "#FF6B6B"
        if getattr(self, "_paused", False):
            return u"⏸", "#F2C230"
        svc = None
        try:
            svc = self.session.nav.getCurrentService()
        except Exception:
            svc = None
        if svc is None:
            return u"⏳", "#8B949E"
        try:
            sk = svc.seek()
            if sk:
                r = sk.getPlayPosition()
                if r and r[0] != 0:
                    return u"⏳", "#8B949E"
        except Exception:
            pass
        return u"▶", "#39D98A"

    def __updateOSD(self):
        if not self._osd_visible:
            try: self._osd_update_timer.stop()
            except: pass
            return
        try:
            if not getattr(self, "_poster_final", False):
                self._paintOsdPoster()
        except Exception:
            pass
        try:
            if self._paused:
                elapsed = self._paused_elapsed
            else:
                elapsed = current_play_secs()

            color = "#F2C230" if self._paused else "#E5D1A6"
            try:
                if parseColor is not None:
                    self["osd_elapsed"].instance.setForegroundColor(parseColor(color))
            except Exception:
                pass
            self["osd_elapsed"].setText(self._clockText(elapsed))

            # [player-ux] state glyph
            try:
                glyph, gcolor = self._stateGlyph()
                self["osd_state"].setText(glyph)
                if parseColor is not None:
                    try:
                        self["osd_state"].instance.setForegroundColor(parseColor(gcolor))
                    except Exception:
                        pass
            except Exception:
                pass

            if self._total_secs > 0 and not self._osd_video_info:
                # [FIX] width/height read 0/-1 for the first seconds of a stream and the
                # frame rate comes in thousandths (24000 = 24 fps): keep asking until the
                # size is known, and show the rate as fps.
                try:
                    service = self.session.nav.getCurrentService()
                    info = service.info() if service else None
                    w = h = fr = 0
                    if info is not None and iServiceInformation is not None:
                        for wk, hk in (("sVideoWidth", "sVideoHeight"), ("sWidth", "sHeight")):
                            try:
                                w = info.getInfo(getattr(iServiceInformation, wk))
                                h = info.getInfo(getattr(iServiceInformation, hk))
                            except Exception:
                                w = h = 0
                            if w > 0 and h > 0:
                                break
                        try:
                            fr = info.getInfo(iServiceInformation.sFrameRate)
                        except Exception:
                            fr = 0
                    parts = []
                    if w > 0 and h > 0:
                        parts.append(u"{}×{}".format(w, h))
                    if fr and fr > 0:
                        fps = fr / 1000.0 if fr > 1000 else float(fr)
                        parts.append(u"{:g}fps".format(round(fps, 3)))
                    # only keep it once the size is known (otherwise ask again next tick)
                    if w > 0 and h > 0:
                        self._osd_video_info = u" · ".join(parts)
                    else:
                        self["osd_videinfo"].setText(u" · ".join(parts))
                except Exception:
                    self._osd_video_info = ""
            if self._osd_video_info:
                self["osd_videinfo"].setText(self._osd_video_info)

            try:
                from novaplay_subtitles import get_subtitle_state
                _sub_state = get_subtitle_state() or {}
                _cc_on = bool(_sub_state.get("path"))
                self["osd_subinfo"].setText("CC" if _cc_on else "cc")
                # [POLISH v1] subtitle file + offset in the second row (empty when off)
                try:
                    _detail = ""
                    if _cc_on:
                        _fn = os.path.basename(_sub_state.get("path") or "")
                        _off = int(_sub_state.get("offset_ms") or 0)
                        _detail = ("CC · {} · {:+d}ms".format(_fn[:38], _off) if _off
                                   else "CC · {}".format(_fn[:44]))
                    self["osd_subdetail"].setText(_detail)
                except Exception:
                    pass
                if parseColor is not None:
                    try:
                        self["osd_subinfo"].instance.setForegroundColor(
                            parseColor("#F2C230" if _cc_on else "#6E7681"))
                    except Exception:
                        pass
            except Exception:
                pass

            now = time.strftime("%H:%M")
            self["osd_clock"].setText(now)

            # [POLISH v1] episode id (+ next episode) and the last seek step
            try:
                _ep_text = ""
                _m = re.search(r"\bS(\d{1,2})\s*E(\d{1,3})\b", self._raw_title or "", re.I)
                if _m:
                    _ep_text = "S{}E{}".format(_m.group(1).zfill(2), _m.group(2).zfill(2))
                    _nt = ""
                    try:
                        _nt = (self._next_episode or {}).get("title") or ""
                    except Exception:
                        pass
                    _m2 = re.search(r"\bS\d{1,2}\s*E(\d{1,3})\b", _nt, re.I)
                    if _m2:
                        _ep_text += " → E" + _m2.group(1).zfill(2)
                self["osd_episode"].setText(_ep_text)
                self._osd_step = int(getattr(self, "_last_seek_step", 10) or 10)
            except Exception:
                pass

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
                # [osd-ui] left: 42:10 / 2:10:00 -- right: -1:27:50 · Ends 23:14
                self["osd_elapsed"].setText(u"{} / {}".format(self._clockText(elapsed), self._clockText(total)))
                self["osd_durtext"].setText(u"\u2212{}  \u00b7  Ends {}".format(
                    self._clockText(rem), time.strftime("%H:%M", time.localtime(time.time() + rem))))
                try:
                    self["seekbar"].setRange((0, 100))
                    self["seekbar"].setValue(int(pct * 100))
                except Exception:
                    pass
                self["prog_bar"].setText("{:.0f}%".format(pct * 100))
                # [POLISH v1] playhead marker follows the fill edge
                try:
                    _sb = self["seekbar"].instance
                    _mk = self["seekMarker"].instance
                    if _sb and _mk:
                        _sbp = _sb.position(); _sbs = _sb.size(); _mks = _mk.size()
                        _wx = _sbp.x() + int(_sbs.width() * pct) - (_mks.width() // 2)
                        _wy = _sbp.y() - ((_mks.height() - _sbs.height()) // 2)
                        _mk.move(ePoint(int(_wx), int(_wy)))
                except Exception:
                    pass

                # [player-ux] watched-at-90%
                if (not getattr(self, "_watched_at_90_fired", False)
                        and pct >= 0.90):
                    self._watched_at_90_fired = True
                    try:
                        from plugin_watched import mark_watched
                        if self._item_url:
                            mark_watched(self._item_url)
                            my_log("watched-at-90%: marked {}".format(self._item_url[:60]))
                    except Exception as e:
                        my_log("watched-at-90% error: {}".format(e))
            else:
                self["osd_durtext"].setText("")
                self["prog_bar"].setText("")
                try:
                    self["seekbar"].setValue(0)
                except Exception:
                    pass
            try:
                self["keyGreen"].setText(self._greenLabel())
            except Exception:
                pass
            # [POLISH v1] the colour keys are shown as chips now
            self["osd_keys"].setText(u"OK pause  \u00b7  0-9 jump  \u00b7  \u25c0\u25b6 \u00b1{}s  \u00b7  STOP exit".format(
                getattr(self, "_osd_step", 10)))
        except Exception as e:
            my_log("updateOSD error: {}".format(e))

    # ─── Studio tick ─────────────────────────────────────────────────────
    def _studioNowMs(self):
        try:
            svc = self.session.nav.getCurrentService()
            seek = svc and svc.seek()
            if seek:
                r = seek.getPlayPosition()
                if r and r[0] == 0 and r[1] > 0:
                    return int(r[1] // 90)
        except Exception:
            pass
        return current_play_ms()

    def __studioTick(self):
        try:
            from novaplay_substudio import STUDIO
            if STUDIO.is_attached():
                STUDIO.update(self._studioNowMs())
        except Exception:
            pass

    # ── [green] Skip intro early on, Next episode near the end ───────────
    def _greenPhase(self):
        try:
            pos = self._paused_elapsed if self._paused else current_play_secs()
        except Exception:
            pos = 0
        total = int(self._total_secs or 0)
        if (self._next_episode and self._on_next and total > 0
                and total - pos <= max(300, int(total * 0.10))):
            return "next"
        return "intro" if pos < 600 else "skip"

    def _greenLabel(self):
        try:
            secs = max(10, min(600, int(_get_config("skip_intro_secs", "90") or 90)))
        except Exception:
            secs = 90
        ph = self._greenPhase()
        if ph == "next":
            return u"Next episode ⏭"
        if ph == "intro":
            return u"Skip intro"
        return u"Skip +{}s".format(secs)

    def __greenKey(self):
        if getattr(self, "_studioOverlayActive", False):
            return
        if getattr(self, "_autonext_active", False):
            nxt = getattr(self, "_autonext_nxt", None) or self._next_episode
            if nxt:
                self._hideAutoNextCard()
                self.__nextAnswer(True, nxt)
            return
        if self._greenPhase() == "next":
            my_log("green: next episode")
            self.__nextAnswer(True, self._next_episode)
            return
        self.__skipIntro()

    def __skipIntro(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        try:
            secs = int(_get_config("skip_intro_secs", "90") or 90)
        except Exception:
            secs = 90
        secs = max(10, min(600, secs))
        my_log("skip intro: +{}s".format(secs))
        self.__seek(+secs)

    def _autoSubNotify(self, msg):
        try:
            self["status"].setText(msg)
            self.__showOSD(True)
            self._hide_timer.start(3000, True)
        except Exception:
            pass

    def __startAutoSubtitle(self):
        try:
            if str(_get_config("autosub", "true")).lower() not in ("true", "1", "yes", "on"):
                return
            if not self._item_url or not self.title:
                return
            from novaplay_autosub import auto_subtitle_async
            self._autosub_gen = getattr(self, "_autosub_gen", 0) + 1
            auto_subtitle_async(self, self._autosub_gen)
        except Exception as e:
            my_log("autosub spawn error: {}".format(e))

    @staticmethod
    def _normalizeQualityVariants(variants):
        out = []
        if not variants:
            return out
        try:
            items = list(variants)
        except Exception:
            return out
        for it in items:
            label = url = ""
            if isinstance(it, dict):
                url = str(it.get("url") or it.get("stream") or it.get("link") or "")
                label = str(it.get("quality") or it.get("label") or it.get("name") or "")
            elif isinstance(it, (tuple, list)) and len(it) >= 2:
                a, b = str(it[0]), str(it[1])
                if a.lower().startswith("http"):
                    url, label = a, b
                else:
                    label, url = a, b
            elif isinstance(it, str) and it.startswith("http"):
                url = it
            if url.startswith("http"):
                if not label:
                    m = re.search(r"(\d{3,4})\s*p", url, re.I)
                    label = (m.group(1) + "p") if m else "AUTO"
                out.append((label, url))
        seen = set()
        uniq = []
        for label, url in out:
            if url not in seen:
                seen.add(url)
                uniq.append((label, url))
        return uniq

    def _qualityUsable(self):
        variants = list(getattr(self, "_quality_variants", []) or [])
        if not variants:
            try:
                from extractors.base import get_last_quality_variants
                raw = get_last_quality_variants() or []
                variants = self._normalizeQualityVariants(raw)
                if variants:
                    my_log("quality: get_last_quality_variants fallback → {} entries".format(len(variants)))
            except Exception:
                pass
        cur = getattr(self, "_quality_current_url", "")
        dead = getattr(self, "_quality_dead_urls", set())
        return [(l, u) for (l, u) in variants if u != cur and u not in dead]

    def _reorderCandidatesByPref(self, cands):
        try:
            if not cands:
                return cands
            key = _candidate_pref_key(getattr(self, "_item_url", ""))
            pref = str(_get_config(key, "") or "").strip() if key else ""
            # [2026-10-21] the remembered method is per SITE, but one site's
            # servers live on different hosts: TopCinema remembered "5001 direct"
            # (Streamtape) and used it for VideoTube, whose CDN needs a Referer --
            # the header-less request hung Enigma2. When this stream comes with
            # headers, never put a header-less method first; send them first.
            def _plain(lbl):
                l = str(lbl)
                return "headers" not in l and "proxy" not in l
            needs_headers = any(c and len(c) > 2 and not _plain(c[2]) and "headers" in str(c[2])
                                for c in cands)
            if needs_headers:
                if pref and _plain(pref):
                    my_log("candidates: not promoting '{}' -- this stream needs its headers".format(pref))
                    pref = ""
                # only the "+ headers" ones move up; the proxy keeps its place
                hdr = [c for c in cands if "headers" in str(c[2]) and "proxy" not in str(c[2])]
                rest = [c for c in cands if c not in hdr]
                # only when header-less methods are in the list (a strict stream
                # has none left and keeps the proxy first)
                if hdr and cands[0] not in hdr and any(_plain(c[2]) for c in cands):
                    cands[:] = hdr + rest
                    my_log("candidates: methods with headers first -> {}".format(
                        ", ".join(str(c[2]) for c in cands[:3])))
            for i, c in enumerate(cands if pref else []):
                if c and len(c) > 2 and str(c[2]) == pref:
                    if i > 0:
                        win = cands.pop(i)
                        cands.insert(0, win)
                        my_log("candidates: '{}' remembered as working — moved to front".format(pref))
                    break
        except Exception:
            pass
        return self._preferSeekableForMp4(self._orderByPlayMemory(cands))

    def _preferSeekableForMp4(self, cands):
        """[2026-10-22] a plain MP4 file played with 5001 (gstplayer) can't be
        jumped in: on the box it sent the proxy one request from byte 0 and
        ignored the resume and every ◀▶▲▼ (Mixdrop). exteplayer3 (ffmpeg)
        asks for byte ranges, so it goes first for MP4 files -- directly with
        the stream's headers, the proxy as the fallback."""
        try:
            if not cands:
                return cands
            up = (_upstream_of(cands[0][1]) or "").lower().split("?")[0]
            if any(x in up for x in (".m3u8", "master.txt", "/hls", "/playlist", ".mpd")):
                return cands
            if not up.endswith((".mp4", ".m4v", ".mov")):
                return cands
            ext = [c for c in cands if str(c[2]).startswith("5002 exteplayer3")]
            if _wants_ahead_cache(_upstream_of(cands[0][1])):
                # read-ahead cache host: through the proxy, which downloads ahead
                ext.sort(key=lambda c: 0 if "proxy" in str(c[2]) else 1)
                if ext and cands[0] is not ext[0]:
                    cands = ext + [c for c in cands if c not in ext]
                    my_log("candidates: MP4 via the proxy's read-ahead cache -> {}".format(
                        ", ".join(str(c[2]) for c in cands[:3])))
                return cands
            # [2026-10-22] direct "+ headers" first: through the proxy every piece
            # went CDN -> curl_cffi helper -> proxy -> player on the box's CPU and
            # each seek opened a new TLS connection along that chain -- playback
            # stuttered and jumps were slow. ffmpeg sends the headers on every
            # request itself (box: "+ headers" resumed at exactly 1052 s).
            ext.sort(key=lambda c: 0 if "headers" in str(c[2]) else 1)
            if ext and cands[0] is not ext[0]:
                cands = ext + [c for c in cands if c not in ext]
                my_log("candidates: MP4 file -- exteplayer3 first so resume/seek work -> {}".format(
                    ", ".join(str(c[2]) for c in cands[:3])))
        except Exception as e:
            my_log("mp4 player order failed (non-fatal): {}".format(e))
        return cands

    def _orderByPlayMemory(self, cands):
        """[NEW] methods that played on this video server before go first; ones that
        keep failing there go last (plugin_play_memory)."""
        try:
            import plugin_play_memory as _pm
            before = [c[2] for c in cands]
            out = _pm.order(list(cands))
            if [c[2] for c in out] != before:
                my_log("candidates: by play history ({}) -> {}".format(
                    _pm.describe(cands[0][1]), ", ".join(c[2] for c in out[:4])))
            return out
        except Exception as e:
            my_log("play memory order failed (non-fatal): {}".format(e))
            return cands

    def _recordPlayMemory(self, ok):
        try:
            idx = self._candidate_idx
            if 0 <= idx < len(self.candidates):
                import plugin_play_memory as _pm
                c = self.candidates[idx]
                _pm.record(c[1], c[2], ok)
        except Exception:
            pass

    # ── [panel] one-panel menu: Quality | Server | Speed (Yellow) ─────────
    _SPEEDS = ((u"0.5x", 0.5), (u"1x", 1), (u"2x", 2), (u"4x", 4))

    def __panelMenu(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        cur_lbl = str(getattr(self, "_quality_current_label", "") or "")
        dead = getattr(self, "_quality_dead_urls", set())
        vs = [(l, u) for (l, u) in (getattr(self, "_quality_variants", []) or []) if u not in dead]
        if not vs:
            vs = list(self._qualityUsable())
        try:
            from extractors.htmlmedia import sort_by_quality
            vs = sort_by_quality(vs)
        except Exception:
            pass
        cur_url = getattr(self, "_quality_current_url", "")
        q_items, self._panel_q = [], []
        marked = False
        for l, u in vs:
            is_cur = (u == cur_url) or (not marked and cur_lbl and str(l).strip() == cur_lbl.strip())
            marked = marked or is_cur
            q_items.append((str(l), is_cur))
            self._panel_q.append((l, u))
        if not marked:
            q_items.insert(0, (cur_lbl or u"current", True))
            self._panel_q.insert(0, None)
        s_items = [(str(sv.get("name") or u"Server"), (sv.get("url") == self._server_url))
                   for sv in self._servers]
        sp_items = [(lbl, v == self._speed) for lbl, v in self._SPEEDS]
        from plugin_player_panel import NovaPlayerPanel
        self.__hideOSD()
        self.session.openWithCallback(self.__onPanelPicked, NovaPlayerPanel,
                                      q_items, s_items, sp_items)

    def __onPanelPicked(self, res):
        if not res:
            return
        kind, idx = res
        try:
            if kind == "quality":
                pick = self._panel_q[idx] if 0 <= idx < len(self._panel_q) else None
                if pick:
                    self._switch_kind = "quality"
                    self.__qualitySwitch(pick[0], pick[1])
            elif kind == "server":
                if 0 <= idx < len(self._servers):
                    self.__serverSwitch(self._servers[idx])
            elif kind == "speed":
                self.__setSpeed(self._SPEEDS[idx][1])
        except Exception as e:
            my_log("panel pick error: {}".format(e))

    def __setSpeed(self, v):
        try:
            svc = self.session.nav.getCurrentService()
            p = svc and svc.pause()
            if not p:
                raise Exception("no pause interface")
            if v == 1:
                p.unpause()
            elif v > 1:
                p.setFastForward(int(v))
            else:
                p.setSlowMotion(int(round(1.0 / v)))
            novaplay_tracker.set_play_speed(v)
            self._speed = v
            self._paused = False
            self["status"].setText(u"⏩ Speed {}x".format(v) if v != 1 else u"Playing")
            my_log("speed: {}x".format(v))
        except Exception as e:
            my_log("speed {}x not supported: {}".format(v, e))
            self["status"].setText(u"Speed {}x is not supported by this player".format(v))
        self.__showOSD(True)

    def __nextServer(self):
        """Red: jump to the next server of this title, same minute."""
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        if getattr(self, "_quality_switch_in_flight", False):
            return
        n = len(self._servers)
        if n < 2:
            self["status"].setText(u"No other server")
            self.__showOSD(True)
            return
        i = next((k for k, sv in enumerate(self._servers) if sv.get("url") == self._server_url), -1)
        nxt = self._servers[(i + 1) % n]
        self.__serverSwitch(nxt)

    def _cancelSpeed(self):
        """Back to 1x before a pause/seek/stream change: the position clock and
        the pause/seek maths all assume normal speed."""
        if getattr(self, "_speed", 1) == 1:
            return
        try:
            svc = self.session.nav.getCurrentService()
            p = svc and svc.pause()
            if p:
                p.unpause()
        except Exception:
            pass
        novaplay_tracker.set_play_speed(1)
        self._speed = 1

    def __serverSwitch(self, server):
        if not server or server.get("url") == self._server_url:
            return
        try:
            pos = self._paused_elapsed if self._paused else current_play_secs()
        except Exception:
            pos = 0
        name = server.get("name") or u"Server"
        my_log("server switch: {} → {} (resume at {}s)".format(self._server_name or "?", name, int(pos)))
        self["status"].setText(u"🔄 Preparing {}…".format(name))
        self.__showOSD(False)
        site = self._site

        def _bg():
            res = None
            try:
                res = _resolve_server_for_player(site, server)
            except Exception as e:
                my_log("server switch extract error: {}".format(e))
            callInMainThread(self.__onServerResolved, server, int(pos), res)
        threading.Thread(target=_bg, daemon=True).start()

    def __onServerResolved(self, server, pos, res):
        if getattr(self, "_closed_flag", False):
            return
        name = server.get("name") or u"Server"
        if not res or not res[0]:
            my_log("server switch: {} found no stream".format(name))
            self["status"].setText(u"✗ {} does not work — try another server".format(name))
            self.__showOSD(True)
            try:
                from plugin_server_rank import record_host_result
                record_host_result(server.get("url", ""), False)
            except Exception:
                pass
            return
        stream, qual, ref, variants = res
        url = _stream_play_url(stream, ref)
        self._server_prev = (self._server_url, self._server_name, list(getattr(self, "_quality_variants", []) or []))
        self._server_url = server.get("url", "")
        self._server_name = name
        self._quality_variants = self._normalizeQualityVariants(variants)
        self._quality_dead_urls = set()
        lbl = ""
        try:
            from extractors.base import _label_quality_variant
            lbl = _label_quality_variant(stream) or ""
        except Exception:
            pass
        self._switch_kind = "server"
        self.__qualitySwitch(lbl or str(qual or "AUTO"), url, keep_headers=False, resume=pos,
                             status=u"🔄 Server: {}".format(name))

    def __qualityMenu(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        variants = self._qualityUsable()
        if not variants:
            self["status"].setText("🎛 No other qualities")
            self.__showOSD(True)
            self._hide_timer.start(2500, True)
            return
        try:
            from extractors.htmlmedia import sort_by_quality
            variants = sort_by_quality(variants)
        except Exception:
            pass
        items = []
        for label, url in variants:
            mark = u"●" if url == self._quality_current_url else u"○"
            items.append(("{} {}".format(mark, label), (label, url)))
        try:
            items.append(("{} current".format(u"●"), None))
        except Exception:
            pass
        self.session.openWithCallback(self.__onQualityPicked, ChoiceBox,
                                      "Quality", items)

    def __onQualityPicked(self, choice):
        if not choice:
            return
        picked = choice[1] if len(choice) > 1 else None
        if not picked:
            return
        label, url = picked
        self.__qualitySwitch(label, url)

    def __qualitySwitch(self, label, url, keep_headers=True, resume=None, status=None):
        if not url or url == getattr(self, "_quality_current_url", ""):
            return
        if resume is not None:
            pos = resume
        else:
            try:
                pos = self._paused_elapsed if self._paused else current_play_secs()
            except Exception:
                pos = 0
        my_log("quality switch: {} → {} (resume at {}s)".format(
            self._quality_current_label or "?", label, int(pos)))
        hdr = ""
        for _pt, _u, _l, _p in (self.candidates or []) if keep_headers else []:
            if "#" in _u:
                hdr = "#" + _u.split("#", 1)[1]
                break

        def _midpoint():
            for t in ("_seek_timer", "_seek_verify_timer", "_retry_timer",
                      "_force_confirmation_timer", "_stall_timer", "_stall_kick_timer"):
                try:
                    getattr(self, t).stop()
                except Exception:
                    pass
            self._osd_video_info = ""
            self._paused = False
            self._play_confirmed = False
            self._candidate_idx = -1
            self._exhausted = False
            self._resume_pos = int(pos)
            self._quality_prev_url = getattr(self, "_quality_current_url", "")
            self._quality_prev_label = getattr(self, "_quality_current_label", "")
            self._quality_prev_candidates = list(self.candidates or [])
            self._quality_switch_in_flight = True
            self._speed = 1
            self._quality_pending_pref = str(label or "").strip() if self._switch_kind == "quality" else ""
            self._quality_current_url = url
            self._quality_current_label = label
            self._quality_switch_target_url = url
            self._autosub_gen = getattr(self, "_autosub_gen", 0) + 1
            _cands = _build_remote_play_candidates(url + hdr)
            self.candidates = _cands[:5] if len(_cands) > 5 else _cands
            self.candidates = self._reorderCandidatesByPref(self.candidates)
            try:
                self._hideSeekFlash()
                self.__hideOSD()
            except Exception:
                pass
            self.__playNext()

        try:
            self["status"].setText(status or u"🎛 Switching quality: {}".format(label))
        except Exception:
            pass
        # [CHANGE] no full-screen cyan wipe any more (it flashed over the picture
        # on every quality/server change): switch straight away.
        _midpoint()

    def _onQualityRetry(self, ans):
        self._quality_switch_in_flight = False
        self._quality_pending_pref = ""
        prev_cands = getattr(self, "_quality_prev_candidates", [])
        if ans and prev_cands:
            my_log("quality retry: reverting to {} (resume at {}s)".format(
                getattr(self, "_quality_prev_label", "?"), self._resume_pos))
            self.candidates = prev_cands
            self._quality_current_url = getattr(self, "_quality_prev_url", "")
            self._quality_current_label = getattr(self, "_quality_prev_label", "")
            if getattr(self, "_switch_kind", "") == "server" and getattr(self, "_server_prev", None):
                self._server_url, self._server_name, self._quality_variants = self._server_prev
                self._server_prev = None
            self._play_confirmed = False
            self._candidate_idx = -1
            self._exhausted = False
            self._stall_last_pts = -1
            self._stall_count = 0
            self._stall_fail_count = 0
            try:
                self["status"].setText(u"🎛 Going back to the previous quality…")
                self.__showOSD(True)
            except Exception:
                pass
            self.__playNext()
        else:
            self.__onExit(clear_position=False)

    _STALL_TICK_MS = 5000
    _STALL_TICKS = 3
    _STALL_MAX_KICKS = 2

    def __stallWatchdog(self):
        try:
            if not getattr(self, "_play_confirmed", False):
                return
            if self._paused or getattr(self, "_autonext_active", False):
                self._stall_last_pts = -1
                self._stall_count = 0
                return
            if getattr(self, "_stall_recovering", False):
                return
            svc = self.session.nav.getCurrentService()
            if not svc:
                return
            seek = svc.seek()
            if not seek:
                return
            r = seek.getPlayPosition()
            if not r or r[0] != 0 or r[1] <= 0:
                return
            pts = r[1]
            last = self._stall_last_pts
            self._stall_last_pts = pts
            if last <= 0:
                return
            advanced = pts - last
            if advanced < 0 or advanced > 30 * 90000:
                self._stall_count = 0
                return
            if advanced >= 90000:
                self._stall_count = 0
                self._stall_fail_count = 0
                return
            self._stall_count += 1
            my_log("Stall watchdog: frozen (count {}/{})".format(
                self._stall_count, self._STALL_TICKS))
            if self._stall_count >= self._STALL_TICKS:
                if self._stall_fail_count >= self._STALL_MAX_KICKS:
                    self.__stallSwitchCandidate(pts)
                else:
                    self.__stallRecover(pts)
        except Exception as e:
            my_log("stall watchdog error: {}".format(e))

    def __stallKick(self, target_secs):
        try:
            svc = self.session.nav.getCurrentService()
            if not svc:
                return False
            sk = svc.seek()
            if not sk:
                return False
            t = max(0, int(target_secs))
            _tot = self._total_secs
            if _tot > 0:
                t = min(t, _tot - 3)
            sk.seekTo(t * 90000)
            with novaplay_tracker._GLOBAL_POS_LOCK:
                novaplay_tracker._GLOBAL_LAST_SEEK_TARGET = t
                novaplay_tracker._GLOBAL_PLAY_START_POS = max(0, t - 2)
                novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
            if self._paused:
                self._paused_elapsed = t
            return True
        except Exception as e:
            my_log("stall kick error: {}".format(e))
            return False

    def __stallRecover(self, frozen_pts):
        self._stall_fail_count += 1
        self._stall_recovering = True
        self._stall_count = 0
        self._stall_recover_target = frozen_pts // 90000
        my_log("Stall watchdog: frozen ~{}s — kicking pipeline (attempt {})".format(
            self._stall_recover_target, self._stall_fail_count))
        try:
            self["status"].setText("⏳ Restoring playback…")
            self.__showOSD(True)
        except Exception:
            pass
        if self.__stallKick(self._stall_recover_target + 7):
            self._stall_kick_timer.start(1500, True)
        else:
            self._stall_recovering = False

    def __stallKickBack(self):
        if not getattr(self, "_stall_recovering", False):
            return
        try:
            t = getattr(self, "_stall_recover_target", 0)
            if t > 0 and self.__stallKick(t):
                my_log("Stall watchdog: playback restored at {}s".format(t))
                try:
                    self["status"].setText("Restored")
                    self.__showOSD(True)
                except Exception:
                    pass
        finally:
            self._stall_recovering = False
            self._stall_last_pts = -1
            self._stall_count = 0

    def __stallSwitchCandidate(self, frozen_pts):
        frozen = int(frozen_pts // 90000)
        my_log("Stall watchdog: dead stream — switching candidate (resume at {}s)".format(frozen))
        try:
            self._stall_timer.stop()
        except Exception:
            pass
        try:
            self._stall_kick_timer.stop()
        except Exception:
            pass
        self._stall_recovering = False
        self._stall_count = 0
        self._stall_fail_count = 0
        self._stall_last_pts = -1
        self._resume_pos = frozen
        self._autosub_gen = getattr(self, "_autosub_gen", 0) + 1
        try:
            self["status"].setText("🔄 Switching server…")
            self.__showOSD(True)
        except Exception:
            pass
        self.__playNext()

    def _skipDeadHostCandidates(self):
        """[2026-10-21] after a failed attempt: if the proxy saw this stream's
        server answer as down (Cloudflare 52x / gateway 502/504), the other
        ways of playing it would only fail too -- skip them (Street Fighter's
        LuluStream copy on a dead node spun ~90 s through six methods)."""
        try:
            idx = self._candidate_idx
            if idx < 0 or idx >= len(self.candidates):
                return
            up = _upstream_of(self.candidates[idx][1])
            code = novaplay_proxy.dead_host(up) if up else 0
            if not code:
                return
            host = novaplay_proxy._host_of(up)
            rest = [c for c in self.candidates[idx + 1:]
                    if novaplay_proxy._host_of(_upstream_of(c[1])) == host]
            if rest:
                my_log("candidates: {} is down (HTTP {}) -- skipping the other {} method(s)".format(
                    host, code, len(rest)))
                self.candidates = self.candidates[:idx + 1] + [
                    c for c in self.candidates[idx + 1:] if c not in rest]
        except Exception as e:
            my_log("dead-host check failed (non-fatal): {}".format(e))

    def __playNext(self):
        if getattr(self, "_is_advancing", False): return
        self._is_advancing = True
        if not self._play_confirmed:
            self._recordPlayMemory(False)   # the attempt we are leaving didn't play
            self._skipDeadHostCandidates()
        self._candidate_idx += 1
        if self._candidate_idx >= len(self.candidates):
            if not getattr(self, "_exhausted", False):
                self._exhausted = True
                try:
                    from plugin_server_rank import record_host_result, record_title_failed
                    record_host_result(self._server_url, False)
                    record_title_failed(self._item_url, self._server_url)
                except Exception as e:
                    my_log("server-rank record (exhausted) failed (non-fatal): {}".format(e))
                self._disarmSkipHint()
                self["status"].setText("Could not play the link after all attempts")
                def _fail_callback(ans):
                    if ans:
                        self._exhausted = False
                        self._candidate_idx = -1
                        self._play_confirmed = False
                        self._stall_last_pts = -1
                        self._stall_count = 0
                        self._stall_fail_count = 0
                        first = (self.candidates[0][1] if self.candidates else "")
                        if first:
                            self.candidates = _build_remote_play_candidates(first)
                            self.candidates = self._reorderCandidatesByPref(self.candidates)
                        self.__playNext()
                    else:
                        self.__onExit(clear_position=False)
                if getattr(self, "_quality_switch_in_flight", False):
                    self._quality_switch_in_flight = False
                    _tgt = getattr(self, "_quality_switch_target_url", "")
                    if _tgt and _tgt not in self._quality_dead_urls:
                        self._quality_dead_urls.add(_tgt)
                        my_log("quality: variant marked dead: {}…".format(_tgt[:70]))
                    _msg = (u"The new server did not play\nGo back to the previous server?"
                            if getattr(self, "_switch_kind", "") == "server" else
                            u"The new quality is not available (link expired)\nGo back to the previous quality?")
                    self.session.openWithCallback(
                        self._onQualityRetry, MessageBox, _msg,
                        MessageBox.TYPE_YESNO, timeout=10, default=True)
                else:
                    self.session.openWithCallback(
                        _fail_callback, MessageBox,
                        u"Could not play the link after all attempts\n\nTry again?",
                        MessageBox.TYPE_YESNO, timeout=8, default=True)
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

        self["status"].setText("Playing... {}".format(label))
        self.__showOSD(False)
        my_log("Play attempt: {}".format(label))
        try:
            self.session.nav.stopService()
        except: pass
        try:
            _nav_play_unhooked(self.session.nav, self.sref)
            # proxy attempts that are broken fail within a second or two; one that is
            # still waiting on a slow CDN playlist gets 30 s (seen: 16-46 s on the box)
            self._retry_timer.start(30000 if uses_proxy else 12000, True)
            self._force_confirmation_timer.start(3000, True)
            self._armSkipHint()
        except Exception as e:
            my_log("SimplePlayer fallback error: {}".format(e))
            self._is_advancing = False
            self.__playNext()
            return
        self._is_advancing = False

    def __onConfirmed(self):
        if self._play_confirmed: return
        self._play_confirmed = True
        self._confirmed_ts = time.time()
        try:
            self._retry_timer.stop()
            self._force_confirmation_timer.stop()
        except: pass
        self._disarmSkipHint()
        my_log("Play confirmed: {}".format(self._candidate_label))
        self._reapplySavedPep()
        self._recordPlayMemory(True)
        try:
            from plugin_server_rank import record_host_result, record_title_working
            record_host_result(self._server_url, True)
            record_title_working(self._item_url, self._server_url)
        except Exception as e:
            my_log("server-rank record (confirmed) failed (non-fatal): {}".format(e))
        try:
            _pk = _candidate_pref_key(self._item_url)
            if _pk and self._candidate_label and \
                    str(_get_config(_pk, "") or "") != self._candidate_label:
                _set_config(_pk, self._candidate_label)
        except Exception:
            pass
        if getattr(self, "_quality_pending_pref", ""):
            try:
                _qk = _quality_pref_key(getattr(self, "_raw_title", "") or self.title)
                if _qk and not str(self._quality_pending_pref).lower().startswith("quality "):
                    _set_config(_qk, self._quality_pending_pref)
                    my_log("quality pref saved: {} = {}".format(_qk, self._quality_pending_pref))
            except Exception as e:
                my_log("quality pref save failed: {}".format(e))
            self._quality_pending_pref = ""
        self._quality_switch_in_flight = False
        self._switch_kind = "quality"
        self._server_prev = None
        try:
            self._startVidySearch()
        except Exception:
            pass
        start_pos_tracker(self.session, self._item_url, start_pos=self._resume_pos)
        try:
            maybe_resume_subtitle(self.session, self.title, self._item_url)
        except Exception:
            pass
        try:
            self._studioTimer.start(100, False)
        except Exception:
            pass
        try:
            self._stall_last_pts = -1
            self._stall_count = 0
            self._stall_fail_count = 0
            self._stall_recovering = False
            self._stall_timer.start(self._STALL_TICK_MS, False)
        except Exception:
            pass
        try:
            if not has_subtitle_for(self._item_url):
                self.__startAutoSubtitle()
        except Exception:
            pass
        if self._resume_pos > 30:
            self._seek_retry_count = 0
            self._seek_timer.start(6000, True)
        self["osd_title"].setText(self.title)
        self["status"].setText(u"Playing")
        self._total_secs = 0
        self._watched_at_90_fired = False
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
        if (self._play_confirmed and getattr(self, "_candidate_uses_proxy", False)
                and time.time() - getattr(self, "_confirmed_ts", 0) < 180):
            # [FIX 2026-10-17] Box: a stream 'ended' 72 s after starting (the player gave up
            # waiting for a slow quality playlist). That was marked watched, the resume point
            # cleared and the player closed. A movie can't end within 3 minutes of starting:
            # treat it as a failure and try the next way of playing it.
            my_log("Early EOF {:.0f}s after start: treating as a failure ({})".format(
                time.time() - self._confirmed_ts, self._candidate_label))
            try:
                stop_pos_tracker()
            except Exception:
                pass
            try:
                from plugin_server_rank import retract_title_working
                retract_title_working(self._item_url, self._server_url)
            except Exception as e:
                my_log("server-rank retract failed (non-fatal): {}".format(e))
            self._play_confirmed = False
            self.__playNext()
            return
        if self._play_confirmed:
            try:
                from plugin_watched import mark_watched
                if self._item_url:
                    mark_watched(self._item_url)
            except Exception:
                pass
            if (self._next_episode and self._on_next
                    and self._next_prompt_enabled
                    and not getattr(self, "_eof_prompted", False)):
                self._eof_prompted = True
                my_log("EOF: next episode available — showing auto-next card")
                self._showAutoNextCard(self._next_episode)
                return
            if getattr(self, "_autonext_active", False):
                return
            my_log("Playback EOF reached, clearing resume position.")
            self.__onExit(clear_position=True)
            return
        self.__onFailed()

    def __onTimeout(self):
        if self._play_confirmed: return
        if self._candidate_uses_proxy and novaplay_proxy._PROXY_LAST_HIT >= self._candidate_start_ts and novaplay_proxy._PROXY_LAST_BYTES > 50000:
            my_log("Play proxy confirmed by traffic: {} bytes".format(novaplay_proxy._PROXY_LAST_BYTES))
            self.__onConfirmed()
            return
        if (self._candidate_uses_proxy and novaplay_proxy.playlist_fetch_in_progress()
                and time.time() - self._candidate_start_ts < 150):
            my_log("Play: slow playlist still downloading or just arrived, waiting ({})".format(self._candidate_label))
            self._retry_timer.start(15000, True)
            return
        my_log("Play timeout: {}".format(self._candidate_label))
        self.__playNext()

    def __forceConfirm(self):
        if self._play_confirmed: return
        if self._candidate_uses_proxy and novaplay_proxy._PROXY_LAST_HIT >= self._candidate_start_ts and novaplay_proxy._PROXY_LAST_BYTES > 50000:
            my_log("Play proxy confirmed early by traffic: {} bytes".format(novaplay_proxy._PROXY_LAST_BYTES))
            self.__onConfirmed()

    # ─── auto-next card ──────────────────────────────────────────────────
    def _showAutoNextCard(self, nxt):
        _delay = self._nextDelaySecs()
        self._autonext_nxt = nxt
        self._autonext_secs = _delay
        try:
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
            (nxt or {}).get("title") or "", width=30, fallback="Next episode"))
        self["autoNextEpisode"].setText("Next episode")
        self["autoNextText"].setText("Playing automatically in %d seconds" % _delay)
        self["autoNextHelp"].setText("OK = play now    EXIT = cancel")
        try:
            self["autoNextProgress"].setRange((0, _delay))
            self["autoNextProgress"].setValue(_delay)
        except Exception:
            pass
        self._autonext_slide_p = 0.0
        for w in self._AUTONEXT_WIDGETS:
            try:
                inst = self[w].instance
                if inst:
                    dx = self._AUTONEXT_DESIGN_X.get(w, 1110)
                    inst.move(ePoint(dx + self._AUTONEXT_SLIDE_OFFSET,
                                     inst.position().y()))
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
        self._startAutoNextSlide(1.0)

    def _hideAutoNextCard(self):
        self._autonext_active = False
        self._autonext_poster_polls = 0
        try: self._autonext_timer.stop()
        except Exception: pass
        self._startAutoNextSlide(0.0)

    def __autonextTick(self):
        if not getattr(self, "_autonext_active", False):
            return
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
            self["autoNextText"].setText("Playing automatically in %d seconds" % self._autonext_secs)
            self["autoNextProgress"].setValue(self._autonext_secs)
            if self._autonext_secs <= 3:
                _c = "#FF6B6B" if (self._autonext_secs % 2) else "#E8E8E8"
                try:
                    if parseColor is not None:
                        self["autoNextText"].instance.setForegroundColor(parseColor(_c))
                except Exception:
                    pass
            else:
                try:
                    if parseColor is not None:
                        self["autoNextText"].instance.setForegroundColor(parseColor("#E8E8E8"))
                except Exception:
                    pass
        except Exception:
            pass

    def __nextAnswer(self, ans, nxt):
        if ans and self._on_next:
            self._skip_restore = True
            cb = self._on_next
            self.__onExit(clear_position=True)
            _defer_open_next(cb, nxt)
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
            self._onSubtitles()
            return
        if getattr(self, "_autonext_active", False):
            self._hideAutoNextCard()
            self.__onExit(clear_position=True)
            return
        if getattr(self, "_is_preview", False):
            # [detail-trailer] skip the near-end mark/save/next prompt
            # entirely for a trailer -- there's nothing to mark, save or
            # queue, so exit should just exit.
            self.__onExit()
            return
        # [player-ux] near-end exit prompt
        total = self._total_secs
        try:
            elapsed = (self._paused_elapsed if self._paused
                       else current_play_secs())
        except Exception:
            elapsed = 0
        near_end = (total > 0 and elapsed > 0
                    and float(elapsed) / float(total) >= 0.90)

        if near_end or (self._next_episode and self._next_prompt_enabled
                        and total > 0 and elapsed > 0
                        and float(elapsed) / float(total) >= 0.80):
            items = []
            if self._next_episode:
                items.append((u"▶ Next episode", "next"))
            items.append((u"✓ Mark as watched and exit", "mark"))
            items.append((u"↵ Save position and exit", "save"))
            items.append((u"✕ Cancel", "cancel"))
            self.session.openWithCallback(
                self._onExitChoice, ChoiceBox,
                title=u"Near the end — what now?",
                list=items)
            return
        self.__onExit()

    def _onExitChoice(self, choice):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "next":
            try:
                from plugin_watched import mark_watched
                if self._item_url:
                    mark_watched(self._item_url)
            except Exception:
                pass
            self._hideAutoNextCard()
            self.__nextAnswer(True, self._next_episode)
        elif action == "mark":
            try:
                from plugin_watched import mark_watched
                if self._item_url:
                    mark_watched(self._item_url)
            except Exception:
                pass
            self.__onExit(clear_position=True)
        elif action == "save":
            self.__onExit(clear_position=False)
        elif action == "cancel":
            return

    def __studioDedup(self, direction):
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
        if not getattr(self, "_studioOverlayActive", False):
            return
        if not (0 <= self._studioIdx < len(self._studioItems)):
            return
        key = self._studioItems[self._studioIdx][2]
        if key in ("font", "presets", "pickline", "reset", "remove"):
            return
        self._studioAdjust(direction)

    # ── [player-ux] hold-to-accelerate seek ────────────────────────────
    def _stepForHold(self, direction):
        now = time.time()
        if direction != getattr(self, "_hold_dir", 0) or \
                (now - getattr(self, "_hold_last_t", 0.0)) > 1.2:
            self._hold_dir = direction
            self._hold_count = 1
        else:
            self._hold_count = min(self._hold_count + 1, 30)
        self._hold_last_t = now
        n = self._hold_count
        if n <= 1:
            return 10
        if n <= 4:
            return 30
        return 60

    def __navLeftKey(self):
        if self._studioOverlayActive:
            self._studioMove(-1)
            return
        if not self.__studioDedup(-1):
            self.__seek(-self._stepForHold(-1))

    def __navRightKey(self):
        if self._studioOverlayActive:
            self._studioMove(+1)
            return
        if not self.__studioDedup(+1):
            self.__seek(+self._stepForHold(+1))

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
            return      # ◀ ▶ already arrive as left/right; acting here too skipped a card
        if getattr(self, "_autonext_active", False):
            return
        if not self.__studioDedup(+1):
            self.__seek(+self._stepForHold(+1))

    def __navSeekBackKey(self):
        if getattr(self, "_studioOverlayActive", False):
            return      # ◀ ▶ already arrive as left/right; acting here too skipped a card
        if getattr(self, "_autonext_active", False):
            return
        if not self.__studioDedup(-1):
            self.__seek(-self._stepForHold(-1))

    def __togglePause(self):
        if getattr(self, "_speed", 1) != 1:
            # first press while fast/slow: back to normal speed
            self._cancelSpeed()
            self["status"].setText(u"Playing")
            self.__showOSD(True)
            return
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
                self["status"].setText(u"Playing")
                self._hidePauseBadge()
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
                self["status"].setText(u"Paused")
                self._showPauseBadge()
            self.__showOSD(True)
        except Exception as e:
            my_log("togglePause error: {}".format(e))
            self.__showOSD(True)

    def __seek(self, delta_secs):
        self._cancelSpeed()
        try:
            self._last_seek_step = abs(int(delta_secs)) or self._last_seek_step  # [POLISH v1]
        except Exception:
            pass
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
            my_log("seek: delta={}s → target={}s".format(int(delta_secs), target))
            _th = target // 3600; _tm = (target % 3600) // 60; _ts = target % 60
            _arr = u"➡" if delta_secs > 0 else u"⬅"
            self["status"].setText(u"{} {:02d}:{:02d}:{:02d}".format(_arr, _th, _tm, _ts))
            self._showSeekFlash(1 if delta_secs > 0 else -1, delta_secs, target)
            self.__showOSD(True)
            self._showSeekBubble(target)
            self._hide_timer.start(2500, True)
        except Exception as e:
            my_log("seek error: {}".format(e))

    def __seekPct(self, pct):
        if getattr(self, "_studioOverlayActive", False):
            # [studio-ui] 1-4 pick a tab while the studio is open
            if pct in (10, 20, 30, 40):
                self._studioGroup(pct // 10 - 1)
            return
        self._cancelSpeed()
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
                self["status"].setText("Duration not known yet…")
                self.__showOSD(True)
                return
            self.__seek(int(total * pct / 100) - cur)
        except Exception as e:
            my_log("seekPct error: {}".format(e))

    def __onRestart(self):
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
        self["status"].setText(u"Restarting + resuming from {}:{:02d}...".format(
            self._resume_pos // 60, self._resume_pos % 60) if self._resume_pos > 30 else u"Restarting...")
        self.__showOSD(True)
        try:
            if getattr(self, "_restart_timer", None):
                self._restart_timer.stop()
        except Exception:
            pass
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
        try:
            self._disarmSkipHint()
        except Exception:
            pass
        stop_pos_tracker()
        try:
            self._restoreSystemAspect()
        except Exception:
            pass
        _saved_secs = 0
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
                    _floor = getattr(novaplay_tracker, "_GLOBAL_SAVE_FLOOR", 0)
                    if 30 < secs < _floor:
                        my_log("Exit save: kept {}s (resume jump failed, at {}s)".format(_floor, int(secs)))
                    elif secs > 30:
                        _save_position(self._item_url, secs, force=True)
                        _saved_secs = int(secs)
                        my_log("Exit save: {}s".format(secs))
                        if _tot > 0:
                            try:
                                from plugin_state import _save_total_duration
                                _save_total_duration(self._item_url, _tot)
                            except Exception:
                                pass
        except Exception as e:
            my_log("Exit save error: {}".format(e))
        if _saved_secs > 30:
            try:
                _t = _saved_secs // 3600
                _m = (_saved_secs % 3600) // 60
                _s = _saved_secs % 60
                _label = u"✓ Saved at {:02d}:{:02d}:{:02d}".format(_t, _m, _s) \
                    if _t else u"✓ Saved at {:02d}:{:02d}".format(_m, _s)
                self["seekFlash"].setText(_label)
                self["seekFlash"].show()
            except Exception:
                pass
        try:
            self.session.nav.stopService()
        except: pass
        try:
            from novaplay_substudio import STUDIO
            STUDIO.unbind()
        except Exception:
            pass
        if not getattr(self, "_skip_restore", False):
            _restore_previous_service(self.session, self.previous_service)
        self.close()

    def __stop(self):
        self._closed_flag = True
        _tr_call("resume_later")
        self.__hideOSD()
        try:
            self._restoreSystemAspect()
        except Exception:
            pass
        self._autosub_gen = getattr(self, "_autosub_gen", 0) + 1
        for t in ("_seek_timer","_seek_verify_timer","_retry_timer","_hide_timer",
                  "_osd_update_timer","_force_confirmation_timer","_restart_timer",
                  "_sleep_timer","_autonext_timer",
                  "_studioTimer","_rec_blink_timer",
                  "_stall_timer","_stall_kick_timer",
                  "_osd_slide_timer","_autonext_slide_timer",
                  "_seek_flash_timer","_quality_wipe_timer",
                  "_skip_hint_timer",
                  "_aspect_anim_timer","_studio_slide_timer",
                  "_sync_toast_timer"):
            try:
                timer = getattr(self, t, None)
                if timer: timer.stop()
            except Exception: pass

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
                # [2026-10-21] "-60" called 10 s a success for a 68 s target;
                # the tolerance now scales with the target (max 60 s)
                if actual_pos >= max(0, self._resume_pos - min(60, max(10, self._resume_pos // 3))):
                    with novaplay_tracker._GLOBAL_POS_LOCK:
                        novaplay_tracker._GLOBAL_PLAY_START_POS = actual_pos
                        novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
                        novaplay_tracker._GLOBAL_LAST_SEEK_TARGET = actual_pos
                    if self._paused:
                        self._paused_elapsed = actual_pos
                    my_log("verifySeek OK via PTS: actual={}s target={}s".format(actual_pos, self._resume_pos))
                else:
                    if seek and self._seek_retry_count < 3:
                        self._seek_retry_count += 1
                        seek.seekTo(self._resume_pos * 90000)
                        my_log("verifySeek double-tap {}/3: actual={}s target={}s".format(self._seek_retry_count, actual_pos, self._resume_pos))
                        self._seek_verify_timer.start(3000, True)
                    else:
                        with novaplay_tracker._GLOBAL_POS_LOCK:
                            novaplay_tracker._GLOBAL_PLAY_START_POS = max(0, int(actual_pos))
                            novaplay_tracker._GLOBAL_PLAY_START_WALL = time.time()
                            novaplay_tracker._GLOBAL_LAST_SEEK_TARGET = int(actual_pos)
                            # keep the saved place: until playback really passes it,
                            # don't overwrite 229 s with 19 s (box 2026-10-21)
                            novaplay_tracker._GLOBAL_SAVE_FLOOR = int(self._resume_pos)
                        my_log("verifySeek giving up — TRUSTING DEMUXER at {}s (target {}s not reached)".format(
                            actual_pos, self._resume_pos))
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

    # ── [player-ux] labelled audio-track entries ────────────────────────
    def __audioSelect(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        if AudioSelection is not None:
            try:
                self.session.open(AudioSelection, self)
                return
            except Exception as e:
                my_log("AudioSelection failed ({}), using list".format(e))
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
                extra = ""
                for probe in ("getTrackLanguage", "getTrackCodec"):
                    try:
                        fn = getattr(tracks, probe, None)
                        if fn:
                            v = fn(i)
                            if v:
                                extra = (extra + " · " + str(v)).strip(" ·")
                    except Exception:
                        pass
                if extra:
                    label = u"Track {} — {}{}{}".format(
                        i + 1, name, (u" · " + extra) if name else u"", u" ▶" if i == cur else u"")
                else:
                    label = u"Track {}: {}{}".format(i + 1, name, u" ▶" if i == cur else u"")
                entries.append((label, i))
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

    def __streamInfo(self):
        try:
            service = self.session.nav.getCurrentService()
            info = service.info() if service else None
            lines = [u"Stream info"]
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

    def _sleepLabel(self):
        return "off" if not self._sleep_minutes else "%d min" % self._sleep_minutes

    def __sleepMenu(self):
        entries = [("Off", 0), ("15 min", 15), ("30 min", 30), ("60 min", 60), ("90 min", 90)]
        self.session.openWithCallback(self.__sleepPicked, ChoiceBox, "Sleep timer", entries)

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

    def __recordCurrentStream(self):
        if getattr(self, "_record_task", None) and self._record_task.status == "downloading":
            self["status"].setText("Recording already running")
            self.__showOSD(True)
            return
        # [player-ux] disk-space guard
        try:
            _target = "/media/hdd"
            if not os.path.exists(_target):
                _target = "/"
            st = os.statvfs(_target)
            free_mb = (st.f_bavail * st.f_frsize) // (1024 * 1024)
            if free_mb < 500:
                self.session.open(
                    MessageBox,
                    u"Not enough disk space to record\nFree: {} MB (minimum 500 MB)".format(free_mb),
                    MessageBox.TYPE_WARNING, timeout=6)
                self.__showOSD(True)
                return
        except Exception as e:
            my_log("record disk-space check skipped: {}".format(e))
        url = ""
        referer = ""
        for cand in (self.candidates or []):
            if len(cand) >= 4 and cand[3]:
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

            _t = threading.Thread(target=_bg)
            _t.daemon = True
            _t.start()
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

    def __subtitleDelayBack(self):
        if getattr(self, "_studioOverlayActive", False):
            return
        if getattr(self, "_autonext_active", False):
            return
        try:
            adjust_sync(self.session, -SYNC_STEP_MS)
            state = get_subtitle_state()
            off = int(state.get("offset_ms") or 0)
            self["status"].setText("Subtitles: %+d ms" % off)
            self._showSyncToast("%+d ms" % off)
        except Exception as e:
            my_log("subtitle delay error: {}".format(e))
        self.__showOSD(True)

    def _playerMenu(self):
        """[menu-panel] Menu: Picture | Timer and record | Settings."""
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        from plugin_player_panel import NovaMenuPanel
        self.__hideOSD()
        self._menu_panel = self.session.openWithCallback(
            self._onMenuPanel, NovaMenuPanel, self._menuPictureItems(), self._menuTimerItems(),
            self._menuSettingsItems(), live_cb=self._menuLive)

    _SLEEP_STEPS = (0, 30, 60, 90, 120)
    _SKIP_STEPS = (60, 90, 120)

    def _picExtras(self):
        try:
            import novaplay_picture
            return novaplay_picture.available()
        except Exception as e:
            my_log("picture modes: {}".format(e))
            return []

    def _menuPictureItems(self):
        """Picture column: brightness / contrast / colour (+ reset) only --
        the aspect / zoom modes did nothing on the box and were removed."""
        rows = []
        self._pic_pep_rows = []
        try:
            import novaplay_picture as NP
            for key, lbl in NP.pep_available():
                pct = NP.pep_percent(key)
                rows.append((u"{} +   ({}%)".format(lbl, pct), False, True))
                rows.append((u"{} −".format(lbl), False, True))
                self._pic_pep_rows += [(key, +1), (key, -1)]
            if self._pic_pep_rows:
                rows.append((u"Reset (normal)", False, True))
                self._pic_pep_rows.append((None, 0))
        except Exception as e:
            my_log("picture pep: {}".format(e))
        if not rows:
            rows = [(u"Not supported by this box", False)]
        return rows

    def _applyPep(self, j):
        import novaplay_picture as NP
        key, d = self._pic_pep_rows[j]
        if key is None:
            for k, _l in NP.pep_available():
                NP.pep_set(k, 128)
                _set_config("pep_" + k, "")
            return u"Brightness / colour: normal"
        lvl = NP.pep_get(key) + d * NP.PEP_STEP
        ok = NP.pep_set(key, lvl)
        if ok:
            _set_config("pep_" + key, str(NP.pep_get(key)))
        lbl = dict(NP.PEP).get(key, key)
        return (u"{}: {}%".format(lbl, NP.pep_percent(key)) if ok
                else u"{}: not supported by this box".format(lbl))

    def _reapplySavedPep(self):
        """Brightness / contrast / colour picked in an earlier film."""
        try:
            import novaplay_picture as NP
            for key, _l in NP.pep_available():
                v = (_get_config("pep_" + key, "") or "").strip()
                if v.isdigit() and int(v) != 128:
                    NP.pep_set(key, int(v))
        except Exception as e:
            my_log("picture pep reapply: {}".format(e))

    def _applyPictureExtra(self, j):
        """Box display modes (letterbox / pan & scan / stretch / zoom) from
        novaplay_picture -> status text."""
        import novaplay_picture
        label, kind, value = self._pic_extra_list[j]
        self._aspect_idx = 0
        self._applyAspectMode()            # bars off, system aspect back
        ok = novaplay_picture.apply(kind, value)
        self._pic_extra = j if ok else None
        my_log("picture: {} ({} {}) -> {}".format(label, kind, value, "ok" if ok else "refused"))
        return u"Picture: {}".format(label) if ok else u"Picture: {} — not supported by this box".format(label)

    def _menuTimerItems(self):
        rows = [((u"Sleep: Off" if m == 0 else u"Sleep {} min".format(m)),
                 (self._sleep_minutes or 0) == m, True) for m in self._SLEEP_STEPS]
        rec_on = (getattr(self, "_record_task", None) is not None
                  and self._record_task.status == "downloading")
        rows.append((u"■ Stop recording" if rec_on else u"● Record stream", rec_on))
        return rows

    def _menuSettingsItems(self):
        try:
            skip = int(_get_config("skip_intro_secs", "90") or 90)
        except Exception:
            skip = 90
        rows = [(u"Next-ep prompt: {}".format("ON" if self._next_prompt_enabled else "OFF"),
                 self._next_prompt_enabled, True)]
        rows += [(u"Skip intro {}s".format(v), skip == v, True) for v in self._SKIP_STEPS]
        rows += [(u"Chapters", False), (u"Help", False), (u"Self-test", False)]
        return rows

    def _menuLive(self, col, idx):
        msg = u""
        if col == "picture":
            if idx < len(getattr(self, "_pic_pep_rows", []) or []):
                msg = self._applyPep(idx)
                try:
                    self["status"].setText(msg)
                except Exception:
                    pass
        elif col == "timer" and idx < len(self._SLEEP_STEPS):
            self.__sleepPicked((u"", self._SLEEP_STEPS[idx]))
            msg = u"Sleep: {}".format(self._sleepLabel())
        elif col == "settings":
            if idx == 0:
                self._next_prompt_enabled = not self._next_prompt_enabled
                _set_config("next_episode_prompt", "true" if self._next_prompt_enabled else "false")
                msg = u"Next-episode prompt: {}".format("ON" if self._next_prompt_enabled else "OFF")
            elif 1 <= idx <= len(self._SKIP_STEPS):
                v = self._SKIP_STEPS[idx - 1]
                _set_config("skip_intro_secs", str(v))
                msg = u"Skip intro: {}s".format(v)
        p = getattr(self, "_menu_panel", None)
        if p is not None:
            builders = {"picture": self._menuPictureItems, "timer": self._menuTimerItems,
                        "settings": self._menuSettingsItems}
            p.setColumn(col, builders[col]())
            try:
                p._sel[col] = idx
                p._paint()
            except Exception:
                pass
        return msg

    def _onMenuPanel(self, res):
        self._menu_panel = None
        if not res:
            return
        col, idx = res
        try:
            if col == "timer" and idx == len(self._SLEEP_STEPS):
                rec_on = (getattr(self, "_record_task", None) is not None
                          and self._record_task.status == "downloading")
                if rec_on:
                    self.__stopRecording()
                else:
                    self.__recordCurrentStream()
            elif col == "settings":
                base = 1 + len(self._SKIP_STEPS)
                act = idx - base
                if act == 0:
                    self._showChapters()
                elif act == 1:
                    self._showHelp()
                elif act == 2:
                    self._runSelfTest()
        except Exception as e:
            my_log("menu panel pick error: {}".format(e))

    def _onPlayerMenuChoice(self, choice):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "subs":
            self._subsPanel()
        elif action == "audio":
            self.__audioSelect()
        elif action == "info":
            self.__streamInfo()
        elif action == "chapters":
            self._showChapters()
        elif action == "selftest":
            self._runSelfTest()
        elif action == "help":
            self._showHelp()
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

    # ── [player-ux] chapters / jump points ──────────────────────────────
    # Streams here have no real chapter markers (remote URLs, not local
    # files we could ffprobe), so this mirrors the WGFileManager player's
    # fallback path directly: synthetic quarter-marks across the known
    # duration, plus the saved resume point as an extra entry when one
    # exists. Reuses __seek()'s existing OSD-flash + tracker bookkeeping
    # by converting the chosen absolute target into a relative delta.
    @staticmethod
    def _fmtChapterTime(secs):
        secs = max(0, int(secs))
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return u"{:d}:{:02d}:{:02d}".format(h, m, s) if h else u"{:d}:{:02d}".format(m, s)

    def _showChapters(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        total = self._total_secs
        if not total or total <= 60:
            self["status"].setText(u"Duration not known yet — no jump points")
            self.__showOSD(True)
            return
        marks = [
            (u"Start", 0),
            (u"25%", total * 0.25),
            (u"Halfway", total * 0.5),
            (u"75%", total * 0.75),
            (u"End", max(0, total - 10)),
        ]
        items = [(u"{}   {}".format(name, self._fmtChapterTime(t)), int(t))
                 for name, t in marks]
        try:
            saved = _get_saved_position(self._item_url) if self._item_url else 0
        except Exception:
            saved = 0
        if saved and saved > 30:
            items.append((u"↺ Resume point   {}".format(
                self._fmtChapterTime(saved)), int(saved)))
        self.session.openWithCallback(self._onChapterChosen, ChoiceBox,
                                      title=u"Jump to", list=items)

    def _onChapterChosen(self, choice):
        if not choice:
            return
        target = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        try:
            target = int(target)
        except Exception:
            return
        try:
            cur = int(self._paused_elapsed if self._paused else current_play_secs())
        except Exception:
            cur = 0
        self.__seek(target - cur)

    # ── [player-ux] on-device help / keymap reference ───────────────────
    def _showHelp(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        lines = [
            u"Player Controls",
            u"",
            u"Playback",
            u"OK            Play / Pause",
            u"STOP          Exit straight to TV",
            u"EXIT/CANCEL   Exit — near the end: next / mark / save / cancel",
            u"",
            u"Seeking",
            u"LEFT / RIGHT     Seek (hold to speed up 10s→30s→60s)",
            u"UP / DOWN        Seek 60 seconds",
            u"FF / REW         Seek (same hold speed-up)",
            u"0-9              Jump to 0%/10%/20%/…/90% of the runtime (0 = start)",
            u"",
            u"Tracks & Subtitles",
            u"BLUE / SUBTITLE  Audio, subtitles, more panel",
            u"AUDIO            Audio track selection",
            u"",
            u"Quality & Info",
            u"RED           Next server",
            u"GREEN         Skip intro / next episode",
            u"YELLOW        Quality, server, speed panel",
            u"INFO          Stream info",
            u"",
            u"MENU          Picture (brightness / contrast / colour) · Sleep timer & recording ·",
            u"              Settings (next episode, skip-intro length, chapters, help)",
        ]
        self.session.open(MessageBox, u"\n".join(lines), MessageBox.TYPE_INFO)

    # ── [player-ux] non-destructive self-test ────────────────────────────
    # Mostly read-only queries (service/seek reachability, track counts,
    # subtitle state, proxy stats, OSD widget attachment). The only real
    # actions are a pause/unpause round-trip and a tiny +5s/-5s seek
    # round-trip, both of which restore the original state -- same
    # convention as the WGFileManager player's self-test, scaled down
    # since this is a live/remote stream rather than a local file.
    def _selfTestAudioQuery(self):
        svc = self.session.nav.getCurrentService()
        if not svc:
            return False
        tracks = svc.audioTracks()
        return bool(tracks and tracks.getNumberOfTracks() >= 1)

    def _selfTestSubtitleQuery(self):
        state = get_subtitle_state()
        if self._item_url:
            has_subtitle_for(self._item_url)
        return isinstance(state, dict)

    def _selfTestPauseRoundtrip(self):
        was_paused = self._paused
        self.__togglePause()
        self.__togglePause()
        return self._paused == was_paused

    def _selfTestSeekRoundtrip(self):
        if not self._total_secs or self._total_secs < 20:
            return True
        before = self._paused_elapsed if self._paused else current_play_secs()
        self.__seek(5)
        self.__seek(-5)
        after = self._paused_elapsed if self._paused else current_play_secs()
        return abs(after - before) <= 3

    def _selfTestOSDRoundtrip(self):
        was_visible = self._osd_visible
        self.__showOSD(True)
        shown = self._osd_visible
        if not was_visible:
            self.__hideOSD()
        return shown is True

    def _runSelfTest(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        results = []

        def t(name, fn):
            try:
                r = fn()
                results.append((name, "OK" if r is not False else "returned False"))
            except Exception as e:
                results.append((name, "FAIL: {}".format(e)))

        t("service-reachable", lambda: self.session.nav.getCurrentService() is not None)
        t("seek-object", lambda: bool(self.session.nav.getCurrentService() and
                                       self.session.nav.getCurrentService().seek()))
        t("duration-known", lambda: self._total_secs > 0)
        t("audio-tracks-query", self._selfTestAudioQuery)
        t("subtitle-state-query", self._selfTestSubtitleQuery)
        t("proxy-stats", lambda: bool(novaplay_proxy.get_stats()))
        t("osd-widgets-attached", lambda: all(
            self[n].instance is not None for n in self._OSD_WIDGETS))
        t("pause-roundtrip", self._selfTestPauseRoundtrip)
        t("seek-roundtrip", self._selfTestSeekRoundtrip)
        t("osd-show-hide", self._selfTestOSDRoundtrip)

        ok = sum(1 for _, r in results if r == "OK")
        for name, r in results:
            my_log("[SelfTest] {:22s} {}".format(name, r))
        self["status"].setText(u"Self-test: {}/{}".format(ok, len(results)))
        self.__showOSD(True)
        # Bilingual header, same precedent as __streamInfo: an Arabic
        # header over technical/diagnostic lines left in English, since
        # the check names themselves (service-reachable, osd-widgets-
        # attached, …) are debug labels, not end-user copy.
        lines = [u"Self-test: {}/{} passed".format(ok, len(results)), u""]
        for name, r in results:
            mark = u"✓" if r == "OK" else u"✗"
            lines.append(u"{} {}".format(mark, name) if r == "OK"
                         else u"{} {} — {}".format(mark, name, r))
        self.session.open(MessageBox, u"\n".join(lines), MessageBox.TYPE_INFO, timeout=15)

    def _aspectLabel(self):
        try:
            idx = getattr(self, "_aspect_idx", 0)
            n_fake = len(self._ASPECT_MODES)
            if idx < n_fake:
                return self._ASPECT_MODES[idx][0]
            return self._AV_PROBE_MODES[idx - n_fake][0]
        except Exception:
            return "Full"

    def __cycleAspect(self):
        if getattr(self, "_studioOverlayActive", False):
            return
        if getattr(self, "_autonext_active", False):
            return
        total = len(self._ASPECT_MODES) + len(self._AV_PROBE_MODES)
        self._aspect_idx = (getattr(self, "_aspect_idx", 0) + 1) % total
        self._applyAspectMode()

    def _applyAspectMode(self):
        idx = getattr(self, "_aspect_idx", 0)
        n_fake = len(self._ASPECT_MODES)
        if idx < n_fake:
            self._restoreSystemAspect()
            label, bar = self._ASPECT_MODES[idx]
            # [player-anim] slide the bars from 0 → bar width over 150 ms
            self._aspect_anim_from = 0
            self._aspect_anim_to = int(bar)
            self._aspect_anim_p = 0.0
            try:
                self["aspect_bar_l"].show()
                self["aspect_bar_r"].show()
                self._aspect_anim_timer.start(20, False)
            except Exception as e:
                my_log("aspect slide start failed: {}".format(e))
                try:
                    if bar:
                        self["aspect_bar_l"].instance.resize(eSize(bar, 1080))
                        self["aspect_bar_l"].instance.move(ePoint(0, 0))
                        self["aspect_bar_r"].instance.resize(eSize(bar, 1080))
                        self["aspect_bar_r"].instance.move(ePoint(1920 - bar, 0))
                        self["aspect_bar_l"].show()
                        self["aspect_bar_r"].show()
                    else:
                        self["aspect_bar_l"].hide()
                        self["aspect_bar_r"].hide()
                except Exception:
                    pass
            self["status"].setText("Aspect: %s" % label)
        else:
            try:
                self["aspect_bar_l"].hide()
                self["aspect_bar_r"].hide()
            except Exception:
                pass
            plabel, av_value = self._AV_PROBE_MODES[idx - n_fake]
            ok = self._setSystemAspect(av_value)
            self["status"].setText("Aspect PROBE: %s → %s" % (
                plabel, "applied" if ok else "eAVSwitch FAILED"))
        self.__showOSD(True)

    def _setSystemAspect(self, value):
        if eAVSwitch is None:
            return False
        try:
            eAVSwitch.getInstance().setAspectRatio(int(value))
            self._av_aspect_dirty = True
            my_log("aspect: eAVSwitch.setAspectRatio({})".format(value))
            return True
        except Exception as e:
            my_log("aspect: eAVSwitch error: {}".format(e))
            return False

    def _restoreSystemAspect(self):
        try:
            import novaplay_picture
            novaplay_picture.restore()
        except Exception:
            pass
        if not getattr(self, "_av_aspect_dirty", False):
            return
        saved = getattr(self, "_saved_av_aspect", None)
        if saved is None:
            return
        try:
            if eAVSwitch is not None:
                eAVSwitch.getInstance().setAspectRatio(int(saved))
            my_log("aspect: system aspect restored to {}".format(saved))
        except Exception as e:
            my_log("aspect: restore error: {}".format(e))
        self._av_aspect_dirty = False

    # ── [subs-panel] Blue: Audio | Subtitles | More (Movy-style) ─────────
    def _audioTrackItems(self):
        items = []
        try:
            service = self.session.nav.getCurrentService()
            tracks = service and service.audioTracks()
            n = tracks.getNumberOfTracks() if tracks else 0
            cur = tracks.getCurrentTrack() if n else -1
            for i in range(n):
                name = ""
                try:
                    info = tracks.getTrackInfo(i)
                    name = " ".join(x for x in (info.getLanguage(), info.getDescription()) if x)
                except Exception:
                    pass
                if not name:
                    try:
                        name = tracks.getTrackName(i) or ""
                    except Exception:
                        name = ""
                items.append((name or u"Track {}".format(i + 1), i == cur))
        except Exception as e:
            my_log("audio tracks: {}".format(e))
        return items

    def _subsItems(self):
        """Subtitles column + the action behind each row."""
        st = get_subtitle_state() or {}
        path = st.get("path") or ""
        rows, acts = [], []
        rows.append((u"Off — no subtitles", not path)); acts.append(("off", None))
        if path:
            rows.append((os.path.basename(st.get("_orig_path") or path)[:40], True)); acts.append(("cur", None))
        res = getattr(self, "_vidy_results", None)
        if res is None:
            rows.append((u"⏳ Movy subtitles…", False)); acts.append(("wait", None))
        elif not res:
            rows.append((u"Movy: " + (getattr(self, "_vidy_error", "") or u"No subtitles")[:34], False))
            acts.append(("wait", None))
        else:
            for r in res[:60]:
                hi = u" HI" if "(HI)" in (r.get("label") or "") else u""
                rows.append((u"{} · {}{}".format(r.get("language", "?"), r.get("title", "")[:30], hi), False))
                acts.append(("vidy", r))
        self._subs_acts = acts
        return rows

    def _moreItems(self):
        st = get_subtitle_state() or {}
        autosub_on = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
        studio_on = str(_get_config("substudio_mode", "false")).lower() == "true"
        try:
            skip_s = max(10, min(600, int(_get_config("skip_intro_secs", "90") or 90)))
        except Exception:
            skip_s = 90
        rows, acts = [], []
        if st.get("path"):
            off = int(st.get("offset_ms") or 0)
            rows += [(u"Sync +{} ms".format(SYNC_STEP_MS), False, True),
                     (u"Sync −{} ms".format(SYNC_STEP_MS), False, True),
                     (u"Reset sync ({:+d})".format(off), False, True)]
            acts += ["sync+", "sync-", "sync0"]
        rows.append((u"Auto subs: {}".format("ON" if autosub_on else "OFF"), autosub_on, True)); acts.append("autosub_toggle")
        rows.append((u"Skip intro +{}s".format(skip_s), False)); acts.append("skipintro")
        rows.append((u"Studio: {}".format("ON" if studio_on else "OFF"), studio_on)); acts.append("studio_toggle")
        if studio_on:
            rows.append((u"Studio settings", False)); acts.append("studio_open")
        rows += [(u"SubSource", False), (u"OpenSubtitles", False), (u"Local files", False)]
        acts += ["subsource", "opensubtitles", "browse"]
        self._more_acts = acts
        return rows

    def _subsPanel(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        from plugin_player_panel import NovaSubsPanel
        audio = self._audioTrackItems()
        self._audio_count = len(audio)
        if not audio:
            audio = [(u"— " + u"single track", True)]
        subs = self._subsItems()
        more = self._moreItems()
        self.__hideOSD()
        self._subs_panel = self.session.openWithCallback(
            self._onSubsPanel, NovaSubsPanel, audio, subs, more, live_cb=self._subsLive)
        self._startVidySearch()

    def _startVidySearch(self):
        """Movy subtitle list, fetched once per film in the background (started
        when playback is confirmed, so Blue opens with the list already there)."""
        if getattr(self, "_vidy_results", None) is not None or getattr(self, "_vidy_busy", False):
            return
        if getattr(self, "_is_preview", False):
            return
        self._vidy_busy = True
        title, item_url = self._raw_title or self.title, self._item_url

        def _bg():
            try:
                from novaplay_subtitles import vidy_search
                ok, err, res = vidy_search(title, item_url)
            except Exception as e:
                ok, err, res = False, str(e)[:40], []
            callInMainThread(self._onVidyResults, res if ok else [], "" if ok else err)
        threading.Thread(target=_bg, daemon=True).start()

    def _onVidyResults(self, res, err):
        self._vidy_busy = False
        self._vidy_results = res or []
        self._vidy_error = err or ""
        my_log("subs panel: Movy subtitles {} ({})".format(len(self._vidy_results), err or "ok"))
        p = getattr(self, "_subs_panel", None)
        if p is not None:
            try:
                p.setColumn("subs", self._subsItems())
            except Exception:
                pass

    def _subsLive(self, col, idx):
        """'stay' rows: run without closing the panel; the text goes on its help line."""
        if col != "more":
            return ""
        act = self._more_acts[idx] if idx < len(self._more_acts) else ""
        msg = ""
        if act == "sync+":
            msg = adjust_sync(self.session, SYNC_STEP_MS)
        elif act == "sync-":
            msg = adjust_sync(self.session, -SYNC_STEP_MS)
        elif act == "sync0":
            msg = reset_sync(self.session)
        elif act == "autosub_toggle":
            was = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
            _set_config("autosub", "false" if was else "true")
            msg = u"Auto subtitles: {}".format("OFF" if was else "ON")
        p = getattr(self, "_subs_panel", None)
        if p is not None:
            sel = idx
            p.setColumn("more", self._moreItems())
            try:
                p._sel["more"] = min(sel, len(p._data["more"]) - 1)
                p._paint()
            except Exception:
                pass
        my_log("subs panel: {} -> {}".format(act, msg))
        return msg or u"✓"

    def _onSubsPanel(self, res):
        self._subs_panel = None
        if not res:
            return
        col, idx = res
        try:
            if col == "audio":
                if idx < getattr(self, "_audio_count", 0):
                    self.__audioPicked((u"", idx))
            elif col == "subs":
                act, data = self._subs_acts[idx]
                if act == "off":
                    turn_off_subtitle(self.session, self._item_url)
                    self["status"].setText(u"CC off")
                    self.__showOSD(True)
                elif act == "vidy":
                    self._downloadVidySub(data)
            elif col == "more":
                act = self._more_acts[idx]
                self._onSubtitleAction((u"", act))
        except Exception as e:
            my_log("subs panel pick error: {}".format(e))

    def _downloadVidySub(self, r):
        self["status"].setText(u"⬇ {} …".format(r.get("language", "")))
        self.__showOSD(True)
        title, item_url = self._raw_title or self.title, self._item_url

        def _bg():
            try:
                from novaplay_subtitles import vidy_download
                ok, err, path = vidy_download(r.get("id"), title)
            except Exception as e:
                ok, err, path = False, str(e)[:60], ""
            callInMainThread(self._onVidyDownloaded, ok, err, path, r)
        threading.Thread(target=_bg, daemon=True).start()

    def _onVidyDownloaded(self, ok, err, path, r):
        if getattr(self, "_closed_flag", False):
            return
        if ok and path:
            remember_subtitle(self._item_url, path, 0)
            update_subtitle_state(path=path, _orig_path=path, offset_ms=0)
            disable_subtitle(self.session)
            apply_subtitle(self.session, path)
            self["status"].setText(u"CC ✓ {}".format(r.get("language", "")))
        else:
            self["status"].setText(u"✗ {}".format(err or u"download failed"))
        self.__showOSD(True)

    def _onSubtitles(self):
        studio_on = str(_get_config("substudio_mode", "false")).lower() == "true"
        autosub_on = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
        try:
            _skip_s = max(10, min(600, int(_get_config("skip_intro_secs", "90") or 90)))
        except Exception:
            _skip_s = 90
        items = [
            ("Skip intro +%ds" % _skip_s, "skipintro"),
            ("Auto subtitles (%s)" % ("ON" if autosub_on else "OFF"), "autosub_toggle"),
            ("Audio tracks", "audio"),
            ("Quality", "quality"),
            ("Subtitle Studio (%s)" % ("ON" if studio_on else "OFF"), "studio_toggle"),
        ]
        if studio_on:
            items.append(("Studio settings", "studio_open"))
        items.append(("Browse local subtitles", "browse"))
        items.append(("Search online (Movy — Arabic/English, no key)", "vidy"))
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
        if choice == "autosub_toggle":
            _was = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
            _set_config("autosub", "false" if _was else "true")
            return
        if choice == "audio":
            self.__audioSelect()
            return
        if choice == "skipintro":
            self.__skipIntro()
            return
        if choice == "quality":
            self.__panelMenu()
            return
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
        elif choice in ("vidy", "subsource", "opensubtitles"):
            self.session.openWithCallback(self._onSubtitlePicked,
                                          NovaOnlineSubsScreen, self.title,
                                          self._item_url, choice,
                                          _cfg(choice + "_api_key", ""))
        elif choice == "sync+":
            my_log(adjust_sync(self.session, SYNC_STEP_MS))
        elif choice == "sync-":
            my_log(adjust_sync(self.session, -SYNC_STEP_MS))
        elif choice == "sync0":
            my_log(reset_sync(self.session))
        elif choice == "off":
            turn_off_subtitle(self.session, self._item_url)

    def _onSubtitlePicked(self, path):
        if path and os.path.exists(path):
            remember_subtitle(self._item_url, path, 0)
            update_subtitle_state(path=path, _orig_path=path, offset_ms=0)
            disable_subtitle(self.session)
            apply_subtitle(self.session, path)

    # ── Subtitle Studio overlay (v3) ─────────────────────────────────────
    def _studioItemsList(self):
        from novaplay_substudio import STUDIO
        s = STUDIO.style
        align_lbl = ["Top", "Bottom", "Center"][s["align"]]
        font_lbl = os.path.basename(s.get("font_path") or "") or s.get("font_name") or "Default"
        try:
            _delay_val = int(get_subtitle_state().get("offset_ms") or 0)
        except Exception:
            _delay_val = int(STUDIO.get_offset() or 0)
        return [
            ("Delay", "%+d ms" % _delay_val, "delay", 250),
            ("Size", "%d px" % s["size"], "size", 2),
            ("Font", font_lbl + "  ›", "font", 1),
            ("Color", ["White","Yellow","Gold","Cyan","Red","Green"][s["color_idx"] % 6], "color", 1),
            ("Outline", "On" if s["outline"] else "Off", "outline", 1),
            ("Outline color", ["White","Yellow","Gold","Cyan","Red","Green"][s["outline_color_idx"] % 6], "ocolor", 1),
            ("Background", "On" if s["bg"] else "Off", "bg", 1),
            ("Opacity", "%d%%" % int(round((255 - int(s["bg_alpha"])) * 100 / 255.0)), "bg_alpha", 16),
            ("Position", "%+d px" % int(s["offset_y"]), "offset_y", 10),
            ("Align", align_lbl, "align", 1),
            ("Spacing", str(s["spacing"]), "spacing", 2),
            ("Auto-wrap", "On" if s.get("auto_wrap", True) else "Off", "autowrap", 1),
            ("Cue position", "On" if s.get("use_cue_pos", True) else "Off", "cuepos", 1),
            ("Presets", "A / B / C  ›", "presets", 0),
            ("Pick line to sync", "OK  ›", "pickline", 0),
            ("Reset", "OK  ›", "reset", 0),
            ("Remove", "OK  ›", "remove", 0),
        ]

    def _showStudioOverlay(self):
        self._studioOverlayActive = True
        self._studioItems = self._studioItemsList()
        if self._studioIdx >= len(self._studioItems):
            self._studioIdx = 0
        # [FIX] keep the subtitles running under the overlay so every edit is
        # seen live on the real subtitle (the tick used to be stopped here)
        try:
            self._studioTimer.start(100, False)
        except Exception:
            pass
        for k in self._STUDIO_KEYS:
            try:
                self[k].show()
            except Exception:
                pass
        # [player-anim] drop the panel down from -320 → design
        self._studio_slide_p = 0.0
        self._studio_slide_target = 1.0
        self._applyStudioSlide()
        try:
            self._studio_slide_timer.start(20, False)
        except Exception:
            pass
        self._studioRefresh()

    def _hideStudioOverlay(self):
        self._studioOverlayActive = False
        try:
            self._studioTimer.start(100, False)
        except Exception:
            pass
        # [player-anim] slide the panel back off the top before hiding
        self._studio_slide_target = 0.0
        try:
            self._studio_slide_timer.start(20, False)
        except Exception:
            for k in self._STUDIO_KEYS:
                try: self[k].hide()
                except Exception: pass
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

    # ── [studio-ui] tabs ────────────────────────────────────────────────
    def _studioGroupIdx(self, idx=None):
        idx = self._studioIdx if idx is None else idx
        try:
            key = self._studioItems[idx][2]
        except Exception:
            return 0
        for g, (_t, keys) in enumerate(self._STUDIO_GROUPS):
            if key in keys:
                return g
        return 0

    def _studioMembers(self, g):
        keys = self._STUDIO_GROUPS[g][1]
        pos = dict((it[2], i) for i, it in enumerate(self._studioItems))
        return [pos[k] for k in keys if k in pos]

    def _studioGroup(self, g):
        g %= len(self._STUDIO_GROUPS)
        members = self._studioMembers(g)
        if members:
            self._studioIdx = members[0]
        self._studioRefresh()

    def _studioRefresh(self):
        total = len(self._studioItems)
        if not total:
            return
        idx = max(0, min(self._studioIdx, total - 1))
        self._studioIdx = idx
        g = self._studioGroupIdx(idx)
        members = self._studioMembers(g)
        # tabs
        for t, tab in enumerate(self._STUDIO_TABS):
            try:
                self[tab].setText(self._STUDIO_GROUPS[t][0])
                inst = self[tab].instance
                if inst and parseColor is not None:
                    inst.setBackgroundColor(parseColor("#F2C230" if t == g else "#171718"))
                    inst.setForegroundColor(parseColor("#0C1017" if t == g else "#8B949E"))
                    inst.invalidate()
            except Exception:
                pass
        # cards: this tab's settings, centred, the chosen one in gold
        n = len(members)
        start = (len(self._STUDIO_CARDS) - n) // 2
        from novaplay_substudio import STUDIO, _COLORS
        for slot, card in enumerate(self._STUDIO_CARDS):
            j = slot - start
            if not (0 <= j < n):
                try:
                    self[card].hide()
                except Exception:
                    pass
                continue
            item_i = members[j]
            label, value, key, _s = self._studioItems[item_i]
            sel = item_i == idx
            txt = u"%s\n%s" % (label, value) if value else label
            fg = "#F0F6FC" if sel else "#D0D0D0"
            if key in ("color", "ocolor"):
                ci = STUDIO.style["color_idx" if key == "color" else "outline_color_idx"] % len(_COLORS)
                fg = "#" + _COLORS[ci][0][-6:]
            try:
                self[card].setText(txt)
                inst = self[card].instance
                if inst:
                    pos = inst.position()
                    inst.move(ePoint(self._STUDIO_SLOTS_X[slot], int(pos.y())))
                    if parseColor is not None:
                        inst.setBackgroundColor(parseColor("#5C4A12" if sel else "#171718"))
                        inst.setForegroundColor(parseColor(fg))
                        inst.invalidate()
                self[card].show()
            except Exception:
                pass
        try:
            pos_in = members.index(idx) + 1 if idx in members else 1
            self["studioStatus"].setText(u"%d / %d" % (pos_in, max(1, n)))
        except Exception:
            pass
        cur_key = self._studioItems[idx][2]
        try:
            self["studioPreview"].setText(self._STUDIO_HINTS.get(cur_key, u""))
        except Exception:
            pass
        try:
            self["studioPreviewBg"].hide()
        except Exception:
            pass
        show_safe = cur_key in ("offset_y", "align")
        for k in ("safeTop", "safeBottom", "safeLeft", "safeRight"):
            try:
                if show_safe:
                    self[k].show()
                else:
                    self[k].hide()
            except Exception:
                pass

    def _studioChKey(self, d):
        if getattr(self, "_studioOverlayActive", False):
            self._studioGroup(self._studioGroupIdx() + d)

    def _studioMove(self, step):
        """◀ ▶ inside the current tab."""
        if not self._studioItems:
            return
        g = self._studioGroupIdx()
        members = self._studioMembers(g)
        if self._studioIdx not in members:
            self._studioGroup(g)
            return
        # stays inside the tab (wraps around); 1-4 change the tab
        j = (members.index(self._studioIdx) + step) % len(members)
        self._studioIdx = members[j]
        self._studioRefresh()

    def _studioDelayTotal(self):
        try:
            return int(get_subtitle_state().get("offset_ms") or 0)
        except Exception:
            try:
                from novaplay_substudio import STUDIO
                return int(STUDIO.get_offset() or 0)
            except Exception:
                return 0

    def _studioSetDelay(self, new_off):
        from novaplay_substudio import STUDIO
        new_off = int(new_off)
        if not STUDIO.is_attached():
            adjust_sync(self.session, new_off - self._studioDelayTotal())
            return
        st = get_subtitle_state()
        base_path = st.get("_orig_path") or st.get("path") or ""
        if base_path and os.path.exists(base_path) and base_path != STUDIO.get_path():
            STUDIO.attach(base_path)
        STUDIO.set_offset(new_off)
        STUDIO._last_rendered = None
        if base_path:
            update_subtitle_state(path=base_path, _orig_path=base_path,
                                  offset_ms=new_off)
            if st.get("item_url"):
                try:
                    remember_subtitle(st["item_url"], base_path, new_off)
                except Exception:
                    pass
        my_log("studio delay: {:+d} ms".format(new_off))

    def _studioAdjust(self, step):
        if not (0 <= self._studioIdx < len(self._studioItems)):
            return
        from novaplay_substudio import STUDIO
        label, value, key, stp = self._studioItems[self._studioIdx]
        s = STUDIO.style
        if key == "delay":
            self._studioSetDelay(self._studioDelayTotal() + int(stp * step))
        elif key == "size":
            s["size"] = max(22, min(80, s["size"] + stp * step))
            STUDIO._layout_static()
        elif key == "color":
            s["color_idx"] = (s["color_idx"] + step) % 6
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
            s["outline_color_idx"] = (s["outline_color_idx"] + step) % 6
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
            turn_off_subtitle(self.session, self._item_url)
        elif key == "pickline":
            self._studioPickLine()
            return
        try:
            STUDIO.refresh()
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._studioRefresh()

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
            "Pick the line being spoken now", entries)

    def _onStudioPickLine(self, choice):
        from novaplay_substudio import STUDIO
        from novaplay_subtitles import get_subtitle_state, remember_subtitle
        if not choice:
            self._showStudioOverlay()
            return
        idx = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        cue = STUDIO._cues[idx]
        self._studioSetDelay(int(self._studioNowMs() - cue[0]))
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
                choices = [("White", 0), ("Yellow", 1), ("Gold", 2), ("Cyan", 3), ("Red", 4), ("Green", 5)]
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
                choices = [("White", 0), ("Yellow", 1), ("Gold", 2), ("Cyan", 3), ("Red", 4), ("Green", 5)]
            elif key == "align":
                choices = [("Top", 0), ("Bottom", 1), ("Center", 2)]
            self._studioChoiceKey = key
            self._hideStudioOverlay()
            self.session.openWithCallback(self._onStudioChoice, ChoiceBox,
                                          title=label, list=choices)
            return
        self._studioAdjust(1)

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
                self._studioSetDelay(int(value))
            elif key == "size":
                s["size"] = int(value)
                STUDIO._layout_static()
            elif key == "color":
                s["color_idx"] = int(value) % 6
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
                s["outline_color_idx"] = int(value) % 6
                STUDIO._layout_static()
            elif key == "align":
                s["align"] = int(value) % 3
        except Exception:
            pass
        try:
            STUDIO.refresh()
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()

    def _studioPresetMenu(self):
        items = []
        for slot in ("A", "B", "C"):
            items.append(("Load %s" % slot, "load_" + slot))
            items.append(("Save %s" % slot, "save_" + slot))
        self._hideStudioOverlay()
        self.session.openWithCallback(self._onStudioPreset, ChoiceBox,
                                      "Style Presets", items)

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
            <screen name="NovaFontPick" position="center,center" size="1500,860" backgroundColor="transparent" title="Select Font" flags="wfNoBorder">
                <eLabel position="0,0" size="1500,860" backgroundColor="#121822" cornerRadius="18" zPosition="0" />
        <eLabel position="40,78" size="140,4" backgroundColor="#F2C230" cornerRadius="2" zPosition="2" />
                <widget name="path" position="40,18" size="1420,44" font="Bold;30" foregroundColor="#F2C230" transparent="1" zPosition="2" />
                <widget name="list" position="30,100" size="1440,680" scrollbarMode="showOnDemand" foregroundColor="#D6DCE5" foregroundColorSelected="#F2C230" backgroundColor="#121822" backgroundColorSelected="#2B3648" zPosition="2" />
                <widget name="hint" position="40,800" size="1420,36" font="Regular;24" foregroundColor="#A3ADBB" transparent="1" zPosition="2" />
            </screen>"""
            def __init__(self2, session, start_dir):
                Screen.__init__(self2, session)
                self2["path"] = Label(start_dir)
                self2["list"] = FileList(start_dir, showDirectories=True,
                                         showFiles=True,
                                         matchingPattern=r"^.*\.(ttf|otf|ttc)$")
                self2["hint"] = Label("OK select  |  EXIT back  |  YELLOW default font")
                self2["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
                    "ok": self2._ok,
                    "cancel": lambda: self2.close("__keep__"),
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
        from novaplay_substudio import STUDIO
        try:
            if path == "__keep__" or path is None:
                pass
            elif path:
                STUDIO.register_font(path)
            else:
                STUDIO.use_default_font()
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()


# ─── [PATCH 32a] per-site candidate memory key ──────────────────────────────
def _resolve_server_for_player(site, server):
    """Extract a server's stream from inside the player (background thread).
    -> (stream_url, quality, referer, variants) or None."""
    url = server.get("url", "")
    if not url or url.startswith("magnet:"):
        return None
    ex = None
    try:
        from plugin_screen_detail import _get_extractor
        ex = _get_extractor(site) if site else None
    except Exception:
        ex = None
    if ex is None or getattr(ex, "extract_stream", None) is None:
        from extractors import base as ex
    try:
        from novaplay_proxy import cached_extract, attach_cookies
        result = attach_cookies(cached_extract(ex, url))
    except Exception:
        result = ex.extract_stream(url)
    if not result or not result[0]:
        return None
    variants = result[3] if len(result) >= 4 else []
    if not variants:
        try:
            from extractors.base import get_last_quality_variants
            variants = get_last_quality_variants() or []
        except Exception:
            variants = []
    return result[0], result[1], result[2], variants


def _stream_play_url(stream_url, final_ref=""):
    """Same header handling as the detail screen: 'url|K=V&..' -> 'url#K=V&..'."""
    raw = str(stream_url or "").strip()
    main_url, old_params = (raw.split("|", 1) + [""])[:2] if "|" in raw else (raw, "")
    headers = {"User-Agent": SAFE_UA}
    if final_ref:
        headers["Referer"] = final_ref
    for p in (old_params.split("&") if old_params else []):
        if "=" in p:
            k, v = p.split("=", 1)
            headers[k] = v
    hs = "&".join("{}={}".format(k, v) for k, v in headers.items())
    pure = main_url.split("|")[0].strip()
    return pure + ("#" + hs if hs else "")


def _candidate_pref_key(item_url):
    try:
        host = urlparse(str(item_url or "")).netloc or ""
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        return ("cand_pref_" + host.replace(".", "_")) if host else ""
    except Exception:
        return ""


# ─── Global play function ──────────────────────────────────────────────────

def _play(session, url, title, resume_pos=0, item_url="",
          next_episode=None, on_next=None, poster_url="",
          quality_variants=None, server_url="", servers=None, site="", server_name=""):
    try:
        svc_url = str(url).strip()
        is_remote = svc_url.startswith("http://") or svc_url.startswith("https://")
        previous_service = _capture_previous_service(session)
        if is_remote:
            session.open(NovaPlaySimplePlayer, title,
                         _build_remote_play_candidates(svc_url), previous_service,
                         resume_pos=resume_pos, item_url=item_url,
                         next_episode=next_episode, on_next=on_next,
                         poster_url=poster_url,
                         quality_variants=quality_variants,
                         server_url=server_url, servers=servers, site=site,
                         server_name=server_name)
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
            session.open(NovaPlaySimplePlayer, title,
                         _build_remote_play_candidates(svc_url), previous_service,
                         resume_pos=resume_pos, item_url=item_url,
                         next_episode=next_episode, on_next=on_next,
                         poster_url=poster_url,
                         server_url=server_url)
    except Exception as e:
        my_log("[PLAY_ERROR] " + str(e))                                