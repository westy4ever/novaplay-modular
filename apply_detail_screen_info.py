#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PATCH 111 - show the new EgyDead info-box fields (genre, quality print, country, channel,
runtime) on the Detail screen, using the existing meta/source lines instead of adding new
widgets. "genres" was ALREADY read by this screen's code (data.get("genres")) -- it was simply
never being supplied by any extractor before PATCH 110, so that part needs no change at all.

Run from the plugin folder:  python3 apply_detail_screen_info.py
Idempotent, all-or-nothing, CRLF-safe, backup: plugin_screen_detail.py.bak-p111
"""
import sys, shutil, py_compile

PS = "plugin_screen_detail.py"

OLD_META = '''        meta = []
        if data.get("year"):   meta.append(data["year"])
        if data.get("rating"): meta.append("{}/10".format(data["rating"]))
        if data.get("type"):   meta.append(_TYPE_LABELS.get(data["type"], "\u0639\u0646\u0635\u0631"))
        if data.get("genres"): meta.append(data["genres"])
        self["meta"].setText(_wrap_ui_text("   ".join(meta), width=58, max_lines=2))'''

NEW_META = '''        meta = []
        if data.get("year"):    meta.append(data["year"])
        if data.get("rating"):  meta.append("{}/10".format(data["rating"]))
        if data.get("type"):    meta.append(_TYPE_LABELS.get(data["type"], "\u0639\u0646\u0635\u0631"))
        if data.get("quality"): meta.append(data["quality"])          # [PATCH 111] print type e.g. HDCAM
        if data.get("genres"):  meta.append(data["genres"])
        self["meta"].setText(_wrap_ui_text("   ".join(meta), width=58, max_lines=2))'''

OLD_COUNTS = '''        if data.get("year"):
            counts.append("\u0627\u0644\u0633\u0646\u0629: {}".format(data.get("year")))
        self["source"].setText(_wrap_ui_text("\u0627\u0644\u0645\u0635\u062f\u0631: {}  |  {}".format(_site_label(self._site), "  |  ".join(counts)), width=58, max_lines=2))'''

NEW_COUNTS = '''        if data.get("year"):
            counts.append("\u0627\u0644\u0633\u0646\u0629: {}".format(data.get("year")))
        if data.get("country"):                                       # [PATCH 111]
            counts.append("\u0627\u0644\u0628\u0644\u062f: {}".format(data.get("country")))
        if data.get("runtime"):
            counts.append("\u0627\u0644\u0645\u062f\u0629: {}".format(data.get("runtime")))
        if data.get("channel"):
            counts.append("\u0627\u0644\u0642\u0646\u0627\u0629: {}".format(data.get("channel")))
        self["source"].setText(_wrap_ui_text("\u0627\u0644\u0645\u0635\u062f\u0631: {}  |  {}".format(_site_label(self._site), "  |  ".join(counts)), width=58, max_lines=2))'''


def main():
    raw = open(PS, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")
    if "[PATCH 111]" in s:
        sys.exit("already applied - nothing to do")
    if s.count(OLD_META) != 1:
        sys.exit("ABORT (nothing written): meta anchor found %d times (expected 1)" % s.count(OLD_META))
    if s.count(OLD_COUNTS) != 1:
        sys.exit("ABORT (nothing written): counts anchor found %d times (expected 1)" % s.count(OLD_COUNTS))
    s = s.replace(OLD_META, NEW_META, 1)
    s = s.replace(OLD_COUNTS, NEW_COUNTS, 1)
    shutil.copy(PS, PS + ".bak-p111")
    open(PS, "wb").write((s.replace("\n", "\r\n") if crlf else s).encode("utf-8"))
    try:
        py_compile.compile(PS, doraise=True)
    except Exception as e:
        shutil.copy(PS + ".bak-p111", PS)
        sys.exit("COMPILE FAILED, file restored: %s" % e)
    print("OK: Detail screen now shows quality/country/runtime/channel when present (backup: %s.bak-p111)" % PS)


main()
