# -*- coding: utf-8 -*-
"""NovaPlay player -- the next-episode card and its countdown.

Part of NovaPlaySimplePlayer (plugin_screen_player.py), split out
2026-10-23 without changing behaviour. Methods use the player's widgets
and state through self; module-level helpers come from player_base.
"""
from player_base import *  # noqa: F401,F403


class PlayerAutoNext(object):
    # ── [A3] next-episode delay, from config ────────────────────────────
    def _nextDelaySecs(self):
        """[A3] read next_episode_delay from config; clamped 5-60s."""
        try:
            v = int(_get_config("next_episode_delay", str(self._AUTONEXT_SECONDS_DEFAULT)) or self._AUTONEXT_SECONDS_DEFAULT)
        except Exception:
            v = self._AUTONEXT_SECONDS_DEFAULT
        return max(5, min(60, v))

    # ─── auto-next card ──────────────────────────────────────────────────
    def _showAutoNextCard(self, nxt):
        _delay = self._nextDelaySecs()
        self._autonext_nxt = nxt
        self._autonext_secs = _delay
        try:
            poster = (nxt or {}).get("poster") or getattr(self, "_poster_url", "") or ""
            path = ""
            if poster:
                path = plugin_imagecache.getCachedImage(poster, target_size=(100, 150))
                if not path:
                    try:
                        from plugin_util import _get_cached_poster
                        path = _get_cached_poster(poster) or ""
                    except Exception:
                        path = ""
                if not path:
                    plugin_imagecache.requestImageAsyncPriority(poster, target_size=(100, 150))
                    try:
                        path = plugin_imagecache.getCachedImage(poster) or ""
                    except Exception:
                        path = ""
            if not path:
                path = placeholder_for_item(nxt) or ""
            if path:
                self["autoNextPoster"].instance.setScale(1)
                self["autoNextPoster"].instance.setPixmapFromFile(path)
                self["autoNextPoster"].show()
            else:
                self["autoNextPoster"].hide()
        except Exception:
            self["autoNextPoster"].hide()
        self["autoNextTitle"].setText(_single_line_text(
            (nxt or {}).get("title") or "", width=30, fallback="Next episode"))
        self["autoNextEpisode"].setText("Next episode")
        self["autoNextText"].setText("Playing automatically in %d seconds" % _delay)
        self["autoNextHelp"].setText("OK = play now    EXIT = cancel")
        try:
            self["autoNextProgress"].setRange((0, _delay))
            self["autoNextProgress"].setValue(_delay)
        except Exception:
            pass
        self._autonext_slide_p = 0.0
        for w in self._AUTONEXT_WIDGETS:
            try:
                inst = self[w].instance
                if inst:
                    dx = self._AUTONEXT_DESIGN_X.get(w, 1110)
                    inst.move(ePoint(dx + self._AUTONEXT_SLIDE_OFFSET,
                                     inst.position().y()))
            except Exception:
                pass
        for w in self._AUTONEXT_WIDGETS:
            try: self[w].show()
            except Exception: pass
        self._autonext_active = True
        self._autonext_poster_url = poster if poster else ""
        self._autonext_poster_polls = 8
        try: self._autonext_timer.stop()
        except Exception: pass
        self._autonext_timer.start(1000, False)
        self._startAutoNextSlide(1.0)

    def _hideAutoNextCard(self):
        self._autonext_active = False
        self._autonext_poster_polls = 0
        try: self._autonext_timer.stop()
        except Exception: pass
        self._startAutoNextSlide(0.0)

    def _pl_autonextTick(self):
        if not getattr(self, "_autonext_active", False):
            return
        if getattr(self, "_autonext_poster_polls", 0) > 0 and getattr(self, "_autonext_poster_url", ""):
            self._autonext_poster_polls -= 1
            try:
                p = plugin_imagecache.getCachedImage(self._autonext_poster_url, target_size=(100, 150)) \
                    or plugin_imagecache.getCachedImage(self._autonext_poster_url)
                if p:
                    self._autonext_poster_polls = 0
                    self["autoNextPoster"].instance.setScale(1)
                    self["autoNextPoster"].instance.setPixmapFromFile(p)
                    self["autoNextPoster"].show()
            except Exception:
                pass
        self._autonext_secs -= 1
        if self._autonext_secs <= 0:
            self._hideAutoNextCard()
            self._pl_nextAnswer(True, self._autonext_nxt)
            return
        try:
            self["autoNextText"].setText("Playing automatically in %d seconds" % self._autonext_secs)
            self["autoNextProgress"].setValue(self._autonext_secs)
            if self._autonext_secs <= 3:
                _c = "#FF6B6B" if (self._autonext_secs % 2) else "#E8E8E8"
                try:
                    if parseColor is not None:
                        self["autoNextText"].instance.setForegroundColor(parseColor(_c))
                except Exception:
                    pass
            else:
                try:
                    if parseColor is not None:
                        self["autoNextText"].instance.setForegroundColor(parseColor("#E8E8E8"))
                except Exception:
                    pass
        except Exception:
            pass

    def _pl_nextAnswer(self, ans, nxt):
        if ans and self._on_next:
            self._skip_restore = True
            cb = self._on_next
            self._pl_onExit(clear_position=True)
            _defer_open_next(cb, nxt)
        else:
            self._pl_onExit(clear_position=True)
