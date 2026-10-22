#!/usr/bin/env python3
"""How are sound and picture laid out in a remote MP4? (stutter diagnosis)

Run on the box from the NovaPlay folder:
    python3 tools/mp4_layout.py [embed-url]
Downloads only the MP4's index (the 'moov' box) with Range requests and
prints, every few minutes of the film, how many bytes apart the video and
the audio for that moment are stored. A player reading over HTTP has to
jump between the two; tens of MB apart = it reconnects constantly.
"""
import os
import struct
import sys
import urllib.request as U
import ssl

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from extractors import hosts, net   # noqa: E402

EMBED = sys.argv[1] if len(sys.argv) > 1 else "https://mixdrop.ps/e/vkk971jec3vk8x"
CTX = ssl._create_unverified_context()

s, _ = hosts.resolve_iframe_chain(EMBED, referer="https://topcinema.film/")
if not s:
    s = hosts.resolve_mixdrop(EMBED) or ""
URL = s.split("|")[0]
if not URL:
    sys.exit("could not resolve " + EMBED)
HDR = {"User-Agent": net.UA, "Referer": "https://mxdrop.click/", "Origin": "https://mxdrop.click"}
for part in (s.split("|", 1)[1] if "|" in s else "").split("&"):
    if "=" in part:
        k, v = part.split("=", 1)
        HDR[k] = v


def get(start, end):
    h = dict(HDR)
    h["Range"] = "bytes={}-{}".format(start, end)
    r = U.urlopen(U.Request(URL, headers=h), timeout=30, context=CTX)
    total = 0
    cr = r.headers.get("Content-Range") or ""
    if "/" in cr:
        try:
            total = int(cr.rsplit("/", 1)[1])
        except ValueError:
            pass
    return r.read(), total


def boxes(data, off=0, end=None):
    end = len(data) if end is None else end
    while off + 8 <= end:
        size, typ = struct.unpack(">I4s", data[off:off + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr:
            break
        yield typ.decode("latin-1"), off, off + hdr, off + size
        off += size


# 1) find moov among the top-level boxes
head, total = get(0, 65535)
print("file size: {:.1f} MB".format(total / 1e6))
pos, moov = 0, None
while pos < total:
    blk, _ = get(pos, pos + 15)
    size, typ = struct.unpack(">I4s", blk[:8])
    if size == 1:
        size = struct.unpack(">Q", blk[8:16])[0]
    typ = typ.decode("latin-1")
    print("  top-level box '{}' at {:.1f} MB, {:.1f} MB long".format(typ, pos / 1e6, size / 1e6))
    if typ == "moov":
        moov = (pos, size)
        break
    if size <= 0:
        break
    pos += size
if not moov:
    sys.exit("no moov box found")
data, _ = get(moov[0], moov[0] + moov[1] - 1)


def child(data, s, e, name):
    for t, o, b, en in boxes(data, s, e):
        if t == name:
            return b, en
    return None


tracks = []
for t, o, b, e in boxes(data, 8, len(data)):
    if t != "trak":
        continue
    mdia = child(data, b, e, "mdia")
    if not mdia:
        continue
    mdhd = child(data, *mdia, "mdhd")
    hdlr = child(data, *mdia, "hdlr")
    kind = data[hdlr[0] + 8:hdlr[0] + 12].decode("latin-1")
    ver = data[mdhd[0]]
    scale = struct.unpack(">I", data[mdhd[0] + (20 if ver == 1 else 12):][:4])[0]
    minf = child(data, *mdia, "minf")
    stbl = child(data, *minf, "stbl")

    def table(name):
        return child(data, *stbl, name)

    stts = table("stts")
    n = struct.unpack(">I", data[stts[0] + 4:stts[0] + 8])[0]
    durs = []
    for i in range(n):
        cnt, d = struct.unpack(">II", data[stts[0] + 8 + i * 8:stts[0] + 16 + i * 8])
        durs.append((cnt, d))
    stsc = table("stsc")
    n = struct.unpack(">I", data[stsc[0] + 4:stsc[0] + 8])[0]
    runs = [struct.unpack(">III", data[stsc[0] + 8 + i * 12:stsc[0] + 20 + i * 12]) for i in range(n)]
    co = table("stco")
    if co:
        n = struct.unpack(">I", data[co[0] + 4:co[0] + 8])[0]
        offs = list(struct.unpack(">{}I".format(n), data[co[0] + 8:co[0] + 8 + 4 * n]))
    else:
        co = table("co64")
        n = struct.unpack(">I", data[co[0] + 4:co[0] + 8])[0]
        offs = list(struct.unpack(">{}Q".format(n), data[co[0] + 8:co[0] + 8 + 8 * n]))
    # samples per chunk -> start time of each chunk (seconds)
    spc = []
    for i, (first, per, _d) in enumerate(runs):
        last = runs[i + 1][0] - 1 if i + 1 < len(runs) else len(offs)
        spc += [per] * (last - first + 1)
    sample_t, t_acc = [], 0
    for cnt, d in durs:
        for _ in range(cnt):
            sample_t.append(t_acc)
            t_acc += d
    chunk_t, si = [], 0
    for per in spc[:len(offs)]:
        chunk_t.append(sample_t[si] / float(scale) if si < len(sample_t) else t_acc / float(scale))
        si += per
    tracks.append((kind, chunk_t, offs, t_acc / float(scale)))
    print("  track {}: {} chunks, {:.0f} min".format(kind, len(offs), t_acc / float(scale) / 60))

vid = next((t for t in tracks if t[0] == "vide"), None)
aud = next((t for t in tracks if t[0] == "soun"), None)
if not vid or not aud:
    sys.exit("need one video and one audio track")


def off_at(track, sec):
    _, ct, offs, _ = track
    best = 0
    for i, t in enumerate(ct):
        if t <= sec:
            best = i
        else:
            break
    return offs[best]


print("\nminute   video at    audio at    apart")
dur = min(vid[3], aud[3])
worst = 0
for m in range(0, int(dur // 60), 5):
    v, a = off_at(vid, m * 60), off_at(aud, m * 60)
    worst = max(worst, abs(v - a))
    print("{:>5}   {:>7.1f} MB  {:>7.1f} MB  {:>7.2f} MB".format(m, v / 1e6, a / 1e6, abs(v - a) / 1e6))
print("\nworst distance: {:.2f} MB  ({})".format(
    worst / 1e6, "well interleaved" if worst < 2e6 else "POORLY interleaved -- a network player has to jump back and forth"))
