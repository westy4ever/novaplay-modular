# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- the top of the screen: title fit, tagline, details chip, facts, cast,
poster strip, layout, reveal animation, poster / backdrop pictures,
_onLoaded (fills everything when the page data arrives).

Part of NovaPlayDetail (plugin_screen_detail.py), split out 2026-10-23
without changing behaviour. Methods use the screen's widgets and state
through self; module-level helpers come from detail_base.
"""
from detail_base import *  # noqa: F401,F403


class DetailHeader(object):
    # ── [cast] round TMDb photos of the main actors (Modern only) ────────
    def _loadCast(self, tmdb):
        key = (tmdb.get("tmdb_kind"), tmdb.get("tmdb_id"))
        if key == self._cast_token:
            return
        self._cast_token = key

        def _worker():
            cast = tmdb.get("cast")
            if not cast:
                try:
                    from plugin_tmdb import _tmdb_request
                    cr = _tmdb_request("/{}/{}/credits".format(key[0], key[1])) or {}
                    cast = [{"name": c.get("name", ""), "photo": c.get("profile_path") or ""}
                            for c in (cr.get("cast") or [])[:CAST_N]]
                except Exception as e:
                    my_log("detail cast: {}".format(e))
                    cast = []
            out = []
            for c in cast[:CAST_N]:
                out.append((c.get("name", ""), _round_photo(c.get("photo"), _CAST_PHOTO)))
            callInMainThread(self._paintCast, key, out)
        threading.Thread(target=_worker, daemon=True).start()

    def _paintCast(self, key, cast):
        if getattr(self, "_closed", False) or key != self._cast_token:
            return
        for i in range(CAST_N):
            if i < len(cast):
                name, path = cast[i]
                self["castName%d" % i].setText(_single_line_text(name, width=16, fallback=""))
                self["castName%d" % i].show()
                inst = self["castImg%d" % i].instance
                if path and inst is not None:
                    inst.setPixmapFromFile(path)
                    self["castImg%d" % i].show()
                    self["castInit%d" % i].hide()
                else:
                    # [hdr-ui] no photo: a round circle with the initials
                    self["castImg%d" % i].hide()
                    self["castInit%d" % i].setText(_initials(name))
                    self["castInit%d" % i].show()
            else:
                self["castName%d" % i].hide()
                self["castImg%d" % i].hide()
                self["castInit%d" % i].hide()

    # ── [detail-layout] stack title / meta / source / plot with no gaps ──
    _HDR_X = 525
    _HDR_W = 1320
    _HDR_BOTTOM = 650

    def _plotTextHeight(self):
        """Height the plot text needs at its width (36 px a line)."""
        try:
            inst = self["plot"].instance
            if inst:
                h = int(inst.calculateSize().height())
                if h > 0:
                    return min(256, h + 6)
        except Exception:
            pass
        try:
            t = self["plot"].getText() or ""
        except Exception:
            t = ""
        if not t.strip():
            return 36
        lines = sum(max(1, -(-len(par) // 92)) for par in t.split("\n"))
        return min(256, lines * 36)

    # ── [hdr-ui] header: title size, tagline, rating chip, poster strip ──
    def _hdrInit(self):
        for k in ("tagline", "metaCert", "posStrip", "posStatus", "posFav", "posBarBg", "posBar"):
            try:
                self[k].hide()
            except Exception:
                pass
        if getattr(self, "_modern", False):
            for i in range(CAST_N):
                try:
                    self["castInit%d" % i].hide()
                except Exception:
                    pass

    def _setFont(self, key, face, size):
        try:
            from enigma import gFont
            inst = self[key].instance
            if inst is not None:
                inst.setFont(gFont(face, int(size)))
        except Exception as e:
            my_log("detail font {}: {}".format(key, e))

    # (base size, smaller steps, 2-line size, box width)
    _TITLE_FIT = {True: (52, (44, 38, 32), 30, 900), False: (40, (34, 30), 24, 1120)}

    def _fitTitle(self, text):
        """[hdr-ui] long titles: the font steps down until the title fits on
        one line; still too long -> two smaller lines (never cut short)."""
        text = re.sub(r"\s+", " ", str(text or "")).strip() or "Untitled"
        base, steps, two, box = self._TITLE_FIT[bool(getattr(self, "_modern", False))]
        n = len(text)
        size = base
        for sz in (base,) + tuple(steps):
            size = sz
            if n * sz * 0.55 <= box:
                self._setFont("title", "Bold", sz)
                self["title"].setText(text)
                return
        per_line = int(box / (two * 0.55))
        self._setFont("title", "Bold", two)
        self["title"].setText(_wrap_ui_text(text, width=per_line, max_lines=2, fallback="Untitled"))

    def _setMetaLine(self, cert, text):
        """[hdr-ui] age rating as an outlined chip, the rest after it"""
        modern = bool(getattr(self, "_modern", False))
        x0, y = (420, 242) if modern else (240, 86)
        box = 900 if modern else 1120
        cert = str(cert or "").strip()
        if cert:
            cw = 26 + 12 * len(cert)
            self["metaCert"].setText(cert)
            self["metaCert"].show()
            mx = x0 + cw + 14
        else:
            cw = 0
            self["metaCert"].hide()
            mx = x0
        self._metaCertW = cw
        self._metaX = mx
        self["meta"].setText(_single_line_text(text, width=max(20, int((box - (mx - x0)) / 12.5)), fallback=""))

    def _layoutHeader(self):
        """[hdr-ui] Modern: title / tagline / details / facts / cast / plot
        stacked over the backdrop -- without a tagline everything below the
        title moves up into its place. Classic: the tagline sits above the
        plot in the right column."""
        self._hdr_y = {}
        pos = {}
        tag = getattr(self, "_tagline", "")
        modern = bool(getattr(self, "_modern", False))
        cw = getattr(self, "_metaCertW", 0)
        mx = getattr(self, "_metaX", 420 if modern else 240)
        if modern:
            dy = 0 if tag else -34
            if tag:
                self["tagline"].setText(_single_line_text(tag, width=74, fallback=""))
                self["tagline"].show()
            else:
                self["tagline"].hide()
            pos["metaCert"] = (420, 242 + dy)
            pos["meta"] = (mx, 242 + dy)
            pos["source"] = (420, 280 + dy)
            pos["plot_title"] = (420, 548 + dy)
            pos["plot"] = (420, 576 + dy)
            self._moveResize("metaCert", 420, 242 + dy, max(1, cw), 32)
            self._moveResize("meta", mx, 242 + dy, 900 - (mx - 420), 32)
            self._moveResize("source", 420, 280 + dy, 900, 30)
            self._moveResize("plot_title", 420, 548 + dy, 600, 26)
            self._moveResize("plot", 420, 576 + dy, 1460, 110 - dy)
            for i in range(CAST_N):
                x, y = _CAST_X[i % 3], _CAST_Y[i // 3] + dy
                ix = x + (_CAST_W - _CAST_PHOTO) // 2
                self._moveResize("castImg%d" % i, ix, y, _CAST_PHOTO, _CAST_PHOTO)
                self._moveResize("castInit%d" % i, ix, y, _CAST_PHOTO, _CAST_PHOTO)
                self._moveResize("castName%d" % i, x, y + _CAST_PHOTO + 4, _CAST_W, 26)
        else:
            pos["metaCert"] = (240, 86)
            pos["meta"] = (mx, 86)
            self._moveResize("metaCert", 240, 86, max(1, cw), 30)
            self._moveResize("meta", mx, 86, 1120 - (mx - 240), 32)
            if tag:
                self["tagline"].setText(_wrap_ui_text(tag, width=40, max_lines=2, fallback=""))
                self["tagline"].show()
                th = 56 if len(tag) > 40 else 30
                pos["plot"] = (1430, 466 + th + 6)
                self._moveResize("plot", 1430, 466 + th + 6, 430, 476 - th - 6)
            else:
                self["tagline"].hide()
                pos["plot"] = (1430, 466)
                self._moveResize("plot", 1430, 466, 430, 476)
        self._hdr_pos = pos

    def _paintPosterStatus(self):
        """[hdr-ui] strip at the bottom of the poster: "▶ Resume 42:10 · 65%"
        with a progress line, "✓ Watched", and a gold ★ when it's a favourite."""
        try:
            url = (getattr(self, "_item", None) or {}).get("url", "")
            if not url or getattr(self, "_closed", False):
                return
            fav = bool(_is_favorite(url))
            pos = int(_get_saved_position(url) or 0)
            dur = 0
            for h in _history_items():
                if h.get("url") == url:
                    dur = int(h.get("total_duration") or h.get("duration") or h.get("total_seconds") or 0)
                    break
            if not dur and getattr(self, "_rt_min", 0) and self._episode_index < 0:
                dur = int(self._rt_min) * 60
            watched = False
            try:
                from plugin_watched import is_watched
                watched = bool(is_watched(url))
            except Exception:
                pass
            txt, pct = "", 0
            if pos > 30:
                hh, rem = divmod(pos, 3600)
                mm, ss = divmod(rem, 60)
                t = "{}:{:02d}:{:02d}".format(hh, mm, ss) if hh else "{}:{:02d}".format(mm, ss)
                if dur > pos:
                    pct = max(1, min(99, int(pos * 100 / dur)))
                    txt = u"\u25b6 Resume {}  \u00b7  {}%".format(t, pct)
                else:
                    txt = u"\u25b6 Resume {}".format(t)
            elif watched:
                txt = u"\u2713 Watched"
            fav_only = fav and not txt
            if fav_only:
                txt = u"\u2605 Favourite"
            if not txt:
                for k in ("posStrip", "posStatus", "posFav", "posBarBg", "posBar"):
                    self[k].hide()
                return
            self["posStatus"].setText(txt)
            self._svColor("posStatus", fg="#F2C230" if fav_only else "#FFFFFF")
            self["posStrip"].show()
            self["posStatus"].show()
            if fav and not fav_only:
                self["posFav"].setText(u"\u2605")
                self["posFav"].show()
            else:
                self["posFav"].hide()
            if pct:
                px, py = (52, 152) if getattr(self, "_modern", False) else (62, 172)
                self._moveResize("posBar", px, py + 466, max(2, int(316 * pct / 100)), 4)
                self["posBarBg"].show()
                self["posBar"].show()
            else:
                self["posBarBg"].hide()
                self["posBar"].hide()
        except Exception as e:
            my_log("poster status: {}".format(e))

    # ── [detail-common] move+resize helper ─────────────────────────────
    def _moveResize(self, key, x, y, w, h):
        try:
            inst = self[key].instance
            if inst:
                inst.move(ePoint(int(x), int(y)))
                inst.resize(eSize(max(1, int(w)), max(1, int(h))))
        except Exception as e:
            my_log("detail moveResize error for {}: {}".format(key, e))

    # ── [detail-reveal] staggered slide-in on open ────────────────────
    def _startReveal(self):
        # chained episode → instant, no cascade
        if self._episode_index >= 0:
            return
        self._reveal_running = True
        self._reveal_frame = 0
        for _name, widgets, ox, oy, _d, _dur in self._REVEAL_GROUPS:
            for key, dx, dy in widgets:
                dx, dy = getattr(self, "_hdr_pos", {}).get(key, (dx, getattr(self, "_hdr_y", {}).get(key, dy)))
                try:
                    inst = self[key].instance
                    if inst:
                        inst.move(ePoint(int(dx + ox), int(dy + oy)))
                except Exception:
                    pass
        try:
            self._reveal_timer.start(20, False)
        except Exception as e:
            my_log("reveal timer start failed: {}".format(e))
            self._reveal_running = False

    def _revealTick(self):
        if getattr(self, "_closed", False):
            try: self._reveal_timer.stop()
            except Exception: pass
            self._reveal_running = False
            return
        self._reveal_frame += 1
        all_done = True
        for _name, widgets, ox, oy, delay, dur in self._REVEAL_GROUPS:
            local = self._reveal_frame - delay
            if local < 0:
                all_done = False
                continue
            p = min(1.0, float(local) / float(dur))
            if p < 1.0:
                all_done = False
            p_eased = 1.0 - (1.0 - p) ** 3
            xoff = int(ox * (1.0 - p_eased))
            yoff = int(oy * (1.0 - p_eased))
            for key, dx, dy in widgets:
                dx, dy = getattr(self, "_hdr_pos", {}).get(key, (dx, getattr(self, "_hdr_y", {}).get(key, dy)))
                try:
                    inst = self[key].instance
                    if inst:
                        inst.move(ePoint(int(dx + xoff), int(dy + yoff)))
                except Exception:
                    pass
        if all_done:
            self._reveal_running = False
            try: self._reveal_timer.stop()
            except Exception: pass

    def _paintPoster(self, picData=None):
        ptr = self.picLoad.getData()
        if ptr:
            self["poster"].instance.setPixmap(ptr)
            self["poster"].show()
            self._poster_loaded = True
        else:
            my_log("_paintPoster (detail): native decode returned empty picture data")

    def _paintBackdrop(self, picData=None):
        ptr = self.backdropPicLoad.getData()
        if ptr:
            self["backdropImage"].instance.setPixmap(ptr)
            self["backdropImage"].show()
        else:
            my_log("_paintBackdrop (detail): native decode returned empty picture data")

    def _generateBackdrop(self, src_path, is_tmp=False, is_landscape=False):
        """
        [detail-tmdb] Builds the darkened backdrop image for the panel,
        matching real TMDb's header treatment (a backdrop image filling
        the header behind the sharp poster card, dimmed so the title/plot
        text stays readable). Runs on the same background thread as its
        caller (never the UI thread), writes a small derived cache file
        next to the source file, and hands only the native decode/paint
        step to the main thread via callInMainThread, matching the
        existing self.picLoad pattern above.

        Uses the same try/except PIL-optional convention as
        plugin_imagecache.resizeCover/plugin_util -- if PIL isn't
        installed, this silently does nothing and the flat-color
        "backdrop" widget (zPosition 2, already sitting underneath as the
        fallback) stays visible on its own.

        is_landscape distinguishes the two possible sources, which need
        different treatment:
          - True:  src_path is TMDb's own backdrop_path image (served
            ~1280 wide, already roughly the panel's own 1830x600 aspect).
            Confirmed directly against a real TMDb page (the user's own
            screenshot) that TMDb shows this sharp, just darkened under a
            gradient -- no blur -- because a same-aspect cover-crop is a
            normal, unremarkable crop, not a zoom. Handled the same way
            here: cover-crop + darken only.
          - False: no TMDb backdrop was available, so src_path is the
            already-downloaded poster instead -- a tall portrait image
            (e.g. 375x555). Cover-cropping a portrait image into a wide
            landscape box has to zoom in hard to cover the width, which
            is exactly what was confirmed to read as an ugly "stretched/
            extended" zoom on a real device photo rather than a soft
            ambient blur. The downscale/blur/upscale trick below (shrink
            the already-cropped frame way down, blur there, then scale
            back up) erases that recognizable warped content into smooth
            color instead, and is also far cheaper on embedded hardware
            than a large-radius blur on the full 1830x600 frame.

        is_tmp mirrors the source's own caching: when src_path is a
        permanent file under the shared poster cache dir, the derived
        backdrop file is kept there too so it is reused on the next open
        instead of being regenerated every time; when src_path is itself
        a one-off temp file (no cache dir available), the derived file is
        tracked in self._tmp_posters for cleanup alongside it.
        """
        try:
            from PIL import Image, ImageFilter, ImageEnhance
        except ImportError:
            return
        try:
            if not src_path or not os.path.exists(src_path):
                return
            try:
                w = self["backdropImage"].instance.size().width()
                h = self["backdropImage"].instance.size().height()
            except Exception:
                w, h = 1830, 600
            if getattr(self, "_modern", False):
                # [accel] the box has ~5.4 MB of graphics-accelerator memory; a
                # 1920x690 picture (5.3 MB) never fits, so it was drawn by the
                # CPU. Made at 1280x460 (2.4 MB) and scaled up by the widget.
                w, h = 1280, 460

            img = Image.open(src_path).convert("RGB")
            # [FIX backdrop] trust the picture, not the caller: a portrait
            # image handed in as a "backdrop" was cover-cropped hard and
            # stretched; it now gets the blurred-poster treatment instead
            if is_landscape and img.size[0] < img.size[1] * 1.2:
                is_landscape = False

            if getattr(self, "_modern", False):
                fitted = _modern_backdrop(img, w, h, is_landscape)
            elif is_landscape and w < 1000:
                # [panel] small backdrop card in the right column: a plain
                # cover crop of the (same-aspect) TMDb photo, lightly dimmed
                try:
                    from PIL import ImageOps
                    fitted = ImageOps.fit(img, (w, h), Image.LANCZOS)
                except Exception:
                    fitted = img.resize((w, h))
                try:
                    fitted = ImageEnhance.Brightness(fitted).enhance(0.9)
                except Exception:
                    pass
            elif is_landscape:
                # [FIX] confirmed against the user's own reference crop of
                # this exact backdrop image: TMDb's photo is a roughly
                # 16:9 frame, but this panel is an unusually wide ~3:1 box
                # (1830x600). Cover-cropping (the old ImageOps.fit above)
                # scales the image up until it fully covers that 3:1 box,
                # which blows the 16:9 photo up far past its own frame and
                # crops hard on every side -- that's what was zooming into
                # a tight, cut-off close-up instead of showing her full
                # figure. Switched to a contain-fit instead: the WHOLE
                # photo is scaled down to fit inside the box with nothing
                # cropped off, anchored to the panel's RIGHT edge (vertically
                # centered) so it reads the same as the reference -- full
                # figure, positioned right. The leftover space on the left,
                # which is also where the title/meta/plot text sits, is
                # filled with the panel's own flat background color
                # instead of stretched or cropped image content.
                img_w, img_h = img.size
                base_scale = min(w / float(img_w), h / float(img_h))
                # [FIX] pure contain-fit (boost=1.0) left a big flat gap
                # between the poster and the photo. _BACKDROP_WIDTH_BOOST
                # scales the photo up beyond the plain contain size so it
                # reaches closer to the poster -- the overflow this creates
                # (height almost always, since this box is much wider than
                # the source) is cropped symmetrically top/bottom, and any
                # width overflow is cropped off the LEFT (anchored right,
                # keeping the subject, who sits on that side of frame).
                # This is a mild, controlled version of the cover-crop that
                # was rejected before: that one force-covered the full 3:1
                # box (a ~1.43x jump) and cut hard into the figure; this
                # nudges in by _BACKDROP_WIDTH_BOOST and no more.
                scale = base_scale * _BACKDROP_WIDTH_BOOST
                scaled_w = max(1, int(round(img_w * scale)))
                scaled_h = max(1, int(round(img_h * scale)))
                try:
                    scaled = img.resize((scaled_w, scaled_h), Image.LANCZOS)
                except Exception:
                    scaled = img.resize((scaled_w, scaled_h))
                try:
                    # Light touch-up blur only -- just enough to soften
                    # JPEG block noise, not to obscure the photo. Real
                    # TMDb keeps its backdrop sharp, not foggy.
                    scaled = scaled.filter(ImageFilter.GaussianBlur(radius=2))
                except Exception:
                    pass

                crop_w = min(scaled_w, w)
                crop_h = min(scaled_h, h)
                crop_left = scaled_w - crop_w   # keep the rightmost slice (the subject)
                crop_top = (scaled_h - crop_h) // 2
                if crop_w < scaled_w or crop_h < scaled_h:
                    scaled = scaled.crop((crop_left, crop_top, crop_left + crop_w, crop_top + crop_h))

                fitted = Image.new("RGB", (w, h), (28, 35, 51))  # #1C2333, matches the flat "backdrop" fallback widget
                paste_x = w - crop_w   # flush to the right edge
                paste_y = (h - crop_h) // 2
                fitted.paste(scaled, (paste_x, paste_y))

                try:
                    from PIL import ImageDraw
                    gradient = Image.new("L", (w, h), 0)
                    draw = ImageDraw.Draw(gradient)
                    # Dark out exactly to where the photo actually starts
                    # (paste_x -- the flat letterbox area to its left is
                    # already that same color, so this just carries the
                    # same solid dark seamlessly across the seam), fading
                    # to fully clear over the next ~25% of the panel width
                    # so the text keeps contrast a bit further into the
                    # photo without washing out the whole thing.
                    dark_to = paste_x
                    fade_to = max(dark_to + 1, min(w - 1, dark_to + int(w * 0.25)))
                    for x in range(w):
                        if x <= dark_to:
                            a = 215
                        elif x >= fade_to:
                            a = 0
                        else:
                            t = (x - dark_to) / float(fade_to - dark_to)
                            a = int(215 * (1 - t))
                        draw.line([(x, 0), (x, h)], fill=a)
                    dark_layer = Image.new("RGB", (w, h), (28, 35, 51))
                    fitted = Image.composite(dark_layer, fitted, gradient)
                except Exception:
                    pass
            else:
                try:
                    from PIL import ImageOps
                    fitted = ImageOps.fit(img, (w, h), Image.LANCZOS)
                except Exception:
                    fitted = img.resize((w, h), Image.LANCZOS)
                try:
                    sw, sh = max(1, w // 14), max(1, h // 14)
                    tiny = fitted.resize((sw, sh), Image.BILINEAR)
                    tiny = tiny.filter(ImageFilter.GaussianBlur(radius=4))
                    fitted = tiny.resize((w, h), Image.BILINEAR)
                except Exception:
                    try:
                        fitted = fitted.filter(ImageFilter.GaussianBlur(radius=18))
                    except Exception:
                        pass
                try:
                    fitted = ImageEnhance.Brightness(fitted).enhance(0.45)
                except Exception:
                    pass

            out_path = "{}_bd{}.jpg".format(os.path.splitext(src_path)[0],
                                            "m1280c" if getattr(self, "_modern", False) else ("" if w >= 1000 else "_s"))
            fitted.save(out_path, format="JPEG", quality=82)
            if is_tmp and out_path not in self._tmp_posters:
                self._tmp_posters.append(out_path)

            callInMainThread(self.backdropPicLoad.setPara, (w, h, 1, 1, 0, 1, "#000000"))
            callInMainThread(self.backdropPicLoad.startDecode, out_path)
        except Exception as e:
            my_log("_generateBackdrop error: {}".format(e))

    def _downloadBackdrop(self, url, fallback_poster_url=None):
        """
        [detail-tmdb] Fetches the real TMDb backdrop image (data["fanart"],
        TMDb's backdrop_path served at w1280 -- plugin_tmdb.py already
        resolves this during the TMDb merge, it just wasn't used anywhere
        in this screen before) and hands it to _generateBackdrop as the
        sharp, same-aspect source. Mirrors _downloadPoster's own
        fetch/cache structure exactly (_get_cached_poster/_poster_cache_
        path/_fetch_poster_bytes are all URL-keyed and work for any image
        URL, not just posters), just for a second, independent URL.

        Falls back to the already-downloaded poster (blurred, see
        _generateBackdrop's is_landscape=False branch) only when no TMDb
        backdrop URL is available at all, so the panel still gets some
        imagery instead of staying on the flat color.
        """
        try:
            src_url = url or fallback_poster_url
            if not src_url:
                return
            is_landscape = bool(url)
            src_url = _normalize_poster_url(src_url)
            cached = _get_cached_poster(src_url)
            if cached:
                my_log("_downloadBackdrop (detail): using cached file for {}".format(src_url))
                self._generateBackdrop(cached, is_tmp=False, is_landscape=is_landscape)
                return
            cache_path = _poster_cache_path(src_url)
            from urllib.parse import urlparse as _urlparse
            _p = _urlparse(src_url)
            referer = "{}://{}/".format(_p.scheme, _p.netloc)
            my_log("_downloadBackdrop (detail): fetching {}".format(src_url))
            data = _fetch_poster_bytes(src_url, referer, timeout=10)
            if not data:
                return
            save_path = cache_path or "/tmp/ap_detail_bd_{}.jpg".format(int(time.time()))
            with open(save_path, "wb") as f:
                f.write(data)
            is_tmp = not cache_path
            if is_tmp:
                self._tmp_posters.append(save_path)
            self._generateBackdrop(save_path, is_tmp=is_tmp, is_landscape=is_landscape)
        except Exception as e:
            my_log("_downloadBackdrop error: {} (URL: {})".format(e, url))

    def _onLoaded(self, data):
        if getattr(self, "_closed", False): return
        if not data:
            self["status"].setText("Could not load the page")
            return

        self._data = data
        self._quality_choices = []
        current_title = _strip_arabic_from_english_title(data.get("title") or self._item.get("title", ""))
        self._raw_title = re.sub(r"\s+", " ", current_title).strip()
        # [no-repeat] the year is on the poster badge -- not again after the title
        self._fitTitle(_strip_shown_year(current_title, data.get("year") or self._item.get("year")))
        self._tagline = str((data.get("_tmdb") or {}).get("tagline") or "").strip() \
            if self._episode_index < 0 else ""

        # [detail-tmdb-meta] real TMDb page shows one line right under the
        # title: certification badge + release date + genres + runtime
        # (e.g. "PG-13   07/31/2026 (US)   Science Fiction, Action, and
        # Adventure   2h 25m"). plugin_tmdb.py now resolves all three new
        # pieces (certification, full release_date, runtime) during the
        # TMDb merge -- reusing that instead of the old year/rating/type/
        # quality mix, which showed similar-but-different info (rating is
        # already on the poster's own gold-star badge; type/quality are
        # already implied by the source line below). Falls back to the
        # previous construction whenever TMDb has none of these for this
        # title (TMDb disabled, lookup failed, or site-only metadata).
        _rd = str(data.get("release_date") or "").strip()
        _rd_fmt = ""
        if _rd:
            try:
                _y, _m, _d = _rd.split("-")
                # [FIX detail-tmdb-meta] this used to hardcode "(US)" on
                # every date regardless of which country it actually came
                # from -- plugin_tmdb.py now resolves the real country
                # (EG preferred, then US) alongside the date itself, so
                # show that, and only that: never relabel a date with a
                # country we didn't actually confirm it for.
                _rc = str(data.get("release_country") or "").strip()
                _rd_fmt = "{}/{}/{} ({})".format(_m, _d, _y, _rc) if _rc \
                    else "{}/{}/{}".format(_m, _d, _y)
            except Exception:
                _rd_fmt = _rd
        _rt_fmt = ""
        # [FIX runtime] sites keep their own runtime text ("120 دقيقة",
        # "02:10:00", "2h 10m"); the merge never replaced it with TMDb's
        # minutes, so int() failed silently and no runtime was shown. TMDb's
        # own number first, then whatever the site wrote, parsed.
        # TMDb's runtime goes in the details line; a site-only runtime (TMDb
        # had none) goes in the facts line below instead
        _rt_raw = data.get("runtime")
        _rt_min = _runtime_minutes((data.get("_tmdb") or {}).get("runtime")) or \
            (_runtime_minutes(_rt_raw) if isinstance(_rt_raw, (int, float)) else 0)
        if _rt_min > 0:
            _h, _m2 = divmod(_rt_min, 60)
            _rt_fmt = "{}h {}m".format(_h, _m2) if _h else "{}m".format(_m2)
        _rt_site = ""
        if not _rt_fmt:
            _sm = _runtime_minutes(_rt_raw)
            if _sm > 0:
                _sh, _sm2 = divmod(_sm, 60)
                _rt_site = "{}h {}m".format(_sh, _sm2) if _sh else "{}m".format(_sm2)
        _cert = str(data.get("certification") or "").strip()

        # [FIX] data.get("genres") alone is NOT a reliable TMDb-ran signal
        # -- confirmed at least wecima.py's own scraper sets "genres"
        # directly from the site, with no TMDb lookup involved at all. A
        # condition keyed on genres alone would silently drop the
        # year/rating/type/quality fallback below for every such title,
        # even when none of the three actually-TMDb-only fields
        # (certification/release_date/runtime) resolved. Gate on those
        # three instead; genres only decides whether it's appended once
        # the TMDb-style line is already going to be shown.
        self._rt_min = _rt_min
        if _cert or _rd_fmt or _rt_fmt:
            tmdb_meta = []
            if _rd_fmt:             tmdb_meta.append(_rd_fmt)
            # [lang] TMDb's own genres here (this is the TMDb line), never the site's
            _tg = (data.get("_tmdb") or {}).get("genres") or ""
            if _tg:                 tmdb_meta.append(_tg)
            if _rt_fmt:             tmdb_meta.append(_rt_fmt)
            # [no-repeat] no site name here (logo + "Source" fact show it)
            # [hdr-ui] the age rating is its own outlined chip in front
            self._setMetaLine(_cert, "  ·  ".join(tmdb_meta))
        else:
            # [no-repeat] year and rating are on the poster badges already
            meta = []
            if data.get("type") in _TYPE_LABELS: meta.append(_TYPE_LABELS[data["type"]])
            if data.get("quality"): meta.append(data["quality"])          # [PATCH 111] print type e.g. HDCAM
            if data.get("genres"):  meta.append(data["genres"])
            self._setMetaLine("", "  ·  ".join(meta))
        counts = []
        _nav_items = [e for e in data.get("items", []) if e.get("type") in ("episode", "series", "season")]
        has_episodes = bool(_nav_items)
        has_servers = bool([s for s in data.get("servers", []) if s.get("url")])
        is_series_item = (
            data.get("type") in ("series", "show")
            or self._item.get("type") in ("series", "show")
            or has_episodes
        )
        _modern = getattr(self, "_modern", False)
        if has_episodes:
            _nav_label = "Seasons" if all(e.get("type") in ("series", "season") for e in _nav_items) else "Episodes"
            counts.append(("{} " + _nav_label.lower()) .format(len(_nav_items)) if _modern
                          else "{}: {}".format(_nav_label, len(_nav_items)))
        elif not _modern:
            # (Modern: the server count is on the server band already)
            counts.append("Servers: {}".format(len([s for s in data.get("servers", []) if s.get("url")])))
        # [no-repeat] Year is on the poster badge, Runtime in the details line
        # when TMDb gave it -- the facts only keep what is shown nowhere else
        if data.get("country"):                                       # [PATCH 111]
            counts.append(data["country"] if _modern else "Country: {}".format(data.get("country")))
        if _rt_site:
            counts.append(_rt_site if _modern else "Runtime: {}".format(_rt_site))
        if data.get("channel"):
            counts.append(data["channel"] if _modern else "Channel: {}".format(data.get("channel")))
        if _modern:
            # [facts-line] one line under the details: episodes / seasons · country · channel · site-only runtime
            # (the site is the logo top-left; servers are counted on the band)
            self["source"].setText(_single_line_text(u"  \u00b7  ".join(
                str(c) for c in counts if c), width=90, fallback=""))
        else:
            self["source"].setText("\n".join(["Source: {}".format(_site_label(self._site))] + counts))
        if has_episodes:
            plot_label = "Series plot"
        elif has_servers:
            plot_label = "Movie plot"
        elif is_series_item:
            plot_label = "Series plot"
        else:
            plot_label = "Movie plot"
        if current_title:
            plot_label = "{}: {}".format(plot_label, current_title[:32])
        self["plot_title"].setText(_single_line_text(plot_label.split(":")[0].upper(), width=30, fallback="PLOT"))
        self._layoutHeader()

        plot_text, plot_source = _pick_plot_text_with_source(data, self._item)
        # [lang] English film with an English TMDb plot: use it, not a longer
        # Arabic site text (the picker just takes the longest)
        try:
            from plugin_tmdb import _mostly_arabic
            _tm = data.get("_tmdb") or {}
            if (_tm.get("plot") and str(_tm.get("orig_lang") or "") not in ("", "ar")
                    and _mostly_arabic(plot_text) and not _mostly_arabic(_tm["plot"])):
                plot_text, plot_source = _tm["plot"], "tmdb.plot(en)"
        except Exception:
            pass
        plot_text = re.sub(r"^\[.*?\]\s*|^(?:المصدر|Source):\s*.*?\|\s*", "", plot_text)
        _MID_SITES = ("EgyDead", "Wecima", "Akoam", "ArabSeed", "TopCinema", "TopCinemaa", "FaselHD", "Shaheed", "Shaheed4u")
        for _ms in _MID_SITES:
            plot_text = re.sub(r"\s*[|\-]\s*" + re.escape(_ms) + r"[^\u0600-\u06ff\n]{0,25}", " ", plot_text, flags=re.I)
            plot_text = re.sub(r"\u0639\u0644\u0649\s+\u0645\u0648\u0642\u0639\s+" + re.escape(_ms) + r"[^\u0600-\u06ff\n]{0,30}", " ", plot_text, flags=re.I)
        plot_text = re.sub(r"  +", " ", plot_text).strip()
        my_log("Detail plot source: {} | len={}".format(plot_source, len(plot_text)))

        _pt = (plot_text or "").strip()
        _cap = 520 if getattr(self, "_modern", False) else 500    # Modern: ~4 wide lines under the cast
        if len(_pt) > _cap:
            _pt = _pt[:_cap].rsplit(" ", 1)[0] + "…"
        _ar_count = sum(1 for _c in _pt[:80] if "\u0600" <= _c <= "\u06ff")
        if _ar_count > int(len(_pt[:80]) * 0.3):
            _pt = "\u200f" + _pt
        self["plot"].setText(_pt)
        try:
            self._layoutHeader()      # [plot-low] place the plot for its length
            self._paintPosterStatus()
        except Exception as e:
            my_log("plot layout: {}".format(e))

        _title_key = (getattr(self, "_item", None) or {}).get("url", "")
        self._servers = _sort_servers([s for s in data.get("servers", []) if s.get("url")], title_key=_title_key)
        self._initTorrentFilter()
        self._episodes = [e for e in data.get("items", []) if e.get("type") in ("episode", "series", "season")]
        _chain_poster = data.get("poster") or self._item.get("poster") or ""
        if _chain_poster:
            for _ep in self._episodes:
                if not _ep.get("poster"):
                    _ep["poster"] = _chain_poster

        my_log("Detail _onLoaded: servers={}, items={}".format(len(self._servers), len(self._episodes)))

        item_type = data.get("type") or self._item.get("type")
        episode_has_servers = (item_type == "episode" and self._servers)

        # [detail-stagger] menu population is now progressive
        if episode_has_servers:
            self._setServerSection()
            items = [self._format_server_item(s) for s in self._servers]
            self._setMenuStaggered(items)
            self["status"].setText(self._status_hint("Choose a server — OK"))
        elif self._episodes:
            _all_seasons = all(e.get("type") in ("series", "season") for e in self._episodes)
            _list_label = "Available seasons" if _all_seasons else "Available episodes"
            _pick_hint = "Choose a season" if _all_seasons else "Choose an episode"
            _ok_hint = "Choose a season — OK" if _all_seasons else "Choose an episode — OK"
            self._setSection(_single_line_text("{}: {}  |  {}".format(_list_label, len(self._episodes), _pick_hint), width=90))
            items = [self._format_episode_item(ep) for ep in self._episodes]
            self._setMenuStaggered(items)
            self["status"].setText(self._status_hint(_ok_hint))
        elif self._servers:
            self._setServerSection()
            items = [self._format_server_item(s) for s in self._servers]
            self._setMenuStaggered(items)
            self["status"].setText(self._status_hint("Choose a server — OK"))
        elif is_series_item:
            self._setSection("Available episodes: 0")
            self._setMenu([("No episodes available", "", "", "")])
            self["status"].setText("No episodes")
        else:
            self._setSection("Available servers: 0")
            self._setMenu([("No servers available", "", "", "")])
            self["status"].setText("No servers")

        self._downloads = data.get("downloads") or []
        if self._downloads:
            self["key_blue"].setText("Download ({})".format(len(self._downloads)))
        else:
            self["key_blue"].setText("")

        # [detail-trailer] only shown once TMDb has actually matched this
        # title (data["_tmdb"]["tmdb_id"]/["tmdb_kind"], set by
        # _merge_tmdb_data) -- without a TMDb id there is no /videos
        # endpoint to query, so the green key stays blank/inert rather
        # than offering a trailer that can never resolve.
        self._tmdb_id = (data.get("_tmdb") or {}).get("tmdb_id")
        self._tmdb_kind = (data.get("_tmdb") or {}).get("tmdb_kind")
        if getattr(self, "_modern", False) and self._tmdb_id:
            self._loadCast(data.get("_tmdb") or {})
        # [trailers] always offered: without a TMDb match NovaPlay searches
        # YouTube for the title instead
        self["key_green"].setText("Trailer")
        self._syncKeyDots()

        try:
            _r = str(data.get("rating") or self._item.get("rating") or "").strip()
            if _r and float(_r) > 0:
                self["posterRating"].setText(u"★ " + _r[:3])
                try:
                    from skin import parseColor
                    _v = float(_r)
                    _rc = "#30C48D" if _v >= 7 else ("#F2C230" if _v >= 5 else "#A3ADBB")
                    self["posterRating"].instance.setForegroundColor(parseColor(_rc))
                    self["posterRating"].instance.invalidate()
                except Exception:
                    pass
                self["posterRating"].show()
            else:
                self["posterRating"].hide()
        except Exception:
            try: self["posterRating"].hide()
            except Exception: pass
        try:
            _y = str(data.get("year") or self._item.get("year") or "").strip()[:4]
            if _y:
                self["posterYear"].setText(_y)
                self["posterYear"].show()
            else:
                self["posterYear"].hide()
        except Exception:
            try: self["posterYear"].hide()
            except Exception: pass

        poster_url = data.get("poster") or self._item.get("poster", "")
        if poster_url:
            threading.Thread(target=self._downloadPoster, args=(poster_url,), daemon=True).start()
            self._osd_poster = poster_url

        # [detail-tmdb] real TMDb backdrop (data["fanart"], already resolved
        # by plugin_tmdb.py's merge -- backdrop_path served at w1280 -- but
        # unused anywhere in this screen until now). Falls back to the
        # poster (blurred, since it's portrait) only when no TMDb backdrop
        # URL came back at all.
        # [FIX backdrop] TMDb's wide backdrop first: a site's own "fanart"
        # (often a small or portrait image) used to win over it
        backdrop_url = (data.get("_tmdb") or {}).get("backdrop_url") or data.get("fanart") or ""
        if backdrop_url or poster_url:
            threading.Thread(target=self._downloadBackdrop, args=(backdrop_url, poster_url), daemon=True).start()

        if self._resume_direct and self._servers and getattr(self, "_auto_server_idx", None) is None:
            try:
                from plugin_state import _last_server
                _ln, _lu = _last_server(self._item.get("url", ""))
                _idx = next((i for i, s in enumerate(self._servers) if _lu and s.get("url") == _lu), None)
                if _idx is None:
                    _idx = next((i for i, s in enumerate(self._servers) if _ln and s.get("name") == _ln), None)
                if _idx is not None:
                    self._auto_server_idx = _idx
                    self["status"].setText(u"Resuming with {}…".format(self._servers[_idx].get("name", "")))
                else:
                    self._resume_direct = False
                    self["status"].setText(self._status_hint("Choose a server to resume — OK"))
            except Exception as e:
                my_log("resume direct: {}".format(e))
                self._resume_direct = False
        if getattr(self, "_auto_server_idx", None) is not None and self._servers:
            _asi = self._auto_server_idx
            self._auto_server_idx = None
            if 0 <= _asi < len(self._servers):
                try:
                    self._auto_menu_timer = eTimer()
                    self._auto_menu_timer.callback.append(lambda i=_asi: self._autoStartServer(i))
                    self._auto_menu_timer.start(1500, True)
                except Exception:
                    pass

    def _status_hint(self, prefix):
        fav_state = "Saved" if _is_favorite(self._item.get("url")) else "Not saved"
        tmdb_state = "TMDb On" if _tmdb_enabled() else "TMDb Off"
        return "{}  |  {}  |  {}".format(prefix, fav_state, tmdb_state)

    def _refreshPoster(self):
        if getattr(self, "_poster_loaded", False):
            try:
                self["poster"].show()
            except Exception: pass
            return
        poster_url = None
        if self._data and self._data.get("poster"):
            poster_url = self._data["poster"]
        elif self._item.get("poster"):
            poster_url = self._item["poster"]
        if poster_url:
            threading.Thread(target=self._downloadPoster, args=(poster_url,), daemon=True).start()
        else:
            ph = placeholder_for_item(self._item)
            if ph:
                def _paint_ph():
                    try:
                        self["poster"].instance.setScale(1)
                        self["poster"].instance.setPixmapFromFile(ph)
                        self["poster"].show()
                    except Exception:
                        self["poster"].hide()
                callInMainThread(_paint_ph)
            else:
                callInMainThread(self["poster"].hide)

    def _downloadPoster(self, url):
        try:
            if not url: return
            url = _normalize_poster_url(url)
            import urllib.request as _ur
            try:
                w = self["poster"].instance.size().width()
                h = self["poster"].instance.size().height()
            except Exception:
                w, h = 375, 555
            cached = _get_cached_poster(url)
            if cached:
                my_log("_downloadPoster (detail): using cached file for {}".format(url))
                callInMainThread(self.picLoad.setPara, (w, h, 1, 1, 0, 1, "#000000"))
                callInMainThread(self.picLoad.startDecode, cached)
                return
            cache_path = _poster_cache_path(url)
            from urllib.parse import urlparse as _urlparse
            _p = _urlparse(url)
            referer = "{}://{}/".format(_p.scheme, _p.netloc)
            my_log("_downloadPoster (detail): fetching {}".format(url))
            data = _fetch_poster_bytes(url, referer, timeout=10)
            my_log("_downloadPoster (detail): downloaded {} bytes for {}".format(len(data) if data else 0, url))
            save_path = cache_path or "/tmp/ap_detail_{}.jpg".format(int(time.time()))
            with open(save_path, "wb") as f:
                f.write(data)
            if not cache_path:
                self._tmp_posters.append(save_path)
            my_log("_downloadPoster (detail): saved to {}, handing to picLoad".format(save_path))
            callInMainThread(self.picLoad.setPara, (w, h, 1, 1, 0, 1, "#000000"))
            callInMainThread(self.picLoad.startDecode, save_path)
        except Exception as e:
            my_log("_downloadPoster error: {} (URL: {})".format(e, url))
