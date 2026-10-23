# -*- coding: utf-8 -*-
"""NovaPlay player -- choosing and changing what plays: candidate order (preference, play
memory, MP4 seekability), quality / server / speed panel and switches.

Part of NovaPlaySimplePlayer (plugin_screen_player.py), split out
2026-10-23 without changing behaviour. Methods use the player's widgets
and state through self; module-level helpers come from player_base.
"""
from player_base import *  # noqa: F401,F403


class PlayerSwitch(object):
    @staticmethod
    def _normalizeQualityVariants(variants):
        out = []
        if not variants:
            return out
        try:
            items = list(variants)
        except Exception:
            return out
        for it in items:
            label = url = ""
            if isinstance(it, dict):
                url = str(it.get("url") or it.get("stream") or it.get("link") or "")
                label = str(it.get("quality") or it.get("label") or it.get("name") or "")
            elif isinstance(it, (tuple, list)) and len(it) >= 2:
                a, b = str(it[0]), str(it[1])
                if a.lower().startswith("http"):
                    url, label = a, b
                else:
                    label, url = a, b
            elif isinstance(it, str) and it.startswith("http"):
                url = it
            if url.startswith("http"):
                if not label:
                    m = re.search(r"(\d{3,4})\s*p", url, re.I)
                    label = (m.group(1) + "p") if m else "AUTO"
                out.append((label, url))
        seen = set()
        uniq = []
        for label, url in out:
            if url not in seen:
                seen.add(url)
                uniq.append((label, url))
        return uniq

    def _qualityUsable(self):
        variants = list(getattr(self, "_quality_variants", []) or [])
        if not variants:
            try:
                from extractors.base import get_last_quality_variants
                raw = get_last_quality_variants() or []
                variants = self._normalizeQualityVariants(raw)
                if variants:
                    my_log("quality: get_last_quality_variants fallback → {} entries".format(len(variants)))
            except Exception:
                pass
        cur = getattr(self, "_quality_current_url", "")
        dead = getattr(self, "_quality_dead_urls", set())
        return [(l, u) for (l, u) in variants if u != cur and u not in dead]

    def _reorderCandidatesByPref(self, cands):
        try:
            if not cands:
                return cands
            key = _candidate_pref_key(getattr(self, "_item_url", ""))
            pref = str(_get_config(key, "") or "").strip() if key else ""
            # [2026-10-21] the remembered method is per SITE, but one site's
            # servers live on different hosts: TopCinema remembered "5001 direct"
            # (Streamtape) and used it for VideoTube, whose CDN needs a Referer --
            # the header-less request hung Enigma2. When this stream comes with
            # headers, never put a header-less method first; send them first.
            def _plain(lbl):
                l = str(lbl)
                return "headers" not in l and "proxy" not in l
            needs_headers = any(c and len(c) > 2 and not _plain(c[2]) and "headers" in str(c[2])
                                for c in cands)
            if needs_headers:
                if pref and _plain(pref):
                    my_log("candidates: not promoting '{}' -- this stream needs its headers".format(pref))
                    pref = ""
                # only the "+ headers" ones move up; the proxy keeps its place
                hdr = [c for c in cands if "headers" in str(c[2]) and "proxy" not in str(c[2])]
                rest = [c for c in cands if c not in hdr]
                # only when header-less methods are in the list (a strict stream
                # has none left and keeps the proxy first)
                if hdr and cands[0] not in hdr and any(_plain(c[2]) for c in cands):
                    cands[:] = hdr + rest
                    my_log("candidates: methods with headers first -> {}".format(
                        ", ".join(str(c[2]) for c in cands[:3])))
            for i, c in enumerate(cands if pref else []):
                if c and len(c) > 2 and str(c[2]) == pref:
                    if i > 0:
                        win = cands.pop(i)
                        cands.insert(0, win)
                        my_log("candidates: '{}' remembered as working — moved to front".format(pref))
                    break
        except Exception:
            pass
        return self._preferSeekableForMp4(self._orderByPlayMemory(cands))

    def _preferSeekableForMp4(self, cands):
        """[2026-10-22] a plain MP4 file played with 5001 (gstplayer) can't be
        jumped in: on the box it sent the proxy one request from byte 0 and
        ignored the resume and every ◀▶▲▼ (Mixdrop). exteplayer3 (ffmpeg)
        asks for byte ranges, so it goes first for MP4 files -- directly with
        the stream's headers, the proxy as the fallback."""
        try:
            if not cands:
                return cands
            up = (_upstream_of(cands[0][1]) or "").lower().split("?")[0]
            if any(x in up for x in (".m3u8", "master.txt", "/hls", "/playlist", ".mpd")):
                return cands
            if not up.endswith((".mp4", ".m4v", ".mov")):
                return cands
            ext = [c for c in cands if str(c[2]).startswith("5002 exteplayer3")]
            if _wants_ahead_cache(_upstream_of(cands[0][1])):
                # read-ahead cache host: through the proxy, which downloads ahead
                ext.sort(key=lambda c: 0 if "proxy" in str(c[2]) else 1)
                if ext and cands[0] is not ext[0]:
                    cands = ext + [c for c in cands if c not in ext]
                    my_log("candidates: MP4 via the proxy's read-ahead cache -> {}".format(
                        ", ".join(str(c[2]) for c in cands[:3])))
                return cands
            # [2026-10-22] direct "+ headers" first: through the proxy every piece
            # went CDN -> curl_cffi helper -> proxy -> player on the box's CPU and
            # each seek opened a new TLS connection along that chain -- playback
            # stuttered and jumps were slow. ffmpeg sends the headers on every
            # request itself (box: "+ headers" resumed at exactly 1052 s).
            ext.sort(key=lambda c: 0 if "headers" in str(c[2]) else 1)
            if ext and cands[0] is not ext[0]:
                cands = ext + [c for c in cands if c not in ext]
                my_log("candidates: MP4 file -- exteplayer3 first so resume/seek work -> {}".format(
                    ", ".join(str(c[2]) for c in cands[:3])))
        except Exception as e:
            my_log("mp4 player order failed (non-fatal): {}".format(e))
        return cands

    def _orderByPlayMemory(self, cands):
        """[NEW] methods that played on this video server before go first; ones that
        keep failing there go last (plugin_play_memory)."""
        try:
            import plugin_play_memory as _pm
            before = [c[2] for c in cands]
            out = _pm.order(list(cands))
            if [c[2] for c in out] != before:
                my_log("candidates: by play history ({}) -> {}".format(
                    _pm.describe(cands[0][1]), ", ".join(c[2] for c in out[:4])))
            return out
        except Exception as e:
            my_log("play memory order failed (non-fatal): {}".format(e))
            return cands

    def _recordPlayMemory(self, ok):
        try:
            idx = self._candidate_idx
            if 0 <= idx < len(self.candidates):
                import plugin_play_memory as _pm
                c = self.candidates[idx]
                _pm.record(c[1], c[2], ok)
        except Exception:
            pass

    # ── [panel] one-panel menu: Quality | Server | Speed (Yellow) ─────────
    _SPEEDS = ((u"0.5x", 0.5), (u"1x", 1), (u"2x", 2), (u"4x", 4))

    def _pl_panelMenu(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        cur_lbl = str(getattr(self, "_quality_current_label", "") or "")
        dead = getattr(self, "_quality_dead_urls", set())
        vs = [(l, u) for (l, u) in (getattr(self, "_quality_variants", []) or []) if u not in dead]
        if not vs:
            vs = list(self._qualityUsable())
        try:
            from extractors.htmlmedia import sort_by_quality
            vs = sort_by_quality(vs)
        except Exception:
            pass
        cur_url = getattr(self, "_quality_current_url", "")
        q_items, self._panel_q = [], []
        marked = False
        for l, u in vs:
            is_cur = (u == cur_url) or (not marked and cur_lbl and str(l).strip() == cur_lbl.strip())
            marked = marked or is_cur
            q_items.append((str(l), is_cur))
            self._panel_q.append((l, u))
        if not marked:
            q_items.insert(0, (cur_lbl or u"current", True))
            self._panel_q.insert(0, None)
        s_items = [(str(sv.get("name") or u"Server"), (sv.get("url") == self._server_url))
                   for sv in self._servers]
        sp_items = [(lbl, v == self._speed) for lbl, v in self._SPEEDS]
        from plugin_player_panel import NovaPlayerPanel
        self._pl_hideOSD()
        self.session.openWithCallback(self._pl_onPanelPicked, NovaPlayerPanel,
                                      q_items, s_items, sp_items)

    def _pl_onPanelPicked(self, res):
        if not res:
            return
        kind, idx = res
        try:
            if kind == "quality":
                pick = self._panel_q[idx] if 0 <= idx < len(self._panel_q) else None
                if pick:
                    self._switch_kind = "quality"
                    self._pl_qualitySwitch(pick[0], pick[1])
            elif kind == "server":
                if 0 <= idx < len(self._servers):
                    self._pl_serverSwitch(self._servers[idx])
            elif kind == "speed":
                self._pl_setSpeed(self._SPEEDS[idx][1])
        except Exception as e:
            my_log("panel pick error: {}".format(e))

    def _pl_setSpeed(self, v):
        try:
            svc = self.session.nav.getCurrentService()
            p = svc and svc.pause()
            if not p:
                raise Exception("no pause interface")
            if v == 1:
                p.unpause()
            elif v > 1:
                p.setFastForward(int(v))
            else:
                p.setSlowMotion(int(round(1.0 / v)))
            novaplay_tracker.set_play_speed(v)
            self._speed = v
            self._paused = False
            self["status"].setText(u"⏩ Speed {}x".format(v) if v != 1 else u"Playing")
            my_log("speed: {}x".format(v))
        except Exception as e:
            my_log("speed {}x not supported: {}".format(v, e))
            self["status"].setText(u"Speed {}x is not supported by this player".format(v))
        self._pl_showOSD(True)

    def _pl_nextServer(self):
        """Red: jump to the next server of this title, same minute."""
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        if getattr(self, "_quality_switch_in_flight", False):
            return
        n = len(self._servers)
        if n < 2:
            self["status"].setText(u"No other server")
            self._pl_showOSD(True)
            return
        i = next((k for k, sv in enumerate(self._servers) if sv.get("url") == self._server_url), -1)
        nxt = self._servers[(i + 1) % n]
        self._pl_serverSwitch(nxt)

    def _cancelSpeed(self):
        """Back to 1x before a pause/seek/stream change: the position clock and
        the pause/seek maths all assume normal speed."""
        if getattr(self, "_speed", 1) == 1:
            return
        try:
            svc = self.session.nav.getCurrentService()
            p = svc and svc.pause()
            if p:
                p.unpause()
        except Exception:
            pass
        novaplay_tracker.set_play_speed(1)
        self._speed = 1

    def _pl_serverSwitch(self, server):
        if not server or server.get("url") == self._server_url:
            return
        try:
            pos = self._paused_elapsed if self._paused else current_play_secs()
        except Exception:
            pos = 0
        name = server.get("name") or u"Server"
        my_log("server switch: {} → {} (resume at {}s)".format(self._server_name or "?", name, int(pos)))
        self["status"].setText(u"🔄 Preparing {}…".format(name))
        self._pl_showOSD(False)
        site = self._site

        def _bg():
            res = None
            try:
                res = _resolve_server_for_player(site, server)
            except Exception as e:
                my_log("server switch extract error: {}".format(e))
            callInMainThread(self._pl_onServerResolved, server, int(pos), res)
        threading.Thread(target=_bg, daemon=True).start()

    def _pl_onServerResolved(self, server, pos, res):
        if getattr(self, "_closed_flag", False):
            return
        name = server.get("name") or u"Server"
        if not res or not res[0]:
            my_log("server switch: {} found no stream".format(name))
            self["status"].setText(u"✗ {} does not work — try another server".format(name))
            self._pl_showOSD(True)
            try:
                from plugin_server_rank import record_host_result
                record_host_result(server.get("url", ""), False)
            except Exception:
                pass
            return
        stream, qual, ref, variants = res
        url = _stream_play_url(stream, ref)
        self._server_prev = (self._server_url, self._server_name, list(getattr(self, "_quality_variants", []) or []))
        self._server_url = server.get("url", "")
        self._server_name = name
        self._quality_variants = self._normalizeQualityVariants(variants)
        self._quality_dead_urls = set()
        lbl = ""
        try:
            from extractors.base import _label_quality_variant
            lbl = _label_quality_variant(stream) or ""
        except Exception:
            pass
        self._switch_kind = "server"
        self._pl_qualitySwitch(lbl or str(qual or "AUTO"), url, keep_headers=False, resume=pos,
                             status=u"🔄 Server: {}".format(name))

    def _pl_qualityMenu(self):
        if getattr(self, "_studioOverlayActive", False) or getattr(self, "_autonext_active", False):
            return
        variants = self._qualityUsable()
        if not variants:
            self["status"].setText("🎛 No other qualities")
            self._pl_showOSD(True)
            self._hide_timer.start(2500, True)
            return
        try:
            from extractors.htmlmedia import sort_by_quality
            variants = sort_by_quality(variants)
        except Exception:
            pass
        items = []
        for label, url in variants:
            mark = u"●" if url == self._quality_current_url else u"○"
            items.append(("{} {}".format(mark, label), (label, url)))
        try:
            items.append(("{} current".format(u"●"), None))
        except Exception:
            pass
        self.session.openWithCallback(self._pl_onQualityPicked, ChoiceBox,
                                      "Quality", items)

    def _pl_onQualityPicked(self, choice):
        if not choice:
            return
        picked = choice[1] if len(choice) > 1 else None
        if not picked:
            return
        label, url = picked
        self._pl_qualitySwitch(label, url)

    def _pl_qualitySwitch(self, label, url, keep_headers=True, resume=None, status=None):
        if not url or url == getattr(self, "_quality_current_url", ""):
            return
        if resume is not None:
            pos = resume
        else:
            try:
                pos = self._paused_elapsed if self._paused else current_play_secs()
            except Exception:
                pos = 0
        my_log("quality switch: {} → {} (resume at {}s)".format(
            self._quality_current_label or "?", label, int(pos)))
        hdr = ""
        for _pt, _u, _l, _p in (self.candidates or []) if keep_headers else []:
            if "#" in _u:
                hdr = "#" + _u.split("#", 1)[1]
                break

        def _midpoint():
            for t in ("_seek_timer", "_seek_verify_timer", "_retry_timer",
                      "_force_confirmation_timer", "_stall_timer", "_stall_kick_timer"):
                try:
                    getattr(self, t).stop()
                except Exception:
                    pass
            self._osd_video_info = ""
            self._paused = False
            self._play_confirmed = False
            self._candidate_idx = -1
            self._exhausted = False
            self._resume_pos = int(pos)
            self._quality_prev_url = getattr(self, "_quality_current_url", "")
            self._quality_prev_label = getattr(self, "_quality_current_label", "")
            self._quality_prev_candidates = list(self.candidates or [])
            self._quality_switch_in_flight = True
            self._speed = 1
            self._quality_pending_pref = str(label or "").strip() if self._switch_kind == "quality" else ""
            self._quality_current_url = url
            self._quality_current_label = label
            self._quality_switch_target_url = url
            self._autosub_gen = getattr(self, "_autosub_gen", 0) + 1
            _cands = _build_remote_play_candidates(url + hdr)
            self.candidates = _cands[:5] if len(_cands) > 5 else _cands
            self.candidates = self._reorderCandidatesByPref(self.candidates)
            try:
                self._hideSeekFlash()
                self._pl_hideOSD()
            except Exception:
                pass
            self._pl_playNext()

        try:
            self["status"].setText(status or u"🎛 Switching quality: {}".format(label))
        except Exception:
            pass
        # [CHANGE] no full-screen cyan wipe any more (it flashed over the picture
        # on every quality/server change): switch straight away.
        _midpoint()

    def _onQualityRetry(self, ans):
        self._quality_switch_in_flight = False
        self._quality_pending_pref = ""
        prev_cands = getattr(self, "_quality_prev_candidates", [])
        if ans and prev_cands:
            my_log("quality retry: reverting to {} (resume at {}s)".format(
                getattr(self, "_quality_prev_label", "?"), self._resume_pos))
            self.candidates = prev_cands
            self._quality_current_url = getattr(self, "_quality_prev_url", "")
            self._quality_current_label = getattr(self, "_quality_prev_label", "")
            if getattr(self, "_switch_kind", "") == "server" and getattr(self, "_server_prev", None):
                self._server_url, self._server_name, self._quality_variants = self._server_prev
                self._server_prev = None
            self._play_confirmed = False
            self._candidate_idx = -1
            self._exhausted = False
            self._stall_last_pts = -1
            self._stall_count = 0
            self._stall_fail_count = 0
            try:
                self["status"].setText(u"🎛 Going back to the previous quality…")
                self._pl_showOSD(True)
            except Exception:
                pass
            self._pl_playNext()
        else:
            self._pl_onExit(clear_position=False)
