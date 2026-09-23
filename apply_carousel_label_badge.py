#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PATCH 112 - carousel release/quality label badge (HD/CAM/WEB-DL/etc), matching the grid's
badge from PATCH 106. The carousel already has widgets for year and rating (added in an
earlier patch), reading item.get("year")/item.get("rating") -- this adds the same treatment
for item.get("label"), which EgyDead's extractor already fills in.

Positioned in the bottom strip, just above the existing watched/resume badge (cresumeMark),
so the two don't overlap -- the same "stack above the other bottom widget" arrangement the
grid already uses (its progress bar sits above its own label badge).

Touches:
  plugin_gridlist.py  - new clabel{i} widget declared in build_carousel_xml()
  plugin_screen_home.py - widget created, positioned, hidden alongside the other badges,
                          and painted from item.get("label")

Run from the plugin folder:  python3 apply_carousel_label_badge.py
Idempotent, all-or-nothing, CRLF-safe, backups: plugin_gridlist.py.bak-p112, plugin_screen_home.py.bak-p112
"""
import re, sys, shutil, py_compile

GL = "plugin_gridlist.py"
PH = "plugin_screen_home.py"


def patch_gridlist(s):
    if "[PATCH 112]" in s:
        return s, False
    anchor = '''        # [PATCH 53] year badge \u2014 red box, top-left (matches grid)
        parts.append('<widget name="cyearBadge{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#F0F6FC" backgroundColor="#C0392B" transparent="0" cornerRadius="{cr2}" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_bf, cr2=max(3, sc(8))))'''
    if s.count(anchor) != 1:
        raise SystemExit("ABORT (nothing written): gridlist year-badge anchor found %d times (expected 1)" % s.count(anchor))
    addition = '''
        # [PATCH 112] release/quality label badge \u2014 gold box, matches the grid's own
        # label badge; sits just above cresumeMark in _applyCarouselGeometry.
        parts.append('<widget name="clabel{i}" position="0,0" size="1,1" font="Regular;{f}" foregroundColor="#0D1117" backgroundColor="#FFD740" transparent="0" cornerRadius="{cr2}" zPosition="6" halign="center" valign="center" />'.format(i=i, f=_bf, cr2=max(3, sc(8))))'''
    return s.replace(anchor, anchor + addition, 1), True


def patch_home(s):
    if "[PATCH 112]" in s:
        return s, False
    changed = False

    # 1) create the widget in __init__
    old_init = '''            # [PATCH 53] carousel year badge \u2014 red box, top-left
            self["cyearBadge%d" % i] = Label("")'''
    if s.count(old_init) != 1:
        raise SystemExit("ABORT (nothing written): home init anchor found %d times (expected 1)" % s.count(old_init))
    new_init = old_init + '''
            self["clabel%d" % i] = Label("")               # [PATCH 112]'''
    s = s.replace(old_init, new_init, 1)
    changed = True

    # 2) position it just above cresumeMark
    old_geom = '''            self._moveResize('cyearBadge%d' % widget_id, x + 12, y + 22, _yw, 28)
            self._moveResize('cresumeMark%d' % widget_id, x + 10, y + h - 34, w - 20, 30)'''
    if s.count(old_geom) != 1:
        raise SystemExit("ABORT (nothing written): home geometry anchor found %d times (expected 1)" % s.count(old_geom))
    new_geom = '''            self._moveResize('cyearBadge%d' % widget_id, x + 12, y + 22, _yw, 28)
            # [PATCH 112] label badge: bottom strip, stacked just above cresumeMark
            _lh = 26 if is_big else 22
            self._moveResize('clabel%d' % widget_id, x + 10, y + h - 34 - _lh - 2, w - 20, _lh)
            self._moveResize('cresumeMark%d' % widget_id, x + 10, y + h - 34, w - 20, 30)'''
    s = s.replace(old_geom, new_geom, 1)

    # 3) hide it everywhere the other badges get hidden (3 distinct indent/variable shapes)
    hide_pattern = re.compile(
        r'(?P<indent>[ \t]*)self\["cratingBadge%d" % (?P<var>i|widget_id)\]\.hide\(\)\n'
        r'(?P=indent)self\["cyearBadge%d" % (?P=var)\]\.hide\(\)'
    )
    s, n = hide_pattern.subn(
        lambda m: m.group(0) + "\n" + m.group("indent") + 'self["clabel%d" % ' + m.group("var") + '].hide()',
        s)
    if n != 6:
        raise SystemExit("ABORT (nothing written): expected 6 hide-pair sites, found %d" % n)

    # 4) paint it from item.get("label"), right after the year-badge paint block
    old_paint = '''        _cy = str(item.get("year") or "")[:4]
        if _cy:
            self["cyearBadge%d" % widget_id].setText(_cy)
            self["cyearBadge%d" % widget_id].show()
        else:
            self["cyearBadge%d" % widget_id].hide()'''
    if s.count(old_paint) != 1:
        raise SystemExit("ABORT (nothing written): home paint anchor found %d times (expected 1)" % s.count(old_paint))
    new_paint = old_paint + '''
        # [PATCH 112] release/quality label (HD/CAM/WEB-DL/\u0645\u062f\u0628\u0644\u062c/...)
        _lb = str(item.get("label") or "").strip()
        if _lb:
            self["clabel%d" % widget_id].setText(_lb)
            self["clabel%d" % widget_id].show()
        else:
            self["clabel%d" % widget_id].hide()'''
    s = s.replace(old_paint, new_paint, 1)

    return s, changed


def apply_one(path, fn):
    raw = open(path, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")
    new_s, changed = fn(s)
    if not changed:
        return False
    shutil.copy(path, path + ".bak-p112")
    open(path, "wb").write((new_s.replace("\n", "\r\n") if crlf else new_s).encode("utf-8"))
    return True


def main():
    already = "[PATCH 112]" in open(PH, "rb").read().decode("utf-8")
    if already:
        sys.exit("already applied - nothing to do")

    changed_gl = apply_one(GL, patch_gridlist)
    changed_ph = apply_one(PH, patch_home)

    try:
        py_compile.compile(GL, doraise=True)
        py_compile.compile(PH, doraise=True)
    except Exception as e:
        if changed_gl:
            shutil.copy(GL + ".bak-p112", GL)
        if changed_ph:
            shutil.copy(PH + ".bak-p112", PH)
        sys.exit("COMPILE FAILED, files restored: %s" % e)

    print("OK: carousel now shows the release/quality label badge too (backups: %s.bak-p112, %s.bak-p112)" % (GL, PH))


main()
