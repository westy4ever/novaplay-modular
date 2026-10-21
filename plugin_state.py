# -*- coding: utf-8 -*-
"""
NovaPlay - State / persistence
==============================================
Config, favorites, history and saved-playback-position storage.

Adds (this revision):
  * [UX-16] _clear_all_continue() — wipe the whole continue-watching list.
  * [UX-5]  _hide_site() / _unhide_site() / _hidden_sites() /
            _is_site_hidden() — persistent per-site hide for the
            home grid (config key "hidden_sites").
  * [UX-9/15] _entry_from_item() stamps `_ts` (int epoch). _save_position()
            keeps `_ts` in sync with `_pos_ts`.
  * [UX-24] _upsert_library_item() preserves `_ts` when merging.
"""

import os
import re
import json
import time
import threading

from extractors.base import log as _log

_PLUGIN_OWNER = "NovaPlay Team"
_DEFAULT_TMDB_API_KEY = "46b050dc88e3c52e9d1bca4b656036e4"

PLUGIN_PATH = os.path.dirname(__file__)

_STATE_CACHE = None
_STATE_LOCK = threading.RLock()

_POS_DISK_MIN_DELTA = 60
_POS_DISK_LAST = {"url": "", "sec": 0}

_KEYS_FILE = os.path.join(os.path.dirname(__file__), "api_keys.conf")
_KEY_NAMES = ("tmdb_api_key", "subsource_api_key", "opensubtitles_api_key",
              "opensubtitles_user", "opensubtitles_pass")


_STATE_FILE = "novaplay_state.json"

# ─── one-time migration from the old "Advanced Arabic Player" name ────────
# The plugin used to live in Extensions/AdvancedArabicPlayer and keep its
# settings in advanced_arabic_player_state.json and its caches under
# /media/hdd/AdvancedArabicPlayer. After the rename to NovaPlay, carry all of
# that over ONCE, so settings, favourites, history, watch positions, API keys
# and the poster cache survive. Old files are copied, never deleted, so going
# back to the old version still works. Safe to run on every start: each step
# only acts when the new file is missing and the old one exists.
_LEGACY_STATE_FILE = "advanced_arabic_player_state.json"
_LEGACY_PLUGIN_DIR = "/usr/lib/enigma2/python/Plugins/Extensions/AdvancedArabicPlayer"
_MIGRATED = [False]


def _migrate_legacy_files():
    if _MIGRATED[0]:
        return
    _MIGRATED[0] = True
    import shutil
    # settings / favourites / history / positions
    for old, new in (
            ("/etc/enigma2/" + _LEGACY_STATE_FILE, "/etc/enigma2/" + _STATE_FILE),
            (os.path.join(_LEGACY_PLUGIN_DIR, _LEGACY_STATE_FILE), "/etc/enigma2/" + _STATE_FILE),
            (os.path.join(PLUGIN_PATH, _LEGACY_STATE_FILE), os.path.join(PLUGIN_PATH, _STATE_FILE)),
            ("/tmp/" + _LEGACY_STATE_FILE, "/tmp/" + _STATE_FILE)):
        try:
            if os.path.isfile(old) and not os.path.exists(new) and os.access(os.path.dirname(new), os.W_OK):
                shutil.copy2(old, new)
                if os.path.isfile(old + ".bak") and not os.path.exists(new + ".bak"):
                    shutil.copy2(old + ".bak", new + ".bak")
                _log("NovaPlay: migrated settings {} -> {}".format(old, new))
        except Exception as e:
            _log("NovaPlay: settings migration skipped ({}): {}".format(old, e))
    # API keys file lived inside the old plugin folder
    try:
        old_keys = os.path.join(_LEGACY_PLUGIN_DIR, "api_keys.conf")
        new_keys = os.path.join(PLUGIN_PATH, "api_keys.conf")
        if (os.path.realpath(_LEGACY_PLUGIN_DIR) != os.path.realpath(PLUGIN_PATH)
                and os.path.isfile(old_keys) and not os.path.exists(new_keys)):
            shutil.copy2(old_keys, new_keys)
            os.chmod(new_keys, 0o600)
            _log("NovaPlay: migrated api_keys.conf from the old plugin folder")
    except Exception as e:
        _log("NovaPlay: api_keys.conf migration skipped: {}".format(e))
    # poster / TMDB / trailer cache: move entries the new tree doesn't have yet
    for old_root, new_root in (("/media/hdd/AdvancedArabicPlayer", "/media/hdd/NovaPlay"),
                               ("/tmp/AdvancedArabicPlayer", "/tmp/NovaPlay")):
        try:
            if not os.path.isdir(old_root):
                continue
            if not os.path.exists(new_root):
                os.rename(old_root, new_root)
                _log("NovaPlay: moved cache {} -> {}".format(old_root, new_root))
                continue
            for dirpath, dirnames, filenames in os.walk(old_root):
                rel = os.path.relpath(dirpath, old_root)
                dest_dir = os.path.normpath(os.path.join(new_root, rel))
                if not os.path.isdir(dest_dir):
                    os.makedirs(dest_dir)
                for fn in filenames:
                    dest = os.path.join(dest_dir, fn)
                    if not os.path.exists(dest):
                        os.rename(os.path.join(dirpath, fn), dest)
            _log("NovaPlay: merged cache {} into {}".format(old_root, new_root))
        except Exception as e:
            _log("NovaPlay: cache migration skipped ({}): {}".format(old_root, e))


def _state_path():
    _migrate_legacy_files()
    for candidate in ("/etc/enigma2/" + _STATE_FILE,
                      os.path.join(PLUGIN_PATH, _STATE_FILE),
                      "/tmp/" + _STATE_FILE):
        try:
            parent = os.path.dirname(candidate)
            if parent and os.path.isdir(parent) and os.access(parent, os.W_OK):
                return candidate
        except Exception:
            pass
    return "/tmp/" + _STATE_FILE


def _default_state():
    return {
        "config": {
            "owner": _PLUGIN_OWNER,
            "tmdb_api_key": _DEFAULT_TMDB_API_KEY,
            "browser_proxy": "",
            "torrserver_url": "http://127.0.0.1:8090",
        },
        "favorites": [],
        "history": [],
        "search_history": [],
        "hidden_sites": [],
    }


def _load_api_keys_file(state):
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
            try:
                with open(path + ".bak", "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    state.update(loaded)
                    state["config"] = dict(_default_state()["config"], **(loaded.get("config") or {}))
                    _log("State recovered from .bak")
            except Exception:
                pass
        state.setdefault("hidden_sites", [])
        # the owner label saved by the old name
        if (state.get("config") or {}).get("owner") == "ArabicPlayer Team":
            state["config"]["owner"] = _PLUGIN_OWNER
        _STATE_CACHE = state
        _load_api_keys_file(state)
        return _STATE_CACHE


def _save_state(state=None):
    global _STATE_CACHE
    with _STATE_LOCK:
        _STATE_CACHE = state or _STATE_CACHE or _default_state()
        path = _state_path()
        tmp = path + ".tmp"
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
            try:
                os.remove(tmp)
            except Exception:
                pass


def _get_config(key, default=""):
    value = (_load_state().get("config") or {}).get(key, default)
    if key == "tmdb_api_key" and not value:
        return _DEFAULT_TMDB_API_KEY
    if key == "owner" and not value:
        return _PLUGIN_OWNER
    return value


def _write_api_key_to_file(key, value):
    try:
        value = str(value or "").replace("\r", "").replace("\n", "").strip()
        lines = []
        if os.path.exists(_KEYS_FILE):
            with open(_KEYS_FILE, "r") as f:
                lines = f.read().splitlines()
        new_line = "{}={}".format(key, value)
        out, done = [], False
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key:
                if not done:
                    out.append(new_line)
                    done = True
                continue
            out.append(line)
        if not done:
            out.append(new_line)
        tmp = _KEYS_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(out) + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.rename(tmp, _KEYS_FILE)
        return True
    except Exception as e:
        _log("api_keys.conf write error: {}".format(e))
        return False


def _set_config(key, value):
    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("config", {})[key] = value
        if key in _KEY_NAMES:
            _write_api_key_to_file(key, value)
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
        "_ts": int(time.time()),
    }
    if extra:
        entry.update(extra)
    return entry


def _upsert_library_item(bucket, entry, limit=100):
    with _STATE_LOCK:
        state = _load_state()
        items = state.setdefault(bucket, [])
        key = entry.get("url")
        if not entry.get("last_position_sec"):
            for _old in items:
                if _old.get("url") == key and _old.get("last_position_sec"):
                    entry["last_position_sec"] = _old["last_position_sec"]
                    entry["_pos_ts"] = _old.get("_pos_ts") or 0
                    if not entry.get("_ts"):
                        entry["_ts"] = _old.get("_ts") or 0
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
    seconds = int(seconds or 0)
    if 0 < seconds < 30:
        _log("_save_position: skipping {}s (< 30s threshold)".format(seconds))
        return
    with _STATE_LOCK:
        state = _load_state()
        for item in (state.get("history") or []):
            if item.get("url") == url:
                if item.get("last_position_sec") == seconds:
                    return
                old_pos = int(item.get("last_position_sec") or 0)
                item["last_position_sec"] = seconds
                _now = int(time.time())
                item["_pos_ts"] = _now
                item["_ts"] = _now
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


def _clear_continue_item(url):
    if not url:
        return
    with _STATE_LOCK:
        state = _load_state()
        for item in (state.get("history") or []):
            if item.get("url") == url:
                item["last_position_sec"] = 0
                item["_pos_ts"] = 0
                _save_state(state)
                return


def _clear_all_continue():
    with _STATE_LOCK:
        state = _load_state()
        count = 0
        for item in (state.get("history") or []):
            if int(item.get("last_position_sec") or 0) > 0:
                item["last_position_sec"] = 0
                item["_pos_ts"] = 0
                count += 1
        if count:
            _save_state(state)
        return count


def _hidden_sites():
    return list(_load_state().get("hidden_sites") or [])


def _is_site_hidden(site_key):
    if not site_key:
        return False
    return site_key in (_load_state().get("hidden_sites") or [])


def _hide_site(site_key):
    if not site_key:
        return False
    with _STATE_LOCK:
        state = _load_state()
        rows = state.setdefault("hidden_sites", [])
        if site_key in rows:
            return False
        rows.append(site_key)
        _save_state(state)
        return True


def _unhide_site(site_key):
    if not site_key:
        return False
    with _STATE_LOCK:
        state = _load_state()
        rows = state.get("hidden_sites") or []
        if site_key not in rows:
            return False
        state["hidden_sites"] = [s for s in rows if s != site_key]
        _save_state(state)
        return True


# [UX-rename] Per-site custom display name, user-editable via long-press
# ("Rename") on a home-grid tile or a family sub-menu row. Persisted under
# config key "site_custom_names" (site_key -> name). An empty/missing
# entry means "use the registry's default title" -- _site_label() in
# plugin_util.py checks this first and falls back to the registry title.

def _get_site_custom_name(site_key):
    if not site_key:
        return ""
    names = _load_state().get("site_custom_names") or {}
    return str(names.get(site_key) or "").strip()


def _set_site_custom_name(site_key, name):
    """Save a custom display name for site_key. An empty/whitespace-only
    name clears the override (reverts to the registry default)."""
    if not site_key:
        return False
    name = str(name or "").strip()
    with _STATE_LOCK:
        state = _load_state()
        names = state.setdefault("site_custom_names", {})
        if name:
            names[site_key] = name
        else:
            names.pop(site_key, None)
        _save_state(state)
        return True


# [UX-reorder] Per-family row order for the home-grid sub-menu picker,
# user-editable via long-press ("Move up" / "Move down") on a row.
# Persisted under config key "family_order" (representative_key ->
# ordered list of member keys). A family with no saved order uses the
# registry's own _SITE_FAMILIES order.

def _get_family_order(rep_key):
    """Return the saved custom order for rep_key's family, or None if no
    custom order has been saved (caller should fall back to the
    registry default order)."""
    if not rep_key:
        return None
    orders = _load_state().get("family_order") or {}
    order = orders.get(rep_key)
    if isinstance(order, list) and order:
        return list(order)
    return None


def _set_family_order(rep_key, ordered_members):
    if not rep_key or not ordered_members:
        return False
    with _STATE_LOCK:
        state = _load_state()
        orders = state.setdefault("family_order", {})
        orders[rep_key] = list(ordered_members)
        _save_state(state)
        return True


def _save_search_query(query):
    q = re.sub(r"\s+", " ", (query or "")).strip()
    if len(q) < 2:
        return
    with _STATE_LOCK:
        state = _load_state()
        rows = state.setdefault("search_history", [])
        ql = q.lower()
        rows = [r for r in rows if str(r).lower() != ql]
        rows.insert(0, q)
        state["search_history"] = rows[:20]
        _save_state(state)


def _clear_search_history():
    with _STATE_LOCK:
        state = _load_state()
        state["search_history"] = []
        _save_state(state)


def _remove_search_query(query):
    ql = re.sub(r"\s+", " ", (query or "")).strip().lower()
    with _STATE_LOCK:
        state = _load_state()
        state["search_history"] = [r for r in (state.get("search_history") or []) if str(r).strip().lower() != ql]
        _save_state(state)


def _get_recent_searches(limit=10):
    rows = _load_state().get("search_history") or []
    return [str(r) for r in rows[:limit] if r]


def _library_search_suggestions(query="", current_site="", limit=8):
    from plugin_util import _normalize_query
    q = _normalize_query(query)
    rows = []
    seen = set()

    for r in _get_recent_searches(10):
        nr = _normalize_query(r)
        if not nr or nr in seen:
            continue
        if q:
            if nr == q:               score = 0
            elif nr.startswith(q):    score = 1
            elif q in nr:             score = 2
            else:                     continue
        else:
            score = 3
        seen.add(nr)
        rows.append((score, -1, -int(time.time()), {
            "title": r, "query": r,
            "source": "Previous search", "site": "", "kind": "", "year": "",
        }))

    for source_name, items, source_rank in (
        ("Favorites", _favorite_items(), 0),
        ("History", _history_items(), 1),
    ):
        for item in items or []:
            title = re.sub(r"\s+", " ", item.get("title", "") or "").strip()
            if not title:
                continue
            norm = _normalize_query(title)
            if not norm or norm in seen:
                continue
            if q:
                if norm == q:              score = 0
                elif norm.startswith(q):   score = 1
                elif q in norm:            score = 2
                else:                      continue
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
                    "kind": {"movie": "Movie", "series": "Series", "episode": "Episode"}.get(item.get("type", ""), ""),
                    "year": item.get("year", ""),
                }
            ))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows[:limit]]


def _continue_items(limit=7):
    rows = []
    for item in (_load_state().get("history") or []):
        pos = int(item.get("last_position_sec") or 0)
        if pos <= 30 or not item.get("url"):
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