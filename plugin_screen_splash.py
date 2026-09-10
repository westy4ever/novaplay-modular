# -*- coding: utf-8 -*-
"""NovaPlay — splash screen. Opens the Home screen after 2.5s."""

import os

from Screens.Screen import Screen
from Components.Pixmap import Pixmap
from enigma import eTimer, ePicLoad

from plugin_common import PLUGIN_PATH
from plugin_gridlist import scale_skin_xml, SCREEN_W, SCREEN_H
from plugin_screen_home import AdvancedArabicPlayerHome


class AdvancedArabicPlayerSplash(Screen):
    skin = scale_skin_xml("""
    <screen name="AdvancedArabicPlayerSplash" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="#000000">
        <widget name="splash_pic" position="0,0" size="1920,1080" zPosition="1" alphatest="blend" />
    </screen>
    """.format(PLUGIN_PATH))

    def __init__(self, session):
        self.skin = AdvancedArabicPlayerSplash.skin
        Screen.__init__(self, session)
        self["splash_pic"] = Pixmap()
        self._timer = eTimer()
        self._timer.callback.append(self._onFinish)
        self.picLoad = ePicLoad()
        self.picLoad.PictureData.get().append(self._paintSplash)
        self.onLayoutFinish.append(self._start)

    def _start(self):
        # SCREEN_W/SCREEN_H import = the P2 NameError fix, now structural.
        splash_path = os.path.join(PLUGIN_PATH, "images", "splash.png")
        if os.path.exists(splash_path):
            self.picLoad.setPara((SCREEN_W, SCREEN_H, 1, 1, 0, 1, "#000000"))
            self.picLoad.startDecode(splash_path)
        self._timer.start(2500, True)

    def _paintSplash(self, picData=None):
        ptr = self.picLoad.getData()
        if ptr:
            self["splash_pic"].instance.setPixmap(ptr)
            self["splash_pic"].show()

    def _onFinish(self):
        self._timer.stop()
        try:
            self.picLoad.PictureData.get().remove(self._paintSplash)
        except Exception:
            pass
        self.session.open(AdvancedArabicPlayerHome)
        self.close()