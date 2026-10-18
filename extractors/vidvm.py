# -*- coding: utf-8 -*-
"""
VidCore / VidFast resolver (no browser, no PC helper)
=====================================================
Both players (vidcore.io / vidcore.net, vidfast.vc) are the same Next.js
app.  The player chunk hides two bytecode programs (see jsvm.py):

  VM-A  builds the encrypted request(s) from the page token `en`, POSTs
        them to /<secret path>/..., decrypts the reply and calls
        setServers([{name, data, ...}])
  VM-B  decodes the reply of  POST <secret path>/<id>/<server.data>
        into {url, tracks, noReferrer, ...}

We run both with jsvm.py, doing the HTTP requests ourselves.  The chunk
is cached on disk (its file name carries a content hash) together with
the parsed VM specs, so the expensive parse happens once per site build.
"""

import json
import os
import re
import time
from urllib.parse import urljoin, urlparse, quote

from .net import log
from . import jsrt as R
from . import jsvm as V
from . import jsglobals as G

UA = G.FIREFOX_UA
def _pick_cache_dir():
    """On the hard disk when there is one, so the parsed player code survives a
    reboot (the first VidCore/VidFast film after a restart would otherwise spend
    ~20 s re-reading it); /tmp otherwise."""
    for base in ("/media/hdd", "/media/usb"):
        if os.path.isdir(base):
            d = os.path.join(base, "NovaPlay", "cache", "vm")
            try:
                os.makedirs(d, exist_ok=True)
                test = os.path.join(d, ".w")
                open(test, "w").close()
                os.remove(test)
                return d
            except Exception:
                pass
    return "/tmp/novaplay_vmcache"


_CACHE_DIR = _pick_cache_dir()
_PRUNED = False
_SPEC_VERSION = 4
_MEM = {}


# ─── HTTP ────────────────────────────────────────────────────────────────

def _http(url, method="GET", headers=None, data=None, timeout=20):
    """-> (status, text, final_url).  Uses NovaPlay's shared opener/cookie jar."""
    from urllib.request import Request
    from urllib.error import HTTPError
    from . import net as _net
    h = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
    }
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    elif method == "POST":
        body = b""
    req = Request(url, data=body, headers=h, method=method)
    try:
        cookie = _net.get_cookie_header_for_url(url)
        if cookie:
            req.add_header("Cookie", cookie)
    except Exception:
        pass
    try:
        resp = _net._get_opener().open(req, timeout=timeout)
        raw = resp.read()
        return resp.getcode(), _decode(raw, resp.headers), resp.geturl()
    except HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return e.code, _decode(raw, e.headers), url
    except Exception as e:
        log("vidvm: HTTP {} {} failed: {}".format(method, url[:100], e))
        return 0, "", url


def _decode(raw, headers):
    enc = (headers.get("Content-Encoding") or "").lower() if headers else ""
    try:
        if enc == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            import zlib
            raw = zlib.decompress(raw)
        elif enc == "br":
            import brotli
            raw = brotli.decompress(raw)
    except Exception:
        pass
    return raw.decode("utf-8", "replace")


# ─── page / chunk analysis ───────────────────────────────────────────────

def _page_token(html):
    m = re.search(r'\\?"en\\?"\s*:\s*\\?"([^"\\]+)', html)
    return m.group(1) if m else None


def _chunk_urls(html, base):
    seen = []
    for m in re.finditer(r'(?:/_next/)?static/chunks/[\w\-./%\[\]]+?\.js', html):
        u = m.group(0)
        if not u.startswith("/_next/"):
            u = "/_next/" + u
        if u not in seen:
            seen.append(u)
    # framework chunks never hold the player VM
    skip = ("webpack-", "main-app-", "polyfills-", "framework-", "/app/layout-", "/app/page-",
            "/app/not-found", "/app/global-error")
    return [urljoin(base, u) for u in seen if not any(s in u for s in skip)]


def _cache_path(url, ext):
    name = re.sub(r"[^\w.\-]", "_", urlparse(url).netloc + "_" + urlparse(url).path.rsplit("/", 1)[-1])
    return os.path.join(_CACHE_DIR, name + ext)


def _load_chunk(url, referer):
    p = _cache_path(url, "")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        pass
    st, text, _ = _http(url, headers={"Referer": referer}, timeout=25)
    if st != 200 or not text:
        log("vidvm: chunk {} -> HTTP {}".format(url[-60:], st))
        return None
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return text


class SiteSpec(object):
    """Everything we need from one player chunk."""

    def __init__(self):
        self.vm_req = None      # VMSpec that calls setServers
        self.vm_dec = None      # VMSpec that reads `response`
        self.encode_map = {}    # base64url char -> char
        self.stream_path = None  # "/likzi/.../1000063476145130"
        self.stream_id = None    # "C9fq4hGduiM"
        self.stream_method = "POST"
        self.stream_headers = {}

    def to_json(self):
        def vm(sp):
            return {"code": sp.code.hex(), "disp": sp.disp, "lit": {str(k): v for k, v in sp.lit.items()},
                    "binops": sp.binops, "unops": sp.unops, "tables": sp.table_names, "label": sp.label}
        return json.dumps({"req": vm(self.vm_req), "dec": vm(self.vm_dec), "enc": self.encode_map,
                           "path": self.stream_path, "id": self.stream_id, "method": self.stream_method,
                           "headers": self.stream_headers})

    @classmethod
    def from_json(cls, text):
        d = json.loads(text)

        def vm(x):
            sp = V.VMSpec()
            sp.code = bytes.fromhex(x["code"])
            sp.disp = x["disp"]
            sp.lit = {int(k): v for k, v in x["lit"].items()}
            sp.binops = x["binops"]
            sp.unops = x["unops"]
            sp.table_names = [[tuple(p) for p in t] for t in x["tables"]]
            sp.label = x.get("label", "")
            return sp
        site = cls()
        site.vm_req = vm(d["req"])
        site.vm_dec = vm(d["dec"])
        site.encode_map = d["enc"]
        site.stream_path = d["path"]
        site.stream_id = d["id"]
        site.stream_method = d["method"]
        site.stream_headers = d["headers"]
        return site


def _find_encode_map(js, func_name):
    m = re.search(r"function " + re.escape(func_name) +
                  r"\(e\)\{let t=new Map\((\w+)\.map\(\(e,t\)=>\[e,(\w+)\[t\]\]\)\)", js)
    if not m:
        return {}

    def arr(name):
        mm = re.search(r"(?<![\w$])" + re.escape(name) + r"=(\[[^\]]*\])", js)
        if mm:
            return json.loads(mm.group(1))
        mm = re.search(r"(?<![\w$])" + re.escape(name) + r"=JSON\.parse\('(\[[^']*\])'\)", js)
        if mm:
            return json.loads(mm.group(1))
        raise ValueError(name)
    try:
        a = arr(m.group(1))
        b = arr(m.group(2))
    except Exception:
        return {}
    return dict(zip(a, b))


def _find_stream_request(js):
    rx = re.compile(r'fetch\(""\.concat\("(/[^"]+)","/"\)\.concat\("([^"]+)","/"\)\.concat\((\w+)\.data\),'
                    r'\{signal:\w+\.signal,method:"(\w+)",headers:\{\.\.\.JSON\.parse\((\'[^\']*\'|"(?:[^"\\]|\\.)*")\)\}\}\)')
    m = rx.search(js)
    if not m:
        return None
    raw = m.group(5)
    if raw.startswith("'"):
        hjson = raw[1:-1]
    else:
        hjson = json.loads(raw)
    try:
        headers = json.loads(hjson) if hjson else {}
    except ValueError:
        headers = {}
    return m.group(1), m.group(2), m.group(4), headers


def analyse_chunk(js):
    """-> SiteSpec or None"""
    if "function*" not in js or "fromCharCode" not in js:
        return None
    js = V.deobfuscate(js)
    specs = V.find_vms(js, deobfuscated=True)
    if not specs:
        return None
    site = SiteSpec()
    for sp in specs:
        names = {n for t in sp.table_names for n, _ in t}
        if "setServers" in names:
            site.vm_req = sp
            for t in sp.table_names:
                for n, expr in t:
                    if n == "encode":
                        site.encode_map = _find_encode_map(js, expr.strip())
        elif "response" in names:
            site.vm_dec = sp
    req = _find_stream_request(js)
    if req:
        site.stream_path, site.stream_id, site.stream_method, site.stream_headers = req
    if not (site.vm_req and site.vm_dec and site.stream_path):
        log("vidvm: chunk analysed but incomplete (req={} dec={} path={})".format(
            bool(site.vm_req), bool(site.vm_dec), bool(site.stream_path)))
        return None
    return site


def _get_site_spec(chunk_url, js):
    key = chunk_url
    if key in _MEM:
        return _MEM[key]
    pp = _cache_path(chunk_url, ".spec%d" % _SPEC_VERSION)
    try:
        with open(pp, "r") as f:
            site = SiteSpec.from_json(f.read())
            _MEM[key] = site
            return site
    except Exception:
        pass
    t0 = time.time()
    site = analyse_chunk(js)
    log("vidvm: analysed {} in {:.1f}s -> {}".format(chunk_url[-50:], time.time() - t0, "ok" if site else "no VM"))
    if site:
        try:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            with open(pp, "w") as f:
                f.write(site.to_json())
        except Exception:
            pass
    _MEM[key] = site
    return site


def _find_site(html, page_url):
    _prune_cache()
    for cu in _chunk_urls(html, page_url):
        if cu in _MEM:
            if _MEM[cu]:
                return _MEM[cu]
            continue
        pp = _cache_path(cu, ".spec%d" % _SPEC_VERSION)
        neg = _cache_path(cu, ".novm")
        if os.path.exists(neg):
            _MEM[cu] = None
            continue
        if os.path.exists(pp):
            site = _get_site_spec(cu, None)
            if site:
                return site
            continue
        js = _load_chunk(cu, page_url)
        if not js:
            continue                      # download failed: try again next time
        if not _VM_HDR.search(js):
            _MEM[cu] = None
            try:
                os.remove(_cache_path(cu, ""))
                open(neg, "w").close()     # plain chunk, never look at it again
            except Exception:
                pass
            continue
        site = _get_site_spec(cu, js)     # raises if the VM can't be read
        if site:
            try:
                os.remove(_cache_path(cu, ""))   # the spec is what we keep
            except Exception:
                pass
            return site
        # a VM-looking chunk we could not use: keep the file so it can be sent for a fix
        log("vidvm: kept {} for inspection".format(_cache_path(cu, "")))
    return None


_VM_HDR = re.compile(r'function\*[\w$]+\([\w$]+,[\w$]+,[\w$]+\)\{(?:let [\w$]+=[\w$]+;)?var [\w$]+=[\w$]+,'
                     r'[\w$]+=\[\],[\w$]+=\[\];function [\w$]+\(\)\{if\([\w$]+>=[\w$]+\)throw Error\(""\)')


def _prune_cache(max_age_days=60):
    """Old site builds pile up on the hard disk; drop files untouched for 60 days."""
    global _PRUNED
    if _PRUNED:
        return
    _PRUNED = True
    try:
        cutoff = time.time() - max_age_days * 86400
        for name in os.listdir(_CACHE_DIR):
            p = os.path.join(_CACHE_DIR, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


# ─── running the programs ────────────────────────────────────────────────

def _js_fetch_factory(origin, page_url, calls):
    def js_fetch(this, a):
        url = R.to_str(R.arg(a, 0))
        opts = R.arg(a, 1)
        method = "GET"
        headers = {}
        body = None
        if isinstance(opts, R.JSObject):
            m = opts.get("method")
            if isinstance(m, str):
                method = m.upper()
            hd = opts.get("headers")
            if isinstance(hd, R.JSObject):
                for k, v in hd.items():
                    headers[k] = R.to_str(v)
            b = opts.get("body")
            if b is not None and b is not R.UNDEF:
                body = R.as_bytes(b) if not isinstance(b, str) else b
        full = urljoin(origin + "/", url)
        same = urlparse(full).netloc == urlparse(origin).netloc
        h = {"Referer": page_url if same else origin + "/"}
        if method != "GET" or not same:
            h["Origin"] = origin
        h.update({"Sec-Fetch-Site": "same-origin" if same else "cross-site",
                  "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
        h.update(headers)
        st, text, _ = _http(full, method=method, headers=h, data=body, timeout=20)
        calls.append((method, full, st, len(text or "")))
        log("vidvm: {} {} -> {} ({} bytes)".format(method, full[:110], st, len(text or "")))
        resp = R.JSObject()
        resp["status"] = st
        resp["ok"] = 200 <= st < 300
        resp["url"] = full
        resp["text"] = R.JSFunction(lambda t, x: R.JSPromise(True, text or ""), "text")
        resp["json"] = R.JSFunction(lambda t, x: R.JSPromise(True, R.json_parse(text or "")), "json")
        if st == 0:
            return R.JSPromise(False, R.make_error("Failed to fetch", "TypeError"))
        return R.JSPromise(True, resp)
    return R.JSFunction(js_fetch, "fetch")


def _base_env(page_url, fetch_fn, extra_opts=None):
    opts = {"page_url": page_url, "fetch": fetch_fn, "user_agent": UA}
    if extra_opts:
        opts.update(extra_opts)
    g = G.make_globals(opts)
    env = dict(g)
    env["Buffer"] = g["Buffer"]
    env["crypto"] = g["crypto_node"]
    return g, env


def run_request_vm(site, token, page_url, origin, extra_opts=None, fetch_fn=None):
    """-> list of server dicts (python) or [] ; raises on VM error."""
    calls = []
    fetch_fn = fetch_fn or _js_fetch_factory(origin, page_url, calls)
    g, env = _base_env(page_url, fetch_fn, extra_opts)
    box = {}

    def set_servers(this, a):
        v = R.arg(a, 0)
        if isinstance(v, R.JSFunction):
            v = v.fn(R.UNDEF, [R.JSArray()])
        box["servers"] = v
        return R.UNDEF

    enc_map = site.encode_map

    def encode(this, a):
        s = R.buffer_tostring(R.arg(a, 0), "base64").replace("+", "-").replace("/", "_").replace("=", "")
        return "".join(enc_map.get(c, c) for c in s)
    states = []
    env.update({
        "server": R.UNDEF,
        "setState": R.JSFunction(lambda t, a: states.append(R.arg(a, 0)) or R.UNDEF, "setState"),
        "setServers": R.JSFunction(set_servers, "setServers"),
        "setFavServer": R.JSFunction(lambda t, a: R.UNDEF, "setFavServer"),
        "encode": R.JSFunction(encode, "encode"),
        "en": token,
    })
    vm = V.VM(site.vm_req, env, g)
    if vm.missing:
        log("vidvm: request VM wants unknown globals: {}".format(sorted(vm.missing)))
    t0 = time.time()
    res = vm.run_program()
    try:
        R.await_value(res)
    except R.JSThrow as e:
        log("vidvm: request VM threw: {}".format(e))
    log("vidvm: request VM {} ops in {:.1f}s, states={}{}".format(
        vm.ops, time.time() - t0, [R.to_py(x) for x in states][:5],
        ", unresolved={}".format(sorted(vm.unresolved)) if vm.unresolved else ""))
    servers = R.to_py(box.get("servers")) or []
    return [s for s in servers if isinstance(s, dict)]


def run_decode_vm(site, text, page_url, extra_opts=None):
    """-> dict (url, tracks, noReferrer, ...) or None"""
    g, env = _base_env(page_url, R.JSFunction(lambda t, a: R.JSPromise(False, R.make_error("no fetch")), "fetch"),
                       extra_opts)
    dr = R.JSArray()
    resp = R.JSObject()
    resp["text"] = R.JSFunction(lambda t, a: R.JSPromise(True, text), "text")
    env["response"] = resp
    env["dr"] = dr
    vm = V.VM(site.vm_dec, env, g)
    res = vm.run_program()
    try:
        R.await_value(res)
    except R.JSThrow as e:
        log("vidvm: decode VM threw: {}".format(e))
    if vm.unresolved:
        log("vidvm: decode VM looked for unknown names {}".format(sorted(vm.unresolved)))
    if dr and isinstance(dr[0], R.JSObject):
        return R.to_py(dr[0])
    return None


# ─── public resolver ─────────────────────────────────────────────────────

def _page_url_for(url):
    p = urlparse(url)
    host = p.netloc.lower()
    if host.endswith("vidcore.net"):
        # vidcore.net redirects to vidcore.io; go there directly
        return "https://vidcore.io" + p.path + ("?" + p.query if p.query else "")
    return url


def resolve_vid_vm(url, referer=None, max_servers=6):
    """vidcore.io / vidcore.net / vidfast.vc -> (stream_url_with_headers, label) or None"""
    try:
        return _resolve(url, referer, max_servers)
    except Exception as e:
        import traceback
        log("vidvm: failed for {}: {}\n{}".format(url[:100], e, traceback.format_exc()[-1200:]))
        return None


def _resolve(url, referer, max_servers):
    page_url = _page_url_for(url)
    hdr = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Sec-Fetch-Dest": "iframe", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "cross-site",
           "Upgrade-Insecure-Requests": "1"}
    if referer:
        hdr["Referer"] = referer
    st, html, final = _http(page_url, headers=hdr, timeout=20)
    if st != 200 or not html:
        log("vidvm: page {} -> HTTP {}".format(page_url[:100], st))
        return None
    page_url = final or page_url
    pu = urlparse(page_url)
    origin = "{}://{}".format(pu.scheme, pu.netloc)
    token = _page_token(html)
    if not token:
        log("vidvm: no page token (en) on {} ({} bytes)".format(page_url[:100], len(html)))
        return None
    site = _find_site(html, page_url)
    if not site:
        log("vidvm: no player VM found in {}'s chunks".format(pu.netloc))
        return None
    servers = run_request_vm(site, token, page_url, origin)
    log("vidvm: {} servers: {}".format(len(servers), [s.get("name") for s in servers]))
    if not servers:
        return None
    servers.sort(key=lambda s: 0 if s.get("selected") else 1)
    fallback = None
    for srv in servers[:max_servers]:
        data = srv.get("data")
        if not data:
            continue
        surl = "{}{}/{}/{}".format(origin, site.stream_path, site.stream_id, data)
        h = {"Referer": page_url, "Origin": origin, "Sec-Fetch-Site": "same-origin",
             "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
        h.update(site.stream_headers or {})
        st, text, _ = _http(surl, method=site.stream_method, headers=h, timeout=20)
        log("vidvm: server {} -> HTTP {} ({} bytes)".format(srv.get("name"), st, len(text or "")))
        if st != 200 or not text:
            continue
        info = run_decode_vm(site, text, page_url)
        if not info or not info.get("url"):
            log("vidvm: server {} decoded nothing".format(srv.get("name")))
            continue
        stream = info["url"]
        hdrs = {}
        if not info.get("noReferrer"):
            hdrs["Referer"] = origin + "/"
            hdrs["Origin"] = origin
        sh = info.get("headers")
        if isinstance(sh, dict):
            hdrs.update({k: str(v) for k, v in sh.items()})
        full = stream
        if hdrs:
            full = stream + "|" + "&".join("{}={}".format(k, v) for k, v in hdrs.items())
        label = "{} {}".format(pu.netloc.split(".")[0].capitalize(), srv.get("name") or "").strip()
        if fallback is None:
            fallback = (full, label)
        if not _probe(stream, hdrs):
            continue
        log("vidvm: stream {} [{}]".format(full[:160], label))
        return full, label
    if fallback:
        log("vidvm: no server passed the playlist check; using {}".format(fallback[1]))
    return fallback


def _probe(stream, hdrs):
    """Does the playlist actually load from here?"""
    h = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
    h.update(hdrs)
    st, text, _ = _http(stream, headers=h, timeout=10)
    ok = st == 200 and (text or "").lstrip().startswith("#EXTM3U")
    if not ok:
        log("vidvm: playlist check failed for {} (HTTP {}, {!r})".format(stream[:90], st, (text or "")[:40]))
    return ok
