#!/usr/bin/env python3
"""Can ffmpeg (exteplayer3's engine) read Mixdrop's file smoothly? (stutter diagnosis)

Run on the box from the NovaPlay folder:
    python3 tools/mixdrop_read_test.py [embed-url]
Resolves a fresh Mixdrop link, then has ffmpeg copy 60 s of the film from
minute 20 as fast as it can (max 90 s) and prints the video/audio format,
the read speed (x real time) and any errors or reconnects. Also shows the
User-Agent exteplayer3 is configured with (Mixdrop links only work with the
browser identity they were issued to).
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from extractors import hosts, net   # noqa: E402

EMBED = sys.argv[1] if len(sys.argv) > 1 else "https://mixdrop.ps/e/vkk971jec3vk8x"

print("resolving", EMBED)
s, _ = hosts.resolve_iframe_chain(EMBED, referer="https://topcinema.film/")
url = (s or "").split("|")[0]
if not url:
    sys.exit("could not resolve the link")
print("link:", url[:80])
print("User-Agent used:", net.UA)

cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-user_agent", net.UA,
       "-headers", "Referer: https://mxdrop.click/\r\nOrigin: https://mxdrop.click\r\n",
       "-ss", "1200", "-i", url, "-t", "60", "-c", "copy", "-f", "mpegts", "-y", "/dev/null"]
print("running ffmpeg (up to 90 s)...")
t0 = time.time()
try:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    out, code = p.stdout.decode("utf-8", "replace"), p.returncode
except subprocess.TimeoutExpired as e:
    out, code = (e.output or b"").decode("utf-8", "replace"), "TIMEOUT"
print("ffmpeg finished in {:.0f} s, exit {}".format(time.time() - t0, code))
lines = [l for l in re.split(r"[\r\n]+", out) if l.strip()]
for l in lines:
    if re.search(r"Stream #|Video:|Audio:|rror|econnect|403|404", l):
        print("  " + l.strip()[:160])
prog = [l for l in lines if "speed=" in l]
if prog:
    print("  last progress: " + prog[-1].strip()[:160])
else:
    print("  (no progress lines) last output lines:")
    for l in lines[-6:]:
        print("  " + l.strip()[:160])

print("\nexteplayer3 / user-agent settings in /etc/enigma2/settings:")
try:
    hits = [l.strip() for l in open("/etc/enigma2/settings", errors="replace")
            if re.search(r"useragent|user_agent|exteplayer|serviceapp", l, re.I)]
    print("\n".join("  " + h[:160] for h in hits[:15]) or "  (none set -- defaults)")
except Exception as e:
    print("  could not read:", e)
