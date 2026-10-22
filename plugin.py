# -*- coding: utf-8 -*-
"""
NovaPlay Media Center — Enigma2 plugin entry point.
=========================================
Arabic, YTS & Torrent Streaming/Downloading.

PHASE-1 MODULAR SPLIT: this file registers the plugin and opens the
splash screen only. All screens live in plugin_screen_*.py, the
playback engine in novaplay_proxy / plugin_downloads, the subtitle
system in novaplay_subtitles / novaplay_substudio, shared constants in
plugin_common. Import order matters: the sys.path bootstrap below runs
FIRST, before any of our own flat imports — on Python 3 the shim that
lives inside plugin_common.py can never run, because the import of
plugin_common itself fails without this bootstrap.

Single dispatcher note: callInMainThread comes ONLY from
novaplay_thread everywhere — the old in-plugin CMIT queue is gone.
"""

# ─── Enigma2 / Python 3 import bootstrap ──────────────────────────────
# OpenATV 6.2+/7.x uses Python 3, where flat imports like
# `import plugin_common` / `from extractors import ...` no longer look
# inside this plugin's own folder. Adding our folder to sys.path fixes
# all of them at once. KEEP THIS BLOCK ABOVE ALL OTHER IMPORTS.
import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
if _PLUGIN_DIR and _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)
# ──────────────────────────────────────────────────────────────────────

from Plugins.Plugin import PluginDescriptor

# [perf 2026-10-22] Enigma2 imports every plugin's plugin.py while it boots.
# NovaPlay used to pull in all its screens and every site extractor right
# here (curl_cffi, requests, bs4, ...): slower boot and that memory held all
# day even when NovaPlay is never opened. Everything now loads on first open.
_PLUGIN_NAME = "NOVA Media Player"          # same as plugin_common._PLUGIN_NAME
_LOADED = [False]


def _load():
    """Import the plugin for real (first open only)."""
    if _LOADED[0]:
        return
    import plugin_common                                  # noqa: F401  sys.path shim + constants
    # Restore the browser proxy setting (was done at boot).
    try:
        from extractors.base import set_browser_proxy
        from plugin_state import _get_config
        set_browser_proxy(_get_config("browser_proxy", ""))
    except Exception:
        pass
    _LOADED[0] = True


_REEXPORTS = {
    "NovaPlaySplash": "plugin_screen_splash", "NovaPlayHome": "plugin_screen_home",
    "NovaPlayDetail": "plugin_screen_detail", "NovaPlaySimplePlayer": "plugin_screen_player",
    "_play": "plugin_screen_player", "_build_remote_play_candidates": "plugin_screen_player",
    "NovaPlaySearch": "plugin_screen_search", "NovaPlaySettings": "plugin_screen_settings",
}


def __getattr__(name):
    """Old names (plugin.NovaPlayHome, ...) still work -- loaded on first use."""
    mod = _REEXPORTS.get(name)
    if not mod:
        raise AttributeError(name)
    _load()
    import importlib
    return getattr(importlib.import_module(mod), name)


def main(session, **kwargs):
    # Transmission on demand: a daemon left running (e.g. started at boot)
    # with nothing left to download is closed in the background.
    try:
        import threading
        import novaplay_transmission
        threading.Thread(target=novaplay_transmission.stop_if_idle, daemon=True).start()
    except Exception:
        pass
    # the splash opens at once and loads the rest of NovaPlay while it shows
    from plugin_screen_splash import NovaPlaySplash
    session.open(NovaPlaySplash)


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name=_PLUGIN_NAME,
            description="Arabic, YTS & Torrent Streaming",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon="plugin.png",
            fnc=main,
        ),
        PluginDescriptor(
            name=_PLUGIN_NAME,
            description="Arabic, YTS & Torrent Streaming",
            where=PluginDescriptor.WHERE_EXTENSIONSMENU,
            fnc=main,
        ),
    ]