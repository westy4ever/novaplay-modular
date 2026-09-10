# -*- coding: utf-8 -*-
"""NovaPlay - per-site health tracking.

States: "ok" | "down" | "blocked" | "unknown".
Surfaced as colored dots on the home site grid and a Cloudflare warning
in the home status line. record() is called from background threads;
persistence goes through plugin_state._set_config and only fires on
state TRANSITIONS, so healthy sites cost no extra flash writes.
"""

import time
import threading

STATES = ("ok", "down", "blocked", "unknown")
STALE_AFTER = 6 * 3600

_lock = threading.RLock()
_health = {}                  # site_key -> {"state", "ts", "detail"}
_loaded = False


def _ensure_loaded():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from plugin_state import _get_config
        stored = _get_config("site_health", {})
        if isinstance(stored, dict):
            now = time.time()
            for key, state in stored.items():
                if state in STATES:
                    _health[str(key)] = {"state": state, "ts": now, "detail": ""}
    except Exception:
        pass


def _persist():
    try:
        from plugin_state import _set_config
        _set_config("site_health", dict((k, v["state"]) for k, v in _health.items()))
    except Exception:
        pass


def record(site, state, detail=""):
    if state not in STATES:
        return
    site = str(site or "")
    if not site:
        return
    with _lock:
        _ensure_loaded()
        row = _health.get(site)
        if row and row.get("state") == state:
            row["ts"] = time.time()          # refresh only, no disk write
            return
        _health[site] = {"state": state, "ts": time.time(), "detail": str(detail)[:120]}
        _persist()


def get(site):
    with _lock:
        _ensure_loaded()
        row = _health.get(str(site or ""))
    if not row:
        return "unknown"
    if time.time() - row.get("ts", 0) > STALE_AFTER:
        return "unknown"
    return row.get("state") or "unknown"


def blocked_sites():
    with _lock:
        _ensure_loaded()
        return [k for k, v in _health.items() if v.get("state") == "blocked"]