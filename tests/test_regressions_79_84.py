# -*- coding: utf-8 -*-
"""Regression tests for PATCH 79 / 81 / 82 / 83 / 84 (headless, no network)."""

import os
import tempfile


def test_api_key_written_to_conf_file():
    import plugin_state as ps
    tmp = os.path.join(tempfile.mkdtemp(), "api_keys.conf")
    with open(tmp, "w") as f:
        f.write("# header\ntmdb_api_key=old\nsubsource_api_key=keep\n")
    orig = ps._KEYS_FILE
    ps._KEYS_FILE = tmp
    try:
        assert ps._write_api_key_to_file("tmdb_api_key", "new123")
        assert ps._write_api_key_to_file("opensubtitles_api_key", "os456")
        with open(tmp) as f:
            body = f.read()
        state = {}
        ps._load_api_keys_file(state)          # round-trip: what a restart would load
    finally:
        ps._KEYS_FILE = orig
    assert "tmdb_api_key=new123" in body
    assert "tmdb_api_key=old" not in body
    assert "subsource_api_key=keep" in body
    assert "# header" in body
    assert state["config"]["tmdb_api_key"] == "new123"
    assert state["config"]["opensubtitles_api_key"] == "os456"


def test_episode_label_split():
    from plugin_util import _split_episode_label
    assert _split_episode_label("S01E05: Pilot Name") == ("S01E05", "Pilot Name")
    assert _split_episode_label("s2e10 - Finale") == ("S2E10", "Finale")
    assert _split_episode_label("S01E05:") == ("S01E05", "")
    assert _split_episode_label("Season 1") == ("", "Season 1")
    assert _split_episode_label(u"حلقة 5") == ("", u"حلقة 5")
    assert _split_episode_label("") == ("", "Episode")


def test_imdbsu_multiword_actions():
    from extractors.imdbsu import ImdbSuExtractor
    ex = ImdbSuExtractor()
    seen = []
    ex._tmdb_request = lambda path, params=None: (
        seen.append(path) or {"results": [], "total_pages": 1, "page": 1})
    ex.get_category_items("imdb_su_movie_top_rated")
    ex.get_category_items("imdb_su_series_trending_week")
    assert seen == ["/movie/top_rated", "/trending/tv/week"]


def test_vidsrc_multiword_actions():
    from extractors.vidsrc import VidsrcExtractor
    ex = VidsrcExtractor()
    seen = []
    ex._tmdb_request = lambda path, params=None: (
        seen.append(path) or {"results": [], "total_pages": 1, "page": 1})
    ex.get_category_items("vidsrc_movie_top_rated")
    ex.get_category_items("vidsrc_series_on_the_air")
    ex.get_category_items("vidsrc_movie_trending_week")
    assert seen == ["/movie/top_rated", "/tv/on_the_air", "/trending/movie/week"]


def test_topcinema_failed_chain_and_referer():
    from extractors import topcinema as tc
    import extractors.base as b
    ex = tc.TopCinemaExtractor()
    ex._resolved_base = "https://topcinemaa.top/"
    orig = (tc.fetch, tc.resolve_iframe_chain, b.extract_stream)
    try:
        tc.fetch = lambda *a, **k: ('<iframe src="https://embed.example/e/abc"></iframe>', "u")
        url = "topcinema_server|https://x/ajax|1|2|https://x/w"

        # chain fails -> host-resolver fallback fails -> a clean None
        tc.resolve_iframe_chain = lambda *a, **k: (None, "")
        b.extract_stream = lambda u: (None, "", u, [])
        assert ex.extract_stream(url)[0] is None

        # chain succeeds -> referer is an origin URL, never a bare hostname
        tc.resolve_iframe_chain = lambda *a, **k: ("https://cdn.example/a.m3u8", "embed.example")
        r = ex.extract_stream(url)
        assert r[0] == "https://cdn.example/a.m3u8"
        assert r[2] == "https://embed.example/"
    finally:
        tc.fetch, tc.resolve_iframe_chain, b.extract_stream = orig


def test_carousel_skin_has_no_orphan_cbar():
    """Text check (plugin_gridlist needs enigma). If you later implement the
    carousel progress bar, create self["cbar%d"] in Home and delete this test."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "plugin_gridlist.py")) as f:
        src = f.read()
    assert 'name="cbar{i}"' not in src