# -*- coding: utf-8 -*-
"""
TopCinema extractor - topcinemaa.top
Inherits from BaseExtractor.
"""

import sys
import re
from .base import BaseExtractor, fetch, urljoin, log, resolve_iframe_chain
from .referers import get_referer                               # [PATCH 82]

if sys.version_info[0] == 3:
    from urllib.parse import quote_plus, urlparse, urlunparse, quote, urlencode
    from html import unescape as html_unescape
else:
    from urllib import quote_plus, quote, urlencode
    from urlparse import urlparse, urlunparse
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape


class TopCinemaExtractor(BaseExtractor):
    """Extractor for TopCinema - topcinemaa.top"""
    
    DOMAINS = [
        "https://topcinema.io/",          # [PATCH C] primary
        "https://topcinemaa.com/",
        "https://topcinemaa.top/",
        "https://topcinma.com/",
        "https://topcinema.vip/",
        "https://topcima.info/",
        "https://topcinma.red/",
    ]
    VALID_HOST_MARKERS = ("topcinemaa.com", "topcinemaa.top", "topcinma.com", "topcinema.vip", "topcima.info", "topcinma.red", "topcinema", "topcinma", "topcima")
    BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)
    
    def __init__(self):
        super(TopCinemaExtractor, self).__init__()
        self.main_url = self.DOMAINS[0]
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
        if any(m in final for m in self.BLOCKED_HOST_MARKERS):
            return True
        return False
    
    def _looks_like_topcinema_page(self, html):
        text = html or ""
        return (
            "movie__block" in text
            or "allepcont" in text
            or "server--item" in text
            or "topcinema" in text.lower()
            or "توب سينما" in text
        )
    
    def _site_root(self, url):
        parts = urlparse(url)
        return "{}://{}/".format(parts.scheme or "https", parts.netloc)
    
    def _get_base(self):
        if self._resolved_base:
            return self._resolved_base
        for domain in self.DOMAINS:
            log("TopCinema: probing {}".format(domain))
            html, final_url = fetch(domain, referer=domain)
            final_url = final_url or domain
            if not self._is_valid_site_url(final_url):
                log("TopCinema: unexpected host after redirect {}".format(final_url))
                continue
            if self._is_blocked_page(html, final_url):
                log("TopCinema: blocked {}".format(final_url))
                continue
            if html and self._looks_like_topcinema_page(html):
                self._resolved_base = self._site_root(final_url)
                self.main_url = self._resolved_base
                log("TopCinema: selected base {}".format(self._resolved_base))
                return self._resolved_base
        self._resolved_base = self.DOMAINS[0]
        self.main_url = self._resolved_base
        log("TopCinema: all probes failed, falling back to {}".format(self._resolved_base))
        return self._resolved_base
    
    def _normalize_url(self, url):
        if not url:
            return ""
        url = html_unescape(url.strip())
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http"):
            return urljoin(self._get_base(), url)
        return url
    
    def _clean_title(self, title):
        title = html_unescape(title or "")
        title = title.replace("&amp;", "&")
        title = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]+', '', title)
        title = re.sub(r'\[[^\]]*\]\s*', '', title)
        title = re.sub(r'\s*[-|]\s*ت[ةه]?وب\s*سينما\s*$', '', title, flags=re.I)
        title = re.sub(r'ت[ةه]?وب\s*سينما', '', title, flags=re.I)
        noise_phrases = (
            "مشاهدة وتحميل", "مشاهدة وتحميل مباشر", "مشاهدة", "تحميل",
            "مترجمة", "مترجم", "مدبلجة", "مدبلج",
            "اون لاين", "اونلاين", "بجودة عالية", "بجودة", "حصريا", "كامل",
        )
        for phrase in noise_phrases:
            title = re.sub(r'\s*' + re.escape(phrase) + r'\s*', ' ', title, flags=re.I)
        type_words = ("فيلم", "افلام", "مسلسل", "مسلسلات", "انمي", "برنامج", "عرض")
        words = title.split()
        while words and words[0] in type_words:
            words.pop(0)
        return " ".join(words).strip()
    
    def _arabic_ordinals(self):
        return {
            "الاول": 1, "الأول": 1,
            "الثاني": 2,
            "الثالث": 3,
            "الرابع": 4,
            "الخامس": 5,
            "السادس": 6,
            "السابع": 7,
            "الثامن": 8,
            "التاسع": 9,
            "العاشر": 10,
        }
    
    def _season_number(self, title):
        ordinals = self._arabic_ordinals()
        for word, num in ordinals.items():
            if word in title:
                return num
        m = re.search(r'(?<!\d)(\d+)(?!\d)', title)
        return int(m.group(1)) if m else 9999
    
    # ------------------------------------------------------------------
    # [PATCH A] _extract_blocks — data-src-first poster, ribbon + quality,
    #           number/Collection badge, genres.
    # ------------------------------------------------------------------
    def _extract_blocks(self, html):
        items = []
        pattern = r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        for m in re.finditer(pattern, html, re.I | re.S):
            href = m.group(1)
            title = m.group(2)
            inner = m.group(3)
    
            if re.search(r'/(?:category|search|page|tag|author)/', href, re.I):
                continue
    
            # [PATCH A.1] poster: prefer data-src (lazy) over src (placeholder)
            poster = ""
            ds_m = re.search(r'<img[^>]+data-src=["\']([^"\']+)["\']', inner, re.I)
            if ds_m:
                poster = ds_m.group(1)
            else:
                ss_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', inner, re.I)
                if ss_m:
                    poster = ss_m.group(1)
            if not poster or poster.startswith('data:') or 'placeholder' in poster.lower():
                continue

            # [PATCH 46] IMDb rating
            rating = ""
            rating_m = re.search(r'class=["\']imdbRating["\'][^>]*>.*?<i[^>]*>.*?</i>\s*([\d.]+)', inner, re.I | re.S)
            if not rating_m:
                rating_m = re.search(r'class=["\']imdbRating["\'][^>]*>\s*([\d.]+)', inner, re.I)
            if rating_m:
                rating = rating_m.group(1)

            # [PATCH A.2] ribbon — quality tag or status badge ("الاخيرة")
            ribbon = ""
            ribbon_m = re.search(r'class=["\']ribbon["\'][^>]*>(.*?)</div>', inner, re.I | re.S)
            if ribbon_m:
                ribbon = re.sub(r'<[^>]+>', ' ', ribbon_m.group(1))
                ribbon = html_unescape(ribbon)
                ribbon = re.sub(r'\s{2,}', ' ', ribbon).strip()

            # [PATCH A.3] quality from <ul class="liList">
            list_quality = ""
            ul_m = re.search(r'<ul[^>]+class=["\'][^"\']*\bliList\b[^"\']*["\'][^>]*>(.*?)</ul>',
                             inner, re.I | re.S)
            if ul_m:
                for li_cls, li_inner in re.findall(
                    r'<li[^>]*class=["\']([^"\']*)["\'][^>]*>(.*?)</li>',
                    ul_m.group(1), re.I | re.S
                ):
                    if "imdbrating" in (li_cls or "").lower():
                        continue
                    li_text = re.sub(r'<[^>]+>', ' ', li_inner)
                    li_text = html_unescape(li_text)
                    li_text = re.sub(r'\s{2,}', ' ', li_text).strip()
                    if not li_text:
                        continue
                    if re.search(r'\d{3,4}p|WEB[\s\-]?DL|WEBRip|WEBSCR|BluRay|BRRip|HDTV|HDTS|HDTC|HDCAM|HDRip|HC[\s\-]?WEB|CAM|TS\b',
                                 li_text, re.I):
                        list_quality = li_text
                        break

            is_quality_ribbon = bool(re.search(
                r'\d{3,4}p|BluRay|WEB|HDTS|HDTC|HDCAM|HDTV|HDRip|HC[\s\-]?WEB|BRRip|WEBRip',
                ribbon, re.I
            ))
            quality_label = ribbon if is_quality_ribbon else (list_quality or ribbon)
            status_label = "" if is_quality_ribbon else ribbon

            # [PATCH A.4] number / number Collection badge
            number_label = ""
            num_m = re.search(
                r'<div[^>]+class=["\'][^"\']*\bnumber\b[^"\']*["\'][^>]*>(.*?)</div>',
                inner, re.I | re.S
            )
            if num_m:
                number_label = re.sub(r'<[^>]+>', ' ', num_m.group(1))
                number_label = html_unescape(number_label)
                number_label = re.sub(r'\s{2,}', ' ', number_label).strip()

            # [PATCH A.5] genres
            genres = []
            if ul_m:
                for li_cls, li_inner in re.findall(
                    r'<li[^>]*class=["\']([^"\']*)["\'][^>]*>(.*?)</li>',
                    ul_m.group(1), re.I | re.S
                ):
                    if "imdbrating" in (li_cls or "").lower():
                        continue
                    li_text = re.sub(r'<[^>]+>', ' ', li_inner)
                    li_text = html_unescape(li_text)
                    li_text = re.sub(r'\s{2,}', ' ', li_text).strip()
                    if not li_text:
                        continue
                    if re.search(r'\d{3,4}p|WEB[\s\-]?DL|WEBRip|WEBSCR|BluRay|BRRip|HDTV|HDTS|HDTC|HDCAM|HDRip|HC[\s\-]?WEB|CAM|TS\b',
                                 li_text, re.I):
                        continue
                    genres.append(li_text)
    
            link = self._normalize_url(href)
            poster = self._normalize_url(poster)
    
            if "الحلقة" in title and "/series/" not in link:
                item_type = "episode"
            elif "/series/" in link:
                item_type = "series"
            elif "مسلسل" in title or "انمي" in title:
                item_type = "series"
            else:
                item_type = "movie"
    
            title = self._clean_title(title)

            # [PATCH 52] year extraction
            year = ""
            ym = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            if ym:
                year = ym.group(1)
                title = re.sub(r'\s*\b' + year + r'\b\s*', ' ', title)
                title = re.sub(r'\s{2,}', ' ', title).strip(' -|')

            items.append({
                "title": title,
                "url": link,
                "poster": poster,
                "type": item_type,
                "year": year,
                "rating": rating,
                "quality": quality_label,
                "status": status_label,
                "number": number_label,
                "genres": genres,
                "_action": "details"
            })
        return items
    
    # ------------------------------------------------------------------
    # [PATCH B/C] get_categories — full site map. /home1/ is a landing
    #             entry only.
    # ------------------------------------------------------------------
    def get_categories(self, mtype="movie"):
        base = self._get_base()
        return [
            {"title": "🏠 الرئيسية",     "url": base + "home1/",  "type": "category", "_action": "category"},
            {"title": "🎬 المضاف حديثا", "url": base + "recent/", "type": "category", "_action": "category"},

            {"title": "🎬 أفلام أجنبية", "url": base + "category/%D8%A7%D9%81%D9%84%D8%A7%D9%85-%D8%A7%D8%AC%D9%86%D8%A8%D9%8A-8/", "type": "category", "_action": "category"},
            {"title": "🎬 أفلام أنمي",   "url": base + "category/%D8%A7%D9%81%D9%84%D8%A7%D9%85-%D8%A7%D9%86%D9%85%D9%8A-2/",     "type": "category", "_action": "category"},
            {"title": "🎬 أفلام أسيوية", "url": base + "category/%D8%A7%D9%81%D9%84%D8%A7%D9%85-%D8%A7%D8%B3%D9%8A%D9%88%D9%8A/",  "type": "category", "_action": "category"},
            {"title": "🎬 أفلام نتفليكس", "url": base + "netflix-movies/",   "type": "category", "_action": "category"},
            {"title": "🎞️ سلاسل الأفلام", "url": base + "movies-collections/", "type": "category", "_action": "category"},
            {"title": "⭐ أفلام الأعلى تقييما IMDB", "url": base + "top-rating-imdb/", "type": "category", "_action": "category"},

            {"title": "📺 مسلسلات أجنبية", "url": base + "category/%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D8%AC%D9%86%D8%A8%D9%8A/", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات أسيوية", "url": base + "category/%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D8%B3%D9%8A%D9%88%D9%8A%D8%A9/", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات أنمي",   "url": base + "category/%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D9%86%D9%85%D9%8A/",   "type": "category", "_action": "category"},
            {"title": "⭐ مسلسلات الأعلى تقييما IMDB", "url": base + "top-rating-imdb-series/", "type": "category", "_action": "category"},

            {"title": "📺 مسلسلات نتفليكس أجنبي", "url": base + "netflix-series/?cat=7", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات نتفليكس أنمي",  "url": base + "netflix-series/?cat=8", "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات نتفليكس آسيوي", "url": base + "netflix-series/?cat=9", "type": "category", "_action": "category"},

            {"title": "📦 مسلسلات أجنبي كاملة",  "url": base + "full-packs/?cat=7", "type": "category", "_action": "category"},
            {"title": "📦 مسلسلات أنمي كاملة",   "url": base + "full-packs/?cat=8", "type": "category", "_action": "category"},
            {"title": "📦 مسلسلات آسيوية كاملة", "url": base + "full-packs/?cat=9", "type": "category", "_action": "category"},
        ]
    
    def get_category_items(self, url, page=1):
        html, final_url = fetch(url, referer=self._get_base())
        if not html:
            log("TopCinema: fetch returned no content for {}".format(url))
            return []
    
        items = self._extract_blocks(html)
    
        next_url = None
        m = re.search(r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<a[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']next["\']', html, re.I)
        if not m:
            m = re.search(r'<a[^>]+class=["\'][^"\']*\bnext\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*(?:&raquo;|»)\s*</a>', html, re.I | re.S)
        if m:
            next_url = self._normalize_url(m.group(1))
        if next_url:
            items.append({
                "title": "➡️ الصفحة التالية",
                "url": next_url,
                "type": "category",
                "_action": "category"
            })
        return items
    
    def search(self, query, page=1):
        url = self._get_base() + "search/?query=" + quote_plus(query) + "&type=all"
        html, final_url = fetch(url, referer=self._get_base())
        return self._extract_blocks(html)
    
    def get_page(self, url, m_type=None):
        html, final_url = fetch(url, referer=self._get_base())
    
        title_m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
        raw_title = title_m.group(1) if title_m else "Unknown Title"
        title = self._clean_title(raw_title)

        # [PATCH D.1] prefer the visible <h1 class="post-title"> when present
        h1_m = re.search(
            r'<h1[^>]+class=["\'][^"\']*\bpost-title\b[^"\']*["\'][^>]*>(.*?)</h1>',
            html, re.S | re.I
        )
        if h1_m:
            h1_text = re.sub(r'<[^>]+>', ' ', h1_m.group(1)).strip()
            h1_text = re.sub(r'\s{2,}', ' ', h1_text)
            if h1_text:
                title = self._clean_title(h1_text)
    
        poster_m = re.search(r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        poster = self._normalize_url(poster_m.group(1)) if poster_m else ""
        if not poster:
            for img_m in re.finditer(r'<img[^>]+(?:data-src|src)=["\']([^"\']+)["\']', html, re.I):
                candidate = img_m.group(1)
                if "wp-content/uploads" in candidate and "logo" not in candidate.lower():
                    poster = self._normalize_url(candidate)
                    break
    
        plot_m = re.search(r'class=["\']description["\'][^>]*>(.*?)</', html, re.S | re.I)
        plot = self._clean_title(re.sub(r'<[^>]+>', '', plot_m.group(1))) if plot_m else ""

        # [PATCH D.2] assemblies pages carry the plot in <div class="story"><p>…
        if not plot:
            story_m = re.search(
                r'<div[^>]+class=["\'][^"\']*\bstory\b[^"\']*["\'][^>]*>(.*?)</div>',
                html, re.S | re.I
            )
            if story_m:
                plot = self._clean_title(re.sub(r'<[^>]+>', ' ', story_m.group(1)))

        # [PATCH D.3] /assemblies/ URLs are "movie collection" hubs:
        #   /assemblies/{slug}/       → hub with poster + plot + full grid
        #   /assemblies/{slug}/list/  → bare grid of the same movies
        # Both render each movie as a .Small--Box card (on the hub inside
        # <section class="allseasonss">). Reuse _extract_blocks so every
        # card becomes an item the UI can open as a details page.
        if "/assemblies/" in (final_url or url):
            ass_items = []
            ass_m = re.search(
                r'<section[^>]+class=["\'][^"\']*allseasonss[^"\']*["\'][^>]*>(.*?)</section>',
                html, re.S | re.I
            )
            if ass_m:
                ass_items = self._extract_blocks(ass_m.group(1))
            if not ass_items:
                # /list/ page has no .allseasonss — cards are at top level
                ass_items = self._extract_blocks(html)
            return {
                "url": final_url,
                "title": title,
                "plot": plot,
                "poster": poster,
                "servers": [],
                "items": ass_items,
                "type": "series"
            }
    
        servers = []
        episodes = []
        item_type = "movie"
    
        watch_url_m = re.search(
            r'<a[^>]+class=["\'][^"\']*watch[^"\']*["\'][^>]+href=["\']([^"\']+/watch/?)[\"\']',
            html, re.I
        )
        watch_page_html = html
        watch_url = final_url
        if watch_url_m:
            watch_url = self._normalize_url(watch_url_m.group(1))
            watch_page_html, _ = fetch(watch_url, referer=final_url)
            watch_page_html = watch_page_html or ""
    
        post_id = ""
        for pat in [
            r'data-id=["\'](\d+)["\']',
            r'\?p=(\d+)',
            r'postid["\']?\s*[:=]\s*["\']?(\d+)["\']?',
            r'post_id["\']?\s*[:=]\s*["\']?(\d+)["\']?'
        ]:
            m = re.search(pat, watch_page_html, re.I)
            if m:
                post_id = m.group(1)
                break
    
        server_candidates = []
        li_matches = re.findall(
            r'<li(?=[^>]*class=["\'][^"\']*server--item)(?=[^>]*data-id=["\'](\d+))(?=[^>]*data-server=["\'](\d+))[^>]*>(.*?)</li>',
            watch_page_html, re.I | re.S
        )
        for pid, idx, inner in li_matches:
            name = re.sub(r'<[^>]+>', ' ', inner)
            name = self._clean_title(re.sub(r'\s+', ' ', name)).strip()
            if name:
                server_candidates.append((pid, idx, name))
    
        if not server_candidates:
            generic_matches = re.findall(
                r'<(?:li|a|button|div)[^>]*data-id=["\'](\d+)["\'][^>]*data-server=["\'](\d+)["\'][^>]*>(.*?)</(?:li|a|button|div)>',
                watch_page_html, re.I | re.S
            )
            for pid, idx, inner in generic_matches:
                name = re.sub(r'<[^>]+>', ' ', inner)
                name = self._clean_title(re.sub(r'\s+', ' ', name)).strip()
                if name:
                    server_candidates.append((pid, idx, name))
    
        if not server_candidates and post_id:
            known_servers = [
                "متعدد الجودات", "UpDown", "StreamWish", "Doodstream",
                "Filelions", "Streamtape", "LuluStream", "Filemoon",
                "Mixdrop", "VidGuard", "Okru"
            ]
            for i, srv in enumerate(known_servers, 1):
                if re.search(re.escape(srv), watch_page_html, re.I):
                    server_candidates.append((post_id, str(i), srv))
    
        ajax_endpoint = self._get_base() + "wp-content/themes/movies2023/Ajaxat/Single/Server.php"
        seen = set()
        for pid, idx, name in server_candidates:
            if not pid or not idx:
                continue
            key = (pid, idx)
            if key in seen:
                continue
            seen.add(key)
            clean_name = self._clean_title(name or "").strip()
            if not clean_name:
                continue
            clean_name = re.sub(r'\[[^\]]*\]', ' ', clean_name)
            clean_name = re.sub(r'\s{2,}', ' ', clean_name).strip(' -–|')
            if not clean_name:
                clean_name = "Server {}".format(idx)
            s_url = "topcinema_server|{}|{}|{}|{}".format(
                ajax_endpoint, pid, idx, watch_url
            )
            servers.append({
                "name": clean_name,
                "url": s_url,
            })
    
        is_hub_or_season = "/series/" in (final_url or url)
    
        if is_hub_or_season:
            eps_m = re.search(
                r'<section[^>]+class=["\'][^"\']*allepcont[^"\']*["\'][^>]*>(.*?)</section>',
                html, re.S | re.I
            )
            if eps_m:
                for e_link, e_title in re.findall(
                    r'<a[^>]+href=["\']([^"\']+)["\'][^>]+title=["\']([^"\']+)["\']',
                    eps_m.group(1), re.S | re.I
                ):
                    e_link_norm = self._normalize_url(e_link)
                    if not e_link_norm or e_link_norm == final_url:
                        continue
                    e_text = self._clean_title(e_title)
                    e_num_m = re.search(r'الحلقة\s*(\d+)', e_title)
                    episodes.append({
                        "title": ("حلقة " + e_num_m.group(1)) if e_num_m else e_text,
                        "url": e_link_norm,
                        "type": "episode",
                        "_action": "item",
                        "_num": int(e_num_m.group(1)) if e_num_m else 9999,
                    })
                episodes.sort(key=lambda e: e["_num"])
                for e in episodes:
                    del e["_num"]
    
            if not episodes:
                seasons_m = re.search(
                    r'<section[^>]+class=["\'][^"\']*allseasonss[^"\']*["\'][^>]*>(.*?)</section>',
                    html, re.S | re.I
                )
                found_seasons = []
                if seasons_m:
                    for s_block in re.finditer(
                        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                        seasons_m.group(1), re.S | re.I
                    ):
                        s_link, s_inner = s_block.group(1), s_block.group(2)
                        h3_m = re.search(r'<h3[^>]*class=["\']title["\'][^>]*>(.*?)</h3>', s_inner, re.S)
                        s_text = h3_m.group(1).strip() if h3_m else re.sub(r'<[^>]+>', ' ', s_inner).strip()
                        s_link_norm = self._normalize_url(s_link)
                        if s_link_norm and s_link_norm != final_url:
                            s_clean = self._clean_title(s_text) or "موسم"
                            found_seasons.append({
                                "title": s_clean,
                                "url": s_link_norm,
                                "type": "series",
                                "_action": "item",
                                "_num": self._season_number(s_text),
                            })
                found_seasons.sort(key=lambda s: s["_num"])
                for s in found_seasons:
                    del s["_num"]
                episodes = found_seasons
    
            if episodes:
                return {
                    "url": final_url,
                    "title": title,
                    "plot": plot,
                    "poster": poster,
                    "servers": [],
                    "items": episodes,
                    "type": "series"
                }
    
        is_series_like = (
            "مسلسل" in raw_title or
            "الحلقة" in watch_page_html or
            "episodes" in watch_page_html.lower() or
            "season" in watch_page_html.lower()
        )
        if is_series_like:
            eps_container = ""
            m = re.search(
                r'<div[^>]+class=["\'][^"\']*episodes--list--side[^"\']*["\'][^>]*>(.*?)</div>',
                watch_page_html, re.S | re.I
            )
            if m:
                eps_container = m.group(1)
            else:
                for container_pat in [
                    r'<div[^>]+class=["\'][^"\']*(?:episodes|series-episodes|season-episodes|ep_list|episodes-list|series-list|all-episodes)[^"\']*["\'][^>]*>(.*?)</div>',
                    r'<ul[^>]*class=["\'][^"\']*(?:episodes|series-episodes|list-episodes|ep_list)[^"\']*["\'][^>]*>(.*?)</ul>',
                    r'<section[^>]*class=["\'][^"\']*(?:episodes|series)[^"\']*["\'][^>]*>(.*?)</section>',
                    r'<div[^>]+id=["\'][^"\']*(?:episodes|episodes-list|episodes-all)[^"\']*["\'][^>]*>(.*?)</div>'
                ]:
                    m = re.search(container_pat, watch_page_html, re.S | re.I)
                    if m:
                        eps_container = m.group(1)
                        break
            if not eps_container:
                eps_container = watch_page_html
    
            eps_matches = re.findall(
                r'<a[^>]+href=["\']([^"\']+/(?:watch|episode)[^"\']*)["\'][^>]*>(.*?)</a>',
                eps_container, re.DOTALL | re.I
            )
            seen_eps = set()
            for e_link, e_inner in eps_matches:
                full_link = self._normalize_url(e_link)
                if not full_link or full_link == watch_url:
                    continue
                if full_link in seen_eps:
                    continue
                seen_eps.add(full_link)
    
                e_text = re.sub(r'<[^>]+>', '', e_inner).strip()
                e_num_m = re.search(r'الحلقة\s*(\d+)', e_text)
                if not e_num_m:
                    e_num_m = re.search(r'(\d+)', e_text)
    
                e_num = e_num_m.group(1).strip() if e_num_m else (e_text[:30] if e_text else "Episode")
                episodes.append({
                    "title": "حلقة " + e_num if e_num.isdigit() else e_num,
                    "url": full_link,
                    "type": "episode",
                    "_action": "item",
                    "_num": int(e_num) if e_num.isdigit() else 9999,
                })
            episodes.sort(key=lambda e: e["_num"])
            for e in episodes:
                del e["_num"]
    
        if servers and episodes:
            item_type = "episode"
        elif episodes:
            item_type = "series"
    
        return {
            "url": final_url,
            "title": title,
            "plot": plot,
            "poster": poster,
            "servers": servers,
            "downloads": self._extract_download_links(html),   # [PATCH T1]
            "items": episodes,
            "type": item_type
        }
    
    def _extract_download_links(self, html):
        """
        [PATCH T1] topcinema's real download-servers list. Confirmed against a real capture:
        <ul class="download-items"><li><a class="downloadsLink" href="URL">
        <div class="text"><span>NAME</span><p>QUALITY</p></div></a></li>...</ul>
        """
        downloads = []
        if not html:
            return downloads
        block_m = re.search(
            r'<ul[^>]*class="[^"]*download-items[^"]*"[^>]*>(.*?)</ul>',
            html, re.S | re.I)
        if not block_m:
            return downloads
        for li in re.finditer(r'<li\b[^>]*>(.*?)</li>', block_m.group(1), re.S | re.I):
            li_html = li.group(1)
            # the real markup has href="..." BEFORE class="downloadsLink", not after --
            # confirmed directly against a real capture. Matching the whole <a> tag first and
            # checking for both attributes within it, in either order, is robust to this
            # instead of assuming one specific attribute order.
            a_m = re.search(r'<a\b[^>]*>', li_html, re.S | re.I)
            if not a_m or "downloadsLink" not in a_m.group(0):
                continue
            link_m = re.search(r'href="([^"]+)"', a_m.group(0), re.I)
            if not link_m:
                continue
            url = link_m.group(1).strip()
            if not url:
                continue
            name_m = re.search(r'<span>([^<]+)</span>', li_html, re.I)
            name = self._clean_title(name_m.group(1)).strip() if name_m else "Server"
            quality_m = re.search(r'<p>([^<]*)</p>', li_html, re.I)
            quality = quality_m.group(1).strip() if quality_m else ""
            downloads.append({
                "resolution": quality,
                "size": "",
                "quality": name,
                "url": url,
            })
        return downloads

    def extract_stream(self, url):
        log("TopCinema: resolving {}".format(url))
        if url.startswith("topcinema_server|"):
            parts = url.split("|")
            ajax_url = parts[1]
            post_id = parts[2]
            server_index = parts[3]
            referer_url = parts[4] if len(parts) > 4 else self._get_base()
    
            postdata = {
                "id": post_id,
                "i": server_index
            }
            html, _ = fetch(ajax_url, referer=referer_url,
                            extra_headers={"X-Requested-With": "XMLHttpRequest"},
                            post_data=postdata)
    
            ifr_m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html or "")   # [PATCH 82]
            if ifr_m:
                v_url = self._normalize_url(ifr_m.group(1))
                log("TopCinema: Found iframe '{}'".format(v_url))
                from .base import get_last_quality_variants, get_synthesized_variants
    
                def _variants_for(stream):
                    v = [(lbl, u) for lbl, u in get_last_quality_variants() if u != stream]
                    if not v:
                        v = [(lbl, u) for lbl, u in get_synthesized_variants(stream) if u != stream]
                    return v
    
                resolved = resolve_iframe_chain(v_url, referer=self._get_base())
                final_stream, chain_domain = "", ""
                if isinstance(resolved, tuple):
                    final_stream = resolved[0] or ""
                    chain_domain = resolved[1] if len(resolved) > 1 else ""
                elif resolved:
                    final_stream = resolved
                if final_stream:
                    final_referer = (get_referer(final_stream, default_self=False)
                                     or ("https://{}/".format(chain_domain) if chain_domain else self._get_base()))
                    variants = _variants_for(final_stream)
                    quality = self._quality_from_url(final_stream)
                    return final_stream, quality, final_referer, variants
                try:
                    from .base import extract_stream as _base_extract
                    r = _base_extract(v_url)
                    if r and r[0]:
                        return (r[0], r[1] or self._quality_from_url(r[0]),
                                r[2] or self._get_base(), (r[3] if len(r) > 3 else []))
                except Exception as e:
                    log("TopCinema: host-resolver fallback failed: {}".format(e))
                log("TopCinema: no stream from embed {}".format(v_url))
                return None, "", self._get_base(), []
            log("TopCinema: no iframe in the server reply for {}".format(ajax_url))
            return None, "", self._get_base(), []

        from .base import extract_stream as _generic_extract
        return _generic_extract(url)

    def _quality_from_url(self, url):
        if not url:
            return ""
        lower = url.lower()
        if "1080" in lower or "fhd" in lower or "-f3-" in lower or "_o" in lower or "_x" in lower:
            return "1080p"
        if "720" in lower or "-f2-" in lower or "_h" in lower:
            return "720p"
        if "480" in lower or "-f1-" in lower or "_n" in lower:
            return "480p"
        if "360" in lower or "_l" in lower:
            return "360p"
        return "HD"