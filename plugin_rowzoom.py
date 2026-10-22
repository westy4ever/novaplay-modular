# -*- coding: utf-8 -*-
"""NovaPlay -- smooth zoom for a stack of row widgets (Search suggestions,
Downloads).  The selected row grows a little wider and taller (into the gap
to its neighbours) with the same ease-out as the poster / card pops; its
text widgets move with it and the main text gets 2 pt bigger.

    zoomer = RowZoom(screen, place_fn)   # place_fn(i, p) puts row i at zoom p
    zoomer.focus(i)                      # call after every repaint (no-op if same)
    zoomer.stop()                        # onClose
"""
import math

from enigma import eTimer

try:
    from plugin_gridlist import POP_ANIM_FRAMES, POP_ANIM_INTERVAL
except Exception:                                   # pragma: no cover
    POP_ANIM_FRAMES, POP_ANIM_INTERVAL = 12, 15


class RowZoom(object):
    def __init__(self, screen, place):
        self._place = place
        self._cur = None
        self._f = 0
        self._t = eTimer()
        self._t.callback.append(self._tick)
        screen.onClose.append(self.stop)

    def focus(self, i, key=None):
        k = (i, key)
        if k == self._cur:
            return
        try:
            self._t.stop()
        except Exception:
            pass
        if self._cur is not None and self._cur[0] is not None and self._cur[0] >= 0:
            self._place(self._cur[0], 0.0)
        self._cur = k
        if i is None or i < 0:
            return
        self._f = 0
        self._place(i, 0.0)
        try:
            self._t.start(POP_ANIM_INTERVAL, False)
        except Exception:
            self._place(i, 1.0)

    def _tick(self):
        self._f += 1
        t = min(1.0, self._f / float(max(1, POP_ANIM_FRAMES)))
        if self._cur and self._cur[0] is not None and self._cur[0] >= 0:
            self._place(self._cur[0], math.sin(t * math.pi / 2))
        if t >= 1.0:
            self._t.stop()

    def stop(self):
        try:
            self._t.stop()
        except Exception:
            pass
