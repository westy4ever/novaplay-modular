# -*- coding: utf-8 -*-
"""
Extractor for faselhd.rip
Inherits from BaseExtractor.
"""

import sys
import re
import json
from .base import BaseExtractor, fetch, log, urljoin

if sys.version_info[0] == 3:
    from urllib.parse import quote_plus
else:
    from urllib import quote_plus


class FaselhdRipExtractor(BaseExtractor):
    """Extractor for faselhd.rip"""
    
    BASE_URL = "https://faselhd.rip"
    GOVID_BASE = "https://govid.live"
    MAX_AJAX_SERVERS = 16
    NOISE_DOMAINS = {
        "unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com",
        "ajax.googleapis.com", "code.jquery.com", "stackpath.bootstrapcdn.com",
    }
    
    def __init__(self):
        super(FaselhdRipExtractor, self).__init__()
        self.main_url = self.BASE_URL
        self._resolved_base = self.BASE_URL
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            "Referer": self.BASE_URL,
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
    
    def _get_base(self):
        return self._resolved_base or self.BASE_URL
    
    def _normalize_url(self, url):
        if not url:
            return ""
        url = str(url).strip()
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http"):
            return urljoin(self._get_base(), url)
        return url
    
    def _clean_title(self, title):
        if not title:
            return ""
        title = title.replace("&amp;", "&")
        title = title.replace("فاصل إعلاني", "").replace("FaselHD", "")
        title = re.sub(r'\s*[-|]\s*فاصل\s*إعلاني.*$', '', title)
        title = re.sub(r'\s*[-|]\s*FaselHD.*$', '', title, flags=re.I)
        return title.strip()
    
    def _find_m3u8(self, text):
        if not text:
            return None
        m = re.search(r'(https?://[^\s"\'<>`\\]+\.m3u8(?:\?[^\s"\'<>`\\]*)?)', text, re.I)
        if m:
            return m.group(1).replace('\\/', '/').replace('&amp;', '&')
        m = re.search(r'(?:file|src|url|source|hls)\s*[=:]\s*["\']([^"\']+\.m3u8[^"\']*)["\']', text, re.I)
        if m:
            return m.group(1).replace('\\/', '/').replace('&amp;', '&')
        for m in re.finditer(r'["\']([0-9a-fA-F]{64,})["\']', text):
            try:
                decoded = bytes.fromhex(m.group(1)).decode('utf-8', errors='ignore')
                if '.m3u8' in decoded and decoded.startswith('http'):
                    return decoded.replace('\\/', '/').replace('&amp;', '&')
            except Exception:
                pass
        for m in re.finditer(r'\b([0-9a-fA-F]{64,})\b', text):
            try:
                decoded = bytes.fromhex(m.group(1)).decode('utf-8', errors='ignore')
                if '.m3u8' in decoded and decoded.startswith('http'):
                    return decoded.replace('\\/', '/').replace('&amp;', '&')
            except Exception:
                pass
        return None
    
    def _is_noise_domain(self, url):
        for d in self.NOISE_DOMAINS:
            if d in url:
                return True
        return False
    
    def _govid_fetch(self, url, referer):
        hdrs = dict(self.headers)
        hdrs["Referer"] = referer or self.BASE_URL
        hdrs["Origin"] = self.BASE_URL
        return fetch(url, referer=referer or self.BASE_URL, extra_headers=hdrs)
    
    def _scan_page_for_stream(self, html, page_url):
        if not html:
            return None, None
        inline_blocks = re.findall(r'<script(?:\s[^>]*)?>(.+?)</script>', html, re.DOTALL | re.I)
        for i, blk in enumerate(inline_blocks):
            found = self._find_m3u8(blk)
            if found:
                return found, None
        ext_srcs = re.findall(r'<script[^>]+src=["\']?([^"\'>\s]+)["\']?', html, re.I)
        for src in ext_srcs[:6]:
            if not src.startswith('http'):
                src = self.BASE_URL + '/' + src.lstrip('/')
            if self._is_noise_domain(src):
                continue
            js, _ = self._govid_fetch(src, page_url)
            if not js:
                continue
            found = self._find_m3u8(js)
            if found:
                return found, None
            api_m = re.search(r'["\']/((?:stream|hls|vod|live|video|play|src)/)["\']', js)
            if api_m:
                return None, api_m.group(1)
        found = self._find_m3u8(html)
        if found:
            return found, None
        id_m = re.search(r'govid\.live/e/(\d+)/?', html)
        if id_m:
            return None, id_m.group(1)
        return None, None
    
    def _extract_govid_by_id(self, video_id, embed_url):
        canonical = "{}/e/{}/".format(self.GOVID_BASE, video_id)
        urls_to_scan = [canonical]
        if embed_url != canonical and "govid.live" in embed_url:
            urls_to_scan.append(embed_url)
        for page_url in urls_to_scan:
            html, _ = self._govid_fetch(page_url, self.BASE_URL)
            if not html:
                continue
            stream, extra = self._scan_page_for_stream(html, page_url)
            if stream:
                quality = "1080p" if "1080" in stream else ("720p" if "720" in stream else "HD")
                return stream, quality, page_url
            if extra:
                if extra.isdigit():
                    if extra != video_id:
                        return self._extract_govid_by_id(extra, "{}/e/{}/".format(self.GOVID_BASE, extra))
                else:
                    api_url = "{}/{}{}".format(self.GOVID_BASE, extra, video_id)
                    api_resp, _ = self._govid_fetch(api_url, canonical)
                    if api_resp:
                        stream = self._find_m3u8(api_resp)
                        if stream:
                            return stream, "HD", canonical
        return None, "", embed_url
    
    def _extract_govid_stream(self, embed_url, page_referer):
        m = re.search(r'/e/(\d+)/?', embed_url)
        if m:
            return self._extract_govid_by_id(m.group(1), embed_url)
        if '/play/' in embed_url:
            html, _ = self._govid_fetch(embed_url, page_referer)
            if html:
                id_m = re.search(r'govid\.live/e/(\d+)/?', html)
                if not id_m:
                    id_m = re.search(r'(?:video_?id|post_?id|vid|pid)\s*[=:"\']\s*["\']?(\d{4,7})["\']?', html, re.I)
                if id_m:
                    return self._extract_govid_by_id(id_m.group(1), embed_url)
                stream, extra = self._scan_page_for_stream(html, embed_url)
                if stream:
                    quality = "1080p" if "1080" in stream else ("720p" if "720" in stream else "HD")
                    return stream, quality, embed_url
                if extra and extra.isdigit():
                    return self._extract_govid_by_id(extra, embed_url)
        return None, "", embed_url
    
    def get_categories(self, mtype="movie"):
        base = self.BASE_URL
        cats = []
        
        cats.append({"title": "🏠 الرئيسية", "url": base + "/", "type": "category", "_action": "category"})
        
        cats.append({"title": "── أفلام ──", "url": "", "type": "separator"})
        cats.append({"title": "🎬 أفلام أجنبي", "url": base + "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/", "type": "category", "_action": "category"})
        cats.append({"title": "🎬 أفلام عربي", "url": base + "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%b9%d8%b1%d8%a8%d9%8a/", "type": "category", "_action": "category"})
        cats.append({"title": "🎬 أفلام هندي", "url": base + "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d9%87%d9%86%d8%af%d9%8a/", "type": "category", "_action": "category"})
        cats.append({"title": "🎬 أفلام تركية", "url": base + "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%aa%d8%b1%d9%83%d9%8a%d8%a9/", "type": "category", "_action": "category"})
        cats.append({"title": "🎬 أفلام اسيوية", "url": base + "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a%d8%a9/", "type": "category", "_action": "category"})
        cats.append({"title": "🎬 أفلام انمي", "url": base + "/category/%d8%a7%d9%81%d9%84%d8%a7%d9%85-%d8%a7%d9%86%d9%85%d9%8a/", "type": "category", "_action": "category"})
        
        cats.append({"title": "── مسلسلات ──", "url": "", "type": "separator"})
        cats.append({"title": "📺 مسلسلات اجنبي", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%ac%d9%86%d8%a8%d9%8a/", "type": "category", "_action": "category"})
        cats.append({"title": "📺 مسلسلات تركية", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%aa%d8%b1%d9%83%d9%8a%d8%a9/", "type": "category", "_action": "category"})
        cats.append({"title": "📺 مسلسلات انمي", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d9%86%d9%85%d9%8a/", "type": "category", "_action": "category"})
        cats.append({"title": "📺 مسلسلات هندية", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d9%87%d9%86%d8%af%d9%8a%d8%a9/", "type": "category", "_action": "category"})
        cats.append({"title": "📺 مسلسلات مدبلجة", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d9%85%d8%af%d8%a8%d9%84%d8%ac%d8%a9/", "type": "category", "_action": "category"})
        cats.append({"title": "📺 مسلسلات اسيوية", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%a7%d8%b3%d9%8a%d9%88%d9%8a%d8%a9/", "type": "category", "_action": "category"})
        
        cats.append({"title": "── مسلسلات رمضان ──", "url": "", "type": "separator"})
        cats.append({"title": "🌙 مسلسلات رمضان 2026", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2026/", "type": "category", "_action": "category"})
        cats.append({"title": "🌙 مسلسلات رمضان 2025", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2025/", "type": "category", "_action": "category"})
        cats.append({"title": "🌙 مسلسلات رمضان 2024", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2024/", "type": "category", "_action": "category"})
        cats.append({"title": "🌙 مسلسلات رمضان 2023", "url": base + "/category/%d9%85%d8%b3%d9%84%d8%b3%d9%84%d8%a7%d8%aa-%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2023/", "type": "category", "_action": "category"})
        cats.append({"title": "🌙 مسلسلات رمضان 2022", "url": base + "/category/%d8%b1%d9%85%d8%b6%d8%a7%d9%86-2022/", "type": "category", "_action": "category"})
        
        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        cats.append({"title": "🥊 عروض مصارعة", "url": base + "/category/%d8%b9%d8%b1%d9%88%d8%b6-%d9%85%d8%b5%d8%a7%d8%b1%d8%b9%d8%a9/", "type": "category", "_action": "category"})
        cats.append({"title": "📡 برامج تلفزيونية", "url": base + "/category/%d8%a8%d8%b1%d8%a7%d9%85%d8%ac-%d8%aa%d9%84%d9%81%d8%b2%d9%8a%d9%88%d9%86%d9%8a%d8%a9/", "type": "category", "_action": "category"})
        
        return cats
    
    def get_category_items(self, url, page=1):
        page_match = re.search(r'/page/(\d+)/', url)
        if page_match and page == 1:
            page = int(page_match.group(1))
    
        clean_url = re.sub(r'/page/\d+/?$', '', url.rstrip('/'))
        current_url = clean_url + "/page/{}/".format(page) if page > 1 else clean_url + "/"
    
        html, _ = fetch(current_url, referer=self.BASE_URL, extra_headers=self.headers)
        if not html:
            return []
    
        items = []
        seen_urls = set()
    
        for m in re.finditer(r'<a\b([^>]*\bclass="[^"]*\bshow-card\b[^"]*"[^>]*)>(.*?)</a>', html, re.DOTALL | re.I):
            attrs = m.group(1)
            card_content = m.group(2)
            href_m = re.search(r'\bhref="([^"]+)"', attrs, re.I)
            if not href_m:
                continue
            
            href = href_m.group(1)
            full_url = self._normalize_url(href)
            if ('/category/' in full_url or '/page/' in full_url
                    or full_url in seen_urls):
                continue
    
            poster_url = ""
            pm = re.search(r'background-image\s*:\s*url\(([^)]+)\)', attrs, re.I)
            if pm:
                poster_url = pm.group(1).strip('\'"')
    
            tm = re.search(r'<p[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</p>',
                           card_content, re.I)
            title = tm.group(1).strip() if tm else href.split('/')[-1].replace('-', ' ')
    
            seen_urls.add(full_url)
            items.append({
                "title": self._clean_title(title),
                "url": full_url,
                "poster": self._normalize_url(poster_url) if poster_url else "",
                "rating": "",
                "year": "",
                "type": "movie",
                "_action": "details",
            })
            if len(items) >= 50:
                break
    
        next_url = None
        am = re.search(
            r'<a\b[^>]*\bhref="([^"]+)"[^>]*\bclass="[^"]*\bpage-btn\b[^"]*"[^>]*>\s*(?:›|&rsaquo;|&gt;)\s*</a>',
            html, re.I)
        if am:
            next_url = self._normalize_url(am.group(1))
        else:
            next_n = page + 1
            nm = re.search(
                r'<a\b[^>]*\bhref="([^"]+)"[^>]*\bclass="[^"]*\bpage-btn\b[^"]*"[^>]*>\s*{}\s*</a>'.format(next_n),
                html, re.I)
            if nm:
                next_url = self._normalize_url(nm.group(1))
    
        if next_url:
            normalized_current = self._normalize_url(current_url)
            if next_url.rstrip('/') != normalized_current.rstrip('/'):
                items.append({
                    "title": "➡️ الصفحة التالية",
                    "url": next_url,
                    "type": "category",
                    "_action": "category"
                })
    
        return items
    
    def search(self, query, page=1):
        search_url = self.BASE_URL + "/?s=" + quote_plus(query)
        if page > 1:
            search_url += "&page=" + str(page)
        html, _ = fetch(search_url, referer=self.BASE_URL, extra_headers=self.headers)
        if not html:
            return []
        items, seen_urls = [], set()
        for m in re.finditer(r'<a\b([^>]*\bclass="[^"]*\bshow-card\b[^"]*"[^>]*)>(.*?)</a>', html, re.DOTALL | re.I):
            attrs = m.group(1)
            card_content = m.group(2)
            href_m = re.search(r'\bhref="([^"]+)"', attrs, re.I)
            if not href_m:
                continue
            href = href_m.group(1)
            full_url = self._normalize_url(href)
            if full_url in seen_urls:
                continue
            tm = re.search(r'<p[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</p>',
                           card_content, re.I)
            title = tm.group(1) if tm else href.split('/')[-1]
            seen_urls.add(full_url)
            items.append({"title": self._clean_title(title), "url": full_url,
                          "type": "movie", "_action": "details"})
        return items
    
    def get_page(self, url, m_type=None):
        log("faselhd_rip: get_page {}".format(url))
        html, final_url = fetch(url, referer=self.BASE_URL, extra_headers=self.headers)
        if not html:
            return {"title": "Error", "servers": [], "items": [], "type": "movie"}
    
        post_id = None
        for pat in [r'"post_id":\s*"?(\d+)"?', r'var\s+POST_ID\s*=\s*(\d+)', r'data-post-id=["\'](\d+)["\']']:
            m = re.search(pat, html, re.I)
            if m:
                post_id = m.group(1)
                break
    
        title_m = (re.search(r'<h1[^>]*class="[^"]*post-title[^"]*"[^>]*>(.*?)</h1>', html, re.I)
                   or re.search(r'<title>([^<]+)</title>', html, re.I))
        title = self._clean_title(title_m.group(1)) if title_m else ""
        pm = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
        poster = self._normalize_url(pm.group(1)) if pm else ""
        plotm = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html)
        plot = self._clean_title(plotm.group(1)) if plotm else ""
        ym = re.search(r'<span[^>]*class="[^"]*meta-tag[^"]*"[^>]*>📅\s*(\d{4})', html, re.I)
        year = ym.group(1) if ym else ""
        rm = re.search(r'<i[^>]*class="fa fa-star"[^>]*></i>\s*([0-9.]+)', html, re.I)
        rating = rm.group(1) if rm else ""
        item_type = "series" if ("/series" in url or "مسلسل" in title or "/anime" in url) else "movie"
    
        episodes = []
        if item_type == "series":
            for m in re.finditer(
                    r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*episode-link[^"]*"[^>]*>.*?الحلقة\s*(\d+)',
                    html, re.I):
                episodes.append({"title": "الحلقة {}".format(m.group(2)),
                                  "url": self._normalize_url(m.group(1)), "type": "episode", "_action": "details"})
    
        servers, seen_embed = [], []
    
        def _add(embed_url):
            embed_url = embed_url.replace('&amp;', '&').strip()
            if embed_url and embed_url not in seen_embed:
                seen_embed.append(embed_url)
                servers.append({"name": "🎬 Server {}".format(len(servers) + 1),
                                "url": embed_url, "type": "embed"})
    
        if post_id:
            _add("{}/e/{}/".format(self.GOVID_BASE, post_id))
            ajax_url = self.BASE_URL + "/wp-content/themes/timemovies/ajax.php"
            ajax_hdrs = dict(self.headers)
            ajax_hdrs.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": url,
                "Origin": self.BASE_URL
            })
            for server_num in range(0, 16):
                if len(servers) > self.MAX_AJAX_SERVERS:
                    break
                ajax_html, _ = fetch(ajax_url, referer=url,
                                     post_data="post_id={}&server={}".format(post_id, server_num),
                                     extra_headers=ajax_hdrs)
                if not ajax_html:
                    continue
                try:
                    data = json.loads(ajax_html)
                    if data.get("success") and data.get("iframe"):
                        sm = re.search(r'src=["\']([^"\']+)["\']', data["iframe"], re.I)
                        if sm:
                            _add(sm.group(1).replace('&amp;', '&'))
                except Exception:
                    pass
    
        return {"url": final_url or url, "title": title, "plot": plot,
                "poster": poster, "year": year, "rating": rating,
                "servers": servers, "items": episodes, "type": item_type}
    
    def extract_stream(self, url):
        log("faselhd_rip extract_stream: {}".format(url[:100]))
        url = url.replace('&amp;', '&').strip()
    
        govid_headers = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": "https://govid.live/",
            "Origin": "https://govid.live"
        }
    
        if "govid.live" in url:
            if ".m3u8" not in url:
                stream, quality, ref = self._extract_govid_stream(url, self.BASE_URL)
                if stream:
                    return stream, quality, govid_headers
            quality = "1080p" if "1080" in url else ("720p" if "720" in url else "HD")
            return url, quality, govid_headers
    
        if ".m3u8" in url:
            quality = "1080p" if "1080" in url else ("720p" if "720" in url else "HD")
            return url, quality, self.BASE_URL
    
        from .base import extract_stream as base_extract_stream
        stream_url, quality, ref, variants = base_extract_stream(url)
        if stream_url:
            return stream_url, quality, ref

        return None, "", self.BASE_URL