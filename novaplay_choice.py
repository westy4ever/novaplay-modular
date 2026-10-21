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
