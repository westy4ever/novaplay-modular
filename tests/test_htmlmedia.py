# -*- coding: utf-8 -*-
"""Media-URL finders: placeholder blocklist, disguised extensions,
quality-variant labeling (the pinned-regex fix)."""

from extractors.htmlmedia import (
    find_m3u8, find_m3u8_all, find_mp4_all, _correct_stream_url,
    _is_placeholder_media_url, _label_quality_variant)


def test_disguised_extensions():
    assert _correct_stream_url("https://x/master.txt") == "https://x/master.m3u8"
    assert _correct_stream_url("https://x/seg.woff2") == "https://x/seg.ts"
    assert _correct_stream_url("https://x/plain.m3u8") == "https://x/plain.m3u8"

def test_placeholder_blocklist():
    assert _is_placeholder_media_url("https://commondatastorage.googleapis.com/bbb.mp4")
    assert _is_placeholder_media_url("https://cdn/big_buck_bunny_720.mp4")
    assert not _is_placeholder_media_url("https://realcdn.net/movie_2026.mp4")

def test_find_m3u8_skips_placeholders():
    html = 'file: "https://vjs.zencdn.net/bbb.mp4" file: "https://real.io/a.m3u8"'
    assert find_m3u8(html) == "https://real.io/a.m3u8"

def test_find_m3u8_unescapes():
    html = 'source: "https:\\/\\/cdn.io\\/pl\\/x.m3u8?t=1"'
    assert find_m3u8(html).startswith("https://cdn.io/pl/x.m3u8")

def test_quality_suffix_pinned_regex():
    assert _label_quality_variant("https://c/_,l,n,h,o,.urlset/v_h/seg.ts") == "720p"
    assert _label_quality_variant("https://c/idx-f2-master.m3u8") == "720p"
    assert _label_quality_variant("https://c/movie_the_hunt_1080.mp4") == "1080p"
    # '_h' embedded in 'the_hunt' must NOT be labeled 720p:
    assert _label_quality_variant("https://c/the_hunt/master.m3u8") is None

def test_find_mp4_basic():
    urls = find_mp4_all('href="https://x.io/v/bt/movie.mp4"')
    assert "https://x.io/v/bt/movie.mp4" in urls