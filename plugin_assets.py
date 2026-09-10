# -*- coding: utf-8 -*-
"""NovaPlay - static asset resolution + poster placeholder art.

Every missing-artwork code path routes through placeholder_for_item()
so a tile is never blank while artwork downloads (or when a source
has no artwork at all).
"""

import os

PLUGIN_PATH = os.path.dirname(__file__)
ASSET_DIR = os.path.join(PLUGIN_PATH, "images")

_PLACEHOLDER_BY_TYPE = {
    "movie": "placeholder_movie.png",
    "series": "placeholder_series.png",
    "episode": "placeholder_series.png",
}

_warned = set()


def resolve(name):
    """Absolute path of images/<name>, or None. Warns once per missing
    name so a typo'd reference doesn't spam the log every repaint."""
    path = os.path.join(ASSET_DIR, name)
    if os.path.isfile(path):
        return path
    if name not in _warned:
        _warned.add(name)
        try:
            from extractors.base import log
            log("plugin_assets: missing image '{}'".format(name))
        except Exception:
            pass
    return None


def placeholder_for_item(item):
    """Placeholder path for a list item, or None if the tile should stay
    text-only (next/prev-page cards)."""
    item = item or {}
    if item.get("_is_next_page") or item.get("_is_prev_page"):
        return None
    if str(item.get("_action", "")).startswith("site_"):
        return resolve("default.png")
    path = resolve(_PLACEHOLDER_BY_TYPE.get(item.get("type") or "movie", ""))
    return path or resolve("placeholder_generic.png")