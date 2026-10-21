# -*- coding: utf-8 -*-
"""
novaplay_proxy.py — local streaming proxy for NovaPlay Media Center
====================================================================
CONSOLIDATED FILE — contains EVERY amendment from the whole port; it
replaces any earlier partial version. (If plugin.py failed with
"cannot import name 'attach_cookies'", this file is the fix.)

Extracted from plugin.py (LocalProxyHandler / start_proxy) and upgraded
with StreamProxy's playback plumbing:

  1. ThreadingMixIn server — was single-threaded; HLS segment fetches
     now run in parallel and prebuffering doesn't stall.
  2. HLS manifest rewriting — segments AND #EXT-X-KEY / #EXT-X-MAP
     URIs are rewritten to route back through this proxy, carrying the
     same Referer / User-Agent / Cookie as the manifest request.
  3. Decoy-manifest fail-fast — URL/content-type promises a manifest
     but the body isn't #EXTM3U (the .txt/.css disguise trick) →
     immediate 502 instead of the player hanging on garbage.
  4. Cookie forwarding — &cookie= query param (attach_cookies appends
     scraper-earned cf_clearance etc. to resolved stream URLs).
  5. Stream-result cache — 30-min TTL + HEAD liveness probe.
  6. Per-URL extraction locks — concurrent double-resolve protection.
  7. Stats counters — consumed by novaplay_diagnostics.

Changes in this revision (audit fixes):
  * Binds 127.0.0.1, NOT 0.0.0.0. Every consumer (player candidates,
    manifest rewrites, legacy URLs) uses loopback; binding all
    interfaces turned this into a LAN-reachable open relay and an SSRF
    pivot into the box's localhost services (Transmission RPC etc.).
    Same guard philosophy as UltraStalker's _assert_loopback_bind().
  * Log rotation keeps the previous 1MB as .1 instead of deleting the
    whole file (evidence loss made post-mortem debugging guesswork).
  * Manifests larger than the 4MB rewrite budget now fail loudly (502)
    instead of being rewritten truncated — a truncated VOD playlist
    silently stops playback partway with no error.
  * Cache HEAD probe: a transient network error (code None) no longer
    evicts a good entry — only a definitive non-2xx does.
  * Relay write aborts are logged (client-left vs upstream-drop used
    to be indistinguishable from a stall).

URL contract for the player is UNCHANGED:
    http://127.0.0.1:19888/stream?url=<quoted>&referer=<quoted>[&ua=..][&cookie=..]
    http://127.0.0.1:19888/<full-url>|Referer=...&User-Agent=...   (legacy)

Self-contained: stdlib only.
"""

import os
import re
import time
import threading
import http.server
import socketserver
import urllib.request as _urlreq
import urllib.error

try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CURL_CFFI = True
except Exception:
    _HAS_CURL_CFFI = False

try:
    from urllib.parse import urlparse, parse_qs, quote, urljoin
except ImportError:  # py2 fallback
    from urlparse import urlparse, parse_qs, urljoin
    from urllib import quote

SAFE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
_PROXY_PORT = 19888

# Live playback-status globals — SAME names plugin.py used, so
# `import novaplay_proxy as _np; _np._PROXY_LAST_URL` stays live.
_PROXY_STARTED = False
_PROXY_LAST_HIT = 0
_PROXY_LAST_BYTES = 0
_PROXY_LAST_URL = ""


def get_last_hit():
    return _PROXY_LAST_HIT


def get_last_bytes():
    return _PROXY_LAST_BYTES


def get_last_url():
    return _PROXY_LAST_URL


# ─── Logging ─────────────────────────────────────────────────────────────────
_LOG_PATH = "/tmp/novaplay_proxy.log"
_LOG_MAX_BYTES = 1 * 1024 * 1024


def _log(msg):
    try:
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > _LOG_MAX_BYTES:
            try:
                old = _LOG_PATH + ".1"
                if os.path.exists(old):
                    os.remove(old)
                os.rename(_LOG_PATH, old)  # keep the last MB as evidence
            except Exception:
                pass
        with open(_LOG_PATH, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), str(msg)))
    except Exception:
        pass


# ─── Stats ───────────────────────────────────────────────────────────────────
_STATS = {"requests": 0, "manifests_rewritten": 0,
          "cache_hits": 0, "upstream_errors": 0}
_STATS_LOCK = threading.Lock()


def _bump(key, n=1):
    try:
        with _STATS_LOCK:
            _STATS[key] = _STATS.get(key, 0) + n
    except Exception:
        pass


def get_stats():
    with _STATS_LOCK:
        return dict(_STATS)


# ─── Manifest rewriting ──────────────────────────────────────────────────────
_M3U8_CTYPE_RE = re.compile(r"mpegurl|m3u8", re.I)
_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')
_SNIFF_URL_RE = re.compile(r"\.(m3u8|txt)(\?|$)", re.I)
_SNIFF_MAX = 4 * 1024 * 1024


def _proxied(target, referer, ua, cookie, xor="", origin="", extra="", ext=None):
    """Build a /stream URL that carries the upstream context forward.
    quote(safe="") is essential: parse_qs decodes once, and encoding
    '=' / '+' / '/' means values survive the round-trip exactly."""
    q = "url=" + quote(target, safe="")
    if referer:
        q += "&referer=" + quote(referer, safe="")
    if ua:
        q += "&ua=" + quote(ua, safe="")
    if cookie:
        q += "&cookie=" + quote(cookie, safe="")
    if origin:
        q += "&origin=" + quote(origin, safe="")
    if xor:
        q += "&xor=" + quote(xor, safe="")
    # [NEW] end the URL with the target's own file extension. ffmpeg (5001/5002)
    # refuses HLS segments whose URL doesn't end in a known media extension
    # ("is not in allowed_segment_extensions"): seen on the box with fMP4 .m4s
    # segments, where every proxied segment URL ended in "...origin=https://vidcore.io".
    if extra:
        q += extra
    ext = _url_ext(target) if ext is None else ext
    if ext:
        q += "&ext=" + ext
    return "http://127.0.0.1:%d/stream?%s" % (_PROXY_PORT, q)


def _url_ext(url):
    try:
        path = urlparse(url).path
    except Exception:
        return ""
    m = re.search(r"\.([A-Za-z0-9]{1,6})$", path)
    return ("x." + m.group(1).lower()) if m else ""


def _rewrite_manifest(text, base_url, referer, ua, cookie, xor="", origin="", headers=None):
    """Route every media URI in an HLS manifest through this proxy.

    [NEW] fMP4 media playlists (#EXT-X-MAP init segment + .m4s pieces, seen on
    VidCore/VidFast's CDN) don't play on the box: GStreamer 1.28's HLS element
    and exteplayer3 both gave up on them. When the box has ffmpeg, each piece
    is served as MPEG-TS instead (init + piece remuxed, no re-encoding) and the
    #EXT-X-MAP line is dropped, which every player on the box handles."""
    remux_init = None
    if not xor and "#EXT-X-MAP" in text and "BYTERANGE" not in text and remux_available():
        for line in text.splitlines():
            if line.strip().startswith("#EXT-X-MAP"):
                m = _URI_ATTR_RE.search(line)
                if m:
                    remux_init = urljoin(base_url, m.group(1))
                break
    # [NEW] MoviesAPI (netrocdn): the playlist announces AES-128 with a real
    # key, but the pieces are plain MP4 files ("ftyp isom" at byte 0). Players
    # decrypt them into garbage. Drop the fake key and serve the pieces as TS.
    plain = None
    if (not xor and not remux_init and headers is not None
            and re.search(r"#EXT-X-KEY:METHOD=AES-128", text)):
        plain = _plain_segment_mode(text, base_url, headers)
    out = []
    t_pos = 0.0
    last_dur = 0.0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif s.startswith("#"):
            if remux_init and s.startswith("#EXT-X-MAP"):
                continue
            if plain and s.startswith("#EXT-X-KEY"):
                continue
            if s.startswith("#EXTINF:"):
                try:
                    last_dur = float(s[8:].split(",", 1)[0])
                except ValueError:
                    last_dur = 0.0
            m = _URI_ATTR_RE.search(s)
            if m:
                s = (s[:m.start()] + 'URI="%s"' % _proxied(
                    urljoin(base_url, m.group(1)), referer, ua, cookie, xor, origin)
                     + s[m.end():])          # [PATCH 65] keep IV=, BYTERANGE=, etc.
            out.append(s)
        elif remux_init:
            out.append(_proxied(urljoin(base_url, s), referer, ua, cookie, xor, origin,
                                extra="&remux=ts&init=" + quote(remux_init, safe=""), ext="x.ts"))
        elif plain == "mp4":
            out.append(_proxied(urljoin(base_url, s), referer, ua, cookie, xor, origin,
                                extra="&remux=mp4&t0=%.3f" % t_pos, ext="x.ts"))
            t_pos += last_dur
        else:
            out.append(_proxied(urljoin(base_url, s), referer, ua, cookie, xor, origin))
            t_pos += last_dur
    return ("\n".join(out) + "\n").encode("utf-8", "replace")


_PLAIN_CACHE = {}


def _ts_start(data):
    """Offset of the first MPEG-TS packet run (0x47 every 188 bytes, 3 in a row),
    or None when the data is not TS."""
    n = len(data)
    i = data.find(b"\x47")
    while 0 <= i and i + 376 < n:
        if data[i + 188] == 0x47 and data[i + 376] == 0x47:
            return i
        i = data.find(b"\x47", i + 1)
    return None


def _plain_segment_mode(text, base_url, headers):
    """Look at the first piece of an 'AES-128' playlist: 'mp4' / 'ts' when it is
    in fact unencrypted, None when it really is encrypted (or can't be read)."""
    first = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), None)
    if not first:
        return None
    seg = urljoin(base_url, first)
    key = urlparse(seg).netloc + urlparse(base_url).path
    if key in _PLAIN_CACHE:
        return _PLAIN_CACHE[key]
    mode = None
    try:
        h = dict(headers)
        h["Range"] = "bytes=0-1023"
        h["Accept-Encoding"] = "identity"
        head = _np_urlopen(_urlreq.Request(seg, headers=h), timeout=20).read(1024)
        if head[4:8] in (b"ftyp", b"styp", b"moof", b"moov"):
            mode = "mp4" if remux_available() else None
        elif head[:1] == b"\x47" and (len(head) < 189 or head[188:189] == b"\x47"):
            mode = "ts"
        _log("Proxy: AES-128 playlist, first piece starts {} -> {}".format(
            head[:8].hex(), "unencrypted " + mode if mode else "really encrypted"))
    except Exception as e:
        _log("Proxy: could not check the first piece ({}); leaving the key in".format(e))
        return None
    if len(_PLAIN_CACHE) > 100:
        _PLAIN_CACHE.clear()
    _PLAIN_CACHE[key] = mode
    return mode


# ─── fMP4 -> MPEG-TS remux (ffmpeg on the box) ───────────────────────────────
_FFMPEG = "/usr/bin/ffmpeg"
_REMUX_OK = None
_INIT_CACHE = {}
_INIT_LOCK = threading.Lock()


def remux_available():
    """ffmpeg present with the mp4 demuxer and the mpegts muxer (the box's
    ffmpeg is a cut-down build: it has no 'null' muxer, for one)."""
    global _REMUX_OK
    if _REMUX_OK is None:
        ok = False
        try:
            import subprocess
            if os.path.exists(_FFMPEG):
                mux = subprocess.run([_FFMPEG, "-hide_banner", "-muxers"], capture_output=True, timeout=10).stdout
                dmx = subprocess.run([_FFMPEG, "-hide_banner", "-demuxers"], capture_output=True, timeout=10).stdout
                ok = (re.search(rb"\bmpegts\b", mux) is not None and
                      re.search(rb"\bmov,mp4", dmx) is not None)
        except Exception as e:
            _log("Proxy: ffmpeg check failed: {}".format(e))
        _REMUX_OK = ok
        _log("Proxy: fMP4->TS remux {}".format("available" if ok else "NOT available"))
    return _REMUX_OK


def _fetch_bytes(url, headers, timeout=30):
    h = dict(headers)
    h.pop("Range", None)
    h["Accept-Encoding"] = "identity"
    resp = _np_urlopen(_urlreq.Request(url, headers=h), timeout=timeout)
    return resp.read()


def _init_segment(url, headers):
    with _INIT_LOCK:
        hit = _INIT_CACHE.get(url)
    if hit is not None:
        return hit
    data = _fetch_bytes(url, headers)
    with _INIT_LOCK:
        if len(_INIT_CACHE) > 20:
            _INIT_CACHE.clear()
        _INIT_CACHE[url] = data
    return data


def remux_segment(seg_url, init_url, headers, t0=None):
    """Piece -> MPEG-TS bytes (stream copy).
    With init_url: init + fMP4 fragment, original timestamps (-copyts).
    Without: a self-contained MP4 file, shifted to start at t0 seconds so the
    pieces line up (read from a temp file: its index may sit at the end)."""
    import subprocess
    seg = _fetch_bytes(seg_url, headers)
    if init_url:
        init = _init_segment(init_url, headers)
        cmd = [_FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "mp4", "-i", "pipe:0",
               "-map", "0", "-c", "copy", "-copyts", "-muxdelay", "0", "-muxpreload", "0",
               "-f", "mpegts", "pipe:1"]
        p = subprocess.run(cmd, input=init + seg, capture_output=True, timeout=40)
    else:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="np_seg_", suffix=".mp4", dir="/tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(seg)
            cmd = [_FFMPEG, "-hide_banner", "-loglevel", "error", "-i", tmp,
                   "-map", "0", "-c", "copy", "-muxdelay", "0", "-muxpreload", "0"]
            if t0:
                cmd += ["-output_ts_offset", "%.3f" % float(t0)]
            cmd += ["-f", "mpegts", "pipe:1"]
            p = subprocess.run(cmd, capture_output=True, timeout=40)
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("ffmpeg remux failed ({}): {}".format(
            p.returncode, (p.stderr or b"")[-300:].decode("utf-8", "replace")))
    return p.stdout


# ─── XOR-obfuscated playlists (EmbedMaster obfHosts; see extractors/hlsobf.py) ─
# A stream whose pipe headers carry X-NP-Xor=<hex key> has its playlists
# served as "<name>.txt" XORed with that key. The proxy fetches the .txt,
# decodes it and serves a normal playlist whose URIs come back through here
# (still carrying the key, so nested playlists are decoded too).
_XOR_M3U8_RE = re.compile(r"\.m3u8(\?|#|$)", re.I)


# ─── Playlist cache ──────────────────────────────────────────────────────────
# [NEW 2026-10-17] Seen on the box: a CDN took 46 s to answer a playlist; the
# player had given up after 12 s and every new attempt started the slow fetch
# again. Finished playlists are kept for 2 minutes, so the next attempt (or a
# late answer to an abandoned one) is served instantly. Resolvers that already
# downloaded a playlist (EmbedMaster's check) hand it over via
# remember_playlist(), so a short-lived link is fetched only once.
_PL_CACHE = {}
_PL_LOCK = threading.Lock()
_PL_TTL = 120


def remember_playlist(url, text, base_url=None, keep=None):
    # only playlists that never change: a VOD list (ENDLIST) or a master list of
    # qualities. A live playlist is re-fetched every few seconds and must not be cached.
    if not text or ("#EXT-X-ENDLIST" not in text and "#EXT-X-STREAM-INF" not in text
                    and "#EXT-X-PLAYLIST-TYPE:VOD" not in text):
        return
    try:
        with _PL_LOCK:
            ts = time.time()
            if keep and keep > _PL_TTL:
                ts += keep - _PL_TTL      # keep this one longer than the default 2 min
            _PL_CACHE[url] = (ts, text, base_url or url)
            if len(_PL_CACHE) > 200:
                for k in sorted(_PL_CACHE, key=lambda k: _PL_CACHE[k][0])[:50]:
                    _PL_CACHE.pop(k, None)
    except Exception:
        pass


def _cached_playlist(url):
    with _PL_LOCK:
        hit = _PL_CACHE.get(url)
        if hit and time.time() - hit[0] < _PL_TTL:
            return hit[1], hit[2]
        _PL_CACHE.pop(url, None)
    return None


# Single-flight playlist fetch + background warm-up.
# [NEW 2026-10-17] Box: bx.netrocdn.site took 30 s for a master playlist and
# 75 s for its 467 KB level playlist, while the player fired several identical
# requests at once (each starting its own slow download). One download per URL
# now; others wait for it. prefetch_hls() starts it the moment playback is
# requested, so it runs while the player is still trying its other candidates.
_INFLIGHT = {}
_INFLIGHT_LOCK = threading.Lock()
_DEAD_PL = {}          # playlist url -> time it answered 403/404/410
_DEAD_PL_TTL = 300

# [2026-10-21] a CDN host whose origin is down: Cloudflare 520-527 ("origin
# unreachable/timed out", seen: cdn1004.cdn-tnmr.org 522 on LuluStream) or a
# 502/504 gateway error. The player asks dead_host() after a failed attempt
# and skips its remaining ways of playing the same stream instead of spending
# ~90 s on all of them.
_DEAD_HOST_CODES = (502, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530)
_DEAD_HOSTS = {}       # host -> (time, code)
_DEAD_HOST_TTL = 120


def _host_of(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def note_dead_host(url, code):
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        return
    if code in _DEAD_HOST_CODES:
        h = _host_of(url)
        if h:
            if h not in _DEAD_HOSTS:
                _log("Proxy: {} answered HTTP {} (server behind it is down)".format(h, code))
            _DEAD_HOSTS[h] = (time.time(), code)


def dead_host(url):
    """HTTP code if this url's host recently answered as dead, else 0."""
    hit = _DEAD_HOSTS.get(_host_of(url))
    if hit and time.time() - hit[0] < _DEAD_HOST_TTL:
        return hit[1]
    return 0


_LAST_PL_DONE = [0.0]   # when a playlist download last finished successfully


def playlist_fetch_in_progress(recent=30):
    """True while a playlist download runs, or if one finished in the last `recent`
    seconds (box: the player was stopped 1 s after an 87 s download completed)."""
    with _INFLIGHT_LOCK:
        if _INFLIGHT:
            return True
    return time.time() - _LAST_PL_DONE[0] < recent


def wait_inflight(url, timeout=120):
    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.get(url)
    if ev is None:
        return False
    _log("Proxy: waiting for the playlist download already running: {}".format(url[:90]))
    ev.wait(timeout)
    return True


def fetch_playlist_text(url, headers, xor_key="", timeout=90):
    """(text, base_url) of an HLS playlist, from cache, from a download already
    running, or downloaded now. None on failure."""
    hit = _cached_playlist(url)
    if hit:
        return hit
    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.get(url)
        mine = ev is None
        if mine:
            ev = _INFLIGHT[url] = threading.Event()
    if not mine:
        ev.wait(timeout)
        return _cached_playlist(url)
    try:
        fetch_url = _XOR_M3U8_RE.sub(r".txt\1", url, count=1) if xor_key else url
        h = dict(headers)
        h.pop("Range", None)
        h.setdefault("Accept", "*/*")
        h["Accept-Encoding"] = "identity"
        t0 = time.time()
        resp = _np_urlopen(_urlreq.Request(fetch_url, headers=h), timeout=timeout)
        raw = resp.read(_SNIFF_MAX)
        base = url if xor_key else (resp.geturl() or url)
        if xor_key:
            raw = _xor_bytes(raw, xor_key)
        text = raw.decode("utf-8", "ignore")
        if not text.lstrip().startswith("#EXTM3U"):
            _log("Proxy: not a playlist ({}): {}".format("XOR key changed?" if xor_key else "decoy", fetch_url[:90]))
            return None
        _log("Proxy: downloaded playlist in {:.1f}s ({} bytes): {}".format(time.time() - t0, len(raw), url[:90]))
        _LAST_PL_DONE[0] = time.time()
        if "#EXT-X-ENDLIST" in text or "#EXT-X-STREAM-INF" in text:
            remember_playlist(url, text, base)
        else:
            # live playlist: hand it to waiters once, but don't keep it
            with _PL_LOCK:
                _PL_CACHE[url] = (time.time() - _PL_TTL + 5, text, base)
        return text, base
    except Exception as e:
        if getattr(e, "code", None) in (403, 404, 410):
            _DEAD_PL[url] = time.time()
        note_dead_host(url, getattr(e, "code", None))
        _log("Proxy: playlist download failed: {} ({})".format(e, url[:90]))
        return None
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(url, None)
        ev.set()


def _drop_dead_variants(text, base):
    """[NEW 2026-10-17] Box: EmbedMaster listed two qualities, one of which answered
    404 even straight away; the box's player picked it and failed instead of using
    the working one. Leave out qualities known to be gone (if any remain)."""
    if "#EXT-X-STREAM-INF" not in text or not _DEAD_PL:
        return text
    now = time.time()
    lines = text.splitlines()
    out, pending, kept, dropped = [], None, 0, 0
    for line in lines:
        s = line.strip()
        if s.startswith("#EXT-X-STREAM-INF"):
            pending = line
            continue
        if pending is not None and s and not s.startswith("#"):
            u = urljoin(base, s)
            if now - _DEAD_PL.get(u, 0) < _DEAD_PL_TTL:
                dropped += 1
            else:
                out.extend([pending, line])
                kept += 1
            pending = None
            continue
        out.append(line)
    if not dropped or not kept:
        return text
    _log("Proxy: left out {} dead quality level(s), {} kept".format(dropped, kept))
    return "\n".join(out) + "\n"


def _variant_urls(text, base):
    out, want = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#EXT-X-STREAM-INF"):
            want = True
        elif s and not s.startswith("#") and want:
            out.append(urljoin(base, s))
            want = False
    return out


def _rendition_urls(text, base):
    """Playlists listed as #EXT-X-MEDIA URI="..." (separate audio / subtitle tracks).
    [FIX 2026-10-17] Box: EmbedMaster's master had one video level plus a separate
    audio playlist; only the video level was prefetched, the audio link expired
    (404) and the player could not start."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#EXT-X-MEDIA") and "URI=" in s:
            m = _URI_ATTR_RE.search(s)
            if m:
                out.append(urljoin(base, m.group(1)))
    return out


def prefetch_hls(url, headers, xor_key="", wait=False, max_variants=3):
    """Download the master playlist, its first level playlists and every
    separate audio/subtitle playlist into the cache."""
    def run():
        got = fetch_playlist_text(url, headers, xor_key)
        if got:
            kids = _variant_urls(got[0], got[1])[:max_variants] + _rendition_urls(got[0], got[1])
            # in parallel: on a slow CDN each one takes ~80 s, and the player may
            # switch quality right after starting (box: MoviesAPI h -> x)
            ths = []
            for v in kids:
                t = threading.Thread(target=fetch_playlist_text, args=(v, headers, xor_key),
                                     name="np-prefetch-level")
                t.daemon = True
                t.start()
                ths.append(t)
            for t in ths:
                t.join(150)
    if wait:
        run()
    else:
        t = threading.Thread(target=run, name="np-prefetch")
        t.daemon = True
        t.start()


# curl_cffi: "chrome" maps to the newest Chrome the Python side knows, which the
# box's libcurl-impersonate may not have ("Impersonating chrome146 is not
# supported"). Walk down to a supported target once, then stick with it.
_CFFI_TARGETS = ["chrome146", "chrome145", "chrome142", "chrome136", "chrome131", "chrome124",
                 "chrome120", "chrome110", "chrome101", "chrome", None]
_cffi_target_idx = [0]
_cffi_target_lock = threading.Lock()


# [2026-10-21] video hosts with an incomplete certificate chain (downet.net,
# Akwam's servers: "unable to get local issuer certificate") play in browsers,
# which fetch the missing certificate themselves. Like extractors/net.py, the
# proxy doesn't verify certificates.
import ssl as _ssl
_NP_SSL = _ssl._create_unverified_context()


def _np_urlopen(req, timeout=None, **kw):
    kw.setdefault("context", _NP_SSL)
    return _urlreq.urlopen(req, timeout=timeout, **kw)


def _cffi_get(url, headers, **kw):
    kw.setdefault("verify", False)
    # [2026-10-21] inside Enigma2 impersonation fails in-process (system
    # libcurl loaded first) -- use NovaPlay's curl_cffi helper process
    try:
        import novaplay_cffi_helper as _h
        if _h.needed():
            return _h.client().get(url, headers=headers, impersonate=_CFFI_TARGETS[0], **kw)
    except ImportError:
        pass
    while True:
        with _cffi_target_lock:
            idx = _cffi_target_idx[0]
        if idx >= len(_CFFI_TARGETS):
            raise RuntimeError("no usable curl_cffi impersonation target")
        target = _CFFI_TARGETS[idx]
        try:
            if target:
                return _cffi_requests.get(url, headers=headers, impersonate=target, **kw)
            return _cffi_requests.get(url, headers=headers, **kw)
        except Exception as e:
            msg = str(e).lower()
            # only curl-impersonate's own "Impersonating X is not supported"
            # means the target is missing (box 2026-10-21: chrome120 works
            # there, yet every target got dropped on some other error)
            if target and "impersonating" in msg and "not supported" in msg:
                with _cffi_target_lock:
                    if _cffi_target_idx[0] == idx:
                        _cffi_target_idx[0] = idx + 1
                _log("Proxy: curl_cffi target {} unsupported, trying the next one".format(target))
                continue
            _log("Proxy: curl_cffi ({}) error: {}".format(target or "plain", str(e)[:200]))
            raise


def _xor_bytes(data, key_hex):
    key = bytes.fromhex(key_hex)
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(bytearray(data))) if key else bytes(data)


# ─── Stream-result cache (StreamProxy cache_manager pattern) ─────────────────
_STREAM_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 1800  # 30 min — the lifetime StreamProxy converged on for tokenised links


def _split_piped(url):
    """Split 'url|Referer=...' into (url, referer)."""
    if "|" in str(url):
        u, pipe = str(url).split("|", 1)
        for part in pipe.split("&"):
            if part.lower().startswith("referer="):
                return u.strip(), part.split("=", 1)[1]
        return u.strip(), ""
    return str(url), ""


def cache_stream(embed_url, result):
    """Store a resolved extract_stream() result keyed by the embed URL."""
    if not embed_url or not isinstance(result, tuple) or not result or not result[0]:
        return
    try:
        with _CACHE_LOCK:
            _STREAM_CACHE[str(embed_url)] = {"result": result, "ts": time.time()}
    except Exception:
        pass


def get_cached_stream(embed_url):
    """Return a cached result if fresh AND the stream still answers.
    Returns None on any doubt (callers must re-resolve)."""
    key = str(embed_url or "")
    if not key:
        return None
    with _CACHE_LOCK:
        entry = _STREAM_CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] > _CACHE_TTL:
        with _CACHE_LOCK:
            _STREAM_CACHE.pop(key, None)
        return None
    stream = entry["result"][0]
    if not isinstance(stream, str) or not stream.startswith("http"):
        return None
    target, referer = _split_piped(stream)
    code = None
    try:
        req = _urlreq.Request(target, headers={"User-Agent": SAFE_UA,
                                               "Referer": referer or target})
        req.get_method = lambda: "HEAD"
        resp = _np_urlopen(req, timeout=8)
        try:
            code = resp.getcode() or 200
        finally:
            try:
                resp.close()
            except Exception:
                pass
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = None  # network error → don't evict on transient failure
    if code in (405, 501) or code is None:
        # server doesn't answer HEAD (or the probe failed transiently) —
        # don't invalidate a working entry; TTL bounds it anyway.
        _bump("cache_hits")
        return entry["result"]
    if code not in (200, 206):
        with _CACHE_LOCK:
            _STREAM_CACHE.pop(key, None)
        return None
    _bump("cache_hits")
    return entry["result"]


def clear_stream_cache():
    with _CACHE_LOCK:
        _STREAM_CACHE.clear()


# ─── Per-URL extraction locks (StreamProxy _extraction_locks) ────────────────
_EXTRACT_LOCKS = {}
_EXTRACT_LOCKS_GUARD = threading.Lock()


def _get_extract_lock(url):
    with _EXTRACT_LOCKS_GUARD:
        if len(_EXTRACT_LOCKS) > 64:  # bound the dict; stale entries are harmless
            _EXTRACT_LOCKS.clear()
        lock = _EXTRACT_LOCKS.get(url)
        if lock is None:
            lock = threading.Lock()
            _EXTRACT_LOCKS[url] = lock
        return lock


def cached_extract(extractor, url):
    """Wrap extract_stream() — extractor object OR module — with the TTL
    cache and a per-URL lock (concurrent double-resolve of the same
    server blocks on the lock, then hits the cache the winning thread
    filled)."""
    fn = None
    if extractor is not None:
        fn = getattr(extractor, "extract_stream", None)
    if fn is None and callable(extractor):
        fn = extractor
    if fn is None:
        return None
    cached = get_cached_stream(url)
    if cached is not None:
        return cached
    with _get_extract_lock(str(url)):
        cached = get_cached_stream(url)   # re-check: another thread may have won
        if cached is not None:
            return cached
        result = fn(url)
        if isinstance(result, tuple) and result and result[0]:
            cache_stream(url, result)
        return result


def attach_cookies(result):
    """Append |Cookie=... (piped-header format) to a resolved stream URL,
    using the cookies the scraper earned in the CURRENT thread.
    MUST be called from the resolving thread — curl_cffi sessions are
    thread-local, so calling this on the main thread finds nothing."""
    try:
        from extractors.base import get_cookie_header_for_url
    except Exception:
        return result
    try:
        if not isinstance(result, tuple) or not result or not isinstance(result[0], str):
            return result
        stream = result[0]
        if not stream.startswith("http") or "Cookie=" in stream:
            return result
        cookie = get_cookie_header_for_url(stream.split("|", 1)[0])
        if not cookie or "&" in cookie:
            return result  # '&'-containing cookies can't ride in the pipe
        stream += ("&" if "|" in stream else "|") + "Cookie=" + cookie
        return (stream,) + tuple(result[1:])
    except Exception:
        return result


class _CurlCffiResponseAdapter(object):
    def __init__(self, cffi_resp):
        self._resp = cffi_resp
        self._iter = cffi_resp.iter_content(chunk_size=65536)
        self._buf = b""

    def getcode(self):
        return self._resp.status_code

    def info(self):
        return self._resp.headers

    @property
    def headers(self):
        return self._resp.headers

    def geturl(self):
        try:
            return str(self._resp.url)
        except Exception:
            return ""

    def read(self, n=-1):
        if n is None or n < 0:
            chunks = [self._buf]
            self._buf = b""
            for c in self._iter:
                chunks.append(c)
            return b"".join(chunks)
        while len(self._buf) < n:
            try:
                chunk = next(self._iter)
            except StopIteration:
                break
            if chunk:
                self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self):
        try:
            self._resp.close()
        except Exception:
            pass


# ─── HTTP handler ────────────────────────────────────────────────────────────
class LocalProxyHandler(http.server.BaseHTTPRequestHandler):
    # Deliberately left at HTTP/1.0 (default): keep-alive would require a
    # correct Content-Length on every relayed response (including chunked
    # upstreams) or client connections hang.
    timeout = 90  # drop stalled client connections (STB RAM hygiene)

    def do_HEAD(self):
        self._handle("HEAD")

    def do_GET(self):
        self._handle("GET")

    def _parse_request(self):
        """Returns (stream_url, referer, ua, cookie, piped) or None after
        sending an error. Supports both the /stream form and the legacy
        /<url>|Headers form, including a pipe accidentally embedded in
        the url= query value."""
        self._np_xor = self._np_origin = ""   # per request (keep-alive reuses the handler)
        self._np_remux = self._np_init = ""
        self._np_t0 = 0.0
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query or "")
        if parsed.path == "/stream" and query.get("url"):
            stream_url = (query.get("url", [""])[0] or "").strip()
            referer = (query.get("referer", [""])[0] or "").strip()
            ua = (query.get("ua", [""])[0] or "").strip()
            cookie = (query.get("cookie", [""])[0] or "").strip()
            self._np_xor = (query.get("xor", [""])[0] or "").strip()
            self._np_origin = (query.get("origin", [""])[0] or "").strip()
            self._np_remux = (query.get("remux", [""])[0] or "").strip()
            self._np_init = (query.get("init", [""])[0] or "").strip()
            try:
                self._np_t0 = float(query.get("t0", ["0"])[0] or 0)
            except ValueError:
                self._np_t0 = 0.0
            piped = ""
            if "|" in stream_url:
                stream_url, piped = stream_url.split("|", 1)
                stream_url = stream_url.strip()
            return stream_url, referer, ua, cookie, piped
        raw = self.path[1:]
        if not raw or "://" not in raw:
            self._safe_send_error(400, "Bad URL")
            return None
        piped = ""
        if "|" in raw:
            raw, piped = raw.split("|", 1)
            raw = raw.strip()
        return raw.strip(), "", "", "", piped

    def _handle(self, method):
        global _PROXY_LAST_HIT, _PROXY_LAST_BYTES, _PROXY_LAST_URL
        try:
            req_info = self._parse_request()
            if req_info is None:
                return
            stream_url, referer, ua, cookie, piped = req_info
            xor_key = getattr(self, "_np_xor", "")

            # identity encoding: we must be able to sniff/rewrite bodies
            # without decompressing, and the player can't either.
            headers = {"User-Agent": ua or SAFE_UA,
                       "Accept-Encoding": "identity"}
            if piped:
                for part in piped.split("&"):
                    if "=" not in part:
                        continue
                    k, v = part.split("=", 1)
                    k = k.strip()
                    if not k:
                        continue
                    if k.lower() == "cookie":
                        cookie = v.strip()
                    elif k.lower() == "x-np-xor":
                        xor_key = v.strip()
                    else:
                        headers[k] = v.strip()
            if referer:
                headers["Referer"] = referer
            elif "Referer" not in headers:
                try:
                    parts = stream_url.split("/")
                    headers["Referer"] = parts[0] + "//" + parts[2] + "/"
                except Exception:
                    pass
            if cookie:
                headers["Cookie"] = cookie
            if getattr(self, "_np_origin", "") and "Origin" not in headers:
                headers["Origin"] = self._np_origin
            if xor_key and _XOR_M3U8_RE.search(stream_url):
                self._serve_xor_manifest(method, stream_url, headers, xor_key)
                return
            if getattr(self, "_np_remux", "") == "ts" and getattr(self, "_np_init", ""):
                self._serve_remuxed(method, stream_url, self._np_init, headers)
                return
            if getattr(self, "_np_remux", "") == "mp4":
                self._serve_remuxed(method, stream_url, "", headers, getattr(self, "_np_t0", None))
                return
            if method in ("GET", "HEAD"):
                if wait_inflight(stream_url):
                    pass   # the shared download finished (or timed out); cache checked next
                hit = _cached_playlist(stream_url)
                if hit:
                    _log("Proxy: playlist from cache {}".format(stream_url[:100]))
                    self._send_manifest(method, hit[0], hit[1], headers, "")
                    return

            range_hdr = self.headers.get("Range") or self.headers.get("range")
            # [NEW] playlists are small and some CDNs 403 any ranged playlist request
            # (moon.zenoak.top: "bytes=0-" / "bytes=552-" from the box's players)
            if range_hdr and _XOR_M3U8_RE.search(stream_url):
                range_hdr = None
            if range_hdr:
                headers["Range"] = range_hdr

            cffi_headers = dict(headers)
            # the box's libcurl can't decode br/zstd ("curl: (61) Unrecognized content encoding")
            cffi_headers["Accept-Encoding"] = "gzip, deflate"
            cffi_headers.setdefault("Accept", "*/*")
            # [FIX] confirmed live: s1.egybestvid.com (and likely other
            # Arabic-audience CDN edges) gate on Accept-Language actually
            # containing "ar" -- a plain "en-US,en;q=0.9" default (what a
            # generic scraper/proxy sends) gets a flat nginx 403 on the
            # manifest fetch even with a correct Referer and Origin, while
            # the exact same request with "ar,en-US,en;q=0.9" succeeds.
            # Confirmed via isolated testing: Accept value itself (*/* vs
            # text/html) and Referer exactness (bare domain vs full embed
            # URL) made no difference -- this header value alone did.
            cffi_headers.setdefault("Accept-Language", "ar,en-US,en;q=0.9")
            cffi_headers.setdefault("Sec-Fetch-Dest", "empty")
            cffi_headers.setdefault("Sec-Fetch-Mode", "cors")
            cffi_headers.setdefault("Sec-Fetch-Site", "same-site")
            cffi_headers.setdefault("Sec-GPC", "1")
            if "Origin" not in cffi_headers and headers.get("Referer"):
                try:
                    _oparts = headers["Referer"].split("/")
                    if len(_oparts) >= 3:
                        cffi_headers["Origin"] = _oparts[0] + "//" + _oparts[2]
                except Exception:
                    pass

            _log("Proxy: {} {}{}".format(method, stream_url[:120],
                                        "  [Range {}]".format(range_hdr) if range_hdr else ""))
            _PROXY_LAST_HIT = time.time()
            # [PATCH 87] no per-request reset: the player resets the counters per candidate
            _PROXY_LAST_URL = stream_url
            _bump("requests")

            upstream = _urlreq.Request(stream_url, headers=headers)
            range_start, range_end, emulated_range = None, None, False
            resp = None

            # [FIX] see the curl_cffi import note above -- try the
            # fingerprint-safe client first. It doesn't raise on a non-2xx
            # status (just returns it), so the [PATCH 105] Range-403-retry
            # logic below is re-checked explicitly here rather than via
            # exception handling, but does the exact same thing.
            if _HAS_CURL_CFFI:
                try:
                    cffi_resp = _cffi_get(
                        stream_url, cffi_headers,
                        timeout=30, stream=True, allow_redirects=True,
                    )
                    note_dead_host(stream_url, cffi_resp.status_code)
                    m = re.match(r"bytes=(\d+)-(\d*)", (range_hdr or "").strip()) if range_hdr else None
                    if cffi_resp.status_code == 403 and m:
                        range_start = int(m.group(1))
                        range_end = int(m.group(2)) if m.group(2) else None
                        retry_headers = dict(cffi_headers)
                        retry_headers.pop("Range", None)
                        _log("Proxy: {} 403'd a Range request (curl_cffi), retrying without Range ({})".format(
                            stream_url[:90], range_hdr))
                        try:
                            cffi_resp.close()
                        except Exception:
                            pass
                        try:
                            cffi_resp = _cffi_get(
                                stream_url, retry_headers,
                                timeout=30, stream=True, allow_redirects=True,
                            )
                            emulated_range = True
                        except Exception as e:
                            _bump("upstream_errors")
                            _log("Proxy: curl_cffi range-stripped retry failed: {}".format(e))
                            self._safe_send_error(502, str(e))
                            return
                    resp = _CurlCffiResponseAdapter(cffi_resp)
                except Exception as e:
                    _log("Proxy: curl_cffi upstream failed, falling back to urllib: {}".format(e))
                    resp = None
                    range_start, range_end, emulated_range = None, None, False

            if resp is None:
                try:
                    resp = _np_urlopen(upstream, timeout=30)
                except urllib.error.HTTPError as http_err:
                    # [PATCH 105] some CDNs 403 any Range request while a plain GET works fine
                    # (confirmed on mxcontent.net). Retry once without Range and emulate the
                    # requested slice locally instead of giving up on this candidate.
                    m = re.match(r"bytes=(\d+)-(\d*)", (range_hdr or "").strip()) if range_hdr else None
                    if http_err.code == 403 and m:
                        range_start = int(m.group(1))
                        range_end = int(m.group(2)) if m.group(2) else None
                        retry_headers = dict(headers)
                        retry_headers.pop("Range", None)
                        _log("Proxy: {} 403'd a Range request, retrying without Range ({})".format(
                            stream_url[:90], range_hdr))
                        try:
                            resp = _np_urlopen(_urlreq.Request(stream_url, headers=retry_headers), timeout=30)
                            emulated_range = True
                        except urllib.error.HTTPError as http_err2:
                            resp = http_err2
                            _bump("upstream_errors")
                            _log("Proxy: range-stripped retry also failed: HTTP {}".format(
                                getattr(http_err2, "code", "?")))
                        except Exception as e:
                            _bump("upstream_errors")
                            _log("Proxy: range-stripped retry failed: {}".format(e))
                            self._safe_send_error(502, str(e))
                            return
                    else:
                        resp = http_err
                        _bump("upstream_errors")
                        note_dead_host(stream_url, getattr(http_err, "code", None))
                        _log("Proxy: upstream HTTP {} for {}".format(
                            getattr(http_err, "code", "?"), stream_url[:100]))
                except Exception as e:
                    _bump("upstream_errors")
                    _log("Proxy: upstream connection error: {}".format(e))
                    self._safe_send_error(502, str(e))
                    return

            try:
                self._relay(method, resp, stream_url, headers,
                            bool(range_hdr) and not emulated_range,
                            range_start=range_start if emulated_range else None,
                            range_end=range_end if emulated_range else None)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass

        except Exception as e:
            _log("Proxy FATAL: {}".format(e))
            self._safe_send_error(500, str(e))

    def _relay(self, method, resp, stream_url, headers, has_range, range_start=None, range_end=None):
        global _PROXY_LAST_BYTES
        try:
            status = resp.getcode() or 200
        except Exception:
            status = 200
        resp_hdrs = {}
        try:
            info = getattr(resp, "headers", None)
            if info is None:
                info = resp.info()
            for k, v in info.items():
                resp_hdrs[k.lower()] = v
        except Exception:
            pass

        ctype = (resp_hdrs.get("content-type") or "").lower()
        is_manifest = bool(_M3U8_CTYPE_RE.search(ctype))

        body = None          # rewritten manifest bytes (complete response)
        pending_head = b""   # already-read bytes of a non-manifest body
        if (method == "GET" and not has_range
                and (is_manifest or _SNIFF_URL_RE.search(stream_url))):
            try:
                pending_head = resp.read(512)
            except Exception:
                pending_head = b""
            if pending_head.lstrip().startswith(b"#EXTM3U"):
                try:
                    rest = resp.read(_SNIFF_MAX)
                    if rest and len(rest) == _SNIFF_MAX and resp.read(1):
                        # Manifest exceeds the rewrite budget — relaying it
                        # truncated would silently cut playback mid-stream.
                        # Fail loudly; the player falls to the next candidate.
                        _log("Proxy: manifest too large to rewrite: {}".format(stream_url[:90]))
                        self._safe_send_error(502, "Manifest exceeds rewrite limit")
                        return
                except Exception:
                    rest = b""
                text = (pending_head + rest).decode("utf-8", "ignore")
                try:
                    base = resp.geturl() or stream_url
                except Exception:
                    base = stream_url
                remember_playlist(stream_url, text, base)
                body = _rewrite_manifest(
                    text, base,
                    headers.get("Referer", ""),
                    headers.get("User-Agent", ""),
                    headers.get("Cookie", ""),
                    "", headers.get("Origin", ""), headers=headers)
                pending_head = b""
                _bump("manifests_rewritten")
                _log("Proxy: rewrote manifest {} ({} bytes out)".format(
                    stream_url[:90], len(body)))
            elif pending_head:
                # Decoy detection (StreamProxy): the URL/content-type
                # promised an HLS manifest but the body is something else
                # (disguised HTML/asset — the .txt/.css trick). Fail fast
                # so the player errors immediately instead of hanging.
                _bump("upstream_errors")
                _log("Proxy: decoy manifest (not #EXTM3U) for {}".format(
                    stream_url[:90]))
                self._safe_send_error(502, "Not an HLS manifest")
                return

        if body is not None:
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "none")
            self.end_headers()
            if method == "GET":
                self.wfile.write(body)
                # playlist text is not playback: only video data counts towards
                # the player's "confirmed by traffic" check (a 268 KB playlist
                # once 'confirmed' a stream that never played a frame)
            return

        # [PATCH 105] emulated Range: the upstream response is a full 200 body starting
        # at byte 0 (Range was stripped after a 403); skip to range_start locally and
        # re-frame the response as a proper 206 with a correct Content-Range, so the
        # player still sees a valid ranged response.
        if range_start is not None:
            try:
                total = int(resp_hdrs.get("content-length", "0") or "0")
            except (TypeError, ValueError):
                total = 0
            end = range_end if (range_end is not None and (not total or range_end < total)) else (total - 1 if total else None)
            remaining_to_skip = range_start
            try:
                while remaining_to_skip > 0:
                    chunk = resp.read(min(65536, remaining_to_skip))
                    if not chunk:
                        break
                    remaining_to_skip -= len(chunk)
            except Exception as _e:
                _log("Proxy: range emulation skip failed: {}".format(_e))
                self._safe_send_error(502, "Range emulation failed")
                return
            self.send_response(206)
            for key in ("content-type", "last-modified", "etag"):
                if key in resp_hdrs:
                    self.send_header(key.title(), resp_hdrs[key])
            self.send_header("Accept-Ranges", "bytes")
            if total:
                self.send_header("Content-Range", "bytes {}-{}/{}".format(
                    range_start, end if end is not None else total - 1, total))
                self.send_header("Content-Length", str((end - range_start + 1) if end is not None else (total - range_start)))
            self.end_headers()
            if method == "HEAD":
                return
            sent = 0
            budget = (end - range_start + 1) if end is not None else None
            try:
                while budget is None or sent < budget:
                    want = 65536 if budget is None else min(65536, budget - sent)
                    chunk = resp.read(want)
                    if not chunk:
                        break
                    sent += len(chunk)
                    _PROXY_LAST_BYTES += len(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception as _e:
                try:
                    _log("Proxy: relay aborted: {}".format(_e))
                except Exception:
                    pass
            return

        # [2026-10-19] video pieces disguised as pictures (VidBolt's proxy sends
        # its TS segments as image/jpeg, some CDNs put a real PNG/JPEG header in
        # front): drop everything before the first run of TS sync bytes.
        # [2026-10-21] VidAPI's CDN labels its pieces "page-N.html" / text/html
        if (method == "GET" and not has_range and status == 200
                and (ctype.startswith("image/") or ctype.startswith("text/html"))):
            try:
                head = pending_head + resp.read(max(0, 65536 - len(pending_head)))
            except Exception:
                head = pending_head
            cut = _ts_start(head)
            if cut is not None:
                if cut:
                    _log("Proxy: dropped a {}-byte {} header in front of TS data: {}".format(
                        cut, ctype, stream_url[:80]))
                pending_head = head[cut:]
                try:
                    total = int(resp_hdrs.get("content-length") or 0)
                except (TypeError, ValueError):
                    total = 0
                resp_hdrs.pop("content-length", None)
                resp_hdrs["content-type"] = "video/mp2t"
                if total > cut:
                    resp_hdrs["content-length"] = str(total - cut)
            else:
                pending_head = head

        self.send_response(status)
        for key in ("content-type", "content-length", "content-range",
                    "accept-ranges", "last-modified", "etag"):
            if key in resp_hdrs:
                self.send_header(key.title(), resp_hdrs[key])
        if "accept-ranges" not in resp_hdrs:
            self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        if method == "HEAD":
            return
        try:
            if pending_head:
                self.wfile.write(pending_head)
                _PROXY_LAST_BYTES += len(pending_head)
                self.wfile.flush()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                _PROXY_LAST_BYTES += len(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception as _e:
            # client left / upstream dropped — log it; a silent pass made
            # stall debugging guesswork.
            try:
                _log("Proxy: relay aborted: {}".format(_e))
            except Exception:
                pass

    def _serve_remuxed(self, method, seg_url, init_url, headers, seg_t0=None):
        global _PROXY_LAST_HIT, _PROXY_LAST_BYTES, _PROXY_LAST_URL
        _PROXY_LAST_HIT = time.time()
        _PROXY_LAST_URL = seg_url
        _bump("requests")
        t0 = time.time()
        try:
            data = remux_segment(seg_url, init_url, headers, seg_t0)
        except Exception as e:
            _bump("upstream_errors")
            _log("Proxy: remux failed for {}: {}".format(seg_url[:90], e))
            self._safe_send_error(502, "remux failed")
            return
        _log("Proxy: remuxed {} -> {} bytes TS in {:.1f}s".format(seg_url[-40:], len(data), time.time() - t0))
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        if method == "HEAD":
            return
        try:
            self.wfile.write(data)
            _PROXY_LAST_BYTES += len(data)
        except Exception as e:
            _log("Proxy: relay aborted: {}".format(e))

    def _serve_xor_manifest(self, method, stream_url, headers, xor_key):
        global _PROXY_LAST_BYTES
        hit = _cached_playlist(stream_url)
        if hit:
            _log("Proxy: XOR playlist from cache {}".format(stream_url[:100]))
            self._send_manifest(method, hit[0], hit[1], headers, xor_key)
            return
        _log("Proxy: XOR playlist {}".format(stream_url[:120]))
        _bump("requests")
        got = fetch_playlist_text(stream_url, headers, xor_key, timeout=30)
        if not got:
            _bump("upstream_errors")
            self._safe_send_error(502, "XOR playlist unavailable")
            return
        self._send_manifest(method, got[0], got[1], headers, xor_key)

    def _send_manifest(self, method, text, base_url, headers, xor_key):
        global _PROXY_LAST_BYTES
        text = _drop_dead_variants(text, base_url)
        body = _rewrite_manifest(text, base_url, headers.get("Referer", ""),
                                 headers.get("User-Agent", ""), headers.get("Cookie", ""),
                                 xor_key, headers.get("Origin", ""), headers=headers)
        _bump("manifests_rewritten")
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        if method == "GET":
            self.wfile.write(body)   # playlist: not counted as playback traffic

    def _safe_send_error(self, code, msg=""):
        try:
            self.send_error(code, msg)
        except Exception:
            pass

    def log_message(self, *args, **kwargs):
        pass


class ThreadingProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_proxy():
    global _PROXY_STARTED
    if _PROXY_STARTED:
        return True
    try:
        # Loopback-only: every consumer (player candidates, manifest
        # rewrites, legacy URLs) uses 127.0.0.1. Binding 0.0.0.0 turned
        # this into a LAN-reachable open relay / SSRF pivot into the
        # box's localhost services (Transmission RPC etc.).
        server = ThreadingProxyServer(("127.0.0.1", _PROXY_PORT), LocalProxyHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _PROXY_STARTED = True
        _log("LocalProxy Shield: ACTIVE (threaded, loopback, port {})".format(_PROXY_PORT))
        return True
    except Exception as e:
        _log("start_proxy failure: {}".format(e))
        return False