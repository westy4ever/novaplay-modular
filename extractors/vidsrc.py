# -*- coding: utf-8 -*-
"""
VidSrc Extractor
Uses TMDB API to list movies and TV shows, and resolves streams via tvembed.cc, 2embed, and VidSrc.win.
"""
import re
import urllib.parse
import json
from .base import (
    BaseExtractor, fetch, fetch_json, log, find_m3u8, find_m3u8_all, find_mp4,
    resolve_host, resolve_iframe_chain, get_last_quality_variants, 
    _correct_stream_url, _label_quality_variants, extract_stream_urls_from_text
)

class VidsrcExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.main_url = "https://vidsrc.win"
        self._tmdb_api_key = "46b050dc88e3c52e9d1bca4b656036e4"

    def _get_tmdb_key(self):
        try:
            from plugin_state import _get_config
            key = (_get_config("tmdb_api_key", "") or "").strip()
            return key if key else self._tmdb_api_key
        except Exception:
            return self._tmdb_api_key

    def _tmdb_request(self, path, params=None):
        api_key = self._get_tmdb_key()
        url = "https://api.themoviedb.org/3{}?api_key={}".format(path, api_key)
        if params:
            url += "&" + urllib.parse.urlencode(params)
        return fetch_json(url) or {}

    def get_categories(self, mtype="movie"):
        cats = []
        cats.append({"title": "🌐 Movies", "url": "", "type": "separator"})
        cats.append({"title": "📈 Trending Movies", "url": "vidsrc_movie_trending_week", "type": "category"})
        cats.append({"title": "🔥 Popular Movies", "url": "vidsrc_movie_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Movies", "url": "vidsrc_movie_top_rated", "type": "category"})
        cats.append({"title": "🎬 Upcoming Movies", "url": "vidsrc_movie_upcoming", "type": "category"})
        
        cats.append({"title": "📺 Series", "url": "", "type": "separator"})
        cats.append({"title": "📈 Trending Series This Week", "url": "vidsrc_series_trending_week", "type": "category"})
        cats.append({"title": "🔥 Popular Series", "url": "vidsrc_series_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Series", "url": "vidsrc_series_top_rated", "type": "category"})
        cats.append({"title": "📡 Upcoming Series", "url": "vidsrc_series_on_the_air", "type": "category"})
        
        cats.append({"title": "🎭 Series Genres", "url": "", "type": "separator"})
        cats.append({"title": "🎌 Anime Series", "url": "vidsrc_series_genre_16", "type": "category"})
        cats.append({"title": "🎭 Drama Series", "url": "vidsrc_series_genre_18", "type": "category"})
        cats.append({"title": "🔪 Crime Series", "url": "vidsrc_series_genre_80", "type": "category"})
        cats.append({"title": "📰 News Series", "url": "vidsrc_series_genre_10763", "type": "category"})
        cats.append({"title": "🧼 Soap Series", "url": "vidsrc_series_genre_10766", "type": "category"})
        cats.append({"title": "💥 Action & Adventure", "url": "vidsrc_series_genre_10759", "type": "category"})
        
        return cats

    def get_category_items(self, url, page=1):
        page_match = re.search(r'_page_(\d+)$', url)
        if page_match:
            page = int(page_match.group(1))
            url = url[:page_match.start()]

        parts = url.split("_")
        if len(parts) < 3: return []
        media_type = parts[1]
        action = parts[2]
        tmdb_media_type = "tv" if media_type == "series" else "movie"
        item_type = "series" if media_type == "series" else "movie"

        path = "/{}".format(tmdb_media_type)
        params = {"page": page, "language": "en-US"}

        if action == "genre":
            genre_id = parts[3] if len(parts) > 3 else ""
            path = "/discover/{}".format(tmdb_media_type)
            params["with_genres"] = genre_id
            params["sort_by"] = "popularity.desc"
        elif action == "trending":
            time_window = parts[3] if len(parts) > 3 else "week"
            path = "/trending/{}/{}".format(tmdb_media_type, time_window)
        elif action == "popular":
            path = "/{}/popular".format(tmdb_media_type)
        elif action == "top_rated":
            path = "/{}/top_rated".format(tmdb_media_type)
        elif action == "upcoming":
            path = "/movie/upcoming" if tmdb_media_type == "movie" else "/tv/on_the_air"
        elif action == "on_the_air":
            path = "/tv/on_the_air"
        else:
            path = "/{}/{}".format(tmdb_media_type, action)

        data = self._tmdb_request(path, params)
        items = []
        for r in data.get("results", []):
            title = r.get("title") or r.get("name") or "Unknown"
            date_field = r.get("release_date") or r.get("first_air_date") or ""
            year = date_field[:4] if date_field else ""
            items.append({
                "title": title,
                "poster": "https://image.tmdb.org/t/p/w342" + r.get("poster_path", "") if r.get("poster_path") else "",
                "url": "vidsrc_{}_{}".format(item_type, r.get("id")),
                "type": item_type,
                "year": year
            })

        total_pages = data.get("total_pages", 1)
        current_page = data.get("page", page)
        if current_page < total_pages:
            items.append({
                "title": "➡️ Page {} (Next)".format(current_page + 1),
                "url": "{}_page_{}".format(url, current_page + 1),
                "type": "category",
                "_action": "category"
            })
        return items

    def search(self, query, page=1):
        data = self._tmdb_request("/search/multi", {"query": query, "page": page})
        items = []
        for r in data.get("results", []):
            if r.get("media_type") in ("movie", "tv"):
                title = r.get("title") or r.get("name") or "Unknown"
                year = (r.get("release_date") or r.get("first_air_date") or "")[:4]
                item_type = "series" if r.get("media_type") == "tv" else "movie"
                items.append({
                    "title": title,
                    "poster": "https://image.tmdb.org/t/p/w342" + r.get("poster_path", "") if r.get("poster_path") else "",
                    "url": "vidsrc_{}_{}".format(item_type, r.get("id")),
                    "type": item_type,
                    "year": year
                })
        return items

    def get_page(self, url, m_type=None):
        if url.startswith("vidsrc_tv_") or url.startswith("vidsrc_series_"):
            tmdb_id = url.replace("vidsrc_tv_", "").replace("vidsrc_series_", "")
            data = self._tmdb_request("/tv/{}".format(tmdb_id))
            seasons = []
            for s in data.get("seasons", []):
                if s.get("season_number") == 0: continue
                seasons.append({
                    "title": s.get("name", "Season {}".format(s.get("season_number"))),
                    "url": "vidsrc_season_{}_{}".format(tmdb_id, s.get("season_number")),
                    "type": "season"
                })
            return {
                "title": data.get("name", "TV Show"),
                "plot": data.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + data.get("poster_path", "") if data.get("poster_path") else "",
                "servers": [],
                "items": seasons,
                "type": "series"
            }

        elif url.startswith("vidsrc_season_"):
            parts = url.split("_")
            tmdb_id = parts[2]
            season_num = parts[3]
            data = self._tmdb_request("/tv/{}/season/{}".format(tmdb_id, season_num))
            episodes = []
            for ep in data.get("episodes", []):
                episodes.append({
                    "title": "S{:02d}E{:02d}: {}".format(int(season_num), ep.get("episode_number"), ep.get("name")),
                    "url": "vidsrc_episode_{}_{}_{}".format(tmdb_id, season_num, ep.get("episode_number")),
                    "type": "episode"
                })
            return {
                "title": data.get("name", "Season {}".format(season_num)),
                "plot": data.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + data.get("poster_path", "") if data.get("poster_path") else "",
                "servers": [],
                "items": episodes,
                "type": "season"
            }

        elif url.startswith("vidsrc_episode_"):
            parts = url.split("_")
            tmdb_id = parts[2]
            season_num = parts[3]
            ep_num = parts[4]

            servers = [
                {"name": "Server 1 (tvembed)", "url": "https://tvembed.cc/tv/{}/{}/{}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
                {"name": "Server 2 (2Embed)", "url": "https://www.2embed.skin/embedtv/{}&s={}&e={}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
                {"name": "Server 3 (VidSrc.win)", "url": "https://vidsrc.win/watch/{}?s={}&e={}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
            ]

            ep_data = self._tmdb_request("/tv/{}/season/{}/episode/{}".format(tmdb_id, season_num, ep_num))
            return {
                "title": "S{:02d}E{:02d}: {}".format(int(season_num), int(ep_num), ep_data.get("name", "Episode")),
                "plot": ep_data.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + ep_data.get("still_path", "") if ep_data.get("still_path") else "",
                "servers": servers,
                "items": [],
                "type": "episode"
            }

        elif url.startswith("vidsrc_movie_"):
            tmdb_id = url.replace("vidsrc_movie_", "")
            servers = [
                {"name": "Server 1 (tvembed)", "url": "https://tvembed.cc/movie/{}".format(tmdb_id), "quality": "HD"},
                {"name": "Server 2 (2Embed)", "url": "https://www.2embed.skin/embed/{}".format(tmdb_id), "quality": "HD"},
                {"name": "Server 3 (VidSrc.win)", "url": "https://vidsrc.win/watch/{}".format(tmdb_id), "quality": "HD"},
                {"name": "Server 4 (SuperFlix)", "url": "https://superflixapi.sbs/filme/{}".format(tmdb_id), "quality": "HD"},
                {"name": "Server 5 (ZXC Stream)", "url": "https://zxcstream.xyz/player/movie/{}".format(tmdb_id), "quality": "HD"},
            ]

            movie_data = self._tmdb_request("/movie/{}".format(tmdb_id))
            return {
                "title": movie_data.get("title") or movie_data.get("name") or "Movie",
                "plot": movie_data.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + movie_data.get("poster_path", "") if movie_data.get("poster_path") else "",
                "servers": servers,
                "items": [],
                "type": "movie"
            }

        return None

    def extract_stream(self, url):
        log("VidSrc: resolving embed {}".format(url))
        
        # ─── NEW: Try direct resolver from base.py first ──────────────────
        # This handles vidcore.io, superflixapi, zxcstream, etc.
        result = resolve_host(url)
        if result:
            log("VidSrc: resolve_host returned: {}".format(result[:100]))
            # Determine quality
            quality = "HD"
            if "1080" in result.lower() or "fhd" in result.lower():
                quality = "1080p"
            elif "720" in result.lower() or "hd" in result.lower():
                quality = "720p"
            variants = []
            return result, quality, url, variants
        
        # ─── Fallback: scan HTML directly ──────────────────────────────────
        html, _ = fetch(url, referer=self.main_url)
        if html:
            stream, label, ref, variants = self._extract_stream_from_html(html, url)
            if stream:
                return stream, label, ref, variants
        
        # ─── Final fallback: try iframe chain ─────────────────────────────
        stream, _ = resolve_iframe_chain(url, referer=self.main_url)
        if stream:
            quality = "HD"
            if "1080" in stream.lower() or "fhd" in stream.lower():
                quality = "1080p"
            elif "720" in stream.lower() or "hd" in stream.lower():
                quality = "720p"
            return stream, quality, url, []
        
        return super().extract_stream(url)

    def _decode_proxy_url(self, url):
        if not url: return url
        temp_url = url
        for _ in range(3):
            if "%" not in temp_url: break
            try: temp_url = urllib.parse.unquote(temp_url)
            except: break
        match = re.search(r'(https?://[^\s\'"<>\\]+\.(?:mp4|m3u8|mkv|avi)(?:[^\s\'"<>\\]*))', temp_url, re.I)
        return match.group(1) if match else temp_url

    def _extract_stream_from_html(self, html, base_url):
        if not html: return None, "", base_url, []

        all_streams = []
        seen_urls = set()

        def process_url(u):
            if not u: return
            decoded = self._decode_proxy_url(u)
            lower_decoded = decoded.lower().split("?")[0]
            
            # 1. If it's a direct media file
            if lower_decoded.endswith((".mp4", ".m3u8", ".ts")):
                if decoded not in seen_urls:
                    seen_urls.add(decoded)
                    all_streams.append(decoded)
            
            # 2. If it's an iframe, resolve it using base.py
            else:
                stream_url = resolve_host(decoded)
                if stream_url:
                    stream_url = self._decode_proxy_url(stream_url)
                    if stream_url not in seen_urls:
                        seen_urls.add(stream_url)
                        all_streams.append(stream_url)

        # ─── NEW: Look for __NEXT_DATA__ (Next.js/React apps) ────────────
        next_data_match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if next_data_match:
            try:
                data = json.loads(next_data_match.group(1))
                # Recursively search for stream URLs in the data
                stream_urls = self._find_streams_in_json(data)
                for stream_url in stream_urls:
                    if stream_url not in seen_urls:
                        seen_urls.add(stream_url)
                        all_streams.append(stream_url)
            except json.JSONDecodeError:
                pass

        # ─── NEW: Look for SuperFlix API tokens ────────────────────────────
        page_token_match = re.search(r'var PAGE_TOKEN\s*=\s*"([^"]+)"', html)
        csrf_token_match = re.search(r'var CSRF_TOKEN\s*=\s*"([^"]+)"', html)
        api_url_match = re.search(r'var API_URL_SOURCE\s*=\s*"([^"]+)"', html)
        content_id_match = re.search(r'var INITIAL_CONTENT_ID\s*=\s*(\d+)', html)
        
        if page_token_match and content_id_match:
            # Try to call the SuperFlix API
            page_token = page_token_match.group(1)
            csrf_token = csrf_token_match.group(1) if csrf_token_match else ""
            api_source = api_url_match.group(1) if api_url_match else "/player/source"
            content_id = content_id_match.group(1)
            
            # Parse the base URL
            parsed = urllib.parse.urlparse(base_url)
            api_url = "https://" + parsed.netloc + api_source
            
            data = {
                "video_id": content_id,
                "page_token": page_token,
                "host": parsed.netloc,
                "site": parsed.netloc,
                "_token": csrf_token,
            }
            
            headers = {
                "Referer": base_url,
                "Origin": "https://" + parsed.netloc,
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            
            if csrf_token:
                headers["X-CSRF-TOKEN"] = csrf_token
            
            api_response, _ = fetch(api_url, referer=base_url, extra_headers=headers, post_data=data)
            if api_response:
                try:
                    result = json.loads(api_response)
                    if result and result.get('data') and result['data'].get('video_url'):
                        stream_url = result['data']['video_url']
                        if stream_url not in seen_urls:
                            seen_urls.add(stream_url)
                            all_streams.append(stream_url)
                except json.JSONDecodeError:
                    # Try regex on the response
                    for url in extract_stream_urls_from_text(api_response):
                        if url not in seen_urls:
                            seen_urls.add(url)
                            all_streams.append(url)

        # ─── Scan HTML for all media URLs ──────────────────────────────────
        patterns = [
            r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8|ts)[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+proxy[^\s"\'<>]+)',
            r'(https?://[^\s"\'<>]+workers\.dev[^\s"\'<>]+)',
            r'<iframe[^>]+src=["\']([^"\']+)["\']',
            r'"url"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
            r'"file"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
            r'"src"\s*:\s*"([^"]+\.(?:m3u8|mp4)[^"]*)"',
            r'"video_url"\s*:\s*"([^"]+)"',
            r'"stream"\s*:\s*"([^"]+)"',
        ]
        
        for pattern in patterns:
            for match in re.findall(pattern, html, re.I):
                process_url(match)

        # ─── Find all iframes and fetch their sub-html ────────────────────
        iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for iframe in iframes:
            if iframe.startswith("//"): iframe = "https:" + iframe
            elif not iframe.startswith("http"): iframe = urllib.parse.urljoin(base_url, iframe)
            if any(x in iframe.lower() for x in ("ads", "adserver", "popup", "youtube", "google", "recaptcha")):
                continue

            process_url(iframe)

            sub_html, _ = fetch(iframe, referer=base_url)
            if sub_html:
                for pattern in patterns:
                    for match in re.findall(pattern, sub_html, re.I):
                        process_url(match)

        if not all_streams:
            return None, "", base_url, []

        labeled_variants = _label_quality_variants(all_streams)
        if not labeled_variants:
            return None, "", base_url, []

        main_label, main_url = labeled_variants[0]
        variants = labeled_variants[1:]
        return main_url, main_label, base_url, variants

    def _find_streams_in_json(self, obj, path=""):
        """Recursively search for stream URLs in JSON data."""
        urls = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ['url', 'src', 'source', 'video', 'stream', 'playlist', 'file', 'video_url', 'stream_url']:
                    if isinstance(value, str) and ('.m3u8' in value or '.mp4' in value or '.ts' in value):
                        urls.append(value)
                elif isinstance(value, (dict, list)):
                    urls.extend(self._find_streams_in_json(value, path + '.' + key))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                urls.extend(self._find_streams_in_json(item, path + '[' + str(i) + ']'))
        return urls