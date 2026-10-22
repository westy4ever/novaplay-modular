# -*- coding: utf-8 -*-
"""NovaPlay -- small pop-up messages ("toasts") shared by the panel screens.

A gold pill slides down under the header, stays ~1.8 s and disappears:
    "★ Added to favorites", "Removed from Continue Watching", ...

Use:
    skin  += TOAST_XML                      (before </screen>)
    __init__:  toast_init(self)             (after the other self[...] widgets)
    anywhere:  toast(self, u"Added to favorites")
"""
from Components.Label import Label
from enigma import eTimer, ePoint

try:
    from plugin_gridlist import sc
except Exception:                                   # pragma: no cover
    def sc(v):
        return int(round(v))

_X, _Y, _W, _H = 660, 128, 600, 56
_FROM = 92                       # slides down from here
_STEPS, _STEP_MS, _HOLD_MS = 8, 18, 1800

TOAST_XML = ('<widget name="toast" position="{x},{y}" size="{w},{h}" font="Regular;24" halign="center" '
             'valign="center" foregroundColor="#0C1017" backgroundColor="#F2C230" cornerRadius="18" '
             'zPosition="30" />').format(x=_X, y=_Y, w=_W, h=_H)


def toast_init(screen):
    screen["toast"] = Label("")
    screen._toast_f = 0
    screen._toastAnim = eTimer()
    screen._toastAnim.callback.append(lambda: _tick(screen))
    screen._toastHide = eTimer()
    screen._toastHide.callback.append(lambda: _hide(screen))
    # Enigma2 exec()s anything in onLayoutFinish that isn't a bound method,
    # so these must be real methods (a lambda crashed the screen on open)
    h = _Hooks(screen)
    screen._toast_hooks = h
    screen.onLayoutFinish.append(h.layout)
    screen.onClose.append(h.close)


class _Hooks(object):
    def __init__(self, screen):
        self.screen = screen

    def layout(self):
        try:
            self.screen["toast"].hide()
        except Exception:
            pass

    def close(self):
        _stop(self.screen._toastAnim)
        _stop(self.screen._toastHide)


def _stop(t):
    try:
        t.stop()
    except Exception:
        pass


def _place(screen, y):
    inst = screen["toast"].instance
    if inst is not None:
        try:
            inst.move(ePoint(sc(_X), sc(y)))
        except Exception:
            pass


def toast(screen, text):
    """Show `text` in the pop-up (restarts if one is already showing)."""
    try:
        screen["toast"].setText(u"  " + text + u"  ")
        _stop(screen._toastAnim)
        _stop(screen._toastHide)
        screen._toast_f = 0
        _place(screen, _FROM)
        screen["toast"].show()
        screen._toastAnim.start(_STEP_MS, False)
        screen._toastHide.start(_HOLD_MS, True)
    except Exception:
        pass


def _tick(screen):
    import math
    screen._toast_f += 1
    t = min(1.0, screen._toast_f / float(_STEPS))
    _place(screen, _FROM + (_Y - _FROM) * math.sin(t * math.pi / 2))
    if t >= 1.0:
        _stop(screen._toastAnim)


def _hide(screen):
    try:
        screen["toast"].hide()
    except Exception:
        pass
