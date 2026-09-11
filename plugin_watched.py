# -*- coding: utf-8 -*-
"""plugin_watched.py — watched / in-progress badges for cards (v2).

The player clears the saved position at EOF, so a missing position is
ambiguous (never started vs. finished). This module keeps a small JSON
list of watched URLs in the plugin config (one key, capped at 500):

    ✓  finished (marked at natural EOF by the player)
    ◐  in progress = the home screen's existing "متابعة" resume bar
    ''  nothing

v2: 2s TTL cache — a grid repaint calls is_watched ~24 times; without
the cache that's 24 config reads + json.loads per paint. mark/unmark
invalidate the cache immediately."""

import json
import time

_MAX = 500
_KEY = "watched_urls"
_TTL = 2.0
_CACHE = {"t": 0.0, "v": set()}


def _load():
    now = time.time()
    if (now - _CACHE["t"]) < _TTL:
        return _CACHE["v"]
    try:
        from plugin_state import _get_config
        lst = json.loads(_get_config(_KEY, "[]") or "[]")
    except Exception:
        lst = []
    _CACHE["v"] = set(lst) if isinstance(lst, list) else set()
    _CACHE["t"] = now
    return _CACHE["v"]


def _save(lst):
    try:
        from plugin_state import _set_config
        _set_config(_KEY, json.dumps(list(lst)[-_MAX:]))
    except Exception:
        pass
    _CACHE["t"] = 0.0            # invalidate


def mark_watched(url):
    url = (url or "").strip()
    if not url:
        return
    if url in _load():
        return
    try:
        from plugin_state import _get_config
        lst = json.loads(_get_config(_KEY, "[]") or "[]")
    except Exception:
        lst = []
    if url not in lst:
        lst.append(url)
        _save(lst)


def unmark_watched(url):
    url = (url or "").strip()
    lst = [u for u in _load() if u != url]
    _save(lst)


def is_watched(url):
    return (url or "").strip() in _load()


def badge_for(url):
    """'✓' finished, '◐' in progress, '' otherwise."""
    url = (url or "").strip()
    if not url:
        return u""
    if is_watched(url):
        return u"✓"
    try:
        from plugin_state import _get_saved_position
        if _get_saved_position(url) > 60:
            return u"◐"
    except Exception:
        pass
    return u""