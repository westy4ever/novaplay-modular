# -*- coding: utf-8 -*-
"""Read-only state + registry tests. NOTE: nothing here writes state —
tests inject _STATE_CACHE directly (the in-memory cache the savers use)."""

import time
import plugin_state
from extractors.registry import get_extractor, get_home_sites, _SITE_REGISTRY


def test_singleton_identity():
    a = get_extractor("topcinema")
    b = get_extractor("topcinema")
    assert a is b
    # egydead fallback shares ONE instance:
    c = get_extractor("nonexistent-site-xyz")
    d = get_extractor("egydead")
    assert c is d

def test_home_sites_complete_and_unique():
    sites = get_home_sites()
    assert len(sites) == len(_SITE_REGISTRY)
    keys = [s[0] for s in sites]
    assert len(set(keys)) == len(keys)

def test_registry_has_expected_sites():
    for key in ("egydead", "egybest", "wecima", "onlyflix", "imdb_su"):
        assert key in _SITE_REGISTRY, "missing site: {}".format(key)

def test_home_sites_order_stable():
    assert [s[0] for s in get_home_sites()] == [s[0] for s in get_home_sites()]

def test_continue_items_filter_and_order():
    plugin_state._STATE_CACHE = {
        "history": [
            {"url": "u1", "title": "A", "poster": "p", "last_position_sec": 700,
             "_pos_ts": 100, "_saved_at": 100},
            {"url": "u2", "title": "B", "poster": "p", "last_position_sec": 45,
             "_pos_ts": 200, "_saved_at": 200},
            {"url": "u3", "title": "C", "poster": "p", "last_position_sec": 0,
             "_pos_ts": 300, "_saved_at": 300},
            {"url": "u4", "title": "D", "poster": "p", "last_position_sec": 300,
             "_pos_ts": 50, "_saved_at": 50},
        ]
    }
    items = plugin_state._continue_items(5)
    assert [i["url"] for i in items] == ["u1", "u4"]

def test_continue_items_dedupe_by_url():
    plugin_state._STATE_CACHE = {"history": [
        {"url": "dupe", "title": "X", "poster": "p", "last_position_sec": 100,
         "_pos_ts": 10, "_saved_at": 10},
        {"url": "dupe", "title": "X", "poster": "p", "last_position_sec": 200,
         "_pos_ts": 20, "_saved_at": 20},
    ]}
    assert len(plugin_state._continue_items(5)) == 1