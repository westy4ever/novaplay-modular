# -*- coding: utf-8 -*-
"""
Advanced Arabic Player - TMDb client
=======================================
Self-contained TMDb (themoviedb.org) lookups used to enrich scraped
titles/posters/plots. No Enigma2 or UI dependency.

Now includes XtreamNew-style JSON metadata caching to prevent API spamming
and make Fanart/Poster loading instant on subsequent views.

Changes in this revision (audit fixes):
  * Cache-quality gate: a weak match (no plot AND no poster from TMDB =
    almost certainly a wrong entry) is NOT cached anymore. The old
    behavior cached every result for 100 years, so one bad match
    permanently pinned the wrong poster/plot to a title with no way to
    correct it short of wiping the cache dir.
  * _save_cached_meta(): atomic (tmp+rename) — a crash mid-write used to
    leave a truncated .json that silently cost one wasted API re-fetch.
  * _merge_tmdb_data(): TMDB plot now FILLS only (never replaces the
    site's own plot). The old longer-wins rule let machine-translated
    TMDB 'ar' overviews replace correct site plots.
  * Rating formatting is string-safe (some proxies mangle vote_average
    into a string; float() used to raise inside the dict build).
"""

import os
import re
import json
import hashlib
import time
import threading
from urllib.parse import urlencode

from extractors.base import fetch as base_fetch, log as _log

from plugin_state import _get_config
from plugin_util import _clean_title_for_tmdb, _normalize_query

_TMDB_API_BASE  = "https://api.themoviedb.org/3"
_TMDB_IMG_BASE  = "https://image.tmdb.org/t/p/w500"
_TMDB_BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"

# --- Metadata Cache Configuration ---
_CACHE_BASE_HDD = "/media/hdd/AdvancedArabicPlayer/cache"
_CACHE_BASE_TMP = "/tmp/AdvancedArabicPlayer/cache"
_CACHE_DIRNAME = "tmdb"
_CACHE_MAX_AGE = 60 * 60 * 24 * 365 * 100  # effectively no expiry (100 years)

def _get_cache_dir():
    base = _CACHE_BASE_HDD
    if not os.path.exists("/media/hdd"):
        base = _CACHE_BASE_TMP
    cache_dir = os.path.join(base, _CACHE_DIRNAME)
    try:
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
    except Exception:
        pass
    return cache_dir

def _get_cache_path(title, year, item_type):
    key = "%s|%s|%s" % (item_type, title, year or "")
    try:
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    except Exception:
        digest = hashlib.md5(str(key).encode("utf-8")).hexdigest()
    return os.path.join(_get_cache_dir(), digest + ".json")

def _load_cached_meta(cache_path):
    try:
        if not os.path.exists(cache_path):
            return None
        age = time.time() - os.path.getmtime(cache_path)
        if age > _CACHE_MAX_AGE:
            return None
        with open(cache_path, "r") as f:
            return json.load(f)
    except Exception:
        return None

def _save_cached_meta(cache_path, data):
    try:
        # Atomic: a crash mid-write must not leave a truncated .json
        # that _load_cached_meta would silently swallow.
        tmp = cache_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.rename(tmp, cache_path)
    except Exception:
        try:
            if os.path.exists(cache_path + ".tmp"):
                os.remove(cache_path + ".tmp")
        except Exception:
            pass

# --- TMDB API Functions ---

# [FIX] confirmed directly on a real device: rapid grid browsing fires a
# TMDB lookup per visible title, and each title's own search can cost up
# to ~28 HTTP requests in the worst case (7 title-cleaning variants x
# with/without year x 2 languages) when TMDB genuinely has no match --
# exactly the "not found" case grid browsing hits constantly for very
# new or obscure titles. Scrolling through a few 40-item grid pages can
# easily fire hundreds of requests within under a minute. Confirmed this
# was the real cause of a run where EVERY single TMDB search failed,
# including for long-established, famous titles (Avengers: Infinity War,
# 2018) -- re-running the exact same request (same key, same query)
# moments later succeeded cleanly, proving the failures were a transient
# burst/rate-limit effect, not missing titles or a dead key. This
# serializes every outbound TMDB request through one minimum spacing,
# regardless of how many threads/titles are requesting concurrently, so
# the plugin's own request rate stays well under whatever threshold
# triggered this.
_tmdb_rate_lock = threading.Lock()
_tmdb_last_request_time = [0.0]
_TMDB_MIN_REQUEST_INTERVAL = 0.15  # seconds between outbound TMDB requests


def _tmdb_throttle():
    with _tmdb_rate_lock:
        now = time.monotonic()
        wait = _TMDB_MIN_REQUEST_INTERVAL - (now - _tmdb_last_request_time[0])
        if wait > 0:
            time.sleep(wait)
        _tmdb_last_request_time[0] = time.monotonic()


def _tmdb_enabled():
    return bool((_get_config("tmdb_api_key", "") or "").strip())

def _safe_rating(value):
    try:
        return "{:.1f}".format(float(value))
    except (TypeError, ValueError):
        return ""

def _tmdb_extract_certification(detail, media_kind):
    """
    [detail-tmdb-meta] Pulls the US certification (PG-13, R, TV-MA, ...)
    out of the release_dates (movie) / content_ratings (tv) block that
    _tmdb_search_metadata now appends to the en-US detail request. Both
    are lists of per-country entries -- only the US one is used, same
    country TMDb's own web page defaults to. Movies can list several
    release_dates per country (theatrical, digital, physical, ...); the
    first one with a non-empty certification string is used, matching
    what TMDb's own site shows.
    """
    try:
        if media_kind == "movie":
            for entry in (detail.get("release_dates") or {}).get("results") or []:
                if entry.get("iso_3166_1") != "US":
                    continue
                for rd in entry.get("release_dates") or []:
                    cert = (rd.get("certification") or "").strip()
                    if cert:
                        return cert
        else:
            for entry in (detail.get("content_ratings") or {}).get("results") or []:
                if entry.get("iso_3166_1") == "US":
                    cert = (entry.get("rating") or "").strip()
                    if cert:
                        return cert
    except Exception:
        pass
    return ""


def _tmdb_extract_release_country(detail, media_kind, preferred=("EG", "US")):
    """
    [detail-tmdb-meta] Movies' own "release_date" field (used for the
    date shown under the title) is just TMDb's first/primary release
    date -- not tied to any specific country, and the hardcoded "(US)"
    label this plugin used to print next to it was simply wrong: it
    never checked which country that date actually belonged to.

    TMDb's own web page shows a per-country release date -- confirmed
    directly (user's own screenshot): a title page showed
    "10/08/2026 (EG)", not a US date. Reuses the same per-country
    release_dates block _tmdb_extract_certification() already reads,
    preferring EG (this plugin's own audience) then US (TMDb's site
    default), and returns the FIRST theatrical/digital/physical entry
    with a non-empty release_date for whichever of those actually has
    one -- so the date and the country label always agree.

    TV has no equivalent per-country release-date block in this API
    (content_ratings only carries certifications, not dates), so this
    only applies to movies; callers fall back to the dateless
    first_air_date/release_date field for TV.

    Returns (date_str "YYYY-MM-DD", country_code) or ("", "") if none
    of the preferred countries has a usable date.
    """
    if media_kind != "movie":
        return "", ""
    try:
        by_country = {}
        for entry in (detail.get("release_dates") or {}).get("results") or []:
            cc = entry.get("iso_3166_1") or ""
            if not cc:
                continue
            for rd in entry.get("release_dates") or []:
                date_str = (rd.get("release_date") or "")[:10]
                if date_str:
                    by_country[cc] = date_str
                    break
        for cc in preferred:
            if by_country.get(cc):
                return by_country[cc], cc
    except Exception:
        pass
    return "", ""


def _tmdb_request(path, params=None):
    api_key = (_get_config("tmdb_api_key", "") or "").strip()
    if not api_key:
        return None
    base_payload = {"api_key": api_key}
    if params:
        base_payload.update(params)
    for language in ("ar", "en-US"):
        payload = dict(base_payload)
        payload["language"] = language
        url = "{}{}?{}".format(_TMDB_API_BASE, path, urlencode(payload))
        try:
            _tmdb_throttle()
            raw, _ = base_fetch(url, referer="https://www.themoviedb.org/", extra_headers={"Accept": "application/json"})
            if not raw:
                _log("TMDb: empty HTTP body for {} [{}]".format(path, language))
                continue
            # Reduced verbosity: log length instead of 300 chars of raw JSON
            _log("TMDb response [{}] query={}: {} chars".format(
                language, payload.get("query"), len(raw or "")))
            data = json.loads(raw)
            if isinstance(data, dict):
                if data.get("status_code"):
                    _log("TMDb API ERROR for {} [{}]: status_code={} message={}".format(
                        path, language, data.get("status_code"), data.get("status_message")))
                    continue
                if data.get("results") is not None and len(data.get("results")) == 0:
                    _log("TMDb: zero results for {} [{}] query={}".format(
                        path, language, payload.get("query")))
                if data.get("overview") or data.get("results") or language == "en-US":
                    return data
        except Exception as e:
            _log("TMDb request failed {} [{}]: {}".format(path, language, e))
    return None

def _tmdb_request_language(path, language="ar", params=None, accept_any=False):
    api_key = (_get_config("tmdb_api_key", "") or "").strip()
    if not api_key:
        return None
    payload = {"api_key": api_key, "language": language}
    if params:
        payload.update(params)
    url = "{}{}?{}".format(_TMDB_API_BASE, path, urlencode(payload))
    try:
        raw, _ = base_fetch(url, referer="https://www.themoviedb.org/", extra_headers={"Accept": "application/json"})
        if not raw:
            _log("TMDb: empty HTTP body for {} [{}]".format(path, language))
            return None
        # Reduced verbosity: log length instead of 300 chars of raw JSON
        _log("TMDb response (lang-request) [{}]: {} chars".format(language, len(raw or "")))
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        if data.get("status_code"):
            _log("TMDb API ERROR for {} [{}]: status_code={} message={}".format(
                path, language, data.get("status_code"), data.get("status_message")))
            return None
        if accept_any or data.get("overview") or data.get("results"):
            return data
    except Exception as e:
        _log("TMDb language request failed {} [{}]: {}".format(path, language, e))
    return None

def _tmdb_poster_url(path):
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return _TMDB_IMG_BASE + path

def _tmdb_backdrop_url(path):
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return _TMDB_BACKDROP_BASE + path

def _tmdb_pick_poster(media_kind, tmdb_id, fallback_path=""):
    if not tmdb_id:
        return _tmdb_poster_url(fallback_path or "")
    images = _tmdb_request_language(
        "/{}/{}/images".format(media_kind, tmdb_id),
        language="en-US",
        params={"include_image_language": "ar,en,null"},
        accept_any=True,
    ) or {}
    posters = images.get("posters") or []
    for wanted_lang in ("ar", None, "en"):
        for poster in posters:
            if poster.get("iso_639_1") == wanted_lang and poster.get("file_path"):
                return _tmdb_poster_url(poster.get("file_path"))
    return _tmdb_poster_url(fallback_path or "")

def _tmdb_media_kind(item_type):
    if item_type in ("series", "episode", "tv"):
        return "tv"
    return "movie"

def _tmdb_pick_best(results, query, year=""):
    query_norm = _normalize_query(query)
    target_year = (year or "")[:4]
    scored = []
    for idx, result in enumerate(results or []):     # [PATCH 85]
        # Get both localized title and original title
        title = result.get("title") or result.get("name") or ""
        original_title = result.get("original_title") or result.get("original_name") or ""
        
        title_norm = _normalize_query(title)
        original_norm = _normalize_query(original_title)
        
        score = 9
        # Check exact match against either title
        if query_norm == title_norm or query_norm == original_norm:
            score = 0
        elif title_norm.startswith(query_norm) or original_norm.startswith(query_norm):
            score = 1
        elif (query_norm and query_norm in title_norm) or (query_norm and query_norm in original_norm):
            score = 2
            
        release = str(result.get("release_date") or result.get("first_air_date") or "")
        if target_year and release[:4] == target_year:
            score -= 1
            
        # Sort by score, then by original title to avoid weird alphabetical mismatches
        scored.append((score, idx, result))      # [PATCH 85] ties keep TMDB's popularity order
    scored.sort(key=lambda row: (row[0], row[1]))
    return scored[0][2] if scored else None

def _tmdb_search_metadata(title, year="", item_type="movie"):
    if not title or not _tmdb_enabled():
        return None
        
    # 1. Check local JSON cache first!
    cache_path = _get_cache_path(title, year, item_type)
    cached = _load_cached_meta(cache_path)
    # [FIX] confirmed directly on a real device: this cache has no expiry
    # (100 years), so a title looked up before runtime/release_date/
    # certification existed in this module's result dict comes back from
    # disk forever missing those three keys -- the new detail-screen meta
    # line silently falls back to the old year/rating/type/quality
    # construction for every already-cached title, which looks like the
    # feature never shipped. Treating a cache entry missing any of those
    # keys as a miss self-heals it: one re-fetch (now including the new
    # fields) the next time that exact title/year/type is looked up, then
    # the re-save below overwrites the stale file with the complete one.
    if cached is not None and all(k in cached for k in ("runtime", "release_date", "certification")):
        return cached

    # 2. Fetch from TMDB API
    media_kind = _tmdb_media_kind(item_type)

    # Extract a bare trailing year (e.g. "The Last House 2026" -> year=2026)
    # so it can be used as a ranking hint instead of polluting the text
    # search itself. TMDb's title search matches literal title text, and
    # scraped titles frequently have the release year appended without
    # parentheses - a movie is never actually titled "Movie Name 2026" in
    # TMDb, so leaving the year in the query text reliably produces zero
    # results even for well-known films.
    if not year:
        year_m = re.search(r'\b(19\d{2}|20\d{2})\s*$', title.strip())
        if year_m:
            year = year_m.group(1)

    variants = []
    seen_normalized = set()

    def _add_variant(text):
        text = (text or "").strip()
        if not text:
            return
        norm = _normalize_query(text)
        if not norm or norm in seen_normalized:
            return
        seen_normalized.add(norm)
        variants.append(text)

    # Strip a bare trailing year (no parens) first, so the very first
    # variant we try is already year-free text.
    no_year = re.sub(r'\s*\b(19\d{2}|20\d{2})\s*$', '', title).strip()
    _add_variant(no_year)
    _add_variant(title)
    simple = re.sub(r"\s*\(\d{4}\)\s*$", "", no_year).strip()
    _add_variant(simple)
    plain = re.sub(r"[:|_\-]+", " ", simple).strip()
    _add_variant(plain)
    clean = re.sub(r"\b(bluray|webrip|web-dl|hdrip|hdcam|cam|1080p|720p|480p|360p)\b", "", plain, flags=re.I).strip()
    clean = re.sub(r"\s+", " ", clean).strip(" -|")
    _add_variant(clean)
    arabic_clean = re.sub(
        r"\b(مشاهدة|فيلم|مسلسل|الحلقة|حلقة|الموسم|مترجم(?:ة)?|مدبلج(?:ة)?|اون لاين|أون لاين)\b",
        "",
        clean,
        flags=re.I,
    ).strip()
    arabic_clean = re.sub(r"\s+", " ", arabic_clean).strip(" -|")
    # [FIX] confirmed directly: the module's OWN docstring already says
    # "leaving the year in the query text reliably produces zero results
    # even for well-known films" and strips a bare trailing year for
    # exactly that reason -- but that strip runs on the RAW title, before
    # the Arabic wrapper (فيلم/مترجم/etc.) is removed. Every scraped title
    # from this site family is "... YEAR مترجم", so the year is never
    # actually the last token when that first strip runs, and it silently
    # never fires. Confirmed on a real device: "Avengers: Infinity War"
    # (a long-established, famous movie) got "zero results" every time
    # because the query text sent was "Avengers Infinity War 2018", not
    # "Avengers Infinity War" -- TMDb has no movie literally titled with
    # a trailing year like that. Stripping it again here, now that
    # "مترجم" etc. are gone and the year is genuinely trailing, fixes it:
    # confirmed this exact cleaned string against the live TMDb API and
    # it correctly found the movie.
    arabic_clean = re.sub(r'\s*\b(19\d{2}|20\d{2})\s*$', '', arabic_clean).strip()
    _add_variant(arabic_clean)
    # Arabic ordinals (الثالث/الرابع...) survive every variant above —
    # add the bare-English-prefix variant: "Silo الموسم الثالث" → "Silo"
    bare_en = re.sub(r"[\u0600-\u06ff]+", " ", arabic_clean)
    bare_en = re.sub(r"\b(?:موسم|حلقة|مسلسل|فيلم|مترجم|والأخيرة|الأخيرة)\b", " ", bare_en, flags=re.I)
    bare_en = re.sub(r"\s+", " ", bare_en).strip(" -|")
    _add_variant(bare_en)

    best = None
    for query in variants:
        params = {"query": query}
        used_year = False
        if year:
            used_year = True
            if media_kind == "movie":
                params["year"] = year[:4]
            else:
                params["first_air_date_year"] = year[:4]
        data = _tmdb_request("/search/{}".format(media_kind), params) or {}
        best = _tmdb_pick_best(data.get("results") or [], query, year)
        if not best and used_year:
            params.pop("year", None)
            params.pop("first_air_date_year", None)
            best = _tmdb_pick_best((_tmdb_request("/search/{}".format(media_kind), params) or {}).get("results") or [], query, "")
        if best:
            break
            
    if not best:
        _log("TMDb: no match found for title='{}' year='{}' type='{}' after trying {} variant(s)".format(
            title, year, item_type, len(variants)))
        return None
        
    # [detail-tmdb-meta] certification (PG-13 etc.) isn't on the base
    # detail response -- it needs release_dates (movie) / content_ratings
    # (tv) appended. Certification data is per-country, not per-language,
    # so it only needs to ride along on one of the two language requests;
    # piggybacked on detail_en since that one is the more reliable
    # fallback of the two (confirmed en-US detail requests succeed even
    # for titles with no Arabic metadata at all).
    _cert_append = "release_dates" if media_kind == "movie" else "content_ratings"
    detail_ar = _tmdb_request_language(
        "/{}/{}".format(media_kind, best.get("id")),
        language="ar",
        params={"append_to_response": "credits"},
        accept_any=True,
    ) or {}
    detail_en = _tmdb_request_language(
        "/{}/{}".format(media_kind, best.get("id")),
        language="en-US",
        params={"append_to_response": "credits,{}".format(_cert_append)},
        accept_any=True,
    ) or {}
    detail = detail_ar or detail_en
    if not detail:
        detail = _tmdb_request("/{}/{}".format(media_kind, best.get("id"))) or {}
    if not detail:
        detail = best
        
    genres_source = detail_ar or detail_en or detail
    genres = ", ".join([g.get("name", "") for g in genres_source.get("genres") or [] if g.get("name")])
    localized_plot = (
        (detail_ar.get("overview") or "").strip()
        or (detail_en.get("overview") or "").strip()
        or (best.get("overview") or "").strip()
    )
    localized_title = (
        detail_ar.get("title")
        or detail_ar.get("name")
        or detail_en.get("title")
        or detail_en.get("name")
        or detail.get("title")
        or detail.get("name")
        or title
    )
    
    backdrop_path = detail.get("backdrop_path") or best.get("backdrop_path") or ""

    # [detail-tmdb-meta] runtime: movies carry a single "runtime" (minutes);
    # TV only has "episode_run_time" (a list -- use its first value as the
    # typical episode length, same number TMDb's own site shows on a show's
    # page). release_dates/content_ratings were only appended to detail_en
    # above, so certification must be read from detail_en specifically --
    # "detail" itself may be detail_ar, which never has that block.
    runtime = detail.get("runtime")
    if not runtime:
        _ert = detail.get("episode_run_time") or []
        runtime = _ert[0] if _ert else ""
    release_date_full = str(detail.get("release_date") or detail.get("first_air_date") or "")
    certification = _tmdb_extract_certification(detail_en or detail, media_kind)
    # [detail-tmdb-meta] prefer an actual per-country release date (EG,
    # then US) over the dateless generic field above, so the "(XX)" label
    # shown under the title always matches the date it's printed next to.
    _country_date, release_country = _tmdb_extract_release_country(detail_en or detail, media_kind)
    if _country_date:
        release_date_full = _country_date

    result = {
        "title": localized_title,
        "plot": localized_plot,
        "poster": _tmdb_pick_poster(media_kind, best.get("id"), detail_ar.get("poster_path") or detail_en.get("poster_path") or detail.get("poster_path") or ""),
        "backdrop_url": _tmdb_backdrop_url(backdrop_path),
        "rating": _safe_rating(detail.get("vote_average")) if detail.get("vote_average") else "",
        "year": str(detail.get("release_date") or detail.get("first_air_date") or "")[:4],
        "genres": genres,
        "runtime": runtime,
        "release_date": release_date_full,
        "release_country": release_country,
        "certification": certification,
        "tmdb_id": detail.get("id"),
        "tmdb_kind": media_kind,
    }
    
    # 3. Cache only trustworthy matches. Weak matches (no plot AND no
    # poster from TMDB = almost certainly a wrong entry) stay uncached,
    # so a later, better-scraped title (with year, cleaner text) can
    # still find the right one. A poisoned 100-year cache entry is the
    # worst failure mode this module can have.
    if result.get("plot") and result.get("poster"):
        _save_cached_meta(cache_path, result)
    else:
        _log("TMDb: weak match for '{}', not cached (plot={} poster={})".format(
            title[:40], bool(result.get("plot")), bool(result.get("poster"))))
    
    return result

def _merge_tmdb_data(data):
    if not data or not data.get("title"):
        return data
    data = dict(data)
    if not data.get("plot") and data.get("desc"):
        data["plot"] = data.get("desc")
    item_type = data.get("type", "movie")
    if item_type == "episode":
        return data
    tmdb = _tmdb_search_metadata(data.get("title"), data.get("year", ""), item_type)
    if not tmdb:
        return data
    merged = dict(data)
    if tmdb.get("title") and len((data.get("title") or "").strip()) < 2:
        merged["title"] = tmdb["title"]
    if tmdb.get("poster") and (not merged.get("poster")):
        merged["poster"] = tmdb["poster"]
    if tmdb.get("backdrop_url") and (not merged.get("fanart")):
        merged["fanart"] = tmdb["backdrop_url"]
    # Fill-only: the site's own plot is authoritative; TMDB's overview
    # (often machine-translated ar) replaces nothing.
    if tmdb.get("plot") and not merged.get("plot"):
        merged["plot"] = tmdb["plot"]
    if tmdb.get("rating") and not merged.get("rating"):
        merged["rating"] = tmdb["rating"]
    if tmdb.get("year") and not merged.get("year"):
        merged["year"] = tmdb["year"]
    if tmdb.get("genres"):
        merged["genres"] = tmdb["genres"]
    # [detail-tmdb-meta] fill-only, same as rating/year above: a site's
    # own scraped runtime (already in some extractors' "runtime" field)
    # is kept over TMDb's; release_date/certification have no other
    # source in this codebase, so TMDb is always authoritative for them.
    if tmdb.get("runtime") and not merged.get("runtime"):
        merged["runtime"] = tmdb["runtime"]
    if tmdb.get("release_date") and not merged.get("release_date"):
        merged["release_date"] = tmdb["release_date"]
    if tmdb.get("release_country") and not merged.get("release_country"):
        merged["release_country"] = tmdb["release_country"]
    if tmdb.get("certification") and not merged.get("certification"):
        merged["certification"] = tmdb["certification"]
    if tmdb.get("plot") or tmdb.get("poster") or tmdb.get("rating") or tmdb.get("genres") or tmdb.get("year"):
        merged["_tmdb"] = tmdb
    return merged

def _tmdb_fetch_trailer_key(media_kind, tmdb_id):
    """
    [detail-trailer] Looks up a title's YouTube trailer via TMDb's own
    /videos endpoint (same API this module already calls everywhere
    else) -- called lazily, only when the user actually presses the
    trailer button, not during the regular TMDb merge: resolving the
    video key here is cheap (one more TMDb request), but turning that key
    into a playable URL (plugin_screen_detail.py's job, via yt-dlp) is
    not, so there is no reason to pay that cost for every detail screen
    opened versus only the ones where a trailer is actually requested.

    Prefers an official YouTube trailer, then any YouTube trailer, then a
    YouTube teaser -- TMDb's own site uses the same preference order.
    Non-YouTube entries (Vimeo etc.) are skipped; this plugin only has a
    YouTube resolver (yt-dlp) to play them with. Returns "" if nothing
    usable is found.
    """
    if not tmdb_id or not media_kind:
        return ""
    data = _tmdb_request_language(
        "/{}/{}/videos".format(media_kind, tmdb_id),
        language="en-US",
        accept_any=True,
    ) or {}
    vids = [v for v in (data.get("results") or [])
            if (v.get("site") or "").lower() == "youtube" and v.get("key")]
    if not vids:
        return ""

    def _rank(v):
        vtype = (v.get("type") or "").lower()
        if vtype == "trailer" and v.get("official"):
            return 0
        if vtype == "trailer":
            return 1
        if vtype == "teaser":
            return 2
        return 3

    vids.sort(key=_rank)
    return vids[0].get("key", "")

def _tmdb_search_suggestions(query, limit=8):
    query = re.sub(r"\s+", " ", query or "").strip()
    if len(query) < 2 or not _tmdb_enabled():
        return []

    suggestions = []
    seen = set()
    for media_kind, kind_label in (("movie", "فيلم"), ("tv", "مسلسل")):
        try:
            data = _tmdb_request("/search/{}".format(media_kind), {"query": query, "page": 1}) or {}
            for result in data.get("results") or []:
                title = (result.get("title") or result.get("name") or "").strip()
                if not title:
                    continue
                norm = _normalize_query(title)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                year = str(result.get("release_date") or result.get("first_air_date") or "")[:4]
                suggestions.append({
                    "title": title,
                    "query": title,
                    "source": "TMDb",
                    "site": "",
                    "kind": kind_label,
                    "year": year,
                })
                if len(suggestions) >= limit:
                    return suggestions[:limit]
        except Exception as e:
            _log("TMDb suggestions failed for {}: {}".format(media_kind, e))
    return suggestions[:limit]

# ─── [tmdb-rating] shared lazy rating fill ──────────────────────────────────
def fill_item_rating_async(item, on_done=None):
    """[tmdb-rating] One-shot background TMDb lookup for an item whose
    source extractor publishes no rating on its listing cards (EgyDead,
    egydead_coupons, and any future site that omits one). Writes the
    value into `item["rating"]` and invokes `on_done` on the main thread
    when the mutation lands.

    Contract:
      * Guarded by item["_tmdb_rating_tried"] -- repeat calls for the
        same item are no-ops, safe to call from every paint.
      * Reuses _tmdb_search_metadata's long-lived JSON cache -- if the
        item's poster or backdrop was already TMDb-resolved through
        another path, this costs zero network.
      * The caller is responsible for redrawing; `on_done` is invoked
        via callInMainThread (lazy import to keep this module free of
        Enigma2 dependencies at import time).
      * Fails silently on any error -- a missing rating must never
        break the caller's paint loop.
    """
    if not item:
        return
    if item.get("rating") or item.get("_tmdb_rating_tried"):
        return
    title = item.get("title", "")
    if not title:
        return
    item["_tmdb_rating_tried"] = True
    year = item.get("year", "")
    m_type = item.get("type", "movie")

    def _worker():
        try:
            meta = _tmdb_search_metadata(title, year, m_type)
            if meta and meta.get("rating"):
                item["rating"] = meta["rating"]
                _log("fill_item_rating_async: '{}' -> {}".format(
                    title[:40], meta["rating"]))
                if on_done:
                    try:
                        from novaplay_thread import callInMainThread
                        callInMainThread(on_done)
                    except Exception:
                        try:
                            on_done()
                        except Exception:
                            pass
        except Exception as e:
            try:
                _log("fill_item_rating_async error for {}: {}".format(title[:40], e))
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()

