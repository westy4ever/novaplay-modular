# -*- coding: utf-8 -*-
"""
EgyBest extractor — WordPress site

Canonical base: https://egybests.live/ (stable mirror; a number of
egybest.* aliases redirect here).  Content pages on every mirror live
at bare WordPress paths (/category/..., /?s=..., /<slug>/) — the
rotating homepage prefix trick that EgyDead uses does NOT apply here;
`egybest.fyi` / `egybest.cam` / `egybest.live` all resolve to the same
bare paths.

Sequence followed (mirrors egydead.py):

    homepage      → get_categories(mtype) — the 7 top nav tabs plus
                    every Arabic taxonomy + every genre taxonomy in
                    the sidebar menu (Latin-slug genres included)
    category      → get_category_items(url, page) — parses postBlock
                    and postBlockCol cards, follows /page/N/ pagination
    movie landing → get_page(url) — parses postTable metadata
    revealed      → same get_page() — hits the /watch/ URL (or
                    ?watch=1) when the servers list is rendered lazily
    embeds        → .servList li / iframes / data-* attributes
    downloads     → downloadMaster / dnlTables / data-href link pairs

Fixes applied in this revision:
  * Multi-domain probing with a deep-content probe (a landing-only
    mirror returns an empty /category/movies/ page; rejecting it
    prevents "all categories look identical").
  * _full_url() percent-encodes non-ASCII bytes for ABSOLUTE URLs too —
    EgyBest slugs are Arabic and the HTTP client cannot put raw
    non-ASCII in a request line.
  * Pagination regex tolerates both `/page/N/` and `?paged=N` shapes.
  * Playable-server parsing is defensive: data-link / data-url /
    data-iframe / iframe src / <source> / direct media URL, in order,
    de-duplicated by URL.
  * Download entries: single downloadMaster block OR per-resolution
    tables (dnlTables), both shapes existing on the real site.

NEW (categories revision):
  * get_categories() now emits the FULL menu the site exposes:
      - "recent/" alongside "last/" (both live menu entries)
      - "افلام الجيزاوي" (curated EgyBest-only category)
      - "غير مصنف" (Uncategorized catch-all)
      - the entire genre/* taxonomy: ~55 Arabic genre pages
        (دراما، اكشن، رعب، كوميدي، …) + the 8 Latin-slug pages
        (comedy, romance, music, reality-tv, game-show, short,
        life, melodrama).
    Arabic slugs are written unquoted — _full_url() percent-encodes
    them, which keeps the source readable instead of %d8%a7… soup.
"""

import re
import base64
import time
import threading
import json

from .base import (
    BaseExtractor, fetch, log,
    _correct_stream_url,
    extract_iframes,
)
from urllib.parse import urljoin, urlparse, quote_plus, quote, unquote
from html import unescape as html_unescape


# ─── Process-wide base-domain cache ──────────────────────────────────────────
_BASE_CACHE_TTL = 300            # 5 minutes
_PROBE_COOLDOWN = 30             # 30 seconds
_base_cache = {"url": None, "resolved_at": 0, "probed_at": 0}
_base_cache_lock = threading.Lock()
_probe_lock = threading.Lock()

_PROBE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
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


class EgyBestExtractor(BaseExtractor):
    """Extractor for EgyBest (egybests.live and mirrors)."""

    MAIN_URL = "https://egybests.live/"

    DOMAINS = [
        "https://egybests.live/",
        "https://egybest.live/",
        "https://egybest.fyi/",
        "https://egybest.cam/",
        "https://egybest.top/",
        "https://egybest.vip/",
        "https://www.egybest.live/",
    ]

    VALID_HOST_MARKERS = ("egybest",)
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com", "watch-it-legally")

    CLEAN_WORDS = [
        "مشاهدة", "تحميل", "فيلم", "مسلسل", "الحلقة", "حلقة",
        "مترجم", "مترجمة", "مدبلج", "مدبلجة",
        "اون لاين", "أون لاين", "اونلاين",
        "بجودة", "عالية", "كامل", "حصريا",
        "والاخيرة", "والأخيرة", "الاخيرة", "الأخيرة",
        "جميع مواسم", "جميع الحلقات",
    ]

    def __init__(self):
        super(EgyBestExtractor, self).__init__()
        self.main_url = self.MAIN_URL
        self._resolved_base = None

    # ─── Domain probing ─────────────────────────────────────────────────
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

    def _looks_like_egybest_page(self, html):
        text = html or ""
        return (
            "postBlock" in text
            or "EgyBest" in text
            or "ايجي بست" in text
            or "ايجى بست" in text
            or "egybest" in text.lower()
            or "postTable" in text
            or "servList" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        """Confirm the candidate actually serves listing pages, not just a
        homepage.  Some mirrors respond 200 to `/` but return the same
        landing page for every deep URL — accepting them makes every
        category collapse to the same items."""
        if not base:
            return False
        test_url = urljoin(base, "category/movies/")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("EgyBest: deep-content probe failed for {}: {}".format(base, e))
            return False
        if not html:
            return False
        if self._is_blocked_page(html, final or ""):
            return False
        # A real /category/movies/ listing contains many postBlock cards.
        n_cards = html.count("postBlock")
        if n_cards < 6:
            log("EgyBest: {} responded but only {} postBlock markers on "
                "/category/movies/ — likely a landing, not a content mirror".format(
                    base, n_cards))
            return False
        return True

    def _get_base(self):
        if self._resolved_base:
            return self._resolved_base

        now = time.time()
        with _base_cache_lock:
            cached_url = _base_cache["url"]
            resolved_at = _base_cache["resolved_at"]
            probed_at = _base_cache["probed_at"]

        if cached_url and (now - resolved_at) < _BASE_CACHE_TTL:
            self._resolved_base = cached_url
            self.main_url = cached_url
            return cached_url

        if cached_url and (now - probed_at) < _PROBE_COOLDOWN:
            self._resolved_base = cached_url
            self.main_url = cached_url
            return cached_url

        with _probe_lock:
            # Double-check after acquiring the lock
            now = time.time()
            with _base_cache_lock:
                cached_url = _base_cache["url"]
                resolved_at = _base_cache["resolved_at"]
                probed_at = _base_cache["probed_at"]
            if cached_url and (now - resolved_at) < _BASE_CACHE_TTL:
                self._resolved_base = cached_url
                self.main_url = cached_url
                return cached_url
            if cached_url and (now - probed_at) < _PROBE_COOLDOWN:
                self._resolved_base = cached_url
                self.main_url = cached_url
                return cached_url

            for domain in self.DOMAINS:
                log("EgyBest: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain,
                                        extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain

                if not self._is_valid_site_url(final_url):
                    log("EgyBest: unexpected host after redirect {}".format(final_url))
                    continue
                if self._is_blocked_page(html, final_url):
                    log("EgyBest: blocked {}".format(final_url))
                    continue
                if not (html and self._looks_like_egybest_page(html)):
                    log("EgyBest: page shape mismatch on {}".format(final_url))
                    continue

                candidate = self._site_root(final_url)
                if not self._base_serves_deep_content(candidate):
                    log("EgyBest: {} is not a content mirror — trying next".format(candidate))
                    continue

                self._resolved_base = candidate
                self.main_url = candidate
                with _base_cache_lock:
                    _base_cache["url"] = candidate
                    _base_cache["resolved_at"] = now
                    _base_cache["probed_at"] = now
                log("EgyBest: selected base {}".format(candidate))
                return candidate

            # Fallback: the stable alias
            self._resolved_base = self.MAIN_URL
            self.main_url = self._resolved_base
            with _base_cache_lock:
                _base_cache["url"] = self._resolved_base
                _base_cache["probed_at"] = now
            log("EgyBest: all probes failed, falling back to {}".format(self._resolved_base))
            return self._resolved_base

    # ─── URL / title helpers ────────────────────────────────────────────
    def _clean_title(self, title):
        title = self._strip_tags(title)
        title = html_unescape(title or "")
        # Strip an appended release year (it becomes its own field)
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
        EgyBest slugs are Arabic (‎/مشاهدة-مسلسل-…/), and the HTTP
        client used by the plugin cannot put raw non-ASCII bytes in a
        request line.
        """
        if not path:
            return ""
        path = html_unescape(path.strip())
        if path.startswith("//"):
            path = "https:" + path
        if path.startswith("http"):
            full_url = path
        else:
            base = self._get_base()
            full_url = urljoin(base, path)
            parsed = urlparse(full_url)
            if "//" in parsed.path and parsed.path != "/":
                cleaned = re.sub(r"/+", "/", parsed.path)
                full_url = parsed._replace(path=cleaned).geturl()
        try:
            if any(ord(c) > 127 for c in full_url) or any(
                    c in full_url for c in ' <>"\''):
                full_url = quote(full_url, safe=":/?&=#+%")
        except Exception:
            pass
        return full_url

    def _encode_arabic_url(self, url):
        try:
            parsed = urlparse(url)
            segments = []
            for segment in parsed.path.split("/"):
                if segment and any(ord(c) > 127 for c in segment):
                    segments.append(quote(segment, safe="%"))
                else:
                    segments.append(segment)
            path = "/".join(segments)
            if not path.startswith("/"):
                path = "/" + path
            encoded_query = ""
            if parsed.query:
                parts = []
                for part in parsed.query.split("&"):
                    if "=" in part:
                        key, val = part.split("=", 1)
                        if any(ord(c) > 127 for c in val):
                            parts.append(key + "=" + quote_plus(val.encode("utf-8"), safe="%"))
                        else:
                            parts.append(part)
                    else:
                        parts.append(part)
                encoded_query = "&".join(parts)
            return parsed._replace(path=path, query=encoded_query).geturl()
        except Exception:
            return url

    def _fetch(self, url, referer=None, post_data=None, extra_headers=None):
        extra = dict(_PROBE_HEADERS)
        if post_data:
            extra["Content-Type"] = "application/x-www-form-urlencoded"
            extra["X-Requested-With"] = "XMLHttpRequest"
        if extra_headers:
            extra.update(extra_headers)
        encoded = self._encode_arabic_url(url)
        return fetch(encoded,
                     referer=referer or self._get_base(),
                     extra_headers=extra,
                     post_data=post_data)

    # ─── Cards ──────────────────────────────────────────────────────────
    def _pick_real_image(self, block):
        """Return the real lazy-loaded poster URL.  EgyBest uses
        src-default-new.jpg as a placeholder while the actual URL sits
        in data-img / data-src."""
        best = None
        for tag in re.findall(r'<img[^>]+>', block or "", re.I):
            for attr in ("data-img", "data-src", "data-lazy-src",
                         "data-original", "src"):
                m = re.search(attr + r'=["\']([^"\']+)["\']', tag, re.I)
                if not m:
                    continue
                url = m.group(1).strip()
                if any(x in url.lower() for x in (
                        "src-default", "src-default-new",
                        "placeholder", "lazy_load", "loading.gif",
                        "data:image", "logo")):
                    continue
                if attr == "data-img":
                    return url           # prefer the explicit real URL
                if best is None:
                    best = url
        return best

    def _parse_movie_items(self, html, current_url=None):
        items = []
        seen = set()

        # Split into postBlock / postBlockCol cards.  Both share the
        # same inner structure: <a><img><h3 class="title">…</h3></a>.
        blocks = re.split(r'(?=<a[^>]+class="[^"]*postBlock)', html or "")
        for raw in blocks[1:]:
            # Trim the block to its closing </a> so we don't pull in
            # the next card's rating.
            end = raw.find("</a>")
            if end < 0:
                continue
            block = raw[:end + 4]

            a_m = re.search(
                r'<a[^>]+class=["\'][^"\']*postBlock[^"\']*["\'][^>]*'
                r'href=["\']([^"\']+)["\']',
                block, re.I)
            if not a_m:
                continue
            url = self._full_url(a_m.group(1))
            if not url or url in seen:
                continue
            if any(x in url for x in ("/page/", "page=", "/category/",
                                      "/genre/", "/tag/", "#")):
                continue
            seen.add(url)

            title = ""
            h3_m = re.search(r'<h3[^>]*class=["\'][^"\']*title[^"\']*["\'][^>]*>(.*?)</h3>',
                             block, re.S | re.I)
            if h3_m:
                title = self._clean_title(h3_m.group(1))
            if not title:
                alt_m = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', block, re.I)
                if alt_m:
                    title = self._clean_title(alt_m.group(1))
            if not title:
                continue

            poster = self._pick_real_image(block)
            if poster:
                poster = self._full_url(poster)

            rating = ""
            rm = re.search(
                r'<i[^>]*class=["\'][^"\']*\brating\b[^"\']*["\'][^>]*>'
                r'.*?<i[^>]*>\s*([\d.]+)\s*</i>',
                block, re.S | re.I)
            if not rm:
                rm = re.search(r'class=["\'][^"\']*\brating\b[^"\']*["\'][^>]*>'
                               r'\s*([\d.]+)', block, re.I)
            if rm:
                rating = rm.group(1).strip()

            # Ribbon (quality/status badge)
            label = ""
            rib_m = re.search(r'<div[^>]*class=["\'][^"\']*\bribbon\b[^"\']*["\'][^>]*>'
                              r'\s*<span[^>]*>(.*?)</span>',
                              block, re.S | re.I)
            if rib_m:
                label = self._strip_tags(rib_m.group(1)).strip()

            # Year from title
            year = ""
            ym = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            if ym:
                year = ym.group(1)
                title = re.sub(r'\s*\b' + year + r'\b\s*', ' ', title)
                title = re.sub(r'\s{2,}', ' ', title).strip(' -|')

            url_low = url.lower()
            raw_title = h3_m.group(1) if h3_m else title
            if "الحلقة" in raw_title or "حلقة" in raw_title or "/episode" in url_low:
                item_type = "episode"
            elif "مسلسل" in raw_title or "انمي" in raw_title or "/series" in url_low:
                item_type = "series"
            else:
                item_type = "movie"

            items.append({
                "title": title,
                "url": url,
                "poster": poster or "",
                "plot": "",
                "label": label,
                "year": year,
                "rating": rating,
                "type": item_type,
                "_action": "details",
            })
        return items

    def _parse_episode_list(self, html):
        """Parse an episode list from a series page."""
        items = []
        seen = set()
        # EgyBest renders episodes inside .all-episodes / .EpisodesList
        for m in re.finditer(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                html or "", re.S | re.I):
            url = self._full_url(m.group(1))
            if not url or url in seen:
                continue
            inner = m.group(2)
            text = self._strip_tags(inner).strip()
            if not text:
                continue
            # Only count actual episode links
            if ("حلقة" not in text and "الحلقة" not in text
                    and "/episode" not in url.lower()):
                continue
            seen.add(url)
            items.append({
                "title": text,
                "url": url,
                "type": "episode",
                "_action": "details",
            })
        return items

    def _parse_season_list(self, html):
        """Parse season links from a series landing page."""
        items = []
        seen = set()
        for m in re.finditer(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                html or "", re.S | re.I):
            url = self._full_url(m.group(1))
            if not url or url in seen:
                continue
            if "/season" not in url.lower():
                continue
            title = self._strip_tags(m.group(2)).strip() or "موسم"
            seen.add(url)
            items.append({
                "title": title,
                "url": url,
                "type": "season",
                "_action": "details",
            })
        return items

    def _parse_pagination(self, html, current_url):
        next_href = ""
        m = re.search(r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']',
                      html or "", re.I)
        if not m:
            m = re.search(r'<a[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']',
                          html or "", re.I)
        if not m:
            m = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']next["\']',
                          html or "", re.I)
        if not m:
            m = re.search(r'<a[^>]+class=["\'][^"\']*\bnext\b[^"\']*["\'][^>]+'
                          r'href=["\']([^"\']+)["\']', html or "", re.I)
        if not m:
            m = re.search(r'<a[^>]+href=["\']([^"\']+)"[^>]*>\s*'
                          r'(?:&raquo;|»|التالي)\s*</a>', html or "", re.I | re.S)
        if m:
            next_href = m.group(1)
        if not next_href:
            return None
        next_url = self._full_url(next_href)
        if next_url and next_url != current_url:
            return {
                "title": "➡️ الصفحة التالية",
                "url": next_url,
                "type": "category",
                "_action": "category",
            }
        return None

    # ─── Public API ─────────────────────────────────────────────────────
    def get_categories(self, mtype="movie"):
        """Homepage → the site's own top-level navigation tabs plus every
        taxonomy in the sidebar menu (Arabic sections + the full genre
        taxonomy, both Arabic and Latin slugs).  Each entry is a
        `category` item the UI opens with `get_category_items()`."""
        base = self._get_base()

        cats = []

        # ── Quick landing pages (Latin slugs — always available) ────
        cats.append({"title": "🏠 الرئيسية", "url": base, "type": "category", "_action": "category"})
        cats.append({"title": "🆕 جديد ايجى بست",  "url": urljoin(base, "recent/"), "type": "category", "_action": "category"})
        cats.append({"title": "⏰ المضاف حديثًا",   "url": urljoin(base, "last/"),   "type": "category", "_action": "category"})
        cats.append({"title": "🔥 التريند",         "url": urljoin(base, "trends/"), "type": "category", "_action": "category"})

        # ── Latin-slug section landings ─────────────────────────────
        cats.append({"title": "── أقسام ──", "url": "", "type": "separator"})
        cats.append({"title": "🎬 أحدث الأفلام", "url": urljoin(base, "movies/"), "type": "category", "_action": "category"})
        cats.append({"title": "📺 أحدث الحلقات", "url": urljoin(base, "series/"), "type": "category", "_action": "category"})
        cats.append({"title": "🎌 أحدث الكرتون", "url": urljoin(base, "category/anime/"), "type": "category", "_action": "category"})

        # ── Movie taxonomies ────────────────────────────────────────
        cats.append({"title": "── أفلام ──", "url": "", "type": "separator"})
        movie_tax = [
            ("🎬 أفلام",            "category/movies/"),
            ("🌍 أفلام أجنبي",      "category/movies/افلام-اجنبي/"),
            ("🌏 أفلام اسيوي",      "category/movies/افلام-اسيوي/"),
            ("🌏 أفلام اسيوية",     "category/movies/افلام-اسيوية/"),
            ("🇹🇷 أفلام تركية",      "category/movies/افلام-تركية/"),
            ("🇨🇳 أفلام صينية",      "category/movies/افلام-صينية/"),
            ("🇮🇳 أفلام هندية",      "category/movies/افلام-هندية/"),
            ("📽️ أفلام وثائقية",    "category/movies/افلام-وثائقية/"),
            ("🎙️ أفلام الجيزاوي",    "category/movies/افلام-اسلام-الجيزاوي/"),
            ("🎙️ أفلام مدبلجة",      "category/افلام-اجنبية-مدبلجة/"),
        ]
        for title, path in movie_tax:
            cats.append({
                "title": title,
                "url": urljoin(base, path),
                "type": "category",
                "_action": "category",
            })

        # ── Series taxonomies ───────────────────────────────────────
        cats.append({"title": "── مسلسلات ──", "url": "", "type": "separator"})
        series_tax = [
            ("📺 مسلسلات",          "category/series/"),
            ("📺 مسلسلات اجنبي",    "category/series/مسلسلات-اجنبي/"),
            ("📺 مسلسلات مدبلجة",   "category/series/مسلسلات-اجنبي-مدبلجة/"),
            ("📺 مسلسلات اسيوية",   "category/series/مسلسلات-اسيوية/"),
            ("📺 مسلسلات تركية",    "category/series/مسلسلات-تركية/"),
            ("📺 مسلسلات لاتينية",  "category/series/مسلسلات-لاتينية/"),
            ("📺 مسلسلات وثائقية",  "category/series/مسلسلات-وثائقية/"),
        ]
        for title, path in series_tax:
            cats.append({
                "title": title,
                "url": urljoin(base, path),
                "type": "category",
                "_action": "category",
            })

        # ── Anime / cartoon ─────────────────────────────────────────
        cats.append({"title": "── انمى وكرتون ──", "url": "", "type": "separator"})
        anime_tax = [
            ("🎌 انمى",            "category/anime/"),
            ("🎌 أفلام انمي",      "category/anime/افلام-انمي/"),
            ("🎠 أفلام كرتون",     "category/anime/افلام-كرتون/"),
            ("🎌 مسلسلات انمي",    "category/anime/مسلسلات-انمي/"),
            ("🎠 مسلسلات كرتون",   "category/anime/مسلسلات-كرتون/"),
        ]
        for title, path in anime_tax:
            cats.append({
                "title": title,
                "url": urljoin(base, path),
                "type": "category",
                "_action": "category",
            })

        # ── Other Arabic sections ───────────────────────────────────
        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        other_tax = [
            ("📡 برامج تلفزيونية",   "category/برامج-تلفزيونية/"),
            ("🎤 عروض وحفلات",       "category/عروض-وحفلات/"),
            ("📁 غير مصنف",           "category/غير-مصنف/"),
        ]
        for title, path in other_tax:
            cats.append({
                "title": title,
                "url": urljoin(base, path),
                "type": "category",
                "_action": "category",
            })

        # ── Genre taxonomy — Arabic slugs ───────────────────────────
        cats.append({"title": "── تصنيفات حسب النوع ──", "url": "", "type": "separator"})
        genre_ar = [
            ("🎭 أكشن",              "genre/أكشن/"),
            ("🎭 اكشن",              "genre/اكشن/"),
            ("😂 كوميدي",            "genre/كوميدي/"),
            ("😂 كوميدى",            "genre/كوميدى/"),
            ("😂 كوميديا",           "genre/كوميديا/"),
            ("🎭 دراما",             "genre/دراما/"),
            ("🎭 درا",               "genre/درا/"),
            ("🔪 جريمة",             "genre/جريمة/"),
            ("👻 رعب",               "genre/رعب/"),
            ("💕 رومانسي",            "genre/رومانسي/"),
            ("💕 رومانسية",           "genre/رومانسية/"),
            ("💕 روماسية",            "genre/روماسية/"),
            ("💕 رومنسية",            "genre/رومنسية/"),
            ("💕 روم",                "genre/روم/"),
            ("😱 تشويق",              "genre/تشويق/"),
            ("😱 إثارة",              "genre/إثارة/"),
            ("😱 اثارة",              "genre/اثارة/"),
            ("😱 ﺗﺸﻮﻳﻖ ﻭﺇﺛﺎﺭﺓ",       "genre/ﺗﺸﻮﻳﻖ-ﻭﺇﺛﺎﺭﺓ/"),
            ("🚀 خيال علمي",          "genre/خيال-علمي/"),
            ("🚀 خيال علمي وفانتازيا", "genre/خيال-علمي-وفانتازيا/"),
            ("🧙 فانتازيا",           "genre/فانتازيا/"),
            ("🧙 خيال",               "genre/خيال/"),
            ("⚔️ حرب",                "genre/حرب/"),
            ("⚔️ حربي",               "genre/حربي/"),
            ("⚔️ حرب وسياسة",         "genre/حرب-وسياسة/"),
            ("⚔️ حروب",               "genre/حروب/"),
            ("🌍 مغامرة",             "genre/مغامرة/"),
            ("🌍 مغامرات",            "genre/مغامرات/"),
            ("🏃 حركة",               "genre/حركة/"),
            ("🏃 حركة ومغامرة",       "genre/حركة-ومغامرة/"),
            ("🎌 انمي",               "genre/انمي/"),
            ("🎠 كرتون",              "genre/كرتون/"),
            ("🎨 انيميشن",            "genre/انيميشن/"),
            ("🎨 رسوم متحركة",        "genre/رسوم-متحركة/"),
            ("📽️ وثائقي",             "genre/وثائقي/"),
            ("😕 غموض",               "genre/غموض/"),
            ("👨‍👩‍👧 عائلي",              "genre/عائلي/"),
            ("🏛️ تاريخي",              "genre/تاريخي/"),
            ("🏛️ تاريخ",               "genre/تاريخ/"),
            ("🎵 موسيقي",              "genre/موسيقي/"),
            ("🎵 موسيقي / استعراضي",  "genre/موسيقي-استعراضي/"),
            ("🎵 موسيقى",              "genre/موسيقى/"),
            ("🎮 مسابقات",             "genre/مسابقات/"),
            ("👮 بوليسي",              "genre/بوليسي/"),
            ("👤 سيرة ذاتية",          "genre/سيرة-ذاتية/"),
            ("💄 اجتماعي",             "genre/اجتماعي/"),
            ("😈 خارق للطبيعة",        "genre/خارق-للطبيعة/"),
            ("💪 قوة خارقة",            "genre/قوة-خارقة/"),
            ("🏆 رياضي",               "genre/رياضي/"),
            ("😂 ساخر",                "genre/ساخر/"),
            ("😢 ميلودراما",           "genre/ميلودراما/"),
            ("🎬 فيلم تلفازي",         "genre/فيلم-تلفازي/"),
            ("🎬 قصير",                "genre/قصير/"),
            ("🕌 ديني",                "genre/ديني/"),
            ("👑 حريم",                "genre/حريم/"),
            ("🎭 كلاسيك",              "genre/كلاسيك/"),
            ("🎬 ايتشي",               "genre/ايتشي/"),
            ("🎬 اتشي",                "genre/اتشي/"),
            ("🎬 ايسيكاي",             "genre/ايسيكاي/"),
            ("🎬 ايدولز",              "genre/ايدولز/"),
            ("🎬 جوسي",                "genre/جوسي/"),
            ("🎬 شونين",               "genre/شونين/"),
            ("🎬 شريحة من الحياة",     "genre/شريحة-من-الحياة/"),
            ("🎬 سنين",                "genre/سنين/"),
            ("🎬 أوبرا صابونية",       "genre/أوبرا-صابونية/"),
            ("🎬 واقعي",               "genre/واقعي/"),
            ("🎬 ويسترن",              "genre/ويسترن/"),
            ("🎬 غربي",                "genre/غربي/"),
            ("🎬 تلفزيون الواقع",      "genre/تلفزيون-الواقع/"),
        ]
        for title, path in genre_ar:
            cats.append({
                "title": title,
                "url": urljoin(base, path),
                "type": "category",
                "_action": "category",
            })

        # ── Genre taxonomy — Latin slugs (site's English spellings) ─
        cats.append({"title": "── Genres (Latin) ──", "url": "", "type": "separator"})
        genre_en = [
            ("Comedy",     "genre/comedy/"),
            ("Romance",    "genre/romance/"),
            ("Music",      "genre/music/"),
            ("Reality-TV", "genre/reality-tv/"),
            ("Game-Show",  "genre/game-show/"),
            ("Life",       "genre/life/"),
            ("Melodrama",  "genre/melodrama/"),
            ("Short",      "genre/short/"),
        ]
        for title, path in genre_en:
            cats.append({
                "title": title,
                "url": urljoin(base, path),
                "type": "category",
                "_action": "category",
            })

        return cats

    def get_category_items(self, url, page=None):
        """Category page → cards + optional next-page link."""
        fetch_url = url
        if page and page > 1:
            parsed = urlparse(fetch_url)
            if "page=" in parsed.query or "paged=" in parsed.query:
                # Replace existing page param
                parts = []
                for part in parsed.query.split("&"):
                    if part.startswith("page=") or part.startswith("paged="):
                        parts.append("page={}/".format(page))
                    else:
                        parts.append(part)
                fetch_url = parsed._replace(query="&".join(parts)).geturl()
            else:
                # WordPress default: /category/xxx/page/N/
                trimmed = fetch_url.rstrip("/")
                if re.search(r"/page/\d+/?$", trimmed):
                    trimmed = re.sub(r"/page/\d+/?$", "", trimmed)
                fetch_url = "{}/page/{}/".format(trimmed, page)

        log("EgyBest: fetching category page: {}".format(fetch_url))
        html, final_url = self._fetch(fetch_url)
        if not html:
            log("EgyBest: get_category_items failed for {}".format(fetch_url))
            return []

        items = self._parse_movie_items(html, final_url or fetch_url)
        open("/tmp/egybest_debug.html", "w", encoding="utf-8").write(html or "")

        # Only add pagination on first page (or when caller didn't pass one)
        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        log("EgyBest: category {} page {} → {} items".format(
            url, page or 1, len(items)))
        return items

    def search(self, query, page=1):
        """WordPress search: /?s=QUERY&paged=N."""
        base = self._get_base().rstrip("/")
        search_url = "{}/?s={}".format(base, quote_plus(query))
        if page > 1:
            search_url += "&paged={}".format(page)

        log("EgyBest: searching: {}".format(search_url))
        html, final_url = self._fetch(search_url)
        if not html:
            return []

        items = self._parse_movie_items(html, final_url or search_url)
        if page == 1:
            nxt = self._parse_pagination(html, search_url)
            if nxt:
                items.append(nxt)
        return items

    # ─── Detail / revealed / embeds / downloads ─────────────────────────
    def _parse_info_box(self, html):
        """EgyBest's postTable carries القسم/النوع/الجودة/اللغة/السنة/المدة."""
        info = {}
        if not html:
            return info
        box_m = re.search(r'<table[^>]*class=["\'][^"\']*postTable[^"\']*["\'][^>]*>'
                          r'(.*?)</table>', html, re.S | re.I)
        if not box_m:
            return info
        box = box_m.group(1)
        labels = {
            "القسم": "category",
            "النوع": "genres",
            "الجودة": "quality",
            "اللغة": "language",
            "البلد": "country",
            "السنة": "year",
            "المدة": "runtime",
            "مدة العرض": "runtime",
            "القناة": "channel",
        }
        for tr in re.finditer(r'<tr[^>]*>(.*?)</tr>', box, re.S | re.I):
            row = tr.group(1)
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S | re.I)
            if len(cells) < 2:
                continue
            label = self._strip_tags(cells[0]).rstrip(":：").strip()
            key = labels.get(label)
            if not key:
                continue
            value = self._strip_tags(cells[1]).strip()
            if not value:
                continue
            if key == "genres":
                parts = [v.strip() for v in re.split(r"[,،]", value) if v.strip()]
                info[key] = ", ".join(parts)
            else:
                info[key] = value
        return info

    def _parse_downloads(self, html):
        """
        Return [{"resolution","size","quality","url"}, ...] for the
        download section.  EgyBest has two variants:
          1. a single .downloadMaster block with <li><span.ser-name>
             and an <a.ser-link href="…">
          2. multiple per-resolution tables whose rows carry
             <a class="btn" href="…"> with the resolution in the row.
        """
        downloads = []
        seen = set()
        if not html:
            return downloads

        def _add(url, resolution="", size="", quality=""):
            url = html_unescape(url.strip())
            if url.startswith("//"):
                url = "https:" + url
            elif not url.startswith("http"):
                url = self._full_url(url)
            if not url or url in seen:
                return
            seen.add(url)
            downloads.append({
                "resolution": resolution,
                "size": size,
                "quality": quality,
                "url": url,
            })

        # Variant 1 — the single downloadMaster block
        block_m = re.search(
            r'<div[^>]*class=["\'][^"\']*(?:downloadMaster|Download--Wecima--Single|List--Download)[^"\']*["\'][^>]*>'
            r'(.*?)</ul>',
            html, re.S | re.I)
        if block_m:
            block = block_m.group(1)
            for li in re.finditer(
                    r'<li[^>]*>(.*?)</li>', block, re.S | re.I):
                inner = li.group(1)
                href_m = re.search(
                    r'<a[^>]*class=["\'][^"\']*(?:ser-link|download-card|btn)[^"\']*["\'][^>]*'
                    r'href=["\']([^"\']+)["\']',
                    inner, re.I) or re.search(
                    r'<a[^>]*href=["\']([^"\']+)["\']', inner, re.I)
                if not href_m:
                    continue
                href = href_m.group(1)
                name_m = re.search(
                    r'<span[^>]*class=["\'][^"\']*ser-name[^"\']*["\'][^>]*>(.*?)</span>',
                    inner, re.S | re.I)
                quality = self._strip_tags(name_m.group(1)).strip() if name_m else ""
                res_m = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b', inner, re.I)
                resolution = res_m.group(1) if res_m else ""
                size_m = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b', inner, re.I)
                size = size_m.group(1) if size_m else ""
                _add(href, resolution, size, quality)

        # Variant 2 — per-resolution tables
        if not downloads:
            for tbl in re.finditer(
                    r'<table[^>]*class=["\'][^"\']*(?:dls_table|download-table)[^"\']*["\'][^>]*>'
                    r'(.*?)</table>', html, re.S | re.I):
                table = tbl.group(1)
                for tr in re.finditer(r'<tr[^>]*>(.*?)</tr>', table, re.S | re.I):
                    row = tr.group(1)
                    cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S | re.I)
                    if not cells:
                        continue
                    a_m = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', row, re.I)
                    if not a_m:
                        continue
                    resolution = ""
                    for c in cells:
                        m = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b',
                                      self._strip_tags(c), re.I)
                        if m:
                            resolution = m.group(1)
                            break
                    size = ""
                    for c in cells:
                        m = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB))\b',
                                      self._strip_tags(c), re.I)
                        if m:
                            size = m.group(1)
                            break
                    _add(a_m.group(1), resolution, size)

        return downloads

    def _extract_watch_servers(self, html, page_url):
        """Parse streaming servers from a revealed/watch page."""
        servers = []
        seen = set()
        if not html:
            return servers

        def _add(url, name="", label=""):
            url = html_unescape(str(url).strip())
            if url.startswith("//"):
                url = "https:" + url
            if not url or url in seen:
                return
            if any(x in url.lower() for x in (
                    "facebook.com", "twitter.com", "instagram.com",
                    "youtube.com/embed", "google.com", "doubleclick",
                    "googletag", "analytics")):
                return
            seen.add(url)
            servers.append({
                "name": name or "سيرفر {}".format(len(servers) + 1),
                "url": url,
                "type": label or "embed",
                "quality": "",
            })

        # [PATCH E1] onclick="loadIframe(this, '...?url=BASE64')" — the real shape,
        # confirmed against a real captured page. The link is base64 inside a url=
        # query parameter on the onclick handler, not in a data-* attribute at all,
        # and "servList" is the parent <ul>'s class, not each <li>'s.
        for li in re.finditer(
                r"<li[^>]*onclick=.loadIframe\(this,\s*'([^']+)'\).[^>]*>(.*?)</li>",
                html, re.S | re.I):
            onclick_url, inner = li.groups()
            name_m = re.search(r'</i>\s*([^\s<][^<]*)', inner)
            name = self._strip_tags(name_m.group(1)).strip() if name_m else ""
            url_m = re.search(r"[?&]url=([^&'\"]+)", onclick_url)
            if url_m:
                payload = unquote(url_m.group(1))
                pad = (-len(payload)) % 4
                try:
                    decoded = base64.b64decode(payload + "=" * pad).decode("utf-8")
                except Exception:
                    decoded = None
                if decoded and (decoded.startswith("http://") or decoded.startswith("https://")):
                    _add(decoded, name)
                    continue
            # not the loadIframe/url= shape -- fall through to the older patterns
            href_m = (re.search(r'data-(?:link|url|iframe|src|server)=["\']([^"\']+)["\']', inner, re.I)
                      or re.search(r'href=["\']([^"\']+)["\']', inner, re.I))
            if href_m:
                _add(href_m.group(1), name)

        # .servList li[data-*] — an older shape, kept as a fallback for other pages
        if not servers:
            for li in re.finditer(
                    r'<li[^>]*class=["\'][^"\']*(?:server--item|servList)[^"\']*["\'][^>]*>(.*?)</li>',
                    html, re.S | re.I):
                inner = li.group(1)
                href_m = (re.search(r'data-(?:link|url|iframe|src|server)=["\']([^"\']+)["\']', inner, re.I)
                          or re.search(r'href=["\']([^"\']+)["\']', inner, re.I))
                if not href_m:
                    continue
                name_m = re.search(r'<span[^>]*>([^<]+)</span>', inner, re.I)
                name = self._strip_tags(name_m.group(1)).strip() if name_m else ""
                _add(href_m.group(1), name)

        # Fallback: any data-* attribute carrying a playable link
        if not servers:
            for attr in ("data-link", "data-url", "data-iframe",
                         "data-src", "data-server", "data-embed"):
                for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                    _add(m.group(1))

        # Fallback: iframes
        if not servers:
            for iframe in extract_iframes(html, page_url):
                _add(iframe)

        # Fallback: direct <source> / <video> tags
        if not servers:
            for m in re.finditer(
                    r'<(?:source|video)[^>]+src=["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                    html, re.I):
                _add(m.group(1), "مشاهدة مباشرة", "direct")

        return servers

    def _find_watch_url(self, html, page_url):
        """Find the watch-page URL from the landing page."""
        m = (re.search(r'<a[^>]+class=["\'][^"\']*\bwatch\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
                       html, re.I)
             or re.search(r'<a[^>]+href=["\']([^"\']+/watch/?)["\']', html, re.I)
             or re.search(r'<a[^>]+href=["\']([^"\']*[?&]watch=1[^"\']*)["\']', html, re.I))
        if m:
            return self._full_url(m.group(1))
        base = page_url.rstrip("/")
        if not base.endswith("/watch"):
            return base + "/watch/"
        return page_url

    def get_page(self, url, m_type=None):
        """Movie landing → revealed → servers + episodes + downloads."""
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
            "downloads": [],
            "type": m_type or "movie",
        }
        if not html:
            log("EgyBest: get_page failed for {}".format(url))
            return result

        # ── Metadata ────────────────────────────────────────────────────
        title = ""
        title_m = (re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S | re.I)
                   or re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I))
        if title_m:
            title = self._clean_title(title_m.group(1))
        if not title:
            tm = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
            if tm:
                title = self._clean_title(tm.group(1).split("|")[0])
        result["title"] = title
        raw_title = title  # before year-strip

        pm = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
              or re.search(r'<div[^>]*class=["\'][^"\']*postImg[^"\']*["\'][^>]*>.*?<img[^>]+src=["\']([^"\']+)["\']', html, re.S | re.I))
        if pm:
            result["poster"] = self._full_url(pm.group(1))
            result["poster"] = re.sub(r"-\d+x\d+(?=\.\w+$)", "", result["poster"])

        plot = ""
        for pat in (
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
                r'<div[^>]*class=["\'][^"\']*(?:postStory|singleStory|story|description)[^"\']*["\'][^>]*>(.*?)</div>'):
            plm = re.search(pat, html, re.S | re.I)
            if plm:
                plot = self._strip_tags(plm.group(1)).strip()
                if plot:
                    break
        result["plot"] = plot

        # Rating (poster overlay)
        rat_m = re.search(r'class=["\'][^"\']*\bpostRating\b[^"\']*["\'][^>]*>.*?<span[^>]*>([\d.]+)',
                          html, re.S | re.I)
        if rat_m:
            result["rating"] = rat_m.group(1).strip()

        # ── Info box (القسم/النوع/الجودة/…) ─────────────────────────────
        info = self._parse_info_box(html)
        for k in ("category", "genres", "quality", "language",
                  "country", "year", "channel", "runtime"):
            if info.get(k):
                result[k] = info[k]

        # Strip a trailing release year from the title
        ym = re.search(r'\b(19\d{2}|20\d{2})\b', result["title"])
        if ym:
            result["year"] = result["year"] or ym.group(1)
            result["title"] = re.sub(r'\s*\b' + ym.group(1) + r'\b\s*', ' ',
                                     result["title"]).strip(" -|")

        # ── Type detection ──────────────────────────────────────────────
        url_low = (final_url or url).lower()
        if "/episode" in url_low or "الحلقة" in raw_title:
            result["type"] = "episode"
        elif "/series" in url_low or "مسلسل" in raw_title:
            result["type"] = "series"
        elif m_type:
            result["type"] = m_type

        # ── Servers (revealed) ──────────────────────────────────────────
        servers = self._extract_watch_servers(html, final_url or url)

        if not servers:
            watch_url = self._find_watch_url(html, final_url or url)
            if watch_url and watch_url != (final_url or url):
                log("EgyBest: no servers on landing, fetching watch page: {}".format(watch_url))
                watch_html, watch_final = self._fetch(
                    watch_url, referer=final_url or url)
                if watch_html:
                    servers = self._extract_watch_servers(
                        watch_html, watch_final or watch_url)
                    if not result["plot"]:
                        pm2 = re.search(
                            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
                            watch_html, re.I)
                        if pm2:
                            result["plot"] = self._strip_tags(pm2.group(1)).strip()
        result["servers"] = servers

        # ── Downloads ───────────────────────────────────────────────────
        result["downloads"] = self._parse_downloads(html)

        # ── Episodes / seasons ──────────────────────────────────────────
        if result["type"] == "series":
            seasons = self._parse_season_list(html)
            episodes = self._parse_episode_list(html)
            result["items"] = seasons or episodes
        elif result["type"] == "season":
            result["items"] = self._parse_episode_list(html)

        log("EgyBest: detail {} → servers={} items={} downloads={}".format(
            url, len(result["servers"]), len(result["items"]),
            len(result["downloads"])))
        return result

    # ─── Stream extraction ──────────────────────────────────────────────
    def _quality_from_url(self, url, default="HD"):
        if not url:
            return default
        low = _correct_stream_url(url).lower()
        for marker, label in (
                ("2160", "2160p"), ("4k", "2160p"),
                ("1080", "1080p"), ("fhd", "1080p"), ("hd1080", "1080p"),
                ("720", "720p"), ("hd720", "720p"),
                ("480", "480p"), ("360", "360p"), ("240", "240p")):
            if marker in low:
                return label
        if "master.m3u8" in low or "playlist" in low:
            return "HD"
        return default

    def extract_stream(self, url, _depth=0):
        """Resolve a server URL to a playable stream."""
        from .base import extract_stream as base_extract_stream
        from .base import get_last_quality_variants, get_synthesized_variants

        def _variants_for(stream):
            v = [(lbl, u) for lbl, u in (get_last_quality_variants() or [])
                 if u != stream]
            if not v:
                v = [(lbl, u) for lbl, u in (get_synthesized_variants(stream) or [])
                     if u != stream]
            return v

        if not url:
            return None, "", self._get_base(), []

        url = _correct_stream_url(url)
        low = url.lower()

        # Direct media URL
        if ".m3u8" in low:
            q = self._quality_from_url(url)
            return url, q, self._get_base(), _variants_for(url)
        if ".mp4" in low:
            return url, self._quality_from_url(url), self._get_base(), []

        # Anything else → delegate to the shared host dispatcher
        if base_extract_stream is not None:
            try:
                result = base_extract_stream(url)
            except Exception as e:
                log("EgyBest: generic resolver failed for {}: {}".format(url[:80], e))
                return None, "", self._get_base(), []
            if isinstance(result, tuple):
                if len(result) >= 3:
                    stream = result[0]
                    quality = result[1] or ""
                    referer = result[2] or self._get_base()
                    raw_variants = list(result[3]) if len(result) > 3 and result[3] else []
                    variants = [(lbl, u) for (lbl, u) in raw_variants
                                if isinstance((lbl, u), tuple) and u != stream]
                    return stream, quality, referer, variants
                if len(result) == 2:
                    return result[0], result[1] or "", self._get_base(), []
                if len(result) == 1:
                    return result[0], "", self._get_base(), []
            elif isinstance(result, str) and result:
                return result, "", self._get_base(), []
        return None, "", self._get_base(), []