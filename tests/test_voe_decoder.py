# -*- coding: utf-8 -*-
"""PATCH 102 (Voe) regression tests. The stub is the real 745-byte page voe.sx returned on the box on
2026-09-21; the payload prefix is the real start of the obfuscated JSON on jamesbornmain.com."""

import re
import json
import base64
import extractors.hosts as h

VOE_STUB = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Redirecting...</title>
</head>
<body>
<script>
        if (typeof localStorage !== 'undefined') {
            const permanentToken = localStorage.getItem('permanentToken');
            if (permanentToken) {
                const currentUrl = new URL(window.location.href);
                currentUrl.searchParams.set('permanentToken', permanentToken);
                window.location.href = currentUrl.toString();
            } else {
                window.location.href = 'https://jamesbornmain.com/e/0eqlw6fhwfeg';
            }
        } else {
            window.location.href = 'https://jamesbornmain.com/e/0eqlw6fhwfeg';
        }
    </script>
</body>
</html>'''
# real first 90 characters of the payload string on https://jamesbornmain.com/e/0eqlw6fhwfeg
REAL_PREFIX = "CR1J*~MKyE*~pR94^^o1cp^^qmuj~@MayA!!AJMi#&AScp!!qR1f!!HzkL*~JzIe*~BQIo@$o1Io@$MU1A%?Ex9f"


def _rot13(s):
    return h._voe_rot13(s)


def _encode(obj):
    """Independent inverse of Voe's scheme (test-only): json -> base64 -> reverse -> +3 -> base64 -> junk -> rot13."""
    b64 = base64.b64encode(json.dumps(obj).encode()).decode()[::-1]
    shifted = "".join(chr((ord(c) + 3) % 256) for c in b64)
    s = base64.b64encode(shifted.encode("latin-1")).decode()
    toks, out = ("@$", "^^", "~@", "%?", "*~", "!!", "#&"), []
    for i, c in enumerate(s):
        out.append(c)
        if i % 4 == 3:
            out.append(toks[(i // 4) % len(toks)])
    return _rot13("".join(out))


def _page(enc):
    return '<html><script type="application/json">%s</script><script>var a=1;</script></html>' % json.dumps([enc])


def test_real_payload_prefix_unscrambles_to_the_real_json_tail():
    tail = h._voe_unscramble(REAL_PREFIX)                    # base64 text of the END of the JSON
    assert tail == "iLCJzaXRlX25hbWUiOiJqYW1lc2Jvcm5tYWluLmNvbSJ9"
    assert base64.b64decode(tail[1:]) == b',"site_name":"jamesbornmain.com"}'


def test_payload_roundtrip_and_stream_choice():
    obj = {"source": "https://ugc.cloudwindow-route.com/x/master.m3u8?t=1", "direct_access_url": "https://d/x.mp4", "site_name": "j"}
    assert h._voe_decode_payload(_encode(obj)) == obj
    assert h._voe_payload_stream(_page(_encode(obj))) == obj["source"]                 # HLS preferred
    only_mp4 = {"direct_access_url": "https://d/x.mp4"}
    assert h._voe_payload_stream(_page(_encode(only_mp4))) == "https://d/x.mp4"


def test_garbage_payload_is_none_not_a_crash():
    assert h._voe_decode_payload("not*~a@$payload") is None
    assert h._voe_payload_stream("<script type='application/json'>[\"short\"]</script>") is None
    assert h._voe_payload_stream("") is None and h._voe_payload_stream(None) is None


def test_resolve_voe_follows_the_stub_and_decodes_the_real_page():
    real_page = _page(_encode({"source": "https://ugc.cloudwindow-route.com/engine/hls2/0eqlw6fhwfeg_,n,.urlset/master.m3u8?t=abc"}))
    calls = []

    def fake(url, referer=None, extra_headers=None, **kw):
        calls.append((url, referer))
        if url == "https://voe.sx/e/0eqlw6fhwfeg":
            return VOE_STUB, url
        if url == "https://jamesbornmain.com/e/0eqlw6fhwfeg":
            return real_page, url
        return None, url

    saved, h.fetch = h.fetch, fake
    try:
        out = h.resolve_voe("https://voe.sx/e/0eqlw6fhwfeg")
    finally:
        h.fetch = saved
    assert out == "https://ugc.cloudwindow-route.com/engine/hls2/0eqlw6fhwfeg_,n,.urlset/master.m3u8?t=abc|Referer=https://jamesbornmain.com/", out
    assert [c[0] for c in calls] == ["https://voe.sx/e/0eqlw6fhwfeg", "https://jamesbornmain.com/e/0eqlw6fhwfeg"]
