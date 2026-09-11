# -*- coding: utf-8 -*-
"""novaplay_autosub.py — auto-subtitle on playback start (v1).

Background SubSource worker: searches the playing title, picks the best
Arabic (then English) subtitle, downloads it and applies it through the
main thread. Completely silent on any failure — convenience, never
disruption.

Self-contained by design: uses its own minimal urllib fetch with the
exact endpoints the plugin log proved working:
    /api/v1/movies/search?searchType=text&q=...&type=all&year=...
    /api/v1/subtitles?movieId=...&language=arabic&limit=100&sort=newest
so it never touches the extractors' proxy/cookie state. Response
shapes are parsed defensively (several field-name/shape variants).
"""

import os
import re
import io
import json
import time
import gzip
import zipfile
import threading
import urllib.request
import urllib.parse

_API = "https://api.subsource.net/api/v1"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_SUB_DIR = "/tmp/novaplay_autosub"
_TIMEOUT = 15
_REFERER = "https://subsource.net/"

_TAG_RE = re.compile(r"\s*\[\d{3,4}p\]\s*$")
_AR_NOISE = ("فيلم", "مسلسل", "الحلقة", "مترجم", "مترجمة", "مدبلج", "مدبلجة",
             "مشاهدة", "تحميل", "اون لاين", "أون لاين", "كامل", "والموسم", "الحلقات")


def _dbg(msg):
    try:
        from plugin_common import my_log
        my_log(msg)
    except Exception:
        pass


def _cfg(key, default):
    try:
        from plugin_state import _get_config
        return str(_get_config(key, default))
    except Exception:
        return default


def _http_get(url):
    """v1.1: two-tier fetch.

    Tier 1 — the plugin's own fetch() (extractors.base): identical code
    path to the subtitle screen's requests, which SUCCEED where a bare
    urllib client gets 401 (log-proven: autosub 401 at 23:15:10, plugin
    fetch OK on the same endpoint one minute later — net.py's fetch
    carries whatever header/session shape the API expects).

    Tier 2 — minimal-header urllib (User-Agent only; the browser-style
    Referer/Accept headers were the suspected 401 trigger). Kept for
    raw/binary payloads where the text path can't be used.
    """
    try:
        from extractors.base import fetch
        html, _final = fetch(url)
        if html:
            return html.encode("utf-8", "surrogateescape")
    except Exception as e:
        _dbg("autosub plugin-fetch error: {} ({})".format(e, url[:70]))
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding", "").lower().find("gzip") >= 0
                    or raw[:2] == b"\x1f\x8b"):
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return raw
    except Exception as e:
        _dbg("autosub http error: {} ({})".format(e, url[:90]))
    return None


def _clean_title(raw):
    t = (raw or "").strip()
    t = _TAG_RE.sub("", t)
    t = t.replace(u"\u2026", " ")
    for w in _AR_NOISE:
        t = t.replace(w, " ")
    t = re.sub(r"\s+", " ", t).strip(" -|:.")
    return t.strip()


def _year_of(t):
    m = re.search(r"\b(19\d{2}|20\d{2})\b", t)
    return m.group(1) if m else ""


def _search_movie(title, year):
    url = "{}/movies/search?searchType=text&q={}&type=all".format(
        _API, urllib.parse.quote_plus(title))
    if year:
        url += "&year=" + year
    raw = _http_get(url)
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    movies = data.get("movies") or data.get("data") or []
    if isinstance(movies, dict):
        movies = list(movies.values())
    for m in movies:
        mid = m.get("movieId") or m.get("id") or m.get("movieID")
        if mid:
            try:
                return int(mid)
            except Exception:
                pass
    return None


def _list_subs(movie_id, lang):
    url = "{}/subtitles?movieId={}&language={}&limit=100&sort=newest".format(
        _API, movie_id, lang)
    raw = _http_get(url)
    if not raw:
        return []
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return []
    subs = data.get("subtitles") or data.get("data") or []
    if isinstance(subs, dict):
        subs = list(subs.values())
    out = []
    for s in subs:
        sid = s.get("id") or s.get("subtitleId")
        if not sid:
            continue
        name = str(s.get("releaseName") or s.get("linkName") or s.get("name") or "")
        out.append((str(sid), name))
    return out


def _pick_best(subs, title):
    if not subs:
        return None
    tokens = [w for w in re.split(r"[^0-9a-zA-Z]+", title.lower()) if len(w) > 2]

    def score(item):
        name = item[1].lower()
        sc = sum(1 for w in tokens if w in name)
        if "bluray" in name or "brrip" in name or "web" in name:
            sc += 1
        return sc

    return max(subs, key=score)


def _looks_like_sub(raw):
    if not raw or len(raw) < 50:
        return False
    try:
        head = raw[:8192].decode("utf-8", "replace")
    except Exception:
        return False
    if "-->" in head:
        return True
    if head.lstrip().upper().startswith("WEBVTT"):
        return True
    if "[Script Info]" in head:
        return True
    return False


def _extract_sub_data(raw):
    """Raw text / zip archive / JSON-with-link → subtitle bytes."""
    if _looks_like_sub(raw):
        return raw
    if raw[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
            names = [n for n in zf.namelist()
                     if n.lower().endswith((".srt", ".ass", ".vtt", ".txt"))]
            for n in (names or zf.namelist()):
                data = zf.read(n)
                if _looks_like_sub(data):
                    return data
        except Exception:
            pass
        return None
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
        link = d.get("link") or d.get("url") or d.get("download") or d.get("file")
        if isinstance(link, str) and link.startswith("http"):
            raw2 = _http_get(link)
            if raw2:
                return _extract_sub_data(raw2)
    except Exception:
        pass
    return None


def _download_sub(sub_id):
    for url in ("{}/download?subtitleId={}".format(_API, sub_id),
                "{}/subtitle?subtitleId={}".format(_API, sub_id)):
        raw = _http_get(url)
        if raw:
            data = _extract_sub_data(raw)
            if data:
                return data
    return None


def _save(raw):
    try:
        os.makedirs(_SUB_DIR, exist_ok=True)
    except Exception:
        return None
    ext = ".srt"
    try:
        head = raw[:8192].decode("utf-8", "replace")
        if "[Script Info]" in head:
            ext = ".ass"
        elif head.lstrip().upper().startswith("WEBVTT"):
            ext = ".vtt"
    except Exception:
        pass
    path = os.path.join(_SUB_DIR, "auto_%d%s" % (int(time.time()), ext))
    try:
        with open(path, "wb") as fh:
            fh.write(raw)
        return path
    except Exception as e:
        _dbg("autosub save error: {}".format(e))
    return None


def auto_subtitle_async(screen, gen):
    t = threading.Thread(target=_worker, args=(screen, gen), daemon=True)
    t.start()


def _worker(screen, gen):
    try:
        title = _clean_title(getattr(screen, "title", ""))
        item_url = getattr(screen, "_item_url", "")
        if not title:
            return
        langs = [x.strip() for x in _cfg("autosub_langs", "arabic,english").split(",") if x.strip()]
        year = _year_of(title)
        _dbg("autosub: searching '{}' (year={})".format(title, year or "-"))
        movie_id = _search_movie(title, year)
        if not movie_id:
            _dbg("autosub: no movie match")
            return
        raw = chosen_lang = None
        for lang in langs:
            subs = _list_subs(movie_id, lang)
            if not subs:
                continue
            sub_id, sub_name = _pick_best(subs, title)
            _dbg("autosub: trying [{}] {} (id={})".format(lang, sub_name[:60], sub_id))
            raw = _download_sub(sub_id)
            if raw:
                chosen_lang = lang
                break
        if not raw:
            _dbg("autosub: nothing downloadable")
            return
        path = _save(raw)
        if not path:
            return
        _apply(screen, gen, path, item_url, chosen_lang or "")
    except Exception as e:
        _dbg("autosub worker error: {}".format(e))


def _apply(screen, gen, path, item_url, lang):
    def _do():
        try:
            if getattr(screen, "_autosub_gen", 0) != gen:
                return                      # player restarted/closed — stale
            from novaplay_subtitles import (get_subtitle_state,
                                            apply_subtitle,
                                            remember_subtitle)
            if get_subtitle_state().get("path"):
                return                      # user picked something meanwhile
            apply_subtitle(screen.session, path)
            if item_url:
                remember_subtitle(item_url, path, 0)
            _dbg("autosub: applied [{}] {}".format(lang, path))
            try:
                screen._autoSubNotify("CC ✓ ترجمة تلقائية")
            except Exception:
                pass
        except Exception as e:
            _dbg("autosub apply error: {}".format(e))
    try:
        from novaplay_thread import callInMainThread
        callInMainThread(_do)
    except Exception:
        _do()