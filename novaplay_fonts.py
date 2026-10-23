# -*- coding: utf-8 -*-
"""NovaPlay -- make sure Arabic text can be drawn while NovaPlay is open.

Skins draw Arabic from the image's single "Replacement" (fallback) font. Any
skin or plugin can register its own fallback into that one slot while the box
runs; when the last one has no Arabic letters, every Arabic label is blank
(still there, still working -- just invisible). Box check 2026-10-23: seven
different Replacement fonts across the installed skins / defaults.

ensure_arabic()  -> NovaPlay's screens use DejaVuSans (Arabic + Latin +
                    Cyrillic + Greek, ships with the image) or ae_AlMateen
                    as the fallback; called whenever the Home screen opens.
restore_skin()   -> put the active skin's own fallback back when NovaPlay
                    closes, so the rest of the box looks as the skin wants.
"""
import os
import re

_CANDIDATES = ("/usr/share/fonts/DejaVuSans.ttf", "/usr/share/fonts/ae_AlMateen.ttf")


def _log(msg):
    try:
        from plugin_common import my_log
        my_log("fonts: " + msg)
    except Exception:
        pass


def _add(path, scale=100):
    try:
        from enigma import addFont
        try:
            addFont(path, "Replacement", int(scale), True)
        except TypeError:
            addFont(path, "Replacement", int(scale), True, 0)
        return True
    except Exception as e:
        _log("addFont {}: {}".format(path, e))
        return False


def ensure_arabic():
    for path in _CANDIDATES:
        if os.path.exists(path):
            if _add(path):
                _log("fallback font -> {}".format(path))
            return path
    return ""


def _skin_replacement():
    """(file, scale) of the active skin's Replacement font, or None."""
    try:
        skin = ""
        with open("/etc/enigma2/settings") as f:
            for line in f:
                if line.startswith("config.skin.primary_skin="):
                    skin = line.split("=", 1)[1].strip()
        path = os.path.join("/usr/share/enigma2", skin or "skin.xml")
        if not os.path.exists(path):
            return None
        with open(path, errors="replace") as f:
            xml = f.read()
        for tag in re.findall(r"<font\b[^>]*>", xml):
            if 'replacement="1"' in tag:
                fn = re.search(r'filename="([^"]+)"', tag)
                sc = re.search(r'scale="(\d+)"', tag)
                if fn:
                    file_ = fn.group(1)
                    if not file_.startswith("/"):
                        file_ = os.path.join(os.path.dirname(path), file_)
                    return file_, int(sc.group(1)) if sc else 100
    except Exception as e:
        _log("skin replacement lookup: {}".format(e))
    return None


def restore_skin():
    rep = _skin_replacement()
    if rep and os.path.exists(rep[0]):
        if _add(rep[0], rep[1]):
            _log("fallback font restored -> {}".format(rep[0]))
