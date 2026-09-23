#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PATCH 103 - false Cloudflare "challenge" on ordinary small pages. Run from the plugin folder:

    python3 apply_cf_detector.py

_is_cloudflare_challenge() treats a page under 5000 bytes that merely contains the word "cloudflare" as a
challenge. Every Cloudflare site with Web Analytics injects <script src="https://static.cloudflareinsights.com/
beacon.min.js/...">, so ordinary small pages (streamwish.to's 819-byte loader, byseraguci.com's 1972-byte app
shell) were thrown away and fetch() returned nothing. The detector now ignores URLs of the two benign hosts
(cloudflareinsights, cdnjs.cloudflare.com) before looking for markers. Real challenges are unchanged: they carry
strong markers ("just a moment", "challenge-platform", cf-chl ...) or challenges.cloudflare.com/turnstile.

Wraps the existing function instead of editing its body, so it also works on top of earlier patches to it.
Idempotent, all-or-nothing, CRLF-safe, backup: extractors/net.py.bak-p103
"""
import sys, shutil, py_compile

NET = "extractors/net.py"
DEF = "def _is_cloudflare_challenge(html):\n"

WRAPPER = '''# [PATCH 103] URLs of these hosts appear on ordinary pages (analytics beacon, CDN libraries)
_BENIGN_CF_URL_RE = re.compile(
    r'https?://[^\\s"\\'<>]*(?:cloudflareinsights|cdnjs\\.cloudflare)[^\\s"\\'<>]*', re.I)


def _is_cloudflare_challenge(html):
    """[PATCH 103] See _is_cloudflare_challenge_raw; benign Cloudflare-hosted URLs are ignored first."""
    if html:
        html = _BENIGN_CF_URL_RE.sub(" ", html)
    return _is_cloudflare_challenge_raw(html)


def _is_cloudflare_challenge_raw(html):
'''

raw = open(NET, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
s = raw.replace("\r\n", "\n")
if "_is_cloudflare_challenge_raw" in s:
    sys.exit("already applied - nothing to do")
if s.count(DEF) != 1:
    sys.exit("ABORT (nothing written): '%s' found %d times" % (DEF.strip(), s.count(DEF)))
if "\nimport re\n" not in s:
    sys.exit("ABORT (nothing written): net.py does not import re")
s = s.replace(DEF, WRAPPER, 1)
shutil.copy(NET, NET + ".bak-p103")
open(NET, "wb").write((s.replace("\n", "\r\n") if crlf else s).encode("utf-8"))
try:
    py_compile.compile(NET, doraise=True)
except Exception as e:
    shutil.copy(NET + ".bak-p103", NET)
    sys.exit("COMPILE FAILED, file restored: %s" % e)
print("OK: Cloudflare detector ignores analytics-beacon/CDN URLs (backup: %s.bak-p103)" % NET)
