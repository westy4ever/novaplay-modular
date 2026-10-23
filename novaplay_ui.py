# -*- coding: utf-8 -*-
"""NovaPlay -- small widget helpers shared by every screen (one copy
instead of one per screen).

    class NovaPlayHub(UiHelpers, Screen): ...

    self._color("name", bg="#F2C230", fg="#0C1017")   recolour a widget
    self._mr("name", x, y, w, h)                      move (+ resize), 1080p
    self._mrc(...)                                    same as _mr
                                                      design values, scaled;
                                                      skipped when nothing changes
"""
from enigma import ePoint, eSize

try:
    from skin import parseColor
except Exception:                                    # pragma: no cover
    parseColor = None

from plugin_gridlist import sc


def set_color(screen, name, bg=None, fg=None):
    try:
        inst = screen[name].instance
        if inst is None or parseColor is None:
            return
        if bg:
            inst.setBackgroundColor(parseColor(bg))
        if fg:
            inst.setForegroundColor(parseColor(fg))
        inst.invalidate()
    except Exception:
        pass


def move_resize(screen, name, x, y, w=None, h=None):
    """[perf] each move / resize makes Enigma2 redraw the widget -- a call
    that would not change a pixel is skipped (cache in screen._geo)"""
    inst = screen[name].instance
    if inst is None:
        return
    geo = screen.__dict__.setdefault("_geo", {})
    pos = (sc(x), sc(y))
    size = (max(1, sc(w)), max(1, sc(h))) if w is not None else None
    old = geo.get(name)
    if old and old[0] == pos and (size is None or old[1] == size):
        return
    try:
        if not old or old[0] != pos:
            inst.move(ePoint(*pos))
        if size is not None and (not old or old[1] != size):
            inst.resize(eSize(*size))
        geo[name] = (pos, size if size is not None else (old[1] if old else None))
    except Exception:
        pass


class UiHelpers(object):
    """mixin: put it before Screen in the class bases"""

    def _color(self, name, bg=None, fg=None):
        set_color(self, name, bg, fg)

    def _mr(self, name, x, y, w=None, h=None):
        move_resize(self, name, x, y, w, h)

    _mrc = _mr
