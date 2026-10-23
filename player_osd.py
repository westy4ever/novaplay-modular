# -*- coding: utf-8 -*-
"""NovaPlay player -- the overlay: show / hide / slide, the 1-second update (times, seek bar,
video info), seek flash and bubble, pause sign, quality wipe, aspect bars,
studio slide, sync toast, MENU-skip hint.

Part of NovaPlaySimplePlayer (plugin_screen_player.py), split out
2026-10-23 without changing behaviour. Methods use the player's widgets
and state through self; module-level helpers come from player_base.
"""
from player_base import *  # noqa: F401,F403


class PlayerOSD(object):
    # ── [player-anim] OSD slide helpers ─────────────────────────────────
    def _designX(self, key):
        try:
            inst = self[key].instance
            if inst:
                return inst.position().x()
        except Exception:
            pass
        return 0

    def _moveResizeY(self, key, design_y, offset, w=None, h=None):
        try:
            inst = self[key].instance
            if not inst:
                return
            inst.move(ePoint(int(self._designX(key)),
                             int(design_y + offset)))
            if w is not None and h is not None:
                inst.resize(eSize(int(w), int(h)))
        except Exception as e:
            my_log("moveResizeY error for {}: {}".format(key, e))

    def _startOSDSlide(self, target):
        self._osd_slide_target = target
        try:
            self._osd_slide_timer.start(self._OSD_ANIM_MS, False)
        except Exception as e:
            my_log("OSD slide start failed: {}".format(e))

    def _osdSlideTick(self):
        diff = self._osd_slide_target - self._osd_slide_p
        if abs(diff) < 0.02:
            self._osd_slide_p = self._osd_slide_target
            self._applyOSDSlide()
            try: self._osd_slide_timer.stop()
            except Exception: pass
            if self._osd_slide_p <= 0.01 and not self._osd_visible:
                for w in self._OSD_WIDGETS:
                    try: self[w].hide()
                    except Exception: pass
            return
        self._osd_slide_p += diff * self._OSD_ANIM_EASE
        self._applyOSDSlide()

    def _applyOSDSlide(self):
        offset = int((1.0 - self._osd_slide_p) * self._OSD_SLIDE_OFFSET)
        for w in self._OSD_WIDGETS:
            dy = self._OSD_DESIGN_Y.get(w)
            if dy is None:
                continue
            self._moveResizeY(w, dy, offset)

    # ── [player-anim] auto-next card slide helpers ──────────────────────
    def _startAutoNextSlide(self, target):
        self._autonext_slide_target = target
        try:
            self._autonext_slide_timer.start(self._AUTONEXT_ANIM_MS, False)
        except Exception as e:
            my_log("auto-next slide start failed: {}".format(e))

    def _autonextSlideTick(self):
        diff = self._autonext_slide_target - self._autonext_slide_p
        if abs(diff) < 0.02:
            self._autonext_slide_p = self._autonext_slide_target
            self._applyAutoNextSlide()
            try: self._autonext_slide_timer.stop()
            except Exception: pass
            if self._autonext_slide_p <= 0.01:
                for w in self._AUTONEXT_WIDGETS:
                    try: self[w].hide()
                    except Exception: pass
            return
        self._autonext_slide_p += diff * self._AUTONEXT_ANIM_EASE
        self._applyAutoNextSlide()

    def _applyAutoNextSlide(self):
        offset = int((1.0 - self._autonext_slide_p) * self._AUTONEXT_SLIDE_OFFSET)
        for w in self._AUTONEXT_WIDGETS:
            dx = self._AUTONEXT_DESIGN_X.get(w)
            if dx is None:
                continue
            try:
                inst = self[w].instance
                if inst:
                    pos = inst.position()
                    inst.move(ePoint(int(dx + offset), int(pos.y())))
            except Exception:
                pass

    # ── [player-anim] seek flash ────────────────────────────────────────
    def _showSeekFlash(self, direction, delta_secs, target_secs):
        try:
            if direction > 0:
                arrow = u"▶▶"
            else:
                arrow = u"◀◀"
            _th = target_secs // 3600
            _tm = (target_secs % 3600) // 60
            _ts = target_secs % 60
            tstr = u"{:02d}:{:02d}:{:02d}".format(_th, _tm, _ts)
            self["seekFlash"].setText(u"{} {}s\n{}".format(arrow, int(abs(delta_secs)), tstr))
            self["seekFlash"].show()
            try:
                self._seek_flash_timer.stop()
            except Exception:
                pass
            self._seek_flash_timer.start(self._SEEK_FLASH_MS, True)
        except Exception as e:
            my_log("seekFlash error: {}".format(e))

    def _hideSeekFlash(self):
        try: self["seekFlash"].hide()
        except Exception: pass
        try: self["seekBubble"].hide()
        except Exception: pass

    @staticmethod
    def _clockText(secs):
        """[osd-ui] 42:10 / 1:02:10"""
        secs = max(0, int(secs))
        h, rem = divmod(secs, 3600)
        m, s_ = divmod(rem, 60)
        return u"{}:{:02d}:{:02d}".format(h, m, s_) if h else u"{}:{:02d}".format(m, s_)

    def _showSeekBubble(self, target_secs):
        """[osd-ui] small gold bubble with the target time over the seek bar,
        right above where the marker will be"""
        try:
            total = self._total_secs
            if total <= 0:
                return
            pct = min(1.0, max(0.0, float(target_secs) / float(total)))
            x = 184 + int(1552 * pct) - 75
            x = max(170, min(1750 - 150, x))
            inst = self["seekBubble"].instance
            if inst is not None:
                inst.move(ePoint(x, 818))
            self["seekBubble"].setText(self._clockText(target_secs))
            self["seekBubble"].show()
        except Exception as e:
            my_log("seek bubble: {}".format(e))

    def _showPauseBadge(self):
        try:
            self["pauseBadge"].show()
            self._pause_badge_timer.stop()
            self._pause_badge_timer.start(1500, True)
        except Exception:
            pass

    def _hidePauseBadge(self):
        try:
            self._pause_badge_timer.stop()
        except Exception:
            pass
        try:
            self["pauseBadge"].hide()
        except Exception:
            pass

    # ── [player-anim] quality switch wipe ───────────────────────────────
    def _startQualityWipe(self, on_midpoint=None):
        self._quality_wipe_cb = on_midpoint
        self._quality_wipe_frame = 0
        self._quality_wipe_target = 1
        try:
            self["qualityWipe"].show()
            self._moveResizeBar("qualityWipe", 0, 0, 1920, 0)
            self._quality_wipe_timer.start(self._QUALITY_WIPE_MS, False)
        except Exception as e:
            my_log("quality wipe start failed: {}".format(e))
            if on_midpoint:
                try: on_midpoint()
                except Exception: pass

    def _moveResizeBar(self, key, x, y, w, h):
        try:
            inst = self[key].instance
            if inst:
                inst.move(ePoint(int(x), int(y)))
                inst.resize(eSize(int(w), int(h)))
        except Exception:
            pass

    def _qualityWipeTick(self):
        self._quality_wipe_frame += 1
        frames = self._QUALITY_WIPE_FRAMES
        p = min(1.0, float(self._quality_wipe_frame) / float(frames))
        if self._quality_wipe_target == 1:
            h = int(1080 * p)
            self._moveResizeBar("qualityWipe", 0, 0, 1920, h)
            if p >= 1.0:
                if self._quality_wipe_cb:
                    cb = self._quality_wipe_cb
                    self._quality_wipe_cb = None
                    try: cb()
                    except Exception as e:
                        my_log("quality wipe midpoint error: {}".format(e))
                self._quality_wipe_frame = 0
                self._quality_wipe_target = 0
        else:
            y = int(-1080 * p)
            h = int(1080 * (1.0 - p))
            self._moveResizeBar("qualityWipe", 0, y + 1080, 1920, h)
            if p >= 1.0:
                try:
                    self._quality_wipe_timer.stop()
                    self["qualityWipe"].hide()
                    self._moveResizeBar("qualityWipe", 0, 0, 1920, 0)
                except Exception:
                    pass

    # ── [player-anim] aspect-bar slide ─────────────────────────────────
    def _aspectAnimTick(self):
        diff = 1.0 - self._aspect_anim_p
        if diff < 0.02:
            self._aspect_anim_p = 1.0
            self._applyAspectBarWidth(self._aspect_anim_to)
            try: self._aspect_anim_timer.stop()
            except Exception: pass
            if self._aspect_anim_to == 0:
                try:
                    self["aspect_bar_l"].hide()
                    self["aspect_bar_r"].hide()
                except Exception:
                    pass
            return
        self._aspect_anim_p += 0.25
        p_eased = 1.0 - (1.0 - min(1.0, self._aspect_anim_p)) ** 3
        cur = int(self._aspect_anim_from
                  + (self._aspect_anim_to - self._aspect_anim_from) * p_eased)
        self._applyAspectBarWidth(cur)

    def _applyAspectBarWidth(self, w):
        try:
            inst_l = self["aspect_bar_l"].instance
            inst_r = self["aspect_bar_r"].instance
            w = max(1, int(w))
            if inst_l:
                inst_l.resize(eSize(w, 1080))
                inst_l.move(ePoint(0, 0))
            if inst_r:
                inst_r.resize(eSize(w, 1080))
                inst_r.move(ePoint(1920 - w, 0))
        except Exception:
            pass

    # ── [player-anim] studio-panel slide ───────────────────────────────
    def _applyStudioSlide(self):
        offset = int((1.0 - self._studio_slide_p) * -self._STUDIO_SLIDE_PX)
        for k in self._STUDIO_KEYS:
            dy = self._STUDIO_DESIGN_Y.get(k)
            if dy is None:
                continue
            try:
                inst = self[k].instance
                if inst:
                    pos = inst.position()
                    inst.move(ePoint(int(pos.x()), int(dy + offset)))
            except Exception:
                pass

    def _studioSlideTick(self):
        diff = self._studio_slide_target - self._studio_slide_p
        if abs(diff) < 0.02:
            self._studio_slide_p = self._studio_slide_target
            self._applyStudioSlide()
            try: self._studio_slide_timer.stop()
            except Exception: pass
            if self._studio_slide_p <= 0.01:
                for k in self._STUDIO_KEYS:
                    try: self[k].hide()
                    except Exception: pass
            return
        self._studio_slide_p += diff * 0.35
        self._applyStudioSlide()

    # ── [player-anim] sync-offset toast ────────────────────────────────
    def _showSyncToast(self, text):
        try:
            self["syncToast"].setText(text)
            self["syncToast"].show()
            self._sync_toast_x_from = 1920 + 20
            self._sync_toast_x_target = 1550
            self._sync_toast_p = 0.0
            try:
                self["syncToast"].instance.move(ePoint(self._sync_toast_x_from, 760))
            except Exception:
                pass
            self._sync_toast_frame = 0
            try:
                self._sync_toast_slide_timer.stop()
                self._sync_toast_slide_timer.start(20, False)
            except Exception:
                pass
            try:
                self._sync_toast_timer.stop()
                self._sync_toast_timer.start(1500, True)
            except Exception:
                pass
        except Exception as e:
            my_log("showSyncToast error: {}".format(e))

    def _syncToastSlideTick(self):
        self._sync_toast_frame += 1
        p = min(1.0, self._sync_toast_frame / 6.0)
        p_eased = 1.0 - (1.0 - p) ** 3
        x = int(self._sync_toast_x_from
                + (self._sync_toast_x_target - self._sync_toast_x_from) * p_eased)
        try:
            inst = self["syncToast"].instance
            if inst:
                inst.move(ePoint(x, 760))
        except Exception:
            pass
        if p >= 1.0:
            try: self._sync_toast_slide_timer.stop()
            except Exception: pass

    def _hideSyncToast(self):
        try:
            self["syncToast"].hide()
        except Exception:
            pass

    # ── [player-ux] extraction skip hint ────────────────────────────────
    def _armSkipHint(self):
        self._skip_hint_shown = False
        try:
            self._skip_hint_timer.stop()
            self._skip_hint_timer.start(self._EXTRACT_HINT_MS, True)
        except Exception:
            pass

    def _disarmSkipHint(self):
        try: self._skip_hint_timer.stop()
        except Exception: pass
        self._skip_hint_shown = False

    def _showSkipHint(self):
        if getattr(self, "_closed", False) or self._play_confirmed:
            return
        self._skip_hint_shown = True
        try:
            cur = self["status"].getText() or "Playing..."
            self["status"].setText(u"{}   —   MENU = skip".format(cur))
            self._pl_showOSD(True)
        except Exception:
            pass

    # ─── OSD ────────────────────────────────────────────────────────────
    def _pl_initOSD(self):
        for w in self._OSD_WIDGETS + list(self._OSD_RETIRED) + ["seekBubble", "pauseBadge"]:
            try: self[w].hide()
            except: pass
        for w in self._AUTONEXT_WIDGETS:
            try: self[w].hide()
            except: pass

    def _pl_autoHideOSD(self):
        """[osd-ui] the auto-hide timer: the OSD stays while paused and while
        the player methods are still being tried (until one plays)"""
        if self._paused or not getattr(self, "_play_confirmed", False):
            return
        self._pl_hideOSD()

    def _pl_hideOSD(self):
        if self._osd_visible:
            self._osd_visible = False
            try: self["seekBubble"].hide()
            except Exception: pass
            try: self._osd_update_timer.stop()
            except: pass
            self._startOSDSlide(0.0)

    def _pl_showOSD(self, auto_hide=True):
        self._osd_visible = True
        if self._osd_slide_p <= 0.01:
            self._osd_slide_p = 0.0
        for w in self._OSD_WIDGETS:
            try: self[w].show()
            except: pass
        self._startOSDSlide(1.0)
        try:
            self._paintOsdPoster()
        except Exception:
            pass
        self._pl_updateOSD()
        try:
            self._osd_update_timer.start(1000, False)
        except: pass
        if auto_hide:
            try:
                self._hide_timer.stop()
                self._hide_timer.start(self._osd_auto_hide_secs * 1000, True)
            except: pass

    def _paintOsdPoster(self):
        if getattr(self, "_poster_final", False):
            return
        url = getattr(self, "_poster_url", "") or ""
        if not url:
            self._poster_final = True
            if not getattr(self, "_poster_painted", False):
                self._pl_setOsdPosterPixmap(placeholder_for_item({"type": "movie"}) or "")
                self._poster_painted = True
            return
        path = ""
        try:
            from plugin_util import _get_cached_poster
            path = _get_cached_poster(url) or ""
        except Exception:
            path = ""
        if not path:
            try:
                path = plugin_imagecache.getCachedImage(url, target_size=(180, 270)) or ""
            except Exception:
                path = ""
        if not path and not getattr(self, "_poster_requested", False):
            try:
                plugin_imagecache.requestImageAsyncPriority(url, target_size=(180, 270))
                self._poster_requested = True
            except Exception:
                pass
        if not path:
            try:
                path = plugin_imagecache.getCachedImage(url) or ""
            except Exception:
                path = ""
        if path:
            self._poster_final = True
            self._poster_painted = True
            self._pl_setOsdPosterPixmap(path)
            my_log("OSD poster painted: {}".format(url[:80]))
            return
        if not getattr(self, "_poster_painted", False):
            self._pl_setOsdPosterPixmap(placeholder_for_item({"type": "movie"}) or "")
            self._poster_painted = True
            my_log("OSD poster placeholder shown (waiting for download)")

    def _pl_setOsdPosterPixmap(self, path):
        if not path:
            return
        try:
            self["osdPoster"].instance.setScale(1)
            self["osdPoster"].instance.setPixmapFromFile(path)
            self["osdPoster"].show()
            self["osdPosterBox"].show()
            self["posterEdge"].show()
        except Exception:
            try:
                self["osdPoster"].hide()
                self["osdPosterBox"].hide()
                self["posterEdge"].hide()
            except Exception:
                pass

    # ── [player-ux] state glyph ─────────────────────────────────────────
    def _stateGlyph(self):
        """▶ playing · ⏸ paused · ⏳ buffering · ⚠ stalled/recovering"""
        if getattr(self, "_stall_recovering", False):
            return u"⚠", "#FF6B6B"
        if getattr(self, "_paused", False):
            return u"⏸", "#F2C230"
        svc = None
        try:
            svc = self.session.nav.getCurrentService()
        except Exception:
            svc = None
        if svc is None:
            return u"⏳", "#8B949E"
        try:
            sk = svc.seek()
            if sk:
                r = sk.getPlayPosition()
                if r and r[0] != 0:
                    return u"⏳", "#8B949E"
        except Exception:
            pass
        return u"▶", "#39D98A"

    def _pl_updateOSD(self):
        if not self._osd_visible:
            try: self._osd_update_timer.stop()
            except: pass
            return
        try:
            if not getattr(self, "_poster_final", False):
                self._paintOsdPoster()
        except Exception:
            pass
        try:
            if self._paused:
                elapsed = self._paused_elapsed
            else:
                elapsed = current_play_secs()

            color = "#F2C230" if self._paused else "#E5D1A6"
            try:
                if parseColor is not None:
                    self["osd_elapsed"].instance.setForegroundColor(parseColor(color))
            except Exception:
                pass
            self["osd_elapsed"].setText(self._clockText(elapsed))

            # [player-ux] state glyph
            try:
                glyph, gcolor = self._stateGlyph()
                self["osd_state"].setText(glyph)
                if parseColor is not None:
                    try:
                        self["osd_state"].instance.setForegroundColor(parseColor(gcolor))
                    except Exception:
                        pass
            except Exception:
                pass

            if self._total_secs > 0 and not self._osd_video_info:
                # [FIX] width/height read 0/-1 for the first seconds of a stream and the
                # frame rate comes in thousandths (24000 = 24 fps): keep asking until the
                # size is known, and show the rate as fps.
                try:
                    service = self.session.nav.getCurrentService()
                    info = service.info() if service else None
                    w = h = fr = 0
                    if info is not None and iServiceInformation is not None:
                        for wk, hk in (("sVideoWidth", "sVideoHeight"), ("sWidth", "sHeight")):
                            try:
                                w = info.getInfo(getattr(iServiceInformation, wk))
                                h = info.getInfo(getattr(iServiceInformation, hk))
                            except Exception:
                                w = h = 0
                            if w > 0 and h > 0:
                                break
                        try:
                            fr = info.getInfo(iServiceInformation.sFrameRate)
                        except Exception:
                            fr = 0
                    parts = []
                    if w > 0 and h > 0:
                        parts.append(u"{}×{}".format(w, h))
                    if fr and fr > 0:
                        fps = fr / 1000.0 if fr > 1000 else float(fr)
                        parts.append(u"{:g}fps".format(round(fps, 3)))
                    # only keep it once the size is known (otherwise ask again next tick)
                    if w > 0 and h > 0:
                        self._osd_video_info = u" · ".join(parts)
                    else:
                        self["osd_videinfo"].setText(u" · ".join(parts))
                except Exception:
                    self._osd_video_info = ""
            if self._osd_video_info:
                self["osd_videinfo"].setText(self._osd_video_info)

            try:
                from novaplay_subtitles import get_subtitle_state
                _sub_state = get_subtitle_state() or {}
                _cc_on = bool(_sub_state.get("path"))
                self["osd_subinfo"].setText("CC" if _cc_on else "cc")
                # [POLISH v1] subtitle file + offset in the second row (empty when off)
                try:
                    _detail = ""
                    if _cc_on:
                        _fn = os.path.basename(_sub_state.get("path") or "")
                        _off = int(_sub_state.get("offset_ms") or 0)
                        _detail = ("CC · {} · {:+d}ms".format(_fn[:38], _off) if _off
                                   else "CC · {}".format(_fn[:44]))
                    self["osd_subdetail"].setText(_detail)
                except Exception:
                    pass
                if parseColor is not None:
                    try:
                        self["osd_subinfo"].instance.setForegroundColor(
                            parseColor("#F2C230" if _cc_on else "#6E7681"))
                    except Exception:
                        pass
            except Exception:
                pass

            now = time.strftime("%H:%M")
            self["osd_clock"].setText(now)

            # [POLISH v1] episode id (+ next episode) and the last seek step
            try:
                _ep_text = ""
                _m = re.search(r"\bS(\d{1,2})\s*E(\d{1,3})\b", self._raw_title or "", re.I)
                if _m:
                    _ep_text = "S{}E{}".format(_m.group(1).zfill(2), _m.group(2).zfill(2))
                    _nt = ""
                    try:
                        _nt = (self._next_episode or {}).get("title") or ""
                    except Exception:
                        pass
                    _m2 = re.search(r"\bS\d{1,2}\s*E(\d{1,3})\b", _nt, re.I)
                    if _m2:
                        _ep_text += " → E" + _m2.group(1).zfill(2)
                self["osd_episode"].setText(_ep_text)
                self._osd_step = int(getattr(self, "_last_seek_step", 10) or 10)
            except Exception:
                pass

            total = self._total_secs
            if not total:
                try:
                    svc = self.session.nav.getCurrentService()
                    seek = svc and svc.seek()
                    if seek:
                        r = seek.getLength()
                        if r and r[0] == 0 and r[1] > 0:
                            total = r[1] // 90000
                            self._total_secs = total
                except: pass
            if total > 0:
                rem = max(0, total - elapsed)
                pct = min(1.0, float(elapsed) / float(total))
                # [osd-ui] left: 42:10 / 2:10:00 -- right: -1:27:50 · Ends 23:14
                self["osd_elapsed"].setText(u"{} / {}".format(self._clockText(elapsed), self._clockText(total)))
                self["osd_durtext"].setText(u"\u2212{}  \u00b7  Ends {}".format(
                    self._clockText(rem), time.strftime("%H:%M", time.localtime(time.time() + rem))))
                try:
                    self["seekbar"].setRange((0, 100))
                    self["seekbar"].setValue(int(pct * 100))
                except Exception:
                    pass
                self["prog_bar"].setText("{:.0f}%".format(pct * 100))
                # [POLISH v1] playhead marker follows the fill edge
                try:
                    _sb = self["seekbar"].instance
                    _mk = self["seekMarker"].instance
                    if _sb and _mk:
                        _sbp = _sb.position(); _sbs = _sb.size(); _mks = _mk.size()
                        _wx = _sbp.x() + int(_sbs.width() * pct) - (_mks.width() // 2)
                        _wy = _sbp.y() - ((_mks.height() - _sbs.height()) // 2)
                        _mk.move(ePoint(int(_wx), int(_wy)))
                except Exception:
                    pass

                # [player-ux] watched-at-90%
                if (not getattr(self, "_watched_at_90_fired", False)
                        and pct >= 0.90):
                    self._watched_at_90_fired = True
                    try:
                        from plugin_watched import mark_watched
                        if self._item_url:
                            mark_watched(self._item_url)
                            my_log("watched-at-90%: marked {}".format(self._item_url[:60]))
                    except Exception as e:
                        my_log("watched-at-90% error: {}".format(e))
            else:
                self["osd_durtext"].setText("")
                self["prog_bar"].setText("")
                try:
                    self["seekbar"].setValue(0)
                except Exception:
                    pass
            try:
                self["keyGreen"].setText(self._greenLabel())
            except Exception:
                pass
            # [POLISH v1] the colour keys are shown as chips now
            self["osd_keys"].setText(u"OK pause  \u00b7  0-9 jump  \u00b7  \u25c0\u25b6 \u00b1{}s  \u00b7  STOP exit".format(
                getattr(self, "_osd_step", 10)))
        except Exception as e:
            my_log("updateOSD error: {}".format(e))
