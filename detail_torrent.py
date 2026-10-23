# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- torrents: magnet actions, quality tabs and order, TorrServer add,
file pick and buffering.

Part of NovaPlayDetail (plugin_screen_detail.py), split out 2026-10-23
without changing behaviour. Methods use the screen's widgets and state
through self; module-level helpers come from detail_base.
"""
from detail_base import *  # noqa: F401,F403


class DetailTorrent(object):
    def _promptMagnetAction(self, server):
        magnet_link = server.get("url", "")
        title = server.get("name", "Magnet Stream")
        choices = [
            ("Play (TorrServer Stream)", "play"),
            ("Download to HDD (Transmission)", "download")
        ]
        self.session.openWithCallback(
            lambda choice: self._onMagnetActionChosen(choice, server, magnet_link, title),
            ChoiceBox,
            title="Choose action for Magnet link",
            list=choices
        )

    def _onMagnetActionChosen(self, choice, server, magnet_link, title):
        if not choice: return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "play":
            with self._extract_lock:
                if self._extracting: return
                self._extracting = True
                self._extract_token += 1
                token = self._extract_token
            self["status"].setText("TorrServer: Starting Stream...")
            self["status"].show()
            self._startProgress()             # [detail-progress]
            threading.Thread(target=self._bgTorrServerMagnet, args=(magnet_link, server, token), daemon=True).start()
        elif action == "download":
            self._initiateTransmissionDownload(magnet_link, title)

    def _initiateTransmissionDownload(self, magnet_link, title):
        """Transmission on demand (novaplay_transmission): start the daemon
        with safe settings if needed and add the magnet over RPC. Global
        speed limits are left as set (the old transmission-remote call
        passed -d 1000000, which lifted the download cap for everything)."""
        try:
            from Plugins.Extensions.Transmission.torrmgr import config as tconfig
            download_path = '%s/transmission' % tconfig.plugins.torreplayer.poster_path.value
        except Exception:
            download_path = '/media/hdd/transmission'
        self["status"].setText("Starting Transmission...")
        self["status"].show()

        def _worker():
            try:
                import novaplay_transmission
                ok, msg = novaplay_transmission.add(magnet_link, download_path)
            except Exception as e:
                ok, msg = False, str(e)
            my_log("Transmission add '{}': {} {}".format(title[:40], ok, msg))

            def _done():
                if getattr(self, "_closed", False):
                    return
                if ok:
                    self["status"].setText(msg[:80])
                    self.session.open(MessageBox, "{}\n\nSaved to: {}\nTorrents pause while you watch and Transmission "
                                      "stops by itself when nothing is left to download.".format(msg[:120], download_path),
                                      MessageBox.TYPE_INFO, timeout=8)
                else:
                    self["status"].setText("Transmission failed")
                    self.session.open(MessageBox, "Could not send to Transmission.\n{}".format(msg[:160]),
                                      MessageBox.TYPE_ERROR, timeout=8)
            callInMainThread(_done)
        threading.Thread(target=_worker, daemon=True).start()

    # ── [2026-10-21] torrent lists: "🧲 Torrents N" + All / 4K / 1080p / 720p / SD
    _TOR_BUCKETS = ("All", "4K", "1080p", "720p", "SD")

    @staticmethod
    def _torBucket(sv):
        q = "{} {}".format(sv.get("quality") or "", sv.get("name") or "").upper()
        if "2160" in q or "4K" in q:
            return "4K"
        if "1080" in q:
            return "1080p"
        if "720" in q:
            return "720p"
        return "SD"

    @staticmethod
    def _torSeeds(sv):
        try:
            return int(re.sub(r"[^\d]", "", str(sv.get("seeders") or "")) or -1)
        except Exception:
            return -1

    _TOR_PREF = {"1080p": ("1080p", "720p", "4K", "SD"),
                 "720p": ("720p", "1080p", "SD", "4K"),
                 "4K": ("4K", "1080p", "720p", "SD")}

    def _orderTorrents(self, srv):
        """[2026-10-23] magnets: the quality chosen in Settings first, then by
        seeders inside each quality (more seeders = fills the buffer faster);
        CAM/TS copies last. Non-magnet servers keep their place after them."""
        mags = [x for x in srv if str(x.get("url", "")).startswith("magnet:")]
        if len(mags) < 2:
            return srv
        pref = str(_get_config("torrent_quality", "1080p") or "1080p")
        order = self._TOR_PREF.get(pref)

        def key(sv):
            q = "{} {}".format(sv.get("quality") or "", sv.get("name") or "").upper()
            cam = 1 if re.search(r"\b(HD-?CAM|CAM|TELESYNC|HDTS|TS)\b", q) else 0
            b = self._torBucket(sv)
            rank = order.index(b) if order and b in order else 0
            return (cam, rank, -self._torSeeds(sv))
        mags.sort(key=key)
        it = iter(mags)
        return [next(it) if str(x.get("url", "")).startswith("magnet:") else x for x in srv]

    def _initTorrentFilter(self):
        try:
            self._servers = self._orderTorrents(list(self._servers or []))
        except Exception as e:
            my_log("torrent order: {}".format(e))
        srv = list(self._servers or [])
        self._tor_all = None
        self._tor_filter = "All"
        # any list with 2+ torrents (YTS adds embed servers after its torrents)
        if sum(1 for x in srv if str(x.get("url", "")).startswith("magnet:")) >= 2:
            self._tor_all = srv
            # [tor-tabs] open on the quality chosen in Settings when it has torrents
            pref = str(_get_config("torrent_quality", "1080p") or "1080p")
            if pref in self._TOR_BUCKETS and self._torCounts().get(pref):
                self._tor_filter = pref
                self._servers = [x for x in srv if str(x.get("url", "")).startswith("magnet:")
                                 and self._torBucket(x) == pref]
        try:
            self["key_yellow"].setText(self._yellowLabel())
        except Exception:
            pass

    def _torCounts(self):
        c = {b: 0 for b in self._TOR_BUCKETS}
        mags = [x for x in (self._tor_all or []) if str(x.get("url", "")).startswith("magnet:")]
        for x in mags:
            c[self._torBucket(x)] += 1
        c["All"] = len(mags)
        c["_other"] = len(self._tor_all or []) - len(mags)
        return c

    def _yellowLabel(self):
        if getattr(self, "_tor_all", None):
            return u"Quality \u25b8"
        return "Update TMDb"

    # ── [tor-tabs] quality filter as pill tabs ───────────────────────
    _TQ_GOLD, _TQ_OFF, _TQ_TXT = "#F2C230", "#2A3342", "#D6DCE5"

    def _tqHide(self):
        try:
            self._tqT.stop()
        except Exception:
            pass
        self._tq_pop = -1
        for k in ["torLead", "torHint"] + ["tq%d" % i for i in range(5)]:
            try:
                self[k].hide()
            except Exception:
                pass

    def _setSection(self, text):
        """section line for anything but the torrent tabs"""
        self._tqHide()
        try:
            self["section"].setText(text)
            self["section"].show()
        except Exception:
            pass

    def _tqRow(self):
        # (x, y, h, right edge) of the tab row, same place as the section line
        if getattr(self, "_modern", False):
            return 70, 704, 40, 1220
        return 430, 156, 40, 1380

    def _paintTorTabs(self, pop=True):
        c = self._torCounts()
        tabs = [b for b in self._TOR_BUCKETS if b == "All" or c[b]]
        x, y, h, right = self._tqRow()
        self["section"].setText("")
        lead = u"🧲 Torrents"
        lw = 190 if getattr(self, "_modern", False) else 196
        self["torLead"].setText(lead)
        self._moveResize("torLead", x, y, lw, h)
        self["torLead"].show()
        cx = x + lw
        active = -1
        self._tq_geo = {}
        for i in range(5):
            k = "tq%d" % i
            if i >= len(tabs):
                self[k].hide()
                continue
            b = tabs[i]
            n = c["All"] + c["_other"] if b == "All" else c[b]
            txt = u"{} {}".format(b, n)
            w = 30 + 13 * len(txt)
            on = b == self._tor_filter
            if on:
                active = i
            self[k].setText(txt)
            self._svColor(k, bg=self._TQ_GOLD if on else self._TQ_OFF,
                          fg="#0C1017" if on else self._TQ_TXT)
            self._tq_geo[i] = (cx, y, w, h)
            self._moveResize(k, cx, y, w, h)
            self[k].show()
            cx += w + 12
        hint = u"\u25c0 CH- / CH+ \u25b6" if getattr(self, "_modern", False) else u"\u25c0 CH \u25b6"
        hw = 190 if getattr(self, "_modern", False) else 90
        if len(tabs) > 1 and cx + hw <= right:
            self["torHint"].setText(hint)
            self._moveResize("torHint", cx + 6, y, hw, h)
            self["torHint"].show()
        else:
            self["torHint"].hide()
        if pop and active >= 0 and anim_frames() > 1:
            self._tq_pop = active
            self._tq_f = 0
            self._tqPlace(active, 0.0)
            try:
                self._tqT.start(POP_ANIM_INTERVAL, False)
            except Exception:
                self._tqPlace(active, 1.0)

    def _tqPlace(self, i, p):
        g = self._tq_geo.get(i)
        if not g:
            return
        x, y, w, h = g
        # grows ~14% and settles back: a quick "pop" on the new tab
        import math
        s = 1.0 + 0.14 * math.sin(p * math.pi)
        nw, nh = w * s, h * s
        self._moveResize("tq%d" % i, x - (nw - w) / 2.0, y - (nh - h) / 2.0, nw, nh)

    def _tqTick(self):
        if self._tq_pop < 0 or getattr(self, "_closed", False):
            self._tqT.stop()
            return
        self._tq_f += 1
        t = min(1.0, self._tq_f / float(max(1, anim_frames())))
        self._tqPlace(self._tq_pop, t)
        if t >= 1.0:
            self._tqT.stop()
            self._tqPlace(self._tq_pop, 1.0)
            self._tq_pop = -1

    def _setServerSection(self):
        if getattr(self, "_tor_all", None):
            try:
                self._paintTorTabs()
            except Exception as e:
                my_log("torrent tabs: {}".format(e))
        else:
            self._setSection(_single_line_text("Available servers: {}  |  pick a quality or server".format(len(self._servers)), width=90))

    def _onYellow(self):
        if getattr(self, "_tor_all", None):
            self._stepTorrentFilter(1)
        else:
            self._refreshTMDb()

    def _stepTorrentFilter(self, d):
        if not getattr(self, "_tor_all", None) or self._quality_choices:
            return
        c = self._torCounts()
        order = [b for b in self._TOR_BUCKETS if b == "All" or c[b]]
        if len(order) < 2:
            return
        i = order.index(self._tor_filter) if self._tor_filter in order else 0
        self._tor_filter = order[(i + d) % len(order)]
        self._servers = [x for x in self._tor_all
                         if self._tor_filter == "All" or (
                             str(x.get("url", "")).startswith("magnet:")
                             and self._torBucket(x) == self._tor_filter)]
        self["key_yellow"].setText(self._yellowLabel())
        self._setServerSection()
        self._setMenu([self._format_server_item(x) for x in self._servers])
        try:
            self["menu"].instance.moveSelectionTo(0)
            self._updateMenuAccent()
        except Exception:
            pass
        self["status"].setText(self._status_hint(u"{} torrent(s) — OK to play".format(len(self._servers))))

    def _bgTorrServerMagnet(self, magnet_link, server, token=None):
        try:
            import json
            import urllib.request as _ur
            import urllib.error
            from urllib.parse import quote

            ts_url = (_get_config("torrserver_url", "http://127.0.0.1:8090") or "").strip().rstrip("/")
            if not ts_url:
                callInMainThread(self["status"].setText, "TorrServer URL is not set in settings!")
                return

            add_url = ts_url + "/torrents"
            payload = json.dumps({
                "action": "add",
                "link": magnet_link,
                "title": server.get("name", "Magnet Stream"),
                "save_to_db": True
            }).encode("utf-8")
            req = _ur.Request(add_url, data=payload, headers={"Content-Type": "application/json"})
            try:
                with _ur.urlopen(req, timeout=20) as resp:
                    add_resp = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                my_log("TorrServer: add refused, HTTP {} ({})".format(e.code, ts_url))
                callInMainThread(self["status"].setText, "TorrServer HTTP Error: {}".format(e.code))
                callInMainThread(self._stopProgress)
                return

            torrent_hash = add_resp.get("hash")
            if not torrent_hash:
                m = re.search(r'btih:([a-zA-Z0-9]+)', magnet_link)
                if m: torrent_hash = m.group(1)

            if not torrent_hash:
                my_log("TorrServer: add gave no hash: {}".format(str(add_resp)[:200]))
                callInMainThread(self["status"].setText, "TorrServer failed to add torrent.")
                callInMainThread(self._stopProgress)
                return
            my_log("TorrServer: added {} -- waiting for its file list".format(torrent_hash))
            # [2026-10-21] Torrentio's fileIdx (magnet "ix") names the episode's
            # file inside a season pack; the largest file is usually ANOTHER episode
            want_ix = None
            m_ix = re.search(r'[?&]ix=(\d+)', magnet_link)
            if m_ix:
                want_ix = int(m_ix.group(1))

            get_url = ts_url + "/torrents"
            get_payload = json.dumps({"action": "get", "hash": torrent_hash}).encode("utf-8")
            file_index = 0
            file_path = "stream"
            file_size = 0
            video_exts = ('.mpg', '.vob', '.m4v', '.mkv', '.avi', '.divx', '.dat', '.flv', '.mp4', '.mov', '.wmv', '.asf', '.3gp', '.3g2', '.mpeg', '.mpe', '.rm', '.rmvb', '.ogm', '.ogv', '.m2ts', '.mts', '.webm', '.ts')

            for attempt in range(20):
                if getattr(self, "_closed", False): return
                try:
                    req = _ur.Request(get_url, data=get_payload, headers={"Content-Type": "application/json"})
                    with _ur.urlopen(req, timeout=15) as resp:
                        info = json.loads(resp.read().decode("utf-8"))
                except Exception:
                    info = None

                tor_obj = None
                if isinstance(info, list) and len(info) > 0:
                    tor_obj = info[0]
                elif isinstance(info, dict) and info:
                    tor_obj = info

                if tor_obj:
                    files = tor_obj.get("file_stats") or []
                    if not files:
                        raw_data = tor_obj.get("data")
                        if raw_data:
                            try:
                                parsed_data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                                files = ((parsed_data or {}).get("TorrServer") or {}).get("Files", [])
                            except Exception as parse_err:
                                my_log("TorrServer: failed to parse nested data field: {}".format(parse_err))
                                files = []

                    f = None
                    if files and want_ix is not None:
                        # TorrServer numbers files from 1, in torrent order
                        f = next((x for x in files if x.get("id") == want_ix + 1), None)
                        if f is None and 0 <= want_ix < len(files):
                            f = files[want_ix]
                    if f is not None:
                        if (f.get("path") or "").lower().endswith(video_exts):
                            file_index = f.get("id", want_ix + 1)
                            file_path = f.get("path", "stream")
                            file_size = f.get("length") or f.get("size") or 0
                            my_log("TorrServer: file {} of {} (from the torrent's file index): {}".format(
                                want_ix + 1, len(files), file_path[-80:]))
                            break
                    if files:
                        largest_file = None
                        max_size = 0
                        for f in files:
                            path = (f.get("path") or "").lower()
                            size = f.get("length") or f.get("size") or 0
                            if path.endswith(video_exts):
                                if size > max_size:
                                    max_size = size
                                    largest_file = f

                        if largest_file:
                            file_index = largest_file.get("id", 0)
                            file_path = largest_file.get("path", "stream")
                            file_size = max_size
                            break

                callInMainThread(self["status"].setText, "TorrServer: Fetching metadata ({}s)...".format((attempt+1)*2))
                time.sleep(2)
            else:
                my_log("TorrServer: no file list after 40 s for {} (no peers yet?)".format(torrent_hash))
                callInMainThread(self["status"].setText, "TorrServer: Timeout fetching metadata.")
                callInMainThread(self._stopProgress)      # [detail-progress]
                return

            encoded_path = quote(file_path, safe='/')
            stream_url = "{}/stream/{}?link={}&index={}&play".format(
                ts_url, encoded_path, torrent_hash, file_index)
            my_log("TorrServer Stream URL: {}".format(stream_url))

            # [2026-10-23] fill TorrServer's cache before the player starts --
            # starting on an empty cache was the main cause of torrent spinning
            if not self._torrPreload(ts_url, torrent_hash, file_index, encoded_path, file_size, token):
                return

            callInMainThread(self._onStreamFound, stream_url, server.get("quality", "HD"), stream_url, server)

        except Exception as e:
            my_log("TorrServer Error: {}".format(e))
            callInMainThread(self["status"].setText, "TorrServer Error: {}".format(str(e)[:30]))
            callInMainThread(self._stopProgress)          # [detail-progress]
        finally:
            # [PATCH 80] every early return above used to leave _extracting stuck True
            if token is not None:
                with self._extract_lock:
                    if token == self._extract_token:
                        self._extracting = False

    _TORR_WAIT_S = 75            # longest wait for the buffer before giving up / playing

    def _torrPreload(self, ts_url, thash, idx, enc_path, file_size, token=None):
        """Ask TorrServer to preload the start of the file and wait until a
        buffer is ready, showing "Buffering 45% · 12 peers · 2.3 MB/s".
        Target: ~1/400 of the file (16-48 MB; a 2 GB 1080p ~16 MB, a 15 GB 4K
        ~37 MB), never more than TorrServer's own preload size. Returns False
        when the user left / picked another server, or nothing arrived."""
        import json
        import urllib.request as _ur
        MB = 1024 * 1024
        target = max(16 * MB, min(48 * MB, int(file_size or 0) // 400))
        done = []

        def _kick():
            u = "{}/stream/{}?link={}&index={}&preload".format(ts_url, enc_path, thash, idx)
            try:
                with _ur.urlopen(u, timeout=self._TORR_WAIT_S + 30) as r:
                    r.read(256)
            except Exception as e:
                my_log("TorrServer preload request: {}".format(e))
            done.append(1)
        threading.Thread(target=_kick, daemon=True).start()

        payload = json.dumps({"action": "get", "hash": thash}).encode("utf-8")
        t0 = time.time()
        got = 0
        peers = 0
        while True:
            if getattr(self, "_closed", False):
                return False
            if token is not None and token != getattr(self, "_extract_token", token):
                return False
            st = None
            try:
                req = _ur.Request(ts_url + "/torrents", data=payload,
                                  headers={"Content-Type": "application/json"})
                with _ur.urlopen(req, timeout=8) as resp:
                    st = json.loads(resp.read().decode("utf-8"))
                if isinstance(st, list):
                    st = st[0] if st else None
            except Exception:
                st = None
            speed = 0.0
            if isinstance(st, dict):
                ps = int(st.get("preload_size") or 0)
                if ps > 0:
                    target = min(target, ps)
                got = max(int(st.get("preloaded_bytes") or 0),
                          int(st.get("bytes_read_useful_data") or 0))
                speed = float(st.get("download_speed") or 0)
                peers = int(st.get("active_peers") or 0)
                seeds = int(st.get("connected_seeders") or 0)
                pct = min(100, int(got * 100 / target)) if target else 100
                txt = u"Buffering {}%  ·  {} peers{}  ·  {:.1f} MB/s".format(
                    pct, peers, u" ({} seeds)".format(seeds) if seeds else u"", speed / MB)
                try:
                    callInMainThread(self["status"].setText, txt)
                except Exception:
                    pass
            elapsed = time.time() - t0
            if done and got == 0 and elapsed > 8:
                # preload refused/unsupported (older TorrServer): play as before
                my_log("TorrServer: preload gave nothing -- playing without the buffer")
                return True
            if got >= target or (done and got > 0):
                my_log("TorrServer: buffered {:.1f} MB in {:.0f} s -- playing".format(got / float(MB), elapsed))
                return True
            if elapsed >= self._TORR_WAIT_S:
                if got >= 4 * MB:
                    my_log("TorrServer: only {:.1f} MB after {:.0f} s -- playing anyway".format(
                        got / float(MB), elapsed))
                    return True
                my_log("TorrServer: {:.1f} MB after {:.0f} s, {} peers -- giving up".format(
                    got / float(MB), elapsed, peers))
                try:
                    callInMainThread(self["status"].setText,
                                     u"Too slow ({} peers) — try a torrent with more seeders".format(peers))
                    callInMainThread(self._stopProgress)
                except Exception:
                    pass
                return False
            time.sleep(1.0)
