# -*- coding: utf-8 -*-
"""
Extractor for Akwams - akwams.org
Includes Recent category (latest added content)
Now supports multi-quality: shows all available qualities for each server.
Inherits from BaseExtractor.
FIX: Updated domain from akwams.com.co to akwams.org with multi-domain probing.
FIX: Updated category URLs to match actual site navigation structure.
FIX: Rewrote card extraction to use entry-box blocks for perfect title/poster matching.
FIX: Fixed watch page URL detection by explicitly looking for /watch/ links.
FIX: Pass embed URLs directly to UI to prevent background resolution timeouts.
FIX: Added medixiru, audinifer, morencius to streaming hosts based on JSON log.
FIX: Added proper pagination support for /page/N/ format.
FIX: Added series/episode detection and extraction.
FIX: Removed akwams.info domain (no longer functional).
"""

import re
import sys
from .base import BaseExtractor, fetch, log, urljoin

if sys.version_info[0] == 3:
    from urllib.parse import quote_plus, unquote
    from html import unescape as html_unescape
else:
    from urllib import quote_plus, unquote
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape


class AkwamsExtractor(BaseExtractor):
    """Extractor for Akwams - akwams.org"""
    
    MAIN_URL = "https://akwams.org/"
    
    DOMAINS = [
        "https://akwams.org/",
        "https://akwams.com.co/",
        "https://akwams.co/",
        "https://akwams.net/",
    ]
    
    # Streaming host domains that appear on akwams watch pages
    STREAMING_HOSTS = (
        "hgplaycdn", "hglamioz", "hanerix", "streamwish", "filemoon",
        "lulustream", "mixdrop", "dood", "streamtape", "vidguard",
        "fastvid", "hgcloud", "cloudwindow-route", "gentlebrookmediagroup",
        "cloudatacdn", "bysekoze", "minochinos", "playmogo", "forafile",
        "smoothpre", "vidbom", "upstream", "uqload", "voe", "downet",
        "cybervynx", "stmruby", "medixiru", "audinifer", "morencius", "vidaraa"
    )
    
    def __init__(self):
        super(AkwamsExtractor, self).__init__()
        self.main_url = self.MAIN_URL
        self._resolved_base = None
    
    def _get_base(self):
        """Probe known domains and return the first working one."""
        if self._resolved_base:
            return self._resolved_base
        
        for domain in self.DOMAINS:
            try:
                log("Akwams: probing domain {}".format(domain))
                html, final_url = fetch(domain, referer=domain)
                if not html:
                    continue
                
                lower_html = html.lower()
                if "just a moment" in lower_html or "cf-chl" in lower_html:
                    log("Akwams: domain {} blocked by Cloudflare".format(domain))
                    continue
                
                final_url = final_url or domain
                if "akwam" in final_url.lower() or "أكوام" in html:
                    base = final_url if final_url.endswith("/") else final_url + "/"
                    self._resolved_base = base
                    self.main_url = base
                    log("Akwams: selected base: {}".format(base))
                    return base
            except Exception as e:
                log("Akwams: domain {} failed: {}".format(domain, e))
                continue
        
        self._resolved_base = self.MAIN_URL
        log("Akwams: all probes failed, falling back to {}".format(self.MAIN_URL))
        return self._resolved_base
    
    def _clean_title(self, title):
        if not title:
            return ""
        title = html_unescape(title)
        title = title.replace("&amp;", "&")
        for word in ("مشاهدة", "تحميل", "فيلم", "مسلسل", "مترجم", "مترجمة",
                     "مدبلج", "مدبلجة", "اون لاين", "أون لاين", "مباشرة",
                     "بجودة", "عالية", "كامل", "حصريا"):
            title = title.replace(word, "")
        title = re.sub(r'\s*[-|]\s*أكوام.*$', '', title)
        title = re.sub(r'\s*[-|]\s*Akwam.*$', '', title, flags=re.I)
        title = re.sub(r'\s+', ' ', title).strip(" -|")
        return title
    
    def _normalize_url(self, url):
        if not url:
            return ""
        url = str(url).strip().replace("&amp;", "&")
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return urljoin(self._get_base(), url)
        if not url.startswith("http") and "://" not in url:
            return urljoin(self._get_base(), url)
        return url
    
    def _determine_item_type(self, url, title):
        url_lower = url.lower()
        title_lower = (title or "").lower()
        if "الحلقة" in title or "حلقة" in title:
            return "episode"
        if "مسلسل" in title or "/series" in url_lower or "مسلسلات" in title:
            return "series"
        return "movie"
    
    def _extract_items_from_html(self, html):
        """Extract movie/series items accurately by finding entry-box blocks."""
        if not html:
            return []
        
        items = []
        seen = set()
        
        # Split by entry-box to isolate each movie card
        blocks = re.split(r'<div class="entry-box entry-box-1">', html)
        
        for block in blocks[1:]:
            # Find link
            link_m = re.search(r'<a[^>]+href="([^"]+)"[^>]*class="box"', block, re.I)
            if not link_m:
                continue
            
            url = self._normalize_url(link_m.group(1))
            if not url or url in seen:
                continue
            if any(x in url.lower() for x in ('/category/', '/page/', '/tag/', '/feed/', '#')):
                continue
            if url.rstrip('/') == self._get_base().rstrip('/'):
                continue
            
            # Find poster
            poster = ""
            img_m = re.search(r'<img[^>]+data-src="([^"]+)"', block, re.I)
            if img_m:
                img_url = img_m.group(1)
                if not any(x in img_url.lower() for x in ('placeholder', 'loading.gif', 'lazy_load', 'data:image', 'logo')):
                    poster = self._normalize_url(img_url)
            
            # Find title
            title = ""
            title_m = re.search(r'<h3[^>]*>\s*<a[^>]+class="text-white"[^>]*>(.*?)</a>', block, re.S | re.I)
            if title_m:
                title = self._clean_title(re.sub(r'<[^>]+>', '', title_m.group(1)).strip())
            
            # Fallback to alt text
            if not title or len(title) < 2:
                alt_m = re.search(r'<img[^>]+alt="([^"]+)"', block, re.I)
                if alt_m:
                    title = self._clean_title(alt_m.group(1))
            
            # Fallback to slug
            if not title or len(title) < 2:
                slug = url.rstrip('/').split('/')[-1]
                try:
                    slug = unquote(slug)
                except:
                    pass
                title = self._clean_title(slug.replace('-', ' ').strip())
            
            if not title or len(title) < 2:
                continue
            
            year_m = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            year = year_m.group(1) if year_m else ""
            
            item_type = self._determine_item_type(url, title)
            
            seen.add(url)
            items.append({
                "title": title,
                "url": url,
                "poster": poster,
                "year": year,
                "type": item_type,
                "_action": "details",
            })
            
        return items
    
    def get_categories(self, mtype="movie"):
        """Return all categories from Akwams navigation menu."""
        base = self._get_base()
        return [
            {"title": "🆕 أضيف حديثا",          "url": urljoin(base, "recent/"),              "type": "category", "_action": "category"},
            {"title": "🎬 جميع الأفلام",         "url": urljoin(base, "movies/"),              "type": "category", "_action": "category"},
            {"title": "🎬 أفلام أجنبي",          "url": urljoin(base, "category/movies/افلام-اجنبي/"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام عربي",           "url": urljoin(base, "category/movies/افلام-عربي/"),  "type": "category", "_action": "category"},
            {"title": "🎬 أفلام آسيوية",         "url": urljoin(base, "category/movies/افلام-اسيوية/"), "type": "category", "_action": "category"},
            {"title": "🎬 أفلام هندية",          "url": urljoin(base, "category/movies/افلام-هندية/"),  "type": "category", "_action": "category"},
            {"title": "🎬 أفلام تركية",          "url": urljoin(base, "category/movies/افلام-تركية/"),  "type": "category", "_action": "category"},
            {"title": "🎬 أفلام انمي",           "url": urljoin(base, "category/movies/افلام-انمي/"),   "type": "category", "_action": "category"},
            {"title": "🎬 أفلام كرتون",          "url": urljoin(base, "category/movies/افلام-كرتون/"),  "type": "category", "_action": "category"},
            {"title": "📺 جميع المسلسلات",       "url": urljoin(base, "series/"),              "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات أجنبي",        "url": urljoin(base, "category/series/مسلسلات-اجنبي/"),   "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات تركية",        "url": urljoin(base, "category/series/مسلسلات-تركية/"),   "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات آسيوية",       "url": urljoin(base, "category/series/مسلسلات-اسيوية/"),  "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات انمي",         "url": urljoin(base, "category/series/مسلسلات-انمي/"),    "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات كرتون",        "url": urljoin(base, "category/series/مسلسلات-كرتون/"),   "type": "category", "_action": "category"},
            {"title": "📡 أحدث الحلقات",         "url": urljoin(base, "tv/"),                   "type": "category", "_action": "category"},
            {"title": "📡 برامج التلفزيون",      "url": urljoin(base, "category/tv/"),          "type": "category", "_action": "category"},
            {"title": "🎵 أغانى وكليبات",        "url": urljoin(base, "category/music/"),       "type": "category", "_action": "category"},
            {"title": "🤼 مصارعة",               "url": urljoin(base, "category/wrestling/"),   "type": "category", "_action": "category"},
        ]
    
    def get_category_items(self, url, page=1):
        url = self._normalize_url(url)
        
        current_page = 1
        page_match = re.search(r'/page/(\d+)/?', url)
        if page_match:
            current_page = int(page_match.group(1))
            url = re.sub(r'/page/\d+/?', '/', url)
        
        if page > 1 or current_page > 1:
            fetch_page = page if page > 1 else current_page
            if url.endswith('/'):
                fetch_url = url + 'page/{}/'.format(fetch_page)
            else:
                fetch_url = url + '/page/{}/'.format(fetch_page)
            current_page = fetch_page
        else:
            fetch_url = url
        
        log("Akwams: Fetching category URL: {} (page {})".format(fetch_url, current_page))
        
        html, final_url = fetch(fetch_url, referer=self._get_base())
        if not html:
            log("Akwams: get_category_items failed for {}".format(fetch_url))
            return []
        
        items = []
        seen = set()
        
        items.append({
            "title": "━━━ Page {} ━━━".format(current_page),
            "type": "separator",
            "_action": "separator",
        })
        
        extracted = self._extract_items_from_html(html)
        for item in extracted:
            if item["url"] not in seen:
                seen.add(item["url"])
                items.append(item)
        
        # ── Pagination ──
        next_url = None
        next_patterns = [
            r'<a[^>]+class="[^"]*page-link[^"]*"[^>]+href="([^"]+)"[^>]*>\s*التالي\s*»?\s*</a>',
            r'<a[^>]+class="[^"]*next[^"]*"[^>]+href="([^"]+)"',
            r'<a[^>]+rel="next"[^>]+href="([^"]+)"',
            r'<link[^>]+rel="next"[^>]+href="([^"]+)"',
            r'<a[^>]+href="([^"]+)"[^>]*>\s*التالي\s*</a>',
            r'<a[^>]+href="([^"]+)"[^>]*>\s*»\s*</a>',
            r'<a[^>]+href="([^"]+)"[^>]*>\s*Next\s*</a>',
            r'<a[^>]+href="([^"]+)"[^>]*>\s*المزيد\s*</a>',
        ]
        
        for pattern in next_patterns:
            m = re.search(pattern, html, re.I | re.S)
            if m:
                next_url = self._normalize_url(m.group(1))
                break
        
        if not next_url:
            next_page_num = current_page + 1
            next_match = re.search(r'<a[^>]+href="([^"]+/page/{}/?)"'.format(next_page_num), html, re.I)
            if next_match:
                next_url = self._normalize_url(next_match.group(1))
        
        if not next_url:
            next_page_num = current_page + 1
            if url.endswith('/'):
                candidate = url + 'page/{}/'.format(next_page_num)
            else:
                candidate = url + '/page/{}/'.format(next_page_num)
            if 'page/{}'.format(next_page_num) in html:
                next_url = candidate
        
        if next_url and next_url != fetch_url:
            items.append({
                "title": "➡️ Page {} (Next)".format(current_page + 1),
                "url": next_url,
                "type": "category",
                "_action": "category",
            })
        
        log("Akwams: category {} -> {} items (page {})".format(url, len(items), current_page))
        return items
    
    def search(self, query, page=1):
        base = self._get_base()
        search_urls = [
            urljoin(base, "?s=" + quote_plus(query)),
            urljoin(base, "search?q=" + quote_plus(query)),
        ]
        if page > 1:
            search_urls = [
                urljoin(base, "page/{}/?s={}".format(page, quote_plus(query))),
                urljoin(base, "search?q={}&page={}".format(quote_plus(query), page)),
            ]
        
        for search_url in search_urls:
            log("Akwams: Searching: {}".format(search_url))
            html, _ = fetch(search_url, referer=base)
            if not html:
                continue
            
            items = self._extract_items_from_html(html)
            if items:
                log("Akwams: Search found {} results".format(len(items)))
                return items
        
        log("Akwams: Search found 0 results")
        return []
    
    def _extract_servers_from_html(self, html, page_url):
        """Extract stream servers from HTML, passing embeds directly to avoid UI timeouts."""
        servers = []
        seen = set()
        IMG_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico')
        SKIP_DOMAINS = ('facebook.com', 'twitter.com', 'google.com', 'youtube.com/embed',
                        'disqus.com', 'googletagmanager', 'doubleclick', 'analytics')
        
        base_host = re.search(r'https?://([^/]+)', self._get_base())
        base_host_str = base_host.group(1).lower() if base_host else "akwams.org"
        
        def _add_server(url, name_hint="", quality=""):
            url = url.strip().replace("&amp;", "&")
            if not url or url in seen:
                return
            if url.lower().startswith("data:"):  # [PATCH AK1] inline data URI, never a stream
                return
            if any(ext in url.lower().split('?')[0] for ext in IMG_EXTS):
                return
            if any(x in url.lower() for x in SKIP_DOMAINS):
                return
            
            # Skip obvious internal site links that aren't shortlinks or watch/stream URLs
            if any(x in url.lower() for x in ('/category/', '/page/', '/tag/', '/feed/', '/author/', '/login', '/register')):
                return
            # Skip broken internal short links like /58 that don't resolve to streams
            if base_host_str in url.lower() and not any(x in url.lower() for x in ('/watch', '/embed', '/stream', '/player')):
                return

            seen.add(url)
            full_url = self._normalize_url(url)
            if not full_url:
                return
            
            # Determine server name from host
            host_name = ""
            host_m = re.search(r'https?://([^/]+)', full_url)
            if host_m:
                host = host_m.group(1).lower()
                if "hgplaycdn" in host: host_name = "HGPlay"
                elif "hglamioz" in host: host_name = "HGLamioz"
                elif "hanerix" in host: host_name = "Hanerix"
                elif "streamwish" in host: host_name = "StreamWish"
                elif "filemoon" in host: host_name = "FileMoon"
                elif "lulustream" in host: host_name = "LuluStream"
                elif "mixdrop" in host: host_name = "MixDrop"
                elif "dood" in host: host_name = "DoodStream"
                elif "streamtape" in host: host_name = "StreamTape"
                elif "vidguard" in host or "vgfplay" in host: host_name = "VidGuard"
                elif "fastvid" in host: host_name = "FastVid"
                elif "cloudwindow" in host: host_name = "Voe"
                elif "gentlebrookmediagroup" in host: host_name = "Gentlebrook"
                elif "cloudatacdn" in host: host_name = "CloudData"
                elif "hgcloud" in host: host_name = "HGCloud"
                elif "downet" in host: host_name = "Downet"
                elif "cybervynx" in host: host_name = "CyberVynx"
                elif "stmruby" in host or "streamruby" in host: host_name = "StreamRuby"
                elif "smoothpre" in host: host_name = "SmoothPre"
                elif "medixiru" in host: host_name = "Medixiru"
                elif "audinifer" in host: host_name = "Audinifer"
                elif "morencius" in host: host_name = "Morencius"
                elif "vidaraa" in host: host_name = "Vidaraa"
                elif "playmogo" in host: host_name = "PlayMogo"
                else: host_name = host_m.group(1)
            
            base_name = "🎬 {}".format(host_name) if host_name else "🎬 Server {}".format(len(servers) + 1)
            if name_hint:
                base_name = "🎬 {}".format(name_hint)
            
            # Pass embed URL directly to the UI to prevent background resolution timeouts
            servers.append({
                "name": base_name,
                "url": full_url,
                "type": "embed",
                "quality": quality
            })
        
        # 1. data-link / data-url / data-iframe / data-src attributes (used by akwams watch pages)
        # [PATCH AK1] "data-server" removed -- confirmed against a real capture it holds a
        # numeric server ID (e.g. data-server="58") for an unrelated JS/AJAX flow on the
        # movie detail page's watch/download buttons, not a URL. Picking it up produced a
        # bogus "https://akwams.org/58" entry that silently blocked the already-correct
        # /watch/ page fallback below (its `if not servers:` guard saw a non-empty list).
        for attr in ("data-link", "data-url", "data-iframe", "data-href", "data-embed"):
            for m in re.finditer(attr + r'=["\']([^"\']+)["\']', html, re.I):
                _add_server(m.group(1))
        
        # 2. iframe src attributes
        for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
            _add_server(m.group(1))
        
        # 3. <source> tags
        for m in re.finditer(r'<source[^>]+src="([^"]+)"', html, re.I):
            video_url = self._normalize_url(m.group(1))
            if video_url and video_url not in seen:
                seen.add(video_url)
                quality = "HD"
                lowered = video_url.lower()
                if "1080" in lowered: quality = "1080p"
                elif "720" in lowered: quality = "720p"
                elif "480" in lowered: quality = "480p"
                servers.append({
                    "name": "🎬 Direct - {}".format(quality),
                    "url": video_url,
                    "type": "direct"
                })
        
        # 4. Links to known streaming hosts
        host_re = re.compile(
            r'(https?://(?:www\.)?(?:' + '|'.join(self.STREAMING_HOSTS) + r')[^\s"\'<>]+)',
            re.IGNORECASE
        )
        for m in host_re.finditer(html):
            _add_server(m.group(1).replace('\\/', '/').replace('&amp;', '&'))
        
        # 5. JavaScript variables with stream URLs
        if not servers:
            for pat in (
                r'file\s*:\s*["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                r'source\s*:\s*["\']([^"\']+\.(?:mp4|m3u8|txt)[^"\']*)["\']',
                r'"url"\s*:\s*"([^"]+\.(?:mp4|m3u8|txt)[^"]*)"',
                r'data-video=["\']([^"\']+)["\']',
            ):
                for m in re.finditer(pat, html, re.I):
                    _add_server(m.group(1).replace('\\/', '/'))
        

        # 6. Quality-grouped download cards (akwams.org's real download
        # page structure, confirmed live: a <h3> quality/source label
        # (e.g. "سيرفرات تحميل WEB-DL") followed by a block of
        # <div class="download-card"><a href="...">...</div> entries. A site
        # with multiple real quality tiers has multiple such <h3>+cards
        # blocks in sequence -- matched here by position (nearest preceding
        # heading for each server link) rather than trying to match nested,
        # balanced HTML tags via regex, which is unreliable. Confirmed
        # directly: a real captured page for this site had exactly one such
        # block (a single WEB-DL tier, no size info published at all for
        # that title) -- this correctly produces one shared label for every
        # server in that case, and correctly distinguishes separate labels
        # for a title with genuinely multiple quality tiers.
        quality_positions = []
        for qm in re.finditer(r"<h3[^>]*>\s*([^<]+?)\s*</h3>", html, re.S):
            label = qm.group(1).strip()
            label = re.sub(r"^سيرفرات\s+تحميل\s+", "", label).strip() or label
            quality_positions.append((qm.start(), label))
        quality_positions.sort()

        if quality_positions:
            for cm in re.finditer(r"<div class=\"download-card\">.*?href=\"([^\"]+)\"", html, re.S):
                card_pos = cm.start()
                card_url = cm.group(1)
                card_quality = ""
                for qpos, qlabel in quality_positions:
                    if qpos < card_pos:
                        card_quality = qlabel
                    else:
                        break
                _add_server(card_url, quality=card_quality)

        return servers
    
    def _parse_download_servers(self, html):
        """
        Download-page quality-grouped server list -- a separate
        "downloads" structure from result["servers"]/the watch-page
        embeds, matching egydead.py's own result["downloads"] shape
        and field names (resolution/size/quality/url) for UI
        consistency, confirmed by reading that file directly. Reuses
        the same quality-heading-by-position matching already proven
        correct in _extract_servers_from_html's strategy 6. akwams.org
        publishes no file-size info at all (confirmed live against a
        real page), so "size" is always empty here, unlike egydead.py
        which has real size text to capture.
        """
        downloads = []
        if not html:
            return downloads

        quality_positions = []
        for qm in re.finditer(r"<h3[^>]*>\s*([^<]+?)\s*</h3>", html, re.S):
            label = qm.group(1).strip()
            label = re.sub(r"^سيرفرات\s+تحميل\s+", "", label).strip() or label
            quality_positions.append((qm.start(), label))
        quality_positions.sort()

        for cm in re.finditer(r"<div class=\"download-card\">.*?href=\"([^\"]+)\"", html, re.S):
            card_pos = cm.start()
            card_url = cm.group(1).strip().replace("&amp;", "&")
            if not card_url:
                continue
            card_quality = ""
            for qpos, qlabel in quality_positions:
                if qpos < card_pos:
                    card_quality = qlabel
                else:
                    break
            host_m = re.search(r"https?://(?:www\.)?([^/]+)", card_url)
            host_name = host_m.group(1) if host_m else "Server"
            downloads.append({
                "resolution": card_quality,
                "size": "",
                "quality": host_name,
                "url": card_url,
            })
        return downloads

    def get_page(self, url, m_type=None):
        if not url or url.startswith("javascript"):
            return {"title": "Error", "servers": [], "items": [], "type": "movie"}
        
        url = self._normalize_url(url)
        log("Akwams: Getting page: {}".format(url))
        
        html, final_url = fetch(url, referer=self._get_base())
        if not html:
            log("Akwams: get_page failed for {}".format(url))
            return {"title": "Error", "servers": [], "items": []}
        
        result = {
            "url": final_url or url,
            "title": "",
            "poster": "",
            "plot": "",
            "year": "",
            "rating": "",
            "servers": [],
            "items": [],
            "type": "movie",
        }
        
        # ── Extract metadata ──
        raw_title = ""                                       # [PATCH 72]
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S | re.I)
        if title_match:
            raw_title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
        else:
            og_title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.I)
            if og_title:
                raw_title = og_title.group(1)
        result["title"] = self._clean_title(raw_title)
        
        poster_match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html, re.I)
        if poster_match:
            result["poster"] = self._normalize_url(poster_match.group(1))
        
        plot_match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html, re.I)
        if plot_match:
            result["plot"] = self._clean_title(plot_match.group(1))
        
        year_m = re.search(r'\b(19\d{2}|20\d{2})\b', result["title"])
        if year_m:
            result["year"] = year_m.group(1)
        
        is_series = ("مسلسل" in raw_title or "/series" in url.lower())
        is_episode = "الحلقة" in raw_title or "حلقة" in raw_title
        
        if is_episode:
            result["type"] = "episode"
        elif is_series:
            result["type"] = "series"
        else:
            result["type"] = "movie"
        
        # ── Extract servers from the current page ──
        servers = self._extract_servers_from_html(html, url)
        
        # ── If no servers found, try fetching the /watch/ page explicitly ──
        if not servers:
            watch_url = None

            # Look explicitly for a /watch/ link in the HTML (matches #watchBtn href)
            watch_link_match = re.search(r'href="([^"]+/watch/?)"', html, re.I)
            if watch_link_match:
                watch_url = self._normalize_url(watch_link_match.group(1))
            elif not url.rstrip("/").endswith("/watch"):
                # Fallback: append /watch/ to the current URL
                watch_url = url.rstrip("/") + "/watch/"

            if watch_url and watch_url != url:
                log("Akwams: No servers on detail page, trying watch page: {}".format(watch_url))
                watch_html, _ = fetch(watch_url, referer=url)
                if watch_html:
                    servers = self._extract_servers_from_html(watch_html, watch_url)

        # [FIX] confirmed directly, from the real site own JavaScript source
        # (akwam.js / inline script on a real captured movie page): both the
        # watch and download buttons navigate to the SAME predictable pattern
        # -- the current page own URL with /watch or /download appended, behind
        # a Telegram-popup marketing gate, not an AJAX call at all. The /watch/
        # fallback above already existed and already works (confirmed: watch
        # streams already played fine) -- but there was no equivalent /download/
        # fallback anywhere, so a real, separate set of servers (confirmed via
        # real captures tonight to resolve to MixDrop/DoodStream, both already
        # supported by the shared hosts.py dispatch with zero new resolver code
        # needed) was never discovered at all. Merged with, not replacing, the
        # watch-page servers above, deduped by URL, since a real site may expose
        # genuinely different servers on each of its two pages.
        if not url.rstrip("/").endswith("/download"):
            download_url = url.rstrip("/") + "/download/"
            if download_url != url:
                log("Akwams: Also trying download page for additional servers: {}".format(download_url))
                download_html, _ = fetch(download_url, referer=url)
                if download_html:
                    download_servers = self._extract_servers_from_html(download_html, download_url)
                    seen_urls = set(s["url"] for s in servers)
                    for s in download_servers:
                        if s["url"] not in seen_urls:
                            seen_urls.add(s["url"])
                            servers.append(s)
                    result["downloads"] = self._parse_download_servers(download_html)

        result.setdefault("downloads", [])
        result["servers"] = servers

        # ── Extract episodes for series pages ──
        if is_series and not is_episode:
            episodes = []
            seen_eps = set()
            
            for pattern in [
                r'<a[^>]+href="([^"]*(?:مسلسل-|مشاهدة-مسلسل-)[^"]*الحلقة[^"]*)"[^>]*>(.*?)</a>',
                r'<a[^>]+href="([^"]*الحلقة[^"]*)"[^>]*>(.*?)</a>',
                r'<a[^>]+href="([^"]*(?:مسلسل-|مشاهدة-مسلسل-)[^"]*حلقة[^"]*)"[^>]*>(.*?)</a>',
            ]:
                for m in re.finditer(pattern, html, re.S | re.I):
                    ep_url = self._normalize_url(m.group(1))
                    if not ep_url or ep_url in seen_eps or ep_url == url:
                        continue
                    seen_eps.add(ep_url)
                    ep_text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                    if not ep_text:
                        ep_text = "حلقة"
                    episodes.append({
                        "title": ep_text,
                        "url": ep_url,
                        "type": "episode",
                        "_action": "details",
                    })
                if episodes:
                    break
            
            result["items"] = episodes
            log("Akwams: Found {} episodes for series".format(len(episodes)))
        
        log("Akwams: Found {} servers for {}".format(len(result["servers"]), result["title"]))
        return result
    
    def extract_stream(self, url):
        from .base import extract_stream as base_extract_stream
        return base_extract_stream(url)