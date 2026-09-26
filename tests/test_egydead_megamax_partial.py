# -*- coding: utf-8 -*-
"""PATCH 100: the live MegaMax page has NO `streams` prop (props = errors/video/config/ads); it is a
deferred Inertia prop loaded by a second, partial-reload GET. Page shape taken from the live box output,
request headers from the browser capture, response = the real captured JSON."""

import json
import extractors.egydead as eg
from tests.test_egydead_megamax import MM_JSON, _wrap

URL = "https://megamax.me/iframe/NDPBlLci0sk8E"
FINAL = "https://eg.megamax.cam/iframe/NDPBlLci0sk8E"
VERSION = "a601a2d0d16b8ae7121ceb1fd46c1f5a"
SHELL = json.dumps({"component": "files/mirror/video", "url": "/iframe/NDPBlLci0sk8E", "version": VERSION,
                    "props": {"errors": {}, "video": {"id": "NDPBlLci0sk8E"}, "config": {}, "ads": []}})


def _ex():
    e = eg.EgyDeadExtractor()
    e._resolved_base = "https://tv10.egydead.live/"
    return e


def test_deferred_streams_are_fetched_with_partial_reload_headers():
    e = _ex()
    e._fetch = lambda u, referer=None, post_data=None: (_wrap(SHELL), FINAL)
    calls = []

    def fake_fetch(url, referer=None, extra_headers=None, **kw):
        calls.append((url, referer, dict(extra_headers or {})))
        return MM_JSON, url

    saved, eg.fetch = eg.fetch, fake_fetch
    try:
        servers = e._megamax_servers(URL)
    finally:
        eg.fetch = saved
    assert len(servers) == 23 and servers[0]["name"] == "StreamRuby (1080p, 3.65 GB)"
    url, ref, h = calls[0]
    assert url == FINAL and ref == FINAL                      # same host the browser used, self-referer
    assert h["X-Inertia"] == "true" and h["X-Inertia-Version"] == VERSION
    assert h["X-Inertia-Partial-Data"] == "streams"
    assert h["X-Inertia-Partial-Component"] == "files/mirror/video"
    assert h["X-Requested-With"] == "XMLHttpRequest"


def test_inline_streams_do_not_trigger_a_second_request():
    e = _ex()
    e._fetch = lambda u, referer=None, post_data=None: (_wrap(MM_JSON), FINAL)

    def boom(*a, **k):
        raise AssertionError("partial reload must not run when streams are already inline")

    saved, eg.fetch = eg.fetch, boom
    try:
        assert len(e._megamax_servers(URL)) == 23
    finally:
        eg.fetch = saved


def test_failed_partial_reload_gives_no_servers_not_a_crash():
    e = _ex()
    e._fetch = lambda u, referer=None, post_data=None: (_wrap(SHELL), FINAL)
    saved, eg.fetch = eg.fetch, (lambda *a, **k: ("<html>409 conflict</html>", FINAL))
    try:
        assert e._megamax_servers(URL) == []
    finally:
        eg.fetch = saved
