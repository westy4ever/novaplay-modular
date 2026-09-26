# -*- coding: utf-8 -*-
"""PATCH 121 regression test: azrak.mycima.cv download-preparation resolver. The check_status
-> start_download sequence and its "ready on the first check" timing are confirmed against a
real network log from an actual successful download (2026-09-23): both requests returned
HTTP 200."""

import extractors.hosts as h

REAL_URL = ("https://azrak.mycima.cv/download/747b737f807a91d0c422d837071dea2bcc2c0e2bea6c2dc"
            "421cb27cb1ee4748dbfc2cbc8b9acc1b37491837382741ccd19f81ff6370021cecfdbbbb9b7c2b88e8"
            "e82a2b7bca5a9b3c5b6b7cbb876b8c1cbbcb3a891b2cdc2bcaeadbab6ac7eaf8f978fb7909689abcc7e"
            "b0ab9da28fb7aeb8cb8fb68fc5a09cadc0947ba8d5a0b27486a8acadc48ebaa574acaec6b5")
EXPECTED = REAL_URL + "?start_download=1"


def _patched(fake_fetch):
    class Ctx(object):
        def __enter__(self):
            self.saved_fetch = h.fetch
            self.saved_sleep = h.time.sleep
            h.fetch = fake_fetch
            h.time.sleep = lambda x: None
        def __exit__(self, *a):
            h.fetch = self.saved_fetch
            h.time.sleep = self.saved_sleep
    return Ctx()


def test_ready_on_first_check_matches_real_network_log_timing():
    calls = []
    def fake(u, referer=None, **k):
        calls.append(u)
        return "<html>ok</html>", u
    with _patched(fake):
        result = h.resolve_azrak_mycima(REAL_URL)
    assert result == EXPECTED, result
    assert len(calls) == 1, "real session needed only one check_status call"


def test_ready_after_a_few_retries():
    attempts = [0]
    def fake(u, referer=None, **k):
        attempts[0] += 1
        if attempts[0] < 3:
            return None, u
        return "<html>ok</html>", u
    with _patched(fake):
        result = h.resolve_azrak_mycima(REAL_URL)
    assert result == EXPECTED, result
    assert attempts[0] == 3


def test_never_ready_gives_up_cleanly():
    with _patched(lambda u, referer=None, **k: (None, u)):
        result = h.resolve_azrak_mycima(REAL_URL)
    assert result is None


def test_existing_query_params_on_input_url_are_stripped_before_use():
    # a URL that already has a query string (e.g. someone passes the check_status URL itself)
    # must still resolve to the correct base + start_download, not double up query params
    with _patched(lambda u, referer=None, **k: ("<html>ok</html>", u)):
        result = h.resolve_azrak_mycima(REAL_URL + "?check_status=1")
    assert result == EXPECTED, result


def test_dispatch_routes_azrak_mycima_to_this_resolver():
    with _patched(lambda u, referer=None, **k: ("<html>ok</html>", u)):
        result = h.resolve_host(REAL_URL)
    assert result == EXPECTED, result


def test_exception_during_fetch_does_not_crash():
    def raises(u, referer=None, **k):
        raise RuntimeError("network exploded")
    with _patched(raises):
        result = h.resolve_azrak_mycima(REAL_URL)
    assert result is None
