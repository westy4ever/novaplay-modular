# -*- coding: utf-8 -*-
"""NovaPlay — shared constants and logging.

Leaf module: imports nothing else from the plugin package. Every screen
module imports this first — the sys.path shim below makes sibling
imports (plugin_state, novaplay_*, extractors) work regardless of how
Enigma2 loaded the package."""

import os
import sys

PLUGIN_PATH = os.path.dirname(__file__)
if PLUGIN_PATH not in sys.path:
    sys.path.insert(0, PLUGIN_PATH)

from extractors.base import log as _base_log

_PLUGIN_VERSION = "4.1.0"        # bumped: modular build
_PLUGIN_NAME    = "NovaPlay Media Center"
_TYPE_LABELS    = {"movie": "فيلم", "series": "مسلسل", "episode": "حلقة"}


def my_log(msg):
    _base_log(msg)