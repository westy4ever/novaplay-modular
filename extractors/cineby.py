# -*- coding: utf-8 -*-
"""
Cineby Extractor (www.cineby.tech)

New site added from a real browser capture (homepage + /category/movie,
/category/tv, /category/action + a movie detail page for "Spider-Man:
Brand New Day" (TMDB id 969681) + full network/stream recording).

Site architecture, confirmed directly from the capture:
  * Cineby is a thin, purely TMDB-id-addressed skin: its homepage and
    /category/<slug> pages are built straight from TMDB's own
    trending/discover/genre endpoints, called client-side with Cineby's
    own public TMDB v3 api_key (seen on every api.themoviedb.org request
    in the capture: 8871b4dba1715cd776c063a458ae8795). There is nothing
    Cineby-specific to scrape for listings -- it's the same TMDB catalog
    every other TMDB-id aggregator in this plugin (vidsrc.py) already
    uses, just under a different skin.
  * URLs are /movie/<tmdb_id>/watch and /tv/<tmdb_id>/<season>/<episode>
    (confirmed: the captured detail page's canonical URL was
    https://www.cineby.tech/movie/969681/watch for Spider-Man: Brand New
    Day, TMDB id 969681). Category pages: /category/movie, /category/tv,
    /category/<genre-slug> (action, sci-fi, ... ), /category/trending,
    /category/top-rated.
  * Playback: Cineby embeds vidnest.fun directly
    (https://vidnest.fun/movie/969681?startAt=... in the capture,
    confirmed via Sec-Fetch-Dest: iframe / initiator: cineby.tech).
    vidnest.fun's own page calls new.vidnest.fun/yflix/movie/<id>, which
    returns an opaque encrypted blob ({"d": "<ciphertext>", "n": ...,
    "v": 2}) that its own obfuscated client JS decrypts into the final
    {"file": "<m3u8 url>"} handed to hls.js -- there is no cleartext
    server-side endpoint for this exposed anywhere in the capture, and
    reverse-engineering a rotating client-side cipher from minified JS
    isn't attempted here.
  * [UPDATE] That encrypted blob is no longer a dead end: the user pulled
    vidnest.fun's own client JS chunk via DevTools (confirmed via a
    Sources-panel search for "decryptCipherResponse") and it decrypts
    with AES-256-GCM: key = SHA256(f"{nonce}|{SECRET}") where SECRET is
    a static string baked into the bundle and nonce is the response's
    own "n" field; the ciphertext is base64url in "d", first 12 bytes
    IV, rest is ciphertext+tag. hosts.py's resolve_vidnest() is a
    faithful, verified port of this (reproduces the exact same
    pwcloud.animanga.fun master.m3u8 URL the real browser got for this
    same TMDB id, byte for byte, through both the `cryptography`-backed
    path and the pure-Python fallback that actually runs on the user's
    C-compiler-less device). Registered in HOST_RESOLVERS under
    "vidnest"/"vidnest.fun", so Server 1 below now actually works rather
    than being a placeholder.
  * MedMedia03 (added for OnlyFlix) is ALSO purely TMDB-id-addressed and
    queries a provider literally named "vidnest" among others via its
    own clean JSON API -- kept as Server 2, a legitimate independent
    fallback if vidnest.fun's own encryption scheme changes. A couple of
    the same public multi-embed providers vidsrc.py already relies on
    (tvembed.cc, VidSrc.win) round out the list as further fallbacks.
  * [UPDATE 2] vidnest.fun actually proxies TWELVE backend providers this
    same way, not just "catflix"/yflix -- a second, larger JS chunk the
    user pulled (the movie-player component itself) contained the full
    per-provider response-handling logic for all twelve, so
    resolve_vidnest() in hosts.py now supports all of them via a
    `?provider=<key>` query param appended to the vidnest.fun URL
    (catflix is still the default with no param). Added here as extra
    movie-only fallback servers -- movie-only because TV was only ever
    evidenced (as an extrapolation, not a capture) for catflix; no TV
    shape evidence exists for the other 11, so they're not offered for
    TV. Unlike catflix, none of these 11 have been checked against a
    real captured ciphertext (only the verified decrypt algorithm + the
    verified response-handling JS) -- if one doesn't actually pan out on
    a real device, it's a harmless extra entry in the server list, not a
    regression of anything that worked before.
"""
import re
import urllib.parse
from .base import BaseExtractor, fetch_json, log


class CinebyExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.main_url = "https://www.cineby.tech"
        # Confirmed directly from the capture's own api.themoviedb.org
        # requests -- Cineby's own public v3 key, not guessed.
        self._tmdb_api_key = "8871b4dba1715cd776c063a458ae8795"

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

    def _poster(self, path):
        return "https://image.tmdb.org/t/p/w342" + path if path else ""

    # ─── Categories ───────────────────────────────────────────────────────

    def get_categories(self, mtype="movie"):
        cats = []
        cats.append({"title": "🌐 Movies", "url": "", "type": "separator"})
        cats.append({"title": "📈 Trending Movies", "url": "cineby_movie_trending_week", "type": "category"})
        cats.append({"title": "🔥 Popular Movies", "url": "cineby_movie_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Movies", "url": "cineby_movie_top_rated", "type": "category"})
        cats.append({"title": "🎬 Upcoming Movies", "url": "cineby_movie_upcoming", "type": "category"})

        cats.append({"title": "📺 Series", "url": "", "type": "separator"})
        cats.append({"title": "📈 Trending Series This Week", "url": "cineby_series_trending_week", "type": "category"})
        cats.append({"title": "🔥 Popular Series", "url": "cineby_series_popular", "type": "category"})
        cats.append({"title": "⭐ Top Rated Series", "url": "cineby_series_top_rated", "type": "category"})
        cats.append({"title": "📡 On The Air", "url": "cineby_series_on_the_air", "type": "category"})

        # Genre categories -- "action" and "sci-fi" slugs were confirmed
        # directly in the capture's /category/<slug> nav; the rest are
        # TMDB's own stable, standard genre ids, used the same way
        # vidsrc.py already uses them in this plugin.
        cats.append({"title": "🎭 Movie Genres", "url": "", "type": "separator"})
        for label, genre_id in (
            ("💥 Action", 28), ("😂 Comedy", 35), ("🎭 Drama", 18),
            ("👻 Horror", 27), ("🚀 Sci-Fi", 878), ("❤️ Romance", 10749),
            ("🔪 Crime", 80), ("🎌 Animation", 16),
        ):
            cats.append({"title": label, "url": "cineby_movie_genre_{}".format(genre_id), "type": "category"})

        cats.append({"title": "🎭 Series Genres", "url": "", "type": "separator"})
        for label, genre_id in (
            ("🎌 Anime", 16), ("🎭 Drama", 18), ("🔪 Crime", 80),
            ("💥 Action & Adventure", 10759), ("🧼 Soap", 10766),
        ):
            cats.append({"title": label, "url": "cineby_series_genre_{}".format(genre_id), "type": "category"})

        return cats

    def get_category_items(self, url, page=1):
        page_match = re.search(r'_page_(\d+)$', url)
        if page_match:
            page = int(page_match.group(1))
            url = url[:page_match.start()]

        parts = url.split("_")
        if len(parts) < 3:
            return []
        media_type = parts[1]
        action = parts[2]
        if action in ("top", "on"):
            _full = "_".join(parts[2:])
            if _full in ("top_rated", "on_the_air"):
                action = _full
        tmdb_media_type = "tv" if media_type == "series" else "movie"
        item_type = "series" if media_type == "series" else "movie"

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
                "poster": self._poster(r.get("poster_path", "")),
                "url": "cineby_{}_{}".format(item_type, r.get("id")),
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
                    "poster": self._poster(r.get("poster_path", "")),
                    "url": "cineby_{}_{}".format(item_type, r.get("id")),
                    "type": item_type,
                    "year": year
                })
        return items

    # ─── Servers ──────────────────────────────────────────────────────────

    def _servers_for(self, tmdb_id, season=None, episode=None):
        """Build the server list for a given TMDB id.

        Server 1 is the real embed Cineby itself uses (vidnest.fun,
        confirmed via the capture) -- left in even though its own
        encrypted resolution isn't implemented, in case a future
        resolver covers it. Server 2 (medmedia03) is the one most
        likely to actually produce a stream right now: same TMDB-id
        addressing, a provider literally named "vidnest" among its
        options, and an already-confirmed-working resolver in this
        codebase (hosts.py resolve_medmedia03, proven via OnlyFlix).
        Servers 3-5 are the same public multi-embed providers vidsrc.py
        already relies on successfully, as further fallbacks.
        """
        if season and episode:
            return [
                {"name": "Server 1 (Vidnest)", "url": "https://vidnest.fun/tv/{}/{}/{}".format(tmdb_id, season, episode), "quality": "HD"},
                {"name": "Server 2 (MedMedia03)", "url": "https://balance.medmedia03.wiki/embed/tv/{}/{}/{}".format(tmdb_id, season, episode), "quality": "HD"},
                {"name": "Server 3 (tvembed)", "url": "https://tvembed.cc/tv/{}/{}/{}".format(tmdb_id, season, episode), "quality": "HD"},
                {"name": "Server 4 (2Embed)", "url": "https://www.2embed.skin/embedtv/{}&s={}&e={}".format(tmdb_id, season, episode), "quality": "HD"},
                {"name": "Server 5 (VidSrc.win)", "url": "https://vidsrc.win/watch/{}?s={}&e={}".format(tmdb_id, season, episode), "quality": "HD"},
            ]
        servers = [
            {"name": "Server 1 (Vidnest)", "url": "https://vidnest.fun/movie/{}".format(tmdb_id), "quality": "HD"},
            {"name": "Server 2 (MedMedia03)", "url": "https://balance.medmedia03.wiki/embed/movie/{}".format(tmdb_id), "quality": "HD"},
            {"name": "Server 3 (tvembed)", "url": "https://tvembed.cc/movie/{}".format(tmdb_id), "quality": "HD"},
            {"name": "Server 4 (2Embed)", "url": "https://www.2embed.skin/embed/{}".format(tmdb_id), "quality": "HD"},
            {"name": "Server 5 (VidSrc.win)", "url": "https://vidsrc.win/watch/{}".format(tmdb_id), "quality": "HD"},
        ]
        # Extra Vidnest-backed fallbacks (movie-only -- see module docstring
        # "[UPDATE 2]"), one per remaining provider resolve_vidnest() now
        # understands via ?provider=<key>. Not re-verified against a real
        # device capture the way catflix (Server 1) was.
        for i, (provider, label) in enumerate((
            ("beta", "Beta"), ("gama", "Gama"), ("ophim", "Ophim"), ("lamda", "Lamda"),
            ("prime", "Prime"), ("zeta", "Zeta"), ("sigma", "Sigma"), ("hexa", "Hexa"),
            ("alfa", "Alfa"), ("delta", "Delta (Hindi)"), ("filxer", "Filxer"),
        ), start=6):
            servers.append({
                "name": "Server {} (Vidnest-{})".format(i, label),
                "url": "https://vidnest.fun/movie/{}?provider={}".format(tmdb_id, provider),
                "quality": "HD",
            })
        return servers

    # ─── Detail pages ─────────────────────────────────────────────────────

    def get_page(self, url, m_type=None):
        if url.startswith("cineby_tv_") or url.startswith("cineby_series_"):
            tmdb_id = url.replace("cineby_tv_", "").replace("cineby_series_", "")
            data = self._tmdb_request("/tv/{}".format(tmdb_id))
            seasons = []
            for s in data.get("seasons", []):
                if s.get("season_number") == 0:
                    continue
                seasons.append({
                    "title": s.get("name", "Season {}".format(s.get("season_number"))),
                    "url": "cineby_season_{}_{}".format(tmdb_id, s.get("season_number")),
                    "type": "season"
                })
            return {
                "title": data.get("name", "TV Show"),
                "plot": data.get("overview", ""),
                "poster": self._poster(data.get("poster_path", "")),
                "servers": [],
                "items": seasons,
                "type": "series"
            }

        elif url.startswith("cineby_season_"):
            parts = url.split("_")
            tmdb_id = parts[2]
            season_num = parts[3]
            data = self._tmdb_request("/tv/{}/season/{}".format(tmdb_id, season_num))
            episodes = []
            for ep in data.get("episodes", []):
                episodes.append({
                    "title": "S{:02d}E{:02d}: {}".format(int(season_num), ep.get("episode_number"), ep.get("name")),
                    "url": "cineby_episode_{}_{}_{}".format(tmdb_id, season_num, ep.get("episode_number")),
                    "type": "episode"
                })
            return {
                "title": data.get("name", "Season {}".format(season_num)),
                "plot": data.get("overview", ""),
                "poster": self._poster(data.get("poster_path", "")),
                "servers": [],
                "items": episodes,
                "type": "season"
            }

        elif url.startswith("cineby_episode_"):
            parts = url.split("_")
            tmdb_id = parts[2]
            season_num = parts[3]
            ep_num = parts[4]

            servers = self._servers_for(tmdb_id, season_num, ep_num)
            ep_data = self._tmdb_request("/tv/{}/season/{}/episode/{}".format(tmdb_id, season_num, ep_num))
            return {
                "title": "S{:02d}E{:02d}: {}".format(int(season_num), int(ep_num), ep_data.get("name", "Episode")),
                "plot": ep_data.get("overview", ""),
                "poster": self._poster(ep_data.get("still_path", "")),
                "servers": servers,
                "items": [],
                "type": "episode"
            }

        elif url.startswith("cineby_movie_"):
            tmdb_id = url.replace("cineby_movie_", "")
            servers = self._servers_for(tmdb_id)
            movie_data = self._tmdb_request("/movie/{}".format(tmdb_id))
            return {
                "title": movie_data.get("title") or movie_data.get("name") or "Movie",
                "plot": movie_data.get("overview", ""),
                "poster": self._poster(movie_data.get("poster_path", "")),
                "servers": servers,
                "items": [],
                "type": "movie"
            }

        return None
