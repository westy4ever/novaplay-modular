# -*- coding: utf-8 -*-
"""
One-panel player menu (Yellow in the player): Quality | Server | Speed.

Modelled on Movy's player: one dark panel under the top of the screen with
three columns. LEFT/RIGHT moves between columns, UP/DOWN through a column,
OK picks, EXIT or YELLOW closes. Closes with ("quality"|"server"|"speed", index)
or None.
"""
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import ePoint

try:
    from skin import parseColor
except Exception:
    parseColor = None

from plugin_gridlist import scale_skin_xml

ROWS = 9
_COLS = (
    # key, x, width, title
    ("quality", 300, 330, "QUALITY — الجودة"),
    ("server", 650, 640, "SERVER — السيرفر"),
    ("speed", 1310, 310, "SPEED — السرعة"),
)
_Y0 = 200        # first row
_RH = 50         # row height
# enigma2 doesn't blend widgets with each other: a widget's pixels REPLACE the
# panel under it, so a see-through colour punches a hole to the video/TV
# picture. Every colour here uses the panel's own alpha (1A).
_FOCUS = "#1A00707F"     # focused column highlight (dark cyan)
_IDLE = "#1A2D333B"      # remembered row in the other columns (dark grey)


def _skin():
    w = []
    w.append('<widget name="panelBg" position="270,100" size="1380,{h}" backgroundColor="#1A0D1117" '
             'cornerRadius="22" zPosition="1" />'.format(h=150 + ROWS * _RH))
    w.append('<widget name="panelTop" position="270,100" size="1380,6" backgroundColor="#00E5FF" zPosition="2" />')
    for key, x, wd, _t in _COLS:
        w.append('<widget name="h_{k}" position="{x},126" size="{w},40" font="Regular;24" '
                 'foregroundColor="#8B949E" transparent="1" zPosition="3" />'.format(k=key, x=x + 14, w=wd - 14))
        w.append('<widget name="bar_{k}" position="{x},{y}" size="{w},{h}" backgroundColor="{c}" '
                 'cornerRadius="10" zPosition="2" />'.format(k=key, x=x, y=_Y0, w=wd, h=_RH - 4, c=_FOCUS))
        for i in range(ROWS):
            w.append('<widget name="r_{k}{i}" position="{x},{y}" size="{w},{h}" font="Regular;30" '
                     'valign="center" foregroundColor="#F0F6FC" transparent="1" zPosition="3" />'.format(
                         k=key, i=i, x=x + 18, y=_Y0 + i * _RH, w=wd - 30, h=_RH - 4))
    for x in (640, 1300):
        w.append('<widget name="sep{x}" position="{x},130" size="2,{h}" backgroundColor="#1A30363D" '
                 'zPosition="2" />'.format(x=x, h=60 + ROWS * _RH))
    w.append('<widget name="help" position="300,{y}" size="1320,36" font="Regular;24" halign="center" '
             'foregroundColor="#8B949E" transparent="1" zPosition="3" />'.format(y=_Y0 + ROWS * _RH + 12))
    return ('<screen name="NovaPlayerPanel" position="0,0" size="1920,1080" flags="wfNoBorder" '
            'backgroundColor="transparent">' + "".join(w) + "</screen>")


class NovaPlayerPanel(Screen):
    skin = scale_skin_xml(_skin())

    def __init__(self, session, quality, server, speed, start_col="quality"):
        """quality/server/speed: lists of (text, is_current)."""
        Screen.__init__(self, session)
        self._data = {"quality": list(quality or []), "server": list(server or []),
                      "speed": list(speed or [])}
        self._sel = {}
        self._top = {}
        for key, _x, _w, title in _COLS:
            items = self._data[key]
            cur = next((i for i, (_t, c) in enumerate(items) if c), 0)
            self._sel[key] = cur
            self._top[key] = max(0, min(cur - ROWS // 2, max(0, len(items) - ROWS)))
            self["h_" + key] = Label(title)
            self["bar_" + key] = Label("")
            for i in range(ROWS):
                self["r_%s%d" % (key, i)] = Label("")
        self["panelBg"] = Label("")
        self["panelTop"] = Label("")
        self["sep640"] = Label("")
        self["sep1300"] = Label("")
        self["help"] = Label(u"◀ ▶ عمود    ▲ ▼ اختيار    OK تطبيق    EXIT إغلاق")
        cols = [k for k, _x, _w, _t in _COLS if self._data[k]]
        self._cols = cols or ["quality"]
        self._col = start_col if start_col in self._cols else self._cols[0]
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self._ok,
            "cancel": lambda: self.close(None),
            "yellow": lambda: self.close(None),
            "left": lambda: self._moveCol(-1),
            "right": lambda: self._moveCol(1),
            "up": lambda: self._move(-1),
            "down": lambda: self._move(1),
        }, -1)
        self.onLayoutFinish.append(self._paint)

    def _moveCol(self, d):
        i = self._cols.index(self._col) if self._col in self._cols else 0
        self._col = self._cols[(i + d) % len(self._cols)]
        self._paint()

    def _move(self, d):
        items = self._data[self._col]
        if not items:
            return
        s = (self._sel[self._col] + d) % len(items)
        self._sel[self._col] = s
        top = self._top[self._col]
        if s < top:
            top = s
        elif s >= top + ROWS:
            top = s - ROWS + 1
        self._top[self._col] = top
        self._paint()

    def _ok(self):
        if self._data[self._col]:
            self.close((self._col, self._sel[self._col]))

    def _paint(self):
        for key, _x, _w, _t in _COLS:
            items = self._data[key]
            top = self._top[key]
            for i in range(ROWS):
                j = top + i
                txt = ""
                if j < len(items):
                    t, cur = items[j]
                    txt = (u"● " if cur else u"   ") + t
                if i == 0 and top > 0 and txt:
                    txt = u"▲ " + txt.lstrip(u"● ").strip()
                if i == ROWS - 1 and j < len(items) - 1:
                    txt = u"▼ " + txt.lstrip(u"● ").strip()
                self["r_%s%d" % (key, i)].setText(txt)
            bar = self["bar_" + key]
            if not items:
                self["h_" + key].setText(self["h_" + key].getText().split(" (")[0] + u" (—)")
                bar.hide()
                continue
            row = self._sel[key] - top
            try:
                rl = self["r_%s%d" % (key, max(0, min(ROWS - 1, row)))].instance
                bi = bar.instance
                if rl and bi:
                    bi.move(ePoint(bi.position().x(), rl.position().y()))
                    if parseColor is not None:
                        bi.setBackgroundColor(parseColor(_FOCUS if key == self._col else _IDLE))
                        bi.invalidate()
            except Exception:
                pass
            # the other columns only show where you were, faintly
            bar.show()
            try:
                if parseColor is not None:
                    self["h_" + key].instance.setForegroundColor(
                        parseColor("#00E5FF" if key == self._col else "#8B949E"))
            except Exception:
                pass
