# -*- coding: utf-8 -*-
"""TopCinema extract_stream must always return a 4-tuple (PATCH 98)."""


def _ex():
    from extractors import topcinema as tc
    ex = tc.TopCinemaExtractor()
    ex._resolved_base = "https://topcinemaa.top/"
    return tc, ex


SERVER_URL = "topcinema_server|https://x/ajax|1|2|https://x/w"


def test_topcinema_no_iframe_returns_failure_tuple():
    tc, ex = _ex()
    orig = tc.fetch
    try:
        tc.fetch = lambda *a, **k: (None, "")                                   # AJAX failed outright
        r = ex.extract_stream(SERVER_URL)
        assert isinstance(r, tuple) and len(r) == 4 and r[0] is None, r
        tc.fetch = lambda *a, **k: ("<html>no iframe here</html>", "u")         # reply without an iframe
        r = ex.extract_stream(SERVER_URL)
        assert isinstance(r, tuple) and len(r) == 4 and r[0] is None, r
    finally:
        tc.fetch = orig


def test_topcinema_plain_url_goes_to_generic_resolver():
    import extractors.base as b
    tc, ex = _ex()
    orig = b.extract_stream
    b.extract_stream = lambda u: ("https://cdn.example/x.m3u8", "720p", "https://r/", [])
    try:
        r = ex.extract_stream("https://direct.example/embed/abc")
        assert r[0] == "https://cdn.example/x.m3u8" and len(r) == 4, r
    finally:
        b.extract_stream = orig
