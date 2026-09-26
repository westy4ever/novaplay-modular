# -*- coding: utf-8 -*-
"""plugin_screen_home.py regression test: home-grid health/recency sort (PATCH G4) and the
7-column centering constants (PATCH G3). Extracts and executes the real sort block directly
against synthetic inputs, avoiding the full Enigma2 import chain."""

import re
import os


def _load_sort_block():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin_screen_home.py")
    if not os.path.exists(path):
        path = "plugin_screen_home.py"
    src = open(path, encoding="utf-8").read()
    m = re.search(r'(_HEALTH_SORT_RANK = .*?)\n\n        site_items = \[\]', src, re.S)
    return m.group(1)


def _run_sort(raw_sites, health_map, hidden_keys, recent_map):
    block = _load_sort_block()
    # de-indent (block is captured with method-body indentation) and provide the closures
    # the real code expects as free variables: plugin_health.get, _is_site_hidden, recent_sites
    lines = block.split("\n")
    dedented = "\n".join(l[8:] if l.startswith("        ") else l for l in lines)

    class _FakeHealth:
        def get(self, key):
            return health_map.get(key, "unknown")

    plugin_health = _FakeHealth()

    def _is_site_hidden(key):
        return key in hidden_keys

    recent_sites = recent_map
    ns = dict(locals())
    exec(dedented, ns)
    return ns["visible_sites"]


def test_ok_beats_blocked_beats_down():
    raw = [("a", "A", ""), ("b", "B", ""), ("c", "C", "")]
    health = {"a": "down", "b": "ok", "c": "blocked"}
    result = _run_sort(raw, health, set(), {})
    assert [r[0] for r in result] == ["b", "c", "a"], result


def test_recency_breaks_ties_within_same_health_rank():
    raw = [("a", "A", ""), ("b", "B", ""), ("c", "C", "")]
    health = {"a": "ok", "b": "ok", "c": "ok"}
    recent = {"a": 1, "b": 5, "c": 0}
    result = _run_sort(raw, health, set(), recent)
    assert [r[0] for r in result] == ["b", "a", "c"], result


def test_unknown_ranks_with_ok_not_separately():
    raw = [("a", "A", ""), ("b", "B", ""), ("c", "C", "")]
    health = {"a": "unknown", "b": "blocked", "c": "ok"}
    result = _run_sort(raw, health, set(), {})
    # a (unknown) and c (ok) share rank 0; b (blocked) sinks below both
    assert result[-1][0] == "b", result
    assert set(r[0] for r in result[:2]) == {"a", "c"}, result


def test_hidden_sites_excluded_entirely():
    raw = [("a", "A", ""), ("b", "B", ""), ("c", "C", "")]
    result = _run_sort(raw, {}, {"b"}, {})
    assert [r[0] for r in result] == ["a", "c"], result


def test_stable_sort_preserves_registry_order_when_all_equal():
    raw = [("a", "A", ""), ("b", "B", ""), ("c", "C", ""), ("d", "D", "")]
    result = _run_sort(raw, {}, set(), {})
    assert [r[0] for r in result] == ["a", "b", "c", "d"], result


# ── PATCH G3: 7-column centering constants ──────────────────────────────
def _load_grid_constants():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin_gridlist.py")
    if not os.path.exists(path):
        path = "plugin_gridlist.py"
    src = open(path, encoding="utf-8").read()
    m = re.search(r'^HOME_GRID_COLS = .*?HOME_CENTER_OFFSET_X = .*?$', src, re.S | re.M)
    ns = {"sc": lambda v: int(round(v * 1.0))}  # standard 1920-wide screen, _SCALE=1.0
    exec(m.group(0), ns)
    return ns


def test_seven_columns_fit_within_grid_width_with_positive_offset():
    ns = _load_grid_constants()
    assert ns["HOME_GRID_COLS"] == 7, ns["HOME_GRID_COLS"]
    assert ns["HOME_CELL_W"] == 260, ns["HOME_CELL_W"]
    block_w = ns["HOME_GRID_COLS"] * ns["HOME_CELL_W"]
    assert block_w == 1820, block_w
    assert ns["HOME_CENTER_OFFSET_X"] == 30, ns["HOME_CENTER_OFFSET_X"]
    rightmost = ns["HOME_CENTER_OFFSET_X"] + block_w
    assert rightmost <= ns["HOME_GRID_WIDTH"], (rightmost, ns["HOME_GRID_WIDTH"])
