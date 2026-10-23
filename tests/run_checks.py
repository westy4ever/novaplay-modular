#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NovaPlay checks -- run on a PC before pushing (not on the box):

    python3 tests/run_checks.py

1. every .py file compiles
2. every screen builds with fake Enigma2 modules, and
     - each widget the code creates is in its skin and the other way round
       (a missing one is a "skin error" crash on the box)
     - nothing in onLayoutFinish is a lambda / plain function (Enigma2
       exec()s those -> "exec() arg 1 must be a string" crash on open)
3. small logic tests (title year, runtime, torrent order, OSD times, ...)

Exit code 0 = all passed, 1 = something failed.
"""
from __future__ import print_function

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2stubs                                   # noqa: E402  (must come first)
from e2stubs import Dummy, widgets_of, REPO      # noqa: E402

FAILS = []


def fail(msg):
    FAILS.append(msg)
    print("  FAIL  " + msg)


def ok(msg):
    print("  ok    " + msg)


# ── 1. compile ───────────────────────────────────────────────────────────
def check_compile():
    print("\n[1] compile")
    n = 0
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                try:
                    with open(p, "rb") as fh:
                        compile(fh.read(), p, "exec")
                    n += 1
                except Exception as e:
                    fail("compile %s: %s" % (os.path.relpath(p, REPO), e))
    ok("%d files compile" % n)


# ── 2. screens ───────────────────────────────────────────────────────────
def _set_style(style):
    import plugin_state
    plugin_state._get_config = (lambda st: (lambda k, d="": st if k == "screen_style" else d))(style)


def check_screen(label, build):
    try:
        obj = build()
    except Exception as e:
        fail("%s: could not build (%r)" % (label, e))
        traceback.print_exc(limit=4)
        return
    skin = getattr(obj, "skin", None) or type(obj).skin
    w = widgets_of(skin)
    c = set(k for k in obj._hw if not k.lower().endswith("actions"))
    bad_layout = [f for f in obj.onLayoutFinish if not hasattr(f, "__self__")]
    problems = []
    if c - w:
        problems.append("in code, not in skin: %s" % sorted(c - w)[:8])
    if w - c:
        problems.append("in skin, not in code: %s" % sorted(w - c)[:8])
    if bad_layout:
        problems.append("%d non-method onLayoutFinish entries" % len(bad_layout))
    if problems:
        fail("%s: %s" % (label, "; ".join(problems)))
    else:
        ok("%-24s %d widgets" % (label, len(w)))


def check_screens():
    print("\n[2] screens")
    import importlib
    for style in ("classic", "modern"):
        _set_style(style)
        for mn in ("plugin_screen_site", "plugin_screen_detail"):
            if mn in sys.modules:
                importlib.reload(sys.modules[mn])
        import plugin_screen_site as S
        import plugin_screen_detail as D
        S._get_config = D._get_config = __import__("plugin_state")._get_config
        check_screen("Site/" + style, lambda: S.NovaPlaySite(Dummy(), "cinejoy"))
        check_screen("Detail/" + style, lambda: D.NovaPlayDetail(Dummy(), {"title": "X", "url": "u"}, "cinejoy"))
    import plugin_screen_hub as H
    import plugin_screen_settings as T
    import plugin_screen_search as Q
    import plugin_screen_downloads as W
    import plugin_screen_player as P
    check_screen("Hub", lambda: H.NovaPlayHub(Dummy()))
    check_screen("Settings", lambda: T.NovaPlaySettings(Dummy()))
    check_screen("Search", lambda: Q.NovaPlaySearch(Dummy()))
    check_screen("Downloads", lambda: W.NovaPlayDownloads(Dummy()))
    check_screen("Player", lambda: P.NovaPlaySimplePlayer(Dummy(), "T", [(4097, "http://x/a.mp4", "x", False)]))


# ── 3. logic ─────────────────────────────────────────────────────────────
def expect(label, got, want):
    if got == want:
        ok(label)
    else:
        fail("%s: got %r, want %r" % (label, got, want))


def check_logic():
    print("\n[3] logic")
    from plugin_util import _strip_shown_year as y
    expect("year: 'Gladiator II (2024)'", y("Gladiator II (2024)", "2024"), "Gladiator II")
    expect("year: Arabic title", y(u"فيلم الحريفة 2 2024", "2024"), u"فيلم الحريفة 2")
    expect("year: '1917' stays", y("1917", "2019"), "1917")
    expect("year: 'Blade Runner 2049' stays", y("Blade Runner 2049", "2017"), "Blade Runner 2049")
    expect("year: no badge -> unchanged", y("Inception 2010", ""), "Inception 2010")

    import plugin_screen_detail as D
    expect("runtime '2h 10m'", D._runtime_minutes("2h 10m"), 130)
    expect("runtime '02:10:00'", D._runtime_minutes("02:10:00"), 130)
    expect("runtime 130", D._runtime_minutes(130), 130)
    expect("initials", (D._initials("Tom Hardy"), D._initials("Zendaya")), ("TH", "Z"))

    D._get_config = lambda k, d="": "1080p" if k == "torrent_quality" else d
    srv = [{"url": "magnet:a", "quality": "2160p", "seeders": "300"},
           {"url": "magnet:b", "quality": "1080p", "seeders": "12"},
           {"url": "magnet:c", "quality": "1080p", "seeders": "240"},
           {"url": "https://embed", "quality": "HD"},
           {"url": "magnet:d", "quality": "720p", "seeders": "999"},
           {"url": "magnet:e", "quality": "1080p HDTS", "seeders": "5000"}]
    import inspect
    fake = type("F", (), {k: inspect.getattr_static(D.NovaPlayDetail, k) for k in
                          ("_torBucket", "_torSeeds", "_TOR_PREF", "_orderTorrents")})()
    expect("torrent order (1080p first, seeders, CAM last, embeds keep place)",
           [x["url"] for x in fake._orderTorrents(srv)],
           ["magnet:c", "magnet:b", "magnet:d", "https://embed", "magnet:a", "magnet:e"])

    import plugin_screen_player as P
    expect("OSD time 42:10", P.NovaPlaySimplePlayer._clockText(2530), "42:10")
    expect("OSD time 2:10:00", P.NovaPlaySimplePlayer._clockText(7800), "2:10:00")
    c = P._build_remote_play_candidates("http://127.0.0.1:8090/stream/M.mkv?link=abc&index=1&play")
    expect("TorrServer link: no proxy candidates", any(x[3] for x in c), False)

    import plugin_screen_hub as H
    expect("hub remaining", H._remaining({"last_position_sec": 600, "total_duration": 1800}),
           ("20 min left", 33))


def check_sites_offline():
    print("\n[4] sites, played back from recordings (tests/fixtures)")
    import test_sites_offline as T
    paths = T.fixtures()
    if not paths:
        ok("no recordings yet (run_site_test.py <site> --record on the box)")
        return
    for p in paths:
        try:
            good, lines = T.replay(p)
        except Exception as e:
            fail("%s: %r" % (os.path.basename(p), e))
            continue
        if good:
            ok(lines[0])
        else:
            fail(lines[0] + "\n        " + "\n        ".join(lines[1:]))


def main():
    check_compile()
    check_screens()
    check_logic()
    check_sites_offline()
    print("\n%s" % ("ALL PASSED" if not FAILS else "%d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
