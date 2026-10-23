# -*- coding: utf-8 -*-
"""NovaPlay player -- subtitles: Audio/Subs panel, online search (Movy), subtitle actions and
the sync studio.

Part of NovaPlaySimplePlayer (plugin_screen_player.py), split out
2026-10-23 without changing behaviour. Methods use the player's widgets
and state through self; module-level helpers come from player_base.
"""
from player_base import *  # noqa: F401,F403


class PlayerSubs(object):
    # ── [subs-panel] Blue: Audio | Subtitles | More (Movy-style) ─────────
    def _audioTrackItems(self):
        items = []
        try:
            service = self.session.nav.getCurrentService()
            tracks = service and service.audioTracks()
            n = tracks.getNumberOfTracks() if tracks else 0
            cur = tracks.getCurrentTrack() if n else -1
            for i in range(n):
                name = ""
                try:
                    info = tracks.getTrackInfo(i)
                    name = " ".join(x for x in (info.getLanguage(), info.getDescription()) if x)
                except Exception:
                    pass
                if not name:
                    try:
                        name = tracks.getTrackName(i) or ""
                    except Exception:
                        name = ""
                items.append((name or u"Track {}".format(i + 1), i == cur))
        except Exception as e:
            my_log("audio tracks: {}".format(e))
        return items

    def _subsItems(self):
        """Subtitles column + the action behind each row."""
        st = get_subtitle_state() or {}
        path = st.get("path") or ""
        rows, acts = [], []
        rows.append((u"Off — no subtitles", not path)); acts.append(("off", None))
        if path:
            rows.append((os.path.basename(st.get("_orig_path") or path)[:40], True)); acts.append(("cur", None))
        res = getattr(self, "_vidy_results", None)
        if res is None:
            rows.append((u"⏳ Movy subtitles…", False)); acts.append(("wait", None))
        elif not res:
            rows.append((u"Movy: " + (getattr(self, "_vidy_error", "") or u"No subtitles")[:34], False))
            acts.append(("wait", None))
        else:
            for r in res[:60]:
                hi = u" HI" if "(HI)" in (r.get("label") or "") else u""
                rows.append((u"{} · {}{}".format(r.get("language", "?"), r.get("title", "")[:30], hi), False))
                acts.append(("vidy", r))
        self._subs_acts = acts
        return rows

    def _moreItems(self):
        st = get_subtitle_state() or {}
        autosub_on = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
        studio_on = str(_get_config("substudio_mode", "false")).lower() == "true"
        try:
            skip_s = max(10, min(600, int(_get_config("skip_intro_secs", "90") or 90)))
        except Exception:
            skip_s = 90
        rows, acts = [], []
        if st.get("path"):
            off = int(st.get("offset_ms") or 0)
            rows += [(u"Sync +{} ms".format(SYNC_STEP_MS), False, True),
                     (u"Sync −{} ms".format(SYNC_STEP_MS), False, True),
                     (u"Reset sync ({:+d})".format(off), False, True)]
            acts += ["sync+", "sync-", "sync0"]
        rows.append((u"Auto subs: {}".format("ON" if autosub_on else "OFF"), autosub_on, True)); acts.append("autosub_toggle")
        rows.append((u"Skip intro +{}s".format(skip_s), False)); acts.append("skipintro")
        rows.append((u"Studio: {}".format("ON" if studio_on else "OFF"), studio_on)); acts.append("studio_toggle")
        if studio_on:
            rows.append((u"Studio settings", False)); acts.append("studio_open")
        rows += [(u"SubSource", False), (u"OpenSubtitles", False), (u"Local files", False)]
        acts += ["subsource", "opensubtitles", "browse"]
        self._more_acts = acts
        return rows

    def _subsPanel(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        from plugin_player_panel import NovaSubsPanel
        audio = self._audioTrackItems()
        self._audio_count = len(audio)
        if not audio:
            audio = [(u"— " + u"single track", True)]
        subs = self._subsItems()
        more = self._moreItems()
        self._pl_hideOSD()
        self._subs_panel = self.session.openWithCallback(
            self._onSubsPanel, NovaSubsPanel, audio, subs, more, live_cb=self._subsLive)
        self._startVidySearch()

    def _startVidySearch(self):
        """Movy subtitle list, fetched once per film in the background (started
        when playback is confirmed, so Blue opens with the list already there)."""
        if getattr(self, "_vidy_results", None) is not None or getattr(self, "_vidy_busy", False):
            return
        if getattr(self, "_is_preview", False):
            return
        self._vidy_busy = True
        title, item_url = self._raw_title or self.title, self._item_url

        def _bg():
            try:
                from novaplay_subtitles import vidy_search
                ok, err, res = vidy_search(title, item_url)
            except Exception as e:
                ok, err, res = False, str(e)[:40], []
            callInMainThread(self._onVidyResults, res if ok else [], "" if ok else err)
        threading.Thread(target=_bg, daemon=True).start()

    def _onVidyResults(self, res, err):
        self._vidy_busy = False
        self._vidy_results = res or []
        self._vidy_error = err or ""
        my_log("subs panel: Movy subtitles {} ({})".format(len(self._vidy_results), err or "ok"))
        p = getattr(self, "_subs_panel", None)
        if p is not None:
            try:
                p.setColumn("subs", self._subsItems())
            except Exception:
                pass

    def _subsLive(self, col, idx):
        """'stay' rows: run without closing the panel; the text goes on its help line."""
        if col != "more":
            return ""
        act = self._more_acts[idx] if idx < len(self._more_acts) else ""
        msg = ""
        if act == "sync+":
            msg = adjust_sync(self.session, SYNC_STEP_MS)
        elif act == "sync-":
            msg = adjust_sync(self.session, -SYNC_STEP_MS)
        elif act == "sync0":
            msg = reset_sync(self.session)
        elif act == "autosub_toggle":
            was = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
            _set_config("autosub", "false" if was else "true")
            msg = u"Auto subtitles: {}".format("OFF" if was else "ON")
        p = getattr(self, "_subs_panel", None)
        if p is not None:
            sel = idx
            p.setColumn("more", self._moreItems())
            try:
                p._sel["more"] = min(sel, len(p._data["more"]) - 1)
                p._paint()
            except Exception:
                pass
        my_log("subs panel: {} -> {}".format(act, msg))
        return msg or u"✓"

    def _onSubsPanel(self, res):
        self._subs_panel = None
        if not res:
            return
        col, idx = res
        try:
            if col == "audio":
                if idx < getattr(self, "_audio_count", 0):
                    self._pl_audioPicked((u"", idx))
            elif col == "subs":
                act, data = self._subs_acts[idx]
                if act == "off":
                    turn_off_subtitle(self.session, self._item_url)
                    self["status"].setText(u"CC off")
                    self._pl_showOSD(True)
                elif act == "vidy":
                    self._downloadVidySub(data)
            elif col == "more":
                act = self._more_acts[idx]
                self._onSubtitleAction((u"", act))
        except Exception as e:
            my_log("subs panel pick error: {}".format(e))

    def _downloadVidySub(self, r):
        self["status"].setText(u"⬇ {} …".format(r.get("language", "")))
        self._pl_showOSD(True)
        title, item_url = self._raw_title or self.title, self._item_url

        def _bg():
            try:
                from novaplay_subtitles import vidy_download
                ok, err, path = vidy_download(r.get("id"), title)
            except Exception as e:
                ok, err, path = False, str(e)[:60], ""
            callInMainThread(self._onVidyDownloaded, ok, err, path, r)
        threading.Thread(target=_bg, daemon=True).start()

    def _onVidyDownloaded(self, ok, err, path, r):
        if getattr(self, "_closed_flag", False):
            return
        if ok and path:
            remember_subtitle(self._item_url, path, 0)
            update_subtitle_state(path=path, _orig_path=path, offset_ms=0)
            disable_subtitle(self.session)
            apply_subtitle(self.session, path)
            self["status"].setText(u"CC ✓ {}".format(r.get("language", "")))
        else:
            self["status"].setText(u"✗ {}".format(err or u"download failed"))
        self._pl_showOSD(True)

    def _onSubtitles(self):
        studio_on = str(_get_config("substudio_mode", "false")).lower() == "true"
        autosub_on = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
        try:
            _skip_s = max(10, min(600, int(_get_config("skip_intro_secs", "90") or 90)))
        except Exception:
            _skip_s = 90
        items = [
            ("Skip intro +%ds" % _skip_s, "skipintro"),
            ("Auto subtitles (%s)" % ("ON" if autosub_on else "OFF"), "autosub_toggle"),
            ("Audio tracks", "audio"),
            ("Quality", "quality"),
            ("Subtitle Studio (%s)" % ("ON" if studio_on else "OFF"), "studio_toggle"),
        ]
        if studio_on:
            items.append(("Studio settings", "studio_open"))
        items.append(("Browse local subtitles", "browse"))
        items.append(("Search online (Movy — Arabic/English, no key)", "vidy"))
        items.append(("Search online (SubSource)", "subsource"))
        items.append(("Search online (OpenSubtitles)", "opensubtitles"))
        state = get_subtitle_state()
        if state.get("path"):
            items.append(("Sync +%d ms" % SYNC_STEP_MS, "sync+"))
            items.append(("Sync -%d ms" % SYNC_STEP_MS, "sync-"))
            items.append(("Reset sync", "sync0"))
            items.append(("Disable subtitles", "off"))
        self.session.openWithCallback(self._onSubtitleAction, ChoiceBox, "Subtitles", items)

    def _onSubtitleAction(self, result):
        if not result:
            return
        choice = result[1]
        if choice == "autosub_toggle":
            _was = str(_get_config("autosub", "true")).lower() in ("true", "1", "yes", "on")
            _set_config("autosub", "false" if _was else "true")
            return
        if choice == "audio":
            self._pl_audioSelect()
            return
        if choice == "skipintro":
            self._pl_skipIntro()
            return
        if choice == "quality":
            self._pl_panelMenu()
            return
        if choice == "studio_toggle":
            from novaplay_substudio import STUDIO
            was_on = str(_get_config("substudio_mode", "false")).lower() == "true"
            _set_config("substudio_mode", "false" if was_on else "true")
            if not was_on:
                state = get_subtitle_state()
                path = state.get("_orig_path") or state.get("path") or ""
                if path and os.path.exists(path):
                    apply_subtitle(self.session, path)
                self._showStudioOverlay()
            else:
                STUDIO.detach()
                state = get_subtitle_state()
                path = state.get("_orig_path") or state.get("path") or ""
                if path and os.path.exists(path):
                    from novaplay_subtitles import _apply_path_with_offset
                    _apply_path_with_offset(self.session, self._item_url, path,
                                            int(state.get("offset_ms") or 0))
            return
        if choice == "studio_open":
            if not getattr(self, "_studioOverlayActive", False):
                self._showStudioOverlay()
            return
        if choice == "browse":
            self.session.openWithCallback(self._onSubtitlePicked,
                                          NovaSubtitleBrowser, "", self.title)
        elif choice in ("vidy", "subsource", "opensubtitles"):
            self.session.openWithCallback(self._onSubtitlePicked,
                                          NovaOnlineSubsScreen, self.title,
                                          self._item_url, choice,
                                          _cfg(choice + "_api_key", ""))
        elif choice == "sync+":
            my_log(adjust_sync(self.session, SYNC_STEP_MS))
        elif choice == "sync-":
            my_log(adjust_sync(self.session, -SYNC_STEP_MS))
        elif choice == "sync0":
            my_log(reset_sync(self.session))
        elif choice == "off":
            turn_off_subtitle(self.session, self._item_url)

    def _onSubtitlePicked(self, path):
        if path and os.path.exists(path):
            remember_subtitle(self._item_url, path, 0)
            update_subtitle_state(path=path, _orig_path=path, offset_ms=0)
            disable_subtitle(self.session)
            apply_subtitle(self.session, path)

    # ── Subtitle Studio overlay (v3) ─────────────────────────────────────
    def _studioItemsList(self):
        from novaplay_substudio import STUDIO
        s = STUDIO.style
        align_lbl = ["Top", "Bottom", "Center"][s["align"]]
        font_lbl = os.path.basename(s.get("font_path") or "") or s.get("font_name") or "Default"
        try:
            _delay_val = int(get_subtitle_state().get("offset_ms") or 0)
        except Exception:
            _delay_val = int(STUDIO.get_offset() or 0)
        return [
            ("Delay", "%+d ms" % _delay_val, "delay", 250),
            ("Size", "%d px" % s["size"], "size", 2),
            ("Font", font_lbl + "  ›", "font", 1),
            ("Color", ["White","Yellow","Gold","Cyan","Red","Green"][s["color_idx"] % 6], "color", 1),
            ("Outline", "On" if s["outline"] else "Off", "outline", 1),
            ("Outline color", ["White","Yellow","Gold","Cyan","Red","Green"][s["outline_color_idx"] % 6], "ocolor", 1),
            ("Background", "On" if s["bg"] else "Off", "bg", 1),
            ("Opacity", "%d%%" % int(round((255 - int(s["bg_alpha"])) * 100 / 255.0)), "bg_alpha", 16),
            ("Position", "%+d px" % int(s["offset_y"]), "offset_y", 10),
            ("Align", align_lbl, "align", 1),
            ("Spacing", str(s["spacing"]), "spacing", 2),
            ("Auto-wrap", "On" if s.get("auto_wrap", True) else "Off", "autowrap", 1),
            ("Cue position", "On" if s.get("use_cue_pos", True) else "Off", "cuepos", 1),
            ("Presets", "A / B / C  ›", "presets", 0),
            ("Pick line to sync", "OK  ›", "pickline", 0),
            ("Reset", "OK  ›", "reset", 0),
            ("Remove", "OK  ›", "remove", 0),
        ]

    def _showStudioOverlay(self):
        self._studioOverlayActive = True
        self._studioItems = self._studioItemsList()
        if self._studioIdx >= len(self._studioItems):
            self._studioIdx = 0
        # [FIX] keep the subtitles running under the overlay so every edit is
        # seen live on the real subtitle (the tick used to be stopped here)
        try:
            self._studioTimer.start(100, False)
        except Exception:
            pass
        for k in self._STUDIO_KEYS:
            try:
                self[k].show()
            except Exception:
                pass
        # [player-anim] drop the panel down from -320 → design
        self._studio_slide_p = 0.0
        self._studio_slide_target = 1.0
        self._applyStudioSlide()
        try:
            self._studio_slide_timer.start(20, False)
        except Exception:
            pass
        self._studioRefresh()

    def _hideStudioOverlay(self):
        self._studioOverlayActive = False
        try:
            self._studioTimer.start(100, False)
        except Exception:
            pass
        # [player-anim] slide the panel back off the top before hiding
        self._studio_slide_target = 0.0
        try:
            self._studio_slide_timer.start(20, False)
        except Exception:
            for k in self._STUDIO_KEYS:
                try: self[k].hide()
                except Exception: pass
        try:
            from novaplay_substudio import STUDIO
            STUDIO.flush_style()
        except Exception:
            pass
        for k in ("safeTop", "safeBottom", "safeLeft", "safeRight"):
            try:
                self[k].hide()
            except Exception:
                pass

    # ── [studio-ui] tabs ────────────────────────────────────────────────
    def _studioGroupIdx(self, idx=None):
        idx = self._studioIdx if idx is None else idx
        try:
            key = self._studioItems[idx][2]
        except Exception:
            return 0
        for g, (_t, keys) in enumerate(self._STUDIO_GROUPS):
            if key in keys:
                return g
        return 0

    def _studioMembers(self, g):
        keys = self._STUDIO_GROUPS[g][1]
        pos = dict((it[2], i) for i, it in enumerate(self._studioItems))
        return [pos[k] for k in keys if k in pos]

    def _studioGroup(self, g):
        g %= len(self._STUDIO_GROUPS)
        members = self._studioMembers(g)
        if members:
            self._studioIdx = members[0]
        self._studioRefresh()

    def _studioRefresh(self):
        total = len(self._studioItems)
        if not total:
            return
        idx = max(0, min(self._studioIdx, total - 1))
        self._studioIdx = idx
        g = self._studioGroupIdx(idx)
        members = self._studioMembers(g)
        # tabs
        for t, tab in enumerate(self._STUDIO_TABS):
            try:
                self[tab].setText(self._STUDIO_GROUPS[t][0])
                inst = self[tab].instance
                if inst and parseColor is not None:
                    inst.setBackgroundColor(parseColor("#F2C230" if t == g else "#171718"))
                    inst.setForegroundColor(parseColor("#0C1017" if t == g else "#8B949E"))
                    inst.invalidate()
            except Exception:
                pass
        # cards: this tab's settings, centred, the chosen one in gold
        n = len(members)
        start = (len(self._STUDIO_CARDS) - n) // 2
        from novaplay_substudio import STUDIO, _COLORS
        for slot, card in enumerate(self._STUDIO_CARDS):
            j = slot - start
            if not (0 <= j < n):
                try:
                    self[card].hide()
                except Exception:
                    pass
                continue
            item_i = members[j]
            label, value, key, _s = self._studioItems[item_i]
            sel = item_i == idx
            txt = u"%s\n%s" % (label, value) if value else label
            fg = "#F0F6FC" if sel else "#D0D0D0"
            if key in ("color", "ocolor"):
                ci = STUDIO.style["color_idx" if key == "color" else "outline_color_idx"] % len(_COLORS)
                fg = "#" + _COLORS[ci][0][-6:]
            try:
                self[card].setText(txt)
                inst = self[card].instance
                if inst:
                    pos = inst.position()
                    inst.move(ePoint(self._STUDIO_SLOTS_X[slot], int(pos.y())))
                    if parseColor is not None:
                        inst.setBackgroundColor(parseColor("#5C4A12" if sel else "#171718"))
                        inst.setForegroundColor(parseColor(fg))
                        inst.invalidate()
                self[card].show()
            except Exception:
                pass
        try:
            pos_in = members.index(idx) + 1 if idx in members else 1
            self["studioStatus"].setText(u"%d / %d" % (pos_in, max(1, n)))
        except Exception:
            pass
        cur_key = self._studioItems[idx][2]
        try:
            self["studioPreview"].setText(self._STUDIO_HINTS.get(cur_key, u""))
        except Exception:
            pass
        try:
            self["studioPreviewBg"].hide()
        except Exception:
            pass
        show_safe = cur_key in ("offset_y", "align")
        for k in ("safeTop", "safeBottom", "safeLeft", "safeRight"):
            try:
                if show_safe:
                    self[k].show()
                else:
                    self[k].hide()
            except Exception:
                pass

    def _studioChKey(self, d):
        if getattr(self, "_studioOverlayActive", False):
            self._studioGroup(self._studioGroupIdx() + d)

    def _studioMove(self, step):
        """◀ ▶ inside the current tab."""
        if not self._studioItems:
            return
        g = self._studioGroupIdx()
        members = self._studioMembers(g)
        if self._studioIdx not in members:
            self._studioGroup(g)
            return
        # stays inside the tab (wraps around); 1-4 change the tab
        j = (members.index(self._studioIdx) + step) % len(members)
        self._studioIdx = members[j]
        self._studioRefresh()

    def _studioDelayTotal(self):
        try:
            return int(get_subtitle_state().get("offset_ms") or 0)
        except Exception:
            try:
                from novaplay_substudio import STUDIO
                return int(STUDIO.get_offset() or 0)
            except Exception:
                return 0

    def _studioSetDelay(self, new_off):
        from novaplay_substudio import STUDIO
        new_off = int(new_off)
        if not STUDIO.is_attached():
            adjust_sync(self.session, new_off - self._studioDelayTotal())
            return
        st = get_subtitle_state()
        base_path = st.get("_orig_path") or st.get("path") or ""
        if base_path and os.path.exists(base_path) and base_path != STUDIO.get_path():
            STUDIO.attach(base_path)
        STUDIO.set_offset(new_off)
        STUDIO._last_rendered = None
        if base_path:
            update_subtitle_state(path=base_path, _orig_path=base_path,
                                  offset_ms=new_off)
            if st.get("item_url"):
                try:
                    remember_subtitle(st["item_url"], base_path, new_off)
                except Exception:
                    pass
        my_log("studio delay: {:+d} ms".format(new_off))

    def _studioAdjust(self, step):
        if not (0 <= self._studioIdx < len(self._studioItems)):
            return
        from novaplay_substudio import STUDIO
        label, value, key, stp = self._studioItems[self._studioIdx]
        s = STUDIO.style
        if key == "delay":
            self._studioSetDelay(self._studioDelayTotal() + int(stp * step))
        elif key == "size":
            s["size"] = max(22, min(80, s["size"] + stp * step))
            STUDIO._layout_static()
        elif key == "color":
            s["color_idx"] = (s["color_idx"] + step) % 6
            STUDIO._layout_static()
        elif key == "bg":
            s["bg"] = not s["bg"]
        elif key == "bg_alpha":
            s["bg_alpha"] = max(0, min(224, s["bg_alpha"] + stp * step))
            STUDIO._layout_static()
        elif key == "offset_y":
            s["offset_y"] = max(-250, min(120, s["offset_y"] + stp * step))
        elif key == "spacing":
            s["spacing"] = max(0, min(120, s["spacing"] + stp * step))
        elif key == "outline":
            s["outline"] = not s["outline"]
        elif key == "ocolor":
            s["outline_color_idx"] = (s["outline_color_idx"] + step) % 6
            STUDIO._layout_static()
        elif key == "align":
            s["align"] = (s["align"] + step) % 3
        elif key == "autowrap":
            s["auto_wrap"] = not s.get("auto_wrap", True)
        elif key == "cuepos":
            s["use_cue_pos"] = not s.get("use_cue_pos", True)
            STUDIO._last_rendered = None
        elif key == "presets":
            self._studioPresetMenu()
            return
        elif key == "font":
            self._studioFontBrowser()
            return
        elif key == "reset":
            STUDIO.reset_style()
        elif key == "remove":
            turn_off_subtitle(self.session, self._item_url)
        elif key == "pickline":
            self._studioPickLine()
            return
        try:
            STUDIO.refresh()
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._studioRefresh()

    def _studioPickLine(self):
        from novaplay_substudio import STUDIO
        if not STUDIO.is_attached():
            return
        self._hideStudioOverlay()
        entries = []
        for i, (start, end, body, _, _) in enumerate(STUDIO._cues):
            lines_preview = " / ".join(body[:2])
            entries.append((lines_preview[:60], i))
        self.session.openWithCallback(
            self._onStudioPickLine, ChoiceBox,
            "Pick the line being spoken now", entries)

    def _onStudioPickLine(self, choice):
        from novaplay_substudio import STUDIO
        from novaplay_subtitles import get_subtitle_state, remember_subtitle
        if not choice:
            self._showStudioOverlay()
            return
        idx = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        cue = STUDIO._cues[idx]
        self._studioSetDelay(int(self._studioNowMs() - cue[0]))
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()

    def _studioOk(self):
        if not (0 <= self._studioIdx < len(self._studioItems)):
            return
        label, value, key, stp = self._studioItems[self._studioIdx]
        if key == "presets":
            self._studioPresetMenu()
            return
        if key == "font":
            self._studioFontBrowser()
            return
        if key == "pickline":
            self._studioPickLine()
            return
        if key in ("delay", "size", "bg_alpha", "offset_y", "spacing", "color", "bg",
                   "outline", "ocolor", "align"):
            choices = []
            if key == "delay":
                choices = [("%+d ms" % v, v) for v in range(-5000, 5001, 250)]
            elif key == "size":
                choices = [(str(v), v) for v in range(22, 81, 2)]
            elif key == "color":
                choices = [("White", 0), ("Yellow", 1), ("Gold", 2), ("Cyan", 3), ("Red", 4), ("Green", 5)]
            elif key == "bg":
                choices = [("On", 1), ("Off", 0)]
            elif key == "bg_alpha":
                choices = [(str(v), v) for v in range(0, 225, 16)]
            elif key == "offset_y":
                choices = [(str(v), v) for v in range(-250, 121, 10)]
            elif key == "spacing":
                choices = [(str(v), v) for v in range(0, 121, 2)]
            elif key == "outline":
                choices = [("On", 1), ("Off", 0)]
            elif key == "ocolor":
                choices = [("White", 0), ("Yellow", 1), ("Gold", 2), ("Cyan", 3), ("Red", 4), ("Green", 5)]
            elif key == "align":
                choices = [("Top", 0), ("Bottom", 1), ("Center", 2)]
            self._studioChoiceKey = key
            self._hideStudioOverlay()
            self.session.openWithCallback(self._onStudioChoice, ChoiceBox,
                                          title=label, list=choices)
            return
        self._studioAdjust(1)

    def _onStudioChoice(self, choice):
        if not choice:
            self._showStudioOverlay()
            return
        value = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        key = getattr(self, "_studioChoiceKey", "")
        from novaplay_substudio import STUDIO
        s = STUDIO.style
        try:
            if key == "delay":
                self._studioSetDelay(int(value))
            elif key == "size":
                s["size"] = int(value)
                STUDIO._layout_static()
            elif key == "color":
                s["color_idx"] = int(value) % 6
                STUDIO._layout_static()
            elif key == "bg":
                s["bg"] = bool(value)
            elif key == "bg_alpha":
                s["bg_alpha"] = int(value)
                STUDIO._layout_static()
            elif key == "offset_y":
                s["offset_y"] = int(value)
            elif key == "spacing":
                s["spacing"] = int(value)
            elif key == "outline":
                s["outline"] = bool(value)
            elif key == "ocolor":
                s["outline_color_idx"] = int(value) % 6
                STUDIO._layout_static()
            elif key == "align":
                s["align"] = int(value) % 3
        except Exception:
            pass
        try:
            STUDIO.refresh()
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()

    def _studioPresetMenu(self):
        items = []
        for slot in ("A", "B", "C"):
            items.append(("Load %s" % slot, "load_" + slot))
            items.append(("Save %s" % slot, "save_" + slot))
        self._hideStudioOverlay()
        self.session.openWithCallback(self._onStudioPreset, ChoiceBox,
                                      "Style Presets", items)

    def _onStudioPreset(self, choice):
        from novaplay_substudio import STUDIO
        if not choice:
            self._showStudioOverlay()
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        slot = str(action)[-1] if action else "A"
        if str(action).startswith("save_"):
            STUDIO.save_preset(slot)
            STUDIO.flush_style()
        elif str(action).startswith("load_"):
            STUDIO.load_preset(slot)
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()

    def _studioFontBrowser(self):
        try:
            from Components.FileList import FileList
        except Exception:
            FileList = None
        if FileList is None:
            self["status"].setText("Font browser unavailable")
            self._pl_showOSD(True)
            return
        self._hideStudioOverlay()

        class _FontPick(Screen):
            skin = """
            <screen name="NovaFontPick" position="center,center" size="1500,860" backgroundColor="transparent" title="Select Font" flags="wfNoBorder">
                <eLabel position="0,0" size="1500,860" backgroundColor="#121822" cornerRadius="18" zPosition="0" />
        <eLabel position="40,78" size="140,4" backgroundColor="#F2C230" cornerRadius="2" zPosition="2" />
                <widget name="path" position="40,18" size="1420,44" font="Bold;30" foregroundColor="#F2C230" transparent="1" zPosition="2" />
                <widget name="list" position="30,100" size="1440,680" scrollbarMode="showOnDemand" foregroundColor="#D6DCE5" foregroundColorSelected="#F2C230" backgroundColor="#121822" backgroundColorSelected="#2B3648" zPosition="2" />
                <widget name="hint" position="40,800" size="1420,36" font="Regular;24" foregroundColor="#A3ADBB" transparent="1" zPosition="2" />
            </screen>"""
            def __init__(self2, session, start_dir):
                Screen.__init__(self2, session)
                self2["path"] = Label(start_dir)
                self2["list"] = FileList(start_dir, showDirectories=True,
                                         showFiles=True,
                                         matchingPattern=r"^.*\.(ttf|otf|ttc)$")
                self2["hint"] = Label("OK select  |  EXIT back  |  YELLOW default font")
                self2["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
                    "ok": self2._ok,
                    "cancel": lambda: self2.close("__keep__"),
                    "yellow": lambda: self2.close(""),
                    "up": lambda: self2["list"].up(),
                    "down": lambda: self2["list"].down(),
                    "left": lambda: self2["list"].pageUp(),
                    "right": lambda: self2["list"].pageDown(),
                }, -1)
            def _ok(self2):
                try:
                    if self2["list"].canDescent():
                        self2["list"].descent()
                        try:
                            self2["path"].setText(str(self2["list"].getCurrentDirectory() or ""))
                        except Exception:
                            pass
                        return
                except Exception:
                    pass
                try:
                    sel = self2["list"].getFilename()
                    if sel:
                        self2.close(sel)
                except Exception:
                    pass

        start = "/media/hdd" if os.path.exists("/media/hdd") else "/"
        self.session.openWithCallback(self._onStudioFontPicked, _FontPick, start)

    def _onStudioFontPicked(self, path=None):
        from novaplay_substudio import STUDIO
        try:
            if path == "__keep__" or path is None:
                pass
            elif path:
                STUDIO.register_font(path)
            else:
                STUDIO.use_default_font()
        except Exception:
            pass
        self._studioItems = self._studioItemsList()
        self._showStudioOverlay()


# ─── [PATCH 32a] per-site candidate memory key ──────────────────────────────
