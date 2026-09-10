# -*- coding: utf-8 -*-
"""
NovaPlay Media Center - Extractors Package
===========================================
Each site has its own extractor module that inherits from BaseExtractor.
This keeps code clean, separated, and maintainable.

Adding a new site = new extractor file + ONE entry in registry.py
(_SITE_REGISTRY + optionally _HOME_SITE_ORDER / _SEARCH_SITE_ORDER).
Nothing in this file needs to change — registry.py already imports
every extractor class.

The explicit per-class imports below are redundant with registry.py but
kept for backwards compatibility with older direct imports such as
`from extractors import YTSExtractor`. To remove them, first verify:
    grep -rn "from extractors import" plugin*.py
shows no class-name imports, then delete the import lines + their
__all__ entries.
"""

from .base import BaseExtractor, fetch, log, set_browser_proxy, get_proxy_used, get_curl_failed_needs_proxy
from .registry import get_extractor, get_site_names, get_site_metadata, get_search_site_order, get_home_sites

# Explicitly import extractors to make them available directly under the package
from .yts import YTSExtractor
from .torrentio import TorrentioExtractor
from .vidsrc import VidsrcExtractor
from .imdbsu import ImdbSuExtractor
from .wecima_sarl import WecimaSarlExtractor

__all__ = [
    'BaseExtractor',
    'fetch',
    'log',
    'set_browser_proxy',
    'get_proxy_used',
    'get_curl_failed_needs_proxy',
    'get_extractor',
    'get_site_names',
    'get_site_metadata',
    'get_search_site_order',
    'get_home_sites',
    'YTSExtractor',
    'TorrentioExtractor',
    'VidsrcExtractor',
    'ImdbSuExtractor',
    'WecimaSarlExtractor',
]