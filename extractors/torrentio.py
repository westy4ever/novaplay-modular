# -*- coding: utf-8 -*-
"""
Torrentio Extractor
Fetches TMDB catalogs and resolves streams via Torrentio (Magnet links).

Updates:
  * Capped at 40 results per title (avoids oversized server lists)
  * Deduped by infoHash
  * Magnet builder kept identical — plugin's _bgTorrServerMagnet handles playback

[PATCH 30]
  * Lazy IMDB resolution: TMDB ids ride in the URLs; IMDB resolves ONCE
    when a title is opened (get_page → external_ids). Category & search
    pages dropped from ~21 HTTP requests to 1 — pages load in ~1–2s
    instead of 10–20s. Old "tt…" URLs (history/favorites) still resolve
    via /find.
  * _extract_quality: word-boundary CAM/TS detection (plain 'TS' matched
    HITS/ARTS/BITS and mislabeled releases as CAM)
  * _parse_streams: seeders (👤 N) and size (N GB/MB) parsed from the
    torrent title into the server dict → StreamList columns
  * raw-title debug log removed (was 40 lines per detail view)

[PATCH 34e]
  * infoHash length validation (40 hex / 32 base32) backported from YTS —
    malformed hashes are skipped before they become selectable dead magnets
"""
import re
import urllib.parse
from .base import BaseExtractor, fetch, fetch_json, log


class TorrentioExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.main_url = "https://torrentio.strem.fun"
        self._tmdb_api_key = "46b050dc88e3c52e9d1bca4b656036e4"
        # Cap results to avoid oversized lists (popular titles can return 100+)
        self._max_servers = 40

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
        return [
            {"title": "🔥 Popular Movies", "url": "torr_movie_popular", "type": "category"},
            {"title": "⭐ Top Rated Movies", "url": "torr_movie_top_rated", "type": "category"},
            {"title": "🎬 Now Playing", "url": "torr_movie_now_playing", "type": "category"},
            {"title": "📈 Trending Movies", "url": "torr_movie_trending_week", "type": "category"},
            {"title": "📺 Popular Series", "url": "torr_tv_popular", "type": "category"},
            {"title": "⭐ Top Rated Series", "url": "torr_tv_top_rated", "type": "category"},
            {"title": "📡 On Air Series", "url": "torr_tv_on_the_air", "type": "category"},
            {"title": "📈 Trending Series", "url": "torr_tv_trending_week", "type": "category"},
        ]

    def get_category_items(self, url, page=1):
        page_match = re.search(r'_page_(\d+)$', url)
        if page_match:
            page = int(page_match.group(1))
            url = url[:page_match.start()]

        parts = url.split("_", 2)
        if len(parts) < 3:
            return []
        media_type = parts[1]
        action = parts[2]

        path = "/{}".format(media_type)
        params = {"page": page, "language": "en-US"}

        if action == "popular":
            path = "/{}/popular".format(media_type)
        elif action == "top_rated":
            path = "/{}/top_rated".format(media_type)
        elif action == "now_playing" and media_type == "movie":
            path = "/movie/now_playing"
        elif action == "on_the_air" and media_type == "tv":
            path = "/tv/on_the_air"
        elif action == "trending_week":
            path = "/trending/{}/week".format(media_type)
        else:
            path = "/{}/{}".format(media_type, action)

        data = self._tmdb_request(path, params)
        items = []
        for r in data.get("results", []):
            # [PATCH 30c] no per-item external_ids call — the tmdb id rides
            # in the URL; imdb resolves ONCE when the title is opened.
            # Was 20 extra HTTP requests per page.
            if not r.get("id"):
                continue
            title = r.get("title") or r.get("name") or "Unknown"
            date_field = r.get("release_date") or r.get("first_air_date") or ""
            year = date_field[:4] if date_field else ""
            items.append({
                "title": title,
                "poster": "https://image.tmdb.org/t/p/w342" + r.get("poster_path", "") if r.get("poster_path") else "",
                "url": "torr_{}_{}".format(media_type, r.get("id")),
                "type": "movie" if media_type == "movie" else "series",
                "year": year
            })

        total_pages = data.get("total_pages", 1)
        if data.get("page", page) < total_pages:
            items.append({
                "title": "➡️ Page {} (Next)".format(page + 1),
                "url": "{}_page_{}".format(url, page + 1),
                "type": "category",
                "_action": "category"
            })
        return items

    def search(self, query, page=1):
        data = self._tmdb_request("/search/multi", {"query": query, "page": page})
        items = []
        for r in data.get("results", []):
            # [PATCH 30c] same lazy-id change as get_category_items
            if r.get("media_type") in ("movie", "tv") and r.get("id"):
                title = r.get("title") or r.get("name") or "Unknown"
                year = (r.get("release_date") or r.get("first_air_date") or "")[:4]
                items.append({
                    "title": title,
                    "poster": "https://image.tmdb.org/t/p/w342" + r.get("poster_path", "") if r.get("poster_path") else "",
                    "url": "torr_{}_{}".format(r.get("media_type"), r.get("id")),
                    "type": "movie" if r.get("media_type") == "movie" else "series",
                    "year": year
                })
        return items

    def _clean_magnet_name(self, name):
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
        t = title.upper()
        q = "HD"
        if '2160P' in t or '4K' in t:
            q = '2160p'
        elif '1080P' in t:
            q = '1080p'
        elif '720P' in t:
            q = '720p'
        elif '480P' in t:
            q = '480p'
        # [PATCH 30a] word-boundary check — plain 'TS' matched HITS/ARTS/
        # BITS etc. and mislabeled them CAM
        # [PATCH 40] preserve the ACTUAL source tag from the name —
        # "1080p HDTS" instead of a generic "1080p CAM". Longest-first
        # alternation: HDTS beats TS, WEB-DL beats WEB. Word boundaries
        # keep HITS/ARTS/BITS from matching.
        m = re.search(r'\b(HD-?CAM|TELESYNC|HDTS|WEB-DL|WEBRIP|BLURAY|BRRIP|BDRIP|HDRIP|CAM|WEB|TS|DVD)\b', t)
        if m:
            q += " " + m.group(0)
        return q

    def _parse_streams(self, data):
        """Parse torrentio streams response → magnet server dicts.
        Deduped by infoHash, capped at self._max_servers.
        [PATCH 30d] seeders/size parsed from the title (👤 N / N GB).
        [PATCH 34e] infoHash length validation (40 hex / 32 base32)."""
        servers, seen = [], set()
        for s in data.get("streams", []) or []:
            info_hash = str(s.get("infoHash", ""))
            # [PATCH 34e] backport of YTS's validation: a valid v1 infoHash
            # is 40 hex chars or 32 base32 chars — anything else would
            # build a magnet TorrServer can never resolve
            if not info_hash or len(info_hash) not in (32, 40) or info_hash.lower() in seen:
                continue
            seen.add(info_hash.lower())
            title = str(s.get("title") or s.get("name") or "Unknown Source")
            file_idx = s.get("fileIdx")
            magnet = self._build_magnet(info_hash, title, file_idx)
            quality = self._extract_quality(title)
            # [PATCH 30d] light up the StreamList size/seeders columns
            seeds_m = re.search(r'👤\s*(\d+)', title)
            size_m = re.search(r'(\d+(?:[.,]\d+)?\s*(?:GB|MB))', title, re.I)
            servers.append({
                "name": title,
                "url": magnet,
                "quality": quality,
                "seeders": seeds_m.group(1) if seeds_m else "",
                "size": size_m.group(1) if size_m else "",
            })
            if len(servers) >= self._max_servers:
                break
        return servers

    def get_page(self, url, m_type=None):
        parts = url.split("_")
        if len(parts) < 3:
            return None
        url_type = parts[1]
        id_part = parts[2]

        # [PATCH 30c] URLs now carry TMDB ids; old "tt…" URLs (history /
        # favorites) still resolve through /find
        media_type = "movie" if url_type == "movie" else "tv"
        tmdb_id = None
        if id_part.startswith("tt"):
            find_data = self._tmdb_request("/find/{}".format(id_part), {"external_source": "imdb_id"})
            results = find_data.get("{}_results".format(media_type)) or []
            tmdb_id = results[0].get("id") if results else None
        else:
            try:
                tmdb_id = int(id_part)
            except Exception:
                tmdb_id = None

        def _imdb():
            ext = self._tmdb_request("/{}/{}/external_ids".format(media_type, tmdb_id)) or {}
            return ext.get("imdb_id", "") or ""

        # ─── Series: Show Seasons List ──────────────────────────────
        if url_type == "tv":
            if not tmdb_id:
                return None
            series_data = self._tmdb_request("/tv/{}".format(tmdb_id))
            seasons = []
            for s in series_data.get("seasons", []):
                if s.get("season_number") == 0:
                    continue
                seasons.append({
                    "title": s.get("name", "Season {}".format(s.get("season_number"))),
                    "url": "torr_season_{}_{}".format(tmdb_id, s.get("season_number")),
                    "type": "season"
                })
            return {
                "title": series_data.get("name", "TV Show"),
                "plot": series_data.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + (series_data.get("poster_path") or "") if series_data.get("poster_path") else "",
                "servers": [],
                "items": seasons,
                "type": "series"
            }

        # ─── Season: Show Episodes List ─────────────────────────────
        elif url_type == "season":
            season_num = parts[3]
            if not tmdb_id:
                return None
            season_data = self._tmdb_request("/tv/{}/season/{}".format(tmdb_id, season_num))
            episodes = []
            for ep in season_data.get("episodes", []):
                episodes.append({
                    "title": "S{:02d}E{:02d}: {}".format(int(season_num), ep.get("episode_number"), ep.get("name")),
                    "url": "torr_episode_{}_{}_{}".format(tmdb_id, season_num, ep.get("episode_number")),
                    "type": "episode"
                })
            return {
                "title": season_data.get("name", "Season {}".format(season_num)),
                "plot": season_data.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + (season_data.get("poster_path") or "") if season_data.get("poster_path") else "",
                "servers": [],
                "items": episodes,
                "type": "season"
            }

        # ─── Episode: Fetch Torrent Streams ──────────────────────────
        elif url_type == "episode":
            season_num = parts[3]
            ep_num = parts[4]
            if not tmdb_id:
                return None
            imdb_id = _imdb()
            torr_url = "{}/stream/series/{}:{}:{}.json".format(self.main_url, imdb_id, season_num, ep_num)
            data = fetch_json(torr_url) or {}
            servers = self._parse_streams(data)
            details = self._tmdb_request("/tv/{}/season/{}/episode/{}".format(tmdb_id, season_num, ep_num))
            return {
                "title": "S{:02d}E{:02d}: {}".format(int(season_num), int(ep_num), details.get("name", "Episode")),
                "plot": details.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + (details.get("still_path") or "") if details.get("still_path") else "",
                "servers": servers,
                "items": [],
                "type": "episode"
            }

        # ─── Movie: Fetch Torrent Streams ────────────────────────────
        elif url_type == "movie":
            if not tmdb_id:
                return None
            imdb_id = _imdb()
            torr_url = "{}/stream/movie/{}.json".format(self.main_url, imdb_id)
            data = fetch_json(torr_url) or {}
            servers = self._parse_streams(data)
            details = self._tmdb_request("/movie/{}".format(tmdb_id))
            return {
                "title": details.get("title") or details.get("name") or "Stream",
                "plot": details.get("overview", ""),
                "poster": "https://image.tmdb.org/t/p/w342" + (details.get("poster_path") or "") if details.get("poster_path") else "",
                "servers": servers,
                "items": [],
                "type": "movie"
            }

        return None

    def extract_stream(self, url):
        # Magnet links are returned directly by get_page; plugin's
        # _bgTorrServerMagnet handles magnet→TorrServer conversion.
        return url, "HD", url, []