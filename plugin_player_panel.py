# -*- coding: utf-8 -*-
"""
Movy-style column panels for the player.

  NovaPlayerPanel (Yellow): Quality | Server | Speed
  NovaSubsPanel   (Blue):   Audio | Subtitles | More

One dark panel under the top of the screen. LEFT/RIGHT moves between
columns, UP/DOWN through a column, OK picks, EXIT (or the colour key that
opened it) closes. Closes with (column_key, index) or None.

Items are (text, is_current) or (text, is_current, stay): a "stay" item
calls the panel's live callback and keeps the panel open (used for the
subtitle sync steps), showing the callback's answer on the help line.
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
_Y0 = 200        # first row
_RH = 50         # row height
# enigma2 doesn't blend widgets with each other: a widget's pixels REPLACE the
# panel under it, so a see-through colour punches a hole to the video/TV
# picture. Every colour here uses the panel's own alpha (1A).
_FOCUS = "#1A00707F"     # focused column highlight (dark cyan)
_IDLE = "#1A2D333B"      # remembered row in the other columns (dark grey)
_HELP = u"◀ ▶ عمود    ▲ ▼ اختيار    OK تطبيق    EXIT إغلاق"

_PLAYER_COLS = (
    # key, x, width, title
    ("quality", 300, 330, u"QUALITY — الجودة"),
    ("server", 650, 640, u"SERVER — السيرفر"),
    ("speed", 1310, 310, u"SPEED — السرعة"),
)
_SUBS_COLS = (
    ("audio", 300, 330, u"AUDIO — الصوت"),
    ("subs", 650, 640, u"SUBTITLES — الترجمة"),
    ("more", 1310, 310, u"MORE — المزيد"),
)


def _skin(name, cols):
    w = []
    w.append('<widget name="panelBg" position="270,100" size="1380,{h}" backgroundColor="#1A0D1117" '
             'cornerRadius="22" zPosition="1" />'.format(h=150 + ROWS * _RH))
    w.append('<widget name="panelTop" position="270,100" size="1380,6" backgroundColor="#00E5FF" zPosition="2" />')
    for key, x, wd, _t in cols:
        w.append('<widget name="h_{k}" position="{x},126" size="{w},40" font="Regular;24" '
                 'foregroundColor="#8B949E" transparent="1" zPosition="3" />'.format(k=key, x=x + 14, w=wd - 14))
        w.append('<widget name="bar_{k}" position="{x},{y}" size="{w},{h}" backgroundColor="{c}" '
                 'cornerRadius="10" zPosition="2" />'.format(k=key, x=x, y=_Y0, w=wd, h=_RH - 4, c=_FOCUS))
        for i in range(ROWS):
            w.append('<widget name="r_{k}{i}" position="{x},{y}" size="{w},{h}" font="Regular;30" '
                     'valign="center" foregroundColor="#F0F6FC" transparent="1" zPosition="3" />'.format(
                         k=key, i=i, x=x + 18, y=_Y0 + i * _RH, w=wd - 30, h=_RH - 4))
    for i, x in enumerate((640, 1300)):
        w.append('<widget name="sep{i}" position="{x},130" size="2,{h}" backgroundColor="#1A30363D" '
                 'zPosition="2" />'.format(i=i, x=x, h=60 + ROWS * _RH))
    w.append('<widget name="help" position="300,{y}" size="1320,36" font="Regular;24" halign="center" '
             'foregroundColor="#8B949E" transparent="1" zPosition="3" />'.format(y=_Y0 + ROWS * _RH + 12))
    return ('<screen name="{n}" position="0,0" size="1920,1080" flags="wfNoBorder" '
            'backgroundColor="transparent">'.format(n=name) + "".join(w) + "</screen>")


class _ColumnPanel(Screen):
    COLS = ()
    CLOSE_KEY = "yellow"

    def __init__(self, session, data, start_col=None, live_cb=None):
        Screen.__init__(self, session)
        self._data = dict((k, list(data.get(k) or [])) for k, _x, _w, _t in self.COLS)
        self._live_cb = live_cb
        self._sel = {}
        self._top = {}
        for key, _x, _w, title in self.COLS:
            self._resetPos(key)
            self["h_" + key] = Label(title)
            self["bar_" + key] = Label("")
            for i in range(ROWS):
                self["r_%s%d" % (key, i)] = Label("")
        self["panelBg"] = Label("")
        self["panelTop"] = Label("")
        self["sep0"] = Label("")
        self["sep1"] = Label("")
        self["help"] = Label(_HELP)
        keys = [k for k, _x, _w, _t in self.COLS]
        self._col = start_col if start_col in keys else next(
            (k for k in keys if self._data[k]), keys[0])
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self._ok,
            "cancel": lambda: self.close(None),
            self.CLOSE_KEY: lambda: self.close(None),
            "left": lambda: self._moveCol(-1),
            "right": lambda: self._moveCol(1),
            "up": lambda: self._move(-1),
            "down": lambda: self._move(1),
        }, -1)
        self.onLayoutFinish.append(self._paint)

    def _resetPos(self, key):
        items = self._data[key]
        cur = next((i for i, it in enumerate(items) if it[1]), 0)
        self._sel[key] = cur
        self._top[key] = max(0, min(cur - ROWS // 2, max(0, len(items) - ROWS)))

    def setColumn(self, key, items):
        """Replace a column's items while the panel is open (async results)."""
        if key not in self._data:
            return
        self._data[key] = list(items or [])
        self._resetPos(key)
        try:
            self._paint()
        except Exception:
            pass

    def setHelp(self, text):
        try:
            self["help"].setText(text or _HELP)
        except Exception:
            pass

    def _cols(self):
        return [k for k, _x, _w, _t in self.COLS if self._data[k]] or [self._col]

    def _moveCol(self, d):
        cols = self._cols()
        i = cols.index(self._col) if self._col in cols else 0
        self._col = cols[(i + d) % len(cols)]
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
        items = self._data[self._col]
        if not items:
            return
        idx = self._sel[self._col]
        it = items[idx]
        if len(it) > 2 and it[2] and self._live_cb:
            try:
                msg = self._live_cb(self._col, idx)
            except Exception as e:
                msg = str(e)
            self.setHelp(msg)
            return
        self.close((self._col, idx))

    def _paint(self):
        for key, _x, _w, _t in self.COLS:
            items = self._data[key]
            top = self._top[key]
            for i in range(ROWS):
                j = top + i
                txt = ""
                if j < len(items):
                    t, cur = items[j][0], items[j][1]
                    txt = (u"● " if cur else u"   ") + t
                if i == 0 and top > 0 and txt:
                    txt = u"▲ " + txt.lstrip(u"● ").strip()
                if i == ROWS - 1 and j < len(items) - 1:
                    txt = u"▼ " + txt.lstrip(u"● ").strip()
                self["r_%s%d" % (key, i)].setText(txt)
            bar = self["bar_" + key]
            if not items:
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
            bar.show()
            try:
                if parseColor is not None:
                    self["h_" + key].instance.setForegroundColor(
                        parseColor("#00E5FF" if key == self._col else "#8B949E"))
            except Exception:
                pass


class NovaPlayerPanel(_ColumnPanel):
    """Yellow: Quality | Server | Speed."""
    COLS = _PLAYER_COLS
    CLOSE_KEY = "yellow"
    skin = scale_skin_xml(_skin("NovaPlayerPanel", _PLAYER_COLS))

    def __init__(self, session, quality, server, speed, start_col="quality"):
        _ColumnPanel.__init__(self, session,
                              {"quality": quality, "server": server, "speed": speed},
                              start_col=start_col)


class NovaSubsPanel(_ColumnPanel):
    """Blue: Audio | Subtitles | More."""
    COLS = _SUBS_COLS
    CLOSE_KEY = "blue"
    skin = scale_skin_xml(_skin("NovaSubsPanel", _SUBS_COLS))

    def __init__(self, session, audio, subs, more, live_cb=None, start_col="subs"):
        _ColumnPanel.__init__(self, session,
                              {"audio": audio, "subs": subs, "more": more},
                              start_col=start_col, live_cb=live_cb)
