# -*- coding: utf-8 -*-
"""aflaam.py regression tests: watch-server extraction, the bogus /login exclusion (PATCH A2),
and watch-page stream resolution via JSON-LD contentUrl (PATCH A1). All fixtures are real
captured content (Batman Returns, 2026-09-23)."""

import sys
import os

sys.path.insert(0, os.getcwd())
import extractors.aflaam as _aflaam_mod

MOVIE_FIXTURE = """<div class=\"bg-black d-flex flex-wrap align-items-center justify-content-center p-3 qualities mb-4\">
                    <img src=\"https://aflaam.com/style/assets/images/quality-1080.png\" class=\"img-fluid\" alt=\"Quality\">
                    <span class=\"text-white mr-2 ml-auto\">1080p</span>
                    <div class=\"col-12 my-2 d-sm-none\"></div>
                                                                  <a href=\"https://aflaam.com/watch/16455/3069/batman-returns\" class=\"link-show d-flex align-items-center mx-2 ml-2\">
                          <i class=\"icon-play-2 ml-2\"></i><span class=\"text\">\u0645\u0634\u0627\u0647\u062f\u0629</span>
                        </a>
                                                                    <a href=\"https://aflaam.com/download/16455/3069/batman-returns\" class=\"link-download d-flex align-items-center mx-2 ml-2\">
                          <i class=\"icon-download ml-2\"></i>
                          <span class=\"text\">\u062a\u062d\u0645\u064a\u0644</span>
                          <span class=\"text mr-auto\">1.4 GB</span>
                        </a>"""

LOGIN_FIXTURE = r'''<a class="user-toggle d-block public" data-fancybox="" data-type="ajax" data-src="https://aflaam.com/login" href="javascript:;"><i class="icon-user"></i></a>'''

WATCH_JSONLD_FIXTURE = """<script type=\"application/ld+json\">
    {\"@context\":\"https://schema.org\",\"@type\":\"VideoObject\",\"name\":\"\u0645\u0634\u0627\u0647\u062f\u0629 \u0641\u064a\u0644\u0645 Batman Returns\",\"description\":\"\u0628\u064a\u0646\u0645\u0627 \u064a\u062a\u0639\u0627\u0645\u0644 \u0628\u0627\u062a\u0645\u0627\u0646 \u0645\u0639 \u0631\u062c\u0644 \u0645\u0634\u0648\u0647 \u064a\u0637\u0644\u0642 \u0639\u0644\u0649 \u0646\u0641\u0633\u0647 \u0627\u0633\u0645 \\\"\u0627\u0644\u0628\u0637\u0631\u064a\u0642\\\" \u0648\u064a\u062d\u062f\u062b \u0627\u0644\u0641\u0648\u0636\u0649 \u0641\u064a \u062c\u0648\u062b\u0627\u0645 \u0628\u0645\u0633\u0627\u0639\u062f\u0629 \u0631\u062c\u0644 \u0623\u0639\u0645\u0627\u0644 \u0642\u0627\u0633\u064a\u060c \u062a\u0635\u0628\u062d \u0627\u0644\u0645\u0648\u0638\u0641\u0629 \u0644\u062f\u0649 \u0647\u0630\u0627 \u0627\u0644\u0623\u062e\u064a\u0631 \u0647\u064a \u0627\u0644\u0645\u0631\u0623\u0629 \u0627\u0644\u0642\u0637\u0629 \u0645\u0639 \u062b\u0623\u0631\u0647\u0627 \u0627\u0644\u062e\u0627\u0635. \u0645\u0634\u0627\u0647\u062f\u0629 \u0645\u0628\u0627\u0634\u0631\u0629 \u0627\u0648\u0646 \u0644\u0627\u064a\u0646\",\"embedUrl\":\"https://aflaam.com/watch/16456/3069/batman-returns\",\"contentUrl\":\"https://af3.downet.net/download/1790246941/6ab3ae9d88ead/Batman.Returns.1992.1080p.BluRay.aflaam.com.mp4\",\"thumbnailUrl\":[\"https://images.aflaam.com/thumb/830x506/uploads/uHyZu.jpeg\",\"https://images.aflaam.com/thumb/400x300/uploads/uHyZu.jpeg\",\"https://images.aflaam.com/thumb/1600x900/uploads/uHyZu.jpeg\"],\"uploadDate\":\"2023-10-01T01:26:47+03:00\",\"interactionStatistic\":{\"@type\":\"InteractionCounter\",\"interactionType\":{\"@type\":\"http://schema.org/WatchAction\"},\"userInteractionCount\":7766}}
  </script>"""

EXPECTED_STREAM = "https://af3.downet.net/download/1790246941/6ab3ae9d88ead/Batman.Returns.1992.1080p.BluRay.aflaam.com.mp4"


def _ex():
    return _aflaam_mod.AflaamExtractor()


def test_real_movie_page_finds_the_watch_link_not_a_bogus_one():
    e = _ex()
    servers = e._extract_watch_servers(MOVIE_FIXTURE, "https://aflaam.com/movie/3069/batman-returns")
    urls = [s["url"] for s in servers]
    assert "https://aflaam.com/watch/16455/3069/batman-returns" in urls, urls
    assert not any("/login" in u for u in urls), urls


def test_login_icon_alone_is_correctly_excluded():
    e = _ex()
    servers = e._extract_watch_servers(LOGIN_FIXTURE, "https://aflaam.com/movie/3069/batman-returns")
    assert servers == [], servers


def test_watch_page_jsonld_resolves_to_the_real_stream():
    e = _ex()
    page = "<html><body>" + WATCH_JSONLD_FIXTURE + "</body></html>"
    _aflaam_mod.fetch = lambda url, referer=None, **k: (page, url)
    stream_url, quality, referer, variants = e.extract_stream("https://aflaam.com/watch/16455/3069/batman-returns")
    assert stream_url == EXPECTED_STREAM, stream_url
    assert quality == "1080p", quality


def test_download_url_unaffected_by_the_watch_branch():
    e = _ex()
    page = "<html><body>" + WATCH_JSONLD_FIXTURE + "</body></html>"
    _aflaam_mod.fetch = lambda url, referer=None, **k: (page, url)
    # a /download/ URL must still go through the download branch, not silently
    # get treated as a /watch/ URL just because both branches call the same helper
    stream_url, quality, referer, variants = e.extract_stream("https://aflaam.com/download/16455/3069/batman-returns")
    assert stream_url == EXPECTED_STREAM, stream_url


def test_empty_html_returns_no_servers_not_a_crash():
    e = _ex()
    assert e._extract_watch_servers("", "https://aflaam.com/movie/3069/batman-returns") == []
    assert e._extract_watch_servers(None, "https://aflaam.com/movie/3069/batman-returns") == []
