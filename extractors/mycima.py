# -*- coding: utf-8 -*-
"""
MyCima (Cimawbas / سيما وبس) extractor — vid.mycima.cc
=======================================================

PHP Melody engine. Arabic streaming portal branded "سيما وبس Cimawbas".

URL shapes (confirmed against captures, 2026-09-27):

  Homepage        /   or   /index.php
  Movies listing  /movies.php                 (paginated ?&page=N)
  Series listing  /all-series.php
  Episodes list   /episodes.php
  Top videos      /topvideos.php
  New videos      /newvideos.php
  Search          /search.php?keywords=<q>
  Category        /category.php?cat=<slug>&page=N[&order=DESC]
                  /categories-cimawbas.php?cat=<slug>&page=N
  Watch / detail  /watch.php?vid=<hash>
  Play            /play.php?vid=<hash>
  Downloads       /downloads.php?vid=<hash>
  Embed           /embed.php?vid=<hash>

Listing card markup (verbatim, /movies.php and /movies.php?&page=2):

    <li class="col-xs-6 col-sm-4 col-md-3">
      <div class="thumbnail">
        <div class="pm-video-thumb" style="overflow: unset!important">
          <span class="pm-label-duration">2:21:28</span>
          <a class="pm-watch-later-add btn ..." ...><i class="fa fa-clock-o"></i></a>
          <a href="https://vid.mycima.cc/watch.php?vid=841bbbdad"
             title="مشاهدة فيلم DC 2026 مترجم">
            <div class="pm-video-labels">...</div>
            <img src="https://vid.mycima.cc/uploads/thumbs/841bbbdad-1.jpg"
                 alt="مشاهدة فيلم DC 2026 مترجم" class="img-responsive"
                 style="width: 240px; height: 136px; ...">
            <span class="overlay"></span>
          </a>
        </div>
        <div class="caption">
          <h3><a href="..." title="..." class="ellipsis">...</a></h3>
        </div>
      </div>
    </li>

Lazy-load variant seen on the tail of /movies.php (page 1) and on the
homepage carousels — real thumb lives in `data-echo`, `src` is the
template's own `melody-lzld.png` placeholder:

    <img src="https://vid.mycima.cc/templates/3arbserv/img/melody-lzld.png"
         alt="..." data-echo="https://vid.mycima.cc/uploads/thumbs/e1a9d50f3-1.jpg"
         class="img-responsive" ...>

Optional label spans (a card may carry any combination):

    <span class="rating">7.6 <i class="fa fa-star" style="color:#FFD700;"></i></span>
    <div class="ribon"><span class="hot" style="background:#ff0000;">مدبلج</span></div>
    <div class="pm-video-labels"><span class="ep">الحلقة 5</span></div>

Pagination (verbatim, /movies.php?&page=2):

    <ul class="pagination pagination-sm pagination-arrows">
      <li class=""><a href="movies.php?&amp;page=1"><i class="fa fa-arrow-right"></i></a></li>
      <li class=""><a href="movies.php?&amp;page=1">1</a></li>
      <li class="active"><a href="#" onclick="...">2</a></li>
      <li class=""><a href="movies.php?&amp;page=3">3</a></li>
      ...
      <li class=""><a href="movies.php?&amp;page=3"><i class="fa fa-arrow-left"></i></a></li>
    </ul>

Two important notes on pagination:

  * The site's own pagination uses the odd `?&page=N` shape (empty first
    query key, then page). We preserve it when following the link the
    site gives us, and normalise to plain `?page=N` when the caller
    supplies page>1 to get_category_items().
  * Arrow glyphs are RTL-swapped in JS: the numeric page link is the
    authoritative target, not the arrow.

Server list on watch/play pages (verbatim, 2026-09-27):

    <ul class="list_servers list_embedded col-sec">
      <li id="server_Vipserver" class=""
          data-embed="&lt;iframe src='https://vipserver.liiivideo.com/embed-rx85zbfnm325.html'
                     scrolling='no' frameborder='0' width='100%' height='100%'
                     allowfullscreen='true' ...&gt;&lt;/iframe&gt;">
        <a><span><i class="fa fa-play"></i></span> <strong>Vipserver</strong></a>
      </li>
      ... (one <li> per server)
    </ul>

    <div class="embedded"><iframe src="https://bysebuho.com/e/0azqrs4othc0" ...></iframe></div>

  data-embed values are HTML-escaped; we must unescape them before pulling
  the iframe src out.

Detail meta (watch / play page):

    <meta property="og:title"     content="...">
    <meta property="og:image"     content="...uploads/thumbs/<id>-1.jpg">
    <meta itemprop="image"        content="...uploads/thumbs/<id>-1.jpg">
    <meta name="description"      content="...">

  We prefer itemprop="image" over og:image because the play.php template
  overrides og:image late with social-thumb.php.
"""

import re
import time
import base64
import html as _htmllib
import threading

from .base import (
    BaseExtractor, fetch, log, urljoin,
    _correct_stream_url,
)

try:
    from urllib.parse import quote, urlparse, urljoin as _urljoin, parse_qs, urlencode
except ImportError:                              # py2 shim (parity)
    from urllib import quote
    from urlparse import urlparse, parse_qs, urljoin as _urljoin
    from urllib import urlencode


# ─── Debug flags ────────────────────────────────────────────────────────────
_DEBUG_POSTER  = False
_DEBUG_SERVERS = False


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
    "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class MyCimaExtractor(BaseExtractor):
    """Extractor for MyCima / Cimawbas (vid.mycima.cc)."""

    MAIN_URL = "https://vid.mycima.cc/"
    DOMAINS = [
        "https://vid.mycima.cc/",
        "https://mycima.cc/",
        "https://www.mycima.cc/",
    ]
    VALID_HOST_MARKERS = ("vid.mycima.cc", "mycima.cc", "cimawbas")
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)

    # Recognised listing / navigation paths (used to ignore those URLs as
    # "servers" and to guide category detection).
    _NAV_PATH_RE = re.compile(
        r'^/?(?:movies\.php|all-series\.php|episodes\.php|'
        r'topvideos\.php|newvideos\.php|search\.php|category\.php|'
        r'categories-cimawbas\.php|watch\.php|play\.php|embed\.php|'
        r'downloads\.php|login\.php|suggest\.php|upload\.php|'
        r'rss\.php|ajax\.php|ajax-search\.php|tag\.php|user\.php)',
        re.I
    )

    # ── Category catalogue (site's own drawer menu) ─────────────────────
    _CATEGORIES = [
        ("🆕 أحدث الأفلام",        "movies.php"),
        ("📺 أحدث المسلسلات",      "all-series.php"),
        ("📺 أحدث الحلقات",        "episodes.php"),
        ("🔥 الاكثر مشاهدة",       "topvideos.php"),
        ("🆕 الجديد",              "newvideos.php"),

        ("🎬 افلام اجنبي",         "category.php?cat=all-english-movies"),
        ("🇮🇳 افلام هندي",          "category.php?cat=indian-movies-cimawbas"),
        ("🎌 افلام كرتون وانمي",   "category.php?cat=the-anime-movies-cimawbas"),
        ("🇪🇬 افلام عربي",          "category.php?cat=5-cimawbas-aflam-3arby"),
        ("🇰🇷 افلام كورية",        "category.php?cat=the-korean-movies"),
        ("🇹🇷 أفلام تركية",         "category.php?cat=turkey-movie"),
        ("🌏 افلام اسيوية",        "category.php?cat=Asian-movies"),

        ("📅 افلام 2026",           "category.php?cat=Movies-2026"),
        ("📅 افلام 2025",           "category.php?cat=movies-2025-cimawbas"),
        ("📅 افلام 2024",           "category.php?cat=movies-2024-cimawbas"),
        ("📅 افلام 2023",           "category.php?cat=cimawbas-aflam-2023"),
        ("📅 افلام 2022",           "category.php?cat=aflam-2022"),

        ("📺 مسلسلات اجنبية",      "category.php?cat=english-series-1"),
        ("🇹🇷 مسلسلات تركية",       "category.php?cat=moslslat-turkeya"),
        ("🎌 مسلسلات انمي",        "category.php?cat=animition-cimawbas"),
        ("🇰🇷 مسلسلات كورية",      "category.php?cat=korean-series"),
        ("🌏 مسلسلات اسيوية",      "category.php?cat=Asian-series"),
        ("🇪🇬 مسلسلات عربية",       "category.php?cat=1-cimawbas-mosalsalat-3araby"),

        ("🎬 افلام اكشن",           "category.php?cat=1-action-movies-cimawbas"),
        ("🎬 افلام رعب",            "category.php?cat=horror-movies"),
        ("🎬 افلام اثارة",          "category.php?cat=thriller-movies"),
        ("🎬 افلام غموض",           "category.php?cat=mystery-movies"),
        ("🎬 افلام رومانسية",       "category.php?cat=cimawbas-romantic-movies"),
        ("🎬 افلام جريمة",          "category.php?cat=crime-movies"),
        ("🎬 افلام كوميدي",         "category.php?cat=comedy-movies-cimawbas"),
        ("🎬 افلام خيال علمي",      "category.php?cat=science-fiction-movies"),
        ("🎬 افلام مغامرة",         "category.php?cat=adventure-movies"),
        ("🎬 افلام دراما",          "category.php?cat=drama-movies-cimawbas"),
        ("🎬 افلام سيرة ذاتية",     "category.php?cat=biography-movies"),
        ("🎬 افلام تاريخية",        "category.php?cat=historical-movies"),
        ("🎬 افلام حرب",            "category.php?cat=war-movies"),
        ("🎬 افلام فنتازيا",        "category.php?cat=fantasy-movies"),
        ("🎬 افلام عائلية",         "category.php?cat=family-movies"),
        ("🎬 افلام وثائقية",        "category.php?cat=documentary-movies"),
        ("🎬 افلام موسيقى",         "category.php?cat=music-movies"),
        ("🎬 افلام رياضة",          "category.php?cat=sports-movies"),
        ("🎬 افلام غربي",           "category.php?cat=western-movies"),
    ]

    _NON_MEDIA_HOSTS = (
        "facebook.com", "twitter.com", "instagram.com", "youtube.com/embed",
        "google.com", "t.me", "telegram.me", "whatsapp.com",
        "googletagmanager", "googlesyndication", "doubleclick",
        "google-analytics", "cloudflareinsights", "histats.com",
        "i.ibb.co", "img-place.com",
    )

    _MEDIA_HOST_MARKERS = (
        "streamwish", "filemoon", "dood", "playmogo", "vidhide", "voe",
        "mixdrop", "luluvdo", "lulustream", "streamtape", "mp4upload",
        "vidmoly", "savefiles", "mxcontent", "downet", "ok.ru", "okru",
        "streamruby", "stmruby", "uqload", "byselapuix", "hgcloud",
        "govid", "fastvid", "vidguard", "vidaraa", "morencius",
        "audinifer", "hanerix", "vibuxer", "earnvids", "cloudwindow",
        "upstream", "vidsrc", "superflixapi", "liiivideo", "minochinos",
        "bysebuho", "byse", "n1mwq.org", "streamhg", "vipserver",
    )

    # Template/placeholder markers that never appear on a real poster.
    # "/templates/" catches the melody-lzld.png lazy placeholder; "/img/"
    # catches adjacent template assets; "spacer" catches 1x1 spacers.
    _BAD_IMG_HINTS = (
        "placeholder", "melody-lzld", "loading.gif", "lazy.gif",
        "data:image", "/logo", "custom-logo", "/templates/", "/img/",
        "/favicon", "spacer", "spacer.gif", "blank.gif", "1x1",
        "pixel.gif",
    )

    _IMG_TAG_RE = re.compile(r'<img\b[^>]*?>', re.I | re.S)
    _BG_URL_RE  = re.compile(
        r"background(?:-image)?\s*:\s*url\(\s*['\"]?([^'\")\s]+)",
        re.I,
    )
    _IFRAME_SRC_RE = re.compile(
        r'<iframe[^>]+src\s*=\s*["\']([^"\']+)["\']',
        re.I,
    )

    # ── Construction ────────────────────────────────────────────────────
    def __init__(self):
        super(MyCimaExtractor, self).__init__()
        self.main_url = self.MAIN_URL
        self._resolved_base = None

    # ── Host / validity helpers ─────────────────────────────────────────
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

    def _looks_like_mycima_page(self, html):
        text = html or ""
        return (
            "cimawbas" in text.lower()
            or "vid.mycima.cc" in text.lower()
            or "pm-video-thumb" in text
            or "pm-ul-browse-videos" in text
            or "myNavmenu" in text
            or "watch.php?vid=" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        if not base:
            return False
        test_url = urljoin(base, "category.php?cat=all-english-movies")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("MyCima: deep-content probe failed for {}: {}".format(base, e))
            return False
        if not html or self._is_blocked_page(html, final or ""):
            log("MyCima: deep-content probe empty/blocked for {}".format(base))
            return False
        if html.count("pm-video-thumb") < 6:
            log("MyCima: {} answered but only {} card markers — "
                "not a real listing".format(base, html.count("pm-video-thumb")))
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
                log("MyCima: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain,
                                        extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain

                if not self._is_valid_site_url(final_url):
                    log("MyCima: unexpected host after redirect {}".format(final_url))
                    continue
                if self._is_blocked_page(html, final_url):
                    log("MyCima: blocked {}".format(final_url))
                    continue
                if not (html and self._looks_like_mycima_page(html)):
                    continue

                candidate = self._site_root(final_url)
                if not self._base_serves_deep_content(candidate):
                    log("MyCima: {} is not a content mirror — next".format(candidate))
                    continue

                self._resolved_base = candidate
                self.main_url = candidate
                with _base_cache_lock:
                    _base_cache["url"] = candidate
                    _base_cache["resolved_at"] = now
                    _base_cache["probed_at"] = now
                log("MyCima: selected base {}".format(candidate))
                return candidate

            self._resolved_base = self.MAIN_URL
            self.main_url = self.MAIN_URL
            with _base_cache_lock:
                _base_cache["url"] = self.MAIN_URL
                _base_cache["probed_at"] = now
            log("MyCima: all probes failed, using {}".format(self.MAIN_URL))
            return self._resolved_base

    # ── URL helpers ─────────────────────────────────────────────────────
    def _full_url(self, path):
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
        text = re.sub(r"^(?:مشاهدة\s+فيلم|مشاهدة\s+مسلسل|مشاهدة|تحميل|فيلم|مسلسل)\s+",
                      "", text).strip()
        for word in (" مترجم", " مدبلج", " مترجمة", " مدبلجة",
                     " يوتيوب", " كامل", " كاملة", " اون لاين", " أون لاين",
                     " الاخيرة", " الأخيرة", " والاخيرة", " والأخيرة"):
            if text.endswith(word):
                text = text[:-len(word)].rstrip()
        text = re.sub(r"\s*[-|]\s*(?:cimawbas|سيما\s*وبس|mycima).*$", "",
                      text, flags=re.I).strip()
        return text

    # ── Poster helpers ──────────────────────────────────────────────────
    def _is_real_poster(self, url):
        if not url:
            return False
        low = url.lower().strip()
        if not low:
            return False
        if any(x in low for x in self._BAD_IMG_HINTS):
            return False
        return bool(re.search(r'\.(?:jpe?g|png|webp|gif|avif)(?:[?#]|$)', low))

    def _pick_real_image(self, block):
        if not block:
            return None

        tags = self._IMG_TAG_RE.findall(block)

        # 1. lazy attrs first — a template card often has a placeholder
        #    in `src` and the real file only in one of the data-* attrs.
        lazy_attrs = ("data-echo", "data-lazy-src", "data-original",
                      "data-src", "data-img", "data-image")
        for tag in tags:
            for attr in lazy_attrs:
                m = re.search(attr + r'\s*=\s*["\']([^"\']+)["\']', tag, re.I)
                if m and self._is_real_poster(m.group(1)):
                    return m.group(1).strip()

        # 2. srcset — pick the largest width descriptor
        for tag in tags:
            m = re.search(r'srcset\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if not m:
                continue
            best, best_w = None, -1
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                cand = part.split(" ", 1)[0]
                if not self._is_real_poster(cand):
                    continue
                wm = re.search(r'(\d+)w', part)
                w = int(wm.group(1)) if wm else 0
                if w > best_w:
                    best, best_w = cand, w
            if best:
                return best

        # 3. plain src (last, because it's most likely to be the placeholder)
        for tag in tags:
            m = re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if m and self._is_real_poster(m.group(1)):
                return m.group(1).strip()

        # 4. CSS background-image (used on /play.php for the poster)
        m = self._BG_URL_RE.search(block)
        if m and self._is_real_poster(m.group(1)):
            return m.group(1).strip().rstrip(")'\"")

        return None

    # ── Card parser ─────────────────────────────────────────────────────
    def _extract_cards(self, html, max_items=200):
        items = []
        seen_urls = set()
        if not html:
            return items

        # Split on the outer <li> wrapper used on listings and carousels.
        # Both `col-*` (grid) and `slick-slide` (carousel) variants exist
        # on the same page — same inner structure in both cases.
        blocks = re.split(
            r'(?=<li[^>]+class="[^"]*(?:\bcol-\w+|slick-slide)[^"]*")',
            html)
        for block in blocks:
            # A card must carry the thumb container AND a watch link —
            # both appear on every real card and on neither the page
            # header nor the sidebar.
            if "pm-video-thumb" not in block and "block-post" not in block:
                continue
            if "watch.php?vid=" not in block:
                continue

            url_m = re.search(r'href="([^"]*watch\.php\?vid=[^"]+)"', block, re.I)
            if not url_m:
                continue
            url = self._full_url(url_m.group(1))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Title — prefer the img alt (matches the h3 exactly on this
            # template), fall back to the anchor title.
            title = ""
            alt_m = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
            if alt_m:
                title = alt_m.group(1)
            if not title:
                tm = re.search(r'<a[^>]+title="([^"]+)"', block, re.I)
                if tm:
                    title = tm.group(1)
            title = self._clean_title(title)
            if not title:
                continue

            # Poster — see _pick_real_image for the lazy/placeholder dance.
            poster = ""
            raw = self._pick_real_image(block)
            if raw:
                poster = self._full_url(raw)
            if _DEBUG_POSTER:
                log("MyCima poster: raw={!r} url={!r} block-id={}".format(
                    raw, poster, url.split("vid=")[-1][:16]))

            # Rating — e.g. <span class="rating">7.6 <i ...></i></span>
            rating = ""
            rating_m = re.search(
                r'<span[^>]+class="[^"]*\brating\b[^"]*"[^>]*>\s*([\d.]+)',
                block, re.I)
            if rating_m:
                rating = rating_m.group(1).strip()

            # Quality / status ribbon — e.g. <span class="hot">مدبلج</span>
            label = ""
            ribbon_m = re.search(
                r'<span[^>]+class="[^"]*\bhot\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.I)
            if ribbon_m:
                label = ribbon_m.group(1).strip()

            # Episode marker — e.g. <span class="ep">الحلقة 5</span>
            episode = ""
            ep_m = re.search(
                r'<span[^>]+class="[^"]*\bep\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.I)
            if ep_m:
                episode = ep_m.group(1).strip()

            # Year pulled out of the title (kept as its own field for the
            # grid badge, same convention as egydead / topcinema).
            year = ""
            ym = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            if ym:
                year = ym.group(1)

            # Type detection. Watch URLs don't carry a /series/ segment on
            # this site (everything is /watch.php?vid=<hash>), so the title
            # is what disambiguates.
            url_low = url.lower()
            if episode or "حلقة" in title or "الحلقة" in title:
                item_type = "episode"
            elif ("/series/" in url_low or "مسلسل" in title
                  or "الموسم" in title):
                item_type = "series"
            else:
                item_type = "movie"

            display_title = title
            if episode and episode not in display_title:
                display_title = "{} — {}".format(title, episode)

            items.append({
                "title": display_title,
                "url": url,
                "poster": poster,
                "thumbnail": poster,
                "image": poster,
                "plot": label,
                "label": label,
                "rating": rating,
                "year": year,
                "episode": episode,
                "type": item_type,
                "_action": "details",
            })
            if len(items) >= max_items:
                break

        return items

    # ── Pagination ──────────────────────────────────────────────────────
    def _parse_pagination(self, html, current_url):
        next_url = None
        pag_m = re.search(
            r'<ul[^>]+class="[^"]*pagination[^"]*"[^>]*>(.*?)</ul>',
            html or "", re.S | re.I)
        if not pag_m:
            return None
        pag_html = pag_m.group(1)

        # Current page: prefer the explicit <li class="active"> label, then
        # fall back to deriving it from the URL.
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

        # Strategy 1: numeric anchor whose visible text is exactly next_page.
        # We unescape &amp; in the captured href once we've got it — the
        # regex itself sees the raw HTML.
        for m in re.finditer(
                r'<a[^>]+href="([^"]+)"[^>]*>\s*{}\s*</a>'.format(next_page),
                pag_html, re.I):
            next_url = self._full_url(m.group(1).replace("&amp;", "&"))
            if next_url:
                break

        # Strategy 2: any href containing [?&]page=next_page. The raw HTML
        # escapes the & as &amp;, so unescape FIRST — otherwise
        # "?&amp;page=3" never matches the [?&]page=3 shape.
        if not next_url:
            unescaped_pag = pag_html.replace("&amp;", "&")
            m = re.search(
                r'href="([^"]*[?&]page={}\b[^"]*)"'.format(next_page),
                unescaped_pag, re.I)
            if m:
                next_url = self._full_url(m.group(1))

        # Strategy 3 (last resort): the RTL "next" arrow. On this template
        # the visible arrow is <i class="fa fa-arrow-left"></i> because the
        # whole page is RTL-flipped — "next" points visually left.
        if not next_url:
            m = re.search(
                r'<a[^>]+href="([^"]+)"[^>]*>\s*<i[^>]+class="[^"]*fa-arrow-left[^"]*"',
                pag_html, re.I | re.S)
            if m:
                next_url = self._full_url(m.group(1).replace("&amp;", "&"))

        if next_url and next_url != current_url:
            return {
                "title": "➡️ الصفحة التالية",
                "url": next_url,
                "type": "category",
                "_action": "category",
            }
        return None

    # ── Public: categories ──────────────────────────────────────────────
    def get_categories(self, mtype="movie"):
        self._get_base()
        cats = []
        for title, path in self._CATEGORIES:
            cats.append({
                "title": title,
                "url": self._full_url(path),
                "type": "category",
                "_action": "category",
            })
        return cats

    # ── Public: category / listing ──────────────────────────────────────
    def get_category_items(self, url, page=1):
        fetch_url = self._full_url(url)

        if page and page > 1:
            parsed = urlparse(fetch_url)
            q = parse_qs(parsed.query or "", keep_blank_values=True)
            q["page"] = [str(page)]
            # The site's own pagination adds order=DESC on category
            # listings — preserve it if already present, don't add it
            # on the flat movies.php/all-series.php routes.
            fetch_url = parsed._replace(
                query=urlencode([(k, v[0]) for k, v in q.items()])
            ).geturl()

        log("MyCima: fetching listing {}".format(fetch_url))
        html, final_url = fetch(fetch_url, referer=self._get_base(),
                                extra_headers=_PROBE_HEADERS)
        if not html:
            log("MyCima: get_category_items failed for {}".format(fetch_url))
            return []

        items = self._extract_cards(html)
        log("MyCima: {} card(s) extracted".format(len(items)))

        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        return items

    # ── Public: search ──────────────────────────────────────────────────
    def search(self, query, page=1):
        q = quote(str(query or ""), safe="")
        search_url = self._full_url("search.php?keywords=" + q)
        if page and page > 1:
            search_url += "&page=" + str(page)

        log("MyCima: search '{}'".format(query))
        html, _ = fetch(search_url, referer=self._get_base(),
                        extra_headers=_PROBE_HEADERS)
        if not html:
            return []

        items = self._extract_cards(html)
        if page == 1:
            nxt = self._parse_pagination(html, search_url)
            if nxt:
                items.append(nxt)
        log("MyCima: search '{}' → {} items".format(query, len(items)))
        return items

    # ── Detail meta ─────────────────────────────────────────────────────
    def _extract_detail_meta(self, html):
        title = ""
        title_m = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if title_m:
            title = self._clean_title(title_m.group(1))
        if not title:
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
            if tm:
                title = self._clean_title(tm.group(1).split("|")[0])

        # Poster — itemprop="image" is the canonical upload thumb; the
        # play.php template overwrites og:image late with social-thumb.php
        # so og:image is only a fallback.
        poster = ""
        poster_m = re.search(
            r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if poster_m:
            poster = self._full_url(poster_m.group(1))

        if not poster:
            poster_m = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if poster_m:
                poster = self._full_url(poster_m.group(1))

        if not poster:
            tw_m = re.search(
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if tw_m:
                poster = self._full_url(tw_m.group(1))

        if not poster:
            raw = self._pick_real_image(html)
            if raw:
                poster = self._full_url(raw)

        if not poster:
            img_m = re.search(
                r'<div[^>]+class="[^"]*pm-video-thumb[^"]*"[^>]*>.*?<img[^>]+src="([^"]+)"',
                html, re.S | re.I)
            if img_m:
                poster = self._full_url(img_m.group(1))

        # play.php puts the poster in a CSS background on the player.
        if not poster:
            bg_m = re.search(
                r'class="[^"]*video-bibplayer-poster[^"]*"[^>]*style="[^"]*'
                r'background-image:\s*url\(([^)]+)\)',
                html, re.I)
            if bg_m:
                poster = self._full_url(bg_m.group(1).strip("'\" "))

        plot = ""
        desc_m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if desc_m:
            plot = re.sub(r"<[^>]+>", " ", desc_m.group(1)).strip()
        if not plot:
            story_m = re.search(
                r'<div[^>]+class="[^"]*(?:pm-series-description|story|plot)[^"]*"[^>]*>(.*?)</div>',
                html, re.S | re.I)
            if story_m:
                plot = re.sub(r"<[^>]+>", " ", story_m.group(1)).strip()

        year = ""
        ym = re.search(r'\b(19\d{2}|20\d{2})\b', title + " " + plot)
        if ym:
            year = ym.group(1)

        if _DEBUG_POSTER:
            log("MyCima detail: poster={!r} title={!r}".format(poster, title))

        return title, poster, plot, year

    # ── Server extraction (watch / play page) ──────────────────────────
    def _decode_wecima_b64(self, raw):
        if not raw:
            return ""
        s = str(raw).strip().replace("+", "").replace(" ", "")
        if s.startswith("http://") or s.startswith("https://"):
            return s
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

    @staticmethod
    def _unescape_html_fragment(raw):
        """Turn `&lt;iframe src='…'&gt;` back into `<iframe src='…'>`."""
        if not raw:
            return ""
        try:
            out = _htmllib.unescape(raw)
        except Exception:
            out = (raw.replace("&lt;", "<")
                      .replace("&gt;", ">")
                      .replace("&quot;", '"')
                      .replace("&#39;", "'")
                      .replace("&apos;", "'")
                      .replace("&amp;", "&"))
        return out

    def _extract_iframe_src(self, blob):
        if not blob:
            return ""
        m = self._IFRAME_SRC_RE.search(blob)
        if m:
            return m.group(1).strip()
        # Fallback: bare URL with no iframe wrapper
        m = re.search(r'https?://[^\s"\'<>]+', blob)
        if m:
            return m.group(0).strip()
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
            ("cloudwindow", "Voe"), ("upstream", "UpStream"),
            ("superflixapi", "SuperFlix"), ("vidsrc", "VidSrc"),
            ("liiivideo", "Vipserver"), ("minochinos", "Minochinos"),
            ("bysebuho", "Bysebuho"), ("byse", "Byse"),
            ("n1mwq.org", "Byse"),
        )
        for frag, label in mapping:
            if frag in lowered:
                return label
        return host or "Server"

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

    def _extract_watch_servers(self, html, page_url):
        servers = []
        seen = set()
        if not html:
            return servers

        # ── Strategy 0: <ul class="list_servers"> / list_embedded ──────
        # The 3arbserv template stores the embed HTML (escaped) inside a
        # `data-embed` attribute on each <li>. Structure:
        #   <li id="server_X" data-embed="&lt;iframe src='…' …&gt;">
        #       <a><span>…</span> <strong>Label</strong></a>
        #   </li>
        for ul_m in re.finditer(
                r'<ul[^>]+class="[^"]*list_servers[^"]*"[^>]*>(.*?)</ul>',
                html, re.S | re.I):
            inner = ul_m.group(1)
            for li_m in re.finditer(r'<li\b[^>]*>(.*?)</li>', inner, re.S | re.I):
                li_full = li_m.group(0)              # includes opening <li …>
                li_inner = li_m.group(1)
                em = re.search(r'data-embed\s*=\s*"([^"]*)"', li_full, re.S)
                if not em:
                    continue
                unescaped = self._unescape_html_fragment(em.group(1))
                src = self._extract_iframe_src(unescaped)
                if not src:
                    continue
                name_m = re.search(r'<strong>(.*?)</strong>',
                                   li_inner, re.S | re.I)
                label = None
                if name_m:
                    label = re.sub(r"<[^>]+>", "", name_m.group(1)).strip()
                    label = self._unescape_html_fragment(label) or label
                self._add_server(servers, seen, src, label=label)

        # ── Strategy 0b: bare data-embed attributes anywhere ───────────
        for m in re.finditer(r'data-embed\s*=\s*"([^"]*)"', html, re.S):
            unescaped = self._unescape_html_fragment(m.group(1))
            src = self._extract_iframe_src(unescaped)
            if src:
                self._add_server(servers, seen, src)

        # ── Strategy 1: WatchServersList (legacy PHP Melody) ───────────
        block_m = re.search(
            r'class="[^"]*WatchServersList[^"]*"[^>]*>(.*?)</ul>',
            html, re.S | re.I)
        if block_m:
            inner = block_m.group(1)
            for m in re.finditer(
                    r'(?:data-url|data-watch|href|data-link)=["\']([^"\']+)["\'][^>]*>(.*?)'
                    r'</(?:button|li|div|a|span)>',
                    inner, re.S | re.I):
                raw = m.group(1)
                decoded = self._decode_wecima_b64(raw) or raw
                name_m = re.search(r'<strong>(.*?)</strong>', m.group(2), re.S | re.I)
                label = None
                if name_m:
                    label = re.sub(r"<[^>]+>", "", name_m.group(1)).strip()
                self._add_server(servers, seen, decoded, label=label)

        # ── Strategy 2: other data-* attributes anywhere ───────────────
        for attr in ("data-watch", "data-url", "data-server", "data-link",
                     "data-iframe", "data-src"):
            for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                raw = m.group(1)
                # Skip anything that looks like escaped HTML — Strategy 0
                # already handled those and unescaping here would just
                # re-add them.
                if "&lt;" in raw or "<" in raw:
                    continue
                decoded = self._decode_wecima_b64(raw) or raw
                self._add_server(servers, seen, decoded)

        # ── Strategy 3: <iframe src> (server-rendered currently-playing) ──
        for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
            self._add_server(servers, seen, m.group(1).strip())

        # ── Strategy 4: <source> tags ─────────────────────────────────
        for m in re.finditer(
                r'<source[^>]+src=["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            u = _correct_stream_url(m.group(1))
            self._add_server(servers, seen, u, srv_type="direct")

        # ── Strategy 5: <video src> ───────────────────────────────────
        for m in re.finditer(
                r'<video[^>]+src=["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            u = _correct_stream_url(m.group(1))
            self._add_server(servers, seen, u, srv_type="direct")

        # ── Strategy 6: JS literals file:/src:/url: ───────────────────
        for m in re.finditer(
                r'(?:file|src|url|source)\s*[:=]\s*["\']'
                r'([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            u = _correct_stream_url(m.group(1).replace("\\/", "/"))
            self._add_server(servers, seen, u, srv_type="direct")

        # ── Strategy 7: known media host URLs anywhere ────────────────
        host_re = re.compile(
            r'(https?://(?:[^"\'\s<>]*(?:'
            + "|".join(self._MEDIA_HOST_MARKERS)
            + r'))[^"\'\s<>]*)', re.I)
        for m in host_re.finditer(html):
            self._add_server(servers, seen, m.group(1).replace("\\/", "/"))

        if _DEBUG_SERVERS:
            for s in servers:
                log("MyCima server: {!r} — {}".format(s.get("name"), s.get("url")))

        log("MyCima: {} watch server(s) for {}".format(
            len(servers), page_url[:80]))
        return servers

    # ── Download links ──────────────────────────────────────────────────
    def _extract_download_links(self, html):
        downloads = []
        seen = set()
        if not html:
            return downloads

        block = None
        for pat in (
            r'<ul[^>]+class="[^"]*(?:donwload-servers-list|download-items|download-list|List--Download)[^"]*"[^>]*>(.*?)</ul>',
            r'<div[^>]+class="[^"]*(?:downloadMaster|Download--Wecima--Single)[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]+class="[^"]*(?:download|Download)[^"]*"[^>]*>(.*?)</div>',
        ):
            m = re.search(pat, html, re.S | re.I)
            if m:
                block = m.group(1)
                break

        scan = block if block is not None else html
        for item_m in re.finditer(
                r'<(?:li|a|div)[^>]*?'
                r'(?:data-href|data-url|data-link|href)=["\']([^"\']+)["\']'
                r'[^>]*>(.*?)</(?:li|a|div)>',
                scan, re.S | re.I):
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
            if self._is_valid_site_url(url):
                continue

            seen.add(url)

            resolution = ""
            size = ""
            quality = ""
            res_m = re.search(r'class="[^"]*\bresolution\b[^"]*"[^>]*>\s*([^<]+?)\s*<',
                              inner, re.S | re.I)
            if res_m:
                resolution = res_m.group(1).strip()
            sz_m = re.search(r'class="[^"]*\bsize\b[^"]*"[^>]*>\s*([^<]+?)\s*<',
                             inner, re.S | re.I)
            if sz_m:
                size = sz_m.group(1).strip()
            q_m = re.search(r'class="[^"]*\bquality\b[^"]*"[^>]*>\s*([^<]+?)\s*<',
                            inner, re.S | re.I)
            if q_m:
                quality = q_m.group(1).strip()

            if not resolution:
                m2 = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b', inner, re.I)
                if m2:
                    resolution = m2.group(1)
            if not size:
                m2 = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b', inner, re.I)
                if m2:
                    size = m2.group(1)
            if not quality:
                host_m = re.search(r'https?://([^/]+)', url)
                if host_m:
                    quality = self._host_display_name(host_m.group(1))

            downloads.append({
                "resolution": resolution,
                "size": size,
                "quality": quality,
                "url": url,
            })

        log("MyCima: {} download link(s)".format(len(downloads)))
        return downloads

    # ── get_page — full detail path ─────────────────────────────────────
    def get_page(self, url, m_type=None):
        result = {
            "url": url,
            "title": "",
            "poster": "",
            "thumbnail": "",
            "image": "",
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
            log("MyCima: get_page failed for {}".format(url))
            return result

        title, poster, plot, year = self._extract_detail_meta(html)
        result["title"] = title
        result["poster"] = poster
        result["thumbnail"] = poster
        result["image"] = poster
        result["plot"] = plot
        result["year"] = year
        result["url"] = final_url or url

        url_low = (final_url or url).lower()
        if "/episode" in url_low or "الحلقة" in title or "حلقة" in title:
            result["type"] = "episode"
        elif "/series/" in url_low or "مسلسل" in title or "/serie/" in url_low:
            result["type"] = "series"
        elif m_type:
            result["type"] = m_type

        servers = self._extract_watch_servers(html, final_url or url)

        # Lazy reveal — some templates render the server list after ?watch=1
        if not servers:
            reveal_url = ((final_url or url)
                          + ("&" if "?" in (final_url or url) else "?")
                          + "watch=1")
            log("MyCima: no servers on initial load, retrying: {}"
                .format(reveal_url[:100]))
            rev_html, _ = fetch(reveal_url, referer=final_url or url,
                                extra_headers=_PROBE_HEADERS)
            if rev_html:
                servers = self._extract_watch_servers(rev_html, reveal_url)

        # If we landed on the poster-only play.php view, the server list
        # lives on watch.php — try that as a last resort.
        if not servers:
            vid_m = re.search(r'[?&]vid=([A-Za-z0-9]+)', final_url or url)
            if vid_m:
                watch_url = self._full_url("watch.php?vid=" + vid_m.group(1))
                if watch_url != (final_url or url):
                    log("MyCima: falling back to {}".format(watch_url))
                    w_html, _ = fetch(watch_url, referer=final_url or url,
                                      extra_headers=_PROBE_HEADERS)
                    if w_html:
                        servers = self._extract_watch_servers(w_html, watch_url)

        result["servers"] = servers
        result["downloads"] = self._extract_download_links(html)

        # Episodes / seasons — series pages
        if result["type"] == "series":
            episodes = []
            seen_eps = set()
            eps_container_m = re.search(
                r'<(?:div|ul)[^>]+(?:id|class)="[^"]*(?:eps|episodes|EpsList)[^"]*"[^>]*>(.*?)</(?:div|ul)>',
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
            log("MyCima: {} episode(s) found".format(len(episodes)))

        log("MyCima: get_page {} → {} server(s), {} download(s), type={}"
            .format(url[:60], len(result["servers"]),
                    len(result["downloads"]), result["type"]))
        return result

    # ── extract_stream ─────────────────────────────────────────────────
    def extract_stream(self, url):
        """Resolve a watch-page server URL to a playable stream."""
        from .base import extract_stream as base_extract_stream
        from .base import extract_stream_all as base_extract_all

        if not url:
            return None, "", self._get_base(), []

        clean = _correct_stream_url(url.strip())

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

        try:
            variants = base_extract_all(clean)
            if variants:
                best_url, best_quality = variants[0]
                others = [(q, u) for u, q in variants[1:]]
                return best_url, best_quality, self._get_base(), others
        except Exception as e:
            log("MyCima: extract_stream_all failed for {}: {}"
                .format(clean[:80], e))

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
            log("MyCima: extract_stream failed for {}: {}"
                .format(clean[:80], e))

        return None, "", self._get_base(), []