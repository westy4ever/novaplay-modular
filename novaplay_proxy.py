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
    from urllib.parse import urlparse, parse_qs, quote, urljoin
except ImportError:  # py2 fallback
    from urlparse import urlparse, parse_qs, urljoin
    from urllib import quote

SAFE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
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


def _proxied(target, referer, ua, cookie):
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
    return "http://127.0.0.1:%d/stream?%s" % (_PROXY_PORT, q)


def _rewrite_manifest(text, base_url, referer, ua, cookie):
    """Route every media URI in an HLS manifest through this proxy."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif s.startswith("#"):
            m = _URI_ATTR_RE.search(s)
            if m:
                s = (s[:m.start()] + 'URI="%s"' % _proxied(
                    urljoin(base_url, m.group(1)), referer, ua, cookie)
                     + s[m.end():])          # [PATCH 65] keep IV=, BYTERANGE=, etc.
            out.append(s)
        else:
            out.append(_proxied(urljoin(base_url, s), referer, ua, cookie))
    return ("\n".join(out) + "\n").encode("utf-8", "replace")


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
        resp = _urlreq.urlopen(req, timeout=8)
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
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query or "")
        if parsed.path == "/stream" and query.get("url"):
            stream_url = (query.get("url", [""])[0] or "").strip()
            referer = (query.get("referer", [""])[0] or "").strip()
            ua = (query.get("ua", [""])[0] or "").strip()
            cookie = (query.get("cookie", [""])[0] or "").strip()
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

            range_hdr = self.headers.get("Range") or self.headers.get("range")
            if range_hdr:
                headers["Range"] = range_hdr

            _log("Proxy: {} {}".format(method, stream_url[:120]))
            _PROXY_LAST_HIT = time.time()
            # [PATCH 87] no per-request reset: the player resets the counters per candidate
            _PROXY_LAST_URL = stream_url
            _bump("requests")

            upstream = _urlreq.Request(stream_url, headers=headers)
            range_start, range_end, emulated_range = None, None, False
            try:
                resp = _urlreq.urlopen(upstream, timeout=30)
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
                        resp = _urlreq.urlopen(_urlreq.Request(stream_url, headers=retry_headers), timeout=30)
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
                body = _rewrite_manifest(
                    text, base,
                    headers.get("Referer", ""),
                    headers.get("User-Agent", ""),
                    headers.get("Cookie", ""))
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
                _PROXY_LAST_BYTES += len(body)
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