#!/usr/bin/env python3
"""Does NOVA's proxy serve byte ranges of a plain MP4 (needed to resume/seek)?

Run on the box from the NovaPlay folder:
    python3 tools/mp4_range_test.py [embed-url]
Starts its own copy of the proxy on port 19899 (so it doesn't matter whether
Enigma2's is running), resolves a Mixdrop link, then asks the proxy for the
file's start and for a slice from the middle, printing what comes back.
"""
import os
import sys
import threading
from urllib.parse import quote
import urllib.request as U

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import novaplay_proxy as P            # noqa: E402
from extractors import hosts          # noqa: E402

PORT = 19899
EMBED = sys.argv[1] if len(sys.argv) > 1 else "https://mixdrop.ps/e/vkk971jec3vk8x"


def ask(url, rng=None):
    h = {"User-Agent": "GStreamer souphttpsrc"}
    if rng:
        h["Range"] = rng
    try:
        r = U.urlopen(U.Request(url, headers=h), timeout=30)
    except Exception as e:
        r = e
    code = getattr(r, "status", None) or getattr(r, "code", "?")
    hdrs = getattr(r, "headers", None) or {}
    body = b""
    try:
        body = r.read(2048)
    except Exception:
        pass
    print("  HTTP {}".format(code))
    for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
        if hdrs.get(k):
            print("  {}: {}".format(k, hdrs.get(k)))
    print("  first bytes: {}".format(body[:12]))


srv = P.ThreadingProxyServer(("127.0.0.1", PORT), P.LocalProxyHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

s, _ = hosts.resolve_iframe_chain(EMBED, referer="https://topcinema.film/")
if not s:
    s = hosts.resolve_mixdrop(EMBED)
if not s:
    print("could not resolve", EMBED)
    sys.exit(1)
u = s.split("|")[0]
print("stream:", u[:110])
px = "http://127.0.0.1:{}/stream?url={}&referer={}&origin={}".format(
    PORT, quote(u, safe=""), quote("https://mxdrop.click/", safe=""),
    quote("https://mxdrop.click", safe=""))

print("== start of file, no Range")
ask(px)
print("== start of file, Range bytes=0-")
ask(px, "bytes=0-")
print("== middle of file, Range bytes=500000000-")
ask(px, "bytes=500000000-")
print("== proxy log:")
os.system("tail -6 /tmp/novaplay_proxy.log")
