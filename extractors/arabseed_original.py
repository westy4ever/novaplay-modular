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

    def get_categories(self, mtype="movie"):
        """
        [PATCH AS7] arabseed.wine's own category paths, confirmed
        2026-10-13 directly from a real captured homepage network
        recording + the nav menu's own href list (not guessed, and NOT
        the same as arabseed.trade's). The parent ArabseedExtractor's
        get_categories() returns flat Arabic-slug paths like
        ``/category/افلام-اجنبي/`` -- those are specifically correct for
        arabseed.trade (per its own [PATCH AS3] comment), but on
        arabseed.wine every one of those 404s/redirects to an empty
        shell page: this site nests categories under an English parent
        slug instead (``/category/films/foreign-movies/``,
        ``/category/tv/foreign-series/``, ``/category/anime/...``).
        Confirmed by diffing a real network capture of the "foreign
        movies" page against the plugin's own fetch -- the real page
        the site served (and that the user's browser loaded) was
        ``/category/films/foreign-movies/``, not the Arabic-slug path,
        and that page's raw HTML carries all 40 real movie__block
        cards (re-ran this extractor's own _extract_items_from_html()
        against that capture -- Strategy 1 finds all 40, cleanly).

        Only entries seen directly in the real captured nav menu are
        listed here. Several categories the base class lists for
        arabseed.trade (Indian/Arabic movies & series, dubbed series,
        wrestling, Ramadan) do NOT appear in arabseed.wine's main nav at
        all -- some of those exist only as `data-term` filter-widget
        entries on the homepage (e.g. "افلام هندية" is term id 87,
        "مسلسلات هندية" is term id 17889), which drive an AJAX filter
        rather than a plain category URL. Left out rather than guessed
        at a URL for them; revisit if/when that AJAX filter call itself
        gets captured.
        """
        base = self._get_base().rstrip("/")
        return [
            {"title": "🎬 كل الأفلام",         "url": base + "/category/films/",                     "type": "category", "_action": "category"},
            {"title": "🌍 أفلام أجنبي",        "url": base + "/category/films/foreign-movies/",      "type": "category", "_action": "category"},
            {"title": "🌏 أفلام آسيوية",       "url": base + "/category/films/asian-movies/",        "type": "category", "_action": "category"},
            {"title": "🇹🇷 أفلام تركية",       "url": base + "/category/films/turkish-movies/",      "type": "category", "_action": "category"},
            {"title": "🎙 أفلام تركية مدبلجة", "url": base + "/category/films/movies-turkish-dubbed/", "type": "category", "_action": "category"},
            {"title": "📺 كل المسلسلات",       "url": base + "/category/tv/",                        "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات أجنبي",      "url": base + "/category/tv/foreign-series/",         "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات آسيوية",     "url": base + "/category/tv/asian-series/",           "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات لاتينية",    "url": base + "/category/tv/مسلسلات-لاتينية/",        "type": "category", "_action": "category"},
            {"title": "📺 مسلسلات وثائقية",    "url": base + "/category/tv/مسلسلات-وثائقية/",        "type": "category", "_action": "category"},
            {"title": "🎭 كل الأنمي",          "url": base + "/category/anime/",                     "type": "category", "_action": "category"},
            {"title": "🎭 أفلام انمي",         "url": base + "/category/anime/anime-movies/",        "type": "category", "_action": "category"},
            {"title": "🎭 مسلسلات انمي",       "url": base + "/category/anime/anime-series/",        "type": "category", "_action": "category"},
            {"title": "🧸 أفلام كرتون",        "url": base + "/category/anime/cartoon-movies/",      "type": "category", "_action": "category"},
            {"title": "🧸 مسلسلات كرتون",      "url": base + "/category/anime/cartoon-series/",      "type": "category", "_action": "category"},
            {"title": "📺 برامج تلفزيونية",    "url": base + "/category/tv-shows/",                  "type": "category", "_action": "category"},
            {"title": "🏆 رياضة",              "url": base + "/category/sport/",                     "type": "category", "_action": "category"},
        ]
