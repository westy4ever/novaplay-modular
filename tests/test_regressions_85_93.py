# -*- coding: utf-8 -*-
"""Regression tests for PATCH 85 / 86 / 91 / 93 (headless, no network)."""

import os
import tempfile
import time


def test_tmdb_ties_keep_tmdb_order():
    from plugin_tmdb import _tmdb_pick_best
    res = [{"title": "Zebra", "id": 1}, {"title": "Alpha", "id": 2}]
    assert _tmdb_pick_best(res, "unrelated query")["id"] == 1     # was Alpha (alphabetical)


def test_tmdb_exact_match_still_wins():
    from plugin_tmdb import _tmdb_pick_best
    res = [{"title": "Zebra", "id": 1}, {"title": "Alpha", "id": 2}]
    assert _tmdb_pick_best(res, "alpha")["id"] == 2


def test_direct_media_ignores_host_substrings():
    from extractors.hosts import _looks_like_direct_media as f
    assert f("https://cdn.io/a/master.m3u8?t=1")
    assert f("https://x.io/api?seg=index.m3u8")
    assert f("https://h.io/seg_1.ts")
    assert f("https://h.io/master.txt")
    assert f("https://x.example-cdn.org/video_1080.mp4")
    assert not f("https://vidstream.tsvideo.com/e/abc")
    assert not f("https://tsstream.to/e/abc")


def test_quality_label_bare_hd_is_not_720p():
    from extractors.hosts import _quality_label_for as q
    assert q("https://hdstream.example/e/abc") == "HD"
    assert q("https://c.scdns.io/hls/hd1080/index.m3u8") == "1080p"
    assert q("https://c.scdns.io/hls/hd720/index.m3u8") == "720p"
    assert q("https://cdn.x/v/index-f2-v1-a1.m3u8") == "720p"
    assert q("https://cdn.x/movie_the_hunt/master.m3u8") == "HD"


def test_opensubtitles_login_keys_are_file_owned():
    import plugin_state as ps
    assert "opensubtitles_user" in ps._KEY_NAMES
    assert "opensubtitles_pass" in ps._KEY_NAMES


def test_bw_variant_built_off_thread():
    try:
        from PIL import Image
    except Exception:
        return                                   # no PIL on this image
    import io
    import plugin_imagecache as ic
    tmp = tempfile.mkdtemp()
    orig = ic.getImagesDir
    ic.getImagesDir = lambda: tmp                # keep the real cache untouched
    try:
        url, size = "https://example.invalid/p.jpg", (21, 33)
        buf = io.BytesIO()
        Image.new("RGB", size, (200, 50, 50)).save(buf, format="JPEG")
        ic.writeFileAtomic(ic.buildCachePath(url, size), buf.getvalue())
        assert ic.getBwVariant(url, size) == ""  # returns immediately, worker started
        got = ""
        for _ in range(60):
            got = ic.getBwVariant(url, size)
            if got:
                break
            time.sleep(0.1)
        assert got and os.path.exists(got)
        px = Image.open(got).convert("RGB").getpixel((1, 1))
        assert abs(px[0] - px[1]) < 10 and abs(px[1] - px[2]) < 10   # grayscale
    finally:
        ic.getImagesDir = orig