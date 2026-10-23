# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- trailers: TMDb / YouTube lookup, stream or download, playback.

Part of NovaPlayDetail (plugin_screen_detail.py), split out 2026-10-23
without changing behaviour. Methods use the screen's widgets and state
through self; module-level helpers come from detail_base.
"""
from detail_base import *  # noqa: F401,F403


class DetailTrailer(object):
    def _trailerQuery(self):
        """[trailers] YouTube search words: the English title when there is
        one, the year, and 'trailer' (Arabic titles get 'اعلان' too)."""
        data = self._data or {}
        tm = data.get("_tmdb") or {}
        title = (_strip_arabic_from_english_title(self._raw_title or self._item.get("title", ""))
                 or self._raw_title or self._item.get("title", "") or tm.get("title", ""))
        year = str(data.get("year") or self._item.get("year") or "")[:4]
        arabic = any(u"\u0600" <= c <= u"\u06ff" for c in title)
        return u"{} {} {}".format(title, year, u"اعلان trailer" if arabic else "official trailer").strip()

    def _playTrailer(self):
        """
        [detail-trailer] Green-key handler. Confirmed directly on the
        user's own device: python3-yt-dlp is already installed system-
        wide (opkg, /usr/bin/yt-dlp, importable as `yt_dlp`) independent
        of any other plugin, so this resolves trailers itself rather than
        depending on a separate closed-source YouTube plugin's internals
        (which ships its own bundled yt-dlp copy, but is a stripped ARM
        binary with no inspectable Python API from outside it).
        """
        if getattr(self, "_closed", False):
            return
        if getattr(self, "_trailer_busy", False):
            return
        self._trailer_busy = True
        self["status"].setText("Preparing the trailer...")
        self["status"].show()
        self._startProgress()          # [detail-progress]
        threading.Thread(target=self._bgResolveTrailer, daemon=True).start()

    def _bgResolveTrailer(self):
        try:
            try:
                import yt_dlp
            except ImportError:
                callInMainThread(self._onTrailerFailed, "yt-dlp is not installed on the box")
                return
            youtube_url = ""
            if getattr(self, "_tmdb_id", None):
                from plugin_tmdb import _tmdb_fetch_trailer_url
                youtube_url = _tmdb_fetch_trailer_url(self._tmdb_kind, self._tmdb_id)
            if getattr(self, "_closed", False):
                return
            if not youtube_url:
                # [trailers] TMDb has none (or no TMDb match): search YouTube
                callInMainThread(self["status"].setText, "Searching YouTube for the trailer...")
                youtube_url = _youtube_search_trailer(yt_dlp, self._trailerQuery())
            if getattr(self, "_closed", False):
                return
            if not youtube_url:
                callInMainThread(self._onTrailerFailed, "No trailer found for this title")
                return
            # cache key: the video id (YouTube) or vimeo_<id>
            _m = re.search(r"[?&]v=([\w-]+)", youtube_url) or re.search(r"vimeo\.com/(\d+)", youtube_url)
            key = (("vimeo_" if "vimeo" in youtube_url else "") + _m.group(1)) if _m else \
                re.sub(r"\W+", "_", youtube_url)[-40:]

            # [FIX] confirmed directly on a real device, across two
            # different trailers: requiring a single progressive (audio+
            # video together) format still fails with "Requested format
            # is not available" -- not a one-off, but a title genuinely
            # having NO muxed format at all, which is increasingly common
            # as YouTube phases those out. A direct-URL hand-off to the
            # player can never work for those, whatever format selector is
            # used, since there is no single URL that carries both.
            #
            # ffmpeg is confirmed installed on this device (8.0, opkg),
            # so instead of asking for a progressive format, this
            # downloads bestvideo+bestaudio and lets yt-dlp mux them via
            # ffmpeg into one local mp4, then plays that local file --
            # which works whether or not a muxed format ever existed.
            # Keyed by video id under _trailer_cache_dir() so a repeat
            # view of the same trailer is instant, not a re-download.
            trailer_dir = _trailer_cache_dir()
            cache_key = "{}_v{}".format(key, _TRAILER_CACHE_VERSION)
            local_path = os.path.join(trailer_dir, "{}.mp4".format(cache_key))
            # [trailer-stream] Settings > Trailer = "Stream first": when the
            # video still has ONE combined H.264 + AAC format (usually 360p)
            # play its URL straight away instead of downloading; otherwise
            # (or with "Download") fall through to the download below.
            # A trailer already downloaded earlier always plays from disk.
            if (not os.path.exists(local_path)
                    and str(_get_config("trailer_mode", "stream") or "stream") == "stream"):
                callInMainThread(self["status"].setText, "Opening the trailer...")
                stream = _trailer_stream_url(yt_dlp, youtube_url)
                if getattr(self, "_closed", False):
                    return
                if stream:
                    trailer_title = "{} - Trailer".format(self._raw_title or self._item.get("title", ""))
                    callInMainThread(self._onTrailerResolved, stream, trailer_title)
                    return
                my_log("trailer: no combined format to stream, downloading instead")

            if not os.path.exists(local_path):
                callInMainThread(self["status"].setText, "Downloading the trailer...")
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                    # avc1(H.264)/mp4a(AAC) kept at every rung except the
                    # final no-filter fallback -- see _TRAILER_CACHE_VERSION
                    # comment above for why.
                    "format": (
                        "bestvideo[vcodec^=avc1][height<={h}][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]"
                        "/bestvideo[vcodec^=avc1][height<={h}]+bestaudio[acodec^=mp4a]"
                        "/best[vcodec^=avc1][height<={h}][ext=mp4]"
                        "/bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]"
                        "/best[vcodec^=avc1]"
                        "/best[height<={h}][ext=mp4]"
                        "/best[height<={h}]"
                        "/best"
                    ).format(h=_TRAILER_MAX_HEIGHT),
                    "merge_output_format": "mp4",
                    "outtmpl": os.path.join(trailer_dir, "{}.%(ext)s".format(cache_key)),
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([youtube_url])
                _evict_old_trailers(trailer_dir)

            if getattr(self, "_closed", False):
                return
            if not os.path.exists(local_path):
                # [FIX] genuinely nothing downloadable/mergeable at all
                # (rare once ffmpeg handles the split-stream case, but
                # still possible -- e.g. a region-restricted or removed
                # video). Distinct message from the generic failure below
                # so it's clear this isn't a transient error.
                callInMainThread(self._onTrailerFailed, "Could not download the trailer for this title")
                return

            trailer_title = "{} - Trailer".format(self._raw_title or self._item.get("title", ""))
            callInMainThread(self._onTrailerResolved, local_path, trailer_title)
        except Exception as e:
            my_log("_bgResolveTrailer error: {}".format(e))
            if not getattr(self, "_closed", False):
                callInMainThread(self._onTrailerFailed, "Error while preparing the trailer")
        finally:
            self._trailer_busy = False

    def _onTrailerResolved(self, local_path, trailer_title):
        self._stopProgress()           # [detail-progress]
        if getattr(self, "_closed", False):
            return
        self["status"].setText("Opening the trailer...")
        try:
            # [FIX] switched to the app's own NovaPlaySimplePlayer
            # instead of either _play() or the stock Screens.InfoBar.MoviePlayer
            # tried before. Two real-device-confirmed problems with the stock
            # player are just how it's built, not something fixable from the
            # outside: on EOF it defers to the box's global "After a Movie"
            # setting (which opened the movie-list browser instead of closing,
            # even after forcing that setting to "stop" for the duration --
            # it didn't take effect on this device/image), and its exit key
            # goes through the SAME near-end/leave-player logic regardless of
            # what's playing. NovaPlaySimplePlayer's own _pl_onEOF
            # (used for every other stream in this app already) just calls
            # _pl_onExit() and closes -- no movielist, no global setting
            # involved -- and _pl_onCancelKey does the same unless playback is
            # past ~90% elapsed, which a quick trailer exit never reaches.
            # previous_service=None and item_url="" mean there's nothing to
            # restore and nothing to save/resume on close, so closing this
            # screen (by EOF or by exit) just reveals the detail screen
            # underneath, same as any other screen on the stack.
            if local_path.startswith("http"):
                # [trailer-stream] a YouTube URL: the usual remote candidates
                from plugin_screen_player import _build_remote_play_candidates
                candidates = _build_remote_play_candidates(local_path)
            else:
                candidates = [(4097, local_path, u"local trailer", False)]
            # [FIX exit freeze] remember the TV channel so closing the trailer
            # brings it back (stop + forceRestart, like the films) instead of
            # leaving the trailer's last frame frozen on screen
            from plugin_screen_player import _capture_previous_service
            self.session.open(NovaPlaySimplePlayer, trailer_title, candidates,
                               previous_service=_capture_previous_service(self.session),
                               resume_pos=0, item_url="",
                               next_episode=None, on_next=None,
                               poster_url=self._osd_poster, quality_variants=None,
                               server_url="", is_preview=True)
            try:
                self["status"].setText("")
                self["status"].hide()
            except Exception:
                pass
        except Exception as e:
            my_log("_onTrailerResolved: open player failed: {}".format(e))
            self["status"].setText("Error opening the trailer: {}".format(str(e)[:60]))

    def _onTrailerFailed(self, message):
        self._stopProgress()           # [detail-progress]
        if getattr(self, "_closed", False):
            return
        self["status"].setText(message)
