# -*- coding: utf-8 -*-
"""
EgyDead extractor — WordPress site

Canonical base: https://egydead.fyi/ (stable alias, redirects to the
currently active mirror's homepage, e.g. https://tv10.egydead.live/h3/).

Content pages on every mirror live at BARE paths (/category/...,
/serie/..., /series-category/..., /season/..., /episode/...).  The
rotating homepage prefix (h1, h2, h3, ...) applies ONLY to the homepage
and MUST be stripped from the base — applying it to content URLs causes
WordPress to fall back to the homepage for every category, making all
listings appear identical.

Fixes applied in this revision:
  * MAIN_URL is the stable alias https://egydead.fyi/.
  * DOMAINS: egydead.fyi first (auto-redirects to the active mirror),
    followed by the live tvN.egydead.live mirrors.
  * _site_root() returns the bare origin (scheme://netloc/) — it no
    longer keeps the rotating /hN/ prefix.
  * _base_serves_deep_content() rejects landing domains that pass the
    shallow check but return the same page for every deep URL.
  * _full_url() percent-encodes non-ASCII bytes for ABSOLUTE URLs too.
    EgyDead serves many posters with Arabic filenames
    (…/فيلم-Sinners-2025-مترجم-217x280.jpg); the HTTP client used by
    the plugin cannot put raw non-ASCII bytes in a request line.
    Skipping encoding for absolute URLs was what caused Arabic-named
    posters to fail in the grid while ASCII-named ones worked.
  * _parse_movie_items() extracts the release/quality label
    (CAM, HDTS, HD, WEB-DL, مدبلج, بالمصري, الاخيرة, …) into the item
    dict as "label".  The UI does not read it yet — it is available for
    a future badge widget.

Fixes carried over from previous revisions:
  CRITICAL: _detect_quality_variants() was outdented to MODULE level and
            never bound to the class — every call raised AttributeError,
            breaking all direct .m3u8/.txt resolution. Now a real method.
  HIGH:     streamruby urlset synthesis sliced at start(1) instead of
            end(1) and lost the video id — every variant 404'd.
            .m3u8 quality badge came from the LAST variant (loop had no
            break); now derived from the URL being returned.
            f1/f2/f3 synthesis produced "index-ff1-..." and a
            nonexistent "f4". Both fixed, best-first ordering.
  MEDIUM:   .txt-manifest branch parses real master playlists; final
            fallback normalizes base's 3-tuple into this extractor's
            4-tuple; guarded imports; Py2 shim removed.
  LOW:      quality markers tightened; episode/season numbering prefers
            explicit markers and skips years; streamruby variants carry
            the same Referer pipe; URL quoting fixed (path uses quote()
            and preserves existing %XX escapes); pagination regex
            accepts any attribute order / rel=next; episode <li> may
            carry attributes; base-domain probing is single-flight;
            "miixdrop" matches the MixDrop branch; _lnho synthesis is
            domain-gated.

NEW FIXES (2026-09-09):
  * Pagination now correctly handles ?page=N/ format (query parameter with trailing slash)
  * Next page detection prioritizes class="next page-numbers" over rel="next"
  * Category and search pagination properly handles query parameters
  * Homepage pagination (/h2/) now supported
  * Relative pagination links (?page=2/) properly resolved
  * Import system fixed using safe_import for all resolvers

NEW FIX (2026-09-10):
  * [PATCH 76] "send.cm" check no longer matches any URL containing "send".
  * [PATCH 76] vidhide/vidhidevip now routed to resolve_streamwish
    (matches hosts.py mapping) instead of resolve_vidguard.
"""

import re
import json
import time
import threading
import sys

from .base import (
    BaseExtractor, fetch, log,
    _correct_stream_url, _extract_quality_from_streamruby_url,
    extract_iframes,
)
from urllib.parse import urljoin, urlparse, quote_plus, quote
from html import unescape as html_unescape

# ─── Safe imports from base.py ──────────────────────────────────────────────
def _safe_import(name):
    """[PATCH 63/99] The dedicated resolvers live in hosts.py (and the quality helpers in htmlmedia.py);
    base.py never re-exported them, so looking only in base left every one of them None."""
    import importlib
    for mod in ('.hosts', '.htmlmedia', '.base'):
        try:
            found = getattr(importlib.import_module(mod, package=__package__), name, None)
        except Exception as e:
            log("EgyDead: cannot import {}: {}".format(mod, e))
            continue
        if found is not None:
            return found
    log("EgyDead: {} not found in hosts/htmlmedia/base".format(name))
    return None


# Import all needed resolvers using safe import
resolve_govid = _safe_import('resolve_govid')
resolve_streamruby = _safe_import('resolve_streamruby')
resolve_mixdrop = _safe_import('resolve_mixdrop')
resolve_doodstream = _safe_import('resolve_doodstream')
resolve_streamwish = _safe_import('resolve_streamwish')
resolve_voe = _safe_import('resolve_voe')
resolve_byselapuix = _safe_import('resolve_byselapuix')
resolve_vidguard = _safe_import('resolve_vidguard')
get_last_quality_variants = _safe_import('get_last_quality_variants')
get_synthesized_variants = _safe_import('get_synthesized_variants')
base_extract_stream = _safe_import('extract_stream')
_quality_bucket = _safe_import('_quality_bucket')      # [PATCH 99]

# ─── Process-wide base-domain cache ──────────────────────────────────────────
_BASE_CACHE_TTL   = 300   # 5 minutes
_PROBE_COOLDOWN   = 30    # 30 seconds
_base_cache = {"url": None, "resolved_at": 0, "probed_at": 0}
_base_cache_lock = threading.Lock()
# Single-flight probe — while one thread scans the DOMAINS list, other
# threads wait on this lock and reuse whatever it found.
_probe_lock = threading.Lock()

_PROBE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class EgyDeadExtractor(BaseExtractor):
    """Extractor for EgyDead."""

    # egydead.fyi is a stable alias that redirects to the current
    # active mirror's homepage (e.g. tv10.egydead.live/h3/).
    # Content pages on every mirror live at bare paths
    # (/category/..., /serie/...), NOT under the rotating prefix.
    MAIN_URL = "https://egydead.fyi/"

    DOMAINS = [
        "https://egydead.fyi/",           # stable alias, redirects to active mirror
        "https://tv10.egydead.live/",
        "https://tv9.egydead.live/",
        "https://tv8.egydead.live/",
        "https://tv7.egydead.live/",
        "https://tv6.egydead.live/",
        "https://tv5.egydead.live/",
        "https://tv4.egydead.live/",
        "https://tv3.egydead.live/",
        "https://tv2.egydead.live/",
        "https://tv1.egydead.live/",
        "https://tv.egydead.live/",
        "https://a46.egydead.live/",
        "https://www.egydead.live/",
        "https://egydead.live/",
        "https://egydead.com/",
        "https://egydead.video/",
        "https://egydead.watch/",
        "https://egydead.pics/",
        "https://egydead.org/",
        "https://x7k9f.sbs/",
    ]

    VALID_HOST_MARKERS = ("egydead", "x7k9f.sbs")
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)

    CLEAN_WORDS = [
        "مشاهدة فيلم", "مشاهدة", "فيلم", "مسلسل",
        "مترجمة اون لاين", "مترجم اون لاين",
        "مترجمة", "مترجم", "اون لاين", "أون لاين",
        "مدبلجة", "مدبلج", "كرتون", "انمي",
        "بالمصري", "سلسلة افلام", "عرض", "برنامج", "جميع مواسم",
    ]

    def __init__(self):
        super(EgyDeadExtractor, self).__init__()
        self.main_url = self.MAIN_URL
        self._resolved_base = None

    def _host(self, url):
        try:
            return (urlparse(url).netloc or "").lower()
        except Exception:
            return ""

    def _is_valid_site_url(self, url):
        host = self._host(url)
        if not host:
            return False
        if any(m in host for m in self.BLOCKED_HOST_MARKERS):
            return False
        return any(m in host for m in self.VALID_HOST_MARKERS)

    def _is_blocked_page(self, html, final_url=""):
        text = (html or "").lower()
        final = (final_url or "").lower()
        if not text:
            return True
        if "just a moment" in text and ("cf-chl" in text or "challenge" in text):
            return True
        if "enable javascript and cookies to continue" in text:
            return True
        if any(m in final for m in self.BLOCKED_HOST_MARKERS):
            return True
        return False

    def _looks_like_egydead_page(self, html):
        text = html or ""
        return (
            "movieItem" in text
            or "BottomTitle" in text
            or "egydead" in text.lower()
            or "serversList" in text
            or "EpsList" in text
            or "seasons-list" in text
        )

    def _site_root(self, url):
        """
        Return the bare origin (scheme://netloc/) from a URL.

        Content pages on EgyDead mirrors live at bare paths
        (/category/..., /serie/..., /season/...), NOT under the rotating
        homepage prefix (/h1/, /h2/, /h3/).  The prefix applies only to
        the homepage and MUST be stripped so category and search URLs
        resolve correctly.

        Applying the prefix to content URLs causes WordPress to fall
        back to the homepage for every category, making all listings
        appear identical.
        """
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        """
        Confirm the candidate base actually serves listing pages, not
        just a homepage/landing.

        Some domains respond successfully to `/` but return the same
        page for every deep URL — accepting them makes every category
        collapse to the same items.
        """
        if not base:
            return False

        test_url = urljoin(base, "serie/")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("EgyDead: deep-content probe failed for {}: {}".format(base, e))
            return False

        if not html:
            log("EgyDead: deep-content probe got empty body for {}".format(base))
            return False
        if self._is_blocked_page(html, final or ""):
            log("EgyDead: deep-content probe blocked for {}".format(base))
            return False

        # A real /serie/ listing contains many movieItem <li> entries.
        # A landing page does not.
        n_items = html.count("movieItem")
        if n_items < 10:
            log("EgyDead: {} responded but only {} movieItem markers on "
                "/serie/ — likely a landing, not a content mirror".format(
                    base, n_items))
            return False

        return True

    def _get_base(self):
        """Get the base URL with caching and single-flight probing."""
        if self._resolved_base:
            return self._resolved_base

        now = time.time()
        with _base_cache_lock:
            cached_url  = _base_cache["url"]
            resolved_at = _base_cache["resolved_at"]
            probed_at   = _base_cache["probed_at"]

        if cached_url and (now - resolved_at) < _BASE_CACHE_TTL:
            log("EgyDead: reusing cached base {} (resolved {}s ago)".format(cached_url, int(now - resolved_at)))
            self._resolved_base = cached_url
            self.main_url = cached_url
            return cached_url

        if cached_url and (now - probed_at) < _PROBE_COOLDOWN:
            log("EgyDead: skipping full re-probe (cooldown), reusing {} for now".format(cached_url))
            self._resolved_base = cached_url
            self.main_url = cached_url
            return cached_url

        with _probe_lock:
            # Double-check: another thread may have finished probing
            # while we were waiting for the lock.
            now = time.time()
            with _base_cache_lock:
                cached_url  = _base_cache["url"]
                resolved_at = _base_cache["resolved_at"]
                probed_at   = _base_cache["probed_at"]
            if cached_url and (now - resolved_at) < _BASE_CACHE_TTL:
                self._resolved_base = cached_url
                self.main_url = cached_url
                return cached_url
            if cached_url and (now - probed_at) < _PROBE_COOLDOWN:
                self._resolved_base = cached_url
                self.main_url = cached_url
                return cached_url

            for domain in self.DOMAINS:
                log("EgyDead: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain, extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain

                if not self._is_valid_site_url(final_url):
                    log("EgyDead: unexpected host after redirect {}".format(final_url))
                    continue

                if self._is_blocked_page(html, final_url):
                    log("EgyDead: blocked {}".format(final_url))
                    continue

                if html and self._looks_like_egydead_page(html):
                    # Strip any rotating homepage prefix (/h1/, /h2/,
                    # /h3/, ...) — content pages are served at bare
                    # paths on the same domain.
                    candidate = self._site_root(final_url)

                    # Deep-content probe: a landing domain passes the
                    # shallow check but returns the same page for every
                    # deep URL.  Reject it before locking it in.
                    if not self._base_serves_deep_content(candidate):
                        log("EgyDead: {} is not a content mirror — "
                            "trying next domain".format(candidate))
                        continue

                    self._resolved_base = candidate
                    self.main_url = candidate
                    with _base_cache_lock:
                        _base_cache["url"] = candidate
                        _base_cache["resolved_at"] = now
                        _base_cache["probed_at"] = now
                    log("EgyDead: selected base {}".format(candidate))
                    return candidate

            # Fallback: the stable alias.  All mirrors redirect through
            # it anyway, so its origin is the safest guess we have.
            self._resolved_base = self.MAIN_URL
            self.main_url = self._resolved_base
            with _base_cache_lock:
                _base_cache["url"] = self._resolved_base
                _base_cache["probed_at"] = now
            log("EgyDead: all probes failed, falling back to {}".format(self._resolved_base))
            return self._resolved_base

    def _clean_title(self, title):
        title = self._strip_tags(title)
        for word in self.CLEAN_WORDS:
            title = title.replace(word, "")
        title = re.sub(r"\s*\|\s*$", "", title)
        title = re.sub(r"\s*\-\s*$", "", title)
        return re.sub(r"\s+", " ", title).strip(" -|")

    def _full_url(self, path):
        """
        Resolve a relative or absolute URL against the base, then
        percent-encode any non-ASCII bytes in the result.

        The percent-encoding step MUST run for absolute URLs too:
        EgyDead serves many posters with Arabic filenames
        (…/فيلم-Sinners-2025-مترجم-217x280.jpg), and the HTTP client
        used by the plugin cannot put raw non-ASCII bytes in a request
        line.  Skipping encoding for absolute URLs was what caused
        Arabic-named posters to fail in the grid while ASCII-named
        ones worked.
        """
        if not path:
            return ""

        path = html_unescape(path.strip())

        # Protocol-relative → https
        if path.startswith("//"):
            path = "https:" + path

        if path.startswith("http"):
            full_url = path
        else:
            base = self._get_base()
            full_url = urljoin(base, path)

            # Collapse duplicate slashes in the path segment.
            parsed = urlparse(full_url)
            if "//" in parsed.path and parsed.path != "/":
                cleaned_path = re.sub(r"/+", "/", parsed.path)
                full_url = parsed._replace(path=cleaned_path).geturl()

        # Percent-encode raw non-ASCII / URL-unsafe characters.  `safe`
        # keeps the delimiters and any existing %XX escapes intact, so
        # calling this twice is a no-op.
        try:
            if any(ord(c) > 127 for c in full_url) or any(
                c in full_url for c in ' <>"\''
            ):
                full_url = quote(full_url, safe=":/?&=#+%")
        except Exception:
            pass

        return full_url

    def _pick_real_image(self, html_chunk):
        best = None
        for img_tag in re.findall(r'<img[^>]+>', html_chunk, re.I):
            tag_candidates = []
            for attr in ('data-src', 'data-lazy-src', 'data-original', 'data-lazy', 'src'):
                m = re.search(attr + r'=["\']([^"\']+)["\']', img_tag, re.I)
                if m:
                    tag_candidates.append(m.group(1))
            for c in tag_candidates:
                if '/wp-content/uploads/' in c:
                    return c
            if best is None and tag_candidates:
                best = tag_candidates[0]
        return best

    def _encode_arabic_url(self, url):
        try:
            parsed = urlparse(url)
            path_segments = []
            for segment in parsed.path.split('/'):
                if segment:
                    if any(ord(c) > 127 for c in segment):
                        path_segments.append(quote(segment, safe='%'))
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
                                query_parts.append(key + '=' + quote_plus(val.encode('utf-8'), safe='%'))
                            else:
                                query_parts.append(part)
                        else:
                            query_parts.append(part)
                    encoded_query = '&'.join(query_parts)
                except Exception:
                    encoded_query = parsed.query
            encoded_url = parsed._replace(path=encoded_path, query=encoded_query).geturl()
            return encoded_url
        except Exception:
            return url

    def _fetch(self, url, referer=None, post_data=None):
        extra = {}
        if post_data:
            extra["Content-Type"] = "application/x-www-form-urlencoded"
            extra["X-Requested-With"] = "XMLHttpRequest"
        extra["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        extra["Accept-Language"] = "ar-EG,ar;q=0.9,en;q=0.8"
        extra["Cache-Control"] = "no-cache"
        extra["Pragma"] = "no-cache"
        extra["Sec-Fetch-Dest"] = "document"
        extra["Sec-Fetch-Mode"] = "navigate"
        extra["Sec-Fetch-Site"] = "none"
        extra["Sec-Fetch-User"] = "?1"
        extra["Upgrade-Insecure-Requests"] = "1"
        encoded_url = self._encode_arabic_url(url)
        return fetch(
            encoded_url,
            referer=referer or self._get_base(),
            extra_headers=extra if extra else None,
            post_data=post_data,
        )

    def _quality_from_url(self, url, default="HD"):
        """Best-effort quality label for a stream URL."""
        if not url:
            return default
        low = _correct_stream_url(url).lower()

        # urlset-style quality suffix pinned to a token: ".../token_h/..."
        m = re.search(r'[0-9a-z]_([lnhox])(?=/)', low)
        if m:
            return {"o": "Original", "x": "Original",
                    "h": "720p", "n": "480p", "l": "360p"}[m.group(1)]

        for marker, label in (
            ("2160", "2160p"), ("4k", "2160p"),
            ("index-f3", "1080p"), ("-f3-", "1080p"),
            ("1080", "1080p"), ("fhd", "1080p"), ("hd1080", "1080p"),
            ("index-f2", "720p"), ("-f2-", "720p"),
            ("720", "720p"), ("hd720", "720p"),
            ("index-f1", "480p"), ("-f1-", "480p"), ("480", "480p"),
            ("360", "360p"),
            ("240", "240p"),
        ):
            if marker in low:
                return label

        if "master.m3u8" in low or "playlist" in low:
            return "HD"
        return default

    def _extract_quality_from_url(self, url):
        return self._quality_from_url(url, default="")

    def _parse_movie_items(self, html, current_url=None):
        items = []
        seen = set()

        for li in re.findall(r'<li[^>]*class=["\'][^"\']*(?:movieItem)[^"\']*["\'][^>]*>(.*?)</li>', html, re.S | re.I):
            url_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', li)
            if not url_match:
                continue
            url = self._full_url(url_match.group(1))
            if not url or url in seen:
                continue
            seen.add(url)

            if any(x in url for x in ("/page/", "page=")):
                continue

            title = ""
            title_match = (
                re.search(r'<h1[^>]*class=["\'][^"\']*BottomTitle[^"\']*["\'][^>]*>(.*?)</h1>', li, re.S | re.I) or
                re.search(r'<h[1-3][^>]*>(.*?)</h[1-3]>', li, re.S | re.I) or
                re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', li) or
                re.search(r'<a[^>]+title=["\']([^"\']+)["\']', li)
            )
            if title_match:
                title = self._clean_title(title_match.group(1))

            poster = self._pick_real_image(li)
            if poster:
                poster = self._full_url(poster)
                poster = re.sub(r'-\d+x\d+(?=\.\w+$)', '', poster)
            else:
                poster = ""

            cat_match = re.search(r'<span[^>]*class=["\'][^"\']*cat_name[^"\']*["\'][^>]*>(.*?)</span>', li, re.S | re.I)
            quality = self._strip_tags(cat_match.group(1)) if cat_match else ""

            # Release/quality label (CAM, HDTS, HD, WEB-DL, مدبلج, بالمصري,
            # الاخيرة, …).  Sits in a <span class="label"> next to cat_name.
            # Not the same as `quality` (which is the site's category —
            # افلام اجنبي etc.); this one tells the viewer what copy they
            # are about to get.
            label = ""
            label_match = re.search(
                r'<span[^>]*class=["\'][^"\']*\blabel\b[^"\']*["\'][^>]*>(.*?)</span>',
                li, re.S | re.I
            )
            if label_match:
                label = self._strip_tags(label_match.group(1)).strip()

            ep_num = ""
            ep_match = re.search(r'<span[^>]*class=["\'][^"\']*number_episode[^"\']*["\'][^>]*>.*?<em>(\d+)</em>', li, re.S | re.I)
            if ep_match:
                ep_num = ep_match.group(1)

            url_low = url.lower()
            raw_title_text = title_match.group(1) if title_match else ""

            if "/episode/" in url_low or "حلقه" in raw_title_text or ep_num:
                item_type = "episode"
            elif "/season/" in url_low or "موسم" in raw_title_text:
                item_type = "season"
            elif "/serie/" in url_low or "/series/" in url_low or "مسلسل" in raw_title_text:
                item_type = "series"
            else:
                item_type = "movie"

            # year: extract from the title into its own field (feeds the grid's red
            # badge, same as topcinema) and strip it from the caption text -- EgyDead
            # titles arrive as "Movie Name 2026" the same way topcinema's do. [PATCH 107]
            year = ""
            ym = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            if ym:
                year = ym.group(1)
                title = re.sub(r'\s*\b' + year + r'\b\s*', ' ', title)
                title = re.sub(r'\s{2,}', ' ', title).strip(' -|')

            display_title = title
            if ep_num and item_type == "episode":
                display_title = "{} - حلقة {}".format(title, ep_num)

            if display_title:
                items.append({
                    "title": display_title,
                    "url": url,
                    "poster": poster,
                    "plot": quality,
                    "label": label,
                    "year": year,
                    "type": item_type,
                    "_action": "details",
                })

        return items

    def _episode_number(self, item):
        url = item.get("url", "") or ""
        m = re.search(r'-e(\d{1,4})(?:[-/]|$)', url, re.I)
        if m:
            return int(m.group(1))
        title = item.get("title", "") or ""
        m = re.search(r'(?:حلقة|حلقه|episode|\bep\.?)\s*(\d{1,4})', title, re.I)
        if m:
            return int(m.group(1))
        nums = re.findall(r'\d+', title)
        if nums:
            candidates = [n for n in nums if not (len(n) == 4 and 1900 <= int(n) <= 2099)]
            return int(candidates[-1] if candidates else nums[-1])
        return 999999

    def _parse_episode_list(self, html):
        items = []
        seen = set()

        eps_match = re.search(r'<div[^>]*class=["\'][^"\']*EpsList[^"\']*["\'][^>]*>(.*?)</div>', html, re.S | re.I)
        if not eps_match:
            return items

        eps_html = eps_match.group(1)
        # Allow attributes on <li> — the old pattern matched only bare
        # "<li>" tags and silently dropped styled ones.
        for ep in re.finditer(r'<li\b[^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>\s*</li>', eps_html, re.S | re.I):
            url = self._full_url(ep.group(1))
            if url in seen or not url:
                continue
            seen.add(url)
            title = self._strip_tags(ep.group(2)).strip()
            items.append({
                "title": "{}".format(title),
                "url": url,
                "type": "episode",
                "_action": "details",
            })

        items.sort(key=self._episode_number)
        return items

    def _arabic_season_ordinals(self):
        return {
            "الاول": 1, "الأول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4,
            "الخامس": 5, "السادس": 6, "السابع": 7, "الثامن": 8, "التاسع": 9,
            "العاشر": 10,
        }

    def _season_number(self, item):
        url = (item.get("url", "") or "").lower()
        m = re.search(r'[-_]s(\d{1,3})(?:[-/]|$)', url)
        if m:
            return int(m.group(1))
        m2 = re.search(r'season[-_](\d{1,3})', url)
        if m2:
            return int(m2.group(1))
        title = item.get("title", "") or ""
        m3 = re.search(r'(?:موسم|season|\bs\.?)\s*(\d{1,3})', title, re.I)
        if m3:
            return int(m3.group(1))
        ordinals = self._arabic_season_ordinals()
        for word, num in ordinals.items():
            if word in title:
                return num
        nums = re.findall(r'\d+', title)
        if nums:
            candidates = [n for n in nums if not (len(n) == 4 and 1900 <= int(n) <= 2099)]
            return int(candidates[-1] if candidates else nums[-1])
        return 999999

    def _parse_season_list(self, html):
        items = []
        seen = set()

        season_match = re.search(r'<div[^>]*class=["\'][^"\']*seasons-list[^"\']*["\'][^>]*>(.*?)</div>', html, re.S | re.I)
        if not season_match:
            return items

        season_html = season_match.group(1)
        for item in self._parse_movie_items(season_html):
            if item.get("url") and item.get("url") not in seen:
                seen.add(item.get("url"))
                item["type"] = "season"
                items.append(item)

        numbers = [self._season_number(it) for it in items]
        if items and all(n == 999999 for n in numbers):
            items.reverse()
        else:
            items.sort(key=self._season_number)

        return items

    def _parse_pagination(self, html, current_url):
        """
        Parse pagination links from HTML.
        FIXED: Properly handles ?page=N/ format with trailing slash.
        """
        next_href = ""

        # Method 1: Look for "next" class (most common on EgyDead)
        next_match = re.search(r'<a[^>]*class="[^"]*next[^"]*"[^>]*href="([^"]+)"', html, re.I)
        if next_match:
            next_href = next_match.group(1)
        else:
            # Method 2: Look for rel="next" attribute
            for a_match in re.finditer(r'<a\b[^>]*>', html, re.I):
                tag = a_match.group(0)
                if 'rel="next"' in tag.lower() or "rel='next'" in tag.lower():
                    href_m = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
                    if href_m:
                        next_href = href_m.group(1)
                        break

        if not next_href:
            return None

        raw_href = html_unescape(next_href.strip())

        # Handle different URL formats
        if raw_href.startswith("http"):
            next_url = raw_href
        elif raw_href.startswith("//"):
            next_url = "https:" + raw_href
        else:
            # Handle relative URLs like "?page=2/"
            next_url = urljoin(current_url, raw_href)

        if next_url and next_url != current_url:
            return {
                "title": "➡️ Next Page",
                "url": next_url,
                "type": "category",
                "_action": "category",
            }
        return None

    def _extract_detail_meta(self, html):
        title = ""
        title_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if title_match:
            title = self._clean_title(title_match.group(1))

        if not title:
            title_match = re.search(r'<title>(.*?)</title>', html, re.I)
            if title_match:
                title = self._clean_title(title_match.group(1).split('|')[0])

        poster = ""
        poster_match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if poster_match and '/wp-content/uploads/' in poster_match.group(1):
            poster = self._full_url(poster_match.group(1))
            poster = re.sub(r'-\d+x\d+(?=\.\w+$)', '', poster)

        if not poster:
            poster_area_match = re.search(r'<div[^>]+class=["\'][^"\']*[Pp]oster[^"\']*["\'][^>]*>(.*?)</div>', html, re.S | re.I)
            found = self._pick_real_image(poster_area_match.group(1)) if poster_area_match else None
            if not found:
                found = self._pick_real_image(html)
            if found:
                poster = self._full_url(found)
                poster = re.sub(r'-\d+x\d+(?=\.\w+$)', '', poster)

        plot = ""
        desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if desc_match:
            plot = self._strip_tags(desc_match.group(1))

        if not plot:
            story_match = re.search(r'<div[^>]*class=["\'][^"\']*singleStory[^"\']*["\'][^>]*>(.*?)</div>', html, re.S | re.I)
            if story_match:
                plot = self._strip_tags(story_match.group(1))

        year = ""
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', title + " " + plot)
        if year_match:
            year = year_match.group(1)

        return title, poster, plot, year

    def _parse_info_box(self, html):
        """
        [PATCH 110] The page's info box ("القسم/النوع/الجوده/اللغه/البلد/السنه/القناة/مده العرض"),
        confirmed against a real capture: <div class="LeftBox"><ul><li><span>LABEL : </span>
        <a>VALUE</a>...</li>...</ul></div>. Returns a dict with whichever of these keys the
        page actually has: category, genres (comma-joined string), quality, language, country,
        year, channel, runtime.
        """
        info = {}
        if not html:
            return info
        box_m = re.search(r'<div[^>]*class=["\'][^"\']*LeftBox[^"\']*["\'][^>]*>(.*?)</ul>', html, re.S | re.I)
        if not box_m:
            return info
        box_html = box_m.group(1)

        label_map = {
            "القسم": "category",
            "النوع": "genres",
            "الجوده": "quality",
            "اللغه": "language",
            "البلد": "country",
            "السنه": "year",
            "القناه": "channel",
            "مده العرض": "runtime",
        }

        for li in re.finditer(r'<li\b[^>]*>(.*?)</li>', box_html, re.S | re.I):
            li_html = li.group(1)
            span_m = re.search(r'<span[^>]*>(.*?)</span>', li_html, re.S | re.I)
            if not span_m:
                continue
            label = self._strip_tags(span_m.group(1)).strip().rstrip(":：").strip()
            key = label_map.get(label)
            if not key:
                continue
            values = [self._strip_tags(v).strip() for v in re.findall(r'<a[^>]*>(.*?)</a>', li_html, re.S | re.I)]
            values = [v for v in values if v]
            if not values:
                continue
            info[key] = ", ".join(values) if key == "genres" else values[0]

        return info

    def _extract_watch_servers(self, html, page_url):
        servers = []
        seen = set()

        servers_html = self._find_servers_html(html)

        if servers_html:
            for li_match in re.finditer(r'<li[^>]*data-link=["\']([^"\']+)["\'][^>]*>(.*?)</li>', servers_html, re.S | re.I):
                video_url = html_unescape(li_match.group(1).strip())
                li_content = li_match.group(2)

                if not video_url:
                    continue

                if video_url.startswith("//"):
                    video_url = "https:" + video_url

                # De-dup check AFTER the "//" -> "https:" normalization.
                if video_url in seen:
                    continue
                seen.add(video_url)

                name_match = re.search(r'<span[^>]*><p[^>]*>(.*?)</p></span>', li_content, re.I) or \
                            re.search(r'<p[^>]*>(.*?)</p>', li_content, re.I) or \
                            re.search(r'<span[^>]*>(.*?)</span>', li_content, re.I)

                name = self._strip_tags(name_match.group(1)) if name_match else "Watch Server {}".format(len(servers) + 1)

                if self._is_megamax_iframe(video_url):            # [PATCH 99]
                    try:
                        mm_servers = self._megamax_servers(video_url)
                    except Exception as e:
                        log("EgyDead: MegaMax expansion failed: {}".format(e))
                        mm_servers = []
                    if mm_servers:
                        for s in mm_servers:
                            if s["url"] not in seen:
                                seen.add(s["url"])
                                servers.append(s)
                        continue

                quality = self._extract_quality_from_url(video_url)
                servers.append({
                    "name": name.strip(),
                    "url": video_url,
                    "type": "embed",
                    "quality": quality
                })

        if not servers:
            iframe_match = re.search(r'<iframe[^>]+id=["\']videoIframe["\'][^>]+src=["\']([^"\']+)["\']', html, re.I)
            if iframe_match:
                video_url = iframe_match.group(1)
                if video_url and video_url not in seen:
                    seen.add(video_url)
                    quality = self._extract_quality_from_url(video_url)
                    servers.append({
                        "name": "Video Player",
                        "url": video_url,
                        "type": "embed",
                        "quality": quality
                    })

        log("EgyDead: Found {} watch servers for {}".format(len(servers), page_url))
        return servers

    # ---- MegaMax (Inertia.js) mirror list [PATCH 99] ----------------------------------------
    _MEGAMAX_DRIVERS = {
        "streamruby": "StreamRuby", "streamhg": "StreamWish", "uqload": "Uqload", "mixdrop": "MixDrop",
        "byse": "Byse", "doodstream": "DoodStream", "vidara": "Vidara", "earnvids": "VidHide", "voe": "Voe",
    }
    _MEGAMAX_ORDER = ("streamruby", "mixdrop", "voe", "streamhg", "earnvids", "doodstream", "byse", "vidara", "uqload")

    def _is_megamax_iframe(self, url):
        low = (url or "").lower()
        return "megamax." in low and "/iframe/" in low

    @staticmethod
    def _parse_inertia_page(text):
        """The page dict from an Inertia response: JSON body, data-page attribute, or embedded JSON."""
        text = (text or "").strip()
        if not text:
            return None
        if text[0] == "{":
            try:
                return json.loads(text)
            except ValueError:
                pass
        for pat in (r'data-page="([^"]*)"', r"data-page='([^']*)'"):
            m = re.search(pat, text)
            if m:
                try:
                    return json.loads(html_unescape(m.group(1)))
                except ValueError:
                    pass
        for src in (text, html_unescape(text)):
            i = src.find('{"component"')
            if i >= 0:
                try:
                    return json.JSONDecoder().raw_decode(src[i:])[0]
                except ValueError:
                    pass
        return None

    @staticmethod
    def _streams_data(page):
        """props.streams.data (a list) from an Inertia page dict, or None when it is not there."""
        props = page.get("props") if isinstance(page, dict) else None
        streams = props.get("streams") if isinstance(props, dict) else None
        data = streams.get("data") if isinstance(streams, dict) else None
        return data if isinstance(data, list) else None

    def _inertia_partial(self, url, page, prop):
        """[PATCH 100] Deferred prop: repeat the browser's partial-reload GET (headers copied from a real
        capture) and return the parsed page. Needs the page's own `component` and `version`."""
        hd = {
            "X-Inertia": "true",
            "X-Inertia-Version": str(page.get("version") or ""),
            "X-Inertia-Partial-Data": prop,
            "X-Inertia-Partial-Component": str(page.get("component") or ""),
            "X-Inertia-Except-Once-Props": "video,config,ads",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, application/xhtml+xml",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        body, _final = fetch(url, referer=url, extra_headers=hd)
        return self._parse_inertia_page(body)

    def _megamax_groups(self, iframe_url):
        """[{"label","resolution","size","mirrors":[(driver, link), ...]}, ...] from the iframe page."""
        cands = [iframe_url]
        m = re.search(r"/iframe/[A-Za-z0-9_-]+", iframe_url)
        if m and "eg.megamax.cam" not in iframe_url:
            cands.append("https://eg.megamax.cam" + m.group(0))
        for cand in cands:
            try:
                html, final = self._fetch(cand, referer=self._get_base())
            except Exception as e:
                log("EgyDead: MegaMax fetch failed {}: {}".format(cand, e))
                continue
            page = self._parse_inertia_page(html)
            data = self._streams_data(page)
            if data is None and isinstance(page, dict) and page.get("component"):
                try:
                    data = self._streams_data(self._inertia_partial(final or cand, page, "streams"))
                    log("EgyDead: MegaMax deferred streams via partial reload: {}".format(
                        "ok" if data is not None else "not returned"))
                except Exception as e:
                    log("EgyDead: MegaMax partial reload failed: {}".format(e))
            if data is None:
                continue
            groups = []
            for g in data:
                if not isinstance(g, dict):
                    continue
                mirrors = []
                for mm in (g.get("mirrors") or []):
                    link = str((mm or {}).get("link") or "").strip()
                    if link.startswith("//"):
                        link = "https:" + link
                    if link.startswith("http"):
                        mirrors.append((str((mm or {}).get("driver") or "").lower(), link))
                try:
                    size = int(g.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                if mirrors:
                    groups.append({"label": str(g.get("label") or ""), "resolution": str(g.get("resolution") or ""),
                                   "size": size, "mirrors": mirrors})
            if groups:
                log("EgyDead: MegaMax {} -> {} quality group(s), {} mirror(s)".format(
                    cand, len(groups), sum(len(g["mirrors"]) for g in groups)))
                return groups
        return []

    @staticmethod
    def _fmt_size(n):
        try:
            n = float(n)
        except (TypeError, ValueError):
            return ""
        if n >= 1024.0 ** 3:
            return "{:.2f} GB".format(n / 1024.0 ** 3)
        if n >= 1024.0 ** 2:
            return "{:.1f} MB".format(n / 1024.0 ** 2)
        return ""

    def _group_quality(self, g):
        m = re.match(r"(\d+)\s*x\s*(\d+)", g.get("resolution") or "")
        if m and _quality_bucket:
            return _quality_bucket(int(m.group(1)), int(m.group(2)))
        mm = re.search(r"(\d{3,4})p", g.get("label") or "")
        return (mm.group(1) + "p") if mm else "HD"

    def _megamax_servers(self, iframe_url):
        """Server entries (quality x mirror). Each carries a private "_dl" download entry."""
        order = self._MEGAMAX_ORDER
        servers = []
        for g in self._megamax_groups(iframe_url):
            q, size_txt = self._group_quality(g), self._fmt_size(g["size"])
            info = ", ".join([x for x in (q, size_txt) if x])
            for driver, link in sorted(g["mirrors"], key=lambda it: order.index(it[0]) if it[0] in order else len(order)):
                dname = self._MEGAMAX_DRIVERS.get(driver, driver.title() or "Server")
                servers.append({"name": "{} ({})".format(dname, info), "url": link, "type": "embed", "quality": q,
                                "_dl": {"resolution": q, "size": size_txt, "quality": dname, "url": link}})
        return servers

    def _parse_download_servers(self, html):
        """
        [PATCH 109] The site's own download-servers list (a "downloadMaster" block),
        completely separate from the watch-server list. EgyDead's own CSS has a typo in
        this class name: "donwload-servers-list", confirmed against a real page capture --
        matched here exactly as the site actually writes it, not the "correct" spelling.
        """
        downloads = []
        if not html:
            return downloads
        block_m = re.search(
            r'<ul[^>]*class=["\'][^"\']*donwload-servers-list[^"\']*["\'][^>]*>(.*?)</ul>',
            html, re.S | re.I)
        if not block_m:
            return downloads

        for li in re.finditer(r'<li\b[^>]*>(.*?)</li>', block_m.group(1), re.S | re.I):
            li_html = li.group(1)

            link_m = re.search(
                r'class=["\'][^"\']*ser-link[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
                li_html, re.I)
            if not link_m:
                continue
            url = html_unescape(link_m.group(1).strip())
            if url.startswith("//"):
                url = "https:" + url
            elif not url.startswith("http"):
                url = self._full_url(url)

            name_m = re.search(
                r'class=["\'][^"\']*ser-name[^"\']*["\'][^>]*>(.*?)</span>',
                li_html, re.S | re.I)
            name = self._strip_tags(name_m.group(1)).strip() if name_m else "Server"

            quality_m = re.search(
                r'class=["\'][^"\']*server-info[^"\']*["\'][^>]*>.*?<em>(.*?)</em>',
                li_html, re.S | re.I)
            quality = self._strip_tags(quality_m.group(1)).strip() if quality_m else ""

            downloads.append({
                "resolution": quality,
                "size": "",
                "quality": name,
                "url": url,
            })
        return downloads

    def _pull_downloads(self, servers):
        out = []
        for s in servers or []:
            dl = s.pop("_dl", None)
            if dl:
                out.append(dl)
        return out

    def _find_servers_html(self, html):
        m = re.search(
            r'<ul[^>]+class=["\'][^"\']*serversList[^"\']*["\'][^>]*>(.*?)</ul>',
            html, re.S | re.I
        )
        return m.group(1) if m else ""

    # ── Public API ───────────────────────────────────────────────────────────

    def get_categories(self, mtype="movie"):
        """Get categories for the site. FIXED: Proper URL construction."""
        self._get_base()  # warm/resolves the base-domain cache

        # FIX: Use _full_url() which properly handles the base origin.
        # Content pages live at bare paths on the mirror.
        if mtype == "movie":
            return [
                {"title": "🎬 English Movies",        "url": self._full_url("category/english-movies/"),      "type": "category", "_action": "category"},
                {"title": "🇪🇬 Arabic Movies",          "url": self._full_url("category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%b9%d8%b1%d8%a8%d9%8a/"),       "type": "category", "_action": "category"},
                {"title": "🌏 Asian Movies",           "url": self._full_url("category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a%d8%a9/"),     "type": "category", "_action": "category"},
                {"title": "🇹🇷 Turkish Movies",         "url": self._full_url("category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%aa%d8%b1%d9%83%d9%8a%d8%a9/"),      "type": "category", "_action": "category"},
                {"title": "🇮🇳 Indian Movies",          "url": self._full_url("category/hindi-movies/"),     "type": "category", "_action": "category"},
                {"title": "🎭 Cartoon Movies",         "url": self._full_url("category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d9%83%d8%b1%d8%aa%d9%88%d9%86/"),      "type": "category", "_action": "category"},
                {"title": "🎌 Anime Movies",           "url": self._full_url("category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d9%86%d9%85%d9%8a/"),       "type": "category", "_action": "category"},
                {"title": "📽️ Documentary Movies",    "url": self._full_url("category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d9%88%d8%ab%d8%a7%d8%a6%d9%82%d9%8a%d8%a9/"),    "type": "category", "_action": "category"},
                {"title": "🎬 All Movies",             "url": self._full_url("category/movies/"),             "type": "category", "_action": "category"},
            ]

        return [
            {"title": "📺 Complete Series",      "url": self._full_url("serie/"),              "type": "category", "_action": "category"},
            {"title": "📺 Complete Seasons",     "url": self._full_url("season/"),             "type": "category", "_action": "category"},
            {"title": "📺 Episodes",             "url": self._full_url("episode/"),            "type": "category", "_action": "category"},
            {"title": "📺 English Series",        "url": self._full_url("series-category/english-series/"),    "type": "category", "_action": "category"},
            {"title": "🇪🇬 Arabic Series",         "url": self._full_url("series-category/arabic-series/"),     "type": "category", "_action": "category"},
            {"title": "🇹🇷 Turkish Series",       "url": self._full_url("series-category/turkish-series/"),    "type": "category", "_action": "category"},
            {"title": "🌏 Asian Series",          "url": self._full_url("series-category/asian-series/"),      "type": "category", "_action": "category"},
            {"title": "🎌 Anime Series",          "url": self._full_url("series-category/anime-series/"),      "type": "category", "_action": "category"},
            {"title": "🎠 Cartoon Series",        "url": self._full_url("series-category/cartoon-series/"),    "type": "category", "_action": "category"},
            {"title": "🇮🇳 Indian Series",         "url": self._full_url("series-category/indian-series/"),     "type": "category", "_action": "category"},
            {"title": "📽️ Documentary Series",    "url": self._full_url("series-category/documentary-series/"), "type": "category", "_action": "category"},
            {"title": "📡 TV Shows",              "url": self._full_url("series-category/tv-shows/"),          "type": "category", "_action": "category"},
        ]

    def get_category_items(self, url, page=None):
        """
        Get items from a category page.
        FIXED: Properly handles ?page=N/ format with trailing slash.
        """
        fetch_url = url

        if page and page > 1:
            parsed = urlparse(fetch_url)
            query = parsed.query

            # Check if page parameter already exists
            if 'page=' in query:
                # Replace existing page parameter
                query_parts = []
                for part in query.split('&'):
                    if part.startswith('page='):
                        query_parts.append(f'page={page}/')
                    else:
                        query_parts.append(part)
                new_query = '&'.join(query_parts)
                fetch_url = parsed._replace(query=new_query).geturl()
            else:
                # Add page parameter with trailing slash
                if query:
                    fetch_url = fetch_url + f'&page={page}/'
                else:
                    fetch_url = fetch_url + f'?page={page}/'

        log("EgyDead: Fetching category page: {}".format(fetch_url))
        html, final_url = self._fetch(fetch_url)

        if not html:
            log("EgyDead: get_category_items failed: {}".format(fetch_url))
            return []

        items = self._parse_movie_items(html, final_url or fetch_url)

        # Only add pagination for page 1 or when no page specified
        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        log("EgyDead: category {} page {} → {} items".format(url, page or 1, len(items)))
        return items

    def search(self, query, page=1):
        """
        Search for content.
        FIXED: Proper pagination support with ?page=N/ format.
        """
        base = self._get_base().rstrip("/")
        search_url = f"{base}/?s={quote_plus(query)}"

        if page > 1:
            search_url += f"&page={page}/"

        log("EgyDead: Searching: {}".format(search_url))
        html, final_url = self._fetch(search_url)

        if not html:
            log("EgyDead: search failed for '{}'".format(query))
            return []

        items = self._parse_movie_items(html, final_url or search_url)

        if page == 1:
            nxt = self._parse_pagination(html, search_url)
            if nxt:
                items.append(nxt)

        log("EgyDead: search '{}' → {} items".format(query, len(items)))
        return items

    def get_page(self, url, m_type=None):
        html, final_url = self._fetch(url)
        result = {
            "url": url,
            "title": "",
            "poster": "",
            "plot": "",
            "year": "",
            "rating": "",
            "servers": [],
            "items": [],
            "type": m_type or "movie",
        }

        if not html:
            log("EgyDead: get_page failed: {}".format(url))
            return result

        # NOTE: "/episode/..." detection works on the bare path.
        url_low = url.lower()

        if "/episode/" in url_low:
            log("EgyDead: parsing episode page")
            title, poster, plot, year = self._extract_detail_meta(html)
            result["title"] = title
            result["poster"] = poster
            result["plot"] = plot
            result["year"] = year
            info = self._parse_info_box(html)         # [PATCH 110]
            if info.get("year"):
                result["year"] = info["year"]
            for _k in ("genres", "country", "quality", "language", "channel", "runtime", "category"):
                if info.get(_k):
                    result[_k] = info[_k]
            result["type"] = "episode"

            servers = self._extract_watch_servers(html, final_url or url)
            if not servers:
                log("EgyDead: no servers on initial load, retrying with View=1 POST")
                post_html, post_final_url = self._fetch(url, post_data={"View": "1"})
                if post_html:
                    servers = self._extract_watch_servers(post_html, post_final_url or url)

            result["servers"] = servers
            # [PATCH 109] the site's own download list takes priority; fall back to the
            # MegaMax-mirror reuse (PATCH 99) only when this page has no download list.
            real_downloads = self._parse_download_servers(html)
            result["downloads"] = real_downloads if real_downloads else self._pull_downloads(servers)
            log("EgyDead: episode {} → {} servers".format(title, len(servers)))
            return result

        if "/season/" in url_low:
            log("EgyDead: parsing season page")
            title, poster, plot, year = self._extract_detail_meta(html)
            result["title"] = title
            result["poster"] = poster
            result["plot"] = plot
            result["year"] = year
            info = self._parse_info_box(html)         # [PATCH 110]
            if info.get("year"):
                result["year"] = info["year"]
            for _k in ("genres", "country", "quality", "language", "channel", "runtime", "category"):
                if info.get(_k):
                    result[_k] = info[_k]
            result["type"] = "season"

            episodes = self._parse_episode_list(html)

            # [PATCH 108] sibling seasons of the SAME series -- a season page carries the
            # exact same "seasons-list" block a series page does; reuse the already-proven
            # parser instead of only reading it on series pages.
            siblings = []
            try:
                for s in self._parse_season_list(html):
                    if s.get("url") and s.get("url") != url:
                        s = dict(s)
                        s["title"] = "🎬 " + (s.get("title") or "")
                        siblings.append(s)
            except Exception as e:
                log("EgyDead: sibling-season parse failed: {}".format(e))

            result["items"] = episodes + siblings
            log("EgyDead: season {} → {} episodes, {} other season(s)".format(
                title, len(episodes), len(siblings)))
            return result

        if "/serie/" in url_low or "/series/" in url_low:
            log("EgyDead: parsing series page")
            title, poster, plot, year = self._extract_detail_meta(html)
            result["title"] = title
            result["poster"] = poster
            result["plot"] = plot
            result["year"] = year
            info = self._parse_info_box(html)         # [PATCH 110]
            if info.get("year"):
                result["year"] = info["year"]
            for _k in ("genres", "country", "quality", "language", "channel", "runtime", "category"):
                if info.get(_k):
                    result[_k] = info[_k]
            result["type"] = "series"

            seasons = self._parse_season_list(html)
            result["items"] = seasons
            log("EgyDead: series {} → {} seasons".format(title, len(seasons)))
            return result

        log("EgyDead: parsing movie page (fallback)")
        title, poster, plot, year = self._extract_detail_meta(html)
        result["title"] = title
        result["poster"] = poster
        result["plot"] = plot
        result["year"] = year
        info = self._parse_info_box(html)         # [PATCH 110]
        if info.get("year"):
            result["year"] = info["year"]
        for _k in ("genres", "country", "quality", "language", "channel", "runtime", "category"):
            if info.get(_k):
                result[_k] = info[_k]
        result["type"] = "movie"

        servers = self._extract_watch_servers(html, final_url or url)
        if not servers:
            log("EgyDead: no servers on initial load, retrying with View=1 POST")
            post_html, post_final_url = self._fetch(url, post_data={"View": "1"})
            if post_html:
                servers = self._extract_watch_servers(post_html, post_final_url or url)

        result["servers"] = servers
        # [PATCH 109] the site's own download list takes priority; fall back to the
        # MegaMax-mirror reuse (PATCH 99) only when this page has no download list.
        real_downloads = self._parse_download_servers(html)
        result["downloads"] = real_downloads if real_downloads else self._pull_downloads(servers)
        log("EgyDead: movie {} → {} servers".format(title, len(servers)))
        return result

    def extract_stream(self, url, _depth=0):
        """
        Resolve a server URL to a playable stream.

        Returns a 4-tuple: (stream_url, quality, referer, variants) where
        `variants` is a best-first list of (label, url) quality options
        EXCLUDING the stream being returned. A failed resolution returns
        (None, "", referer, []) — callers must check stream_url.
        """
        _STMRUBY_PIPE = "Referer=https://stmruby.com/&Origin=https://stmruby.com"

        def _pipe_stmruby(u):
            if not u or "|" in u:
                return u
            return u + "|" + _STMRUBY_PIPE

        def _variants_for(stream):
            v = []
            if get_last_quality_variants:
                v = [(lbl, u) for lbl, u in (get_last_quality_variants() or []) if u != stream]
            if not v and get_synthesized_variants:
                v = [(lbl, u) for lbl, u in (get_synthesized_variants(stream) or []) if u != stream]
            return v

        # Correct the URL first
        url = _correct_stream_url(url or "")
        low = url.lower()

        # [PATCH 99] a bare MegaMax iframe (old history/favorites, or the mirror list could not be read)
        if self._is_megamax_iframe(url) and _depth < 2:
            tried = 0
            try:
                mm_servers = self._megamax_servers(url)
            except Exception as e:
                log("EgyDead: MegaMax expansion failed: {}".format(e))
                mm_servers = []
            for s in mm_servers:
                if tried >= 5:
                    break
                tried += 1
                res = self.extract_stream(s["url"], _depth=_depth + 1)
                if res and res[0]:
                    return res
            return None, "", self._get_base(), []


        # ─── STREAMRUBY ──────────────────────────────────────────────────
        if ("stmruby" in low or "streamruby" in low) and resolve_streamruby:
            stream = resolve_streamruby(url)
            if stream:
                stream = _correct_stream_url(stream)
                quality = _extract_quality_from_streamruby_url(stream)
                variants = _variants_for(stream)
                variants = [(lbl, _pipe_stmruby(u)) for lbl, u in variants]
                return (
                    _pipe_stmruby(stream),
                    quality,
                    "https://stmruby.com/",
                    variants,
                )

        # ─── MIXDROP ──────────────────────────────────────────────────────
        if "mixdrop" in low or "miixdrop" in low or "mxcontent" in low:
            if resolve_mixdrop:
                stream = resolve_mixdrop(url)
                if stream:
                    stream = _correct_stream_url(stream)
                    return stream, None, None, _variants_for(stream)

        # ─── DOODSTREAM ───────────────────────────────────────────────────
        if "dood" in low or "cloudatacdn" in low:
            if resolve_doodstream:
                stream = resolve_doodstream(url)
                if stream:
                    stream = _correct_stream_url(stream)
                    return stream, None, None, _variants_for(stream)

        # ─── GOVID ────────────────────────────────────────────────────────
        if "govid.live" in low and resolve_govid is not None:
            stream = resolve_govid(url)
            if stream:
                stream = _correct_stream_url(stream)
                return stream, None, None, _variants_for(stream)

        # ─── STREAMWISH ──────────────────────────────────────────────────
        if "streamwish" in low or "wishfast" in low or "filelion" in low or "filelions" in low:
            if resolve_streamwish:
                stream = resolve_streamwish(url)
                if stream:
                    stream = _correct_stream_url(stream)
                    return stream, None, None, _variants_for(stream)

        # ─── VOE ──────────────────────────────────────────────────────────
        if "voe.sx" in low:
            if resolve_voe:
                stream = resolve_voe(url)
                if stream:
                    stream = _correct_stream_url(stream)
                    return stream, None, None, _variants_for(stream)

        # ─── SEND.CM ──────────────────────────────────────────────────────
        if "send.cm" in low:                      # [PATCH 76] "send" matched any URL containing it
            try:
                html, _ = fetch(url, referer=self._get_base())
                if html:
                    # Look for download link patterns
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
                            stream = _correct_stream_url(match.group(1))
                            if stream:
                                return stream, None, None, []
                    # If no direct URL, try iframe chain
                    for iframe_url in extract_iframes(html, url):
                        return self.extract_stream(iframe_url, _depth=_depth + 1)
            except Exception as e:
                log("EgyDead: send.cm resolution failed: {}".format(e))

        # ─── VIKINGFILE ──────────────────────────────────────────────────
        if "vikingfile" in low:
            try:
                html, _ = fetch(url, referer=self._get_base())
                if html:
                    patterns = [
                        r'(https?://[^\s"\'<>]+\.vikingfile\.com[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
                        r'(https?://[^\s"\'<>]+\.vikingfile\.com[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
                        r'(https?://[^\s"\'<>]+/f/[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
                        r'<iframe[^>]+src=["\']([^"\']+)["\']',
                        r'"file"\s*:\s*"([^"]+\.(?:mp4|m3u8)[^"]*)"',
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, html, re.I)
                        if match:
                            stream = _correct_stream_url(match.group(1))
                            if stream:
                                return stream, None, None, []
                    # If no direct URL, try iframe chain
                    for iframe_url in extract_iframes(html, url):
                        return self.extract_stream(iframe_url, _depth=_depth + 1)
            except Exception as e:
                log("EgyDead: vikingfile resolution failed: {}".format(e))

        # ─── BYSERAGUCI ──────────────────────────────────────────────────
        if "byseragucu" in low or "byse" in low:
            if resolve_byselapuix:
                stream = resolve_byselapuix(url)
                if stream:
                    stream = _correct_stream_url(stream)
                    return stream, None, None, _variants_for(stream)

        # ─── VIDHIDEVIP ──────────────────────────────────────────────────
        if "vidhidevip" in low or "vidhide" in low:
            if resolve_streamwish:
                stream = resolve_streamwish(url)
                if stream:
                    stream = _correct_stream_url(stream)
                    return stream, None, None, _variants_for(stream)

        # ─── .TXT MANIFESTS (Maplecrest, etc.) ─────────────────────────────
        if ".txt" in low and any(k in low for k in (
            "systemorchestration", "highqualityprints", "dramiyos",
            "maplecrest", "lakesideproductionstudio",
        )):
            log("EgyDead: .txt HLS manifest detected: {}".format(url[:80]))
            html, _ = fetch(url, referer=self._get_base())
            if html and "#EXTM3U" in html:
                if "#EXT-X-STREAM-INF" in html:
                    parsed = self._parse_master_playlist(url, html)
                    if parsed:
                        best_label, best_url = parsed[0]
                        return best_url, best_label, self._get_base(), parsed[1:]
                # Plain media playlist: hand the raw manifest URL to the
                # player (content is sniffed; the extension is disguised).
                return url, "HD", self._get_base(), []

        # ─── .M3U8 DIRECT ──────────────────────────────────────────────────
        if ".m3u8" in low:
            variants = self._detect_quality_variants(url)
            quality = self._quality_from_url(url)
            variants = [(lbl, u) for lbl, u in (variants or []) if u != url]
            return url, quality, self._get_base(), variants

        # ─── .MP4 DIRECT ───────────────────────────────────────────────────
        if ".mp4" in low:
            return url, self._quality_from_url(url), self._get_base(), []

        # ─── VIBUXER ──────────────────────────────────────────────────────
        if "vibuxer" in low:
            log("EgyDead: vibuxer embed detected: {}".format(url[:80]))
            html, _ = fetch(url, referer=self._get_base())
            if html and _depth < 3:
                iframe = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
                if iframe:
                    return self.extract_stream(iframe.group(1), _depth=_depth + 1)
                stream = re.search(r'(https?://[^\s"\'<>]+\.(?:m3u8|txt|woff2)[^\s"\'<>]*)', html, re.I)
                if stream:
                    return self.extract_stream(_correct_stream_url(stream.group(1)), _depth=_depth + 1)

        # ─── GENERIC IFRAME RESOLUTION ────────────────────────────────────
        # If we have an iframe URL, try to resolve it
        if "iframe" in low or "/e/" in low or "/embed/" in low:
            html, _ = fetch(url, referer=self._get_base())
            if html and _depth < 3:
                # Try to find stream URL in the iframe page
                for pattern in [
                    r'(https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)',
                    r'"file"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
                    r'"src"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
                ]:
                    match = re.search(pattern, html, re.I)
                    if match:
                        stream = _correct_stream_url(match.group(1))
                        if stream:
                            return stream, None, None, []
                # Try nested iframes
                for iframe_url in extract_iframes(html, url):
                    return self.extract_stream(iframe_url, _depth=_depth + 1)

        # ─── FALLBACK (generic resolver) ──────────────────────────────────
        if base_extract_stream is None:
            return None, "", self._get_base(), []
        try:
            result = base_extract_stream(url)
        except Exception as e:
            log("EgyDead: generic resolver failed for {}: {}".format(url[:80], e))
            return None, "", self._get_base(), []

        # Normalize whatever the base resolver returns (its documented
        # contract is a 3-tuple) into this extractor's 4-tuple.
        if isinstance(result, tuple):
            if len(result) >= 3:
                stream = result[0]
                quality = result[1] or ""
                referer = result[2] or self._get_base()
                raw_variants = list(result[3]) if len(result) > 3 and result[3] else []
                variants = []
                for entry in raw_variants:
                    if isinstance(entry, (tuple, list)) and len(entry) == 2:
                        lbl, u = entry
                        if u != stream:
                            variants.append((lbl, u))
                return stream, quality, referer, variants
            if len(result) == 2:
                return result[0], result[1] or "", self._get_base(), []
            if len(result) == 1:
                return result[0], "", self._get_base(), []
        elif isinstance(result, str) and result:
            return result, "", self._get_base(), []
        return None, "", self._get_base(), []

    def _detect_quality_variants(self, stream_url):
        """
        Build a best-first (label, url) list of quality variants for a
        stream URL. The returned list may include the current URL;
        callers filter it out when building the quality menu.

        (CRITICAL fix carried over: this was previously outdented to
        module level and never bound to the class — every call raised
        AttributeError.)
        """
        if not stream_url:
            return []

        stream_url = _correct_stream_url(stream_url)
        low = stream_url.lower()
        variants = []
        seen = set()

        def _add(label, u):
            u = _correct_stream_url(u)
            if u and u not in seen:
                seen.add(u)
                variants.append((label, u))

        # For streamruby.net master playlists, prefer fetching and
        # parsing the actual HLS master playlist over guessing by string
        # surgery.
        if "streamruby.net" in low and "master.m3u8" in low:
            body = None
            try:
                body, _ = fetch(stream_url, referer="https://stmruby.com/")
            except Exception:
                body = None
            if body and "#EXT-X-STREAM-INF" in body:
                parsed = []
                try:
                    from .base import _parse_hls_master_variants as _base_parse_master
                    _res = _base_parse_master(stream_url, body)
                    if (
                        _res
                        and isinstance(_res, (list, tuple))
                        and _res[0]
                        and isinstance(_res[0], (list, tuple))
                        and len(_res[0]) == 2
                    ):
                        parsed = [tuple(x) for x in _res]
                except Exception:
                    parsed = []
                if not parsed:
                    parsed = self._parse_master_playlist(stream_url, body)
                if parsed:
                    return parsed

        # ─── STREAMRUBY QUALITY PATTERN: _,l,n,h,o,.urlset/ ──────────────
        # Example: .../psbm7b7diwj7_,l,n,h,o,.urlset/master.m3u8
        quality_pattern = re.search(r'([^/]+)_,((?:l,|n,|h,|o,)+)\.urlset/', stream_url)
        if quality_pattern:
            # group(1) IS the video id — slice to end(1) so it survives.
            base_part = stream_url[:quality_pattern.end(1)]
            tail = stream_url[quality_pattern.end():]
            present_suffixes = [tok for tok in quality_pattern.group(2).split(',') if tok]

            quality_map = {'o': 'Original', 'h': '720p', 'n': '480p', 'l': '360p'}
            for suffix in present_suffixes:
                label = quality_map.get(suffix)
                if not label:
                    continue
                _add(label, "{}_{},.urlset/{}".format(base_part, suffix, tail))

            order = {"Original": 0, "1080p": 1, "720p": 2, "480p": 3, "360p": 4}
            variants.sort(key=lambda v: order.get(v[0], 9))
            if variants:
                return variants

        # ─── F1/F2/F3 PATTERN ──────────────────────────────────────────────
        # e.g. ".../index-f2-v1-a1.m3u8" (or ".../index-2-v1.m3u8")
        f_match = re.search(r'(index-?f?)(\d+)([-_v][^\s"\'<>]+\.(?:m3u8|txt))', stream_url, re.I)
        if f_match:
            prefix = f_match.group(1)
            if not prefix.endswith('f'):
                prefix += 'f'
            head = stream_url[:f_match.start(1)] + prefix
            tail = f_match.group(3)
            for num, label in (('3', '1080p'), ('2', '720p'), ('1', '480p')):
                _add(label, head + num + tail)
            if variants:
                return variants

        # ─── _LNHO PATTERN ──────────────────────────────────────────────────
        l_match = re.search(r'(_[lnhox])(?=/)', stream_url)
        if l_match:
            synth_domains = (
                "streamruby", "stmruby", "cdn-video.xyz", "uqload",
                "tnmr", "sprintcdn", "systemorchestration",
                "highqualityprints", "maplecrestwellness",
                "lakesideproductionstudio", "dramiyos",
            )
            if any(d in low for d in synth_domains):
                if "streamruby" in low or "stmruby" in low or "tnmr" in low:
                    suffix_pairs = (
                        ('_o', 'Original'), ('_h', '720p'),
                        ('_n', '480p'), ('_l', '360p'),
                    )
                else:
                    suffix_pairs = (
                        ('_x', 'Original'), ('_h', '720p'),
                        ('_n', '480p'), ('_l', '360p'),
                    )
                base_part = stream_url[:l_match.start()]
                rest = stream_url[l_match.end():]
                for s, label in suffix_pairs:
                    _add(label, base_part + s + rest)
                if variants:
                    return variants

        # ─── HLS PATTERN ────────────────────────────────────────────────────
        if re.search(r'/hls[23]/', low):
            variants.append(('HD', stream_url))

        return variants

    def _parse_master_playlist(self, master_url, body):
        """
        Parse a standard HLS master playlist into a best-first list of (label, url) variants.
        [PATCH 99] labels come from the rendition WIDTH (1920x1036 is 1080p, 1336x720 is 720p), and the
        order is numeric (width, height, bandwidth) - not a label table.
        """
        entries = []
        pw = ph = pbw = 0
        for raw_line in (body or "").splitlines():
            line = raw_line.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = line[len("#EXT-X-STREAM-INF:"):]
                res_m = re.search(r'RESOLUTION=(\d+)x(\d+)', attrs)
                bw_m = re.search(r'BANDWIDTH=(\d+)', attrs)
                pw = int(res_m.group(1)) if res_m else 0
                ph = int(res_m.group(2)) if res_m else 0
                pbw = int(bw_m.group(1)) if bw_m else 0
                continue
            if not line or line.startswith("#"):
                continue
            u = line
            if not re.match(r'^https?://', u, re.I):
                u = urljoin(master_url, u)
            w, h, bw = pw, ph, pbw
            pw = ph = pbw = 0
            if _quality_bucket:
                label = _quality_bucket(w, h, bw)
            elif h >= 1000:
                label = "1080p"
            elif h >= 680:
                label = "720p"
            elif h >= 440:
                label = "480p"
            elif h > 0:
                label = "360p"
            elif bw > 0:
                label = "Quality {}k".format(max(1, bw // 1000))
            else:
                label = "Auto"
            entries.append((label, u, w, h, bw))

        entries.sort(key=lambda e: (e[2], e[3], e[4]), reverse=True)

        seen = set()
        out = []
        for label, u, _w, _h, _bw in entries:
            cu = _correct_stream_url(u)
            if cu and cu not in seen:
                seen.add(cu)
                out.append((label, cu))
        return out