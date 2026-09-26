# -*- coding: utf-8 -*-
"""akwams.py regression tests: the movie detail page's bogus data-server="58" entry no
longer blocks the already-correct /watch/ page fallback (PATCH AK1), and data: URIs are
correctly filtered from the server list. Fixtures are real captured content
(Fall 2: Deadpoint 2026, 2026-09-25)."""

import os


def _load_extractor():
    import sys
    sys.path.insert(0, os.getcwd())
    from extractors.akwams import AkwamsExtractor
    return AkwamsExtractor()


DETAIL_FIXTURE = """     <div class=\"widget widget-style-1 mb-5\">
          <header class=\"widget-header border-0 mb-4\" id=\"downloads\">
            <h2 class=\"header-title font-size-18 font-weight-bold mb-0\">
            <span class=\"header-link text-white\">\u0645\u0634\u0627\u0647\u062f\u0629
              \u0648\u062a\u062d\u0645\u064a\u0644</span></h2>
          </header>

                            
<div class=\"widget-body\">
    <div class=\"header-tabs-container\">
        <ul class=\"header-tabs tabs d-flex list-unstyled p-0 m-0\">
            <li><a href=\"#tab-5\" class=\"selected\">\u0645\u062a\u0639\u062f\u062f \u062c\u0648\u062f\u0627\u062a</a></li>
        </ul>
    </div>
    <div class=\"bg-primary2 p-4\" style=\"border-radius: 6px 0 6px 6px\">
        <div class=\"tab-content quality\" id=\"tab-5\" style=\"\">
            <div class=\"qualities row flex-wrap align-items-center\">
                <div class=\"col-lg-6 row\" data-server=\"58\" data-quality=\"5\">
                    <div class=\"col-lg-6 col\">
                        <a href=\"javascript:void(0);\" id=\"watchBtn\" class=\"btn btn-orange btn-pill d-flex align-items-center text-white mt-2\">
                            <span id=\"watchText\" class=\"font-size-18 font-weight-medium\">\u0645\u0634\u0627\u0647\u062f\u0629</span>
                            <i class=\"icon-play2 font-size-20 mr-auto\"></i>
                        </a>
                    </div>
                    <div class=\"col-lg-6 col\">
                        <a href=\"javascript:void(0);\" id=\"downloadBtn\" class=\"btn btn-info btn-pill d-flex align-items-center text-white mt-2\">
                            <span id=\"downloadText\" class=\"font-size-18 font-weight-medium\">\u062a\u062d\u0645\u064a\u0644</span>
                            <i class=\"icon-download font-size-20 mr-auto\"></i>
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </div>
"""

WATCH_FIXTURE = """</h1>

        <div class=\"watch-top\">

            <span class=\"servers-label\">\u0633\u064a\u0631\u0641\u0631\u0627\u062a \u0627\u0644\u0645\u0634\u0627\u0647\u062f\u0629</span>

                            
                    <button class=\"server-btn\" data-link=\"https://cybervynx.com/e/gso2qagv3rnd\">

                        \u25b6 \u0633\u064a\u0631\u0641\u0631 1
                    </button>

                
                    <button class=\"server-btn\" data-link=\"https://cybervynx.com/e/s991q1g883em\">

                        \u25b6 \u0633\u064a\u0631\u0641\u0631 2
                    </button>

                
                    <button class=\"server-btn\" data-link=\"https://doodstream.com/e/7beeokp4twg4\">

                        \u25b6 \u0633\u064a\u0631\u0641\u0631 3
                    </button></div>"""


def test_detail_page_data_server_id_no_longer_produces_a_bogus_server():
    e = _load_extractor()
    servers = e._extract_servers_from_html(DETAIL_FIXTURE, "https://akwams.org/x/")
    assert servers == [], servers


def test_watch_page_finds_the_real_server_list():
    e = _load_extractor()
    servers = e._extract_servers_from_html(WATCH_FIXTURE, "https://akwams.org/x/watch/")
    assert len(servers) == 3, servers
    urls = [s["url"] for s in servers]
    assert "https://cybervynx.com/e/gso2qagv3rnd" in urls, urls
    assert "https://cybervynx.com/e/s991q1g883em" in urls, urls
    assert "https://doodstream.com/e/7beeokp4twg4" in urls, urls


def test_data_uri_never_added_as_a_server():
    e = _load_extractor()
    html = '''<iframe src="data:text/html;charset=utf-8;base64,PGh0bWw+"></iframe>'''
    servers = e._extract_servers_from_html(html, "https://akwams.org/x/")
    assert servers == [], servers


def test_get_page_falls_through_to_watch_page_when_detail_page_has_no_real_servers():
    import extractors.akwams as ak_mod
    e = _load_extractor()
    detail_url = "https://akwams.org/movie-x/"
    calls = []

    def fake_fetch(url, referer=None, **k):
        calls.append(url)
        if url == detail_url:
            return DETAIL_FIXTURE, url
        if "watch" in url:
            return WATCH_FIXTURE, url
        return "<html>base probe</html>", url

    ak_mod.fetch = fake_fetch
    result = e.get_page(detail_url)
    assert any("watch" in c for c in calls), calls
    assert len(result["servers"]) == 3, result["servers"]
