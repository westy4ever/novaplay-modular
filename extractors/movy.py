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

from .base import fetch, fetch_json, log
from .sflix_pro import SflixProExtractor

_HOME_TTL = 30 * 60

# Movy's anime catalogue (anime.vidy.st, the AniKoto API behind /browse/anime).
# Its own anime player (api.wecollege.net/anikoto/sources-id) returns no
# sources at the moment, so an anime title is matched to its TMDB entry and
# played through Movy's city servers like any other show.
_ANIME_API = "https://anime.vidy.st/api"
_ANIME_GENRES = (
    "Action", "Adventure", "Cars", "Comedy", "Demons", "Drama", "Fantasy",
    "Game", "Harem", "Historical", "Horror", "Isekai", "Josei", "Kids",
    "Magic", "Martial Arts", "Mecha", "Military", "Music", "Mystery",
    "Parody", "Police", "Psychological", "Romance", "Samurai", "School",
    "Sci-Fi", "Seinen", "Shoujo", "Shounen", "Slice of Life", "Space",
    "Sports", "Super Power", "Supernatural", "Thriller", "Vampire",
)
# "Season 2", "2nd Season", "Part 2", "(2021)" ... -> stripped for TMDB search
_SEASON_RE = re.compile(
    r'\s*(?:[:\-]\s*)?(?:season\s*(\d+)|(\d+)(?:st|nd|rd|th)\s+season|'
    r'part\s*\d+|cour\s*\d+|\(\d{4}\))\s*$', re.I)

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
            # fetch() returns (html, final_url) -- the tuple itself used to be
            # searched, so the home rows always fell back to TMDB
            res = fetch(self.main_url + "/", referer=self.main_url + "/")
            html = (res[0] if isinstance(res, tuple) else res) or ""
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
        cats.append({"title": "🎌 Movy Anime", "url": "", "type": "separator"})
        cats.append({"title": "🎌 Featured Anime", "url": "movy_anime_featured", "type": "category"})
        cats.append({"title": "🎌 Top Airing Anime (TMDB)", "url": "movy_series_genre_16", "type": "category"})
        cats.append({"title": "🎌 Anime Movies (TMDB)", "url": "movy_movie_genre_16", "type": "category"})
        for g in _ANIME_GENRES:
            cats.append({"title": "🎌 Anime · {}".format(g),
                         "url": "movy_anime_genre_{}".format(self._genre_slug(g)),
                         "type": "category"})
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
        if url == "movy_anime_featured":
            return self._anime_featured(page)
        if url.startswith("movy_anime_genre_"):
            return self._anime_list("/genre/{}?page={}".format(
                url[len("movy_anime_genre_"):], int(page or 1)))
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
        if url.startswith("movy_anime_") and not url.startswith("movy_anime_genre_"):
            return self._anime_page(url[len("movy_anime_"):], m_type)
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

    # ─── Movy anime (anime.vidy.st) ───────────────────────────────────────

    _anime_map = {}      # slug -> (kind, tmdb_id, season) or None

    @staticmethod
    def _genre_slug(name):
        return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

    def _anime_api(self, path):
        d = fetch_json(_ANIME_API + path, referer=self.main_url + "/",
                       extra_headers={"Origin": self.main_url}) or {}
        return d.get("results") if isinstance(d, dict) else None

    def _anime_item(self, r):
        # genre API: "dandadan-lzcmw/ep-1"; browse grid: "/anime/dandadan-lzcmw"
        slug = re.sub(r'^/?anime/', '', r.get("slug") or "").split("/")[0] or str(r.get("id") or "")
        if not slug:
            return None
        kind = (r.get("type") or r.get("animeType") or "").upper()
        cnt = r.get("animeEpisodeCount") or {}
        sub = r.get("sub", cnt.get("sub")) or 0
        dub = r.get("dub", cnt.get("dub")) or 0
        try:
            rating = "{:.1f}".format(float(r.get("rating") or 0)) if float(r.get("rating") or 0) > 0 else ""
        except (TypeError, ValueError):
            rating = ""
        it = {
            "title": r.get("title") or r.get("name") or slug,
            "poster": r.get("poster") or "",
            "url": "movy_anime_" + slug,
            "type": "movie" if kind == "MOVIE" else "series",
            "year": "",
            "rating": rating,
        }
        lab = []
        if kind and kind not in ("TV",):
            lab.append(kind)
        if sub:
            lab.append("SUB {}".format(sub))
        if dub:
            lab.append("DUB {}".format(dub))
        if lab:
            it["label"] = " · ".join(lab)
        return it

    def _anime_list(self, path):
        res = self._anime_api(path) or {}
        out = []
        for r in res.get("data") or []:
            if isinstance(r, dict):
                it = self._anime_item(r)
                if it:
                    out.append(it)
        return out

    def _anime_featured(self, page=1):
        if int(page or 1) == 1:
            try:
                res = fetch(self.main_url + "/browse/anime", referer=self.main_url + "/")
                html = (res[0] if isinstance(res, tuple) else res) or ""
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
                if m:
                    pp = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
                    items = [self._anime_item(r) for r in pp.get("initialGrid") or []
                             if isinstance(r, dict)]
                    items = [i for i in items if i]
                    if items:
                        return items
            except Exception as e:
                log("[Movy] anime browse: {}".format(e))
        return self._anime_list("/genre/action?page={}".format(int(page or 1)))

    @staticmethod
    def _norm(t):
        return re.sub(r'[^a-z0-9]+', ' ', (t or "").lower()).strip()

    def _match_tmdb(self, info):
        """Find the TMDB entry for an AniKoto title -> (kind, id, season)."""
        title = info.get("title") or ""
        kind = "movie" if (info.get("type") or "").upper() == "MOVIE" else "tv"
        m = re.search(r'(\d{4})', info.get("aired") or info.get("premiered") or "")
        year = m.group(1) if m else ""
        backdrop = (info.get("backgroundImage") or "").rsplit("/", 1)[-1]
        season = None
        sm = _SEASON_RE.search(title)
        if sm and (sm.group(1) or sm.group(2)):
            season = int(sm.group(1) or sm.group(2))
        names = []
        for n in [title, info.get("japaneseTitle")] + (info.get("altNames") or "").replace(";", ",").split(","):
            n = (n or "").strip()
            base = _SEASON_RE.sub("", n).strip(" :-")
            for x in (base, n):
                if x and x.lower() not in [y.lower() for y in names]:
                    names.append(x)
        best, best_score = None, 0
        for q in names[:5]:
            for k in ((kind, "tv") if kind == "movie" else ("tv", "movie")):
                params = {"query": q}
                if k == kind and year and season is None:
                    params["first_air_date_year" if k == "tv" else "year"] = year
                d = self._tmdb_request("/search/{}".format(k), params)
                for i, r in enumerate((d.get("results") or [])[:8]):
                    sc = 0
                    if backdrop and (r.get("backdrop_path") or "").endswith(backdrop):
                        sc += 100
                    if 16 in (r.get("genre_ids") or []):
                        sc += 20
                    if "JP" in (r.get("origin_country") or []) or r.get("original_language") == "ja":
                        sc += 15
                    nm = self._norm(r.get("name") or r.get("title"))
                    on = self._norm(r.get("original_name") or r.get("original_title"))
                    if self._norm(q) in (nm, on):
                        sc += 25
                    ry = (r.get("first_air_date") or r.get("release_date") or "")[:4]
                    if year and ry == year:
                        sc += 10
                    if k == kind:
                        sc += 5
                    sc -= i
                    if sc > best_score:
                        best, best_score = (k, r.get("id")), sc
                if best_score >= 100:
                    break
            if best_score >= 100:
                break
        if not best or best_score < 30:
            return None
        k, tid = best
        if k == "tv" and season:
            d = self._tmdb_request("/tv/{}".format(tid))
            nums = [x.get("season_number") for x in d.get("seasons") or []]
            if season not in nums:
                season = None
        return (k, tid, season if k == "tv" else None)

    def _anime_page(self, slug, m_type=None):
        info = self._anime_api("/info?id={}".format(urllib.parse.quote(slug))) or {}
        if slug not in self._anime_map:
            try:
                self._anime_map[slug] = self._match_tmdb(info) if info else None
            except Exception as e:
                log("[Movy] anime match {}: {}".format(slug, e))
                self._anime_map[slug] = None
        hit = self._anime_map.get(slug)
        log("[Movy] anime {} -> {}".format(slug, hit))
        if hit:
            k, tid, season = hit
            if k == "movie":
                target = "movy_movie_{}".format(tid)
            elif season:
                target = "movy_season_{}_{}".format(tid, season)
            else:
                target = "movy_series_{}".format(tid)
            res = self.get_page(target, m_type)
            if res:
                if info.get("poster") and not res.get("poster"):
                    res["poster"] = info["poster"]
                if season and info.get("title"):
                    res["title"] = "{} · {}".format(info["title"], res.get("title") or "")
                return res
        plot = info.get("synopsis") or ""
        return {
            "title": info.get("title") or slug,
            "plot": ("No TMDB match for this title, so it has no server yet.\n\n" + plot).strip(),
            "poster": info.get("poster") or "",
            "servers": [],
            "items": [],
            "type": "movie" if (info.get("type") or "").upper() == "MOVIE" else "series",
            "genres": ", ".join(info.get("genres") or []),
        }
