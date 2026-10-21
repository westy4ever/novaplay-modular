# -*- coding: utf-8 -*-
"""
Picture modes for the NovaPlay player (Menu panel -> Picture column).

Uses the box's own video controls under /proc/stb (Enigma2 drivers):
  * /proc/stb/video/policy   - how 4:3 pictures are shown on a 16:9 TV
    /proc/stb/video/policy2  - how 16:9 pictures are shown (and, on most
    drivers, other aspect mismatches): letterbox / panscan / bestfit /
    nonlinear (stretch) / scale ... -- only values listed in the matching
    *_choices file are offered.
  * /proc/stb/vmpeg/0/dst_{left,top,width,height} (hex, 720x576 units) -
    the video window. A window larger than the screen is a zoom (crops the
    black bars of 2.35:1 films). Drivers that refuse a window outside the
    screen just keep the normal picture; the player reports it.

Everything changed is remembered and put back when playback ends.
"""
import os

_VIDEO = "/proc/stb/video/"
_VMPEG = "/proc/stb/vmpeg/0/"

# (label, kind, value)
MODES = (
    (u"Letterbox", "policy", "letterbox"),
    (u"Pan & Scan", "policy", "panscan"),
    (u"Best fit", "policy", "bestfit"),
    (u"Stretch", "policy", ("nonlinear", "scale", "full")),
    (u"Zoom 1.15×", "zoom", 1.15),
    (u"Zoom 1.33× — fill 2.35:1", "zoom", 1.33),
)

_saved = {}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def _write(path, value):
    try:
        if path not in _saved:
            old = _read(path)
            if old is not None:
                _saved[path] = old
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except Exception:
        return False


def _choices(name):
    return (_read(_VIDEO + name + "_choices") or "").split()


def _policy_value(value):
    vals = value if isinstance(value, tuple) else (value,)
    have = set(_choices("policy")) | set(_choices("policy2"))
    for v in vals:
        if v in have:
            return v
    return None


def available():
    """Modes this box supports -> list of (label, kind, value)."""
    out = []
    for label, kind, value in MODES:
        if kind == "policy":
            v = _policy_value(value)
            if v:
                out.append((label, kind, v))
        elif kind == "zoom" and os.path.exists(_VMPEG + "dst_width"):
            out.append((label, kind, value))
    return out


def _apply_dst():
    _write(_VMPEG + "dst_apply", "1") if os.path.exists(_VMPEG + "dst_apply") else None


def apply(kind, value):
    """-> True when the box accepted the change."""
    restore(keep_pep=True)
    if kind == "policy":
        ok = False
        for name in ("policy", "policy2"):
            if value in _choices(name):
                ok = _write(_VIDEO + name, value) or ok
        return ok
    if kind == "zoom":
        w, h = int(720 * value), int(576 * value)
        left, top = (720 - w) // 2, (576 - h) // 2
        ok = True
        for name, v in (("dst_left", left), ("dst_top", top), ("dst_width", w), ("dst_height", h)):
            ok = _write(_VMPEG + name, "%08x" % (v & 0xFFFFFFFF)) and ok
        _apply_dst()
        if ok:
            got = _read(_VMPEG + "dst_width")
            try:
                ok = int(got, 16) == w
            except Exception:
                ok = False
        return ok
    return False


def restore(keep_pep=False):
    """Put back everything this module changed (keep_pep: leave brightness /
    contrast / colour as they are)."""
    if not _saved:
        return
    if keep_pep:
        keep = dict((p, v) for p, v in _saved.items() if "pep_" in p)
        for p in keep:
            _saved.pop(p)
        _restore_items()
        _saved.update(keep)
        return
    _restore_items(all_items=True)


def _restore_items(all_items=False):
    if not _saved:
        return
    dst = False
    paths = list(_saved)
    for path, old in list(_saved.items()):
        try:
            with open(path, "w") as f:
                f.write(old)
        except Exception:
            pass
        dst = dst or path.startswith(_VMPEG)
    _saved.clear()
    _pep_level.clear()
    if any("pep_" in p for p in paths):
        try:
            if os.path.exists(_VMPEG + "pep_apply"):
                with open(_VMPEG + "pep_apply", "w") as f:
                    f.write("1")
        except Exception:
            pass
    if dst:
        try:
            if os.path.exists(_VMPEG + "dst_apply"):
                with open(_VMPEG + "dst_apply", "w") as f:
                    f.write("1")
        except Exception:
            pass


# ─── Brightness / contrast / colour (Picture Enhancement, /proc/stb/vmpeg/0/pep_*) ──
# Same files and scale as Enigma2's own Video Enhancement settings:
# level 0..256 (128 = normal), written as "%08X" % (level * 256), then pep_apply.
PEP = (("brightness", u"Brightness"), ("contrast", u"Contrast"), ("saturation", u"Colour"))
PEP_STEP = 16
_pep_level = {}


def pep_available():
    return [(k, lbl) for k, lbl in PEP if os.path.exists(_VMPEG + "pep_" + k)]


def pep_get(key):
    if key in _pep_level:
        return _pep_level[key]
    raw = _read(_VMPEG + "pep_" + key)
    try:
        lvl = int(raw, 16) // 256
        if not 0 <= lvl <= 256:
            raise ValueError
    except Exception:
        lvl = 128
    _pep_level[key] = lvl
    return lvl


def pep_set(key, level):
    level = max(0, min(256, int(level)))
    ok = _write(_VMPEG + "pep_" + key, "%08X" % (level * 256))
    if ok and os.path.exists(_VMPEG + "pep_apply"):
        try:
            with open(_VMPEG + "pep_apply", "w") as f:
                f.write("1")
        except Exception:
            pass
    if ok:
        _pep_level[key] = level
    return ok


def pep_percent(key):
    return int(round(pep_get(key) * 100 / 256.0))


def pep_forget():
    _pep_level.clear()
