# -*- coding: utf-8 -*-
"""
novaplay_subtitles.py — Subtitle engine for NovaPlay Media Center
==================================================================
- SubSource + OpenSubtitles.com online search & download (Arabic-first)
- Local subtitle auto-match (strict scoring, episode markers)
- Folder browser with [BEST] tagging
- Last-subtitle memory: re-attaches remembered subtitle (+sync) on replay
- Subtitle sync shift (+/-250 ms) via cached SRT rewrite — image-independent
- Auto-attach on playback start (optional)
- Settings screen with cache clearing (NovaSubtitleSettings)

Fixes vs. the original revision:
  * maybe_resume_subtitle no longer runs a 3000-entry disk scan on the
    MAIN thread (multi-second UI freeze 3.5s after playback start).
  * Downloaded text subtitles are normalized to UTF-8 at write time
    (cp1256 Arabic subs used to render garbled unless you happened to
    sync-shift them, which re-encoded as a side effect).
  * NovaOnlineSubsScreen: results and displayed labels are kept
    index-aligned (an empty label used to shift every later selection
    onto the WRONG subtitle).
  * Arabic-only titles now try TMDB for an English title before failing.
  * adjust_sync/reset_sync failures actually retry (via _apply_with_retry).
  * Negative sync shifts drop cues that shift fully into the past
    instead of emitting zero-duration cues at 00:00:00,000.
  * SubSource fallback queries use an 8s timeout instead of 18s each.
  * callInMainThread comes from the shared novaplay_thread module
    (fallback copy kept in case that file is missing).
  * JSON searches go through extractors.base.fetch (gzip/charset/retry);
    BINARY downloads stay on raw urlopen — base.fetch decodes text and
    would corrupt zip payloads.
  * find_best_local_subtitle also scans the downloads dir, so sidecar
    subtitles saved by plugin_downloads auto-match on later plays.

NovaSubtitleBrowser._refresh and NovaSubtitleSettings are the plugin's
ORIGINAL classes (verbatim); only the browser's _ok/_cancel handlers are
new code (the original handlers were never shared).

plugin.py integration (Edits A–D):
  A) from novaplay_subtitles import (maybe_resume_subtitle,
     remember_subtitle, apply_subtitle, get_subtitle_state,
     SYNC_STEP_MS, NovaSubtitleSettings)
  B) call maybe_resume_subtitle(self.session, self.title, self._item_url)
     right after _start_pos_tracker(...) in __onConfirmed
  C) bind a free key (e.g. blue) to a subtitle menu — see the menu
     methods in the re-add instructions
  D) open NovaSubtitleSettings from the plugin's settings screen

Adapted from the XtreamNew vodplayer engine, reworked for NovaPlay.
"""

import os
import re
import json
import time
import hashlib
import zipfile
import threading

try:
    from urllib.parse import quote, unquote, urlencode
except ImportError:
    from urllib import quote, unquote, urlencode

try:
    import urllib.request as _urlreq
except ImportError:
    import urllib2 as _urlreq

try:
    from io import BytesIO
except ImportError:
    try:
        from StringIO import StringIO as BytesIO
    except ImportError:
        BytesIO = None

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ScrollLabel import ScrollLabel
from enigma import eTimer

try:
    from Screens.VirtualKeyBoard import VirtualKeyBoard
except Exception:
    VirtualKeyBoard = None

try:
    from Components.FileList import FileList
except Exception:
    FileList = None

try:
    from plugin_state import _get_config, _set_config
except Exception:
    _get_config = None
    _set_config = None

# shared main-thread dispatcher (leaf module — no circular imports)
try:
    from novaplay_thread import callInMainThread
except Exception:
    # fallback: self-contained copy, so the module still imports if
    # novaplay_thread.py is missing
    _MAIN_LOCK = threading.Lock()
    _MAIN_QUEUE = []
    _MAIN_TIMER = None

    def _drain_main_queue():
        global _MAIN_TIMER
        with _MAIN_LOCK:
            items = list(_MAIN_QUEUE)
            del _MAIN_QUEUE[:]
        for _f, _a, _kw in items:
            try:
                _f(*_a, **_kw)
            except Exception:
                pass
        with _MAIN_LOCK:
            pending = bool(_MAIN_QUEUE)
        if pending and _MAIN_TIMER is not None:
            try:
                _MAIN_TIMER.start(50, True)
            except Exception:
                pass

    def callInMainThread(func, *args, **kwargs):
        global _MAIN_TIMER
        with _MAIN_LOCK:
            _MAIN_QUEUE.append((func, args, kwargs))
            need_timer = (_MAIN_TIMER is None)
            if need_timer:
                try:
                    _MAIN_TIMER = eTimer()
                    _MAIN_TIMER.callback.append(_drain_main_queue)
                except Exception:
                    _MAIN_TIMER = None
        if _MAIN_TIMER is not None:
            try:
                _MAIN_TIMER.start(50, True)
            except Exception:
                pass
        else:
            try:
                from twisted.internet import reactor
                reactor.callFromThread(_drain_main_queue)
            except Exception:
                pass

# optional: route JSON API reads through the extractor transport
# (gzip/deflate/charset handling + retries). BINARY downloads never go
# through this — fetch() decodes bodies as text and would corrupt zips.
try:
    from extractors.base import fetch as _BASE_FETCH
except Exception:
    _BASE_FETCH = None

PLUGIN_DIR = os.path.dirname(__file__)
SAFE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_OS_UA = "NovaPlayE2/4.0"

SUBTITLE_EXT_RE = re.compile(r"\.(srt|sub|txt|ass|ssa)$", re.I)
AUTO_SUB_MIN_SCORE = 200      # strict-score threshold for auto-attach
MAX_BROWSER_ENTRIES = 800     # cap folder browser list
MAX_SCAN_ENTRIES = 3000       # cap auto-match directory scan
SYNC_STEP_MS = 250            # subtitle sync adjustment step

_state_cfg = {}


def _cfg(name, default=""):
    """Read config NovaPlay-style ("true"/"false"), with in-memory fallback."""
    if _get_config is not None:
        try:
            v = _get_config(name, "")
            if v not in (None, ""):
                return v
        except Exception:
            pass
    return _state_cfg.get(name, default)


def _cfg_bool(name, default=False):
    v = str(_cfg(name, "true" if default else "false")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _cfg_set(name, value):
    _state_cfg[name] = value
    if _set_config is not None:
        try:
            _set_config(name, value)
        except Exception:
            pass


def _fmt_secs(value):
    try:
        value = int(value)
    except Exception:
        value = 0
    if value < 0:
        value = 0
    h, rest = divmod(value, 3600)
    m, s = divmod(rest, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


def _fmt_offset(ms):
    return ("%+d ms" % int(ms)) if ms else "0 ms"


# =============================================================================
#  Title / name cleaning
# =============================================================================

def clean_play_title(title):
    """Strips metadata-dict leaks and de-duplicates series titles:
    'Show - 1x1 Show Name...' -> 'Show - 1x1'."""
    txt = str(title or "").strip()
    if not txt:
        return ""
    txt = re.sub(r'\s*\{[\'"]?_unified_metadata.*$', "", txt).strip()
    txt = re.sub(r'\s*\|\s*\{[\'"]?_unified_metadata.*$', "", txt).strip()
    m = re.match(r"^(.*?)\s+-\s+((?:S\d+E\d+)|(?:\d+x\d+))\s+(.+)$", txt, re.I)
    if m:
        series = (m.group(1) or "").strip()
        marker = (m.group(2) or "").strip()
        tail = (m.group(3) or "").strip()
        norm_series = re.sub(r"[^a-z0-9]+", " ", series.lower()).strip()
        norm_tail = re.sub(r"[^a-z0-9]+", " ", tail.lower()).strip()
        if norm_series and norm_tail and (
            norm_series.startswith(norm_tail[:min(len(norm_tail), 18)]) or
            norm_tail.startswith(norm_series[:min(len(norm_series), 18)])
        ):
            txt = "%s - %s" % (series, marker)
    return txt


def clean_subtitle_query(title, url=""):
    """Build the best clean English-ish query from a (possibly Arabic) title."""
    txt = str(title or "").strip()
    if not txt:
        txt = str(url or "").strip()
    txt = re.sub(r"\s+-\s+Trailer\s*$", " ", txt, flags=re.I)
    txt = re.sub(r"\s+\|\s+.*$", " ", txt)
    txt = clean_play_title(txt)
    txt = re.sub(r"\\[cCpPbBuU][0-9A-Fa-f]{0,8}", " ", txt)
    low = _clean_name(txt)
    return low


def _clean_name(text):
    text = str(text or "").lower()
    text = os.path.basename(text)
    text = os.path.splitext(text)[0]
    text = re.sub(r"[._\-]+", " ", text)
    text = re.sub(r"\b(1080p|720p|480p|2160p|4k|x264|x265|h264|h265|hevc|bluray|blu-ray|web-?dl|web-?rip|web|hdrip|dvdrip|brrip|hdts|cam|nf|amzn|hbo|dsnp|proper|repack|extended|remastered|unrated|aac|ac3|eac3|ddp\d*|dd\+?|atmos|dts(hd)?|ma|yify|yts|tsx|hc|korsub|subs|ara|eng|arabic|english|مترجم|مدبلج|فيلم|مسلسل|حلقة|كامل)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _search_terms(title, url="", extra=""):
    candidates = []
    for item in (title, extra, url):
        txt = _clean_name(item)
        if txt and txt not in candidates:
            candidates.append(txt)
    expanded = []
    for txt in candidates:
        expanded.append(txt)
        for part in re.split(r"\s+", txt):
            if len(part) >= 3 and part not in expanded:
                expanded.append(part)
    return [x for x in expanded if x]


_STOPWORDS = set([
    'the', 'and', 'for', 'with', 'from', 'web', 'webrip', 'webdl', 'bluray',
    'hdrip', 'dvdrip', 'x264', 'x265', 'h264', 'h265', 'aac', 'ddp', 'atmos',
    'proper', 'repack', 'yts', 'brrip', 'movie', 'film', 'episode', 'season',
    'part', 'eng', 'english', 'arabic', 'ara', 'sub', 'subs', 'yify', 'final',
    'last', 'new', 'all', 'full',
])


def _significant_words(terms):
    words, seen = [], set()
    for term in (terms or []):
        clean = _clean_name(term)
        if not clean:
            continue
        for word in clean.split():
            if len(word) < 3 or word in _STOPWORDS or word in seen:
                continue
            seen.add(word)
            words.append(word)
    words.sort(key=lambda x: (-len(x), x))
    return words


def _episode_markers(terms):
    joined = " ".join([_clean_name(x) for x in (terms or [])])
    found = []
    for pat in (r"\bs\d{1,2}e\d{1,2}\b", r"\b\d{1,2}x\d{1,2}\b"):
        for m in re.findall(pat, joined, re.I):
            val = str(m).lower()
            if val not in found:
                found.append(val)
    return found


def _score_name(name, terms):
    low = _clean_name(name)
    if not low:
        return 0
    score = 0
    for term in (terms or []):
        term = _clean_name(term)
        if not term:
            continue
        if low == term:
            score += 500
        elif low.startswith(term) or term.startswith(low):
            score += 250
        elif term in low:
            score += 120
        else:
            pieces = [p for p in term.split(' ') if len(p) >= 3]
            hits = sum(1 for p in pieces if p in low)
            score += hits * 35
    if re.search(r"\b(ar|ara|arabic|english|eng|forced)\b", low):
        score += 10
    return score


def strict_score(name, terms, words, markers):
    """Strict mode: requires at least one strong word hit, rewards episode markers."""
    low = _clean_name(name)
    if not low:
        return 0
    hit_words = [w for w in (words or []) if w in low]
    if words and not hit_words:
        return 0
    score = 0
    for w in hit_words:
        score += 80 + min(len(w), 12)
    for term in (terms or []):
        term = _clean_name(term)
        if not term:
            continue
        if low == term:
            score += 500
        elif low.startswith(term):
            score += 260
        elif term in low:
            score += 120
    if markers:
        marker_hit = False
        for mk in markers:
            if mk in low:
                score += 160
                marker_hit = True
        if not marker_hit:
            score -= 120
    return score


# =============================================================================
#  Directory scanning (py2-safe)
# =============================================================================

def _iter_sub_entries(path):
    """Yield (name, full_path, is_dir) for subtitle files + dirs, bounded."""
    try:
        if hasattr(os, "scandir"):
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_dir():
                            yield (entry.name, entry.path, True)
                        elif SUBTITLE_EXT_RE.search(entry.name):
                            yield (entry.name, entry.path, False)
                    except Exception:
                        continue
        else:
            for name in os.listdir(path):
                full = os.path.join(path, name)
                try:
                    if os.path.isdir(full):
                        yield (name, full, True)
                    elif SUBTITLE_EXT_RE.search(name):
                        yield (name, full, False)
                except Exception:
                    continue
    except Exception:
        return


def subtitle_dir():
    d = _cfg("subtitle_dir", "/media/hdd/subtitles")
    return d if d and os.path.isdir(d) else ""


# =============================================================================
#  Subtitle memory (per item_url — detail-page URL, so it survives
#  server switches and restarts, same key as resume position)
# =============================================================================

def _subs_memory_path():
    base = "/media/hdd/NovaPlay/cache"
    if not os.path.exists("/media/hdd"):
        base = os.path.join(PLUGIN_DIR, "cache")
    try:
        if not os.path.exists(base):
            os.makedirs(base)
    except Exception:
        pass
    return os.path.join(base, "subtitle_memory.json")


def _load_subs_memory():
    try:
        with open(_subs_memory_path(), "r") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_subs_memory(data):
    try:
        # keep memory bounded: newest 400 entries
        if len(data) > 400:
            items = sorted(data.items(), key=lambda kv: int((kv[1] or {}).get("time") or 0), reverse=True)
            data = dict(items[:400])
        with open(_subs_memory_path(), "w") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def remember_subtitle(item_url, path, offset_ms=0):
    item_url = str(item_url or "").strip()
    if not item_url or not path:
        return
    data = _load_subs_memory()
    data[item_url] = {"path": str(path), "offset_ms": int(offset_ms or 0),
                      "time": int(time.time())}
    _save_subs_memory(data)
    global _CURRENT_SUB
    _CURRENT_SUB.update({"item_url": item_url, "path": str(path),
                         "offset_ms": int(offset_ms or 0)})


def recall_subtitle(item_url):
    entry = _load_subs_memory().get(str(item_url or "").strip())
    if isinstance(entry, dict) and entry.get("path") and os.path.exists(entry["path"]):
        return entry.get("path"), int(entry.get("offset_ms") or 0)
    return "", 0


# current in-session subtitle state (for sync adjustments)
_CURRENT_SUB = {"item_url": "", "path": "", "offset_ms": 0}


# =============================================================================
#  SubSource online provider
# =============================================================================

def _ss_cache_dir():
    base = "/media/hdd/NovaPlay/cache/subsource"
    if not os.path.exists("/media/hdd"):
        base = os.path.join(PLUGIN_DIR, "cache", "subsource")
    try:
        if not os.path.exists(base):
            os.makedirs(base)
    except Exception:
        pass
    return base


def _ss_api_key():
    return str(_cfg("subsource_api_key", "")).strip()


def _ss_headers(api_key):
    return {
        "User-Agent": SAFE_UA,
        "Accept": "application/json, */*",
        "X-API-Key": str(api_key or "").strip(),
    }


def _ss_read_json(url, api_key, timeout=16):
    # JSON search endpoint: prefer the extractor transport (handles
    # forced gzip, charsets, retries). Fallback: plain urlopen.
    if _BASE_FETCH is not None:
        try:
            text, _final = _BASE_FETCH(url, extra_headers=_ss_headers(api_key))
            if text:
                return json.loads(text)
            return None
        except Exception:
            return None
    try:
        req = _urlreq.Request(url, headers=_ss_headers(api_key))
        raw = _urlreq.urlopen(req, timeout=timeout).read()
        try:
            txt = raw.decode('utf-8', 'ignore')
        except Exception:
            txt = str(raw or '')
        return json.loads(txt)
    except Exception:
        return None


def _ss_collect_dicts(obj, out=None, depth=0):
    if out is None:
        out = []
    if depth > 5:
        return out
    if isinstance(obj, dict):
        low_keys = set([str(k).lower() for k in obj.keys()])
        if any(k in low_keys for k in ('id', 'subtitle_id', 'subtitleid')) and \
           any(k in low_keys for k in ('title', 'release', 'name', 'file_name', 'filename', 'language')):
            out.append(obj)
        for v in obj.values():
            _ss_collect_dicts(v, out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _ss_collect_dicts(item, out, depth + 1)
    return out


def _ss_value(row, keys):
    if not isinstance(row, dict):
        return ''
    for key in keys:
        for k, v in row.items():
            if str(k).lower() == key.lower():
                if isinstance(v, (str, int, float)):
                    return str(v)
                return v
    return ''


def _ss_looks_like_content(value):
    txt = str(value or '')
    if not txt:
        return False
    if re.search(r'\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}', txt):
        return True
    if txt.count('-->') >= 1:
        return True
    if len(txt) > 160 and ('-->' in txt or '\n' in txt or '\\N' in txt):
        return True
    return False


def _ss_clean_label(value):
    txt = str(value or '').strip()
    if not txt:
        return ''
    if _ss_looks_like_content(txt):
        return ''
    txt = txt.replace('\r', ' ').replace('\n', ' ')
    txt = re.sub(r'\s+', ' ', txt).strip()
    if _ss_looks_like_content(txt):
        return ''
    if '/' in txt or '\\' in txt:
        base = os.path.basename(txt.replace('\\', '/'))
        if base:
            txt = base
    txt = txt.strip(' ._-')
    low = txt.lower()
    if low in ('true', 'false', 'none', 'null', 'success', 'ok', 'data', 'results'):
        return ''
    if re.match(r'^\d{3,}$', txt):
        return ''
    if re.match(r'^[0-9a-f]{16,}$', low):
        return ''
    if low.startswith('subsource ') and re.search(r'\d{3,}', low):
        return ''
    if len(txt) > 150:
        txt = txt[:150].strip()
    return txt


def _ss_iter_release_info(value):
    out = []
    if isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_ss_iter_release_info(item))
    elif isinstance(value, dict):
        for key in ('release', 'releaseName', 'release_name', 'fileName',
                    'file_name', 'filename', 'name', 'title'):
            val = _ss_value(value, (key,))
            label = _ss_clean_label(val)
            if label:
                out.append(label)
    else:
        label = _ss_clean_label(value)
        if label:
            out.append(label)
    clean, seen = [], set()
    for item in out:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            clean.append(item)
    return clean


def _ss_pick_release_title(row, sid=''):
    if not isinstance(row, dict):
        return ''
    release_info = _ss_value(row, ('releaseInfo', 'release_info', 'releases', 'releaseInfos'))
    releases = _ss_iter_release_info(release_info)
    if releases:
        return releases[0]
    keys = (
        'release_name', 'releaseName', 'release_title', 'releaseTitle',
        'movie_release_name', 'movieReleaseName', 'release',
        'file_name', 'fileName', 'filename', 'file',
        'subtitle_name', 'subtitleName', 'full_name', 'fullName',
        'display_name', 'displayName', 'name', 'title',
    )
    for key in keys:
        val = _ss_value(row, (key,))
        if isinstance(val, dict):
            for subkey in keys:
                subval = _ss_value(val, (subkey,))
                label = _ss_clean_label(subval)
                if label and label != str(sid or '').strip():
                    return label
        label = _ss_clean_label(val)
        if label and label != str(sid or '').strip():
            return label
    return ''


def _ss_build_release_titles(row, sid=''):
    if not isinstance(row, dict):
        return []
    release_info = _ss_value(row, ('releaseInfo', 'release_info', 'releases', 'releaseInfos'))
    releases = _ss_iter_release_info(release_info)
    if releases:
        return releases
    title = _ss_pick_release_title(row, sid)
    return [title] if title else []


def _ss_language_rank(item):
    try:
        lang = str((item or {}).get('language') or '').strip().lower()
        label = str((item or {}).get('label') or '').strip().lower()
    except Exception:
        lang, label = '', ''
    if lang in ('arabic', 'ar', 'ara', 'العربية') or label.startswith('[arabic]') or label.startswith('[ar]'):
        return 0
    if lang in ('english', 'en', 'eng') or label.startswith('[english]') or label.startswith('[en]'):
        return 1
    return 2


def _ss_normalize_results(data, query_terms=None):
    rows = _ss_collect_dicts(data)
    terms = _search_terms(' '.join(query_terms or []))
    results, seen = [], set()
    for row in rows:
        sid = _ss_value(row, ('subtitleId', 'subtitle_id', 'id', 'subtitleid'))
        if isinstance(sid, dict):
            sid = _ss_value(sid, ('subtitleId', 'subtitle_id', 'id', 'subtitleid'))
        sid = str(sid or '').strip()
        if not sid:
            continue
        lang = _ss_value(row, ('language', 'lang', 'language_name', 'languageName'))
        if isinstance(lang, dict):
            lang = _ss_value(lang, ('name', 'language', 'code'))
        lang = str(lang or '').strip()
        release_titles = _ss_build_release_titles(row, sid)
        if not release_titles:
            release_titles = ['Subtitle %s' % sid]
        for rel_title in release_titles:
            title = _ss_clean_label(rel_title)
            if not title:
                continue
            unique_key = sid + '|' + title.lower()
            if unique_key in seen:
                continue
            seen.add(unique_key)
            score = _score_name(title, terms) if terms else 0
            if lang.lower() in ('arabic', 'ar', 'ara', 'العربية'):
                score += 80
            elif lang:
                score += 5
            label = ('[%s] %s' % (lang, title)) if lang else title
            results.append({'id': sid, 'title': title, 'language': lang,
                            'score': score, 'label': label})
    results.sort(key=lambda x: (_ss_language_rank(x), -int(x.get('score') or 0),
                                x.get('label', '').lower()))
    return results[:80]


def _ss_add_unique(out, value):
    try:
        value = str(value or '').strip()
    except Exception:
        value = ''
    if not value:
        return
    value = re.sub(r'\s+', ' ', value).strip()
    key = value.lower()
    for old in out:
        if str(old or '').strip().lower() == key:
            return
    out.append(value)


def _ss_title_fallbacks(title):
    """Base-first candidate list: 'Greenland 2 Migration' -> ['Greenland 2', full]."""
    raw = str(title or '').strip()
    out = []
    if not raw:
        return out
    clean = re.sub(r'[_\.\-]+', ' ', raw)
    clean = re.sub(r'\s+', ' ', clean).strip()
    base_candidates = []
    for sep in (':', '|', ' - ', ' – ', ' — '):
        if sep in raw:
            _ss_add_unique(base_candidates, raw.split(sep, 1)[0].strip())
    m = re.match(r'^(.+?\b\d{1,2})\b\s+.+$', clean)
    if m:
        _ss_add_unique(base_candidates, m.group(1).strip())
    words = clean.split()
    if len(words) > 3:
        _ss_add_unique(base_candidates, ' '.join(words[:3]))
    if len(words) > 2:
        _ss_add_unique(base_candidates, ' '.join(words[:2]))
    for item in base_candidates:
        _ss_add_unique(out, item)
    _ss_add_unique(out, raw)
    return out


def subsource_search(title, video_url='', api_key=''):
    api_key = str(api_key or '').strip()
    if not api_key:
        return False, 'SubSource API key is empty.\nAdd it in NovaPlay settings.', []
    query = clean_subtitle_query(title, video_url)
    if not query:
        return False, 'No usable title for subtitle search.', []
    base = 'https://api.subsource.net/api/v1'

    year = ''
    m = re.search(r'\b(19\d{2}|20\d{2})\b', query)
    if m:
        year = m.group(1)
    q = re.sub(r'\b(19\d{2}|20\d{2})\b', ' ', query)
    q = re.sub(r'\s+', ' ', q).strip()

    queries = [q]
    for cand in _ss_title_fallbacks(q):
        cq = _clean_name(cand)
        if cq and cq not in queries:
            queries.append(cq)
    parts = q.split()
    if len(parts) > 5 and ' '.join(parts[:5]) not in queries:
        queries.append(' '.join(parts[:5]))
    if len(parts) > 3 and ' '.join(parts[:3]) not in queries:
        queries.append(' '.join(parts[:3]))
    queries = queries[:5]

    movie_ids = []
    last_data = None
    for q_idx, query_str in enumerate(queries):
        # fallback queries get a short timeout — 5 x 18s sequential
        # misses used to cost ~90s before the "no match" message.
        q_timeout = 18 if q_idx == 0 else 8
        params = {'searchType': 'text', 'q': query_str, 'type': 'all'}
        if year:
            params['year'] = year
        url = base + '/movies/search?' + urlencode(params)
        data = _ss_read_json(url, api_key, timeout=q_timeout)
        if data is None and year:
            params.pop('year', None)
            url = base + '/movies/search?' + urlencode(params)
            data = _ss_read_json(url, api_key, timeout=q_timeout)
        if data is None:
            continue
        last_data = data
        try:
            rows = data.get('data') or data.get('results') or []
        except Exception:
            rows = []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get('movieId') or row.get('movie_id') or row.get('id') or '').strip()
            if mid and mid not in movie_ids:
                movie_ids.append(mid)
        if movie_ids:
            break

    if not movie_ids:
        if last_data is not None:
            return False, 'No SubSource match found for:\n%s' % query, []
        return False, 'SubSource search failed.\nCheck API key / connection.', []

    results = []
    for movie_id in movie_ids[:3]:
        for lang in ('arabic', 'english'):
            params = {'movieId': movie_id, 'language': lang, 'limit': 100, 'sort': 'newest'}
            data = _ss_read_json(base + '/subtitles?' + urlencode(params), api_key, timeout=25)
            if data is None:
                continue
            results.extend(_ss_normalize_results(data, [query, video_url]))
        if results:
            break

    if not results:
        return False, 'No SubSource subtitles found for:\n%s' % query, []

    unique, seen = [], set()
    for item in sorted(results, key=lambda x: (_ss_language_rank(x), -int(x.get('score') or 0),
                                               x.get('label', '').lower())):
        sid = str(item.get('id') or '')
        tkey = str(item.get('title') or item.get('label') or '').strip().lower()
        key = sid + '|' + tkey
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return True, '', unique[:80]


def _ss_pick_srt_from_zip(raw, filename_base):
    if BytesIO is None:
        return None, ''
    try:
        zf = zipfile.ZipFile(BytesIO(raw))
    except Exception:
        return None, ''
    best_name, best_data = '', None
    try:
        names = zf.namelist()
    except Exception:
        names = []
    for name in names:
        low = str(name or '').lower()
        if low.endswith(('.srt', '.sub', '.txt', '.ass', '.ssa')) and not low.endswith('/'):
            try:
                data = zf.read(name)
                if data:
                    best_name = os.path.basename(name) or (filename_base + '.srt')
                    best_data = data
                    if low.endswith('.srt'):
                        break
            except Exception:
                pass
    return best_data, best_name


def _normalize_text_subtitle_bytes(data):
    """Best-effort convert a text subtitle to UTF-8.
    FIX: cp1256 Arabic subs used to stay cp1256 on disk unless the user
    sync-shifted them (which re-encoded as a side effect)."""
    if not data:
        return data
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 BOM
        try:
            return data.decode("utf-16").encode("utf-8")
        except Exception:
            return data
    for enc in ("utf-8-sig", "utf-8", "cp1256", "iso-8859-6"):
        try:
            return data.decode(enc).encode("utf-8")
        except (UnicodeDecodeError, LookupError):
            continue
    return data  # last resort: pass through untouched


def _write_subtitle_file(data, out_name):
    out_path = os.path.join(_ss_cache_dir(), out_name)
    tmp = out_path + ".tmp"
    try:
        if out_name.lower().endswith((".srt", ".txt", ".ass", ".ssa")):
            # NB: ".sub" intentionally excluded — VobSub .sub is binary
            # (idx/sub pair); text conversion would corrupt it.
            data = _normalize_text_subtitle_bytes(data)
        with open(tmp, 'wb') as f:
            f.write(data)
        os.rename(tmp, out_path)   # atomic on same dir, py2-safe
        return out_path
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return ""


def subsource_download(subtitle_id, title, api_key=''):
    # NOTE: binary payload (possibly a zip) — deliberately raw urlopen,
    # NOT extractors.base.fetch (which decodes bodies as text).
    api_key = str(api_key or '').strip()
    subtitle_id = str(subtitle_id or '').strip()
    if not api_key or not subtitle_id:
        return False, 'Missing SubSource data.', ''
    url = 'https://api.subsource.net/api/v1/subtitles/%s/download' % subtitle_id
    try:
        req = _urlreq.Request(url, headers=_ss_headers(api_key))
        raw = _urlreq.urlopen(req, timeout=25).read()
    except Exception:
        return False, 'SubSource download failed.', ''
    if not raw:
        return False, 'Empty SubSource subtitle file.', ''
    filename_base = re.sub(r'[^A-Za-z0-9_\-\.]+', '_', _clean_name(title) or
                           ('subsource_%s' % subtitle_id)).strip('._') or ('subsource_%s' % subtitle_id)
    data, out_name = raw, filename_base + '.srt'
    if raw[:2] == b'PK':
        picked, picked_name = _ss_pick_srt_from_zip(raw, filename_base)
        if not picked:
            return False, 'Downloaded zip has no supported subtitle file.', ''
        data = picked
        out_name = picked_name or out_name
    elif raw[:1] in (b'{', b'['):
        got = False
        try:
            js = json.loads(raw.decode('utf-8', 'ignore'))
            link = _ss_value(js, ('url', 'download_url', 'downloadUrl', 'link'))
            if link:
                fetched = _urlreq.urlopen(
                    _urlreq.Request(str(link), headers=_ss_headers(api_key)),
                    timeout=25).read()
                if fetched:
                    data = fetched
                    got = True
        except Exception:
            got = False
        if not got:
            # JSON body with no usable link — the old code fell through
            # and wrote the JSON itself out as an ".srt" file.
            return False, 'SubSource returned no downloadable file.', ''
    out_path = _write_subtitle_file(data, out_name)
    if out_path:
        return True, '', out_path
    return False, 'Write failed.', ''


# =============================================================================
#  OpenSubtitles.com provider
# =============================================================================

_OS_BASE = "https://api.opensubtitles.com/api/v1"


def _os_api_key():
    return str(_cfg("opensubtitles_api_key", "")).strip()


def _os_headers(api_key, json_body=False):
    h = {
        "Api-Key": str(api_key or "").strip(),
        "User-Agent": _OS_UA,
        "Accept": "application/json",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _os_read_json(url, api_key, timeout=20):
    if _BASE_FETCH is not None:
        try:
            text, _final = _BASE_FETCH(url, extra_headers=_os_headers(api_key))
            if text:
                return json.loads(text)
            return None
        except Exception:
            return None
    try:
        req = _urlreq.Request(url, headers=_os_headers(api_key))
        raw = _urlreq.urlopen(req, timeout=timeout).read()
        return json.loads(raw.decode('utf-8', 'ignore'))
    except Exception:
        return None


def opensubtitles_search(title, video_url='', api_key=''):
    api_key = str(api_key or '').strip()
    if not api_key:
        return False, 'OpenSubtitles API key is empty.\nAdd it in NovaPlay settings.', []
    query = clean_subtitle_query(title, video_url)
    if not query:
        return False, 'No usable title for subtitle search.', []

    year = ''
    m = re.search(r'\b(19\d{2}|20\d{2})\b', query)
    if m:
        year = m.group(1)
    q = re.sub(r'\b(19\d{2}|20\d{2})\b', ' ', query)
    q = re.sub(r'\s+', ' ', q).strip()
    if not q:
        q = query

    results = []
    last_ok = False
    for q_idx, query_str in enumerate([q] + _ss_title_fallbacks(q)[:2]):
        params = {'query': query_str, 'languages': 'ar,en'}
        if year:
            params['movieyear'] = year
        url = _OS_BASE + '/subtitles?' + urlencode(params)
        data = _os_read_json(url, api_key, timeout=(20 if q_idx == 0 else 10))
        if data is None:
            continue
        last_ok = True
        rows = []
        try:
            rows = data.get('data') or []
        except Exception:
            rows = []
        if not isinstance(rows, list):
            continue
        for row in rows:
            try:
                attrs = row.get('attributes') or {}
                lang = str(attrs.get('language') or '').strip()
                files = attrs.get('files') or []
                feat = attrs.get('feature_details') or {}
                base_name = (_ss_clean_label(attrs.get('release')) or
                             _ss_clean_label(feat.get('movie_name')) or
                             _ss_clean_label(feat.get('title')) or 'Subtitle')
                for f in files:
                    fid = str(f.get('file_id') or '').strip()
                    if not fid:
                        continue
                    fname = _ss_clean_label(f.get('file_name')) or base_name
                    score = _score_name(fname, [query]) + _score_name(base_name, [query])
                    if lang.lower() in ('arabic', 'ar', 'ara', 'العربية'):
                        score += 80
                    results.append({'id': fid, 'title': fname, 'language': lang,
                                    'score': score,
                                    'label': ('[%s] %s' % (lang, fname)) if lang else fname})
            except Exception:
                continue
        if results:
            break

    if not results:
        if last_ok:
            return False, 'No OpenSubtitles match found for:\n%s' % query, []
        return False, 'OpenSubtitles search failed.\nCheck API key / connection.', []

    unique, seen = [], set()
    for item in sorted(results, key=lambda x: (_ss_language_rank(x), -int(x.get('score') or 0),
                                               x.get('label', '').lower())):
        key = str(item.get('id') or '') + '|' + str(item.get('title') or '').lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return True, '', unique[:80]


def opensubtitles_download(file_id, title, api_key=''):
    api_key = str(api_key or '').strip()
    file_id = str(file_id or '').strip()
    if not api_key or not file_id:
        return False, 'Missing OpenSubtitles data.', ''
    try:
        body = json.dumps({'file_id': int(file_id)})
    except (TypeError, ValueError):
        return False, 'Invalid OpenSubtitles file id.', ''
    js = None
    # POST returns JSON (text) — base.fetch is fine here.
    if _BASE_FETCH is not None:
        try:
            text, _f = _BASE_FETCH(_OS_BASE + '/download',
                                   extra_headers=_os_headers(api_key, json_body=True),
                                   post_data=body)
            if text:
                js = json.loads(text)
        except Exception:
            js = None
    if js is None:
        try:
            req = _urlreq.Request(_OS_BASE + '/download', data=body.encode('utf-8'),
                                  headers=_os_headers(api_key, json_body=True))
            raw = _urlreq.urlopen(req, timeout=25).read()
            js = json.loads(raw.decode('utf-8', 'ignore'))
        except Exception:
            return False, 'OpenSubtitles download failed.', ''
    link = _ss_value(js, ('link', 'url', 'download_url'))
    if not link:
        return False, 'OpenSubtitles returned no download link.', ''
    try:
        # binary payload (possibly zip) — raw urlopen, NOT base.fetch
        req2 = _urlreq.Request(str(link), headers={"User-Agent": _OS_UA})
        data = _urlreq.urlopen(req2, timeout=30).read()
    except Exception:
        return False, 'OpenSubtitles file fetch failed.', ''
    if not data:
        return False, 'Empty OpenSubtitles subtitle file.', ''
    filename_base = re.sub(r'[^A-Za-z0-9_\-\.]+', '_', _clean_name(title) or
                           ('opensubs_%s' % file_id)).strip('._') or ('opensubs_%s' % file_id)
    out_name = filename_base + '.srt'
    if data[:2] == b'PK':
        picked, picked_name = _ss_pick_srt_from_zip(data, filename_base)
        if not picked:
            return False, 'Downloaded zip has no supported subtitle file.', ''
        data = picked
        out_name = picked_name or out_name
    out_path = _write_subtitle_file(data, out_name)
    if out_path:
        return True, '', out_path
    return False, 'Write failed.', ''


_PROVIDERS = {
    "subsource": {"name": "SubSource", "key": _ss_api_key,
                  "search": subsource_search, "download": subsource_download},
    "opensubtitles": {"name": "OpenSubtitles", "key": _os_api_key,
                      "search": opensubtitles_search, "download": opensubtitles_download},
}


# =============================================================================
#  SRT sync shift — block-aware: cues that shift fully into the past are
#  DROPPED (the old regex version clamped them to 00:00:00,000 zero-length
#  cues that some players render as a flicker at playback start).
# =============================================================================

_TS_RE = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')
_TIMING_RE = re.compile(
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')


def _ts_ms(h, m, s, ms):
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms))


def _fmt_ts(total_ms):
    if total_ms < 0:
        total_ms = 0
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _shift_srt_text(text, delta_ms):
    if not text or not delta_ms:
        return text
    delta = int(delta_ms)
    blocks = re.split(r'\r?\n[ \t]*\r?\n', text)
    if len(blocks) == 1 and text.count('-->') > 1:
        # malformed SRT without blank-line separators — fall back to the
        # old pure regex substitution so we never make things worse
        def _repl(m):
            start = _ts_ms(m.group(1), m.group(2), m.group(3), m.group(4)) + delta
            end = _ts_ms(m.group(5), m.group(6), m.group(7), m.group(8)) + delta
            return _fmt_ts(start) + " --> " + _fmt_ts(end)
        return _TIMING_RE.sub(_repl, text)
    kept = []
    for block in blocks:
        if '-->' not in block:
            kept.append(block)
            continue

        def _repl(m):
            start = _ts_ms(m.group(1), m.group(2), m.group(3), m.group(4)) + delta
            end = _ts_ms(m.group(5), m.group(6), m.group(7), m.group(8)) + delta
            return _fmt_ts(start) + " --> " + _fmt_ts(end)

        new_block = _TIMING_RE.sub(_repl, block, count=1)
        tm = _TIMING_RE.search(new_block)
        if tm:
            end_ms = _ts_ms(tm.group(5), tm.group(6), tm.group(7), tm.group(8))
            if end_ms <= 0:
                continue  # cue shifted entirely before playback start
        kept.append(new_block)
    return "\n\n".join(kept)


def _make_shifted_copy(path, offset_ms):
    """Write a timestamp-shifted copy of an .srt into cache; returns new path
    or '' on failure (non-srt / unreadable)."""
    if not path or offset_ms == 0 or not str(path).lower().endswith('.srt'):
        return ""
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
        text = None
        for enc in ('utf-8', 'utf-8-sig', 'cp1256', 'latin-1'):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                text = None
        if text is None:
            return ""
        shifted = _shift_srt_text(text, offset_ms)
        base = os.path.splitext(os.path.basename(path))[0]
        base = re.sub(r'(_sync[+-]?\d+)?$', '', base) or 'sub'
        out_name = "%s_sync%+d.srt" % (re.sub(r'[^A-Za-z0-9_\-\.]+', '_', base), int(offset_ms))
        out_path = os.path.join(_ss_cache_dir(), out_name)
        with open(out_path, 'wb') as fh:
            fh.write(shifted.encode('utf-8'))
        return out_path
    except Exception:
        return ""


def get_subtitle_state():
    return dict(_CURRENT_SUB)


def adjust_sync(session, delta_ms):
    """Shift subtitle timing by delta_ms and re-attach. Returns status text."""
    state = _CURRENT_SUB
    path = state.get("path") or ""
    if not path or not os.path.exists(path):
        return "No subtitle active"
    if not path.lower().endswith('.srt'):
        return "Sync: SRT files only"
    orig = state.get("_orig_path") or path
    base_path = orig if os.path.exists(orig) else path
    new_offset = int(state.get("offset_ms") or 0) + int(delta_ms)
    if new_offset == 0:
        shifted = base_path
    else:
        shifted = _make_shifted_copy(base_path, new_offset)
        if not shifted:
            return "Sync shift failed"
    _CURRENT_SUB.update({"path": shifted, "_orig_path": base_path,
                         "offset_ms": new_offset})
    disable_subtitle(session)
    ok = apply_subtitle(session, shifted)
    if not ok:
        # FIX: actually retry (service often isn't ready immediately)
        _apply_with_retry(session, shifted, tries=3)
    if state.get("item_url"):
        remember_subtitle(state["item_url"], base_path, new_offset)
    return "Sync: %s%s" % (_fmt_offset(new_offset), "" if ok else " (retrying)")


def reset_sync(session):
    state = _CURRENT_SUB
    base_path = state.get("_orig_path") or state.get("path") or ""
    if not base_path or not os.path.exists(base_path):
        return "No subtitle active"
    _CURRENT_SUB.update({"path": base_path, "_orig_path": base_path, "offset_ms": 0})
    disable_subtitle(session)
    ok = apply_subtitle(session, base_path)
    if not ok:
        _apply_with_retry(session, base_path, tries=3)
    if state.get("item_url"):
        remember_subtitle(state["item_url"], base_path, 0)
    return "Sync: 0 ms%s" % ("" if ok else " (retrying)")


# =============================================================================
#  Subtitle attach / detach on the running service
# =============================================================================

def apply_subtitle(session, path):
    """Attach an external subtitle file to the currently playing service.
    Subtitle Studio mode renders via the custom engine instead of the
    service layer; non-SRT or engine failure falls back to the service."""
    path = str(path or '')
    if not path or not os.path.exists(path):
        return False
    if _cfg_bool("substudio_mode", False):
        try:
            from novaplay_substudio import STUDIO
            if STUDIO.attach(path):
                try:
                    # avoid double rendering: kill the service subtitle
                    svc = session.nav.getCurrentService()
                    if svc:
                        sub = svc.subtitle()
                        if sub:
                            sub.disableSubtitle()
                except Exception:
                    pass
                return True
        except Exception:
            pass
    try:
        service = session.nav.getCurrentService()
        if not service:
            return False
        sub = service.subtitle()
        if not sub:
            return False
        for sub_tuple in ((1, 0, 0, path), (1, 0, 1, path)):
            try:
                sub.enableSubtitle(sub_tuple)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def disable_subtitle(session):
    try:
        from novaplay_substudio import STUDIO
        STUDIO.detach()
    except Exception:
        pass
    try:
        service = session.nav.getCurrentService()
        if service:
            sub = service.subtitle()
            if sub:
                sub.disableSubtitle()
                return True
    except Exception:
        pass
    return False


# =============================================================================
#  Local auto-match + memory-first attach on playback start
# =============================================================================

def find_best_local_subtitle(title, url="", extra_roots=None):
    """Score subtitle files in the configured dir (+ one subdir level,
    plus extra roots, plus the downloads dir so sidecar subs match).
    Returns (path, score) or ('', 0)."""
    roots = []
    root = subtitle_dir()
    if root:
        roots.append(root)
    for extra in (extra_roots or []):
        if extra and extra not in roots and os.path.isdir(extra):
            roots.append(extra)
    try:
        from plugin_downloads import download_dir as _dl_dir
        _dl = _dl_dir()
        if _dl and _dl not in roots and os.path.isdir(_dl):
            roots.append(_dl)
    except Exception:
        pass
    if not roots:
        return '', 0
    title = clean_play_title(title)
    terms = _search_terms(title, url)
    if not terms:
        return '', 0
    words = _significant_words(terms)
    markers = _episode_markers(terms)

    scored = []
    scanned = [0]

    def _scan(dirpath, depth):
        if scanned[0] >= MAX_SCAN_ENTRIES:
            return
        for name, full, is_dir in _iter_sub_entries(dirpath):
            scanned[0] += 1
            if scanned[0] >= MAX_SCAN_ENTRIES:
                return
            if is_dir:
                if depth == 0:
                    _scan(full, depth + 1)
                continue
            sc = strict_score(name, terms, words, markers)
            if sc > 0:
                scored.append((sc, full))

    try:
        for r in roots:
            _scan(r, 0)
    except Exception:
        return '', 0
    if not scored:
        return '', 0
    best = max(scored, key=lambda x: x[0])
    return best[1], best[0]


_RETRY_TIMERS = []


def _apply_with_retry(session, path, tries=3, delay_ms=2500):
    """Service may not be ready right at playback start — retry a few times."""
    if tries <= 0:
        return
    if apply_subtitle(session, path):
        return
    t = eTimer()

    def _retry():
        _apply_with_retry(session, path, tries - 1, delay_ms)
    try:
        t.callback.append(_retry)
        t.start(delay_ms, True)
    except Exception:
        pass
    global _RETRY_TIMERS
    _RETRY_TIMERS.append(t)
    if len(_RETRY_TIMERS) > 6:
        _RETRY_TIMERS = _RETRY_TIMERS[-6:]


_AUTO_SUB_TIMER = None


def _apply_path_with_offset(session, item_url, path, offset_ms):
    """Attach subtitle, applying a remembered sync offset if any."""
    if not path or not os.path.exists(path):
        return False
    target = path
    if offset_ms:
        shifted = _make_shifted_copy(path, offset_ms)
        if shifted:
            target = shifted
    _CURRENT_SUB.update({"item_url": item_url or "", "path": target,
                         "_orig_path": path, "offset_ms": int(offset_ms or 0)})
    ok = apply_subtitle(session, target)
    if not ok:
        _apply_with_retry(session, target, tries=3)
    return True


def maybe_resume_subtitle(session, title, item_url=""):
    """Call right after playback starts. Priority:
    1) remembered subtitle for this item (+ remembered sync)
    2) auto-matched local subtitle (if 'auto_subtitle' enabled)

    FIX: the disk scan now runs on a WORKER thread — it used to run in
    the eTimer callback (= main thread) and could freeze the UI for
    seconds right when the user reaches for the remote."""
    item_url = str(item_url or "").strip()

    def _do():
        try:
            _CURRENT_SUB.update({"item_url": item_url, "path": "",
                                 "_orig_path": "", "offset_ms": 0})
            path, offset = recall_subtitle(item_url)
            if path:
                _apply_path_with_offset(session, item_url, path, offset)
                return
            if _cfg_bool("auto_subtitle", False):
                def _scan():
                    try:
                        lpath, score = find_best_local_subtitle(title, item_url)
                        if lpath and score >= AUTO_SUB_MIN_SCORE:
                            callInMainThread(_apply_path_with_offset,
                                             session, item_url, lpath, 0)
                    except Exception:
                        pass
                threading.Thread(target=_scan, daemon=True).start()
        except Exception:
            pass

    global _AUTO_SUB_TIMER
    try:
        if _AUTO_SUB_TIMER is not None:
            try:
                _AUTO_SUB_TIMER.stop()
            except Exception:
                pass
        _AUTO_SUB_TIMER = eTimer()
        _AUTO_SUB_TIMER.callback.append(_do)
        _AUTO_SUB_TIMER.start(3500, True)
    except Exception:
        threading.Thread(target=_do, daemon=True).start()


def maybe_auto_subtitle(session, title, url="", delay_ms=3500):
    """Backward-compatible wrapper (memory-first is maybe_resume_subtitle)."""
    maybe_resume_subtitle(session, title, url)


# =============================================================================
#  Screens
# =============================================================================

def _menu_index(menu_list, entries_count):
    """Robust current-index lookup across Enigma2 generations."""
    try:
        idx = menu_list.getSelectedIndex()
        if idx is not None and 0 <= int(idx) < entries_count:
            return int(idx)
    except Exception:
        pass
    try:
        cur = menu_list.getCurrent()
    except Exception:
        return -1
    if isinstance(cur, tuple) and len(cur) >= 2:
        try:
            idx = int(cur[1])
            if 0 <= idx < entries_count:
                return idx
        except Exception:
            pass
    elif isinstance(cur, int):
        if 0 <= cur < entries_count:
            return cur
    return -1


class NovaOnlineSubsScreen(Screen):
    """Background online search + download (SubSource or OpenSubtitles);
    closes with the .srt path."""

    skin = """
    <screen name="NovaOnlineSubsScreen" position="center,center" size="1700,940" title="Online subtitles" flags="wfNoBorder">
        <eLabel position="0,0" size="1700,940" backgroundColor="#0D1117" zPosition="0" />
        <eLabel position="0,0" size="1700,90" backgroundColor="#161B22" zPosition="1" />
        <widget name="header" position="50,20" size="1600,50" font="Regular;38" foregroundColor="#00E5FF" transparent="1" zPosition="2" />
        <widget name="list" position="40,110" size="1620,720" scrollbarMode="showOnDemand" foregroundColor="#F0F6FC" foregroundColorSelected="#00E5FF" backgroundColor="#161B22" backgroundColorSelected="#21262D" font="Regular;40" itemHeight="60" zPosition="2" />
        <widget name="hint" position="50,860" size="1600,40" font="Regular;28" foregroundColor="#8B949E" transparent="1" zPosition="2" />
    </screen>"""

    def __init__(self, session, title="", video_url="", provider="subsource", api_key=""):
        Screen.__init__(self, session)
        self.skinName = ["NovaOnlineSubsScreen"]
        self.search_title = str(title or "")
        self.video_url = str(video_url or "")
        self.provider = provider if provider in _PROVIDERS else "subsource"
        self.api_key = str(api_key or "").strip()
        self.results = []
        self.chosen = None
        self.mode = "search"
        prov_name = _PROVIDERS[self.provider]["name"]

        self["header"] = Label("Searching %s ..." % prov_name)
        self["hint"] = Label("OK = download      EXIT = back")
        self["list"] = MenuList([])
        self["actions"] = ActionMap(["OkCancelActions"], {
            "ok": self._ok,
            "cancel": self._cancel,
        }, -1)
        threading.Thread(target=self._searchThread, daemon=True).start()

    def _searchThread(self):
        title = self.search_title
        # (1) collapse Latin series markers
        try:
            from novaplay_subtitles import clean_play_title as _cpt
            title = _cpt(title) or title
        except Exception:
            pass
        # (2) NEW: strip Arabic season/episode words → bare series name
        try:
            _ep_markers = re.search(
                r"(?:الموسم|موسم|الحلقة|حلقة|والأخيرة|والاخيرة|الأخيرة|الاخيرة|مترجم(?:ة)?|مدبلج(?:ة)?|كامل|مسلسل)\s*[\d\u0660-\u0669]*",
                title)
            if _ep_markers:
                _bare_series = title[:_ep_markers.start()].strip(" -_|")
                if len(_bare_series) >= 3:
                    title = _bare_series
        except Exception:
            pass
        # (3) TMDB English-title fallback (now also fires on Arabic-heavy queries)
        try:
            q = clean_subtitle_query(title, self.video_url)
            _ar_ratio = sum(1 for c in (q or "") if "\u0600" <= c <= "\u06ff") / max(1, len(q or " "))
            if not q or len(q.strip()) < 3 or _ar_ratio > 0.5:
                from plugin_tmdb import _tmdb_search_metadata
                meta = _tmdb_search_metadata(title, "", "movie")
                if isinstance(meta, dict):
                    for k in ("title", "original_title", "name", "original_name"):
                        if meta.get(k):
                            title = str(meta[k])
                            break
        except Exception:
            pass
        prov = _PROVIDERS[self.provider]
        ok, error, results = prov["search"](title, self.video_url, self.api_key)
        callInMainThread(self._onSearchDone, results or [])

    def _downloadThread(self):
        prov = _PROVIDERS[self.provider]
        ok, error, path = prov["download"](
            self.chosen.get("id", ""), self.chosen.get("title", ""), self.api_key)
        callInMainThread(self._onDownloadDone, bool(ok), error or "Download failed.", path or "")

    def _onSearchDone(self, results):
        # FIX: user may EXIT while the search thread is in flight —
        # touching widgets on a closed screen raises.
        try:
            if self.session is None:   # closed screens null this in Enigma2
                return
        except Exception:
            return
        # FIX: results and labels are filtered TOGETHER —
        # filtered empty labels out of the display list only, so any
        # empty label shifted every selection after it onto the wrong
        # subtitle (wrong file downloaded).
        pairs = []
        for i in (results or []):
            label = str(i.get("label") or i.get("title") or "")
            if label:
                pairs.append((label, i))
        if not pairs:
            self._fail('No subtitles found.')
            return
        self.results = [i for _, i in pairs]
        self["list"].setList([l for l, _ in pairs])
        ar = sum(1 for i in self.results if str(i.get("language", "")).lower() in ("arabic", "ar", "ara", "العربية"))
        en = sum(1 for i in self.results if str(i.get("language", "")).lower() in ("english", "en", "eng"))
        self["header"].setText("%s: %d subs — عربي %d / English %d (اسحب للأسفل)" % (
            _PROVIDERS[self.provider]["name"], len(pairs), ar, en))
        
    def _onDownloadDone(self, ok, error, path):
        try:
            if self.session is None:
                return
        except Exception:
            return
        if ok and path:
            self.close(path)
        else:
            self._fail(error)

    def _fail(self, message):
        try:
            self.session.openWithCallback(
                lambda a: self.close(""), MessageBox,
                message, MessageBox.TYPE_ERROR, timeout=10)
        except Exception:
            self.close("")

    def _ok(self):
        if self.mode != "search" or not self.results:
            return
        idx = _menu_index(self["list"], len(self.results))
        if idx < 0:
            return
        self.chosen = self.results[idx]
        self.mode = "download"
        self["header"].setText("Downloading: %s" % (self.chosen.get("title") or ""))
        threading.Thread(target=self._downloadThread, daemon=True).start()

    def _cancel(self):
        self.close("")


# Backwards-compatible alias (older patches referenced this name)
NovaSubSourceScreen = NovaOnlineSubsScreen


class NovaSubtitleBrowser(Screen):
    """Folder browser for local subtitle files; best match tagged [BEST]."""

    skin = """
    <screen name="NovaSubtitleBrowser" position="center,center" size="1500,860" title="Select subtitle" flags="wfNoBorder">
        <eLabel position="0,0" size="1500,860" backgroundColor="#0D1117" zPosition="0" />
        <eLabel position="0,0" size="1500,80" backgroundColor="#161B22" zPosition="1" />
        <widget name="path" position="40,18" size="1420,44" font="Regular;30" foregroundColor="#00E5FF" transparent="1" zPosition="2" />
        <widget name="list" position="30,100" size="1440,680" scrollbarMode="showOnDemand" zPosition="2" />
        <widget name="hint" position="40,800" size="1420,36" font="Regular;24" foregroundColor="#8B949E" transparent="1" zPosition="2" />
    </screen>"""

    def __init__(self, session, start_dir="", video_title=""):
        Screen.__init__(self, session)
        self.skinName = ["NovaSubtitleBrowser"]
        video_title = clean_play_title(video_title)
        self.terms = _search_terms(video_title)
        self.words = _significant_words(self.terms)
        self.markers = _episode_markers(self.terms)
        self.entries = []
        self.current_dir = start_dir or subtitle_dir() or "/media/hdd"

        self["path"] = Label(self.current_dir)
        self["hint"] = Label("OK = select      EXIT = up / back")
        self["list"] = MenuList([])
        self["actions"] = ActionMap(["OkCancelActions"], {
            "ok": self._ok,
            "cancel": self._cancel,
        }, -1)
        self.onLayoutFinish.append(self._refresh)

    def _refresh(self):
        entries = []
        try:
            for name, full, is_dir in _iter_sub_entries(self.current_dir):
                entries.append((name, full, is_dir))
                if len(entries) >= MAX_BROWSER_ENTRIES:
                    break
            entries.sort(key=lambda item: (0 if item[2] else 1, item[0].lower()))
        except Exception:
            entries = []

        best_path, best_score = "", 0
        for name, full, is_dir in entries:
            if not is_dir:
                sc = strict_score(name, self.terms, self.words, self.markers)
                if sc > best_score:
                    best_score, best_path = sc, full

        labels = []
        self.entries = []
        for name, full, is_dir in entries:
            if is_dir:
                labels.append("[ %s ]" % name)
            else:
                labels.append(name + ("  [BEST]" if full == best_path and best_score > 0 else ""))
            self.entries.append((name, full, is_dir))
        self["list"].setList(labels)
        self["path"].setText(self.current_dir or "/")

    # ── _ok / _cancel are the only new code in this class (the original
    # handlers were never shared). They close(path) on a file pick,
    # matching plugin.py's _onSubtitlePicked callback contract. ──
    def _ok(self):
        idx = _menu_index(self["list"], len(self.entries))
        if idx < 0 or idx >= len(self.entries):
            return
        name, full, is_dir = self.entries[idx]
        if is_dir:
            self.current_dir = full
            self._refresh()
        elif os.path.exists(full):
            self.close(full)

    def _cancel(self):
        parent = os.path.dirname(self.current_dir.rstrip("/")) or "/"
        if parent and parent != self.current_dir:
            self.current_dir = parent
            self._refresh()
        elif self.current_dir not in ("/", "/media"):
            # top of the subtree — jump to "/" so mounted USB/HDD
            # devices are reachable instead of closing the browser
            self.current_dir = "/"
            self._refresh()
        else:
            self.close("")


class NovaFolderBrowser(Screen):
    """Directory picker for the subtitle folder — remote-friendly
    replacement for typing a path on the VirtualKeyBoard."""

    skin = """
    <screen name="NovaFolderBrowser" position="center,center" size="1500,860" title="Select Folder" flags="wfNoBorder">
        <eLabel position="0,0" size="1500,860" backgroundColor="#0D1117" zPosition="0" />
        <eLabel position="0,0" size="1500,80" backgroundColor="#161B22" zPosition="1" />
        <widget name="path" position="40,18" size="1420,44" font="Regular;30" foregroundColor="#00E5FF" transparent="1" zPosition="2" />
        <widget name="list" position="30,100" size="1440,680" scrollbarMode="showOnDemand" zPosition="2" />
        <widget name="hint" position="40,800" size="1420,36" font="Regular;24" foregroundColor="#8B949E" transparent="1" zPosition="2" />
    </screen>"""

    def __init__(self, session, start_dir="/"):
        Screen.__init__(self, session)
        start = start_dir if start_dir and os.path.isdir(start_dir) else "/media/hdd"
        if not os.path.isdir(start):
            start = "/"
        self["path"] = Label(start)
        self["list"] = self._makeFileList(start)
        self["hint"] = Label("OK فتح  |  GREEN اختيار  |  RED الجذر  |  YELLOW /media  |  EXIT رجوع")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self._ok, "cancel": self.close,
            "green": self._select, "red": lambda: self._go("/"),
            "yellow": lambda: self._go("/media"),
            "up": self._up, "down": self._down,
            "left": self._pageUp, "right": self._pageDown,
        }, -1)

    def _makeFileList(self, d):
        for kwargs in [
            {"showDirectories": True, "showFiles": False, "inhibitMounts": False, "inhibitDirs": False, "isTop": False},
            {"showDirectories": True, "showFiles": False, "inhibitMounts": False, "isTop": False},
            {"showDirectories": True, "showFiles": False},
        ]:
            try:
                return FileList(d, **kwargs)
            except Exception:
                continue
        return FileList(d, showDirectories=True, showFiles=False)

    def _go(self, d):
        if os.path.isdir(d):
            try:
                self["list"] = self._makeFileList(d)
                self["path"].setText(d)
            except Exception:
                pass

    def _ok(self):
        try:
            if self["list"].canDescent():
                self["list"].descent()
                self["path"].setText(self._current())
                return
        except Exception:
            pass
        try:
            sel = self["list"].getFilename()
            if sel and os.path.isdir(sel):
                self._go(sel)
        except Exception:
            pass

    def _current(self):
        try:
            c = self["list"].getCurrentDirectory()
            if c:
                return str(c)
        except Exception:
            pass
        return "/"

    def _select(self):
        d = self._current()
        if d and os.path.isdir(d):
            self.close(d)

    def _up(self):
        try: self["list"].up()
        except Exception: pass
    def _down(self):
        try: self["list"].down()
        except Exception: pass
    def _pageUp(self):
        try: self["list"].pageUp()
        except Exception: pass
    def _pageDown(self):
        try: self["list"].pageDown()
        except Exception: pass


class NovaSubtitleSettings(Screen):
    """Subtitle settings: provider API keys, subtitle dir, auto-attach,
    cache clearing."""

    skin = """
    <screen name="NovaSubtitleSettings" position="center,center" size="1500,860" title="Subtitle Settings" flags="wfNoBorder">
        <eLabel position="0,0" size="1500,860" backgroundColor="#0D1117" zPosition="0" />
        <eLabel position="0,0" size="1500,80" backgroundColor="#161B22" zPosition="1" />
        <widget name="header" position="40,18" size="1420,44" font="Regular;32" foregroundColor="#00E5FF" transparent="1" zPosition="2" />
        <widget name="body" position="40,100" size="1420,660" font="Regular;26" foregroundColor="#F0F6FC" transparent="1" zPosition="2" />
        <eLabel position="0,790" size="1500,70" backgroundColor="#161B22" zPosition="1" />
        <widget name="key_red"    position="30,806"  size="350,32" font="Regular;24" foregroundColor="#FF6B6B" transparent="1" halign="center" zPosition="2" />
        <widget name="key_green"  position="390,806" size="350,32" font="Regular;24" foregroundColor="#39D98A" transparent="1" halign="center" zPosition="2" />
        <widget name="key_yellow" position="750,806" size="350,32" font="Regular;24" foregroundColor="#FFD740" transparent="1" halign="center" zPosition="2" />
        <widget name="key_blue"   position="1110,806" size="360,32" font="Regular;24" foregroundColor="#58A6FF" transparent="1" halign="center" zPosition="2" />
    </screen>"""

    def __init__(self, session):
        Screen.__init__(self, session)
        self.skinName = ["NovaSubtitleSettings"]
        self["header"] = Label("Subtitle Settings — الإعدادات")
        self["body"] = ScrollLabel("")
        self["key_red"] = Label("API Keys")
        self["key_green"] = Label("Subtitle Folder")
        self["key_yellow"] = Label("Auto-Attach: OFF")
        self["key_blue"] = Label("Clear Cache")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "red": self._chooseKey,
            "green": self._editDir,
            "yellow": self._toggleAuto,
            "blue": self._clearCache,
            "ok": self._chooseKey,
            "cancel": self.close,
        }, -1)
        self._refresh()

    @staticmethod
    def _mask(key):
        return (key[:6] + "..." + key[-4:]) if len(key) > 12 else (key or "(not set)")

    def _refresh(self):
        ss = _ss_api_key()
        osk = _os_api_key()
        auto = "ON" if _cfg_bool("auto_subtitle", False) else "OFF"
        self["key_yellow"].setText("Auto-Attach: %s" % auto)
        txt = (
            "SubSource API Key:\n  %s\n\n"
            "OpenSubtitles API Key:\n  %s\n\n"
            "Subtitle Folder:\n  %s\n\n"
            "Auto-Attach on Play: %s\n"
            "  (remembered subtitles + sync are ALWAYS re-attached)\n\n"
            "Free keys: subsource.net / opensubtitles.com\n"
            "Search is Arabic-first, English-second.\n\n"
            "During playback press the BLUE key:\n"
            "  online search / local browse / sync +/-250 ms\n\n"
            "OK or BLUE here = clear subtitle cache + memory\n"
            "EXIT = close"
        ) % (self._mask(ss), self._mask(osk),
             _cfg("subtitle_dir", "/media/hdd/subtitles"), auto)
        self["body"].setText(txt)

    def _chooseKey(self):
        entries = [
            ("SubSource API Key", "subsource"),
            ("OpenSubtitles API Key", "opensubtitles"),
        ]
        self.session.openWithCallback(self._keyProviderChosen, ChoiceBox,
                                      title="API Keys", list=entries)

    def _keyProviderChosen(self, choice):
        if not choice:
            return
        if VirtualKeyBoard is None:
            return
        which = choice[1]
        current = _ss_api_key() if which == "subsource" else _os_api_key()
        self.session.openWithCallback(
            lambda v: self._keyCb(which, v), VirtualKeyBoard,
            title="%s API Key" % choice[0], text=current)

    def _keyCb(self, which, value):
        if value is not None and str(value).strip():
            _cfg_set("subsource_api_key" if which == "subsource" else "opensubtitles_api_key",
                     str(value).strip())
        self._refresh()

    def _editDir(self):
        # Browse instead of typing — VKB path entry was typo-prone on a remote.
        if FileList is not None:
            try:
                self.session.openWithCallback(self._dirCb, NovaFolderBrowser,
                                              _cfg("subtitle_dir", "/media/hdd/subtitles"))
                return
            except Exception:
                pass
        if VirtualKeyBoard is None:
            return
        self.session.openWithCallback(self._dirCb, VirtualKeyBoard,
            title="Subtitle folder path",
            text=_cfg("subtitle_dir", "/media/hdd/subtitles"))

    def _dirCb(self, value=None):
        # Called from BOTH the VirtualKeyBoard (value = typed string,
        # possibly None on cancel) and NovaFolderBrowser (value = selected
        # directory string). Some images also re-fire the callback with
        # no arguments during processDelay — tolerate all shapes.
        try:
            if value is not None:
                d = str(value).strip()
                if d:
                    if os.path.isdir(d):
                        _cfg_set("subtitle_dir", d)
                    else:
                        try:
                            os.makedirs(d)
                            _cfg_set("subtitle_dir", d)
                        except Exception:
                            pass
        except Exception:
            pass
        self._refresh()

    def _toggleAuto(self):
        current = _cfg_bool("auto_subtitle", False)
        _cfg_set("auto_subtitle", "false" if current else "true")
        self._refresh()

    def _clearCache(self):
        """Delete all cached downloaded subtitles + the subtitle memory file,
        reset in-session subtitle state, then close."""
        freed, n = 0, 0
        try:
            cache_dir = _ss_cache_dir()
            for name in os.listdir(cache_dir):
                full = os.path.join(cache_dir, name)
                try:
                    if os.path.isfile(full):
                        freed += os.path.getsize(full)
                        os.remove(full)
                        n += 1
                except Exception:
                    pass
        except Exception:
            pass
        try:
            mem = _subs_memory_path()
            if os.path.exists(mem):
                freed += os.path.getsize(mem)
                os.remove(mem)
                n += 1
        except Exception:
            pass
        # reset in-session subtitle state
        global _CURRENT_SUB
        _CURRENT_SUB = {"item_url": "", "path": "", "offset_ms": 0}
        msg = "Cleared %d file(s)\n%.1f MB freed" % (n, freed / 1024.0 / 1024.0)
        try:
            self.session.openWithCallback(lambda a: self.close(), MessageBox,
                                          msg, MessageBox.TYPE_INFO, timeout=6)
        except Exception:
            self.close()