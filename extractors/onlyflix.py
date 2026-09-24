# -*- coding: utf-8 -*-
"""
OnlyFlix extractor — onlyflix.to
=================================
WordPress 7.x site running the "Onlyflix 2.0" theme plus the family of
custom plugins (onlyflix-bests, onlyflix-tvapi-featured, onlyflix-accounts,
onlyflix-title-popularity, onlyflix-pwa).

Sequence (confirmed against real captures, 2026-09-24):

    homepage      → get_categories() — the site's own drawer menu:
                    /movies/, /series/, /best/{movies,tv-shows}/{YEAR}/,
                    /genre/{slug}/, /reality-tv/ (NOT under /genre/),
                    /coming-soon/, /apps/.
    category      → get_category_items() — .of-title-card-v2 cards
                    (data-* attributes; the old of-media-card parser is
                    kept as _extract_cards_v1 fallback), WordPress
                    /page/N/ pagination (.mcs-light-pagination, no
                    rel="next").
    detail page   → get_page() — JSON-LD Movie/TVSeries block first
                    (name, image, description, datePublished, genre,
                    duration, IMDb identifier, aggregateRating), then
                    og-tags.
    servers       → DOM .player-tab-btn[data-player-url] first; when the
                    tabs are AJAX-injected and missing from raw html:
                    POST action=mcp_get_available_players with
                    {nonce, post_id, type} to data-player-ajax-url;
                    last resort: synthesize the four family embed URLs
                    from the IMDb id.
    episodes      → inline allSeasonsData JS object on series pages
                    (season → episode → {id, title, permalink}), with a
                    DOM .episode-watch-link fallback.
    embeds        → vidfast.vc / vidapi.xyz / share.cdnm.ink /
                    sv2.nontongo.{stream,day} are resolved by hosts.py's
                    resolve_vidsrc_family.

Notes:
  * Posters: wp-content/uploads/posters/{id}_medium.jpg (the card img);
    backdrops live under wp-content/uploads/onlyflix-backdrops/.
  * data-genres / data-overview / data-runtime may be empty; data-runtime
    is a display string ("1 h 39 min", "0", "").
  * Type detection uses authoritative sources (data-player-content-type,
    OnlyFlixAccounts.current.type, body class single-tvshows, JSON-LD
    @type) — never the URL alone: movie AND tv detail pages both use
    plain slugs (/i-was-a-stranger/, /series/lanterns/).
  * mcp_get_available_players takes the MOVIE or EPISODE post id —
    never the series id (Lanterns: series=1547757, S1E6=1548186).
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

try:
    from html import unescape as html_unescape
except ImportError:                              # py2 shim
    def html_unescape(s):
        return s

try:
    from urllib.parse import urljoin, urlparse, quote_plus, quote, parse_qs
except ImportError:                              # py2 shim
    from urllib import quote, quote_plus
    from urlparse import urlparse
    from urlparse import urljoin
    from urlparse import parse_qs


# ─── Process-wide base cache ────────────────────────────────────────────────
_BASE_CACHE_TTL  = 300
_PROBE_COOLDOWN  = 30
_base_cache      = {"url": None, "resolved_at": 0, "probed_at": 0}
_base_cache_lock = threading.Lock()
_probe_lock      = threading.Lock()

_PROBE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class OnlyFlixExtractor(BaseExtractor):
    """Extractor for OnlyFlix (onlyflix.to)."""

    MAIN_URL = "https://onlyflix.to/"

    DOMAINS = [
        "https://onlyflix.to/",
        "https://www.onlyflix.to/",
    ]

    VALID_HOST_MARKERS = ("onlyflix.to",)
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)

    # Third-party scripts/CDNs and social embeds we never want as servers.
    _NON_MEDIA_HOSTS = (
        "facebook.com", "twitter.com", "instagram.com", "youtube.com/embed",
        "google.com", "t.me", "telegram.me", "whatsapp.com",
        "googletagmanager", "googlesyndication", "doubleclick",
        "google-analytics", "cloudflareinsights",
        "mc.yandex.ru", "yandex.ru",
    )

    # Known host fragments that only appear on watch pages
    # (vidfast / cdnm.ink / vidapi / nontongo = the onlyflix server tabs).
    _MEDIA_HOST_MARKERS = (
        "streamwish", "filemoon", "dood", "playmogo", "vidhide", "voe",
        "mixdrop", "luluvdo", "lulustream", "streamtape", "mp4upload",
        "vidmoly", "savefiles", "mxcontent", "downet", "ok.ru", "okru",
        "streamruby", "stmruby", "uqload", "byselapuix", "hgcloud",
        "govid", "fastvid", "vidguard", "vidaraa", "morencius",
        "audinifer", "hanerix", "vibuxer", "earnvids", "cloudwindow",
        "superflixapi", "zxcstream", "vidsrc", "vidcore",
        "vidfast", "cdnm.ink", "vidapi", "nontongo",
    )

    # Confirmed server-list AJAX action (Network capture, 2026-09-24):
    #   POST <data-player-ajax-url>
    #   action=mcp_get_available_players
    #   nonce=<data-player-nonce>
    #   post_id=<movie|episode post id>      (never the series id)
    #   type=movie|episode
    _SERVER_AJAX_ACTION = "mcp_get_available_players"

    # Genre slugs the site exposes at /genre/<slug>/ (drawer-confirmed).
    _GENRES = [
        ("animation",   "Animation"),
        ("adventure",   "Adventure"),
        ("action",      "Action"),
        ("biography",   "Biography"),
        ("comedy",      "Comedy"),
        ("crime",       "Crime"),
        ("documentary", "Documentary"),
        ("drama",       "Drama"),
        ("family",      "Family"),
        ("fantasy",     "Fantasy"),
        ("history",     "History"),
        ("horror",      "Horror"),
        ("kids",        "Kids"),
        ("music",       "Music"),
        ("musical",     "Musical"),
        ("mystery",     "Mystery"),
        ("reality",     "Reality"),
        ("romance",     "Romance"),
        ("sci-fi",      "Sci-Fi"),
        ("sport",       "Sport"),
        ("thriller",    "Thriller"),
        ("war",         "War"),
        ("western",     "Western"),
    ]

    _YEARS = ["2026", "2025", "2024", "2023", "2022", "2021", "2020",
              "2019", "2018", "2017", "2016", "2015"]

    # ── Construction / base probe ──────────────────────────────────────────
    def __init__(self):
        super(OnlyFlixExtractor, self).__init__()
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
        if "cf-browser-verification" in text:
            return True
        if any(m in final for m in self.BLOCKED_HOST_MARKERS):
            return True
        return False

    def _looks_like_onlyflix_page(self, html):
        text = html or ""
        return (
            "onlyflix" in text.lower()
            or "of-title-card-v2" in text        # 2026-09-24: real card class
            or "of-media-card" in text           # legacy/featured markup
            or "of-home-hero" in text
            or "of-browse-rail" in text
            or "Onlyflix 2.0" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        """
        Confirm the candidate actually serves listing pages (real cards),
        not just a homepage/landing. 2026-09-24: grid cards are
        of-title-card-v2; count both classes (featured/homepage rails
        still use of-media-card).
        """
        if not base:
            return False
        test_url = urljoin(base, "movies/")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("OnlyFlix: deep-content probe failed for {}: {}".format(base, e))
            return False
        if not html or self._is_blocked_page(html, final or ""):
            log("OnlyFlix: deep-content probe empty/blocked for {}".format(base))
            return False
        count = html.count("of-title-card-v2") + html.count("of-media-card")
        if count < 6:
            log("OnlyFlix: {} answered but only {} card markers on "
                "/movies/ — not a real listing".format(base, count))
            return False
        return True

    def _get_base(self):
        if self._resolved_base:
            return self._resolved_base

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

        with _probe_lock:
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
                log("OnlyFlix: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain,
                                        extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain

                if not self._is_valid_site_url(final_url):
                    log("OnlyFlix: unexpected host after redirect {}"
                        .format(final_url))
                    continue
                if self._is_blocked_page(html, final_url):
                    log("OnlyFlix: blocked {}".format(final_url))
                    continue
                if not (html and self._looks_like_onlyflix_page(html)):
                    continue

                candidate = self._site_root(final_url)
                if not self._base_serves_deep_content(candidate):
                    log("OnlyFlix: {} is not a content mirror — next".format(
                        candidate))
                    continue

                self._resolved_base = candidate
                self.main_url = candidate
                with _base_cache_lock:
                    _base_cache["url"] = candidate
                    _base_cache["resolved_at"] = now
                    _base_cache["probed_at"] = now
                log("OnlyFlix: selected base {}".format(candidate))
                return candidate

            self._resolved_base = self.MAIN_URL
            self.main_url = self.MAIN_URL
            with _base_cache_lock:
                _base_cache["url"] = self.MAIN_URL
                _base_cache["probed_at"] = now
            log("OnlyFlix: all probes failed, using {}".format(self.MAIN_URL))
            return self._resolved_base

    # ── URL / title helpers ────────────────────────────────────────────────
    def _clean_title(self, title):
        if not title:
            return ""
        text = html_unescape(str(title))
        text = text.replace("&amp;", "&").replace("&#39;", "'")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Strip "Open X" prefix that aria-label sometimes carries.
        text = re.sub(r"^Open\s+", "", text, flags=re.I)
        return text

    def _full_url(self, path):
        if not path:
            return ""
        path = html_unescape(str(path).strip())
        if path.startswith("//"):
            path = "https:" + path
        if path.startswith("http"):
            full_url = path
        else:
            full_url = urljoin(self._get_base(), path)
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

    # ── Card parser (of-title-card-v2, confirmed 2026-09-24) ──────────────
    def _extract_cards(self, html, max_items=200, default_type=None):
        """
        Real markup (verbatim, /series/ grid + related-titles on detail
        pages):

          <article class="of-title-card-v2"
                   data-ofa-post-id="1548305" data-ofa-kind="tvshows"
                   data-title="City of Blood"
                   data-url="https://onlyflix.to/series/city-of-blood/"
                   data-backdrop="https://onlyflix.to/wp-content/uploads/posters/1548305_medium.jpg?v=..."
                   data-year="2026" data-rating="6.4" data-rating-tier="ok"
                   data-runtime="0" data-genres="Mystery"
                   data-overview="Berlin — a city-state on the brink...">
            <a class="of-title-card-v2__link" href="..." aria-label="...">
              <img class="of-title-card-v2__poster"
                   src="...posters/1548305_medium.jpg?v=..." ...>
            <div class="ofa-card-actions" data-ofa-type="tvshow">

        Everything needed is in data-* attributes. data-genres /
        data-overview / data-runtime can be empty; data-runtime is a
        display string ("1 h 39 min", "0", ""). data-backdrop is
        inconsistent (backdrops/ on detail pages, posters/ on grid
        cards) so it's only trusted when it contains /posters/.
        Featured-rail items duplicate the newest grid items → dedupe on
        URL + post-id; of-archive-featured-card blocks are skipped on
        purpose (they're the duplicates).
        """
        items, seen_urls, seen_ids = [], set(), set()
        if not html:
            return items

        for block in re.split(r'(?=<article[^>]+class="[^"]*of-title-card-v2)', html):
            if "of-title-card-v2" not in block:
                continue
            end = block.find("</article>")
            block = block[:end + 10] if end >= 0 else block

            def d(name, blk=block):
                m = re.search(name + r'="([^"]*)"', blk, re.I)
                return (m.group(1) if m else "").strip()

            url = self._full_url(d("data-url"))
            if not url:
                m = re.search(
                    r'class="[^"]*of-title-card-v2__link[^"]*"[^>]*href="([^"]+)"',
                    block, re.I)
                if m:
                    url = self._full_url(m.group(1))
            if not url or url in seen_urls:
                continue

            post_id = d("data-ofa-post-id")
            if post_id and post_id in seen_ids:
                continue

            title = self._clean_title(d("data-title"))
            if not title:
                m = re.search(
                    r'class="[^"]*of-title-card-v2__title[^"]*"[^>]*>(.*?)</span>',
                    block, re.S | re.I)
                if m:
                    title = self._clean_title(m.group(1))
            if not title:
                m = re.search(r'aria-label="([^"]+)"', block, re.I)
                if m:
                    title = self._clean_title(m.group(1))
            if not title:
                continue
            seen_urls.add(url)
            if post_id:
                seen_ids.add(post_id)

            # Poster: img first; data-backdrop only when it's a poster URL
            poster = ""
            pm = (re.search(
                    r'<img[^>]+class="[^"]*of-title-card-v2__poster[^"]*"[^>]+src="([^"]+)"',
                    block, re.I) or
                  re.search(
                    r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*of-title-card-v2__poster',
                    block, re.I))
            if pm:
                poster = self._full_url(pm.group(1))
            if not poster:
                bd = d("data-backdrop")
                if bd and "/posters/" in bd:
                    poster = self._full_url(bd)

            rating = d("data-rating")
            year   = d("data-year")
            runtime = d("data-runtime")
            if runtime in ("0", ""):
                runtime = ""
            genres = self._clean_title(d("data-genres"))
            plot   = self._clean_title(d("data-overview"))

            # Type: data-ofa-kind ("movies"/"tvshows", plural) or
            # data-ofa-type ("movie"/"tvshow", singular) or URL
            kind = (d("data-ofa-kind") or d("data-ofa-type")).lower()
            url_low = url.lower()
            if "tvshow" in kind:
                item_type = "series"
            elif "movie" in kind:
                item_type = "movie"
            elif "/series/" in url_low:
                item_type = "series"
            else:
                item_type = default_type or "movie"

            items.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": plot,
                "label": "",
                "rating": rating,
                "runtime": runtime,
                "genres": genres,
                "year": year,
                "type": item_type,
                "_action": "details",
            })
            if len(items) >= max_items:
                break

        if not items:
            items = self._extract_cards_v1(html, max_items=max_items)
        if not items:
            items = self._extract_cards_best_rail(html, max_items=max_items)  # [PATCH O4]
        return items

    def _extract_cards_best_rail(self, html, max_items=200):
        """
        [PATCH O4] "Best of Year" rail pages (/best/{movies,tv-shows}/{YEAR}/, a real,
        documented category) use a third card structure -- confirmed against a real
        capture, neither of-title-card-v2 nor of-media-card appear on this page type at
        all. Real structure per card (verbatim):

          <article class="ofb-card">
            <a class="ofb-card-link" href="https://onlyflix.to/i-was-a-stranger/">
            <img src=".../posters/1487813_medium.jpg" alt="I Was a Stranger">
            <button class="ofb-card-play" data-post-id="1487813" data-kind="movies"
                    data-content-type="movie" data-title="I Was a Stranger"
                    data-poster-url="...">
            <span class="ofb-card-rating ...">★ 9.3</span>
            <span class="ofb-card-hover__meta">
              <span class="ofb-card-hover__rating ...">★ 9.3</span>
              <span>1h 43m</span>
              <span>2026</span>
            </span>
            <span class="ofb-card-hover__genres">Drama, Thriller</span>
          </article>
        """
        items = []
        seen = set()
        for block_m in re.finditer(
                r'<article[^>]+class="[^"]*\bofb-card\b[^"]*"[^>]*>(.*?)</article>',
                html, re.S | re.I):
            block = block_m.group(1)

            url_m = re.search(r'class="[^"]*ofb-card-link[^"]*"[^>]*href="([^"]+)"',
                              block, re.I)
            if not url_m:
                continue
            url = self._full_url(url_m.group(1))
            if not url or url in seen:
                continue

            title_m = re.search(r'data-title="([^"]+)"', block, re.I)
            if not title_m:
                title_m = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
            title = self._clean_title(html_unescape(title_m.group(1))) if title_m else ""
            if not title:
                continue
            seen.add(url)

            poster_m = re.search(r'data-poster-url="([^"]+)"', block, re.I)
            if not poster_m:
                poster_m = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
            poster = poster_m.group(1) if poster_m else ""

            kind_m = re.search(r'data-content-type="([^"]+)"', block, re.I)
            if not kind_m:
                kind_m = re.search(r'data-kind="([^"]+)"', block, re.I)
            kind = (kind_m.group(1) if kind_m else "").lower()
            item_type = "series" if kind in ("tvshows", "tv", "series") else "movie"

            rating_m = re.search(r'class="[^"]*ofb-card-rating[^"]*"[^>]*>[^\d]*([\d.]+)',
                                 block, re.I)
            rating = rating_m.group(1) if rating_m else ""

            meta_m = re.search(
                r'class="[^"]*ofb-card-hover__meta[^"]*"[^>]*>(.*?)'
                r'(?=<span class="[^"]*ofb-card-hover__genres)',
                block, re.S | re.I)
            runtime = year = ""
            if meta_m:
                spans = re.findall(r'<span[^>]*>([^<]*)</span>', meta_m.group(1))
                spans = [s.strip() for s in spans if s.strip()]
                for s in spans:
                    if re.match(r'^\d{4}$', s):
                        year = s
                    elif re.search(r'\d', s) and not year:
                        runtime = s

            genres_m = re.search(
                r'class="[^"]*ofb-card-hover__genres[^"]*"[^>]*>([^<]*)<',
                block, re.I)
            genres = genres_m.group(1).strip() if genres_m else ""

            items.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": "",
                "label": "",
                "rating": rating,
                "runtime": runtime,
                "genres": genres,
                "year": year,
                "type": item_type,
                "_action": "details",
            })
            if len(items) >= max_items:
                break
        return items

    # ── Legacy card parser (of-media-card, kept as fallback) ──────────────
    def _extract_cards_v1(self, html, max_items=200):
        """
        Older / featured-rail markup. Real card shape:

          <article class="of-media-card of-lazy-bg is-lazy-loaded"
                   data-post-id="1548101" data-kind="movies"
                   data-of-lazy-bg="...featured-cards/.._backdrop.jpg"
                   data-of-lazy-poster="...posters/1548101_small.jpg">
            <a class="of-media-card__link" href="..." aria-label="Open Runner">
            <span class="of-media-card__name">Runner</span>
            <span class="of-media-card__rating">★ 6.7</span>
            <span class="of-media-card__runtime">1 h 37 min</span>
            <span class="of-media-card__year">2026</span>
            <span class="of-media-card__genres">Action, Comedy, Thriller</span>
            <span class="of-quality-badge of-media-card__quality">HD</span>
        """
        items = []
        seen_urls = set()
        if not html:
            return items

        for block in re.split(r'(?=<article[^>]+class="[^"]*of-media-card)', html):
            if "of-media-card__link" not in block:
                continue
            # Trim to the next </article>
            end = block.find("</article>")
            if end < 0:
                continue
            block = block[:end + len("</article>")]

            # ── URL ───────────────────────────────────────────────────────
            url_m = re.search(
                r'class="[^"]*of-media-card__link[^"]*"[^>]*href="([^"]+)"',
                block, re.I)
            if not url_m:
                url_m = re.search(
                    r'href="([^"]+)"[^>]*class="[^"]*of-media-card__link',
                    block, re.I)
            if not url_m:
                continue
            url = self._full_url(url_m.group(1))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # ── Title — .of-media-card__name, else img alt ───────────────
            title = ""
            name_m = re.search(
                r'class="[^"]*of-media-card__name[^"]*"[^>]*>(.*?)</span>',
                block, re.S | re.I)
            if name_m:
                title = self._clean_title(name_m.group(1))
            if not title:
                alt_m = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
                if alt_m:
                    title = self._clean_title(alt_m.group(1))
            if not title:
                aria_m = re.search(
                    r'class="[^"]*of-media-card__link[^"]*"[^>]*aria-label="([^"]+)"',
                    block, re.I)
                if aria_m:
                    title = self._clean_title(aria_m.group(1))
            if not title:
                continue

            # ── Poster — prefer the site's own _small.jpg ────────────────
            poster = ""
            pm = re.search(r'data-of-lazy-poster="([^"]+)"', block, re.I)
            if pm:
                poster = self._full_url(pm.group(1))
            if not poster:
                pm = re.search(r'data-of-lazy-bg="([^"]+)"', block, re.I)
                if pm:
                    poster = self._full_url(pm.group(1))
            if not poster:
                pm = re.search(
                    r'<img[^>]+class="[^"]*of-media-card__logo[^"]*"[^>]+src="([^"]+)"',
                    block, re.I)
                if pm:
                    poster = self._full_url(pm.group(1))
                else:
                    pm = re.search(
                        r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*of-media-card__logo',
                        block, re.I)
                    if pm:
                        poster = self._full_url(pm.group(1))

            # ── Rating ───────────────────────────────────────────────────
            rating = ""
            rm = re.search(
                r'class="[^"]*of-media-card__rating[^"]*"[^>]*>(.*?)</span>',
                block, re.S | re.I)
            if rm:
                rt = re.sub(r"<[^>]+>", " ", rm.group(1))
                rt = re.sub(r"[^\d.]", "", rt)
                rating = rt

            # ── Runtime ──────────────────────────────────────────────────
            runtime = ""
            rtm = re.search(
                r'class="[^"]*of-media-card__runtime[^"]*"[^>]*>(.*?)</span>',
                block, re.S | re.I)
            if rtm:
                runtime = self._clean_title(rtm.group(1))

            # ── Year ─────────────────────────────────────────────────────
            year = ""
            ym = re.search(
                r'class="[^"]*of-media-card__year[^"]*"[^>]*>(.*?)</span>',
                block, re.S | re.I)
            if ym:
                ym2 = re.search(r"\b(19\d{2}|20\d{2})\b", ym.group(1))
                if ym2:
                    year = ym2.group(1)
            if not year:
                ym2 = re.search(r"\b(19\d{2}|20\d{2})\b", title)
                if ym2:
                    year = ym2.group(1)

            # ── Genres ───────────────────────────────────────────────────
            genres = ""
            gm = re.search(
                r'class="[^"]*of-media-card__genres[^"]*"[^>]*>(.*?)</span>',
                block, re.S | re.I)
            if gm:
                genres = self._clean_title(gm.group(1))

            # ── Quality label ────────────────────────────────────────────
            label = ""
            qm = re.search(
                r'class="[^"]*of-quality-badge[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.S | re.I)
            if qm:
                label = self._clean_title(qm.group(1))

            # ── Type (data-kind attribute on the article) ────────────────
            kind_m = re.search(r'data-kind="([^"]+)"', block, re.I)
            kind = (kind_m.group(1) if kind_m else "").lower()
            url_low = url.lower()
            if "tvshows" in kind or "tv" in kind:
                item_type = "series"
            elif "movies" in kind or "movie" in kind:
                item_type = "movie"
            elif "/series/" in url_low or "/episodes/" in url_low:
                item_type = "series"
            else:
                item_type = "movie"

            items.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": genres,
                "label": label,
                "rating": rating,
                "runtime": runtime,
                "genres": genres,
                "year": year,
                "type": item_type,
                "_action": "details",
            })
            if len(items) >= max_items:
                break

        return items

    # ── Pagination ────────────────────────────────────────────────────────
    def _parse_pagination(self, html, current_url):
        """
        Real markup (verbatim, /series/, 2026-09-24):

          <nav class="mcs-light-pagination" aria-label="Navigation Page">
            <ul class="pagination">
              <li class="page-item active"><span class="page-link">1</span></li>
              <li class="page-item"><a class="page-link" href=".../series/page/2/">2</a></li>
              <li class="page-item mcs-light-pagination__arrow">
                <a class="page-link" href=".../series/page/2/">›</a></li>
            </ul>
          </nav>

        No rel="next" anywhere. URL pattern: /page/N/.
        """
        next_url = None

        # 1. Authoritative: the mcs-light-pagination arrow item
        m = re.search(
            r'class="page-item[^"]*mcs-light-pagination__arrow[^"]*"[^>]*>\s*'
            r'<a[^>]+href="([^"]+)"', html or "", re.S | re.I)
        if m:
            next_url = self._full_url(m.group(1))

        # 2. Standard WP rel=next
        if not next_url:
            for pat in (
                r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']',
                r'<a[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']',
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']next["\']',
            ):
                m = re.search(pat, html or "", re.I)
                if m:
                    next_url = self._full_url(m.group(1))
                    break

        # 3. Numeric "next" arrow inside the pagination block
        if not next_url:
            pag_m = re.search(
                r'<(?:ul|div|nav)[^>]+class="[^"]*(?:pagination|page-numbers|nav-links)[^"]*"[^>]*>(.*?)</(?:ul|div|nav)>',
                html or "", re.S | re.I)
            scope = pag_m.group(1) if pag_m else (html or "")
            m = (re.search(
                    r'<a[^>]+class="[^"]*\bnext\b[^"]*"[^>]+href="([^"]+)"',
                    scope, re.I)
                 or re.search(
                    r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*\bnext\b[^"]*"',
                    scope, re.I)
                 or re.search(
                    r'<a[^>]+href="([^"]+)"[^>]*>\s*(?:&raquo;|»|›|→)\s*</a>',
                    scope, re.I))
            if m:
                next_url = self._full_url(m.group(1))

        # 4. Derive current page and try /page/N/ or ?page=N
        if not next_url:
            m = re.search(r'/page/(\d+)/?', current_url or "")
            current = int(m.group(1)) if m else 1
            m = re.search(r'[?&]page=(\d+)', current_url or "")
            if m:
                current = max(current, int(m.group(1)))
            want = current + 1
            m = re.search(
                r'href="([^"]*(?:/page/{}/|[?&]page={})\b[^"]*)"'.format(want, want),
                html or "", re.I)
            if m:
                next_url = self._full_url(m.group(1))

        if next_url and next_url != current_url:
            return {
                "title": "➡️ Next Page",
                "url": next_url,
                "type": "category",
                "_action": "category",
            }
        return None

    # ── Public: categories ─────────────────────────────────────────────────
    def get_categories(self, mtype="movie"):
        """The site's own drawer menu (Movies / TV Shows / Genres) plus
        every /best/<scope>/<year>/ and /genre/<slug>/ branch."""
        self._get_base()
        base = self._get_base().rstrip("/")
        cats = []

        cats.append({"title": "🏠 الرئيسية",
                     "url": base + "/",
                     "type": "category", "_action": "category"})

        # ── Movies ──────────────────────────────────────────────────────
        cats.append({"title": "── أفلام ──", "url": "", "type": "separator"})
        cats.append({"title": "🎬 جميع الأفلام",
                     "url": base + "/movies/",
                     "type": "category", "_action": "category"})
        for year in self._YEARS[:6]:
            cats.append({
                "title": "🎬 أفضل أفلام {}".format(year),
                "url": base + "/best/movies/{}/".format(year),
                "type": "category", "_action": "category",
            })

        # ── TV Shows ────────────────────────────────────────────────────
        cats.append({"title": "── مسلسلات ──", "url": "", "type": "separator"})
        cats.append({"title": "📺 جميع المسلسلات",
                     "url": base + "/series/",
                     "type": "category", "_action": "category"})
        for year in self._YEARS[:6]:
            cats.append({
                "title": "📺 أفضل مسلسلات {}".format(year),
                "url": base + "/best/tv-shows/{}/".format(year),
                "type": "category", "_action": "category",
            })

        # ── Genres ─────────────────────────────────────────────────────
        cats.append({"title": "── تصنيفات ──", "url": "", "type": "separator"})
        for slug, name in self._GENRES:
            cats.append({
                "title": "🎭 {}".format(name),
                "url": base + "/genre/{}/".format(slug),
                "type": "category", "_action": "category",
            })
        # Reality-TV is at the site root, not under /genre/.
        cats.append({"title": "🎭 Reality-TV",
                     "url": base + "/reality-tv/",
                     "type": "category", "_action": "category"})

        # ── Genre × year (deep) ────────────────────────────────────────
        cats.append({"title": "── تصنيف × سنة ──", "url": "", "type": "separator"})
        for slug, name in self._GENRES[:8]:        # keep the list compact
            for year in self._YEARS[:3]:
                cats.append({
                    "title": "🎭 {} {}".format(name, year),
                    "url": base + "/best/genre/{}/{}/".format(slug, year),
                    "type": "category", "_action": "category",
                })

        # ── Other sections ─────────────────────────────────────────────
        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        # [PATCH O5] "Coming Soon" removed -- confirmed against a real capture it's not a
        # browsable category at all: its cards have no <a href>, just notify-me buttons for
        # unreleased content, nothing to navigate to or watch.
        cats.append({"title": "📱 التطبيقات",
                     "url": base + "/apps/",
                     "type": "category", "_action": "category"})

        return cats

    # ── Public: category / listing ─────────────────────────────────────────
    def get_category_items(self, url, page=1):
        fetch_url = self._full_url(url)

        # WordPress /page/N/ — splice N onto the path (or replace existing).
        if page and page > 1:
            parsed = urlparse(fetch_url)
            path = parsed.path or "/"
            if re.search(r"/page/\d+/?$", path):
                path = re.sub(r"/page/\d+/?$", "/page/{}/".format(page), path)
            else:
                if not path.endswith("/"):
                    path += "/"
                path += "page/{}/".format(page)
            fetch_url = parsed._replace(path=path).geturl()

        log("OnlyFlix: fetching listing {}".format(fetch_url))
        html, final_url = fetch(fetch_url, referer=self._get_base(),
                                extra_headers=_PROBE_HEADERS)
        if not html:
            log("OnlyFlix: get_category_items failed for {}".format(fetch_url))
            return []

        # 2026-09-24: TV archives carry post-type-archive-tvshows on
        # <body> — force series as the card default there.
        default_type = "series" if "post-type-archive-tvshows" in html else None
        items = self._extract_cards(html, default_type=default_type)
        log("OnlyFlix: {} card(s) extracted".format(len(items)))

        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        return items

    # ── Public: search ─────────────────────────────────────────────────────
    def search(self, query, page=1):
        q = quote_plus(query)
        search_url = self._get_base().rstrip("/") + "/?s=" + q
        if page > 1:
            search_url += "&paged={}".format(page)

        log("OnlyFlix: search '{}'".format(query))
        html, _ = fetch(search_url, referer=self._get_base(),
                        extra_headers=_PROBE_HEADERS)
        if not html:
            return []

        items = self._extract_cards(html)
        if page == 1:
            nxt = self._parse_pagination(html, search_url)
            if nxt:
                items.append(nxt)
        log("OnlyFlix: search '{}' → {} items".format(query, len(items)))
        return items

    # ── Detail page meta ───────────────────────────────────────────────────
    def _extract_jsonld(self, html):
        """
        Detail pages embed a schema.org block with the richest metadata
        available (confirmed 2026-09-24):

          {"@type": "Movie", "name": "I Was a Stranger",
           "description": "Five strangers are pulled together...",
           "image": "https://m.media-amazon.com/...jpg",
           "datePublished": "2026-01-09", "genre": ["Drama", "Thriller"],
           "duration": "1h 43m",
           "identifier": {"propertyID": "IMDb", "value": "tt21272942"},
           "aggregateRating": {"ratingValue": "8", "ratingCount": "13405"},
           "director": {"name": "Brandt Andersen"}}

        Returns the first Movie/TVSeries/TVSeason/TVEpisode node, or {}.
        """
        best = {}
        for m in re.finditer(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html or "", re.S | re.I):
            raw = html_unescape((m.group(1) or "").strip())
            try:
                data = json.loads(raw)
            except Exception:
                continue
            nodes = data.get("@graph") if isinstance(data, dict) else None
            if nodes is None:
                nodes = [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                t = node.get("@type")
                if t in ("Movie", "TVSeries", "TVSeason", "TVEpisode"):
                    if not best:
                        best = node
        return best

    def _extract_detail_meta(self, html):
        ld = self._extract_jsonld(html)

        title = ""
        if ld.get("name"):
            title = self._clean_title(ld.get("name"))
        if not title:
            tm = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if tm:
                title = self._clean_title(tm.group(1))
        if not title:
            tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S | re.I)
            if tm:
                title = self._clean_title(tm.group(1))
        if not title:
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
            if tm:
                title = self._clean_title(tm.group(1).split("|")[0])

        poster = ""
        if ld.get("image"):
            poster = self._full_url(str(ld.get("image")))
        if not poster:
            pm = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if pm:
                poster = self._full_url(pm.group(1))

        plot = ""
        if ld.get("description"):
            plot = re.sub(r"<[^>]+>", " ",
                          str(ld.get("description"))).strip()
        if not plot:
            dm = re.search(
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if dm:
                plot = re.sub(r"<[^>]+>", " ", dm.group(1)).strip()

        year = ""
        dp = str(ld.get("datePublished") or "")
        ym = re.match(r"(\d{4})", dp)
        if ym:
            year = ym.group(1)
        if not year:
            ym = re.search(r'\b(19\d{2}|20\d{2})\b', title + " " + plot)
            if ym:
                year = ym.group(1)

        # Rating: JSON-LD aggregateRating first, card/hero fallbacks after
        rating = ""
        ar = ld.get("aggregateRating")
        if isinstance(ar, dict) and ar.get("ratingValue"):
            rating = str(ar.get("ratingValue"))
        if not rating:
            rm = re.search(
                r'class="[^"]*of-home-hero__rating[^"]*"[^>]*>.*?([\d.]+)',
                html, re.S | re.I)
            if not rm:
                rm = re.search(
                    r'class="[^"]*of-media-card__rating[^"]*"[^>]*>(.*?)</span>',
                    html, re.S | re.I)
                if rm:
                    rt = re.sub(r"[^\d.]", "", rm.group(1))
                    if rt:
                        rating = rt
            else:
                rating = rm.group(1)

        # Genres: JSON-LD genre[] first
        genres = ""
        g = ld.get("genre")
        if isinstance(g, list):
            genres = ", ".join(str(x) for x in g)
        elif g:
            genres = str(g)
        if not genres:
            gm = re.search(
                r'class="[^"]*of-media-card__genres[^"]*"[^>]*>(.*?)</span>',
                html, re.S | re.I)
            if gm:
                genres = self._clean_title(gm.group(1))

        runtime = ""
        if ld.get("duration"):
            runtime = str(ld.get("duration"))
        if not runtime:
            rtm = re.search(
                r'class="[^"]*of-media-card__runtime[^"]*"[^>]*>(.*?)</span>',
                html, re.S | re.I)
            if rtm:
                runtime = self._clean_title(rtm.group(1))

        quality = ""
        qm = re.search(
            r'class="[^"]*of-quality-badge[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
            html, re.S | re.I)
        if qm:
            quality = self._clean_title(qm.group(1))

        imdb_id = ""
        ident = ld.get("identifier")
        if isinstance(ident, dict) and ident.get("value"):
            imdb_id = str(ident.get("value"))
        elif isinstance(ident, str) and ident.startswith("tt"):
            imdb_id = ident

        return {
            "title": title,
            "poster": poster,
            "plot": plot,
            "year": year,
            "rating": rating,
            "genres": genres,
            "runtime": runtime,
            "quality": quality,
            "imdb_id": imdb_id,
        }

    # ── Episode data (TV series detail pages) ──────────────────────────────
    def _extract_tv_episodes_json(self, html):
        """
        TV series pages embed the full episode index inline (confirmed on
        /series/lanterns/, 2026-09-24):

          var allSeasonsData = {
            "1": {
              "1": {"id": 1547758, "title": "…Pilot",
                    "permalink": "…/episode-1-pilot/", "season": 1, "episode": 1},
              "6": {"id": 1548186, "title": "…Bad Optics",
                    "permalink": "…/episode-6-bad-optics/", "season": 1, "episode": 6}
            }
          };

        DOM equivalent: #episodes-container .episode-watch-link
        → https://onlyflix.to/episodes/<show>-season-S-episode-N-<slug>/
        """
        # [PATCH O3] "var" widened to var/const/let -- confirmed against a real capture
        # (Lanterns, 2026-09-24) that the page actually declares this with "const", which
        # the original var-only regex never matched, silently finding zero episodes.
        m = re.search(r'(?:var|const|let)\s+allSeasonsData\s*=\s*(\{.*?\})\s*;', html, re.S)
        if m:
            try:
                data = json.loads(html_unescape(m.group(1)))
            except Exception as e:
                log("OnlyFlix: allSeasonsData parse failed: {}".format(e))
                data = None
            rows = []
            if isinstance(data, dict):
                for s_key, eps in data.items():
                    if not isinstance(eps, dict):
                        continue
                    for e_key, ep in eps.items():
                        if not isinstance(ep, dict):
                            continue
                        url = self._full_url(ep.get("permalink") or "")
                        if not url:
                            continue
                        sn = ep.get("season") or s_key
                        en = ep.get("episode") or e_key
                        rows.append({
                            "title": self._clean_title(ep.get("title") or
                                        "S{}E{}".format(sn, en)),
                            "url": url,
                            "season": sn,
                            "episode": en,
                            "type": "episode",
                            "_action": "details",
                        })
                if rows:
                    log("OnlyFlix: {} episode(s) from allSeasonsData".format(
                        len(rows)))
                    return rows
        return self._extract_episode_links_dom(html)

    def _extract_episode_links_dom(self, html):
        rows, seen = [], set()
        for m in re.finditer(
                r'<a[^>]+class="[^"]*episode-watch-link[^"]*"[^>]+href="([^"]+)"',
                html, re.I):
            url = self._full_url(m.group(1))
            if not url or url in seen:
                continue
            seen.add(url)
            sm = re.search(r'season-(\d+)-episode-(\d+)', url, re.I)
            rows.append({
                "title": ("S{}E{}".format(sm.group(1), sm.group(2))
                          if sm else "Episode"),
                "url": url,
                "season": sm.group(1) if sm else "",
                "episode": sm.group(2) if sm else "",
                "type": "episode",
                "_action": "details",
            })
        return rows

    # ── Server extraction ──────────────────────────────────────────────────
    def _add_server(self, servers, seen, url, label=None, srv_type="embed"):
        if not url:
            return
        url = url.strip().replace("&amp;", "&").replace("\\/", "/")
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            return
        if any(x in url.lower() for x in self._NON_MEDIA_HOSTS):
            return
        low_path = url.lower().split("?", 1)[0]
        if low_path.endswith((".jpg", ".jpeg", ".png", ".gif",
                              ".webp", ".svg", ".css", ".js", ".ico")):
            return
        if url in seen:
            return
        seen.add(url)
        host_m = re.search(r'https?://([^/]+)', url)
        host = host_m.group(1) if host_m else ""
        name = label or self._host_display_name(host)
        servers.append({
            "name": "🎬 " + name,
            "url": url,
            "type": srv_type,
        })

    def _host_display_name(self, host):
        lowered = (host or "").lower()
        mapping = (
            ("streamwish", "StreamWish"), ("filemoon", "FileMoon"),
            ("dood", "DoodStream"), ("playmogo", "PlayMogo"),
            ("vidhide", "VidHide"), ("voe", "Voe"), ("mixdrop", "MixDrop"),
            ("luluvdo", "LuluStream"), ("lulustream", "LuluStream"),
            ("streamtape", "StreamTape"), ("mp4upload", "Mp4Upload"),
            ("vidmoly", "VidMoly"), ("savefiles", "SaveFiles"),
            ("mxcontent", "MxContent"), ("downet", "Downet"),
            ("ok.ru", "OK.ru"), ("okru", "OK.ru"),
            ("streamruby", "StreamRuby"), ("stmruby", "StreamRuby"),
            ("uqload", "UqLoad"), ("byselapuix", "Byse"),
            ("hgcloud", "HGCloud"), ("govid", "GoVid"),
            ("fastvid", "FastVid"), ("vidguard", "VidGuard"),
            ("vidaraa", "Vidaraa"), ("morencius", "Morencius"),
            ("audinifer", "Audinifer"), ("hanerix", "Hanerix"),
            ("vibuxer", "Vibuxer"), ("earnvids", "EarnVids"),
            ("cloudwindow", "Voe"), ("superflixapi", "SuperFlix"),
            ("zxcstream", "ZXC Stream"), ("vidsrc", "VidSrc"),
            ("vidcore", "VidCore"),
            # onlyflix server tabs (2026-09-24):
            ("vidfast", "VidFast"), ("cdnm.ink", "CDNM"),
            ("vidapi", "VidApi"), ("nontongo", "Nontongo"),
        )
        for frag, label in mapping:
            if frag in lowered:
                return label
        return host or "Server"

    def _extract_servers_from_html(self, html, page_url):
        """
        Real markup (verbatim, movie detail page, 2026-09-24):

          <div class="player-frame" data-player-ajax-url=".../admin-ajax.php"
               data-player-nonce="ee003eeb3f" data-player-post-id="1487813"
               data-player-content-type="movie" ...>
            <button class="player-poster"
                    data-player-url="https://vidfast.vc/movie/tt21272942">
            <iframe id="main-player" data-src="..."
                    src="https://vidfast.vc/movie/tt21272942">
          </div>
          <ul class="player-server-tabs">
            <li><button class="player-tab-btn"
                  data-player-url="https://share.cdnm.ink/embed/imdb/tt21272942">Server 1</button>
            <li><button class="player-tab-btn"
                  data-player-url="https://vidapi.xyz/embed/movie/tt21272942">Server 2</button>
            <li><button class="player-tab-btn"
                  data-player-url="https://sv2.nontongo.stream/soap/movie/tt21272942">Server 3</button>
            <li><button class="player-tab-btn active"
                  data-player-url="https://vidfast.vc/movie/tt21272942">Server 4</button>
          </ul>

        The tabs are client-side-injected from mcp_get_available_players
        on player-frame--async pages, so the raw html may only carry the
        poster button / iframe; get_page's AJAX fallback covers that.
        The embed URLs are opaque Next.js players — resolved by hosts.py's
        resolve_vidsrc_family, never fetched for an m3u8 here.
        """
        servers = []
        seen = set()
        if not html:
            return servers

        # 1. PRIMARY — server tab buttons, in site order
        for m in (list(re.finditer(
                r'<button[^>]+class="[^"]*player-tab-btn[^"]*"[^>]*\bdata-player-url="([^"]+)"[^>]*>(.*?)</button>',
                html, re.S | re.I)) +
                list(re.finditer(
                r'<button[^>]+\bdata-player-url="([^"]+)"[^>]*class="[^"]*player-tab-btn[^"]*"[^>]*>(.*?)</button>',
                html, re.S | re.I))):
            label = re.sub(r"\s+", " ",
                           re.sub(r"<[^>]+>", " ", m.group(2))).strip()
            self._add_server(servers, seen, m.group(1),
                             label=(label if label.lower().startswith("server")
                                    else None))

        # 2. poster button + main iframe (dedupes against the active tab)
        for m in re.finditer(r'<(?:button|iframe)[^>]+\bdata-player-url="([^"]+)"',
                             html, re.I):
            self._add_server(servers, seen, m.group(1))
        for m in re.finditer(r'<iframe[^>]+(?:data-src|src)="([^"]+)"',
                             html, re.I):
            self._add_server(servers, seen, m.group(1).strip())

        # 3. legacy: <source>/<video> tags
        for m in re.finditer(
                r'<(?:source|video)[^>]+src=["\']'
                r'([^"\']+\.(?:mp4|m3u8|mkv|txt)[^"\']*)["\']',
                html, re.I):
            self._add_server(servers, seen, _correct_stream_url(m.group(1)),
                             srv_type="direct")

        # 4. legacy: data-server-url / data-iframe / data-link / data-embed
        # [PATCH O1] "data-url" deliberately excluded -- confirmed against a real capture,
        # it's the exact same attribute the movie-card parser uses for a related-movie's
        # detail-page link, so every "related movies" card on the page was being picked up
        # here as a bogus "server" alongside the real ones.
        for attr in ("data-server-url", "data-src", "data-video",
                     "data-iframe", "data-player", "data-embed", "data-link"):
            for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                self._add_server(servers, seen, m.group(1).strip())

        # 5. known streaming hosts referenced anywhere on the page
        host_re = re.compile(
            r'(https?://(?:[^"\'\s<>]*(?:'
            + "|".join(self._MEDIA_HOST_MARKERS)
            + r'))[^"\'\s<>]*)', re.I)
        for m in host_re.finditer(html):
            self._add_server(servers, seen, m.group(1))

        # 6. direct media literals in inline JS
        for m in re.finditer(
                r'(?:file|src|url|source)\s*[:=]\s*["\']'
                r'([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            self._add_server(servers, seen,
                             _correct_stream_url(m.group(1).replace("\\/", "/")),
                             srv_type="direct")

        log("OnlyFlix: {} server(s) found directly on {}".format(
            len(servers), (page_url or "")[:80]))
        return servers

    # ── AJAX server discovery ──────────────────────────────────────────────
    def _page_ajax_context(self, html):
        """
        The player-frame carries the full AJAX contract (confirmed
        2026-09-24): data-player-ajax-url / data-player-nonce /
        data-player-post-id / data-player-content-type.
        Secondary source: the OnlyFlixAccounts JS object
        (<script id="onlyflix-accounts-js-extra">) — current.postId /
        current.type / playerNonce / playerAjaxUrl mirror the attributes.
        """
        if not html:
            return None

        def _attr(name):
            m = re.search(name + r'=["\']([^"\']+)["\']', html, re.I)
            return m.group(1) if m else ""

        ajax_url = _attr("data-player-ajax-url")
        nonce    = _attr("data-player-nonce")
        post_id  = _attr("data-player-post-id")
        ctype    = _attr("data-player-content-type")

        if not (ajax_url and nonce and post_id):
            m = re.search(
                r'<script[^>]*\bid=["\']onlyflix-accounts-js-extra["\'][^>]*>(.*?)</script>',
                html, re.S | re.I)
            if m:
                raw = m.group(1).split("//#", 1)[0].strip().rstrip(";")
                try:
                    cfg = json.loads(raw)
                except Exception:
                    cfg = None
                if isinstance(cfg, dict):
                    cur = cfg.get("current") or {}
                    ajax_url = ajax_url or cfg.get("playerAjaxUrl") or ""
                    nonce    = nonce or cfg.get("playerNonce") or ""
                    if not post_id:
                        pid = cur.get("postId") or self._post_id_from_html(html)
                        post_id = str(pid) if pid else ""
                    ctype    = ctype or cur.get("type") or ""

        if not (ajax_url and nonce and post_id):
            return None
        return {
            "ajax_url": ajax_url,
            "nonce":    nonce,
            "post_id":  str(post_id),
            "type":     ctype or "movie",
        }

    def _post_id_from_html(self, html):
        m = re.search(r'\bpostid-(\d+)\b', html or "")
        if not m:
            m = re.search(
                r'rel=["\']shortlink["\'][^>]*href=["\'][^"\']*\?p=(\d+)',
                html or "", re.I)
        return int(m.group(1)) if m else 0

    def _try_ajax_servers(self, context, page_url=""):
        """
        Confirmed contract (Network capture, 2026-09-24):

          POST <ajax_url>   application/x-www-form-urlencoded
          action=mcp_get_available_players
          nonce=<data-player-nonce>
          post_id=<movie|episode post id>      (never the series id)
          type=movie|episode

        Server switching itself is a pure iframe.src swap (no XHR on
        click). The response body hasn't been captured; the site's JS
        builds .player-tab-btn buttons from it, so scan for
        data-player-url plus the generic JSON shapes.
        """
        if not context:
            return []
        payload = {
            "action":  self._SERVER_AJAX_ACTION,
            "nonce":   context["nonce"],
            "post_id": context["post_id"],
            "type":    context.get("type") or "movie",
        }
        try:
            body, _ = fetch(
                context["ajax_url"],
                referer=page_url or context["ajax_url"],
                extra_headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                post_data=payload,
            )
        except Exception as e:
            log("OnlyFlix: {} raised: {}".format(
                self._SERVER_AJAX_ACTION, e))
            return []
        if not body:
            return []
        urls = [m.group(1) for m in re.finditer(
            r'data-player-url="([^"]+)"', body, re.I)]
        urls.extend(self._urls_from_ajax_body(body))
        return urls

    def _urls_from_ajax_body(self, body):
        """Extract every playable/embeddable URL from an AJAX response."""
        urls = []
        seen = set()

        # JSON body
        data = None
        try:
            data = json.loads(body)
        except Exception:
            data = None
        if isinstance(data, dict):
            # Common shapes: {html: "..."}  {data: {...}}  {server: "..."}
            for key in ("html", "data", "server", "url", "iframe", "servers"):
                v = data.get(key)
                if isinstance(v, str):
                    urls.extend(self._urls_from_html_string(v))
                elif isinstance(v, dict):
                    for kk in ("url", "server", "iframe", "src", "link"):
                        vv = v.get(kk)
                        if isinstance(vv, str):
                            urls.append(vv)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            for kk in ("url", "server", "iframe", "src",
                                       "link", "file"):
                                vv = item.get(kk)
                                if isinstance(vv, str):
                                    urls.append(vv)
                        elif isinstance(item, str):
                            urls.append(item)
        else:
            urls.extend(self._urls_from_html_string(body))

        # De-dup, normalise, filter
        out = []
        for u in urls:
            u = (u or "").strip().replace("&amp;", "&").replace("\\/", "/")
            if u.startswith("//"):
                u = "https:" + u
            if not u.startswith("http"):
                continue
            if any(x in u.lower() for x in self._NON_MEDIA_HOSTS):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out

    def _urls_from_html_string(self, html):
        urls = []
        # iframes
        for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
            urls.append(m.group(1))
        # data-* attributes (data-player-url first: the tab buttons)
        for attr in ("data-player-url", "data-server-url", "data-url",
                     "data-link", "data-src", "data-iframe", "data-embed"):
            for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                urls.append(m.group(1))
        # Known hosts
        host_re = re.compile(
            r'(https?://(?:[^"\'\s<>]*(?:'
            + "|".join(self._MEDIA_HOST_MARKERS)
            + r'))[^"\'\s<>]*)', re.I)
        for m in host_re.finditer(html):
            urls.append(m.group(1))
        return urls

    # ── Download links ────────────────────────────────────────────────────
    def _extract_download_links(self, html):
        """
        Best-effort download extraction. Confirmed 2026-09-24: this site
        has NO download UI — the method is kept for future markup and
        only invoked when the page actually mentions "download".
        """
        downloads = []
        seen = set()
        if not html:
            return downloads

        block = None
        for pat in (
            r'<ul[^>]*class="[^"]*(?:download-items|donwload-servers-list|List--Download)[^"]*"[^>]*>(.*?)</ul>',
            r'<div[^>]*class="[^"]*downloadMaster[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*(?:Download--|download--|downloads?)[^"]*"[^>]*>(.*?)</div>',
        ):
            m = re.search(pat, html, re.S | re.I)
            if m:
                block = m.group(1)
                break

        scan = block if block is not None else html
        for m in re.finditer(
                r'<(?:a|li)[^>]*?(?:href|data-href|data-url)=["\']([^"\']+)["\'][^>]*>(.*?)</(?:a|li)>',
                scan, re.S | re.I):
            href = m.group(1).strip().replace("&amp;", "&")
            if href.startswith("//"):
                href = "https:" + href
            if not href.startswith("http") or href in seen:
                continue
            if any(x in href.lower() for x in self._NON_MEDIA_HOSTS):
                continue
            # Only accept known file hosts when deep-scanning the whole page.
            if block is None:
                host = (urlparse(href).netloc or "").lower()
                if not any(frag in host for frag in (
                        "savefiles", "dood", "mixdrop", "luluvdo",
                        "downet", "mxcontent", "voe", "streamtape",
                        "filemoon", "vidhide", "streamwish", "hgcloud",
                        "govid", "fastvid", "vidguard", "uqload",
                        "streamruby", "stmruby", "byselapuix",
                        "superflixapi", "zxcstream", "vidcore")):
                    continue
            seen.add(href)
            inner = m.group(2)
            res_m = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b',
                              inner, re.I)
            size_m = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b',
                               inner, re.I)
            name_m = re.search(r'<span[^>]*>([^<]+)</span>', inner, re.I)
            downloads.append({
                "resolution": res_m.group(1) if res_m else "",
                "size":       size_m.group(1) if size_m else "",
                "quality":    self._clean_title(name_m.group(1)) if name_m else "",
                "url":        href,
            })

        return downloads

    # ── Synthesized servers (last-resort safety net) ───────────────────────
    def _synthesized_servers(self, imdb_id, item_type, season=None, episode=None):
        """
        All four providers key off the IMDb id with fixed URL shapes
        (confirmed across movie + episode captures, 2026-09-24). Used
        only when neither the DOM tabs nor mcp_get_available_players
        yield servers. nontongo TLD rotates (.stream / .day).
        """
        if not imdb_id:
            return []
        imdb_id = str(imdb_id).strip()
        if item_type in ("episode", "series") and season and episode:
            return [
                "https://share.cdnm.ink/embed/imdb/{}?season={}&episode={}".format(
                    imdb_id, season, episode),
                "https://vidapi.xyz/embed/tv/{}/{}/{}".format(
                    imdb_id, season, episode),
                "https://sv2.nontongo.stream/soap/tv/{}/{}/{}".format(
                    imdb_id, season, episode),
                "https://vidfast.vc/tv/{}/{}/{}".format(
                    imdb_id, season, episode),
            ]
        if item_type == "series":
            return []          # series has no direct embed — pick an episode
        return [
            "https://share.cdnm.ink/embed/imdb/{}".format(imdb_id),
            "https://vidapi.xyz/embed/movie/{}".format(imdb_id),
            "https://sv2.nontongo.stream/soap/movie/{}".format(imdb_id),
            "https://vidfast.vc/movie/{}".format(imdb_id),
        ]

    def _imdb_id_from_html(self, html):
        m = re.search(r'\b(tt\d{7,8})\b', html or "")
        return m.group(1) if m else ""

    # ── get_page ──────────────────────────────────────────────────────────
    def get_page(self, url, m_type=None):
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
        html, final_url = fetch(self._full_url(url),
                                referer=self._get_base(),
                                extra_headers=_PROBE_HEADERS)
        if not html:
            log("OnlyFlix: get_page failed for {}".format(url))
            return result

        ld = self._extract_jsonld(html)
        meta = self._extract_detail_meta(html)
        result.update({
            "title":   meta.get("title") or "",
            "poster":  meta.get("poster") or "",
            "plot":    meta.get("plot") or "",
            "year":    meta.get("year") or "",
            "rating":  meta.get("rating") or "",
            "genres":  meta.get("genres") or "",
            "runtime": meta.get("runtime") or "",
            "quality": meta.get("quality") or "",
            "imdb_id": meta.get("imdb_id") or "",
        })
        result["url"] = final_url or url

        # ── Type detection — authoritative sources first ───────────────
        ctype = ""
        cm = re.search(r'data-player-content-type="([^"]+)"', html, re.I)
        if cm:
            ctype = cm.group(1).lower()
        ld_type = (ld.get("@type") or "").lower()
        url_low = (final_url or url).lower()
        # [PATCH O2] ctype == "episode" removed from this check -- confirmed against a
        # real capture that data-player-content-type reflects the embedded "play episode"
        # player widget, which is always "episode" on ANY TV show page (series overview
        # included, since that page also shows a "play episode 1" widget), not the page's
        # own type. The URL-based /episodes/ check alone is a reliable signal for a real,
        # dedicated episode page.
        if "/episodes/" in url_low:
            result["type"] = "episode"
        elif ("single-tvshows" in html
              or ctype in ("tvshow", "tvshows")
              or ld_type == "tvseries"
              or "/series/" in url_low):
            result["type"] = "series"
        elif ctype == "movie" or ld_type == "movie":
            result["type"] = "movie"

        # ── Season/episode (episode pages) ────────────────────────────
        season = episode = ""
        sm = re.search(r'season-(\d+)-episode-(\d+)', url_low)
        if sm:
            season, episode = sm.group(1), sm.group(2)
        else:
            qm = re.search(r'[?&]season=(\d+)', url_low)
            em = re.search(r'[?&]episode=(\d+)', url_low)
            season = qm.group(1) if qm else ""
            episode = em.group(1) if em else ""

        # ── Servers: DOM tabs → mcp AJAX → synthesized ─────────────────
        servers = self._extract_servers_from_html(html, final_url or url)

        if not servers:
            ctx = self._page_ajax_context(html)
            if ctx:
                log("OnlyFlix: no server tabs in raw html, calling {} "
                    "(post_id={})".format(self._SERVER_AJAX_ACTION,
                                          ctx.get("post_id")))
                seen = set()
                for u in self._try_ajax_servers(ctx, final_url or url):
                    self._add_server(servers, seen, u)

        if not servers:
            imdb_id = result.get("imdb_id") or self._imdb_id_from_html(html)
            if imdb_id:
                log("OnlyFlix: synthesizing servers for {} ({})".format(
                    imdb_id, result["type"]))
                seen = set()
                for u in self._synthesized_servers(imdb_id, result["type"],
                                                   season, episode):
                    self._add_server(servers, seen, u)

        # ── Episodes (series pages) ────────────────────────────────────
        if result["type"] == "series":
            eps = self._extract_tv_episodes_json(html)
            if eps:
                result["items"] = eps

        # ── Downloads (confirmed absent on this site — gated) ──────────
        if "download" in (html or "").lower():
            result["downloads"] = self._extract_download_links(html)

        result["servers"] = servers

        log("OnlyFlix: get_page {} → servers={}, items={}, downloads={}, "
            "type={}".format(
                (url or "")[:60], len(result["servers"]),
                len(result["items"]), len(result["downloads"]),
                result["type"]))
        return result

    # ── Stream resolution ──────────────────────────────────────────────────
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

        if ".m3u8" in low:
            q = self._quality_from_url(url)
            return url, q, self._get_base(), _variants_for(url)
        if ".mp4" in low:
            return url, self._quality_from_url(url), self._get_base(), []

        if base_extract_stream is not None:
            try:
                result = base_extract_stream(url)
            except Exception as e:
                log("OnlyFlix: generic resolver failed for {}: {}".format(
                    url[:80], e))
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