# -*- coding: utf-8 -*-
"""
ZetMoon Extractor (zetmoon.live)

Built from a Stream Recorder Pro capture (2026-10-20: home, search, and
"Jackpot!" (tmdb 1094138) with ~30 of its server buttons clicked).

What the capture shows:
  * Next.js site keyed on TMDB ids: /watch?type=movie|tv&id=<tmdb>.
    The home rows are plain TMDB lists: Trending Today / Week, Hot now,
    Popular Movies, Top Rated, Trending / Top Series, and provider rows
    (Netflix, Amazon, Apple, Disney, Paramount). So the catalogue here is
    built from TMDB with SflixProExtractor's code, like Cinejoy and Movy.
  * Its player is a row of ~30 buttons, each loading another site's embed
    (Spark=vidspark, Vidlux, Vidflix, Vidy, Vix=vixsrc, Cine=cinesrc,
    Vidsrcme=vsembed/stellarconductornexus, Vidfast, Mapple, Orion=insertunit,
    Tea/Seefa/Killjoy/Raze=kaembed, Filesun/Hunu=embedsun, ...). Most of the
    ZetMoon-only ones hand out their link through per-request obfuscated
    worker keys or encrypted APIs. The detail pages therefore list the
    players that resolve on the box (the Vidsrc list, named after ZetMoon's
    buttons where they are the same player).
  * The buttons are in two groups. "External Player" (Cinesrc ... Mapple)
    embeds the other site's page. "Main Player" (Tea, Loto, Hunu, Seefa,
    reelvault, Holly, Orion, Killjoy, Raze, Zeon, Levi) is ZetMoon's own
    player fed by scrapers that read other sites through a CORS relay
    (proxy.semoteb377.workers.dev?id=base64(hex(url XOR 0x7c))):
        Tea      lordflix.app -> kembed worker (AES-wrapped key) -> kaembed.net
        Loto/Hunu filesun.sbs/embed/movie/<imdb> -> sun worker -> embedsun.cc
        Holly    hollymoviehd.cc (WP admin-ajax) -> goodstream.cc
        Orion    api.insertunit.ws/embed/imdb/<imdb> (separate audio tracks)
        Killjoy  m4uhdtv.tv,  Raze lucky-smoke worker,  Zeon fzmovies.live +
                 vidapi.xyz/embed/movie/<tmdb> -> vscdn.xyz
    Zeon's vidapi.xyz route is the only one that needs no per-site key.
"""
import re

from .base import log
from .sflix_pro import SflixProExtractor

# ZetMoon home: provider rows (TMDB watch-provider ids)
_PROVIDERS = (
    (8, "Netflix"), (9, "Amazon Prime"), (350, "Apple TV+"), (337, "Disney+"),
    (2303, "Paramount+"),
)

# (button name, movie url, tv url); {i}=tmdb id, {s}/{e}=season/episode.
# All of these resolved on the box in the Vidsrc / Cinejoy tests.
_SERVERS = (
    ("Vidsrcme", "https://vsembed.ru/embed/movie/{i}", "https://vsembed.ru/embed/tv/{i}/{s}/{e}"),
    ("Bolt", "https://vidbolt.xyz/movie/{i}", "https://vidbolt.xyz/tv/{i}/{s}/{e}"),
    ("Vidfast", "https://vidfast.vc/movie/{i}?autoPlay=true", "https://vidfast.vc/tv/{i}/{s}/{e}?autoPlay=true"),
    ("VidCore", "https://vidcore.io/movie/{i}?autoPlay=true", "https://vidcore.io/tv/{i}/{s}/{e}?autoPlay=true"),
    ("Nest", "https://vidnest.fun/movie/{i}", "https://vidnest.fun/tv/{i}/{s}/{e}"),
    ("MoviesAPI", "https://moviesapi.to/movie/{i}", "https://moviesapi.to/tv/{i}/{s}/{e}"),
    ("Vidplay", "https://vaplayer.ru/embed/movie/{i}", "https://vaplayer.ru/embed/tv/{i}/{s}/{e}"),
    # Main Player "Zeon" (vidapi.xyz -> vscdn.xyz) left out: on 2026-10-21 vidapi.xyz's
    # Apache answered 403 both to the box and through ZetMoon's relay
    # (hosts.resolve_vidapi still handles it if that changes).
)


class ZetmoonExtractor(SflixProExtractor):
    PREFIX = "zetmoon_"

    def __init__(self):
        super().__init__()
        self.main_url = "https://zetmoon.live"

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

    def _servers_for(self, tmdb_id, season=None, episode=None):
        out = []
        for name, movie, tv in _SERVERS:
            tpl = tv if season else movie
            out.append({"name": "{} ({})".format(name, len(out) + 1),
                        "url": tpl.format(i=tmdb_id, s=season, e=episode), "quality": "HD"})
        return out

    # ─── Categories (ZetMoon home rows) ──────────────────────────────────

    def get_categories(self, mtype="movie"):
        cats = [
            {"title": "🌙 ZetMoon", "url": "", "type": "separator"},
            {"title": "🔥 Trending Today", "url": "zetmoon_movie_trending_day", "type": "category"},
            {"title": "📈 Trending This Week", "url": "zetmoon_movie_trending_week", "type": "category"},
            {"title": "🌶️ Hot Now", "url": "zetmoon_movie_latest", "type": "category"},
            {"title": "🎬 Popular Movies", "url": "zetmoon_movie_popular", "type": "category"},
            {"title": "⭐ Top Rated Movies", "url": "zetmoon_movie_top_rated", "type": "category"},
            {"title": "📺 Trending Series", "url": "zetmoon_series_trending_week", "type": "category"},
            {"title": "⭐ Top Series", "url": "zetmoon_series_top_rated", "type": "category"},
            {"title": "📡 Providers", "url": "", "type": "separator"},
        ]
        for pid, name in _PROVIDERS:
            cats.append({"title": "🎬 {} · Movies".format(name),
                         "url": "zetmoon_prov_movie_{}".format(pid), "type": "category"})
            cats.append({"title": "📺 {} · Series".format(name),
                         "url": "zetmoon_prov_series_{}".format(pid), "type": "category"})
        cats.append({"title": "🎭 Genres", "url": "", "type": "separator"})
        for c in super().get_categories(mtype):
            if "_genre_" in (c.get("url") or "") and c.get("type") == "category":
                cats.append(self._from_sflix(dict(c)))
        return cats

    def get_category_items(self, url, page=1):
        page = int(page or 1)
        m = re.match(r'zetmoon_prov_(movie|series)_(\d+)$', url)
        if m:
            kind = "tv" if m.group(1) == "series" else "movie"
            data = self._tmdb_request("/discover/{}".format(kind), {
                "with_watch_providers": m.group(2), "watch_region": "US",
                "sort_by": "popularity.desc", "page": page, "language": "en-US"})
            out = []
            for r in data.get("results") or []:
                out.append({
                    "title": r.get("title") or r.get("name") or "Unknown",
                    "poster": self._poster(r.get("poster_path", "")),
                    "url": "{}{}_{}".format(self.PREFIX, m.group(1), r.get("id")),
                    "type": m.group(1),
                    "year": (r.get("release_date") or r.get("first_air_date") or "")[:4],
                    "rating": self._rating(r),
                })
            return out
        return self._from_sflix(super().get_category_items(self._to_sflix(url), page))

    def search(self, query, page=1):
        return self._from_sflix(super().search(query, page))

    def get_page(self, url, m_type=None):
        res = super().get_page(self._to_sflix(url), m_type)
        return self._from_sflix(res) if res else res
