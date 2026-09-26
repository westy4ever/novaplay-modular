# -*- coding: utf-8 -*-
"""PATCH 101 regression tests. Fixtures come from the real pages fetched on the box on 2026-09-21:
the DoodStream page (doodstream.com/e/... -> playmogo.com) and Vidara's /api/stream call."""

import re
import json
import extractors.hosts as h

# verbatim from the live DoodStream page (the part of the inline script that matters)
DOOD_PAGE = ("<html><script>dsplayer.mobileUi({}); "
             "$.get('/pass_md5/279481356-197-164-1789977170-9d48fabd56c6c360f0f5d01c16de307c/hfxb2fhm72khv1jj5mbev10j', "
             "function(data) { if (data === \"RELOAD\"){location.reload();} dpload(data); "
             "dsplayer.src({ type: \"video/mp4\", src: data + makePlay() }); });</script></html>")
PASS_MD5 = "https://playmogo.com/pass_md5/279481356-197-164-1789977170-9d48fabd56c6c360f0f5d01c16de307c/hfxb2fhm72khv1jj5mbev10j"
EMBED = "https://doodstream.com/e/4yf8ri1gcej8"
FINAL = "https://playmogo.com/e/4yf8ri1gcej8"


def _patched(fake):
    class Ctx(object):
        def __enter__(self):
            self.saved = h.fetch
            h.fetch = fake
        def __exit__(self, *a):
            h.fetch = self.saved
    return Ctx()


def test_dood_uses_the_redirected_domain_and_returns_a_referer():
    calls = []

    def fake(url, referer=None, extra_headers=None, post_data=None, **kw):
        calls.append((url, referer, dict(extra_headers or {})))
        if url == EMBED:
            return DOOD_PAGE, FINAL
        if url == PASS_MD5:
            return "https://kf313l.cloudatacdn.com/abc123/xyz~\n", url
        return None, url

    with _patched(fake):
        out = h.resolve_doodstream(EMBED)
    stream, _, pipe = out.partition("|")
    assert re.match(r"^https://kf313l\.cloudatacdn\.com/abc123/xyz~[A-Za-z0-9]{10}\?token=hfxb2fhm72khv1jj5mbev10j&expiry=\d{13}$", stream), stream
    assert pipe == "Referer=https://playmogo.com/"
    pm = [c for c in calls if "pass_md5" in c[0]]
    assert len(pm) == 1 and pm[0][0] == PASS_MD5           # playmogo.com, NOT doodstream.com
    assert pm[0][1] == FINAL and pm[0][2].get("X-Requested-With") == "XMLHttpRequest"


def test_dood_reload_answer_is_retried_once():
    seq = {"n": 0}

    def fake(url, referer=None, extra_headers=None, **kw):
        if url == EMBED or url == FINAL:
            return DOOD_PAGE, FINAL
        if url == PASS_MD5:
            seq["n"] += 1
            return ("RELOAD" if seq["n"] == 1 else "https://cdn.example/a/b~"), url
        return None, url

    with _patched(fake):
        out = h.resolve_doodstream(EMBED)
    assert out and out.startswith("https://cdn.example/a/b~") and seq["n"] == 2


def test_dood_garbage_answer_is_not_returned_as_a_stream():
    def fake(url, referer=None, extra_headers=None, **kw):
        return ((DOOD_PAGE, FINAL) if url == EMBED else ("<html>error</html>", url))

    with _patched(fake):
        assert h.resolve_doodstream(EMBED) is None


def test_vidara_posts_a_json_body_to_the_embed_host():
    got = {}

    def fake(url, referer=None, extra_headers=None, post_data=None, **kw):
        got.update(url=url, referer=referer, headers=dict(extra_headers or {}), body=post_data)
        return json.dumps({"streaming_url": "https:\\/\\/cdn.example\\/hls\\/master.m3u8?t=1", "title": "x"}), url

    with _patched(fake):
        out = h.resolve_vidaraa("https://vidaraa.cc/e/7syr7xvKW6Ld1")
    assert out == "https://cdn.example/hls/master.m3u8?t=1"
    assert got["url"] == "https://vidaraa.cc/api/stream" and got["referer"] == "https://vidaraa.cc/e/7syr7xvKW6Ld1"
    assert isinstance(got["body"], str) and json.loads(got["body"]) == {"filecode": "7syr7xvKW6Ld1", "device": "web"}
    assert got["headers"]["Content-Type"] == "application/json" and got["headers"]["Origin"] == "https://vidaraa.cc"


def test_vidara_follows_a_rotated_domain_and_survives_bad_answers():
    seen = []

    def fake(url, referer=None, extra_headers=None, post_data=None, **kw):
        seen.append(url)
        return "not json at all", url

    with _patched(fake):
        assert h.resolve_vidaraa("https://vidara.so/e/AbC123") is None
    assert seen == ["https://vidara.so/api/stream"]
