# -*- coding: utf-8 -*-
"""plugin_screen_home.py regression test: continue-strip hero backdrop TMDB fallback
(PATCH G5). Extracts and executes the real _paintContinueBackdrop / _bgFetchContinueBackdrop
/ _paintContinueTmdbBackdrop methods directly against stubbed dependencies (TMDB lookup,
image cache, threading), avoiding the full Enigma2 import chain. callInMainThread runs
synchronously here so the fetch's effects are observable without real threading."""

import os
import re


def _load_methods():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin_screen_home.py")
    if not os.path.exists(path):
        path = "plugin_screen_home.py"
    src = open(path, encoding="utf-8").read()
    start = src.find("    # \u2500\u2500 [UX-3] hero fanart backdrop")
    end = src.find("    def _paintContinueTmdbBackdrop")
    end = src.find("\n\n", end) + 1
    return src[start:end]


class _FakeWidget(object):
    def __init__(self):
        self.shown = False
        self.instance = self
        self.last_path = None

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False

    def setPixmapFromFile(self, p):
        self.last_path = p


def my_log(msg):
    pass


class _FakeImageCache(object):
    def __init__(self, precached=True):
        self.precached = precached

    def getCachedImage(self, url, target_size=None):
        return "/cache/bd.jpg" if self.precached else None

    def requestImageAsyncPriority(self, *a, **k):
        pass

    def downloadUrl(self, *a, **k):
        return b"data"

    def resizeCover(self, *a, **k):
        return b"processed"

    def buildCachePath(self, *a, **k):
        return "/cache/bd.jpg"

    def writeFileAtomic(self, *a, **k):
        return True


def _make_fake_screen(tmdb_result, run_thread_sync=True):
    import threading as _threading_mod

    calls = {"tmdb": [], "threads_started": 0}

    def _tmdb_search_metadata(title, year, type_):
        calls["tmdb"].append((title, year, type_))
        return tmdb_result

    def callInMainThread(fn, *args):
        fn(*args)

    class _SyncThread(object):
        def __init__(self, target, args, daemon=True):
            self._target = target
            self._args = args
            calls["threads_started"] += 1

        def start(self):
            if run_thread_sync:
                self._target(*self._args)

    class _FakeScreen(object):
        def __init__(self):
            self._cont_index = 0
            self._cont_items = []
            self._cont_backdrop_token = 0
            self._current_backdrop_path = ""
            self._widgets = {"backdropImg": _FakeWidget(), "shade_overlay": _FakeWidget()}
            self._plugin_imagecache = _FakeImageCache()

        def __getitem__(self, key):
            return self._widgets[key]

    src = _load_methods()
    ns = {
        "my_log": my_log,
        "_tmdb_search_metadata": _tmdb_search_metadata,
        "callInMainThread": callInMainThread,
        "threading": type("T", (), {"Thread": _SyncThread}),
    }
    exec("class _M(object):\n" + src, ns)
    _M = ns["_M"]
    for name in ("_paintContinueBackdrop", "_bgFetchContinueBackdrop", "_paintContinueTmdbBackdrop"):
        setattr(_FakeScreen, name, getattr(_M, name))

    screen = _FakeScreen()
    screen.plugin_imagecache = _FakeImageCache()
    # inject plugin_imagecache into the exec namespace so the bound methods can see it
    ns["plugin_imagecache"] = screen.plugin_imagecache
    for name in ("_paintContinueBackdrop", "_bgFetchContinueBackdrop", "_paintContinueTmdbBackdrop"):
        fn = getattr(_M, name)
        fn.__globals__.update(ns)

    return screen, calls


def test_no_backdrop_url_triggers_one_background_fetch():
    screen, calls = _make_fake_screen({"backdrop_url": "https://x/bd.jpg"})
    screen._cont_items = [{"title": "Show", "year": "2026", "type": "movie"}]
    screen._paintContinueBackdrop()
    assert calls["threads_started"] == 1, calls
    assert screen._cont_items[0]["_backdrop_fetch_tried"] is True


def test_fetch_not_retried_once_already_tried():
    screen, calls = _make_fake_screen({"backdrop_url": "https://x/bd.jpg"})
    screen._cont_items = [{"title": "Show", "_backdrop_fetch_tried": True}]
    screen._paintContinueBackdrop()
    assert calls["threads_started"] == 0, calls


def test_existing_fanart_skips_fetch_entirely():
    screen, calls = _make_fake_screen({"backdrop_url": "https://x/bd.jpg"})
    screen._cont_items = [{"title": "Show", "fanart": "https://existing/already.jpg"}]
    screen._paintContinueBackdrop()
    assert calls["threads_started"] == 0, calls
    assert calls["tmdb"] == []


def test_successful_fetch_paints_the_backdrop():
    screen, calls = _make_fake_screen({"backdrop_url": "https://x/bd.jpg"})
    screen._cont_items = [{"title": "Show", "year": "2026", "type": "movie"}]
    screen._paintContinueBackdrop()
    assert screen._widgets["backdropImg"].shown is True
    assert screen._widgets["backdropImg"].last_path == "/cache/bd.jpg"
    assert screen._current_backdrop_path == "/cache/bd.jpg"


def test_stale_token_does_not_paint():
    screen, calls = _make_fake_screen({"backdrop_url": "https://x/bd.jpg"}, run_thread_sync=False)
    screen._cont_items = [{"title": "Show", "year": "2026", "type": "movie"}]
    screen._paintContinueBackdrop()
    stale_token = screen._cont_backdrop_token
    # simulate the user moving to a different card before the fetch would complete
    screen._cont_backdrop_token += 1
    screen._paintContinueTmdbBackdrop("/cache/bd.jpg", 0, stale_token)
    assert screen._widgets["backdropImg"].shown is False


def test_moved_to_different_card_does_not_paint():
    screen, calls = _make_fake_screen({"backdrop_url": "https://x/bd.jpg"}, run_thread_sync=False)
    screen._cont_items = [{"title": "A"}, {"title": "B"}]
    screen._paintContinueBackdrop()
    token = screen._cont_backdrop_token
    screen._cont_index = 1  # user arrowed to the next card
    screen._paintContinueTmdbBackdrop("/cache/bd.jpg", 0, token)
    assert screen._widgets["backdropImg"].shown is False


def test_tmdb_returning_nothing_does_not_crash_or_paint():
    screen, calls = _make_fake_screen(None)
    screen._cont_items = [{"title": "Show", "year": "2026", "type": "movie"}]
    screen._paintContinueBackdrop()
    assert screen._widgets["backdropImg"].shown is False
