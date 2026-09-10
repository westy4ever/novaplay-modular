# -*- coding: utf-8 -*-
"""NovaPlay — StreamList: the Stremio-style MultiContent list used by the
Detail screen for servers / episodes / qualities. Extracted verbatim
from the monolithic plugin.py (Phase-1)."""

from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText
from enigma import (eListbox, eListboxPythonMultiContent, gFont,
                    RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_HALIGN_RIGHT,
                    RT_VALIGN_CENTER, RT_VALIGN_TOP)


class StreamList(GUIComponent):
    GUI_WIDGET = eListbox

    def __init__(self):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("Regular", 26))
        self.l.setFont(1, gFont("Regular", 22))
        self.l.setFont(2, gFont("Regular", 20))
        self.l.setItemHeight(120)
        self.onSelectionChanged = []

    def selectionChanged(self):
        for cb in self.onSelectionChanged:
            cb()

    def getCurrent(self):
        cur = self.l.getCurrentSelection()
        return cur[0] if cur else None

    def getCurrentIndex(self):
        idx = self.l.getCurrentSelectionIndex()
        if idx < 0:
            idx = 0
        return idx

    def setList(self, items):
        res = []
        for item in items:
            quality = item[0]
            filename = item[1]
            size = item[2] if len(item) > 2 else ""
            seeders_source = item[3] if len(item) > 3 else ""
            entry = [
                item,
                MultiContentEntryText(pos=(10, 5), size=(190, 30), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=quality, color="#F0F6FC"),
                MultiContentEntryText(pos=(210, 5), size=(1340, 70), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP, text=filename, color="#F0F6FC"),
                MultiContentEntryText(pos=(1560, 5), size=(230, 30), font=2, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=size, color="#F0F6FC"),
                MultiContentEntryText(pos=(1560, 45), size=(230, 30), font=2, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=seeders_source, color="#39D98A")
            ]
            res.append(entry)
        self.l.setList(res)

    def up(self):
        if self.instance: self.instance.moveSelection(self.instance.moveUp)

    def down(self):
        if self.instance: self.instance.moveSelection(self.instance.moveDown)

    def pageUp(self):
        if self.instance: self.instance.moveSelection(self.instance.pageUp)

    def pageDown(self):
        if self.instance: self.instance.moveSelection(self.instance.pageDown)

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setItemHeight(120)
        try: instance.setSelectionEnable(True)
        except: pass

    def preWidgetDelete(self, instance):
        instance.setContent(None)