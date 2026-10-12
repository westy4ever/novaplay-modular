# -*- coding: utf-8 -*-
"""
Arabseed.living extractor
==========================
Same theme/markup as arabseed.rent/.wine (confirmed against real
captures 2026-10-12: identical ``movie__block`` card classes, identical
``<ul id="watch">`` server list with ``data-link="govid.live/play/=..."``
wrappers, identical watch-page structure) -- but a SEPARATE, independent
movie/series catalog, confirmed by the user directly ("movies there is
different from movies there"). Because the catalogs differ, this can't
just be folded into arabseed.py's existing domain-fallback list (that
would mean picking only ONE of the two catalogs at a time, silently,
whichever domain answers first) -- it needs to be its own selectable
site so both libraries stay reachable.

Since the markup is identical, this subclasses ArabseedExtractor
wholesale and only swaps in its own domain -- every bit of parsing,
pagination, the govid.live decode chain, and the per-title server
ranking (keyed by MAIN_URL being under this domain, so it never
collides with regular Arabseed's history/favorites/rankings) is reused
unchanged.

If arabseed.living's markup ever actually diverges (a different card
class, a different watch-page layout), override just the specific
method here rather than forking the rest of the parent file.
"""

from .arabseed import ArabseedExtractor

_KNOWN_DOMAINS = [
    "https://arabseed.living/",
]


class ArabseedLivExtractor(ArabseedExtractor):
    """Extractor for arabseed.living -- same engine as Arabseed, own catalog."""

    MAIN_URL = _KNOWN_DOMAINS[0]
    _known_domains = _KNOWN_DOMAINS
