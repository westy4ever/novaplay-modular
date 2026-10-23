# -*- coding: utf-8 -*-
"""
egydead_ca.py — Extractor for EgyDead / ايجي ديد (egydead.ca)
=============================================================

Confirmed live, 2026-10-04: this is a React/React-Router SPA (confirmed via
`data-discover="true"` attributes in real captured markup, and a bare
fetch() of "/" returning only "You need to have javascript enabled..."),
backed by a clean, well-structured JSON API. Listing and detail pages are
NOT scraped as HTML at all here -- every piece of real data comes from a
directly-fetchable JSON endpoint, confirmed live against the real site:

  Movies listing   /api/v1/channel/2?restriction=&order=created_at:desc
                       &page=<N>&paginate=lengthAware&returnContentOnly=true
  Series listing   /api/v1/channel/series?channelType=channel&restriction=
                       &loader=channelPage   (confirmed real, but its OWN
                       pagination shape was not independently confirmed the
                       same way channel/2's was -- same list-wrapper parsing
                       is used here since both are the same underlying API
                       family, but flagged in case this one differs)
  Title detail     /api/v1/titles/<id>?loader=titlePage
  Season/episodes  /api/v1/titles/<id>/seasons/<N>?loader=seasonPage
  Watch (stream)   /api/v1/watch/<id>

Despite being an SPA, the /watch/<id> PAGE ITSELF (not its API) is also
confirmed server-rendered for its <title>/meta tags (real curl_cffi fetch
returned a real <title> with the movie's name) -- but its actual player
<iframe> is NOT present in that server-rendered HTML at all (confirmed:
searched the raw response, found no <iframe> anywhere). The real embed
only exists in the separate /api/v1/watch/<id> JSON call, confirmed live:

    "video":{"id":25030,...,"src":"https://egybestvid.com/embed-<id>.html",
             "type":"embed","quality":"hd", ...},
    "alternative_videos":[ ... same shape, additional servers if any ... ]

egybestvid.com embeds are already fully handled by the shared
hosts.py -> resolve_iframe_chain() path with NO dedicated resolver needed
at all -- confirmed directly, live: extract_stream() on a real
"https://egybestvid.com/embed-<id>.html" URL returned the real, playable
s1.egybestvid.com HLS stream on its own.

Search  /api/v1/search/<query>?loader=searchPage   -- confirmed live,
            2026-10-13, via a real captured browser session (autocomplete
            fires per keystroke on /api/v1/search/<partial>?loader=
            searchAutocomplete, then the full search on Enter hits this
            same path with loader=searchPage). Returns {"results": [...]}
            with items in the same shape as a channel-listing item.
"""

import re
import json
import sys

from .base import BaseExtractor, fetch, log, urljoin

if sys.version_info[0] == 3:
    from urllib.parse import quote
else:
    from urllib import quote


class EgydeadCaExtractor(BaseExtractor):
    """Extractor for EgyDead / ايجي ديد (egydead.ca)."""

    MAIN_URL = "https://egydead.ca/"
    DOMAINS = [
        "https://egydead.ca/",
    ]

    # Confirmed live, 2026-10-04. channel/2 = Movies, channel/series =
    # Series (note: a string key here, not a number, unlike channel/2 --
    # confirmed real in a captured network request).
    _CATEGORIES = [
        ("🔥 الاكثر مشاهدة", "api/v1/channel/5381"),
        ("🎬 أحدث الأفلام العالمية", "api/v1/channel/2"),
        ("📺 أحدث المسلسلات", "api/v1/channel/series"),
        ("🇹🇷 مسلسلات تركية", "api/v1/channel/5375"),
        ("🇹🇷 افلام تركية", "api/v1/channel/1274"),
        ("💥 افلام اكشن", "api/v1/channel/16"),
        ("💕 افلام رومانسية", "api/v1/channel/18"),
        ("🎭 افلام دراما", "api/v1/channel/6"),
        ("🄿 افلام نتفليكس", "api/v1/channel/19"),
        ("⚔️ افلام حربية", "api/v1/channel/3"),
        ("👻 افلام رعب", "api/v1/channel/17"),
        ("🚀 افلام خيال علمي", "api/v1/channel/15"),
        ("🐉 افلام فانتازيا", "api/v1/channel/7"),
        ("🗺️ افلام مغامرات", "api/v1/channel/2644"),
        ("🎌 افلام ومسلسلات انمي", "api/v1/channel/5382"),
        ("👨‍👩‍👧 افلام عائلية", "api/v1/channel/5"),
        ("🌏 افلام اسيوية", "api/v1/channel/1305"),
        ("🇮🇳 افلام هندي", "api/v1/channel/13"),
    ]

    _PROBE_HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
    }

    def __init__(self):
        super(EgydeadCaExtractor, self).__init__()
        self.main_url = self.MAIN_URL

    # ── helpers ──────────────────────────────────────────────────────────
    def _full_url(self, path):
        if not path:
            return ""
        if path.startswith("http"):
            return path
        return urljoin(self.main_url, path.lstrip("/"))

    def _build_json_url(self, path, **params):
        url = self._full_url(path)
        if params:
            sep = "&" if "?" in url else "?"
            url += sep + "&".join("{}={}".format(k, quote(str(v), safe=""))
                                   for k, v in params.items())
        return url

    def _fetch_json(self, path, **extra_params):
        url = self._build_json_url(path, **extra_params)
        html, final_url = fetch(url, referer=self.main_url,
                                extra_headers=self._PROBE_HEADERS)
        if not html:
            log("Egydead: empty response for {}".format(url[:120]))
            return None
        try:
            return json.loads(html)
        except Exception as e:
            log("Egydead: JSON parse failed for {}: {}".format(url[:120], e))
            return None

    def _year_from_date(self, date_str):
        if not date_str:
            return ""
        m = re.match(r'(\d{4})', date_str)
        return m.group(1) if m else ""

    def _card_from_title_item(self, item):
        """Build a listing card from one item of a channel/<id> response's
        pagination.data array. Confirmed live shape, 2026-10-04."""
        is_series = bool(item.get("is_series"))
        title = (item.get("name") or "").strip()
        tid = item.get("id")
        poster = item.get("poster") or ""
        backdrop = item.get("backdrop") or ""
        rating = item.get("rating")
        rating = str(rating) if rating else ""
        year = self._year_from_date(item.get("release_date"))

        if is_series:
            # Series: no single watch id yet -- route to the title/detail
            # page, which lists its seasons.
            url = self._full_url("titles/{}".format(tid))
            item_type = "series"
        else:
            pv = item.get("primary_video") or {}
            vid = pv.get("id")
            if vid:
                url = self._full_url("watch/{}".format(vid))
            else:
                # No primary video at all (rare) -- fall back to the title
                # page so the user can still reach it.
                url = self._full_url("titles/{}".format(tid))
            item_type = "movie"

        return {
            "title": title,
            "url": url,
            "poster": poster,
            "thumbnail": backdrop or poster,
            "image": poster,
            "plot": item.get("description") or "",
            "rating": rating,
            "year": year,
            "type": item_type,
            "_action": "details",
        }

    # ── Public: categories ──────────────────────────────────────────────
    def get_categories(self, mtype="movie"):
        cats = []
        for title, path in self._CATEGORIES:
            cats.append({
                "title": title,
                "url": self._full_url(path),
                "type": "category",
                "_action": "category",
            })
        return cats

    # ── Public: category / listing ──────────────────────────────────────
    def get_category_items(self, url, page=1):
        # [FIX] confirmed directly, against a real, already-working
        # extractor in this same plugin (akwams.py): the "next page" item
        # must hand back a COMPLETE, new url with the next page number
        # already baked directly into it -- the plugin framework calls
        # get_category_items(url, page) again using exactly that url, and
        # a separate "_page" metadata field (what this used to do) is not
        # a real, respected convention at all. The old code reused the
        # exact same bare url for every "next page" click, so clicking it
        # silently re-fetched page 1 again, forever.
        #
        # [FIX] confirmed directly, live, against the real plugin
        # framework code (then plugin_screen_home.py, now plugin_screen_site.py): for a site key NOT in
        # its special-cased list (which "egydead_ca" correctly is not),
        # the generic path calls get_category_items(url) with NO page
        # argument at all -- so this function's own `page=1` default was
        # silently overriding whatever real page number was already
        # baked into the incoming url. The previous version threw that
        # away unconditionally (bare_url = url.split("?")[0] every time,
        # then rebuilt the query from the page *argument*), which is
        # exactly backwards: the url is the reliable source of truth
        # here, the argument is not. The real page number is now parsed
        # out of the incoming url itself first, falling back to the page
        # argument (or 1) only when the url doesn't already carry one --
        # matching akwams.py's own defensive approach of trusting the
        # url over a passed-in argument.
        full_url = self._full_url(url)
        bare_url = full_url.split("?", 1)[0]
        url_page_m = re.search(r'[?&]page=(\d+)', full_url)
        if url_page_m:
            page = int(url_page_m.group(1))

        # The real API response's own pagination.current_page is more
        # reliable here than trusting the page argument blindly (the
        # plugin framework's own page tracking was exactly what this bug
        # was built around getting wrong) -- but it isn't known until
        # after the first fetch, so page (or 1) is still used for THIS
        # request; next_page below always comes from the response itself.
        data = self._fetch_json(
            bare_url,
            restriction="",
            order="created_at:desc",
            page=page or 1,
            paginate="lengthAware",
            returnContentOnly="true",
        )
        if not data:
            log("Egydead: get_category_items failed for {}".format(url))
            return []

        pagination = data.get("pagination") or {}
        raw_items = pagination.get("data") or []
        items = [self._card_from_title_item(it) for it in raw_items]
        log("Egydead: {} item(s) from {}".format(len(items), url[:80]))

        next_page = pagination.get("next_page")
        if next_page:
            next_url = self._build_json_url(
                bare_url,
                restriction="",
                order="created_at:desc",
                page=next_page,
                paginate="lengthAware",
                returnContentOnly="true",
            )
            items.append({
                "title": "➡️ الصفحة التالية",
                "url": next_url,
                "type": "category",
                "_action": "category",
            })
        return items

    # ── Public: search ────────────────────────────────────────────────
    def search(self, query, page=1):
        # Confirmed live, 2026-10-13, from a real captured browser session
        # (autocomplete request immediately followed by the full search
        # request): GET /api/v1/search/<query>?loader=searchPage returns
        # {"results": [...]} with each item in the exact same shape as a
        # channel-listing item (id, name, type, is_series, release_date,
        # poster, backdrop, rating, description, ...) -- just without a
        # primary_video field, which _card_from_title_item() already
        # handles by falling back to the titles/<id> page for those.
        if not query:
            return []
        data = self._fetch_json(
            "api/v1/search/{}".format(quote(query, safe="")),
            loader="searchPage")
        if not data:
            log("Egydead: search failed for {}".format(query))
            return []
        raw_items = data.get("results") or []
        items = [self._card_from_title_item(it) for it in raw_items]
        log("Egydead: {} search result(s) for {}".format(len(items), query))
        return items

    # ── Public: detail / watch page ──────────────────────────────────────
    def get_page(self, url, m_type=None):
        result = {
            "url": url,
            "title": "",
            "poster": "",
            "thumbnail": "",
            "image": "",
            "plot": "",
            "year": "",
            "rating": "",
            "servers": [],
            "items": [],
            "downloads": [],
            "type": m_type or "movie",
        }

        full_url = self._full_url(url)

        # ── /watch/<id> — a real, playable video ────────────────────────
        watch_m = re.search(r'/watch/(\d+)', full_url)
        if watch_m:
            vid = watch_m.group(1)
            data = self._fetch_json("api/v1/watch/{}".format(vid))
            if not data:
                log("Egydead: get_page failed for watch/{}".format(vid))
                return result

            title_info = data.get("title") or {}
            video = data.get("video") or {}
            episode = data.get("episode")

            result["title"] = (title_info.get("name") or "").strip()
            result["poster"] = title_info.get("poster") or ""
            result["thumbnail"] = title_info.get("backdrop") or result["poster"]
            result["image"] = result["poster"]
            result["plot"] = title_info.get("description") or ""
            result["year"] = str(title_info.get("year") or "") or \
                self._year_from_date(title_info.get("release_date"))
            result["type"] = "episode" if episode or video.get("episode_id") else \
                ("series" if title_info.get("is_series") else "movie")

            servers = []
            seen = set()
            alt_videos = data.get("alternative_videos") or []
            # Always include the primary video too, in case it's ever
            # absent from alternative_videos on some title.
            all_videos = [video] + [v for v in alt_videos if v.get("id") != video.get("id")]
            for v in all_videos:
                src = v.get("src")
                if not src or src in seen:
                    continue
                seen.add(src)
                name = v.get("name") or "🎬 Server"
                quality = (v.get("quality") or "").upper()
                label = "🎬 {}{}".format(name, " [{}]".format(quality) if quality else "")
                servers.append({
                    "name": label,
                    "url": src,
                    "type": "embed",
                })
            result["servers"] = servers
            log("Egydead: get_page watch/{} -> {} server(s)".format(vid, len(servers)))
            return result

        # [FIX] confirmed directly via a local test: /titles/<id>/seasons/<N>
        # also matches the generic /titles/(\d+) pattern below as a
        # substring, so the more specific season check must run first or
        # every season URL silently gets treated as a plain title page.
        # ── /titles/<id>/seasons/<N> — episode list for one season ──────
        season_m = re.search(r'/titles/(\d+)/seasons/(\d+)', full_url)
        if season_m:
            tid, snum = season_m.group(1), season_m.group(2)
            data = self._fetch_json(
                "api/v1/titles/{}/seasons/{}".format(tid, snum),
                loader="seasonPage"
            )
            if not data:
                log("Egydead: get_page failed for titles/{}/seasons/{}".format(tid, snum))
                return result

            t = data.get("title") or {}
            season = data.get("season") or {}
            result["title"] = "{} — الموسم {}".format(
                (t.get("name") or "").strip(), snum)
            result["poster"] = season.get("poster") or t.get("poster") or ""
            result["thumbnail"] = t.get("backdrop") or result["poster"]
            result["image"] = result["poster"]
            result["type"] = "series"

            episodes_raw = (data.get("episodes") or {}).get("data") or []
            items = []
            for ep in episodes_raw:
                pv = ep.get("primary_video") or {}
                vid = pv.get("id")
                if not vid:
                    continue
                ep_num = ep.get("episode_number")
                ep_title = ep.get("name") or "حلقة {}".format(ep_num or "")
                items.append({
                    "title": "🎬 {}".format(ep_title),
                    "url": self._full_url("watch/{}".format(vid)),
                    "poster": ep.get("poster") or "",
                    "thumbnail": ep.get("poster") or "",
                    "plot": ep.get("description") or "",
                    "year": self._year_from_date(ep.get("release_date")),
                    "type": "episode",
                    "_action": "details",
                })
            result["items"] = items
            log("Egydead: get_page season {}/{} -> {} episode(s)"
                .format(tid, snum, len(items)))
            return result

        # ── /titles/<id> — detail page (movie or series) ────────────────
        title_m = re.search(r'/titles/(\d+)', full_url)
        if title_m:
            tid = title_m.group(1)
            data = self._fetch_json("api/v1/titles/{}".format(tid),
                                     loader="titlePage")
            if not data:
                log("Egydead: get_page failed for titles/{}".format(tid))
                return result

            t = data.get("title") or {}
            result["title"] = (t.get("name") or "").strip()
            result["poster"] = t.get("poster") or ""
            result["thumbnail"] = t.get("backdrop") or result["poster"]
            result["image"] = result["poster"]
            result["plot"] = t.get("description") or ""
            result["year"] = str(t.get("year") or "")
            rating = t.get("rating")
            result["rating"] = str(rating) if rating else ""

            is_series = bool(t.get("is_series"))
            if not is_series:
                # A movie's detail page -- confirmed live this site also
                # exposes a primary_video directly off the title object
                # in the channel listing, but NOT confirmed present on
                # this specific titlePage response; if a season/0-style
                # direct video reference isn't present, there's nothing
                # further this page can offer beyond the metadata above.
                result["type"] = "movie"
                return result

            # Series: list seasons as navigable items. Confirmed live:
            # seasons_count tells us how many /seasons/<N> endpoints exist.
            result["type"] = "series"
            seasons_count = t.get("seasons_count") or 0
            items = []
            for n in range(1, seasons_count + 1):
                items.append({
                    "title": "📺 الموسم {}".format(n),
                    "url": self._full_url("titles/{}/seasons/{}".format(tid, n)),
                    "type": "season",
                    "_action": "details",
                })
            result["items"] = items
            log("Egydead: get_page titles/{} -> {} season(s)".format(tid, len(items)))
            return result

        log("Egydead: get_page -- unrecognized URL shape: {}".format(full_url[:120]))
        return result

    # ── extract_stream ─────────────────────────────────────────────────
    def extract_stream(self, url):
        # [FIX] confirmed live, directly: egybestvid.com (and other shared
        # embed hosts) check the Referer header against the actual parent
        # site that embedded them, and reject with a literal
        # "Video embed restricted for this domain" when it doesn't match --
        # a real browser playing this exact video succeeded with
        # Referer: https://egydead.ca/, while the same request with no
        # referer context (or egybestvid.com's own self-referer default)
        # was rejected every time. hosts.extract_stream() has no referer
        # parameter of its own -- it only picks up a Referer via the
        # existing "<url>|Referer=<value>" pipe-encoded convention it
        # already parses internally -- so thread our own site through that
        # convention instead of calling it with a bare URL.
        from .base import extract_stream as base_extract_stream
        from .referers import get_referer
        piped_url = url
        if url and "|" not in url:
            piped_url = "{}|Referer={}".format(url, self.main_url)
        result = base_extract_stream(piped_url)
        # [FIX] confirmed live: the egydead.ca Referer above exists only to
        # get PAST egybestvid.com's own domain whitelist while RESOLVING the
        # embed page -- it must not leak into the Referer the PLAYER then
        # sends for the manifest/segment requests. Those are a separate hop
        # (made by the embed page's own JS, running on egybestvid.com's own
        # origin), so they need egybestvid.com's Referer, not egydead.ca's.
        # Returning the wrong one here resolved fine but made every single
        # player backend fail outright (confirmed live: all of direct HLS,
        # exteplayer3, and the proxy variants failed/timed out when the
        # egydead.ca referer was left in place). Recompute the referer
        # against the actual stream's own domain before returning.
        if isinstance(result, tuple) and len(result) >= 3 and result[0]:
            stream, quality, ref = result[0], result[1], result[2]
            others = result[3] if len(result) > 3 else []
            correct_ref = get_referer(stream)
            if correct_ref and correct_ref != ref:
                log("EgydeadCa: correcting stream referer {} -> {}".format(ref, correct_ref))
                result = (stream, quality, correct_ref, others)
        return result
