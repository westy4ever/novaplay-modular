# -*- coding: utf-8 -*-
"""NovaPlay player -- shared module level: imports, candidate building,
service capture / restore and small helpers. Everything here is
re-exported to plugin_screen_player and the player_* parts.
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
from plugin_common import my_log, set_play_id


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


# every name (also the _private ones) for "from player_base import *"
__all__ = [_n for _n in list(globals()) if not _n.startswith("__")]
