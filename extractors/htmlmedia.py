# -*- coding: utf-8 -*-
"""HTML media-URL extraction: m3u8/mp4 finders, quality-variant
labeling/synthesis, placeholder blocklist, iframe extraction.

Depends only on net.log — deliberately does NOT import net.fetch
(keeps the Phase-2 graph acyclic: net never imports htmlmedia)."""

import re
from urllib.parse import urljoin, urlparse

from .net import log

# ─── Quality variant storage (v4.4 fix) ──────────────────────────────────
# Was threading.local() — but the flow is cross-thread: the background
# resolver thread SETS .variants (find_m3u8 / find_mp4 / _best_media_url
# and resolvers in hosts.py), while the MAIN thread READS it (detail's
# _onStreamFound + the player's quality switcher). threading.local
# attributes are per-thread by design, so the main thread always saw an
# empty list. A plain shared holder gives the intended "last writer
# wins" semantics; list reassignment is atomic in CPython, no lock
# needed.
class _SharedQualityStore(object):
    pass

_quality_tls = threading_local = _SharedQualityStore()

_QUALITY_SUFFIX_LABELS = {
    "_o": "Original", "_x": "Original", "_h": "720p", "_n": "480p", "_l": "360p",
    "-f3-": "1080p", "-f2-": "720p", "-f1-": "480p",
}

# ─── Placeholder / demo-video blocklist ─────────────────────────────────────
_PLACEHOLDER_MEDIA_DOMAINS = (
    "test-videos.co.uk", "sample-videos.com", "samplelib.com",
    "file-examples.com", "learningcontainer.com",
    "commondatastorage.googleapis.com", "download.blender.org",
    "media.w3.org", "html5demos.com", "bitdash-a.akamaihd.net",
    "bitmovin-a.akamaihd.net", "devstreaming-cdn.apple.com",
    "vjs.zencdn.net",
)
_PLACEHOLDER_MEDIA_MARKERS = (
    "bigbuckbunny", "big_buck_bunny", "sintel", "tears_of_steel",
    "elephantsdream", "elephants_dream",
)


def _is_placeholder_media_url(url):
    if not url:
        return False
    lower = url.lower()
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        host = ""
    if any(d in host for d in _PLACEHOLDER_MEDIA_DOMAINS):
        return True
    if any(m in lower for m in _PLACEHOLDER_MEDIA_MARKERS):
        return True
    return False


def _correct_stream_url(url):
    """Corrects disguised HLS URLs with .txt or .woff2 extensions."""
    if not url:
        return url
    lower_url = url.lower()
    # Correct playlist files (.txt to .m3u8)
    if lower_url.endswith('.txt') and ('master' in lower_url or 'index' in lower_url):
        return url[:-4] + '.m3u8'
    # Correct segment files (.woff2 to .ts)
    if lower_url.endswith('.woff2'):
        return url[:-6] + '.ts'
    return url


def _extract_quality_from_streamruby_url(url):
    """Legacy: extract quality label from streamruby.net URL patterns.
    Superseded by _label_quality_variant's pinned regexes, kept for
    import-compatibility with older resolver code."""
    if not url:
        return "HD"

    lower = url.lower()

    if '_o' in lower or '1080' in lower or 'fhd' in lower:
        return "1080p"
    elif '_h' in lower or '720' in lower or 'hd' in lower:
        return "720p"
    elif '_n' in lower or '480' in lower:
        return "480p"
    elif '_l' in lower or '360' in lower:
        return "360p"

    return "HD"


def find_m3u8_all(html):
    if not html:
        return []
    patterns = [
        r'["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'["\']([^"\']+\.txt[^"\']*)["\']',
        r'["\']([^"\']+\.woff2[^"\']*)["\']',
        r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'file\s*:\s*["\']([^"\']+\.txt[^"\']*)["\']',
        r'source\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'hls\.loadSource\(["\']([^"\']+)["\']',
        r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"',
        r'"url"\s*:\s*"([^"]+\.txt[^"]*)"',
        r'data-(?:url|src)=["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'data-(?:url|src)=["\']([^"\']+\.txt[^"\']*)["\']',
        r'hlsManifestUrl["\']?\s*:\s*["\']([^"\']+)["\']',
        r'(https?://[^\s"\'<>]+/pl/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/hls2/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/hls/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/e/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/playlist/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/api\?[^\s"\'<>]+&seg=[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.hakunaymatata\.com[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.polarcandy\.top[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.1x2\.space[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.scalablecontentengine\.site[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.panoplypalaver\.site[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.1shows\.app[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.netrocdn\.site[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.shows\.st[^\s"\'<>]+\.(?:m3u8|ts)[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.streamrk\.site[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
    ]
    seen = set()
    result = []
    for p in patterns:
        for m in re.finditer(p, html, re.I):
            url = m.group(1).replace("\\/", "/").replace("&amp;", "&").replace("\\u0026", "&").strip()
            if url.startswith("//"):
                url = "https:" + url
            if not (url.startswith("http") and (".m3u8" in url or ".txt" in url or ".woff2" in url or ".mp4" in url)):
                continue
            if _is_placeholder_media_url(url):
                log("find_m3u8_all: skipping known placeholder/demo URL: {}".format(url[:100]))
                continue
            # Correct disguised extensions
            url = _correct_stream_url(url)
            if url in seen:
                continue
            seen.add(url)
            result.append(url)
    return result


def _label_quality_variant(url):
    if not url:
        return None
    lower = _correct_stream_url(url).lower()
    # pinned urlset-style suffix: "..._h/segment.ts" — NOT a substring test
    m = re.search(r'[0-9a-z]_([lnhox])(?=/)', lower)
    if m:
        return {"o": "Original", "x": "Original", "h": "720p",
                "n": "480p", "l": "360p"}[m.group(1)]
    m = re.search(r'[-_]f([123])(?=[-/_.])', lower)
    if m:
        return {"3": "1080p", "2": "720p", "1": "480p"}.get(m.group(1))
    m = re.search(r'\b(2160|1080|720|480|360|240)\b', lower)
    if m:
        return m.group(1) + "p"
    if "fhd" in lower or "4k" in lower:
        return "2160p" if "4k" in lower else "1080p"
    return None


def _label_quality_variants(urls):
    _ORDER = {"Original": 0, "1080p": 1, "720p": 2, "480p": 3, "360p": 4, "240p": 5}
    labeled = []
    unlabeled = []
    for u in urls:
        lbl = _label_quality_variant(u)
        if lbl:
            labeled.append((lbl, u))
        else:
            unlabeled.append(u)
    labeled.sort(key=lambda pair: _ORDER.get(pair[0], 99))
    for i, u in enumerate(unlabeled, 1):
        labeled.append(("Quality {}".format(i), u))
    return labeled


def get_last_quality_variants():
    urls = getattr(_quality_tls, "variants", [])
    if len(urls) <= 1:
        return []
    return _label_quality_variants(urls)


def _synthesize_suffix_variants(url):
    if not url:
        return []
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        domain = ""
    _SAFE_SYNTH_DOMAINS = (
        "streamruby.net", "cdn-video.xyz", "uqload.is",
        "systemorchestration.space", "highqualityprints.shop",
        "maplecrestwellnessspace.space",
        "lakesideproductionstudio.cfd",
    )
    if not any(d in domain for d in _SAFE_SYNTH_DOMAINS):
        return []
    m = re.search(r'(_[lnhox])(?=/)', url)
    if m:
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            domain = ""
        if "streamruby" in domain or "tnmr" in url.lower():
            quad = ("_l", "_n", "_h", "_o")
        else:
            quad = ("_l", "_n", "_h", "_x")
        base_part = url[:m.start(1)]
        rest = url[m.end(1):]
        out, seen = [], set()
        for s in quad:
            u = base_part + s + rest
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out
    m2 = re.search(r'(-f)(\d)(-)', url)
    if m2:
        prefix = m2.group(1)
        before = url[:m2.start(1)]
        after = url[m2.end(3):]
        out, seen = [], set()
        for d in ("1", "2", "3"):
            u = before + prefix + d + "-" + after
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out
    return []


def get_synthesized_variants(url):
    urls = _synthesize_suffix_variants(url)
    if len(urls) <= 1:
        return []
    return _label_quality_variants(urls)


def find_m3u8(html):
    urls = find_m3u8_all(html)
    _quality_tls.variants = urls
    return urls[0] if urls else None


def find_mp4_all(html):
    if not html:
        return []
    patterns = [
        r'["\']([^"\']+\.mp4[^"\']*)["\']',
        r'file\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
        r'source\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
        r'data-(?:url|src)=["\']([^"\']+\.mp4[^"\']*)["\']',
        r'"url"\s*:\s*"([^"]+\.mp4[^"]*)"',
        r'(https?://[^\s"\'<>]+/convert-h264/[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/bt/[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.hakunaymatata\.com[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.polarcandy\.top[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.streamrk\.site[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.moviepire\.co[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
    ]
    seen = set()
    result = []
    for p in patterns:
        for m in re.finditer(p, html, re.I):
            url = m.group(1).replace("\\/", "/").replace("&amp;", "&").replace("\\u0026", "&").strip()
            if url.startswith("//"):
                url = "https:" + url
            if not (url.startswith("http") and ".mp4" in url):
                continue
            if _is_placeholder_media_url(url):
                log("find_mp4_all: skipping known placeholder/demo URL: {}".format(url[:100]))
                continue
            if url in seen:
                continue
            seen.add(url)
            result.append(url)
    return result


def find_mp4(html):
    urls = find_mp4_all(html)
    _quality_tls.variants = urls
    return urls[0] if urls else None


def _best_media_url(text):
    if not text:
        return None
    candidates = []
    seen = set()

    def score(url):
        lowered = url.lower()
        if "2160" in lowered or "4k" in lowered:   return 5000
        if "1080" in lowered or "fhd" in lowered:  return 4000
        if "720" in lowered  or "hd" in lowered:   return 3000
        if "480" in lowered:                        return 2000
        if "360" in lowered:                        return 1000
        if "240" in lowered or "sd" in lowered:     return 500
        if ".m3u8" in lowered:                      return 3500
        return 100

    patterns = [
        r'sources\s*:\s*\[{[^}]*file\s*:\s*["\']([^"\']+)["\']',
        r'"file"\s*:\s*"([^"]+(?:m3u8|mp4|txt)[^"]*)"',
        r"'file'\s*:\s*'([^']+(?:m3u8|mp4|txt)[^']*)'",
        r'"source"\s*:\s*"([^"]+(?:m3u8|mp4|txt)[^"]*)"',
        r"'source'\s*:\s*'([^']+(?:m3u8|mp4|txt)[^']*)'",
        r'"src"\s*:\s*"([^"]+(?:m3u8|mp4|txt)[^"]*)"',
        r'(https?://[^\s"\'<>]+\.(?:m3u8|mp4|txt|woff2)[^\s"\'<>]*)',
        r'hlsManifestUrl["\']?\s*:\s*["\']([^"\']+)["\']',
        r'"(?:playlist|stream|hls|hls2|master)"\s*:\s*"([^"]+)"',
        r"'(?:playlist|stream|hls|hls2|master)'\s*:\s*'([^']+)'",
    ]
    for pat in patterns:
        for match in re.findall(pat, text, re.I):
            url = match.replace("\\/", "/").replace("&amp;", "&").replace("\\u0026", "&").strip()
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith("http"):
                continue
            if url in seen:
                continue
            if _is_placeholder_media_url(url):
                log("_best_media_url: skipping known placeholder/demo URL: {}".format(url[:100]))
                continue
            seen.add(url)
            candidates.append((score(url), url))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    best_url = candidates[0][1]
    best_ext = ".m3u8" if ".m3u8" in best_url.lower() else (".mp4" if ".mp4" in best_url.lower() else None)
    _quality_tls.variants = [u for _, u in candidates if not best_ext or best_ext in u.lower()]
    return best_url


def extract_iframes(html, base_url=""):
    iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
    result = []
    for src in iframes:
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/") and base_url:
            p = urlparse(base_url)
            src = "{}://{}{}".format(p.scheme, p.netloc, src)
        if src.startswith("http"):
            result.append(src)
    return result