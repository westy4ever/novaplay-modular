# -*- coding: utf-8 -*-
"""
NovaPlay Media Center — Enigma2 plugin entry point.
=========================================
Arabic, YTS & Torrent Streaming/Downloading.

PHASE-1 MODULAR SPLIT: this file registers the plugin and opens the
splash screen only. All screens live in plugin_screen_*.py, the
playback engine in novaplay_proxy / plugin_downloads, the subtitle
system in novaplay_subtitles / novaplay_substudio, shared constants in
plugin_common. Import order matters: plugin_common's sys.path shim runs
first via this import chain.

Single dispatcher note: callInMainThread comes ONLY from
novaplay_thread everywhere — the old in-plugin CMIT queue is gone.
"""

from Plugins.Plugin import PluginDescriptor

import plugin_common                                  # sys.path shim + constants
from plugin_common import _PLUGIN_NAME
from plugin_screen_splash import AdvancedArabicPlayerSplash

# Re-exported so external references (diagnostics, tools, any skin
# patches) keep working against the old names:
from plugin_screen_home import AdvancedArabicPlayerHome            # noqa: F401
from plugin_screen_detail import AdvancedArabicPlayerDetail        # noqa: F401
from plugin_screen_player import (AdvancedArabicPlayerSimplePlayer,  # noqa: F401
                                  _play, _build_remote_play_candidates)
from plugin_screen_search import AdvancedArabicPlayerSearch        # noqa: F401
from plugin_screen_settings import AdvancedArabicPlayerSettings    # noqa: F401

# Restore the browser proxy setting at boot (was in the monolith).
try:
    from extractors.base import set_browser_proxy
    from plugin_state import _get_config
    set_browser_proxy(_get_config("browser_proxy", ""))
except Exception:
    pass


def main(session, **kwargs):
    session.open(AdvancedArabicPlayerSplash)


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