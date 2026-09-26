# -*- coding: utf-8 -*-
"""PATCH 114 regression test: wecima card enrichment. Fixtures are real card HTML from actual
wecima.cx captures (2026-09-22) -- CAROUSEL_CARD from the homepage carousel (no schema.org
microdata), LISTING_CARD from a category-listing page (has schema.org/VideoObject microdata)."""

import sys, types

def _load_wecima():
    src = open("extractors/wecima.py", encoding="utf-8").read()
    src = src.replace(
        "from .base import BaseExtractor, fetch, log, urljoin",
        "from extractors.base import BaseExtractor, fetch, log, urljoin",
    )
    mod = types.ModuleType("wecima_under_test")
    exec(compile(src, "wecima.py", "exec"), mod.__dict__)
    return mod

_mod = _load_wecima()

CAROUSEL_CARD = """<div class=\"Wecima-Card\"> <a class=\"Wecima-Card__link\" href=\"https://wecima.cx/watch/the-mothers-monster-2026-movie\" title=\"\u0641\u064a\u0644\u0645 The Mother's Monster 2026 \u0645\u062a\u0631\u062c\u0645\"> <div class=\"Wecima-Card__poster\"> <span class=\"Wecima-Card__rating\">7.5</span> <span class=\"Wecima-Card__quality\">HD</span> <span class=\"Wecima-Card__genre\">\u0641\u0627\u0646\u062a\u0627\u0632\u064a\u0627</span> <span class=\"Wecima-Card__img\" style=\"--image:url(https://wecima.cx/upload/posters/69340.webp);\"></span> <div class=\"Wecima-Card__shade\"></div> <div class=\"Wecima-Card__info\"> <strong dir=\"auto\" class=\"Wecima-Card__title\">\u0641\u064a\u0644\u0645 The Mother's Monster 2026 \u0645\u062a\u0631\u062c\u0645</strong> </div> </div> </a> </div></li><li class=\"splide__slide is-visible is-next\" id=\"splide01-slide02\" role=\"group\" aria-roledescription=\"slide\" aria-la"""
LISTING_CARD = """<div class=\"Wecima-Card\" itemscope=\"\" itemtype=\"https://schema.org/VideoObject\"> <meta itemprop=\"uploadDate\" content=\"2026-08-30T11:13:49+00:00\"> <meta itemprop=\"description\" content=\"\u0634\u0627\u0647\u062f \u0627\u0644\u0622\u0646 \u0648\u0642\u0628\u0644 \u0627\u0644\u062c\u0645\u064a\u0639 \u0641\u064a\u0644\u0645 Heart of the Beast 2026 \u0645\u062a\u0631\u062c\u0645 \u0628\u062c\u0648\u062f\u0629 \u0641\u0627\u0626\u0642\u0629 \u0639\u0644\u0649 \u0645\u0627\u064a \u0633\u064a\u0645\u0627 \u0627\u0644\u0623\u0635\u0644\u064a.\"> <meta itemprop=\"contentUrl\" content=\"https://wecima.cx/watch/heart-of-the-beast-2026-movie\"> <meta itemprop=\"duration\" content=\"PT1H41M\"> <meta itemprop=\"inLanguage\" content=\"ar\"> <meta itemprop=\"thumbnailUrl\" content=\"https://wecima.cx/upload/posters/67502.webp\"> <p class=\"media-card__description\" style=\"display: none;\" itemprop=\"description\">\u0634\u0627\u0647\u062f \u0627\u0644\u0622\u0646 \u0648\u0642\u0628\u0644 \u0627\u0644\u062c\u0645\u064a\u0639 \u0641\u064a\u0644\u0645 Heart of the Beast 2026 \u0645\u062a\u0631\u062c\u0645 \u0628\u062c\u0648\u062f\u0629 \u0641\u0627\u0626\u0642\u0629 \u0639\u0644\u0649 \u0645\u0627\u064a \u0633\u064a\u0645\u0627 \u0627\u0644\u0623\u0635\u0644\u064a.</p> <a class=\"Wecima-Card__link\" href=\"https://wecima.cx/watch/heart-of-the-beast-2026-movie\" title=\"\u0645\u0634\u0627\u0647\u062f\u0629 \u0641\u064a\u0644\u0645 Heart of the Beast 2026 \u0645\u062a\u0631\u062c\u0645\"> <div class=\"Wecima-Card__poster\"> <span class=\"Wecima-Card__rating\">6.0</span> <span class=\"Wecima-Card__quality\">HDCAM</span> <span class=\"Wecima-Card__genre\">\u0623\u0643\u0634\u0646</span> <span class=\"Wecima-Card__img\" data-src=\"https://wecima.cx/upload/posters/67502.webp\" style=\"--image: url(https://wecima.cx/static/img/wecima.webp); background-image: url(&quot;https://wecima.cx/upload/posters/67502.webp&quot;);\"></span> <div class=\"Wecima-Card__shade\"></div> <div class=\"Wecima-Card__info\"> <h2 dir=\"auto\" class=\"Wecima-Card__title\" itemprop=\"name\">\u0645\u0634\u0627\u0647\u062f\u0629 \u0641\u064a\u0644\u0645 Heart of the Beast 2026 \u0645\u062a\u0631\u062c\u0645</h2> </div> </div> </a> <div class=\"watchlater Wecima-Card__fav\" data-video=\"67502\"> <svg class=\"icons mt-2 favorite-icon\" width=\"16\" height=\"16\"> <use href=\"/static/icons.svg#icon-heart\"></use> </svg> </div> </div>"""


def _ex():
    return _mod.WecimaExtractor()


def test_carousel_card_gets_rating_quality_genre_year():
    e = _ex()
    cards = e._extract_cards(CAROUSEL_CARD)
    assert len(cards) == 1, cards
    c = cards[0]
    assert c["title"] == "The Mother's Monster 2026"
    assert c["year"] == "2026"
    assert c["rating"] == "7.5"
    assert c["quality"] == "HD"
    assert c["genres"] == "\u0641\u0627\u0646\u062a\u0627\u0632\u064a\u0627"
    # carousel cards have no schema.org microdata -- runtime should be genuinely absent,
    # not fabricated
    assert "runtime" not in c


def test_listing_card_gets_full_microdata():
    e = _ex()
    cards = e._extract_cards(LISTING_CARD)
    assert len(cards) == 1, cards
    c = cards[0]
    assert c["year"] == "2026"
    assert c["rating"] == "6.0"
    assert c["quality"] == "HDCAM"
    assert c["runtime"] == "1h 41m"
    assert "Heart of the Beast" in c["plot"]


def test_fields_absent_when_not_present_are_not_invented():
    e = _ex()
    # a block with a title/link but no rating/quality/genre spans at all
    minimal = (
        '<div class="Wecima-Card"> <a class="Wecima-Card__link" '
        'href="https://wecima.cx/watch/some-movie" title="Some Movie 2025"> '
        '<strong class="Wecima-Card__title">Some Movie 2025</strong> </a> </div>'
    )
    cards = e._extract_cards(minimal)
    assert len(cards) == 1
    c = cards[0]
    assert c["title"] == "Some Movie 2025" and c["year"] == "2025"
    assert "rating" not in c and "quality" not in c and "genres" not in c and "runtime" not in c


def test_empty_html_returns_no_cards():
    e = _ex()
    assert _ex()._extract_cards("") == []
    assert _ex()._extract_cards("<html>nothing here</html>") == []
