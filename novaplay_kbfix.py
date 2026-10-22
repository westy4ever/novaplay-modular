# -*- coding: utf-8 -*-
"""NovaPlay -- in-memory fix for the image's on-screen keyboard Google
suggestions (no system file is changed).

Some OpenATV images ship a keyboard (Screens/VirtualKeyBoard.py, class
textInputSuggestions) that asks Google for
    http://suggestqueries.google.com/complete/search?output=firefox&...
Google now answers that with HTTP 400 (box check 2026-10-23), so the
suggestion list stays empty; and for Arabic it decodes Google's UTF-8 answer
as windows-1256 (gibberish). `client=firefox` over plain http still works
(200) and returns UTF-8 JSON:  ["query", ["suggestion", ...]]

apply() swaps in fixed getGoogleSuggestions / parseGoogleData when that class
exists (any image version, also after image updates); on an image without it,
or if anything looks different, it does nothing.
"""
import json

_URL = "http://suggestqueries.google.com/complete/search?client=firefox&hl={hl}&ds=yt&q={q}"


def _log(msg):
    try:
        from plugin_common import my_log
        my_log("kbfix: " + msg)
    except Exception:
        pass


def apply():
    try:
        import Screens.VirtualKeyBoard as vkb
    except Exception:
        return False
    cls = getattr(vkb, "textInputSuggestions", None)
    if cls is None or getattr(cls, "_np_fixed", False):
        return False
    if not all(hasattr(cls, n) for n in ("getGoogleSuggestions", "parseGoogleData",
                                         "setGoogleSuggestions", "dataError")):
        return False

    def getGoogleSuggestions(self, queryString, hl="en"):
        self.hl = hl or "en"
        self.queryString = queryString
        if not queryString:
            return []
        try:
            from twisted.web.client import getPage
            try:
                from urllib.parse import quote
            except ImportError:                      # pragma: no cover
                from urllib import quote
            url = _URL.format(hl=quote(str(self.hl)), q=quote(queryString.encode("utf-8")
                                                               if not isinstance(queryString, bytes) else queryString))
            getPage(url.encode("utf-8"),
                    headers={b"User-Agent": b"Mozilla/5.0"}).addCallback(
                self.parseGoogleData).addErrback(self.dataError)
        except Exception as e:
            _log("request failed: {}".format(e))
            try:
                self.callback([])
            except Exception:
                pass

    def parseGoogleData(self, output):
        try:
            text = output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output or "")
            data = json.loads(text) if text.strip().startswith("[") else []
            items = [s for s in (data[1] if len(data) > 1 else []) if isinstance(s, str) and s.strip()]
            if items:
                self.setGoogleSuggestions(items)
            else:
                self.callback([])
        except Exception as e:
            _log("parse failed: {}".format(e))
            try:
                self.callback([])
            except Exception:
                pass

    cls.getGoogleSuggestions = getGoogleSuggestions
    cls.parseGoogleData = parseGoogleData
    cls._np_fixed = True
    _log("keyboard Google suggestions fixed in memory")
    return True
