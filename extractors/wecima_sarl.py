# -*- coding: utf-8 -*-
"""
Wecima.sarl extractor - wecima.sarl
Inherits from BaseExtractor.
Different from wecima.cx - uses different HTML structure and URL patterns.

FIX: download support brought to parity with wecima.py - the detail page's
download section ('Download--Wecima--Single' / data-href items) is now
parsed into the same {"resolution", "size", "quality", "url"} entry shape,
and get_page() returns it under the "downloads" key so the shared
plugin_downloads.resolve_download_link() -> download_manager() flow works
unchanged for both extractors.

FIX (2026-09-10): [PATCH 77] _extract_next_page_sarl no longer invents a
next URL on the last page and no longer picks the first numbered link
(which walked backwards on page 3+). It now only returns a real next
link, or the numbered link matching current_page+1.
"""

import re
import sys
import base64
import json
import urllib.parse
from .base import BaseExtractor, fetch, log, urljoin

if sys.version_info[0] == 3:
    from urllib.parse import quote_plus, urlparse, quote, parse_qs
    from html import unescape as html_unescape
else:
    from urllib import quote_plus
    from urlparse import urlparse, parse_qs
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape


class WecimaSarlExtractor(BaseExtractor):
    """Extractor for Wecima.sarl - wecima.sarl"""

    DOMAINS = [
        "https://wecima.sarl/",
    ]
    VALID_HOST_MARKERS = ("wecima.sarl", "akhbarworld.online", "pinecrestdesignstudio.shop")
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)

    # Hosts that never host downloadable media - filtered from every
    # server/download extraction pass.
    _NON_MEDIA_HOSTS = (
        "facebook.com", "twitter.com", "instagram.com", "youtube.com",
        "google.com", "t.me", "telegram.me", "whatsapp.com",
    )

    CATEGORY_FALLBACKS = {
        # Movies
        "افلام اجنبي":    "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/",
        "افلام عربي":     "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%b9%d8%b1%d8%a8%d9%8a/",
        "افلام انمي":     "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d9%86%d9%85%d9%8a/",
        "افلام اسيوي":    "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a/",
        "افلام تركية":    "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%aa%d8%b1%d9%83%d9%8a%d8%a9/",
        "افلام هندي":     "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d9%87%d9%86%d8%af%d9%8a/",
        
        # TV Series
        "مسلسلات اجنبي":  "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/",
        "مسلسلات عربية":  "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b9%d8%b1%d8%a8%d9%8a%d8%a9/",
        "مسلسلات انمي":   "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d9%86%d9%85%d9%8a/",
        "مسلسلات تركية":  "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%aa%d8%b1%d9%83%d9%8a%d8%a9/",
        "مسلسلات هندية":  "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d9%87%d9%86%d8%af%d9%8a%d8%a9/",
        "مسلسلات اسيوية": "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a%d8%a9/",
        "مسلسلات مدبلجة": "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d9%85%d8%af%d8%a8%d9%84%d8%ac%d8%a9/",
        
        # Ramadan
        "مسلسلات رمضان 2026": "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2026/",
        "مسلسلات رمضان 2025": "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2025/",
        "مسلسلات رمضان 2024": "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2024/",
        
        # Quick links (homepage tabs)
        "جديد":          "/",
        "افلام جديدة":   "/movies/",
        "آخر الحلقات":   "/episodes/",
        "مسلسلات جديدة": "/series/",
    }

    def __init__(self):
        super(WecimaSarlExtractor, self).__init__()
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
        if "watch it legally" in text or "alliance for creativity" in text:
            return True
        if any(m in final for m in self.BLOCKED_HOST_MARKERS):
            return True
        return False

    def _looks_like_wecima_page(self, html):
        text = html or ""
        return (
            "wecima.sarl" in text.lower()
            or "Wecima" in text
            or "وى سيما" in text
            or "ماى سيما" in text
            or "mycima" in text.lower()
            or "عرض الفلاتر" in text
            or "الفئة العمرية" in text
            or "الافلام" in text
            or "Grid--WecimaPosts" in text
            or "wecima--filter" in text
        )

    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)

    def _get_base(self):
        if self._resolved_base:
            return self._resolved_base
        for domain in self.DOMAINS:
            log("Wecima.sarl: probing {}".format(domain))
            html, final_url = fetch(domain, referer=domain)
            final_url = final_url or domain
            if self._is_blocked_page(html, final_url):
                log("Wecima.sarl: blocked {}".format(final_url))
                continue
            if html and self._looks_like_wecima_page(html):
                self._resolved_base = self._site_root(final_url)
                self.main_url = self._resolved_base
                self._home_html_cache = html
                log("Wecima.sarl: selected base {}".format(self._resolved_base))
                return self._resolved_base
        self._resolved_base = self.DOMAINS[0]
        self.main_url = self._resolved_base
        log("Wecima.sarl: fallback base {}".format(self.main_url))
        return self.main_url

    def _normalize_url(self, url):
        if not url:
            return ""
        url = url.strip()
        url = url.replace("\\u0026", "&").replace("&amp;", "&").replace("\\/", "/")
        url = html_unescape(url)
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http"):
            return urljoin(self._get_base(), url)
        if any(m in self._host(url) for m in self.BLOCKED_HOST_MARKERS):
            return ""
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
            log("Wecima.sarl: fetching {}".format(candidate))
            html, final_url = fetch(candidate, referer=referer or self._get_base())
            final_url = final_url or candidate
            if self._is_blocked_page(html, final_url):
                log("Wecima.sarl: blocked {}".format(final_url))
                continue
            if html and self._looks_like_wecima_page(html):
                log("Wecima.sarl: success {}".format(final_url))
                return html, final_url
            if html:
                log("Wecima.sarl: page shape mismatch {}".format(final_url))
        log("Wecima.sarl: fetch failed for {}".format(url))
        return "", ""

    def _clean_html(self, text):
        text = html_unescape(text or "")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _clean_title(self, title):
        title = self._clean_html(title)
        # Remove common prefixes/suffixes
        prefixes = [
            "مشاهدة فيلم", "مشاهدة مسلسل", "مشاهدة",
            "فيلم", "مسلسل", "اون لاين", "أون لاين",
            "مترجم", "مترجمة", "مدبلج", "مدبلجة",
            "مشاهدة", "حلقة", "الحلقة",
        ]
        for token in prefixes:
            title = title.replace(token, "")
        # Remove extra spaces and separators
        title = re.sub(r"\s+", " ", title).strip(" -|")
        # Remove trailing numbers if they're episode numbers
        title = re.sub(r"\s+\d+$", "", title)
        return title

    def _home_html(self):
        if self._home_html_cache:
            return self._home_html_cache
        base = self._get_base()
        html, final_url = self._fetch_live(base, referer=base)
        self._home_html_cache = html if not self._is_blocked_page(html, final_url) else ""
        return self._home_html_cache

    def _guess_type(self, title, url):
        text = "{} {}".format(title or "", url or "").lower()
        if any(t in text for t in ("episode", "الحلقة", "حلقة", "/episode/")):
            return "episode"
        if any(t in text for t in ("series", "/series", "مسلسل", "season", "/season/")):
            return "series"
        return "movie"

    def _extract_cards_sarl(self, html):
        """
        Extract content cards from wecima.sarl.
        Based on the HTML snapshot, items use the GridItem structure.
        """
        cards = []
        seen = set()
        
        if not html:
            return cards
        
        # Find all GridItem blocks
        grid_item_pattern = r'<div\s+class="[^"]*GridItem[^"]*"[^>]*>(.*?)</div>\s*(?:<ul[^>]*>.*?</ul>)?'
        grid_items = re.findall(grid_item_pattern, html, re.S | re.I)
        
        if not grid_items:
            log("Wecima.sarl: No GridItem blocks found with primary pattern. Trying fallback.")
            # Fallback: Directly find links with titles in any GridItem context
            fallback_pattern = r'<div[^>]*class="[^"]*GridItem[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>.*?<strong[^>]*>(.*?)</strong>.*?</div>'
            fallback_matches = re.findall(fallback_pattern, html, re.S | re.I)
            for url, title_html in fallback_matches:
                if not url or not title_html:
                    continue
                url = self._normalize_url(url)
                if url in seen or not url:
                    continue
                seen.add(url)
                
                title = self._clean_title(title_html)
                year = ""
                year_match = re.search(r'<span[^>]*class="[^"]*year[^"]*"[^>]*>\s*\(?\s*(\d{4})\s*\)?\s*</span>', title_html, re.I)
                if year_match:
                    year = year_match.group(1)
                    title = re.sub(r'\s*\(?\s*' + year + r'\s*\)?\s*', '', title).strip()
                
                # Check if it's an episode
                ep_match = re.search(r'<div[^>]*class="[^"]*Episode--number[^"]*"[^>]*>.*?<span>(\d+)</span>', html, re.S | re.I)
                item_type = "episode" if ep_match else self._guess_type(title, url)
                if ep_match:
                    ep_num = ep_match.group(1)
                    if not re.search(r'حلقة\s*' + ep_num, title):
                        title = "{} حلقة {}".format(title, ep_num)
                
                poster = ""
                poster_match = re.search(r'data-lazy-style="[^"]*--image:\s*url\(([^)]+)\)', html, re.I)
                if poster_match:
                    poster = self._normalize_url(poster_match.group(1).strip("'\" "))
                elif re.search(r'style="[^"]*--image:\s*url\(([^)]+)\)', html, re.I):
                    poster_match = re.search(r'style="[^"]*--image:\s*url\(([^)]+)\)', html, re.I)
                    if poster_match:
                        poster = self._normalize_url(poster_match.group(1).strip("'\" "))
                
                cards.append({
                    "title": title,
                    "url": url,
                    "poster": poster or "",
                    "plot": year,
                    "year": year,
                    "type": item_type,
                    "_action": "details",
                })
            log("Wecima.sarl: extracted {} cards from fallback".format(len(cards)))
            return cards
        
        # Process each GridItem block
        for item in grid_items:
            # Extract link and title
            link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>.*?<strong[^>]*>(.*?)</strong>', item, re.S | re.I)
            if not link_match:
                continue
            url = self._normalize_url(link_match.group(1))
            if not url or url in seen:
                continue
            
            # Skip unwanted URLs
            lowered = url.lower()
            if any(t in lowered for t in ("/category/", "/tag/", "/page/", "/filtering", "/feed/")):
                continue
            
            title_html = link_match.group(2)
            title = self._clean_title(title_html)
            if not title:
                continue
            
            # Extract year
            year = ""
            year_match = re.search(r'<span[^>]*class="[^"]*year[^"]*"[^>]*>\s*\(?\s*(\d{4})\s*\)?\s*</span>', title_html, re.I)
            if year_match:
                year = year_match.group(1)
                title = re.sub(r'\s*\(?\s*' + year + r'\s*\)?\s*', '', title).strip()
            
            # Extract episode number
            ep_match = re.search(r'<div[^>]*class="[^"]*Episode--number[^"]*"[^>]*>.*?<span>(\d+)</span>', item, re.S | re.I)
            item_type = "episode" if ep_match else self._guess_type(title, url)
            if ep_match:
                ep_num = ep_match.group(1)
                if not re.search(r'حلقة\s*' + ep_num, title):
                    title = "{} حلقة {}".format(title, ep_num)
            
            # Extract poster
            poster = ""
            poster_match = re.search(r'data-lazy-style="[^"]*--image:\s*url\(([^)]+)\)', item, re.I)
            if not poster_match:
                poster_match = re.search(r'style="[^"]*--image:\s*url\(([^)]+)\)', item, re.I)
            if poster_match:
                poster = self._normalize_url(poster_match.group(1).strip("'\" "))
            
            seen.add(url)
            cards.append({
                "title": title,
                "url": url,
                "poster": poster or "",
                "plot": year,
                "year": year,
                "type": item_type,
                "_action": "details",
            })
        
        log("Wecima.sarl: extracted {} cards".format(len(cards)))
        return cards

    def _extract_next_page_sarl(self, html, current_url):
        """[PATCH 77] only a real next link, or the numbered link for page+1."""
        if not html:
            return ""
        m = re.search(r'/page/(\d+)', current_url or "")
        nxt = (int(m.group(1)) if m else 1) + 1
        pag = re.search(r'<div[^>]*class="[^"]*pagination[^"]*"[^>]*>(.*?)</div>', html, re.S | re.I)
        scopes = [pag.group(1), html] if pag else [html]

        for scope in scopes:
            for pat in (
                r'<a[^>]+class="[^"]*\bnext\b[^"]*"[^>]+href="([^"]+)"',
                r'<a[^>]+rel="next"[^>]+href="([^"]+)"',
                r'<a[^>]+href="([^"]+)"[^>]+rel="next"',
            ):
                mm = re.search(pat, scope, re.I | re.S)
                if mm:
                    url = self._normalize_url(mm.group(1))
                    if url and url != current_url:
                        return url

        scope = scopes[0]
        for href, num in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>\s*(\d+)\s*</a>', scope, re.I | re.S):
            if int(num) == nxt:
                url = self._normalize_url(href)
                if url and url != current_url:
                    return url
        return ""

    def _decode_wecima_url(self, encoded):
        """
        Decode base64 encoded URLs from wecima.sarl.
        Based on snapshot: ?my_player=JMrLYcoO or ?mycimafsd=...

        FIX (parity with wecima.py): adds percent-encoding normalization of
        the decoded URL and the 'https'/'http' prefix-without-scheme repair.
        Both are required for the download-section data-href values, which
        plugin_downloads.resolve_download_link() must receive as clean
        absolute URLs.
        """
        if not encoded:
            return None
        
        if encoded.startswith('http://') or encoded.startswith('https://'):
            return encoded
            
        log("Wecima.sarl: decoding: {}".format(repr(encoded[:80])))

        # Try different base64 decoding approaches
        cleaned = encoded.strip().replace('+', '').replace(' ', '')
        
        # Some URLs have a prefix like "aHR0c" that needs to be handled
        try:
            fixed = 'aHR0c' + cleaned
            fixed = re.sub(r'[^A-Za-z0-9+/=]', '', fixed)
            missing_padding = len(fixed) % 4
            if missing_padding:
                fixed += '=' * (4 - missing_padding)
            decoded_bytes = base64.b64decode(fixed)
            decoded_url = decoded_bytes.decode('utf-8', errors='replace')
            decoded_url = decoded_url.replace('\\u0026', '&').replace('\\/', '/')
            if decoded_url.startswith('http://') or decoded_url.startswith('https://'):
                log("Wecima.sarl: decode success (prefix scheme): {}".format(decoded_url[:80]))
                return decoded_url
        except Exception as e:
            log("Wecima.sarl: prefix-scheme decode failed: {}".format(str(e)[:50]))

        # Try plain base64
        try:
            cleaned = re.sub(r'[^A-Za-z0-9+/=]', '', cleaned)
            missing_padding = len(cleaned) % 4
            if missing_padding:
                cleaned += '=' * (4 - missing_padding)
            decoded_bytes = base64.b64decode(cleaned)
            
            for encoding in ('ascii', 'utf-8', 'latin-1'):
                try:
                    decoded_url = decoded_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                decoded_url = decoded_bytes.decode('ascii', errors='replace')
                
            decoded_url = decoded_url.replace('\\u0026', '&').replace('\\/', '/')
            
            # FIX: normalize unicode/special chars and repair scheme prefixes
            # like "httpscdn..." -> "https://cdn..." (same as wecima.py).
            decoded_url = quote(decoded_url, safe=':/?&=#+')
            
            if decoded_url.startswith('//'):
                decoded_url = 'https:' + decoded_url
            elif decoded_url.startswith('https') and not decoded_url.startswith('https://'):
                decoded_url = 'https://' + decoded_url[5:]
            elif decoded_url.startswith('http') and not decoded_url.startswith('http://'):
                decoded_url = 'http://' + decoded_url[4:]
                
            if decoded_url and ('http://' in decoded_url or 'https://' in decoded_url):
                log("Wecima.sarl: decode success (plain b64): {}".format(decoded_url[:80]))
                return decoded_url
        except Exception as e:
            log("Wecima.sarl: plain-b64 decode failed: {}".format(str(e)[:50]))

        # Try URL pattern extraction
        url_pattern = r'[a-zA-Z0-9\-]+\.(?:com|net|org|tv|cx|bid|site|click|show|video|rent|date|live|rip|top|xyz|ps|shop)(?:/[a-zA-Z0-9\-_/]+)?'
        match = re.search(url_pattern, encoded)
        if match:
            url = "https://" + match.group(0)
            log("Wecima.sarl: extracted URL pattern: {}".format(url))
            return url

        log("Wecima.sarl: decode failed entirely for: {}".format(repr(encoded[:80])))
        return None

    def _extract_servers(self, html):
        """
        Extract STREAMING server URLs from the wecima.sarl detail page.

        FIX: the old version also tried to parse 'Download--Wecima--Single'
        here using a fabricated '<resolution></resolution>' tag regex that
        could never match real markup (dead code - zero download links ever
        surfaced). Download links are now extracted separately by
        _extract_download_links() - mirroring wecima.py - so this method
        returns watch servers ONLY.
        """
        servers = []
        seen = set()
        if not html:
            log("Wecima.sarl: empty HTML in _extract_servers")
            return []

        # Primary: watch list - <ul id="watch"> with
        # <li data-watch="<obfuscated b64>">...</li>
        watch_pattern = r'<ul[^>]*id="[^"]*watch[^"]*"[^>]*>(.*?)</ul>'
        match = re.search(watch_pattern, html, re.S | re.I)
        
        if match:
            watch_content = match.group(1)
            items = re.findall(r'<li[^>]*data-watch="([^"]+)"[^>]*>(.*?)</li>', watch_content, re.S | re.I)
            for encoded_url, inner_html in items:
                decoded_url = self._decode_wecima_url(encoded_url)
                if not decoded_url or not decoded_url.startswith("http"):
                    continue
                if decoded_url in seen:
                    continue
                
                # Extract server name
                name_match = re.search(r'<i[^>]*></i>\s*(.*?)(?:<noscript|$)', inner_html, re.S | re.I)
                server_name = name_match.group(1).strip() if name_match else "Watch Server"
                server_name = re.sub(r'<[^>]+>', '', server_name).strip()
                if not server_name or server_name.startswith('<'):
                    server_name = "مشاهدة مباشرة"
                
                seen.add(decoded_url)
                servers.append({
                    "name": server_name,
                    "url": decoded_url,
                    "type": "direct"
                })
                log("Wecima.sarl: Found server '{}' -> {}".format(server_name, decoded_url[:60]))

        # Fallback deep scan (mirrors wecima.py): look for data-url /
        # data-link embed attributes anywhere on the page. NOTE: data-href
        # is deliberately NOT scanned here - it belongs to the download
        # section and is handled by _extract_download_links().
        if not servers:
            log("Wecima.sarl: watch list not found, running deep scan fallback...")
            skip_markers = self._NON_MEDIA_HOSTS + (
                "wecima.", "akhbarworld.online", "pinecrestdesignstudio.shop",
            ) + self.BLOCKED_HOST_MARKERS
            fallback_items = re.findall(r'(?:data-url|data-link)="([^"]+)"', html, re.I)
            for encoded_url in fallback_items:
                decoded_url = self._decode_wecima_url(encoded_url)
                if not (decoded_url and decoded_url.startswith("http")):
                    continue
                if decoded_url in seen:
                    continue
                if any(x in decoded_url.lower() for x in skip_markers):
                    continue
                seen.add(decoded_url)
                servers.append({
                    "name": "Server Fallback",
                    "url": decoded_url,
                    "type": "direct"
                })
                log("Wecima.sarl: Found fallback server -> {}".format(decoded_url[:60]))

        if not servers:
            log("Wecima.sarl: No servers found in HTML")
        else:
            log("Wecima.sarl: Successfully extracted {} servers".format(len(servers)))
        return servers

    def _extract_download_links(self, html):
        """
        Parse the download section ('Download--Wecima--Single' /
        'List--Download' / custom section wrapper) into a list of
        {"resolution", "size", "quality", "url"} dicts.

        This mirrors wecima.py's _extract_download_links so the shared
        plugin_downloads.resolve_download_link() -> download_manager()
        flow works unchanged for both extractors.

        Confirmed wecima-family markup (wecima.cx):
          <div class="Download--Wecima--Single">
            <ul class="List--Download--Wecima--Single">
              <li class="download-item openLinkDown" data-href="<obfuscated>">
                <a class="download-card" ...>
                  <div class="info">
                    <span class="resolution">Full HD 1080p</span>
                    <div class="sub">
                      <span class="size">1.33 GB</span>
                      <span class="quality">WEB-DL</span>
                    </div>
                  </div>
                </a>
              </li>
              ...
            </ul>
          </div>
        wecima.sarl uses custom <singlesection> wrappers elsewhere on the
        page, so several block shapes are accepted. data-href values use
        the exact same obfuscated-base64 scheme as the watch list's
        data-watch, so _decode_wecima_url handles them as-is.
        """
        downloads = []
        seen = set()
        if not html:
            return downloads

        block = None
        for pat in (
            # wecima.cx-style dedicated download section (up to </ul>)
            r'class=["\'][^"\']*Download--Wecima--Single[^"\']*["\'][^>]*>(.*?)</ul>',
            # Generic download list
            r'<ul[^>]*class=["\'][^"\']*List--Download[^"\']*["\'][^>]*>(.*?)</ul>',
            # wecima.sarl custom singlesection/section/div wrapper
            r'<(?:singlesection|section|div)[^>]*class=["\'][^"\']*Download[^"\']*["\'][^>]*>(.*?)</(?:singlesection|section|div)>',
        ):
            m = re.search(pat, html, re.S | re.I)
            if m:
                block = m.group(1)
                break

        if block is not None:
            # Structured section: accept data-href / data-url / data-link /
            # plain href items.
            item_iter = re.finditer(
                r'<(?:li|a)[^>]*?(?:data-href|data-url|data-link|href)=["\']([^"\']+)["\'][^>]*>(.*?)</(?:li|a)>',
                block, re.S | re.I
            )
        else:
            # No recognizable section: deep-scan the whole page for
            # data-href/data-url attributes ONLY (plain href is far too
            # noisy page-wide, and data-watch belongs to the watch list).
            log("Wecima.sarl: no download section found, deep-scanning data-href items")
            item_iter = re.finditer(
                r'<(?:li|a|div)[^>]*?(?:data-href|data-url)=["\']([^"\']+)["\'][^>]*>(.*?)</(?:li|a|div)>',
                html, re.S | re.I
            )

        for item_m in item_iter:
            encoded_url, inner = item_m.groups()
            url = self._decode_wecima_url(encoded_url)
            if not url or not url.startswith("http") or url in seen:
                continue

            host = self._host(url)
            # Skip social / blocked / on-site navigation links - real
            # download entries always point at an external file host
            # (savefiles.com, dood, mixdrop, luluvdo, ...).
            if any(x in host for x in self._NON_MEDIA_HOSTS):
                continue
            if any(m in host for m in self.BLOCKED_HOST_MARKERS):
                continue
            if any(m in host for m in self.VALID_HOST_MARKERS):
                continue

            seen.add(url)

            resolution_m = re.search(r'class=["\'][^"\']*resolution[^"\']*["\'][^>]*>\s*([^<]+?)\s*<', inner, re.S | re.I)
            size_m = re.search(r'class=["\'][^"\']*size[^"\']*["\'][^>]*>\s*([^<]+?)\s*<', inner, re.S | re.I)
            quality_m = re.search(r'class=["\'][^"\']*quality[^"\']*["\'][^>]*>\s*([^<]+?)\s*<', inner, re.S | re.I)

            resolution = resolution_m.group(1).strip() if resolution_m else ""
            size = size_m.group(1).strip() if size_m else ""
            quality = quality_m.group(1).strip() if quality_m else ""

            # Fallback labels when the card has no structured spans
            if not resolution:
                m = re.search(r'\b(2160p|1440p|1080p|720p|480p|360p)\b', inner, re.I)
                if m:
                    resolution = m.group(1)
            if not size:
                m = re.search(r'\b(\d+(?:[.,]\d+)?\s*(?:MB|GB|TB))\b', inner, re.I)
                if m:
                    size = m.group(1)

            downloads.append({
                "resolution": resolution,
                "size": size,
                "quality": quality,
                "url": url,
            })

        log("Wecima.sarl: extracted {} download link(s)".format(len(downloads)))
        return downloads

    def _extract_episodes_from_list(self, html):
        """Extract episodes from wecima.sarl detail page."""
        episodes = []
        seen = set()
        
        if not html:
            return episodes
        
        # Look for episode links in the series section
        # Based on snapshot: singlesection class="Series--Section"
        series_section = re.search(r'<singlesection[^>]*class="[^"]*Series--Section[^"]*"[^>]*>(.*?)</singlesection>', html, re.S | re.I)
        if series_section:
            content = series_section.group(1)
            # Look for episode links
            episode_links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>.*?حلقة\s*(\d+).*?</a>', content, re.S | re.I)
            for url, num in episode_links:
                normalized_url = self._normalize_url(url)
                if normalized_url in seen:
                    continue
                seen.add(normalized_url)
                title = "حلقة {}".format(num)
                episodes.append({
                    "title": title,
                    "url": normalized_url,
                    "type": "episode",
                    "_action": "details",
                })
        
        if not episodes:
            # Fallback: look for any link with "حلقة" in the text
            links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html or "", re.S | re.I)
            for url, text in links:
                if "حلقة" in text:
                    normalized_url = self._normalize_url(url)
                    if normalized_url in seen:
                        continue
                    if any(x in normalized_url.lower() for x in ('facebook', 'twitter', 'whatsapp')):
                        continue
                    seen.add(normalized_url)
                    title = self._clean_title(text)
                    # Extract episode number
                    ep_match = re.search(r'(\d+)', title)
                    if ep_match and not title.startswith("حلقة"):
                        title = "حلقة {}".format(ep_match.group(1))
                    episodes.append({
                        "title": title,
                        "url": normalized_url,
                        "type": "episode",
                        "_action": "details",
                    })
        
        log("Wecima.sarl: extracted {} episodes".format(len(episodes)))
        return episodes

    def _parse_json_ld(self, html):
        json_ld_match = re.search(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html or "", re.S | re.I)
        if not json_ld_match:
            return None
        try:
            data = json.loads(json_ld_match.group(1))
            return data
        except Exception:
            return None

    def _detail_title(self, html):
        data = self._parse_json_ld(html)
        if data and isinstance(data, dict):
            graph = data.get("@graph") or ([data] if data.get("@type") else [])
            preferred_types = ("Movie", "TVSeries", "TVSeason", "TVEpisode", "VideoObject")
            for item in graph:
                if item.get("@type") in preferred_types and item.get("name"):
                    return self._clean_title(item["name"])
        patterns = [
            r'<h1[^>]*>(.*?)</h1>',
            r'<title[^>]*>(.*?)</title>',
            r'property="og:title"[^>]+content="([^"]+)"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html or "", re.S | re.I)
            if m:
                title = self._clean_title(m.group(1))
                if title:
                    return title
        return ""

    def _detail_plot(self, html):
        patterns = [
            r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
            r'property="og:description"[^>]+content="([^"]+)"',
            r'<div[^>]+class="[^"]*StoryMovieContent[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]+class="[^"]*story[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]+class="[^"]*description[^"]*"[^>]*>(.*?)</div>',
        ]
        for pattern in patterns:
            m = re.search(pattern, html or "", re.S | re.I)
            if m:
                text = self._clean_html(m.group(1))
                if text and "موقع وي سيما" not in text.lower() and len(text) > 20:
                    return text
        return ""

    def _detail_poster(self, html):
        patterns = [
            r'property="og:image"[^>]+content="([^"]+)"',
            r'<meta[^>]+itemprop="thumbnailUrl"[^>]+content="([^"]+)"',
            r'<div[^>]+class="[^"]*Poster--Single-begin[^"]*"[^>]*>.*?<a[^>]+class="[^"]*Img--Poster--Single-begin[^"]*"[^>]*style="[^"]*background-image:\s*url\(([^)]+)\)',
            r'<span[^>]+class="[^"]*BG--GridItem[^"]*"[^>]+data-lazy-style="[^"]*--image:\s*url\(([^)]+)\)',
            r'<img[^>]+src="([^"]+)"[^>]*>/',
            r'data-src="([^"]+)"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html or "", re.I)
            if m:
                poster = m.group(1).strip("'\" ")
                if poster:
                    return self._normalize_url(poster) or poster
        return ""

    def _detail_year(self, title, html):
        # Check title first
        m = re.search(r'\b(19\d{2}|20\d{2})\b', title or "")
        if m:
            return m.group(1)
        # Check HTML for year
        m = re.search(r'<span[^>]*class="[^"]*year[^"]*"[^>]*>\(?\s*(\d{4})\s*\)?</span>', html or "", re.I)
        if m:
            return m.group(1)
        # Check JSON-LD
        data = self._parse_json_ld(html)
        if data and isinstance(data, dict):
            graph = data.get("@graph") or ([data] if data.get("@type") else [])
            for item in graph:
                if item.get("datePublished"):
                    m = re.search(r'(\d{4})', item["datePublished"])
                    if m:
                        return m.group(1)
        return ""

    def _detail_rating(self, html):
        m = re.search(r'(\d+(?:\.\d+)?)\s*/\s*10', html or "", re.I)
        if m:
            return m.group(1)
        m = re.search(r'"ratingValue"\s*:\s*"?(\d+(?:\.\d+)?)', html or "", re.I)
        if m:
            return m.group(1).replace("\\", "")
        return ""

    def get_categories(self, mtype="movie"):
        """
        Get categories from wecima.sarl.
        Based on the HTML snapshot navigation menu.
        """
        categories = [
            # Movies
            {
                "title": "أفلام أجنبية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["افلام اجنبي"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "أفلام عربية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["افلام عربي"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "أفلام انمي",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["افلام انمي"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "أفلام اسيوية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["افلام اسيوي"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "أفلام تركية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["افلام تركية"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "أفلام هندية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["افلام هندي"]),
                "type": "category",
                "_action": "category"
            },
            # TV Series
            {
                "title": "مسلسلات أجنبية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات اجنبي"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات عربية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات عربية"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات انمي",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات انمي"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات تركية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات تركية"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات هندية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات هندية"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات اسيوية",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات اسيوية"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات مدبلجة",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات مدبلجة"]),
                "type": "category",
                "_action": "category"
            },
            # Ramadan Series
            {
                "title": "مسلسلات رمضان 2026",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات رمضان 2026"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات رمضان 2025",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات رمضان 2025"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات رمضان 2024",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات رمضان 2024"]),
                "type": "category",
                "_action": "category"
            },
            # Quick links from homepage tabs
            {
                "title": "جديد وى سيما",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["جديد"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "افلام جديدة",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["افلام جديدة"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "آخر الحلقات",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["آخر الحلقات"]),
                "type": "category",
                "_action": "category"
            },
            {
                "title": "مسلسلات جديدة",
                "url": self._normalize_url(self.CATEGORY_FALLBACKS["مسلسلات جديدة"]),
                "type": "category",
                "_action": "category"
            },
        ]
        return categories

    def get_category_items(self, url, page=1):
        base = self._get_base()
        # Add page parameter if needed
        if page > 1 and '/page/' not in url:
            url = url.rstrip('/') + '/page/{}/'.format(page)
        html, final_url = self._fetch_live(url, referer=base)
        if self._is_blocked_page(html, final_url):
            log("Wecima.sarl: category blocked {}".format(url))
            return []
        items = self._extract_cards_sarl(html)
        next_page = self._extract_next_page_sarl(html, final_url or url)
        if next_page:
            items.append({
                "title": "➡️ الصفحة التالية",
                "url": next_page,
                "type": "category",
                "_action": "category"
            })
        return items

    def search(self, query, page=1):
        base = self._get_base()
        items = []
        html = ""
        final_url = ""
        for search_url in [
            base + "?s=" + quote_plus(query),
            base + "search/" + quote_plus(query),
        ]:
            html, final_url = self._fetch_live(search_url, referer=base)
            if self._is_blocked_page(html, final_url):
                continue
            items = self._extract_cards_sarl(html)
            if items:
                break
        log("Wecima.sarl: search '{}' -> {} items".format(query, len(items)))
        if not items:
            return []
        next_page = self._extract_next_page_sarl(html, final_url)
        if next_page:
            items.append({
                "title": "➡️ الصفحة التالية",
                "url": next_page,
                "type": "category",
                "_action": "category"
            })
        return items

    def get_page(self, url, m_type=None):
        base = self._get_base()
        html, final_url = self._fetch_live(url, referer=base)
        if self._is_blocked_page(html, final_url) or not html:
            log("Wecima.sarl: detail failed {}".format(url))
            return {"title": "Error", "servers": [], "items": [], "downloads": [], "type": m_type or "movie"}
        
        title = self._detail_title(html)
        poster = self._detail_poster(html)
        plot = self._detail_plot(html)
        year = self._detail_year(title, html)
        rating = self._detail_rating(html)
        
        # Extract servers and episodes
        servers = self._extract_servers(html)
        
        # NEW: parse the site's own download section (separate from the
        # watch list) - same entry shape as wecima.py so the shared
        # plugin_downloads.resolve_download_link() flow works unchanged.
        downloads = self._extract_download_links(html)
        
        episodes = []
        # If no servers, look for episodes
        if not servers:
            episodes = self._extract_episodes_from_list(html)
        
        log("Wecima.sarl: detail {} -> servers={}, episodes={}, downloads={}".format(
            url, len(servers), len(episodes), len(downloads)))
        
        # Determine type
        item_type = m_type or self._guess_type(title, final_url or url)
        if episodes:
            item_type = "series"
        elif servers and any(t in (title or "").lower() for t in ("حلقة", "episode")):
            item_type = "episode"
        
        return {
            "url": final_url or url,
            "title": title,
            "plot": plot,
            "poster": poster,
            "rating": rating,
            "year": year,
            "servers": servers,
            "items": episodes,
            "downloads": downloads,
            "type": item_type,
        }

    def extract_stream(self, url):
        from .base import extract_stream as base_extract_stream
        # Add referer if missing
        if "|" not in url and url.startswith("http"):
            url += "|Referer=https://wecima.sarl/"
        return base_extract_stream(url)