# -*- coding: utf-8 -*-
"""Subtitle Studio SRT parser: encodings, position tags, italic,
cue boundaries. Pure-function layer — no screen required."""

from novaplay_substudio import parse_srt, _estimate_width

import os
import tempfile


def _srt(body):
    fd, path = tempfile.mkstemp(suffix=".srt")
    with os.fdopen(fd, "wb") as f:
        f.write(body)
    return path


def test_utf8_bom_parse():
    p = _srt(b"\xef\xbb\xbf1\n00:00:01,000 --> 00:00:03,000\nHello\n\n")
    cues = parse_srt(p)
    os.unlink(p)
    assert len(cues) == 1
    assert cues[0][0] == 1000 and cues[0][1] == 3000
    assert cues[0][2] == ["Hello"]

def test_cp1256_arabic_parse():
    p = _srt("1\n00:00:01,000 --> 00:00:02,000\nمرحبا\n\n".encode("cp1256"))
    cues = parse_srt(p)
    os.unlink(p)
    assert cues and cues[0][2] == ["مرحبا"]

def test_v3_position_and_italic_tags():
    body = (b"1\n00:00:05,000 --> 00:00:07,000\nX1:100 X2:280 Y1:50 Y2:100\n"
            b"<i>song lyric line</i>\n\n")
    p = _srt(body)
    cues = parse_srt(p)
    os.unlink(p)
    assert cues[0][3].get("X1") == 100 and cues[0][3].get("Y1") == 50
    assert cues[0][4] is True

def test_cue_boundaries_inclusive_exclusive():
    body = b"1\n00:00:10,000 --> 00:00:12,000\nA\n\n2\n00:00:12,000 --> 00:00:14,000\nB\n\n"
    p = _srt(body)
    cues = parse_srt(p)
    os.unlink(p)
    assert cues[0][1] == cues[1][0]

def test_malformed_blocks_skipped():
    body = b"garbage no timing\n\n1\n00:00:01,000 --> 00:00:02,000\nReal\n\n"
    p = _srt(body)
    cues = parse_srt(p)
    os.unlink(p)
    assert len(cues) == 1 and cues[0][2] == ["Real"]

def test_tags_stripped_from_body():
    body = b"1\n00:00:01,000 --> 00:00:02,000\n<b>bold</b> and <font x>text</font>\n\n"
    p = _srt(body)
    cues = parse_srt(p)
    os.unlink(p)
    assert cues[0][2] == ["bold and text"]

def test_arabic_width_estimate():
    assert _estimate_width("شش", 40) > _estimate_width("  ", 40)
    assert _estimate_width("abc", 40) > 0