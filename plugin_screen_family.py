# -*- coding: utf-8 -*-
"""NovaPlay — version picker for grouped sites (EgyDead, Arabseed, Akwam…).

A centred card dialog: one row per version with its logo, name, tagline
and health dot. Closes with the chosen site key (None on EXIT).

Keys: ▲ ▼ move, OK open, 1-9 pick directly,
      MENU rename / move up / move down (order and names are saved),
      EXIT back.
"""
import os

from Screens.Screen import Screen
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap

try:
    from skin import parseColor
except Exception:
    parseColor = None

from extractors import get_family_members
import plugin_health
from plugin_gridlist import resolve_icon_path, scale_skin_xml
from plugin_util import _site_label, _site_tagline, _single_line_text

GOLD, EDGE, CARD, WHITE = "#F2C230", "#262E3B", "#1A212D", "#FFFFFF"
DOT = {"ok": "#30C48D", "down": "#E5484D", "blocked": "#F2A33A"}
MAXR = 6
_W, _RH, _GAP = 1200, 112, 14


class NovaFamilyPicker(Screen):
    def __init__(self, session, rep_key):
        self._rep = rep_key
        self._keys = list(get_family_members(rep_key))
        n = max(1, min(MAXR, len(self._keys)))
        h = 112 + n * (_RH + _GAP) + 66
        parts = ["""
        <screen name="NovaFamilyPicker{n}" position="center,center" size="{w},{h}" flags="wfNoBorder" backgroundColor="transparent">
            <eLabel position="0,0" size="{w},{h}" backgroundColor="#121822" cornerRadius="18" zPosition="0" />
            <widget name="title" position="36,20" size="{tw},48" font="Bold;32" foregroundColor="{gold}" transparent="1" zPosition="2" />
            <eLabel position="36,76" size="120,4" backgroundColor="{gold}" cornerRadius="2" zPosition="2" />
            <widget name="hint" position="36,{hy}" size="{tw},40" font="Regular;22" valign="center" foregroundColor="#A3ADBB" transparent="1" zPosition="2" />
        """.format(n=n, w=_W, h=h, tw=_W - 72, hy=h - 58, gold=GOLD)]
        for i in range(n):
            y = 104 + i * (_RH + _GAP)
            parts.append("""
            <widget name="e{i}" position="28,{y}" size="{cw},{rh}" backgroundColor="{edge}" cornerRadius="14" zPosition="1" />
            <eLabel position="31,{y3}" size="{cw3},{rh3}" backgroundColor="{card}" cornerRadius="12" zPosition="2" />
            <widget name="logo{i}" position="50,{ly}" size="190,67" alphatest="blend" scale="1" zPosition="3" />
            <widget name="name{i}" position="268,{ny}" size="{nw},42" font="Bold;30" foregroundColor="{white}" backgroundColor="{card}" transparent="1" zPosition="3" />
            <widget name="tag{i}" position="268,{gy}" size="{nw},32" font="Regular;22" foregroundColor="#B4BCC8" backgroundColor="{card}" transparent="1" zPosition="3" />
            <widget name="dot{i}" position="{dx},{dy}" size="16,16" backgroundColor="{card}" cornerRadius="8" zPosition="3" />
            """.format(i=i, y=y, y3=y + 3, cw=_W - 56, cw3=_W - 62, rh=_RH, rh3=_RH - 6,
                       ly=y + 22, ny=y + 16, gy=y + 60, nw=_W - 360, dx=_W - 70, dy=y + 48,
                       edge=EDGE, card=CARD, white=WHITE))
        parts.append("</screen>")
        self.skin = scale_skin_xml("".join(parts))
        Screen.__init__(self, session)
        self.skinName = ["NovaFamilyPicker%d" % n]
        self._n = n
        self._sel = 0
        self["title"] = Label(u"{} — choose a version".format(_site_label(rep_key)))
        self["hint"] = Label(u"OK  Open    ▲ ▼  Move    MENU  Rename / reorder    EXIT  Back")
        for i in range(n):
            for k in ("e", "name", "tag", "dot"):
                self["%s%d" % (k, i)] = Label("")
            self["logo%d" % i] = Pixmap()
        acts = {
            "ok": lambda: self.close(self._keys[self._sel] if self._keys else None),
            "cancel": lambda: self.close(None),
            "up": lambda: self._move(-1),
            "down": lambda: self._move(1),
            "menu": self._menu,
        }
        for d in range(1, 10):
            acts[str(d)] = (lambda k=d: self.close(self._keys[k - 1]) if k <= len(self._keys) else None)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "MenuActions", "NumberActions"], acts, -1)
        self.onLayoutFinish.append(self._paint)

    def _color(self, name, bg=None, fg=None):
        inst = self[name].instance
        if inst is None or parseColor is None:
            return
        if bg:
            inst.setBackgroundColor(parseColor(bg))
        if fg:
            inst.setForegroundColor(parseColor(fg))
        inst.invalidate()

    def _paint(self):
        for i in range(self._n):
            if i >= len(self._keys):
                continue
            k = self._keys[i]
            sel = i == self._sel
            self._color("e%d" % i, bg=GOLD if sel else EDGE)
            self["name%d" % i].setText(_single_line_text(_site_label(k), width=40, fallback=k))
            self._color("name%d" % i, fg=GOLD if sel else WHITE)
            self["tag%d" % i].setText(_single_line_text(_site_tagline(k), width=70, fallback=""))
            self._color("dot%d" % i, bg=DOT.get(plugin_health.get(k), CARD))
            path = resolve_icon_path({"_action": "site_" + k})
            inst = self["logo%d" % i].instance
            if inst is not None and path and os.path.exists(path):
                inst.setPixmapFromFile(path)

    def _move(self, d):
        if self._keys:
            self._sel = (self._sel + d) % min(self._n, len(self._keys))
            self._paint()

    def _menu(self):
        if not self._keys:
            return
        k = self._keys[self._sel]
        opts = [(u"Rename", "rename")]
        if self._sel > 0:
            opts.append((u"Move up", "up"))
        if self._sel < len(self._keys) - 1:
            opts.append((u"Move down", "down"))

        def _do(ans):
            act = ans[1] if ans else None
            if act == "rename":
                def _typed(name):
                    if name is not None:
                        try:
                            from plugin_state import _set_site_custom_name
                            _set_site_custom_name(k, name)
                        except Exception:
                            pass
                        self._paint()
                self.session.openWithCallback(_typed, VirtualKeyBoard,
                                              title=u"Display name for {}".format(_site_label(k)), text=_site_label(k))
            elif act in ("up", "down"):
                j = self._sel + (-1 if act == "up" else 1)
                self._keys[self._sel], self._keys[j] = self._keys[j], self._keys[self._sel]
                self._sel = j
                try:
                    from plugin_state import _set_family_order
                    _set_family_order(self._rep, list(self._keys))
                except Exception:
                    pass
                self._paint()
        from novaplay_choice import NovaChoiceBox
        self.session.openWithCallback(_do, NovaChoiceBox, title=_site_label(k), list=opts)
