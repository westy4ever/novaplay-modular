# -*- coding: utf-8 -*-
"""PATCH 105 regression tests: novaplay_proxy.py's Range-triggered-403 fallback.

MockCDN reproduces EXACTLY what curl proved against the real mxcontent.net on
2026-09-22: a plain GET returns 200 with real bytes and a correct Content-Length;
the identical request with ANY Range header returns 403, independent of User-Agent.
These tests run the actual LocalProxyHandler over real sockets against that mock,
then verify the client-facing response is byte-exact against the true source data.
"""

import threading
import http.server
import urllib.request

import novaplay_proxy as np

VIDEO = bytes((i % 256) for i in range(2_000_000))  # 2MB deterministic fake "video"


class _MockCDN(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Range"):
            body = b"Forbidden"
            self.send_response(403)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(VIDEO)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(VIDEO)

    def log_message(self, *a):
        pass


_cdn = http.server.HTTPServer(("127.0.0.1", 0), _MockCDN)
threading.Thread(target=_cdn.serve_forever, daemon=True).start()
_CDN_URL = "http://127.0.0.1:{}/v2/fake.mp4".format(_cdn.server_port)

_proxy = http.server.HTTPServer(("127.0.0.1", 0), np.LocalProxyHandler)
threading.Thread(target=_proxy.serve_forever, daemon=True).start()
_PROXY_PORT = _proxy.server_port


def _fetch(range_header=None):
    req = urllib.request.Request("http://127.0.0.1:{}/{}".format(_PROXY_PORT, _CDN_URL))
    if range_header:
        req.add_header("Range", range_header)
    r = urllib.request.urlopen(req, timeout=10)
    return r.status, dict(r.headers), r.read()


def test_plain_get_through_proxy_is_unaffected():
    status, _hdrs, body = _fetch(None)
    assert status == 200 and body == VIDEO


def test_mid_file_range_is_emulated_correctly():
    status, hdrs, body = _fetch("bytes=500000-599999")
    assert status == 206
    assert body == VIDEO[500000:600000]
    assert hdrs.get("Content-Range") == "bytes 500000-599999/2000000"
    assert hdrs.get("Content-Length") == "100000"


def test_open_ended_range_serves_remaining_content():
    status, hdrs, body = _fetch("bytes=0-")
    assert status == 206 and body == VIDEO
    assert hdrs.get("Content-Range") == "bytes 0-1999999/2000000"


def test_tail_range_is_byte_exact():
    status, hdrs, body = _fetch("bytes=1999990-1999999")
    assert status == 206 and len(body) == 10
    assert body == VIDEO[1999990:2000000]
