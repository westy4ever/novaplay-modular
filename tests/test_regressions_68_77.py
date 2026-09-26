# -*- coding: utf-8 -*-
"""Regression tests for PATCH 68 / 70 / 71 / 75 / 77 (headless)."""

import os
import tempfile


class _NoWidgets(object):
    def __getitem__(self, k):
        raise KeyError(k)


def test_studio_bind_resets_previous_title():
    from novaplay_substudio import SubtitleStudio
    s = SubtitleStudio()
    s._cues = [(0, 1000, ["old"], {}, False)]
    s._starts = [0]
    s._offset_ms = 250
    s.bind(_NoWidgets())
    assert s._cues == [] and s._offset_ms == 0


def test_studio_use_default_font():
    from novaplay_substudio import SubtitleStudio
    s = SubtitleStudio()
    s.style["font_name"] = "X"
    s.style["font_path"] = "/x.ttf"
    s.use_default_font()
    assert s.style["font_name"] == "Regular" and s.style["font_path"] == ""


def test_direct_download_flags_hls_manifest():
    import plugin_downloads as pd

    class _Resp(object):
        headers = {"Content-Type": "application/octet-stream"}

        def __init__(self):
            self._d = [b"#EXTM3U\n#EXT-X-VERSION:3\n"]

        def read(self, n):
            return self._d.pop(0) if self._d else b""

        def close(self):
            pass

    orig = pd._open_request
    pd._open_request = lambda *a, **k: _Resp()
    try:
        dest = os.path.join(tempfile.mkdtemp(), "v.mp4")
        t = pd.DownloadTask(title="t", url="https://x/api?seg=index")
        raised = False
        try:
            pd.download_direct(t, dest)
        except pd._LooksLikeHls:
            raised = True
        assert raised
        assert not os.path.exists(dest + ".part")
    finally:
        pd._open_request = orig


def test_shaheed_normal_page_not_blocked():
    from extractors.shaheed import ShaheedExtractor
    ex = ShaheedExtractor()
    normal = ("<html><script src='https://cdnjs.cloudflare.com/x.js'></script>"
              "<script src='https://www.google.com/recaptcha/api.js'></script>"
              + "x" * 6000 + "</html>")
    assert not ex._is_blocked_page(normal)
    assert ex._is_blocked_page("<title>Just a moment...</title>" + "x" * 100)


def test_akwam_nav_link_keeps_details():
    from extractors.akwam import AkwamExtractor
    ex = AkwamExtractor()
    assert ex._is_nav_link("/series?section=30&category=0")
    assert ex._is_nav_link("/recent")
    assert ex._is_nav_link("/")
    assert not ex._is_nav_link("https://akwam.to/series/123/some-show")
    assert not ex._is_nav_link("/movie/456/some-film")


def test_wecima_sarl_next_page_is_real():
    from extractors.wecima_sarl import WecimaSarlExtractor
    ex = WecimaSarlExtractor()
    base = "https://wecima.sarl/category/x/"
    html = ('<div class="pagination"><ul>'
            '<li><a class="page-numbers" href="{b}page/1/">1</a></li>'
            '<li><span>2</span></li>'
            '<li><a class="page-numbers" href="{b}page/3/">3</a></li>'
            '</ul></div>').format(b=base)
    assert ex._extract_next_page_sarl(html, base + "page/2/") == base + "page/3/"
    last = ('<div class="pagination"><ul>'
            '<li><a class="page-numbers" href="{b}page/1/">1</a></li>'
            '<li><a class="page-numbers" href="{b}page/2/">2</a></li>'
            '<li><span>3</span></li></ul></div>').format(b=base)
    assert ex._extract_next_page_sarl(last, base + "page/3/") == ""