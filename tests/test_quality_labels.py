# -*- coding: utf-8 -*-
"""Quality-label regression tests for PATCH 93 / 94 / 95 (headless, no network)."""

from extractors.htmlmedia import (find_m3u8, find_mp4_all, _correct_stream_url,
    _is_placeholder_media_url, _label_quality_variant as lv)
from extractors.hosts import (extract_stream, _quality_label_for as q,
    _looks_like_direct_media as direct)

# ---- existing repo tests (test_htmlmedia / test_hosts) ----
def test_existing_htmlmedia():
    assert _correct_stream_url("https://x/master.txt") == "https://x/master.m3u8"
    assert _correct_stream_url("https://x/seg.woff2") == "https://x/seg.ts"
    assert _is_placeholder_media_url("https://commondatastorage.googleapis.com/bbb.mp4")
    assert not _is_placeholder_media_url("https://realcdn.net/movie_2026.mp4")
    assert find_m3u8('file: "https://vjs.zencdn.net/bbb.mp4" file: "https://real.io/a.m3u8"') == "https://real.io/a.m3u8"
    assert lv("https://c/_,l,n,h,o,.urlset/v_h/seg.ts") == "720p"
    assert lv("https://c/idx-f2-master.m3u8") == "720p"
    assert lv("https://c/movie_the_hunt_1080.mp4") == "1080p"
    assert lv("https://c/the_hunt/master.m3u8") is None
    assert "https://x.io/v/bt/movie.mp4" in find_mp4_all('href="https://x.io/v/bt/movie.mp4"')

def test_existing_hosts():
    r = extract_stream("https://x.example-cdn.org/video_1080.mp4")
    assert isinstance(r, tuple) and len(r) == 4 and r[0].startswith("http") and r[1] == "1080p"

# ---- PATCH 93 ----
def test_p93_direct_media():
    assert direct("https://cdn.io/a/master.m3u8?t=1")
    assert direct("https://x.io/api?seg=index.m3u8")
    assert direct("https://h.io/seg_1.ts") and direct("https://h.io/master.txt")
    assert not direct("https://vidstream.tsvideo.com/e/abc")
    assert not direct("https://tsstream.to/e/abc")

def test_p93_wiring():
    assert extract_stream("https://hdcdn.example/v/movie.mp4")[1] == "HD"
    assert extract_stream("https://cdn.example/v/movie_720.mp4?t=abc")[1] == "720p"

def test_p93_labels():
    assert q("https://hdstream.example/e/abc") == "HD"
    assert q("https://c.scdns.io/hls/hd1080/index.m3u8") == "1080p"
    assert q("https://c.scdns.io/hls/hd720/index.m3u8") == "720p"
    assert q("https://cdn.x/v/index-f2-v1-a1.m3u8") == "720p"
    assert q("https://cdn.x/movie_the_hunt/master.m3u8") == "HD"

# ---- PATCH 94 ----
def test_p94_ignores_query_host_headers():
    assert lv("https://cdn.x/v/master.m3u8?t=Ab4kZq") is None
    assert lv("https://cdn.x/v/x/master.m3u8?t=aa_h/bb") is None
    assert lv("https://cdn.x/v/master.m3u8|Referer=https://y.example/1080/") is None
    assert lv("https://cdn.x/v/master.m3u8#User-Agent=Mozilla/5.0 (Win64; x64)&Referer=https://y/720/") is None

def test_p94_still_reads_path():
    assert lv("https://cdn.x/v/movie_4k.mp4") == "2160p"
    assert lv("https://cdn.x/v/movie_fhd.mp4") == "1080p"
    assert lv("https://cdn.x/v/movie_1080.mp4?t=abc") == "1080p"
    assert lv("https://c/_,l,n,h,o,.urlset/v_h/seg.ts?t=1") == "720p"

def test_p94_quality_label_for_ignores_query():
    assert q("https://cdn.x/e/abc?title=hd1080") == "HD"
    assert q("https://c.scdns.io/hls/hd1080/index.m3u8?x=1") == "1080p"

# ---- PATCH 95 ----
def test_p95_p_suffix():
    assert lv("https://cdn.x/v/movie.720p.mp4") == "720p"
    assert lv("https://cdn.x/v/1080p/index.m3u8") == "1080p"
    assert lv("https://cdn.x/v/movie_720p_x264.mp4") == "720p"
    assert lv("https://cdn.x/v/movie_1080.mp4") == "1080p"
    assert lv("https://cdn.x/v/movie1080p.mp4") is None
    assert q("https://cdn.x/v/Movie.2020.1080p.WEB-DL.mp4") == "1080p"

