# -*- coding: utf-8 -*-
"""
IMDB.su Extractor
Uses TMDB API to list movies and TV shows, and resolves streams via imdb.su embeds.
"""
import re
import urllib.parse
from .base import (
    BaseExtractor, fetch, fetch_json, log, find_m3u8, find_m3u8_all,
    resolve_host, resolve_iframe_chain, resolve_generic_embed, get_last_quality_variants, 
    _correct_stream_url, _label_quality_variants
)

class ImdbSuExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.main_url = "https://imdb.su"
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
        cats.append({"title": "📈 Trending Movies", "url": "imdb_su_movie_trending_week", "type": "category"})
        cats.append({"title": "🔥 Popular Movies", "url": "imdb_su_movie_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Movies", "url": "imdb_su_movie_top_rated", "type": "category"})
        
        cats.append({"title": "📺 Series", "url": "", "type": "separator"})
        cats.append({"title": "📈 Trending Series This Week", "url": "imdb_su_series_trending_week", "type": "category"})
        cats.append({"title": "🔥 Popular Series", "url": "imdb_su_series_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Series", "url": "imdb_su_series_top_rated", "type": "category"})
        
        return cats

    def get_category_items(self, url, page=1):
        page_match = re.search(r'_page_(\d+)$', url)
        if page_match:
            page = int(page_match.group(1))
            url = url[:page_match.start()]

        parts = url.split("_", 3)          # [PATCH 83] keep "top_rated" / "trending_week" whole
        if len(parts) < 4: return []
        media_type = parts[2]
        action = parts[3]
        tmdb_media_type = "tv" if media_type == "series" else "movie"
        item_type = "series" if media_type == "series" else "movie"

        path = "/{}".format(tmdb_media_type)
        params = {"page": page, "language": "en-US"}

        if action == "popular":
            path = "/{}/popular".format(tmdb_media_type)
        elif action == "top_rated":
            path = "/{}/top_rated".format(tmdb_media_type)
        elif action == "trending_week":
            path = "/trending/{}/week".format(tmdb_media_type)
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
                "url": "imdb_su_{}_{}".format(item_type, r.get("id")),
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
                    "url": "imdb_su_{}_{}".format(item_type, r.get("id")),
                    "type": item_type,
                    "year": year
                })
        return items

    def get_page(self, url, m_type=None):
        if url.startswith("imdb_su_tv_") or url.startswith("imdb_su_series_"):
            tmdb_id = url.replace("imdb_su_tv_", "").replace("imdb_su_series_", "")
            data = self._tmdb_request("/tv/{}".format(tmdb_id))
            seasons = []
            for s in data.get("seasons", []):
                if s.get("season_number") == 0: continue
                seasons.append({
                    "title": s.get("name", "Season {}".format(s.get("season_number"))),
                    "url": "imdb_su_season_{}_{}".format(tmdb_id, s.get("season_number")),
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

        elif url.startswith("imdb_su_season_"):
            parts = url.split("_")
            tmdb_id = parts[3]
            season_num = parts[4]
            data = self._tmdb_request("/tv/{}/season/{}".format(tmdb_id, season_num))
            episodes = []
            for ep in data.get("episodes", []):
                episodes.append({
                    "title": "S{:02d}E{:02d}: {}".format(int(season_num), ep.get("episode_number"), ep.get("name")),
                    "url": "imdb_su_episode_{}_{}_{}".format(tmdb_id, season_num, ep.get("episode_number")),
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

        elif url.startswith("imdb_su_episode_"):
            parts = url.split("_")
            tmdb_id = parts[3]
            season_num = parts[4]
            ep_num = parts[5]

            servers = [
                {"name": "IMDB.su", "url": "https://imdb.su/embed/tv/{}/{}/{}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
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

        elif url.startswith("imdb_su_movie_"):
            tmdb_id = url.replace("imdb_su_movie_", "")
            servers = [
                {"name": "IMDB.su", "url": "https://imdb.su/embed/movie/{}".format(tmdb_id), "quality": "HD"},
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
        log("IMDB.su: resolving embed {}".format(url))
        html, _ = fetch(url, referer=self.main_url)
        if html:
            stream, label, ref, variants = self._extract_stream_from_html(html, url)
            if stream:
                return stream, label, ref, variants
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
            
            if lower_decoded.endswith((".mp4", ".m3u8")):
                if decoded not in seen_urls:
                    seen_urls.add(decoded)
                    all_streams.append(decoded)
            else:
                stream_url = resolve_host(decoded)
                if stream_url:
                    stream_url = self._decode_proxy_url(stream_url)
                    if stream_url not in seen_urls:
                        seen_urls.add(stream_url)
                        all_streams.append(stream_url)

        for pattern in [
            r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8)[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+proxy[^\s"\'<>]+)',
            r'(https?://[^\s"\'<>]+workers\.dev[^\s"\'<>]+)',
            r'<iframe[^>]+src=["\']([^"\']+)["\']'
        ]:
            for match in re.findall(pattern, html, re.I):
                process_url(match)

        iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for iframe in iframes:
            if iframe.startswith("//"): iframe = "https:" + iframe
            elif not iframe.startswith("http"): iframe = urllib.parse.urljoin(base_url, iframe)
            if any(x in iframe.lower() for x in ("ads", "adserver", "popup", "youtube", "google")): continue

            process_url(iframe)

            sub_html, _ = fetch(iframe, referer=base_url)
            if sub_html:
                for pattern in [
                    r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8)[^\s"\'<>]*)',
                    r'(https?://[^\s"\'<>]+proxy[^\s"\'<>]+)',
                    r'(https?://[^\s"\'<>]+workers\.dev[^\s"\'<>]+)',
                    r'<iframe[^>]+src=["\']([^"\']+)["\']'
                ]:
                    for match in re.findall(pattern, sub_html, re.I):
                        process_url(match)

        if not all_streams: return None, "", base_url, []

        labeled_variants = _label_quality_variants(all_streams)
        if not labeled_variants: return None, "", base_url, []

        main_label, main_url = labeled_variants[0]
        variants = labeled_variants[1:]
        return main_url, main_label, base_url, variants