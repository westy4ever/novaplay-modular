# -*- coding: utf-8 -*-
"""
Extractor for Akwam - Multi-domain support
Updated for akwam.ss layout, categories, and stream extraction.
Inherits from BaseExtractor.

FIX: Watch URL regex was truncating /watch/<id>/<movie_id>/<slug> to /watch/<id>.
FIX: Plot description no longer mangled by _clean_title().
FIX: Quality detection now matches the trailing quality tag in the filename.
FIX: Now reads the <source size="XXX"> attribute as the primary quality signal.
UPD: get_categories() labels switched to Arabic to match the site's UI.
FIX: Episode extraction now matches URLs by structure (/episode/<id>...).
FIX: Percent-encode all URLs before use.
FIX: Episodes are now returned in ascending order (episode 1 first).
FIX: Series with paginated episode lists (e.g. many-episode series) are now
     fully extracted. We read `numberOfEpisodes` from the JSON-LD block and,
     if we got fewer episodes than that, follow `?page=N` variants until the
     count matches or the site stops returning new episodes.
"""

import re
import sys
from .base import BaseExtractor, fetch, log, urljoin

try:
    from urllib.parse import urlsplit, urlunsplit, quote, unquote
except ImportError:
    from urlparse import urlsplit, urlunsplit
    from urllib import quote, unquote


class AkwamExtractor(BaseExtractor):
    """Extractor for Akwam - supports multiple domains"""

    MAIN_URL = "https://akwam.ss/"

    DOMAINS = [
        "https://akwam.ss/",
        "https://akwam.it/",
        "https://akwam.net/",
    ]

    # Hard cap on the number of follow-up pages fetched to complete an
    # episode list. Prevents runaway loops if the site misreports its own
    # episode count or the pagination never terminates.
    MAX_EPISODE_PAGES = 25

    def __init__(self):
        super(AkwamExtractor, self).__init__()
        self.main_url = self.MAIN_URL
        self._resolved_base = None

    # ─────────────────────────────────────────────────────────────────────
    # URL handling
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _encode_url(url):
        if not url:
            return url
        try:
            parts = urlsplit(url)
        except Exception:
            return url

        try:
            url.encode("ascii")
            return url
        except UnicodeEncodeError:
            pass

        path = quote(parts.path, safe="/%:@!$&'()*+,;=~-._")
        query = quote(parts.query, safe="=&%:@!$'()*+,;~-._?/")
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))

    def _normalize_url(self, url):
        if not url:
            return ""
        url = str(url).strip()
        url = url.replace('&amp;', '&')

        if "downet.net" in url:
            url = url.replace(" ", "%20")

        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith("http"):
            url = urljoin(self._get_base(), url)

        return self._encode_url(url)

    def _get_base(self):
        if self._resolved_base:
            return self._resolved_base

        for domain in self.DOMAINS:
            try:
                log("Akwam: Probing domain {}".format(domain))
                html, final_url = fetch(domain, referer=domain)
                if not html:
                    continue

                lower_html = html.lower()
                if "just a moment" in lower_html or "cf-chl" in lower_html:
                    log("Akwam: Domain {} blocked by Cloudflare".format(domain))
                    continue

                final_host = re.search(r'https?://([^/]+)', final_url or "")
                if final_host and not any(d in final_host.group(1) for d in ("akwam", "akoam")):
                    log("Akwam: Domain {} redirected to unknown host {}".format(domain, final_host.group(1)))
                    continue

                self._resolved_base = domain
                self.main_url = domain
                log("Akwam: Selected working domain: {}".format(domain))
                return self._resolved_base
            except Exception as e:
                log("Akwam: Domain {} failed with error: {}".format(domain, e))
                continue

        log("Akwam: All domains failed, falling back to {}".format(self.MAIN_URL))
        self._resolved_base = self.MAIN_URL
        return self._resolved_base

    def _clean_title(self, title):
        if not title:
            return ""
        title = title.replace("&amp;", "&")
        title = title.replace("مشاهدة", "")
        title = title.replace("تحميل", "")
        title = title.replace("فيلم", "")
        title = title.replace("مسلسل", "")
        title = re.sub(r'\s*[-|]\s*أكوام.*$', '', title)
        title = re.sub(r'\s*[-|]\s*Akwam.*$', '', title, flags=re.I)
        return title.strip()

    _NAV_PATH_RE = re.compile(
        r'^/?(?:movies|series|shows|mix|recent|category/[^/?]*)/?(?:\?.*)?$',
        re.I
    )

    def _is_nav_link(self, href):
        href = (href or "").strip()
        if "#" in href or "/page/" in href or "/category/" in href:
            return True
        path = re.sub(r'^(?:https?:)?//[^/]+', '', href)
        if not path.strip("/"):
            return True
        return bool(self._NAV_PATH_RE.match(path))

    # ─────────────────────────────────────────────────────────────────────
    # Quality detection
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _quality_from_size(size):
        if not size:
            return None
        size = str(size).strip()
        if size == "2160":
            return "4K"
        if size == "1440":
            return "1440p"
        if size in ("1080", "720", "480", "360", "240"):
            return "{}p".format(size)
        return None

    @staticmethod
    def _detect_quality(url):
        if not url:
            return "HD"
        lowered = url.lower().split("?")[0]

        for res in ("1080", "720", "480", "360", "240"):
            if re.search(r'(?:^|[^\d]){}p\.mp4'.format(res), lowered):
                return "{}p".format(res)

        for res in ("1080", "720", "480", "360", "240"):
            if re.search(r'\.{}\.mp4'.format(res), lowered):
                return "{}p".format(res)

        for res in ("1080", "720", "480", "360", "240"):
            if res in lowered:
                return "{}p".format(res)

        return "HD"

    # ─────────────────────────────────────────────────────────────────────
    # Categories / listing / search
    # ─────────────────────────────────────────────────────────────────────

    def get_categories(self, mtype="movie"):
        base = self._get_base()
        return [
            {"title": "🎬 أفلام اجنبي",     "url": urljoin(base, "movies?section=30&category=0"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام عربي",      "url": urljoin(base, "movies?section=29&category=0"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام هندي",      "url": urljoin(base, "movies?section=31&category=0"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام تركي",      "url": urljoin(base, "movies?section=32&category=0"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام اسيوي",     "url": urljoin(base, "movies?section=33&category=0"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام انمي",      "url": urljoin(base, "movies?section=30&category=30"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام نيتفليكس",  "url": urljoin(base, "movies?section=30&category=72"), "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات اجنبي",   "url": urljoin(base, "series?section=30&category=0"), "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات عربي",    "url": urljoin(base, "series?section=29&category=0"), "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات تركي",    "url": urljoin(base, "series?section=32&category=0"), "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات انمي",    "url": urljoin(base, "series?section=30&category=30"), "type": "category", "_action": "category"},
            {"title": "📡 تلفزيون",              "url": urljoin(base, "shows"),                          "type": "category", "_action": "category"},
            {"title": "🤼 مصارعة حرة",           "url": urljoin(base, "shows?section=43&category=0"),    "type": "category", "_action": "category"},
            {"title": "🎞️ برامج وثائقية",        "url": urljoin(base, "shows?section=46&category=0"),    "type": "category", "_action": "category"},
            {"title": "🎭 منوعات",               "url": urljoin(base, "mix"),                            "type": "category", "_action": "category"},
            {"title": "🆕 أضيف حديثا",           "url": urljoin(base, "recent"),                         "type": "category", "_action": "category"},
        ]

    def get_category_items(self, url, page=1):
        url = url.replace('&amp;', '&')

        if 'page=' not in url:
            if '?' in url:
                url += '&page=1'
            else:
                url += '?page=1'

        url = self._encode_url(url)
        log("Akwam: Fetching category URL: {}".format(url))

        html, final_url = fetch(url, referer=self._get_base())
        if not html:
            log("Akwam: get_category_items failed for {}".format(url))
            return []

        items = []
        seen = set()

        current_page = 1
        page_match = re.search(r'[?&]page=(\d+)', url)
        if page_match:
            current_page = int(page_match.group(1))

        log("Akwam: Current page: {}".format(current_page))

        items.append({
            "title": "━━━ Page {} ━━━".format(current_page),
            "type": "separator",
            "_action": "separator",
        })

        entry_boxes = re.split(r'<div class="entry-box', html)
        log("Akwam: Found {} entry-box sections".format(len(entry_boxes) - 1))

        for box in entry_boxes[1:]:
            link_match = re.search(r'<a\s+href="([^"]+)"', box, re.I)
            if not link_match:
                continue

            movie_url = link_match.group(1)
            if self._is_nav_link(movie_url):
                continue

            full_url = self._normalize_url(movie_url)
            if not full_url or full_url in seen:
                continue

            title = ""
            title_match = re.search(r'<h3[^>]*>(.*?)</h3>', box, re.S | re.I)
            if title_match:
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            else:
                title_match = re.search(r'(?:alt|title)="([^"]+)"', box, re.I)
                if title_match:
                    title = title_match.group(1)

            if not title:
                continue

            seen.add(full_url)

            poster = ""
            img_match = re.search(r'<img[^>]+(?:data-src|data-lazy-src|data-original|src)="([^"]+)"', box, re.I)
            if img_match:
                poster = img_match.group(1)
                if "placeholder" in poster.lower():
                    poster = ""
                else:
                    poster = self._normalize_url(poster)

            items.append({
                "title": self._clean_title(title),
                "url": full_url,
                "poster": poster,
                "type": "movie",
                "_action": "details",
            })

        log("Akwam: Extracted {} movie items from page {}".format(len(items) - 1, current_page))

        next_url = None
        next_page_num = current_page + 1

        next_match = re.search(
            r'<a\s+class="page-link"[^>]+href="([^"]+)"[^>]*>{}</a>'.format(next_page_num),
            html, re.I
        )
        if not next_match:
            next_match = re.search(
                r'<a\s+[^>]*href="([^"]+)"[^>]*>.*?التالي.*?</a>',
                html, re.I | re.S
            )

        if next_match:
            next_url = self._normalize_url(next_match.group(1))
            if next_url and next_url != url:
                log("Akwam: Found next page: {}".format(next_url))
                items.append({
                    "title": "➡️ Page {} (Next)".format(current_page + 1),
                    "url": next_url,
                    "type": "category",
                    "_action": "category",
                })

        log("Akwam: Total items returned: {}".format(len(items)))
        return items

    def search(self, query, page=1):
        base = self._get_base()
        search_url = urljoin(base, "search?q=" + quote(query))
        if page > 1:
            search_url = urljoin(base, "search?q={}&page={}".format(quote(query), page))

        search_url = self._encode_url(search_url)
        log("Akwam: Searching for: {}".format(query))

        html, _ = fetch(search_url, referer=base)
        if not html:
            return []

        items = []
        entry_boxes = re.split(r'<div class="entry-box', html)

        for box in entry_boxes[1:]:
            link_match = re.search(r'<a\s+href="([^"]+)"', box, re.I)
            if not link_match:
                continue

            movie_url = link_match.group(1)
            if self._is_nav_link(movie_url):
                continue

            full_url = self._normalize_url(movie_url)
            title = ""
            title_match = re.search(r'<h3[^>]*>(.*?)</h3>', box, re.S | re.I)
            if title_match:
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            else:
                title_match = re.search(r'(?:alt|title)="([^"]+)"', box, re.I)
                if title_match:
                    title = title_match.group(1)

            if title and full_url:
                items.append({
                    "title": self._clean_title(title),
                    "url": full_url,
                    "poster": "",
                    "type": "movie",
                    "_action": "details",
                })

        log("Akwam: Search found {} results".format(len(items)))
        return items

    # ─────────────────────────────────────────────────────────────────────
    # Stream extraction
    # ─────────────────────────────────────────────────────────────────────

    def _extract_servers_from_watch_html(self, watch_html):
        servers = []
        seen_urls = set()
        seen_qualities = set()

        def _add(url, quality=None):
            if not url:
                return
            url = url.strip().replace("&amp;", "&")
            if url.startswith("https//"):
                url = "https://" + url[6:]
            elif url.startswith("http//"):
                url = "http://" + url[5:]
            if url.lower().startswith("data:"):
                return
            if url in seen_urls:
                return

            if not quality:
                quality = self._detect_quality(url)

            if quality in seen_qualities:
                seen_urls.add(url)
                return

            seen_urls.add(url)
            seen_qualities.add(quality)

            if "downet.net" in url:
                url = url.replace(" ", "%20")
            url = self._normalize_url(url)

            servers.append({
                "name": "🎬 {} - Akwam".format(quality),
                "url": url,
                "type": "direct"
            })
            log("Akwam: Added {}: {}".format(quality, url[:90]))

        for tag_m in re.finditer(r'<source\b[^>]*>', watch_html, re.I):
            tag = tag_m.group(0)
            src_m = re.search(r'src="([^"]+)"', tag, re.I)
            if not src_m:
                continue
            size_m = re.search(r'size="(\d+)"', tag, re.I)
            explicit_quality = self._quality_from_size(size_m.group(1)) if size_m else None
            _add(src_m.group(1), explicit_quality)

        for tag_m in re.finditer(r'<video\b[^>]*>', watch_html, re.I):
            tag = tag_m.group(0)
            src_m = re.search(r'src="([^"]+)"', tag, re.I)
            if src_m:
                _add(src_m.group(1))

        if not servers:
            for m in re.findall(
                r'(https?:?//[^"\'\s<>#]+\.downet\.net/[^"\'\s<>#]+\.mp4)',
                watch_html, re.I
            ):
                _add(m)

        if not servers:
            for pattern in [
                r'file\s*:\s*["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
                r'source\s*:\s*["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
                r'data-(?:url|src|video)=["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
                r'"(?:file|src|url)"\s*:\s*"([^"]+\.(?:mp4|m3u8)[^"]*)"',
            ]:
                matches = re.findall(pattern, watch_html, re.I)
                for m in matches:
                    _add(m)
                if servers:
                    break

        return servers

    # ─────────────────────────────────────────────────────────────────────
    # Episode extraction with pagination
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_expected_episode_count(html):
        """
        Read the expected episode count from the JSON-LD block. Returns 0
        if the count cannot be determined.
        """
        m = re.search(
            r'"numberOfEpisodes"\s*:\s*(\d+)',
            html, re.I
        )
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return 0
        return 0

    def _collect_episodes_from_html(self, html, base_url, seen, episodes):
        """
        Find all `/episode/<id>...` links in `html` and append new ones
        to `episodes`, tracking URLs in `seen` to dedupe across pages.
        """
        found_any = False
        for m in re.finditer(
            r'<a[^>]+href="([^"]*/episode/\d+[^"]*)"[^>]*>(.*?)</a>',
            html, re.S | re.I
        ):
            ep_url = self._normalize_url(m.group(1))
            if not ep_url or ep_url in seen or ep_url == base_url:
                continue
            seen.add(ep_url)
            found_any = True

            ep_text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            ep_text = re.sub(r'\s+', ' ', ep_text)
            if not ep_text:
                slug = ep_url.rstrip('/').split('/')[-1]
                try:
                    slug = unquote(slug)
                except Exception:
                    pass
                ep_text = slug.replace('-', ' ').strip() or "حلقة"

            episodes.append({
                "title": ep_text,
                "url": ep_url,
                "type": "episode",
                "_action": "details",
            })
        return found_any

    def _try_episode_page_variants(self, base_url, page_num):
        """
        Return a list of candidate URLs to try for the Nth page of an
        episode list. The site has used several patterns over time; we
        probe each and use whichever yields new episodes.
        """
        # Strip any pre-existing page param to avoid duplication
        clean = re.sub(r'([?&])page=\d+', r'\1', base_url).rstrip('&?')

        candidates = []
        sep = "&" if "?" in clean else "?"
        candidates.append("{}{}page={}".format(clean, sep, page_num))
        candidates.append("{}{}ep_page={}".format(clean, sep, page_num))
        candidates.append("{}/page/{}".format(clean.rstrip("/"), page_num))
        candidates.append("{}/page/{}/".format(clean.rstrip("/"), page_num))
        return candidates

    # ─────────────────────────────────────────────────────────────────────
    # Main page fetch
    # ─────────────────────────────────────────────────────────────────────

    def get_page(self, url, m_type=None):
        if not url or url.startswith("javascript"):
            return {"title": "Error", "servers": [], "items": [], "type": "movie"}

        url = self._normalize_url(url)
        log("Akwam: Getting movie page: {}".format(url))

        html, final_url = fetch(url, referer=self._get_base())
        if not html:
            log("Akwam: get_page failed for {}".format(url))
            return {"title": "Error", "servers": [], "items": []}

        result = {
            "url": final_url or url,
            "title": "",
            "poster": "",
            "plot": "",
            "servers": [],
            "items": [],
            "type": "movie",
        }

        title_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.I)
        if title_match:
            result["title"] = self._clean_title(title_match.group(1))

        poster_match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html, re.I)
        if poster_match:
            result["poster"] = self._normalize_url(poster_match.group(1))

        plot_match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html, re.I)
        if plot_match:
            result["plot"] = plot_match.group(1)

        page_app_match = re.search(
            r'<input[^>]+id="page_app"[^>]+value="([^"]+)"',
            html, re.I
        )
        page_app = page_app_match.group(1).lower() if page_app_match else ""

        url_lower = url.lower()
        is_episode = (
            "/episode/" in url_lower
            or page_app == "episodes"
            or "الحلقة" in result["title"]
            or "حلقة" in result["title"]
        )
        is_series = (
            not is_episode
            and (
                "/series/" in url_lower
                or page_app == "series"
                or "مسلسل" in result["title"]
            )
        )

        if is_episode:
            result["type"] = "episode"
        elif is_series:
            result["type"] = "series"
        else:
            result["type"] = "movie"

        watch_url = None
        watch_html = html

        if "/watch/" not in url:
            watch_match = re.search(
                r'(https?://[^"\'\s<>]+/watch/\d+[^"\'\s<>]*)',
                html, re.I
            )
            if watch_match:
                watch_url = watch_match.group(1)
            else:
                rel_watch_match = re.search(r'href="(/watch/[^"]+)"', html, re.I)
                if rel_watch_match:
                    watch_url = rel_watch_match.group(1)

            if watch_url:
                watch_url = self._normalize_url(watch_url)
                log("Akwam: Fetching watch page: {}".format(watch_url))
                watch_html, _ = fetch(watch_url, referer=url)

        if watch_html:
            servers = self._extract_servers_from_watch_html(watch_html)
            if servers:
                result["servers"] = servers
                log("Akwam: Found {} server(s) for {}".format(len(servers), result["title"]))

        if not result["servers"]:
            watch_match = re.search(
                r'href="(https?://go\.[^"]*akwam[^"]*/watch/\d+)"',
                html, re.I
            )
            if watch_match:
                normalized_url = self._normalize_url(watch_match.group(1))
                if normalized_url:
                    from .base import extract_stream_all
                    variants = extract_stream_all(normalized_url)
                    if variants:
                        for stream_url, quality in variants:
                            result["servers"].append({
                                "name": "🎬 {} - Akwam (Redirect)".format(quality),
                                "url": stream_url,
                                "type": "direct"
                            })
                    else:
                        result["servers"].append({
                            "name": "🎬 Play Movie",
                            "url": normalized_url,
                            "type": "redirect"
                        })

        # ── Extract episodes for series pages ──
        if is_series and not is_episode:
            episodes = []
            seen_eps = set()

            # Extract whatever the initial page shows.
            self._collect_episodes_from_html(html, url, seen_eps, episodes)

            # If the JSON-LD declares more episodes than we found, assume
            # the widget is paginated and follow ?page=N variants.
            expected = self._parse_expected_episode_count(html)
            log("Akwam: Found {} episodes on initial page (expected {})".format(
                len(episodes), expected
            ))

            if expected > len(episodes):
                for page_num in range(2, self.MAX_EPISODE_PAGES + 1):
                    if len(episodes) >= expected:
                        break

                    got_new = False
                    for candidate in self._try_episode_page_variants(url, page_num):
                        try:
                            page_html, _ = fetch(candidate, referer=url)
                        except Exception as e:
                            log("Akwam: episode page {} failed: {}".format(candidate, e))
                            continue
                        if not page_html:
                            continue

                        before = len(episodes)
                        self._collect_episodes_from_html(
                            page_html, url, seen_eps, episodes
                        )
                        if len(episodes) > before:
                            log("Akwam: page {} added {} episodes (total {})".format(
                                page_num, len(episodes) - before, len(episodes)
                            ))
                            got_new = True
                            break

                    if not got_new:
                        log("Akwam: no new episodes on page {} — stopping".format(page_num))
                        break

            # Site lists newest-first; reverse so episode 1 appears first.
            episodes.reverse()

            result["items"] = episodes
            log("Akwam: Found {} episodes for series (expected {})".format(
                len(episodes), expected
            ))

        log("Akwam: Found {} servers for {}".format(len(result["servers"]), result["title"]))
        return result

    def extract_stream(self, url):
        from .base import extract_stream as base_extract_stream
        return base_extract_stream(url)