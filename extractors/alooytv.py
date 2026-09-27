# -*- coding: utf-8 -*-
"""
AlooyTV extractor — WordPress site (fixed for OpenATV 7)

Key fixes in this revision:
  * Server extraction now uses the site's AJAX endpoint
    (wp-content/themes/timemovies/ajax.php) with POST_ID and server
    index, returning JSON with server names and iframe HTML.
  * Stream resolution follows the govid.live iframe chain:
    /play/=...  →  /e/{id}/  →  JWPlayer HLS source (hex-encoded Mohix)
  * Detail page parsing updated for sng-* CSS classes (sng-detail-row,
    sng-tags, sng-subtitle, sng-btn-sec).
  * Download link extraction from sng-btn-sec (govid.live/d/{id}/).
  * Card parsing unchanged — pm-card still used on category/related pages.
  * Playback headers fixed to bypass Cloudflare on govid.live streams.
"""

import re
import json
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
_BASE_CACHE_TTL = 300
_PROBE_COOLDOWN = 30
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
            or "sng-hero" in text
            or "arc-wrap" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
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
        items = []
        seen = set()

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
                tm = (re.search(r'<a[^>]+title=["\']([^"\']+)["\']', block, re.I)
                      or re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', block, re.I))
                if tm:
                    title = self._clean_title(tm.group(1))
            if not title:
                continue

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

            quality = ""
            qm = (re.search(r'<span[^>]*class=["\'][^"\']*\bpm-quality\b[^"\']*["\'][^>]*>(.*?)</span>', block, re.S | re.I)
                  or re.search(r'<span[^>]*class=["\'][^"\']*\b__quality\b[^"\']*["\'][^>]*>(.*?)</span>', block, re.S | re.I))
            if qm:
                quality = self._strip_tags(qm.group(1)).strip()

            year = ""
            ym = re.search(r'<span[^>]*class=["\'][^"\']*\bpm-year\b[^"\']*["\'][^>]*>.*?(\d{4})',
                           block, re.S | re.I)
            if ym:
                year = ym.group(1)
            if not year:
                ym2 = re.search(r'\b(19\d{2}|20\d{2})\b', title)
                if ym2:
                    year = ym2.group(1)

            label = ""
            lm = re.search(r'<span[^>]*class=["\'][^"\']*\bpm-ep\b[^"\']*["\'][^>]*>(.*?)</span>',
                           block, re.S | re.I)
            if lm:
                label = self._strip_tags(lm.group(1)).strip()

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

            if year:
                title = re.sub(r'\s*\b' + year + r'\b\s*', ' ', title)
                title = re.sub(r'\s{2,}', ' ', title).strip(' -|')

            url_low = url.lower()
            raw_title = block
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

        # Fallback: plain article / bare <a>+<img>+<h3>
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
        items = []
        seen = set()
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

    # ─── Public API: categories ─────────────────────────────────────────
    def get_categories(self, mtype="movie"):
        base = self._get_base()
        cats = []

        cats.append({"title": "🏠 الرئيسية",       "url": urljoin(base, "home/"),  "type": "category", "_action": "category"})
        cats.append({"title": "🆕 المضاف حديثًا",   "url": urljoin(base, "last/"),  "type": "category", "_action": "category"})

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

        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        cats.append({"title": "📡 برامج تلفزيونية",
                     "url": urljoin(base, "category/%d8%a8%d8%b1%d8%a7%d9%85%d8%ac-%d8%aa%d9%84%d9%81%d8%b2%d9%8a%d9%88%d9%86%d9%8a%d8%a9/"),
                     "type": "category", "_action": "category"})
        cats.append({"title": "🤼 عروض مصارعة",
                     "url": urljoin(base, "category/%d8%b9%d8%b1%d9%88%d8%b6-%d9%85%d8%b5%d8%a7%d8%b1%d8%b9%d8%a9/"),
                     "type": "category", "_action": "category"})

        cats.append({"title": "── تصنيفات حسب النوع ──", "url": "", "type": "separator"})
        genres = [
            ("🎭 أكشن",       "أكشن"),     ("😂 كوميدي",     "كوميدي"),
            ("🎭 دراما",       "دراما"),    ("👻 رعب",         "رعب"),
            ("💕 رومانسي",    "رومانسي"),  ("🔪 جريمة",       "جريمة"),
            ("😱 تشويق",       "تشويق-واثارة"), ("🚀 خيال علمي", "خيال-علمي"),
            ("🧙 فانتازيا",   "فانتازيا"),  ("⚔️ حروب",        "حروب"),
            ("🌍 مغامرات",     "مغامرات"),   ("🎌 انمي",        "انمي"),
            ("🎠 كرتون",       "كرتون"),    ("📽️ وثائقي",      "وثائقي"),
            ("😕 غموض",        "غموض"),    ("👨‍👩‍👧 عائلي",        "عائلي"),
            ("🏛️ تاريخي",      "تاريخي"),   ("🎵 موسيقى",      "موسيقى"),
            ("👮 بوليسي",      "بوليسي"),   ("🏃 حركة",        "حركة"),
            ("🎮 مسابقات",    "مسابقات"),  ("🕵️ سيرة ذاتية",  "سيرة-ذاتية"),
            ("🎬 قصير",        "قصير"),
        ]
        for title, slug in genres:
            cats.append({
                "title": title,
                "url": urljoin(base, "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/?filter-genre=" + slug),
                "type": "category", "_action": "category",
            })

        cats.append({"title": "── حسب السنة ──", "url": "", "type": "separator"})
        years = ["2026", "2025", "2024", "2023", "2022", "2021", "2020",
                 "2019", "2018", "2017", "2016", "2015", "2014", "2013",
                 "2012", "2011", "2010"]
        for yr in years:
            cats.append({
                "title": "📅 {}".format(yr),
                "url": urljoin(base, "category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/?filter-year=" + yr),
                "type": "category", "_action": "category",
            })

        return cats

    def get_category_items(self, url, page=None):
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

    # ─── AJAX server extraction (NEW) ──────────────────────────────────
    def _extract_post_id(self, html):
        m = re.search(r'var\s+POST_ID\s*=\s*(\d+)', html or "")
        return m.group(1) if m else None

    def _extract_ajax_url(self, html):
        m = re.search(r'var\s+AJAX_URL\s*=\s*["\']([^"\']+)["\']', html or "")
        if m:
            return self._full_url(m.group(1))
        return urljoin(self._get_base(), 'wp-content/themes/timemovies/ajax.php')

    def _extract_watch_servers(self, html, page_url):
        servers = []
        post_id = self._extract_post_id(html)
        if not post_id:
            log("AlooyTV: POST_ID not found on page, falling back to legacy")
            return self._extract_watch_servers_legacy(html, page_url)

        ajax_url = self._extract_ajax_url(html)
        log("AlooyTV: POST_ID={} AJAX_URL={}".format(post_id, ajax_url))

        post_data = 'post_id={}&server=0'.format(post_id)
        ajax_html, _ = self._fetch(ajax_url, referer=page_url, post_data=post_data)

        try:
            if isinstance(ajax_html, bytes):
                ajax_html = ajax_html.decode('utf-8', errors='ignore')
            data = json.loads(ajax_html)
        except (ValueError, TypeError) as e:
            log("AlooyTV: AJAX JSON parse failed: {}".format(e))
            return self._extract_watch_servers_legacy(html, page_url)

        if not data.get('success'):
            log("AlooyTV: AJAX returned success=false")
            return servers

        server_names = data.get('servers', [])
        log("AlooyTV: AJAX returned {} servers: {}".format(
            len(server_names), server_names))

        iframe_html = data.get('iframe', '')
        iframe_m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', iframe_html, re.I)
        if iframe_m:
            servers.append({
                'name': server_names[0] if server_names else 'سيرفر 1',
                'url': iframe_m.group(1),
                'type': 'embed',
                'quality': '',
            })

        for i in range(1, len(server_names)):
            post_data = 'post_id={}&server={}'.format(post_id, i)
            ajax_html, _ = self._fetch(ajax_url, referer=page_url, post_data=post_data)
            try:
                if isinstance(ajax_html, bytes):
                    ajax_html = ajax_html.decode('utf-8', errors='ignore')
                data_i = json.loads(ajax_html)
                if data_i.get('success'):
                    iframe_m = re.search(
                        r'<iframe[^>]+src=["\']([^"\']+)["\']',
                        data_i.get('iframe', ''), re.I)
                    if iframe_m:
                        servers.append({
                            'name': server_names[i] if i < len(server_names) else 'سيرفر {}'.format(i + 1),
                            'url': iframe_m.group(1),
                            'type': 'embed',
                            'quality': '',
                        })
            except (ValueError, TypeError):
                pass

        log("AlooyTV: extracted {} servers via AJAX".format(len(servers)))
        return servers

    def _extract_watch_servers_legacy(self, html, page_url):
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
                "url": url, "type": kind, "quality": "",
            })

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

        if not servers:
            for m in re.finditer(
                    r'<[^>]*class=["\'][^"\']*\bsingle_servers\b[^"\']*["\'][^>]*'
                    r'(?:data-(?:server|url|link|src)=["\']([^"\']+)["\'])?',
                    html, re.I):
                if m.group(1):
                    _add(m.group(1), "سيرفر")

        if not servers:
            for attr in ("data-link", "data-url", "data-iframe",
                         "data-src", "data-server", "data-embed"):
                for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                    _add(m.group(1))

        if not servers:
            for iframe in extract_iframes(html, page_url):
                _add(iframe)

        if not servers:
            for m in re.finditer(
                    r'<(?:source|video)[^>]+src=["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                    html, re.I):
                _add(m.group(1), "مشاهدة مباشرة", "direct")

        return servers

    # ─── Detail page parsing ────────────────────────────────────────────
    def _parse_info_box(self, html):
        info = {}
        if not html:
            return info
        labels = {
            "القسم": "category",   "النوع": "genres",
            "الجودة": "quality",   "اللغة": "language",
            "البلد": "country",    "السنة": "year",
            "المدة": "runtime",    "مدة العرض": "runtime",
            "القناة": "channel",   "المخرج": "director",
            "الكتابة": "writer",   "البطولة": "cast",
            "القصة": "plot",
        }
        for m in re.finditer(
                r'<div[^>]*class=["\'][^"\']*\bsng-detail-row\b[^"\']*["\'][^>]*>'
                r'\s*<span[^>]*class=["\'][^"\']*\bsng-dk\b[^"\']*["\'][^>]*>(.*?)</span>'
                r'\s*<span[^>]*class=["\'][^"\']*\bsng-dv\b[^"\']*["\'][^>]*>(.*?)</span>',
                html, re.S | re.I):
            key_raw = self._strip_tags(m.group(1)).rstrip(":：").strip()
            key = labels.get(key_raw)
            if not key:
                continue
            value = self._strip_tags(m.group(2)).strip()
            if value:
                info[key] = value
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

    def _extract_quality_from_tags(self, html):
        tags_m = re.search(
            r'<div[^>]*class=["\'][^"\']*\bsng-tags\b[^"\']*["\'][^>]*>(.*?)</div>',
            html or "", re.S | re.I)
        if tags_m:
            tags = tags_m.group(1)
            for tag_m in re.finditer(
                    r'<span[^>]*class=["\'][^"\']*\bsng-tag\b[^"\']*["\'][^>]*>(.*?)</span>',
                    tags, re.S | re.I):
                text = self._strip_tags(tag_m.group(1)).strip()
                if re.search(r'\b(?:2160p|1440p|1080p|720p|480p|360p|4k|HD|CAM|TS|WEB-?DL|BluRay|BDRip)\b',
                             text, re.I):
                    return text
        return ""

    def _extract_year_from_tags(self, html):
        tags_m = re.search(
            r'<div[^>]*class=["\'][^"\']*\bsng-tags\b[^"\']*["\'][^>]*>(.*?)</div>',
            html or "", re.S | re.I)
        if tags_m:
            tags = tags_m.group(1)
            for tag_m in re.finditer(
                    r'<span[^>]*class=["\'][^"\']*\bsng-tag\b[^"\']*["\'][^>]*>(.*?)</span>',
                    tags, re.S | re.I):
                text = self._strip_tags(tag_m.group(1)).strip()
                ym = re.search(r'\b(19\d{2}|20\d{2})\b', text)
                if ym:
                    return ym.group(1)
        return ""

    def _parse_downloads(self, html):
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
                "resolution": resolution, "size": size,
                "quality": quality, "url": url,
            })

        for m in re.finditer(
                r'<a[^>]+class=["\'][^"\']*\bsng-btn-sec\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
                html, re.I):
            dl_url = m.group(1)
            if 'govid.live/d/' in dl_url or '/download' in dl_url.lower():
                _add(dl_url)

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

    def _find_watch_url(self, html, page_url):
        m = (re.search(r'<a[^>]+class=["\'][^"\']*\bwatch\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
             or re.search(r'<a[^>]+href=["\']([^"\']+/watch/?)["\']', html, re.I)
             or re.search(r'<a[^>]+href=["\']([^"\']*[?&]watch=1[^"\']*)["\']', html, re.I))
        if m:
            return self._full_url(m.group(1))
        base = page_url.split("?")[0].rstrip("/")
        if not base.endswith("/watch"):
            return base + "/watch/"
        return page_url

    # ─── govid.live stream resolution (NEW) ────────────────────────────
    def _resolve_govid_stream(self, url, _depth=0):
        if _depth > 3:
            return None

        html, final_url = self._fetch(url)
        if not html:
            return None

        if '/play/' in url.lower():
            iframe_m = re.search(
                r'<iframe[^>]+src=["\']([^"\']+)["\']',
                html, re.I)
            if iframe_m:
                embed_url = self._full_url(iframe_m.group(1))
                if embed_url and embed_url != url:
                    log("AlooyTV: following inner iframe: {}".format(embed_url))
                    return self._resolve_govid_stream(embed_url, _depth + 1)

        for m in re.finditer(
                r'(?:const|var|let)\s+\w+\s*=\s*["\']([0-9a-fA-F]{40,})["\']',
                html):
            try:
                decoded = bytes.fromhex(m.group(1)).decode('utf-8', errors='ignore')
                if '.m3u8' in decoded or '.mp4' in decoded:
                    log("AlooyTV: decoded hex stream URL: {}".format(decoded[:80]))
                    return decoded
            except Exception:
                pass

        sources_m = re.search(
            r'sources\s*:\s*\[[^\]]*?file\s*:\s*["\']([^"\']+)["\']',
            html, re.S | re.I)
        if sources_m:
            return sources_m.group(1)

        m3u8_m = re.search(
            r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            html, re.I)
        if m3u8_m:
            return m3u8_m.group(1)

        mp4_m = re.search(
            r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
            html, re.I)
        if mp4_m:
            return mp4_m.group(1)

        return None

    # ─── get_page ───────────────────────────────────────────────────────
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
            "downloads": [],
            "type": m_type or "movie",
        }
        if not html:
            log("AlooyTV: get_page failed for {}".format(url))
            return result

        title = ""
        tm = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if tm:
            title = self._clean_title(tm.group(1))
        if not title:
            tm = re.search(r'<h1[^>]*class=["\'][^"\']*\bsng-title\b[^"\']*["\'][^>]*>(.*?)</h1>', html, re.S | re.I)
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
            poster = re.sub(r"-\d+x\d+(?=\.\w+$)", "", poster)
            result["poster"] = self._full_url(poster)

        plot = ""
        sm = re.search(
            r'<div[^>]*class=["\'][^"\']*\bsng-subtitle\b[^"\']*["\'][^>]*>(.*?)</div>',
            html, re.S | re.I)
        if sm:
            plot = self._strip_tags(sm.group(1)).strip()
        if not plot:
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

        rat_m = re.search(r'class=["\'][^"\']*\bpostRating\b[^"\']*["\'][^>]*>.*?<span[^>]*>([\d.]+)',
                          html, re.S | re.I)
        if rat_m:
            result["rating"] = rat_m.group(1).strip()

        info = self._parse_info_box(html)
        for k in ("category", "genres", "quality", "language",
                  "country", "year", "channel", "runtime"):
            if info.get(k):
                result[k] = info[k]

        quality = self._extract_quality_from_tags(html)
        if quality:
            result["quality"] = quality

        year_tag = self._extract_year_from_tags(html)
        if year_tag:
            result["year"] = year_tag
        ym = re.search(r'\b(19\d{2}|20\d{2})\b', result["title"])
        if ym:
            result["year"] = result["year"] or ym.group(1)
            result["title"] = re.sub(r'\s*\b' + ym.group(1) + r'\b\s*', ' ',
                                     result["title"]).strip(" -|")

        url_low = (final_url or url).lower()
        if "/episode" in url_low or "الحلقة" in raw_title:
            result["type"] = "episode"
        elif "/series" in url_low or "مسلسل" in raw_title:
            result["type"] = "series"
        elif m_type:
            result["type"] = m_type

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

        result["downloads"] = self._parse_downloads(html)

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
            headers = {
                "User-Agent": _PROBE_HEADERS["User-Agent"],
                "Referer": self._get_base()
            }
            return url, q, headers, _variants_for(url)
        if ".mp4" in low:
            return url, self._quality_from_url(url), self._get_base(), []

        # ── govid.live / vidhide embed → resolve HLS ──────────────────
        if any(h in low for h in (
                "govid.live", "vidhide", "vidplay", "vidhide.pro",
                "vidhide.com", "go-stream.link")):
            log("AlooyTV: resolving govid.live embed: {}".format(url[:80]))
            stream_url = self._resolve_govid_stream(url, _depth)
            if stream_url:
                # govid.live uses Cloudflare and requires specific headers to allow playback
                headers = {
                    "User-Agent": _PROBE_HEADERS["User-Agent"],
                    "Referer": "https://govid.live/",
                    "Origin": "https://govid.live"
                }
                return stream_url, "HD", headers, _variants_for(stream_url)
            log("AlooyTV: govid.live resolution failed for {}".format(url[:80]))

        # ── Generic host dispatcher ────────────────────────────────────
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