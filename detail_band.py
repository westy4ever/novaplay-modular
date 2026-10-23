# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- Modern style's server / episode cards band: placing, pop and slide,
painting the cards.

Part of NovaPlayDetail (plugin_screen_detail.py), split out 2026-10-23
without changing behaviour. Methods use the screen's widgets and state
through self; module-level helpers come from detail_base.
"""
from detail_base import *  # noqa: F401,F403


class DetailBand(object):
    # ── [theme] Modern server cards ───────────────────────────────────
    def _svInit(self):
        try:
            self["menu"].hide()
            self["menuAccent"].hide()
        except Exception:
            pass
        for i in range(CAST_N):
            self["castImg%d" % i].hide()
            self["castInit%d" % i].hide()
            self["castName%d" % i].hide()
        try:
            # every selection move (incl. the auto-pick of the last server)
            self["menu"].instance.selectionChanged.get().append(self._svPaint)
        except Exception as e:
            my_log("detail modern: selectionChanged hook failed: {}".format(e))
        self._svPaint()

    # [pop-anim] the selected card grows 8 % (same smooth zoom as the
    # posters / cards on the home and site screens)
    _SV_PARTS = (("svEdge", -4, -4, _SV_W + 8, _SV_H + 8, True), ("svBg", 0, 0, _SV_W, _SV_H, True),
                 ("svNum", 16, 14, 46, 46, True), ("svQ", _SV_W - 126, 14, 110, 30, False),
                 ("svName", 16, 68, _SV_W - 32, 62, False), ("svInfo", 16, 118, _SV_W - 32, 24, False))

    def _svPlace(self, i, p, xoff=0.0):
        x0 = _SV_X0 + i * (_SV_W + _SV_GAP) + xoff
        k = 1.0 + 0.08 * p
        cx, cy = x0 - (_SV_W * k - _SV_W) / 2.0, _SV_Y - (_SV_H * k - _SV_H) / 2.0
        geo = self.__dict__.setdefault("_sv_geo", {})
        for name, rx, ry, w, h, rs in self._SV_PARTS:
            key = "%s%d" % (name, i)
            pos = (int(cx + rx * k), int(cy + ry * k))
            size = (max(1, int(w * k)), max(1, int(h * k))) if rs else None
            old = geo.get(key)
            if old == (pos, size):
                continue                      # [perf] nothing would change
            try:
                inst = self[key].instance
                if inst is None:
                    continue
                if not old or old[0] != pos:
                    inst.move(ePoint(*pos))
                if size is not None and (not old or old[1] != size):
                    inst.resize(eSize(*size))
                geo[key] = (pos, size)
            except Exception:
                pass

    def _svPopFocus(self, slot, extra):
        key = (slot,) + tuple(extra) + (id(self._sv_items),) if slot >= 0 else None
        if key == self._sv_pop_key:
            return
        try:
            self._svPopT.stop()
        except Exception:
            pass
        if 0 <= self._sv_pop_slot < SV_CARDS:
            self._svPlace(self._sv_pop_slot, 0.0)
        self._sv_pop_key = key
        self._sv_pop_slot = slot if (slot >= 0 and extra[1]) else -1
        if self._sv_pop_slot < 0 and not self._sv_slide:
            return
        self._sv_pop_f = 0
        self._svFrame(0.0)
        try:
            self._svPopT.start(POP_ANIM_INTERVAL, False)
        except Exception:
            self._svFrame(1.0)

    def _svFrame(self, p):
        """[slide-anim] one step past the edge: the whole row glides one card
        sideways while the newly selected card grows; otherwise just the zoom."""
        d = self._sv_slide
        if d:
            off = d * (_SV_W + _SV_GAP) * (1.0 - p)
            for i in range(SV_CARDS):
                self._svPlace(i, p if i == self._sv_pop_slot else 0.0, off)
        elif self._sv_pop_slot >= 0:
            self._svPlace(self._sv_pop_slot, p)

    def _svPopTick(self):
        import math
        if (self._sv_pop_slot < 0 and not self._sv_slide) or getattr(self, "_closed", False):
            self._svPopT.stop()
            return
        self._sv_pop_f += 1
        t = min(1.0, self._sv_pop_f / float(max(1, anim_frames())))
        self._svFrame(math.sin(t * math.pi / 2))
        if t >= 1.0:
            self._svPopT.stop()
            self._sv_slide = 0

    def _svStep(self, d):
        n = len(self._sv_items)
        if not n:
            return
        idx = self["menu"].getCurrentIndex()
        t = idx + d
        if abs(d) == 1:
            t %= n                       # wrap around at the ends
        else:
            t = max(0, min(n - 1, t))
        try:
            self["menu"].instance.moveSelectionTo(t)
        except Exception:
            pass
        self._svPaint()

    def _svColor(self, name, bg=None, fg=None):
        set_color(self, name, bg, fg)        # [ui-helpers]
    def _svPaint(self):
        if not getattr(self, "_modern", False) or getattr(self, "_closed", False):
            return
        items = self._sv_items or []
        n = len(items)
        try:
            idx = self["menu"].getCurrentIndex()
        except Exception:
            idx = 0
        idx = max(0, min(idx, n - 1)) if n else 0
        # [slide-anim] the row scrolls one card at a time: stepping past the
        # last (first) visible card moves the window by one and slides
        old = getattr(self, "_sv_base", 0)
        if id(items) != getattr(self, "_sv_list_id", None):
            self._sv_list_id = id(items)
            base = min((idx // SV_CARDS) * SV_CARDS, max(0, n - SV_CARDS))
            old = base
        else:
            base = old
            if idx < base:
                base = idx
            elif idx >= base + SV_CARDS:
                base = idx - SV_CARDS + 1
            base = max(0, min(base, max(0, n - SV_CARDS)))
        self._sv_base = base
        if abs(base - old) == 1:
            self._sv_slide = base - old
        for i in range(SV_CARDS):
            g = base + i
            names = ["%s%d" % (k, i) for k in ("svEdge", "svBg", "svNum", "svQ", "svName", "svInfo")]
            if g >= n:
                for k in names:
                    self[k].hide()
                continue
            it = tuple(items[g]) + ("", "", "", "")
            q, name, size, extra = (str(x or "") for x in it[:4])
            q = re.sub(r"[\[\]]", "", q).strip()
            sel = g == idx
            if not name:
                # placeholder row ("No servers available")
                for k in names:
                    self[k].hide()
                self["svBg%d" % i].show()
                self["svName%d" % i].setText(_wrap_ui_text(q, width=20, max_lines=2, fallback=""))
                self["svName%d" % i].show()
                continue
            is_ep = bool(re.match(r"^S\d{1,3}E\d{1,4}$", q, re.I))
            self["svEdge%d" % i].show() if sel else self["svEdge%d" % i].hide()
            self["svBg%d" % i].show()
            self._svColor("svBg%d" % i, bg=_SV_ON if sel else _SV_CARD)
            self["svNum%d" % i].setText(str(g + 1))
            self["svNum%d" % i].show()
            badge = q.upper() if q else ""
            if badge in ("", "AUTO"):
                self["svQ%d" % i].hide()
            else:
                gold = badge.startswith("4K") or "2160" in badge
                blue = is_ep or any(t in badge for t in ("HD", "1080", "720", "1440"))
                self["svQ%d" % i].setText(_single_line_text(badge, width=9, fallback=""))
                self._svColor("svQ%d" % i, bg=_SV_GOLD if gold else ("#3E7BFA" if blue else "#566070"),
                              fg="#0C1017" if gold else "#FFFFFF")
                self["svQ%d" % i].show()
            self["svName%d" % i].setText(_wrap_ui_text(name, width=21, max_lines=2, fallback="—"))
            self._svColor("svName%d" % i, fg=_SV_GOLD if sel else "#FFFFFF")
            self["svName%d" % i].show()
            info = " · ".join(x.replace("Size: ", "").replace("Seeders: ", "\u25b2 ") for x in (size, extra) if x)
            if sel and self._last_server_idx == g and not info:
                info = "\u25cf Last watched"
            self["svInfo%d" % i].setText(_single_line_text(info, width=28, fallback=""))
            self["svInfo%d" % i].show()
        self._svPopFocus(idx - base if n else -1, (base, n > 0 and bool(str((tuple(items[idx]) + ("", ""))[1] or ""))))
        pages = (n + SV_CARDS - 1) // SV_CARDS if n else 0
        self["svPos"].setText(u"{} / {}   \u00b7   \u25c0 \u25b6 choose   \u00b7   OK play".format(idx + 1, n) if n else "")
        self["svLeft"].show() if base > 0 else self["svLeft"].hide()
        self["svRight"].show() if base + SV_CARDS < n else self["svRight"].hide()
