# -*- coding: utf-8 -*-
"""
Arabseed (original / .wine mirror family) extractor
=====================================================
arabseed.py's main domain moved to arabseed.trade (with .rent/.loan kept
only as legacy redirectors into it). The old arabseed.wine mirror family
is a SEPARATE, independent site/catalog from arabseed.trade -- confirmed
live, 2026-10-13, via real captured snapshots of its homepage, a category
page, and a movie detail page ("Sacrifice 2025"), every one of which
canonicalized to https://www.arabseed.wine/... . Kept here as its own
extractor (named "original" since this is the long-running arabseed.wine
catalog) rather than folded into arabseed.py's domain-fallback list,
since that would mean silently picking only one of the two catalogs at a
time depending on whichever domain answers first.

Domain map (confirmed/stated):
    https://www.arabseed.wine/     main domain
    https://arabseed.wine/         non-www alias
    https://arabseed.in/           → https://www.arabseed.wine/
    https://m.arabseed.wine/       → https://www.arabseed.wine/
    https://arabseed.rocks/        → https://www.arabseed.wine/home/
    https://m.arsd.bid/            → https://www.arabseed.wine/home/

Markup is identical to arabseed.py's (same ``movie__block`` card classes,
same ``<ul id="watch">`` server list / govid.live decode chain, same
watch-page structure), so this subclasses ArabseedExtractor wholesale
and only swaps in its own domain list -- every bit of parsing,
pagination and server-ranking logic is reused unchanged, same as
arabseed_liv.py does for arabseed.living.
"""

from .arabseed import ArabseedExtractor

_KNOWN_DOMAINS = [
    "https://www.arabseed.wine/",      # live -- main domain
    "https://arabseed.wine/",          # non-www alias
    # Legacy redirectors (kept as fallbacks):
    "https://arabseed.in/",            # → https://www.arabseed.wine/
    "https://m.arabseed.wine/",        # → https://www.arabseed.wine/
    "https://arabseed.rocks/",         # → https://www.arabseed.wine/home/
    "https://m.arsd.bid/",             # → https://www.arabseed.wine/home/
]


class ArabseedOriginalExtractor(ArabseedExtractor):
    """Extractor for the arabseed.wine mirror family -- same engine as
    Arabseed, independent catalog from arabseed.trade."""

    MAIN_URL = _KNOWN_DOMAINS[0]
    _known_domains = _KNOWN_DOMAINS
