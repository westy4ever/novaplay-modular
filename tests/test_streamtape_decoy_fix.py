# -*- coding: utf-8 -*-
"""PATCH T2 regression test: resolve_streamtape's decoy-assignment parsing. STREAMTAPE_FIXTURE
is the real assignment block from a real streamtape.cc embed page capture (The Love Hypothesis
2026, 2026-09-23) -- includes both the decoy assignments (to "ideoolink") and the two correct
ones (to "botlink" and "robotlink"), confirming the fix does not depend on any single element id."""

import sys
import os

sys.path.insert(0, os.getcwd())
import extractors.hosts as _mod

STREAMTAPE_FIXTURE = r'''<div id="ideoolink" style="display:none;">//streamtape.cc/get_vbdeo?id=VmvjYmZAA0IYwk&amp;expires=1790244774&amp;ip=F0yTKRWPES9XKxR&amp;token=wXTgTMv0RPh2</div>
<span id="botlink" style="display:none;">//streamtape.cc/get_video?id=VmvjYmZAA0IYwk&amp;expires=1790244774&amp;ip=F0yTKRWPES9XKxR&amp;token=wXTgTMv0RPh2</span>
<div id="robotlink" style="display:none;">//streamtape.cc/get_video?id=VmvjYmZAA0IYwk&amp;expires=1790244774&amp;ip=F0yTKRWPES9XKxR&amp;token=wXTgTMv0RPh2</div>
<script>document.getElementById('ideoolink').innerHTML = "/streamtape.cc/get_vi" + ''+ ('xcdbdeo?id=VmvjYmZAA0IYwk&expires=1790244774&ip=F0yTKRWPES9XKxR&token=wXTgTMv0RPh2').substring(1).substring(2);
document.getElementById('ideoolink').innerHTML = "//streamtape.cc/get_v" + ''+ ('xnftbdeo?id=VmvjYmZAA0IYwk&expires=1790244774&ip=F0yTKRWPES9XKxR&token=wXTgTMv0RPh2').substring(3).substring(1);
document.getElementById('botlink').innerHTML = '//streamtape.cc/get_v'+ ('xyzaideo?id=VmvjYmZAA0IYwk&expires=1790244774&ip=F0yTKRWPES9XKxR&token=wXTgTMv0RPh2').substring(4);
document.getElementById('robotlink').innerHTML = '//streamtape.cc/get_v'+ ('xcdideo?id=VmvjYmZAA0IYwk&expires=1790244774&ip=F0yTKRWPES9XKxR&token=wXTgTMv0RPh2').substring(2).substring(1);
</script>'''

REAL_CORRECT_URL = ("https://streamtape.cc/get_video?id=VmvjYmZAA0IYwk"
                     "&expires=1790244774&ip=F0yTKRWPES9XKxR&token=wXTgTMv0RPh2")


def _patched(fake_fetch):
    class Ctx(object):
        def __enter__(self):
            self.saved = _mod.fetch
            _mod.fetch = fake_fetch
        def __exit__(self, *a):
            _mod.fetch = self.saved
    return Ctx()


def test_real_page_resolves_to_the_exact_correct_url():
    with _patched(lambda u, referer=None, **k: (STREAMTAPE_FIXTURE, u)):
        result = _mod.resolve_streamtape("https://streamtape.cc/e/VmvjYmZAA0IYwk")
    assert result == REAL_CORRECT_URL, result


def test_decoy_assignments_are_never_returned():
    # the exact bug this patches: two of the four real assignments decode to garbled endpoint
    # names ("get_vbdeo", "get_vibdeo") -- neither must ever be returned as the resolved URL.
    with _patched(lambda u, referer=None, **k: (STREAMTAPE_FIXTURE, u)):
        result = _mod.resolve_streamtape("https://streamtape.cc/e/VmvjYmZAA0IYwk")
    assert "get_vbdeo" not in result
    assert "get_vibdeo" not in result
    assert "get_video" in result


def test_substring_chain_evaluator_matches_manual_trace():
    # directly verify the chain evaluator against all four real assignments, independent of
    # which one the resolver ends up picking
    cases = [
        ("/streamtape.cc/get_vi", "xcdbdeo?id=X", ".substring(1).substring(2)",
         "/streamtape.cc/get_vibdeo?id=X"),
        ("//streamtape.cc/get_v", "xnftbdeo?id=X", ".substring(3).substring(1)",
         "//streamtape.cc/get_vbdeo?id=X"),
        ("//streamtape.cc/get_v", "xyzaideo?id=X", ".substring(4)",
         "//streamtape.cc/get_video?id=X"),
        ("//streamtape.cc/get_v", "xcdideo?id=X", ".substring(2).substring(1)",
         "//streamtape.cc/get_video?id=X"),
    ]
    for prefix, computed, chain, expected in cases:
        got = _mod._evaluate_streamtape_assignment(prefix, computed, chain)
        assert got == expected, (got, expected)


def test_fetch_failure_returns_none():
    with _patched(lambda u, referer=None, **k: (None, u)):
        assert _mod.resolve_streamtape("https://streamtape.cc/e/x") is None


def test_page_with_no_recognizable_assignment_falls_through_safely():
    with _patched(lambda u, referer=None, **k: ("<html>nothing relevant here</html>", u)):
        result = _mod.resolve_streamtape("https://streamtape.cc/e/x")
    assert result is None
