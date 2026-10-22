#!/usr/bin/env python3
"""Test every mirror of a MegaMax iframe (EgyDead / ArabSeed) on the box.

Run from the NovaPlay folder:
    python3 tools/megamax_test.py [megamax-iframe-url]
Lists the MegaMax mirrors (quality x server) the way EgyDead does, resolves
each one through EgyDead's extract_stream and fetches the first bytes of the
stream like the player would. Doesn't need EgyDead's own pages, so it works
while EgyDead is behind a Cloudflare challenge.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from extractors import egydead   # noqa: E402
import run_site_test as rst      # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://q7v3k8m2p1.top/iframe/UdfowbGyvZ8AM"
E = egydead.EgyDeadExtractor()
E._get_base = lambda: "https://tv10.egydead.live/"   # skip the (Cloudflare-blocked) mirror probe

print("MegaMax iframe:", URL)
servers = E._megamax_servers(URL)
print("{} mirror(s)\n".format(len(servers)))
ok = 0
for i, s in enumerate(servers, 1):
    t0 = time.time()
    try:
        res = E.extract_stream(s["url"])
    except Exception as e:
        res = (None, "", "", [])
        print("  error:", e)
    stream = res[0] if res else None
    if not stream:
        print("[BAD ] {:>2}. {}  ({:.1f}s)\n         {}\n         no stream".format(i, s["name"], time.time() - t0, s["url"][:90]))
        continue
    try:
        st, detail = rst.check_stream(stream, res[2] if len(res) > 2 else None)
    except Exception as e:
        st, detail = False, "check failed: {}".format(e)
    good = st == "PASS"
    ok += good
    print("[{}] {:>2}. {}  ({:.1f}s)\n         {}\n         {}".format(
        "PASS" if good else ("WARN" if st == "WARN" else "BAD "), i, s["name"], time.time() - t0, stream.split("|")[0][:100], detail))
print("\n{} of {} mirrors passed".format(ok, len(servers)))
