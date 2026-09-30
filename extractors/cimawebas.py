# -*- coding: utf-8 -*-
"""
CimaWebas extractor — cimawebas.com (سـيمـا وبس)
==================================================
WordPress 7.x site running the NovaCinemaPlus theme (class prefix `ncp-`).

Confirmed markup (2026-09-27 captures):

  Home / listings:
    /last/                (المضاف حديثا)
    /movies/              (أفلام)
    /series/              (مسلسلات)
    /complated-series/    (تحميل مسلسلات كاملة برابط واحد)
    /assemblies/          (سلاسل الأفلام)
    /imdb/                (الأفلام الأعلى تقييماً IMDb)
    /top-rating-imdb-series/
    /trending/            (الأعمال الرائجة)
    /category/<arabic-slug>/            e.g. افلام-اجنبي, مسلسلات-عربي
    /category/<arabic-slug>/page/N/     WordPress pagination
    /page/N/                            site-wide pagination for /last/, /movies/, ...

  Search:  /?s=<query>   (+ ncp_search_type=all|movies|series|episodes|actors)

  Detail pages:
    /<slug>/            (movie landing)
    /series/<slug>/     (series landing)
    /<slug>/watch/      (watch page — where server list lives)
    /<slug>/download/   (download page)

  Landing page (verbatim, confirmed 2026-09-28):
    <article class="ncp-single post-1367567 ...">
      <div class="ncp-single-poster is-loaded">
        <img src="POSTER" data-src="POSTER" alt="TITLE" ...>
      </div>
      <div class="ncp-single-content">
        <h1 class="ncp-single-title">TITLE</h1>
        <div class="ncp-single-meta-row">
          <span class="ncp-single-rating">…</span><span>2026</span><span>1080p HDCAM</span>
        </div>
        <div class="ncp-single-actions">
          <a class="ncp-single-action is-primary"
             href="https://cimawebas.com/<slug>/watch/">مشاهدة الآن</a>
          <a class="ncp-single-action is-download"
             href="https://cimawebas.com/<slug>/download/">تحميل الآن</a>
        </div>
        …
      </div>
    </article>

    NOTE: the landing page has NO server list. The servers only appear
    on the /watch/ page.

  Watch page (verbatim, confirmed 2026-09-28):
    <header class="ncp-stream-header">
      <a class="ncp-stream-thumb is-loaded" href="...">
        <img src="POSTER" data-src="POSTER" alt="TITLE" ...>
      </a>
      <div class="ncp-stream-header-copy">
        <h1>TITLE</h1>
        …
      </div>
    </header>
    <div class="ncp-server-list" role="list" aria-label="سيرفرات المشاهدة">
      <button class="ncp-server-button" type="button"
              data-ncp-watch-server="https://firestream.to/e/SII8kK0n"
              data-server-name="firestream" aria-pressed="false">
        <span class="ncp-stream-icon">…</span>
        <span>سيرفر 1</span>
        <strong>firestream</strong>
      </button>
      …  (10 buttons on the confirmed capture: firestream, down,
          earnvids, streamwish, updown, mixdrop, d0o0d, byseqekaho,
          streamtape, VidAPI)
    </div>
    <div class="ncp-player-frame ncp-fullscreen-control-visible">
      <iframe class="ncp-watch-iframe"
              src="https://firestream.to/e/SII8kK0n"
              title="مشاهدة …" allow="autoplay; encrypted-media; …"
              allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
    </div>
    <aside class="ncp-stream-cta is-download">
      <a href="https://cimawebas.com/<slug>/download/">صفحة التحميل</a>
    </aside>

Resolved stream URLs (confirmed 2026-09-28 — what the base resolvers
return for each server, i.e. what `extract_stream()` must pass through
or resolve to):

    firestream    →  https://us-cdn-1.firestream.to/encodings/<uuid>/.../video.mp4/video.m3u8?md5=…&expires=…
                     https://edge1-madrid-sprintcdn.r66nv9ed.com/... (older variant)
    down          →  https://serv-stream-cdn12.cdn-video.xyz/vp/01/00082/<id>_l/<title>.mp4?t=…
    updown        →  https://dayt-fer-dw9.gamescdn.online/d/<token>/video.mp4
                     https://dayt-fer-dw9.gamescdn.online/d/<token>/<title>.mp4
    byseqekaho    →  https://edge1-madrid-sprintcdn.r66nv9ed.com/hls2/.../master.m3u8?t=…
                     https://edge1-madrid-sprintcdn.r66nv9ed.com/hls2/.../index-v1-a1.m3u8?t=…

    The CDN hosts above are all direct-media hosts — they must be
    recognised as such so `extract_stream` can pass them through
    untouched rather than recursively trying to resolve them again.

Notes carried over from the family (egydead.py / alooytv.py / mywecima.py):
  * Single-flight base probe + TTL + cooldown.
  * `_base_serves_deep_content` rejects homepage-only mirrors that answer every
    deep URL with the same page (would collapse every category to one grid).
  * `_full_url` percent-encodes non-ASCII — every category slug here is Arabic.
  * The watch page's server list uses `data-ncp-watch-server` (a site-specific
    data attribute), *not* the generic `data-server` used elsewhere in the
    family. A dedicated scan handles it before the generic data-* pass.

Recent fixes (2026-09-28):
  * `_add_server` now rejects URLs on the site's own host. The generic
    data-*/href pass previously matched every navigation link on the page
    (brand, breadcrumbs, the /download/ CTA, related cards) and treated them
    as servers — which also prevented `get_page` from ever following through
    to the real `/watch/` page on landing pages.
  * `_extract_detail_meta` now prefers the on-page poster
    (`.ncp-single-poster img` on landing, `.ncp-stream-thumb img` on watch)
    over `og:image`. This site sets `og:image` to its site icon on every
    page, so `og:image` would otherwise always return the logo.
  * `_extract_cards` now requires each split block to actually start with
    `<article>` before parsing, so the head/header prefix (which contains
    `ncp-card` inside inline CSS) can never be mis-parsed as a card.
  * `get_page` now:
      - Detects when it's handed a `/watch/` URL directly and fetches it
        first, then fetches the landing page only for better metadata.
      - Fetches the landing page AND the corresponding watch page
        deterministically — never relies on URL equality to decide whether
        to fetch the watch page.
      - Has a last-ditch fallback that constructs `…/watch/` directly from
        the URL if the action link was not found.
      - Logs every step so the pipeline can be debugged from the console.
  * Confirmed direct-media CDN hosts (firestream.to, cdn-video.xyz,
    gamescdn.online, sprintcdn/r66nv9ed.com) added to `_MEDIA_HOST_MARKERS`
    so `_extract_watch_servers` picks them up if they ever appear inline,
    and `extract_stream` recognises them as direct streams to pass through
    instead of trying to re-resolve them.
  * `_host_display_name` now maps the new CDN hosts back to their parent
    service (gamescdn.online → UpDown, sprintcdn/r66nv9ed → Byse,
    cdn-video.xyz → VidTube), so the UI shows a clean label instead of a
    raw CDN hostname.
  * `_NON_MEDIA_HOSTS` now blocks ad/redirect hosts seen during resolution
    (e.g. `ymvhnxcaanmuy.site`) so they can never be surfaced as servers.
"""

import re
import time
import threading

from .base import (
    BaseExtractor, fetch, log,
    _correct_stream_url,
    extract_iframes,
)

try:
    from urllib.parse import (
        urljoin, urlparse, quote, quote_plus, unquote, parse_qs,
    )
except ImportError:                                # py2 shim
    from urllib import quote, quote_plus
    from urlparse import urljoin, urlparse, parse_qs


# ─── Process-wide base cache (survives extractor-instance reuse) ────────────
_BASE_CACHE_TTL = 300          # success cache — 5 minutes
_PROBE_COOLDOWN = 30           # don't re-scan domains within 30s after a failure
_base_cache = {"url": None, "resolved_at": 0, "probed_at": 0}
_base_cache_lock = threading.Lock()
_probe_lock = threading.Lock()

_PROBE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
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


class CimaWebasExtractor(BaseExtractor):
    """Extractor for CimaWebas (cimawebas.com)."""

    MAIN_URL = "https://cimawebas.com/"

    DOMAINS = [
        "https://cimawebas.com/",
        "https://www.cimawebas.com/",
    ]

    VALID_HOST_MARKERS = ("cimawebas",)
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com", "watch-it-legally")

    CLEAN_WORDS = [
        "مشاهدة", "تحميل", "فيلم", "مسلسل", "الحلقة", "حلقة",
        "مترجم", "مترجمة", "مدبلج", "مدبلجة",
        "اون لاين", "أون لاين", "اونلاين",
        "بجودة", "عالية", "كامل", "حصريا",
        "والاخيرة", "والأخيرة", "الاخيرة", "الأخيرة",
        "جميع مواسم", "جميع الحلقات", "الموسم الأول", "الموسم الاول",
    ]

    # Third-party scripts / social embeds / ad-redirect hosts we never want
    # as servers. The `ymvhnxcaanmuy.site` entry is the ad/redirect hop seen
    # when resolving FireStream embeds — it is NOT media.
    _NON_MEDIA_HOSTS = (
        "facebook.com", "twitter.com", "instagram.com", "youtube.com/embed",
        "google.com", "t.me", "telegram.me", "whatsapp.com",
        "googletagmanager", "googlesyndication", "doubleclick",
        "google-analytics", "cloudflareinsights",
        "effectivecpmnetwork", "w.org",
        # Ad/redirect hops confirmed 2026-09-28:
        "ymvhnxcaanmuy.site",
        "dressedoutrageousrun.com",
        "omoonsih.net",
        "mc.yandex.ru",
    )

    # Known streaming hosts + direct-media CDN hosts. The CDN hosts are the
    # ones the base resolvers hand back as playable URLs (see module
    # docstring). Their presence here lets `_extract_watch_servers` pick
    # them up if they appear inline in the watch-page HTML, and lets
    # `extract_stream` recognise them as passthrough media.
    _MEDIA_HOST_MARKERS = (
        # ── Embed-hosting services ─────────────────────────────────────
        "streamwish", "wishfast", "filemoon", "dood", "playmogo", "vidhide",
        "voe", "mixdrop", "luluvdo", "lulustream", "streamtape", "mp4upload",
        "vidmoly", "savefiles", "mxcontent", "downet", "ok.ru", "okru",
        "streamruby", "stmruby", "uqload", "upstream", "vidguard", "vgfplay",
        "fastvid", "govid", "hgcloud", "hanerix", "audinifer", "morencius",
        "vibuxer", "earnvids", "cloudwindow", "vidaraa", "byselapuix",
        "cybervynx", "dhcplay", "superflixapi", "zxcstream", "vidcore",
        "vidsrc", "vidfast", "vidapi", "cdnm.ink", "nontongo",
        "downet.net", "af3.downet", "drive.google", "mega.nz", "mediafire",
        # Added 2026-09-28 from live watch-page captures:
        "topcinemaa", "topcinema.io",
        "d0o0d", "vaplayer", "nextgencloudfabric",
        "streamwish.fun", "digitalnomadventures", "cloudatacdn",
        # Embed services on the two additional watch captures:
        "firestream", "vidtube.one", "updown.icu", "mixdrop.ps",
        "streamtape.cc", "byseqekaho.com",
        # ── Direct-media CDN hosts (what the base resolvers return) ────
        "us-cdn-1.firestream.to",
        "gamescdn.online",
        "sprintcdn",
        "r66nv9ed.com",
        "cdn-video.xyz",
        "serv-stream-cdn",
    )

    # Account/UI paths on the site's own domain the generic data-* scan can
    # pick up — never real servers.
    _NON_MEDIA_PATHS = (
        "/login", "/logout", "/register", "/signup", "/favorite",
        "/wp-login", "/wp-admin", "/wp-json", "/feed",
    )

    # Extensions we always treat as passthrough media (no resolver needed).
    _DIRECT_MEDIA_EXT = (".mp4", ".m3u8", ".mkv", ".ts", ".mpd")

    def __init__(self):
        super(CimaWebasExtractor, self).__init__()
        self.main_url = self.MAIN_URL
        self._resolved_base = None

    # ── Host / URL helpers ─────────────────────────────────────────────
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

    def _is_direct_media_url(self, url):
        """True if the URL's path looks like a direct playable stream."""
        if not url:
            return False
        path = url.lower().split("?", 1)[0].split("#", 1)[0]
        return path.endswith(self._DIRECT_MEDIA_EXT)

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

    def _looks_like_cimawebas_page(self, html):
        text = html or ""
        return (
            "cimawebas" in text.lower()
            or "CimaWebas" in text
            or "سـيمـا وبس" in text
            or "ncp-card" in text
            or "ncp-site-header" in text
            or "NovaCinemaPlus" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _base_serves_deep_content(self, base):
        """Confirm the candidate actually serves listing pages, not just a
        homepage/landing. A real /movies/ page contains dozens of
        `.ncp-card` blocks; a landing page does not."""
        if not base:
            return False
        test_url = urljoin(base, "movies/")
        try:
            html, final = fetch(test_url, referer=base,
                                extra_headers=_PROBE_HEADERS)
        except Exception as e:
            log("CimaWebas: deep-content probe failed for {}: {}".format(base, e))
            return False
        if not html or self._is_blocked_page(html, final or ""):
            log("CimaWebas: deep-content probe empty/blocked for {}".format(base))
            return False
        if html.count("ncp-card") < 6:
            log("CimaWebas: {} answered but only {} ncp-card markers — "
                "likely a landing, not a content mirror".format(
                    base, html.count("ncp-card")))
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
                log("CimaWebas: probing {}".format(domain))
                html, final_url = fetch(domain, referer=domain,
                                        extra_headers=_PROBE_HEADERS)
                final_url = final_url or domain

                if not self._is_valid_site_url(final_url):
                    log("CimaWebas: unexpected host after redirect {}"
                        .format(final_url))
                    continue
                if self._is_blocked_page(html, final_url):
                    log("CimaWebas: blocked {}".format(final_url))
                    continue
                if not (html and self._looks_like_cimawebas_page(html)):
                    continue

                candidate = self._site_root(final_url)
                if not self._base_serves_deep_content(candidate):
                    log("CimaWebas: {} is not a content mirror — trying next"
                        .format(candidate))
                    continue

                self._resolved_base = candidate
                self.main_url = candidate
                with _base_cache_lock:
                    _base_cache["url"] = candidate
                    _base_cache["resolved_at"] = now
                    _base_cache["probed_at"] = now
                log("CimaWebas: selected base {}".format(candidate))
                return candidate

            self._resolved_base = self.MAIN_URL
            self.main_url = self.MAIN_URL
            with _base_cache_lock:
                _base_cache["url"] = self.MAIN_URL
                _base_cache["probed_at"] = now
            log("CimaWebas: all probes failed, using {}".format(self.MAIN_URL))
            return self._resolved_base

    # ── URL / text helpers ─────────────────────────────────────────────
    def _full_url(self, path):
        """
        Resolve a relative/absolute URL against the base, then percent-encode
        non-ASCII. The encoding step MUST run for ABSOLUTE URLs too — every
        category slug here is Arabic (‎/category/افلام-اجنبي/‎) and the HTTP
        client cannot put raw non-ASCII bytes in a request line.
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
        text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Strip the site's own trailing "| سـيمـا وبس" if present.
        text = re.sub(r"\s*[|\-]\s*(?:سـيمـا\s*وبس|CimaWebas|سيما\s*وبس)\s*$",
                      "", text, flags=re.I).strip()
        return text

    def _encode_query(self, query):
        try:
            return quote_plus(str(query or ""))
        except Exception:
            return str(query or "")

    def _unwrap_player_proxy(self, url):
        """
        Some watch-page servers wrap the real target as a URL-encoded query
        parameter, e.g.:

          https://topcinemaa.com/play.php?to=streamwish.fun%2Fe%2F6an2ocqsdkmh

        Passing the unwrapped URL to the base resolvers is a small win, but
        not required for correctness — the resolver follows the wrapper
        itself. We only unwrap when the outer host is a known proxy to avoid
        mangling unrelated URLs.
        """
        if not url:
            return url
        low = url.lower()
        if ("topcinemaa.com/play.php" not in low
                and "topcinema.io/play.php" not in low):
            return url
        try:
            qs = parse_qs(urlparse(url).query)
            inner = (qs.get("to") or [""])[0]
            if not inner:
                return url
            if not inner.startswith("http"):
                inner = "https://" + inner
            return inner
        except Exception:
            return url

    # ── Watch-URL helpers ──────────────────────────────────────────────
    def _is_watch_page_url(self, url):
        """True if the URL looks like a /watch/ page URL."""
        if not url:
            return False
        path = url.split("?")[0].split("#")[0]
        return path.rstrip("/").lower().endswith("/watch")

    def _landing_url_from_watch(self, url):
        """Strip a trailing `/watch/` segment to get the landing URL."""
        if not url:
            return ""
        path = url.split("?")[0].split("#")[0]
        stripped = re.sub(r'/watch/?$', '/', path)
        return stripped if stripped != path else ""

    # ── Card parser (works for /last, /movies, /series, /category/, search) ─
    def _extract_cards(self, html, max_items=200):
        """
        Real markup (verbatim, /last/ p1 + /category/.../page/2/):

          <article class="ncp-card post-1367567 ...">
            <a class="ncp-card-link" href="URL" aria-label="TITLE">
              <span class="ncp-card-poster ...">
                <span class="ncp-card-badges ncp-card-badges-top ...">
                  [<span class="ncp-new-badge">جديد</span>]
                  [<span class="ncp-rating-badge"><span class="ncp-rating-value">
                     <span class="ncp-rating-number">6.9</span>
                     <span class="ncp-rating-star">★</span></span></span>]
                </span>
                <span class="ncp-card-badges ncp-card-badges-bottom">
                  <span class="ncp-quality-badge ...">1080p HDCAM</span>
                  [<span class="ncp-episode-badge">الحلقة 1180</span>]
                  [<span class="ncp-season-count-badge">2 مواسم</span>]
                </span>
                <img src="POSTER" data-src="POSTER" alt="TITLE" ...>
              </span>
              <span class="ncp-card-body">
                <span class="ncp-card-title">TITLE</span>
                <span class="ncp-card-meta" dir="rtl">
                  <span class="ncp-card-meta-row">
                    [is-rating • ] [is-quality • ] [is-year]
                  </span>
                  <span class="ncp-card-meta-row">
                    [is-season • ] [is-episode]
                  </span>
                </span>
              </span>
            </a>
          </article>
        """
        items = []
        seen_urls = set()
        if not html:
            return items

        # Split on the outer <article class="ncp-card ..."> wrapper.
        for block in re.split(r'(?=<article[^>]+class="[^"]*\bncp-card\b)',
                              html):
            if "ncp-card" not in block:
                continue
            # re.split with a lookahead yields the head/header prefix (which
            # contains `ncp-card` inside inline <style> CSS) as the first
            # element. Require each block to actually be an <article> so we
            # never mis-parse the preamble.
            if not block.lstrip().startswith("<article"):
                continue
            end = block.find("</article>")
            block = block[:end + 10] if end >= 0 else block

            # ── URL ───────────────────────────────────────────────────────
            url_m = re.search(
                r'<a[^>]+class="[^"]*ncp-card-link[^"]*"[^>]+href="([^"]+)"',
                block, re.I)
            if not url_m:
                url_m = re.search(r'<a[^>]+href="([^"]+)"', block, re.I)
            if not url_m:
                continue
            url = self._full_url(url_m.group(1))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Skip nav-style links that leaked through.
            path_only = re.sub(r'^https?://[^/]+', '', url)
            if not path_only or path_only == "/":
                continue

            # ── Title — prefer aria-label on the anchor, then .ncp-card-title,
            #    then <img alt>. ────────────────────────────────────────────
            title = ""
            am = re.search(
                r'<a[^>]+class="[^"]*ncp-card-link[^"]*"[^>]+aria-label="([^"]+)"',
                block, re.I)
            if am:
                title = self._clean_title(am.group(1))
            if not title:
                tm = re.search(
                    r'<span[^>]+class="[^"]*ncp-card-title[^"]*"[^>]*>(.*?)</span>',
                    block, re.S | re.I)
                if tm:
                    title = self._clean_title(tm.group(1))
            if not title:
                im = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
                if im:
                    title = self._clean_title(im.group(1))
            if not title:
                continue

            # ── Poster — src or data-src (site emits both), skip placeholders
            poster = ""
            pm = (re.search(r'<img[^>]+data-src="([^"]+)"', block, re.I) or
                  re.search(r'<img[^>]+src="([^"]+)"', block, re.I))
            if pm:
                cand = pm.group(1).strip()
                low = cand.lower()
                if not any(x in low for x in
                           ("placeholder", "data:image", "loading.gif",
                            "lazy_load", "poster-placeholder",
                            "/logo", "spinner")):
                    poster = self._full_url(cand)

            # ── Rating (from the top badge) ───────────────────────────────
            rating = ""
            rm = re.search(
                r'<span[^>]+class="[^"]*ncp-rating-number[^"]*"[^>]*>\s*([\d.]+)\s*<',
                block, re.I)
            if rm:
                rating = rm.group(1).strip()

            # ── Quality / release label (bottom badge) ────────────────────
            quality = ""
            qm = re.search(
                r'<span[^>]+class="[^"]*ncp-quality-badge[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.S | re.I)
            if qm:
                quality = self._clean_title(qm.group(1))

            # ── "New" ribbon (used as an item-level status flag) ──────────
            is_new = bool(re.search(
                r'<span[^>]+class="[^"]*ncp-new-badge[^"]*"[^>]*>\s*جديد\s*</span>',
                block, re.I))

            # ── Episode number ────────────────────────────────────────────
            episode = ""
            em = re.search(
                r'<span[^>]+class="[^"]*ncp-episode-badge[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.S | re.I)
            if em:
                episode = self._clean_title(em.group(1))

            # ── Season count (series only) ────────────────────────────────
            season_count = ""
            scm = re.search(
                r'<span[^>]+class="[^"]*ncp-season-count-badge[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                block, re.S | re.I)
            if scm:
                season_count = self._clean_title(scm.group(1))

            # ── Year (own meta field) ─────────────────────────────────────
            year = ""
            ym = re.search(
                r'<span[^>]+class="[^"]*ncp-card-meta-item\b[^"]*\bis-year\b[^"]*"[^>]*>\s*(\d{4})\s*<',
                block, re.I)
            if ym:
                year = ym.group(1)
            if not year:
                ym2 = re.search(r'\b(19\d{2}|20\d{2})\b', title)
                if ym2:
                    year = ym2.group(1)

            # ── Type ──────────────────────────────────────────────────────
            url_low = url.lower()
            title_low = title
            if episode or "/episode" in url_low or "الحلقة" in title_low:
                item_type = "episode"
            elif ("/series/" in url_low or season_count
                  or "مسلسل" in title_low or "الموسم" in title_low):
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
                "plot": quality,
                "label": quality,
                "rating": rating,
                "year": year,
                "episode": episode,
                "season_count": season_count,
                "is_new": is_new,
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
          <nav class="navigation pagination">
            <div class="nav-links">
              <span class="page-numbers current">1</span>
              <a class="page-numbers" href=".../page/2/">2</a>
              ...
              <a class="next page-numbers" href=".../page/2/">التالي</a>
            </div>
          </nav>
        """
        next_url = None
        # 1. Explicit next link ("التالي" / rel=next / class=next)
        for pat in (
            r'<a[^>]+class="[^"]*\bnext\b[^"]*"[^>]+href="([^"]+)"',
            r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*\bnext\b[^"]*"',
            r'<a[^>]+rel=["\']next["\'][^>]+href="([^"]+)"',
            r'<a[^>]+href="([^"]+)"[^>]+rel=["\']next["\']',
            r'<a[^>]+href="([^"]+)"[^>]*>\s*(?:التالي|Next|›|»)\s*</a>',
        ):
            m = re.search(pat, html or "", re.I | re.S)
            if m:
                next_url = self._full_url(m.group(1))
                if next_url:
                    break

        # 2. Derive from current URL and try /page/<N+1>/
        if not next_url:
            m = re.search(r'/page/(\d+)/?', current_url or "")
            current = int(m.group(1)) if m else 1
            want = current + 1
            m = re.search(
                r'href="([^"]*/page/{}/?)"'.format(want),
                html or "", re.I)
            if m:
                next_url = self._full_url(m.group(1))

        if next_url and next_url != current_url:
            return {
                "title": "➡️ الصفحة التالية",
                "url": next_url,
                "type": "category",
                "_action": "category",
            }
        return None

    # ── Public: categories ─────────────────────────────────────────────────
    def get_categories(self, mtype="movie"):
        """Return the site's own navigation menu — verified against the
        confirmed header markup."""
        self._get_base()
        base = self._get_base().rstrip("/")

        cats = []
        cats.append({"title": "🆕 المضاف حديثًا",
                     "url": base + "/last/",
                     "type": "category", "_action": "category"})
        cats.append({"title": "🔥 الأعمال الرائجة",
                     "url": base + "/trending/",
                     "type": "category", "_action": "category"})

        # ── Movies ───────────────────────────────────────────────────────
        cats.append({"title": "── أفلام ──", "url": "", "type": "separator"})
        movies = [
            ("🎬 جميع الأفلام",       "/movies/"),
            ("🌍 أفلام أجنبي",        "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/"),
            ("🎌 أفلام انمي",         "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d9%86%d9%85%d9%8a/"),
            ("🌏 أفلام اسيوي",        "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a/"),
            ("🇮🇳 أفلام هندي",         "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d9%87%d9%86%d8%af%d9%8a/"),
            ("🎬 أفلام صينية",        "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%b5%d9%8a%d9%86%d9%8a%d8%a9/"),
            ("🇪🇬 أفلام عربي",         "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%b9%d8%b1%d8%a8%d9%8a/"),
            ("🎠 أفلام كرتون",        "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d9%83%d8%b1%d8%aa%d9%88%d9%86/"),
            ("🎭 مسرحيات",             "/category/%d9%85%d8%b3%d8%b1%d8%ad%d9%8a%d8%a7%d8%aa/"),
            ("🎞️ سلاسل الأفلام",       "/assemblies/"),
            ("⭐ الأعلى تقييماً IMDb", "/imdb/"),
        ]
        for title, path in movies:
            cats.append({"title": title, "url": self._full_url(path),
                         "type": "category", "_action": "category"})

        # ── Series ───────────────────────────────────────────────────────
        cats.append({"title": "── مسلسلات ──", "url": "", "type": "separator"})
        series = [
            ("📺 جميع المسلسلات",       "/series/"),
            ("📺 مسلسلات أجنبي",         "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/"),
            ("📺 مسلسلات عربي",          "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b9%d8%b1%d8%a8%d9%8a/"),
            ("📺 مسلسلات انمي",          "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d9%86%d9%85%d9%8a/"),
            ("📺 مسلسلات اسيوية",        "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a%d8%a9/"),
            ("⭐ المسلسلات الأعلى IMDb", "/top-rating-imdb-series/"),
            ("📦 مسلسلات كاملة برابط واحد", "/complated-series/"),
        ]
        for title, path in series:
            cats.append({"title": title, "url": self._full_url(path),
                         "type": "category", "_action": "category"})

        # ── Other ────────────────────────────────────────────────────────
        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        cats.append({
            "title": "📡 برامج تلفزيونية",
            "url": self._full_url("/category/%d8%a8%d8%b1%d8%a7%d9%85%d8%ac-%d8%aa%d9%84%d9%81%d8%b2%d9%8a%d9%88%d9%86%d9%8a%d8%a9/"),
            "type": "category", "_action": "category",
        })

        return cats

    # ── Public: category / listing pages ───────────────────────────────────
    def get_category_items(self, url, page=1):
        """Categories paginate via /page/N/ (path segment)."""
        fetch_url = self._full_url(url)

        if page and page > 1:
            parsed = urlparse(fetch_url)
            path = parsed.path or "/"
            if re.search(r'/page/\d+/?$', path):
                path = re.sub(r'/page/\d+/?$', '/page/{}/'.format(page), path)
            else:
                if not path.endswith("/"):
                    path += "/"
                path += "page/{}/".format(page)
            fetch_url = parsed._replace(path=path).geturl()

        log("CimaWebas: fetching listing {}".format(fetch_url))
        html, final_url = fetch(fetch_url, referer=self._get_base(),
                                extra_headers=_PROBE_HEADERS)
        if not html:
            log("CimaWebas: get_category_items failed for {}".format(fetch_url))
            return []

        items = self._extract_cards(html)
        log("CimaWebas: {} card(s) extracted".format(len(items)))

        if not page or page == 1:
            nxt = self._parse_pagination(html, fetch_url)
            if nxt:
                items.append(nxt)

        return items

    # ── Public: search ─────────────────────────────────────────────────────
    def search(self, query, page=1):
        """WordPress search: /?s=<q>  (+&ncp_search_type=all)"""
        base = self._get_base().rstrip("/")
        search_url = "{}/?s={}&ncp_search_type=all".format(
            base, self._encode_query(query))
        if page and page > 1:
            search_url = "{}/page/{}/?s={}&ncp_search_type=all".format(
                base, page, self._encode_query(query))

        log("CimaWebas: searching: {}".format(search_url))
        html, _ = fetch(search_url, referer=base,
                        extra_headers=_PROBE_HEADERS)
        if not html:
            return []

        items = self._extract_cards(html)
        if page == 1:
            nxt = self._parse_pagination(html, search_url)
            if nxt:
                items.append(nxt)
        log("CimaWebas: search '{}' → {} items".format(query, len(items)))
        return items

    # ── Detail-page metadata ───────────────────────────────────────────────
    def _extract_detail_meta(self, html):
        title = ""
        # og:title is the cleanest
        tm = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if not tm:
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
        if tm:
            title = self._clean_title(tm.group(1).split("|")[0])
        if not title:
            tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S | re.I)
            if tm:
                title = self._clean_title(tm.group(1))

        # Poster: prefer the on-page image over `og:image`. This site's
        # `og:image` is the site icon on EVERY page
        # (cropped-site-icon-512.png), so it must never be used as the
        # primary source.
        poster = ""
        # 1. Landing page: <div class="ncp-single-poster">…<img>…</div>
        im = re.search(
            r'<div[^>]*class="[^"]*ncp-single-poster[^"]*"[^>]*>.*?'
            r'<img[^>]+(?:data-src|src)="([^"]+)"',
            html, re.S | re.I)
        # 2. Watch page: <a class="ncp-stream-thumb">…<img>…</a>
        if not im:
            im = re.search(
                r'<a[^>]*class="[^"]*ncp-stream-thumb[^"]*"[^>]*>.*?'
                r'<img[^>]+(?:data-src|src)="([^"]+)"',
                html, re.S | re.I)
        if im:
            poster = self._full_url(im.group(1))
        # 3. Last resort: og:image (usually the site icon here).
        if not poster:
            pm = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if pm:
                poster = self._full_url(pm.group(1))

        plot = ""
        for pat in (
                r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
                r'<div[^>]+class="[^"]*ncp-single-story[^"]*"[^>]*>(.*?)</div>'):
            plm = re.search(pat, html, re.S | re.I)
            if plm:
                plot = re.sub(r"<[^>]+>", " ", plm.group(1)).strip()
                if plot:
                    break

        year = ""
        ym = re.search(r'\b(19\d{2}|20\d{2})\b', title + " " + plot)
        if ym:
            year = ym.group(1)

        rating = ""
        rm = re.search(
            r'<span[^>]+class="[^"]*ncp-rating-number[^"]*"[^>]*>\s*([\d.]+)\s*<',
            html, re.I)
        if rm:
            rating = rm.group(1).strip()

        return title, poster, plot, year, rating

    # ── Server extraction (watch / detail page) ────────────────────────────
    def _add_server(self, servers, seen, url, label=None, srv_type="embed"):
        if not url:
            return
        url = url.strip().replace("&amp;", "&").replace("\\/", "/")
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            return

        # Never treat the site's own pages as servers. The generic data-* /
        # href scan otherwise matches every navigation link on the page
        # (brand, breadcrumbs, category chips, the /download/ CTA, related
        # cards) — and, worse, causes `get_page` to skip the real /watch/
        # page when only those links are found on the landing page.
        if self._is_valid_site_url(url):
            return

        low = url.lower()

        # Ads, social embeds, analytics, redirect hops — never media.
        if any(x in low for x in self._NON_MEDIA_HOSTS):
            return
        if any(x in low for x in self._NON_MEDIA_PATHS):
            return

        # Static asset extensions (js/css/img/ico…). Note: we deliberately
        # do NOT include .mp4 / .m3u8 here — those are the stream URLs we
        # actually want.
        low_path = low.split("?", 1)[0]
        if low_path.endswith((".jpg", ".jpeg", ".png", ".gif",
                              ".webp", ".svg", ".css", ".js", ".ico")):
            return

        # HLS master playlists are wrapped by their parent service page;
        # treat a `.js` path that also contains `/assets/` as a script, not
        # media, even if it would slip through (defensive — the extension
        # check above already catches it).

        # Unwrap `topcinemaa.com/play.php?to=<enc>` — purely cosmetic, but
        # makes the name/host lookup land on the real host.
        resolved = self._unwrap_player_proxy(url)
        if resolved and resolved not in seen:
            url = resolved

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
            # ── Embed services ─────────────────────────────────────────
            ("streamwish", "StreamWish"), ("wishfast", "StreamWish"),
            ("filemoon", "FileMoon"), ("dood", "DoodStream"),
            ("d0o0d", "DoodStream"), ("playmogo", "PlayMogo"),
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
            ("cloudwindow", "Voe"),
            ("superflixapi", "SuperFlix"), ("zxcstream", "ZXC Stream"),
            ("vidsrc", "VidSrc"), ("vidcore", "VidCore"),
            ("vidfast", "VidFast"), ("vidapi", "VidAPI"),
            ("vaplayer", "VidAPI"),
            ("nextgencloudfabric", "VidAPI"),
            ("topcinemaa", "TopCinema"), ("topcinema.io", "TopCinema"),
            ("cdnm.ink", "CDNM"), ("nontongo", "Nontongo"),
            ("cybervynx", "CyberVynx"), ("dhcplay", "DHCPlay"),
            ("firestream", "FireStream"),
            ("vidtube.one", "VidTube"),
            ("updown", "UpDown"),
            ("mixdrop.ps", "MixDrop"),
            ("streamtape.cc", "StreamTape"),
            ("byseqekaho", "Byse"),
            # ── Direct-media CDN hosts (returned by the base resolvers) ─
            # Order matters: check the more specific patterns first.
            ("us-cdn-1.firestream.to", "FireStream CDN"),
            ("gamescdn.online", "UpDown CDN"),
            ("sprintcdn", "Byse CDN"),
            ("r66nv9ed.com", "Byse CDN"),
            ("cdn-video.xyz", "VidTube CDN"),
            ("serv-stream-cdn", "VidTube CDN"),
        )
        for frag, label in mapping:
            if frag in lowered:
                return label
        return host or "Server"

    def _extract_watch_servers(self, html, page_url):
        """
        Watch-page server extraction. The NovaCinemaPlus theme renders the
        server list as:

          <button class="ncp-server-button" type="button"
                  data-ncp-watch-server="URL" data-server-name="NAME">
            <span class="ncp-stream-icon">…</span>
            <span>سيرفر N</span><strong>NAME</strong>
          </button>

        …and the active player as an iframe:

          <iframe class="ncp-watch-iframe" src="URL" …></iframe>

        We scan in this order:
          (1a) `data-ncp-watch-server` buttons  ← the site's own list
          (1b) generic data-* / href items      ← safety net / other themes
          (2)  iframes
          (3)  known-host URLs anywhere on the page
          (4)  direct <source>/<video> tags
          (5)  JS literals: file/src/url/source
        """
        servers = []
        seen = set()
        if not html:
            log("CimaWebas: _extract_watch_servers called with empty html")
            return servers

        # (1a) Site's own watch-server buttons.
        count_1a = 0
        for m in re.finditer(
                r'<button[^>]*\bdata-ncp-watch-server=["\']([^"\']+)["\'][^>]*>'
                r'(.*?)</button>',
                html, re.S | re.I):
            raw = m.group(1).strip()
            tag = m.group(0)
            inner = m.group(2)
            count_1a += 1

            # Build a friendly label from inner <span>/<strong> content.
            # The site renders:  <span>سيرفر 1</span><strong>topcinemaa</strong>
            parts = []
            for pat in (r'<span[^>]*>\s*([^<]+?)\s*</span>',
                        r'<strong[^>]*>\s*([^<]+?)\s*</strong>'):
                mm = re.search(pat, inner)
                if mm:
                    cleaned = self._clean_title(mm.group(1))
                    if cleaned:
                        parts.append(cleaned)
            # Fall back to data-server-name if the inner text was empty.
            if not parts:
                sn = re.search(r'data-server-name=["\']([^"\']+)["\']',
                               tag, re.I)
                if sn:
                    parts.append(sn.group(1).strip())
            # De-dup parts while preserving order.
            seen_parts = set()
            ordered = []
            for p in parts:
                if p and p not in seen_parts:
                    seen_parts.add(p)
                    ordered.append(p)
            label = " – ".join(ordered) if ordered else None

            self._add_server(servers, seen, raw, label=label)
        log("CimaWebas: [watch-scan] 1a matched {} button(s); "
            "kept {}".format(count_1a, len(servers)))

        # (1b) Generic data-* / href items on buttons, list items, anchors,
        #     divs — kept as a safety net for theme variants. Note that
        #     `_add_server` will drop same-site URLs (breadcrumbs, brand,
        #     category chips, /download/ CTA, related cards, …).
        before_1b = len(servers)
        for m in re.finditer(
                r'<(?:li|button|a|div)[^>]*?'
                r'(?:data-(?:link|url|iframe|server|embed|src|href)|href)=["\']([^"\']+)["\']',
                html, re.I):
            raw = m.group(1).strip()
            if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            self._add_server(servers, seen, raw)
        log("CimaWebas: [watch-scan] 1b added {} server(s)".format(
            len(servers) - before_1b))

        # (2) iframes
        before_2 = len(servers)
        for m in re.finditer(r'<iframe[^>]+(?:src|data-src)="([^"]+)"',
                             html, re.I):
            self._add_server(servers, seen, m.group(1).strip())
        log("CimaWebas: [watch-scan] 2 added {} server(s)".format(
            len(servers) - before_2))

        # (3) known streaming-host URLs anywhere on the page
        before_3 = len(servers)
        host_re = re.compile(
            r'(https?://(?:[^"\'\s<>]*(?:'
            + "|".join(self._MEDIA_HOST_MARKERS)
            + r'))[^"\'\s<>]*)', re.I)
        for m in host_re.finditer(html):
            self._add_server(servers, seen, m.group(1).replace("\\/", "/"))
        log("CimaWebas: [watch-scan] 3 added {} server(s)".format(
            len(servers) - before_3))

        # (4) <source> / <video> tags
        before_4 = len(servers)
        for m in re.finditer(
                r'<(?:source|video)[^>]+src=["\']'
                r'([^"\']+\.(?:mp4|m3u8|mkv|txt)[^"\']*)["\']',
                html, re.I):
            self._add_server(servers, seen, _correct_stream_url(m.group(1)),
                             srv_type="direct")
        log("CimaWebas: [watch-scan] 4 added {} direct stream(s)".format(
            len(servers) - before_4))

        # (5) JS literals
        before_5 = len(servers)
        for m in re.finditer(
                r'(?:file|src|url|source)\s*[:=]\s*["\']'
                r'([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                html, re.I):
            self._add_server(servers, seen,
                             _correct_stream_url(m.group(1).replace("\\/", "/")),
                             srv_type="direct")
        log("CimaWebas: [watch-scan] 5 added {} JS literal(s)".format(
            len(servers) - before_5))

        log("CimaWebas: {} server(s) found on {}".format(
            len(servers), (page_url or "")[:80]))
        return servers

    # ── Download-section extraction ────────────────────────────────────────
    def _extract_download_links(self, html):
        """
        Best-effort. The site has a dedicated /download/ page per title.
        Preferred shape: a list of quality groups ("سيرفرات تحميل 1080",
        "سيرفرات تحميل 720", ...) each containing a set of external
        file-host anchors. Also accepts generic data-href/data-url items.
        """
        downloads = []
        seen = set()
        if not html:
            return downloads

        # Quality-group splitting (same shape family uses in Shaheed).
        sections = re.split(r'سيرفرات\s*تحميل\s*(\d+)', html)
        for i in range(1, len(sections), 2):
            quality = sections[i]
            content = sections[i + 1]
            for m in re.finditer(
                    r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
                    content, re.S | re.I):
                url = m.group(1).strip().replace("&amp;", "&")
                if url.startswith("//"):
                    url = "https:" + url
                if not url.startswith("http") or url in seen:
                    continue
                if any(x in url.lower() for x in self._NON_MEDIA_HOSTS):
                    continue
                if self._is_valid_site_url(url):
                    continue
                seen.add(url)
                inner = m.group(2)
                name_m = re.search(r'<span[^>]*>([^<]+)</span>', inner, re.I)
                name = self._clean_title(name_m.group(1)) if name_m else "Download"
                downloads.append({
                    "resolution": "{}p".format(quality),
                    "size": "",
                    "quality": name or "Server",
                    "url": url,
                })

        # Fallback: scan data-href / data-url items on a dedicated download page.
        if not downloads:
            for m in re.finditer(
                    r'<(?:a|li|div)[^>]*?(?:data-href|data-url)=["\']([^"\']+)["\'][^>]*>(.*?)</(?:a|li|div)>',
                    html, re.S | re.I):
                url = m.group(1).strip().replace("&amp;", "&")
                if url.startswith("//"):
                    url = "https:" + url
                if not url.startswith("http") or url in seen:
                    continue
                if any(x in url.lower() for x in self._NON_MEDIA_HOSTS):
                    continue
                # Only external file hosts — skip same-site nav links.
                if self._is_valid_site_url(url):
                    continue
                seen.add(url)
                inner = m.group(2)
                res_m = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b',
                                  inner, re.I)
                size_m = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b',
                                   inner, re.I)
                name_m = re.search(r'<span[^>]*>([^<]+)</span>', inner, re.I)
                downloads.append({
                    "resolution": res_m.group(1) if res_m else "",
                    "size": size_m.group(1) if size_m else "",
                    "quality": self._clean_title(name_m.group(1)) if name_m else "Server",
                    "url": url,
                })

        log("CimaWebas: {} download link(s)".format(len(downloads)))
        return downloads

    # ── Public: get_page ───────────────────────────────────────────────────
    def _find_watch_url(self, html, page_url):
        """
        Prefer the site's own action link:
          <a class="ncp-single-action is-primary"
             href=".../<slug>/watch/">مشاهدة الآن</a>
        Fall back to any /watch/ link, then to appending /watch/ to the URL.
        """
        if html:
            m = (re.search(
                    r'<a[^>]+class="[^"]*ncp-single-action[^"]*is-primary[^"]*"[^>]+href="([^"]+)"',
                    html, re.I)
                 or re.search(
                    r'<a[^>]+href="([^"]+/watch/?)"', html, re.I))
            if m:
                url = self._full_url(m.group(1))
                if url:
                    return url
        base = (page_url or "").split("?")[0].split("#")[0].rstrip("/")
        if base.endswith("/watch"):
            return base + "/"
        if base:
            return base + "/watch/"
        return ""

    def _find_download_url(self, html, page_url):
        if html:
            m = re.search(
                r'<a[^>]+class="[^"]*ncp-single-action[^"]*is-download[^"]*"[^>]+href="([^"]+)"',
                html, re.I)
            if m:
                url = self._full_url(m.group(1))
                if url:
                    return url
        base = (page_url or "").split("?")[0].split("#")[0].rstrip("/")
        if base.endswith("/download"):
            return ""
        if base:
            return base + "/download/"
        return ""

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

        clean_url = self._full_url(url)
        if not clean_url:
            log("CimaWebas: get_page — empty URL after normalization: {!r}"
                .format(url))
            return result

        is_watch = self._is_watch_page_url(clean_url)
        log("CimaWebas: get_page start {} (is_watch={})".format(
            clean_url[:100], is_watch))

        landing_html = ""
        landing_final = ""
        watch_html = ""
        watch_final = ""

        # ── 1. Fetch pages ────────────────────────────────────────────────
        if is_watch:
            # Fetch the watch page first — it's where the servers live.
            watch_html, watch_final = fetch(
                clean_url, referer=self._get_base(),
                extra_headers=_PROBE_HEADERS)
            watch_final = watch_final or clean_url
            log("CimaWebas: watch page fetch status html={} final={}".format(
                len(watch_html or ""), (watch_final or "")[:100]))

            # Also try the landing page — the watch page's `og:image` is the
            # site icon, and the landing page has the real poster.
            landing_url = self._landing_url_from_watch(watch_final)
            if landing_url and landing_url != watch_final:
                landing_html, landing_final = fetch(
                    landing_url, referer=watch_final,
                    extra_headers=_PROBE_HEADERS)
                landing_final = landing_final or landing_url
                log("CimaWebas: landing fetch status html={} final={}".format(
                    len(landing_html or ""), (landing_final or "")[:100]))
        else:
            # Fetch the landing page first (metadata).
            landing_html, landing_final = fetch(
                clean_url, referer=self._get_base(),
                extra_headers=_PROBE_HEADERS)
            landing_final = landing_final or clean_url
            log("CimaWebas: landing fetch status html={} final={}".format(
                len(landing_html or ""), (landing_final or "")[:100]))

            if not landing_html:
                log("CimaWebas: get_page — landing fetch returned empty, "
                    "aborting")
                return result

            # Now find + fetch the /watch/ page. Always do this — do NOT
            # rely on URL equality to decide. If we already know the exact
            # watch URL, use it; otherwise construct it from the landing.
            watch_url = self._find_watch_url(landing_html, landing_final)
            log("CimaWebas: watch URL candidate: {}".format(watch_url))
            if watch_url:
                watch_html, watch_final = fetch(
                    watch_url, referer=landing_final,
                    extra_headers=_PROBE_HEADERS)
                watch_final = watch_final or watch_url
                log("CimaWebas: watch fetch status html={} final={}".format(
                    len(watch_html or ""), (watch_final or "")[:100]))

        # ── 2. Metadata ───────────────────────────────────────────────────
        # Landing page has the better poster; watch page is a fallback.
        meta_html = landing_html or watch_html
        if meta_html:
            title, poster, plot, year, rating = self._extract_detail_meta(
                meta_html)
            result["title"] = title
            result["poster"] = poster
            result["plot"] = plot
            result["year"] = year
            result["rating"] = rating
            result["url"] = landing_final or watch_final or clean_url

        # ── 3. Type ───────────────────────────────────────────────────────
        effective_url = (landing_final or watch_final or clean_url).lower()
        if "/episode" in effective_url or "الحلقة" in result["title"]:
            result["type"] = "episode"
        elif ("/series/" in effective_url or "مسلسل" in result["title"]
              or "الموسم" in result["title"]):
            result["type"] = "series"
        elif m_type:
            result["type"] = m_type

        # ── 4. Servers ────────────────────────────────────────────────────
        servers = []
        if landing_html:
            servers = self._extract_watch_servers(
                landing_html, landing_final or clean_url)
            log("CimaWebas: get_page — {} server(s) found on landing"
                .format(len(servers)))

        if not servers and watch_html:
            servers = self._extract_watch_servers(watch_html, watch_final)
            log("CimaWebas: get_page — {} server(s) found on watch page"
                .format(len(servers)))

        # Last-ditch: if we're on a landing URL and still nothing, try
        # constructing `…/watch/` directly from the URL.
        if not servers and not is_watch:
            base = clean_url.split("?")[0].split("#")[0].rstrip("/")
            if not base.endswith("/watch"):
                direct_watch = base + "/watch/"
                log("CimaWebas: get_page — last-ditch direct watch URL: {}"
                    .format(direct_watch))
                wh, wf = fetch(direct_watch, referer=clean_url,
                               extra_headers=_PROBE_HEADERS)
                if wh:
                    servers = self._extract_watch_servers(
                        wh, wf or direct_watch)
                    log("CimaWebas: get_page — {} server(s) found on direct "
                        "watch URL".format(len(servers)))
                    if not watch_html:
                        watch_html = wh
                        watch_final = wf or direct_watch

        result["servers"] = servers

        # ── 5. Downloads ──────────────────────────────────────────────────
        download_source_html = watch_html or landing_html
        download_source_url = watch_final or landing_final or clean_url
        if download_source_html:
            result["downloads"] = self._extract_download_links(
                download_source_html)
        if not result["downloads"]:
            dl_url = self._find_download_url(
                landing_html or watch_html, download_source_url)
            if dl_url and dl_url != download_source_url:
                log("CimaWebas: get_page — fetching download page: {}"
                    .format(dl_url))
                dl_html, _ = fetch(dl_url, referer=download_source_url,
                                   extra_headers=_PROBE_HEADERS)
                if dl_html:
                    result["downloads"] = self._extract_download_links(
                        dl_html)

        # ── 6. Episodes (series only) ─────────────────────────────────────
        if result["type"] == "series" and landing_html:
            episodes = []
            seen_eps = set()
            for m in re.finditer(
                    r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                    landing_html, re.S | re.I):
                ep_url = self._full_url(m.group(1))
                if not ep_url or ep_url == (landing_final or clean_url):
                    continue
                if ep_url in seen_eps:
                    continue
                low = ep_url.lower()
                if ("الحلقة" not in low and "/episode" not in low
                        and "-حلقة-" not in low
                        and "حلقة" not in self._clean_title(m.group(2))):
                    continue
                seen_eps.add(ep_url)
                ep_text = self._clean_title(m.group(2)) or "حلقة"
                episodes.append({
                    "title": ep_text,
                    "url": ep_url,
                    "type": "episode",
                    "_action": "details",
                })
            result["items"] = episodes
            log("CimaWebas: get_page — {} episode(s) found"
                .format(len(episodes)))

        log("CimaWebas: get_page END {} → servers={} items={} downloads={} "
            "type={}".format(clean_url[:60], len(result["servers"]),
                             len(result["items"]), len(result["downloads"]),
                             result["type"]))
        return result

    # ── extract_stream ─────────────────────────────────────────────────────
    def extract_stream(self, url):
        """Resolve a server/download URL to a playable stream."""
        from .base import extract_stream as base_extract_stream
        from .base import extract_stream_all as base_extract_all

        if not url:
            return None, "", self._get_base(), []

        # Unwrap `topcinemaa.com/play.php?to=<enc>` before handing off.
        clean = self._unwrap_player_proxy(url.strip())
        clean = _correct_stream_url(clean)

        if not clean or not clean.startswith("http"):
            return None, "", self._get_base(), []

        # Direct media → pass through. This covers the confirmed CDN URLs:
        #   us-cdn-1.firestream.to/…/video.m3u8?md5=…&expires=…
        #   dayt-fer-dw9.gamescdn.online/d/…/video.mp4
        #   edge1-madrid-sprintcdn.r66nv9ed.com/hls2/…/master.m3u8?t=…
        #   serv-stream-cdn12.cdn-video.xyz/vp/…/<title>.mp4?t=…
        if self._is_direct_media_url(clean):
            quality = "HD"
            low = clean.lower()
            if "2160" in low or "4k" in low:
                quality = "2160p"
            elif "1440" in low:
                quality = "1440p"
            elif "1080" in low:
                quality = "1080p"
            elif "720" in low:
                quality = "720p"
            elif "480" in low:
                quality = "480p"
            elif "360" in low:
                quality = "360p"
            log("CimaWebas: extract_stream — direct media passthrough ({}): {}"
                .format(quality, clean[:100]))
            return clean, quality, self._get_base(), []

        # Multi-quality attempt first.
        try:
            variants = base_extract_all(clean)
            if variants:
                best_url, best_quality = variants[0]
                others = [(q, u) for u, q in variants[1:]]
                return best_url, best_quality, self._get_base(), others
        except Exception as e:
            log("CimaWebas: extract_stream_all failed for {}: {}".format(
                clean[:80], e))

        # Single-URL resolver.
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
            log("CimaWebas: extract_stream failed for {}: {}".format(
                clean[:80], e))

        return None, "", self._get_base(), []