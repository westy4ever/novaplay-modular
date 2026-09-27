# -*- coding: utf-8 -*-
"""
Arabseed extractor - Multi-domain support
Inherits from BaseExtractor.
"""

import base64
import html as html_lib
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .base import BaseExtractor, fetch, log, urljoin, clear_cookies

QUALITY_ORDER = {"1080": 0, "720": 1, "480": 2}
BLOCKED_HOSTS = ("vidara.to", "bysezejataos.com")

# [PATCH AS3] Real live domains first (fastest path, no redirect hop).
# The legacy domains below 302 to one of these two, but we keep them as
# fallbacks in case the live domains are temporarily unreachable.
_KNOWN_DOMAINS = [
    "https://arabseed.rent/",          # live (captured pages confirm this)
    "https://www.arabseed.wine/",      # live (redirect target for .rocks / .bid / .in)
    "https://arabseed.wine/",          # non-www alias
    # Legacy redirectors (kept as fallbacks):
    "https://arabseed.loan/",          # → https://arabseed.rent/home/
    "https://arabseed.rocks/",         # → https://www.arabseed.wine/home/
    "https://m.arsd.bid/",             # → https://www.arabseed.wine/home/
    "https://arabseed.in/",            # → https://www.arabseed.wine/home/
]

# Class name fragments that identify movie/post blocks
_BLOCK_CLASS_FRAGMENTS = (
    "movie__block", "recent--block", "post--block",
    "movie-block", "post-block", "movie-item", "post-item",
    "film-item", "movieItem", "postItem", "filmItem",
    "Movies--Box", "SmallBox", "GridItem", "movie-card",
    "post-card", "card-item", "movie-box", "post-box",
    "Thumb--GridItem", "GridItem", "poster", "card",
)


class ArabseedExtractor(BaseExtractor):
    """Extractor for Arabseed - supports multiple domains"""

    MAIN_URL = _KNOWN_DOMAINS[0]

    def __init__(self):
        super(ArabseedExtractor, self).__init__()
        self.main_url = self.MAIN_URL
        self._resolved_base = None  # Will be set dynamically by _get_base()

    def _get_base(self):
        """Probe known domains and return the first working one.

        [PATCH AS3] The old version stored the *original* probe domain
        (e.g. ``https://arabseed.loan/``) as the resolved base, even
        though that URL immediately 302s to ``https://arabseed.rent/home/``.
        That meant every subsequent ``fetch()`` still hit the redirector
        and sent a mismatched ``Referer`` — which is what broke
        pagination and category pages in practice. We now resolve to the
        **origin of the final, post-redirect URL** (scheme + host only,
        no path) and cache that instead.
        """
        if self._resolved_base:
            return self._resolved_base

        for domain in _KNOWN_DOMAINS:
            try:
                log("ArabSeed: Probing domain {}".format(domain))
                html, final_url = fetch(domain, referer=domain)
                if not html:
                    continue

                # Check for Cloudflare or dead redirects
                lower_html = html.lower()
                if "just a moment" in lower_html or "cf-chl" in lower_html:
                    log("ArabSeed: Domain {} blocked by Cloudflare".format(domain))
                    continue

                final_url = final_url or domain
                final_host_match = re.search(r'https?://([^/]+)', final_url)
                final_host = final_host_match.group(1) if final_host_match else ""

                # Accept any arabseed/arsd host (the redirector chain
                # lands on arabseed.rent or www.arabseed.wine)
                if final_host and not any(
                    marker in final_host for marker in ("arabseed", "arsd")
                ):
                    log("ArabSeed: Domain {} redirected to unknown host {}".format(
                        domain, final_host))
                    continue

                # [PATCH AS3] Resolve to the *origin* of the final URL,
                # not the original probe domain, and not the full redirect
                # path (the redirects end in /home/ which we don't want
                # as a base for urljoin).
                origin_match = re.match(r'(https?://[^/]+)', final_url)
                resolved = origin_match.group(1) if origin_match else domain
                if not resolved.endswith("/"):
                    resolved += "/"

                self._resolved_base = resolved
                self.main_url = resolved
                log("ArabSeed: Selected working domain: {} (redirected from {})".format(
                    resolved, domain))
                return self._resolved_base
            except Exception as e:
                log("ArabSeed: Domain {} failed with error: {}".format(domain, e))
                continue

        # Fallback if all fail
        log("ArabSeed: All domains failed, falling back to {}".format(self.MAIN_URL))
        self._resolved_base = self.MAIN_URL
        return self._resolved_base

    def _full_url(self, path):
        if not path:
            return ""
        path = html_lib.unescape(path.strip()).replace("\\/", "/").replace("&amp;", "&")

        if path.startswith("http"):
            # If the URL points to a known Arabseed domain, rewrite it to
            # the currently active domain to avoid hitting a dead link /
            # redirector again. [PATCH AS3] _KNOWN_DOMAINS now includes
            # both live domains so links between arabseed.rent and
            # arabseed.wine get normalised.
            current_base = self._get_base()
            for d in _KNOWN_DOMAINS:
                if path.startswith(d) and d != current_base:
                    path = current_base + path[len(d):]
                    break
            return path

        if path.startswith("//"):
            return "https:" + path
        return urljoin(self._get_base(), path)

    def _clean_title(self, title):
        title = html_lib.unescape(title or "")
        for word in ("مشاهدة", "فيلم", "مسلسل", "تحميل", "اون لاين", "أون لاين",
                     "مترجم", "مترجمة", "مدبلج", "مدبلجة", "بجودة", "عالية"):
            title = title.replace(word, "")
        title = re.sub(r'\s*[-|]\s*arabseed.*$', '', title, flags=re.I)
        title = re.sub(r'\s*[-|]\s*عرب\s*سيد.*$', '', title, flags=re.I)
        return re.sub(r'\s+', ' ', title).strip(" -|")

    def _extract_first(self, patterns, text):
        for pattern in patterns:
            match = re.search(pattern, text or "", re.S)
            if match:
                return match.group(1).strip()
        return ""

    def _decode_hidden_url(self, url):
        if not (url or "").strip():
            return ""
        url = (url or "").replace("\\/", "/").replace("&amp;", "&").strip()
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            url = urljoin(self._get_base(), url)
        for key in ("url", "id"):
            marker = key + "="
            if marker not in url:
                continue
            raw = url.split(marker, 1)[1].split("&", 1)[0]
            try:
                raw += "=" * ((4 - len(raw) % 4) % 4)
                decoded = base64.b64decode(raw).decode("utf-8")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass
        if url.rstrip("/") == self.MAIN_URL.rstrip("/"):
            return ""
        return url

    def _determine_item_type(self, link, title):
        link_lower = link.lower()
        if "-season-" in link_lower or "-episode-" in link_lower or "/episode/" in link_lower:
            return "episode"
        if "/series-" in link_lower or "/serie/" in link_lower or "/series/" in link_lower or "/selary/" in link_lower:
            return "series"
        if "مسلسل" in (title or "") or "الحلقة" in (title or "") or "حلقة" in (title or ""):
            return "series"
        return "movie"

    def _extract_item_from_block(self, block_html):
        """Extract a single item dict from an HTML block."""
        href_m = re.search(r'href=["\']([^"\']+)["\']', block_html, re.IGNORECASE)
        if not href_m:
            return None
        link = self._full_url(href_m.group(1))
        if not link or "/category/" in link or "/page/" in link or "/tag/" in link:
            return None
        if link.startswith("#") or link.startswith("javascript:"):
            return None

        title = ""
        title_m = (
            re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', block_html, re.IGNORECASE) or
            re.search(r'title=["\']([^"\']+)["\']', block_html, re.IGNORECASE) or
            re.search(r'<(?:h[1-4])[^>]*>([^<]+)</', block_html, re.IGNORECASE) or
            re.search(r'<(?:span|p|div)[^>]*class=["\'][^"\']*(?:title|name)[^"\']*["\'][^>]*>([^<]+)</', block_html, re.IGNORECASE)
        )
        if title_m:
            title = self._clean_title(title_m.group(1))
        if not title or len(title) < 2:
            return None

        img = ""
        img_m = re.search(
            r'<img[^>]+(?:data-src|data-lazy-src|data-original|src)=["\']([^"\']+)["\']',
            block_html, re.IGNORECASE
        )
        if img_m:
            img = self._full_url(img_m.group(1))
            if any(x in img.lower() for x in ("logo", "placeholder", "loading.gif", "lazy_load", "data:image")):
                img = ""

        return {
            "title": title,
            "url": link,
            "poster": img,
            "type": self._determine_item_type(link, title),
            "_action": "details",
        }

    def _extract_items_from_html(self, html):
        """Extract movie/series items using 4 strategies."""
        if not html:
            return []

        items = []
        seen = set()
        class_pattern = "|".join(re.escape(f) for f in _BLOCK_CLASS_FRAGMENTS)

        # === Strategy 1: <a> tags with movie-related classes ===
        a_block_re = re.compile(
            r'<a\s[^>]*class=["\'][^"\']*(?:' + class_pattern + r')[^"\']*["\'][^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL
        )
        for m in a_block_re.finditer(html):
            item = self._extract_item_from_block(m.group(0))
            if item and item["url"] not in seen:
                seen.add(item["url"])
                items.append(item)
        if items:
            log("ArabSeed: Strategy 1 found {} items".format(len(items)))
            return items

        # === Strategy 2: Container elements with movie-related classes ===
        container_re = re.compile(
            r'<(?:article|div|li)\s[^>]*class=["\'][^"\']*(?:' + class_pattern + r')[^"\']*["\'][^>]*>',
            re.IGNORECASE
        )
        for m in container_re.finditer(html):
            start = m.end()
            tag_name = re.match(r'<(\w+)', m.group(0)).group(1).lower()
            close_tag = '</{}>'.format(tag_name)
            depth = 1
            pos = start
            end = -1
            while depth > 0 and pos < len(html):
                no = html.find('<' + tag_name, pos)
                nc = html.find(close_tag, pos)
                if nc == -1:
                    break
                if no != -1 and no < nc:
                    depth += 1
                    pos = no + 1
                else:
                    depth -= 1
                    end = nc
                    pos = nc + len(close_tag)
            block = html[start:end] if end > start else html[start:start+2000]
            item = self._extract_item_from_block(m.group(0) + block)
            if item and item["url"] not in seen:
                seen.add(item["url"])
                items.append(item)
        if items:
            log("ArabSeed: Strategy 2 found {} items".format(len(items)))
            return items

        # === Strategy 3: Broad fallback - any <a> with href + title (img optional) ===
        log("ArabSeed: Using broad fallback extraction")
        for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL):
            link = self._full_url(m.group(1))
            inner = m.group(2)
            if not link or link in seen:
                continue
            if any(x in link.lower() for x in ("/category/", "/page/", "/tag/", "/author/",
                                                "/feed/", "#", "javascript:", "facebook.com",
                                                "twitter.com", "youtube.com", "google.com",
                                                "whatsapp.com", "telegram")):
                continue

            img = ""
            img_m = re.search(r'<img[^>]+(?:data-src|data-lazy-src|data-original|src)=["\']([^"\']+)["\']', inner, re.IGNORECASE)
            if img_m:
                img = self._full_url(img_m.group(1))
                if any(x in img.lower() for x in ("logo", "placeholder", "loading.gif", "lazy_load", "data:image", "sprite", "icon")):
                    img = ""

            if not img:
                bg_m = re.search(r'background(?:-image)?\s*:\s*url\(["\']?([^"\')]+)["\']?\)', inner, re.IGNORECASE)
                if bg_m:
                    img = self._full_url(bg_m.group(1))

            title_m = (
                re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', inner, re.IGNORECASE) or
                re.search(r'title=["\']([^"\']+)["\']', m.group(0), re.IGNORECASE) or
                re.search(r'<(?:h[1-4]|span|p)[^>]*>([^<]{2,})</', inner, re.IGNORECASE)
            )
            if not title_m:
                continue
            title = self._clean_title(title_m.group(1))
            if not title or len(title) < 2:
                continue

            seen.add(link)
            items.append({
                "title": title, "url": link, "poster": img,
                "type": self._determine_item_type(link, title), "_action": "details",
            })
        if items:
            log("ArabSeed: Strategy 3 found {} items".format(len(items)))
            return items

        # === Strategy 4: CSS Background Image Fallback ===
        log("ArabSeed: Using CSS background fallback extraction")
        for m in re.finditer(r'<(?:div|article|a)[^>]+style=["\'][^"\']*background-image:\s*url\(([^)]+)\)[^"\']*["\'][^>]*>(.*?)</(?:div|article|a)>', html, re.IGNORECASE | re.DOTALL):
            bg_url = m.group(1).strip("'\" ")
            block_html = m.group(2)

            href_m = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.IGNORECASE)
            if not href_m:
                continue
            link = self._full_url(href_m.group(1))
            if not link or link in seen:
                continue

            if any(x in link.lower() for x in ("/category/", "/page/", "/tag/", "/author/", "/feed/", "#", "javascript:")):
                continue

            title_m = (
                re.search(r'title=["\']([^"\']+)["\']', m.group(0), re.IGNORECASE) or
                re.search(r'<(?:h[1-4]|span|p)[^>]*>([^<]{2,})</', block_html, re.IGNORECASE)
            )
            if not title_m:
                continue
            title = self._clean_title(title_m.group(1))
            if not title or len(title) < 2:
                continue

            seen.add(link)
            items.append({
                "title": title, "url": link, "poster": self._full_url(bg_url),
                "type": self._determine_item_type(link, title), "_action": "details",
            })

        if items:
            log("ArabSeed: Strategy 4 found {} items".format(len(items)))
        else:
            log("ArabSeed: All strategies failed - no items found")
        return items

    def _server_priority(self, server_url):
        lowered = server_url.lower()
        if "reviewrate" in lowered or "reviewtech" in lowered:
            return 0
        if "vidmoly" in lowered:
            return 1
        if "downet.net" in lowered:
            return 2
        if "mxcontent.net" in lowered:
            return 3
        return 9

    def _server_name(self, server_url, label_hint=""):
        lowered = (server_url or "").lower()
        names = {
            "reviewrate": "عرب سيد", "reviewtech": "عرب سيد",
            "vidmoly": "VidMoly", "downet.net": "Downet (Direct)",
            "mxcontent.net": "MxContent", "streamwish": "StreamWish",
            "wishfast": "StreamWish", "filemoon": "FileMoon",
            "lulustream": "LuluStream", "mixdrop": "MixDrop",
            "dood": "DoodStream", "streamtape": "StreamTape",
            "vidguard": "VidGuard", "vgfplay": "VidGuard",
            "fastvid": "FastVid", "ok.ru": "OK.ru", "okru": "OK.ru",
            "uqload": "UqLoad", "streamruby": "StreamRuby",
            "hgcloud": "HGCloud", "filelions": "FileLions",
            "vidhide": "VidHide", "streamhide": "StreamHide",
            "govid": "GoVid", "savefiles": "SaveFiles",
        }
        for key, name in names.items():
            if key in lowered:
                return name
        if label_hint:
            return label_hint.strip()
        domain_match = re.search(r'https?://([^/]+)', server_url or "")
        return domain_match.group(1) if domain_match else "Server"

    def _extract_servers_from_watch_page(self, watch_html):
        """[PATCH AS4] Parse the watch page's static server list.

        Real structure on arabseed.rent / arabseed.wine:

            <ul id="watch">
              <li data-link="https://govid.live/play/..." class="active">
                <i class="fal fa-play"></i>
                <span>Vidsharing</span>
              </li>
              ...
            </ul>

        The server name lives inside a <span>, the URL inside data-link.
        On the current site variant there is no csrf_token / post_id AJAX
        endpoint, so this static list is the *only* server source -- the
        AJAX path simply returns nothing and the generic fallback would
        otherwise flatten every entry to "GoVid".

        These URLs are govid.live iframe embeds, not direct stream URLs,
        so they are marked ``type: "embed"``.
        """
        servers = []
        seen = set()
        if not watch_html:
            return servers

        # Narrow to the <ul id="watch"> block if present
        ul_match = re.search(
            r'<ul[^>]+id=["\']watch["\'][^>]*>(.*?)</ul>',
            watch_html, re.S | re.I
        )
        scope = ul_match.group(1) if ul_match else watch_html

        for m in re.finditer(
            r'<li[^>]+data-link=["\']([^"\']+)["\'][^>]*>(.*?)</li>',
            scope, re.S | re.I
        ):
            raw_url = m.group(1)
            url = self._decode_hidden_url(raw_url)
            if not url.startswith("http") or url in seen:
                continue
            if any(h in url for h in BLOCKED_HOSTS):
                continue

            inner = m.group(2)
            name_m = re.search(r'<span[^>]*>([^<]+)</span>', inner, re.I)
            if name_m:
                name = html_lib.unescape(name_m.group(1).strip())
            else:
                name = self._server_name(url, "سيرفر {}".format(len(servers) + 1))

            seen.add(url)
            servers.append({"name": name, "url": url, "type": "embed"})

        if servers:
            log("ArabSeed: watch page server list -> {} servers".format(len(servers)))
        return servers

    def _collect_ajax_servers(self, watch_html, watch_url):
        base_domain = self._get_base()
        try:
            host = re.search(r'https?://([^/]+)', base_domain).group(1)
            clear_cookies(host)
        except Exception:
            pass

        token = self._extract_first([
            r"csrf__token['\"]?\s*[:=]\s*['\"]([^'\"]+)",
            r"csrf_token['\"]?\s*[:=]\s*['\"]([^'\"]+)",
            r"csrfToken['\"]?\s*[:=]\s*['\"]([^'\"]+)",
            r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)',
            r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf_token',
            r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)',
            r'window\.\w*csrf\w*\s*=\s*["\']([^"\']+)',
            r'var\s+\w*csrf\w*\s*=\s*["\']([^"\']+)',
            r'csrf["\']\s*:\s*["\']([^"\']+)',
        ], watch_html)

        post_id = self._extract_first([
            r"psot_id['\"]?\s*[:=]\s*['\"]?(\d+)",
            r"post_id['\"]?\s*[:=]\s*['\"]?(\d+)",
            r"postID['\"]?\s*[:=]\s*['\"]?(\d+)",
            r"postId['\"]?\s*[:=]\s*['\"]?(\d+)",
            r'data-post-id=["\'](\d+)',
            r'data-post=["\'](\d+)',
            r'data-id=["\'](\d+)',
            r'name=["\']post_id["\'][^>]*value=["\'](\d+)',
            r'var\s+post_id\s*=\s*["\']?(\d+)',
            r'var\s+postId\s*=\s*["\']?(\d+)',
            r'"post_id"\s*:\s*"?(\d+)',
            r'"postId"\s*:\s*"?(\d+)',
            r'postid-(\d+)',
            r'\?p=(\d+)',
            r'post-(\d+)',
        ], watch_html)

        home_url = self._extract_first([
            r"main__obj\s*=\s*\{\s*'home__url':\s*'([^']+)'",
            r"home_url['\"]?\s*[:=]\s*['\"]([^'\"]+)",
            r"siteUrl['\"]?\s*[:=]\s*['\"]([^'\"]+)",
        ], watch_html) or base_domain

        if not token or not post_id:
            log("ArabSeed: Missing token={} post_id={}".format(bool(token), bool(post_id)))
            return []

        log("ArabSeed: AJAX token found, post_id={}".format(post_id))
        quality_url = urljoin(home_url, "get__quality__servers/")
        watch_server_url = urljoin(home_url, "get__watch__server/")
        results, seen, lock = [], set(), threading.Lock()

        def _cb(u):
            sep = "&" if "?" in u else "?"
            return "{}{}_cb={}{:04d}".format(u, sep, int(time.time() * 1000), random.randint(0, 9999))

        def fetch_row(rp, sid, rq, label):
            hdrs = {"X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Referer": watch_url}
            body, _ = fetch(_cb(watch_server_url),
                            post_data={"post_id": rp, "quality": rq, "server": sid, "csrf_token": token},
                            referer=watch_url, extra_headers=hdrs)
            if not body:
                return None
            try:
                d = json.loads(body)
            except Exception:
                return None
            if d.get("type") != "success" or not d.get("server"):
                return None
            u = self._decode_hidden_url(d.get("server", ""))
            if not u.startswith("http") or any(h in u for h in BLOCKED_HOSTS):
                return None
            return {"quality": rq, "url": u, "name": self._server_name(u, label)}

        def fetch_quality(q):
            local = []
            hdrs = {"X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Referer": watch_url}
            body, _ = fetch(_cb(quality_url),
                            post_data={"post_id": post_id, "quality": q, "csrf_token": token},
                            referer=watch_url, extra_headers=hdrs)
            if not body:
                return local
            try:
                d = json.loads(body)
            except Exception:
                return local
            if d.get("type") != "success":
                return local
            ds = self._decode_hidden_url(d.get("server", ""))
            if ds.startswith("http") and not any(h in ds for h in BLOCKED_HOSTS):
                local.append({"quality": q, "url": ds, "name": self._server_name(ds, "سيرفر عرب سيد")})
            rows = re.findall(
                r'<li[^>]+data-post="([^"]+)"[^>]+data-server="([^"]+)"[^>]+data-qu="([^"]+)"[^>]*>.*?<span>([^<]+)</span>',
                d.get("html", ""), re.S)
            if not rows:
                rows = re.findall(
                    r'<(?:li|div|a|button)[^>]+data-post="([^"]+)"[^>]+data-server="([^"]+)"[^>]+data-qu="([^"]+)"[^>]*>(.*?)</',
                    d.get("html", ""), re.S)
                rows = [(r[0], r[1], r[2], re.sub(r'<[^>]+>', '', r[3]).strip()) for r in rows]
            if rows:
                with ThreadPoolExecutor(max_workers=min(3, len(rows))) as ex:
                    for r in ex.map(lambda x: fetch_row(*x), rows):
                        if r:
                            local.append(r)
            return local

        with ThreadPoolExecutor(max_workers=3) as ex:
            for tier in ex.map(fetch_quality, ("1080", "720", "480")):
                for item in tier:
                    uk, nk = (item["quality"], item["url"]), (item["quality"], item["name"])
                    with lock:
                        if uk in seen or nk in seen:
                            continue
                        seen.add(uk); seen.add(nk)
                    results.append(item)

        if not results:
            log("ArabSeed: AJAX returned 0 servers")
        else:
            log("ArabSeed: AJAX returned {} servers".format(len(results)))
        results.sort(key=lambda i: (QUALITY_ORDER.get(i["quality"], 9), self._server_priority(i["url"]), i["name"]))
        return results

    def get_categories(self, mtype="movie"):
        # [PATCH AS3] Corrected to the paths the current site actually
        # serves (verified against the captured nav menu). The old
        # ``category/films/``, ``category/tv/foreign-series/`` etc.
        # paths 404 on arabseed.rent / arabseed.wine.
        base = self._get_base().rstrip("/")
        return [
            {"title": "🎬 كل الأفلام",       "url": base + "/افلام/",                          "type": "category", "_action": "category"},
            {"title": "🌍 أفلام أجنبي",      "url": base + "/category/افلام-اجنبي/",           "type": "category", "_action": "category"},
            {"title": "🌏 أفلام آسيوية",     "url": base + "/category/افلام-اسيوية/",          "type": "category", "_action": "category"},
            {"title": "🇮🇳 أفلام هندي",      "url": base + "/category/افلام-هندي/",            "type": "category", "_action": "category"},
            {"title": "🇹🇷 أفلام تركية",     "url": base + "/category/افلام-تركية/",           "type": "category", "_action": "category"},
            {"title": "🇸🇦 أفلام عربي",      "url": base + "/category/افلام-عربي/",            "type": "category", "_action": "category"},
            {"title": "📺 كل المسلسلات",     "url": base + "/مسلسلات/",                       "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات أجنبي",    "url": base + "/category/مسلسلات-اجنبي/",         "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات آسيوية",   "url": base + "/category/مسلسلات-اسيوية/",        "type": "category", "_action": "category"},
            {"title": "🇹🇷 مسلسلات تركية",   "url": base + "/category/مسلسلات-تركية/",         "type": "category", "_action": "category"},
            {"title": "🇮🇳 مسلسلات هندية",   "url": base + "/category/مسلسلات-هندية/",         "type": "category", "_action": "category"},
            {"title": "🇸🇦 مسلسلات عربي",    "url": base + "/category/مسلسلات-عربي/",          "type": "category", "_action": "category"},
            {"title": "🎙 مسلسلات مدبلجة",   "url": base + "/category/مسلسلات-مدبلجة/",        "type": "category", "_action": "category"},
            {"title": "🎭 أفلام انمي",       "url": base + "/category/افلام-انمي/",            "type": "category", "_action": "category"},
            {"title": "🎭 مسلسلات انمي",     "url": base + "/category/مسلسلات-انمي/",          "type": "category", "_action": "category"},
            {"title": "📺 برامج تلفزيونية",  "url": base + "/category/برامج-تلفزيونية/",       "type": "category", "_action": "category"},
            {"title": "🤼 عروض مصارعة",      "url": base + "/category/عروض-مصارعة/",           "type": "category", "_action": "category"},
            {"title": "🌙 رمضان 2026",       "url": base + "/category/مسلسلات-رمضان-2026/",    "type": "category", "_action": "category"},
        ]

    def get_category_items(self, url, page=1):
        # Ensure URL uses the active domain
        url = self._full_url(url)
        log("ArabSeed: get_category_items url={} page={}".format(url, page))
        html, _ = fetch(url, referer=self._get_base())
        if not html:
            log("ArabSeed: Failed to fetch: {}".format(url))
            return []
        if "just a moment" in html.lower() and ("cf-chl" in html.lower() or "challenge" in html.lower()):
            log("ArabSeed: Cloudflare challenge detected")
            return []
        items = self._extract_items_from_html(html)
        log("ArabSeed: Extracted {} items".format(len(items)))

        # ── [PATCH AS2] Pagination ────────────────────────────────────────
        # Two changes vs. the old code:
        #  1. The <a> regex is attribute-order-agnostic (the site always
        #     emits class first today, but the reverse order is common in
        #     WP themes and would silently kill pagination if it ever flips).
        #  2. We independently detect the CURRENT page from the WP-style
        #     `<span class="page-numbers current">N</span>` marker. That
        #     lets us reconstruct page/N+1 even if no rel="next" /
        #     .next link is present (and confirms the page is actually a
        #     paginated listing, not a stripped/soft-block response).
        pag_block_match = re.search(
            r'<div[^>]+class=["\'][^"\']*paginate[^"\']*["\'][^>]*>(.*?)</div>',
            html, re.S | re.I
        )
        pag_html = pag_block_match.group(1) if pag_block_match else html

        # Current page number
        current_page = None
        cur_match = re.search(
            r'<span[^>]+class=["\'][^"\']*page-numbers[^"\']*current[^"\']*["\'][^>]*>\s*(\d+)\s*</span>',
            pag_html, re.I
        )
        if cur_match:
            current_page = int(cur_match.group(1))

        # Next-page anchor — try both attribute orders, plus rel=next
        next_url_raw = None
        next_match = (
            re.search(r'<a[^>]+class=["\'][^"\']*\bnext\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', pag_html, re.I) or
            re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*\bnext\b[^"\']*["\']', pag_html, re.I) or
            re.search(r'<a[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', pag_html, re.I) or
            re.search(r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
        )
        if next_match:
            next_url_raw = next_match.group(1)

        # Highest page number shown in the pagination list (so we don't
        # build page/3 when the site only shows 1..2)
        max_page_shown = 0
        for m in re.finditer(r'/page/(\d+)/', pag_html):
            max_page_shown = max(max_page_shown, int(m.group(1)))

        next_url = None
        if next_url_raw:
            next_url = self._full_url(next_url_raw)
        elif current_page is not None and max_page_shown > current_page:
            base_no_page = re.sub(r'/page/\d+/?$', '/', url.rstrip('/') + '/')
            next_url = "{}/page/{}/".format(base_no_page.rstrip('/'),
                                            current_page + 1)

        if next_url and next_url.rstrip('/') != url.rstrip('/'):
            items.append({
                "title": "➡️ الصفحة التالية",
                "url": next_url,
                "type": "category",
                "_action": "category",
            })
        else:
            log("ArabSeed: no next page detected (current_page={} max_shown={})"
                .format(current_page, max_page_shown))

        return items

    def search(self, query, page=1):
        search_url = urljoin(self._get_base(), "?s=" + query.replace(" ", "+"))
        if page > 1:
            search_url = urljoin(self._get_base(), "page/{}/?s={}".format(page, query.replace(" ", "+")))
        log("ArabSeed: search url={}".format(search_url))
        html, _ = fetch(search_url, referer=self._get_base())
        if not html:
            return []
        return self._extract_items_from_html(html)

    def get_page(self, url, m_type=None):
        url = self._full_url(url)
        log("ArabSeed: get_page url={}".format(url))
        html, final_url = fetch(url, referer=self._get_base())
        if not html:
            return {"title": "Error", "servers": []}
        result = {"url": final_url or url, "title": "", "plot": "", "poster": "",
                  "rating": "", "year": "", "servers": [], "items": []}

        tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S) or re.search(r'og:title[^>]+content="([^"]+)"', html)
        if tm:
            result["title"] = self._clean_title(tm.group(1).split("-")[0])
        pm = re.search(r'og:image"[^>]+content="([^"]+)"', html)
        if pm:
            result["poster"] = self._full_url(pm.group(1))
        plm = re.search(r'name="description"[^>]+content="([^"]+)"', html)
        if plm:
            result["plot"] = html_lib.unescape(plm.group(1))
        ym = re.search(r'\(\s*(\d{4})\s*\)', result["title"])
        if ym:
            result["year"] = ym.group(1)

        is_series = (any(m in (final_url or url) for m in ("/series-", "-season-", "-episode-", "/selary/"))
                     or "مسلسل" in result["title"] or "الحلقة" in result["title"])

        base_url = (final_url or url).rstrip("/")
        watch_url = base_url + "/watch/"
        wm = re.search(r'href="([^"]+/watch/?)"', html)
        if wm:
            watch_url = self._full_url(wm.group(1))
        if not wm:
            wa = re.search(r'data-(?:watch|href|url)=["\']([^"\']+/watch[^"\']*)["\']', html, re.I)
            if wa:
                watch_url = self._full_url(wa.group(1))

        log("ArabSeed: watch_url={}".format(watch_url))
        watch_html, watch_final = fetch(watch_url, referer=final_url or url)
        if not watch_html:
            watch_html, watch_final = html, (final_url or url)

        # [PATCH AS4] Try the watch page's static server list first --
        # that's the only source on the current site variant (no csrf
        # token, no post_id AJAX endpoint, so _collect_ajax_servers
        # returns []). Reading the <span> labels next to each data-link
        # also gives proper names (Vidsharing, earnvids, vinovo, ...)
        # instead of the generic "GoVid" that _server_name() would
        # otherwise return for every entry.
        for server in self._extract_servers_from_watch_page(watch_html):
            result["servers"].append({
                "name": server["name"],
                "url": server["url"],
                "type": server["type"],
            })

        # Older variants still expose the AJAX endpoint; try it only
        # if the static list came back empty.
        if not result["servers"]:
            for server in self._collect_ajax_servers(watch_html, watch_final or watch_url):
                result["servers"].append({
                    "name": "[{}p] {}".format(server["quality"], server["name"]),
                    "url": server["url"], "type": "direct",
                })

        # Last-resort generic scraper
        if not result["servers"]:
            log("ArabSeed: static + AJAX both empty, using generic fallback")
            result["servers"] = self._extract_servers_fallback(
                watch_html, html, final_url or url
            )

        if is_series:
            seen_eps = set()
            cm = re.search(r'<ul[^>]+class=["\'][^"\']*episodes__list[^"\']*["\'][^>]*>(.*?)</ul>', html, re.S | re.I)
            if not cm:
                cm = re.search(r'<div[^>]+class=["\'][^"\']*(?:episodes|eps-list|all-eps)[^"\']*["\'][^>]*>(.*?)</div>', html, re.S | re.I)
            if cm:
                for em in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>.*?(?:الحلقة|Episode|EP|حلقة)\s*(\d+)', cm.group(1), re.S | re.I):
                    if em.group(1) in seen_eps:
                        continue
                    seen_eps.add(em.group(1))
                    result["items"].append({"title": "{} - الحلقة {}".format(result["title"], em.group(2)).strip(),
                                            "url": self._full_url(em.group(1)), "type": "episode", "_action": "details"})
            if not result["items"]:
                for ep_url, ep_title in re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]+title="([^"]+)"', html, re.S):
                    if ("الحلقة" not in ep_title and "حلقة" not in ep_title) or ep_url in seen_eps:
                        continue
                    if not any(x in ep_url for x in ("series-", "-season", "episode", "selary")):
                        continue
                    seen_eps.add(ep_url)
                    result["items"].append({"title": ep_title.strip(), "url": self._full_url(ep_url), "type": "episode", "_action": "details"})
        return result

    def _extract_servers_fallback(self, watch_html, main_html, page_url):
        """Fallback server extraction when AJAX fails."""
        servers, seen = [], set()
        source = watch_html or main_html or ""
        IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico")

        for attr in ("data-link", "data-url", "data-iframe", "data-src", "data-href", "data-server"):
            for m in re.finditer(attr + r'=["\']([^"\']+)["\']', source, re.I):
                u = self._decode_hidden_url(m.group(1))
                if not u.startswith("http") or u.lower().split("?", 1)[0].endswith(IMG_EXT):
                    continue
                if any(h in u for h in BLOCKED_HOSTS) or u in seen:
                    continue
                seen.add(u)
                servers.append({"name": self._server_name(u, "سيرفر {}".format(len(servers)+1)), "url": u, "type": "direct"})

        for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', source, re.I):
            u = self._full_url(m.group(1))
            if not u or u in seen or any(x in u.lower() for x in ("facebook.com", "twitter.com", "google.com", "youtube.com/embed", "disqus.com")):
                continue
            if any(h in u for h in BLOCKED_HOSTS):
                continue
            seen.add(u)
            servers.append({"name": self._server_name(u, "سيرفر {}".format(len(servers)+1)), "url": u, "type": "embed"})

        for m in re.finditer(r'<(?:source|video)[^>]+src=["\']([^"\']+\.(?:mp4|m3u8|mkv)[^"\']*)["\']', source, re.I):
            u = self._full_url(m.group(1))
            if not u or u in seen:
                continue
            seen.add(u)
            q = "1080p" if "1080" in u.lower() else ("720p" if "720" in u.lower() else ("480p" if "480" in u.lower() else "HD"))
            servers.append({"name": "Direct - {}".format(q), "url": u, "type": "direct"})

        for pat in (r'file\s*:\s*["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
                    r'source\s*:\s*["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
                    r'"url"\s*:\s*"([^"]+\.(?:mp4|m3u8)[^"]*)"'):
            for m in re.finditer(pat, source, re.I):
                u = self._full_url(m.group(1).replace("\\/", "/"))
                if not u or u in seen or any(h in u for h in BLOCKED_HOSTS):
                    continue
                seen.add(u)
                q = "1080p" if "1080" in u.lower() else ("720p" if "720" in u.lower() else ("480p" if "480" in u.lower() else "HD"))
                servers.append({"name": "Direct - {}".format(q), "url": u, "type": "direct"})

        host_re = re.compile(
            r'(https?://(?:www\.)?(?:streamtape|doodstream|dood\.|mixdrop|uqload|voe\.|'
            r'streamwish|filemoon|lulustream|ok\.ru|vidguard|fastvid|'
            r'reviewrate|reviewtech|vidmoly|downet\.net|mxcontent\.net|'
            r'savefiles|delucloud|sprintcdn|filelions|vidhide|streamhide|'
            r'govid|hgcloud|vidbom|upstream|streamruby|abstream)[^"\'>\s]+)', re.IGNORECASE)
        for m in host_re.finditer(source):
            u = m.group(1).replace("\\/", "/").replace("&amp;", "&")
            if u in seen or any(h in u for h in BLOCKED_HOSTS):
                continue
            seen.add(u)
            servers.append({"name": self._server_name(u, "سيرفر {}".format(len(servers)+1)), "url": u, "type": "embed"})

        log("ArabSeed: Fallback found {} servers".format(len(servers)))
        return servers

    def extract_stream(self, url):
        log("ArabSeed extract_stream: {}".format(url))

        referer = self._get_base()
        if "|" in url:
            parts = url.split("|", 1)
            url = parts[0]
            if "Referer=" in parts[1]:
                referer = parts[1].split("Referer=")[1].strip()

        if url.startswith("/"):
            url = urljoin(self._get_base(), url)

        # [PATCH AS5] govid.live wrapper chain -- play/ -> /e/{id}/ -> hex m3u8
        if "govid.live/play/" in url or "govid.live/e/" in url:
            stream = self._extract_govid_stream(url, referer)
            if stream:
                return stream, None, referer

        from .base import extract_stream as base_extract_stream
        return base_extract_stream(url)

    def _extract_govid_stream(self, play_url, referer):
        """Resolve a govid.live server URL down to its raw HLS stream.

        [PATCH AS5] The URLs ArabSeed hands out for every server (Vidsharing,
        earnvids, vinovo, doodstream, vidaraa, savefiles, streamtape, luluvdo,
        voe, ok) are all `govid.live/play/...` iframe wrappers, not streams.
        The real chain, confirmed against a Stream Recorder capture, is:

          1. GET  govid.live/play/=XXX
             -> <iframe src="govid.live/e/{id}/?pic=...">

          2. GET  govid.live/e/{id}/?pic=...
             -> JWPlayer setup with:
                  const Mohix = "<hex>";
                  function Gonalosr() { ... hex-decode ... }
                  sources: [{ file: Gonalosr(), type: "hls" }]

          3. Hex-decode Mohix -> the actual .m3u8 URL, which already carries
             the tokenbaseip + expire query params needed to fetch it.

        The base extractor can't resolve this because (a) it doesn't follow
        the /play/ -> /e/ hop, and (b) it has no notion of hex-obfuscated
        URLs. We do both here.
        """
        # ── Step 1: fetch the play page and find the /e/ iframe ──────────
        if "/e/" in play_url and "/play/" not in play_url:
            # Already on the embed page (retry / recursive call)
            embed_url = play_url
            embed_html, _ = fetch(embed_url, referer=referer)
        else:
            play_html, _ = fetch(play_url, referer=referer)
            if not play_html:
                return None

            iframe_m = re.search(
                r'<iframe[^>]+src=["\']([^"\']*govid\.live/e/[^"\']+)["\']',
                play_html, re.I
            )
            if not iframe_m:
                # The play page itself may already contain the hex string
                # (no intermediate /e/ hop) -- try to decode from here first.
                stream = self._hexdecode_govid_stream(play_html)
                if stream:
                    return stream
                return None

            embed_url = iframe_m.group(1)
            if embed_url.startswith("//"):
                embed_url = "https:" + embed_url
            elif embed_url.startswith("/"):
                embed_url = urljoin(play_url, embed_url)

            embed_html, _ = fetch(embed_url, referer=play_url)
            if not embed_html:
                return None

        # ── Step 2: pull the hex-encoded m3u8 out of the embed ───────────
        return self._hexdecode_govid_stream(embed_html)

    def _hexdecode_govid_stream(self, embed_html):
        """Find and hex-decode the govid m3u8 URL from an embed page.

        The variable name is obfuscated per-build (`Mohix` in the captured
        build, but it changes). Rather than hard-coding the name, we hunt
        for any 80+ char hex string that decodes to an http(s) URL. The
        decoded value is used verbatim -- its `tokenbaseip` and `expire`
        query params are already correct for the current session.
        """
        if not embed_html:
            return None

        # Preferred: read the value passed to `String.fromCharCode(parseInt(
        # ..., 16))` -- that's the exact loop the site uses. The variable
        # being iterated is whatever `const X = "<hex>"` assigned just above.
        # We just scan all long hex literals; decoding is cheap.
        seen = set()
        for m in re.finditer(r'["\']([0-9a-fA-F]{80,})["\']', embed_html):
            hex_str = m.group(1)
            if hex_str in seen:
                continue
            seen.add(hex_str)

            # Odd-length hex can't be bytes-decoded as-is
            if len(hex_str) % 2:
                continue

            try:
                decoded = bytes.fromhex(hex_str).decode("utf-8", errors="ignore")
            except Exception:
                continue

            if not decoded.startswith(("http://", "https://")):
                continue

            # Must be a real stream URL -- filter out stray decoded HTML
            # or obfuscated anti-scraper garbage.
            lowered = decoded.lower()
            if not any(marker in lowered for marker in (
                ".m3u8", ".mp4", ".mkv", "/video-", "/hls/", "/playlist"
            )):
                continue

            log("ArabSeed: decoded govid stream: {}".format(decoded[:120]))
            return decoded

        log("ArabSeed: no decodable stream URL found on govid embed page")
        return None