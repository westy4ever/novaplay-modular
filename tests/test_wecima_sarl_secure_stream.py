# -*- coding: utf-8 -*-
"""PATCH 120 regression test: wecima_sarl secure_stream download decoding. DOWNLOAD_FIXTURE is
the real download section from a real wecima.style capture (Evolution 2026, 2026-09-23). The
expected URL below is independently confirmed against a SEPARATE real capture -- the actual
azrak.mycima.cv download-prep page this exact link leads to -- not just derived from the same
source, so this is a genuine end-to-end, byte-exact ground-truth check."""

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

DOWNLOAD_FIXTURE = """<div class=\"Download--Wecima--Single\"><titleshape>\u0633\u064a\u0631\u0641\u0631\u0627\u062a \u0627\u0644\u062a\u062d\u0645\u064a\u0644</titleshape>


							<ul class=\"List--Download--Wecima--Single\">
																						<li><a class=\"hoverable activable\" target=\"_blank\" href=\"https://akhbarworld.online/?secure_stream=eJwVT21OREEIu9G--XoDeBtaZqLRdY1uNHp6WfhDC6XwfL9_fD0dh_99-uvl-suXq1_4fcTt5_3t5nHIEEiXrUXcahSO1kK7FKmxvIFsLKth-WSL7FaiCVHXGjI0sHMAVJiTFV2G1ZR3bTIqGdW21r1nbiypXdyBDIOwQXVltrQR0E83dJ6Y2QNUJpTpBqK7WkVjpIa-POCYTlm-dZvohlixqeZ5rywUh4W3B-8rtyCrqZunF6MHi-XXrpE4nxk6Xf3BD11wP2UkWpw4_wF0tWHh.6c21f05da2151f92\"><quality>download</quality><resolution><i class=\"ion ion-md-tv\"></i> 720p HD</resolution><i class=\"fal fa-arrow-to-bottom\"></i></a></li>
																																																											</ul>

																				</div>"""

REAL_DOWNLOAD_URL = "https://azrak.mycima.cv/download/747b737f807a91d0c422d837071dea2bcc2c0e2bea6c2dc421cb27cb1ee4748dbfc2cbc8b9acc1b37491837382741ccd19f81ff6370021cecfdbbbb9b7c2b88e8e82a2b7bca5a9b3c5b6b7cbb876b8c1cbbcb3a891b2cdc2bcaeadbab6ac7eaf8f978fb7909689abcc7eb0ab9da28fb7aeb8cb8fb68fc5a09cadc0947ba8d5a0b27486a8acadc48ebaa574acaec6b5"


def _ex():
    return _mod.WecimaSarlExtractor()


def test_real_download_link_decodes_to_the_exact_real_gateway_url():
    e = _ex()
    downloads = e._extract_download_links(DOWNLOAD_FIXTURE)
    assert len(downloads) == 1, downloads
    assert downloads[0]["url"] == REAL_DOWNLOAD_URL, downloads[0]["url"]


def test_resolution_label_extracted_correctly():
    e = _ex()
    downloads = e._extract_download_links(DOWNLOAD_FIXTURE)
    assert downloads[0]["resolution"] == "720p"


def _real_payload():
    # derive the real secure_stream payload straight from the fixture rather than retyping
    # this very long string a second time, which is exactly how a transcription slip creeps in
    import re
    m = re.search(r'secure_stream=([^"&]+)', DOWNLOAD_FIXTURE)
    assert m, "fixture does not contain a secure_stream link"
    return m.group(1)


def test_secure_stream_payload_decodes_directly():
    e = _ex()
    result = e._decode_secure_stream(_real_payload())
    assert result == REAL_DOWNLOAD_URL, result


def test_secure_stream_url_is_not_returned_unchanged():
    # the exact bug this patches: a secure_stream link must NOT be handed back as-is just
    # because it already starts with https://
    e = _ex()
    href = "https://akhbarworld.online/?secure_stream=" + _real_payload()
    result = e._decode_wecima_url(href)
    assert result != href
    assert result == REAL_DOWNLOAD_URL


def test_non_secure_stream_urls_unaffected():
    e = _ex()
    assert e._decode_wecima_url("https://example.com/x") == "https://example.com/x"


def test_garbage_payload_does_not_crash():
    e = _ex()
    assert e._decode_secure_stream("not-valid-base64-!!!") is None
    assert e._decode_secure_stream("") is None
    assert e._decode_secure_stream(None) is None
