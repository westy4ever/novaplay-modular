#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PATCH O5 - removes "Coming Soon" from get_categories(). Confirmed against a real capture:
this page isn't a browsable category at all -- its cards have no <a href> whatsoever, just
"notify me when released" / "add to expected favorites" buttons for unreleased content. There's
nothing to navigate to or watch, so it doesn't belong in a list of things you can browse into.

Run from the plugin folder:  python3 apply_onlyflix_remove_coming_soon.py
Idempotent, all-or-nothing, CRLF-safe, backup: extractors/onlyflix.py.bak-o5
"""
import sys
import shutil
import py_compile

OF = "extractors/onlyflix.py"

OLD = '''        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        cats.append({"title": "🆕 قريباً",
                     "url": base + "/coming-soon/",
                     "type": "category", "_action": "category"})
        cats.append({"title": "📱 التطبيقات",
                     "url": base + "/apps/",
                     "type": "category", "_action": "category"})'''

NEW = '''        cats.append({"title": "── أخرى ──", "url": "", "type": "separator"})
        # [PATCH O5] "Coming Soon" removed -- confirmed against a real capture it's not a
        # browsable category at all: its cards have no <a href>, just notify-me buttons for
        # unreleased content, nothing to navigate to or watch.
        cats.append({"title": "📱 التطبيقات",
                     "url": base + "/apps/",
                     "type": "category", "_action": "category"})'''


def main():
    raw = open(OF, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")
    if "[PATCH O5]" in s:
        sys.exit("already applied - nothing to do")
    if s.count(OLD) != 1:
        sys.exit("ABORT (nothing written): anchor found %d times (expected 1)" % s.count(OLD))
    s = s.replace(OLD, NEW, 1)
    shutil.copy(OF, OF + ".bak-o5")
    open(OF, "wb").write((s.replace("\n", "\r\n") if crlf else s).encode("utf-8"))
    try:
        py_compile.compile(OF, doraise=True)
    except Exception as e:
        shutil.copy(OF + ".bak-o5", OF)
        sys.exit("COMPILE FAILED, file restored: %s" % e)
    print("OK: 'Coming Soon' removed from categories (backup: %s.bak-o5)" % OF)


main()
