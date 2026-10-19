# -*- coding: utf-8 -*-
"""
Cinejoy Extractor (cinejoy.pk)

Built from a Stream Recorder Pro capture (2026-10-19: home, Netflix provider
page, /movies, /series, Spider-Man: Brand New Day 969681 playing).

What the capture shows:
  * Cinejoy is a SvelteKit app; every list comes straight from TMDB
    (api.themoviedb.org with the site's own key), so the catalogue here is
    built from TMDB too and shares SflixProExtractor's TMDB code:
        trending/day, top_rated, discover?with_watch_providers=<id>
        ("Browse by Provider": Netflix 8, Prime Video 9, Disney+ 337, ...)
  * Its curated collections come from lists.wing.st/joy (no key):
        /joy                      -> {collections:[{id,title,...}]}
        /joy/<id>?limit=25&offset -> {items:[{type, title, year, poster,
                                       ids:{tmdb, imdb}, score}]}
  * Item URLs on the site: /movie/<tmdb>-<slug>, /tv/<tmdb>-<slug>.
  * Its own player (servers Nebula, Lisbon, Solara, Athens) asks
    POST api.wing.st/g with a request sealed by crush.wasm (seal_request)
    and gets an encrypted reply -- not reproducible without that WASM.
    The Nebula CDN (nebula.bright67.online) is the same one cinesrc.st uses
    (also behind a WASM proof-of-work). So the detail pages use the
    TMDB-id players that resolve on the box (the Vidsrc list: Vidsrc,
    Bolt, VidCore, VidFast, Nest, MoviesAPI, Vidplay).
"""
import re

from .base import fetch_json, log
from .sflix_pro import SflixProExtractor

_LISTS_API = "https://lists.wing.st/joy"
_PAGE = 25

# "Browse by Provider" row on the home page (TMDB watch-provider ids)
_PROVIDERS = (
    (8, "Netflix"), (9, "Prime Video"), (337, "Disney+"), (350, "Apple TV+"),
    (15, "Hulu"), (1899, "HBO Max"), (2303, "Paramount+"), (386, "Peacock"),
    (283, "Crunchyroll"),
)

# lists.wing.st/joy as captured (used when the API can't be reached)
_COLLECTIONS = (
    ("oscar-nominees-best-picture", "Oscar Nominees for Best Picture"),
    ("psychological-thrillers", "Psychological Thrillers"),
    ("cannes-film-festival", "Cannes Film Festival"),
    ("halloween-top-100", "Top 100 Halloween Movies"),
    ("rotten-tomatoes-best-of-all-time", "Rotten Tomatoes: Best of All Time"),
    ("mindfuck-movies", "Mind-Bending Movies"),
    ("based-on-a-true-story", "Based on a True Story"),
)


class CinejoyExtractor(SflixProExtractor):
    PREFIX = "cinejoy_"

    def __init__(self):
        super().__init__()
        self.main_url = "https://cinejoy.pk"
        self._lists = None

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
            if isinstance(obj.get("items"), list):
                self._from_sflix(obj["items"])
        return obj

    # ─── Servers: the TMDB-id players that resolve on the box ────────────

    def _servers_for(self, tmdb_id, season=None, episode=None):
        from .vidsrc import VidsrcExtractor
        return VidsrcExtractor._servers(VidsrcExtractor, tmdb_id, season, episode)

    # ─── Categories ───────────────────────────────────────────────────────

    def _collections(self):
        if self._lists is None:
            d = fetch_json(_LISTS_API, referer=self.main_url + "/",
                           extra_headers={"Origin": self.main_url}) or {}
            cols = [(c.get("id"), c.get("title")) for c in d.get("collections") or []
                    if isinstance(c, dict) and c.get("id")]
            self._lists = cols or list(_COLLECTIONS)
        return self._lists

    def get_categories(self, mtype="movie"):
        cats = [
            {"title": "🏠 Cinejoy", "url": "", "type": "separator"},
            {"title": "📈 Trending Movies Today", "url": "cinejoy_movie_trending_day", "type": "category"},
            {"title": "📈 Trending Shows Today", "url": "cinejoy_series_trending_day", "type": "category"},
            {"title": "🆕 New Movies", "url": "cinejoy_movie_latest", "type": "category"},
            {"title": "📺 On the Air", "url": "cinejoy_series_on_the_air", "type": "category"},
            {"title": "⭐ Top Rated Movies", "url": "cinejoy_movie_top_rated", "type": "category"},
            {"title": "⭐ Top Rated Shows", "url": "cinejoy_series_top_rated", "type": "category"},
            {"title": "📡 Browse by Provider", "url": "", "type": "separator"},
        ]
        for pid, name in _PROVIDERS:
            cats.append({"title": "🎬 {} · Movies".format(name),
                         "url": "cinejoy_prov_movie_{}".format(pid), "type": "category"})
            cats.append({"title": "📺 {} · Shows".format(name),
                         "url": "cinejoy_prov_series_{}".format(pid), "type": "category"})
        cats.append({"title": "📚 Collections", "url": "", "type": "separator"})
        for cid, title in self._collections():
            cats.append({"title": "📚 " + (title or cid), "url": "cinejoy_list_" + cid,
                         "type": "category"})
        cats.append({"title": "🎭 Genres", "url": "", "type": "separator"})
        # SFlix's genre rows (TMDB), relabelled for this site
        for c in super().get_categories(mtype):
            u = c.get("url") or ""
            if "_genre_" in u and c.get("type") == "category":
                cats.append(self._from_sflix(dict(c)))
        return cats

    def get_category_items(self, url, page=1):
        page = int(page or 1)
        m = re.match(r'cinejoy_prov_(movie|series)_(\d+)$', url)
        if m:
            kind = "tv" if m.group(1) == "series" else "movie"
            data = self._tmdb_request("/discover/{}".format(kind), {
                "with_watch_providers": m.group(2), "watch_region": "US",
                "sort_by": "popularity.desc", "page": page, "language": "en-US"})
            return [self._tmdb_item(r, m.group(1)) for r in data.get("results") or []]
        if url.startswith("cinejoy_list_"):
            return self._list_items(url[len("cinejoy_list_"):], page)
        if url == "cinejoy_series_on_the_air":
            data = self._tmdb_request("/tv/on_the_air", {"page": page, "language": "en-US"})
            return [self._tmdb_item(r, "series") for r in data.get("results") or []]
        return self._from_sflix(super().get_category_items(self._to_sflix(url), page))

    def _tmdb_item(self, r, item_type):
        return {
            "title": r.get("title") or r.get("name") or "Unknown",
            "poster": self._poster(r.get("poster_path", "")),
            "url": "{}{}_{}".format(self.PREFIX, item_type, r.get("id")),
            "type": item_type,
            "year": (r.get("release_date") or r.get("first_air_date") or "")[:4],
            "rating": self._rating(r),
        }

    def _list_items(self, cid, page):
        d = fetch_json("{}/{}?limit={}&offset={}".format(_LISTS_API, cid, _PAGE, (page - 1) * _PAGE),
                       referer=self.main_url + "/", extra_headers={"Origin": self.main_url}) or {}
        out = []
        for r in d.get("items") or []:
            if not isinstance(r, dict):
                continue
            tid = (r.get("ids") or {}).get("tmdb")
            if not tid:
                continue
            kind = "movie" if (r.get("type") or "movie") == "movie" else "series"
            try:
                score = "{:.1f}".format(float(r.get("score"))) if r.get("score") else ""
            except (TypeError, ValueError):
                score = ""
            poster = (r.get("poster") or "").replace("/w200/", "/w342/")
            out.append({
                "title": r.get("title") or "Unknown",
                "poster": poster,
                "url": "{}{}_{}".format(self.PREFIX, kind, tid),
                "type": kind,
                "year": str(r.get("year") or ""),
                "rating": score,
            })
        return out

    # ─── Search / detail pages ────────────────────────────────────────────

    def search(self, query, page=1):
        return self._from_sflix(super().search(query, page))

    def get_page(self, url, m_type=None):
        res = super().get_page(self._to_sflix(url), m_type)
        return self._from_sflix(res) if res else res
