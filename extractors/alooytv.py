# -*- coding: utf-8 -*-
"""
AlooyTV extractor — WordPress site

Canonical base: https://alooytv.co/ (a WordPress 6.x site running the
AlooyTV/Tajawal theme — cards use `pm-card` class on category pages,
`movie__block` on some carousel templates; both share the same inner
structure).

Sequence followed (mirrors egydead.py):

    homepage      → get_categories(mtype) — the site's own top nav
                    plus every filter taxonomy (genre × quality × year)
    category      → get_category_items(url, page) — parses pm-card /
                    movie__block cards, follows /page/N/ pagination
    movie landing → get_page(url) — og:title / og:image / meta desc,
                    plus site-specific info fields
    revealed      → same get_page() — hits /watch/ or appends ?watch=1
                    when the server list is rendered lazily
    embeds        → .servList li / .single_servers / iframes / data-*
    downloads     → downloadMaster / dls_table / data-href link pairs

Fixes applied in this revision:
  * Multi-domain probing with a deep-content probe.  Every category
    page carries `.pm-card` markers; a landing page does not.
  * _full_url() percent-encodes non-ASCII bytes for ABSOLUTE URLs too —
    AlooyTV's categories are Arabic (‎/category/افلام-اجنبي/‎), and the
    HTTP client used by the plugin cannot put raw non-ASCII bytes in
    a request line.
  * Cards are parsed defensively: `pm-card`, `movie__block`, plain
    `<article class="post">`, and bare `<a>+<img>`+<h3> — covers the
    3 markup shapes actually used across the site's templates.
  * Pagination handles both `/page/N/` and `?paged=N`.
  * Playable servers try, in order: `.servList li[data-*]`,
    `.single_servers` blocks, any `data-link`/`data-url`/`data-server`
    attribute, `<iframe src>`, then `<source>`/`<video>`.
  * The quality/genre/year filter dropdowns are exposed as category
    items (a curated recent-years list keeps the menu navigable on a
    remote).
"""

import re
import time
import threading

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


class AlooyTvExtractor(BaseExtractor):
    """Extractor for AlooyTV (alooytv.co and any mirror)."""

    MAIN_URL = "https://alooytv.co/"

    DOMAINS = [
        "https://alooytv.co/",
        "https://www.alooytv.co/",
        "https://alooytv.tv/",
        "https://alooytv.net/",
    ]

    VALID_HOST_MARKERS = ("alooytv",)
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com", "watch-it-legally")

    CLEAN_WORDS = [
        "مشاهدة", "تحميل", "فيلم", "مسلسل", "الحلقة", "حلقة",
        "مترجم", "مترجمة", "مدبلج", "مدبلجة",
        "اون لاين", "أون لاين", "اونلاين", "اون لاين",
        "بجودة", "عالية", "كامل", "حصريا",
        "والاخيرة", "والأخيرة", "الاخيرة", "الأخيرة",
    ]

    def __init__(self):
        super(AlooyTvExtractor, self).__init__()
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

    def _looks_like_alooytv_page(self, html):
        text = html or ""
        return (
            "Alooy" in text
            or "alooytv" in text.lower()
            or "الوي تي في" in text
            or "pm-card" in text
            or "movie__block" in text
            or "arc-wrap" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        """Confirm the candidate actually serves listing pages, not just a
        homepage.  AlooyTV's /home/ is a landing-only page with no cards;
        real cards live under /category/*.  Some WordPress mirrors
        respond 200 to `/` but return the same landing page for every
        deep URL — accepting them makes every category collapse to the
        same empty items."""
        if not base:
            return False
        test_url = urljoin(base, "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("AlooyTV: deep-content probe failed for {}: {}".format(base, e))
            return False
        if not html:
            return False
        if self._is_blocked_page(html, final or ""):
            return False
        n_cards = html.count("pm-card") + html.count("movie__block")
        if n_cards < 4:
            log("AlooyTV: {} responded but only {} card markers on "
                "/category/... — likely a landing, not a content mirror".format(
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
                log("AlooyTV: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain,
                                        extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain

                if not self._is_valid_site_url(final_url):
                    log("AlooyTV: unexpected host after redirect {}".format(final_url))
                    continue
                if self._is_blocked_page(html, final_url):
                    log("AlooyTV: blocked {}".format(final_url))
                    continue
                if not (html and self._looks_like_alooytv_page(html)):
                    log("AlooyTV: page shape mismatch on {}".format(final_url))
                    continue

                candidate = self._site_root(final_url)
                if not self._base_serves_deep_content(candidate):
                    log("AlooyTV: {} is not a content mirror — trying next".format(candidate))
                    continue

                self._resolved_base = candidate
                self.main_url = candidate
                with _base_cache_lock:
                    _base_cache["url"] = candidate
                    _base_cache["resolved_at"] = now
                    _base_cache["probed_at"] = now
                log("AlooyTV: selected base {}".format(candidate))
                return candidate

            self._resolved_base = self.MAIN_URL
            self.main_url = self._resolved_base
            with _base_cache_lock:
                _base_cache["url"] = self._resolved_base
                _base_cache["probed_at"] = now
            log("AlooyTV: all probes failed, falling back to {}".format(self._resolved_base))
            return self._resolved_base

    # ─── URL / title helpers ────────────────────────────────────────────
    def _clean_title(self, title):
        title = self._strip_tags(title)
        title = html_unescape(title or "")
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
        AlooyTV's categories are Arabic (‎/category/افلام-اجنبي/‎), and
        the HTTP client used by the plugin cannot put raw non-ASCII
        bytes in a request line.
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
    def _parse_movie_items(self, html, current_url=None):
        """
        Extract movie/series cards from a listing page.

        AlooyTV uses three markup shapes across its templates:
          1. `a.pm-card` — the category-page template, present in every
             capture (Arabic categories, filters, pagination).
          2. `a.movie__block` — the homepage/carousel template.
          3. plain `<article class="post">` / bare link+img+h3 — rare
             fallback for WordPress default pages.
        All three share: href on the wrapper, poster <img>, and a
        title inside an <h3>/<h2>/<img alt>.
        """
        items = []
        seen = set()

        # Strategy 1+2: pm-card and movie__block — split on either
        blocks = re.split(
            r'(?=<a[^>]+class="[^"]*(?:pm-card|movie__block))',
            html or "")

        for raw in blocks[1:]:
            end = raw.find("</a>")
            if end < 0:
                continue
            block = raw[:end + 4]

            href_m = re.search(
                r'<a[^>]+class=["\'][^"\']*(?:pm-card|movie__block)[^"\']*["\'][^>]*'
                r'href=["\']([^"\']+)["\']', block, re.I)
            if not href_m:
                continue

            url = self._full_url(href_m.group(1))
            if not url or url in seen:
                continue
            low = url.lower()
            if any(x in low for x in ("/category/", "/genre/", "/tag/",
                                      "/page/", "page=", "#",
                                      "facebook.com", "twitter.com")):
                continue
            seen.add(url)

            title = ""
            # pm-card uses <div class="pm-title">; movie__block uses
            # <div class="title"> or <h3 class="title">
            for pat in (
                    r'<div[^>]*class=["\'][^"\']*\bpm-title\b[^"\']*["\'][^>]*>(.*?)</div>',
                    r'<(?:h[1-4])[^>]*class=["\'][^"\']*\btitle\b[^"\']*["\'][^>]*>(.*?)</(?:h[1-4])>',
                    r'<div[^>]*class=["\'][^"\']*\btitle\b[^"\']*["\'][^>]*>(.*?)</div>'):
                tm = re.search(pat, block, re.S | re.I)
                if tm:
                    title = self._clean_title(tm.group(1))
                    if title:
                        break
            if not title:
                # Fallback: the card's own title attr, or the img alt
                tm = (re.search(r'<a[^>]+title=["\']([^"\']+)["\']', block, re.I)
                      or re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', block, re.I))
                if tm:
                    title = self._clean_title(tm.group(1))
            if not title:
                continue

            # Poster: try lazy attrs, then src.  Skip placeholders.
            poster = ""
            for pat in (
                    r'<img[^>]+class=["\'][^"\']*\bpm-img\b[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                    r'<img[^>]+data-src=["\']([^"\']+)["\']',
                    r'<img[^>]+data-lazy-src=["\']([^"\']+)["\']',
                    r'<img[^>]+src=["\']([^"\']+)["\']'):
                pm = re.search(pat, block, re.I)
                if pm:
                    cand = pm.group(1).strip()
                    if any(x in cand.lower() for x in (
                            "src-default", "placeholder", "lazy_load",
                            "loading.gif", "data:image", "logo", "no-img")):
                        continue
                    poster = self._full_url(cand)
                    break

            # Quality badge: `pm-quality` on pm-card, `__quality` on movie__block
            quality = ""
            qm = (re.search(r'<span[^>]*class=["\'][^"\']*\bpm-quality\b[^"\']*["\'][^>]*>(.*?)</span>', block, re.S | re.I)
                  or re.search(r'<span[^>]*class=["\'][^"\']*\b__quality\b[^"\']*["\'][^>]*>(.*?)</span>', block, re.S | re.I))
            if qm:
                quality = self._strip_tags(qm.group(1)).strip()

            # Year: `pm-year` (with a star), or a bare 20xx in the title
            year = ""
            ym = re.search(r'<span[^>]*class=["\'][^"\']*\bpm-year\b[^"\']*["\'][^>]*>.*?(\d{4})',
                           block, re.S | re.I)
            if ym:
                year = ym.group(1)
            if not year:
                ym2 = re.search(r'\b(19\d{2}|20\d{2})\b', title)
                if ym2:
                    year = ym2.group(1)

            # Status badge: `pm-ep` → "جديد" (new) or similar
            label = ""
            lm = re.search(r'<span[^>]*class=["\'][^"\']*\bpm-ep\b[^"\']*["\'][^>]*>(.*?)</span>',
                           block, re.S | re.I)
            if lm:
                label = self._strip_tags(lm.group(1)).strip()

            # Category name (badge on hover) — useful as plot teaser
            plot = ""
            cm = re.search(r'<span[^>]*class=["\'][^"\']*\bpm-categ\b[^"\']*["\'][^>]*>(.*?)</span>',
                           block, re.S | re.I)
            if cm:
                plot = self._strip_tags(cm.group(1)).strip()
            if not plot:
                pm2 = re.search(r'<div[^>]*class=["\'][^"\']*\bpm-desc\b[^"\']*["\'][^>]*>(.*?)</div>',
                                block, re.S | re.I)
                if pm2:
                    plot = self._strip_tags(pm2.group(1)).strip()

            # Strip a trailing year from the title so the "title" field
            # is clean (year moves to its own field)
            if year:
                title = re.sub(r'\s*\b' + year + r'\b\s*', ' ', title)
                title = re.sub(r'\s{2,}', ' ', title).strip(' -|')

            url_low = url.lower()
            raw_title = block  # for "مسلسل" detection keep the raw block
            if ("الحلقة" in raw_title or "حلقة" in raw_title or
                    "/episode" in url_low):
                item_type = "episode"
            elif ("مسلسل" in raw_title or "انمي" in raw_title or
                    "/series" in url_low):
                item_type = "series"
            else:
                item_type = "movie"

            items.append({
                "title": title,
                "url": url,
                "poster": poster or "",
                "plot": plot or "",
                "label": label,
                "quality": quality,
                "year": year,
                "type": item_type,
                "_action": "details",
            })

        # Strategy 3: fallback — plain article / bare <a>+<img>+<h3>
        if not items:
            for m in re.finditer(
                    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    html or "", re.S | re.I):
                url = self._full_url(m.group(1))
                if not url or url in seen:
                    continue
                low = url.lower()
                if any(x in low for x in ("/category/", "/genre/", "/tag/",
                                          "/page/", "page=", "#",
                                          "facebook.com", "twitter.com")):
                    continue
                inner = m.group(2)
                img_m = re.search(r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']', inner, re.I)
                title_m = (re.search(r'<h[1-4][^>]*>(.*?)</h[1-4]>', inner, re.S | re.I)
                           or re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', inner, re.I))
                if not (img_m and title_m):
                    continue
                poster = img_m.group(1).strip()
                if any(x in poster.lower() for x in (
                        "src-default", "placeholder", "lazy_load",
                        "loading.gif", "data:image", "logo")):
                    poster = ""
                title = self._clean_title(title_m.group(1))
                if not title or len(title) < 2:
                    continue
                seen.add(url)
                items.append({
                    "title": title,
                    "url": url,
                    "poster": self._full_url(poster) if poster else "",
                    "plot": "",
                    "label": "",
                    "quality": "",
                    "year": "",
                    "type": "movie",
                    "_action": "details",
                })
        return items

    def _parse_episode_list(self, html):
        """Parse an episode list from a series landing page."""
        items = []
        seen = set()
        # The theme renders episodes inside .EpisodesList / .all-episodes
        for scope_m in re.finditer(
                r'<(?:div|ul)[^>]*class=["\'][^"\']*(?:EpisodesList|all-episodes|episodes-list)[^"\']*["\'][^>]*>(.*?)</(?:div|ul)>',
                html or "", re.S | re.I):
            scope = scope_m.group(1)
            for m in re.finditer(
                    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    scope, re.S | re.I):
                url = self._full_url(m.group(1))
                if not url or url in seen:
                    continue
                text = self._strip_tags(m.group(2)).strip()
                if not text:
                    text = "حلقة"
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
        """WordPress pagination → returns the next-page item or None."""
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
            # AlooyTV's own pagination: <a class="arc-page-btn">›</a>
            m = re.search(r'<a[^>]+class=["\'][^"\']*\barc-page-btn\b[^"\']*["\'][^>]+'
                          r'href=["\']([^"\']+)["\'][^>]*>\s*›\s*</a>',
                          html or "", re.I | re.S)
        if not m:
            m = re.search(r'<a[^>]+href=["\']([^"\']+/page/\d+/?)["\']', html or "", re.I)
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
        """Homepage → the site's own top-level navigation plus every
        filter taxonomy (quality × genre × year)."""
        base = self._get_base()

        cats = []

        # ── Landing pages ────────────────────────────────────────────
        cats.append({"title": "🏠 الرئيسية",       "url": urljoin(base, "home/"),  "type": "category", "_action": "category"})
        cats.append({"title": "🆕 المضاف حديثًا",   "url": urljoin(base, "last/"),  "type": "category", "_action": "category"})

        # ── Movies ───────────────────────────────────────────────────
        cats.append({"title": "── أفلام ──", "url": "", "type": "separator"})
        movies = [
            ("🎬 كل الأفلام",   "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/"),
            ("🌍 أفلام أجنبي",  "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/"),
            ("🇪🇬 أفلام عربي",   "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%b9%d8%b1%d8%a8%d9%8a/"),
            ("🇮🇳 أفلام هندي",   "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d9%87%d9%86%d8%af%d9%8a/"),
            ("🇹🇷 أفلام تركية",  "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%aa%d8%b1%d9%83%d9%8a%d8%a9/"),
            ("🎌 أفلام انمي",   "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d9%86%d9%85%d9%8a/"),
            ("🌏 أفلام اسيوية", "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a%d8%a9/"),
        ]
        for title, path in movies:
            cats.append({"title": title, "url": urljoin(base, path),
                         "type": "category", "_action": "category"})

        # ── Series ───────────────────────────────────────────────────
        cats.append({"title": "── مسلسلات ──", "url": "", "type": "separator"})
        series = [
            ("📺 مسلسلات اجنبي",   "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/"),
            ("📺 مسلسلات عربي",    "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b9%d8%b1%d8%a8%d9%8a/"),
            ("📺 مسلسلات اسيوية",  "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a%d8%a9/"),
            ("📺 مسلسلات هندية",   "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d9%87%d9%86%d8%af%d9%8a%d8%a9/"),
            ("📺 مسلسلات انمي",    "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d9%86%d9%85%d9%8a/"),
            ("📺 مسلسلات مدبلجة",  "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d9%85%d8%af%d8%a8%d9%84%d8%ac%d8%a9/"),
            ("📺 مسلسلات تركية",   "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%aa%d8%b1%d9%83%d9%8a%d8%a9/"),
        ]
        for title, path in series:
            cats.append({"title": title, "url": urljoin(base, path),
                         "type": "category", "_action": "category"})

        # ── Ramadan ──────────────────────────────────────────────────
        cats.append({"title": "── مسلسلات رمضان ──", "url": "", "type": "separator"})
        ramadan = [
            ("🌙 رمضان 2026", "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2026/"),
            ("🌙 رمضان 2025", "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2025/"),
            ("🌙 رمضان 2024", "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2024/"),
            ("🌙 رمضان 2023", "category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2023/"),
            ("🌙 رمضان 2022", "category/%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2022/"),
        ]
        for title, path in ramadan:
            cats.append({"title": title, "url": urljoin(base, path),
                         "type": "category", "_action": "category"})

        # ── Other ────────────────────────────────────────────────────
        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        cats.append({"title": "📡 برامج تلفزيونية",
                     "url": urljoin(base, "category/%d8%a8%d8%b1%d8%a7%d9%85%d8%ac-%d8%aa%d9%84%d9%81%d8%b2%d9%8a%d9%88%d9%86%d9%8a%d8%a9/"),
                     "type": "category", "_action": "category"})
        cats.append({"title": "🤼 عروض مصارعة",
                     "url": urljoin(base, "category/%d8%b9%d8%b1%d9%88%d8%b6-%d9%85%d8%b5%d8%a7%d8%b1%d8%b9%d8%a9/"),
                     "type": "category", "_action": "category"})

        # ── Genres (filter URLs) ─────────────────────────────────────
        cats.append({"title": "── تصنيفات حسب النوع ──", "url": "", "type": "separator"})
        genres = [
            ("🎭 أكشن",       "أكشن"),
            ("😂 كوميدي",     "كوميدي"),
            ("🎭 دراما",       "دراما"),
            ("👻 رعب",         "رعب"),
            ("💕 رومانسي",    "رومانسي"),
            ("🔪 جريمة",       "جريمة"),
            ("😱 تشويق",       "تشويق-واثارة"),
            ("🚀 خيال علمي",   "خيال-علمي"),
            ("🧙 فانتازيا",   "فانتازيا"),
            ("⚔️ حروب",        "حروب"),
            ("🌍 مغامرات",     "مغامرات"),
            ("🎌 انمي",        "انمي"),
            ("🎠 كرتون",       "كرتون"),
            ("📽️ وثائقي",      "وثائقي"),
            ("😕 غموض",        "غموض"),
            ("👨‍👩‍👧 عائلي",        "عائلي"),
            ("🏛️ تاريخي",      "تاريخي"),
            ("🎵 موسيقى",      "موسيقى"),
            ("👮 بوليسي",      "بوليسي"),
            ("🏃 حركة",        "حركة"),
            ("🎮 مسابقات",    "مسابقات"),
            ("🕵️ سيرة ذاتية",  "سيرة-ذاتية"),
            ("🎬 قصير",        "قصير"),
        ]
        for title, slug in genres:
            cats.append({
                "title": title,
                "url": urljoin(base, "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/?filter-genre=" + slug),
                "type": "category",
                "_action": "category",
            })

        # ── Years (filter URLs, curated recent) ─────────────────────
        cats.append({"title": "── حسب السنة ──", "url": "", "type": "separator"})
        years = ["2026", "2025", "2024", "2023", "2022", "2021", "2020",
                 "2019", "2018", "2017", "2016", "2015", "2014", "2013",
                 "2012", "2011", "2010"]
        for yr in years:
            cats.append({
                "title": "📅 {}".format(yr),
                "url": urljoin(base, "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/?filter-year=" + yr),
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
                parts = []
                for part in parsed.query.split("&"):
                    if part.startswith("page=") or part.startswith("paged="):
                        parts.append("paged={}".format(page))
                    else:
                        parts.append(part)
                fetch_url = parsed._replace(query="&".join(parts)).geturl()
            else:
                # WordPress default: /category/xxx/page/N/
                trimmed = fetch_url.rstrip("/")
                if re.search(r"/page/\d+/?$", trimmed):
                    trimmed = re.sub(r"/page/\d+/?$", "", trimmed)
                fetch_url = "{}/page/{}/".format(trimmed, page)

        log("AlooyTV: fetching category page: {}".format(fetch_url))
        html, final_url = self._fetch(fetch_url)
        if not html:
            log("AlooyTV: get_category_items failed for {}".format(fetch_url))
            return []

        items = self._parse_movie_items(html, final_url or fetch_url)

        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        log("AlooyTV: category {} page {} → {} items".format(
            url, page or 1, len(items)))
        return items

    def search(self, query, page=1):
        """WordPress search: /?s=QUERY&paged=N."""
        base = self._get_base().rstrip("/")
        search_url = "{}/?s={}".format(base, quote_plus(query))
        if page > 1:
            search_url += "&paged={}".format(page)

        log("AlooyTV: searching: {}".format(search_url))
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
        """Pull labelled metadata out of the single-post layout.  AlooyTV
        uses <dl class="dl-horizontal"> on detail pages, plus Yoast
        meta tags we already read in get_page."""
        info = {}
        if not html:
            return info
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
        # <dt>Label</dt><dd>Value</dd>
        for m in re.finditer(r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>',
                             html, re.S | re.I):
            key_raw = self._strip_tags(m.group(1)).rstrip(":：").strip()
            key = labels.get(key_raw)
            if not key:
                continue
            value = self._strip_tags(m.group(2)).strip()
            if value:
                info[key] = value
        return info

    def _parse_downloads(self, html):
        """
        Return [{"resolution","size","quality","url"}, ...] for the
        download section.  The theme ships two shapes:
          1. a `.downloadMaster` block with <span.ser-name> and
             an <a.ser-link href="…">
          2. a `dls_table` whose rows carry an <a href="…"> with the
             resolution inside the row.
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

        # Variant 1 — downloadMaster
        block_m = re.search(
            r'<div[^>]*class=["\'][^"\']*(?:downloadMaster|List--Download)[^"\']*["\'][^>]*>'
            r'(.*?)</ul>',
            html, re.S | re.I)
        if block_m:
            for li in re.finditer(r'<li[^>]*>(.*?)</li>', block_m.group(1), re.S | re.I):
                inner = li.group(1)
                href_m = (re.search(r'<a[^>]*class=["\'][^"\']*(?:ser-link|download-card)[^"\']*["\'][^>]*href=["\']([^"\']+)["\']', inner, re.I)
                          or re.search(r'<a[^>]*href=["\']([^"\']+)["\']', inner, re.I))
                if not href_m:
                    continue
                name_m = re.search(r'<span[^>]*class=["\'][^"\']*ser-name[^"\']*["\'][^>]*>(.*?)</span>',
                                   inner, re.S | re.I)
                quality = self._strip_tags(name_m.group(1)).strip() if name_m else ""
                res_m = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b', inner, re.I)
                resolution = res_m.group(1) if res_m else ""
                size_m = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b', inner, re.I)
                size = size_m.group(1) if size_m else ""
                _add(href_m.group(1), resolution, size, quality)

        # Variant 2 — dls_table
        if not downloads:
            for tbl in re.finditer(
                    r'<table[^>]*class=["\'][^"\']*\bdls_table\b[^"\']*["\'][^>]*>(.*?)</table>',
                    html, re.S | re.I):
                for tr in re.finditer(r'<tr[^>]*>(.*?)</tr>', tbl.group(1), re.S | re.I):
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

        def _add(url, name="", kind="embed"):
            url = html_unescape(str(url).strip())
            if url.startswith("//"):
                url = "https:" + url
            if not url or url in seen:
                return
            if any(x in url.lower() for x in (
                    "facebook.com", "twitter.com", "instagram.com",
                    "youtube.com/embed", "google.com", "doubleclick",
                    "googletag", "analytics", "cloudflareinsights")):
                return
            seen.add(url)
            servers.append({
                "name": name or "سيرفر {}".format(len(servers) + 1),
                "url": url,
                "type": kind,
                "quality": "",
            })

        # Primary: `.servList li` — data attributes or an <a> inside
        for li in re.finditer(
                r'<li[^>]*class=["\'][^"\']*\bservList\b[^"\']*["\'][^>]*>(.*?)</li>',
                html, re.S | re.I):
            inner = li.group(1)
            href_m = (re.search(r'data-(?:link|url|iframe|server|src)=["\']([^"\']+)["\']', inner, re.I)
                      or re.search(r'<a[^>]+href=["\']([^"\']+)["\']', inner, re.I))
            if not href_m:
                continue
            name_m = re.search(r'>([^<>]{2,})<', inner)
            name = self._strip_tags(name_m.group(1)).strip() if name_m else ""
            _add(href_m.group(1), name)

        # Secondary: `.single_servers` block with a data-server attribute
        if not servers:
            for m in re.finditer(
                    r'<[^>]*class=["\'][^"\']*\bsingle_servers\b[^"\']*["\'][^>]*'
                    r'(?:data-(?:server|url|link|src)=["\']([^"\']+)["\'])?',
                    html, re.I):
                if m.group(1):
                    _add(m.group(1), "سيرفر")

        # Tertiary: any data-* attribute carrying a playable URL
        if not servers:
            for attr in ("data-link", "data-url", "data-iframe",
                         "data-src", "data-server", "data-embed"):
                for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                    _add(m.group(1))

        # Quaternary: iframes
        if not servers:
            for iframe in extract_iframes(html, page_url):
                _add(iframe)

        # Last resort: <source>/<video> tags
        if not servers:
            for m in re.finditer(
                    r'<(?:source|video)[^>]+src=["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                    html, re.I):
                _add(m.group(1), "مشاهدة مباشرة", "direct")

        return servers

    def _find_watch_url(self, html, page_url):
        """Find the watch URL from the landing page."""
        # 1. Explicit link to /watch/ or ?watch=1
        m = (re.search(r'<a[^>]+class=["\'][^"\']*\bwatch\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
             or re.search(r'<a[^>]+href=["\']([^"\']+/watch/?)["\']', html, re.I)
             or re.search(r'<a[^>]+href=["\']([^"\']*[?&]watch=1[^"\']*)["\']', html, re.I))
        if m:
            return self._full_url(m.group(1))
        # 2. The theme's own "شاهد الآن" button often points to #watch or
        #    the post itself with ?watch=1 — fall back to appending it.
        base = page_url.split("?")[0].rstrip("/")
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
            log("AlooyTV: get_page failed for {}".format(url))
            return result

        # ── Metadata ────────────────────────────────────────────────────
        title = ""
        # Yoast's og:title is the cleanest
        tm = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if tm:
            title = self._clean_title(tm.group(1))
        if not title:
            tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S | re.I)
            if tm:
                title = self._clean_title(tm.group(1))
        if not title:
            tm = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
            if tm:
                title = self._clean_title(tm.group(1).split("|")[0])
        result["title"] = title
        raw_title = title

        pm = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if pm:
            poster = pm.group(1).strip()
            # Normalize the WordPress size suffix, e.g. 336x600
            poster = re.sub(r"-\d+x\d+(?=\.\w+$)", "", poster)
            result["poster"] = self._full_url(poster)

        plot = ""
        for pat in (
                r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
                r'<div[^>]*class=["\'][^"\']*\bstory\b[^"\']*["\'][^>]*>(.*?)</div>'):
            plm = re.search(pat, html, re.S | re.I)
            if plm:
                plot = self._strip_tags(plm.group(1)).strip()
                if plot:
                    break
        result["plot"] = plot

        # Rating — theme has `.postRating` from egydead-style templates,
        # but AlooyTV primary template doesn't show a rating; skip if absent.
        rat_m = re.search(r'class=["\'][^"\']*\bpostRating\b[^"\']*["\'][^>]*>.*?<span[^>]*>([\d.]+)',
                          html, re.S | re.I)
        if rat_m:
            result["rating"] = rat_m.group(1).strip()

        # ── Info box ────────────────────────────────────────────────────
        info = self._parse_info_box(html)
        for k in ("category", "genres", "quality", "language",
                  "country", "year", "channel", "runtime"):
            if info.get(k):
                result[k] = info[k]

        # Trailing year from title
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
                log("AlooyTV: no servers on landing, fetching watch page: {}".format(watch_url))
                watch_html, watch_final = self._fetch(watch_url, referer=final_url or url)
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

        log("AlooyTV: detail {} → servers={} items={} downloads={}".format(
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
                log("AlooyTV: generic resolver failed for {}: {}".format(url[:80], e))
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