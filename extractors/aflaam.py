# -*- coding: utf-8 -*-
"""
Aflaam extractor — aflaam.com
==============================
PHP-Laravel streaming site. Confirmed against real captures (2026-09-23):

  Homepage / listing  /movies, /series
  Filters             ?section=1|2|3  (Arabic/Foreign/Indian)
                      ?category=N     (genre ID)
                      ?year=YYYY
                      ?rating=N
                      ?quality=240p|480p|720p|1080p|3D|4K
  Pagination          ?page=N
  Search              /search?q=<query>
  Movie detail        /movie/<id>/<slug>
  Series detail       /series/<id>/<slug>
  Download page       /download/<vid_id>/<movie_id>/<slug>

Listing card markup (verbatim, /movies?page=2):

  <div class="col-lg-auto ... item">
    <div class="entry-box" style="margin-bottom: 12px;">
      <div class="actions d-flex">
        <a href="https://aflaam.com/movie/3069/batman-returns" class="icn size-1"><i class="icon-play"></i></a>
      </div>
      <a href="https://aflaam.com/movie/3069/batman-returns" class="box">
        <span class="label rating"><i class="icon-star mr-2"></i>7.1</span>
        <span class="label quality">Blu-Ray</span>
        <div class="entry-image">
          <svg>...</svg>
          <picture>
            <img src="https://images.aflaam.com/thumb/212x300/uploads/uHyZu.jpeg"
                 class="img-fluid w-100 lazy" alt="Batman Returns">
          </picture>
        </div>
        <div class="entry-body px-3 pb-3">
          <h3 class="entry-title text-center text-white font-size-16 m-0">Batman Returns</h3>
        </div>
      </a>
    </div>
  </div>

Series listing uses the same shape but href → /series/<id>/<slug>.

Pagination (real markup, /movies?page=2):

  <ul class="pagination justify-content-center" role="navigation">
    <li class="page-item mx-1 active" aria-current="page"><span class="page-link">2</span></li>
    <li class="page-item mx-1"><a class="page-link" href="https://aflaam.com/movies?page=3">3</a></li>
    ...
    <li class="page-item mx-1"><a class="page-link" href="https://aflaam.com/movies?page=3" rel="next">›</a></li>
  </ul>

Download page (/download/16454/3069/batman-returns):

  <div class="loader-container">
    <a href="javascript:;" download="" class="link download"></a>
    ...
  </div>
  <p class="file-name" style="display: none">
    <a href="javascript:;" class="download"
       download="https://af3.downet.net/download/1790247017/6ab3aee99b782/Batman.Returns.1992.480p.BluRay.aflaam.com.mp4">…</a>
  </p>
  <script>
    if ($('a.download').length) {
      setTimeout(function () {
        $('.file-name').show();
        $('a.download').attr('href', 'https://af3.downet.net/…/…mp4');
      }, 2200);
    }
  </script>

The real file URL is present in TWO places in the raw HTML — the
`download="…"` attribute of the anchor AND the `attr('href', '…')`
call inside the inline script. Either is enough; the "reveal" is purely
client-side JS, no extra request needed.
"""

import re
import time
import threading

from .base import BaseExtractor, fetch, log, urljoin, _correct_stream_url

try:
    from urllib.parse import quote, urlparse, parse_qs, urlencode
except ImportError:                              # py2 shim
    from urllib import quote, urlencode
    from urlparse import urlparse, parse_qs


# ─── Process-wide base cache ────────────────────────────────────────────────
_BASE_CACHE_TTL = 300
_PROBE_COOLDOWN = 30
_base_cache      = {"url": None, "resolved_at": 0, "probed_at": 0}
_base_cache_lock = threading.Lock()
_probe_lock      = threading.Lock()

_PROBE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class AflaamExtractor(BaseExtractor):
    """Extractor for Aflaam (aflaam.com)."""

    MAIN_URL = "https://aflaam.com/"

    DOMAINS = [
        "https://aflaam.com/",
        "https://www.aflaam.com/",
    ]

    VALID_HOST_MARKERS = ("aflaam.com",)
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)

    # Third-party scripts/CDNs and social embeds we never want as servers.
    _NON_MEDIA_HOSTS = (
        "facebook.com", "twitter.com", "instagram.com", "youtube.com/embed",
        "google.com", "t.me", "telegram.me", "whatsapp.com",
        "googletagmanager", "googlesyndication", "doubleclick",
        "google-analytics", "cloudflareinsights",
        "histats.com", "al5sm.com", "quge5.com", "pulseadnetwork.com",
        "googleplay.com",
    )

    # [PATCH A2] account/UI path segments on the site's own domain that the generic
    # data-* attribute scan can pick up (confirmed: data-src="https://aflaam.com/login"
    # on the header's login icon) -- these are never real streaming servers.
    _NON_MEDIA_PATHS = (
        "/login", "/logout", "/register", "/favorite", "/signup",
    )

    # Known host fragments that only appear on watch/download pages.
    _MEDIA_HOST_MARKERS = (
        "streamwish", "filemoon", "dood", "playmogo", "vidhide", "voe",
        "mixdrop", "luluvdo", "lulustream", "streamtape", "mp4upload",
        "vidmoly", "savefiles", "mxcontent", "downet", "ok.ru", "okru",
        "streamruby", "stmruby", "uqload", "upstream", "vidguard",
        "vidaraa", "morencius", "audinifer", "hanerix", "vibuxer",
        "earnvids", "cloudwindow", "fastvid", "govid", "byseraguci",
        "byselapuix", "hgcloud",
    )

    # ── Genre catalogue (IDs verified from the site's own filter menu) ─────
    _GENRES = [
        (18, "اكشن"),
        (19, "مغامرة"),
        (20, "كوميدي"),
        (21, "جريمة"),
        (22, "رعب"),
        (23, "دراما"),
        (24, "خيال علمي"),
        (25, "حربي"),
        (26, "تاريخي"),
        (27, "رومانسي"),
        (28, "وثائقي"),
        (29, "سيرة ذاتية"),
        (30, "انمي"),
        (31, "موسيقى"),
        (32, "رياضي"),
        (33, "عائلي"),
        (34, "غموض"),
        (35, "اثارة"),
    ]

    _QUALITY_FILTERS = ["4K", "1080p", "720p", "480p", "240p"]

    # ── Construction / base probe ──────────────────────────────────────────
    def __init__(self):
        super(AflaamExtractor, self).__init__()
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

    def _looks_like_aflaam_page(self, html):
        text = html or ""
        return (
            "aflaam" in text.lower()
            or "entry-box" in text
            or "widget-1 widget widget-style-1" in text
            or "page-archive" in text
            or "main-header" in text and "main-menu" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        """Confirm the candidate host actually serves listing pages, not
        just a homepage (same gate as egydead/mywecima)."""
        if not base:
            return False
        test_url = urljoin(base, "movies")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("Aflaam: deep-content probe failed for {}: {}".format(base, e))
            return False
        if not html or self._is_blocked_page(html, final or ""):
            log("Aflaam: deep-content probe empty/blocked for {}".format(base))
            return False
        if html.count("entry-box") < 6:
            log("Aflaam: {} answered but only {} entry-box markers — "
                "not a real listing".format(base, html.count("entry-box")))
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
                log("Aflaam: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain,
                                        extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain
                if not self._is_valid_site_url(final_url):
                    continue
                if self._is_blocked_page(html, final_url):
                    continue
                if not (html and self._looks_like_aflaam_page(html)):
                    continue
                candidate = self._site_root(final_url)
                if not self._base_serves_deep_content(candidate):
                    continue
                self._resolved_base = candidate
                self.main_url = candidate
                with _base_cache_lock:
                    _base_cache["url"] = candidate
                    _base_cache["resolved_at"] = now
                    _base_cache["probed_at"] = now
                log("Aflaam: selected base {}".format(candidate))
                return candidate

            self._resolved_base = self.MAIN_URL
            self.main_url = self.MAIN_URL
            with _base_cache_lock:
                _base_cache["url"] = self.MAIN_URL
                _base_cache["probed_at"] = now
            log("Aflaam: all probes failed, using {}".format(self.MAIN_URL))
            return self._resolved_base

    # ── URL & title helpers ────────────────────────────────────────────────
    def _full_url(self, path):
        if not path:
            return ""
        path = str(path).strip().replace("&amp;", "&")
        if path.startswith("//"):
            path = "https:" + path
        if path.startswith("http"):
            return path
        return urljoin(self._get_base(), path)

    def _clean_title(self, title):
        if not title:
            return ""
        text = str(title)
        text = text.replace("&amp;", "&").replace("&#39;", "'")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Strip the site's trailing "| افلام" suffix when it's inside a title
        text = re.sub(r"\s*\|\s*افلام\s*$", "", text).strip()
        return text

    def _encode_query(self, query):
        try:
            return quote(str(query or ""), safe="")
        except Exception:
            return str(query or "")

    # ── Card parser (works for /movies, /series, search results) ───────────
    def _extract_cards(self, html, max_items=200):
        """
        Real markup (verbatim, /movies?page=2):

          <div class="col-lg-auto col-md-3 col-sm-4 col-6 item">
            <div class="entry-box" style="margin-bottom: 12px;">
              <div class="actions d-flex">
                <a href="..." class="icn size-1"><i class="icon-play"></i></a>
              </div>
              <a href="..." class="box">
                <span class="label rating"><i class="icon-star mr-2"></i>7.1</span>
                <span class="label quality">Blu-Ray</span>
                <div class="entry-image">
                  <svg>...</svg>
                  <picture>
                    <img src="..." class="img-fluid w-100 lazy" alt="...">
                  </picture>
                </div>
                <div class="entry-body px-3 pb-3">
                  <h3 class="entry-title ...">Batman Returns</h3>
                </div>
              </a>
            </div>
          </div>

        Some cards carry `data-src="..."` on the <img> instead of `src`
        (lazy-loading variants) — we check both.
        """
        items = []
        seen_urls = set()
        if not html:
            return items

        # Split by the outer <li>-equivalent wrapper. Aflaam uses a <div>
        # per card, but the outer element is unique enough: the combo
        # `entry-box` + a `box` anchor.
        for block in re.split(r'(?=<div class="col-[^"]*\bitem\b[^"]*")',
                              html):
            if "entry-box" not in block:
                continue
            # Cut the block at the next card so we don't accidentally grab
            # data from neighbours when the splitter's lookahead fires.
            next_card = re.search(r'<div class="col-[^"]*\bitem\b[^"]*"',
                                  block[1:])
            if next_card:
                block = block[:next_card.start() + 1]

            # ── URL — prefer the .box anchor, fall back to .icn ───────────
            url_m = (re.search(r'<a[^>]+class="[^"]*\bbox\b[^"]*"[^>]+href="([^"]+)"',
                               block, re.I) or
                     re.search(r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*\bbox\b',
                               block, re.I) or
                     re.search(r'<a[^>]+class="[^"]*\bicn\b[^"]*"[^>]+href="([^"]+)"',
                               block, re.I))
            if not url_m:
                continue
            url = self._full_url(url_m.group(1))
            if not url or url in seen_urls:
                continue
            # Skip nav-style links that slipped through
            path_only = re.sub(r'^https?://[^/]+', '', url)
            if not re.search(r'/(movie|series|download)/', path_only, re.I):
                continue
            seen_urls.add(url)

            # ── Title — prefer <img alt>, then .entry-title text ─────────
            title = ""
            alt_m = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
            if alt_m:
                title = alt_m.group(1)
            if not title:
                h3_m = re.search(
                    r'<h3[^>]*class="[^"]*\bentry-title\b[^"]*"[^>]*>(.*?)</h3>',
                    block, re.S | re.I)
                if h3_m:
                    title = h3_m.group(1)
            title = self._clean_title(title)
            if not title:
                continue

            # ── Poster — src or data-src, ignore placeholders ────────────
            poster = ""
            for pat in (
                r'<img[^>]+data-src="([^"]+)"[^>]*class="[^"]*\blazy\b',
                r'<img[^>]+class="[^"]*\blazy\b[^"]*"[^>]+data-src="([^"]+)"',
                r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*\blazy\b',
                r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*img-fluid[^"]*"',
                r'<img[^>]+src="([^"]+)"',
            ):
                m = re.search(pat, block, re.I)
                if m:
                    cand = m.group(1)
                    if not any(x in cand.lower() for x in
                               ("placeholder", "loading.gif", "data:image",
                                "/logo")):
                        poster = self._full_url(cand)
                        break

            # ── Rating ───────────────────────────────────────────────────
            rating = ""
            r_m = re.search(
                r'<span[^>]+class="[^"]*\brating\b[^"]*"[^>]*>(.*?)</span>',
                block, re.S | re.I)
            if r_m:
                rating = re.sub(r"<[^>]+>", " ", r_m.group(1)).strip()
                rating = re.sub(r"[^\d.]", "", rating)

            # ── Quality / release label ──────────────────────────────────
            label = ""
            q_m = re.search(
                r'<span[^>]+class="[^"]*\bquality\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.I)
            if q_m:
                label = q_m.group(1).strip()

            # ── Type from URL prefix ─────────────────────────────────────
            if "/series/" in url.lower():
                item_type = "series"
            elif "/download/" in url.lower():
                item_type = "movie"
            elif "/movie/" in url.lower():
                item_type = "movie"
            elif "مسلسل" in title:
                item_type = "series"
            else:
                item_type = "movie"

            # ── Year (own field for the grid badge, matches egydead) ─────
            year = ""
            ym = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            if ym:
                year = ym.group(1)

            items.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": label,
                "label": label,
                "rating": rating,
                "year": year,
                "type": item_type,
                "_action": "details",
            })
            if len(items) >= max_items:
                break

        return items

    # ── Pagination ─────────────────────────────────────────────────────────
    def _parse_pagination(self, html, current_url):
        """
        Real markup (verbatim):

          <ul class="pagination justify-content-center" role="navigation">
            <li class="page-item mx-1 active" aria-current="page"><span class="page-link">2</span></li>
            <li class="page-item mx-1"><a class="page-link" href="https://aflaam.com/movies?page=3">3</a></li>
            ...
            <li class="page-item mx-1"><a class="page-link" href="https://aflaam.com/movies?page=3" rel="next">›</a></li>
          </ul>
        """
        pag_m = re.search(
            r'<ul[^>]+class="[^"]*pagination[^"]*"[^>]*>(.*?)</ul>',
            html or "", re.S | re.I)
        if not pag_m:
            return None
        pag_html = pag_m.group(1)

        # Prefer explicit rel="next"
        next_m = re.search(
            r'<a[^>]+href="([^"]+)"[^>]*\brel="next"', pag_html, re.I)
        if not next_m:
            next_m = re.search(
                r'<a[^>]+\brel="next"[^>]+href="([^"]+)"', pag_html, re.I)

        # Fallback: numeric link for current+1
        if not next_m:
            m = re.search(r'[?&]page=(\d+)', current_url or "")
            cur = int(m.group(1)) if m else 1
            want = cur + 1
            num_m = re.search(
                r'<a[^>]+href="([^"]*[?&]page={}\b[^"]*)"'.format(want),
                pag_html, re.I)
            if num_m:
                next_m = num_m

        if not next_m:
            return None
        next_url = self._full_url(next_m.group(1).replace("&amp;", "&"))
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
        """Movies / series top-level plus section and genre filters."""
        self._get_base()
        base = self._get_base().rstrip("/")
        cats = []

        # Movies
        cats.append({"title": "🎬 أحدث الأفلام",
                     "url": base + "/movies",
                     "type": "category", "_action": "category"})
        cats.append({"title": "🇪🇬 أفلام عربي",
                     "url": base + "/movies?section=1",
                     "type": "category", "_action": "category"})
        cats.append({"title": "🌍 أفلام أجنبي",
                     "url": base + "/movies?section=2",
                     "type": "category", "_action": "category"})
        cats.append({"title": "🇮🇳 أفلام هندي",
                     "url": base + "/movies?section=3",
                     "type": "category", "_action": "category"})
        for gid, gname in self._GENRES:
            cats.append({"title": "🎬 " + gname,
                         "url": base + "/movies?category={}".format(gid),
                         "type": "category", "_action": "category"})

        # Series
        cats.append({"title": "📺 أحدث المسلسلات",
                     "url": base + "/series",
                     "type": "category", "_action": "category"})
        cats.append({"title": "🇪🇬 مسلسلات عربي",
                     "url": base + "/series?section=1",
                     "type": "category", "_action": "category"})
        cats.append({"title": "🌍 مسلسلات أجنبي",
                     "url": base + "/series?section=2",
                     "type": "category", "_action": "category"})
        cats.append({"title": "🇮🇳 مسلسلات هندي",
                     "url": base + "/series?section=3",
                     "type": "category", "_action": "category"})
        for gid, gname in self._GENRES:
            cats.append({"title": "📺 " + gname,
                         "url": base + "/series?category={}".format(gid),
                         "type": "category", "_action": "category"})

        # Quality filters
        for q in self._QUALITY_FILTERS:
            cats.append({"title": "🎞️ أفلام {}".format(q),
                         "url": base + "/movies?quality=" + q,
                         "type": "category", "_action": "category"})

        return cats

    # ── Public: category / listing ─────────────────────────────────────────
    def get_category_items(self, url, page=1):
        """
        Aflaam uses `?page=N` (query param, not path segment). If the
        caller passes page>1 with an un-paginated URL we splice it in.
        """
        fetch_url = self._full_url(url)

        if page and page > 1:
            parsed = urlparse(fetch_url)
            q = parse_qs(parsed.query or "", keep_blank_values=True)
            q["page"] = [str(page)]
            fetch_url = parsed._replace(
                query=urlencode([(k, v[0]) for k, v in q.items()])
            ).geturl()

        log("Aflaam: fetching listing {}".format(fetch_url))
        html, final_url = fetch(fetch_url, referer=self._get_base(),
                                extra_headers=_PROBE_HEADERS)
        if not html:
            log("Aflaam: get_category_items failed for {}".format(fetch_url))
            return []

        items = self._extract_cards(html)
        log("Aflaam: {} card(s) extracted".format(len(items)))

        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        return items

    # ── Public: search ─────────────────────────────────────────────────────
    def search(self, query, page=1):
        q = self._encode_query(query)
        search_url = self._full_url("search?q=" + q)
        if page and page > 1:
            search_url += "&page=" + str(page)

        log("Aflaam: search '{}'".format(query))
        html, _ = fetch(search_url, referer=self._get_base(),
                        extra_headers=_PROBE_HEADERS)
        if not html:
            return []

        items = self._extract_cards(html)
        if page == 1:
            nxt = self._parse_pagination(html, search_url)
            if nxt:
                items.append(nxt)
        log("Aflaam: search '{}' → {} items".format(query, len(items)))
        return items

    # ── Detail page ────────────────────────────────────────────────────────
    def _extract_detail_meta(self, html):
        title = ""
        title_m = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if title_m:
            title = self._clean_title(title_m.group(1))
        if not title:
            # Prefer the H1 that carries the actual title on detail pages.
            h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S | re.I)
            if h1_m:
                title = self._clean_title(h1_m.group(1))
        if not title:
            t_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
            if t_m:
                title = self._clean_title(t_m.group(1).split("|")[0])
        # Strip "تحميل فيلم " / "مشاهدة " prefixes the H1 sometimes carries.
        title = re.sub(r'^(?:تحميل|مشاهدة)\s+(?:فيلم|مسلسل)?\s*', "",
                       title).strip()

        poster = ""
        p_m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if p_m:
            poster = self._full_url(p_m.group(1))
        if not poster:
            img_m = re.search(
                r'<div[^>]+class="[^"]*\bentry-image\b[^"]*"[^>]*>.*?'
                r'<img[^>]+src="([^"]+)"', html, re.S | re.I)
            if img_m:
                poster = self._full_url(img_m.group(1))

        plot = ""
        d_m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if d_m:
            plot = re.sub(r"<[^>]+>", " ", d_m.group(1)).strip()

        year = ""
        ym = re.search(r'\b(19\d{2}|20\d{2})\b', title + " " + plot)
        if ym:
            year = ym.group(1)

        return title, poster, plot, year

    # ── Servers (embeds/iframes/direct media) ──────────────────────────────
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
        if any(x in url.lower() for x in self._NON_MEDIA_PATHS):   # [PATCH A2]
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
            ("uqload", "UqLoad"), ("upstream", "UpStream"),
            ("vidguard", "VidGuard"), ("vidaraa", "Vidaraa"),
            ("morencius", "Morencius"), ("audinifer", "Audinifer"),
            ("hanerix", "Hanerix"), ("vibuxer", "Vibuxer"),
            ("earnvids", "EarnVids"), ("cloudwindow", "Voe"),
            ("fastvid", "FastVid"), ("govid", "GoVid"),
            ("byseraguci", "Byse"), ("byselapuix", "Byse"),
            ("hgcloud", "HGCloud"),
        )
        for frag, label in mapping:
            if frag in lowered:
                return label
        return host or "Server"

    def _extract_watch_servers(self, html, page_url):
        """
        Defensive extraction — the detail-page markup wasn't in the
        snapshots, so we try every plausible shape in order and de-dupe:
          (1) iframe src
          (2) video/source tags
          (3) data-* attributes
          (4) known-host URLs anywhere on the page
          (5) direct .mp4/.m3u8 literals in inline JS
        """
        servers = []
        seen = set()
        if not html:
            return servers

        # [PATCH A1] real /watch/<vid>/<movie>/<slug> links -- highest confidence,
        # confirmed against a real capture. Sits right next to the /download/ link
        # that already works, using the same href-based matching.
        for m in re.finditer(
                r'<a[^>]+href=["\']([^"\']*/watch/[^"\']+)["\']',
                html, re.I):
            self._add_server(servers, seen, self._full_url(m.group(1)))

        # (1) iframes — highest confidence
        for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']',
                             html, re.I):
            self._add_server(servers, seen, m.group(1).strip())

        # (2) <video>/<source> tags
        for m in re.finditer(
                r'<(?:source|video)[^>]+src=["\']'
                r'([^"\']+\.(?:mp4|m3u8|mkv|txt)[^"\']*)["\']',
                html, re.I):
            self._add_server(servers, seen, _correct_stream_url(m.group(1)),
                             srv_type="direct")

        # (3) data-* attributes
        for attr in ("data-src", "data-url", "data-video", "data-iframe",
                     "data-player", "data-embed", "data-link"):
            for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                self._add_server(servers, seen, m.group(1))

        # (4) known streaming hosts referenced anywhere
        host_re = re.compile(
            r'(https?://(?:[^"\'\s<>]*(?:'
            + "|".join(self._MEDIA_HOST_MARKERS)
            + r'))[^"\'\s<>]*)', re.I)
        for m in host_re.finditer(html):
            self._add_server(servers, seen, m.group(1))

        # (5) direct media literals in inline JS
        for m in re.finditer(
                r'(?:file|src|url|source)\s*[:=]\s*["\']'
                r'([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            self._add_server(servers, seen,
                             _correct_stream_url(m.group(1).replace("\\/", "/")),
                             srv_type="direct")

        log("Aflaam: {} server(s) found on {}".format(
            len(servers), (page_url or "")[:80]))
        return servers

    # ── Download-page URL discovery (from the movie/series detail page) ───
    def _extract_download_page_links(self, html):
        """
        On a detail page the site links each available download to its own
        `/download/<vid_id>/<movie_id>/<slug>` page. We don't have that
        page's markup in the snapshots, so:
          1. find every <a href=".../download/...">…</a>
          2. also scan for the same path inside inline JS strings
        The label comes from the anchor's inner text if it isn't empty,
        else from any "1080p" / "720p" / etc. token in the URL or the
        surrounding text.
        """
        downloads = []
        seen = set()
        if not html:
            return downloads

        # (1) real anchors
        for m in re.finditer(
                r'<a[^>]+href=["\']([^"\']*/download/[^"\']+)["\'][^>]*>(.*?)</a>',
                html, re.S | re.I):
            raw_url = m.group(1)
            inner = m.group(2)
            url = self._full_url(raw_url)
            if not url or url in seen:
                continue
            seen.add(url)

            text = re.sub(r"<[^>]+>", " ", inner)
            text = re.sub(r"\s+", " ", text).strip()

            resolution = ""
            rm = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p|240p)\b',
                           text + " " + url, re.I)
            if rm:
                resolution = rm.group(1)

            size = ""
            sm = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b',
                           text, re.I)
            if sm:
                size = sm.group(1)

            host_m = re.search(r'https?://([^/]+)', url)
            host = host_m.group(1) if host_m else ""

            downloads.append({
                "resolution": resolution,
                "size": size,
                "quality": "Aflaam",
                "url": url,
                "label": text or (resolution or "Download"),
                "host": host,
            })

        # (2) bare strings in inline JS (defensive — some templates load
        #     the download list via AJAX)
        if not downloads:
            for m in re.finditer(
                    r'["\'](/download/\d+/\d+/[A-Za-z0-9_\-]+/?)["\']',
                    html, re.I):
                url = self._full_url(m.group(1))
                if url and url not in seen:
                    seen.add(url)
                    downloads.append({
                        "resolution": "",
                        "size": "",
                        "quality": "Aflaam",
                        "url": url,
                        "label": "Download",
                        "host": "",
                    })

        log("Aflaam: {} download page link(s)".format(len(downloads)))
        return downloads

    # ── Public: get_page ───────────────────────────────────────────────────
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
            log("Aflaam: get_page failed for {}".format(url))
            return result

        title, poster, plot, year = self._extract_detail_meta(html)
        result["title"] = title
        result["poster"] = poster
        result["plot"] = plot
        result["year"] = year
        result["url"] = final_url or url

        u = (final_url or url).lower()
        if "/series/" in u or "مسلسل" in title:
            result["type"] = "series"
        else:
            result["type"] = "movie"

        # Servers — try once on the plain page, then a `?watch=1` fallback
        # only if we found nothing (some templates reveal lazily).
        servers = self._extract_watch_servers(html, final_url or url)
        if not servers:
            reveal = (final_url or url) + \
                     ("&" if "?" in (final_url or url) else "?") + "watch=1"
            log("Aflaam: no servers on initial load, retrying {}".format(
                reveal[:100]))
            rev_html, _ = fetch(reveal, referer=final_url or url,
                                extra_headers=_PROBE_HEADERS)
            if rev_html:
                servers = self._extract_watch_servers(rev_html, reveal)
                if rev_html != html:
                    result["downloads"] = self._extract_download_page_links(rev_html)
        result["servers"] = servers

        # Downloads — from the detail page anchor list
        if not result["downloads"]:
            result["downloads"] = self._extract_download_page_links(html)

        # Episodes — series pages carry a season/episode list. The detail
        # markup wasn't snapshotted, so scan any `/series/<id>/<slug>` or
        # `/download/...` links inside a container that also mentions
        # "حلقة" / "Episode".
        if result["type"] == "series":
            episodes = []
            seen_eps = set()
            for m in re.finditer(
                    r'<a[^>]+href=["\']([^"\']*(?:/series/|/episode/|/download/)[^"\']*)["\'][^>]*>(.*?)</a>',
                    html, re.S | re.I):
                ep_url = self._full_url(m.group(1))
                if not ep_url or ep_url == (final_url or url) or ep_url in seen_eps:
                    continue
                # [PATCH A3] the /episode/, /series/, or /download/ URL pattern that
                # gates entry to this loop already reliably identifies a real episode
                # link -- confirmed against a real capture that the visible anchor text
                # never actually contains "حلقة"/"Episode" (it's just a bare number plus
                # the English title), so requiring that text rejected every real episode.
                seen_eps.add(ep_url)
                num_m = re.search(r'<span[^>]*>\s*(\d+)\s*</span>', m.group(2))
                ep_num = num_m.group(1) if num_m else ""
                ep_text = re.sub(r"<[^>]+>", " ", m.group(2)).strip()
                ep_text = re.sub(r"\s+", " ", ep_text)
                if ep_num and ep_text.startswith(ep_num):
                    ep_text = ep_text[len(ep_num):].strip()
                title = "{}. {}".format(ep_num, ep_text) if ep_num and ep_text else (ep_text or "حلقة")
                episodes.append({
                    "title": title,
                    "url": ep_url,
                    "type": "episode",
                    "_action": "details",
                })
            result["items"] = episodes
            log("Aflaam: {} episode(s) found".format(len(episodes)))

        log("Aflaam: get_page {} → {} server(s), {} download(s), type={}"
            .format(url[:60], len(result["servers"]),
                    len(result["downloads"]), result["type"]))
        return result

    # ── Download-page resolution ───────────────────────────────────────────
    def _resolve_download_page(self, url):
        """
        Fetch a `/download/<vid>/<movie>/<slug>` page and pull the real
        file URL that its inline script reveals. Both the `download="…"`
        attribute and the `attr('href', '…')` call carry the same value;
        either is enough.
        """
        html, _ = fetch(url, referer=self._get_base(),
                        extra_headers=_PROBE_HEADERS)
        if not html:
            return ""

        # Pattern 1 — `download="<file_url>"` attribute on an <a>
        m = re.search(r'download="(https?://[^"]+\.(?:mp4|mkv|m3u8)[^"]*)"',
                      html, re.I)
        if m:
            return m.group(1).replace("&amp;", "&")

        # Pattern 2 — `attr('href', '<file_url>')` in the reveal script
        m = re.search(
            r"attr\(\s*['\"]href['\"]\s*,\s*['\"]([^'\"]+\.(?:mp4|mkv|m3u8)[^'\"]*)['\"]",
            html, re.I)
        if m:
            return m.group(1).replace("&amp;", "&")

        # Pattern 3 — any direct .mp4/.m3u8 literal on the page
        m = re.search(
            r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8|mkv)[^\s"\'<>]*)', html, re.I)
        if m:
            return m.group(1).replace("&amp;", "&")

        return ""

    @staticmethod
    def _quality_from_filename(url):
        """Extract a quality label from a URL or filename."""
        if not url:
            return "HD"
        low = url.lower()
        for token, label in (
            ("2160", "2160p"), ("4k", "2160p"),
            ("1440", "1440p"),
            ("1080", "1080p"), ("fhd", "1080p"),
            ("720",  "720p"),
            ("480",  "480p"),
            ("360",  "360p"),
            ("240",  "240p"),
        ):
            if token in low:
                return label
        return "HD"

    # ── Public: extract_stream ─────────────────────────────────────────────
    def extract_stream(self, url):
        """
        Resolve a server/download URL to a playable stream.

        - `/download/...` pages → fetch the page and pull the direct file
          URL from the reveal script's download= / attr('href',…) markup.
        - Direct media (.mp4/.m3u8/.mkv/.ts) → pass through.
        - Everything else → delegate to the shared host resolver.
        """
        from .base import extract_stream as base_extract_stream
        from .base import extract_stream_all as base_extract_all

        if not url:
            return None, "", self._get_base(), []

        clean = _correct_stream_url(url.strip())
        low = clean.lower()

        # Download-page URL → reveal the real file URL
        if "/download/" in low:
            real = self._resolve_download_page(clean)
            if real:
                real = _correct_stream_url(real)
                quality = self._quality_from_filename(real)
                log("Aflaam: revealed download URL → {}".format(real[:100]))
                return real, quality, self._get_base(), []

        # [PATCH A1] /watch/ page URL -> the same reveal logic works here too,
        # confirmed against a real capture: the page's own JSON-LD contentUrl
        # is a direct .mp4/.m3u8 literal, which _resolve_download_page's
        # generic Pattern 3 already finds correctly.
        if "/watch/" in low:
            real = self._resolve_download_page(clean)
            if real:
                real = _correct_stream_url(real)
                quality = self._quality_from_filename(real)
                log("Aflaam: revealed watch-page stream → {}".format(real[:100]))
                return real, quality, self._get_base(), []

        # Direct media URL → pass through
        low_path = low.split("?", 1)[0]
        if low_path.endswith((".mp4", ".m3u8", ".mkv", ".ts", ".txt")):
            quality = self._quality_from_filename(clean)
            return clean, quality, self._get_base(), []

        # Multi-quality first
        try:
            variants = base_extract_all(clean)
            if variants:
                best_url, best_quality = variants[0]
                others = [(q, u) for u, q in variants[1:]]
                return best_url, best_quality, self._get_base(), others
        except Exception as e:
            log("Aflaam: extract_stream_all failed for {}: {}".format(
                clean[:80], e))

        # Single-URL resolver
        try:
            result = base_extract_stream(clean)
            if isinstance(result, tuple):
                if len(result) >= 4:
                    return result[0], result[1] or "", result[2] or self._get_base(), result[3] or []
                if len(result) >= 3:
                    return result[0], result[1] or "", result[2] or self._get_base(), []
                if len(result) == 1:
                    return result[0], "", self._get_base(), []
            elif isinstance(result, str) and result:
                return result, "", self._get_base(), []
        except Exception as e:
            log("Aflaam: extract_stream failed for {}: {}".format(
                clean[:80], e))

        return None, "", self._get_base(), []