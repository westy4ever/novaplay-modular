#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""curl_cffi in a process of its own.

Measured on the box (2026-10-21): inside Enigma2, curl_cffi can't impersonate
any browser ("Impersonating chrome120 is not supported"), because Enigma2 has
already loaded the system libcurl.so.4 (pycurl, GStreamer) and its symbols win
over libcurl-impersonate's. In a plain python3 process the same call works and
gives a real Chrome TLS fingerprint. So NovaPlay runs those requests here.

    server:  python3 -I novaplay_cffi_helper.py [port]     (started by NovaPlay)
    client:  from novaplay_cffi_helper import needed, client
             if needed(): r = client().get(url, headers=..., impersonate="chrome146")

The client response looks like curl_cffi's: status_code, url, headers, content,
text, iter_content(), close(). Only 127.0.0.1 is served.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time

VERSION = "3"
PORT = 19891
_HERE = os.path.dirname(os.path.abspath(__file__))
_TARGETS = ("chrome146", "chrome145", "chrome142", "chrome136", "chrome131",
            "chrome124", "chrome120", "chrome110", "chrome101", "chrome")


# ─── server ──────────────────────────────────────────────────────────────────
def _serve(port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from curl_cffi import requests as cr

    target = {"i": 0}
    jar = {"c": None}
    jar_lock = threading.Lock()

    def session():
        # each request runs in its own thread: give it a fresh Session that
        # starts from the shared cookies (site sessions, Cloudflare clearance)
        s = cr.Session()
        with jar_lock:
            if jar["c"] is not None:
                try:
                    s.cookies.update(jar["c"])
                except Exception:
                    pass
        return s

    def keep_cookies(s):
        with jar_lock:
            try:
                if jar["c"] is None:
                    jar["c"] = cr.Cookies()
                jar["c"].update(s.cookies)
            except Exception:
                pass

    def do_request(p):
        method = p.get("method", "GET")
        kw = dict(headers=p.get("headers") or {}, timeout=p.get("timeout") or 30,
                  allow_redirects=p.get("allow_redirects", True), stream=bool(p.get("stream")),
                  verify=bool(p.get("verify", False)))
        if p.get("data_b64") is not None:
            kw["data"] = base64.b64decode(p["data_b64"])
        imp = p.get("impersonate")
        while True:
            if imp and imp not in _TARGETS[target["i"]:]:
                imp = _TARGETS[target["i"]] if target["i"] < len(_TARGETS) else None
            try:
                ss = session()
                r = ss.request(method, p["url"], impersonate=imp, **kw) if imp \
                    else ss.request(method, p["url"], **kw)
                keep_cookies(ss)
                return r
            except Exception as e:
                m = str(e).lower()
                if imp and "impersonating" in m and "not supported" in m:
                    i = _TARGETS.index(imp) + 1 if imp in _TARGETS else len(_TARGETS)
                    target["i"] = max(target["i"], i)
                    imp = _TARGETS[target["i"]] if target["i"] < len(_TARGETS) else None
                    continue
                raise

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/ping"):
                body = json.dumps({"v": VERSION, "pid": os.getpid()}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/quit"):
                self.send_response(200)
                self.end_headers()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_error(404)

        def do_POST(self):
            if not self.path.startswith("/r"):
                return self.send_error(404)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                p = json.loads(self.rfile.read(n).decode("utf-8"))
                r = do_request(p)
            except Exception as e:
                self.send_response(502)
                self.send_header("X-NP-Error", base64.b64encode(str(e)[:900].encode()).decode())
                self.end_headers()
                return
            hdrs = {}
            for k, v in r.headers.items():
                if k.lower() in ("content-encoding", "content-length", "transfer-encoding", "connection"):
                    continue          # the body below is already decoded
                hdrs[k] = v
            self.send_response(200)
            self.send_header("X-NP-Status", str(r.status_code))
            self.send_header("X-NP-Url", base64.b64encode(str(r.url).encode()).decode())
            self.send_header("X-NP-Headers", base64.b64encode(json.dumps(hdrs).encode()).decode())
            self.end_headers()
            try:
                if p.get("stream"):
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            self.wfile.write(chunk)
                else:
                    self.wfile.write(r.content)
            except Exception:
                pass
            finally:
                try:
                    r.close()
                except Exception:
                    pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    srv.serve_forever()


# ─── client ──────────────────────────────────────────────────────────────────
_needed = None
_lock = threading.Lock()
_started = {"t": 0.0}


def needed():
    """True when curl_cffi can't impersonate inside this process (Enigma2)."""
    global _needed
    if _needed is None:
        try:
            from curl_cffi import Curl
            c = Curl()
            ret = c.impersonate("chrome120")
            try:
                c.close()
            except Exception:
                pass
            _needed = bool(ret)
        except Exception:
            _needed = True
    return _needed


def _ping(port=PORT, timeout=1.0):
    try:
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/ping")
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return data
    except Exception:
        return None


def _ensure(port=PORT):
    info = _ping(port)
    if info and info.get("v") == VERSION:
        return True
    with _lock:
        info = _ping(port)
        if info and info.get("v") == VERSION:
            return True
        if info:                                   # an older helper: replace it
            try:
                import http.client
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                c.request("GET", "/quit")
                c.getresponse().read()
            except Exception:
                pass
            time.sleep(0.5)
        if time.time() - _started["t"] < 10:
            pass                                   # just launched one: wait for it
        else:
            py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"
            env = dict(os.environ)
            env.pop("LD_PRELOAD", None)
            subprocess.Popen([py, "-I", os.path.join(_HERE, "novaplay_cffi_helper.py"), str(port)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=env, close_fds=True,
                             start_new_session=True, cwd="/")
            _started["t"] = time.time()
        for _ in range(50):
            time.sleep(0.1)
            info = _ping(port, 0.5)
            if info and info.get("v") == VERSION:
                return True
    return False


class _CI(dict):
    """Case-insensitive header dict."""
    def __init__(self, d):
        super().__init__()
        self._k = {}
        for k, v in (d or {}).items():
            self[k] = v

    def __setitem__(self, k, v):
        self._k[k.lower()] = k
        super().__setitem__(k, v)

    def __getitem__(self, k):
        return super().__getitem__(self._k.get(k.lower(), k))

    def get(self, k, default=None):
        kk = self._k.get(str(k).lower())
        return super().get(kk, default) if kk else default

    def __contains__(self, k):
        return str(k).lower() in self._k


class _Resp(object):
    def __init__(self, conn, resp, stream):
        self._conn, self._resp = conn, resp
        self.status_code = int(resp.getheader("X-NP-Status") or 0)
        self.url = base64.b64decode(resp.getheader("X-NP-Url") or "").decode("utf-8", "replace")
        try:
            self.headers = _CI(json.loads(base64.b64decode(resp.getheader("X-NP-Headers") or "e30=")))
        except Exception:
            self.headers = _CI({})
        self._content = None if stream else resp.read()
        if not stream:
            self.close()

    @property
    def content(self):
        if self._content is None:
            self._content = self._resp.read()
            self.close()
        return self._content

    @property
    def text(self):
        ct = self.headers.get("content-type", "") or ""
        enc = None
        if "charset=" in ct.lower():
            enc = ct.lower().split("charset=", 1)[1].split(";")[0].strip().strip('"') or None
        try:
            return self.content.decode(enc or "utf-8")
        except (LookupError, UnicodeDecodeError):
            return self.content.decode("utf-8", "replace")

    def iter_content(self, chunk_size=65536):
        while True:
            b = self._resp.read(chunk_size)
            if not b:
                break
            yield b
        self.close()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


class _Client(object):
    def request(self, method, url, headers=None, data=None, impersonate=None,
                timeout=30, allow_redirects=True, stream=False, **_ignored):
        if not _ensure():
            raise RuntimeError("cffi helper not available")
        headers = dict(headers or {})
        if isinstance(data, dict):
            # curl_cffi labels a dict body as a form by itself; once it is text
            # here, the label has to be added (TopCinema's Server.php got an
            # unlabelled body inside Enigma2 and answered 149 bytes)
            from urllib.parse import urlencode
            data = urlencode(data)
            if not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = "application/x-www-form-urlencoded"
        if isinstance(data, str):
            data = data.encode("utf-8")
        payload = {"method": method, "url": url, "headers": dict(headers or {}),
                   "impersonate": impersonate, "timeout": timeout,
                   "allow_redirects": allow_redirects, "stream": bool(stream),
                   "verify": bool(_ignored.get("verify", False)),
                   "data_b64": base64.b64encode(data).decode() if data is not None else None}
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=(timeout or 30) + 15)
        body = json.dumps(payload).encode()
        conn.request("POST", "/r", body=body, headers={"Content-Type": "application/json",
                                                        "Content-Length": str(len(body))})
        resp = conn.getresponse()
        if resp.status != 200:
            err = base64.b64decode(resp.getheader("X-NP-Error") or "").decode("utf-8", "replace")
            conn.close()
            raise RuntimeError(err or "cffi helper HTTP {}".format(resp.status))
        return _Resp(conn, resp, stream)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, data=None, **kw):
        return self.request("POST", url, data=data, **kw)


_client = _Client()


def client():
    return _client


if __name__ == "__main__":
    try:
        _serve(int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
    except OSError:
        pass          # port taken: another helper is already serving
