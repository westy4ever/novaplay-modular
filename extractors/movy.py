# -*- coding: utf-8 -*-
"""
Movy Extractor (www.movy.sx)

Built from 8 page snapshots (home, /browse/movie, /browse/tv, /browse/anime
and four states of the "Matchbox the Movie" watch page, TMDB id 1148365).

What the snapshots show:
  * Movy is a Next.js (pages router) site keyed on TMDB ids:
    /movie/<tmdb_id>, /tv/<tmdb_id>. The home page ships its rows inside
    <script id="__NEXT_DATA__"> (props.pageProps):
        trendingSections  -> popularMovies, popularShowTV       ("TOP 10 on Movy")
        defaultSections   -> trending, topratedmovie, topratedtv
        streamingSections -> netflix, primevideo, hbomax, disney, appletv,
                             paramount, hulu (only netflix had items)
        upcomingTv        -> "Current & upcoming TV shows" (with episodeCode)
        recent4k          -> "Recently added in 4K"
    Each item: {id, title, poster, slug "/movie/<id>", mediaType, rating,
    release_date, genre_ids}. Posters are wsrv.nl-wrapped TMDB images in
    webp; we unwrap them to the plain image.tmdb.org JPEG (the box's picture
    loader can't show webp).
  * The /browse pages are rendered in the browser, so their lists come from
    TMDB here (same endpoints SFlix uses).
  * Movy's own player (city-named servers Miami, Boise, Vegas ... with
    2160p/1080p/720p/480p) gets its streams from api.wecollege.net; the
    seed + cipher protocol is in movy_api.py (from a Stream Recorder Pro
    capture). Those servers come first, followed by the TMDB-id embed
    servers that already work on the box (the SFlix list: VidSrc-Embed,
    EmbedMaster, YapGrid, MoviesAPI, VidFast, VidCore), then Movy's dubbed
    servers.

Item URLs use the SFlix scheme with a "movy_" prefix, so all the TMDB
paging/season/episode logic is shared with SflixProExtractor.
"""
import json
import re
import time
import urllib.parse

from .base import fetch, log
from .sflix_pro import SflixProExtractor

_HOME_TTL = 30 * 60

# (pageProps key, section name or None, menu title, item type)
_HOME_ROWS = (
    ("trendingSections", "popularMovies", "🔟 TOP 10 Movies on Movy", "movie"),
    ("trendingSections", "popularShowTV", "🔟 TOP 10 Shows on Movy", "series"),
    ("recent4k", None, "🔴 Recently added in 4K", None),
    ("defaultSections", "trending", "📈 Trending This Week", None),
    ("upcomingTv", None, "📅 Current & upcoming TV shows", "series"),
    ("streamingSections", "netflix", "🟥 Only on Netflix", None),
    ("streamingSections", "primevideo", "🟦 Only on Prime Video", None),
    ("streamingSections", "hbomax", "🟪 Only on HBO Max", None),
    ("streamingSections", "disney", "🏰 Only on Disney+", None),
    ("streamingSections", "appletv", "🍏 Only on Apple TV+", None),
    ("streamingSections", "paramount", "⛰️ Only on Paramount+", None),
    ("streamingSections", "hulu", "🟩 Only on Hulu", None),
    ("defaultSections", "topratedmovie", "⭐ Top Rated Movies", "movie"),
    ("defaultSections", "topratedtv", "⭐ Top Rated Series", "series"),
)


def _unwrap_poster(url, size="w342"):
    """wsrv.nl/?url=<tmdb jpg>&output=webp -> the TMDB jpg itself."""
    if not url:
        return ""
    try:
        q = urllib.parse.urlparse(url)
        if "wsrv.nl" in q.netloc:
            inner = urllib.parse.parse_qs(q.query).get("url", [""])[0]
            if inner:
                url = inner
    except Exception:
        pass
    if url.startswith("//"):
        url = "https:" + url
    return url


class MovyExtractor(SflixProExtractor):
    PREFIX = "movy_"

    def __init__(self):
        super().__init__()
        self.main_url = "https://www.movy.sx"
        self._home = None
        self._home_ts = 0

    # ─── URL prefix helpers (share SFlix's TMDB logic) ───────────────────

    def _to_sflix(self, url):
        return "sflix_" + url[len(self.PREFIX):] if url.startswith(self.PREFIX) else url

    def _from_sflix(self, obj):
        if isinstance(obj, list):
            for it in obj:
                self._from_sflix(it)
        elif isinstance(obj, dict):
            u = obj.get("url")
            if isinstance(u, str) and u.startswith("sflix_"):
                obj["url"] = self.PREFIX + u[len("sflix_"):]
            for k in ("items",):
                if isinstance(obj.get(k), list):
                    self._from_sflix(obj[k])
        return obj

    # ─── Home page rows (__NEXT_DATA__) ───────────────────────────────────

    def _home_props(self):
        if self._home is not None and time.time() - self._home_ts < _HOME_TTL:
            return self._home
        props = {}
        try:
            html = fetch(self.main_url + "/", referer=self.main_url + "/") or ""
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
            if m:
                props = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
        except Exception as e:
            log("[Movy] home fetch failed: {}".format(e))
        if props:
            self._home, self._home_ts = props, time.time()
        return props

    @staticmethod
    def _row_items(props, key, name):
        val = props.get(key)
        if name is None:
            return val if isinstance(val, list) else []
        for sec in val or []:
            if isinstance(sec, dict) and sec.get("name") == name:
                return sec.get("movies") or []
        return []

    def _item(self, r, force_type=None):
        mt = (r.get("mediaType") or "").lower()
        if not mt:
            mt = "tv" if "/tv/" in (r.get("slug") or "") else "movie"
        item_type = force_type or ("series" if mt == "tv" else "movie")
        try:
            rating = "{:.1f}".format(float(r.get("rating") or 0)) if float(r.get("rating") or 0) > 0 else ""
        except (TypeError, ValueError):
            rating = ""
        it = {
            "title": r.get("title") or r.get("name") or "Unknown",
            "poster": _unwrap_poster(r.get("poster") or ""),
            "url": "{}{}_{}".format(self.PREFIX, item_type, r.get("id")),
            "type": item_type,
            "year": (r.get("release_date") or "")[:4],
            "rating": rating,
        }
        if r.get("episodeCode"):
            it["label"] = r["episodeCode"]
        return it

    # ─── Categories ───────────────────────────────────────────────────────

    def get_categories(self, mtype="movie"):
        cats = [{"title": "🏠 Movy Home", "url": "", "type": "separator"}]
        props = self._home_props()
        for i, (key, name, title, _t) in enumerate(_HOME_ROWS):
            # With the home page unreachable, still list the rows; each one
            # falls back to a TMDB equivalent when opened.
            if props and not self._row_items(props, key, name):
                continue
            if not props and i not in self._ROW_FALLBACK:
                continue
            cats.append({"title": title, "url": "movy_home_{}".format(i), "type": "category"})
        cats.append({"title": "🎌 Anime", "url": "", "type": "separator"})
        cats.append({"title": "🎌 Top Airing Anime", "url": "movy_series_genre_16", "type": "category"})
        cats.append({"title": "🎌 Anime Movies", "url": "movy_movie_genre_16", "type": "category"})
        for c in super().get_categories(mtype):
            if c.get("url") == "sflix_series_genre_16" and c.get("title", "").endswith("Anime"):
                continue
            cats.append(self._from_sflix(dict(c)))
        return cats

    # TMDB stand-ins for home rows when movy.sx can't be reached.
    _ROW_FALLBACK = {
        0: "sflix_movie_trending_week", 1: "sflix_series_trending_week",
        3: "sflix_movie_trending_week", 4: "sflix_series_popular",
        12: "sflix_movie_top_rated", 13: "sflix_series_top_rated",
    }

    def get_category_items(self, url, page=1):
        m = re.match(r'movy_home_(\d+)$', url)
        if m:
            idx = int(m.group(1))
            if idx >= len(_HOME_ROWS):
                return []
            key, name, _title, force = _HOME_ROWS[idx]
            rows = self._row_items(self._home_props(), key, name)
            if rows:
                items = [self._item(r, force) for r in rows if isinstance(r, dict) and r.get("id")]
                if key == "trendingSections":
                    for n, it in enumerate(items[:10], start=1):
                        it["rank"] = n
                        it["label"] = "TOP {:02d}".format(n)
                return items
            fb = self._ROW_FALLBACK.get(idx)
            return self._from_sflix(super().get_category_items(fb, page)) if fb else []
        return self._from_sflix(super().get_category_items(self._to_sflix(url), page))

    def search(self, query, page=1):
        return self._from_sflix(super().search(query, page))

    def get_page(self, url, m_type=None):
        res = super().get_page(self._to_sflix(url), m_type)
        if res and res.get("servers"):
            try:
                own = self._own_servers(url, res)
            except Exception as e:
                log("[Movy] own servers: {}".format(e))
                own = []
            if own:
                # Movy's English/original-audio servers first, then the shared
                # embed servers, then the dubbed (German/French/Hindi/Spanish) ones.
                dub = [s for s in own if "audio" in s["name"]]
                main = [s for s in own if "audio" not in s["name"]]
                res["servers"] = main + res["servers"] + dub
        return self._from_sflix(res) if res else res

    def _own_servers(self, url, res):
        """Movy's city servers (api.wecollege.net, see movy_api.py)."""
        from .movy_api import server_urls
        if url.startswith("movy_movie_"):
            tmdb_id = url[len("movy_movie_"):]
            d = self._tmdb_request("/movie/{}".format(tmdb_id))
            return server_urls(tmdb_id, d.get("title") or res.get("title"),
                               (d.get("release_date") or "")[:4], d.get("imdb_id") or "")
        if url.startswith("movy_episode_"):
            _p = url.split("_")
            tmdb_id, season, episode = _p[2], _p[3], _p[4]
            d = self._tmdb_request("/tv/{}".format(tmdb_id), {"append_to_response": "external_ids"})
            return server_urls(tmdb_id, d.get("name") or "", (d.get("first_air_date") or "")[:4],
                               (d.get("external_ids") or {}).get("imdb_id") or "",
                               season=season, episode=episode,
                               total_seasons=d.get("number_of_seasons"))
        return []
