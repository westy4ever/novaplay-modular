#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline site tests: play back runs recorded on the box.

Record on the box (real network):
    cd /usr/lib/enigma2/python/Plugins/Extensions/NovaPlay
    python3 run_site_test.py cinejoy --record
      -> /tmp/novaplay_fixtures/cinejoy.json.gz
Put that file in tests/fixtures/ in the repo.

Then, on a PC, without network (also run by tests/run_checks.py):
    python3 tests/test_sites_offline.py            # every fixture
    python3 tests/test_sites_offline.py cinejoy    # one

Each fixture is the site test run step by step (categories, items, search,
detail page, servers, extract_stream) with every page the extractor fetched.
Played back, the extractor gets the same pages, so the same steps must give
the same results. A difference means the extractor's code changed how it
reads those pages -- caught here instead of on the TV. (When the SITE
changes, record a new fixture.)
"""
from __future__ import print_function

import argparse
import gzip
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2stubs                                    # noqa: E402,F401
from e2stubs import REPO                          # noqa: E402

FIXTURES = os.path.join(REPO, "tests", "fixtures")


def _no_network(*a, **k):
    raise OSError("offline test: network is switched off (this request was not recorded)")


def replay(path):
    """(ok, message lines)"""
    import run_site_test as R
    import extractors.net as net
    with gzip.open(path, "rt") as f:
        fx = json.load(f)
    key = fx["site"]
    checks = fx.get("stream_checks") or {}
    real_check, real_conn = R.check_stream, socket.create_connection

    def check_stream(stream_url, referer=None):
        return tuple(checks.get("{} | {}".format(stream_url, referer or ""),
                                ("BAD", "stream check was not recorded")))

    a = fx.get("args") or {}
    args = argparse.Namespace(page=a.get("page"), search=a.get("search"),
                              servers=a.get("servers") or 5, all_servers=bool(a.get("all_servers")),
                              old_ua=False, record=False)
    R.check_stream = check_stream
    socket.create_connection = _no_network
    net.tape_start("replay", fx.get("responses") or {})
    try:
        name, ext = R.load_extractor(fx.get("target") or key)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):      # the step-by-step report stays quiet
            rep = R.test_site(name, ext, args)
        got = R.normalize_report(rep.lines)
    finally:
        net.tape_stop()
        R.check_stream = real_check
        socket.create_connection = real_conn
    want = fx.get("expected") or []
    msgs = []
    if got != want:
        for i in range(max(len(got), len(want))):
            g = got[i] if i < len(got) else "(nothing)"
            w = want[i] if i < len(want) else "(nothing)"
            if g != w:
                msgs.append("first difference at step line {}:".format(i + 1))
                msgs.append("  recorded: " + w)
                msgs.append("  now:      " + g)
                break
    missing = list(dict.fromkeys(net._TAPE["missing"]))
    if missing:
        msgs.append("{} request(s) not in the recording, e.g. {}".format(len(missing), missing[0][:120]))
    return got == want, ["{} (recorded {})".format(key, fx.get("recorded", "?"))] + msgs


def fixtures(only=None):
    if not os.path.isdir(FIXTURES):
        return []
    out = sorted(os.path.join(FIXTURES, f) for f in os.listdir(FIXTURES) if f.endswith(".json.gz"))
    if only:
        out = [p for p in out if os.path.basename(p).split(".")[0] in only]
    return out


def main(argv=None):
    only = set((argv if argv is not None else sys.argv[1:]))
    paths = fixtures(only)
    if not paths:
        print("no fixtures in tests/fixtures/ -- record one on the box with run_site_test.py <site> --record")
        return 0
    bad = 0
    for p in paths:
        ok, lines = replay(p)
        print(("  ok    " if ok else "  FAIL  ") + lines[0])
        for l in lines[1:]:
            print("        " + l)
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
