# -*- coding: utf-8 -*-
"""Stand-ins for Enigma2's modules so NovaPlay's screens can be built on a
PC (no receiver needed). Only what building a screen touches is faked:
every enigma / Components / Screens / Tools name becomes a harmless object.
Import this BEFORE any NovaPlay module."""
import importlib.abc
import importlib.machinery
import os
import re
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


class Dummy(object):
    """a session / service / anything: every call and attribute works"""
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return Dummy()
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return Dummy()
    def __iter__(self): return iter([])
    def __bool__(self): return False
    def __int__(self): return 0
    def __index__(self): return 0
    def __float__(self): return 0.0


class Screen(object):
    """Screens.Screen.Screen: keeps the widgets so they can be compared with the skin"""
    def __init__(self, session, parent=None):
        self.session = session
        self._hw = {}
        for n in ("onLayoutFinish", "onClose", "onShown", "onExecBegin", "onShow", "onHide",
                  "onFirstExecBegin", "onExecEnd"):
            setattr(self, n, [])
        self.instance = None
    def __setitem__(self, k, v): self._hw[k] = v
    def __getitem__(self, k): return self._hw[k]
    def __contains__(self, k): return k in self._hw
    def close(self, *a): pass
    def setTitle(self, *a): pass


class _Meta(type):
    def __getattr__(cls, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return n


class Comp(object, metaclass=_Meta):
    """any Enigma2 component / C++ object"""
    def __init__(self, *a, **k):
        self.instance = None
        self.onSelectionChanged = []
        self.list = []
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return Comp()
    def __call__(self, *a, **k): return Comp()
    def append(self, *a): pass
    def get(self, *a): return Comp()
    def __bool__(self): return False
    def __iter__(self): return iter([])


class _Generic(types.ModuleType):
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return Comp


_FAKE = ("enigma", "Screens", "Components", "Tools", "skin", "twisted", "Plugins",
         "ServiceReference", "keyids", "RecordTimer", "Navigation")


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in _FAKE:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
    def create_module(self, spec):
        m = _Generic(spec.name)
        m.__path__ = []
        if spec.name == "Screens.Screen":
            m.Screen = Screen
        return m
    def exec_module(self, m): pass


sys.meta_path.insert(0, _Finder())
os.chdir(REPO)


def widgets_of(xml):
    return set(re.findall(r'<widget[^>]*\sname="(\w+)"', xml or ""))
