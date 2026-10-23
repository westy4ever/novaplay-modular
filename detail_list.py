# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- loading the page, the server / episode list, OK / EXIT, clock,
favorites and TMDb refresh, closing.

Part of NovaPlayDetail (plugin_screen_detail.py), split out 2026-10-23
without changing behaviour. Methods use the screen's widgets and state
through self; module-level helpers come from detail_base.
"""
from detail_base import *  # noqa: F401,F403


class DetailList(object):
    # ── [detail-stagger] progressive menu population ──────────────────
    def _applyMenuRowHeight(self):
        """[row-compact] Only torrentio/yts ever put a real seeder count
        in a server row (see StreamList's ROW_H_COMPACT comment) -- every
        other site's rows are one line of text, so they get the shorter
        row. Called once per menu (re)population; StreamList.setCompact()
        is a no-op if the mode isn't actually changing."""
        try:
            self["menu"].setCompact(self._site not in _TORRENT_SEEDER_SITES)
        except Exception:
            pass

    def _setMenu(self, items):
        """Central setList — updates the accent bar's item count."""
        self._applyMenuRowHeight()
        try:
            self._menu_item_count = len(items)
            self._sv_items = list(items)
            self["menu"].setList(items)
        except Exception as e:
            my_log("setMenu error: {}".format(e))
            return
        self._updateMenuAccent()

    def _setMenuStaggered(self, items):
        """Reveal menu rows a few at a time so a slow response feels live.
        Lists longer than 16 items advance in groups of 3 to cap the
        animation at ~400 ms regardless of size."""
        self._applyMenuRowHeight()
        items = list(items)
        self._stagger_items = items
        self._sv_items = items
        n = len(items)
        self._stagger_step = 1 if n <= 8 else (2 if n <= 16 else 3)
        self._menu_item_count = n
        first = min(self._stagger_step, n)
        self._stagger_idx = first
        try: self._stagger_timer.stop()
        except Exception: pass
        try:
            self["menu"].setList(items[:first])
        except Exception as e:
            my_log("stagger initial setList error: {}".format(e))
            return
        self._updateMenuAccent()
        if first < n:
            try:
                self._stagger_timer.start(40, False)
            except Exception as e:
                my_log("stagger timer start failed: {}".format(e))

    def _staggerTick(self):
        if getattr(self, "_closed", False):
            try: self._stagger_timer.stop()
            except Exception: pass
            return
        n = len(self._stagger_items)
        if self._stagger_idx >= n:
            try: self._stagger_timer.stop()
            except Exception: pass
            return
        self._stagger_idx = min(self._stagger_idx + self._stagger_step, n)
        try:
            self["menu"].setList(self._stagger_items[:self._stagger_idx])
        except Exception as e:
            my_log("stagger setList error: {}".format(e))
            try: self._stagger_timer.stop()
            except Exception: pass
            return
        self._updateMenuAccent()
        if self._stagger_idx >= n:
            try: self._stagger_timer.stop()
            except Exception: pass

    # ── [detail-accent] gold bar next to the selected server row ──────
    # [PATCH 117] removed: three rounds of trying to mirror this list's
    # scroll behavior (page-snap windowing, confirmed non-trivial) never
    # reliably matched the real widget across both directions, and the
    # native row highlight (backgroundColorSelected) already marks the
    # selected server correctly on its own. Left as a no-op (keeps the
    # widget hidden) rather than ripping out every call site.
    def _updateMenuAccent(self):
        try: self["menuAccent"].hide()
        except Exception: pass
        if getattr(self, "_modern", False):
            self._svPaint()

    # ── [detail-progress] indeterminate bar while extracting ──────────
    def _startProgress(self):
        try: self._progress_timer.stop()
        except Exception: pass
        self._progress_active = True
        self._progress_x = self._PROG_X0
        self._progress_dir = 1
        try:
            self._moveResize("menuProgress",
                             self._progress_x, self._PROG_Y,
                             self._PROG_W, self._PROG_H)
            self["menuProgress"].show()
            self._progress_timer.start(25, False)
        except Exception as e:
            my_log("progress start failed: {}".format(e))

    def _stopProgress(self):
        self._progress_active = False
        try: self._progress_timer.stop()
        except Exception: pass
        try: self["menuProgress"].hide()
        except Exception: pass

    def _progressTick(self):
        if not self._progress_active or getattr(self, "_closed", False):
            try: self._progress_timer.stop()
            except Exception: pass
            return
        limit_r = self._PROG_X0 + self._PROG_SPAN
        limit_l = self._PROG_X0
        self._progress_x += self._progress_dir * self._PROG_STEP
        if self._progress_x >= limit_r:
            self._progress_x = limit_r
            self._progress_dir = -1
        elif self._progress_x <= limit_l:
            self._progress_x = limit_l
            self._progress_dir = 1
        self._moveResize("menuProgress",
                         self._progress_x, self._PROG_Y,
                         self._PROG_W, self._PROG_H)

    def _format_server_item(self, s):
        name = s.get("name", "Server")
        quality = (s.get("quality") or "HD").upper()
        lines = name.split('\n')
        release_name = lines[0].strip()

        # [FIX] yts.py (both _torr_servers/Torrentio and the native
        # en.yts.lu search) already computes real seeders/size and puts
        # them on the server dict directly -- "seeders": "154", "size":
        # "2.67 GB". The emoji-second-line parse below predates that and
        # only applies to sites whose "name" embeds a 👤/💾-marked meta
        # line with no separate dict fields; for a single-line name (every
        # YTS entry) it always came up empty, silently dropping real data
        # the dict already had. Dict fields win when present; the legacy
        # parse is now only the fallback for sites that still need it.
        dict_seeders = str(s.get("seeders") or "").strip()
        dict_size = str(s.get("size") or "").strip()
        if dict_seeders or dict_size:
            seeders = "Seeders: " + dict_seeders if dict_seeders else ""
            if dict_seeders.isdigit() and int(dict_seeders) < 5 and \
                    str(s.get("url", "")).startswith("magnet:"):
                seeders += " (weak)"      # [2026-10-23] likely to buffer
            size = "Size: " + dict_size if dict_size else ""
            seeders_source = " | ".join([x for x in [seeders] if x])
            return (quality, release_name, size, seeders_source)

        meta_line = lines[1] if len(lines) > 1 else ""
        parts = re.split(r'[\U00010000-\U0010ffff]', meta_line)
        parts = [p.strip() for p in parts if p.strip()]
        seeders = ""
        size = ""
        source = ""
        if len(parts) > 0:
            if parts[0].isdigit():
                seeders = "Seeders: " + parts[0]
                if len(parts) > 1:
                    size = "Size: " + parts[1]
                if len(parts) > 2:
                    source = parts[2]
            else:
                source = " | ".join(parts)
        seeders_source = " | ".join([x for x in [seeders, source] if x])
        if not size and not seeders_source:
            fallback = re.sub(r'[\U00010000-\U0010ffff]', '', meta_line).strip()
            fallback = re.sub(r'\s+', ' ', fallback)
            seeders_source = fallback
        return (quality, release_name, size, seeders_source)

    def _format_episode_item(self, ep):
        marker, name = _split_episode_label(ep.get("title", "Episode"))   # [PATCH 81]
        return (marker, name, "", "")

    def _onOk(self):
        idx = self["menu"].getCurrentIndex()
        if idx < 0: return

        if self._quality_choices:
            if idx >= len(self._quality_choices): return
            choice = self._quality_choices[idx]
            self._last_quality_selection = choice
            self._onStreamFound(choice["url"], choice["label"], choice["final_ref"], choice["server"])
            return

        data = self._data or {}
        item_type = data.get("type") or self._item.get("type")
        episode_has_servers = (item_type == "episode" and self._servers)

        if episode_has_servers:
            if idx >= len(self._servers): return
            with self._extract_lock:
                if self._extracting: return
                self._extracting = True
                self._extract_token += 1
                token = self._extract_token
            server = self._servers[idx]
            if server.get("url", "").startswith("magnet:"):
                with self._extract_lock:
                    self._extracting = False
                self._promptMagnetAction(server)
                return
            self["status"].setText("Extracting stream...")
            self["status"].show()
            self._startProgress()             # [detail-progress]
            threading.Thread(target=self._bgExtract, args=(server, token), daemon=True).start()
        elif self._episodes:
            if idx >= len(self._episodes): return
            ep = self._episodes[idx]
            self.session.open(type(self), ep, self._site, ep.get("type", "episode"),
                              episode_chain=self._episodes, episode_index=idx)
        elif self._servers:
            if idx >= len(self._servers): return
            with self._extract_lock:
                if self._extracting: return
                self._extracting = True
                self._extract_token += 1
                token = self._extract_token
            server = self._servers[idx]
            if server.get("url", "").startswith("magnet:"):
                with self._extract_lock:
                    self._extracting = False
                self._promptMagnetAction(server)
                return
            self["status"].setText("Extracting stream...")
            self["status"].show()
            self._startProgress()             # [detail-progress]
            threading.Thread(target=self._bgExtract, args=(server, token), daemon=True).start()

    def _tickClock(self):
        try:
            t = time.localtime()
            self["clock_time"].setText("{}:{:02d}".format(t.tm_hour % 12 or 12, t.tm_min))
            self["clock_date"].setText(time.strftime("%A %d %B", t))
        except Exception:
            pass

    def _syncKeyDots(self):
        for key, dot in (("key_green", "dotGreen"), ("key_blue", "dotBlue")):
            try:
                self[dot].show() if (self[key].getText() or "").strip() else self[dot].hide()
            except Exception:
                pass

    def _load(self):
        self._tickClock()
        try:
            self._clockTimer.start(10000, False)
        except Exception:
            pass
        try:
            from plugin_gridlist import resolve_icon_path
            _lp = resolve_icon_path({"_action": "site_" + str(self._site)})
            if _lp and self["siteLogo"].instance:
                self["siteLogo"].instance.setPixmapFromFile(_lp)
        except Exception as e:
            my_log("detail logo: {}".format(e))
        self._syncKeyDots()
        item_snapshot = self._item
        # [detail-reveal] kick off the entry animation on the main thread
        # before we hand off to the loading worker
        try:
            self._startReveal()
        except Exception as e:
            my_log("startReveal failed: {}".format(e))
        threading.Thread(target=self._bgLoad, args=(self._site, item_snapshot, self._m_type), daemon=True).start()

    def _bgLoad(self, site, item, m_type):
        url = item["url"]
        _done = [False]
        # [PATCH WATCHDOG1] Confirmed directly on a real device log,
        # 2026-10-14: a 30s watchdog here could fire a FALSE positive --
        # the page's own TMDb metadata lookup was simply slow (a single
        # TMDb call took 16+ seconds under load from the plugin's own
        # background grid-fill lookups, see plugin_tmdb.py's PATCH
        # TMDB-SLOW1 for the real fix to that), not actually stuck, and
        # get_page() went on to finish successfully a few seconds later
        # anyway. The old wording ("Timeout — please try again") read as
        # a definite failure and invited the user to back out of a
        # screen that was still loading; if they did, the poster/backdrop
        # decode threads from the load that then finished anyway crashed
        # with AttributeError trying to paint into the now-closed screen
        # (self.picLoad / self.backdropPicLoad gone) -- harmless (caught
        # and logged) but a real symptom of this false alarm.
        #
        # Raised the threshold (TMDb should rarely need it now that
        # TMDB-SLOW1 caps concurrent in-flight TMDb requests) and
        # softened the wording so it reads as "still working," not
        # "failed" -- it no longer tells the user to retry, since
        # retrying while the original load is still running is exactly
        # what produced the race above.
        def _watchdog():
            if not _done[0] and not getattr(self, "_closed", False):
                my_log("_bgLoad watchdog: still loading after 45s for {}".format(url[:60]))
                def _show_timeout_msg():
                    if not getattr(self, "_closed", False) and not _done[0]:
                        self.session.open(MessageBox, "Still loading, please wait...", MessageBox.TYPE_INFO, timeout=5)
                callInMainThread(_show_timeout_msg)
        _wt = threading.Timer(45, _watchdog)
        _wt.daemon = True
        _wt.start()
        try:
            extractor = _get_extractor(site)
            get_page = getattr(extractor, "get_page", None)
            if not get_page:
                if not getattr(self, "_closed", False):
                    callInMainThread(self["status"].setText, u"No data")
                return
            if site in ["egydead", "egydead_coupons", "akwam", "akwams", "wecima"]:
                data = get_page(url, m_type=m_type)
            else:
                data = get_page(url)
            merged_seed = dict(item or {})
            merged_seed.update(data or {})
            data = _merge_tmdb_data(merged_seed)
            _done[0] = True
            plugin_health.record(site, "ok")
            if getattr(self, "_closed", False): return
            callInMainThread(self._onLoaded, data)
        except Exception as e:
            _done[0] = True
            my_log("_bgLoad error: {} -- trying TMDb fallback".format(e))
            plugin_health.record(site, "blocked" if get_curl_failed_needs_proxy() else "down", str(e))
            if getattr(self, "_closed", False): return
            try:
                fallback = _merge_tmdb_data(dict(item or {}))
                if getattr(self, "_closed", False): return
                if fallback and (fallback.get("plot") or fallback.get("poster")):
                    callInMainThread(self._onLoaded, fallback)
                else:
                    callInMainThread(self["status"].setText, u"Loading failed — {}".format(str(e)[:40]))
            except Exception as e2:
                my_log("TMDb fallback failed: {}".format(e2))
                if not getattr(self, "_closed", False):
                    callInMainThread(self["status"].setText, u"Loading failed — {}".format(str(e)[:40]))
        finally:
            _wt.cancel()

    def _onCancel(self):
        if self._quality_choices:
            self._quality_choices = []
            self._setServerSection()
            items = [self._format_server_item(s) for s in self._servers]
            self._setMenu(items)
            self["status"].setText(self._status_hint("Choose a server — OK"))
            return
        self._closed = True
        self._proxy_warning_shown = False
        self._stopProgress()
        try:
            self.picLoad.PictureData.get().remove(self._paintPoster)
        except Exception: pass
        try:
            self.backdropPicLoad.PictureData.get().remove(self._paintBackdrop)
        except Exception: pass
        for p in self._tmp_posters:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception: pass
        self.close()

    def _maybeCloseAfterResume(self):
        """[continue] the player was opened straight from Continue Watching:
        when it closes, go back to Home instead of this screen."""
        if getattr(self, "_close_on_return", False):
            self._close_on_return = False
            self._closeLater = eTimer()
            self._closeLater.callback.append(self.close)
            self._closeLater.start(10, True)

    def _onCloseCleanup(self):
        for t in ("_reveal_timer", "_stagger_timer", "_progress_timer", "_clockTimer", "_svPopT", "_tqT"):
            try:
                timer = getattr(self, t, None)
                if timer:
                    timer.stop()
            except Exception:
                pass
        try: self.picLoad.PictureData.get().remove(self._paintPoster)
        except Exception: pass
        try: self.backdropPicLoad.PictureData.get().remove(self._paintBackdrop)
        except Exception: pass

    def _toggleFavorite(self):
        base = self._data or self._item
        entry = _entry_from_item(
            dict(self._item, **(base or {})),
            self._site,
            self._m_type,
            {"type": (base or {}).get("type", self._item.get("type", self._m_type))}
        )
        added = _toggle_favorite_entry(entry)
        self["status"].setText("Added to favorites" if added else "Removed from favorites")
        toast(self, u"\u2605  Added to favorites" if added else u"Removed from favorites")
        if self._data:
            self._onLoaded(self._data)

    def _refreshTMDb(self):
        if not _tmdb_enabled():
            self["status"].setText("Add a TMDb API key in Settings first")
            return
        self["status"].setText("Updating from TMDb...")
        threading.Thread(target=self._bgRefreshTMDb, daemon=True).start()

    def _bgRefreshTMDb(self):
        try:
            merged = _merge_tmdb_data(self._data or self._item)
            callInMainThread(self._onLoaded, merged)
        except Exception as e:
            my_log("TMDb refresh failed: {}".format(e))
            callInMainThread(self["status"].setText, "TMDb update failed")
