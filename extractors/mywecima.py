# -*- coding: utf-8 -*-
"""
MyWecima extractor — mywecima.quest / mywecima.online
======================================================
PHP Melody engine (Wecima / Mycima family).

Canonical base: https://mywecima.quest/ — probes the mirror list and
locks onto the first host that actually serves listing pages, not just
a homepage/landing (same gate egydead uses).

URL shapes (confirmed against real captures, 2026-09-23):

  Homepage        /main
  Category        /category.php?cat=<slug>&page=N&order=DESC
  Movie listing   /movies.php
  Series listing  /all-series.php
  Episode listing /episodes.php
  Search          /search.php?keywords=<query>
  Watch/detail    /watch.php?vid=<hash>

Listing card markup (from real captures — foreign-films p1/p2):

  <li class="col-...">
    <div class="thumbnail">
      <div class="pm-video-thumb">
        <span class="pm-label-duration">1:31:23</span>
        <a href="/watch.php?vid=faee5e44d" title="فيلم Fall 2: Deadpoint 2026 مدبلج">
          <div class="pm-video-labels">
            <div class="ribon">
              <span class="hot" style="background:#ff0000;">WEB-DL</span>
              <!-- or: HDCAM / قريبا / الاخيرة -->
            </div>
          </div>
          <img src="/uploads/thumbs/faee5e44d-1.jpg" alt="..." class="img-responsive lazy_load">
        </a>
      </div>
      <div class="caption">
        <h3><a href="/watch.php?vid=faee5e44d" title="...">فيلم Fall 2: Deadpoint 2026 مدبلج</a></h3>
      </div>
    </div>
  </li>

Episode cards (from the /main carousels) also carry
  <span class="ep">الحلقة 9</span>  inside pm-video-labels.

Pagination (real markup):

  <ul class="pagination pagination-sm pagination-arrows">
    <li class="disabled"><a><i class="fa fa-arrow-right"></i></a></li>   <!-- prev -->
    <li class="active"><a>2</a></li>
    <li><a href="category.php?cat=foreign-films&amp;page=3&amp;order=DESC">3</a></li>
    ...
    <li><a href="category.php?cat=foreign-films&amp;page=3&amp;order=DESC"><i class="fa fa-arrow-left"></i></a></li>  <!-- next -->
  </ul>

RTL quirk: fa-arrow-right = previous, fa-arrow-left = next. Parsing
prefers an explicit link matching current_page + 1 so RTL glyphs never
matter.

Watch / download layers
------------------------
The watch page was NOT part of the snapshots we were given. To stay
useful against every Wecima-family watch layout, `_extract_watch_servers`
and `_extract_download_links` try several strategies and de-dup:

  1. PHP Melody "WatchServersList" — <li data-url="..."> (base64-wrapped)
  2. Any <iframe src> that isn't a social/analytics host
  3. data-watch / data-server / data-link / data-iframe / data-embed
  4. <source src=...mp4|m3u8>
  5. JS literals: file:/src:/url: "...mp4|m3u8"
  6. Download block: any <a> inside a *download* container

Hosts are de-duped by decoded URL, download URLs are filtered to
external file hosts (savefiles.com, dood, mixdrop, luluvdo, ...) so
Facebook/Telegram/nav links never leak through.

Carried-over best practices from egydead.py:
  * single-flight base-domain probe with TTL + cooldown caches
  * `_base_serves_deep_content` — reject homepages that answer every
    deep URL with the same page (repeating listing collapse)
  * percent-encoding of non-ASCII URLs (Arabic poster filenames work)
  * server-side pagination via query params, not path arithmetic
  * `label` / `year` split so the grid badge reads cleanly
  * `downloads` list in `get_page()` for the shared download manager
"""

import re
import time
import base64
import threading

from .base import (
    BaseExtractor, fetch, log, urljoin,
    _correct_stream_url,
)

try:
    from urllib.parse import quote, urlparse, urljoin as _urljoin, parse_qs, urlencode
except ImportError:                              # py2 shim (kept for parity)
    from urllib import quote
    from urlparse import urlparse, parse_qs, urljoin as _urljoin
    from urllib import urlencode


# ─── Process-wide base cache (survives extractor-instance reuse) ────────────
_BASE_CACHE_TTL  = 300        # success cache — 5 minutes
_PROBE_COOLDOWN  = 30         # don't re-scan domains within 30s after a full failure
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


class MyWecimaExtractor(BaseExtractor):
    """Extractor for MyWecima (mywecima.quest / .online mirrors)."""

    MAIN_URL = "https://mywecima.quest/"

    DOMAINS = [
        "https://mywecima.quest/",
        "https://mywecima.online/",
        "https://www.mywecima.quest/",
        "https://mywecima.live/",
        "https://mywecima.click/",
    ]

    VALID_HOST_MARKERS = ("mywecima.quest", "mywecima.online", "mywecima.",
                          "mywecima.live", "mywecima.click")
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)

    # Filename markers that never appear on watch/detail pages — safe to
    # reject in the video-item detector (same idea as egydead's nav filter).
    _NAV_PATH_RE = re.compile(
        r'^/?(?:category\.php|movies\.php|all-series\.php|episodes\.php|'
        r'newvideos\.php|topvideos\.php|search\.php|register\.php|'
        r'login\.php|suggest\.php|upload\.php|rss\.php)(?:\?|$)',
        re.I
    )

    # ── Category catalogue (from the site's own nav menu) ──────────────────
    _CATEGORIES = [
        # Movies
        ("🎬 افلام اجنبية",       "category.php?cat=foreign-films"),
        ("🎬 افلام عربية",        "category.php?cat=arabic-movies"),
        ("🎬 افلام تركية",        "category.php?cat=turkish-films"),
        ("🎬 افلام هندية",        "category.php?cat=indian-movies"),
        ("🎬 افلام انمي وكرتوني",  "category.php?cat=anime-and-cartoon-movies"),
        # Series
        ("📺 مسلسلات اجنبية",     "category.php?cat=foreign-tv-series"),
        ("📺 مسلسلات عربية",      "category.php?cat=arabic-tv-series"),
        ("📺 مسلسلات تركية",      "category.php?cat=turkish-series"),
        ("📺 مسلسلات اسيوية",     "category.php?cat=asian-dramas"),
        ("📺 مسلسلات هندية",      "category.php?cat=indian-tv-series"),
        ("📺 مسلسلات انمي وكرتون", "category.php?cat=anime-and-cartoons"),
        ("📺 مسلسلات قصيرة",      "category.php?cat=short-series"),
        # Shows
        ("📡 برامج تلفزيونية",     "category.php?cat=tv-programs"),
        # Latest aggregations
        ("🆕 أحدث الأفلام",       "movies.php"),
        ("🆕 أحدث المسلسلات",     "all-series.php"),
        ("🆕 أحدث الحلقات",       "episodes.php"),
        ("🔥 الاكثر مشاهدة",      "topvideos.php"),
    ]

    # Non-media host fragments we never want as servers.
    _NON_MEDIA_HOSTS = (
        "facebook.com", "twitter.com", "instagram.com", "youtube.com/embed",
        "google.com", "t.me", "telegram.me", "whatsapp.com",
        "googletagmanager", "googlesyndication", "doubleclick",
        "google-analytics", "cloudflareinsights",
    )

    # Host fragments that only appear on watch pages → strong signal.
    _MEDIA_HOST_MARKERS = (
        "streamwish", "filemoon", "dood", "playmogo", "vidhide", "voe",
        "mixdrop", "luluvdo", "streamtape", "mp4upload", "vidmoly",
        "savefiles", "mxcontent", "downet", "ok.ru", "okru",
        "streamruby", "stmruby", "uqload", "byselapuix", "hgcloud",
        "govid", "fastvid", "vidguard", "vidaraa", "morencius",
        "audinifer", "hanerix", "vibuxer", "earnvids", "cloudwindow",
    )

    # ── Construction / base probe ──────────────────────────────────────────
    def __init__(self):
        super(MyWecimaExtractor, self).__init__()
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
        # Strong CF-only markers. Ignore the benign analytics beacon —
        # `cloudflareinsights` appears on every legitimate page.
        if "just a moment" in text and ("cf-chl" in text or "challenge" in text):
            return True
        if "enable javascript and cookies to continue" in text:
            return True
        if "cf-browser-verification" in text:
            return True
        if any(m in final for m in self.BLOCKED_HOST_MARKERS):
            return True
        return False

    def _looks_like_mywecima_page(self, html):
        text = html or ""
        return (
            "mywecima" in text.lower()
            or "pm-video-thumb" in text
            or "pm-ul-browse-videos" in text
            or "myNavmenu" in text
            or "ماي سيما" in text
            or "MYCIMA" in text
            or "watch.php?vid=" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        """
        The homepage-alias gate: some domains answer /main but return the
        same listing for every category.php, which collapses all categories
        into one grid. Probe one real category and confirm the page actually
        contains movie-card markup.
        """
        if not base:
            return False
        test_url = urljoin(base, "category.php?cat=foreign-films")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("MyWecima: deep-content probe failed for {}: {}".format(base, e))
            return False
        if not html or self._is_blocked_page(html, final or ""):
            log("MyWecima: deep-content probe empty/blocked for {}".format(base))
            return False
        # A real listing has many `pm-video-thumb` blocks; a landing page
        # doesn't.
        if html.count("pm-video-thumb") < 6:
            log("MyWecima: {} answered but only {} movie-card markers — "
                "likely a landing, not a content mirror".format(
                    base, html.count("pm-video-thumb")))
            return False
        return True

    def _get_base(self):
        """Single-flight domain resolution with success + failure caches."""
        if self._resolved_base:
            return self._resolved_base

        now = time.time()
        with _base_cache_lock:
            cached_url  = _base_cache["url"]
            resolved_at = _base_cache["resolved_at"]
            probed_at   = _base_cache["probed_at"]

        if cached_url and (now - resolved_at) < _BASE_CACHE_TTL:
            log("MyWecima: reusing cached base {} ({}s old)".format(
                cached_url, int(now - resolved_at)))
            self._resolved_base = cached_url
            self.main_url = cached_url
            return cached_url

        if cached_url and (now - probed_at) < _PROBE_COOLDOWN:
            log("MyWecima: skipping full re-probe (cooldown), reusing {}"
                .format(cached_url))
            self._resolved_base = cached_url
            self.main_url = cached_url
            return cached_url

        with _probe_lock:
            # Double-check while we held the lock.
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
                log("MyWecima: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain,
                                        extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain

                if not self._is_valid_site_url(final_url):
                    log("MyWecima: unexpected host after redirect {}"
                        .format(final_url))
                    continue
                if self._is_blocked_page(html, final_url):
                    log("MyWecima: blocked {}".format(final_url))
                    continue
                if not (html and self._looks_like_mywecima_page(html)):
                    continue

                candidate = self._site_root(final_url)
                if not self._base_serves_deep_content(candidate):
                    log("MyWecima: {} is not a content mirror — next".format(
                        candidate))
                    continue

                self._resolved_base = candidate
                self.main_url = candidate
                with _base_cache_lock:
                    _base_cache["url"] = candidate
                    _base_cache["resolved_at"] = now
                    _base_cache["probed_at"] = now
                log("MyWecima: selected base {}".format(candidate))
                return candidate

            # Fallback: the canonical alias. All mirrors redirect via it.
            self._resolved_base = self.MAIN_URL
            self.main_url = self.MAIN_URL
            with _base_cache_lock:
                _base_cache["url"] = self.MAIN_URL
                _base_cache["probed_at"] = now
            log("MyWecima: all probes failed, using {}".format(self.MAIN_URL))
            return self._resolved_base

    # ── URL & title helpers ────────────────────────────────────────────────
    def _full_url(self, path):
        """
        Resolve a relative or absolute URL, then percent-encode non-ASCII
        bytes. The site serves Arabic-titled posters/folders
        (`/uploads/thumbs/فيلم-X-1.jpg`), so the encoding step must run
        for ABSOLUTE URLs too — same fix as egydead.
        """
        if not path:
            return ""
        path = str(path).strip().replace("&amp;", "&")

        if path.startswith("//"):
            path = "https:" + path

        if path.startswith("http"):
            full_url = path
        else:
            base = self._get_base()
            full_url = urljoin(base, path)
            # Collapse duplicated slashes in the path only.
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

    def _clean_title(self, title):
        if not title:
            return ""
        text = str(title)
        text = text.replace("&amp;", "&").replace("&#39;", "'")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Strip trailing "مترجم"/"مدبلج"/"مترجمة" so the caption is
        # comparable against the poster caption, but keep them on the
        # display string (handled separately).
        return text

    def _clean_title_for_display(self, title):
        """
        Remove the site's own boilerplate so the tile caption isn't
        cluttered — but leave the year and the (مترجم/مدبلج) tag visible
        because users distinguish entries by them.
        """
        text = self._clean_title(title)
        # Site name is sometimes appended after a bar.
        text = re.sub(r"\s*[-|]\s*(?:ماي سيما|MYCIMA|WECIMA|mywecima).*$",
                      "", text, flags=re.I).strip()
        return text

    def _encode_query(self, query):
        try:
            return quote(str(query or ""), safe="")
        except Exception:
            return str(query or "")

    # ── Card parser (homepage carousels + category grid + search) ─────────
    def _extract_cards(self, html, max_items=200):
        """
        Real markup (verbatim capture, foreign-films p2):

            <li class="col-xs-6 col-sm-4 col-md-3">
              <div class="thumbnail">
                <div class="pm-video-thumb" ...>
                  <span class="pm-label-duration">1:31:23</span>
                  <a href="watch.php?vid=faee5e44d"
                     title="فيلم Fall 2: Deadpoint 2026 مدبلج">
                    <div class="pm-video-labels">
                      <div class="ribon">
                        <span class="hot" style="background:#ff0000;">WEB-DL</span>
                      </div>
                    </div>
                    <img src="/uploads/thumbs/faee5e44d-1.jpg" alt="..." class="...">
                  </a>
                </div>
                <div class="caption">
                  <h3><a href="watch.php?vid=faee5e44d" title="...">فيلم ...</a></h3>
                </div>
              </div>
            </li>

        Homepage carousel items have the same `.pm-video-thumb` block.
        Episode cards additionally carry `<span class="ep">الحلقة N</span>`.
        """
        items = []
        seen_urls = set()
        if not html:
            return items

        # Split by the outer <li>; the whole card lives inside it.
        # Allow attributes on the <li> tag (varied Bootstrap columns).
        for block in re.split(r'<li\s+class="[^"]*"\s*>', html):
            # A card must contain the marker AND a watch link.
            if "pm-video-thumb" not in block and "block-post" not in block:
                continue
            if "watch.php?vid=" not in block:
                continue

            # ── URL ───────────────────────────────────────────────────────
            url_m = re.search(
                r'href="([^"]*watch\.php\?vid=[^"]+)"', block, re.I)
            if not url_m:
                continue
            url = self._full_url(url_m.group(1))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # ── Title (prefer the <img alt>, fall back to <h3>) ──────────
            title = ""
            alt_m = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
            if alt_m:
                title = alt_m.group(1)
            if not title:
                h3_m = re.search(
                    r'<h3[^>]*>\s*<a[^>]*>(.*?)</a>\s*</h3>',
                    block, re.S | re.I)
                if h3_m:
                    title = h3_m.group(1)
            title = self._clean_title_for_display(title)
            if not title:
                continue

            # ── Poster ────────────────────────────────────────────────────
            poster = ""
            img_m = re.search(
                r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*(?:lazy_load|img-responsive)[^"]*"',
                block, re.I)
            if not img_m:
                img_m = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
            if img_m:
                cand = img_m.group(1)
                if not any(x in cand.lower() for x in
                           ("placeholder", "loading.gif", "lazy_load.gif",
                            "data:image", "/logo")):
                    poster = self._full_url(cand)

            # ── Duration ──────────────────────────────────────────────────
            dur_m = re.search(
                r'class="pm-label-duration"[^>]*>\s*([^<]+?)\s*<',
                block, re.I)
            duration = dur_m.group(1).strip() if dur_m else ""

            # ── Quality / status ribbon ──────────────────────────────────
            # Two real shapes:
            #   <span class="hot" style="...">WEB-DL</span>
            #   <span class="hot" style="...">قريبا</span>
            #   <span class="hot" style="...">الاخيرة</span>
            label = ""
            ribbon_m = re.search(
                r'<span[^>]+class="[^"]*\bhot\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.I)
            if ribbon_m:
                label = ribbon_m.group(1).strip()
            else:
                # Bare <span> inside .ribon (some older templates)
                rb = re.search(
                    r'<div[^>]+class="[^"]*ribon[^"]*"[^>]*>(.*?)</div>',
                    block, re.S | re.I)
                if rb:
                    inner_m = re.search(r'<span[^>]*>\s*([^<]+?)\s*</span>',
                                        rb.group(1), re.I)
                    if inner_m:
                        label = inner_m.group(1).strip()

            # ── Episode marker ────────────────────────────────────────────
            episode = ""
            ep_m = re.search(
                r'<span[^>]+class="[^"]*\bep\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.I)
            if ep_m:
                episode = ep_m.group(1).strip()

            # ── Year (own field, badge-friendly — matches egydead) ───────
            year = ""
            ym = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            if ym:
                year = ym.group(1)

            # ── Type ──────────────────────────────────────────────────────
            url_low = url.lower()
            title_low = title
            if episode or "حلقة" in title_low or "الحلقة" in title_low:
                item_type = "episode"
            elif ("/series" in url_low or "/serie" in url_low
                  or "مسلسل" in title_low or "الموسم" in title_low
                  or "برنامج" in title_low):
                item_type = "series"
            else:
                item_type = "movie"

            # Skip obvious navigation links that slipped past the li split
            path_only = re.sub(r'^https?://[^/]+', '', url)
            if self._NAV_PATH_RE.match(path_only) and "watch.php" not in path_only:
                continue

            display_title = title
            if episode and episode not in display_title:
                display_title = "{} — {}".format(title, episode)

            items.append({
                "title": display_title,
                "url": url,
                "poster": poster,
                "plot": label,
                "label": label,
                "duration": duration,
                "year": year,
                "episode": episode,
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

            <ul class="pagination pagination-sm pagination-arrows">
              <li class="disabled"><a><i class="fa fa-arrow-right"></i></a></li>
              <li class="active"><a>2</a></li>
              <li><a href="...&page=3&order=DESC">3</a></li>
              ...
              <li><a href="...&page=3&order=DESC"><i class="fa fa-arrow-left"></i></a></li>
            </ul>

        Arrow glyphs are RTL-swapped client-side, so we bypass them:
        determine the current page number, then look for a link whose
        `page=` param is current+1. Fall back to the RTL next-arrow only
        if the numeric link isn't present.
        """
        next_url = None
        pag_m = re.search(
            r'<ul[^>]+class="[^"]*pagination[^"]*"[^>]*>(.*?)</ul>',
            html or "", re.S | re.I)
        if not pag_m:
            return None
        pag_html = pag_m.group(1)

        # Current page: prefer the explicit active li; else derive from URL.
        current_page = 1
        url_pg = re.search(r'[?&]page=(\d+)', current_url or "")
        if url_pg:
            try:
                current_page = int(url_pg.group(1))
            except ValueError:
                pass
        active_m = re.search(
            r'<li[^>]+class="[^"]*\bactive\b[^"]*"[^>]*>\s*<a[^>]*>\s*(\d+)\s*</a>',
            pag_html, re.S | re.I)
        if active_m:
            try:
                current_page = int(active_m.group(1))
            except ValueError:
                pass

        next_page = current_page + 1

        # Strategy 1: numeric link to page=current+1
        for m in re.finditer(
                r'<a[^>]+href="([^"]+)"[^>]*>\s*{}\s*</a>'.format(next_page),
                pag_html, re.I):
            next_url = self._full_url(m.group(1).replace("&amp;", "&"))
            if next_url:
                break

        # Strategy 2: any href containing page=current+1
        if not next_url:
            m = re.search(
                r'href="([^"]*[?&]page={}\b[^"]*)"'.format(next_page),
                pag_html, re.I)
            if m:
                next_url = self._full_url(m.group(1).replace("&amp;", "&"))

        # Strategy 3 (last resort): the RTL "next" arrow.
        if not next_url:
            m = re.search(
                r'<a[^>]+href="([^"]+)"[^>]*>\s*<i[^>]+class="[^"]*fa-arrow-left[^"]*"',
                pag_html, re.I | re.S)
            if m:
                next_url = self._full_url(m.group(1).replace("&amp;", "&"))

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
        """Return the category catalogue. `mtype` ignored (single menu)."""
        self._get_base()                       # warm cache
        base = self._get_base()
        cats = []
        for title, path in self._CATEGORIES:
            cats.append({
                "title": title,
                "url": self._full_url(path),
                "type": "category",
                "_action": "category",
            })
        return cats

    # ── Public: category / listing pages ───────────────────────────────────
    def get_category_items(self, url, page=1):
        """
        The site paginates via `?page=N` — page is a QUERY parameter, not a
        path segment. If the caller passes page>1 with the wrong URL shape
        we still rewrite it onto the query string so back/forward works.
        """
        fetch_url = self._full_url(url)

        if page and page > 1:
            parsed = urlparse(fetch_url)
            q = parse_qs(parsed.query or "", keep_blank_values=True)
            q["page"] = [str(page)]
            # Preserve order DESC if the original URL had one
            if "order" not in q:
                q["order"] = ["DESC"]
            fetch_url = parsed._replace(
                query=urlencode([(k, v[0]) for k, v in q.items()])
            ).geturl()

        log("MyWecima: fetching listing {}".format(fetch_url))
        html, final_url = fetch(fetch_url, referer=self._get_base(),
                                extra_headers=_PROBE_HEADERS)
        if not html:
            log("MyWecima: get_category_items failed for {}".format(fetch_url))
            return []

        open("/tmp/mywecima_debug.html", "w", encoding="utf-8").write(html or "")
        items = self._extract_cards(html)
        log("MyWecima: {} cards extracted".format(len(items)))

        # Add pagination as a synthetic trailing item the home screen
        # already understands (type="category", _action="category").
        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        return items

    # ── Public: search ─────────────────────────────────────────────────────
    def search(self, query, page=1):
        q = self._encode_query(query)
        search_url = self._full_url("search.php?keywords=" + q)
        if page and page > 1:
            search_url += "&page=" + str(page)

        log("MyWecima: search '{}'".format(query))
        html, _ = fetch(search_url, referer=self._get_base(),
                        extra_headers=_PROBE_HEADERS)
        if not html:
            return []

        items = self._extract_cards(html)
        if page == 1:
            nxt = self._parse_pagination(html, search_url)
            if nxt:
                items.append(nxt)
        log("MyWecima: search '{}' → {} items".format(query, len(items)))
        return items

    # ── Detail page ────────────────────────────────────────────────────────
    def _extract_detail_meta(self, html):
        """og:meta first, then fall back to DOM nodes."""
        title = ""
        title_m = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if not title_m:
            title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
        if title_m:
            title = self._clean_title_for_display(title_m.group(1).split("|")[0])

        poster = ""
        poster_m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if poster_m:
            poster = self._full_url(poster_m.group(1))
        if not poster:
            img_m = re.search(
                r'<div[^>]+class="[^"]*pm-video-thumb[^"]*"[^>]*>.*?<img[^>]+src="([^"]+)"',
                html, re.S | re.I)
            if img_m:
                poster = self._full_url(img_m.group(1))

        plot = ""
        desc_m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if desc_m:
            plot = re.sub(r"<[^>]+>", " ", desc_m.group(1)).strip()
        if not plot:
            story_m = re.search(
                r'<div[^>]+class="[^"]*(?:description|story|plot)[^"]*"[^>]*>(.*?)</div>',
                html, re.S | re.I)
            if story_m:
                plot = re.sub(r"<[^>]+>", " ", story_m.group(1)).strip()

        year = ""
        ym = re.search(r'\b(19\d{2}|20\d{2})\b', title + " " + plot)
        if ym:
            year = ym.group(1)

        return title, poster, plot, year

    # ── Server extraction (watch page) ─────────────────────────────────────
    def _decode_wecima_b64(self, raw):
        """
        The Wecima family wraps some server URLs in base64 — sometimes
        with a leading `aHR0c` prefix already-present and sometimes
        stripped. Mirror the tolerant decoder wecima.py uses.
        """
        if not raw:
            return ""
        s = str(raw).strip().replace("+", "").replace(" ", "")
        # Fast path: already an http(s) URL
        if s.startswith("http://") or s.startswith("https://"):
            return s

        # Try adding the aHR0c prefix (missing "http" bytes)
        for candidate in (s, "aHR0c" + s):
            cleaned = re.sub(r'[^A-Za-z0-9+/=]', '', candidate)
            cleaned += "=" * ((-len(cleaned)) % 4)
            try:
                dec = base64.b64decode(cleaned).decode("utf-8", "replace")
            except Exception:
                continue
            dec = dec.replace("\\/", "/").replace("\\u0026", "&")
            if dec.startswith("//"):
                dec = "https:" + dec
            if dec.startswith("http"):
                return dec
        return ""

    def _host_display_name(self, host):
        lowered = (host or "").lower()
        mapping = (
            ("streamwish", "StreamWish"), ("wishfast", "StreamWish"),
            ("filemoon", "FileMoon"), ("dood", "DoodStream"),
            ("playmogo", "PlayMogo"), ("vidhide", "VidHide"),
            ("voe", "Voe"), ("mixdrop", "MixDrop"),
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
            ("cloudwindow", "Voe"),
        )
        for frag, label in mapping:
            if frag in lowered:
                return label
        # Domain fallback
        return host or "Server"

    def _add_server(self, servers, seen, url, label=None, srv_type="embed"):
        if not url:
            return
        url = url.strip().replace("&amp;", "&")
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            return
        if any(x in url.lower() for x in self._NON_MEDIA_HOSTS):
            return
        # Skip obvious static assets.
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

    def _extract_watch_servers(self, html, page_url):
        """
        Multiple strategies, in the order a PHP-Melody watch page exposes
        them. Every hit is de-duped by decoded URL.
        """
        servers = []
        seen = set()
        if not html:
            return servers

        # ── Strategy 1: Wecima "WatchServersList" (data-url, b64) ─────────
        block_m = re.search(
            r'class="[^"]*WatchServersList[^"]*"[^>]*>(.*?)</ul>',
            html, re.S | re.I)
        if block_m:
            inner = block_m.group(1)
            for m in re.finditer(
                    r'(?:data-url|data-watch|href)="([^"]+)"[^>]*>(.*?)'
                    r'</(?:button|li|div|a|span)>',
                    inner, re.S | re.I):
                raw = m.group(1)
                decoded = self._decode_wecima_b64(raw) or raw
                # Server label — look for a <strong> first, else strip tags.
                name_m = re.search(r'<strong>(.*?)</strong>', m.group(2),
                                   re.S | re.I)
                label = None
                if name_m:
                    label = re.sub(r"<[^>]+>", "", name_m.group(1)).strip()
                self._add_server(servers, seen, decoded, label=label)

        # ── Strategy 2: data-watch / data-url / data-server / data-link ──
        for attr in ("data-watch", "data-url", "data-server",
                     "data-link", "data-iframe", "data-embed"):
            for m in re.finditer(attr + r'=["\']([^"\']+)["\']',
                                 html, re.I):
                raw = m.group(1)
                decoded = self._decode_wecima_b64(raw) or raw
                self._add_server(servers, seen, decoded)

        # ── Strategy 3: <iframe src> (skip social/analytics) ─────────────
        for m in re.finditer(
                r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
            self._add_server(servers, seen, m.group(1).strip())

        # ── Strategy 4: direct <source> ──────────────────────────────────
        for m in re.finditer(
                r'<source[^>]+src=["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            u = _correct_stream_url(m.group(1))
            self._add_server(servers, seen, u, srv_type="direct")

        # ── Strategy 5: JS literals (file:/src:/url:) ────────────────────
        for m in re.finditer(
                r'(?:file|src|url|source)\s*[:=]\s*["\']'
                r'([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            u = _correct_stream_url(m.group(1).replace("\\/", "/"))
            self._add_server(servers, seen, u, srv_type="direct")

        # ── Strategy 6: bare known-host URLs anywhere on the page ────────
        host_re = re.compile(
            r'(https?://(?:[^"\'\s<>]*(?:'
            + "|".join(self._MEDIA_HOST_MARKERS)
            + r'))[^"\'\s<>]*)', re.I)
        for m in host_re.finditer(html):
            self._add_server(servers, seen,
                             m.group(1).replace("\\/", "/"))

        log("MyWecima: {} watch server(s) for {}".format(
            len(servers), page_url[:80]))
        return servers

    # ── Download-section extraction ────────────────────────────────────────
    def _extract_download_links(self, html):
        """
        A Wecima-family download section presents one entry per available
        resolution. Containers we've seen in the family: `.Download--Wecima--Single`,
        `donwload-servers-list` (site's own typo), `.downloadMaster`,
        and generic `.download`/`.Download`. Each entry exposes the file
        host's link via data-href/data-url/href; label comes from a
        `resolution`/`.quality` span if present, else the anchor text.
        """
        downloads = []
        seen = set()
        if not html:
            return downloads

        block = None
        for pat in (
            r'<div[^>]+class="[^"]*Download--Wecima--Single[^"]*"[^>]*>(.*?)</ul>',
            r'<ul[^>]+class="[^"]*donwload-servers-list[^"]*"[^>]*>(.*?)</ul>',
            r'<div[^>]+class="[^"]*downloadMaster[^"]*"[^>]*>(.*?)</div>',
            r'<ul[^>]+class="[^"]*[Ll]ist--[Dd]ownload[^"]*"[^>]*>(.*?)</ul>',
            r'<div[^>]+class="[^"]*(?:download|Download)[^"]*"[^>]*>(.*?)</div>',
        ):
            m = re.search(pat, html, re.S | re.I)
            if m:
                block = m.group(1)
                break

        # Fall back to page-wide scan for the family's known data attributes.
        if block is None:
            block = html

        for item_m in re.finditer(
                r'<(?:li|a|div)[^>]*?'
                r'(?:data-href|data-url|data-link|href)=["\']([^"\']+)["\']'
                r'[^>]*>(.*?)</(?:li|a|div)>',
                block, re.S | re.I):
            raw_url = item_m.group(1)
            inner = item_m.group(2)
            url = self._decode_wecima_b64(raw_url) or raw_url
            url = url.strip().replace("&amp;", "&")
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith("http") or url in seen:
                continue
            if any(x in url.lower() for x in self._NON_MEDIA_HOSTS):
                continue
            # Skip same-site nav/self links — real downloads go external.
            if self._is_valid_site_url(url):
                continue

            seen.add(url)

            resolution = ""
            size = ""
            quality = ""

            res_m = re.search(
                r'class="[^"]*\bresolution\b[^"]*"[^>]*>\s*([^<]+?)\s*<',
                inner, re.S | re.I)
            if res_m:
                resolution = res_m.group(1).strip()
            sz_m = re.search(
                r'class="[^"]*\bsize\b[^"]*"[^>]*>\s*([^<]+?)\s*<',
                inner, re.S | re.I)
            if sz_m:
                size = sz_m.group(1).strip()
            q_m = re.search(
                r'class="[^"]*\bquality\b[^"]*"[^>]*>\s*([^<]+?)\s*<',
                inner, re.S | re.I)
            if q_m:
                quality = q_m.group(1).strip()

            # Fallbacks from text
            if not resolution:
                m2 = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b',
                               inner, re.I)
                if m2:
                    resolution = m2.group(1)
            if not size:
                m2 = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b',
                               inner, re.I)
                if m2:
                    size = m2.group(1)
            if not quality:
                # Use the file host's display name
                host_m = re.search(r'https?://([^/]+)', url)
                if host_m:
                    quality = self._host_display_name(host_m.group(1))

            downloads.append({
                "resolution": resolution,
                "size": size,
                "quality": quality,
                "url": url,
            })

        log("MyWecima: {} download link(s)".format(len(downloads)))
        return downloads

    # ── get_page — full detail path (landing → revealed → servers) ────────
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
            log("MyWecima: get_page failed for {}".format(url))
            return result

        title, poster, plot, year = self._extract_detail_meta(html)
        result["title"] = title
        result["poster"] = poster
        result["plot"] = plot
        result["year"] = year
        result["url"] = final_url or url

        is_series = ("/watch.php" in (final_url or url)
                     and any(k in title for k in ("مسلسل", "الحلقة", "الموسم",
                                                  "برنامج")))
        # A watch link for a series typically carries the master video's id;
        # the "episode list" block lives inside the page body.
        if is_series:
            result["type"] = "series"
        elif "الحلقة" in title or "حلقة" in title:
            result["type"] = "episode"

        # Servers from the landing page (same-page reveal).
        servers = self._extract_watch_servers(html, final_url or url)

        # Some templates lazily render the WatchServersList block after a
        # POST/GET like `?watch=1` (same trick arablionz uses). Try it only
        # if the plain fetch produced nothing.
        if not servers:
            reveal_url = ((final_url or url)
                          + ("&" if "?" in (final_url or url) else "?")
                          + "watch=1")
            log("MyWecima: no servers on initial load, retrying: {}"
                .format(reveal_url[:100]))
            rev_html, _ = fetch(reveal_url, referer=final_url or url,
                                extra_headers=_PROBE_HEADERS)
            if rev_html:
                servers = self._extract_watch_servers(rev_html,
                                                       reveal_url)
                # Downloads may only appear on the revealed page.
                if rev_html != html:
                    result["downloads"] = self._extract_download_links(rev_html)

        result["servers"] = servers
        if not result["downloads"]:
            result["downloads"] = self._extract_download_links(html)

        # ── Episode list (series pages) ─────────────────────────────────
        if is_series and result["type"] == "series":
            episodes = []
            seen_eps = set()
            # PHP Melody episodes container: any of the family patterns.
            eps_container_m = re.search(
                r'<div[^>]+class="[^"]*(?:episodes|Episodes|EpsList|'
                r'episodes-list)[^"]*"[^>]*>(.*?)</div>',
                html, re.S | re.I)
            eps_scope = eps_container_m.group(1) if eps_container_m else html

            for m in re.finditer(
                    r'<a[^>]+href="([^"]*watch\.php\?vid=[^"]+)"[^>]*>'
                    r'(.*?)</a>',
                    eps_scope, re.S | re.I):
                ep_url = self._full_url(m.group(1))
                if not ep_url or ep_url == (final_url or url) \
                        or ep_url in seen_eps:
                    continue
                seen_eps.add(ep_url)
                ep_text = re.sub(r"<[^>]+>", " ", m.group(2)).strip()
                ep_text = re.sub(r"\s+", " ", ep_text)
                if not ep_text:
                    ep_text = "حلقة"
                episodes.append({
                    "title": ep_text,
                    "url": ep_url,
                    "type": "episode",
                    "_action": "details",
                })
            result["items"] = episodes
            log("MyWecima: {} episode(s) found".format(len(episodes)))

        log("MyWecima: get_page {} → {} server(s), {} download(s), type={}"
            .format(url[:60], len(result["servers"]),
                    len(result["downloads"]), result["type"]))
        return result

    # ── extract_stream (single-server resolution) ─────────────────────────
    def extract_stream(self, url):
        """Resolve a watch-page server URL to a playable stream.

        Direct hits pass straight through; everything else is delegated
        to the shared host-resolver layer (`extract_stream_all` for the
        4-tuple contract, since some hosts expose multi-quality).
        """
        from .base import extract_stream as base_extract_stream
        from .base import extract_stream_all as base_extract_all

        if not url:
            return None, "", self._get_base(), []

        clean = _correct_stream_url(url.strip())

        # Direct media — pass through.
        low = clean.lower().split("?", 1)[0]
        if low.endswith((".mp4", ".m3u8", ".ts", ".txt")):
            quality = "HD"
            if "1080" in clean.lower():
                quality = "1080p"
            elif "720" in clean.lower():
                quality = "720p"
            elif "480" in clean.lower():
                quality = "480p"
            return clean, quality, self._get_base(), []

        # Multi-quality attempt first so the player gets the full list.
        try:
            variants = base_extract_all(clean)
            if variants:
                best_url, best_quality = variants[0]
                others = [(q, u) for u, q in variants[1:]]
                return best_url, best_quality, self._get_base(), others
        except Exception as e:
            log("MyWecima: extract_stream_all failed for {}: {}"
                .format(clean[:80], e))

        # Fall back to the single-URL resolver.
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
            log("MyWecima: extract_stream failed for {}: {}"
                .format(clean[:80], e))

        return None, "", self._get_base(), []