# -*- coding: utf-8 -*-
"""Regression tests for PATCH 63 / 65 / 67."""


def test_manifest_keeps_attrs_after_uri():
    from novaplay_proxy import _rewrite_manifest
    m = '#EXT-X-KEY:METHOD=AES-128,URI="k.key",IV=0x1234\nseg.ts\n'
    out = _rewrite_manifest(m, "https://a.example/x/p.m3u8", "", "", "").decode()
    assert "IV=0x1234" in out
    assert "127.0.0.1" in out


def test_egydead_resolvers_bound():
    from extractors import egydead
    assert egydead.resolve_streamruby is not None
    assert egydead.resolve_mixdrop is not None
    assert egydead.resolve_voe is not None


def test_studio_positive_offset_delays_cues():
    from novaplay_substudio import SubtitleStudio
    s = SubtitleStudio()
    s._screen = object()
    s._cues = [(1000, 2000, ["hi"], {}, False)]
    s._starts = [1000]
    rendered = []
    s._render_cue = lambda body, pos=None, italic=False: rendered.append(body)
    s._hide_lines = lambda: None
    s.set_offset(500)          # +500 = cues appear 500ms LATER
    s.update(1200)             # would be visible with no offset; must not be now
    assert rendered == []
    s.update(1600)
    assert rendered