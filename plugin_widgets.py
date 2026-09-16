# -*- coding: utf-8 -*-
"""NovaPlay — StreamList: the Stremio-style MultiContent list used by the
Detail screen for servers / episodes / qualities. Extracted verbatim
from the monolithic plugin.py (Phase-1)."""

import re
from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText
from enigma import (eListbox, eListboxPythonMultiContent, gFont,
                    RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_HALIGN_RIGHT,
                    RT_VALIGN_CENTER, RT_VALIGN_TOP)

# [PATCH 28] reuse the Arabic-script detector from plugin_gridlist
from plugin_gridlist import _has_arabic


class StreamList(GUIComponent):
    GUI_WIDGET = eListbox

    def __init__(self):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("Regular", 26))
        self.l.setFont(1, gFont("Regular", 22))
        self.l.setFont(2, gFont("Regular", 20))
        self.l.setItemHeight(95)   # [PATCH 19] content is 75px tall; 95 = tight + 20px air
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
            # [PATCH 19] row layout: tight columns, metadata beside the
            # content instead of hugging the screen edge.
            # quality 10-140 | filename 150-1320 | size/seeders 1330-1560
            # [PATCH 40] "1080P CAM" never fit the 130px column and bled
            # into the name — split: resolution (font 0) with the actual
            # source tag beneath (font 2, amber)
            # [PATCH 40] sanitize first: strip stray brackets and trim,
            # THEN split resolution / source-tag. A raw "[ 1080P CAM]"
            # label previously split into "[" and "1080P CAM]" lines.
            q_clean = re.sub(r'[\[\]]', '', (quality or "")).strip()
            q_parts = q_clean.split(" ", 1)
            q_res = q_parts[0] if q_parts else ""
            q_src = q_parts[1] if len(q_parts) > 1 else ""
            entry = [
                item,
                # [PATCH 19+] y 5→12: content optically centered in the
                # 95px row
                MultiContentEntryText(pos=(10, 10), size=(130, 28), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=q_res, color="#F0F6FC"),
                MultiContentEntryText(pos=(10, 42), size=(130, 22), font=2, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=q_src, color="#E3B341"),
                # [PATCH 41b] name column gets breathing room on both
                # sides: starts 20px after the quality column (170),
                # ends 20px before the metadata column (was flush at
                # both edges — long names looked cut off at the right,
                # and quality text visually collided at the left)
                MultiContentEntryText(pos=(170, 12), size=(1130, 70), font=1,
                                      flags=(RT_HALIGN_RIGHT if _has_arabic(filename) else RT_HALIGN_LEFT)|RT_VALIGN_TOP,
                                      text=filename, color="#F0F6FC"),
                # [PATCH 41] column moved right (1330→1560, width 230→330):
                # the old position left a ~240px dead strip between the
                # numbers and the scrollbar
                # [PATCH 41c] moved LEFT of the scrollbar edge (~1790):
                # the 1560 position put the text under the scrollbar bar
                MultiContentEntryText(pos=(1560, 12), size=(210, 30), font=2, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=size, color="#F0F6FC"),
                MultiContentEntryText(pos=(1560, 52), size=(210, 30), font=2, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=seeders_source, color="#39D98A")
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
        instance.setItemHeight(95)
        try: instance.setSelectionEnable(True)
        except: pass

    def preWidgetDelete(self, instance):
        instance.setContent(None)