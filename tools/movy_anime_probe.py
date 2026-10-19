#!/usr/bin/env python3
"""Find how movy.sx loads anime episodes and streams.

Downloads the /anime/<slug> page and every JS chunk it (or the build
manifest) references, then prints the code around fetchAnimeInfo /
fetchAnimeEpisodes / fetchAnimeSources and every anime.vidy.st URL.
Then it calls the endpoints it found for one title and prints the replies.

Usage:  python3 movy_anime_probe.py [slug] > /tmp/movy_anime_probe.txt
"""
import json
import re
import sys
import urllib.request

SITE = "https://www.movy.sx"
SLUG = sys.argv[1] if len(sys.argv) > 1 else "dandadan-lzcmw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def get(url, ref=SITE + "/"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": ref, "Origin": SITE,
        "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "ERROR %s" % e


def main():
    page = get("%s/anime/%s" % (SITE, SLUG))
    print("PAGE", len(page))
    m = re.search(r'"buildId":"([^"]+)"', page)
    build = m.group(1) if m else ""
    print("BUILD", build)
    chunks = set(re.findall(r'(/_next/static/[^"\'\s]+?\.js)', page))
    if build:
        man = get("%s/_next/static/%s/_buildManifest.js" % (SITE, build))
        chunks |= set("/_next/" + c for c in
                      re.findall(r'"(static/chunks/[^"]+?\.js)"', man))
    print("CHUNKS", len(chunks))
    keys = ("fetchAnimeSources", "fetchAnimeEpisodes", "fetchAnimeInfo",
            "anime.vidy.st", "animeApi", "ANIME_API")
    seen = set()
    for c in sorted(chunks):
        body = get(SITE + c)
        for k in keys:
            for mm in re.finditer(re.escape(k), body):
                ctx = body[max(0, mm.start() - 300):mm.start() + 700]
                sig = ctx[250:420]
                if sig in seen:
                    continue
                seen.add(sig)
                print("\n===== %s  [%s]\n%s" % (c, k, ctx))
    # try the obvious endpoints so the reply shapes are in the output too
    api = "https://anime.vidy.st/api"
    for path in ("/info/%s" % SLUG, "/anime/%s" % SLUG,
                 "/episodes/%s" % SLUG, "/anime/%s/episodes" % SLUG,
                 "/sources/%s?ep=1" % SLUG, "/search?q=dandadan"):
        r = get(api + path)
        print("\n----- GET %s%s\n%s" % (api, path, r[:1500]))
    print("\nDONE")


if __name__ == "__main__":
    main()
