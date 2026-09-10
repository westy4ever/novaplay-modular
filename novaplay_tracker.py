# -*- coding: utf-8 -*-
"""NovaPlay — live playback position tracker.

Extracted from plugin.py (Phase-1/Phase-3): the module-level globals
moved here. The PLAYER mutates them via module attributes
(novaplay_tracker._GLOBAL_...); read-only consumers (Detail,
__currentPlaySecs) use current_play_secs(). Mutating via bare `global`
statements from other modules was the correctness-risk the old
plugin_state docstring warned about — this is the fix.
"""

import threading
import time

from enigma import eTimer

from plugin_state import _save_position
from plugin_common import my_log

_GLOBAL_POS_LOCK = threading.Lock()
_GLOBAL_POS_TIMER = None
_GLOBAL_POS_SESSION = None
_GLOBAL_POS_ITEM = ""
_GLOBAL_PLAY_START_WALL = 0.0
_GLOBAL_PLAY_START_POS = 0
_GLOBAL_LAST_SEEK_TARGET = -1
_GLOBAL_IS_PAUSED = False


def current_play_secs():
    """Read-only position estimate. Pause-aware, seek-aware (the player
    keeps _GLOBAL_PLAY_START_* coherent through pauses and seeks)."""
    with _GLOBAL_POS_LOCK:
        wall = _GLOBAL_PLAY_START_WALL
        base = _GLOBAL_PLAY_START_POS
        paused = _GLOBAL_IS_PAUSED
    if paused:
        return int(max(0, base))
    if wall:
        return int(max(0, (time.time() - wall) + base))
    return 0


def _global_pos_tick():
    global _GLOBAL_POS_ITEM, _GLOBAL_PLAY_START_WALL, _GLOBAL_PLAY_START_POS
    with _GLOBAL_POS_LOCK:
        if not _GLOBAL_POS_ITEM or not _GLOBAL_PLAY_START_WALL:
            return
        try:
            if _GLOBAL_IS_PAUSED:
                secs = int(_GLOBAL_PLAY_START_POS)
            else:
                elapsed = time.time() - _GLOBAL_PLAY_START_WALL
                secs    = int(_GLOBAL_PLAY_START_POS + elapsed)
            if secs < 5:
                my_log("Pos tracker: skipping suspicious pos {}s".format(secs))
                return
            _save_position(_GLOBAL_POS_ITEM, secs)
            my_log("Pos tracker saved: {}s for {}".format(secs, _GLOBAL_POS_ITEM[:50]))
        except Exception as e:
            my_log("Pos tracker error: {}".format(e))


def start_pos_tracker(session, item_url, start_pos=0):
    global _GLOBAL_POS_TIMER, _GLOBAL_POS_SESSION, _GLOBAL_POS_ITEM
    global _GLOBAL_PLAY_START_WALL, _GLOBAL_PLAY_START_POS
    global _GLOBAL_LAST_SEEK_TARGET, _GLOBAL_IS_PAUSED
    with _GLOBAL_POS_LOCK:
        _GLOBAL_LAST_SEEK_TARGET = -1
        _GLOBAL_IS_PAUSED       = False
        _GLOBAL_POS_SESSION     = session
        _GLOBAL_POS_ITEM        = item_url or ""
        _GLOBAL_PLAY_START_WALL = time.time()
        _GLOBAL_PLAY_START_POS  = int(start_pos or 0)
        if _GLOBAL_POS_TIMER is None:
            _GLOBAL_POS_TIMER = eTimer()
            _GLOBAL_POS_TIMER.callback.append(_global_pos_tick)
        try:
            _GLOBAL_POS_TIMER.stop()
        except Exception:
            pass
        if _GLOBAL_POS_ITEM:
            _GLOBAL_POS_TIMER.start(20000, False)
            my_log("Pos tracker started (wall-clock base={}s): {}".format(
                _GLOBAL_PLAY_START_POS, item_url[:50]))


def stop_pos_tracker():
    """Stops the tracker with the final force-flush (the old Edit-7
    behavior): position is written to disk immediately, so recency in
    the Continue row survives even a hard crash right after exit."""
    global _GLOBAL_POS_ITEM, _GLOBAL_IS_PAUSED
    with _GLOBAL_POS_LOCK:
        try:
            if _GLOBAL_POS_ITEM and _GLOBAL_PLAY_START_WALL:
                if _GLOBAL_IS_PAUSED:
                    _secs = int(_GLOBAL_PLAY_START_POS)
                else:
                    _secs = int(_GLOBAL_PLAY_START_POS + (time.time() - _GLOBAL_PLAY_START_WALL))
                if _secs >= 5:
                    _save_position(_GLOBAL_POS_ITEM, _secs, force=True)
        except Exception:
            pass
        _GLOBAL_POS_ITEM = ""
        _GLOBAL_IS_PAUSED = False
        try:
            if _GLOBAL_POS_TIMER:
                _GLOBAL_POS_TIMER.stop()
        except Exception:
            pass