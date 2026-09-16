# -*- coding: utf-8 -*-
"""
YTS (YIFY) Extractor — Updated for en.yts.lu / en.yify.sc
=========================================================
Now serves REAL torrent streams via Torrentio (same backend the site uses),
in addition to the original 2Embed/VidSrc embed fallbacks.

  * TMDB catalog (movies + TV — full coverage matching the site sidebar)
  * Torrent streams via Torrentio API → magnets (plugin's _bgTorrServerMagnet
    handles playback, identical to the Torrentio source)
  * 2Embed / VidSrc embeds kept as no-TorrServer fallback
  * Old history URLs (yts_movie_<tmdb_digits>) still resolve via embeds

Mirrors extractors/torrentio.py's magnet builder exactly so both sources
share identical playback behavior.

[PATCH 34] word-boundary CAM/TS detection; ?s= embed URLs; seeders/size
[PATCH 40] quality tag preserves the ACTUAL source word (HDTS not CAM)
[PATCH 44/47] Streaming sections (flat Movies/Series pairs per provider)
[PATCH 45/47] multi-word category actions rejoined (now_playing etc.)
[PATCH 47] collection IDs verified: LOTR 119, Pirates 295, Toy Story 10194,
  Mission Impossible 87359, Shrek 2150, Despicable Me 86066, Avatar 87096
[PATCH 55] Studio Ghibli company ID corrected 287 → 10342 (287 matched
  nothing); all 11 studio IDs batch-verified live (83–3124 movies each).
  Collection trio re-verified: LOTR 120→119, Pirates 259416→295,
  Toy Story 101931→10194.
"""
import re
import urllib.parse
from .base import (
    BaseExtractor, fetch, fetch_json, log, find_m3u8, find_m3u8_all,
    resolve_host, get_last_quality_variants, _correct_stream_url,
    _label_quality_variants
)


class YTSExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.main_url = "https://en.yts.lu"
        self.base_url = "https://en.yts.lu"
        self.yify_url = "https://en.yify.sc"
        # Torrentio backend — matches extractors/torrentio.py
        self.torr_base = "https://torrentio.strem.fun"
        self._tmdb_api_key = "46b050dc88e3c52e9d1bca4b656036e4"

        self.embed_sources = [
            {"name": "2Embed.cc", "url": "https://www.2embed.cc/embed", "api": "https://api.2embed.cc"},
            {"name": "2Embed.skin", "url": "https://www.2embed.skin/embed", "api": "https://api.2embed.cc"},
            {"name": "VidSrc.mov", "url": "https://vidsrc.mov/embed"},
            {"name": "VidSrc.to", "url": "https://vidsrc.to/embed"},
        ]
        self.twoembed_api = "https://api.2embed.cc"
        self.yts_domains = [
            "en.yts.lu", "yts.lu", "en.yify.sc", "yify.sc", "yts.mx", "en.yts.mx"
        ]

    # ═══ TMDB helpers ══════════════════════════════════════════════════

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

    def _twoembed_api_request(self, endpoint, params=None):
        url = "{}{}".format(self.twoembed_api, endpoint)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return fetch_json(url) or {}

    # ═══ Torrent / magnet helpers (mirror torrentio.py exactly) ══════

    def _clean_magnet_name(self, name):
        """Remove emojis, newlines, and control characters from torrent names"""
        if not name:
            return ""
        name = re.sub(r'[\U00010000-\U0010ffff]', '', name)
        name = re.sub(r'[\r\n\t]+', ' ', name)
        return re.sub(r'\s+', ' ', name).strip()

    def _build_magnet(self, info_hash, name, file_idx=None):
        clean_name = self._clean_magnet_name(name)
        xt = 'urn:btih:%s' % info_hash
        params = []
        if clean_name:
            params.append(('dn', clean_name))
        if file_idx is not None:
            params.append(('ix', str(file_idx)))
        params.append(('tr', 'udp://tracker.opentrackr.org:1337/announce'))
        params.append(('tr', 'udp://tracker.openbittorrent.com:6969/announce'))
        qs = urllib.parse.urlencode(params) if params else ''
        return 'magnet:?xt=%s%s%s' % (xt, '&' if qs else '', qs)

    def _extract_quality(self, title):
        t = (title or "").upper()
        q = "HD"
        if '2160P' in t or '4K' in t:
            q = '2160p'
        elif '1080P' in t:
            q = '1080p'
        elif '720P' in t:
            q = '720p'
        elif '480P' in t:
            q = '480p'
        # [PATCH 40] preserve the ACTUAL source tag from the name —
        # "1080p HDTS" instead of a generic "1080p CAM". Longest-first
        # alternation: HDTS beats TS, WEB-DL beats WEB. Word boundaries
        # keep HITS/ARTS/BITS from matching.
        m = re.search(r'\b(HD-?CAM|TELESYNC|HDTS|WEB-DL|WEBRIP|BLURAY|BRRIP|BDRIP|HDRIP|CAM|WEB|TS|DVD)\b', t)
        if m:
            q += " " + m.group(0)
        return q

    def _imdb_id(self, tmdb_id, mtype="movie"):
        """TMDB id → imdb_id (tt...) via external_ids endpoint."""
        seg = "tv" if mtype in ("tv", "series", "season", "episode") else "movie"
        try:
            data = self._tmdb_request("/{}/{}/external_ids".format(seg, tmdb_id))
            imdb = str(data.get("imdb_id") or "")
            return imdb if imdb.startswith("tt") else ""
        except Exception:
            return ""

    def _torr_servers(self, imdb_id, season=None, episode=None, max_n=40):
        """Query Torrentio for streams → magnet server dicts.
        Same backend en.yts.lu / en.yify.sc use; same data as Torrentio source.
        Plugin's _bgTorrServerMagnet handles magnet→TorrServer playback."""
        if not imdb_id or not imdb_id.startswith("tt"):
            return []
        if season is not None:
            url = "{}/stream/series/{}:{}:{}.json".format(
                self.torr_base, imdb_id, int(season), int(episode or 1))
        else:
            url = "{}/stream/movie/{}.json".format(self.torr_base, imdb_id)
        try:
            data = fetch_json(url) or {}
        except Exception:
            return []
        servers, seen = [], set()
        for s in data.get("streams", []) or []:
            h = str(s.get("infoHash") or "")
            if not h or len(h) not in (32, 40) or h.lower() in seen:
                continue
            seen.add(h.lower())
            title = str(s.get("title") or s.get("name") or "Torrent")
            magnet = self._build_magnet(h, title, s.get("fileIdx"))
            # [PATCH 34c] sync with torrentio's PATCH 30d — seeders/size
            # from the title (👤 N / N GB) → StreamList columns
            seeds_m = re.search(r'👤\s*(\d+)', title)
            size_m = re.search(r'(\d+(?:[.,]\d+)?\s*(?:GB|MB))', title, re.I)
            servers.append({
                "name": title,
                "url": magnet,
                "quality": self._extract_quality(title),
                "seeders": seeds_m.group(1) if seeds_m else "",
                "size": size_m.group(1) if size_m else "",
            })
            if len(servers) >= max_n:
                break
        return servers

    # ═══ Categories (movies + TV — matches the site sidebar) ══════════

    def get_categories(self, mtype="movie"):
        """Return combined Movies + TV categories (plugin calls with no arg)."""

        # ─── Movies ──────────────────────────────────────────────
        movie_genres = [
            ("💥 Action", 28), ("🗺️ Adventure", 12), ("🎨 Animation", 16),
            ("😂 Comedy", 35), ("🔪 Crime", 80), ("📹 Documentary", 99),
            ("🎭 Drama", 18), ("👨‍👩‍👧 Family", 10751), ("🧙 Fantasy", 14),
            ("🏛️ History", 36), ("👻 Horror", 27), ("🎵 Music", 10402),
            ("🕵️ Mystery", 9648), ("💕 Romance", 10749), ("🚀 Sci-Fi", 878),
            ("😱 Thriller", 53), ("⚔️ War", 10752), ("🤠 Western", 37),
        ]
        # [PATCH 55] Studio Ghibli 287 → 10342 (287 matched nothing);
        # all 11 IDs batch-verified live against discover/company.
        movie_studios = [
            ("🦸 Marvel Studios", 420), ("🪄 Pixar", 3), ("🐉 DreamWorks", 521),
            ("🏰 Walt Disney", 2), ("🎞️ Warner Bros", 174), ("🌐 Universal", 33),
            ("🎭 A24", 41077), ("🍃 Studio Ghibli", 10342), ("🐲 Legendary", 923),
            ("🔥 Lionsgate", 35), ("😱 Blumhouse", 3172),
        ]
        # [PATCH 47/55] All 19 collection IDs verified live:
        # LOTR 119, Pirates 295, Toy Story 10194 (55); MI 87359, Shrek
        # 2150, Despicable Me 86066, Avatar 87096 (47); rest confirmed
        # by batch test.
        movie_collections = [
            ("🦸 The Avengers", 86311), ("⚡ Harry Potter", 1241), ("🌌 Star Wars", 10),
            ("🕴️ James Bond 007", 645), ("🏎️ Fast & Furious", 9485), ("💍 Lord of the Rings", 119),
            ("🧝 The Hobbit", 121938), ("🦖 Jurassic Park", 328), ("🏹 Hunger Games", 131635),
            ("🏴‍☠️ Pirates Caribbean", 295), ("🕵️ Mission Impossible", 87359), ("🔫 John Wick", 404609),
            ("🦇 Dark Knight", 263), ("🕶️ The Matrix", 2344), ("🧸 Toy Story", 10194),
            ("🟢 Shrek", 2150), ("🍌 Despicable Me", 86066), ("🌊 Avatar", 87096),
            ("🧬 X-Men", 748), ("👽 Alien", 8091),
        ]
        years = [
            ("🏆 Best 2025", 2025), ("🏆 Best 2024", 2024), ("🏆 Best 2023", 2023),
            ("🏆 Best 2022", 2022), ("🏆 Best 2021", 2021), ("🏆 Best 2020", 2020),
        ]

        # TV genres — TMDB TV genre IDs (matching the en.yts.lu/en.yify.sc sidebar)
        tv_genres = [
            ("🗺️ Action & Adventure", 10759), ("🎨 Animation", 16),
            ("😂 Comedy", 35), ("🔪 Crime", 80), ("📹 Documentary", 99),
            ("🎭 Drama", 18), ("👨‍👩‍👧 Family", 10751), ("🕵️ Mystery", 9648),
            ("🚀 Sci-Fi & Fantasy", 10765), ("⚔️ War & Politics", 10768),
            ("🤠 Western", 37),
        ]

        cats = []

        # ─── Movies section ─────────────────────────────────────
        cats.append({"title": "🎬 Movies", "url": "", "type": "separator"})
        cats.append({"title": "🔥 Popular Movies", "url": "yts_movie_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Movies", "url": "yts_movie_top_rated", "type": "category"})
        cats.append({"title": "🎬 Now Playing", "url": "yts_movie_now_playing", "type": "category"})
        cats.append({"title": "📅 Upcoming", "url": "yts_movie_upcoming", "type": "category"})
        cats.append({"title": "📈 This Week", "url": "yts_movie_trending_week", "type": "category"})
        cats.append({"title": "⚡ Today", "url": "yts_movie_trending_day", "type": "category"})

        cats.append({"title": "🌐 Movie Genres", "url": "", "type": "separator"})
        for title, gid in movie_genres:
            cats.append({"title": title, "url": "yts_movie_genre_{}".format(gid), "type": "category"})

        cats.append({"title": "Studios", "url": "", "type": "separator"})
        for title, sid in movie_studios:
            cats.append({"title": title, "url": "yts_movie_studio_{}".format(sid), "type": "category"})

        cats.append({"title": "Collections", "url": "", "type": "separator"})
        for title, cid in movie_collections:
            cats.append({"title": title, "url": "yts_movie_collection_{}".format(cid), "type": "category"})

        cats.append({"title": "Best Of Movies", "url": "", "type": "separator"})
        for title, yr in years:
            cats.append({"title": title, "url": "yts_movie_year_{}".format(yr), "type": "category"})

        # ─── TV Shows section ───────────────────────────────────
        cats.append({"title": "📺 TV Shows", "url": "", "type": "separator"})
        cats.append({"title": "🔥 Popular Series", "url": "yts_series_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Series", "url": "yts_series_top_rated", "type": "category"})
        cats.append({"title": "📡 On Air", "url": "yts_series_on_the_air", "type": "category"})
        cats.append({"title": "📺 Airing Today", "url": "yts_series_airing_today", "type": "category"})
        cats.append({"title": "📈 Trending Series", "url": "yts_series_trending_week", "type": "category"})
        cats.append({"title": "⚡ Trending Today", "url": "yts_series_trending_day", "type": "category"})

        cats.append({"title": "🌐 TV Genres", "url": "", "type": "separator"})
        for title, gid in tv_genres:
            cats.append({"title": title, "url": "yts_series_genre_{}".format(gid), "type": "category"})

        # ─── Streaming section ─────────────────────────────────
        # [PATCH 44/47] flat paired entries per provider. The two-level
        # chooser never fired: category-type items route to
        # get_category_items (not get_page), and the chooser's browse
        # URLs collided with the yts_movie_/yts_series_ detail prefixes.
        streaming = [
            ("🔴 Netflix", 8), ("🔵 Prime Video", 9), ("🏰 Disney+", 337),
            ("🟪 Max", 1899), ("💚 Hulu", 15), ("🍎 Apple TV+", 350),
        ]
        cats.append({"title": "Streaming", "url": "", "type": "separator"})
        for title, pid in streaming:
            cats.append({"title": "🎬 " + title + " — Movies", "url": "yts_movie_provider_{}".format(pid), "type": "category"})
            cats.append({"title": "📺 " + title + " — Series", "url": "yts_series_provider_{}".format(pid), "type": "category"})

        cats.append({"title": "Best Of TV", "url": "", "type": "separator"})
        for title, yr in years:
            cats.append({"title": title, "url": "yts_series_year_{}".format(yr), "type": "category"})

        return cats

    def get_category_items(self, url, page=1):
        """Get category items with full pagination support."""
        log("YTS: get_category_items url={} page={}".format(url, page))

        page_match = re.search(r'_page_(\d+)$', url)
        if page_match:
            page = int(page_match.group(1))
            url = url[:page_match.start()]

        parts = url.split("_")
        if len(parts) < 3:
            return []

        media_type = parts[1]
        action = parts[2]
        # [PATCH 45/47] multi-word actions got split by the "_" parser —
        # "now_playing"→"now", "top_rated"→"top", "on_the_air"→"on",
        # "airing_today"→"airing" — all built truncated TMDB paths → 404.
        # Rejoin when the full tail matches a known multi-word action.
        if action in ("now", "top", "on", "airing"):
            _full = "_".join(parts[2:])
            if _full in ("now_playing", "top_rated", "on_the_air", "airing_today"):
                action = _full

        tmdb_media_type = "tv" if media_type == "series" else "movie"
        item_type = "series" if media_type == "series" else "movie"

        path = "/{}".format(tmdb_media_type)
        params = {"page": page, "language": "en-US"}

        if action == "genre":
            genre_id = parts[3]
            path = "/discover/{}".format(tmdb_media_type)
            params["with_genres"] = genre_id
            params["sort_by"] = "popularity.desc"
        elif action == "trending":
            time_window = parts[3] if len(parts) > 3 else "week"
            path = "/trending/{}/{}".format(tmdb_media_type, time_window)
        # [PATCH 44] streaming-provider browse (Netflix/Prime/Disney+...)
        elif action == "provider":
            provider_id = parts[3] if len(parts) > 3 else "8"
            path = "/discover/{}".format(tmdb_media_type)
            params["with_watch_providers"] = provider_id
            params["watch_region"] = "US"
            params["sort_by"] = "popularity.desc"
        elif action == "country":
            country_code = parts[3] if len(parts) > 3 else "US"
            path = "/discover/{}".format(tmdb_media_type)
            params["with_origin_country"] = country_code
            params["sort_by"] = "popularity.desc"
        elif action == "studio":
            studio_id = parts[3] if len(parts) > 3 else ""
            path = "/discover/{}".format(tmdb_media_type)
            params["with_companies"] = studio_id
            params["sort_by"] = "popularity.desc"
        elif action == "year":
            year = parts[3] if len(parts) > 3 else "2025"
            path = "/discover/{}".format(tmdb_media_type)
            if tmdb_media_type == "movie":
                params["primary_release_year"] = year
            else:
                params["first_air_date_year"] = year
            params["sort_by"] = "popularity.desc"
        elif action == "collection":
            collection_id = parts[3] if len(parts) > 3 else ""
            collection_data = self._tmdb_request("/collection/{}".format(collection_id))
            items = []
            for movie in collection_data.get("parts", []):
                items.append({
                    "title": movie.get("title", "Unknown"),
                    "poster": "https://image.tmdb.org/t/p/w342" + movie.get("poster_path", "") if movie.get("poster_path") else "",
                    "url": "yts_movie_{}".format(movie.get("id")),
                    "type": "movie",
                    "year": movie.get("release_date", "")[:4] if movie.get("release_date") else "",
                    "rating": "{:.1f}".format(movie.get("vote_average")) if movie.get("vote_average") else "",
                })
            return items
        elif action == "popular":
            path = "/{}/popular".format(tmdb_media_type)
        elif action == "top_rated":
            path = "/{}/top_rated".format(tmdb_media_type)
        elif action == "now_playing":
            path = "/movie/now_playing" if tmdb_media_type == "movie" else "/tv/airing_today"
        elif action == "upcoming":
            path = "/movie/upcoming" if tmdb_media_type == "movie" else "/tv/on_the_air"
        elif action == "on_the_air":
            path = "/tv/on_the_air"
        elif action == "airing_today":
            path = "/tv/airing_today"
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
                "url": "yts_{}_{}".format(item_type, r.get("id")),
                "type": item_type,
                "year": year,
                "rating": "{:.1f}".format(r.get("vote_average")) if r.get("vote_average") else "",
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

        log("YTS: category {} -> {} items (page {}/{})".format(url, len(items), current_page, total_pages))
        return items

    def search(self, query, page=1):
        """Search for movies and TV shows."""
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
                    "url": "yts_{}_{}".format(item_type, r.get("id")),
                    "type": item_type,
                    "year": year,
                    "rating": "{:.1f}".format(r.get("vote_average")) if r.get("vote_average") else "",
                })
        return items

    # ═══ Detail pages ════════════════════════════════════════════════

    def get_page(self, url, m_type=None):
        """Get details for a movie, TV show, season, or episode."""
        log("YTS: get_page url={}".format(url))

        # ─── TV Show Seasons List ────────────────────────────────
        if url.startswith("yts_tv_") or url.startswith("yts_series_"):
            tmdb_id = url.replace("yts_tv_", "").replace("yts_series_", "")
            data = self._tmdb_request("/tv/{}".format(tmdb_id))
            seasons = []
            for s in data.get("seasons", []):
                if s.get("season_number") == 0:
                    continue
                seasons.append({
                    "title": s.get("name", "Season {}".format(s.get("season_number"))),
                    "url": "yts_season_{}_{}".format(tmdb_id, s.get("season_number")),
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

        # ─── TV Show Episodes List ───────────────────────────────
        elif url.startswith("yts_season_"):
            parts = url.split("_")
            tmdb_id = parts[2]
            season_num = parts[3]
            data = self._tmdb_request("/tv/{}/season/{}".format(tmdb_id, season_num))
            episodes = []
            for ep in data.get("episodes", []):
                episodes.append({
                    "title": "S{:02d}E{:02d}: {}".format(int(season_num), ep.get("episode_number"), ep.get("name")),
                    "url": "yts_episode_{}_{}_{}".format(tmdb_id, season_num, ep.get("episode_number")),
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

        # ─── Movies (magnet servers + embed fallbacks) ──────────
        elif url.startswith("yts_movie_"):
            tmdb_id = url.replace("yts_movie_", "")
            movie_data = self._tmdb_request("/movie/{}".format(tmdb_id))

            # Magnet streams via Torrentio (same backend as the site)
            imdb = self._imdb_id(tmdb_id, "movie")
            servers = self._torr_servers(imdb) if imdb else []

            # Embed fallbacks (work without TorrServer)
            servers += [
                {"name": "Server 1 (2Embed.cc)", "url": "https://www.2embed.cc/embed/{}".format(tmdb_id), "quality": "HD"},
                {"name": "Server 2 (2Embed.skin)", "url": "https://www.2embed.skin/embed/{}".format(tmdb_id), "quality": "HD"},
                {"name": "Server 3 (VidSrc.mov)", "url": "https://vidsrc.mov/embed/movie/{}".format(tmdb_id), "quality": "HD"},
                {"name": "Server 4 (VidSrc.to)", "url": "https://vidsrc.to/embed/movie/{}".format(tmdb_id), "quality": "HD"},
            ]

            return {
                "title": movie_data.get("title") or movie_data.get("name") or "Movie",
                "plot": movie_data.get("overview") or "",
                "poster": "https://image.tmdb.org/t/p/w342" + movie_data.get("poster_path", "") if movie_data.get("poster_path") else "",
                "rating": "{:.1f}".format(movie_data["vote_average"]) if movie_data.get("vote_average") else "",
                "servers": servers,
                "items": [],
                "type": "movie"
            }

        # ─── TV Episodes (magnet servers + embed fallbacks) ──────
        elif url.startswith("yts_episode_"):
            parts = url.split("_")
            tmdb_id = parts[2]
            season_num = parts[3]
            ep_num = parts[4]

            # Magnet streams via Torrentio (same backend as the site)
            imdb = self._imdb_id(tmdb_id, "tv")
            servers = self._torr_servers(imdb, int(season_num), int(ep_num)) if imdb else []

            # Embed fallbacks
            servers += [
                # [PATCH 34b] ?s= not &s= — a query string starts with ?
                {"name": "Server 1 (2Embed.cc)", "url": "https://www.2embed.cc/embedtv/{}?s={}&e={}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
                {"name": "Server 2 (2Embed.skin)", "url": "https://www.2embed.skin/embedtv/{}?s={}&e={}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
                {"name": "Server 3 (VidSrc.mov)", "url": "https://vidsrc.mov/embed/tv/{}/{}/{}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
                {"name": "Server 4 (VidSrc.to)", "url": "https://vidsrc.to/embed/tv/{}/{}/{}".format(tmdb_id, season_num, ep_num), "quality": "HD"},
                {"name": "Full Season (2Embed.cc)", "url": "https://www.2embed.cc/embedtvfull/{}".format(tmdb_id), "quality": "HD"},
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

        return None

    # ═══ Stream extraction (embed resolution — magnets bypass this) ═══

    def extract_stream(self, url):
        """Resolve embed URLs to direct stream URLs.
        Magnet URLs are handled by plugin's _bgTorrServerMagnet directly
        (never reaching here), but we short-circuit defensively."""
        log("YTS: resolving embed {}".format(url))

        if not url:
            return None, "", url, []

        # Defensive: magnets should never reach here (plugin handles them
        # in _bgTorrServerMagnet), but return as-is if they do.
        if url.startswith("magnet:"):
            return url, "HD", url, []

        # ── Embed resolution (unchanged) ──────────────────────────
        if "vidsrc" in url or "2embed" in url or "embed" in url:
            html, _ = fetch(url, referer="https://en.yts.lu/")
            if html:
                stream, label, ref, variants = self._extract_stream_from_html(html, url)
                if stream:
                    return stream, label, ref, variants

            base_id = self._extract_id_from_url(url)
            if base_id:
                for source in self.embed_sources:
                    if source["url"] in url:
                        continue
                    test_url = self._build_embed_url(source, base_id, url)
                    log("YTS: Trying fallback source: {}".format(test_url))
                    html, _ = fetch(test_url, referer="https://en.yts.lu/")
                    if html:
                        stream, label, ref, variants = self._extract_stream_from_html(html, test_url)
                        if stream:
                            return stream, label, ref, variants

        html, _ = fetch(url, referer="https://en.yts.lu/")
        if html:
            stream, label, ref, variants = self._extract_stream_from_html(html, url)
            if stream:
                return stream, label, ref, variants

        if "2embed" in url or "embed" in url:
            stream = self._extract_via_twoembed_api(url)
            if stream:
                return stream, "HD", url, []

        return super().extract_stream(url)

    # ═══ Embed helpers (unchanged from original) ════════════════════

    def _extract_via_twoembed_api(self, url):
        try:
            movie_match = re.search(r'/embed/(\d+)', url)
            if movie_match:
                tmdb_id = movie_match.group(1)
                api_url = "{}/movie?imdb_id={}".format(self.twoembed_api, tmdb_id)
                data = fetch_json(api_url)
                if data and "stream_url" in data:
                    return data["stream_url"]
            tv_match = re.search(r'/embedtv/(\d+)', url)
            if tv_match:
                tmdb_id = tv_match.group(1)
                season_match = re.search(r's=(\d+)', url)
                episode_match = re.search(r'e=(\d+)', url)
                season = season_match.group(1) if season_match else 1
                episode = episode_match.group(1) if episode_match else 1
                api_url = "{}/tv?imdb_id={}&season={}&episode={}".format(
                    self.twoembed_api, tmdb_id, season, episode)
                data = fetch_json(api_url)
                if data and "stream_url" in data:
                    return data["stream_url"]
        except Exception as e:
            log("YTS: 2Embed API error: {}".format(e))
        return None

    def _extract_id_from_url(self, url):
        movie_match = re.search(r'/movie/(\d+)', url)
        if movie_match:
            return {"type": "movie", "id": movie_match.group(1)}
        embed_match = re.search(r'/embed/(\d+)', url)
        if embed_match:
            return {"type": "movie", "id": embed_match.group(1)}
        tv_match = re.search(r'/tv/(\d+)(?:/(\d+)/(\d+))?', url)
        if tv_match:
            return {"type": "tv", "id": tv_match.group(1),
                    "season": tv_match.group(2) or 1, "episode": tv_match.group(3) or 1}
        embedtv_match = re.search(r'/embedtv/(\d+)', url)
        if embedtv_match:
            season_match = re.search(r's=(\d+)', url)
            episode_match = re.search(r'e=(\d+)', url)
            return {"type": "tv", "id": embedtv_match.group(1),
                    "season": season_match.group(1) if season_match else 1,
                    "episode": episode_match.group(1) if episode_match else 1}
        embedtvfull_match = re.search(r'/embedtvfull/(\d+)', url)
        if embedtvfull_match:
            return {"type": "tv", "id": embedtvfull_match.group(1),
                    "season": None, "episode": None, "full_season": True}
        return None

    def _build_embed_url(self, source, base_id, original_url):
        if base_id.get("full_season"):
            if "2embed" in source["url"]:
                return "{}/embedtvfull/{}".format(source["url"].replace("/embed", ""), base_id["id"])
            return None
        if base_id["type"] == "movie":
            return "{}/movie/{}".format(source["url"], base_id["id"])
        else:
            season = base_id.get("season", 1)
            episode = base_id.get("episode", 1)
            if "2embed" in source["url"]:
                return "{}/embedtv/{}?s={}&e={}".format(
                    source["url"].replace("/embed", ""), base_id["id"], season, episode)
            else:
                return "{}/tv/{}/{}/{}".format(source["url"], base_id["id"], season, episode)

    def _extract_stream_from_html(self, html, base_url):
        if not html:
            return None, "", base_url, []
        all_streams = []
        seen_urls = set()

        iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for iframe in iframes:
            if iframe.startswith("//"):
                iframe = "https:" + iframe
            elif not iframe.startswith("http"):
                iframe = urllib.parse.urljoin(base_url, iframe)
            if any(x in iframe.lower() for x in ("ads", "adserver", "popup", "youtube", "google")):
                continue
            stream_url = resolve_host(iframe)
            if stream_url:
                stream_url = _correct_stream_url(stream_url)
                if stream_url not in seen_urls:
                    seen_urls.add(stream_url)
                    all_streams.append(stream_url)
            for lbl, u in get_last_quality_variants():
                u = _correct_stream_url(u)
                if u not in seen_urls:
                    seen_urls.add(u)
                    all_streams.append(u)

        for m3u8 in find_m3u8_all(html):
            m3u8 = _correct_stream_url(m3u8)
            if m3u8 not in seen_urls:
                seen_urls.add(m3u8)
                all_streams.append(m3u8)

        source_patterns = [
            r'<video[^>]+src=["\']([^"\']+\.(?:m3u8|mp4|ts)[^"\']*)["\']',
            r'<source[^>]+src=["\']([^"\']+\.(?:m3u8|mp4|ts)[^"\']*)["\']',
            r'"file"\s*:\s*["\']([^"\']+\.(?:m3u8|mp4|ts)[^"\']*)["\']',
            r'"src"\s*:\s*["\']([^"\']+\.(?:m3u8|mp4|ts)[^"\']*)["\']',
        ]
        for pattern in source_patterns:
            for match in re.findall(pattern, html, re.I):
                if match.startswith("//"):
                    match = "https:" + match
                elif not match.startswith("http"):
                    match = urllib.parse.urljoin(base_url, match)
                match = _correct_stream_url(match)
                if match not in seen_urls:
                    seen_urls.add(match)
                    all_streams.append(match)

        if not all_streams:
            return None, "", base_url, []

        labeled_variants = _label_quality_variants(all_streams)
        if not labeled_variants:
            return None, "", base_url, []
        main_label, main_url = labeled_variants[0]
        variants = labeled_variants[1:]
        return main_url, main_label, base_url, variants