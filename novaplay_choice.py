# -*- coding: utf-8 -*-
"""NovaPlay's own choice dialog, styled like the rest of the plugin.

Drop-in for Screens.ChoiceBox.ChoiceBox as NovaPlay uses it:
    session.openWithCallback(cb, NovaChoiceBox, "Title", [(label, value), ...])
    session.openWithCallback(cb, NovaChoiceBox, title=..., list=[...])
The callback gets the chosen tuple, or None on EXIT (same as ChoiceBox).

The image's ChoiceBox draws in the receiver skin's colours; this one is a
rounded dark card with a gold title and gold selection, sized to the list.
Keys: ▲ ▼ move, ◀ ▶ page, 1-9 pick directly, OK choose, EXIT back.
"""
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList

from plugin_gridlist import scale_skin_xml

_W = 1100
GOLD_ = "#F2C230"
_ROW = 56
_MAX_ROWS = 10


class NovaChoiceBox(Screen):
    def __init__(self, session, title="", list=None, keys=None, selection=0, **kwargs):
        items = []
        for it in (list or []):
            if isinstance(it, (tuple, type([]))):
                items.append(tuple(it) if it else ("",))
            else:
                items.append((str(it),))
        self._items = items
        rows = max(1, min(_MAX_ROWS, len(items)))
        lh = rows * _ROW
        h = 104 + lh + 74
        self.skin = scale_skin_xml("""
        <screen name="NovaChoiceBox{rows}" position="center,center" size="{w},{h}" flags="wfNoBorder" backgroundColor="transparent">
            <eLabel position="0,0" size="{w},{h}" backgroundColor="#121822" cornerRadius="18" zPosition="0" />
            <widget name="title" position="36,20" size="{tw},48" font="Bold;32" foregroundColor="#F2C230" transparent="1" zPosition="2" />
            <eLabel position="36,76" size="120,4" backgroundColor="#F2C230" cornerRadius="2" zPosition="2" />
            <widget name="list" position="22,100" size="{lw},{lh}" scrollbarMode="showNever" font="Regular;28" itemHeight="{row}"
                foregroundColor="#D6DCE5" foregroundColorSelected="#F2C230" backgroundColor="#121822" backgroundColorSelected="#2B3648" zPosition="2" />
            <widget name="hint" position="36,{hy}" size="{tw},40" font="Regular;22" valign="center" foregroundColor="#A3ADBB" transparent="1" zPosition="2" />
        </screen>""".format(w=_W, h=h, tw=_W - 72, lw=_W - 44, lh=lh, row=_ROW, hy=h - 62, rows=rows))
        Screen.__init__(self, session)
        self.skinName = ["NovaChoiceBox%d" % rows]
        self["title"] = Label(str(title or ""))
        self["list"] = MenuList([str(i[0]) for i in items])
        more = len(items) > _MAX_ROWS
        self["hint"] = Label(u"OK  Choose    ▲ ▼  Move{}    1–9  Quick pick    EXIT  Back".format(
            u"    ◀ ▶  Page" if more else u""))
        self._start = max(0, min(int(selection or 0), len(items) - 1)) if items else 0
        acts = {
            "ok": self._ok,
            "cancel": lambda: self.close(None),
            "up": lambda: self["list"].up(),
            "down": lambda: self["list"].down(),
            "left": lambda: self["list"].pageUp(),
            "right": lambda: self["list"].pageDown(),
        }
        for n in range(1, 10):
            acts[str(n)] = (lambda k=n: self._pick(k - 1))
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "NumberActions"], acts, -1)
        self.onLayoutFinish.append(self._init)

    def _init(self):
        try:
            if self._start:
                self["list"].moveToIndex(self._start)
        except Exception:
            pass

    def _pick(self, i):
        if 0 <= i < len(self._items):
            self.close(self._items[i])

    def _ok(self):
        try:
            i = self["list"].getSelectedIndex()
        except Exception:
            i = -1
        self._pick(i)


class NovaMessageBox(Screen):
    """Drop-in for Screens.MessageBox.MessageBox as NovaPlay uses it:
        session.open(NovaMessageBox, text, NovaMessageBox.TYPE_INFO, timeout=5)
        session.openWithCallback(cb, NovaMessageBox, text, NovaMessageBox.TYPE_YESNO, default=False)
    YES/NO closes with True/False (EXIT = False, timeout = the default);
    the other types close with True. Rounded card, gold title (red for
    errors), Yes / No buttons, countdown when there is a timeout."""
    TYPE_YESNO = 0
    TYPE_INFO = 1
    TYPE_WARNING = 2
    TYPE_ERROR = 3
    _TITLES = {0: u"Please confirm", 1: u"Information", 2: u"Warning", 3: u"Error"}

    def __init__(self, session, text="", type=1, timeout=-1, default=True, title=None, **kwargs):
        from enigma import eTimer
        self._type = type
        self._yes = bool(default) if type == self.TYPE_YESNO else True
        text = str(text or "")
        lines = 0
        for para in text.split("\n"):
            lines += max(1, -(-len(para) // 62))
        if lines <= 10:
            font, lh = 28, 40
        elif lines <= 16:
            font, lh = 24, 33
        else:
            font, lh = 22, 29
        lines = min(lines, 24)
        th = max(lh, lines * lh)
        bh = 76 if type == self.TYPE_YESNO else 0
        h = 104 + th + 24 + bh + 60
        accent = "#E5484D" if type == self.TYPE_ERROR else GOLD_
        parts = ["""
        <screen name="NovaMessageBox{t}_{th}_{f}" position="center,center" size="{w},{h}" flags="wfNoBorder" backgroundColor="transparent">
            <eLabel position="0,0" size="{w},{h}" backgroundColor="#121822" cornerRadius="18" zPosition="0" />
            <widget name="title" position="36,20" size="{tw},48" font="Bold;30" foregroundColor="{a}" transparent="1" zPosition="2" />
            <eLabel position="36,74" size="120,4" backgroundColor="{a}" cornerRadius="2" zPosition="2" />
            <widget name="text" position="36,104" size="{tw},{th}" font="Regular;{f}" foregroundColor="#E6EAF0" transparent="1" zPosition="2" />
            <widget name="hint" position="36,{hy}" size="{tw},40" font="Regular;22" valign="center" foregroundColor="#A3ADBB" transparent="1" zPosition="2" />
        """.format(t=type, th=th, f=font, w=_W, h=h, tw=_W - 72, a=accent, hy=h - 58)]
        if bh:
            by = 104 + th + 24
            parts.append("""
            <widget name="bYes" position="36,{by}" size="240,60" font="Bold;28" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#2B3648" cornerRadius="12" zPosition="2" />
            <widget name="bNo" position="296,{by}" size="240,60" font="Bold;28" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#2B3648" cornerRadius="12" zPosition="2" />
            """.format(by=by))
        parts.append("</screen>")
        self.skin = scale_skin_xml("".join(parts))
        Screen.__init__(self, session)
        self.skinName = ["NovaMessageBox%d_%d_%d" % (type, th, font)]
        self["title"] = Label(title or self._TITLES.get(type, u""))
        self["text"] = Label(text)
        self["hint"] = Label("")
        if bh:
            self["bYes"] = Label(u"Yes")
            self["bNo"] = Label(u"No")
        self._left = int(timeout) if timeout and int(timeout) > 0 else 0
        self._timer = eTimer()
        self._timer.callback.append(self._tick)
        acts = {
            "ok": self._ok,
            "cancel": lambda: self.close(False if self._type == self.TYPE_YESNO else True),
            "left": lambda: self._flip(),
            "right": lambda: self._flip(),
            "up": lambda: self._flip(),
            "down": lambda: self._flip(),
        }
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], acts, -1)
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._timer.stop)

    def _start(self):
        self._paint()
        if self._left:
            self._timer.start(1000, False)

    def _paint(self):
        if self._type == self.TYPE_YESNO:
            try:
                from skin import parseColor
                for name, on in (("bYes", self._yes), ("bNo", not self._yes)):
                    inst = self[name].instance
                    if inst is not None:
                        inst.setBackgroundColor(parseColor(GOLD_ if on else "#2B3648"))
                        inst.setForegroundColor(parseColor("#0C1017" if on else "#FFFFFF"))
                        inst.invalidate()
            except Exception:
                pass
            hint = u"◀ ▶  Choose    OK  Confirm    EXIT  No"
        else:
            hint = u"OK  Close"
        if self._left:
            hint += u"      ({}s)".format(self._left)
        self["hint"].setText(hint)

    def _flip(self):
        if self._type == self.TYPE_YESNO:
            self._yes = not self._yes
            self._left = 0          # the user is deciding: stop the countdown
            self._timer.stop()
            self._paint()

    def _tick(self):
        self._left -= 1
        if self._left <= 0:
            self._timer.stop()
            self._ok()
            return
        self._paint()

    def _ok(self):
        self.close(self._yes if self._type == self.TYPE_YESNO else True)
