# -*- coding: utf-8 -*-
"""NovaPlay -- CardList: a list whose rows are small cards (title line, a
row of chips, an optional badge), used by the subtitle search, the subtitle
folder browser and Diagnostics.

    self["list"] = CardList(width=1620, row_h=86)
    self["list"].setRows([card(...), card(...), ...])

Each row is built with card() / header_row(); the first element of a row
is the payload (getCurrent() returns it). UP / DOWN / page keys are handled
by the listbox itself, like any MenuList.
"""
from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText
from enigma import (eListbox, eListboxPythonMultiContent, gFont,
                    RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_HALIGN_RIGHT, RT_VALIGN_CENTER)

TEXT, MUTED, GOLD, DARK = 0xF0F6FC, 0xA3ADBB, 0xF2C230, 0x0C1017
CHIP_BG, CHIP_BG_SEL = 0x232B38, 0x3A4760
GREEN, ORANGE, RED, GREY = 0x30C48D, 0xF2A33A, 0xE5484D, 0x6E7681

F_TITLE, F_CHIP, F_BADGE, F_SMALL = 0, 1, 2, 3


class CardList(GUIComponent):
    GUI_WIDGET = eListbox

    def __init__(self, width=1600, row_h=86, title_size=28):
        GUIComponent.__init__(self)
        self.width = int(width)
        self.row_h = int(row_h)
        self.l = eListboxPythonMultiContent()
        self.l.setFont(F_TITLE, gFont("Regular", int(title_size)))
        self.l.setFont(F_CHIP, gFont("Regular", 19))
        self.l.setFont(F_BADGE, gFont("Bold", 19))
        self.l.setFont(F_SMALL, gFont("Regular", 21))
        self.l.setItemHeight(self.row_h)
        self.onSelectionChanged = []
        self._rows = []

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        try:
            instance.selectionChanged.get().append(self.selectionChanged)
        except Exception:
            pass

    def preWidgetRemove(self, instance):
        try:
            instance.selectionChanged.get().remove(self.selectionChanged)
        except Exception:
            pass
        instance.setContent(None)

    def selectionChanged(self):
        for cb in self.onSelectionChanged:
            try:
                cb()
            except Exception:
                pass

    def setRows(self, rows):
        self._rows = list(rows or [])
        self.l.setList(self._rows)

    def getSelectedIndex(self):
        try:
            return max(0, self.l.getCurrentSelectionIndex())
        except Exception:
            return 0

    def getCurrent(self):
        cur = self.l.getCurrentSelection()
        return cur[0] if cur else None

    def moveToIndex(self, idx):
        if self.instance is not None:
            try:
                self.instance.moveSelectionTo(int(idx))
            except Exception:
                pass


def _chip_w(text):
    return 22 + 11 * len(text)


def card(payload, width, row_h, title, chips=(), badge=None, title_color=TEXT,
         icon="", icon_color=GOLD, right="", icon_w=0):
    """One card row:
         [icon]  title                                   [BADGE]
                 (chip) (chip) (chip)          right text
       chips: [(text, fg, bg)] or plain strings (grey chips)."""
    row = [payload]
    x = 20
    two = bool(chips) or bool(right)
    if icon:
        iw = icon_w or 44
        row.append(MultiContentEntryText(pos=(x, 0), size=(iw, row_h), font=F_TITLE,
                                         flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER,
                                         text=icon, color=icon_color, color_sel=icon_color))
        x += iw + 10
    bw = 0
    if badge:
        btxt, bfg, bbg = badge
        bw = _chip_w(btxt) + 8
        row.append(MultiContentEntryText(pos=(width - bw - 16, (row_h - 34) // 2), size=(bw, 34),
                                         font=F_BADGE, flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER,
                                         text=btxt, color=bfg, color_sel=bfg,
                                         backcolor=bbg, backcolor_sel=bbg))
    tw = width - x - bw - 40
    if two:
        row.append(MultiContentEntryText(pos=(x, 6), size=(tw, row_h // 2), font=F_TITLE,
                                         flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                         text=title, color=title_color, color_sel=GOLD))
        cy, ch = row_h // 2 + 8, 28
        cx = x
        for c in chips:
            if isinstance(c, (tuple, list)):
                txt, fg, bg = c
            else:
                txt, fg, bg = c, 0xC9D0DA, CHIP_BG
            if not txt:
                continue
            w = _chip_w(txt)
            if cx + w > x + tw:
                break
            row.append(MultiContentEntryText(pos=(cx, cy), size=(w, ch), font=F_CHIP,
                                             flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER, text=txt,
                                             color=fg, color_sel=fg, backcolor=bg,
                                             backcolor_sel=(CHIP_BG_SEL if bg == CHIP_BG else bg)))
            cx += w + 8
        if right:
            row.append(MultiContentEntryText(pos=(cx + 10, cy), size=(max(10, x + tw - cx - 10), ch),
                                             font=F_SMALL, flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER,
                                             text=right, color=MUTED, color_sel=MUTED))
    else:
        row.append(MultiContentEntryText(pos=(x, 0), size=(tw, row_h), font=F_TITLE,
                                         flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                         text=title, color=title_color, color_sel=GOLD))
    return row


def line(payload, width, row_h, text, icon="", icon_color=GOLD, right="", right_color=MUTED,
         text_color=TEXT):
    """One-line row: icon | text .......... right"""
    row = [payload]
    x = 20
    if icon:
        row.append(MultiContentEntryText(pos=(x, 0), size=(40, row_h), font=F_TITLE,
                                         flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER,
                                         text=icon, color=icon_color, color_sel=icon_color))
        x += 52
    rw = min(560, 20 + 11 * len(right)) if right else 0
    row.append(MultiContentEntryText(pos=(x, 0), size=(width - x - rw - 40, row_h), font=F_TITLE,
                                     flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                     text=text, color=text_color, color_sel=GOLD))
    if right:
        row.append(MultiContentEntryText(pos=(width - rw - 20, 0), size=(rw, row_h), font=F_SMALL,
                                         flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER,
                                         text=right, color=right_color, color_sel=right_color))
    return row


def header_row(payload, width, row_h, text):
    """small grey group heading with a thin rule"""
    return [payload,
            MultiContentEntryText(pos=(20, 0), size=(width - 40, row_h - 6), font=F_BADGE,
                                  flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                  text=text.upper(), color=MUTED, color_sel=MUTED),
            MultiContentEntryText(pos=(20, row_h - 4), size=(width - 40, 1), font=F_CHIP,
                                  text="", backcolor=0x2A3240, backcolor_sel=0x2A3240)]
