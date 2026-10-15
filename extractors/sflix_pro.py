# -*- coding: utf-8 -*-
"""
SFlix Extractor (ssflix.pro)

New site, built from a real browser capture: the homepage shell +
/latest-movies, /popular-movies, /upcoming-movies, /tv-series listing
pages + a movie detail/watch page ("Resident Evil", TMDB id 1423191,
captured across all 6 of its server tabs) + a TV show/episode watch page
("Watch What Happens Live with Andy Cohen", TMDB id 22980) + a full
network/stream recording of that whole session.

Site architecture, confirmed directly from the capture:
  * SFlix is the SAME shape of site as Cineby (extractors/cineby.py): a
    thin, purely TMDB-id-addressed skin whose pages are a near-empty
    server-rendered shell (every listing/category snapshot captured was
    under 300 lines -- just nav chrome, no cards) hydrated entirely
    client-side from TMDB's own endpoints, called with SFlix's own public
    TMDB v3 api_key (seen on every api.themoviedb.org request in the
    capture: 31eb6ae13f030d2e334cdd978cfc72b7). Confirmed endpoint ->
    nav-page mapping (every one actually clicked and captured):
        /latest-movies   -> /movie/now_playing
        /popular-movies  -> /movie/popular
        /upcoming-movies -> /movie/upcoming
        /tv-series       -> /discover/tv?sort_by=popularity.desc
        home sliders     -> /trending/movie/day, /trending/movie/week,
                             /trending/tv/day, /trending/tv/week
    /anime and /top-imdb are in the nav bar but weren't clicked in the
    capture -- implemented as reasonable TMDB equivalents (discover/tv
    genre=16 "Animation", movie/top_rated) and flagged inline since
    they're extrapolated, not captured.
  * URLs are /movie/<slug>-<tmdb_id> and /serie/<slug>-<tmdb_id>, watched
    at /watch/movie/<slug>-<tmdb_id> and
    /watch/serie/<slug>-<tmdb_id>/season-<n>/episode-<n> (all confirmed
    directly in the capture's userJourney). Genre ids in the nav sidebar
    (confirmed real TMDB genre ids, not guessed): Action 28, Adventure 12,
    Animation 16, Comedy 35, Crime 80, Drama 18, Family 10751, Fantasy 14,
    History 36, Horror 27, Music 10402, Mystery 9648, Romance 10749,
    Sci-Fi 878, Thriller 53, TV Movie 10770, War 10752, Western 37.
  * Playback: the watch page has 6 server tabs, each a clean TMDB-id
    embed with NO signed token in the default-tab URL (confirmed by
    diffing the captured page snapshot at each of the 6 tabs' "active"
    state against its iframe src):
        Server 1: moviesapi.to/movie/<id>            (tv: /tv/<id>/<s>/<e>)
        Server 2: vidcore.net/movie/<id>?autoPlay=true
        Server 3: yapgrid.com/embed/movie/<id>
        Server 4: vidfast.vc/movie/<id>?autoPlay=true (tv: /tv/<id>/<s>/<e>)
        Server 5: vidsrc-embed.ru/embed/movie/<id>
        Server 6: embedmaster.link/movie/<id>
    Of these, Server 1 (moviesapi.to) is the one with a REAL evidenced
    resolution path: its embed page calls its own clean, UNENCRYPTED JSON
    API (moviesapi.to/api/vidora/v1/movie/<id>), which hosts.py's
    resolve_moviesapi() was upgraded to call directly instead of scraping
    the (JS-hydrated, server-empty) embed HTML -- see that function's own
    docstring for the one open question (an x-player-key header whose
    exact validation rule isn't confirmed). Server 4 (vidfast.vc) already
    has a real, working resolver in this codebase (resolve_vidsrc_family,
    added for OnlyFlix) -- its TV URL shape (/tv/<id>/<s>/<e>) is reused
    directly from onlyflix.py's own already-working implementation, not
    guessed. Server 2 (vidcore.net) reuses resolve_vidcore (registered
    for vidcore.io, which the capture showed vidcore.net redirecting to)
    as a same-family, not independently confirmed, fallback. Servers 3/5/6
    (yapgrid.com, vidsrc-embed.ru, embedmaster.link) have no dedicated
    resolver -- no JS was pulled for any of them -- so they fall through
    to the plugin's existing generic iframe/embed fallback chain, which
    may or may not find anything; they're included because they're real
    servers this site actually offers, not because resolution through
    them is confirmed.
  * TV note: only moviesapi.to and vidfast.vc have an evidenced TV URL
    shape (moviesapi.to's own /tv/<id>/<s>/<e> was captured directly,
    albeit for an episode that backend didn't have; vidfast.vc/tv/... is
    reused from OnlyFlix's own established working code, not this
    capture). The other 4 servers' TV URLs are extrapolated from their
    movie URL's own convention and are NOT offered for TV here, same
    policy cineby.py uses for its own unconfirmed TV server shapes.
"""
import re
import urllib.parse
from .base import BaseExtractor, fetch_json, log


class SflixProExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.main_url = "https://ssflix.pro"
        # Confirmed directly from the capture's own api.themoviedb.org
        # requests -- SFlix's own public v3 key, not guessed.
        self._tmdb_api_key = "31eb6ae13f030d2e334cdd978cfc72b7"

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

    @staticmethod
    def _rating(r):
        # [FIX] the site's own cards show TMDB's vote_average (e.g. "6.3");
        # it's already in every TMDB response we fetch, so carry it on the
        # item instead of making the home grid's fill_item_rating_async()
        # fire a second TMDB lookup per poster just to recover it.
        try:
            v = float((r or {}).get("vote_average") or 0)
        except (TypeError, ValueError):
            return ""
        return "{:.1f}".format(v) if v > 0 else ""

    @staticmethod
    def _genres(data):
        return ", ".join(g.get("name", "") for g in ((data or {}).get("genres") or [])
                         if isinstance(g, dict) and g.get("name"))

    # ─── Categories ───────────────────────────────────────────────────────

    def get_categories(self, mtype="movie"):
        cats = []
        cats.append({"title": "🌐 Movies", "url": "", "type": "separator"})
        cats.append({"title": "📈 Trending Movies", "url": "sflix_movie_trending_week", "type": "category"})
        cats.append({"title": "🆕 Latest Movies", "url": "sflix_movie_latest", "type": "category"})
        cats.append({"title": "🔥 Popular Movies", "url": "sflix_movie_popular", "type": "category"})
        cats.append({"title": "🎬 Upcoming Movies", "url": "sflix_movie_upcoming", "type": "category"})
        # [NOTE] extrapolated, not clicked in the capture -- TMDB's own
        # top_rated endpoint is the closest real equivalent to a
        # "/top-imdb" nav entry.
        cats.append({"title": "⭐ Top IMDb Movies", "url": "sflix_movie_top_rated", "type": "category"})

        cats.append({"title": "📺 Series", "url": "", "type": "separator"})
        cats.append({"title": "📈 Trending Series", "url": "sflix_series_trending_week", "type": "category"})
        cats.append({"title": "📺 TV Series", "url": "sflix_series_popular", "type": "category"})
        # [NOTE] extrapolated, not clicked in the capture -- TMDB has no
        # single "anime" flag, so this is Animation genre + a sort by
        # popularity, the closest real equivalent to a "/anime" nav entry.
        cats.append({"title": "🎌 Anime", "url": "sflix_series_genre_16", "type": "category"})

        # Genre ids below are confirmed real TMDB ids, read directly out
        # of the nav sidebar in the captured listing pages.
        cats.append({"title": "🎭 Movie Genres", "url": "", "type": "separator"})
        for label, genre_id in (
            ("💥 Action", 28), ("🎒 Adventure", 12), ("🎌 Animation", 16),
            ("😂 Comedy", 35), ("🔪 Crime", 80), ("🎭 Drama", 18),
            ("👪 Family", 10751), ("🧙 Fantasy", 14), ("📜 History", 36),
            ("👻 Horror", 27), ("🎵 Music", 10402), ("🔍 Mystery", 9648),
            ("❤️ Romance", 10749), ("🚀 Sci-Fi", 878), ("😱 Thriller", 53),
            ("📺 TV Movie", 10770), ("⚔️ War", 10752), ("🤠 Western", 37),
        ):
            cats.append({"title": label, "url": "sflix_movie_genre_{}".format(genre_id), "type": "category"})

        cats.append({"title": "🎭 Series Genres", "url": "", "type": "separator"})
        for label, genre_id in (
            ("💥 Action & Adventure", 10759), ("🎌 Animation", 16),
            ("😂 Comedy", 35), ("🔪 Crime", 80), ("🎭 Drama", 18),
            ("👻 Horror", 27), ("🔍 Mystery", 9648), ("❤️ Romance", 10749),
            ("🚀 Sci-Fi & Fantasy", 10765),
        ):
            cats.append({"title": label, "url": "sflix_series_genre_{}".format(genre_id), "type": "category"})

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
        if action in ("top", "trending"):
            _full = "_".join(parts[2:])
            if _full.startswith("top_rated"):
                action = "top_rated"
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
        elif action == "latest":
            # Confirmed: /latest-movies -> TMDB /movie/now_playing.
            path = "/movie/now_playing"
        elif action == "popular":
            if tmdb_media_type == "tv":
                # Confirmed: /tv-series -> /discover/tv?sort_by=popularity.desc
                # (not TMDB's own /tv/popular endpoint).
                path = "/discover/tv"
                params["sort_by"] = "popularity.desc"
            else:
                path = "/movie/popular"
        elif action == "top_rated":
            path = "/{}/top_rated".format(tmdb_media_type)
        elif action == "upcoming":
            path = "/movie/upcoming"
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
                "url": "sflix_{}_{}".format(item_type, r.get("id")),
                "type": item_type,
                "year": year,
                "rating": self._rating(r)
            })

        total_pages = data.get("total_pages", 1)
        current_page = data.get("page", page)
        if action == "trending" and current_page == 1:
            # [TOP 10] mirrors the site's own "TOP 01".."TOP 10" poster
            # badges on its trending row: TMDB's trending endpoint returns
            # titles in rank order, so the rank is just the position on
            # page 1. Rendered through the home grid's existing site-label
            # chip (item["label"], top-left under the year badge), so no
            # skin/widget changes are needed. "rank" is kept as a plain int
            # for anything that wants it later.
            for _i, _it in enumerate(items[:10], start=1):
                _it["rank"] = _i
                _it["label"] = "TOP {:02d}".format(_i)
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
                    "url": "sflix_{}_{}".format(item_type, r.get("id")),
                    "type": item_type,
                    "year": year,
                    "rating": self._rating(r)
                })
        return items

    # ─── Servers ──────────────────────────────────────────────────────────

    def _servers_for(self, tmdb_id, season=None, episode=None):
        """Build the 6-server list SFlix itself offers for a given TMDB
        id, in the site's own server-tab order (see module docstring for
        which of these have a real resolver and which fall through to the
        generic embed fallback).
        """
        if season and episode:
            return [
                {"name": "Server 1 (MoviesAPI)", "url": "https://moviesapi.to/tv/{}/{}/{}".format(tmdb_id, season, episode), "quality": "HD"},
                {"name": "Server 4 (VidFast)", "url": "https://vidfast.vc/tv/{}/{}/{}".format(tmdb_id, season, episode), "quality": "HD"},
            ]
        return [
            {"name": "Server 1 (MoviesAPI)", "url": "https://moviesapi.to/movie/{}".format(tmdb_id), "quality": "HD"},
            {"name": "Server 2 (VidCore)", "url": "https://vidcore.net/movie/{}?autoPlay=true".format(tmdb_id), "quality": "HD"},
            {"name": "Server 3 (YapGrid)", "url": "https://yapgrid.com/embed/movie/{}".format(tmdb_id), "quality": "HD"},
            {"name": "Server 4 (VidFast)", "url": "https://vidfast.vc/movie/{}?autoPlay=true".format(tmdb_id), "quality": "HD"},
            {"name": "Server 5 (VidSrc-Embed)", "url": "https://vidsrc-embed.ru/embed/movie/{}".format(tmdb_id), "quality": "HD"},
            {"name": "Server 6 (EmbedMaster)", "url": "https://embedmaster.link/movie/{}".format(tmdb_id), "quality": "HD"},
        ]

    # ─── Detail pages ─────────────────────────────────────────────────────

    def get_page(self, url, m_type=None):
        if url.startswith("sflix_tv_") or url.startswith("sflix_series_"):
            tmdb_id = url.replace("sflix_tv_", "").replace("sflix_series_", "")
            data = self._tmdb_request("/tv/{}".format(tmdb_id))
            seasons = []
            for s in data.get("seasons", []):
                if s.get("season_number") == 0:
                    continue
                seasons.append({
                    "title": s.get("name", "Season {}".format(s.get("season_number"))),
                    "url": "sflix_season_{}_{}".format(tmdb_id, s.get("season_number")),
                    "type": "season"
                })
            return {
                "title": data.get("name", "TV Show"),
                "plot": data.get("overview", ""),
                "poster": self._poster(data.get("poster_path", "")),
                "servers": [],
                "items": seasons,
                "type": "series",
                "year": (data.get("first_air_date") or "")[:4],
                "rating": self._rating(data),
                "genres": self._genres(data)
            }

        elif url.startswith("sflix_season_"):
            parts = url.split("_")
            tmdb_id = parts[2]
            season_num = parts[3]
            data = self._tmdb_request("/tv/{}/season/{}".format(tmdb_id, season_num))
            episodes = []
            for ep in data.get("episodes", []):
                episodes.append({
                    "title": "S{:02d}E{:02d}: {}".format(int(season_num), ep.get("episode_number"), ep.get("name")),
                    "url": "sflix_episode_{}_{}_{}".format(tmdb_id, season_num, ep.get("episode_number")),
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

        elif url.startswith("sflix_episode_"):
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
                "type": "episode",
                "year": (ep_data.get("air_date") or "")[:4],
                "rating": self._rating(ep_data)
            }

        elif url.startswith("sflix_movie_"):
            tmdb_id = url.replace("sflix_movie_", "")
            servers = self._servers_for(tmdb_id)
            movie_data = self._tmdb_request("/movie/{}".format(tmdb_id))
            return {
                "title": movie_data.get("title") or movie_data.get("name") or "Movie",
                "plot": movie_data.get("overview", ""),
                "poster": self._poster(movie_data.get("poster_path", "")),
                "servers": servers,
                "items": [],
                "type": "movie",
                "year": (movie_data.get("release_date") or "")[:4],
                "rating": self._rating(movie_data),
                "genres": self._genres(movie_data)
            }

        return None
