# -*- coding: utf-8 -*-
"""Referer table: longest-key matching, self-referer, merge fidelity."""

from extractors.referers import get_referer


def test_longest_key_wins():
    assert get_referer("https://web5106x.faselhdx.bid/x.m3u8") == \
        "https://web5106x.faselhdx.bid/"

def test_plain_entries():
    assert get_referer("https://scdns.io/seg.ts") == "https://web5106x.faselhdx.bid/"
    assert get_referer("https://govid.live/e/abc") == "https://faselhd.rip/"
    assert get_referer("https://luluvdo.com/e/xyz") == "https://lulustream.com/"
    assert get_referer("https://sub.netrocdn.site/hls/x.m3u8") == "https://moviesapi.to/"

def test_self_referer_default():
    assert get_referer("https://random-cdn.org/file.ts") == "https://random-cdn.org/"

def test_none_entries_self_refer():
    assert get_referer("https://wt4x.dramiyos-cdn.com/hls/m.m3u8") == \
        "https://wt4x.dramiyos-cdn.com/"

def test_no_default_self():
    assert get_referer("https://random-cdn.org/f", default_self=False) == ""

def test_garbage_input():
    get_referer("")     # must not raise
    get_referer(None)   # must not raise
    get_referer("not a url at all")  # must not raise

def test_non_str_input_returns_empty():
    assert get_referer(None) == ""
    assert get_referer(None, default_self=False) == ""
    assert get_referer(b"https://govid.live/e/x") == "https://faselhd.rip/"