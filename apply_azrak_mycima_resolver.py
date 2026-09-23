#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PATCH 121 - adds a dedicated resolver for azrak.mycima.cv's "MYCIMA ULTRA STREAM"
download-preparation page. Confirmed against a real capture and its network log: this page is
NOT a normal embed/download page the generic resolver can handle -- its own JS polls
"/download/<token>?check_status=1" (retrying every second until the server responds) and only
then navigates to "/download/<token>?start_download=1" for the real file. Ground truth from a
real successful download in the network log: both requests returned status 200. Without this,
the generic fallback just fetches the page's initial HTML, finds nothing resembling a stream
URL in it (the real link only exists after the JS polling completes), and silently gives up --
matching exactly what the real device log showed: the page fetches fine, then nothing.

Retries check_status a bounded number of times (matching the site's own 1-second retry
interval) rather than assuming it succeeds on the first try, even though it did in the one real
session captured so far.

Run from the plugin folder:  python3 apply_azrak_mycima_resolver.py
Idempotent, all-or-nothing, CRLF-safe, backup: extractors/hosts.py.bak-p121
"""
import sys, shutil, py_compile

HO = "extractors/hosts.py"

NEW_FUNCTION = '''def resolve_azrak_mycima(url):
    """
    [PATCH 121] azrak.mycima.cv's download-preparation page. Confirmed against a real capture:
    the page's own JS polls "?check_status=1" every second until it succeeds, then navigates
    to "?start_download=1" for the real file. Real network log ground truth: both requests
    returned HTTP 200 in a real successful session. This mirrors that exact sequence, with a
    bounded number of retries rather than polling forever.
    """
    try:
        base_url = url.split("?")[0]
        check_url = base_url + "?check_status=1"
        max_attempts = 10
        for attempt in range(max_attempts):
            html, final_url = fetch(check_url, referer=url)
            if html:
                log("azrak.mycima: check_status ready after {} attempt(s)".format(attempt + 1))
                return base_url + "?start_download=1"
            time.sleep(1)
        log("azrak.mycima: check_status never became ready after {} attempts".format(max_attempts))
        return None
    except Exception as e:
        log("azrak.mycima: error: {}".format(e))
        return None


'''

DISPATCH_OLD = '''    "dhcplay":         resolve_dhcplay,
    "dhcplay.com":     resolve_dhcplay,'''

DISPATCH_NEW = '''    "dhcplay":         resolve_dhcplay,
    "dhcplay.com":     resolve_dhcplay,
    "mycima.cv":       resolve_azrak_mycima,   # [PATCH 121]
    "azrak.mycima.cv": resolve_azrak_mycima,'''


def main():
    raw = open(HO, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")
    if "[PATCH 121]" in s:
        sys.exit("already applied - nothing to do")

    # "import time" is already present in hosts.py (used elsewhere) -- no new import needed.
    anchor = "\ndef resolve_dhcplay(url):"
    if s.count(anchor) != 1:
        sys.exit("ABORT (nothing written): function anchor found %d times (expected 1)" % s.count(anchor))
    s = s.replace(anchor, "\n" + NEW_FUNCTION + "def resolve_dhcplay(url):", 1)

    if s.count(DISPATCH_OLD) != 1:
        sys.exit("ABORT (nothing written): dispatch anchor found %d times (expected 1)" % s.count(DISPATCH_OLD))
    s = s.replace(DISPATCH_OLD, DISPATCH_NEW, 1)

    shutil.copy(HO, HO + ".bak-p121")
    open(HO, "wb").write((s.replace("\n", "\r\n") if crlf else s).encode("utf-8"))
    try:
        py_compile.compile(HO, doraise=True)
    except Exception as e:
        shutil.copy(HO + ".bak-p121", HO)
        sys.exit("COMPILE FAILED, file restored: %s" % e)
    print("OK: azrak.mycima.cv download links are now resolved through the check_status/start_download flow (backup: %s.bak-p121)" % HO)


main()
