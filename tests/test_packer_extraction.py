# -*- coding: utf-8 -*-
"""PATCH 104 regression test. PACKED_BLOB is a real P.A.C.K.E.R. (base62) encoding of the
ACTUAL string the box's own extract_hosts.decode_packer returned for a live MixDrop page on
2026-09-22 -- built with the standard packer's ",0,{}" tail shape, which the old
_extract_packer_blocks()'s literal ".split('|')))" tail search could never match. Confirmed:
the fixture's tail does not contain that literal string, matching the real page."""

import extractors.hosts as h

PACKED_BLOB = r'''eval(function(p,a,c,k,e,d){e=function(c){return c};if(!''.replace(/^/,String)){while(c--)d[e(c)]=k[c]||e(c);k=[function(e){return d[e]}];e=function(){return'\\w+'};c=1};while(c--)if(k[c])p=p.replace(new RegExp('\\b'+e(c)+'\\b','g'),k[c]);return p}('0.1="//2.3.4/5/6.7?8=9&a=b&c=d";0.e="f";',62,16,'MDCore|wurl|ebij8ni1d|mxcontent|net|v2|pjk1dnm1i6g9qo|mp4|s|S9o2GnOe5WixE6pa0DV8rg|e|1790086788|_t|1790066516|vfile|27fbc01ec976dd64e94411efa1852593'.split('|'),0,{}))'''

REAL_JS = ('MDCore.wurl="//ebij8ni1d.mxcontent.net/v2/pjk1dnm1i6g9qo.mp4?s=S9o2GnOe5WixE6pa0DV8rg'
           '&e=1790086788&_t=1790066516";MDCore.vfile="27fbc01ec976dd64e94411efa1852593";')


def test_fixture_has_the_real_marker_but_not_the_old_literal_tail():
    assert "eval(function(p,a,c,k,e,d){" in PACKED_BLOB
    assert ".split('|')))" not in PACKED_BLOB          # the old code's tail never matches real output


def test_extract_packer_blocks_finds_it():
    blocks = h._extract_packer_blocks(PACKED_BLOB)
    assert len(blocks) == 1


def test_decode_packer_recovers_the_exact_real_captured_string():
    blocks = h._extract_packer_blocks(PACKED_BLOB)
    dec = h.decode_packer(blocks[0])
    assert REAL_JS in dec


def test_unpack_all_makes_mdcore_wurl_reachable():
    texts = h._unpack_all(PACKED_BLOB)
    assert len(texts) == 2                              # [original, decoded]
    assert any("MDCore.wurl" in t for t in texts)


def test_resolve_mixdrop_now_finds_the_stream_through_the_unpacked_text():
    page = "<html><body><script>" + PACKED_BLOB + "</script></body></html>"
    saved, h.fetch = h.fetch, (lambda url, referer=None, **k: (page, url))
    try:
        out = h.resolve_mixdrop("https://mixdrop.ag/e/pjk1dnm1i6g9qo")
    finally:
        h.fetch = saved
    assert out == ("https://ebij8ni1d.mxcontent.net/v2/pjk1dnm1i6g9qo.mp4?s=S9o2GnOe5WixE6pa0DV8rg"
                   "&e=1790086788&_t=1790066516|Referer=https://mixdrop.to/"), out


def test_marker_present_yields_at_least_one_block_not_zero():
    # the exact failure mode observed on the box: marker found, but 0 blocks extracted
    assert PACKED_BLOB.find("eval(function(p,a,c,k,e,d){") != -1
    assert len(h._extract_packer_blocks(PACKED_BLOB)) > 0
