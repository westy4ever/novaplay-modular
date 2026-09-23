# -*- coding: utf-8 -*-
"""PATCH 118 regression test: wecima_sarl's URL scheme-repair logic. The real sample below is
a confirmed real value from a wecima.style capture (2026-09-23): the ?mycimafsd= parameter on
an embedded akhbarworld.online frame."""

import sys, types

def _load_wecima_sarl():
    src = open("extractors/wecima_sarl.py", encoding="utf-8").read()
    src = src.replace(
        "from .base import BaseExtractor, fetch, log, urljoin",
        "from extractors.base import BaseExtractor, fetch, log, urljoin",
    )
    mod = types.ModuleType("wecima_sarl_under_test")
    exec(compile(src, "wecima_sarl.py", "exec"), mod.__dict__)
    return mod

_mod = _load_wecima_sarl()


def _ex():
    return _mod.WecimaSarlExtractor()


def test_real_mycimafsd_sample_decodes_correctly():
    e = _ex()
    result = e._decode_wecima_url("aHR0cHM6Ly9mYXN0dmlwLnNwYWNlL2Uv")
    assert result == "https://fastvip.space/e/", result


def test_already_correct_https_url_passed_through_unmodified():
    # a raw https:// URL given directly (the function's own early return for this case)
    e = _ex()
    assert e._decode_wecima_url("https://example.com/x") == "https://example.com/x"


def test_already_correct_http_url_passed_through_unmodified():
    e = _ex()
    assert e._decode_wecima_url("http://example.com/x") == "http://example.com/x"


def test_no_https_corrupted_into_http_s():
    # the exact bug: "https://..." must never become "http://s://..."
    e = _ex()
    result = e._decode_wecima_url("aHR0cHM6Ly9mYXN0dmlwLnNwYWNlL2Uv")
    assert "http://s:" not in (result or ""), result


def test_empty_and_none_input_safe():
    e = _ex()
    assert e._decode_wecima_url("") is None
    assert e._decode_wecima_url(None) is None
