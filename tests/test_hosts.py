# -*- coding: utf-8 -*-
"""Host dispatch: ordered (longest-key-first) matching — the
down.vidtube.one regression — and table integrity."""

from extractors.hosts import HOST_RESOLVERS, _ORDERED_RESOLVER_KEYS, extract_stream


def test_ordered_keys_exist_and_sorted():
    assert len(_ORDERED_RESOLVER_KEYS) == len(HOST_RESOLVERS)
    lens = [len(k) for k, _ in _ORDERED_RESOLVER_KEYS]
    assert lens == sorted(lens, reverse=True)

def test_vidtube_one_reaches_correct_resolver():
    domain = "down.vidtube.one"
    matched = None
    for key, resolver in _ORDERED_RESOLVER_KEYS:
        if key in domain:
            matched = (key, resolver)
            break
    assert matched is not None
    assert matched[0] == "vidtube.one"

def test_dispatcher_is_first_match():
    domain = "c.scdns.io"
    first = None
    for key, resolver in _ORDERED_RESOLVER_KEYS:
        if key in domain:
            first = key
            break
    assert first == "c.scdns.io"

def test_extract_stream_direct_returns_4tuple():
    # Direct-URL branch: no network, immediate return
    r = extract_stream("https://x.example-cdn.org/video_1080.mp4")
    assert isinstance(r, tuple) and len(r) == 4
    assert r[0].startswith("http")
    assert r[1] == "1080p"

def test_no_fstring_log_crashes_import():
    # py2-compat regression: importing hosts.py must never crash
    import extractors.hosts as h
    assert callable(h.resolve_host)