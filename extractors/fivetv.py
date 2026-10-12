# -*- coding: utf-8 -*-
"""
Fivetv Extractor - فايف تي في (5tv)
Inherits from BaseExtractor.

NEW (2026-10-11): brand-new site, no prior extractor existed for it. Built
from 9 real captured snapshots (homepage, 6 category/listing pages incl. one
paginated page 2, and a movie detail page) plus a real recorded stream-link
list, all confirmed against the actual markup rather than guessed.

Domain history (as reported by the user, 2026-10-11): 5tv.asia, 5tv.lol,
5tv.cam, 5tv.rip and new61.5tv.lol all now redirect to
https://no05916ya.5ivtv.in/, which is the real current domain and is
listed first below.

Site is a modern WordPress build (theme "5tv", custom "ftv-*" component
classes throughout -- NOT the GridItem/Wecima-Card family, a different,
unrelated theme). Confirmed real and clean, directly from captured markup:
  - Category/listing cards: <article class="ftv-home-poster-card ...">
    (movies/series) and <article class="ftv-home-episode-card"> (episode
    listings) -- both handled by _extract_cards() below.
  - Pagination: standard WP <a class="page-numbers"> links, with a
    "التالي" (next) link and /page/N/ URLs.
  - Watch servers on a detail page: <div class="ftv-watch-tabs"><button
    data-server-url="PLAIN https:// URL" ...><strong>NAME</strong>
    <small>QUALITY</small></button> -- values are plain, already-decoded
    URLs, no obfuscation at all (confirmed against a real page: 71Stream,
    Ultra4Vid, DSVPlay, Morencius, HGCloud, VidMoly all came through as
    plain https:// URLs).
  - Downloads on a detail page: <div class="ftv-download-list"><article
    class="ftv-download-server"><div class="ftv-download-server__info">
    <strong>NAME</strong><small>QUALITY</small></div><a href="PLAIN URL"
    class="ftv-download-server__button">...</a></article> -- also plain
    URLs, no decoding needed.
  - Metadata: title from <h1 class="ftv-content-hero__title">, plot from
    <div class="ftv-content-hero__description"><p>...</p></div> (falls
    back to og:description), poster from og:image, year from
    <div class="ftv-content-hero__year"><a>YYYY</a></div>.

NOT YET CONFIRMED: a /series/ overview page's episode-listing markup --
only a movie detail page (/movie/...) was captured this session, and only
episode CARDS linking to standalone /episode/... pages were seen (on a
*category* listing page, not on a series' own page). get_page() below
guesses type from the URL path and, for a /series/ URL, tries the same
_extract_cards() scan for ftv-home-episode-card blocks on that page as a
best effort -- if a real /series/... page snapshot shows a different
shape, this will need revisiting rather than assuming the current guess
is right.
"""

import re
import sys
from .base import BaseExtractor, fetch, log, urljoin

if sys.version_info[0] == 3:
    from urllib.parse import quote_plus, urlparse
    from html import unescape as html_unescape
else:
    from urllib import quote_plus
    from urlparse import urlparse
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape


class FivetvExtractor(BaseExtractor):
    """Extractor for Fivetv (فايف تي في) - no05916ya.5ivtv.in"""

    DOMAINS = [
        "https://no05916ya.5ivtv.in/",  # current real domain (confirmed 2026-10-11)
        "https://5tv.lol/",             # now redirects to the domain above
        "https://5tv.asia/",            # now redirects to the domain above
        "https://5tv.cam/",             # now redirects to the domain above
        "https://5tv.rip/",             # now redirects to the domain above
        "https://new61.5tv.lol/",       # now redirects to the domain above
    ]
    VALID_HOST_MARKERS = (
        "5ivtv.in",
        "5tv.lol",
        "5tv.asia",
        "5tv.cam",
        "5tv.rip",
    )
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)

    # Hosts that never host downloadable media - filtered from server/
    # download extraction if this site ever surfaces one (none seen yet
    # in the real captured servers, but kept for parity with the other
    # wecima-family extractors in case a social/share link slips in).
    _NON_MEDIA_HOSTS = (
        "facebook.com", "twitter.com", "instagram.com", "youtube.com",
        "google.com", "t.me", "telegram.me", "whatsapp.com",
    )

    # Confirmed real nav links (from the homepage's <nav class="desktop-nav">)
    # plus the category/listing pages actually captured this session.
    CATEGORY_FALLBACKS = {
        "آخر الأفلام":   "/latest-movies/",
        "آخر الدراما":   "/latest-series/",
        "آخر الحلقات":   "/latest-episodes/",
        "آخر الإضافات":  "/new-rows/",
        "تصفح وأكتشف":   "/discover/",
        "الجدول الأسبوعي": "/schedule/",
    }

    def __init__(self):
        super(FivetvExtractor, self).__init__()
        self.main_url = self.DOMAINS[0]
        self._resolved_base = None
        self._home_html_cache = None

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

    def _looks_like_fivetv_page(self, html):
        text = html or ""
        tl = text.lower()
        return (
            "5ivtv.in" in tl
            or "wp-theme-5tv" in tl
            or "فايف تي في" in text
            or "ftv-home-poster-card" in text
            or "ftv-content-hero" in text
            or "ftv-watch-tabs" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _get_base(self):
        if self._resolved_base:
            return self._resolved_base
        for domain in self.DOMAINS:
            log("Fivetv: probing {}".format(domain))
            html, final_url = fetch(domain, referer=domain)
            final_url = final_url or domain
            if self._is_blocked_page(html, final_url):
                log("Fivetv: blocked {}".format(final_url))
                continue
            if html and self._looks_like_fivetv_page(html):
                self._resolved_base = self._site_root(final_url)
                self.main_url = self._resolved_base
                self._home_html_cache = html
                log("Fivetv: selected base {}".format(self._resolved_base))
                return self._resolved_base
        self._resolved_base = self.DOMAINS[0]
        self.main_url = self._resolved_base
        log("Fivetv: fallback base {}".format(self.main_url))
        return self.main_url

    def _normalize_url(self, url):
        if not url:
            return ""
        url = html_unescape(url.strip())
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http"):
            return urljoin(self._get_base(), url)
        return url

    def _candidate_urls(self, url):
        normalized = self._normalize_url(url)
        if not normalized:
            return []
        parts = urlparse(normalized)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        urls = []
        seen = set()
        seeds = []
        if self.main_url:
            seeds.append(self.main_url)
        seeds.extend(self.DOMAINS)
        if normalized.startswith("http"):
            seeds.insert(0, self._site_root(normalized))
        for domain in seeds:
            if not domain:
                continue
            base = domain if domain.endswith("/") else domain + "/"
            candidate = urljoin(base, path.lstrip("/"))
            if candidate in seen:
                continue
            seen.add(candidate)
            urls.append(candidate)
        if normalized not in seen:
            urls.insert(0, normalized)
        return urls

    def _fetch_live(self, url, referer=None):
        for candidate in self._candidate_urls(url):
            log("Fivetv: fetching {}".format(candidate))
            html, final_url = fetch(candidate, referer=referer or self._get_base())
            final_url = final_url or candidate
            if self._is_blocked_page(html, final_url):
                log("Fivetv: blocked {}".format(final_url))
                continue
            if html and self._looks_like_fivetv_page(html):
                log("Fivetv: success {}".format(final_url))
                return html, final_url
            if html:
                log("Fivetv: page shape mismatch {}".format(final_url))
        log("Fivetv: fetch failed for {}".format(url))
        return "", ""

    def _clean_html(self, text):
        text = html_unescape(text or "")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _guess_type(self, url):
        text = (url or "").lower()
        if "/episode/" in text:
            return "episode"
        if "/series/" in text:
            return "series"
        return "movie"

    # ═══ CARD EXTRACTION ═══
    # Confirmed real markup for both card variants against real captured
    # category/listing pages this session.
    def _extract_cards(self, html):
        cards = []
        seen = set()
        if not html:
            return cards

        # Poster cards (movies/series): <article class="ftv-home-poster-card ...">
        # [FIX] confirmed directly against a real LIVE server fetch (not just the
        # browser-captured snapshots this was first built against): the real
        # site pretty-prints every attribute onto its own line --
        #   <a
        #       href="..."
        #       class="ftv-home-poster-card__image"
        #   >
        # -- whereas a browser's outerHTML/snapshot always serializes a tag's
        # attributes back onto one line. Every regex below originally assumed
        # that single-line shape (a literal space between "<article" and
        # "class=", between a closing attribute quote and the next attribute,
        # and between the last attribute's quote and ">"), which matched the
        # snapshots perfectly and then found ZERO cards against the real
        # live-fetched page (confirmed: 0 cards extracted, 91KB of real HTML
        # fetched successfully). \s+ / \s* in place of those literal
        # adjacencies tolerates both shapes -- it still matches a collapsed
        # single-line tag exactly as before, so this carries no regression
        # risk against the already-verified snapshot-based tests.
        for block in re.findall(
            r'<article\s+class="ftv-home-poster-card[^"]*"\s*>(.*?)</article>',
            html, re.S | re.I,
        ):
            link_m = re.search(
                r'class="ftv-home-poster-card__title"\s*>\s*([^<]+?)\s*</a>',
                block, re.S | re.I,
            )
            href_m = re.search(
                r'<a\s+href="([^"]+)"[^>]*class="ftv-home-poster-card__image"',
                block, re.S | re.I,
            )
            if not href_m or not link_m:
                continue
            url = self._normalize_url(href_m.group(1))
            if not url or url in seen:
                continue
            title = self._clean_html(link_m.group(1))
            if not title:
                continue
            seen.add(url)

            poster_m = re.search(r'<img\s+src="([^"]+)"', block, re.I)
            poster = self._normalize_url(poster_m.group(1)) if poster_m else ""

            year_m = re.search(
                r'ftv-poster-badge--year"\s*>\s*([0-9]{4})\s*</span>', block, re.S
            )
            year = year_m.group(1) if year_m else ""

            cards.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": "",
                "year": year,
                "type": self._guess_type(url),
                "_action": "details",
            })

        # Episode cards: <article class="ftv-home-episode-card">
        # [FIX] same multi-line-attribute tolerance as the poster cards above.
        for block in re.findall(
            r'<article\s+class="ftv-home-episode-card"\s*>(.*?)</article>',
            html, re.S | re.I,
        ):
            link_m = re.search(
                r'class="ftv-home-episode-card__title"\s*>\s*([^<]+?)\s*</a>',
                block, re.S | re.I,
            )
            href_m = re.search(
                r'<a\s+href="([^"]+)"[^>]*class="ftv-home-episode-card__image"',
                block, re.S | re.I,
            )
            if not href_m or not link_m:
                continue
            url = self._normalize_url(href_m.group(1))
            if not url or url in seen:
                continue
            title = self._clean_html(link_m.group(1))
            if not title:
                continue
            seen.add(url)

            poster_m = re.search(r'<img\s+src="([^"]+)"', block, re.I)
            poster = self._normalize_url(poster_m.group(1)) if poster_m else ""

            ep_m = re.search(
                r'ftv-home-episode-card__meta"\s*>(.*?)</span>', block, re.S
            )
            ep_num = ""
            if ep_m:
                num_m = re.search(r'(\d+)', ep_m.group(1))
                if num_m:
                    ep_num = num_m.group(1)
                    title = "{} حلقة {}".format(title, ep_num)

            cards.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": "",
                "year": "",
                "type": "episode",
                "_action": "details",
            })

        return cards

    # ═══ PAGINATION ═══
    def _extract_next_page(self, html, current_url):
        """Confirmed real markup: standard WP <a class="page-numbers"
        href="...">التالي</a> plus numbered page-numbers links, same
        safe "only a real next link, or the numbered link for page+1"
        approach already proven in the other wecima-family extractors
        (never invents a URL past the last real page)."""
        if not html:
            return ""
        m = re.search(r'/page/(\d+)', current_url or "")
        nxt = (int(m.group(1)) if m else 1) + 1

        next_m = re.search(
            r'<a[^>]+class="[^"]*page-numbers[^"]*"[^>]+href="([^"]+)"[^>]*>\s*التالي\s*</a>',
            html, re.S | re.I,
        )
        if next_m:
            url = self._normalize_url(next_m.group(1))
            if url and url != current_url:
                return url

        for href, num in re.findall(
            r'<a[^>]+class="[^"]*page-numbers[^"]*"[^>]+href="([^"]+)"[^>]*>\s*(\d+)\s*</a>',
            html, re.S | re.I,
        ):
            if int(num) == nxt:
                url = self._normalize_url(href)
                if url and url != current_url:
                    return url
        return ""

    # ═══ DETAIL PAGE METADATA ═══
    # [FIX] same multi-line-attribute tolerance as _extract_cards above --
    # "class=...">" closing adjacency and "<h1 class=" tag-to-attribute
    # adjacency both assumed a browser-snapshot's single-line shape, which a
    # real live fetch of this theme's pretty-printed server HTML does not
    # use. og:* meta tags were confirmed single-line even on a real live
    # fetch (meta tags aren't part of the pretty-printed template body), so
    # those are left as a tight match but still widened to \s+ defensively.
    def _detail_title(self, html):
        m = re.search(
            r'<h1\s+class="ftv-content-hero__title"\s*>\s*(.*?)\s*</h1>', html, re.S | re.I
        )
        if m:
            return self._clean_html(m.group(1))
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.I)
        return html_unescape(m.group(1)).strip() if m else ""

    def _detail_poster(self, html):
        m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.I)
        return self._normalize_url(html_unescape(m.group(1))) if m else ""

    def _detail_plot(self, html):
        m = re.search(
            r'ftv-content-hero__description"\s*>\s*<p>(.*?)</p>', html, re.S | re.I
        )
        if m:
            return self._clean_html(m.group(1))
        m = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html, re.I)
        return html_unescape(m.group(1)).strip() if m else ""

    def _detail_year(self, html):
        m = re.search(
            r'ftv-content-hero__year"\s*>.*?>\s*([0-9]{4})\s*</a>', html, re.S | re.I
        )
        return m.group(1) if m else ""

    # ═══ WATCH SERVERS ═══
    def _extract_servers(self, html):
        """Confirmed real markup: <div class="ftv-watch-tabs">
        <button data-server-url="PLAIN https:// URL" ...><strong>NAME
        </strong><small>QUALITY</small></button> -- values are plain,
        already-decoded URLs, no decoding step needed at all."""
        servers = []
        seen = set()
        if not html:
            return servers

        # [NOTE] no reliable closing boundary was found for narrowing this to
        # just the <div class="ftv-watch-tabs"> block (confirmed against the
        # real page: a boundary regex tried here never matched) -- scanning
        # the whole page for data-server-url buttons directly is simpler and
        # already proven correct against the real capture (these buttons
        # only ever appear once, inside the watch-tabs list).
        for btn in re.findall(r'<button[^>]*data-server-url="[^"]+".*?</button>', html, re.S | re.I):
            url_m = re.search(r'data-server-url="([^"]+)"', btn)
            if not url_m:
                continue
            url = url_m.group(1).strip()
            if not url.startswith("http") or url in seen:
                continue
            if not self._host(url):
                continue
            name_m = re.search(r'<strong>\s*([^<]+?)\s*</strong>', btn, re.S)
            quality_m = re.search(r'<small>\s*([^<]+?)\s*</small>', btn, re.S)
            name = name_m.group(1).strip() if name_m else "مشاهدة مباشرة"
            quality = quality_m.group(1).strip() if quality_m else ""
            seen.add(url)
            servers.append({
                "name": "{} ({})".format(name, quality) if quality else name,
                "url": url,
                "type": "direct",
            })
            log("Fivetv: Found server '{}' -> {}".format(name, url[:60]))

        if not servers:
            log("Fivetv: No watch servers found in HTML")
        return servers

    # ═══ DOWNLOAD LINKS ═══
    def _extract_download_links(self, html):
        """Confirmed real markup: <div class="ftv-download-list">
        <article class="ftv-download-server"><div
        class="ftv-download-server__info"><strong>NAME</strong>
        <small>QUALITY</small></div><a href="PLAIN URL"
        class="ftv-download-server__button">...</a></article> -- also
        plain URLs, no decoding needed."""
        downloads = []
        seen = set()
        if not html:
            return downloads

        # [FIX] same multi-line-attribute tolerance as elsewhere in this file.
        list_m = re.search(
            r'ftv-download-list"\s*>(.*?)</div>\s*</div>\s*</div>', html, re.S | re.I
        )
        scope = list_m.group(1) if list_m else html

        for item in re.findall(
            r'<article\s+class="ftv-download-server[^"]*"\s*>(.*?)</article>',
            scope, re.S | re.I,
        ):
            href_m = re.search(r'<a\s+href="([^"]+)"[^>]*class="ftv-download-server__button"', item, re.S | re.I)
            if not href_m:
                continue
            url = href_m.group(1).strip()
            if not url.startswith("http") or url in seen:
                continue
            host = self._host(url)
            if any(x in host for x in self._NON_MEDIA_HOSTS):
                continue
            seen.add(url)

            name_m = re.search(r'<strong>\s*([^<]+?)\s*</strong>', item, re.S)
            quality_m = re.search(r'<small>\s*([^<]+?)\s*</small>', item, re.S)
            name = name_m.group(1).strip() if name_m else ""
            quality = quality_m.group(1).strip() if quality_m else ""

            downloads.append({
                "resolution": quality,
                "size": "",
                "quality": name,
                "url": url,
            })
            log("Fivetv: Found download '{}' ({}) -> {}".format(name, quality, url[:60]))

        if not downloads:
            log("Fivetv: No download links found in HTML")
        return downloads

    # ═══ PUBLIC API ═══
    def get_categories(self, mtype="movie"):
        base = self._get_base()
        return [
            {
                "title": title,
                "url": self._normalize_url(path),
                "type": "category",
                "_action": "category",
            }
            for title, path in self.CATEGORY_FALLBACKS.items()
        ]

    def get_category_items(self, url, page=1):
        base = self._get_base()
        if page > 1 and "/page/" not in url:
            url = url.rstrip("/") + "/page/{}/".format(page)
        html, final_url = self._fetch_live(url, referer=base)
        if self._is_blocked_page(html, final_url):
            log("Fivetv: category blocked {}".format(url))
            return []
        items = self._extract_cards(html)
        next_page = self._extract_next_page(html, final_url or url)
        if next_page:
            items.append({
                "title": "➡️ الصفحة التالية",
                "url": next_page,
                "type": "category",
                "_action": "category",
            })
        return items

    def search(self, query, page=1):
        base = self._get_base()
        search_url = base.rstrip("/") + "/search/?q=" + quote_plus(query)
        html, final_url = self._fetch_live(search_url, referer=base)
        if self._is_blocked_page(html, final_url):
            log("Fivetv: search blocked for '{}'".format(query))
            return []
        items = self._extract_cards(html)
        log("Fivetv: search '{}' -> {} items".format(query, len(items)))
        return items

    def get_page(self, url, m_type=None):
        base = self._get_base()
        html, final_url = self._fetch_live(url, referer=base)
        if self._is_blocked_page(html, final_url) or not html:
            log("Fivetv: detail failed {}".format(url))
            return {"title": "Error", "servers": [], "items": [], "downloads": [], "type": m_type or "movie"}

        title = self._detail_title(html)
        poster = self._detail_poster(html)
        plot = self._detail_plot(html)
        year = self._detail_year(html)

        servers = self._extract_servers(html)
        downloads = self._extract_download_links(html)

        item_type = m_type or self._guess_type(final_url or url)

        # [NOT YET CONFIRMED] a real /series/... page's own markup was never
        # captured this session -- only episode CARDS linking out to
        # standalone /episode/... pages were seen, on a *category* listing
        # page. Best effort only: if this is a /series/ URL and it has no
        # watch servers of its own, try the same card scan in case the
        # series page embeds its episode list the same way a category page
        # does. If a real /series/... snapshot shows a different shape,
        # this will need revisiting.
        episodes = []
        if item_type == "series" and not servers:
            episodes = self._extract_cards(html)

        log("Fivetv: detail {} -> servers={}, episodes={}, downloads={}".format(
            url, len(servers), len(episodes), len(downloads)))

        return {
            "url": final_url or url,
            "title": title,
            "plot": plot,
            "poster": poster,
            "rating": "",
            "year": year,
            "servers": servers,
            "items": episodes,
            "downloads": downloads,
            "type": item_type,
        }
