# -*- coding: utf-8 -*-
"""Network transport: fetch with retries, curl_cffi CF-bypass, browser
proxy fallback, cookie plumbing. TEXT ONLY — binary payloads must never
route through fetch() (it decodes bodies). Referer defaults come from
referers.py — the single table.

Audit fixes baked in:
  * Bug B: a Cloudflare challenge page with no proxy configured now
    returns (None, url) instead of falling through and being returned
    as if it were content.
  * Bug C: clear_cookies() also resets the thread-local curl_cffi
    session's cookies (the poisoned CF cookies lived there).
  * cf_domains trimmed: themoviedb removed (TMDB needs no Chrome
    impersonation — the plain path is faster and lighter).
  * log() rotates to .1 instead of deleting (evidence survives).
"""

import os
import re
import time
import gzip
import zlib
import io
import ssl
import json
import threading
import http.cookiejar as cookiejar

from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor, HTTPSHandler
from urllib.parse import urljoin, urlparse, quote_plus, urlencode
from urllib.error import URLError, HTTPError

from .referers import get_referer

# ─── brotli imports (try both common packages) ──────────────────────────────
try:
    import brotli
except ImportError:
    try:
        import brotlicffi as brotli
    except ImportError:
        brotli = None

# ─── curl_cffi fallback (Cloudflare bypass) ─────────────────────────────────
try:
    from curl_cffi import requests as _curl_requests
    _CURL_CFFI_OK = True
except Exception:
    _CURL_CFFI_OK = False
    print("curl_cffi import failed — Cloudflare bypass unavailable")

# ─── External browser proxy integration ─────────────────────────────────────
_BROWSER_PROXY_URL = None


def set_browser_proxy(url):
    global _BROWSER_PROXY_URL
    _BROWSER_PROXY_URL = (url or "").strip()
    log("Browser proxy set to: {}".format(_BROWSER_PROXY_URL or "DISABLED"))

# ─── UI feedback flags ──────────────────────────────────────────────────────
_PROXY_USED = False
_CURL_FAILED_NEEDS_PROXY = False


def set_proxy_used(val=True):
    global _PROXY_USED
    _PROXY_USED = val


def get_proxy_used():
    return _PROXY_USED


def set_curl_failed_needs_proxy(val=True):
    global _CURL_FAILED_NEEDS_PROXY
    _CURL_FAILED_NEEDS_PROXY = val


def get_curl_failed_needs_proxy():
    return _CURL_FAILED_NEEDS_PROXY


UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
TIMEOUT = 30
ACCEPT_ENCODING = "gzip, deflate, br" if brotli is not None else "gzip, deflate"

_opener = None
_cookiejar = None

# ─── curl_cffi session (shared, thread-local) ───────────────────────────────
_curl_session_local = threading.local()

# ─── Log ────────────────────────────────────────────────────────────────────
_LOG_PATH = "/tmp/novaplay.log"
_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB cap


def log(msg):
    try:
        # Rotate (keep the previous 2MB as .1) instead of deleting —
        # destroying the whole log on rotation lost the evidence that
        # post-mortem debugging depends on.
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > _LOG_MAX_BYTES:
            try:
                old = _LOG_PATH + ".1"
                if os.path.exists(old):
                    os.remove(old)
                os.rename(_LOG_PATH, old)
            except Exception:
                pass

        # Mask API keys in logs for security
        safe_msg = re.sub(r'api_key=[^&]+', 'api_key=****', str(msg))

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_PATH, "a") as f:
            f.write("[{}] {}\n".format(ts, safe_msg))
    except Exception:
        pass


# ─── Blocked-address avoidance (process-wide) ────────────────────────────────
# [NEW 2026-10-17] Measured on the box: perfectbussines.website resolves to
# two Cloudflare IPv4 addresses; 104.21.87.149 connects in 0.0 s while
# 172.67.144.27 is blocked on that line and hangs until the timeout. Python
# tries addresses in DNS order with the FULL timeout each, so every request
# that drew the blocked one first waited 8-30 s (or failed). For hosts with
# several addresses, each one now gets a short connect timeout, and an
# address that times out or is unreachable is skipped for 10 minutes.
# Patching socket.create_connection covers urllib (fetch, the local proxy,
# run_site_test) in this process; curl_cffi/libcurl does its own fallback.
import socket as _socket
import errno as _errno

_DEAD_ADDR_TTL = 600
_PER_ADDR_CONNECT = 4.0
_dead_addrs = {}
_dead_lock = threading.Lock()
_orig_create_connection = _socket.create_connection


def _addr_dead(ip):
    with _dead_lock:
        until = _dead_addrs.get(ip)
        if until and until < time.time():
            _dead_addrs.pop(ip, None)
            return False
        return bool(until)


def _mark_addr_dead(ip, why):
    with _dead_lock:
        fresh = ip not in _dead_addrs
        _dead_addrs[ip] = time.time() + _DEAD_ADDR_TTL
    if fresh:
        try:
            log("Address {} unreachable ({}); skipping it for {} min".format(ip, why, _DEAD_ADDR_TTL // 60))
        except Exception:
            pass


def _np_create_connection(address, timeout=_socket._GLOBAL_DEFAULT_TIMEOUT,
                          source_address=None, *args, **kwargs):
    host, port = address[0], address[1]
    try:
        infos = _socket.getaddrinfo(host, port, 0, _socket.SOCK_STREAM)
    except Exception:
        return _orig_create_connection(address, timeout, source_address, *args, **kwargs)
    if len(infos) <= 1:
        return _orig_create_connection(address, timeout, source_address, *args, **kwargs)
    live = [i for i in infos if not _addr_dead(i[4][0])]
    order = live + [i for i in infos if i not in live]   # dead ones only as a last resort
    has_timeout = timeout is not _socket._GLOBAL_DEFAULT_TIMEOUT and timeout is not None
    err = None
    for n, (fam, typ, proto, _, sa) in enumerate(order):
        last = n == len(order) - 1
        sock = None
        try:
            sock = _socket.socket(fam, typ, proto)
            if last:
                if has_timeout:
                    sock.settimeout(timeout)
            else:
                sock.settimeout(min(_PER_ADDR_CONNECT, timeout) if has_timeout else _PER_ADDR_CONNECT)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            if has_timeout:
                sock.settimeout(timeout)
            else:
                sock.settimeout(_socket.getdefaulttimeout())
            return sock
        except OSError as e:
            err = e
            if sock is not None:
                sock.close()
            if isinstance(e, _socket.timeout) or getattr(e, "errno", None) in (
                    _errno.ENETUNREACH, _errno.EHOSTUNREACH):
                _mark_addr_dead(sa[0], "timed out" if isinstance(e, _socket.timeout) else e.strerror)
    raise err if err else OSError("no address for {}".format(host))


if getattr(_socket.create_connection, "__name__", "") != "_np_create_connection":
    _socket.create_connection = _np_create_connection


def _get_curl_session():
    if not _CURL_CFFI_OK:
        return None
    session = getattr(_curl_session_local, "session", None)
    if session is None:
        session = _curl_requests.Session()
        _curl_session_local.session = session
    return session


# [FIX] curl_cffi's set of valid `impersonate` target strings changes
# between library versions ("chrome120" exists on some releases, not
# others) -- a real device log showed impersonate="chrome120" failing
# with "Impersonating chrome120 is not supported" on 100% of attempts,
# for every single fetch() call across an entire session (confirmed:
# ~20 domain probes, each burning all 3 retries on the identical,
# permanently-doomed call before ever reaching the plain urllib
# fallback that actually works -- pure wasted time, since retrying the
# exact same unsupported string can never succeed). Discover a working
# target (or that none work) ONCE per process and cache it here, instead
# of rediscovering "chrome120 is broken" from scratch on every fetch.
# newest first: the box (curl_cffi 0.16.3 + curl-impersonate 2.2.3) lists up to
# chrome146; a target its libcurl lacks fails with "Impersonating X is not
# supported" and the next one is tried (once per process)
_CFFI_IMPERSONATE_CANDIDATES = ("chrome146", "chrome145", "chrome142", "chrome136", "chrome131",
                                "chrome124", "chrome120", "chrome110", "chrome101", "chrome")
_cffi_impersonate_state = {"index": 0}
_cffi_impersonate_lock = threading.Lock()


def _get_cffi_impersonate():
    """Current best-guess impersonate target, or None once every
    candidate has been confirmed unsupported (meaning: call curl_cffi
    without impersonate at all rather than keep failing)."""
    with _cffi_impersonate_lock:
        idx = _cffi_impersonate_state["index"]
        if idx >= len(_CFFI_IMPERSONATE_CANDIDATES):
            return None
        return _CFFI_IMPERSONATE_CANDIDATES[idx]


def _mark_cffi_impersonate_broken(value):
    """Advances past `value` if it's still the current candidate (a
    concurrent thread may have already advanced past it first) and logs
    once -- every subsequent fetch() call in this process reuses the
    result instead of re-discovering the same breakage."""
    with _cffi_impersonate_lock:
        idx = _cffi_impersonate_state["index"]
        if idx < len(_CFFI_IMPERSONATE_CANDIDATES) and _CFFI_IMPERSONATE_CANDIDATES[idx] == value:
            _cffi_impersonate_state["index"] = idx + 1
            nxt = (_CFFI_IMPERSONATE_CANDIDATES[idx + 1]
                   if idx + 1 < len(_CFFI_IMPERSONATE_CANDIDATES) else None)
            log("curl_cffi: impersonate target '{}' unsupported by the installed "
                "curl_cffi version -- switching to {} for the rest of this "
                "session".format(value, repr(nxt) if nxt else "no impersonation"))


def _get_opener():
    global _opener, _cookiejar
    if _opener:
        return _opener
    _cookiejar = cookiejar.CookieJar()
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    except AttributeError:
        ctx = ssl._create_unverified_context()
    _opener = build_opener(HTTPCookieProcessor(_cookiejar), HTTPSHandler(context=ctx))
    return _opener


def clear_cookies(domain=None):
    global _cookiejar
    _get_opener()
    if _cookiejar is None:
        return
    try:
        if domain:
            for cookie in list(_cookiejar):
                if domain in (cookie.domain or ""):
                    _cookiejar.clear(cookie.domain, cookie.path, cookie.name)
        else:
            _cookiejar.clear()
    except Exception as e:
        log("clear_cookies error: {}".format(e))
    # Audit Bug C: also reset the thread-local curl session's cookies —
    # the failing request went through curl_cffi, whose session keeps
    # the poisoned Cloudflare cookies and re-sends them on every retry.
    try:
        _sess = _get_curl_session()
        if _sess is not None:
            _sess.cookies.clear()
    except Exception:
        pass


def get_cookie_header_for_url(url):
    """Best-effort merged Cookie header for `url`, combining the shared
    urllib cookiejar and the thread-local curl_cffi session jar.
    IMPORTANT: call from the SAME thread that did the scraping — the
    curl sessions are thread-local. Returns "" when nothing applies."""
    parts = []
    try:
        _get_opener()
        if _cookiejar is not None:
            req = Request(url)
            _cookiejar.add_cookie_header(req)
            h = req.get_header("Cookie", "")
            if h:
                parts.append(h)
    except Exception:
        pass
    try:
        session = _get_curl_session()
        jar = None
        if session is not None:
            jar = getattr(session.cookies, "jar", None)  # curl_cffi Cookies wrapper
            if jar is None and hasattr(session.cookies, "add_cookie_header"):
                jar = session.cookies
        if jar is not None:
            req = Request(url)
            jar.add_cookie_header(req)
            h = req.get_header("Cookie", "")
            if h:
                parts.append(h)
    except Exception:
        pass
    seen, merged = set(), []
    for chunk in parts:
        for kv in chunk.split(";"):
            kv = kv.strip()
            if not kv:
                continue
            k = kv.split("=", 1)[0]
            if k in seen:
                continue
            seen.add(k)
            merged.append(kv)
    return "; ".join(merged)


# ─── Manifest validation (decoy-playlist detection) ─────────────────────────
_STATIC_ASSET_EXTS = (".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".gif",
                      ".svg", ".woff", ".woff2", ".ttf", ".ico", ".map", ".html")


def validate_manifest(url, referer=None):
    """Confirm `url` is a real HLS manifest and not a disguised HTML/asset
    file (the .txt/.woff2/.css trick several CDNs use).
    Returns (True, body) or (False, None)."""
    try:
        body, final_url = fetch(url, referer=referer)
    except Exception:
        return False, None
    if not body:
        return False, None
    if not body.lstrip().startswith("#EXTM3U"):
        return False, None
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not any(l.startswith(("#EXTINF", "#EXT-X-STREAM-INF",
                             "#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE",
                             "#EXT-X-KEY", "#EXT-X-MAP")) for l in lines):
        return False, None
    for m in [l for l in lines if not l.startswith("#")]:
        try:
            path = urlparse(urljoin(final_url or url, m)).path.lower()
        except Exception:
            path = m.lower()
        if path.endswith(_STATIC_ASSET_EXTS):
            log("validate_manifest: static asset in playlist: {}".format(m[:80]))
            return False, None
    return True, body


def _decode_response_body(raw, info):
    ce = info.get("Content-Encoding", "").lower()
    if "gzip" in ce:
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception as e:
            log("Gzip decompression error: {}".format(e))
    elif "deflate" in ce:
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            try:
                raw = zlib.decompress(raw)
            except Exception as e:
                log("Deflate decompression error: {}".format(e))
    elif "br" in ce and brotli is not None:
        try:
            raw = brotli.decompress(raw)
        except Exception as e:
            log("Brotli decompression error: {}".format(e))
    charset = "utf-8"
    ctype = info.get("Content-Type", "").lower()
    charset_match = re.search(r'charset=([^\s;]+)', ctype, re.I)
    if charset_match:
        detected_charset = charset_match.group(1).strip()
        detected_charset = detected_charset.strip('"\'')
        if detected_charset:
            charset = detected_charset
    decoders = []
    if charset and charset not in ["utf-8", "utf8"]:
        decoders.append((charset, "Detected charset"))
    decoders.extend([
        ("utf-8", "UTF-8 fallback"),
        ("windows-1256", "Arabic Windows fallback"),
        ("iso-8859-6", "Arabic ISO fallback"),
        ("latin-1", "Latin-1 fallback (preserves bytes)"),
    ])
    for enc, name in decoders:
        try:
            result = raw.decode(enc)
            if enc != "utf-8" and enc != charset:
                log("Decoded with {} encoding".format(name))
            return result
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("latin-1", errors="replace")


def _encode_unicode_url(url):
    try:
        parsed = urlparse(url)
        path_segments = []
        for segment in parsed.path.split('/'):
            if segment:
                if any(ord(c) > 127 for c in segment):
                    path_segments.append(quote_plus(segment.encode('utf-8')))
                else:
                    path_segments.append(segment)
            else:
                path_segments.append('')
        encoded_path = '/'.join(path_segments)
        if not encoded_path.startswith('/'):
            encoded_path = '/' + encoded_path
        encoded_query = ''
        if parsed.query:
            try:
                query_parts = []
                for part in parsed.query.split('&'):
                    if '=' in part:
                        key, val = part.split('=', 1)
                        if any(ord(c) > 127 for c in val):
                            query_parts.append(key + '=' + quote_plus(val.encode('utf-8')))
                        else:
                            query_parts.append(part)
                    else:
                        query_parts.append(part)
                encoded_query = '&'.join(query_parts)
            except Exception:
                encoded_query = parsed.query
        from urllib.parse import urlunparse
        encoded_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            encoded_path,
            parsed.params,
            encoded_query,
            parsed.fragment
        ))
        return encoded_url
    except Exception:
        return url


# [PATCH 103] URLs of these hosts appear on ordinary pages (analytics beacon, CDN libraries)
_BENIGN_CF_URL_RE = re.compile(
    r'https?://[^\s"\'<>]*(?:cloudflareinsights|cdnjs\.cloudflare)[^\s"\'<>]*', re.I)
# [2026-10-21] Cloudflare's passive "JS detections" snippet (window.__CF$cv$params
# + /cdn-cgi/challenge-platform/scripts/jsd/main.js) rides along on ordinary
# pages -- e.g. the ~10 KB cdnmovies player -- and is not a challenge (real ones
# load .../challenge-platform/h/<x>/orchestrate/...).
_BENIGN_CF_JSD_RE = re.compile(
    r'(?:https?://[^\s"\'<>]*)?/cdn-cgi/challenge-platform/(?:h/[a-z]/)?scripts/jsd/[^\s"\'<>;]*', re.I)


def _is_cloudflare_challenge(html):
    """[PATCH 103] See _is_cloudflare_challenge_raw; benign Cloudflare-hosted URLs are ignored first."""
    if html:
        html = _BENIGN_CF_URL_RE.sub(" ", html)
        html = _BENIGN_CF_JSD_RE.sub(" ", html)
    return _is_cloudflare_challenge_raw(html)


def _is_cloudflare_challenge_raw(html):
    if not html:
        return False
    html_lower = html.lower()
    strong_markers = [
        'cf-browser-verification', 'just a moment', 'checking your browser',
        'cf-challenge-running', 'cf-im-under',
        'enable javascript and cookies to continue',
    ]
    if any(m in html_lower for m in strong_markers):
        return True
    if len(html) < 15000 and 'challenge-platform' in html_lower:
        return True
    weak_markers = ['turnstile', 'ray id', 'cloudflare']
    if len(html) < 5000 and any(m in html_lower for m in weak_markers):
        return True
    return False


def _fetch_via_browser_proxy(url, referer=None, post_data=None):
    if not _BROWSER_PROXY_URL:
        return None, url
    try:
        import json as _json
        payload = {'url': url, 'referer': referer or "", 'post_data': post_data}
        req = Request(
            _BROWSER_PROXY_URL + '/fetch',
            data=_json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': UA},
            method='POST'
        )
        with urlopen(req, timeout=60) as resp:
            result = _json.loads(resp.read().decode('utf-8'))
            if result.get('success') and result.get('html'):
                log("Browser proxy SUCCESS for {}".format(url[:60]))
                return result['html'], result.get('url', url)
    except Exception as e:
        log("Browser proxy FAILED: {} for {}".format(e, url[:60]))
    return None, url


# [FIX] dead-host short-circuit. A device log showed a host that
# refused every connection (Errno 111 / curl (7)) being retried 9 times
# over ~19s for ONE server: urllib x3, curl_cffi x2, then the generic
# resolver's own "retry once", then the iframe-chain fetch -- each a fresh
# fetch() with its own full retry budget. "Connection refused" is a
# definite answer (the host actively rejected us), not a blip, so mark the
# host dead for a short TTL: fetch() stops retrying it immediately and
# skips it instantly for the next _DEAD_HOST_TTL seconds. Local/LAN
# addresses (TorrServer, the browser proxy, ...) are never marked, so a
# service the user starts a moment later is picked up right away.
_DEAD_HOST_TTL = 120
_dead_hosts = {}
_dead_hosts_lock = threading.Lock()


def _is_local_host(host):
    h = (host or "").split(":")[0].strip("[]").lower()
    return (not h or h == "localhost" or h.endswith(".local") or h == "::1"
            or h.startswith("127.") or h.startswith("10.")
            or h.startswith("192.168.") or h.startswith("169.254.")
            or any(h.startswith("172.%d." % i) for i in range(16, 32)))


def _is_connection_refused(err):
    t = str(err).lower()
    return ("connection refused" in t or "errno 111" in t
            or "curl: (7)" in t or "could not connect to server" in t)


def _mark_host_dead(url, err):
    host = urlparse(url).netloc.lower()
    if not host or _is_local_host(host):
        return False
    with _dead_hosts_lock:
        _dead_hosts[host] = time.time() + _DEAD_HOST_TTL
    log("Host {} refused the connection -- skipping it for {}s ({})".format(
        host, _DEAD_HOST_TTL, str(err)[:80]))
    return True


def _host_is_dead(url):
    host = urlparse(url).netloc.lower()
    with _dead_hosts_lock:
        exp = _dead_hosts.get(host)
        if exp is None:
            return False
        if time.time() >= exp:
            _dead_hosts.pop(host, None)
            return False
        return True


def fetch(url, referer=None, extra_headers=None, post_data=None,
           timeout=None, max_retries=None):
    """
    Robust fetch with:
    - Referer defaults from the SINGLE referers.py table (was: two
      drifted elif chains, PATH A and PATH B)
    - Auto retry on transient errors
    - curl_cffi fallback for Cloudflare-protected sites
    - External browser proxy fallback for Turnstile CAPTCHAs (if configured)
    - Brotli / gzip / deflate decompression
    - Cookie jar (shared session)
    - UI feedback flags (proxy used / curl failed & proxy missing)
    """
    global _opener
    set_proxy_used(False)
    set_curl_failed_needs_proxy(False)

    # [FIX] optional fast-fail override for speculative candidate/
    # iframe-follow fetches (dead mirrors used to eat up to ~93s
    # each via the default 30s*3-attempt budget before the sweep
    # could move to the next server). None preserves today's
    # behavior exactly for every other caller.
    max_retries = 2 if max_retries is None else max_retries
    if _host_is_dead(url):
        log("Fetch skipped (host refused connections recently): {}".format(url))
        return None, url
    use_cffi = False
    parsed_quick = urlparse(url)
    domain_quick = parsed_quick.netloc.lower()
    # cf_domains trimmed: themoviedb removed — TMDB needs no Chrome
    # impersonation; the plain urllib path is faster and lighter.
    cf_domains = ("egydead", "wecima", "mycima", "topcinema", "arabseed",
                  "shaheed", "shahid", "fasel", "akwam", "akoam",
                  "egybestvid",
                  "cdnm.ink", "cdnmovies")  # [2026-10-21] share.cdnm.ink answered the box's urllib with 404, the browser with 200
                  # [FIX] confirmed directly: plain urllib gets
                  # served a tiny (~38 byte) bot-challenge stub by this CDN's
                  # embed-page host, inconsistently and without tripping
                  # _is_cloudflare_challenge() -- so fetch() treated the stub
                  # as a successful response and retries never escalated to
                  # curl_cffi. A direct curl_cffi(impersonate="chrome") call
                  # to the same URL got the full real page on the first try,
                  # every time.
    if any(d in domain_quick for d in cf_domains):
        use_cffi = True

    for attempt in range(max_retries + 1):
        try:
            # ────────────────────────────────────────────────────────────────
            # PATH A: curl_cffi (Cloudflare JS challenge)
            # ────────────────────────────────────────────────────────────────
            if (use_cffi or attempt > 0) and _CURL_CFFI_OK:
                try:
                    is_ajax = any(x in url.lower() for x in ["ajax", "get__watch", "get__quality", "api/", ".json", "wp-json"])

                    cf_headers = {
                        "User-Agent": UA,
                        "Accept-Language": "ar,en-US,en;q=0.9",
                        "Accept-Encoding": ACCEPT_ENCODING,
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1",
                    }

                    if is_ajax:
                        cf_headers.update({
                            "X-Requested-With": "XMLHttpRequest",
                            "Accept": "application/json, text/javascript, */*; q=0.01",
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Pragma": "no-cache",
                            "Sec-Fetch-Dest": "empty",
                            "Sec-Fetch-Mode": "cors",
                            "Sec-Fetch-Site": "same-origin",
                        })
                    else:
                        cf_headers.update({
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Sec-Fetch-Dest": "document",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-Site": "none",
                            "Sec-Fetch-User": "?1",
                        })

                    if not referer:
                        referer = get_referer(url)
                    cf_headers["Referer"] = referer

                    if extra_headers:
                        cf_headers.update(extra_headers)

                    log("curl_cffi fetch: {} (attempt {})".format(url, attempt + 1))
                    # [FIX] confirmed directly: egybestvid.com's embed host
                    # returns a real page to a one-off curl_cffi module-level
                    # call, but a 38-byte "Video embed restricted for this
                    # domain" stub to the SAME request (identical headers,
                    # identical impersonate, with or without impersonate set
                    # at Session construction) when made through a
                    # curl_cffi Session object. Reproduced 4/4 times outside
                    # the plugin entirely -- Session vs one-off is the only
                    # variable that correlates with the outcome. Bypass the
                    # persistent session for this host specifically rather
                    # than risk the same issue on other sites that currently
                    # rely on session cookie persistence.
                    _no_session_domains = ("egybestvid",)
                    if any(d in domain_quick for d in _no_session_domains):
                        cffi_client = _curl_requests
                    else:
                        session = _get_curl_session()
                        cffi_client = session if session is not None else _curl_requests

                    # [PATCH 66] str/bytes bodies (faselhd_rip AJAX, OpenSubtitles
                    # /download) used to fall into the GET branch
                    _pd = None
                    if post_data:
                        if isinstance(post_data, str):
                            _pd = post_data.encode("utf-8")
                        else:
                            _pd = post_data
                        if not isinstance(post_data, dict) and not any(
                                k.lower() == "content-type" for k in cf_headers):
                            cf_headers["Content-Type"] = "application/x-www-form-urlencoded"
                    _imp = _get_cffi_impersonate()

                    if _imp and not any(k.lower() == "user-agent" for k in (extra_headers or {})):
                        # the impersonated browser sends its own matching
                        # User-Agent -- a Chrome/155 UA on a chrome1xx TLS
                        # fingerprint is a mismatch Cloudflare can see
                        cf_headers.pop("User-Agent", None)
                    if _imp:
                        # declaring impersonate="chrome120" (or whichever
                        # candidate) but then hand-setting Accept-Encoding
                        # without "br"/"zstd" is a fingerprint mismatch real
                        # Chrome never produces -- let curl_cffi's
                        # impersonation layer fill this in itself (matches
                        # the browser header set exactly, and that layer's
                        # own bundled libcurl build actually decodes
                        # whatever it advertises).
                        cf_headers.pop("Accept-Encoding", None)
                    else:
                        # [FIX] a real device log showed this exact failure:
                        # once every impersonate candidate is exhausted and
                        # curl_cffi falls back to no impersonation at all,
                        # popping Accept-Encoding here left curl_cffi/libcurl
                        # to advertise ITS OWN default encoding list (which
                        # includes br/zstd on a normal build) while this
                        # device's actual libcurl can only decode gzip/
                        # deflate -- every single request then failed with
                        # "(61) Unrecognized content encoding type", 100% of
                        # the time, once impersonation was given up on. Keep
                        # an explicit, honest Accept-Encoding (matching what
                        # PATH B below already advertises) so curl never
                        # negotiates a compression this build can't decode.
                        cf_headers["Accept-Encoding"] = ACCEPT_ENCODING

                    _cffi_timeout = 30 if timeout is None else timeout

                    def _do_cffi_request(imp):
                        if _pd is not None:
                            if imp:
                                return cffi_client.post(url, data=_pd, headers=cf_headers, impersonate=imp, timeout=_cffi_timeout)
                            return cffi_client.post(url, data=_pd, headers=cf_headers, timeout=_cffi_timeout)
                        if imp:
                            return cffi_client.get(url, headers=cf_headers, impersonate=imp, timeout=_cffi_timeout)
                        return cffi_client.get(url, headers=cf_headers, timeout=_cffi_timeout)

                    try:
                        r = _do_cffi_request(_imp)
                    except TypeError as _te:
                        if "imperson" not in str(_te).lower():
                            raise
                        # curl_cffi version doesn't accept `impersonate` as a
                        # kwarg at all (very old release) -- same permanent
                        # breakage as the "not supported" case below, just a
                        # different error shape; drop impersonation for the
                        # rest of this process rather than retry it.
                        if _imp:
                            _mark_cffi_impersonate_broken(_imp)
                        cf_headers["Accept-Encoding"] = ACCEPT_ENCODING
                        cf_headers.setdefault("User-Agent", UA)
                        r = _do_cffi_request(None)
                    except Exception as e:
                        # [FIX] a device log confirmed this exact string: the
                        # installed curl_cffi version rejects "chrome120" as
                        # an impersonate target with "Impersonating chrome120
                        # is not supported" -- a permanent, deterministic
                        # failure (unlike a timeout/network blip), so retry
                        # the SAME attempt immediately with the next
                        # candidate rather than let the outer retry loop
                        # sleep 1.5s and burn more attempts on the exact
                        # same doomed call.
                        #
                        # [FIX] a second real device log showed this single-
                        # substitution retry wasn't enough: on a build where
                        # every single candidate is rejected (6 for 6), each
                        # outer "attempt" only burned through ONE extra
                        # candidate before bubbling out to the outer retry
                        # loop's 1.5s sleep, so draining the whole candidate
                        # list cost 2 wasted sleeps (~3s) spread across 3
                        # outer attempts before falling back to plain
                        # urllib -- every single time the process started.
                        # Loop through every remaining candidate (and finally
                        # None) within this same attempt instead, so the
                        # cascade converges in one shot with zero extra
                        # sleeps, no matter how many candidates are dead.
                        if _imp and "not supported" in str(e).lower() and "imperson" in str(e).lower():
                            _mark_cffi_impersonate_broken(_imp)
                            while True:
                                _next_imp = _get_cffi_impersonate()
                                if not _next_imp:
                                    # cascade exhausted -- dropping
                                    # impersonation entirely needs the same
                                    # explicit, decodable Accept-Encoding as
                                    # the no-impersonation path above, or it
                                    # hits the same "(61) Unrecognized
                                    # content encoding type" failure on this
                                    # very attempt.
                                    cf_headers["Accept-Encoding"] = ACCEPT_ENCODING
                                    cf_headers.setdefault("User-Agent", UA)
                                try:
                                    r = _do_cffi_request(_next_imp)
                                    break
                                except Exception as e2:
                                    if _next_imp and "not supported" in str(e2).lower() and "imperson" in str(e2).lower():
                                        _mark_cffi_impersonate_broken(_next_imp)
                                        continue
                                    raise
                        else:
                            raise

                    html = r.text
                    final_url = r.url
                    if any(x in final_url.lower() for x in ("alliance4creativity.com", "watch-it-legally")):
                        log("!!! ACE Redirect detected for {} !!!".format(url))
                        return None, final_url

                    if _is_cloudflare_challenge(html):
                        if _BROWSER_PROXY_URL:
                            log("Cloudflare challenge ({} bytes) via curl_cffi, routing to browser proxy".format(len(html)))
                            proxy_html, proxy_url = _fetch_via_browser_proxy(url, referer, post_data)
                            if proxy_html:
                                set_proxy_used(True)
                                return proxy_html, proxy_url or final_url
                            log("Browser proxy failed for {}".format(url[:60]))
                        else:
                            set_curl_failed_needs_proxy(True)
                            log("Cloudflare challenge detected via curl_cffi, but proxy is NOT configured. Please enable it in Settings.")
                            clear_cookies(domain_quick)
                        # falls through to PATH B (urllib) on the same
                        # iteration — trying the plain path after a curl
                        # challenge is reasonable.
                    else:
                        log("curl_cffi OK: {} ({} bytes)".format(final_url, len(html)))
                        return html, final_url
                except Exception as e:
                    log("curl_cffi attempt {} failed: {}".format(attempt + 1, e))
                    if _is_connection_refused(e) and _mark_host_dead(url, e):
                        return None, url
                    err_str = str(e).lower()
                    if "timeout" in err_str or "network" in err_str or "dns" in err_str:
                        set_curl_failed_needs_proxy(False)
                    elif not _BROWSER_PROXY_URL:
                        set_curl_failed_needs_proxy(True)
                    if attempt < max_retries:
                        time.sleep(1.5)
                        continue

            # ────────────────────────────────────────────────────────────────
            # PATH B: Standard urllib (fast path)
            # ────────────────────────────────────────────────────────────────
            opener = _get_opener()
            encoded_url = _encode_unicode_url(url)

            if not referer:
                # was: ~40-branch elif chain — now the single table
                referer = get_referer(encoded_url)

            headers = {
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ar,en-US,en;q=0.9",
                "Accept-Encoding": ACCEPT_ENCODING,
                "Connection": "keep-alive",
                "Referer": referer,
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
            if any(x in encoded_url.lower() for x in ["ajax", "get__watch", "get__quality", "api/", ".json"]):
                headers.update({
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                })
            if extra_headers:
                headers.update(extra_headers)

            data = post_data
            if data and isinstance(data, dict):
                data = urlencode(data).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            elif data and isinstance(data, (str, bytes)):
                if isinstance(data, str):
                    data = data.encode("utf-8")

            log("Fetching (attempt {}): {}".format(attempt + 1, encoded_url))
            req = Request(encoded_url, headers=headers, data=data)

            with opener.open(req, timeout=(TIMEOUT if timeout is None else timeout)) as resp:
                raw = resp.read()
                final_url = resp.geturl()
                info = resp.info()

                if any(x in final_url.lower() for x in ("alliance4creativity.com", "watch-it-legally")):
                    log("!!! ACE Redirect detected for {} !!!".format(encoded_url))
                    return None, final_url

                html = _decode_response_body(raw, info)

                if not use_cffi and _CURL_CFFI_OK and info.get("Content-Encoding", "").lower() == "br" and brotli is None:
                    log("Brotli response but no brotli module available, switching to curl_cffi for native decompression")
                    use_cffi = True
                    continue

                if not use_cffi and _CURL_CFFI_OK and _is_cloudflare_challenge(html):
                    log("Cloudflare challenge detected in urllib response, switching to curl_cffi")
                    use_cffi = True
                    continue

                if _is_cloudflare_challenge(html):
                    if _BROWSER_PROXY_URL:
                        log("Cloudflare challenge ({} bytes), routing to browser proxy".format(len(html)))
                        proxy_html, proxy_url = _fetch_via_browser_proxy(encoded_url, referer, post_data)
                        if proxy_html:
                            set_proxy_used(True)
                            return proxy_html, proxy_url or final_url
                        log("Browser proxy failed for {}".format(encoded_url[:60]))
                    else:
                        set_curl_failed_needs_proxy(True)
                        log("Cloudflare detected, but proxy is NOT configured. Please enable it in Settings.")
                    # Audit Bug B: the challenge page is NOT content. The
                    # old code fell through to "Fetch OK" and returned the
                    # CAPTCHA HTML as if it were the page — extractors then
                    # scraped garbage "categories". Fail the fetch so error
                    # paths (and site health) trigger correctly.
                    return None, final_url

                log("Fetch OK: {} ({} bytes)".format(final_url, len(html)))
                return html, final_url

        except HTTPError as e:
            if attempt < max_retries and e.code in (503, 429, 502, 504):
                log("Fetch HTTPError {}, retrying in 2s: {}".format(e.code, url))
                time.sleep(2)
                continue
            try:
                raw = e.read()
                html = _decode_response_body(raw, e.info()) if raw else ""
                if not use_cffi and _CURL_CFFI_OK and e.code in (403, 503):
                    log("HTTPError {} + possible Cloudflare, switching to curl_cffi".format(e.code))
                    use_cffi = True
                    continue
                if e.code in (403, 503) and _is_cloudflare_challenge(html):
                    if _BROWSER_PROXY_URL:
                        log("HTTPError {} + Cloudflare, trying browser proxy".format(e.code))
                        proxy_html, proxy_url = _fetch_via_browser_proxy(encoded_url, referer, post_data)
                        if proxy_html:
                            set_proxy_used(True)
                            return proxy_html, proxy_url
                    else:
                        set_curl_failed_needs_proxy(True)
                        log("Cloudflare HTTPError, but proxy is NOT configured.")
                log("Fetch HTTPError: {} → {} {} ({} bytes)".format(url, e.code, e.reason, len(html)))
            except Exception:
                log("Fetch HTTPError: {} → {} {}".format(url, getattr(e, "code", "?"), getattr(e, "reason", e)))
            return None, url

        except URLError as e:
            if _is_connection_refused(getattr(e, "reason", e)) and _mark_host_dead(url, e):
                _opener = None
                return None, url
            if attempt < max_retries:
                log("Fetch URLError (retry {}): {} → {}".format(attempt + 1, url, e))
                _opener = None
                time.sleep(1.5)
                continue
            log("Fetch URLError: {} → {}".format(url, e))
            _opener = None
            return None, url

        except UnicodeEncodeError as e:
            log("Fetch UnicodeEncodeError: {} → {}".format(url, e))
            try:
                encoded_url = url.encode('utf-8').decode('ascii', errors='ignore')
                if encoded_url != url:
                    log("Retrying with encoded URL: {}".format(encoded_url))
                    return fetch(encoded_url, referer, extra_headers, post_data)
            except Exception:
                pass
            return None, url

        except Exception as e:
            if attempt < max_retries:
                log("Fetch Error (retry {}): {} → {}".format(attempt + 1, url, e))
                time.sleep(1)
                continue
            log("Fetch Error: {} → {}".format(url, e))
            return None, url

    return None, url


def fetch_json(url, referer=None, extra_headers=None, post_data=None):
    """
    Fetch a URL and parse the response body as JSON.

    Returns a dict/list on success, or None on failure.
    """
    html, _ = fetch(url, referer=referer, extra_headers=extra_headers, post_data=post_data)
    if not html:
        return None
    try:
        return json.loads(html)
    except (ValueError, TypeError) as e:
        log("fetch_json: failed to parse JSON from {}: {}".format(url, e))
        return None