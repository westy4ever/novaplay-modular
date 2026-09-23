#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NovaPlay release gate: syntax + tests + the accumulated fix
fingerprints. Run before any restart-worthy change:

    python3 run_release_gate.py           # checks
    python3 run_release_gate.py --snapshot  # checks + backup copy
"""

import os
import py_compile
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

PY_FILES = [f for f in os.listdir(HERE)
            if f.endswith(".py") and not f.startswith("run_")]
EXTRACTOR_FILES = [os.path.join("extractors", f)
                   for f in os.listdir(os.path.join(HERE, "extractors"))
                   if f.endswith(".py")]

FINGERPRINTS = [
    ("novaplay_proxy.py", '"127.0.0.1", _PROXY_PORT', "loopback-only proxy bind"),
    ("plugin_downloads.py", "io.BytesIO", "buffered segment retry (corruption fix)"),
    ("plugin_downloads.py", "text/html", "HTML-as-video sniff"),
    ("plugin_downloads.py", "EXT-X-ENDLIST", "live-HLS refusal"),
    ("plugin_tmdb.py", "not cached (plot=", "weak-match cache gate"),
    ("plugin_tmdb.py", "os.rename(tmp, cache_path)", "atomic TMDB cache write"),
    ("plugin_util.py", "plugin_imagecache.getCachedImage", "shared poster cache bridge"),
    ("novaplay_thread.py", "callback crashed", "dispatcher crash logging"),
    ("novaplay_subtitles.py", "returned no downloadable file", "JSON-as-SRT guard"),
    ("novaplay_subtitles.py", "def _dirCb(self, value=None)", "folder-callback tolerance"),
    ("plugin_screen_player.py", "path=None)", "font-callback tolerance"),
    ("extractors/registry.py", "_EXTRACTOR_INSTANCES", "extractor singletons"),
    ("novaplay_substudio.py", "def parse_srt", "studio parser"),
]


def gate_syntax():
    fails = []
    for f in PY_FILES + EXTRACTOR_FILES:
        try:
            py_compile.compile(os.path.join(HERE, f), doraise=True)
        except Exception as e:
            fails.append("{}: {}".format(f, e))
    return fails


def gate_fingerprints():
    missing = []
    for fname, needle, desc in FINGERPRINTS:
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            missing.append("{} — FILE MISSING ({})".format(fname, desc))
            continue
        with open(path, "r", errors="ignore") as fh:
            body = fh.read()
        if needle not in body:
            missing.append("{} missing: {} (fix: {})".format(fname, needle, desc))
    return missing


def gate_tests():
    r = subprocess.call([sys.executable, os.path.join(HERE, "run_tests.py")])
    return [] if r == 0 else ["test suite reported failures"]


def snapshot():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(os.path.dirname(HERE), "AdvancedArabicPlayer.bak-" + stamp)
    shutil.copytree(HERE, dest)
    print("snapshot: {}".format(dest))
    return dest


def main():
    if "--snapshot" in sys.argv:
        snapshot()
    ok = True
    for name, gate in (("SYNTAX", gate_syntax),
                       ("FINGERPRINTS", gate_fingerprints),
                       ("TESTS", gate_tests)):
        print("=== {} ===".format(name))
        fails = gate()
        if fails:
            ok = False
            for f in fails:
                print("  FAIL " + f)
        else:
            print("  ok")
    print("=" * 40)
    print("RELEASE GATE: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())