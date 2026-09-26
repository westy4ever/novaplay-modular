# -*- coding: utf-8 -*-
"""plugin_gridlist.py regression test: moveLeft/moveRight page-change vs wrap-within-row
priority (PATCH G1). Extracts and executes the real moveLeft/moveRight method source directly
against a minimal test double, avoiding the full Enigma2 import chain (GUIComponent, enigma,
plugin_util, etc.) which isn't available off-box. This tests the actual shipped code, not a
reimplementation of its logic."""

import re
import os


def _load_move_methods():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin_gridlist.py")
    if not os.path.exists(path):
        path = "plugin_gridlist.py"
    src = open(path, encoding="utf-8").read()
    m = re.search(r'(    def moveLeft\(self\):.*?)    def setList', src, re.S)
    methods_block = m.group(1)
    ns = {}
    exec("class _M:\n" + methods_block, ns)
    return ns["_M"].moveLeft, ns["_M"].moveRight


class _TestGrid:
    def __init__(self, currentCol, currentRow, currentPage, totalPages, wrap_nav=True, max_col=5, max_row=1):
        self.currentCol = currentCol
        self.currentRow = currentRow
        self.currentPage = currentPage
        self.totalPages = totalPages
        self.wrap_nav = wrap_nav
        self._max_col = max_col
        self._max_row = max_row

    def _getMaxCol(self, row):
        return self._max_col

    def _getMaxRow(self):
        return self._max_row

    def _updateIndex(self):
        pass

    def _redraw(self):
        pass

    def _notify(self):
        pass


_moveLeft, _moveRight = _load_move_methods()
_TestGrid.moveLeft = _moveLeft
_TestGrid.moveRight = _moveRight


def test_right_at_row_end_advances_to_next_page_when_one_exists():
    g = _TestGrid(currentCol=5, currentRow=0, currentPage=0, totalPages=3)
    g.moveRight()
    assert (g.currentCol, g.currentPage) == (0, 1), (g.currentCol, g.currentPage)


def test_right_at_row_end_wraps_within_row_on_last_page():
    g = _TestGrid(currentCol=5, currentRow=0, currentPage=2, totalPages=3)
    g.moveRight()
    assert (g.currentCol, g.currentPage) == (0, 2), (g.currentCol, g.currentPage)


def test_left_at_col_zero_goes_to_previous_page_when_one_exists():
    g = _TestGrid(currentCol=0, currentRow=0, currentPage=1, totalPages=3)
    g.moveLeft()
    assert (g.currentCol, g.currentPage) == (5, 0), (g.currentCol, g.currentPage)


def test_left_at_col_zero_wraps_within_row_on_first_page():
    g = _TestGrid(currentCol=0, currentRow=0, currentPage=0, totalPages=3)
    g.moveLeft()
    assert (g.currentCol, g.currentPage) == (5, 0), (g.currentCol, g.currentPage)


def test_single_page_grid_still_wraps_within_row_both_directions():
    g = _TestGrid(currentCol=5, currentRow=0, currentPage=0, totalPages=1)
    g.moveRight()
    assert (g.currentCol, g.currentPage) == (0, 0), (g.currentCol, g.currentPage)

    g2 = _TestGrid(currentCol=0, currentRow=0, currentPage=0, totalPages=1)
    g2.moveLeft()
    assert (g2.currentCol, g2.currentPage) == (5, 0), (g2.currentCol, g2.currentPage)


def test_wrap_nav_disabled_does_not_wrap_or_crash_on_single_page():
    g = _TestGrid(currentCol=5, currentRow=0, currentPage=0, totalPages=1, wrap_nav=False)
    g.moveRight()
    assert (g.currentCol, g.currentPage) == (5, 0), (g.currentCol, g.currentPage)


def test_middle_of_row_still_moves_normally():
    g = _TestGrid(currentCol=2, currentRow=0, currentPage=0, totalPages=1)
    g.moveRight()
    assert g.currentCol == 3
    g.moveLeft()
    assert g.currentCol == 2


# ── MemoizedPixmapCache regression tests (PATCH G2) ──────────────────────
def _load_pixmap_cache():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin_gridlist.py")
    if not os.path.exists(path):
        path = "plugin_gridlist.py"
    src = open(path, encoding="utf-8").read()
    start = src.find("class MemoizedPixmapCache")
    end = src.find("\ndef build_pixmap_widgets_xml")
    block = src[start:end]
    ns = {}
    exec(block, ns)
    return ns["MemoizedPixmapCache"]


class _FakeInst:
    def __init__(self):
        self.calls = []

    def setPixmapFromFile(self, path):
        self.calls.append(path)

    def setScale(self, s):
        pass


class _NotReadyWidget:
    instance = None


class _ReadyWidget:
    def __init__(self, inst):
        self.instance = inst


def test_widget_not_ready_does_not_poison_the_cache():
    Cache = _load_pixmap_cache()
    c = Cache()
    c.set(_NotReadyWidget(), "poster1", "/a.png")
    assert c.get("poster1") is None, c.get("poster1")


def test_widget_becomes_ready_later_still_paints_the_pixmap():
    Cache = _load_pixmap_cache()
    c = Cache()
    c.set(_NotReadyWidget(), "poster1", "/a.png")
    inst = _FakeInst()
    result = c.set(_ReadyWidget(inst), "poster1", "/a.png")
    assert result is True, result
    assert inst.calls == ["/a.png"], inst.calls


def test_real_memoization_still_skips_unchanged_path_when_ready():
    Cache = _load_pixmap_cache()
    c = Cache()
    inst = _FakeInst()
    w = _ReadyWidget(inst)
    c.set(w, "poster1", "/a.png")
    result = c.set(w, "poster1", "/a.png")
    assert result is False, result
    assert inst.calls == ["/a.png"], inst.calls


def test_changed_path_repaints_even_after_previous_success():
    Cache = _load_pixmap_cache()
    c = Cache()
    inst = _FakeInst()
    w = _ReadyWidget(inst)
    c.set(w, "poster1", "/a.png")
    result = c.set(w, "poster1", "/b.png")
    assert result is True, result
    assert inst.calls == ["/a.png", "/b.png"], inst.calls


def test_setscaled_has_the_same_not_ready_fix():
    Cache = _load_pixmap_cache()
    c = Cache()
    c.setScaled(_NotReadyWidget(), "cont0", "/a.png")
    assert c.get("cont0") is None, c.get("cont0")
    inst = _FakeInst()
    result = c.setScaled(_ReadyWidget(inst), "cont0", "/a.png")
    assert result is True, result
    assert inst.calls == ["/a.png"], inst.calls
