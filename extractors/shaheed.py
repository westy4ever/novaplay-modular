# -*- coding: utf-8 -*-
"""
Shaheed4u extractor - Fixed for current site structure (shhahidd4u.net / shaheed4u.cash)
Supports: Movies, Series, TV Shows, Wrestling Shows
Uses base.fetch for all HTTP requests.
Inherits from BaseExtractor.
"""

import re
import sys
import json
import time
import threading
from .base import BaseExtractor, fetch, urljoin, log, resolve_iframe_chain, extract_stream_all

if sys.version_info[0] == 3:
    from urllib.parse import quote_plus, urlparse, quote
    from html import unescape as html_unescape
else:
    from urllib import quote_plus, quote
    from urlparse import urlparse
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape


# ─── Process-wide base-domain cache (persists across extractor instances,
# since get_extractor() creates a fresh instance every search - without
# this, a dead/unreachable domain gets fully re-probed from scratch on
# every single search, costing 60-100+ seconds each time). ─────────────
_SHAHEED_BASE_CACHE_TTL = 600   # 10 minutes success cache
_SHAHEED_FAIL_COOLDOWN  = 120   # 2 minutes before retrying after a full failure
_shaheed_base_cache = {"url": None, "resolved_at": 0, "last_fail_at": 0}
_shaheed_base_cache_lock = threading.Lock()


class ShaheedExtractor(BaseExtractor):
    """Extractor for Shaheed4u - shhahidd4u.net / shaheed4u.cash"""
    
    DOMAINS = [
        "https://sshahiid4u.net/",  # [PATCH SH1] confirmed working, not in the old list at all
        "https://shhahidd4u.net/",
        "https://shaheed4u.cash/",  # confirmed dead by the user, kept as a low-priority fallback
        "https://shaied4u.co/",
    ]
    VALID_HOST_MARKERS = ("sshahiid4u.net", "shhahidd4u.net", "shaheed4u.cash", "shaied4u.co", "shahid4u")
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)
    
    def __init__(self):
        super(ShaheedExtractor, self).__init__()
        self.main_url = self.DOMAINS[0]
        self._resolved_base = None
        self._home_html = None
        self._home_last_fetch = 0
    
    def _get_base(self, force_refresh=False):
        now = time.time()
        with _shaheed_base_cache_lock:
            cached_url = _shaheed_base_cache["url"]
            resolved_at = _shaheed_base_cache["resolved_at"]
            last_fail_at = _shaheed_base_cache["last_fail_at"]

        if cached_url and not force_refresh and (now - resolved_at) < _SHAHEED_BASE_CACHE_TTL:
            self._resolved_base = cached_url
            self.main_url = cached_url
            return cached_url

        if not force_refresh and (now - last_fail_at) < _SHAHEED_FAIL_COOLDOWN:
            log("Shaheed: skipping re-probe, all domains failed {}s ago (cooldown)".format(int(now - last_fail_at)))
            self._resolved_base = self.DOMAINS[0]
            self.main_url = self._resolved_base
            return self._resolved_base

        for domain in self.DOMAINS:
            html, final_url = fetch(domain, referer=domain)
            if not html:
                continue
            final_url = final_url or domain
            if self._is_blocked_page(html, final_url):
                continue
            if html and ("شاهد" in html or "shahid" in html.lower() or "film" in html.lower() or "مسلسل" in html):
                resolved = self._site_root(final_url)
                self._resolved_base = resolved
                self.main_url = resolved
                self._home_html = html
                self._home_last_fetch = now
                with _shaheed_base_cache_lock:
                    _shaheed_base_cache["url"] = resolved
                    _shaheed_base_cache["resolved_at"] = now
                log("Shaheed: selected base: {}".format(resolved))
                return resolved

        with _shaheed_base_cache_lock:
            _shaheed_base_cache["url"] = None
            _shaheed_base_cache["last_fail_at"] = now
        self._resolved_base = self.DOMAINS[0]
        self.main_url = self._resolved_base
        log("Shaheed: falling back to: {}".format(self._resolved_base))
        return self._resolved_base
    
    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)
    
    def _is_blocked_page(self, html, final_url=""):
        text = (html or "").lower()
        final = (final_url or "").lower()
        if not text:
            return True
        # [PATCH 71] "cloudflare", "challenge", "captcha", "blocked" appear on normal
        # pages (cdnjs.cloudflare.com, reCAPTCHA scripts, CSS class names)
        hard = ("just a moment", "cf-chl", "cf-browser-verification",
                "verify you are human", "enable javascript and cookies")
        if any(p in text for p in hard):
            return True
        if len(text) < 20000 and any(p in text for p in ("cf-turnstile", "captcha", "access denied")):
            return True
        if any(m in final for m in self.BLOCKED_HOST_MARKERS):
            return True
        if len(text) < 500 and ("error" in text or "block" in text):
            return True
        return False
    
    def _is_valid_category_page(self, html):
        if not html:
            return False
        if re.search(r'class="[^"]*show-card[^"]*"', html, re.I):
            return True
        if re.search(r'href="[^"]*/(film|episode|series|watch)/[^"]*"', html, re.I):
            return True
        if '<title>' in html and ('افلام' in html or 'مسلسلات' in html):
            if not self._is_blocked_page(html) and len(html) > 5000:
                return True
        return False
    
    def _normalize_url(self, url):
        if not url:
            return ""
        url = html_unescape(url.strip())
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http"):
            return urljoin(self._get_base(), url)
        return url
    
    def _fetch_live(self, url, referer=None):
        ref = referer or self._get_base()
        html, final_url = fetch(url, referer=ref)
        if self._is_blocked_page(html, final_url):
            self._get_base(force_refresh=True)
            html, final_url = fetch(url, referer=self._get_base())
            if self._is_blocked_page(html, final_url):
                return "", ""
        return html, final_url or url
    
    def _clean_title(self, title):
        if not title:
            return ""
        title = html_unescape(title)
        title = re.sub(r'\s*[-|]\s*شاهد\s*فور\s*يو.*$', '', title)
        title = re.sub(r'\s*[-|]\s*Shahid4u.*$', '', title, flags=re.I)
        return title.strip()
    
    def _extract_servers_from_watch(self, html, base_url):
        """Parse the watch page HTML to extract server information.

        [PATCH SH1] The old "securedServers" JS variable/embed-stream URL scheme is stale --
        confirmed against real captures the site now uses `let servers = [{...}];` instead,
        with a real, ready-to-use "url" field per entry (a /media-issue/watch/<hash> URL) --
        no construction needed. The page's own source comment claims this array carries "one
        invisible decoy as the last item" not matched by any visible server button, as an
        anti-scraper trap; checked against three separate real captures and found no such
        mismatch (every array index had a matching visible button each time) -- but the cross-
        reference against the visible data-index buttons is kept anyway as a defensive, no-cost
        measure in case that ever becomes real, rather than trusting the array wholesale.
        """
        servers = []
        match = re.search(r'let\s+servers\s*=\s*(\[.*?\]);', html, re.DOTALL | re.I)
        if match:
            try:
                servers_data = json.loads(match.group(1))
                visible_indexes = set(int(m) for m in re.findall(r'data-index="(\d+)"', html))
                for idx, server in enumerate(servers_data):
                    if visible_indexes and idx not in visible_indexes:
                        log("Shaheed: server array index {} has no matching visible button, skipping".format(idx))
                        continue
                    name = server.get("name") or "Server {}".format(idx + 1)
                    url = server.get("url")
                    if url:
                        servers.append({"name": name, "url": url, "type": "embed"})
                if servers:
                    return servers
            except Exception as e:
                log("Shaheed: failed to parse servers array: {}".format(e))

        # [PATCH SH1] older/fallback site variant using securedServers + embed-stream
        match = re.search(r'let\s+securedServers\s*=\s*(\[.*?\]);', html, re.DOTALL | re.I)
        if not match:
            match = re.search(r'securedServers\s*=\s*(\[.*?\]);', html, re.DOTALL | re.I)
        if match:
            try:
                servers_data = json.loads(match.group(1))
                for idx, server in enumerate(servers_data):
                    name = server.get("name", "Server {}".format(idx+1))
                    hash_val = server.get("hash")
                    if hash_val:
                        embed_url = "{}/embed-stream/{}".format(base_url.rstrip('/'), quote(hash_val))
                        servers.append({"name": name, "url": embed_url, "type": "embed"})
                if servers:
                    return servers
            except Exception as e:
                log("Shaheed: failed to parse securedServers: {}".format(e))

        # Fallback: iframes
        iframe_matches = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for src in iframe_matches:
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = urljoin(base_url, src)
            skip_domains = ['youtube', 'facebook', 'twitter', 'google', 'doubleclick',
                            'analytics', 'googletagmanager', 'cloudflareinsights',
                            'adsco.re', 'intelligenceadx']
            if any(x in src.lower() for x in skip_domains):
                continue
            variants = []        # [PATCH 73]
            if variants:
                for stream_url, quality in variants:
                    servers.append({
                        "name": f"Embed Player - {quality}",
                        "url": stream_url,
                        "type": "direct" if stream_url.endswith(('.m3u8', '.mp4')) else "embed"
                    })
            else:
                servers.append({"name": "Embed Player", "url": src, "type": "iframe"})
        return servers
    
    def _extract_downloads_from_page(self, html):
        """[PATCH SH1] Parse a real /download/ page: links are grouped under quality
        headers ("سيرفرات تحميل 1080/720/480"), each a plain <a class="btn btn-down"
        href="...media-issue/download/HASH"> with a <span>hostname</span> inside.
        Confirmed against a real capture -- 45 links across 3 quality tiers, all with
        working URLs (unlike an unrelated site's /download/ page this session that
        turned out to be its homepage in disguise on every capture -- this one is real).
        """
        downloads = []
        sections = re.split(r'سيرفرات\s*تحميل\s*(\d+)', html)
        for i in range(1, len(sections), 2):
            quality = sections[i]
            content = sections[i + 1]
            for m in re.finditer(
                r'<a\s+href="([^"]+)"[^>]*class="[^"]*btn-down[^"]*"[^>]*>(.*?)</a>',
                content, re.S | re.I
            ):
                url = self._normalize_url(m.group(1))
                if not url:
                    continue
                inner = m.group(2)
                name_m = re.search(r'<span>([^<]+)</span>', inner)
                name = html_unescape(name_m.group(1).strip()) if name_m else "Download"
                downloads.append({
                    "name": "{} [{}p]".format(name, quality) if quality else name,
                    "url": url,
                    "quality": quality,
                })
        return downloads

    def get_categories(self, mtype="movie"):
        base = self._get_base().rstrip("/")
        return [
            {"title": "🎬 افلام اجنبي", "url": base + "/category/افلام-اجنبي", "type": "category", "_action": "category"},
            {"title": "🎬 افلام عربي", "url": base + "/category/افلام-عربي", "type": "category", "_action": "category"},
            {"title": "🎬 افلام هندي", "url": base + "/category/افلام-هندي", "type": "category", "_action": "category"},
            {"title": "🎬 افلام انمي", "url": base + "/category/افلام-انمي", "type": "category", "_action": "category"},
            {"title": "🎬 افلام تركية", "url": base + "/category/افلام-تركية", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات اجنبي", "url": base + "/category/مسلسلات-اجنبي", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات تركية", "url": base + "/category/مسلسلات-تركية", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات انمي", "url": base + "/category/مسلسلات-انمي", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات مدبلجة", "url": base + "/category/مسلسلات-مدبلجة", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات عربي", "url": base + "/category/مسلسلات-عربي", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات هندية", "url": base + "/category/مسلسلات-هندية", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات اسيوية", "url": base + "/category/مسلسلات-اسيوية", "type": "category", "_action": "category"},
            {"title": "🤼 عروض مصارعة", "url": base + "/category/عروض-مصارعة", "type": "category", "_action": "category"},
            {"title": "📺 برامج تلفزيونية", "url": base + "/category/برامج-تلفزيونية", "type": "category", "_action": "category"},
            {"title": "🌙 مسلسلات رمضان 2026", "url": base + "/category/مسلسلات-رمضان-2026", "type": "category", "_action": "category"},
        ]
    
    def get_category_items(self, url, page=1):
        html, _ = self._fetch_live(url)
        if not html:
            return []

        if not self._is_valid_category_page(html):
            self._get_base(force_refresh=True)
            html, _ = self._fetch_live(url)
            if not html or not self._is_valid_category_page(html):
                return []

        items = []
        seen_urls = set()

        for match in re.finditer(r'<a\s[^>]*class="[^"]*show-card[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL | re.I):
            tag_open = html[match.start():match.start() + 300]
            card_content = match.group(1)

            href_m = re.search(r'href="([^"]+)"', tag_open, re.I)
            if not href_m:
                continue
            full_url = self._normalize_url(href_m.group(1))
            if not full_url or full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            poster_url = ""
            # [PATCH SH1] real cards use a plain <img src="...">, not a CSS background-image --
            # confirmed against a real capture; the old pattern never matched at all
            poster_m = re.search(r'<img[^>]+src="([^"]+)"', tag_open + card_content, re.I)
            if not poster_m:
                poster_m = re.search(r'background-image:\s*url\(([^)]+)\)', tag_open + card_content, re.I)
                if poster_m:
                    poster_url = self._normalize_url(poster_m.group(1).strip("'\" "))
            else:
                poster_url = self._normalize_url(poster_m.group(1))

            title_m = re.search(r'<p[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</p>', card_content, re.I)
            if not title_m:
                title_m = re.search(r'<[^>]+class="[^"]*title[^"]*"[^>]*>([^<]+)</', card_content, re.I)
            if not title_m:
                title_m = re.search(r'>([^<]{3,})<', card_content)
            title = html_unescape(title_m.group(1).strip()) if title_m else ""
            if not title:
                continue

            quality_m = re.search(r'<span[^>]*class="[^"]*sticker[^"]*"[^>]*>([^<]+)</span>', card_content, re.I)
            quality = quality_m.group(1).strip() if quality_m else ""

            categ_m = re.search(r'<span[^>]*class="[^"]*categ[^"]*"[^>]*>([^<]+)</span>', card_content, re.I)
            category = categ_m.group(1).strip() if categ_m else ""

            item_type = "series" if ("مسلسلات" in category or "عروض" in category or
                                      "/category/مسلسلات" in url or "/category/عروض" in url) else "movie"

            display_title = "{} [{}]".format(title, quality) if quality else title
            items.append({
                "title": display_title,
                "url": full_url,
                "poster": poster_url,
                "plot": category,
                "type": item_type,
                "_action": "details",
            })

        if not items:
            for match in re.finditer(
                r'<(?:article|div)[^>]+class="[^"]*(?:card|item|post|movie)[^"]*"[^>]*>(.*?)</(?:article|div)>',
                html, re.S | re.I
            ):
                block = match.group(1)
                href_m = re.search(r'href="([^"]+)"', block, re.I)
                if not href_m:
                    continue
                full_url = self._normalize_url(href_m.group(1))
                if not full_url or full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                title_m = (re.search(r'<h[1-4][^>]*>([^<]+)</h[1-4]>', block, re.I) or
                           re.search(r'alt="([^"]+)"', block, re.I) or
                           re.search(r'title="([^"]+)"', block, re.I))
                title = html_unescape(title_m.group(1).strip()) if title_m else ""
                if not title:
                    continue

                img_m = (re.search(r'src="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', block, re.I) or
                         re.search(r'data-src="([^"]+)"', block, re.I))
                poster_url = self._normalize_url(img_m.group(1)) if img_m else ""

                items.append({
                    "title": title,
                    "url": full_url,
                    "poster": poster_url,
                    "type": "movie",
                    "_action": "details",
                })

        current_page = None
        max_page = None

        curr_match = re.search(
            r'<button[^>]+class="[^"]*page-link[^"]*cursor-normal[^"]*"[^>]*>(\d+)</button>',
            html, re.I
        )
        if curr_match:
            current_page = int(curr_match.group(1))

        page_nums = set()
        for match in re.finditer(r"updateQuery\('page',\s*(\d+)\)", html):
            page_nums.add(int(match.group(1)))
        if page_nums:
            max_page = max(page_nums)

        if current_page is not None and max_page is not None and max_page > current_page:
            sep = "&" if "?" in url else "?"
            items.append({
                "title": "➡️ Next Page",
                "url": url + sep + "page=" + str(current_page + 1),
                "type": "category",
                "_action": "category",
            })

        return items
    
    def search(self, query, page=1):
        base = self._get_base()
        url = base + "/search?s=" + quote_plus(query)
        if page > 1:
            url += "&page=" + str(page)
        html, _ = self._fetch_live(url)
        if not html:
            return []
        return self.get_category_items(url)
    
    def get_page(self, url, m_type=None):
        html, final_url = self._fetch_live(url)

        result = {
            "url": final_url or url,
            "title": "",
            "plot": "",
            "poster": "",
            "servers": [],
            "items": [],
            "downloads": [],  # [PATCH SH1]
            "type": "movie",
        }

        if not html:
            return result

        title_match = re.search(r'<title>(.*?)</title>', html)
        if title_match:
            title = html_unescape(title_match.group(1))
            title = self._clean_title(title)
            result["title"] = title

        desc_match = re.search(r'<meta\s+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if desc_match:
            result["plot"] = html_unescape(desc_match.group(1))

        poster_match = re.search(r'<meta\s+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if poster_match:
            result["poster"] = self._normalize_url(poster_match.group(1))

        watch_url = None
        if "/watch/" in url:
            watch_url = url
        else:
            watch_link_match = re.search(r'<a[^>]+href=["\']([^"\']+/watch/[^"\']*)["\'][^>]*>.*?مشاهدة', html, re.I | re.S)
            if watch_link_match:
                watch_url = self._normalize_url(watch_link_match.group(1))
            else:
                watch_link_match = re.search(r'<a[^>]+class=["\'][^"\']*watch[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
                if watch_link_match:
                    watch_url = self._normalize_url(watch_link_match.group(1))

        watch_html = None
        if watch_url:
            watch_html, _ = self._fetch_live(watch_url)
        else:
            if "/watch/" in url:
                watch_html = html
                watch_url = url

        # [PATCH SH1] the film page's own download link carries a page-specific ?nav=
        # token that differs from the watch link's -- can't be guessed/constructed,
        # has to come from the page's own href, same pattern as the watch link above.
        download_url = None
        if "/download/" in url:
            download_url = url
        else:
            dl_link_match = re.search(r'<a[^>]+href=["\']([^"\']+/download/[^"\']*)["\'][^>]*>.*?تحميل', html, re.I | re.S)
            if dl_link_match:
                download_url = self._normalize_url(dl_link_match.group(1))

        download_html = None
        if download_url:
            download_html, _ = self._fetch_live(download_url)
        elif "/download/" in url:
            download_html = html

        if download_html:
            downloads = self._extract_downloads_from_page(download_html)
            if downloads:
                result["downloads"] = downloads
            else:
                log("Shaheed: no downloads found on download page")

        if watch_html:
            base_for_embed = self._get_base().rstrip('/')
            servers = self._extract_servers_from_watch(watch_html, base_for_embed)
            if servers:
                result["servers"] = servers
            else:
                log("Shaheed: no servers found on watch page")

            eps_container_match = re.search(r'<div[^>]*id=["\']eps["\'][^>]*>(.*?)</div>', watch_html, re.S | re.I)
            if not eps_container_match:
                eps_container_match = re.search(r'<div[^>]*class=["\'][^"\']*eps[^"\']*["\'][^>]*>(.*?)</div>', watch_html, re.S | re.I)
            if eps_container_match:
                eps_html = eps_container_match.group(1)
                for ep_match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', eps_html, re.S | re.I):
                    ep_url = self._normalize_url(ep_match.group(1))
                    if not ep_url or ep_url == watch_url:
                        continue
                    ep_inner = ep_match.group(2)
                    ep_text = re.sub(r'<[^>]+>', '', ep_inner).strip()
                    ep_num_match = re.search(r'الحلقة\s*(\d+)', ep_text)
                    if ep_num_match:
                        ep_title = "حلقة {}".format(ep_num_match.group(1))
                    else:
                        ep_title = ep_text or "حلقة"
                    if ep_url != watch_url and ep_url not in [item.get("url") for item in result["items"]]:
                        result["items"].append({
                            "title": ep_title,
                            "url": ep_url,
                            "type": "episode",
                            "_action": "details",
                        })
            else:
                log("Shaheed: no episode list found on watch page")

        if result["items"]:
            result["type"] = "series"
        elif "مسلسلات" in url or "series" in url.lower() or "/عروض" in url or "/post/" in url:
            result["type"] = "series"
        elif "/episode/" in url:
            result["type"] = "episode"
        else:
            result["type"] = "movie"

        return result
    
    def extract_stream(self, url):
        log("Shaheed extract_stream: {}".format(url))
        referer = self._get_base()
        if "|" in url:
            parts = url.split("|", 1)
            url = parts[0]
            if "Referer=" in parts[1]:
                referer = parts[1].split("Referer=")[1].strip()

        if url.startswith("/"):
            url = urljoin(self._get_base(), url)

        stream, _ = resolve_iframe_chain(url, referer=referer, max_depth=10)
        if stream:
            return stream, None, referer

        html, _ = fetch(url, referer=referer)
        if html:
            video_src = re.search(r'(?:src|data-src)=["\']([^"\']+\.(?:mp4|m3u8|webm)[^"\']*)["\']', html, re.I)
            if video_src:
                return video_src.group(1), None, referer
            iframe_src = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
            if iframe_src:
                return self.extract_stream(iframe_src.group(1))

        from .base import extract_stream as base_extract_stream
        return base_extract_stream(url)