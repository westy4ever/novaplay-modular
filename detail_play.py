# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- from a server to playback: extraction, quality choices, variants,
next episode, auto start, _onStreamFound.

Part of NovaPlayDetail (plugin_screen_detail.py), split out 2026-10-23
without changing behaviour. Methods use the screen's widgets and state
through self; module-level helpers come from detail_base.
"""
from detail_base import *  # noqa: F401,F403


class DetailPlay(object):
    def _backToServers(self, server=None):
        """Quality list -> server list, keeping the cursor on `server`."""
        if not self._quality_choices:
            return
        self._quality_choices = []
        try:
            self._setServerSection()
            self._setMenu([self._format_server_item(s) for s in self._servers])
            self["status"].setText(self._status_hint("Choose a server — OK"))
            if server is not None and server in self._servers:
                self["menu"].instance.moveSelectionTo(self._servers.index(server))
                self._updateMenuAccent()
        except Exception as e:
            my_log("back to servers: {}".format(e))

    def _bgExtract(self, server, token=None):
        try:
            url = server.get("url", "")
            if url.startswith("magnet:"):
                callInMainThread(self["status"].setText, "TorrServer: Starting Stream...")
                threading.Thread(target=self._bgTorrServerMagnet, args=(url, server, token), daemon=True).start()
                return

            extractor = None
            try:
                extractor = _get_extractor(self._site)
            except Exception:
                extractor = None
            if extractor is None or getattr(extractor, "extract_stream", None) is None:
                from extractors import base as extractor

            from novaplay_proxy import cached_extract, attach_cookies
            try:
                result = cached_extract(extractor, url)
                result = attach_cookies(result)
            except Exception:
                result = extractor.extract_stream(url)

            if len(result) >= 4:
                url, qual, final_ref, variants = result[0], result[1], result[2], result[3]
            else:
                url, qual, final_ref = result[0], result[1], result[2]
                variants = []

            if getattr(self, "_closed", False): return
            if token is not None and token != getattr(self, "_extract_token", token): return

            if url:
                if variants:
                    callInMainThread(self._onQualityChoices, url, qual, final_ref, variants, server)
                else:
                    callInMainThread(self._onStreamFound, url, qual, final_ref, server, variants)
            else:
                # [detail-progress] extraction failed — hide the sweep bar
                callInMainThread(self._stopProgress)
                # [server-rank] this server/host failed to resolve -- counts
                # against it so it sinks toward the bottom of future lists.
                try:
                    from plugin_server_rank import record_host_result, record_title_failed
                    record_host_result(server.get("url", ""), False)
                    _rank_item = getattr(self, "_item", None) or {}
                    record_title_failed(_rank_item.get("url", ""), server.get("url", ""))
                except Exception as e:
                    my_log("server-rank record (failure) failed (non-fatal): {}".format(e))
                if get_curl_failed_needs_proxy():
                    try:
                        plugin_health.record(getattr(self, "_site", "unknown"), "blocked")
                    except Exception as e:
                        my_log("_bgExtract: plugin_health.record failed (non-fatal): {}".format(e))
                    # [FIX] confirmed live: same stale-self pattern as the
                    # _item/_site crashes fixed earlier -- self._proxy_warning_shown
                    # can be missing on a stale background thread from a Detail
                    # screen the user already backed out of. Default to "not
                    # shown yet"; worst case is one extra popup, never a crash.
                    if not getattr(self, "_proxy_warning_shown", False):
                        self._proxy_warning_shown = True
                        msg = "⚠️ curl_cffi could not get past Cloudflare.\n"
                        msg += "Please enable the Proxy in Settings (Blue → Red)."
                        callInMainThread(self._showProxyWarningPopup, msg)
                    try:
                        callInMainThread(self["proxy_warning"].setText, "⚠️ Enable Proxy")
                    except Exception as e:
                        my_log("proxy_warning widget update failed: {}".format(e))
                    try:
                        callInMainThread(self["status"].setText, "⚠️ Proxy needed — see Settings")
                    except Exception as e:
                        my_log("status widget update failed: {}".format(e))
                else:
                    # [FIX] confirmed real crash on a real device: a stale
                    # background extraction thread (from a server the user
                    # already navigated away from) reached this point after
                    # the "status" widget was no longer valid, raising
                    # KeyError: 'status' and killing the thread uncaught.
                    # Matches the same try/except pattern already used two
                    # branches above for "proxy_warning"/"status".
                    try:
                        callInMainThread(self["status"].setText, "Could not get the link — try another server")
                    except Exception as e:
                        my_log("status widget update failed: {}".format(e))
        except Exception as e:
            my_log("Detail _bgExtract CRITICAL ERROR: {}: {}\n{}".format(type(e).__name__, e, traceback.format_exc()))
            callInMainThread(self._stopProgress)          # [detail-progress]
            if not getattr(self, "_closed", False):
                # [FIX] same unguarded self["status"] access as above --
                # same fix.
                try:
                    callInMainThread(self["status"].setText, "System error: {}".format(str(e)[:30]))
                except Exception as e2:
                    my_log("status widget update failed in error handler: {}".format(e2))
        finally:
            if token is not None:
                with self._extract_lock:
                    if token == self._extract_token:
                        self._extracting = False

    def _showProxyWarningPopup(self, msg):
        self.session.open(MessageBox, msg, MessageBox.TYPE_WARNING, timeout=8)

    def _onQualityChoices(self, url, qual, final_ref, variants, server):
        if getattr(self, "_closed", False): return
        # [detail-progress] resolving to a menu means "no immediate play" —
        # stop the sweep so it doesn't sit over the resolved quality list
        self._stopProgress()

        # [B3] if a stored quality preference matches an available variant,
        # auto-play it directly instead of showing the menu
        try:
            # [CHANGE 2026-10-18] user wants to be asked every time: the remembered
            # quality is only auto-picked when the "quality_auto_pick" setting is on
            _pk = _quality_pref_key(self._raw_title or self._item.get("title", "")) \
                if _get_config("quality_auto_pick", False) else ""
            if _pk:
                _pl = str(_get_config(_pk, "") or "").strip()
                if _pl:
                    for _lbl, _vurl in (variants or []):
                        if str(_lbl).strip().lower() == _pl.lower():
                            # [PATCH 96] same viability rule as the menu filter below: with a
                            # tokened master, tokenless / cross-host variants are dead links
                            try:
                                _mu0 = urlparse(url)
                                if _mu0.query and not (urlparse(_vurl).query
                                                       and urlparse(_vurl).netloc == _mu0.netloc):
                                    continue
                            except Exception:
                                pass
                            my_log("quality pref: auto-selecting '{}' for key '{}'".format(_pl, _pk))
                            self._onStreamFound(_vurl, _pl, final_ref, server)
                            return
        except Exception as e:
            my_log("quality pref check failed: {}".format(e))

        choices = [{
            "label": qual or "Default",
            "url": url,
            "final_ref": final_ref,
            "server": server,
        }]
        seen_urls = {url}
        try:
            _mu = urlparse(url)
            if _mu.query:
                variants = [(lbl, v) for lbl, v in (variants or [])
                            if urlparse(v).query and urlparse(v).netloc == _mu.netloc]
        except Exception:
            pass
        for lbl, vurl in variants:
            if vurl in seen_urls: continue
            seen_urls.add(vurl)
            choices.append({
                "label": lbl,
                "url": vurl,
                "final_ref": final_ref,
                "server": server,
            })
        # highest quality first (2160p, 1080p, 720p, ...), on every site; the
        # default entry gets a real resolution label when its URL shows one
        try:
            from extractors.htmlmedia import sort_by_quality, _label_quality_variant
            _dl = _label_quality_variant(url)
            if _dl and choices and not re.search(r"\d{3,4}p", str(choices[0]["label"])):
                choices[0]["label"] = _dl
            choices = sort_by_quality(choices, lambda c: c["label"], lambda c: c["url"])
        except Exception as e:
            my_log("quality sort failed (non-fatal): {}".format(e))
        self._quality_choices = choices

        quality_labels = []
        for i, c in enumerate(choices):
            label = c["label"]
            if "2160" in label or "1440" in label or "4K" in label.upper():
                badge = "🔴"
            elif "1080" in label or "Original" in label:
                badge = "🔵"
            elif "720" in label:
                badge = "🟢"
            elif "480" in label:
                badge = "🟡"
            elif "360" in label:
                badge = "⚪"
            else:
                badge = "🟣"
            # [panel] number box + name + resolution badge (StreamList draws them)
            _qm = re.search(r"(2160|1440|1080|720|576|480|360|240)\s*p|\b4k\b", label, re.I)
            _qt = ("4K" if _qm.group(0).lower() == "4k" or _qm.group(1) == "2160" else _qm.group(1) + "P") if _qm else "HD"
            quality_labels.append((_qt, label, "", ""))

        self._setSection(_single_line_text("Available qualities: {}  |  pick a quality".format(len(choices)), width=90))
        # [detail-stagger] quality menu pops in one row at a time
        self._setMenuStaggered(quality_labels)
        self["status"].setText(self._status_hint("Choose a quality — OK"))
        # [panel] the same choice as a Movy-style panel: Quality | Server.
        # EXIT closes it and leaves the list above on screen.
        try:
            from plugin_player_panel import NovaQualityPanel
            q_items = [(str(c["label"]).split(". ", 1)[-1], False) for c in choices]
            s_items = [((sv.get("name") or "Server").split("\n")[0][:46], sv is server)
                       for sv in self._servers]
            self.session.openWithCallback(
                lambda res, _srv=server: self._onQualityPanel(res, _srv),
                NovaQualityPanel, q_items, s_items)
        except Exception as e:
            my_log("quality panel failed, using the list: {}".format(e))

    def _onQualityPanel(self, res, server):
        if not res or getattr(self, "_closed", False):
            return
        col, idx = res
        if col == "quality":
            if 0 <= idx < len(self._quality_choices):
                choice = self._quality_choices[idx]
                self._last_quality_selection = choice
                # the panel made the choice: put the server list back (on the
                # same server) so the old quality list isn't there after the film
                self._backToServers(choice.get("server") or server)
                self._onStreamFound(choice["url"], choice["label"], choice["final_ref"], choice["server"])
        elif col == "server" and 0 <= idx < len(self._servers):
            other = self._servers[idx]
            if other is server:
                return
            self._onCancel()          # back to the server list...
            try:
                self["menu"].instance.moveSelectionTo(idx)
                self._updateMenuAccent()
            except Exception:
                pass
            self._onOk()              # ...and start the picked server

    def _playNextEpisode(self, next_ep):
        if getattr(self, "_closed", False) or not next_ep:
            return
        self._closed = True
        self.session.open(
            type(self), next_ep, self._site,
            next_ep.get("type", "episode"),
            episode_chain=self._episode_chain,
            episode_index=self._episode_index + 1,
            auto_server_idx=self._last_server_idx)
        try:
            self.picLoad.PictureData.get().remove(self._paintPoster)
        except Exception:
            pass
        for p in self._tmp_posters:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass
        self.close()

    def _autoStartServer(self, idx):
        if getattr(self, "_closed", False) or self._quality_choices:
            return
        if not (0 <= idx < len(self._servers)):
            return
        server = self._servers[idx]
        if server.get("url", "").startswith("magnet:"):
            return
        with self._extract_lock:
            if self._extracting:
                return
            self._extracting = True
            self._extract_token += 1
            token = self._extract_token
        self["status"].setText("Playing the next episode...")
        self["status"].show()
        self._startProgress()          # [detail-progress]
        threading.Thread(target=self._bgExtract, args=(server, token), daemon=True).start()

    def _dropBrokenVariants(self, url):
        """[2026-10-21] A master playlist can list a broken copy -- TopCinema's
        VideoTube: 1920x904 at FRAME-RATE=95.948 (a mistimed telesync). The
        player takes it as the best quality and the box's decoder hangs,
        freezing Enigma2. Drop qualities above 60 fps; when one is dropped,
        play the best remaining one directly. Untouched otherwise."""
        self._bad_variants = set()
        try:
            base, frag = (url.split("#", 1) + [""])[:2]
            if ".m3u8" not in base.lower():
                return url
            headers = {}
            for p in (frag.split("&") if frag else []):
                if "=" in p:
                    k, v = p.split("=", 1)
                    headers[k] = v
            from extractors.base import fetch
            text, _final = fetch(base, extra_headers=headers, timeout=8, max_retries=0)
            if not text or "#EXT-X-STREAM-INF" not in text:
                return url
            from urllib.parse import urljoin
            lines = [l.strip() for l in text.splitlines()]
            good, bad = [], []
            for i, line in enumerate(lines):
                if not line.startswith("#EXT-X-STREAM-INF"):
                    continue
                uri = next((l for l in lines[i + 1:] if l and not l.startswith("#")), None)
                if not uri:
                    continue
                m = re.search(r"RESOLUTION=\d+[xX](\d+)", line)
                f = re.search(r"FRAME-RATE=([\d.]+)", line)
                h = int(m.group(1)) if m else 0
                fps = float(f.group(1)) if f else 0.0
                (bad if fps > 60.5 else good).append((h, fps, urljoin(base, uri)))
            if not bad:
                return url
            self._bad_variants = set(u for _h, _f, u in bad)
            for h, fps, u in bad:
                my_log("broken quality dropped: {}p at {:.1f} fps -- {}".format(h, fps, u[-60:]))
            if not good:
                return url
            h, fps, u = max(good, key=lambda g: g[0])
            my_log("playing {}p ({} broken quality left out)".format(h, len(bad)))
            return u + ("#" + frag if frag else "")
        except Exception as e:
            my_log("variant check error: {} -- playing original URL".format(e))
            return url

    def _applyQualityCap(self, url):
        try:
            cap = str(_get_config("max_quality", "auto") or "auto").strip().lower()
            if cap in ("", "auto"):
                return url
            digits = "".join(c for c in cap if c.isdigit())
            cap_h = int(digits) if digits else 0
            if not cap_h:
                return url
            base, frag = (url.split("#", 1) + [""])[:2]
            if ".m3u8" not in base.lower():
                return url
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"}
            if frag:
                for p in frag.split("&"):
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if k not in headers:
                            headers[k] = v
            from extractors.base import fetch
            text, _final = fetch(base, extra_headers=headers)
            if not text or "#EXT-X-STREAM-INF" not in text:
                return url
            from urllib.parse import urljoin
            lines = [l.strip() for l in text.splitlines()]
            best = None
            smallest = None
            i = 0
            while i < len(lines):
                if lines[i].startswith("#EXT-X-STREAM-INF"):
                    j = i + 1
                    while j < len(lines) and not lines[j]:
                        j += 1
                    if j < len(lines) and not lines[j].startswith("#"):
                        uri = lines[j]
                        m = re.search(r"RESOLUTION=(\d+)[xX](\d+)", lines[i])
                        h = int(m.group(2)) if m else 0
                        if h and h <= cap_h and (best is None or h > best[0]):
                            best = (h, uri)
                        if h and (smallest is None or h < smallest[0]):
                            smallest = (h, uri)
                        i = j + 1
                        continue
                i += 1
            pick = best or smallest
            if not pick:
                return url
            variant = urljoin(base, pick[1])
            if not variant or variant == base:
                return url
            my_log("quality cap: {}p master → {}p rendition".format(cap_h, pick[0]))
            return variant + ("#" + frag if frag else "")
        except Exception as e:
            my_log("quality cap error: {} — playing original URL".format(e))
            return url

    def _onStreamFound(self, stream_url, quality, final_ref, server, variants=None):
        if getattr(self, "_closed", False): return
        # [detail-progress] resolution done — the sweep has served its purpose
        self._stopProgress()
        try:
            if server in self._servers:
                self._last_server_idx = self._servers.index(server)
        except Exception:
            pass
        if not stream_url:
            self._resume_direct = False      # the remembered server failed: user picks
            self["status"].setText("{} — not available, try another server".format(server["name"]))
            toast(self, u"{} failed \u2014 try another server".format(server["name"][:24]))
            return
        try:
            from plugin_state import _save_last_server
            _save_last_server((getattr(self, "_item", None) or {}).get("url", ""),
                              server.get("name", ""), server.get("url", ""))
        except Exception:
            pass
        my_log("Stream found: {} [{}]".format(stream_url, quality))
        # [server-rank] NOTE: success/failure recording moved to the player
        # screen (_pl_onConfirmed / exhausted in player_fallback.py) --
        # finding a stream URL here doesn't mean it actually played.
        _base_item = getattr(self, "_item", None) or {}
        try:
            history_entry = _entry_from_item(
                dict(_base_item, **(self._data or {})),
                self._site,
                self._m_type,
                {
                    "server_name": server.get("name", ""),
                    "quality": quality or "",
                    "last_stream_url": stream_url,
                }
            )
            _upsert_library_item("history", history_entry, limit=120)
        except Exception as e:
            my_log("_onStreamFound: history write failed (non-fatal): {}".format(e))
        try:
            # again after the history write: on a first play the entry didn't
            # exist yet when it was saved above
            from plugin_state import _save_last_server
            _save_last_server(_base_item.get("url", ""), server.get("name", ""), server.get("url", ""))
        except Exception:
            pass

        title = getattr(self, "_raw_title", None) or re.sub(r"\s+", " ", self["title"].getText()).strip()

        try:
            from plugin_screen_player import _build_remote_play_candidates
            raw_url = stream_url.strip()
            if "|" in raw_url:
                main_url, old_params = raw_url.split("|", 1)
            else:
                main_url, old_params = raw_url, ""

            lower_main_url = main_url.lower()
            is_media_url = any(marker in lower_main_url for marker in (
                ".m3u8", ".mp4", ".mkv", ".mp3", ".ts", ".avi", "master.txt", "/hls", "/stream", "/playlist"
            ))
            is_embed_page = any(marker in lower_main_url for marker in (
                "/embed-", "/embed/", "/e/", "/watch/"
            ))
            if is_embed_page and not is_media_url:
                self["status"].setText("The link is a player page, not a video file — try another server")
                return

            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"}
            if final_ref:
                headers["Referer"] = final_ref
            own_ref = False
            if old_params:
                for p in old_params.split("&"):
                    if "=" in p:
                        k, v = p.split("=", 1)
                        # [2026-10-21] the resolver's own Referer/Origin win over
                        # the site address (VideoTube's CDN 403s the site's)
                        if k in ("Referer", "Origin") or k not in headers:
                            headers[k] = v
                            own_ref = own_ref or k == "Referer"
            if own_ref:
                # the stream named its Referer: requests without it are refused,
                # and on the box a refused header-less request can hang Enigma2
                headers["X-NP-Strict"] = "1"

            header_str = "&".join(["{}={}".format(k, v) for k, v in headers.items()])
            pure_url = main_url.split("|")[0].strip()
            url = pure_url + "#" + header_str if header_str else pure_url
            url = self._dropBrokenVariants(url)
            url = self._applyQualityCap(url)

            if variants is not None:
                _qv = variants or None
                _bad = getattr(self, "_bad_variants", set())
                if _qv and _bad:
                    _qv = [v for v in _qv if str(v[1]).split("#")[0].split("|")[0] not in _bad] or None
                if _qv:
                    my_log("quality: passing {} variant(s) to player".format(len(_qv)))
            else:
                try:
                    from extractors.base import get_last_quality_variants
                    _qv = get_last_quality_variants() or []
                except Exception:
                    _qv = []
                _bad = getattr(self, "_bad_variants", set())
                if _qv and _bad:
                    _qv = [v for v in _qv if str(v[1]).split("#")[0].split("|")[0] not in _bad]
                if _qv:
                    my_log("quality: passing {} variant(s) to player".format(len(_qv)))
                else:
                    _qv = None

            _item_url = (getattr(self, "_item", None) or {}).get("url", "")
            _saved_pos = _get_saved_position(_item_url)
            if _saved_pos > 30 and self._resume_direct:
                self._resume_direct = False
                self._close_on_return = self._episode_index < 0   # back to Home after watching
                self["status"].setText("Resuming…")
                _play(self.session, url, title, resume_pos=_saved_pos, item_url=_item_url,
                      next_episode=self._next_episode, on_next=self._playNextEpisode,
                      poster_url=self._osd_poster,
                      quality_variants=_qv, server_url=server.get("url", ""),
                      servers=list(self._servers or []), site=self._site,
                      server_name=server.get("name", ""))
            elif _saved_pos > 30:
                if _saved_pos >= 3600:
                    _hours_r = _saved_pos // 3600
                    _mins_r = (_saved_pos % 3600) // 60
                    _secs_r = _saved_pos % 60
                    resume_text = "Resume from {:02d}:{:02d}:{:02d}?".format(_hours_r, _mins_r, _secs_r)
                else:
                    _mins_r = _saved_pos // 60
                    _secs_r = _saved_pos % 60
                    resume_text = "Resume from {}:{:02d}?".format(_mins_r, _secs_r)

                def _on_resume(_ans, _u=url, _t=title, _iu=_item_url, _sp=_saved_pos,
                               _ne=self._next_episode, _cb=self._playNextEpisode,
                               _po=self._osd_poster, _qv=_qv, _su=server.get("url", ""),
                               _svs=list(self._servers or []), _site=self._site,
                               _sn=server.get("name", "")):
                    if not _ans:
                        _save_position(_iu, 0)
                    _play(self.session, _u, _t, resume_pos=_sp if _ans else 0, item_url=_iu,
                          next_episode=_ne, on_next=_cb, poster_url=_po,
                          quality_variants=_qv, server_url=_su,
                          servers=_svs, site=_site, server_name=_sn)
                self["status"].setText("Opening the player...")
                self.session.openWithCallback(_on_resume, MessageBox, resume_text, MessageBox.TYPE_YESNO, timeout=8, default=True)
            else:
                self["status"].setText("Opening player...")
                _play(self.session, url, title, resume_pos=0, item_url=_item_url,
                      next_episode=self._next_episode, on_next=self._playNextEpisode,
                      poster_url=self._osd_poster,
                      quality_variants=_qv, server_url=server.get("url", ""),
                      servers=list(self._servers or []), site=self._site,
                      server_name=server.get("name", ""))
            from extractors.base import get_proxy_used
            if get_proxy_used():
                self["status"].setText("✓ Proxy  " + self["status"].getText())
                self["status"].show()
                self["proxy_warning"].setText("")
            else:
                self["status"].hide()
        except Exception as e:
            my_log("Error opening player: {}".format(e))
            self["status"].setText("Player error: {}".format(str(e)[:60]))
