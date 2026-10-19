#!/usr/bin/env python3
"""Find how movy.sx loads anime episodes and streams.

Step 1: download the JS chunks and print STREAM_API_URL and the anime
        API functions (info / episodes / sources).
Step 2: call the chain for one title and print the replies:
        anime.vidy.st/api/info?id=<slug>
        anime.vidy.st/api/episodes/<slug>
        <STREAM_API_URL>/anikoto/sources-id?providerId=<slug>&episodeId=<n>&dub=<bool>
Step 3: fetch the first stream URL it gets (headers + first lines).

Usage:  python3 movy_anime_probe.py [slug] > /tmp/movy_anime_probe.txt
"""
import json
import re
import sys
import urllib.parse
import urllib.request

SITE = "https://www.movy.sx"
SLUG = sys.argv[1] if len(sys.argv) > 1 else "dandadan-lzcmw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def get(url, ref=SITE + "/", headers=None, limit=None):
    h = {"User-Agent": UA, "Referer": ref, "Origin": SITE, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(limit) if limit else r.read()
            return r.status, dict(r.headers), data.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read(2000).decode("utf-8", "replace")
        return e.code, dict(e.headers or {}), body
    except Exception as e:  # noqa: BLE001
        return 0, {}, "ERROR %s" % e


def ctx(body, i, before=200, after=600):
    return body[max(0, i - before):i + after]


def main():
    _, _, page = get("%s/anime/%s" % (SITE, SLUG))
    m = re.search(r'"buildId":"([^"]+)"', page)
    build = m.group(1) if m else ""
    print("BUILD", build)
    chunks = set(re.findall(r'(/_next/static/[^"\'\s]+?\.js)', page))
    if build:
        _, _, man = get("%s/_next/static/%s/_buildManifest.js" % (SITE, build))
        chunks |= set("/_next/" + c for c in
                      re.findall(r'"(static/chunks/[^"]+?\.js)"', man))
    print("CHUNKS", len(chunks))
    stream_api = ""
    for c in sorted(chunks):
        _, _, body = get(SITE + c)
        for mm in re.finditer(r'STREAM_API_URL', body):
            s = ctx(body, mm.start(), 150, 250)
            v = re.search(r'STREAM_API_URL["\']?\s*[,:=]\s*0?\s*,?\s*["\'`]'
                          r'(https?://[^"\'`]+)', s)
            if v and not stream_api:
                stream_api = v.group(1).rstrip("/")
            if "https://" in s:
                print("\n===== %s  [STREAM_API_URL]\n%s" % (c, s))
        i = body.find('async function o(e){let t=r(e),a=await i(`/info')
        if i >= 0:
            print("\n===== %s  [anime API module]\n%s" % (c, body[i:i + 1800]))
    print("\nSTREAM_API_URL =", stream_api or "(not found)")
    api = "https://anime.vidy.st/api"
    st, _, info = get("%s/info?id=%s" % (api, urllib.parse.quote(SLUG)))
    print("\n----- INFO %s\n%s" % (st, info[:1500]))
    st, _, eps = get("%s/episodes/%s" % (api, SLUG))
    print("\n----- EPISODES %s\n%s" % (st, eps[:600]))

    bases = [stream_api] if stream_api else []
    bases += [b for b in ("https://api.wecollege.net", "https://anime.vidy.st/api",
                          "https://anime.vidy.st") if b not in bases]
    first_url = None
    for base in bases:
        for dub in ("false", "true"):
            u = "%s/anikoto/sources-id?providerId=%s&episodeId=1&dub=%s" % (
                base, urllib.parse.quote(SLUG), dub)
            st, _, body = get(u)
            print("\n----- SOURCES %s dub=%s -> %s\n%s" % (base, dub, st, body[:2500]))
            if st == 200 and not first_url:
                mm = re.search(r'"(?:url|file|src)"\s*:\s*"(https?:[^"]+)"', body)
                if mm:
                    first_url = mm.group(1).replace("\\/", "/")
                    hd = re.search(r'"headers"\s*:\s*(\{[^}]*\})', body)
                    first_hdr = json.loads(hd.group(1)) if hd else {}
        if first_url:
            break

    if first_url:
        print("\n----- STREAM", first_url)
        st, h, body = get(first_url, headers=first_hdr, limit=4000)
        print("status", st, "type", h.get("Content-Type"), "\n", body[:1200])
        st2, _, body2 = get(first_url, ref="", limit=600)
        print("\nwithout referer ->", st2, body2[:200])
    print("\nDONE")


if __name__ == "__main__":
    main()
