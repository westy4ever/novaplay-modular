# -*- coding: utf-8 -*-
"""
Advanced Arabic Player - State / persistence
==============================================
Config, favorites, history and saved-playback-position storage.

Changes in this revision (PATCH 50 — settings persistence fix):
  * _KEY_NAMES trimmed to the three real API keys. browser_proxy and
    torrserver_url were members, which made _save_state() STRIP them
    from the state file on every write — a proxy or TorrServer URL set
    through the Settings screen survived only until restart (the
    "Browser proxy set to: DISABLED" fingerprint in every boot log).
    They are connection settings, not credentials owned by api_keys.conf.

Changes in the previous revision (Continue-Watching support):
  * NEW _continue_items(limit): the Continue-Watching row query for
    the home screen. Returns history entries that have a resumable
    position (> 60s), sorted by _pos_ts (watch recency) with
    _saved_at as tiebreaker, deduped by url. Read by
    AdvancedArabicPlayerHome._paintContinueRow in plugin.py.
  * _save_position() now stamps item["_pos_ts"] = now on every
    in-memory update, so the continue row sorts by when you last
    *progressed* in a title, not when the entry was created. The
    stamp rides along with the existing throttled disk write —
    zero extra flash writes.
  * _upsert_library_item() preserves _pos_ts (the way it already
    preserves last_position_sec), so re-playing an item doesn't
    reset its recency ordering.
  * (plugin_health.py stores per-site health under config key
    "site_health" via _get_config/_set_config — no code needed here,
    noted for discoverability.)

Changes in the previous revision (kept for reference):
  * _save_position() no longer rewrites the state file on every 20s
    position tick. Paused playback now writes NOTHING (it used to
    fsync the identical state every 20s — ~1,400 pointless flash
    writes overnight); active playback writes at most once per
    _POS_DISK_MIN_DELTA (60s) of NEW progress; backward seeks (>30s)
    write immediately; `force=True` flushes on demand (see
    _stop_pos_tracker in plugin.py). The in-memory value that
    _get_saved_position() reads stays current on every tick.
  * import re moved to module level (was imported inside the per-item
    loop of _library_search_suggestions).
  * The state MUTATORS (_set_config / _upsert_library_item /
    _toggle_favorite_entry / _save_position) now run under an RLock,
    so a future background-thread caller can't interleave its dict
    mutation with a concurrent _save_state() json.dump ("dictionary
    changed size during iteration"). Readers stay unsynchronized —
    all current read sites are on the main thread.

NOTE: This does NOT include the live in-memory position tracker
(_GLOBAL_POS_TIMER / _global_pos_tick / _start_pos_tracker / _stop_pos_tracker)
or the local proxy hit counters. Those live in novaplay_tracker.py.
"""

import os
import re
import json
import time
import threading

from extractors.base import log as _log

_PLUGIN_OWNER = "ArabicPlayer Team"
_DEFAULT_TMDB_API_KEY = "46b050dc88e3c52e9d1bca4b656036e4"

PLUGIN_PATH = os.path.dirname(__file__)

_STATE_CACHE = None
# RLock (not Lock): the mutators below wrap _load_state()/_save_state(),
# which each acquire the same lock — a plain Lock would self-deadlock.
_STATE_LOCK = threading.RLock()

# ─── position disk-write throttling ──────────────────────────────────────
_POS_DISK_MIN_DELTA = 60  # seconds of NEW progress before the file is rewritten
_POS_DISK_LAST = {"url": "", "sec": 0}

# ─── api_keys.conf support ──────────────────────────────────────────────
_KEYS_FILE = os.path.join(os.path.dirname(__file__), "api_keys.conf")
# [PATCH 50] browser_proxy and torrserver_url REMOVED from this tuple:
# as members, _save_state() stripped them from the state file on every
# write, so a proxy or TorrServer URL set in the Settings screen was
# lost on restart. They are connection settings, not credentials owned
# by api_keys.conf. The three remaining keys ARE credentials and stay
# file-owned.
_KEY_NAMES = ("tmdb_api_key", "subsource_api_key", "opensubtitles_api_key")


def _state_path():
    for candidate in ("/etc/enigma2/advanced_arabic_player_state.json", os.path.join(PLUGIN_PATH, "advanced_arabic_player_state.json"), "/tmp/advanced_arabic_player_state.json"):
        try:
            parent = os.path.dirname(candidate)
            if parent and os.path.isdir(parent) and os.access(parent, os.W_OK):
                return candidate
        except Exception:
            pass
    return "/tmp/advanced_arabic_player_state.json"


def _default_state():
    return {
        "config": {
            "owner": _PLUGIN_OWNER,
            "tmdb_api_key": _DEFAULT_TMDB_API_KEY,
            "browser_proxy": "",   # external proxy URL
            "torrserver_url": "http://127.0.0.1:8090", # TorrServer URL
        },
        "favorites": [],
        "history": [],
    }


def _load_api_keys_file(state):
    """Optional api_keys.conf next to the plugin: KEY=value lines.
    Values here WIN over state (edit the file, restart, done) and are
    never written back — the file is the owner of these keys."""
    try:
        if not os.path.exists(_KEYS_FILE):
            return
        with open(_KEYS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in _KEY_NAMES and v:
                    state.setdefault("config", {})[k] = v
    except Exception as e:
        _log("api_keys.conf read error: {}".format(e))


def _load_state():
    global _STATE_CACHE
    with _STATE_LOCK:
        if _STATE_CACHE is not None:
            return _STATE_CACHE
        state = _default_state()
        path = _state_path()
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    state.update(loaded)
                    state["config"] = dict(_default_state()["config"], **(loaded.get("config") or {}))
        except Exception as e:
            _log("State load error: {}".format(e))
            # Last-good twin: a corrupt main file no longer resets
            # favorites/history to defaults.
            try:
                with open(path + ".bak", "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    state.update(loaded)
                    state["config"] = dict(_default_state()["config"], **(loaded.get("config") or {}))
                    _log("State recovered from .bak")
            except Exception:
                pass
        _STATE_CACHE = state
        _load_api_keys_file(state)
        return _STATE_CACHE


def _save_state(state=None):
    global _STATE_CACHE
    with _STATE_LOCK:
        _STATE_CACHE = state or _STATE_CACHE or _default_state()
        path = _state_path()
        tmp  = path + ".tmp"
        # api_keys.conf owns these keys: strip them from the state copy
        # so a state restore/backup never fights the file.
        _out = dict(_STATE_CACHE)
        _cfg = dict(_out.get("config") or {})
        for _k in _KEY_NAMES:
            _cfg.pop(_k, None)
        _out["config"] = _cfg
        try:
            with open(tmp, "w") as f:
                json.dump(_out, f)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, path)
            try:
                import shutil
                shutil.copy2(path, path + ".bak")
            except Exception:
                pass
        except Exception as e:
            _log("State save error: {}".format(e))
            try: os.remove(tmp)
            except Exception: pass


def _get_config(key, default=""):
    value = (_load_state().get("config") or {}).get(key, default)
    if key == "tmdb_api_key" and not value:
        return _DEFAULT_TMDB_API_KEY
    if key == "owner" and not value:
        return _PLUGIN_OWNER
    return value


def _set_config(key, value):
    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("config", {})[key] = value
        _save_state(state)


def _entry_from_item(item, site, m_type, extra=None):
    entry = {
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "poster": item.get("poster") or item.get("image") or "",
        "plot": item.get("plot", ""),
        "year": item.get("year", ""),
        "rating": item.get("rating", ""),
        "type": item.get("type", "") or m_type,
        "_action": item.get("_action", "details"),
        "_site": item.get("_site", site),
        "_m_type": item.get("_m_type", m_type),
        "_saved_at": int(time.time()),
    }
    if extra:
        entry.update(extra)
    return entry


def _upsert_library_item(bucket, entry, limit=100):
    with _STATE_LOCK:
        state = _load_state()
        items = state.setdefault(bucket, [])
        key   = entry.get("url")
        if not entry.get("last_position_sec"):
            for _old in items:
                if _old.get("url") == key and _old.get("last_position_sec"):
                    entry["last_position_sec"] = _old["last_position_sec"]
                    # Preserve the watch-recency stamp too, so re-playing
                    # an item keeps (rather than resets) its Continue-row
                    # ordering.
                    entry["_pos_ts"] = _old.get("_pos_ts") or 0
                    break
        items = [i for i in items if i.get("url") != key]
        items.insert(0, entry)
        state[bucket] = items[:limit]
        _save_state(state)


def _toggle_favorite_entry(entry):
    with _STATE_LOCK:
        state = _load_state()
        favorites = state.setdefault("favorites", [])
        key = entry.get("url")
        for idx, item in enumerate(favorites):
            if item.get("url") == key:
                favorites.pop(idx)
                _save_state(state)
                return False
        favorites.insert(0, entry)
        state["favorites"] = favorites[:100]
        _save_state(state)
        return True


def _is_favorite(url):
    return any(item.get("url") == url for item in (_load_state().get("favorites") or []))


def _history_items():
    return _load_state().get("history") or []


def _favorite_items():
    return _load_state().get("favorites") or []


def _get_saved_position(url):
    for item in (_load_state().get("history") or []):
        if item.get("url") == url:
            pos = int(item.get("last_position_sec") or 0)
            return pos if pos > 30 else 0
    return 0


def _save_position(url, seconds, force=False):
    """Record playback position.

    Memory is updated on every call (so _get_saved_position is always
    current); the on-disk state file is rewritten only when:
      - forced (force=True — playback stop / plugin exit), or
      - >= _POS_DISK_MIN_DELTA seconds of NEW progress, or
      - the position JUMPED BACKWARD by > 30s (a seek — persist intent)
    Paused playback (same seconds as stored) writes nothing at all.

    Every in-memory update also stamps item["_pos_ts"] (watch
    recency for the Continue-Watching row) — it reaches disk with
    the next throttled write, at no extra cost.
    """
    seconds = int(seconds or 0)
    if 0 < seconds < 30:
        _log("_save_position: skipping {}s (< 30s threshold)".format(seconds))
        return
    with _STATE_LOCK:
        state = _load_state()
        for item in (state.get("history") or []):
            if item.get("url") == url:
                if item.get("last_position_sec") == seconds:
                    return  # paused / no progress — memory matches, skip disk write
                old_pos = int(item.get("last_position_sec") or 0)
                item["last_position_sec"] = seconds
                item["_pos_ts"] = int(time.time())
                last = _POS_DISK_LAST
                if last.get("url") != url:
                    last["url"] = url
                    last["sec"] = old_pos
                if force or \
                        (seconds < last.get("sec", 0) - 30) or \
                        (seconds - last.get("sec", 0) >= _POS_DISK_MIN_DELTA):
                    last["sec"] = seconds
                    _save_state(state)
                return


def _library_search_suggestions(query="", current_site="", limit=8):
    from plugin_util import _normalize_query
    q = _normalize_query(query)
    rows = []
    seen = set()
    for source_name, items, source_rank in (
        ("المفضلة", _favorite_items(), 0),
        ("السجل", _history_items(), 1),
    ):
        for item in items or []:
            title = re.sub(r"\s+", " ", item.get("title", "") or "").strip()
            if not title:
                continue
            norm = _normalize_query(title)
            if not norm or norm in seen:
                continue
            if q:
                if norm == q:
                    score = 0
                elif norm.startswith(q):
                    score = 1
                elif q in norm:
                    score = 2
                else:
                    continue
            else:
                score = 5
            if current_site and item.get("_site") == current_site:
                score -= 1
            seen.add(norm)
            rows.append((
                score,
                source_rank,
                -int(item.get("_saved_at") or 0),
                {
                    "title": title,
                    "query": title,
                    "source": source_name,
                    "site": item.get("_site", ""),
                    "kind": {"movie": "فيلم", "series": "مسلسل", "episode": "حلقة"}.get(item.get("type", ""), ""),
                    "year": item.get("year", ""),
                }
            ))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows[:limit]]


def _continue_items(limit=7):
    """Continue-Watching row: most-recently-watched history entries
    that still have a resumable position.

    Filter: position > 60s (anything shorter is a false start, and
    EOF playback zeroes the position so finished titles drop out).
    Sort: _pos_ts (last position progress) descending, _saved_at as
    tiebreaker for pre-upgrade entries that lack _pos_ts.
    Dedupe: by url — history can hold at most one entry per url, but
    the guard costs nothing and keeps the row stable if that ever
    changes.

    Reads the in-memory state, so the row updates immediately after
    playback exits (no disk round-trip needed). Call sites are on the
    main thread, consistent with the other unsynchronized readers.
    """
    rows = []
    for item in (_load_state().get("history") or []):
        pos = int(item.get("last_position_sec") or 0)
        if pos <= 60 or not item.get("url"):
            continue
        if not (item.get("poster") or item.get("title")):
            continue
        rows.append(item)
    rows.sort(key=lambda i: (int(i.get("_pos_ts") or 0), int(i.get("_saved_at") or 0)), reverse=True)
    seen, out = set(), []
    for item in rows:
        key = item.get("url")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out