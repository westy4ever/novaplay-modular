#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NovaPlay live site test -- runs a site's extractor through the SAME chain the
plugin uses, against the real site, and reports PASS / WARN / FAIL per step.

    categories -> category items -> movie/series page (-> season -> episode)
    -> servers -> extract_stream() -> fetch the start of the real stream

Run on the box (no Enigma2 needed, nothing is played or changed):

    cd /usr/lib/enigma2/python/Plugins/Extensions/NovaPlay
    python3 run_site_test.py sflix_pro              # one registered site
    python3 run_site_test.py --all                  # quick check of every site
    python3 run_site_test.py --list                 # show site keys
    python3 run_site_test.py extractors/new_site.py # test a (generated) file
    python3 run_site_test.py egydead --search "batman"
    python3 run_site_test.py sflix_pro --page sflix_movie_1153576
    python3 run_site_test.py sflix_pro --servers 8  # try more servers

The report is printed and also saved to /tmp/site_test_<site>.txt; the
plugin's own detailed log keeps going to /tmp/novaplay.log as usual.
Exit code: 0 = all passed, 1 = a step failed, 2 = bad arguments.
"""

from __future__ import print_function

import argparse
import importlib
import importlib.util
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

STEP_TIMEOUT = 90          # seconds per step (sites + retries can be slow)
STREAM_TIMEOUT = 20


# ─── small helpers ───────────────────────────────────────────────────────────

class Report(object):
    def __init__(self, name):
        self.name = name
        self.lines = []
        self.fails = 0
        self.warns = 0

    def out(self, text=""):
        print(text)
        self.lines.append(text)

    def step(self, status, title, detail="", secs=None):
        if status == "FAIL":
            self.fails += 1
        elif status in ("WARN", "BAD"):
            # BAD = one server that didn't work; only counted as a warning,
            # since a site with several servers usually has a few dead ones
            self.warns += 1
        t = " ({:.1f}s)".format(secs) if secs is not None else ""
        self.out("[{:4s}] {}{}".format(status, title, t))
        for line in (detail.splitlines() if detail else []):
            self.out("         " + line)

    def save(self):
        path = "/tmp/site_test_{}.txt".format(
            "".join(c if c.isalnum() or c in "._-" else "_" for c in self.name))
        try:
            with open(path, "w") as f:
                f.write("\n".join(self.lines) + "\n")
            return path
        except Exception:
            return None


def run_with_timeout(func, timeout, *args, **kwargs):
    """(result, error, seconds). A step that hangs past `timeout` is reported
    as a timeout instead of freezing the whole test."""
    box = {}

    def target():
        try:
            box["result"] = func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 -- we report every failure
            import traceback
            box["error"] = "{}: {}\n{}".format(
                type(e).__name__, e,
                "".join(traceback.format_exc().splitlines(True)[-3:]).rstrip())

    t0 = time.time()
    th = threading.Thread(target=target)
    th.daemon = True
    th.start()
    th.join(timeout)
    secs = time.time() - t0
    if th.is_alive():
        return None, "timed out after {}s".format(timeout), secs
    return box.get("result"), box.get("error"), secs


def short(s, n=90):
    s = str(s)
    return s if len(s) <= n else s[:n - 3] + "..."


def is_page_link(it):
    """Next/prev-page tiles."""
    if not isinstance(it, dict):
        return True
    if it.get("_is_next_page") or it.get("_is_prev_page"):
        return True
    t = str(it.get("title", ""))
    return any(k in t for k in ("Next", "Prev", "التالي", "السابق", "Page ", "صفحة"))


def is_subfolder(it):
    """A category inside a category -- the plugin opens it with
    get_category_items(), not get_page()."""
    return (isinstance(it, dict) and not is_page_link(it) and it.get("url")
            and (it.get("type") == "category" or it.get("_action") == "category"))


def is_nav_item(it):
    """Anything that isn't a playable/openable title: separators, page links,
    sub-folders."""
    if not isinstance(it, dict) or is_page_link(it):
        return True
    return it.get("type") in ("separator", "category") or it.get("_action") == "category"


# exactly the sub-item types plugin_screen_detail.py lists as seasons/episodes
DETAIL_CHILD_TYPES = ("episode", "series", "season")


def check_items(items, what):
    """(status, detail, content_items) for a list of item dicts."""
    if not isinstance(items, list):
        return "FAIL", "returned {} instead of a list".format(type(items).__name__), []
    content = [i for i in items if not is_nav_item(i)]
    if not items:
        return "FAIL", ("returned an empty list -- site unreachable/blocked, or its page "
                        "layout changed so nothing matched (details: /tmp/novaplay.log)"), []
    if not content:
        return "FAIL", "no {} returned ({} entries, all navigation/next-page)".format(
            what, len(items)), []
    no_title = sum(1 for i in content if not str(i.get("title", "")).strip())
    no_url = sum(1 for i in content if not i.get("url"))
    no_poster = sum(1 for i in content if not (i.get("poster") or i.get("thumbnail")))
    detail = ["{} {} (+{} navigation entries)".format(len(content), what, len(items) - len(content))]
    detail.append("first: {!r} -> {}".format(short(content[0].get("title", ""), 50),
                                              short(content[0].get("url", ""), 70)))
    status = "PASS"
    if no_url:
        status = "FAIL"
        detail.append("{} item(s) have NO url -- they can't be opened".format(no_url))
    if no_title:
        status = "WARN" if status == "PASS" else status
        detail.append("{} item(s) have no title".format(no_title))
    if no_poster:
        status = "WARN" if status == "PASS" else status
        detail.append("{} of {} item(s) have no poster".format(no_poster, len(content)))
    return status, "\n".join(detail), content


# ─── stream check ────────────────────────────────────────────────────────────

def _split_pipe(url, referer=None):
    """'url|Referer=..&Origin=..' -> (url, headers) -- the same convention the
    player and proxy use."""
    try:
        from urllib.parse import unquote
    except ImportError:
        from urllib import unquote
    url = str(url).strip()
    plain, pipe = (url.split("|", 1) + [""])[:2]
    headers = {}
    for part in pipe.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and k.strip() not in headers:
                headers[k.strip()] = unquote(v.strip())
    if referer and "Referer" not in headers:
        headers["Referer"] = referer
    return plain, headers


def check_stream(stream_url, referer=None):
    """Fetch the first bytes of the resolved stream exactly like the player
    would (with its headers). (status, detail)."""
    try:
        from urllib.request import Request, urlopen
        from urllib.parse import urljoin
    except ImportError:
        return "WARN", "urllib not available -- stream not checked"
    try:
        from extractors.net import UA
    except Exception:
        UA = "Mozilla/5.0"

    if stream_url.startswith("magnet:"):
        return "PASS", "magnet link (played through TorrServer -- not fetched here)"

    plain, headers = _split_pipe(stream_url, referer)
    headers.setdefault("User-Agent", UA)

    # XOR-obfuscated playlists (extractors/hlsobf.py) only play through the
    # local proxy, so test them through it, exactly as the player does.
    xor_key = next((v for k, v in headers.items() if k.lower() == "x-np-xor"), "")
    if xor_key:
        try:
            from urllib.parse import quote
            import novaplay_proxy
            if not novaplay_proxy.start_proxy():
                return "FAIL", "XOR playlist needs the local proxy, which did not start"
            q = "url=" + quote(plain, safe="")
            for k, name in (("Referer", "referer"), ("User-Agent", "ua"), ("Origin", "origin")):
                if headers.get(k):
                    q += "&{}={}".format(name, quote(headers[k], safe=""))
            q += "&xor=" + quote(xor_key, safe="")
            plain = "http://127.0.0.1:{}/stream?{}".format(novaplay_proxy._PROXY_PORT, q)
            headers = {"User-Agent": UA}
        except Exception as e:
            return "FAIL", "XOR playlist: local proxy unavailable ({})".format(short(e, 80))

    def get(u, nbytes):
        h = dict(headers)
        h["Range"] = "bytes=0-{}".format(nbytes - 1)
        resp = urlopen(Request(u, headers=h), timeout=STREAM_TIMEOUT)
        try:
            return resp.getcode(), resp.headers.get("Content-Type", ""), resp.read(nbytes), resp.geturl()
        finally:
            resp.close()

    try:
        code, ctype, data, final = get(plain, 65536)
    except Exception as e:
        code_txt = getattr(e, "code", None)
        reason = "HTTP {} {}".format(code_txt, getattr(e, "reason", "")).strip() if code_txt else short(e, 120)
        return "FAIL", "stream request failed: {}\nheaders sent: {}".format(
            reason, ", ".join(sorted(headers)) or "none")

    text = data[:4096].decode("utf-8", "ignore")
    if text.lstrip().startswith("#EXTM3U"):
        lines = [l.strip() for l in data.decode("utf-8", "ignore").splitlines()]
        if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
            variants = [l for l in lines if l and not l.startswith("#")]
            if not variants:
                return "FAIL", "HLS master playlist lists no variants"
            v_url = urljoin(final, variants[0])
            try:
                _, _, vdata, vfinal = get(v_url, 65536)
            except Exception as e:
                return "FAIL", "master playlist OK ({} variants) but variant failed: {}".format(
                    len(variants), short(e, 100))
            vlines = [l.strip() for l in vdata.decode("utf-8", "ignore").splitlines()]
            segs = [l for l in vlines if l and not l.startswith("#")]
            if not segs:
                return "FAIL", "variant playlist has no segments"
            try:
                scode, sctype, sdata, _ = get(urljoin(vfinal, segs[0]), 2048)
            except Exception as e:
                return "FAIL", "playlists OK but first segment failed: {}".format(short(e, 100))
            if sdata[:200].lstrip().lower().startswith(b"<"):
                return "FAIL", "first segment is an HTML page, not video"
            return "PASS", "HLS OK: {} variant(s), {} segment(s), first segment HTTP {}".format(
                len(variants), len(segs), scode)
        segs = [l for l in lines if l and not l.startswith("#")]
        if not segs:
            return "FAIL", "HLS playlist has no segments"
        try:
            scode, _, sdata, _ = get(urljoin(final, segs[0]), 2048)
        except Exception as e:
            return "FAIL", "playlist OK but first segment failed: {}".format(short(e, 100))
        if sdata[:200].lstrip().lower().startswith(b"<"):
            return "FAIL", "first segment is an HTML page, not video"
        return "PASS", "HLS OK: {} segment(s), first segment HTTP {}".format(len(segs), scode)

    if text.lstrip().lower().startswith(("<!doctype", "<html", "<")) or "text/html" in ctype:
        return "FAIL", "got an HTML page instead of video (blocked, expired or wrong headers)"
    if "video" in ctype or "octet-stream" in ctype or data[4:8] in (b"ftyp", b"moov") or data[:1] == b"\x47":
        return "PASS", "direct video OK (HTTP {}, {})".format(code, ctype or "binary")
    return "WARN", "reachable (HTTP {}, type {!r}) but not recognisably video".format(code, ctype)


def resolve_like_plugin(ext, url):
    """extract_stream() then attach_cookies(), in the SAME thread -- exactly what
    plugin_screen_detail._bgExtract does. The scraper's cookies are thread-local,
    so attaching them from another thread would silently add nothing and
    cookie-protected streams would wrongly look broken."""
    result = ext.extract_stream(url)
    try:
        from novaplay_proxy import attach_cookies
        result = attach_cookies(result)
    except Exception:
        pass
    return result


# ─── loading ─────────────────────────────────────────────────────────────────

def load_extractor(target):
    """(name, instance) from a registry key or a path to an extractor file."""
    importlib.import_module("extractors")   # package must exist for relative imports
    if target.endswith(".py") or os.sep in target or "/" in target:
        path = os.path.abspath(target)
        if not os.path.isfile(path):
            raise SystemExit("file not found: {}".format(target))
        mod_name = "extractors._site_test_" + "".join(
            c if c.isalnum() else "_" for c in os.path.splitext(os.path.basename(path))[0])
        spec = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "extractors"
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        from extractors.base import BaseExtractor
        classes = [v for v in vars(module).values()
                   if isinstance(v, type) and issubclass(v, BaseExtractor)
                   and v is not BaseExtractor and v.__module__ == mod_name]
        if not classes:
            raise SystemExit("no BaseExtractor subclass found in {}".format(target))
        return os.path.splitext(os.path.basename(path))[0], classes[0]()
    from extractors import registry
    if target not in registry._SITE_REGISTRY:
        raise SystemExit("unknown site key {!r} -- use --list to see them".format(target))
    return target, registry.get_extractor(target)


# ─── the test ────────────────────────────────────────────────────────────────

_NEW_V, _OLD_V = "Chrome/155.0.0.0", "Chrome/120.0.0.0"


def _force_old_ua(obj=None):
    """--old-ua: swap the browser identity back to Chrome/120 in every loaded
    NovaPlay module (string constants, header dicts, class attributes)."""
    def fix(v):
        if isinstance(v, str) and _NEW_V in v:
            return v.replace(_NEW_V, _OLD_V).replace('v="155"', 'v="120"')
        if isinstance(v, dict):
            for k in list(v):
                nv = fix(v[k])
                if nv is not v[k]:
                    v[k] = nv
        return v
    if obj is not None:
        for attr, val in list(vars(obj).items()):
            nv = fix(val)
            if nv is not val:
                setattr(obj, attr, nv)
    for mname, mod in list(sys.modules.items()):
        if not mod or not (mname.startswith(("extractors", "novaplay", "plugin_"))):
            continue
        for attr, val in list(vars(mod).items()):
            if isinstance(val, type):
                for ca, cv in list(vars(val).items()):
                    nv = fix(cv)
                    if nv is not cv:
                        try:
                            setattr(val, ca, nv)
                        except Exception:
                            pass
            else:
                nv = fix(val)
                if nv is not val:
                    setattr(mod, attr, nv)


def test_site(name, ext, args, quick=False):
    rep = Report(name)
    rep.out("=" * 64)
    rep.out("NovaPlay site test: {}  ({})".format(name, type(ext).__name__))
    rep.out("main url: {}".format(getattr(ext, "main_url", "?")))
    rep.out("time: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    rep.out("=" * 64)

    page_url = args.page
    first_title = None

    if not page_url:
        # 1. categories
        cats, err, secs = run_with_timeout(ext.get_categories, STEP_TIMEOUT)
        if err:
            rep.step("FAIL", "get_categories()", err, secs)
            return rep
        usable = [c for c in (cats or []) if isinstance(c, dict) and c.get("url")
                  and c.get("type") != "separator"]
        if not usable:
            rep.step("FAIL", "get_categories()", "no usable categories ({} returned)".format(
                len(cats or [])), secs)
            return rep
        rep.step("PASS", "get_categories()", "{} categories; testing {!r}".format(
            len(usable), short(usable[0].get("title", ""), 50)), secs)

        # 2. items in the first category that actually has content
        content = []
        for cat in usable[:3]:
            cat_url, cat_title = cat["url"], cat.get("title", "")
            for _level in range(3):          # category -> sub-folder -> sub-folder
                items, err, secs = run_with_timeout(ext.get_category_items, STEP_TIMEOUT, cat_url)
                title = "get_category_items({!r})".format(short(cat_title, 40))
                if err:
                    rep.step("FAIL", title, err, secs)
                    break
                folders = [i for i in (items or []) if is_subfolder(i)] if isinstance(items, list) else []
                if folders and not [i for i in items if not is_nav_item(i)]:
                    rep.step("PASS", title, "{} sub-folder(s), no titles at this level -> opening {!r}".format(
                        len(folders), short(folders[0].get("title", ""), 40)), secs)
                    cat_url, cat_title = folders[0]["url"], folders[0].get("title", "")
                    continue
                status, detail, content = check_items(items, "items")
                rep.step(status, title, detail, secs)
                break
            if content:
                break
        if not content:
            return rep
        if quick:
            return rep
        page_url = content[0]["url"]
        first_title = content[0].get("title")

    # 3. search (non-fatal: a site can have broken search but working browse)
    query = args.search or (first_title and str(first_title).split()[0]) or None
    if query and not quick:
        res, err, secs = run_with_timeout(ext.search, STEP_TIMEOUT, query)
        title = "search({!r})".format(query)
        if err:
            rep.step("WARN", title, err, secs)
        else:
            status, detail, _ = check_items(res, "results")
            rep.step("WARN" if status == "FAIL" else status, title, detail, secs)

    # 4. detail page; follow series -> season -> episode until servers appear
    data = None
    for depth in range(4):
        data, err, secs = run_with_timeout(ext.get_page, STEP_TIMEOUT, page_url)
        title = "get_page({})".format(short(page_url, 60))
        if err or not isinstance(data, dict):
            rep.step("FAIL", title, err or "returned {}".format(type(data).__name__), secs)
            return rep
        servers = [s for s in (data.get("servers") or []) if isinstance(s, dict) and s.get("url")]
        children = [i for i in (data.get("items") or [])
                    if isinstance(i, dict) and i.get("url") and i.get("type") in DETAIL_CHILD_TYPES]
        info = "title: {!r} | type: {} | servers: {} | sub-items: {}".format(
            short(data.get("title", ""), 40), data.get("type"), len(servers), len(children))
        if servers:
            rep.step("PASS", title, info, secs)
            break
        if children:
            rep.step("PASS", title, info + "\n-> opening first sub-item (season/episode)", secs)
            page_url = children[0]["url"]
            continue
        if data.get("downloads"):
            rep.step("WARN", title, info + "\nno stream servers, but {} download link(s) "
                     "(the blue key in the plugin) -- downloads aren't tested here".format(
                         len(data["downloads"])), secs)
            return rep
        rep.step("FAIL", title, info + "\nno servers and nothing to open", secs)
        return rep
    else:
        rep.step("FAIL", "get_page()", "no servers found after 4 levels")
        return rep

    downloads = data.get("downloads") or []
    if downloads:
        rep.out("         ({} download link(s) also listed)".format(len(downloads)))

    # 5. servers -> extract_stream -> real stream
    tried = 0
    playable = 0
    for srv in (servers if args.all_servers else servers[:max(1, args.servers)]):
        tried += 1
        label = "server {}/{}: {}".format(tried, len(servers), short(srv.get("name") or srv.get("url"), 45))
        if str(srv["url"]).startswith("magnet:"):
            # the plugin hands magnets straight to TorrServer, never to extract_stream()
            playable += 1
            rep.step("PASS", label, "magnet link -> TorrServer (not streamed by this test)")
            if not args.all_servers:
                break
            continue
        res, err, secs = run_with_timeout(resolve_like_plugin, STEP_TIMEOUT, ext, srv["url"])
        if err:
            rep.step("BAD", label, "extract_stream error: " + err, secs)
            continue
        if not res or not isinstance(res, (tuple, list)) or not res[0]:
            rep.step("BAD", label, "extract_stream found no stream\nembed: {}".format(
                short(srv["url"], 100)), secs)
            continue
        stream, quality = res[0], (res[1] if len(res) > 1 else "")
        referer = res[2] if len(res) > 2 else None
        sres, serr, ssecs = run_with_timeout(check_stream, STREAM_TIMEOUT * 4, stream, referer)
        if serr:
            sstatus, sdetail = "FAIL", "stream check error: " + serr
        else:
            sstatus, sdetail = sres
        if sstatus == "FAIL":
            sstatus = "BAD"
        elif sstatus == "PASS":
            playable += 1
        rep.step(sstatus, label, "resolved [{}]: {}\n{}".format(
            quality or "?", short(stream, 100), sdetail), secs + ssecs)
        if sstatus == "PASS" and not args.all_servers:
            untested = len(servers) - tried
            if untested:
                rep.out("         ({} more server(s) not tested -- add --all-servers to test them)".format(untested))
            break
    if not playable:
        rep.step("FAIL", "servers", "none of the {} server(s) tried produced a playable stream".format(tried))
    return rep


def apply_plugin_settings():
    """Use the same browser-proxy setting the plugin applies at startup
    (plugin.py), so Cloudflare-protected sites behave exactly as on screen."""
    try:
        from plugin_state import _get_config
        from extractors.net import set_browser_proxy
        set_browser_proxy(_get_config("browser_proxy", ""))
    except Exception:
        pass


def main():
    apply_plugin_settings()
    ap = argparse.ArgumentParser(description="Live-test a NovaPlay extractor against its real site.")
    ap.add_argument("target", nargs="?", help="registry site key, or path to an extractor .py file")
    ap.add_argument("--all", action="store_true", help="quick check (categories + items) of every registered site")
    ap.add_argument("--list", action="store_true", help="list registered site keys")
    ap.add_argument("--search", help="search query to test (default: first word of the first item's title)")
    ap.add_argument("--page", help="skip browsing and test this item/page url directly")
    ap.add_argument("--servers", type=int, default=5, help="max servers to try (default 5)")
    ap.add_argument("--all-servers", action="store_true", help="keep testing servers after one works")
    ap.add_argument("--only", help="with --all: comma-separated site keys to check")
    ap.add_argument("--old-ua", action="store_true",
                    help="check with the old Chrome/120 browser identity (A/B against Chrome/155)")
    args = ap.parse_args()
    if args.old_ua:
        print("Browser identity for this run: Chrome/120 (old)")

    if args.list:
        from extractors import registry
        for key, entry in registry._SITE_REGISTRY.items():
            print("{:20s} {}".format(key, entry.get("title", "")))
        return 0

    if args.all:
        from extractors import registry
        rows = []
        details = []
        only = set(k.strip() for k in (args.only or "").split(",") if k.strip())
        for key in registry._SITE_REGISTRY:
            if only and key not in only:
                continue
            try:
                name, ext = load_extractor(key)
                if args.old_ua:
                    _force_old_ua(ext)
                rep = test_site(name, ext, args, quick=True)
                rows.append((key, "FAIL" if rep.fails else ("WARN" if rep.warns else "PASS")))
                if rep.fails or rep.warns:
                    details.append("--- {} ---".format(key))
                    details.extend(l for l in rep.lines if l.startswith(("[", "         ")))
            except SystemExit as e:
                rows.append((key, "FAIL ({})".format(e)))
            except Exception as e:  # noqa: BLE001
                rows.append((key, "FAIL ({}: {})".format(type(e).__name__, short(e, 60))))
        print("\n" + "=" * 40 + "\nSUMMARY (categories + items only)\n" + "=" * 40)
        lines = ["{:20s} {}".format(k, v) for k, v in rows]
        print("\n".join(lines))
        try:
            with open("/tmp/site_test_ALL.txt", "w") as f:
                f.write(("Chrome/120 (old identity)\n" if args.old_ua else "") + "\n".join(lines) + "\n")
                if details:
                    f.write("\nDETAILS (failing / warning sites)\n" + "\n".join(details) + "\n")
            print("\nsaved: /tmp/site_test_ALL.txt (with the reasons for every FAIL/WARN)")
        except Exception:
            pass
        return 1 if any(not v.startswith("PASS") and not v.startswith("WARN") for _, v in rows) else 0

    if not args.target:
        ap.print_help()
        return 2

    name, ext = load_extractor(args.target)
    rep = test_site(name, ext, args)
    rep.out("-" * 64)
    verdict = "FAILED" if rep.fails else ("PASSED with warnings" if rep.warns else "PASSED")
    rep.out("RESULT: {}  ({} fail, {} warn)".format(verdict, rep.fails, rep.warns))
    path = rep.save()
    if path:
        rep.out("saved: {}".format(path))
    return 1 if rep.fails else 0


if __name__ == "__main__":
    sys.exit(main())
