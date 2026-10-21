# -*- coding: utf-8 -*-
"""Host resolvers + the extract_stream entry point.

PHASE-2 SPLIT of base.py's resolver layer. Audit fixes baked in:

  * _ORDERED_RESOLVER_KEYS: longest-key-first dispatch — the insertion-
    order substring scan let "vidtube" (topcinema resolver) shadow
    "vidtube.one" (hanerix resolver) so the vidtube.one entries were
    unreachable dead code.
  * resolve_host passes the CALLER's referer through to resolvers that
    accept it (was: accepted and silently dropped).
  * resolve_generic_embed takes a referer (was: ignored).
  * resolve_streamruby's egydead probe list kept verbatim — it's probe
    logic, not a referer default; referers.py owns defaults.
  * resolve_hgcloud's self-import deleted; resolve_vidtube's identical
    re-request fixed (second fetch now uses a different referer).
  * All f-strings converted to .format() (py2-compat policy).

Extracted from base.py (Phase-2): Unbaser/packer, every resolve_*
function, HOST_RESOLVERS, the dispatch chain, extract_stream(_all).
"""

import re
import json
import time
import base64
import random
import hashlib
import html as html_module  # aliased: every resolver uses a local `html` variable for page text
from urllib.parse import urlparse, urljoin, parse_qs, unquote, quote

import threading  # [PATCH H1] for the vidsrc-family recursion guard below
from .net import fetch, fetch_json, log, UA, get_cookie_header_for_url
from .htmlmedia import (find_m3u8, find_m3u8_all, find_mp4, find_mp4_all,
                        _best_media_url, get_last_quality_variants,
                        get_synthesized_variants, _correct_stream_url,
                        _is_placeholder_media_url, extract_iframes,
                        _quality_tls, _quality_lock, _label_quality_variant)
from .referers import get_referer
from .htmlmedia import _quality_probe_text  # [PATCH 94]

# ─── AES-256-GCM backend (n1mwq.org playback decrypt) ──────────────────────
# [PATCH N1/N2] Confirmed directly on the user's real OpenATV/Enigma2 box:
# `pip install cryptography` fails there -- its cffi dependency needs a C
# compiler ("arm-oe-linux-gnueabi-gcc") that doesn't exist on the device,
# so pycryptodome (also a compiled extension) would fail the same way.
# Three tiers, richest/fastest first, with a pure-Python implementation
# (_pure_aes_gcm.py -- no compiled extension, no dependency at all) as the
# guaranteed-to-work last resort. resolve_n1mwq() fails loudly via log()
# only if literally none of the three import, which shouldn't happen.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _CRYPTOGRAPHY_AESGCM
    _AESGCM_BACKEND = "cryptography"
except Exception:
    _CRYPTOGRAPHY_AESGCM = None
    try:
        from Crypto.Cipher import AES as _PYCRYPTODOME_AES
        _AESGCM_BACKEND = "pycryptodome"
    except Exception:
        _PYCRYPTODOME_AES = None
        try:
            from . import _pure_aes_gcm as _PURE_AES_GCM
            _AESGCM_BACKEND = "pure-python"
        except Exception:
            _PURE_AES_GCM = None
            _AESGCM_BACKEND = None
            print("No AES-GCM backend available at all (cryptography / "
                  "pycryptodome / pure-python) -- n1mwq.org playback "
                  "decrypt will be unavailable")


# ─── Video Host Resolvers ─────────────────────────────────────────────────────

def _evaluate_streamtape_assignment(prefix, computed, chain_text):
    """
    [PATCH T2] Replicates one document.getElementById(ID).innerHTML = PREFIX +
    (COMPUTED).substring(N).substring(M)...; assignment exactly as the real page's own JS
    would evaluate it.
    """
    s = computed
    for n in re.findall(r'\.substring\((\d+)\)', chain_text):
        s = s[int(n):]
    return prefix + s


def resolve_streamtape(url):
    """
    [PATCH T2] Confirmed against a real captured streamtape.cc page: the real link is built
    via several decoy document.getElementById(...).innerHTML = PREFIX + (STR).substring(N)...
    assignments, only some of which decode to the real "get_video" URL -- the others are
    deliberately wrong, and which element id gets the real value varies. Every assignment
    found is evaluated the same way the page's own JS would, and only candidates that decode
    to a real get_video URL are kept, rather than trusting a single fixed element id.
    """
    try:
        html, page_final = fetch(url, referer="https://streamtape.com/")
        if not html:
            return None
        page_host = urlparse(page_final or url).netloc.lower()

        assignment_pattern = re.compile(
            r"document\.getElementById\(['\"]\w+['\"]\)\.innerHTML\s*=\s*"
            r"(['\"])(.*?)\1\s*\+\s*"
            r"(?:['\"]{2}\s*\+\s*)?"
            r"\(\s*(['\"])(.*?)\3\s*\)"
            r"((?:\.substring\(\d+\))+)"
        )
        # [2026-10-21] decoys can be well-formed too, just on a host that
        # doesn't exist (box: "streamtape.bc" -> Name or service not known).
        # Keep every well-formed one; prefer the page's own host, then any
        # host that resolves.
        cands = []
        for m in assignment_pattern.finditer(html):
            _, prefix, _, computed, chain_text = m.groups()
            candidate = _evaluate_streamtape_assignment(prefix, computed, chain_text)
            _p = urlparse(candidate); _qs = parse_qs(_p.query)
            if _p.path == "/get_video" and all(_k in _qs for _k in ("id", "expires", "ip", "token")):
                link = candidate
                if link.startswith("//"):
                    link = "https:" + link
                elif not link.startswith("http"):
                    link = "https://" + (page_host or "streamtape.com") + link
                if link not in cands:
                    cands.append(link)
        if cands:
            import socket as _sock

            def _resolves(h):
                try:
                    _sock.getaddrinfo(h, 443)
                    return True
                except Exception:
                    return False
            same = [c for c in cands if urlparse(c).netloc.lower() == page_host]
            for c in same + [c for c in cands if c not in same]:
                if _resolves(urlparse(c).netloc):
                    if len(cands) > 1:
                        log("resolve_streamtape: {} candidate link(s), using {}".format(
                            len(cands), urlparse(c).netloc))
                    return c
            # none resolves: the real path/token on the page's own host
            p0 = urlparse(cands[0])
            return "https://{}{}?{}".format(page_host or "streamtape.com", p0.path, p0.query)

        # fall back to the older, simpler patterns in case a page variant still uses them
        m = re.search(r"robotlink\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*'([^']+)'", html)
        if m:
            link = m.group(1) + m.group(2)
            if not link.startswith("http"):
                link = "https:" + link
            return link
        m = re.search(r"robotlink\)\.innerHTML\s*=\s*['\"]([^'\"]+)['\"]", html)
        if m:
            link = m.group(1)
            return ("https:" + link) if link.startswith("//") else link
        m = re.search(r'(/get_video\?[^"\'&\s]+)', html)
        if m:
            return "https://streamtape.com" + m.group(1)
        return find_mp4(html)
    except Exception:
        pass
    return None


def _follow_landing_iframe(html, base_url):
    """[PATCH WC] Several embed hosts (doodstream/dhcplay, mixdrop, luluvdo/
    lulustream, byselapuix, and likely others) respond to a direct fetch of
    their "/e/" embed URL with a "/d/" or "/f/" landing/share page instead
    of the real player -- confirmed directly against real captured markup
    for all four of these hosts. The real player is a plain, STATIC
    <iframe src="..."> already present in that landing page's raw HTML (no
    JS execution needed), just at a different path or even a completely
    different domain than the original URL. Returns the resolved absolute
    iframe URL, or None. Skips 1x1 tracking/ad iframes."""
    if not html:
        return None
    try:
        for m in re.finditer(r'<iframe\b[^>]*\bsrc=(["\'])([^"\']+)\1[^>]*>', html, re.I):
            attrs = m.group(0)
            src = m.group(2)
            if not src or src.startswith("data:") or src.startswith("javascript:"):
                continue
            wm = re.search(r'\bwidth=["\']?(\d+)', attrs)
            hm = re.search(r'\bheight=["\']?(\d+)', attrs)
            if wm and hm and int(wm.group(1)) <= 2 and int(hm.group(1)) <= 2:
                continue
            return urljoin(base_url or "", src)
    except Exception:
        pass
    return None


def resolve_doodstream(url):
    """[PATCH 101] DoodStream family. The embed page may redirect to another domain (doodstream.com ->
    playmogo.com); pass_md5 must be called on the domain the page ACTUALLY loaded from, with that page as
    Referer. The media URL is returned with the Referer Dood's CDN checks."""
    DOOD_DOMAINS = [
        "dood.re", "dood.to", "dood.so", "dood.pm", "dood.ws",
        "dood.watch", "dood.sh", "dood.la", "dood.li", "dood.cx",
        "dood.xyz", "dood.wf", "d0o0d.com", "dsvplay.com",
        "doods.pro", "ds2play.com", "dooood.com", "doodstream.com",
        # [PATCH WC] confirmed via a real doodstream.com/e/ capture:
        # playmogo.com is a live current mirror -- it just never got tried
        # because this list never had it.
        "playmogo.com",
    ]
    try:
        page_html, page_url = None, url
        html, final = fetch(url, referer=url)
        if html and "pass_md5" in html:
            page_html, page_url = html, (final or url)
        if not page_html:
            tried = 0
            for dom in DOOD_DOMAINS:
                if tried >= 8:  # cap the worst case
                    break
                candidate = re.sub(
                    r'dood\.[a-z]+|dsvplay\.[a-z]+|d0o0d\.[a-z]+|doodstream\.[a-z]+',
                    dom, url)
                if candidate == url:
                    continue
                tried += 1
                html, final = fetch(candidate, referer=candidate)
                if html and "pass_md5" in html:
                    page_html, page_url = html, (final or candidate)
                    break
        if not page_html:
            # [PATCH WC] confirmed directly against a real doodstream.com/e/
            # capture: a direct fetch lands on the "/d/" share/landing page
            # (no pass_md5 anywhere on it), not the real "/e/" player -- the
            # real player is a plain, static <iframe src="..."> already in
            # that landing page's own HTML. Follow it before giving up.
            iframe_url = _follow_landing_iframe(html, final or url)
            if iframe_url:
                html2, final2 = fetch(iframe_url, referer=final or url)
                if html2 and "pass_md5" in html2:
                    page_html, page_url = html2, (final2 or iframe_url)
        if not page_html:
            return None
        for attempt in (1, 2):
            m = re.search(r'\$\.get\(["\'](/pass_md5/[^"\']+)["\']', page_html)
            if m:
                pass_path = m.group(1)
            else:
                m = re.search(r'pass_md5/([^"\'.\s&]+)', page_html)
                if not m:
                    return None
                pass_path = "/pass_md5/" + m.group(1)
            parsed = urlparse(page_url)
            base = "{}://{}".format(parsed.scheme, parsed.netloc)
            data, _ = fetch(base + pass_path, referer=page_url,
                            extra_headers={"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"})
            data = (data or "").strip()
            if data == "RELOAD" and attempt == 1:      # the page itself does location.reload() here
                html2, final2 = fetch(page_url, referer=page_url)
                if not html2:
                    return None
                page_html, page_url = html2, (final2 or page_url)
                continue
            if not data.startswith("http"):
                return None
            chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            rand = "".join(random.choice(chars) for _ in range(10))
            token = pass_path.rstrip("/").split("/")[-1]
            return "{}{}?token={}&expiry={}|Referer={}/".format(data, rand, token, int(time.time() * 1000), base)
    except Exception:
        pass
    return None

def resolve_vidbom(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html) or find_packed_links(html)
    except Exception:
        pass
    return None


def resolve_uqload(url):
    """
    [PATCH UQLOAD1] A real device log showed this resolver always
    returning nothing (embed page fetched fine, 2.7KB, but no stream
    regex matched) -- confirmed via a real recorded browsing session's
    network log that the real source URL is NOT in the embed page's
    initial HTML at all. The page's own JS POSTs to /dl once it loads:

        POST https://uqload.vc/dl
        Content-Type: application/x-www-form-urlencoded
        Referer: <the embed page URL>
        Origin: https://<embed host>
        body: auto=1&file_code=<code>&op=embed&referer=

    and gets back a JSON body (served with a text/html content-type,
    oddly, but JSON regardless) shaped like:
        {"sources":[{"file":"https://strm15.uqload.vc/hls2/.../master.m3u8?t=...&s=...&e=14400&..."}], ...}

    confirmed end-to-end against the real captured request/response
    pair, including the real master.m3u8 URL playing back correctly.
    Try this first; fall back to the old embed-page regex scan in case
    some uqload mirror still inlines the source directly.
    """
    try:
        m = re.search(r'/e/([A-Za-z0-9_-]+)', url)
        file_code = m.group(1) if m else ""
        if file_code:
            p = urlparse(url)
            base = "{}://{}".format(p.scheme or "https", p.netloc or "uqload.vc")
            headers = {
                "Referer": url,
                "Origin": base,
            }
            body, _ = fetch(
                base + "/dl", referer=url, extra_headers=headers,
                post_data="auto=1&file_code={}&op=embed&referer=".format(file_code)
            )
            if body:
                try:
                    data = json.loads(body)
                    sources = data.get("sources") or []
                    if sources and isinstance(sources, list):
                        stream_url = sources[0].get("file") if isinstance(sources[0], dict) else None
                        if stream_url:
                            variants = [s.get("file") for s in sources if isinstance(s, dict) and s.get("file")]
                            _quality_tls.variants = variants or [stream_url]
                            return _correct_stream_url(stream_url)
                except Exception as e:
                    log("resolve_uqload: /dl JSON parse failed: {}".format(e))

        html, _ = fetch(url, referer=url)
        if not html:
            return None
        m = re.search(r'sources:\s*\["([^"]+)"\]', html)
        if m:
            main = m.group(1)
            variants = find_mp4_all(html) or find_m3u8_all(html)
            _quality_tls.variants = variants if main in variants else ([main] + variants)
            return main
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_govid(url, referer=None):
    """Centralized resolver for govid.live / vidhide embeds.
    Handles the /play/ -> /e/ iframe chain and hex-encoded HLS URLs.
    """
    try:
        if '.m3u8' in url:
            return _correct_stream_url(url)
            
        # Use govid.live as referer to bypass Cloudflare on all sites
        req_referer = "https://govid.live/"
        html, _ = fetch(url, referer=req_referer, timeout=10, max_retries=0)
        
        if not html:
            return None

        # If this is a /play/ page, extract the inner iframe to /e/{id}/
        if '/play/' in url.lower():
            iframe_m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
            if iframe_m:
                embed_url = iframe_m.group(1).replace("\\/", "/")
                if not embed_url.startswith('http'):
                    embed_url = "https://govid.live" + embed_url
                if embed_url != url:
                    return resolve_govid(embed_url, referer)

        # Method 1: hex-encoded variable (Mohix pattern used by govid)
        for m in re.finditer(r'(?:const|var|let)\s+\w+\s*=\s*["\']([0-9a-fA-F]{40,})["\']', html):
            try:
                decoded = bytes.fromhex(m.group(1)).decode('utf-8', errors='ignore')
                if '.m3u8' in decoded and decoded.startswith('http'):
                    log("resolve_govid: decoded hex stream URL: {}".format(decoded[:80]))
                    return _correct_stream_url(decoded) + "|Referer=" + req_referer
            except Exception:
                pass

        # Method 2: JWPlayer sources array
        sources_m = re.search(r'sources\s*:\s*\[[^\]]*?file\s*:\s*["\']([^"\']+)["\']', html, re.S | re.I)
        if sources_m:
            stream_url = sources_m.group(1).replace("\\/", "/")
            return _correct_stream_url(stream_url) + "|Referer=" + req_referer

        # Method 3: Any m3u8 URL in the page
        m3u8_m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html, re.I)
        if m3u8_m:
            stream_url = m3u8_m.group(1).replace("\\/", "/")
            return _correct_stream_url(stream_url) + "|Referer=" + req_referer

        return None
    except Exception as e:
        log("resolve_govid error: {}".format(e))
        return None


def resolve_upstream(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def _mixdrop_wurl(html, referer_base):
    if not html:
        return None
    m = re.search(r'MDCore\.wurl\s*=\s*"([^"]+)"', html)
    if m:
        link = m.group(1)
        if link.startswith("//"):
            link = "https:" + link
        return link + "|Referer={0}/&Origin={0}&User-Agent={1}".format(referer_base, quote(UA, safe=""))
    for txt in _unpack_all(html):
        m = re.search(r'MDCore\.wurl\s*=\s*"([^"]+)"', txt)
        if m:
            link = m.group(1)
            if link.startswith("//"):
                link = "https:" + link
            return link + "|Referer={0}/&Origin={0}&User-Agent={1}".format(referer_base, quote(UA, safe=""))
    return None


def _origin(u, default="https://mixdrop.to"):
    try:
        p = urlparse(u)
        return "{}://{}".format(p.scheme or "https", p.netloc) if p.netloc else default
    except Exception:
        return default


def resolve_mixdrop(url):
    # [2026-10-21] mxcontent.net answered 403 to Referer mixdrop.to for a link
    # issued by another Mixdrop mirror: send the issuing page's own origin
    try:
        # the video link is checked against the browser that asked for it:
        # fetch the page and play the video with the same User-Agent
        html, final = fetch(url, referer=url, extra_headers={"User-Agent": UA})
        if not html:
            return None
        log("Mixdrop fetched {} bytes, MDCore present: {}".format(len(html), "MDCore" in html))
        result = _mixdrop_wurl(html, _origin(final or url))
        if result:
            return result
        # [PATCH WC] confirmed directly against a real mixdrop.ps/e/
        # capture: a direct fetch lands on the "/f/" share page (no
        # MDCore.wurl anywhere on it, not even packed), not the real "/e/"
        # player -- the real player is a plain, static <iframe src="...">
        # already in that landing page's own HTML (often a relative path
        # on the SAME mirror domain the "/f/" page redirected to).
        iframe_url = _follow_landing_iframe(html, final or url)
        if iframe_url:
            html2, final2 = fetch(iframe_url, referer=final or url, extra_headers={"User-Agent": UA})
            result = _mixdrop_wurl(html2, _origin(final2 or iframe_url))
            if result:
                return result
    except Exception:
        pass
    return None


def _voe_rot13(text):
    out = []
    for c in text:
        o = ord(c)
        if 65 <= o <= 90:
            o = (o - 65 + 13) % 26 + 65
        elif 97 <= o <= 122:
            o = (o - 97 + 13) % 26 + 97
        out.append(chr(o))
    return "".join(out)


def _voe_unscramble(enc):
    """[PATCH 102] Steps 1-5 of Voe's payload scheme; returns the base64 text of the JSON."""
    s = _voe_rot13(enc)
    for tok in ("@$", "^^", "~@", "%?", "*~", "!!", "#&"):
        s = s.replace(tok, "")
    raw = base64.b64decode(s + "=" * (-len(s) % 4))
    return "".join(chr((b - 3) % 256) for b in bytearray(raw))[::-1]


def _voe_decode_payload(enc):
    """Voe's obfuscated payload string -> dict, or None."""
    try:
        b64 = _voe_unscramble(enc)
        data = json.loads(base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _voe_payload_stream(html):
    """Stream URL from the page's <script type="application/json"> payload(s), or None. HLS preferred."""
    for m in re.finditer(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html or "", re.S | re.I):
        try:
            val = json.loads(m.group(1).strip())
        except Exception:
            continue
        for cand in (val if isinstance(val, list) else [val]):
            if isinstance(cand, str) and len(cand) > 20:
                data = _voe_decode_payload(cand)
                if not data:
                    continue
                for key in ("source", "hls", "direct_access_url", "mp4"):
                    u = str(data.get(key) or "").strip()
                    if u.startswith("http"):
                        return u
    return None


def resolve_voe(url):
    try:
        html, final = fetch(url, referer="https://voe.sx/")
        if not html:
            return None

        # [PATCH 102] obfuscated JSON payload (when the page itself is the player page)
        _pl = _voe_payload_stream(html)
        if _pl:
            _p = urlparse(final or url)
            return "{}|Referer={}://{}/".format(_pl, _p.scheme or "https", _p.netloc)

        for pat in [
            r"'hls'\s*:\s*'([^']+)'",
            r'"hls"\s*:\s*"([^"]+)"',
            r"sources\s*=\s*\[{[^}]*file\s*:\s*'([^']+)'",
            r'"file"\s*:\s*"([^"]+\.m3u8[^"]*)"',
            r'(https?://[^\s"\']+\.cloudwindow-route\.com[^\s"\']+\.m3u8[^\s"\']*)',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                stream_url = m.group(1).replace("\\/", "/")
                if stream_url.startswith("//"):
                    stream_url = "https:" + stream_url
                if "cloudwindow-route.com" in stream_url:
                    log("resolve_voe: Found cloudwindow-route.com stream: {}".format(stream_url[:80]))
                    return stream_url
                if ".m3u8" in stream_url:
                    log("resolve_voe: Found m3u8 stream: {}".format(stream_url[:80]))
                    return stream_url

        for enc in re.finditer(r'atob\([\'"]([A-Za-z0-9+/=]+)[\'"]\)', html):
            try:
                dec = base64.b64decode(enc.group(1) + "==").decode("utf-8", errors="ignore")
                mm = re.search(r'(https?://[^\s\'"<>]+\.(?:m3u8|txt|woff2)[^\s\'"<>]*)', dec)
                if mm:
                    stream_url = _correct_stream_url(mm.group(1))
                    if "cloudwindow-route.com" in stream_url:
                        log("resolve_voe: Found cloudwindow-route.com in base64: {}".format(stream_url[:80]))
                        return stream_url
                    if ".m3u8" in stream_url:
                        log("resolve_voe: Found m3u8 in base64: {}".format(stream_url[:80]))
                        return stream_url
            except Exception:
                pass

        for txt in _unpack_all(html):
            cloud_match = re.search(r'(https?://[^\s"\']+\.cloudwindow-route\.com[^\s"\']+\.m3u8[^\s"\']*)', txt, re.I)
            if cloud_match:
                stream_url = cloud_match.group(1).replace("\\/", "/")
                log("resolve_voe: Found cloudwindow-route.com in unpacked JS: {}".format(stream_url[:80]))
                return stream_url

            m3u8_match = re.search(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', txt, re.I)
            if m3u8_match:
                stream_url = m3u8_match.group(1).replace("\\/", "/")
                if _is_placeholder_media_url(stream_url):
                    continue
                log("resolve_voe: Found m3u8 in unpacked JS: {}".format(stream_url[:80]))
                return stream_url

        js_redirect = _find_js_redirect(html)
        if js_redirect:
            log("resolve_voe: Following JS redirect to {}".format(js_redirect[:100]))
            h2, final2 = fetch(js_redirect, referer="https://voe.sx/")
            if h2:
                _pl = _voe_payload_stream(h2)      # [PATCH 102] the real player page
                if _pl:
                    _p = urlparse(final2 or js_redirect)
                    return "{}|Referer={}://{}/".format(_pl, _p.scheme or "https", _p.netloc)
                cloud_match = re.search(r'(https?://[^\s"\']+\.cloudwindow-route\.com[^\s"\']+\.m3u8[^\s"\']*)', h2, re.I)
                if cloud_match:
                    stream_url = cloud_match.group(1).replace("\\/", "/")
                    log("resolve_voe: Found cloudwindow-route.com after redirect: {}".format(stream_url[:80]))
                    return stream_url

                m3u8_match = find_m3u8(h2) or find_mp4(h2)
                if m3u8_match and not _is_placeholder_media_url(m3u8_match):
                    log("resolve_voe: Found stream after redirect: {}".format(m3u8_match[:80]))
                    return m3u8_match

                for txt in _unpack_all(h2):
                    cloud_match = re.search(r'(https?://[^\s"\']+\.cloudwindow-route\.com[^\s"\']+\.m3u8[^\s"\']*)', txt, re.I)
                    if cloud_match:
                        stream_url = cloud_match.group(1).replace("\\/", "/")
                        log("resolve_voe: Found cloudwindow-route.com in redirect unpacked JS: {}".format(stream_url[:80]))
                        return stream_url

        for embed_url in extract_iframes(html, js_redirect or url):
            if not embed_url.startswith('http') or 'voe.sx' in embed_url:
                continue
            log("resolve_voe: Found wrapped embed iframe: {}".format(embed_url))
            result = resolve_host(embed_url)
            if result and not _is_placeholder_media_url(result):
                return result

        video_match = re.search(r'<video[^>]+src=["\']([^"\']+\.m3u8[^"\']*)["\']', html, re.I)
        if video_match:
            stream_url = video_match.group(1)
            log("resolve_voe: Found video element stream: {}".format(stream_url[:80]))
            return stream_url

        direct = find_m3u8(html) or find_mp4(html)
        if direct and not _is_placeholder_media_url(direct):
            return direct

        return None
    except Exception as e:
        log("resolve_voe error: {}".format(e))
        return None


def _quality_bucket(width, height, bandwidth=0):
    """[PATCH 99] Standard label for a rendition. WIDTH first: letterboxed movies have odd heights
    (1036, 692, 460) but a normalised width (1920, 1336, 854)."""
    try:
        w, h, bw = int(width or 0), int(height or 0), int(bandwidth or 0)
    except (TypeError, ValueError):
        w = h = bw = 0
    if w or h:
        if w >= 3200 or h >= 2000:
            return "2160p"
        if w >= 1800 or h >= 1000:
            return "1080p"
        if w >= 1200 or h >= 680:
            return "720p"
        if w >= 800 or h >= 440:
            return "480p"
        if w >= 600 or h >= 320:
            return "360p"
        return "240p"
    if bw >= 8000000:
        return "2160p"
    if bw >= 4000000:
        return "1080p"
    if bw >= 1500000:
        return "720p"
    if bw >= 700000:
        return "480p"
    if bw > 0:
        return "Quality {}k".format(max(1, bw // 1000))
    return "Auto"


def _parse_hls_master_variants(playlist_url, body):
    """
    Parse an HLS master playlist's #EXT-X-STREAM-INF lines into (label, absolute_url) tuples,
    best quality FIRST. [PATCH 99] The old fixed label table sorted odd heights ("1036p") last.
    """
    if not body or "#EXT-X-STREAM-INF" not in body:
        return []
    entries = []
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        target = ""
        for j in range(i + 1, len(lines)):
            candidate = lines[j].strip()
            if candidate and not candidate.startswith("#"):
                target = candidate
                break
        if not target:
            continue
        if target.startswith("//"):
            target = "https:" + target
        elif not target.startswith("http"):
            target = urljoin(playlist_url, target)
        res_m = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
        bw_m = re.search(r'BANDWIDTH=(\d+)', line)
        w = int(res_m.group(1)) if res_m else 0
        h = int(res_m.group(2)) if res_m else 0
        bw = int(bw_m.group(1)) if bw_m else 0
        if w or h:
            label = _quality_bucket(w, h, bw)
        elif bw:
            label = "{}kbps".format(bw // 1000)
        else:
            label = "Quality {}".format(len(entries) + 1)
        entries.append((w, h, bw, label, _correct_stream_url(target)))
    entries.sort(key=lambda e: (e[0], e[1], e[2]), reverse=True)
    counts = {}
    for e in entries:
        counts[e[3]] = counts.get(e[3], 0) + 1
    out = []
    for w, h, bw, label, url in entries:
        if counts[label] > 1:
            label = "{} ({}k)".format(label, bw // 1000)
        out.append((label, url))
    return out


def resolve_streamruby(url):
    try:
        log("resolve_streamruby: Processing {}...".format(url[:100]))

        url = url.split('|')[0].strip()

        if '.m3u8' in url and 't=' in url and 's=' in url:
            log("resolve_streamruby: Direct stream URL with tokens")
            return _correct_stream_url(url)

        referers = [
            "https://eg.megamax.cam/",      # [PATCH 99] what the real browser sent
            "https://egydead.live/",
            "https://tv10.egydead.live/",
            "https://www.egydead.live/",
            "https://egydead.com/",
            "https://tv.egydead.live/",
        ]

        def _maybe_expand_master(stream_url, referer_for_fetch):
            """If stream_url is an HLS master (multi-variant) playlist,
            fetch and parse it, stash the real variants in the
            thread-local so get_last_quality_variants() works downstream,
            and return the single best-quality variant URL instead of
            the master URL itself."""
            try:
                body, _ = fetch(stream_url, referer=referer_for_fetch)
            except Exception:
                body = None
            if not body or "#EXT-X-STREAM-INF" not in body:
                return stream_url
            variants = _parse_hls_master_variants(stream_url, body)
            if not variants:
                return stream_url
            _quality_tls.variants = [u for _, u in variants]
            log("resolve_streamruby: expanded master playlist into {} variant(s)".format(len(variants)))
            return variants[0][1]

        for referer in referers:
            try:
                log("resolve_streamruby: Trying with referer: {}".format(referer))

                headers = {
                    "Referer": referer,
                    "Origin": referer.rstrip('/'),
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "cross-site",
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                }

                html, final_url = fetch(url, referer=referer, extra_headers=headers)

                if not html:
                    log("resolve_streamruby: No HTML received")
                    continue

                streamruby_patterns = [
                    # FIX: real master URLs look like "..._,l,n,h,o,.urlset/master.m3u8"
                    # (leading AND trailing comma around the quality list) - the old
                    # pattern required a bare l/n/h/o immediately after '_' with no
                    # leading comma, so it could never match real traffic.
                    r'(https?://[^\s"\']+streamruby\.net[^\s"\']+_,(?:l,|n,|h,|o,)*\.urlset/[^\s"\']+\.m3u8[^\s"\']*)',
                    r'(https?://[^\s"\']+\.streamruby\.net[^\s"\']+\.m3u8[^\s"\']*)',
                    r'(https?://[^\s"\']+\.streamruby\.net[^\s"\']+\.txt[^\s"\']*)',
                ]

                for pattern in streamruby_patterns:
                    matches = re.findall(pattern, html, re.I)
                    for stream_url in matches:
                        stream_url = stream_url.replace("\\/", "/").replace("&amp;", "&").strip()
                        if _is_placeholder_media_url(stream_url):
                            continue
                        stream_url = _correct_stream_url(stream_url)
                        if "t=" in stream_url and "s=" in stream_url:
                            log("resolve_streamruby: Found stream URL with tokens: {}...".format(stream_url[:100]))
                            return _maybe_expand_master(stream_url, referer)

                patterns = [
                    r'"(https?://[^\s"\']+\.(?:m3u8|txt|woff2)[^\s"\']*)"',
                    r"'(https?://[^\s\"']+\.(?:m3u8|txt|woff2)[^\s\"']*)'",
                    r'(https?://[^\s"\'<>]+\.(?:m3u8|txt|woff2)[^\s"\'<>]*)',
                    r'file:\s*["\']([^"\']+\.(?:m3u8|txt|woff2)[^"\']*)["\']',
                    r'source:\s*["\']([^"\']+\.(?:m3u8|txt|woff2)[^"\']*)["\']',
                    r'hls\.loadSource\(["\']([^"\']+)["\']',
                    r'"url"\s*:\s*"([^"]+\.(?:m3u8|txt|woff2)[^"]*)"',
                ]

                for pattern in patterns:
                    matches = re.findall(pattern, html, re.I)
                    for stream_url in matches:
                        stream_url = stream_url.replace("\\/", "/").replace("&amp;", "&").strip()
                        if _is_placeholder_media_url(stream_url):
                            continue
                        stream_url = _correct_stream_url(stream_url)
                        if "t=" in stream_url and "s=" in stream_url:
                            log("resolve_streamruby: Found stream URL: {}...".format(stream_url[:100]))
                            return _maybe_expand_master(stream_url, referer)

                for txt in _unpack_all(html):
                    for pattern in patterns:
                        matches = re.findall(pattern, txt, re.I)
                        for stream_url in matches:
                            stream_url = stream_url.replace("\\/", "/").replace("&amp;", "&").strip()
                            if _is_placeholder_media_url(stream_url):
                                continue
                            stream_url = _correct_stream_url(stream_url)
                            if "t=" in stream_url and "s=" in stream_url:
                                log("resolve_streamruby: Found stream URL in unpacked JS: {}...".format(stream_url[:100]))
                                return _maybe_expand_master(stream_url, referer)

            except Exception as e:
                log("resolve_streamruby: Error with referer {}: {}".format(referer, e))
                continue

        log("resolve_streamruby: No stream found, trying to construct from original URL")
        if 'streamruby.net' in url and 'master.m3u8' in url:
            variant_url = url.replace('_,l,n,h,o,.urlset/', '_o/')
            if variant_url != url:
                log("resolve_streamruby: Constructed variant URL: {}".format(variant_url[:100]))
                return _correct_stream_url(variant_url)

        log("resolve_streamruby: No stream URL found")
        return None

    except Exception as e:
        log("resolve_streamruby error: {}".format(e))
        return None


def _resolve_with_retry(resolver_func, url, max_retries=3, delay=2):
    """
    Generic retry helper for resolvers.
    """
    for attempt in range(max_retries):
        try:
            log("_resolve_with_retry: Trying {} (attempt {}/{})".format(
                resolver_func.__name__, attempt + 1, max_retries))
            result = resolver_func(url)
            if result:
                log("_resolve_with_retry: {} succeeded".format(resolver_func.__name__))
                return result
            if attempt < max_retries - 1:
                log("_resolve_with_retry: {} failed, retrying in {}s".format(
                    resolver_func.__name__, delay))
                time.sleep(delay)
        except Exception as e:
            log("_resolve_with_retry: {} error: {}".format(resolver_func.__name__, e))
            if attempt < max_retries - 1:
                time.sleep(delay)
    return None


def _find_js_redirect(html):
    if not html:
        return None
    m = re.search(r'(?:top\.|window\.)?location(?:\.href)?\s*(?:=|\.replace\()\s*["\']([^"\']+)["\']', html, re.I)
    if not m:
        return None
    url = m.group(1).replace("\\/", "/")
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http"):
        return url
    return None


def _hanerix_extract(html):
    if not html:
        return None
    res = find_m3u8(html) or find_mp4(html)
    if res:
        return res
    all_variants = []
    best = None
    for txt in _unpack_all(html):
        m3u8s = find_m3u8_all(txt)
        mp4s = find_mp4_all(txt)
        found = m3u8s or mp4s
        if found:
            if best is None:
                best = found[0]
            for u in found:
                if u not in all_variants:
                    all_variants.append(u)
    if best:
        _quality_tls.variants = all_variants
        return best
    return _best_media_url(html)


def resolve_hanerix_style(url):
    try:
        html, final = fetch(url, referer=url)
        if not html:
            return None
        result = _hanerix_extract(html)
        if result:
            return result
        # [PATCH WC] confirmed directly against a real luluvdo.com/e/
        # capture: a direct fetch lands on the "/d/" landing page (no
        # stream URL anywhere on it), not the real "/e/" player -- the
        # real player is a plain, static <iframe src="..."> already in
        # that landing page's own HTML, often on a DIFFERENT mirror domain
        # (e.g. luluvdo.com's landing page embeds luluvido.com).
        iframe_url = _follow_landing_iframe(html, final or url)
        if iframe_url:
            html2, _ = fetch(iframe_url, referer=final or url)
            result = _hanerix_extract(html2)
            if result:
                return result
    except Exception:
        pass
    return None


def resolve_vinovo(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        m = re.search(r'<video\b[^>]*\bsrc=(["\'])(https?://[^"\']+)\1', html, re.I)
        if m:
            return m.group(2)
    except Exception:
        pass
    return None


def resolve_vidaraa(url):
    """
    [PATCH 101] Vidara: the embed page POSTs {filecode, device} as JSON to /api/stream (same host as the embed)
    and gets back JSON with "streaming_url". A form-encoded body is refused by the API.
    """
    try:
        m = re.search(r'/e/([A-Za-z0-9_-]+)', url)
        file_code = m.group(1) if m else ""
        if not file_code:
            return None
        p = urlparse(url)
        base = "{}://{}".format(p.scheme or "https", p.netloc or "vidaraa.cc")
        headers = {
            "Referer": url,
            "Origin": base,
            "Accept": "*/*",
            "Content-Type": "application/json",
        }
        body, _ = fetch(base + "/api/stream", referer=url, extra_headers=headers,
                        post_data=json.dumps({"filecode": file_code, "device": "web"}))
        if not body:
            return None
        try:
            data = json.loads(body)
        except Exception:
            return _best_media_url(body)
        stream_url = ""
        if isinstance(data, dict):
            stream_url = data.get("streaming_url") or data.get("stream_url") or ""
            if not stream_url and isinstance(data.get("data"), dict):
                stream_url = data["data"].get("streaming_url") or ""
        if stream_url:
            return _correct_stream_url(str(stream_url).replace("\\/", "/"))
        return None
    except Exception as e:
        log("resolve_vidaraa error: {}".format(e))
        return None

def resolve_hgcloud(url):
    """
    [FIX] Previous version looked only for an intermediate "masukestin.com"
    embed/redirect that was never actually confirmed against a real page --
    it never appears on hgcloud.to's actual markup, so this always fell
    through to nothing (confirmed directly: a real captured hgcloud.to/e/...
    page returned "extract_stream FAILED" in production with this logic).

    Confirmed directly against a real captured hgcloud.to/e/{code} page:
    hgcloud.to serves a StreamHG-style player inline -- a P.A.C.K.E.R.-packed
    <script> that, once unpacked, defines
    var links={"hls4":"...","hls3":"...","hls2":"https://.../master.m3u8?t=...
    &s=...&e=...&f=...&srv=..."} and calls jwplayer(...).setup({sources:
    [{file: links.hls4||links.hls3||links.hls2}]}). The unpacked script even
    sets its tracking cookie on domain=".audinifer.com" -- confirming
    hgcloud.to is the same backend/whitelabel network already handled by
    resolve_hanerix_style for audinifer.com/vibuxer.com/morencius.com, so the
    same fetch -> try direct m3u8/mp4 -> unpack packer -> re-scan -> follow
    landing-page iframe approach applies unchanged, and reliably picks the
    always-absolute hls2 URL (the hls3 .txt variant is kept as a
    de-prioritized fallback; the relative hls4 path is correctly skipped
    since it has no scheme).
    """
    return resolve_hanerix_style(url)


def resolve_71stream(url):
    """
    71stream.one embed pages (https://71stream.one/embed/{code}) are a
    Laravel + Inertia.js app (confirmed directly: its own JS bundle is named
    ApplicationLogo-*.js / AuthenticatedLayout-*.js / Dashboard-*.js, standard
    Laravel Breeze/Jetstream component names). The real, signed R2
    object-storage .mp4 URL is never sitting in plain quoted text or inside
    any packed script -- it lives inside the root
    <div id="app" data-page="{&quot;component&quot;:&quot;Video/Embed&quot;,
    &quot;props&quot;:{...,&quot;url&quot;:&quot;https:\\/\\/...
    r2.cloudflarestorage.com\\/...mp4?...&quot;,...}}"> attribute, as
    Inertia's standard HTML-entity-encoded JSON page-props blob. That is
    exactly why resolve_generic_embed's plain-text scan (find_mp4_all /
    _best_media_url, which only match a literal quote character around a
    URL, never "&quot;") always failed here even though the stream link is
    sitting in the page the whole time -- confirmed directly against a real
    captured embed page where the fetch succeeded (37922 bytes) but every
    generic extraction path still came back empty.
    """
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        m = re.search(r'data-page="({.*?})"', html, re.S)
        if not m:
            return None
        raw = html_module.unescape(m.group(1)).replace("\\/", "/")
        stream_url = None
        try:
            data = json.loads(raw)
            props = data.get("props", {}) if isinstance(data, dict) else {}
            stream_url = props.get("url")
        except Exception:
            um = re.search(r'"url"\s*:\s*"(https?://[^"]+)"', raw)
            stream_url = um.group(1) if um else None
        if stream_url and stream_url.startswith("http"):
            return stream_url
        return None
    except Exception as e:
        log("resolve_71stream error: {}".format(e))
        return None


def resolve_vidtube(url):
    try:
        html, _ = fetch(url, referer="https://topcinema.fan/")
        if not html or "restricted for this domain" in html.lower():
            # FIX: different referer, not an identical re-request
            html, _ = fetch(url, referer="https://topcinemaa.top/")
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
    except Exception:
        pass
    return None


def resolve_masukestin(url):
    try:
        html, final_url = fetch(url, referer="https://masukestin.com/")
        if not html:
            return None
        stream_patterns = [
            r'(https?://masukestin\.com/stream/[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)',
            r'(https?://masukestin\.com/stream/[^\s"\']+)',
            r'streamUrl\s*:\s*["\']([^"\']+)["\']',
            r'videoUrl\s*:\s*["\']([^"\']+)["\']',
            r'src:\s*["\']([^"\']+\.(?:m3u8|txt)[^"\']*)["\']',
            r'file:\s*["\']([^"\']+\.(?:m3u8|txt)[^"\']*)["\']',
        ]
        for pattern in stream_patterns:
            match = re.search(pattern, html, re.I)
            if match:
                stream_url = match.group(1)
                stream_url = stream_url.replace("\\/", "/").replace("&amp;", "&")
                if stream_url.startswith("//"):
                    stream_url = "https:" + stream_url
                stream_url = _correct_stream_url(stream_url)
                if ".m3u8" in stream_url:
                    log("masukestin: Found m3u8 stream: {}".format(stream_url[:80]))
                    return stream_url
        script_tags = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S | re.I)
        for script in script_tags:
            for pattern in stream_patterns:
                match = re.search(pattern, script, re.I)
                if match:
                    stream_url = match.group(1)
                    stream_url = _correct_stream_url(stream_url)
                    if ".m3u8" in stream_url:
                        log("masukestin: Found m3u8 in script: {}".format(stream_url[:80]))
                        return stream_url
        b64_patterns = [
            r'atob\(["\']([A-Za-z0-9+/=]+)["\']\)',
            r'Base64\.decode\(["\']([A-Za-z0-9+/=]+)["\']\)',
        ]
        for pattern in b64_patterns:
            for match in re.findall(pattern, html):
                try:
                    decoded = base64.b64decode(match).decode('utf-8')
                    stream_match = re.search(r'(https?://masukestin\.com/stream/[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', decoded)
                    if stream_match:
                        stream_url = _correct_stream_url(stream_match.group(1))
                        log("masukestin: Found m3u8 in base64: {}".format(stream_url[:80]))
                        return stream_url
                except Exception:
                    pass
        log("masukestin: No stream URL found")
        return None
    except Exception as e:
        log("resolve_masukestin error: {}".format(e))
        return None


def resolve_fastvip(url, referer=None):
    try:
        import base64
        from urllib.parse import unquote
        m = re.search(r"mycimafsd=([A-Za-z0-9%_\-]+)", url)
        if m:
            b64 = unquote(m.group(1))
            decoded = base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8")
            if decoded.startswith("http"):
                url = decoded
        
        req_referer = "https://fastvip.space/"
        html, _ = fetch(url, referer=req_referer)
        if not html: return None
        
        texts = [html] + _unpack_all(html)
        for txt in texts:
            stream = find_m3u8(txt) or find_mp4(txt)
            if stream:
                stream = _correct_stream_url(stream)
                
                # If it's an HLS master playlist, fetch and parse it for real variants!
                if ".m3u8" in stream:
                    try:
                        body, _ = fetch(stream, referer=req_referer)
                        if body and "#EXT-X-STREAM-INF" in body:
                            variants = _parse_hls_master_variants(stream, body)
                            if variants:
                                _quality_tls.variants = [u for _, u in variants]
                                log("resolve_fastvip: expanded master playlist into {} variant(s)".format(len(variants)))
                                return variants[0][1] + "|Referer=" + req_referer
                    except Exception:
                        pass
                
                return stream + "|Referer=" + req_referer
    except Exception: pass
    return None

def resolve_streamwish(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_filemoon(url):
    try:
        html, _ = fetch(url, referer="https://filemoon.sx/")
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        for b64 in re.findall(r'atob\(["\']([A-Za-z0-9+/=]{40,})["\']\)', html, re.I):
            try:
                dec = base64.b64decode(b64 + "==").decode("utf-8", "ignore")
                best = _best_media_url(dec)
                if best:
                    return best
            except Exception:
                pass
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_lulustream(url):
    try:
        html, _ = fetch(url, referer="https://1fo1ndyf09qz.tnmr.org",
                        extra_headers={"Origin": "https://lulustream.com"})
        if not html:
            html, _ = fetch(url, referer="https://lulustream.com/")
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_okru(url):
    try:
        m = re.search(r'ok\.ru/(?:video(?:embed)?/|videoembed/)(\d+)', url)
        if not m:
            m = re.search(r'/(\d{10,})', url)
        if not m:
            return None
        video_id = m.group(1)
        api_url = "https://ok.ru/dk/video.playJSON?movieId={}".format(video_id)
        mobile_ua = ("Mozilla/5.0 (iPad; U; CPU OS 3_2 like Mac OS X; en-us) "
                     "AppleWebKit/531.21.10 (KHTML, like Gecko) "
                     "Version/4.0.4 Mobile/7B334b Safari/531.21.10")
        body, _ = fetch(api_url,
                        referer=url,
                        extra_headers={
                            "User-Agent": mobile_ua,
                            "Accept": "application/json",
                        })
        if body:
            try:
                data = json.loads(body)
                hls = data.get("hlsManifestUrl", "")
                if hls:
                    return _correct_stream_url(hls.replace("\\u0026", "&").replace("\\/", "/"))
                for vid in (data.get("videos") or []):
                    u = vid.get("url") or ""
                    if u.startswith("http"):
                        return u.replace("\\u0026", "&").replace("\\/", "/")
            except Exception:
                pass
        embed_url = "https://ok.ru/videoembed/{}".format(video_id)
        html, _ = fetch(embed_url, referer="https://ok.ru/",
                        extra_headers={"User-Agent": mobile_ua})
        if html:
            best = _best_media_url(html)
            if best:
                return best
            m2 = re.search(r'"hlsManifestUrl"\s*:\s*"([^"]+)"', html)
            if m2:
                return _correct_stream_url(m2.group(1).replace("\\u0026", "&").replace("\\/", "/"))
    except Exception:
        pass
    return None

def resolve_send_cm(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        patterns = [
            r'(https?://[^\s"\'<>]+\.send\.cm[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+\.send\.cm[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+/d/[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+/d/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            r'"file"\s*:\s*"([^"]+\.(?:mp4|m3u8)[^"]*)"',
            r'"url"\s*:\s*"([^"]+\.(?:mp4|m3u8)[^"]*)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                return _correct_stream_url(match.group(1))
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None

def resolve_vikingfile(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        patterns = [
            r'(https?://[^\s"\'<>]+\.vikingfile\.com[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+\.vikingfile\.com[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+/f/[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
            r'"file"\s*:\s*"([^"]+\.(?:mp4|m3u8)[^"]*)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                return _correct_stream_url(match.group(1))
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


def resolve_vidguard(url):
    try:
        html, _ = fetch(url, referer="https://vidguard.to/")
        if not html:
            return None
        for pat in [
            r'stream_url\s*=\s*["\']([^"\']+)["\']',
            r'"(?:file|src|url)"\s*:\s*"([^"]+\.(?:m3u8|txt)[^"]*)"',
            r"'(?:file|src|url)'\s*:\s*'([^']+\.(?:m3u8|txt)[^']*)'",
        ]:
            m = re.search(pat, html, re.I)
            if m:
                u = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
                return _correct_stream_url(u)
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        for b64 in re.findall(r'atob\(["\']([A-Za-z0-9+/=]{40,})["\']\)', html, re.I):
            try:
                dec = base64.b64decode(b64 + "==").decode("utf-8", "ignore")
                best = _best_media_url(dec)
                if best:
                    return best
            except Exception:
                pass
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_fastvid(url):
    try:
        html, final_url = fetch(url, referer="https://fastvid.cam/")
        if not html:
            return None
        patterns = [
            r'(https?://[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)',
            r'"(https?://[^"]+\.(?:m3u8|txt)[^"]+)"',
            r"'(https?://[^']+\.(?:m3u8|txt)[^']+)'",
            r'stream/([^\s"\']+\.(?:m3u8|txt))',
        ]
        found_urls = []
        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for match in matches:
                if match.startswith('/'):
                    parsed = urlparse(final_url or url)
                    full_url = "{}://{}{}".format(parsed.scheme, parsed.netloc, match)
                    found_urls.append(full_url)
                elif match.startswith('http'):
                    found_urls.append(match)
        for u in found_urls:
            u = _correct_stream_url(u)
            if 'master.m3u8' in u:
                log("resolve_fastvid: found master.m3u8: {}".format(u))
                return u
        for u in found_urls:
            u = _correct_stream_url(u)
            if 'index-f2' in u:
                log("resolve_fastvid: found 720p stream: {}".format(u))
                return u
        for u in found_urls:
            u = _correct_stream_url(u)
            if 'index-f1' in u:
                log("resolve_fastvid: found 480p stream: {}".format(u))
                return u
        for u in found_urls:
            u = _correct_stream_url(u)
            if '.m3u8' in u:
                log("resolve_fastvid: found m3u8: {}".format(u))
                return u
        jw_pattern = r'file:\s*["\']([^"\']+\.(?:m3u8|txt)[^"\']*)["\']'
        match = re.search(jw_pattern, html, re.I)
        if match:
            stream_url = match.group(1)
            if stream_url.startswith('/'):
                parsed = urlparse(final_url or url)
                stream_url = "{}://{}{}".format(parsed.scheme, parsed.netloc, stream_url)
            stream_url = _correct_stream_url(stream_url)
            log("resolve_fastvid: found JWPlayer stream: {}".format(stream_url))
            return stream_url
        return None
    except Exception as e:
        log("resolve_fastvid error: {}".format(e))
        return None


def resolve_rpmvip(url):
    if '.m3u8' in url or '.txt' in url:
        return _correct_stream_url(url)
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return url if '.m3u8' in url else None


def resolve_upshare(url):
    if '.m3u8' in url or '.txt' in url:
        return _correct_stream_url(url)
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return url if '.m3u8' in url else None


def resolve_cleantechworld(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        if "#EXTM3U" in html:
            return url
        m = re.search(r'(https?://[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', html)
        if m:
            return _correct_stream_url(m.group(1))
        return None
    except Exception as e:
        log("resolve_cleantechworld error: {}".format(e))
        return None


def resolve_scdns(url):
    try:
        if '.m3u8' in url or '.txt' in url:
            log("resolve_scdns: direct stream URL")
            return _correct_stream_url(url)
        html, final_url = fetch(url, referer="https://www.fasel-hd.cam/")
        if html:
            m3u8_patterns = [
                r'(https?://[^\s"\']+\.scdns\.io[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)',
                r'(https?://[^\s"\']+\.c\.scdns\.io[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)',
                r'(https?://master\.[^\s"\']+\.scdns\.io[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)',
                r'(https?://r[0-9]+--[^\s"\']+\.c\.scdns\.io[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)',
            ]
            for pattern in m3u8_patterns:
                matches = re.findall(pattern, html, re.I)
                for stream_url in matches:
                    stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                    if 'hd1080' in stream_url or '1080' in stream_url:
                        log("resolve_scdns: found 1080p stream")
                        return stream_url
                    elif 'hd720' in stream_url or '720' in stream_url:
                        log("resolve_scdns: found 720p stream")
                        return stream_url
            stream = find_m3u8(html)
            if stream:
                log("resolve_scdns: found m3u8 via generic finder")
                return stream
        return None
    except Exception as e:
        log("resolve_scdns error: {}".format(e))
        return None


def resolve_datahowa(url):
    try:
        log("resolve_datahowa: processing {}".format(url[:80]))
        if '.ts' in url:
            base_m3u8 = re.sub(r'/seg_[0-9]+\.ts.*$', '/playlist.m3u8', url)
            if base_m3u8 != url:
                log("resolve_datahowa: converting segment to playlist: {}".format(base_m3u8[:80]))
                return base_m3u8
        if '.m3u8' in url or '.txt' in url:
            return _correct_stream_url(url)
        html, _ = fetch(url, referer="https://faselhd.rip/")
        if html:
            m3u8 = find_m3u8(html)
            if m3u8:
                return m3u8
        return None
    except Exception as e:
        log("resolve_datahowa error: {}".format(e))
        return None


def resolve_downet(url):
    try:
        log("resolve_downet: processing {}".format(url[:80]))
        if '.mp4' in url or '.m3u8' in url or '.txt' in url:
            return _correct_stream_url(url)
        html, _ = fetch(url, referer="https://akwam.com.co/")
        if html:
            mp4 = find_mp4(html) or find_m3u8(html)
            if mp4:
                return mp4
        return None
    except Exception as e:
        log("resolve_downet error: {}".format(e))
        return None


def resolve_tnmr(url):
    try:
        if '.m3u8' in url or '.txt' in url:
            log("resolve_tnmr: direct stream URL, returning as-is")
            return _correct_stream_url(url)
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        m = re.search(r'(https?://[^\s"\']+\.tnmr\.org[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', html)
        if m:
            return _correct_stream_url(m.group(1))
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


def resolve_mxcontent(url):
    try:
        if '.mp4' in url:
            return url
        html, _ = fetch(url, referer="https://wecima.cx/")
        if html:
            return find_mp4(html)
    except Exception:
        return None


def resolve_delucloud(url):
    try:
        if '.m3u8' in url or '.txt' in url:
            log("resolve_delucloud: direct stream URL, returning as-is")
            return _correct_stream_url(url)
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        m = re.search(r'(https?://[^\s"\']+\.delucloud\.xyz[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', html)
        if m:
            return _correct_stream_url(m.group(1))
        return find_m3u8(html)
    except Exception:
        return None

def resolve_savefiles(url):
    try:
        if '.m3u8' in url or '.txt' in url:
            log("resolve_savefiles: direct stream URL, returning as-is")
            return _correct_stream_url(url)

        # savefiles.com/e/{code} never embeds the stream URL statically —
        # the page's xupload.js POSTs to {base}/dl with op=embed... and
        # gets back JSON (sources[0].file) with the real master.m3u8.
        m_code = re.search(r'/e/([A-Za-z0-9]+)', url)
        file_code = m_code.group(1) if m_code else ""
        if file_code:
            try:
                base_m = re.match(r'(https?://[^/]+)', url)
                base = base_m.group(1) if base_m else "https://savefiles.com"
            except Exception:
                base = "https://savefiles.com"

            # Visit the embed page first so the session/CF cookies needed
            # by /dl are present (fetch() shares a persistent cookie jar).
            fetch(url, referer="https://wecima.cx/")

            dl_url = base + "/dl"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": base,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            post_data = {
                "op": "embed",
                "file_code": file_code,
                "auto": "1",
                "referer": "",
            }
            dl_body, _ = fetch(dl_url, referer=url, extra_headers=headers, post_data=post_data)
            if dl_body:
                try:
                    data = json.loads(dl_body)
                    sources = data.get("sources") or []
                    if sources and isinstance(sources, list):
                        stream_url = sources[0].get("file", "")
                        if stream_url:
                            log("resolve_savefiles: got stream via /dl JSON: {}".format(stream_url[:100]))
                            return _correct_stream_url(stream_url)
                except Exception as e:
                    log("resolve_savefiles: /dl JSON parse failed: {}".format(e))
                m2 = re.search(r'(https?://s[0-9]+\.savefiles\.com[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', dl_body)
                if m2:
                    return _correct_stream_url(m2.group(1))

        # Legacy fallback: static-HTML scan.
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        m = re.search(r'(https?://s[0-9]+\.savefiles\.com[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', html)
        if m:
            return _correct_stream_url(m.group(1))
        return find_m3u8(html)
    except Exception:
        return None


def resolve_sprintcdn(url):
    try:
        if '.m3u8' in url or '.txt' in url:
            log("resolve_sprintcdn: direct stream URL, returning as-is")
            return _correct_stream_url(url)
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        return find_m3u8(html)
    except Exception:
        return None


def resolve_aurorafieldnetwork(url):
    try:
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        if '.txt' in url:
            content, _ = fetch(url, referer="https://wecima.cx/")
            if content:
                m = re.search(r'(https?://[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', content)
                if m:
                    return _correct_stream_url(m.group(1))
        return find_m3u8(html)
    except Exception:
        return None


def resolve_abstream(url):
    try:
        html, _ = fetch(url, referer="https://abstream.to/")
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


# ─── n1mwq.org backend (PoW captcha + AES-256-GCM encrypted playback) ──────
#
# [PATCH N1] n1mwq.org is the real player backend that byselapuix.com /
# byseraguci.com landing pages iframe to (see resolve_byselapuix below).
# Fully reverse-engineered, 2026-10-13, from the site's own JS bundles
# (pow-DEJGtdh2.js, videoPagesBundle-Bgi0QmPo.js) and verified against two
# real captured sessions end-to-end -- including a real AES-256-GCM
# decrypt that produced a real, working master.m3u8 CDN URL. Flow:
#
#   1. GET  /api/videos/<id>/embed/settings       -> {captcha_required, ...}
#   2. POST /api/videos/<id>/embed/captcha         -> {pow_nonce, pow_difficulty,
#                                                       pow_token, expires_in}
#      (only if captcha_required)
#   3. solve the PoW (custom hash, NOT real SHA-256 -- see _n1mwq_pow_hash)
#   4. POST /api/videos/<id>/embed/captcha/verify  -> {status, token}
#   5. GET  /api/videos/<id>/embed/playback
#      header X-Captcha-Token: <token from step 4> -> {playback: {...}}
#   6. AES-256-GCM decrypt playback.payload (see _n1mwq_decrypt_playback)
#      -> {"sources": [{url, height, bitrate_kbps, ...}, ...]}
#
# Deliberately NOT implemented: the separate /api/videos/access/challenge +
# /access/attest device-attestation endpoint (ECDSA-signed canvas/audio/
# WebGL/font fingerprint). Read directly from videoPagesBundle-Bgi0QmPo.js:
# every call on this path -- si() (captcha start), oi() (captcha verify),
# Et() (playback) -- treats its `fingerprint` argument as purely optional,
# only adding it to the request body when truthy; a user with fingerprinting
# blocked (or whose browser fails WebCrypto/canvas) still gets `expiresIn`
# back from si() either way. There is no code path on this flow that calls
# attest at all. Replicating attest faithfully would also require rendering
# real canvas text, WebGL params and Web-Audio-synthesized samples --
# nothing an Enigma2/Python box can produce -- so skipping it here is the
# only implementable option, not a guess made to save effort. If the real
# box later gets rejected specifically at /embed/captcha or .../verify (not
# at PoW-solve timeout), that would be the first real evidence attest is
# actually enforced server-side; nothing gathered so far shows that.

_N1MWQ_ORIGIN = "https://n1mwq.org"


def _n1mwq_rotl32(x, n):
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _n1mwq_imul32(a, b):
    return (a * b) & 0xFFFFFFFF


def _n1mwq_mix_round(state):
    """Port of the minified js `ye()` -- one ChaCha-style quarter round
    over state[0..3]."""
    state[0] = (state[0] + state[1]) & 0xFFFFFFFF
    state[3] = _n1mwq_rotl32(state[3] ^ state[0], 16)
    state[2] = (state[2] + state[3]) & 0xFFFFFFFF
    state[1] = _n1mwq_rotl32(state[1] ^ state[2], 12)
    state[0] = (state[0] + state[1]) & 0xFFFFFFFF
    state[3] = _n1mwq_rotl32(state[3] ^ state[0], 8)
    state[2] = (state[2] + state[3]) & 0xFFFFFFFF
    state[1] = _n1mwq_rotl32(state[1] ^ state[2], 7)


def _n1mwq_pow_hash(data_bytes):
    """Port of the minified js `gr()` -- the site's custom PoW hash.
    Seeded with SHA-256's IV words but mixed with ChaCha quarter-rounds,
    NOT real SHA-256. Ported byte-for-byte from pow-DEJGtdh2.js and
    self-verified against a real solved challenge (nonce
    "kkJaZM4KOitHQp8OXdB3MVnC", difficulty 16 -> solution "4078").
    Returns a list of 8 32-bit words."""
    BLOCK = 512
    MASK = BLOCK - 1
    MIX_ROUNDS = 2
    MUL1 = 2654435761
    MUL2 = 2246822519

    e = [1779033703, 3144134277, 1013904242, 2773480762]
    for b in data_bytes:
        e[0] = (e[0] + b) & 0xFFFFFFFF
        e[0] = _n1mwq_rotl32(e[0], 7)
        _n1mwq_mix_round(e)
    for _ in range(8):
        _n1mwq_mix_round(e)

    r = [0] * BLOCK
    for i in range(BLOCK):
        _n1mwq_mix_round(e)
        r[i] = (e[0] ^ e[2]) & 0xFFFFFFFF

    for _ in range(MIX_ROUNDS):
        for s in range(BLOCK):
            a = r[s] & MASK
            c = (r[s] + r[a]) & 0xFFFFFFFF
            c = _n1mwq_rotl32(c, 13)
            c = (c ^ _n1mwq_imul32(r[(s + 1) & MASK], MUL1)) & 0xFFFFFFFF
            r[s] = c
            e[0] = (e[0] ^ c) & 0xFFFFFFFF
            _n1mwq_mix_round(e)

    out = [0] * 8
    chunk = BLOCK // 8
    for i in range(8):
        _n1mwq_mix_round(e)
        s = e[0]
        base = i * chunk
        for c in range(chunk):
            d = r[base + c]
            s = (s + d) & 0xFFFFFFFF
            s = _n1mwq_rotl32(s, 5)
            s = (s ^ _n1mwq_imul32(d, MUL2)) & 0xFFFFFFFF
        out[i] = (s ^ e[2]) & 0xFFFFFFFF
    return out


def _n1mwq_leading_zero_bits(words):
    """Port of `wr()` -- counts leading zero bits across the word array
    using the same Math.clz32 semantics as the page's own JS."""
    total = 0
    for w in words:
        if w == 0:
            total += 32
            continue
        return total + (32 - w.bit_length())
    return total


def _n1mwq_solve_pow(nonce, difficulty, timeout_ms=20000):
    """Port of `Er()`/`no()` -- brute-forces an integer suffix appended to
    "<nonce>:" until the custom hash's leading-zero-bit count meets
    `difficulty`. Returns the solution as a string, or None on timeout."""
    if not nonce:
        return None
    if not difficulty or difficulty <= 0:
        return "0"
    prefix = (nonce + ":").encode("ascii", errors="ignore")
    start = time.time()
    s = 0
    while True:
        for _ in range(2048):
            data = prefix + str(s).encode("ascii")
            if _n1mwq_leading_zero_bits(_n1mwq_pow_hash(data)) >= difficulty:
                return str(s)
            s += 1
        if (time.time() - start) * 1000 > timeout_ms:
            log("n1mwq: PoW not solved within {} ms (difficulty {})".format(
                timeout_ms, difficulty))
            return None


def _n1mwq_b64url_decode(s):
    """Base64url decode, as used throughout the page's own `Xt()`."""
    if not s:
        return b""
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _n1mwq_select_key_parts(key_parts, version):
    """Port of `ws()`/`Ea()`/`Qa()`: the real AES key is just TWO of the
    ~30 decoy `key_parts` entries, selected by 1-indexed positions
    [version, 31-version] (version is itself a small int, "1".."20",
    also present in the response). Falls back to concatenating every
    part if the indices don't validate, matching the page's own
    fallback exactly. Confirmed directly from videoPagesBundle-*.js and
    against a real response (version="5" -> indices 5, 26)."""
    n = len(key_parts)
    try:
        v = int(version)
    except (TypeError, ValueError):
        return key_parts
    if not (1 <= v <= 20):
        return key_parts
    a, i = v, 31 - v
    if a < 1 or i < 1 or a > n or i > n:
        return key_parts
    return [key_parts[a - 1], key_parts[i - 1]]


def _n1mwq_aes_gcm_decrypt(key, iv, ciphertext_and_tag):
    if _AESGCM_BACKEND == "cryptography":
        return _CRYPTOGRAPHY_AESGCM(key).decrypt(iv, ciphertext_and_tag, None)
    if _AESGCM_BACKEND == "pycryptodome":
        ct, tag = ciphertext_and_tag[:-16], ciphertext_and_tag[-16:]
        cipher = _PYCRYPTODOME_AES.new(key, _PYCRYPTODOME_AES.MODE_GCM, nonce=iv)
        return cipher.decrypt_and_verify(ct, tag)
    if _AESGCM_BACKEND == "pure-python":
        # [PATCH N2] confirmed necessary: neither of the above can be
        # pip-installed on the real target device (no C compiler), so
        # this dependency-free fallback is what actually runs there.
        return _PURE_AES_GCM.aes256_gcm_decrypt(key, iv, ciphertext_and_tag)
    raise RuntimeError(
        "no AES-GCM backend available at all -- this should be "
        "unreachable (pure-python fallback ships with the plugin)")


def _n1mwq_decrypt_playback(playback):
    """Port of `La()`/`En()`: AES-256-GCM-decrypt the playback blob into
    the real {"sources": [...]} JSON. Confirmed working end-to-end against
    a real response (key_parts[4]+key_parts[25] for version="5" -> a real,
    playable master.m3u8 URL)."""
    key_parts = playback.get("key_parts") or []
    if not key_parts:
        raise ValueError("playback response has no key_parts")
    chosen = _n1mwq_select_key_parts(key_parts, playback.get("version"))
    key = b"".join(_n1mwq_b64url_decode(p) for p in chosen if p)
    iv = _n1mwq_b64url_decode(playback.get("iv", ""))
    payload = _n1mwq_b64url_decode(playback.get("payload", ""))
    plaintext = _n1mwq_aes_gcm_decrypt(key, iv, payload)
    return json.loads(plaintext.decode("utf-8"))


def _n1mwq_video_id(url):
    # Confirmed path shape: https://n1mwq.org/<5-char share code>/<video id>
    m = re.search(r'n1mwq\.org/[^/\s"\'<>]+/([A-Za-z0-9_-]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'n1mwq\.org/([A-Za-z0-9_-]+)/?(?:[?#].*)?$', url)
    return m.group(1) if m else None


def resolve_n1mwq(url, referer=None):
    try:
        video_id = _n1mwq_video_id(url)
        if not video_id:
            log("n1mwq: could not extract video id from {}".format(url))
            return None
        page_referer = referer or url
        base = "{}/api/videos/{}/embed/".format(_N1MWQ_ORIGIN, video_id)

        settings = fetch_json(base + "settings", referer=page_referer)
        captcha_required = bool(settings and settings.get("captcha_required"))

        captcha_token = None
        if captcha_required:
            start = fetch_json(
                base + "captcha", referer=page_referer,
                post_data="{}",
                extra_headers={"Content-Type": "application/json"})
            if not start or not start.get("pow_token"):
                log("n1mwq: no captcha challenge in response for {}".format(video_id))
                return None
            nonce = start.get("pow_nonce")
            difficulty = start.get("pow_difficulty") or 0
            pow_token = start.get("pow_token")
            expires_in = start.get("expires_in") or 20
            timeout_ms = min(20000, max(4000, (expires_in - 3) * 1000))

            solution = _n1mwq_solve_pow(nonce, difficulty, timeout_ms)
            if solution is None:
                return None

            verify = fetch_json(
                base + "captcha/verify", referer=page_referer,
                post_data=json.dumps({"pow_token": pow_token, "solution": solution}),
                extra_headers={"Content-Type": "application/json"})
            if not verify or verify.get("status") != "ok" or not verify.get("token"):
                log("n1mwq: captcha verify failed for {}: {}".format(video_id, verify))
                return None
            captcha_token = verify.get("token")

        pb_headers = {"X-Captcha-Token": captcha_token} if captcha_token else None
        playback_resp = fetch_json(base + "playback", referer=page_referer,
                                    extra_headers=pb_headers)
        playback = (playback_resp or {}).get("playback")
        if not playback:
            log("n1mwq: no playback payload for {}".format(video_id))
            return None

        try:
            decrypted = _n1mwq_decrypt_playback(playback)
        except Exception as e:
            log("n1mwq: decrypt failed for {}: {}".format(video_id, e))
            return None

        sources = decrypted.get("sources") or []
        if not sources:
            return None
        best = max(sources, key=lambda s: (s.get("height") or 0, s.get("bitrate_kbps") or 0))
        return best.get("url")
    except Exception as e:
        log("n1mwq: resolver error for {}: {}".format(url, e))
        return None


# ─── VidNest (new.vidnest.fun / vidnest.fun) resolver ──────────────────────
#
# [FIX] Added for the Cineby extractor, which embeds vidnest.fun directly
# (confirmed from a real browser capture of a Cineby movie page). vidnest's
# own page calls new.vidnest.fun/<provider>/movie/<tmdb_id> and gets back
# an opaque {"d": "<ciphertext>", "n": "<nonce>", "v": 2} blob -- the user
# pulled the exact client-side chunk that defines this (confirmed via
# DevTools Sources search for "decryptCipherResponse"), so this is a
# faithful port, not a guess:
#
#   key    = SHA-256(f"{nonce}|{SECRET}")           (nonce = response's "n")
#   raw    = base64url_decode(ciphertext)             (ciphertext = response's "d")
#   iv     = raw[:12]
#   ct+tag = raw[12:]                                  (GCM tag is the last 16 bytes)
#   plaintext = AES-256-GCM-decrypt(key, iv, ct+tag)  (no AAD)
#   result = JSON.parse(plaintext)
#
# SECRET is the literal static string baked into vidnest's own bundle
# (not a per-session secret -- the only per-request variable is the
# nonce). Verified end-to-end against a real captured response (TMDB id
# 969681, "Spider-Man: Brand New Day"): this exact algorithm reproduces
# the identical plaintext {"headers": {...}, "url": "https://pwcloud...
# master.m3u8"} that the real browser's own JS produced for that same
# response, byte for byte.
#
# vidnest proxies TWELVE providers this way (all through new.vidnest.fun,
# all through the same decrypt function). "catflix" (-> /yflix/movie) is
# the default server vidnest.fun's own UI opens with and the one Cineby's
# embed actually uses, so it's the primary/default path. The other 11 were
# originally left unimplemented ("future resolver if a site needs one") --
# a second, larger JS bundle the user pulled from vidnest.fun (the actual
# movie-player component, not just the crypto helper) turned out to
# contain the FULL per-provider response-handling logic for all twelve,
# so they're implemented below too, each matched against that real code:
#
#   catflix/filxer  -> {"url": ...}                         (direct)
#   beta            -> {"status":"success","url":...}       (direct)
#   ophim           -> {"sources":[{"url","quality","type"}]}
#   gama/lamda      -> {"streams":[{"url","quality"/"language","type"}]}
#   delta           -> {"streams":[...]}, keep only language=="hindi"
#   prime           -> {"streams":[{"url","type"}]}, headers forced to
#                      {Referer/Origin: https://vidora.stream/} (static,
#                      from the JS -- not per-response)
#   zeta/sigma      -> {"streams":[{"url","headers":{...}}]}; sigma also
#                      filters to type=="hls" and language=="GS-25"
#   hexa            -> {"data":{"stream":{"playlist":...}},"headers":{...}}
#   alfa            -> {"status":"success","streams":[{"url","language"}]},
#                      keep only language in (original, multi)
#
# IMPORTANT caveat: only catflix was ever seen with a REAL captured
# ciphertext/plaintext pair (verified byte-for-byte earlier). The other 11
# are implemented straight from the verified decrypt algorithm + this
# verified response-handling JS, but have NOT been exercised against a
# real device capture -- if one behaves differently in practice, a device
# log will show it and the fix is narrow (this function only).
# The real browser additionally proxies prime/zeta/sigma/hexa through
# rotating workers.dev URLs purely to dodge browser CORS and inject
# per-request headers client-side -- our plugin's player isn't a browser
# and already has a working "<url>|Header=Value&..." convention (used by
# catflix today), so those are passed straight through with their real
# headers instead of reimplementing that proxy hop.
_VIDNEST_BASE = "https://new.vidnest.fun"
_VIDNEST_SECRET = "F9k2mR8vL4qP7wY1zN0fG3bS6tH5jC8d_x4R7vN2mP9qL1"

# provider key -> (new.vidnest.fun path prefix, response "kind")
_VIDNEST_PROVIDERS = {
    "catflix": ("/yflix/movie", "catflix"),
    "filxer":  ("/rogflix/movie", "filxer"),
    "gama":    ("/vidzee/movie", "gama"),
    "lamda":   ("/ophim/movie", "lamda"),
    "beta":    ("/flixhq/movie", "beta"),
    "alfa":    ("/onehd/movie", "alfa"),
    "prime":   ("/rpmvid/movie", "prime"),
    "zeta":    ("/nextgencloudfabric/movie", "zeta"),
    "ophim":   ("/klikxxi/movie", "ophim"),
    "sigma":   ("/hollymoviehd/movie", "sigma"),
    "hexa":    ("/vidlink/movie", "hexa"),
    "delta":   ("/allmovies/movie", "delta"),
}


def _vidnest_headers_suffix(stream_url, headers):
    # [FIX] previously forwarded ONLY Referer and silently dropped every
    # other header -- e.g. the Origin that "prime" (vidora.stream) needs,
    # and whatever per-stream headers zeta/sigma/hexa hand back. Both the
    # player (_build_remote_play_candidates) and the proxy accept any
    # number of "Key=Value" pairs joined with "&" after the pipe, so pass
    # them all through. Referer first, to keep the old URL shape for the
    # common catflix case.
    if not isinstance(headers, dict) or not headers:
        return stream_url
    parts = []
    seen = set()
    ordered = sorted(headers.items(), key=lambda kv: str(kv[0]).lower() != "referer")
    for k, v in ordered:
        k = str(k or "").strip()
        if not k or v is None or str(v).strip() == "":
            continue
        canon = "-".join(w[:1].upper() + w[1:].lower() for w in k.split("-"))
        if canon.lower() in seen:
            continue
        seen.add(canon.lower())
        parts.append("{}={}".format(canon, str(v).strip()))
    if not parts:
        return stream_url
    return "{}|{}".format(stream_url, "&".join(parts))


def _vidnest_pick_from_decrypted(kind, decrypted):
    """Returns (stream_url, headers_dict) or (None, None) if this
    provider's decrypted payload doesn't have a usable stream, per the
    real response-handling logic for each `kind` (see module comment)."""
    if not isinstance(decrypted, dict):
        return None, None

    if kind in ("catflix", "filxer"):
        # catflix's decrypted payload carries its own per-response headers
        # (confirmed from the real captured example: {"headers":{"Referer":
        # "https://pwcloud.animanga.fun/"}, "url": "...master.m3u8"});
        # filxer's JS only ever reads `.url`, no headers field used.
        return decrypted.get("url"), (decrypted.get("headers") if kind == "catflix" else None)

    if kind == "beta":
        if decrypted.get("status") != "success" or not decrypted.get("url"):
            return None, None
        return decrypted.get("url"), None

    if kind == "ophim":
        for s in (decrypted.get("sources") or []):
            if isinstance(s, dict) and s.get("url"):
                return s["url"], None
        return None, None

    if kind in ("gama", "lamda"):
        for s in (decrypted.get("streams") or []):
            if isinstance(s, dict) and s.get("url"):
                return s["url"], None
        return None, None

    if kind == "delta":
        for s in (decrypted.get("streams") or []):
            if isinstance(s, dict) and s.get("url") and str(s.get("language", "")).lower() == "hindi":
                return s["url"], None
        return None, None

    if kind == "prime":
        for s in (decrypted.get("streams") or []):
            if isinstance(s, dict) and s.get("url"):
                return s["url"], {"Referer": "https://vidora.stream/", "Origin": "https://vidora.stream"}
        return None, None

    if kind == "zeta":
        for s in (decrypted.get("streams") or []):
            if isinstance(s, dict) and s.get("url"):
                return s["url"], (s.get("headers") or None)
        return None, None

    if kind == "sigma":
        for s in (decrypted.get("streams") or []):
            if isinstance(s, dict) and s.get("url") and s.get("type") == "hls" and s.get("language") == "GS-25":
                return s["url"], (s.get("headers") or None)
        return None, None

    if kind == "hexa":
        stream = ((decrypted.get("data") or {}).get("stream") or {})
        playlist = stream.get("playlist")
        if not playlist:
            return None, None
        return playlist, (decrypted.get("headers") or None)

    if kind == "alfa":
        if decrypted.get("status") != "success":
            return None, None
        for s in (decrypted.get("streams") or []):
            if isinstance(s, dict) and s.get("url") and str(s.get("language", "")).lower() in ("original", "multi"):
                return s["url"], None
        return None, None

    return None, None


def _vidnest_nonce():
    """Port of withStreamNonceUrl()'s own nonce generator: 16 random bytes,
    base64url with the '=' padding stripped (matches btoa().replace(...)
    in the client JS, modulo the raw-byte-string vs base64url framing --
    this produces the same alphabet/length nonce the real client sends)."""
    raw = bytes(random.getrandbits(8) for _ in range(16))
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _vidnest_decrypt(d, n):
    """Port of secureDecrypt()/decryptCipherResponse(): see module comment
    above for the algorithm. Reuses the n1mwq AES-GCM backend dispatcher
    (cryptography / pycryptodome / pure-python fallback) -- it's generic
    AES-256-GCM, nothing n1mwq-specific about the math itself."""
    key = hashlib.sha256("{}|{}".format(n, _VIDNEST_SECRET).encode("utf-8")).digest()
    raw = _n1mwq_b64url_decode(d)
    iv, ct_and_tag = raw[:12], raw[12:]
    plaintext = _n1mwq_aes_gcm_decrypt(key, iv, ct_and_tag)
    text = plaintext.decode("utf-8")
    try:
        return json.loads(text)
    except Exception:
        return text


def resolve_vidnest(url, referer=None):
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        provider = (qs.get("provider") or ["catflix"])[0].strip().lower()
        if provider not in _VIDNEST_PROVIDERS:
            log("resolve_vidnest: unknown provider '{}', falling back to catflix".format(provider))
            provider = "catflix"
        backend_path, kind = _VIDNEST_PROVIDERS[provider]

        m = re.search(r'/(movie|tv)/(\d+)(?:/(\d+)/(\d+))?', parsed.path)
        if not m:
            log("resolve_vidnest: couldn't parse media id from {}".format(url))
            return None
        media_type, tmdb_id, season, episode = m.groups()
        if media_type == "tv" and season and episode:
            if provider != "catflix":
                # [NOTE] TV was only ever evidenced (as an extrapolation,
                # not a real capture) for catflix/yflix; no evidence at all
                # for the other 11 providers' TV path/shape, so don't guess.
                log("resolve_vidnest: TV not supported for provider '{}' (unconfirmed)".format(provider))
                return None
            # [NOTE] unconfirmed -- no TV capture was available, only the
            # movie path. Mirrors the movie shape (the one piece of real
            # evidence) on the reasonable assumption vidnest keeps the same
            # /<season>/<episode> suffix convention its own site URLs use.
            api_path = "/yflix/tv/{}/{}/{}".format(tmdb_id, season, episode)
        else:
            api_path = "{}/{}".format(backend_path, tmdb_id)

        nonce = _vidnest_nonce()
        api_url = "{}{}?_n={}".format(_VIDNEST_BASE, api_path, nonce)
        body, _ = fetch(api_url, referer="https://vidnest.fun/")
        if not body:
            log("resolve_vidnest: empty response for {}".format(api_url))
            return None
        try:
            data = json.loads(body)
        except Exception:
            # [NOTE] confirmed real-device: hexa (vidlink) and alfa (onehd)
            # both hit this on a real run even after the automatic 502
            # retry already succeeded (curl_cffi OK) -- the upstream
            # backend itself returned something that isn't JSON (an error
            # page, a challenge page, etc.), not a shape our decrypt logic
            # misunderstood. Log a short preview so a future failure like
            # this is diagnosable from the log alone instead of just
            # "non-JSON response".
            preview = body[:200].replace("\n", " ") if isinstance(body, str) else repr(body)[:200]
            log("resolve_vidnest: non-JSON response for {} -- body preview: {}".format(api_url, preview))
            return None

        if not (data and data.get("v") == 2 and data.get("d")):
            log("resolve_vidnest: unexpected response shape (no v2 cipher payload)")
            return None

        decrypted = _vidnest_decrypt(data["d"], data.get("n") or nonce)
        stream_url, headers = _vidnest_pick_from_decrypted(kind, decrypted)
        if not stream_url:
            log("resolve_vidnest: provider '{}' decrypted payload has no usable stream".format(provider))
            return None

        stream_url = _correct_stream_url(stream_url)
        return _vidnest_headers_suffix(stream_url, headers)
    except Exception as e:
        log("resolve_vidnest: error for {}: {}".format(url, e))
        return None


def resolve_byselapuix(url):
    try:
        html, final = fetch(url, referer="https://byselapuix.com/")
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        result = find_m3u8(html) or find_mp4(html)
        if result:
            return result
        # [PATCH WC] confirmed directly against a real byselapuix.com/e/
        # capture: a direct fetch lands on the "/d/" landing page (no
        # stream URL anywhere on it), not the real player -- the real
        # player is a plain, static <iframe src="..."> already in that
        # landing page's own HTML, on a completely different, unrelated
        # mirror domain (confirmed: n1mwq.org).
        iframe_url = _follow_landing_iframe(html, final or url)
        if iframe_url:
            # [PATCH N1] n1mwq.org never puts the stream in static HTML --
            # it's a JS SPA that needs the full PoW-captcha/decrypt flow.
            if "n1mwq.org" in urlparse(iframe_url).netloc.lower():
                result = resolve_n1mwq(iframe_url, referer=final or url)
                if result:
                    return result
            html2, _ = fetch(iframe_url, referer=final or "https://byselapuix.com/")
            if html2:
                best = _best_media_url(html2)
                if best:
                    return best
                for txt in _unpack_all(html2):
                    best = _best_media_url(txt)
                    if best:
                        return best
                result = find_m3u8(html2) or find_mp4(html2)
                if result:
                    return result
        return None
    except Exception:
        return None


def resolve_byse_any(url, referer=None):
    """[2026-10-21] any other "byse*" vanity domain (TopCinema: byseqekaho.com).
    Byse mirrors use one of two known backends: the byselapuix landing page
    (-> n1mwq.org player) or StreamWish's -- try both."""
    r = resolve_byselapuix(url)
    if r:
        return r
    try:
        return resolve_streamwish(url)
    except Exception:
        return None


def resolve_azrak_mycima(url):
    """
    [PATCH 121] azrak.mycima.cv's download-preparation page. Confirmed against a real capture:
    the page's own JS polls "?check_status=1" every second until it succeeds, then navigates
    to "?start_download=1" for the real file. Real network log ground truth: both requests
    returned HTTP 200 in a real successful session. This mirrors that exact sequence, with a
    bounded number of retries rather than polling forever.
    """
    try:
        base_url = url.split("?")[0]
        check_url = base_url + "?check_status=1"
        max_attempts = 10
        for attempt in range(max_attempts):
            html, final_url = fetch(check_url, referer=url)
            if html:
                log("azrak.mycima: check_status ready after {} attempt(s)".format(attempt + 1))
                return base_url + "?start_download=1"
            time.sleep(1)
        log("azrak.mycima: check_status never became ready after {} attempts".format(max_attempts))
        return None
    except Exception as e:
        log("azrak.mycima: error: {}".format(e))
        return None


def resolve_egybest_download_gateway(url):
    """
    [PATCH E3] egybest's "/download/?url=BASE64" gateway page. Confirmed against a real
    capture: the real link sits in a data-url attribute as plain base64, present in the raw
    HTML from the first load regardless of the page's own 10-second UI countdown. Checked
    narrowly against the /download/ + url= shape so this never claims an unrelated
    egybests.live URL.
    """
    if "/download/" not in url or "url=" not in url:
        return None
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        m = re.search(r'id=["\']btn["\'][^>]*data-url=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'data-url=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*download-btn', html, re.I)
        if not m:
            return None
        payload = m.group(1)
        pad = (-len(payload)) % 4
        decoded = base64.b64decode(payload + "=" * pad).decode("utf-8")
        if decoded.startswith("http://") or decoded.startswith("https://"):
            return decoded
        return None
    except Exception:
        return None


def resolve_dhcplay(url):
    return resolve_doodstream(url)


def resolve_go_akwam(url):
    try:
        html, final_url = fetch(url, referer="https://akwam.com.co/")
        if not html:
            return None
        source_match = re.search(r'<source[^>]+src="([^"]+\.(?:mp4|m3u8|txt)[^"]*)"', html, re.I)
        if source_match:
            return _correct_stream_url(source_match.group(1))
        downet_match = re.search(r'(https?://s\d+\.downet\.net[^\s"\']+\.(?:mp4|m3u8|txt)[^\s"\']*)', html, re.I)
        if downet_match:
            return _correct_stream_url(downet_match.group(1))
        meta_match = re.search(r'<meta[^>]+http-equiv="refresh"[^>]+content="\d+;\s*url=([^"]+)"', html, re.I)
        if meta_match:
            redirect_url = meta_match.group(1)
            if redirect_url.startswith("//"):
                redirect_url = "https:" + redirect_url
            return resolve_host(redirect_url, referer=url)
        iframe_match = re.search(r'<iframe[^>]+src="([^"]+)"[^>]*>', html, re.I)
        if iframe_match:
            iframe_url = iframe_match.group(1)
            if iframe_url.startswith("//"):
                iframe_url = "https:" + iframe_url
            return resolve_host(iframe_url, referer=url)
        return None
    except Exception as e:
        log("resolve_go_akwam error: {}".format(e))
        return None


def resolve_savefiles_akwam(url):
    try:
        html, _ = fetch(url, referer="https://savefiles.com/")
        if not html:
            return None
        m = re.search(r'(https?://s[0-9]+\.savefiles\.com[^\s"\']+\.(?:m3u8|txt)[^\s"\']*)', html)
        if m:
            return _correct_stream_url(m.group(1))
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


# ─── NEW: Stream resolvers from LINKS.txt ──────────────────────────────────

def resolve_streamrk(url):
    """Resolver for streamrk.site and similar CDN URLs"""
    try:
        log("resolve_streamrk: Processing {}".format(url[:100]))

        if '.mp4' in url or '.m3u8' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://streamrk.site/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.hakunaymatata\.com[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+/convert-h264/[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+/bt/[^\s"\']+\.mp4[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_streamrk: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        stream = find_m3u8(html) or find_mp4(html)
        if stream:
            return stream

        return None
    except Exception as e:
        log("resolve_streamrk error: {}".format(e))
        return None


def resolve_moviepire(url):
    """Resolver for moviepire.co and hakunaymatata.com URLs"""
    try:
        log("resolve_moviepire: Processing {}".format(url[:100]))

        if '.mp4' in url or '.m3u8' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://moviepire.co/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.hakunaymatata\.com[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+/convert-h264/[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+/bt/[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+/resource/[^\s"\']+\.mp4[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_moviepire: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        script_match = re.search(r'file\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']', html, re.I)
        if script_match:
            stream_url = _correct_stream_url(script_match.group(1))
            if not _is_placeholder_media_url(stream_url):
                log("resolve_moviepire: Found stream URL in script: {}".format(stream_url[:100]))
                return stream_url

        return find_mp4(html) or find_m3u8(html)
    except Exception as e:
        log("resolve_moviepire error: {}".format(e))
        return None


def resolve_polarcandy(url):
    """Resolver for polarcandy.top and peakstorm.top URLs"""
    try:
        log("resolve_polarcandy: Processing {}".format(url[:100]))

        if '.mp4' in url or '.m3u8' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://peakstorm.top/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.polarcandy\.top[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+/vd/[^\s"\']+\.mp4[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_polarcandy: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_polarcandy error: {}".format(e))
        return None


def resolve_xpass(url):
    """Resolver for xpass.top and 1x2.space URLs"""
    try:
        log("resolve_xpass: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.mp4' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://play.xpass.top/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.1x2\.space[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+/playlist/[^\s"\']+\.m3u8[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_xpass: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_xpass error: {}".format(e))
        return None


def resolve_scalablecontentengine(url):
    """Resolver for scalablecontentengine.site URLs"""
    try:
        log("resolve_scalablecontentengine: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.mp4' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://nextgencloudfabric.com/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.scalablecontentengine\.site[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+/pl/[^\s"\']+\.m3u8[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_scalablecontentengine: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_scalablecontentengine error: {}".format(e))
        return None


def resolve_cloudorchestra(url):
    """Resolver for cloudorchestranova.com and panoplypalaver.site URLs"""
    try:
        log("resolve_cloudorchestra: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.mp4' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://cloudorchestranova.com/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.panoplypalaver\.site[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+/pl/[^\s"\']+\.m3u8[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_cloudorchestra: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_cloudorchestra error: {}".format(e))
        return None


def resolve_viduki(url):
    """Resolver for viduki.net and 1shows.app URLs"""
    try:
        log("resolve_viduki: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.mp4' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://viduki.net/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.1shows\.app[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+/e/[^\s"\']+\.m3u8[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_viduki: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_viduki error: {}".format(e))
        return None


# Confirmed static constant from moviesapi.to's own JS bundle (see
# resolve_moviesapi's docstring "[UPDATE 2]") -- not a secret, not
# per-session, just a fixed value every real client sends as
# x-player-key on moviesapi.to's own /api/vidora/v1/* calls.
_MOVIESAPI_PLAYER_KEY = "3a67e8866ae1d2bb9e81fe7f73315a56eb3bdf5e3e755c7554c8be6910aa6b13"


def _moviesapi_pick_host(src_url, headers):
    """[NEW 2026-10-17] The vidora API hands out the same /hls2/... path and token
    on different hosts: the Cloudflare *.workers.dev mirror (the browser's) or
    bx.netrocdn.site (the origin). Seen on the box: the mirror sometimes refuses
    everything (403), and sometimes serves the master playlist but then 403s every
    quality playlist (/cdn/<...>.js) -- it checks the client's TLS fingerprint,
    which the box can't imitate. netrocdn served the same links every time (slowly),
    so always use it for these."""
    host = urlparse(src_url).netloc.lower()
    if not host.endswith(".workers.dev") or "/hls2/" not in src_url:
        return src_url
    alt = src_url.replace("://" + urlparse(src_url).netloc + "/", "://bx.netrocdn.site/", 1)
    log("resolve_moviesapi: using origin bx.netrocdn.site instead of mirror {}".format(host))
    return alt


def resolve_moviesapi(url):
    """Resolver for moviesapi.to and netrocdn.site URLs.

    [UPDATE, SFlix] The old version of this resolver scraped the raw
    moviesapi.to embed-page HTML for an .m3u8 URL with a couple of regex
    patterns -- that was always going to miss, because moviesapi.to's own
    page is a client-rendered app that fetches its stream data from its
    own JSON API after load; the m3u8 URL was never actually sitting in
    the server-rendered HTML to find. Confirmed directly from a real
    browser capture (SFlix's own default embed, Server 1 of 6): the page
    calls GET https://moviesapi.to/api/vidora/v1/movie/<tmdb_id> (or
    /tv/<tmdb_id>/<season>/<episode> for a show) and gets back a clean,
    UNENCRYPTED JSON response:
        {"result":true,"type":"movie","title":"...","sources":[
            {"file_code":"...","url":"<direct .m3u8>","source":"vidora",
             "tracks":[]}]}
    -- no decryption, no signed token in the stream URL itself beyond
    what's already embedded in it (t=/s=/e=/srv= query params the server
    hands back, usable as-is).

    [UPDATE 2] The x-player-key question is resolved: it looked like a
    per-page-view random token (two different captured page loads used
    two different-looking 64-hex-char values), but the user pulled
    moviesapi.to's own JS bundle and it's actually a STATIC constant
    baked into the bundle, not generated at all:
        var fz = `3a67e8866ae1d2bb9e81fe7f73315a56eb3bdf5e3e755c7554c8be6910aa6b13`
    and every one of moviesapi.to's own /api/vidora/v1/* calls (movie,
    tv, subtitles, ice) passes this exact same `fz` constant as the
    header value -- confirmed by both captured sessions (movie page, tv
    page) using the identical value for every one of their own calls.
    (The *other* differently-looking key seen in the earlier capture
    belonged to a completely different host, cdn.vidspark.to, which
    isn't used by this resolver.) So this is now a real, confirmed
    constant, not a guess -- no more need to synthesize anything.
    """
    try:
        log("resolve_moviesapi: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.mp4' in url:
            return _correct_stream_url(url)

        m = re.search(r'/(movie|tv)/(\d+)(?:/(\d+)/(\d+))?', urlparse(url).path)
        if m:
            media_type, tmdb_id, season, episode = m.groups()
            if media_type == "tv" and season and episode:
                api_path = "/api/vidora/v1/tv/{}/{}/{}".format(tmdb_id, season, episode)
                page_url = "https://moviesapi.to/tv/{}/{}/{}".format(tmdb_id, season, episode)
            else:
                api_path = "/api/vidora/v1/movie/{}".format(tmdb_id)
                page_url = "https://moviesapi.to/movie/{}".format(tmdb_id)

            data = fetch_json(
                "https://moviesapi.to{}".format(api_path),
                referer=page_url,
                extra_headers={"x-player-key": _MOVIESAPI_PLAYER_KEY, "Accept": "*/*"},
            )
            if data and data.get("result") and data.get("sources"):
                for src in data["sources"]:
                    src_url = (src or {}).get("url")
                    if src_url:
                        log("resolve_moviesapi: vidora API SUCCESS: {}".format(src_url[:100]))
                        # [FIX] a device log showed this URL (a
                        # *.workers.dev HLS relay) resolving fine but then
                        # failing in all 10 play modes: it was handed to
                        # the player with no Referer/Origin, and these
                        # relays only serve requests that look like they
                        # come from the embedding page. Send what the real
                        # moviesapi.to player sends; a per-source "headers"
                        # dict from the API, if it ever has one, wins.
                        _hdrs = {"Referer": "https://moviesapi.to/",
                                 "Origin": "https://moviesapi.to"}
                        if isinstance((src or {}).get("headers"), dict):
                            _hdrs.update(src["headers"])
                        src_url = _moviesapi_pick_host(_correct_stream_url(src_url), _hdrs)
                        return _vidnest_headers_suffix(src_url, _hdrs)
            elif data:
                log("resolve_moviesapi: vidora API returned no sources: {}".format(
                    str(data.get("message") or data)[:200]))

        # Fallback: the old HTML-scrape path, kept in case the vidora API
        # call above is blocked/rate-limited or the page uses a different
        # backend for some titles.
        html, final_url = fetch(url, referer="https://moviesapi.to/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.netrocdn\.site[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+/hls2/[^\s"\']+\.m3u8[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_moviesapi: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_moviesapi error: {}".format(e))
        return None


def resolve_shows_st(url):
    """Resolver for shows.st API URLs"""
    try:
        log("resolve_shows_st: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.ts' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://shows.st/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.shows\.st/api[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+\.shows\.st/api[^\s"\']+&seg=[^\s"\']+\.ts[^\s"\']*)',
            r'(https?://[^\s"\']+/api\?[^\s"\']+&seg=[^\s"\']+\.ts[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_shows_st: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        master_match = re.search(r'(https?://[^\s"\']+\.shows\.st/api[^\s"\']+)', html, re.I)
        if master_match:
            master_url = master_match.group(1)
            if 'seg=' not in master_url:
                master_url += '&seg=index.m3u8'
            log("resolve_shows_st: Constructed master URL: {}".format(master_url[:100]))
            return master_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_shows_st error: {}".format(e))
        return None


def resolve_vaplayer(url):
    """Resolver for vaplayer.ru URLs"""
    try:
        log("resolve_vaplayer: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.mp4' in url:
            return _correct_stream_url(url)

        html, final_url = fetch(url, referer="https://vaplayer.ru/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\']+\.vaplayer\.ru[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+\.streamdata\.vaplayer\.ru[^\s"\']+\.m3u8[^\s"\']*)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_vaplayer: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        return find_m3u8(html) or find_mp4(html)
    except Exception as e:
        log("resolve_vaplayer error: {}".format(e))
        return None



# ─── VaPlayer stream-data API (nextgencloudfabric.com / vidsrc-embed.ru) ─────
# [NEW 2026-10-15] Confirmed from a real Stream Recorder Pro capture of SFlix's
# "VidSrc-Embed" server (vidsrc-embed.ru/embed/movie/1153576), which loads the
# nextgencloudfabric.com/embed player. That player page sits behind a Cloudflare
# Turnstile check (embed/turnstile-verify.php), so scraping its HTML can't work
# -- but its own player.min.js gets the stream list from ONE plain JSON API,
# needing nothing but the TMDB id:
#     API = 'https://streamdata.vaplayer.ru/api.php'
#     ?tmdb=<id>&type=movie            (or &type=tv&season=<s>&episode=<e>)
# fetched with credentials 'omit' and no special headers. Captured reply:
#     {"status_code":"200","data":{"title":...,"stream_urls":["https://...
#      /pl/H4sI.../master.m3u8", ...]}, ...}
# The page's vsdec.js can also decrypt an ENCRYPTED form of stream_urls (a
# string plus a "vp"/"vs" field, decrypted by a WebAssembly module). The
# capture returned the plain list; if the encrypted form ever shows up, this
# logs it clearly and returns None (no WASM runtime on the box).
# Calling the API directly also sidesteps vidsrc-embed.ru itself, which
# refused connections from the box (device log, 2026-10-15).
_VAPLAYER_API = "https://streamdata.vaplayer.ru/api.php"
_VAPLAYER_PLAYER_ORIGIN = "https://nextgencloudfabric.com"


def _parse_tmdb_media(url):
    """(media_type, tmdb_id, season, episode) from embed-style URLs:
    /embed/movie/<id>, /movie/<id>, /embed/tv/<id>/<s>/<e>, /embed/tv/<id>/<s>-<e>,
    or ?season=&episode= query params. Returns None if no TMDB id is found."""
    p = urlparse(url)
    m = re.search(r'/(movie|tv)/(\d+)(?:/(\d+)(?:[/-](\d+))?)?', p.path)
    if not m:
        return None
    media_type, tmdb_id, season, episode = m.groups()
    if media_type == "tv" and not (season and episode):
        q = parse_qs(p.query)
        season = (q.get("season") or q.get("s") or [None])[0]
        episode = (q.get("episode") or q.get("e") or [None])[0]
    return media_type, tmdb_id, season, episode


def resolve_vaplayer_api(url, referer=None, start_index=0):
    try:
        parsed = _parse_tmdb_media(url)
        if not parsed:
            log("resolve_vaplayer_api: no TMDB id in {}".format(url[:100]))
            return None
        media_type, tmdb_id, season, episode = parsed
        api_url = "{}?tmdb={}&type={}".format(_VAPLAYER_API, tmdb_id, media_type)
        if media_type == "tv":
            if not (season and episode):
                log("resolve_vaplayer_api: TV URL without season/episode: {}".format(url[:100]))
                return None
            api_url += "&season={}&episode={}".format(season, episode)

        data = fetch_json(api_url, referer=_VAPLAYER_PLAYER_ORIGIN + "/",
                          extra_headers={"Origin": _VAPLAYER_PLAYER_ORIGIN, "Accept": "*/*"})
        if not isinstance(data, dict):
            log("resolve_vaplayer_api: no JSON from {}".format(api_url))
            return None
        streams = (data.get("data") or {}).get("stream_urls")
        if isinstance(streams, str):
            log("resolve_vaplayer_api: stream_urls is ENCRYPTED (vp/vs={}) -- "
                "needs the site's WASM decryptor, not supported".format(
                    data.get("vp") or data.get("vs")))
            return None
        if not isinstance(streams, list) or not streams:
            log("resolve_vaplayer_api: no stream_urls (status_code={})".format(data.get("status_code")))
            return None
        # start_index lets a second server (YapGrid) use a different one of the
        # alternative links, so it is a real backup rather than a duplicate
        order = list(range(len(streams)))
        if 0 < start_index < len(streams):
            order = order[start_index:] + order[:start_index]
        for i in order:
            stream_url = streams[i]
            if isinstance(stream_url, str) and stream_url.startswith("http"):
                stream_url = _correct_stream_url(stream_url.replace("\\/", "/"))
                log("resolve_vaplayer_api: SUCCESS ({} of {}): {}".format(
                    i + 1, len(streams), stream_url[:100]))
                # the browser played this from the nextgencloudfabric.com player page
                return _vidnest_headers_suffix(stream_url, {
                    "Referer": _VAPLAYER_PLAYER_ORIGIN + "/",
                    "Origin": _VAPLAYER_PLAYER_ORIGIN,
                })
        return None
    except Exception as e:
        log("resolve_vaplayer_api error for {}: {}".format(url[:100], e))
        return None


# ─── VidBolt (vidsrc.win "Bolt" server) ─────────────────────────────────────
# [NEW 2026-10-19] From a Stream Recorder Pro capture of vidsrc.win (Tenzing,
# tmdb 1288069): vidbolt.xyz/movie/<id> is a static page whose inline script
# builds q="type=movie&id=<id>" (plus the TV fields for an episode) and calls
# GET /api/resolve?<q>[&prefer=<server>] -> JSON {stream:{sources:[{url}]}}.
# window.__VB__.servers lists its backends (Orion, Callisto, FastVa, Vega,
# Quantum, Quasar, Tampa); "prefer" picks one when the default has nothing.
# The playlist is on proxy.vidbolt.xyz (segments come back as image/jpeg;
# the local proxy strips any fake image header in front of the TS data).
_VIDBOLT_ORIGIN = "https://vidbolt.xyz"


def _vidbolt_sources(data):
    """All playable-looking sources of an /api/resolve reply, best first
    (highest quality up to 1080p, 4K last)."""
    stream = (data or {}).get("stream") if isinstance(data, dict) else None
    if not isinstance(stream, dict):
        return []
    srcs = [s for s in stream.get("sources") or []
            if isinstance(s, dict) and str(s.get("url") or "").startswith("http")]

    def _h(s):
        m = re.search(r'(\d{3,4})', str(s.get("quality") or ""))
        h = int(m.group(1)) if m else 0
        return (h <= 1080, h)
    srcs.sort(key=_h, reverse=True)
    out = []
    for s in srcs:
        hdrs = s.get("headers") or stream.get("headers") or {}
        out.append((s["url"].replace("\\/", "/"), hdrs if isinstance(hdrs, dict) else {},
                    str(stream.get("server") or "")))
    return out


def _vidbolt_pick(data):
    srcs = _vidbolt_sources(data)
    return (srcs[0][0], srcs[0][1]) if srcs else (None, {})


def _vidbolt_box_playable(stream_url, headers):
    """[2026-10-19] The box's players can't play an HLS master whose audio is in
    separate renditions (#EXT-X-MEDIA TYPE=AUDIO with a URI): one VidBolt
    backend (img.rousav.tech, 8 audio languages) played only a few seconds of
    picture. Such a source is skipped so another backend is tried."""
    try:
        text, _f = fetch(stream_url, referer=headers.get("Referer"),
                         extra_headers={k: v for k, v in headers.items() if k != "Referer"},
                         timeout=12)
    except Exception:
        return True
    text = text or ""
    if "#EXTM3U" not in text[:200]:
        return True          # can't tell; let the player try
    if re.search(r'#EXT-X-MEDIA:[^\n]*TYPE=AUDIO[^\n]*URI=', text):
        log("resolve_vidbolt: separate audio tracks (box can't play): {}".format(stream_url[:90]))
        return False
    return True


def resolve_vidbolt(url, referer=None):
    try:
        p = urlparse(url)
        origin = "{}://{}".format(p.scheme or "https", p.netloc or "vidbolt.xyz")
        page = origin + p.path
        q, servers = "", []
        try:
            html, _f = fetch(page, referer="https://vidsrc.win/", timeout=15)
        except Exception:
            html = ""
        m = re.search(r'var\s+q\s*=\s*"([^"]+)"', html or "")
        if m:
            q = m.group(1)
        mv = re.search(r'window\.__VB__\s*=\s*(\{.*?\});', html or "", re.S)
        if mv:
            try:
                servers = [s.get("id") for s in json.loads(mv.group(1)).get("servers") or []
                           if isinstance(s, dict) and s.get("id")]
            except ValueError:
                servers = []
        if not q:
            parsed = _parse_tmdb_media(url)
            if not parsed:
                log("resolve_vidbolt: no TMDB id in {}".format(url[:100]))
                return None
            mt, tid, season, episode = parsed
            q = "type={}&id={}".format(mt, tid)
            if mt == "tv":
                q += "&season={}&episode={}".format(season or 1, episode or 1)
        hdr = {"Referer": page, "Origin": origin}
        seen, tried_servers = set(), set()
        for pref in [None] + servers[:7]:
            if pref and pref in tried_servers:
                continue
            api = "{}/api/resolve?{}".format(origin, q)
            if pref:
                api += "&prefer=" + quote(pref, safe="")
            data = fetch_json(api, referer=page, extra_headers={"Accept": "application/json"})
            srcs = _vidbolt_sources(data)
            if not srcs:
                log("resolve_vidbolt: {} -> no stream".format(pref or "default"))
                continue
            for stream_url, extra, server in srcs:
                if server:
                    tried_servers.add(server)
                if stream_url in seen:
                    continue
                seen.add(stream_url)
                h = dict(hdr)
                h.update({k: v for k, v in extra.items() if isinstance(v, str)})
                if not _vidbolt_box_playable(stream_url, h):
                    # one backend serves all its copies the same way (callisto
                    # listed 15 h265 copies, every one with separate audio):
                    # move on to the next backend instead of checking them all
                    break
                log("resolve_vidbolt: SUCCESS ({} / {}): {}".format(
                    pref or "default", server or "?", stream_url[:100]))
                return _vidnest_headers_suffix(stream_url, h)
        return None
    except Exception as e:
        log("resolve_vidbolt error for {}: {}".format(url[:100], e))
        return None


def resolve_wingst(url, referer=None):
    """Cinejoy's own servers (Nebula, Lisbon, ...): sealed api.wing.st calls, see wingst.py."""
    from .wingst import resolve
    return resolve(url, referer)


_ZM_RELAY = "https://proxy.semoteb377.workers.dev/?id="


def _zm_relay_fetch(url, timeout=20):
    """Fetch a page through ZetMoon's CORS relay (the box gets 403 from some
    of the sites it reads). Request: id = base64(hex(urlencode(url) XOR 0x7c));
    reply: hex of the page XOR 0x7c (plain text when not hex)."""
    try:
        enc = bytes(c ^ 0x7c for c in quote(url, safe="").encode()).hex()
        rid = base64.b64encode(enc.encode()).decode()
        body, _f = fetch(_ZM_RELAY + rid, referer="https://zetmoon.live/",
                         extra_headers={"Origin": "https://zetmoon.live"}, timeout=timeout)
        body = (body or "").strip()
        if body and re.fullmatch(r"[0-9a-fA-F]+", body[:2000]) and len(body) % 2 == 0:
            return bytes(c ^ 0x7c for c in bytes.fromhex(body)).decode("utf-8", "replace")
        return body
    except Exception as e:
        log("zm relay: {} -> {}".format(url[:80], e))
        return ""


def _streamwish_from_html(html):
    if not html:
        return None
    best = _best_media_url(html)
    if best:
        return best
    for txt in _unpack_all(html):
        best = _best_media_url(txt)
        if best:
            return best
    return find_m3u8(html) or find_mp4(html)


# [2026-10-21] vidapi.xyz's Apache 403s NOVA's default Chrome/120 request but
# answered the box's own curl (Chrome/155 UA) with 200 -- send what the
# browser sent (recording 2026-10-21, iframe navigation from onlyflix.to).
_VIDAPI_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "iframe", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site", "Upgrade-Insecure-Requests": "1",
}


def _vscdn_pick_link(html, page_url):
    """vscdn.xyz (StreamWish clone) page: links={"hls2": premilkyway.com/...,
    "hls3": <x>.site/.../master.txt, "hls4": "/stream/.../master.m3u8"}.
    The browser played hls4 (on vscdn.xyz itself, which the box reaches);
    hls2 resolved on the box but never played (2026-10-21). Check them in
    the order hls4, hls3, hls2 and return the first that serves a playlist."""
    if not html:
        return None
    texts = [html] + _unpack_all(html)
    links = {}
    for t in texts:
        for m in re.finditer(r'"(hls\d)"\s*:\s*"([^"]+)"', t):
            links.setdefault(m.group(1), m.group(2).replace("\\/", "/"))
    if not links:
        return None
    base = "{0.scheme}://{0.netloc}".format(urlparse(page_url))
    ua = quote(_VIDAPI_HDRS["User-Agent"], safe="")
    hdrs = {"User-Agent": _VIDAPI_HDRS["User-Agent"], "Origin": base, "Accept": "*/*",
            "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"}
    order = [k for k in ("hls4", "hls3", "hls2") if k in links] + \
            sorted(k for k in links if k not in ("hls4", "hls3", "hls2"))
    for k in order:
        u = urljoin(base + "/", links[k])
        try:
            body, _f = fetch(u, referer=base + "/", timeout=10, max_retries=0, extra_headers=hdrs)
        except Exception:
            body = None
        if body and "#EXTM3U" in body:
            log("resolve_vidapi: vscdn {} serves the playlist".format(k))
            # Box 2026-10-21: vscdn's /stream/ links answered this check with 200,
            # then the proxy's own request 403 seconds later -- download each
            # playlist ONCE here and hand it to the proxy (as vidvm does).
            play, text = u, body
            if "#EXT-X-STREAM-INF" in body:
                var = _parse_hls_master_variants(u, body)
                if var:
                    vu = var[0][1]
                    try:
                        sub, _f = fetch(vu, referer=base + "/", timeout=10, max_retries=0,
                                        extra_headers=hdrs)
                    except Exception:
                        sub = None
                    if sub and "#EXTM3U" in sub:
                        play, text = vu, sub
                        log("resolve_vidapi: vscdn {} quality playlist {} ({} bytes)".format(
                            k, var[0][0], len(sub)))
            disguised = ("tiktokcdn" in text) or (".image" in text) or k == "hls4"
            try:
                import novaplay_proxy
                if play != u:
                    novaplay_proxy.remember_playlist(u, body, u, keep=3 * 3600)
                novaplay_proxy.remember_playlist(play, text, play, keep=3 * 3600)
            except Exception as e:
                log("resolve_vidapi: could not hand the playlist to the proxy: {}".format(e))
            return play + "|Referer={}/&Origin={}&User-Agent={}{}".format(
                base, base, ua, "&X-NP-Proxy=1" if disguised else "")
        log("resolve_vidapi: vscdn {} gave no playlist ({} bytes)".format(k, len(body or "")))
    return None


# ─── VidAPI player (player.imdb.su -> nextgencloudfabric.com) ─────────────
# [2026-10-21 capture, imdb.su "Street Fighter 2026"] player.imdb.su/embed/
# movie/<tt> frames nextgencloudfabric.com/embed/movie/<tt>, whose CONFIG has
# streamDataApiUrl = https://streamdata.vaplayer.ru/api.php. The player asks
#   ?imdb=<tt>&type=movie            (or tmdb=<id>)
#   ?imdb=<tt>&type=tv&season=S&episode=E
# and gets {"data": {"file_name": ..., "stream_urls": [".../master.m3u8", ...]}}.
# Referer/Origin https://nextgencloudfabric.com. The pieces are served as
# "page-N.html" (text/html): play through the proxy, which unwraps them.
_VAPI = "https://streamdata.vaplayer.ru/api.php"
_VAPI_ORIGIN = "https://nextgencloudfabric.com"


def resolve_vidapi_player(url, referer=None):
    try:
        path = urlparse(url).path
        m = re.search(r'/embed/(movie|tv)/([^/?#]+)(?:/(\d+)/(\d+))?', path)
        if not m:
            log("resolve_vidapi_player: no id in {}".format(url[:90]))
            return None
        mtype, mid, season, episode = m.groups()
        q = parse_qs(urlparse(url).query)
        season = season or (q.get("season") or [""])[0]
        episode = episode or (q.get("episode") or [""])[0]
        api = "{}?{}={}".format(_VAPI, "imdb" if mid.startswith("tt") else "tmdb", quote(mid))
        api += "&type=tv&season={}&episode={}".format(season or 1, episode or 1) if mtype == "tv" else "&type=movie"
        hdrs = {"Origin": _VAPI_ORIGIN, "User-Agent": _VIDAPI_HDRS["User-Agent"],
                "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
        body, _f = fetch(api, referer=_VAPI_ORIGIN + "/", timeout=15, max_retries=1, extra_headers=hdrs)
        try:
            js = json.loads(body or "{}") or {}
        except ValueError:
            js = {}
        if not isinstance(js, dict):
            js = {}
        data = js.get("data") if isinstance(js.get("data"), dict) else {}
        if str(js.get("status_code", "")) == "404":
            log("resolve_vidapi_player: VidAPI doesn't have this title (404)")
            return None
        urls = [u for u in (data.get("stream_urls") or []) if isinstance(u, str) and u.startswith("http")]
        log("resolve_vidapi_player: {} -> {} link(s) ({})".format(
            api[len(_VAPI):][:60], len(urls), data.get("file_name") or "no file"))
        ua = quote(_VIDAPI_HDRS["User-Agent"], safe="")
        suffix = "|Referer={0}/&Origin={0}&User-Agent={1}&X-NP-Proxy=1".format(_VAPI_ORIGIN, ua)
        import novaplay_proxy
        for su in urls:
            try:
                mbody, _f = fetch(su, referer=_VAPI_ORIGIN + "/", timeout=12, max_retries=0, extra_headers=hdrs)
            except Exception:
                mbody = None
            if not mbody or "#EXTM3U" not in mbody:
                log("resolve_vidapi_player: link gave no playlist ({} bytes)".format(len(mbody or "")))
                continue
            novaplay_proxy.remember_playlist(su, mbody, su, keep=3 * 3600)
            variants = _parse_hls_master_variants(su, mbody)
            if not variants:
                return su + suffix
            # each playlist is downloaded ONCE and handed to the proxy (single-use
            # links were seen on this kind of CDN), best quality first
            for lbl, vu in variants:
                try:
                    vbody, _f = fetch(vu, referer=_VAPI_ORIGIN + "/", timeout=12, max_retries=0, extra_headers=hdrs)
                except Exception:
                    vbody = None
                if vbody and "#EXTM3U" in vbody:
                    novaplay_proxy.remember_playlist(vu, vbody, vu, keep=3 * 3600)
                    _quality_tls.variants = [v + suffix for _l, v in variants]
                    log("resolve_vidapi_player: playing {} ({} quality(ies))".format(lbl, len(variants)))
                    return vu + suffix
                log("resolve_vidapi_player: {} gave no playlist".format(lbl))
        return None
    except Exception as e:
        log("resolve_vidapi_player error: {}".format(e))
        return None


def resolve_vidapi(url, referer=None):
    """[2026-10-21] vidapi.xyz/embed/{movie,tv}/<id> (ZetMoon "Zeon"): the page
    keeps its player in <iframe id="iframesrc" data-src=
    "https://stream.vidapi.xyz/swish?id=<code>">, a StreamWish clone served as
    vscdn.xyz/e/<code> (packed JW player). vidapi.xyz answers the box with 403,
    so its page (and vscdn's, if needed) is read through ZetMoon's relay."""
    try:
        html = ""
        # vidapi.xyz's Apache answers 403 unless the Referer is a site it
        # serves (the browser got 200 with onlyflix.to; the relay, with no
        # usable referer, gets the same 403 page) -- try the embedding sites.
        refs = []
        for r in (referer, "https://onlyflix.to/", "https://zetmoon.live/"):
            if r and r not in refs:
                refs.append(r)
        for r in refs:
            try:
                html, _f = fetch(url, referer=r, timeout=12, max_retries=0,
                                 extra_headers=_VIDAPI_HDRS)
            except Exception:
                html = ""
            if html and "swish" in html:
                log("resolve_vidapi: page OK with Referer {}".format(r))
                break
            log("resolve_vidapi: Referer {} -> {} bytes".format(r, len(html or "")))
        if not html or "swish" not in html:
            log("resolve_vidapi: direct page blocked, using the ZetMoon relay")
            html = _zm_relay_fetch(url)
        html = html_module.unescape(html or "")
        codes = list(dict.fromkeys(re.findall(r'vidapi\.xyz/swish\?id=([A-Za-z0-9]+)', html)))
        if not codes:
            log("resolve_vidapi: no swish player on {} ({} bytes): {}".format(
                url[:90], len(html), re.sub(r"\s+", " ", html[:300])))
        for code in codes:
            emb = "https://vscdn.xyz/e/{}".format(code)
            # vscdn.xyz 403s the Chrome/120 default too: fetch it like the browser
            # did (iframe from stream.vidapi.xyz), and play with the same UA
            try:
                eh, _f = fetch(emb, referer="https://stream.vidapi.xyz/", timeout=12,
                               max_retries=0, extra_headers=_VIDAPI_HDRS)
            except Exception:
                eh = ""
            r = _vscdn_pick_link(eh, emb)
            r = r or _streamwish_from_html(eh)
            if r and "|" not in r:
                r = _correct_stream_url(r) + "|Referer=https://vscdn.xyz/&Origin=https://vscdn.xyz&User-Agent=" + \
                    quote(_VIDAPI_HDRS["User-Agent"], safe="")
            r = r or resolve_streamwish(emb) or _streamwish_from_html(_zm_relay_fetch(emb))
            if r:
                log("resolve_vidapi: {} -> {}".format(emb, r[:90]))
                return r
            log("resolve_vidapi: {} gave no stream".format(emb))
    except Exception as e:
        log("resolve_vidapi error: {}".format(e))
    return None


def resolve_yapgrid(url, referer=None):
    """[NEW 2026-10-18] A browser recording of SFlix's YapGrid server showed its
    player loading vsembed.ru (VidSrc-Embed's player) and playing from the same
    vaplayer source. Use that source's API directly, starting from its 2nd link."""
    return resolve_vaplayer_api(url, start_index=1)


def resolve_nextgencloudfabric(url, referer=None):
    """API first (works past the Turnstile gate); old HTML scrape as fallback."""
    return resolve_vaplayer_api(url) or resolve_scalablecontentengine(url)


# ─── EmbedMaster (SFlix "EmbedMaster" server) ───────────────────────────────
# [NEW 2026-10-17] From a Stream Recorder Pro capture (Matchbox the Movie,
# tmdb 1148365) plus the box log of 2026-10-15:
#   1. embedmaster.link/movie/<tmdb> redirects to embdmstrplayer.com/v2/<token>,
#      a "welcome" page whose Play button is a plain form POST (tr=<site>);
#      the Turnstile div on it is empty (its script is never loaded).
#   2. The POST answers with the player page. Its config is an XOR-wrapped
#      script: atob(blob)[i] ^ ((C + K[i % len(K)] + i) & 255), which sets
#      window.__EM_PLAYER__ = {ticket, pk, scrapeToken, feedUrl,
#      feedUrlBackup, resolveUrl, order, kinds, obfHosts, ...}.
#   3. GET feedUrl + scrapeToken -> NDJSON lines {"type":"server",
#      "source_type", "source_name", "source_url"(opaque)} ... {"type":"done"}.
#   4. GET resolveUrl?src=<source_url> with X-EM-Player: 1 and
#      X-EM-Ticket: <ticket>. The reply is JSON, or base64url text decrypted
#      with pk (em-crypto.min.js EMCrypto.decrypt, verified byte-for-byte
#      against the site's own JS): nonce = first 16 bytes, then XOR with
#      sha256(key + nonce + uint32_be(block)) per 32-byte block.
#      Decrypted: {"ok":true,"url":...,"type":"hls","subtitles":[...]}.
#   "direct" kinds give an m3u8; "embed" kinds (MixDrop, StreamWish, Dood,
#   Abyss, ...) give an embed page that the normal host resolvers handle.
#   Hosts in obfHosts serve XOR-obfuscated playlists (see hlsobf.py); those
#   streams carry X-NP-Xor so the local proxy decodes them.
_EM_ORIGIN = "https://embdmstrplayer.com"


def _em_b64url(text):
    text = re.sub(r"\s+", "", text or "").replace("-", "+").replace("_", "/")
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _em_decrypt(text, key_hex):
    raw = _em_b64url(text)
    if len(raw) < 16:
        return ""
    key = bytes.fromhex(key_hex)
    nonce, data = raw[:16], raw[16:]
    out = bytearray(len(data))
    for block, start in enumerate(range(0, len(data), 32)):
        ks = hashlib.sha256(key + nonce + block.to_bytes(4, "big")).digest()
        for i, b in enumerate(data[start:start + 32]):
            out[start + i] = b ^ ks[i]
    return out.decode("utf-8", "replace")


def _em_player_config(html):
    """Decode the XOR-wrapped script that defines window.__EM_PLAYER__."""
    if not html:
        return None
    for m in re.finditer(r'var\s+\w+\s*=\s*"([A-Za-z0-9+/=]{100,})"\s*,\s*\w+\s*=\s*\[([\d,\s]+)\]\s*,\s*\w+\s*=\s*(\d+)', html):
        try:
            blob = base64.b64decode(m.group(1))
            keys = [int(x) for x in m.group(2).split(",") if x.strip()]
            c = int(m.group(3))
            js = bytes(blob[i] ^ ((c + keys[i % len(keys)] + i) & 255)
                       for i in range(len(blob))).decode("utf-8", "replace")
        except Exception:
            continue
        cm = re.search(r"__EM_PLAYER__\s*=\s*(\{.*\})\s*;?\s*$", js, re.S)
        if cm:
            try:
                return json.loads(cm.group(1))
            except ValueError:
                pass
    return None


def _em_feed(cfg, page_url):
    token = quote(cfg.get("scrapeToken") or "", safe="")
    for base in (cfg.get("feedUrl"), cfg.get("feedUrlBackup")):
        if not base or not token:
            continue
        text, _ = fetch(base + token, referer=page_url, timeout=20, max_retries=0,
                        extra_headers={"Accept": "application/x-ndjson", "Origin": _EM_ORIGIN})
        servers = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "server" and o.get("source_url"):
                servers.append(o)
            elif o.get("type") == "error":
                log("resolve_embedmaster: feed error {} {}".format(o.get("code"), o.get("message")))
        if servers:
            return servers
        log("resolve_embedmaster: no servers from {}".format(base))
    return []


def _em_resolve_get(url, page_url, ticket):
    """resolve.php exactly as the player's XHR sends it (same-origin GET, no
    X-Requested-With), through the shared cookie jar so the session cookie
    from the Play-form POST goes along. Returns (status, body)."""
    from urllib.request import Request
    from urllib.error import HTTPError
    from . import net as _net
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Referer": page_url,
        "X-EM-Player": "1",
        "X-EM-Ticket": ticket,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    })
    # merged jar (urllib + curl_cffi session), in case the Play POST went via curl_cffi
    cookie = get_cookie_header_for_url(url)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        resp = _net._get_opener().open(req, timeout=15)
        return resp.getcode(), resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def _em_probe(stream, headers, xor_key=None):
    """Quick check that the playlist really loads (some CDNs just time out)."""
    from urllib.request import Request
    from . import net as _net
    from .hlsobf import playlist_fetch_url, xor_decode
    url = playlist_fetch_url(stream) if xor_key else stream
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    h.update(headers)
    try:
        data = _net._get_opener().open(Request(url, headers=h), timeout=8).read(
            4 * 1024 * 1024 if xor_key else 4096)
    except Exception as e:
        log("resolve_embedmaster: probe failed for {}: {}".format(url[:90], e))
        return False
    if xor_key:
        data = xor_decode(data, xor_key)
    if not data.lstrip().startswith(b"#EXTM3U"):
        log("resolve_embedmaster: probe got no playlist from {} ({!r})".format(url[:90], data[:40]))
        return False
    if xor_key:
        # Box: the same link answered 404 five seconds after this check, so hand the
        # playlist we already have to the local proxy instead of fetching it again.
        # The level playlists it lists die just as fast (404 a few seconds later on the
        # box), so fetch them now as well.
        try:
            import novaplay_proxy
            novaplay_proxy.remember_playlist(stream, data.decode("utf-8", "ignore"), stream)
            ph = {"User-Agent": novaplay_proxy.SAFE_UA}
            ph.update({k: v for k, v in headers.items() if k in ("Referer", "Origin")})
            novaplay_proxy.prefetch_hls(stream, ph, xor_key, wait=True)
        except Exception as e:
            log("resolve_embedmaster: could not hand playlists to the proxy: {}".format(e))
    return True


def resolve_embedmaster(url, referer=None, max_servers=8):
    try:
        site_ref = referer or "https://ssflix.pro/"
        html, page_url = fetch(url, referer=site_ref, timeout=15)
        page_url = page_url or url
        cfg = _em_player_config(html)
        if not cfg and html and "welcome-play-form" in html:
            tm = re.search(r'name=["\']tr["\'][^>]*value=["\']([^"\']*)', html)
            tr = html_module.unescape(tm.group(1)) if tm else site_ref
            log("resolve_embedmaster: posting the Play form")
            html, final = fetch(page_url, referer=page_url, post_data="tr=" + quote(tr, safe=""),
                                extra_headers={"Origin": _EM_ORIGIN}, timeout=15)
            page_url = final or page_url
            cfg = _em_player_config(html)
        if not cfg:
            log("resolve_embedmaster: no player config on {} ({} bytes)".format(
                page_url[:100], len(html or "")))
            return None
        ticket, pk = cfg.get("ticket") or "", cfg.get("pk") or ""
        servers = _em_feed(cfg, page_url)
        if not servers:
            return None
        order = [str(x) for x in cfg.get("order") or []]
        kinds = cfg.get("kinds") or {}
        obf = [str(h).lower() for h in cfg.get("obfHosts") or []]
        rank = lambda s: order.index(str(s.get("source_type"))) if str(s.get("source_type")) in order else 999
        servers.sort(key=rank)
        log("resolve_embedmaster: {} servers: {}".format(
            len(servers), ", ".join(str(s.get("source_name")) for s in servers)))
        resolve_base = urljoin(page_url, cfg.get("resolveUrl") or "/v2/api/resolve.php")
        tried = 0
        for srv in servers:
            if tried >= max_servers:
                break
            tried += 1
            name = srv.get("source_name") or srv.get("source_type")
            status, body = _em_resolve_get(
                resolve_base + "?src=" + quote(srv["source_url"], safe=""), page_url, ticket)
            body = (body or "").strip()
            if status != 200 or not body:
                log("resolve_embedmaster: {}: HTTP {} {}".format(name, status, body[:200]))
                if tried == 1:
                    log("resolve_embedmaster: cookies sent: {}".format(
                        get_cookie_header_for_url(resolve_base) or "(none)"))
                if status == 403 and tried >= 2:
                    break  # same answer for every server: don't hammer it
                continue
            try:
                d = json.loads(body if body.startswith("{") else _em_decrypt(body, pk))
            except Exception as e:
                log("resolve_embedmaster: {}: cannot read reply ({})".format(name, e))
                continue
            if not (isinstance(d, dict) and d.get("ok") and d.get("url")):
                log("resolve_embedmaster: {}: {}".format(name, (d or {}).get("error") if isinstance(d, dict) else d))
                continue
            stream = str(d["url"])
            if stream.startswith("//"):
                stream = "https:" + stream
            kind = kinds.get(str(srv.get("source_type")), "direct")
            if kind == "embed":
                log("resolve_embedmaster: {}: embed {}".format(name, stream[:100]))
                result = resolve_host(stream, referer=_EM_ORIGIN + "/")
                if result:
                    return result
                continue
            host = urlparse(stream).netloc.lower()
            hdrs = {"Referer": _EM_ORIGIN + "/", "Origin": _EM_ORIGIN}
            xor_key = None
            if any(h and h in host for h in obf):
                from .hlsobf import EM_OBF_KEY, XOR_HEADER, is_playlist_url
                if not is_playlist_url(stream):
                    log("resolve_embedmaster: {}: obfuscated non-HLS link, skipped".format(name))
                    continue
                xor_key = EM_OBF_KEY
            if not _em_probe(stream, hdrs, xor_key):
                continue
            if xor_key:
                hdrs[XOR_HEADER] = xor_key
            log("resolve_embedmaster: SUCCESS {}{}: {}".format(
                name, " (XOR playlist, via proxy)" if xor_key else "", stream[:100]))
            return _vidnest_headers_suffix(stream, hdrs)
        log("resolve_embedmaster: no playable server")
        return None
    except Exception as e:
        log("resolve_embedmaster error for {}: {}".format(url[:100], e))
        return None


# ─── MedMedia03 (OnlyFlix "Server 3") Resolver ──────────────────────────────
# [NEW 2026-10-14] `balance.medmedia03.wiki/embed/movie/<tmdb_id>` (or an
# analogous /embed/tv/<tmdb_id>/<season>/<episode> shape) is a VidSrc-clone
# aggregator -- confirmed against a real capture of OnlyFlix's "Server 3"
# tab for "The Uprising" (tmdb 977942). The iframe's own src redirects from
# the load-balancer alias ("balance.medmedia03.wiki") to a numbered worker
# subdomain (seen: "2.medmedia03.wiki") in the real capture -- fetch()'s own
# redirect-following hands us the real subdomain via final_url, so there's
# no guessing involved.
#
# Once on the real subdomain, the page's own JS queries
# `<base>/embed-api/source/<provider>/movie/<id>` for each of several
# backing providers and uses whichever one responds with a non-empty
# "streams" array -- confirmed from a real capture's network log, which
# shows 9 providers tried in this order (vidlove, vidrock, onetouchtv,
# vidnest, streamflix, vidsrcpm, vaplayer, moviesapi, zxcstreams) with only
# vaplayer and moviesapi returning real streams for this title (the rest
# came back with an empty "streams" array and a diagnostic reason). Tried
# in a working-likelihood order here rather than the page's own order.
# Each hit's `streams[0]["url"]` is a path on medmedia03 itself (e.g.
# "/embed-media/<ts>.<opaque>") that already proxies the real CDN
# response -- playable directly once joined against the base, no further
# unwrapping needed.
_MEDMEDIA03_PROVIDERS = (
    "vaplayer", "moviesapi", "vidsrcpm", "streamflix", "vidnest",
    "onetouchtv", "vidrock", "vidlove", "zxcstreams",
)


def resolve_medmedia03(url):
    """Resolver for balance.medmedia03.wiki / <n>.medmedia03.wiki embed pages."""
    try:
        log("resolve_medmedia03: Processing {}".format(url[:100]))

        if '.m3u8' in url or '.mp4' in url:
            return _correct_stream_url(url)

        parsed = urlparse(url)
        m = re.search(r'/embed/(movie|tv)/(\d+)(?:/(\d+)/(\d+))?', parsed.path)
        if not m:
            log("resolve_medmedia03: couldn't parse media id from {}".format(url))
            return None
        media_type, media_id, season, episode = m.groups()

        html, final_url = fetch(url, referer="https://onlyflix.to/")
        base_url = final_url or url
        base_parsed = urlparse(base_url)
        if "medmedia03.wiki" not in base_parsed.netloc:
            # redirect target wasn't captured for some reason -- fall back
            # to the original (load-balancer alias) host rather than fail.
            base_parsed = parsed
        base = "{}://{}".format(base_parsed.scheme or "https", base_parsed.netloc)

        if season and episode:
            suffix = "/tv/{}/{}/{}".format(media_id, season, episode)
        else:
            suffix = "/{}/{}".format(media_type, media_id)

        # [2026-10-21] ask every provider at once, like the page does, and take
        # the first that answers with a stream. Box: 'vaplayer' alone took 17 s
        # (33 s for the whole server); one at a time, each slow or dead
        # provider added its full wait.
        import concurrent.futures as _cf

        def _ask(provider):
            api_url = base + "/embed-api/source/" + provider + suffix
            try:
                body, _ = fetch(api_url, referer=base + "/", timeout=25, max_retries=0)
                data = json.loads(body) if body else {}
            except Exception:
                return None
            streams = (data.get("streams") or []) if isinstance(data, dict) else []
            if not streams:
                return None
            # [PATCH] prefer a "verified" entry (unverified ones failed upstream
            # with HTTP 429 in a real capture) over list order
            chosen = next((x for x in streams if isinstance(x, dict) and x.get("verified")),
                          streams[0])
            path = chosen.get("url") if isinstance(chosen, dict) else None
            return (provider, urljoin(base + "/", path)) if path else None

        pool = _cf.ThreadPoolExecutor(max_workers=len(_MEDMEDIA03_PROVIDERS))
        t0 = time.time()
        try:
            futs = [pool.submit(_ask, pr) for pr in _MEDMEDIA03_PROVIDERS]
            try:
                for f in _cf.as_completed(futs, timeout=30):
                    res = f.result()
                    if res:
                        provider, stream_url = res
                        log("resolve_medmedia03: provider '{}' answered first ({:.1f} s) -> {}".format(
                            provider, time.time() - t0, stream_url[:100]))
                        return _correct_stream_url(stream_url)
            except _cf.TimeoutError:
                log("resolve_medmedia03: no provider answered within 30 s")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        log("resolve_medmedia03: no provider returned a usable stream")
        return None
    except Exception as e:
        log("resolve_medmedia03 error: {}".format(e))
        return None


# ─── SuperFlix API Resolver ─────────────────────────────────────────────────

def resolve_superflixapi(url):
    """
    Resolver for superflixapi.buzz/sbs and similar players.
    Extracts the video URL from the API response.
    """
    try:
        log("resolve_superflixapi: Processing {}".format(url[:100]))

        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')

        content_type = path_parts[0] if len(path_parts) > 0 else None
        content_id = path_parts[1] if len(path_parts) > 1 else None

        if not content_id:
            log("resolve_superflixapi: Could not parse content ID")
            return None

        html, final_url = fetch(url, referer="https://vidsrc.win/")
        if not html:
            log("resolve_superflixapi: Failed to fetch page")
            return None

        page_token_match = re.search(r'var PAGE_TOKEN\s*=\s*"([^"]+)"', html)
        csrf_token_match = re.search(r'var CSRF_TOKEN\s*=\s*"([^"]+)"', html)
        api_url_match = re.search(r'var API_URL_SOURCE\s*=\s*"([^"]+)"', html)
        content_type_match = re.search(r'var CONTENT_TYPE\s*=\s*"([^"]+)"', html)
        content_id_match = re.search(r'var INITIAL_CONTENT_ID\s*=\s*(\d+)', html)

        page_token = page_token_match.group(1) if page_token_match else None
        csrf_token = csrf_token_match.group(1) if csrf_token_match else None
        api_source = api_url_match.group(1) if api_url_match else "/player/source"
        ct = content_type_match.group(1) if content_type_match else "filme"
        initial_id = content_id_match.group(1) if content_id_match else content_id

        if not page_token:
            log("resolve_superflixapi: No PAGE_TOKEN found")
            return None

        headers = {
            "Referer": final_url or url,
            "Origin": "https://" + parsed.netloc,
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        if csrf_token:
            headers["X-CSRF-TOKEN"] = csrf_token

        api_url = "https://" + parsed.netloc + api_source

        data = {
            "video_id": initial_id,
            "page_token": page_token,
            "host": parsed.netloc,
            "site": parsed.netloc,
            "_token": csrf_token or "",
            "type": ct,
        }

        log("resolve_superflixapi: Calling API: {}".format(api_url))
        api_response, _ = fetch(api_url, referer=final_url, extra_headers=headers, post_data=data)

        if not api_response:
            return None

        try:
            result = json.loads(api_response)

            if result and result.get('data') and result['data'].get('video_url'):
                stream_url = result['data']['video_url']
                log("resolve_superflixapi: Found video URL: {}".format(stream_url[:100]))
                return _correct_stream_url(stream_url)

            if result and result.get('video_url'):
                stream_url = result['video_url']
                log("resolve_superflixapi: Found video URL: {}".format(stream_url[:100]))
                return _correct_stream_url(stream_url)

        except Exception:
            log("resolve_superflixapi: Not JSON, trying regex")
            stream_url = find_m3u8(api_response) or find_mp4(api_response)
            if stream_url:
                log("resolve_superflixapi: Found stream URL in response: {}".format(stream_url[:100]))
                return _correct_stream_url(stream_url)

        for iframe_url in extract_iframes(html, final_url or url):
            result = resolve_host(iframe_url)
            if result:
                return result

        stream_url = find_m3u8(html) or find_mp4(html)
        if stream_url:
            return _correct_stream_url(stream_url)

        return None

    except Exception as e:
        log("resolve_superflixapi error: {}".format(e))
        return None


# ─── ZXC Stream Resolver ─────────────────────────────────────────────────────

def resolve_zxcstream(url):
    """
    Resolver for zxcstream.xyz and similar Next.js-based players.
    Extracts the video URL from the React app's data.
    """
    try:
        log("resolve_zxcstream: Processing {}".format(url[:100]))

        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')

        media_type = path_parts[1] if len(path_parts) > 1 else None
        content_id = path_parts[2] if len(path_parts) > 2 else None

        if not content_id:
            log("resolve_zxcstream: Could not parse content ID")
            return None

        html, final_url = fetch(url, referer="https://vidsrc.win/")
        if not html:
            log("resolve_zxcstream: Failed to fetch page")
            return None

        next_data_match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if next_data_match:
            try:
                data = json.loads(next_data_match.group(1))

                def find_streams(obj, path=""):
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            if key in ['url', 'src', 'source', 'video', 'stream', 'playlist', 'file']:
                                if isinstance(value, str) and ('.m3u8' in value or '.mp4' in value):
                                    log("resolve_zxcstream: Found URL in Next.js data at {}: {}".format(path + '.' + key, value[:100]))
                                    return value
                            result = find_streams(value, path + '.' + key)
                            if result:
                                return result
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj):
                            result = find_streams(item, path + '[' + str(i) + ']')
                            if result:
                                return result
                    return None

                stream_url = find_streams(data)
                if stream_url:
                    return _correct_stream_url(stream_url)

            except Exception:
                log("resolve_zxcstream: Failed to parse __NEXT_DATA__")

        video_match = re.search(r'<video[^>]*src=["\']([^"\']+)["\']', html, re.I)
        if video_match:
            stream_url = video_match.group(1)
            log("resolve_zxcstream: Found video element: {}".format(stream_url[:100]))
            return _correct_stream_url(stream_url)

        stream_url = find_m3u8(html) or find_mp4(html)
        if stream_url:
            return _correct_stream_url(stream_url)

        for iframe_url in extract_iframes(html, final_url or url):
            result = resolve_host(iframe_url)
            if result:
                return result

        return None

    except Exception as e:
        log("resolve_zxcstream error: {}".format(e))
        return None


def resolve_braflix(url):
    """
    Resolver for braflix.win and similar players.
    """
    try:
        log("resolve_braflix: Processing {}".format(url[:100]))

        html, final_url = fetch(url, referer="https://vidsrc.win/")
        if not html:
            return None

        patterns = [
            r'(https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)',
            r'"url"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
            r'"file"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
            r'"src"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for stream_url in matches:
                stream_url = _correct_stream_url(stream_url.replace('\\/', '/').replace('&amp;', '&'))
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_braflix: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        for iframe_url in extract_iframes(html, final_url or url):
            result = resolve_host(iframe_url)
            if result:
                return result

        return find_m3u8(html) or find_mp4(html)

    except Exception as e:
        log("resolve_braflix error: {}".format(e))
        return None


def resolve_vidcore(url):
    """Resolver for vidcore.io and similar React-based players"""
    try:
        log("resolve_vidcore: Processing {}".format(url[:100]))

        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')

        media_type = path_parts[0] if len(path_parts) > 0 else None
        media_id = path_parts[1] if len(path_parts) > 1 else None
        season = path_parts[2] if len(path_parts) > 2 else None
        episode = path_parts[3] if len(path_parts) > 3 else None

        if not media_id:
            log("resolve_vidcore: Could not parse media ID from URL")
            return None

        html, final_url = fetch(url, referer="https://vidsrc.win/")
        if not html:
            return None

        token_match = re.search(r'"en":"([^"]+)"', html)
        token = token_match.group(1) if token_match else None

        api_match = re.search(r'(https?://[^\s"\']+\.vidcore\.io/api/[^\s"\']+)', html, re.I)
        api_url = api_match.group(1) if api_match else None

        if not api_url:
            if media_type == 'tv':
                api_url = "https://vidcore.io/api/play?id={}&type=tv&season={}&episode={}".format(media_id, season, episode)
            else:
                api_url = "https://vidcore.io/api/play?id={}&type=movie".format(media_id)

        headers = {
            "Referer": "https://vidcore.io/",
            "Origin": "https://vidcore.io",
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
        }

        if token:
            headers["Authorization"] = "Bearer {}".format(token)
            api_url += "&token={}".format(token)

        log("resolve_vidcore: Calling API: {}".format(api_url[:100]))
        api_response, _ = fetch(api_url, referer="https://vidcore.io/", extra_headers=headers)

        if not api_response:
            return None

        try:
            data = json.loads(api_response)

            stream_urls = []

            if isinstance(data, dict):
                for key in ['url', 'file', 'src', 'source', 'hls', 'playlist', 'stream', 'stream_url', 'video_url', 'm3u8']:
                    if key in data and isinstance(data[key], str):
                        if '.m3u8' in data[key] or '.mp4' in data[key]:
                            stream_urls.append(data[key])

                if 'sources' in data and isinstance(data['sources'], list):
                    for source in data['sources']:
                        if isinstance(source, dict):
                            for key in ['url', 'file', 'src']:
                                if key in source and isinstance(source[key], str):
                                    if '.m3u8' in source[key] or '.mp4' in source[key]:
                                        stream_urls.append(source[key])

                if 'data' in data and isinstance(data['data'], dict):
                    for key in ['url', 'file', 'src', 'source', 'hls', 'playlist', 'stream', 'stream_url', 'video_url']:
                        if key in data['data'] and isinstance(data['data'][key], str):
                            if '.m3u8' in data['data'][key] or '.mp4' in data['data'][key]:
                                stream_urls.append(data['data'][key])

                    if 'sources' in data['data'] and isinstance(data['data']['sources'], list):
                        for source in data['data']['sources']:
                            if isinstance(source, dict):
                                for key in ['url', 'file', 'src']:
                                    if key in source and isinstance(source[key], str):
                                        if '.m3u8' in source[key] or '.mp4' in source[key]:
                                            stream_urls.append(source[key])
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for key in ['url', 'file', 'src', 'source']:
                            if key in item and isinstance(item[key], str):
                                if '.m3u8' in item[key] or '.mp4' in item[key]:
                                    stream_urls.append(item[key])

            if not stream_urls:
                stream_urls = extract_stream_urls_from_text(api_response)

            for stream_url in stream_urls:
                stream_url = _correct_stream_url(stream_url)
                if not _is_placeholder_media_url(stream_url):
                    log("resolve_vidcore: Found stream URL: {}".format(stream_url[:100]))
                    return stream_url

        except Exception:
            stream = find_m3u8(api_response) or find_mp4(api_response)
            if stream:
                return stream

        stream = find_m3u8(html) or find_mp4(html)
        if stream:
            return stream

        return None
    except Exception as e:
        log("resolve_vidcore error: {}".format(e))
        return None


# ─── Helper: Extract stream URLs from text (for API responses) ──────────────

def extract_stream_urls_from_text(text):
    """Extract stream URLs from text using regex patterns"""
    if not text or not isinstance(text, str):
        return []

    urls = []
    patterns = [
        r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.ts[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+master\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+index-f[0-9]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/pl/[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/hls2/[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/hls/[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/e/[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/playlist/[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/api\?[^\s"\'<>]+&seg=[^\s"\'<>]+\.(?:m3u8|ts)[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.hakunaymatata\.com[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/convert-h264/[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/bt/[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/resource/[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.polarcandy\.top[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/vd/[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.1x2\.space[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.scalablecontentengine\.site[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.panoplypalaver\.site[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.1shows\.app[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.netrocdn\.site[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.shows\.st[^\s"\'<>]+\.(?:m3u8|ts)[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.streamrk\.site[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.vaplayer\.ru[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.streamdata\.vaplayer\.ru[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.moviepire\.co[^\s"\'<>]+\.mp4[^\s"\'<>]*',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.I)
        for match in matches:
            url = match.replace('\\/', '/').replace('&amp;', '&').strip()
            if url and url not in urls:
                urls.append(url)

    return urls

# ─── OnlyFlix server tabs — vidsrc-family players ──────────────────────────
# NEW (2026-09-24): the four embed hosts behind onlyflix.to's server tabs
# (vidfast.vc / vidapi.xyz / share.cdnm.ink / sv2.nontongo.{stream,day}),
# previously unhandled. Generalized resolve_vidcore — the "v"-family
# players are clones of one codebase.

def _ofx_expand_master(stream_url, referer_for_fetch):
    """If the URL is an HLS master playlist, fetch it, stash the real
    variants in _quality_tls (same contract as resolve_streamruby, so
    extract_stream's quality readback works), and return the best
    variant URL. Non-master URLs are returned untouched."""
    try:
        body, _ = fetch(stream_url, referer=referer_for_fetch)
    except Exception:
        body = None
    if not body or "#EXT-X-STREAM-INF" not in body:
        return stream_url
    variants = _parse_hls_master_variants(stream_url, body)
    if not variants:
        return stream_url
    _quality_tls.variants = [u for _, u in variants]
    log("resolve_vidsrc_family: expanded master into {} variant(s)".format(len(variants)))
    return variants[0][1]


_vsf_in_progress = threading.local()  # [PATCH H1]


def resolve_movy_api(url, referer=None):
    """Movy's own city servers (api.wecollege.net/<city>/sources): seed +
    custom stream cipher, see extractors/movy_api.py."""
    from .movy_api import resolve_movy
    return resolve_movy(url, referer)


def resolve_vidvm_family(url, referer=None):
    """vidcore.io / vidcore.net / vidfast.vc: the player builds its API
    calls inside a hidden bytecode VM; extractors/vidvm.py runs that VM
    here (no browser, no PC helper)."""
    from .vidvm import resolve_vid_vm
    res = resolve_vid_vm(url, referer)
    return res[0] if res else None


def resolve_vidsrc_family(url, referer=None):
    """
    [OnlyFlix playback] vidfast.vc / vidapi.xyz / share.cdnm.ink /
    sv2.nontongo.{stream,day} (captures 2026-09-24). Generalized
    resolve_vidcore: vidcore.io/vidcloud.icu/embed.su already share one
    resolver in this file — these are further members of the same player
    family, so the api/play + "en" token contract is tried against
    whatever host the URL carries. Referer-aware per resolve_host's
    convention; defaults to onlyflix.to (vidfast's hydration payload
    carries from="https://onlyflix.to/").
    """
    parsed = urlparse(url)
    host = parsed.netloc

    # [PATCH H1] recursion/cycle guard -- family members can iframe back to
    # themselves or each other; without this, Stage 6 below recurses into
    # resolve_host() unbounded and blows the call stack (confirmed by a
    # real crash: "maximum recursion depth exceeded", which then poisons
    # every other fetch on the same thread until the stack unwinds).
    in_progress = getattr(_vsf_in_progress, "hosts", None)
    if in_progress is None:
        in_progress = set()
        _vsf_in_progress.hosts = in_progress
    if host in in_progress:
        log("resolve_vidsrc_family: {} already in progress on this thread, skipping to avoid recursion".format(host))
        return None
    in_progress.add(host)
    try:
        log("resolve_vidsrc_family: Processing {}".format(url[:100]))
        parts = [p for p in parsed.path.strip('/').split('/') if p]
        query = parse_qs(parsed.query)

        imdb_id, season, episode = "", "", ""
        for i, p in enumerate(parts):
            if re.match(r'^tt\d+', p):
                imdb_id = p
                tail = parts[i + 1:]
                if len(tail) >= 2 and tail[0].isdigit() and tail[1].isdigit():
                    season, episode = tail[0], tail[1]
                break
        if not season:
            season = (query.get("season") or [""])[0]
            episode = (query.get("episode") or [""])[0] or episode
        mtype = "tv" if (season or "tv" in parts) else "movie"

        page_referer = referer or "https://onlyflix.to/"
        html, final_url = fetch(url, referer=page_referer)
        if not html:
            return None

        candidates = []

        def _found(u):
            u = _correct_stream_url(str(u).replace("\\/", "/"))
            if u and not _is_placeholder_media_url(u) and u not in candidates:
                candidates.append(u)

        # 1. direct media, RSC escapes folded (app-router "https:\/\/...")
        scan = (html or "").replace("\\/", "/")
        for u in (find_m3u8_all(scan) or find_mp4_all(scan) or []):
            _found(u)

        # 2. the "en" token (present in the raw SSR html)
        tok_m = re.search(r'["\']en["\']\s*:\s*["\']([A-Za-z0-9_\-]{40,})["\']', html)
        token = tok_m.group(1) if tok_m else ""
        log("resolve_vidsrc_family: {} id={} en={}".format(
            host, imdb_id or "-", "present" if token else "absent"))

        # 3. family API: prefer an API URL found ON the page (mirrors
        #    resolve_vidcore's api_match), else the api/play default.
        #    Skipped for nontongo (soap2day family, different player).
        if imdb_id and "nontongo" not in host:
            api_m = re.search(
                r'(https?://' + re.escape(host) + r'/api/[^\s"\']+)', html, re.I)
            if api_m:
                api_url = api_m.group(1)
            else:
                api_url = "https://{}/api/play?id={}&type={}".format(
                    host, imdb_id, mtype)
                if mtype == "tv" and season:
                    api_url += "&season={}&episode={}".format(season, episode)
            headers = {
                "Referer": "https://{}/".format(host),
                "Origin": "https://{}".format(host),
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
            }
            if token:
                headers["Authorization"] = "Bearer {}".format(token)
                api_url += "&token={}".format(token)
            try:
                api_body, _ = fetch(api_url, referer=url, extra_headers=headers)
            except Exception:
                api_body = None
            if api_body:
                for u in extract_stream_urls_from_text(api_body):
                    _found(u)

        # 4. __NEXT_DATA__ deep walk (pages-router members)
        nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                       html, re.S)
        if nd:
            try:
                data = json.loads(nd.group(1))

                def _walk(obj):
                    if isinstance(obj, dict):
                        for key in ("url", "src", "source", "file", "stream", "playlist"):
                            v = obj.get(key)
                            if isinstance(v, str) and (".m3u8" in v or ".mp4" in v):
                                return v
                        for v in obj.values():
                            r = _walk(v)
                            if r:
                                return r
                    elif isinstance(obj, list):
                        for item in obj:
                            r = _walk(item)
                            if r:
                                return r
                    return None

                found = _walk(data)
                if found:
                    _found(found)
            except Exception:
                pass

        # 5. packed scripts
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                _found(best)

        # 6. family members iframe each other — recurse
        for embed_url in extract_iframes(html, final_url or url):
            if not embed_url.startswith("http"):
                continue
            if any(h in embed_url for h in
                   ("vidfast.", "vidapi.", "cdnm.ink", "nontongo.")):
                result = resolve_host(embed_url, referer=page_referer)
                if result:
                    if "|" in result:
                        return result
                    _found(result)

        if not candidates:
            return None
        for u in candidates:
            if ".m3u8" in u:
                return _ofx_expand_master(u, "https://{}/".format(host))
        return candidates[0]
    except Exception as e:
        log("resolve_vidsrc_family error: {}".format(e))
        return None
    finally:
        in_progress.discard(host)  # [PATCH H1]


# ─── OnlyFlix Server 1 — share.cdnm.ink → <sub>.cdnmovies-stream.online ────
# [2026-10-21 capture] share.cdnm.ink/embed/imdb/<tt> only wraps
#   <iframe id="player" data-src="https://ugly-turkey.cdnmovies-stream.online/
#           imdb/<tt>/iframe?translation_id=145&ftp_server_id=5&poster=...">
# whose page builds a PlayerJS player with
#   file: '#2<base64 with "//"+40-char trash chunks spliced in>'
# Decoded: "[240p]https://s1.cdnmvs.online/<sig>:<exp>/videos_new/.../240.mp4/
# index-v1-a1.m3u8,[360p]...,[1080p]...". The page only *hides* 480p+ in the
# menu (forbidden_quality) -- every quality carries its own signed link.
# Series: the decoded value is a PlayerJS JSON playlist (folders of episodes).

_PJS_TRASH = (  # PlayerJS "bk" keys as seen base64'd on cdnmovies (identical in every capture)
    "d05wMndCVE5jUFJRdlRDMF9DcHhDc3FfOFQxdTlR", "bWQtT2QyRzlSV09nU2E1SG9CU1NiV3JDeUlxUXlZ",
    "UnlUd3RmMTVfR0xFc1h4bnBVNExqamQwUmVZLVZI", "a3p1T1lRcUJfUVNPTC14ek5fS3oza2tna0hoSGl0",
    "Ni14UVdNaDdlcnRMcDh0X005aHVVRGsxTTBWcllK")


def _pjs_try(t):
    try:
        out = base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8")
    except Exception:
        return ""
    if "http" in out and all(31 < ord(c) < 127 or c in "\r\n\t" or ord(c) > 159 for c in out):
        return out
    return ""


def _pjs_decode(enc):
    """PlayerJS '#2' file: strip the '//'+base64(trash) chunks, base64-decode.
    Chunks can be nested (a chunk spliced into the middle of another, seen on
    series pages), so they are removed innermost-first until none is left."""
    s = (enc or "").strip()
    if s.startswith("#2"):
        s = s[2:]
    elif s.startswith("#"):
        return ""
    t = s
    while True:
        u = t
        for k in _PJS_TRASH:
            u = u.replace("//" + k, "")
        if u == t:
            break
        t = u
    out = _pjs_try(t)
    if out:
        return out
    for n in (40, 32, 44, 36, 48, 28):           # unknown keys: same length, innermost first
        t = s
        rx = re.compile(r'//(?:(?!//)[A-Za-z0-9+/=]){%d}' % n)
        for _ in range(64):
            u = rx.sub("", t, count=1)
            if u == t:
                break
            t = u
            out = _pjs_try(t)
            if out:
                return out
    return ""


def _pjs_quality_list(file_value):
    """'[360p]url,[720p]url' (or 'url or url') -> [(label, url)] best first."""
    out = []
    for m in re.finditer(r'\[([^\]]*)\]\s*(https?://[^,\[\s]+)', file_value or ""):
        out.append((m.group(1).strip() or "Auto", m.group(2).split(" or ")[0]))
    if not out:
        for u in re.findall(r'https?://[^,\s]+', file_value or ""):
            out.append(("Auto", u))

    def _rank(lbl):
        m = re.search(r'(\d{3,4})', lbl)
        return int(m.group(1)) if m else 0
    out.sort(key=lambda p: -_rank(p[0]))
    return out


def _pjs_pick_episode(playlist, season, episode):
    """Walk a PlayerJS JSON playlist for season/episode; returns its 'file'."""
    def _num(t):
        m = re.search(r'(\d+)', str(t or ""))
        return m.group(1) if m else ""
    seasons = playlist if isinstance(playlist, list) else []
    for s in seasons:
        if not isinstance(s, dict):
            continue
        if s.get("folder") and (not season or _num(s.get("title")) == str(int(season))
                                or len(seasons) == 1):
            for e in s["folder"]:
                if isinstance(e, dict) and e.get("file") and (
                        not episode or _num(e.get("title")) == str(int(episode))):
                    return e["file"]
                if isinstance(e, dict) and e.get("folder"):        # dub/voice level
                    r = _pjs_pick_episode([dict(title=s.get("title"), folder=e["folder"])],
                                          season, episode)
                    if r:
                        return r
        elif s.get("file") and not season:
            return s["file"]
    return ""


_IFRAME_HDRS = {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "cross-site", "Upgrade-Insecure-Requests": "1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


_cdnm_dead = {}


def resolve_cdnm(url, referer=None):
    if any(t > time.time() for t in _cdnm_dead.values()):
        log("resolve_cdnm: video server unreachable a few minutes ago, skipping")
        return None
    try:
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        season = (q.get("season") or q.get("s") or [""])[0]
        episode = (q.get("episode") or q.get("e") or [""])[0]
        page_url, page_ref = url, (referer or "https://onlyflix.to/")
        if "cdnm.ink" in parsed.netloc:
            try:
                html, _f = fetch(url, referer=page_ref, timeout=12, max_retries=1,
                                 extra_headers=_IFRAME_HDRS)
            except Exception:
                html = ""
            m = re.search(r'<iframe[^>]+(?:data-src|src)="(https?://[^"]*cdnmovies[^"]+)"',
                          html or "", re.I)
            if m:
                page_url = html_module.unescape(m.group(1))
            else:
                # the wrapper is optional: its player URL is fixed apart from the ids
                tt = re.search(r'(tt\d+)', parsed.path)
                if not tt:
                    log("resolve_cdnm: no cdnmovies player on {} ({} bytes)".format(
                        url[:90], len(html or "")))
                    return None
                page_url = "https://ugly-turkey.cdnmovies-stream.online/imdb/{}/iframe?{}translation_id=145&ftp_server_id=5".format(
                    tt.group(1), "season={}&episode={}&".format(season, episode) if season else "")
                log("resolve_cdnm: wrapper gave no player ({} bytes), opening {} directly".format(
                    len(html or ""), page_url[:90]))
            if season and "season=" not in page_url:
                page_url += "&season={}&episode={}".format(season, episode)
            page_ref = "https://{}/".format(parsed.netloc)
        html, final = fetch(page_url, referer=page_ref, timeout=12, max_retries=1,
                            extra_headers=_IFRAME_HDRS)
        m = re.search(r'''\bfile\s*:\s*(['"])(.+?)\1''', html or "", re.S)
        if not m:
            log("resolve_cdnm: no PlayerJS file on {} ({} bytes)".format(
                page_url[:90], len(html or "")))
            return None
        raw = m.group(2)
        value = _pjs_decode(raw) if raw.startswith("#") else raw
        if value.lstrip().startswith("["):
            try:
                pl = json.loads(value)
                value = _pjs_pick_episode(pl, season, episode) or ""
                if value.startswith("#"):
                    value = _pjs_decode(value)
            except ValueError:
                pass                     # plain "[360p]url,..." list
        variants = _pjs_quality_list(value)
        if not variants:
            log("resolve_cdnm: nothing playable in the decoded file ({} chars)".format(len(value)))
            return None
        origin = "{0.scheme}://{0.netloc}".format(urlparse(final or page_url))
        suffix = "|Referer={}/&Origin={}".format(origin, origin)
        # every quality has its own signed link; use the best one that answers
        hdrs = {"Origin": origin, "User-Agent": _VIDAPI_HDRS["User-Agent"],
                "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site"}
        suffix += "&User-Agent=" + quote(hdrs["User-Agent"], safe="")
        for i, (lbl, u) in enumerate(variants):
            body = None
            t0 = time.time()
            try:
                body, _ = fetch(u, referer=origin + "/", timeout=8, max_retries=0,
                                extra_headers=hdrs)
            except Exception:
                body = None
            if body and "#EXTM3U" in body:
                ok = variants[i:]
                _quality_tls.variants = [v + suffix for _, v in ok]
                log("resolve_cdnm: {} quality(ies), playing {}".format(len(ok), lbl))
                return u + suffix
            if time.time() - t0 > 6:
                # a timeout is the connection, not this quality. Measured
                # 2026-10-21: s1.cdnmvs.online (50.7.204.106, same answer from
                # the ISP's DNS and 1.1.1.1) never answers on the user's line --
                # box and PC browser alike. Give up now and skip the server for
                # 10 minutes so the next server is tried at once.
                _cdnm_dead[urlparse(u).netloc] = time.time() + 600
                log("resolve_cdnm: {} timed out -- {} unreachable from this line, skipping it for 10 min".format(
                    lbl, urlparse(u).netloc))
                return None
            log("resolve_cdnm: {} did not answer, trying the next quality".format(lbl))
        return None
    except Exception as e:
        log("resolve_cdnm error: {}".format(e))
        return None


# ─── OnlyFlix Server 4 — sv2.nontongo.stream (→ sv4.nontongo.day) ─────────
# [2026-10-21 capture] /soap/{movie/<tt> | tv/<tt>/<s>/<e>} carries
#   const cfg = {"type":"movie","tmdbId":1263337,"season":1,"episode":1,
#     "servers":[{"movie_url":".../01russia/multisourcesoap.php?id={tmdb_id}&type=movie",
#                 "tv_url":"...?id={tmdb_id}&season={season}&episode={episode}&type=tv"}]}
# and that page lists plain sources:
#   const sources = [{"html":"Soap2Cloud","file":"https://<x>.soapsoap123.workers.dev/cdn/<tok>#file.m3u8"}, ...]
# (HLS master, 1080/720/...; segments on relay.medmedia04.help). The player
# requests them with Referer/Origin https://sv4.nontongo.day.

def resolve_nontongo(url, referer=None):
    try:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        season = episode = ""
        if "tv" in parts:
            tail = parts[parts.index("tv") + 1:]
            if len(tail) >= 3:
                season, episode = tail[1], tail[2]
        multi = ""
        if "multisourcesoap" in url:
            multi, soap_url = url, url
        else:
            soap_url = url
            hosts = [parsed.netloc] + [h for h in ("sv4.nontongo.day", "sv2.nontongo.stream")
                                       if h != parsed.netloc]
            for host in hosts:
                soap_url = "{}://{}{}".format(parsed.scheme or "https", host, parsed.path)
                html, final = fetch(soap_url, referer=referer or "https://onlyflix.to/",
                                    timeout=12, max_retries=0)
                soap_url = final or soap_url
                m = re.search(r'const\s+cfg\s*=\s*(\{.*?\})\s*;\s*\n', html or "", re.S) or \
                    re.search(r'const\s+cfg\s*=\s*(\{.*?\});', html or "", re.S)
                if not m:
                    log("resolve_nontongo: no player config on {} ({} bytes)".format(
                        soap_url[:80], len(html or "")))
                    continue
                cfg = json.loads(m.group(1))
                tmdb = str(cfg.get("tmdbId") or "")
                s = season or str(cfg.get("season") or 1)
                e = episode or str(cfg.get("episode") or 1)
                is_tv = (cfg.get("type") == "tv") or bool(season)
                for srv in cfg.get("servers") or []:
                    tpl = srv.get("tv_url" if is_tv else "movie_url") or ""
                    if tpl and tmdb:
                        multi = (tpl.replace("{tmdb_id}", tmdb)
                                 .replace("{season}", s).replace("{episode}", e))
                        break
                if multi:
                    break
        if not multi:
            return None
        mhost = "{0.scheme}://{0.netloc}".format(urlparse(multi))
        html, _f = fetch(multi, referer=soap_url, timeout=12, max_retries=1)
        m = re.search(r'const\s+sources\s*=\s*(\[.*?\])\s*;', html or "", re.S)
        if not m:
            log("resolve_nontongo: no sources on {} ({} bytes)".format(multi[:90], len(html or "")))
            return None
        sources = [s for s in json.loads(m.group(1))
                   if isinstance(s, dict) and str(s.get("file", "")).startswith("http")]
        log("resolve_nontongo: {} source(s): {}".format(
            len(sources), ", ".join(str(s.get("html")) for s in sources)))
        suffix = "|Referer={}/&Origin={}".format(mhost, mhost)
        for src in sources:
            u = src["file"].split("#", 1)[0]
            try:
                body, _ = fetch(u, referer=mhost + "/", timeout=10, max_retries=0,
                                extra_headers={"Origin": mhost})
            except Exception:
                body = ""
            if not body or "#EXTM3U" not in body:
                log("resolve_nontongo: {} gave no playlist".format(src.get("html")))
                continue
            variants = _parse_hls_master_variants(u, body)
            if variants:
                _quality_tls.variants = [v + suffix for _, v in variants]
                log("resolve_nontongo: {} -> {} quality(ies)".format(src.get("html"), len(variants)))
                return variants[0][1] + suffix
            return u + suffix
        return None
    except Exception as e:
        log("resolve_nontongo error: {}".format(e))
        return None


def megamax_list_mirrors(url, referer=None):
    """[PATCH MM2] List EVERY mirror megamax.me's (q7v3k8m2p1.top)
    file-download aggregator exposes, across ALL qualities -- not just
    the first one that happens to resolve. Extracted out of the
    original resolve_megamax() (see that function's docstring, kept
    below, for the full reverse-engineered protocol writeup) so the
    scraping logic can be shared between:
      - resolve_megamax(): auto-pick wrapper (unchanged external
        contract -- returns the first mirror that resolve_host() can
        actually resolve).
      - arabseed.py's get_page(): wants the FULL list, so the UI can
        show every voe/byse/mixdrop/uqload/etc. choice across every
        quality, not just one auto-picked link.

    Returns a list of dicts, ordered highest-resolution-attachment
    first then in the mirrors[] order the site itself returns per
    attachment:
        {"quality": <att label, e.g. "720p">, "driver": <str>,
         "url": <normalized https:// link, NOT yet resolved>}

    Returns [] (not None) on any failure, so callers can safely
    iterate without a None-check. rapidgator/nitroflare are premium
    cyberlockers with no video-embed resolver and are skipped, same
    as before.
    """
    try:
        if "/iframe/" in url:
            download_url = url.replace("/iframe/", "/download/", 1)
        elif "/download/" in url:
            download_url = url
        else:
            return []

        html, final = fetch(download_url, referer=referer or download_url)
        if not html:
            return []

        page_m = re.search(
            r'<script[^>]+data-page=["\']app["\'][^>]*>(.*?)</script>',
            html, re.S)
        if not page_m:
            return []
        try:
            page_data = json.loads(page_m.group(1))
        except Exception:
            return []

        version = page_data.get("version")
        attachments = (page_data.get("props") or {}).get("attachments") or []
        if not version or not attachments:
            return []

        def _res_width(att):
            try:
                return int(str(att.get("resolution", "0x0")).split("x")[0])
            except Exception:
                return 0
        attachments = sorted(attachments, key=_res_width, reverse=True)

        base = final or download_url
        xsrf = ""
        cookie_m = re.search(r'XSRF-TOKEN=([^;]+)', get_cookie_header_for_url(base))
        if cookie_m:
            xsrf = unquote(cookie_m.group(1))

        # [PATCH MM3] vikingfile.com and vidhidevip.com (driver "earnvids")
        # looked like they should resolve -- both already had a
        # HOST_RESOLVERS entry and fetched real content fine in testing
        # (646KB and 26KB real pages, no Cloudflare block) -- but every
        # extraction path (specific resolver, generic fallback, iframe
        # chain) came back empty every time. Confirmed directly by the
        # user, 2026-10-14: on megamax's aggregator these two are
        # download-link mirrors, not embeddable video players -- there is
        # no stream to find on those pages, same as rapidgator/nitroflare
        # just below. Skipping them here so the UI doesn't offer a
        # "server" that can never produce a playable stream.
        #
        # [PATCH MM4] "send" (send.cm) is a DIFFERENT kind of failure --
        # unlike the three above, it's not structurally download-only; it
        # has a real resolve_send_cm() video resolver and would work fine
        # given a working connection to it. It fails specifically because
        # send.cm is behind Cloudflare and this plugin's proxy setting is
        # disabled (confirmed directly in the real device log: "Cloudflare
        # challenge detected ... but proxy is NOT configured"). User asked
        # for it skipped here too since it never works without a proxy on
        # their setup. Left as its own, separately-commented entry (rather
        # than folded into the list above) in case a proxy gets configured
        # later, at which point this one specifically is worth revisiting.
        _skip_drivers = ("rapidgator", "nitroflare", "vikingfile", "earnvids", "send")
        out = []

        for att in attachments:
            hash_id = att.get("hashId")
            if not hash_id:
                continue
            quality_label = att.get("label") or ""
            headers = {
                "Content-Type": "application/json",
                "Accept": "text/html, application/xhtml+xml",
                "X-Requested-With": "XMLHttpRequest",
                "X-Inertia": "true",
                "X-Inertia-Version": version,
                "X-Inertia-Partial-Data": "download",
                "X-Inertia-Partial-Component": "files/mirror/download",
                "X-Inertia-Except-Once-Props": "file,attachments,ads",
            }
            if xsrf:
                headers["X-XSRF-TOKEN"] = xsrf
            resp, _ = fetch(download_url, referer=base,
                             post_data=json.dumps({"attach": hash_id}),
                             extra_headers=headers)
            if not resp:
                continue
            try:
                resp_data = json.loads(resp)
            except Exception:
                continue
            mirrors = (((resp_data.get("props") or {}).get("download") or {})
                       .get("data") or {}).get("mirrors") or []
            for mirror in mirrors:
                driver = (mirror.get("driver") or "").lower()
                if driver in _skip_drivers:
                    continue
                link = mirror.get("link") or ""
                if link.startswith("//"):
                    link = "https:" + link
                if not link.startswith("http"):
                    continue
                out.append({
                    "quality": quality_label,
                    "driver": mirror.get("driver") or driver,
                    "url": link,
                    "referer": base,
                })
        return out
    except Exception as e:
        log("megamax_list_mirrors error: {}".format(e))
        return []


def resolve_megamax(url, referer=None):
    """[PATCH MM1] megamax.me's own file-download aggregator, seen at
    q7v3k8m2p1.top, reached from arabseed.wine's single "MegaMax"
    watch-page server. Confirmed directly from a real network
    capture, 2026-10-13:

      - GET /iframe/<hash> (the watch-page embed) and GET /download/<hash>
        share the exact same <hash> -- same file, two page types on the
        same Vue/Inertia.js app.
      - /download/<hash>'s initial HTML embeds a JSON blob
        (<script data-page="app">) with an Inertia "version" string and
        an "attachments" list (each: hashId/label/resolution/size) --
        one per quality -- but NOT the mirror links themselves.
      - Clicking a quality's "download" button fires a POST to that
        SAME /download/<hash> URL (Laravel/Inertia "partial reload"
        convention): JSON body {"attach": <attachmentHashId>}, with
        X-Inertia / X-Inertia-Partial-Data=download /
        X-Inertia-Partial-Component=files/mirror/download /
        X-Inertia-Version headers, plus an X-XSRF-TOKEN header that is
        just the XSRF-TOKEN cookie value URL-decoded (standard Laravel
        double-submit CSRF -- no secret to derive, the opener's shared
        cookiejar already holds the cookie from the GET above). The
        response is JSON with props.download.data.mirrors:
        [{driver, link}, ...] -- the real voe/byse/mixdrop/uqload/etc.
        embed links the site's own UI shows.

    Every "driver" name seen in the real capture (send, streamruby,
    Vikingfile, uqload, streamhg, mixdrop, doodstream, byse, earnvids,
    voe) already matches an existing HOST_RESOLVERS entry by domain
    substring.

    [PATCH MM2] Scraping logic moved to megamax_list_mirrors() above
    (shared with arabseed.py's full-choice UI listing); this function
    is now a thin auto-pick wrapper over it, kept for backward
    compatibility with its existing HOST_RESOLVERS registration and
    external contract: iterate the mirrors (already highest-resolution
    attachment first) and hand each link to resolve_host() in order,
    returning the first one that actually resolves.
    """
    try:
        mirrors = megamax_list_mirrors(url, referer=referer)
        for mirror in mirrors:
            result = resolve_host(mirror["url"], referer=mirror.get("referer") or referer)
            if result:
                log("resolve_megamax: resolved via driver={}".format(mirror.get("driver")))
                return result
        return None
    except Exception as e:
        log("resolve_megamax error: {}".format(e))
        return None


# ─── Host dispatcher ──────────────────────────────────────────────────────────

HOST_RESOLVERS = {
    # ─── OnlyFlix server tabs — vidsrc-family players (2026-09-24) ──────
    "vidfast.vc":   resolve_vidvm_family,
    "vidapi.xyz":   resolve_vidapi,
    "player.imdb.su":      resolve_vidapi_player,     # 2026-10-21
    "nextgencloudfabric":  resolve_vidapi_player,
    "vscdn.xyz":    resolve_streamwish,
    "cdnm.ink":     resolve_cdnm,            # share.cdnm.ink → cdnmovies PlayerJS (2026-10-21)
    "cdnmovies-stream": resolve_cdnm,
    "nontongo":     resolve_nontongo,        # sv2.nontongo.stream → sv4.nontongo.day multisource

    "streamtape":  resolve_streamtape,
    "dood":        resolve_doodstream,
    "dsvplay":     resolve_doodstream,
    "d0o0d":       resolve_doodstream,
    "doods":       resolve_doodstream,
    "ds2play":     resolve_doodstream,
    "dooood":      resolve_doodstream,
    "playmogo":    resolve_doodstream,
    "playmogo.com": resolve_doodstream,
    "vidbom":      resolve_vidbom,
    "vidshare":    resolve_vidbom,
    "uqload":      resolve_uqload,
    "govid":       resolve_govid,
    "upstream":    resolve_upstream,
    "mixdrop":     resolve_mixdrop,
    "mxdrop":      resolve_mixdrop,   # [PATCH M2] confirmed against a real mxdrop.top capture
    "mxdrop.top":  resolve_mixdrop,
    "miixdrop":    resolve_mixdrop,
    "miixdrop.top": resolve_mixdrop,
    "voe":         resolve_voe,
    # [PATCH AS5] VOE rotates its embed domain constantly while keeping
    # the exact same obfuscated-payload player template -- resolve_voe
    # is already domain-agnostic (fixed referer, payload-pattern based),
    # so a newly-seen vanity mirror just needs its own dispatch key
    # rather than any new resolver logic. Confirmed as a VOE embed by the
    # site's own "voe" server label on a real arabseed.wine watch page,
    # 2026-10-13.
    "megankiddifference.com": resolve_voe,
    "streamruby":  resolve_streamruby,
    "stmruby":     resolve_streamruby,   # [PATCH M1] confirmed via exact filecode match
    "stmruby.com": resolve_streamruby,
    "hgcloud":     resolve_hgcloud,
    "masukestin":  resolve_masukestin,
    "masukestin.com": resolve_masukestin,
    "71stream.one": resolve_71stream,  # [FIX] confirmed against a real Fivetv embed capture
    "vidtube":     resolve_vidtube,
    "fastvip.space":       resolve_fastvip,
    "akhbarworld.online":  resolve_fastvip,
    "streamwish":  resolve_streamwish,
    "wishfast":    resolve_streamwish,
    "filelion":    resolve_streamwish,
    "filelions":   resolve_streamwish,
    "vidhide":     resolve_govid,
    "vidhide.com": resolve_govid,
    "vidhide.pro": resolve_govid,
    "streamhide":  resolve_govid,
    "dhtpre":      resolve_streamwish,
    "embedrise":   resolve_streamwish,
    "hglamioz":    resolve_streamwish,
    "filemoon":    resolve_filemoon,
    "medixiru":              resolve_streamwish,
    "smoothpre":             resolve_streamwish,
    "gentlebrookmediagroup": resolve_streamwish,
    "bysekoze":              resolve_streamwish,
    "minochinos":            resolve_streamwish,
    "forafile":              resolve_streamwish,
    "hgplaycdn":             resolve_filemoon,    
    "lulustream":  resolve_lulustream,
    "ok.ru":       resolve_okru,
    "okru":        resolve_okru,
    "send.cm":           resolve_send_cm,
    "vikingfile":        resolve_vikingfile,
    "vikingfile.com":    resolve_vikingfile,    
    "vidguard":    resolve_vidguard,
    "vgfplay":     resolve_vidguard,
    "fastvid":     resolve_fastvid,
    "fastvid.cam": resolve_fastvid,
    "rpmvip":      resolve_rpmvip,
    "upshare":     resolve_upshare,
    "upn.one":     resolve_upshare,
    "cleantechworld": resolve_cleantechworld,
    "cleantechworld.shop": resolve_cleantechworld,
    "scdns":              resolve_scdns,
    "scdns.io":           resolve_scdns,
    "c.scdns.io":         resolve_scdns,
    "datahowa":           resolve_datahowa,
    "datahowa.asia":      resolve_datahowa,
    "govid.live":         resolve_govid,
    "downet":             resolve_downet,
    "downet.net":         resolve_downet,
    "tnmr.org":        resolve_tnmr,
    "tnmr":            resolve_tnmr,
    "mxcontent":       resolve_mxcontent,
    "mxcontent.net":   resolve_mxcontent,
    "delucloud":       resolve_delucloud,
    "delucloud.xyz":   resolve_delucloud,
    "savefiles":       resolve_savefiles,
    "savefiles.com":   resolve_savefiles,
    "abstream":        resolve_abstream,
    "abstream.to":     resolve_abstream,
    "byselapuix":      resolve_byselapuix,
    "byselapuix.com":  resolve_byselapuix,
    # [PATCH AS5] byseraguci.com confirmed, 2026-10-13, via a real
    # captured network log + matching HTML snapshot (arabseed.wine
    # "Bys" server): identical landing-page behavior to byselapuix.com
    # -- same exact JS/CSS asset bundle hashes (index-DocunfmE.js,
    # en-C5WysiHD.js, pow-DEJGtdh2.js, videoPagesBundle-*, etc.), same
    # /api/videos/<id>/settings + /details calls, and the real player
    # is the same plain static <iframe src="https://n1mwq.org/..."> --
    # same unrelated mirror domain byselapuix already follows. Other
    # "byse*" vanity domains are NOT assumed to match this (bysekoze
    # uses the StreamWish backend instead, and bysezejataos.com is a
    # confirmed-dead host in arabseed.py's BLOCKED_HOSTS) -- only this
    # specific domain is mapped here, on direct evidence.
    "byseraguci":      resolve_byselapuix,
    "byse":            resolve_byse_any,      # other byse* mirrors (shortest key: specific ones win)
    "byseraguci.com":  resolve_byselapuix,
    # [PATCH N1] n1mwq.org itself, for the case a server list or a future
    # mirror hands us the n1mwq.org iframe URL directly rather than a
    # byselapuix/byseraguci landing page that needs following first.
    "n1mwq":           resolve_n1mwq,
    "n1mwq.org":       resolve_n1mwq,
    "vidnest":         resolve_vidnest,
    "vidnest.fun":     resolve_vidnest,
    # [PATCH MM1] megamax.me's download-mirror aggregator, confirmed via
    # a real network capture of arabseed.wine's single "MegaMax" server
    # -- only this specific vanity domain is mapped, on direct evidence.
    "q7v3k8m2p1.top":  resolve_megamax,
    "dhcplay":         resolve_dhcplay,
    "dhcplay.com":     resolve_dhcplay,
    "mycima.cv":       resolve_azrak_mycima,   # [PATCH 121]
    "azrak.mycima.cv": resolve_azrak_mycima,
    "cybervynx":       resolve_hanerix_style,  # [PATCH E2] unverified against real markup -- see docstring
    "cybervynx.com":   resolve_hanerix_style,
    "egybests.live":   resolve_egybest_download_gateway,  # [PATCH E3]
    "sprintcdn":       resolve_sprintcdn,
    "sprintcdn.com":   resolve_sprintcdn,
    "aurorafieldnetwork": resolve_aurorafieldnetwork,
    "aurorafieldnetwork.store": resolve_aurorafieldnetwork,
    "go.akwam.com.co":  resolve_go_akwam,
    "go.akwam":         resolve_go_akwam,
    "hanerix":          resolve_hanerix_style,
    "hanerix.com":      resolve_hanerix_style,
    "cdn-video":        resolve_hanerix_style,
    "cdn-video.xyz":    resolve_hanerix_style,
    "vibuxer.com":      resolve_hanerix_style,
    "morencius.com":    resolve_hanerix_style,
    "luluvdo.com":      resolve_hanerix_style,
    "audinifer":        resolve_hanerix_style,
    "audinifer.com":    resolve_hanerix_style,
    "vidtube.one":      resolve_hanerix_style,
    "down.vidtube.one": resolve_hanerix_style,
    "earnvids.xyz":     resolve_hanerix_style,
    "vinovo.to":        resolve_vinovo,
    "vidaraa.cc":       resolve_vidaraa,
    "vidaraa":          resolve_vidaraa,
    # [PATCH VIDARA1] "vidara.to" (seen as a shahid4u.courses server, e.g.
    # https://vidara.to/e/<code>) has no resolver entry at all -- it fell
    # through to resolve_generic_embed, which found nothing (confirmed in
    # a real device log: resolver lookup never even matched, straight to
    # "Generic fallback for: vidara.to", then iframe-chain also failed).
    # resolve_vidaraa() builds its /api/stream request from the PASSED-IN
    # url's own netloc (not a hardcoded "vidaraa.cc"), so it is already
    # domain-portable -- the only question is whether vidara.to speaks the
    # same {filecode, device} POST -> {"streaming_url": ...} JSON API as
    # vidaraa.cc. The near-identical naming ("vidara.to" vs "vidaraa.cc",
    # both /e/<code> embed paths) strongly suggests the same underlying
    # platform under a rotated domain, and this is a safe bet either way:
    # if vidara.to's API differs, resolve_vidaraa() returns None exactly
    # like no resolver matching at all, and resolve_host() still falls
    # through to the same generic-embed + iframe-chain fallbacks that run
    # today -- this entry can only add a chance of success, never remove
    # one. Needs a real vidara.to embed-page capture to confirm outright;
    # flagged for the user to verify next time it's hit.
    "vidara.to":        resolve_vidaraa,
    "vidara":           resolve_vidaraa,

    # ─── Modern CDN resolvers from LINKS.txt ──────────────────────────────
    "streamrk.site":        resolve_streamrk,
    "moviepire.co":         resolve_moviepire,
    "hakunaymatata.com":    resolve_moviepire,
    "polarcandy.top":       resolve_polarcandy,
    "peakstorm.top":        resolve_polarcandy,
    "xpass.top":            resolve_xpass,
    "1x2.space":            resolve_xpass,
    "scalablecontentengine.site": resolve_scalablecontentengine,
    "nextgencloudfabric.com": resolve_nextgencloudfabric,
    "embedmaster.link":     resolve_embedmaster,
    "embdmstrplayer.com":   resolve_embedmaster,
    # [NEW] SFlix "VidSrc-Embed": skip the (box-blocked) host, go straight to the API
    "vidsrc-embed.ru":    resolve_vaplayer_api,
    # [2026-10-19] vidsrc.win's VidSrc-family players (vsembed.ru, vidsrc2.ru,
    # stellarconductornexus.com) load the same vaplayer source
    "vsembed.ru":         resolve_vaplayer_api,
    "vidsrc2.ru":         resolve_vaplayer_api,
    "stellarconductornexus.com": resolve_vaplayer_api,
    "vidbolt.xyz":        resolve_vidbolt,
    "api.wing.st":        resolve_wingst,
    "vidbolt.pro":        resolve_vidbolt,
    "yapgrid.com":        resolve_yapgrid,
    "cloudorchestranova.com": resolve_cloudorchestra,
    "panoplypalaver.site":  resolve_cloudorchestra,
    "viduki.net":           resolve_viduki,
    "1shows.app":           resolve_viduki,
    "moviesapi.to":         resolve_moviesapi,
    "netrocdn.site":        resolve_moviesapi,
    "shows.st":             resolve_shows_st,
    "vaplayer.ru":          resolve_vaplayer,
    "streamdata.vaplayer.ru": resolve_vaplayer,
    "medmedia03.wiki":      resolve_medmedia03,

    # ─── SuperFlix and ZXC resolvers ───────────────────────────────────────
    "superflixapi.buzz": resolve_superflixapi,
    "superflixapi.sbs": resolve_superflixapi,
    "zxcstream.xyz": resolve_zxcstream,
    "braflix.win": resolve_braflix,
    "vidcore.io": resolve_vidvm_family,
    # [SFlix] Server 2's static href is vidcore.NET, which the real
    # browser capture showed redirecting straight to vidcore.IO (same
    # app, domain rotation) -- same resolver, unconfirmed independently
    # for .net specifically but same family by evidence.
    "vidcore.net": resolve_vidvm_family,
    "api.wecollege.net": resolve_movy_api,
    "vidcloud.icu": resolve_vidcore,
    "embed.su": resolve_vidcore,
}

# Longest-key-first matching: substring dispatch must try the most
# specific key before a shorter one it contains ("down.vidtube.one"
# must reach the vidtube.one entry, not "vidtube"). All current
# overlapping keys map to the same resolver, so this only un-breaks
# dead entries — it changes nothing else.
_ORDERED_RESOLVER_KEYS = sorted(HOST_RESOLVERS.items(), key=lambda kv: -len(kv[0]))


# ─── Packer decoder with Unbaser (full base62/95 support) ────────────────────

class Unbaser(object):
    """
    Decodes P.A.C.K.E.R.'s symbol table indices back to natural numbers,
    for whatever radix the packer actually used.
    """
    ALPHABET = {
        62: "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        95: (
            " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
        ),
    }

    def __init__(self, base):
        self.base = base
        if 36 < base < 62 and base not in self.ALPHABET:
            self.ALPHABET[base] = self.ALPHABET[62][:base]
        if 2 <= base <= 36:
            self.unbase = lambda string: int(string, base)
        else:
            try:
                self.dictionary = dict(
                    (cipher, index) for index, cipher in enumerate(self.ALPHABET[base])
                )
            except KeyError:
                raise TypeError("Unsupported base encoding.")
            self.unbase = self._dictunbaser

    def __call__(self, string):
        return self.unbase(string)

    def _dictunbaser(self, string):
        ret = 0
        for index, cipher in enumerate(string[::-1]):
            ret += (self.base ** index) * self.dictionary[cipher]
        return ret


def _extract_packer_blocks(html):
    """
    Find every P.A.C.K.E.R. eval(function(p,a,c,k,e,d){...}(...)) call and return
    each one as decodable text. [PATCH 104] Previously this searched for a literal
    closing tail (".split('|')))") that assumed no fill-arguments between
    .split('|') and the closing parens. Real packer output commonly adds ",0,{}"
    (or other variants) there, so that exact string never matched and unpacking
    silently produced nothing -- confirmed on a real page where the marker was
    present but the tail search still found 0 blocks. decode_packer() parses
    p/a/c/k structurally and ignores everything after the k-string, so a generous
    fixed-size window from the marker onward is simpler and correct regardless of
    the tail's exact shape.
    """
    blocks = []
    marker = "eval(function(p,a,c,k,e,d){"
    WINDOW = 60000  # far more than any real packed JS body + token dictionary needs
    html = html or ""
    pos = 0
    while True:
        start = html.find(marker, pos)
        if start == -1:
            break
        blocks.append(html[start:start + WINDOW])
        pos = start + len(marker)
    return blocks


def decode_packer(packed):
    """
    Decode P.A.C.K.E.R. obfuscated JavaScript using the full Unbaser
    class that supports base62 and base95 (not just base36).
    """
    try:
        def read_js_string(text, start_idx):
            quote = text[start_idx]
            i = start_idx + 1
            out = []
            while i < len(text):
                ch = text[i]
                if ch == "\\" and i + 1 < len(text):
                    out.append(text[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    return "".join(out), i + 1
                out.append(ch)
                i += 1
            return "", -1

        start = packed.find("}(")
        if start == -1:
            return ""
        idx = start + 2
        while idx < len(packed) and packed[idx] in " \t\r\n":
            idx += 1
        if idx >= len(packed) or packed[idx] not in ("'", '"'):
            return ""

        p, idx = read_js_string(packed, idx)
        if idx == -1:
            return ""

        nums = re.match(r"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*", packed[idx:], re.S)
        if not nums:
            return ""
        a, c = nums.group(1), nums.group(2)
        idx += nums.end()
        if idx >= len(packed) or packed[idx] not in ("'", '"'):
            return ""

        k, idx = read_js_string(packed, idx)
        if idx == -1:
            return ""

        a, c = int(a), int(c)
        k = k.split("|")

        unbase = Unbaser(a)

        def lookup(match):
            word = match.group(0)
            try:
                idx = unbase(word)
            except (KeyError, ValueError):
                return word
            if 0 <= idx < len(k) and k[idx]:
                return k[idx]
            return word

        return re.sub(r'\b\w+\b', lookup, p)
    except Exception:
        return ""


def find_packed_links(html):
    for ev in _extract_packer_blocks(html):
        dec = decode_packer(ev)
        if dec:
            res = find_m3u8(dec) or find_mp4(dec)
            if res:
                return res
    for ev in re.findall(r"eval\(function\(p,a,c,k,e,d\).*?}\(.*?\)\)", html, re.S):
        dec = decode_packer(ev)
        if dec:
            res = find_m3u8(dec) or find_mp4(dec)
            if res:
                return res
    return None


def _unpack_all(html):
    texts = [html]
    for block in _extract_packer_blocks(html):
        dec = decode_packer(block)
        if dec:
            texts.append(dec)
    return texts


def resolve_generic_embed(url, referer=None):
    try:
        html, final = fetch(url, referer=referer or url)
        # [FIX] some hosts (confirmed directly: vipserver.liiivideo.com)
        # intermittently return a tiny, blocked/incomplete response (as
        # little as 38 bytes) instead of the real page. Confirmed on this
        # exact codebase: a fresh retry of the EXACT SAME URL moments
        # later returned the full, real content (3704 bytes), and
        # _unpack_all()/_best_media_url() both correctly extracted a real
        # stream URL from it once given that real content -- the
        # extraction logic itself was never the problem. A real embed
        # page is never this small; one retry costs little and has
        # already been proven, empirically, to succeed where the first
        # attempt didn't.
        if not html or len(html) < 200:
            log("resolve_generic_embed: suspiciously short response ({} bytes) for {}, retrying once".format(
                len(html) if html else 0, url[:80]))
            time.sleep(1)
            html, final = fetch(url, referer=referer or url)
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        js_redirect = _find_js_redirect(html)
        if js_redirect and js_redirect != url:
            log("resolve_generic_embed: following JS redirect to {}".format(js_redirect[:100]))
            h2, final2 = fetch(js_redirect, referer=referer or url)
            if h2:
                best = _best_media_url(h2)
                if best:
                    return best
                for txt in _unpack_all(h2):
                    best = _best_media_url(txt)
                    if best:
                        return best
                for iframe_url in extract_iframes(h2, final2 or js_redirect)[:3]:
                    h3, _ = fetch(iframe_url, referer=js_redirect, timeout=8, max_retries=0)
                    if h3:
                        best = _best_media_url(h3)
                        if best:
                            return best
        for iframe_url in extract_iframes(html, final or url)[:3]:
            h2, _ = fetch(iframe_url, referer=referer or url, timeout=8, max_retries=0)
            if h2:
                best = _best_media_url(h2)
                if best:
                    return best
    except Exception:
        pass
    return None


# ─── Main host dispatcher ─────────────────────────────────────────────────────

# JSON APIs with their own resolver: a page scrape of them finds nothing and
# only wastes time (Movy's dubbed servers hit the 90 s test limit that way).
_NO_GENERIC_FALLBACK = ("api.wecollege.net", "api.wing.st", "vidapi.xyz", "cdnm.ink")


def resolve_host(url, referer=None):
    domain = urlparse(url).netloc.lower()
    log("resolve_host: domain={} url={}".format(domain, url[:80]))
    for key, resolver in _ORDERED_RESOLVER_KEYS:
        if key in domain:
            log("Using resolver: {} (referer {})".format(key, "kept" if referer else "default"))
            # Referer-aware call first; fall back for 1-arg resolvers.
            try:
                result = resolver(url, referer=referer) if referer else resolver(url)
            except TypeError:
                result = resolver(url)
            if result:
                return result
            if key in _NO_GENERIC_FALLBACK:
                log("Resolver {} returned nothing (no generic fallback for this API)".format(key))
                return None
            log("Resolver {} returned nothing, trying generic".format(key))
            break
    log("Generic fallback for: {}{}".format(domain, "" if not referer else " (with caller referer)"))
    return resolve_generic_embed(url, referer=referer)


# ─── iframe chain resolver ────────────────────────────────────────────────────

def resolve_iframe_chain(url, referer=None, depth=0, max_depth=8):
    if depth > max_depth:
        return None, ""
    html, final_url = fetch(url, referer=referer, timeout=8, max_retries=0)
    if not html:
        return None, ""
    active_url = final_url or url
    domain = urlparse(active_url).netloc.lower()
    stream = find_m3u8(html) or find_mp4(html) or find_packed_links(html)
    if stream:
        return stream, domain
    m = re.search(
        r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\d+\s*;\s*url=([^"\']+)["\']',
        html, re.I
    )
    if m:
        new_url = m.group(1).strip()
        if new_url.startswith("//"):
            new_url = "https:" + new_url
        elif not new_url.startswith("http"):
            new_url = urljoin(active_url, new_url)
        if new_url != active_url:
            return resolve_iframe_chain(new_url, referer=active_url, depth=depth + 1, max_depth=max_depth)
    m = re.search(r'(?:window\.location(?:\.href)?\s*=|location\.replace\()\s*["\']([^"\']+)["\']', html, re.I)
    if m:
        new_url = m.group(1).strip()
        if new_url.startswith("//"):
            new_url = "https:" + new_url
        elif not new_url.startswith("http"):
            new_url = urljoin(active_url, new_url)
        if new_url != active_url and "://" in new_url:
            return resolve_iframe_chain(new_url, referer=active_url, depth=depth + 1, max_depth=max_depth)
    iframe_srcs = re.findall(
        r'<(?:iframe|embed|frame)[^>]+(?:src|data-src|data-url|data-lazy-src)=["\']([^"\']+)["\']',
        html, re.I
    )
    for src in iframe_srcs:
        if src.startswith("//"):
            src = "https:" + src
        elif not src.startswith("http"):
            p = urlparse(active_url)
            if src.startswith("/"):
                src = "{}://{}{}".format(p.scheme, p.netloc, src)
            else:
                continue
        if any(x in src.lower() for x in ("facebook.com", "twitter.com", "googletag", "doubleclick", "analytics")):
            continue
        src_domain = urlparse(src).netloc.lower()
        for key, resolver in _ORDERED_RESOLVER_KEYS:
            if key in src_domain:
                try:
                    result = resolver(src) if not referer else resolver(src, referer=referer)
                except TypeError:
                    result = resolver(src)
                if result:
                    return result, src_domain
                break
        res, h = resolve_iframe_chain(src, referer=active_url, depth=depth + 1, max_depth=max_depth)
        if res:
            return res, h
    return None, ""


# ─── extract_stream_all (multi-quality) ──────────────────────────────────────

def extract_stream_all(url):
    """
    Extract ALL quality variants from a server URL.
    Returns list of (stream_url, quality_label) tuples.
    """
    log("extract_stream_all: {}".format(url[:80]))

    result = extract_stream(url)

    variants = []
    if result and len(result) >= 4:
        main_url, quality, ref, extra_variants = result[0], result[1], result[2], result[3]
        if main_url:
            variants.append((main_url, quality or "HD"))
        for lbl, u in extra_variants:
            if u != main_url:
                variants.append((u, lbl))
    elif result and len(result) >= 3:
        main_url, quality, ref = result[0], result[1], result[2]
        if main_url:
            variants.append((main_url, quality or "HD"))
    elif result and len(result) >= 1:
        variants.append((result[0], "HD"))

    if not variants:
        for lbl, u in get_synthesized_variants(url):
            if u not in [v[0] for v in variants]:
                variants.append((u, lbl))

    if not variants:
        return []

    quality_order = {"Original": 0, "1080p": 1, "720p": 2, "480p": 3, "360p": 4, "240p": 5, "HD": 6}
    variants.sort(key=lambda v: quality_order.get(v[1], 99))

    return variants


# ─── extract_stream (main entry point) ──────────────────────────────────────

def _looks_like_direct_media(url):
    """[PATCH 93] Extension must be the PATH suffix (or .m3u8/.mp4/.mkv after
    the host) — NOT any substring of the whole URL: '.ts' matched hosts like
    x.tsvideo.com and turned embed pages into 'direct streams'."""
    try:
        p = urlparse((url or "").lower())
    except Exception:
        return False
    path = p.path or ""
    rest = path + (("?" + p.query) if p.query else "")
    if any(e in rest for e in (".m3u8", ".mp4", ".mkv")):
        return True
    return path.endswith((".mp3", ".ts", ".txt", ".woff2"))


def _quality_label_for(url):
    """[PATCH 93] Pinned-regex label first, then explicit hd1080/hd720 path
    markers. A bare 'hd' substring no longer means 720p."""
    lbl = _label_quality_variant(url)
    if lbl:
        return lbl
    low = _quality_probe_text(url)      # [PATCH 94] path only
    for marker, label in (("hd1080", "1080p"), ("hd720", "720p"),
                          ("index-f2", "720p"), ("index-f1", "480p")):
        if marker in low:
            return label
    return "HD"


def extract_stream(url):
    """
    Standard entry point used by all extractors.
    Returns (stream_url, quality_label, referer, variants).
    """
    log("--- extract_stream START: {} ---".format(url))
    raw_url = (url or "").strip()
    if not raw_url:
        return None, "", url, []

    piped_headers = {}
    main_url = raw_url
    if "|" in raw_url:
        main_url, raw_hdrs = raw_url.split("|", 1)
        for part in raw_hdrs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                piped_headers[k.strip()] = v.strip()

    # Correct disguised extensions
    main_url = _correct_stream_url(main_url)

    # [FIX] confirmed directly, live, on a real device: playmogo.com/d/<id>
    # (an info/"download" page shape) returns a completely empty response
    # (0 bytes) when fetched -- there is no page content at all for
    # resolve_iframe_chain() below to search, so nothing can recover it once
    # it reaches that fallback. playmogo.com/e/<id> -- the SAME id, same
    # host, already registered in HOST_RESOLVERS -- is confirmed working via
    # a real device log (a doodstream.com/e/<id> resolution for this exact
    # id redirected to, and succeeded through, playmogo.com/e/<id>). This is
    # a fact about playmogo.com itself, not any one site that happens to
    # link to it, so it belongs here rather than duplicated per-extractor.
    _pg_m = re.match(
        r'^(https?://)(playmogo' + chr(92) + r'.com)/d/([A-Za-z0-9]+)',
        main_url, re.I
    )
    if _pg_m:
        _rewritten = '{}{}/e/{}'.format(_pg_m.group(1), _pg_m.group(2), _pg_m.group(3))
        log('extract_stream: rewrote playmogo /d/ -> /e/: {} -> {}'.format(main_url, _rewritten))
        main_url = _rewritten

    # ─── DIRECT STREAM URL (m3u8/mp4/etc) ───────────────────────────────────
    if main_url.startswith("http") and _looks_like_direct_media(main_url):
        ref = piped_headers.get("Referer")
        if not ref:
            # was: ~20-branch elif chain — now the single referers.py table
            ref = get_referer(main_url)

        # Determine quality from URL  [PATCH 93]
        q = _quality_label_for(main_url)

        log("extract_stream DIRECT: {}".format(main_url))
        return main_url, q, ref, []

    # ─── RESOLVE VIA HOST RESOLVERS ────────────────────────────────────────
    # NEW: serialize the reset->resolve->readback sequence. _quality_tls
    # is one shared object (see htmlmedia.py) — without this lock, a
    # second extract_stream() on another thread (e.g. a stale _bgExtract
    # from a Detail screen the user already backed out of) can write
    # its own resolve's variants into _quality_tls between THIS thread's
    # resolve_host() and its get_last_quality_variants() readback below,
    # so this thread would silently return another title's qualities.
    with _quality_lock:
        _quality_tls.variants = []
        stream = resolve_host(main_url, referer=piped_headers.get("Referer"))
        if not stream:
            log("resolve_host failed, trying iframe chain")
            stream, resolved_domain = resolve_iframe_chain(main_url, referer=piped_headers.get("Referer"))
            if not stream:
                log("extract_stream FAILED for: {}".format(main_url))
                return None, "", main_url, []

        variants = get_last_quality_variants()

    stream = _correct_stream_url(stream)
    ref = piped_headers.get("Referer") or get_referer(stream)
    q = _quality_label_for(stream)

    others = [(lbl, u) for lbl, u in variants if u != stream]

    log("extract_stream SUCCESS: {} ({}), {} extra variant(s)".format(stream[:120], q, len(others)))
    return stream, q, ref, others
