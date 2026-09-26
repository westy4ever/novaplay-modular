# -*- coding: utf-8 -*-
"""Pure (network-free) download-engine invariants."""

from plugin_downloads import safe_filename, DownloadTask


def test_safe_filename_sanitizes():
    assert "/" not in safe_filename('a/b:c*d?"e|f', "mp4")
    assert safe_filename("movie", "mp4").endswith(".mp4")
    assert safe_filename("   ", "mp4").startswith("video")
    assert len(safe_filename("x" * 500, "mp4")) <= 125

def test_task_progress_bounds():
    t = DownloadTask(title="t", url="https://x/y.mp4")
    t.bytes_total = 1000
    t.bytes_done = 500
    assert abs(t.progress_pct() - 50.0) < 0.01
    t.bytes_done = 5000
    assert t.progress_pct() == 100.0
    assert DownloadTask(title="t", url="u").progress_pct() == 0.0

def test_task_carries_cookie_field():
    t = DownloadTask(title="t", url="u", referer="r", item_url="i", cookie="ck=1")
    assert t.cookie == "ck=1"