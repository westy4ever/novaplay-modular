# -*- coding: utf-8 -*-
"""NovaPlay — splash screen. Opens the Home screen after 2.5s."""

import os

from Screens.Screen import Screen
from Components.Pixmap import Pixmap
from enigma import eTimer, ePicLoad

# [perf] kept light on purpose: the splash shows at once, and the rest of
# NovaPlay (screens, site extractors, curl_cffi ...) is imported while it is
# on screen (_preload) instead of before it appears
PLUGIN_PATH = os.path.dirname(os.path.realpath(__file__))
try:
    from enigma import getDesktop
    _ds = getDesktop(0).size()
    SCREEN_W, SCREEN_H = int(_ds.width()), int(_ds.height())
except Exception:
    SCREEN_W, SCREEN_H = 1920, 1080


def scale_skin_xml(xml):
    """the splash is one full-screen picture: just use the real screen size"""
    return xml.replace('size="1920,1080"', 'size="{},{}"'.format(SCREEN_W, SCREEN_H))


class NovaPlaySplash(Screen):
    skin = scale_skin_xml("""
    <screen name="NovaPlaySplash" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="#000000">
        <widget name="splash_pic" position="0,0" size="1920,1080" zPosition="1" alphatest="blend" />
    </screen>
    """.format(PLUGIN_PATH))

    def __init__(self, session):
        self.skin = NovaPlaySplash.skin
        Screen.__init__(self, session)
        self["splash_pic"] = Pixmap()
        self._timer = eTimer()
        self._timer.callback.append(self._onFinish)
        self._preT = eTimer()
        self._preT.callback.append(self._preload)
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
        self._preT.start(80, True)        # load the rest once the splash is drawn

    def _preload(self):
        try:
            import plugin_common                    # noqa: F401  (sys.path + constants)
            try:
                from extractors.base import set_browser_proxy
                from plugin_state import _get_config
                set_browser_proxy(_get_config("browser_proxy", ""))
            except Exception:
                pass
            try:
                import novaplay_kbfix               # keyboard Google suggestions (in memory)
                novaplay_kbfix.apply()
            except Exception:
                pass
            import plugin_screen_hub                # noqa: F401
            import plugin_screen_home               # noqa: F401
        except Exception as e:
            try:
                from plugin_common import my_log
                my_log("splash preload: {}".format(e))
            except Exception:
                pass

    def _paintSplash(self, picData=None):
        ptr = self.picLoad.getData()
        if ptr:
            self["splash_pic"].instance.setPixmap(ptr)
            self["splash_pic"].show()

    def _onFinish(self):
        self._timer.stop()
        self._preload()                   # no-op when already loaded
        try:
            self.picLoad.PictureData.get().remove(self._paintSplash)
        except Exception:
            pass
        # [hub] panel Home by default; Settings > Library & Display > Home Style
        try:
            from plugin_state import _get_config
            classic = _get_config("home_style", "panel") == "classic"
        except Exception:
            classic = False
        if classic:
            from plugin_screen_home import NovaPlayHome
            self.session.open(NovaPlayHome)
        else:
            from plugin_screen_hub import NovaPlayHub
            self.session.open(NovaPlayHub)
        self.close()