#!/usr/bin/env python3
"""Collect what is needed to study Cinejoy's own player (api.wing.st/g).

Downloads crush.wasm and every SvelteKit JS module of a Cinejoy watch page
(following imports), keeps the modules that mention the player API or the
WASM, and packs everything into /tmp/cinejoy_probe.tar.gz.

Usage:  python3 cinejoy_probe.py [tmdb_id]
"""
import os
import re
import sys
import tarfile
import urllib.request

SITE = "https://cinejoy.pk"
TMDB = sys.argv[1] if len(sys.argv) > 1 else "969681"
OUT = "/tmp/cinejoy_probe"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
KEYS = ("crush", "wing.st/g", "seal_request", "api.wing.st", "/servers",
        "Nebula", "WebAssembly")


def get(url, binary=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": SITE + "/", "Origin": SITE})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
            return data if binary else data.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print("  fail", url, e)
        return b"" if binary else ""


def main():
    os.makedirs(OUT, exist_ok=True)
    wasm = get("https://api.wing.st/crush.wasm", binary=True)
    open(os.path.join(OUT, "crush.wasm"), "wb").write(wasm)
    print("crush.wasm", len(wasm), "bytes")

    page = get("{}/movie/{}".format(SITE, TMDB))
    open(os.path.join(OUT, "page.html"), "w").write(page)
    todo = set(re.findall(r'(/_app/immutable/[^"\'\s)]+?\.js)', page))
    seen, kept = set(), 0
    while todo and len(seen) < 400:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        js = get(SITE + path)
        if not js:
            continue
        base = path.rsplit("/", 1)[0]
        for imp in re.findall(r'["\'](\.{1,2}/[^"\']+?\.js)["\']', js):
            parts = (base + "/" + imp).split("/")
            norm = []
            for p in parts:
                if p == "..":
                    norm.pop()
                elif p != ".":
                    norm.append(p)
            todo.add("/".join(norm))
        for abs_imp in re.findall(r'["\'](/_app/immutable/[^"\']+?\.js)["\']', js):
            todo.add(abs_imp)
        if any(k in js for k in KEYS):
            name = path.replace("/_app/immutable/", "").replace("/", "_")
            open(os.path.join(OUT, name), "w").write(js)
            kept += 1
            print("  kept", path, len(js))
    print("modules read:", len(seen), "kept:", kept)
    with tarfile.open("/tmp/cinejoy_probe.tar.gz", "w:gz") as t:
        t.add(OUT, arcname="cinejoy_probe")
    print("DONE -> /tmp/cinejoy_probe.tar.gz")


if __name__ == "__main__":
    main()
