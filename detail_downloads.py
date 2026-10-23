# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- download to HDD: choices, resolve and watch / download tasks.

Part of NovaPlayDetail (plugin_screen_detail.py), split out 2026-10-23
without changing behaviour. Methods use the screen's widgets and state
through self; module-level helpers come from detail_base.
"""
from detail_base import *  # noqa: F401,F403


class DetailDownloads(object):
    def _openDownloads(self):
        if self._active_download_task and self._active_download_task.status == "downloading":
            pct = self._active_download_task.progress_pct()
            msg = "Downloading: {:.0f}%\nCancel?".format(pct) if pct else "Downloading...\nCancel?"
            self.session.openWithCallback(self._onCancelDownloadPrompt, MessageBox,
                                        msg, MessageBox.TYPE_YESNO, timeout=8, default=False)
            return
        if not self._downloads:
            self["status"].setText("No download links for this title")
            return
        entries = []
        for d in self._downloads:
            label = d.get("resolution") or "Unknown quality"
            extra = " | ".join([x for x in (d.get("size"), d.get("quality")) if x])
            if extra:
                label = "{} ({})".format(label, extra)
            entries.append((label, d))
        self.session.openWithCallback(self._onDownloadChoiceMade, ChoiceBox,
                                    title="Choose a quality to download", list=entries)

    def _onCancelDownloadPrompt(self, confirmed):
        if confirmed and self._active_download_task:
            self._active_download_task.cancel()
            self["status"].setText("Download cancelled")

    def _onDownloadChoiceMade(self, choice):
        if not choice:
            return
        entry = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else None
        if not entry:
            return
        self.session.openWithCallback(
            lambda c: self._onDownloadActionChosen(c, entry),
            ChoiceBox,
            title="Watch or download?",
            list=[("▶ Watch now", "watch"), ("⬇ Download to disk", "download")]
        )

    def _onDownloadActionChosen(self, choice, entry):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "watch":
            self["status"].setText("Preparing the link to watch...")
            self["status"].show()
            self._startProgress()          # [detail-progress]
            threading.Thread(target=self._bgResolveAndWatch, args=(entry,), daemon=True).start()
        elif action == "download":
            self["status"].setText("Preparing the download link...")
            self._startProgress()          # [detail-progress]
            threading.Thread(target=self._bgResolveAndDownload, args=(entry,), daemon=True).start()

    def _bgResolveAndWatch(self, entry):
        try:
            from plugin_downloads import resolve_download_link
            resolved_url = resolve_download_link(entry["url"])
            referer = entry["url"]

            if not resolved_url:
                extract_fn = None
                try:
                    extractor = _get_extractor(self._site)
                    extract_fn = getattr(extractor, "extract_stream", None)
                except Exception:
                    extract_fn = None
                if extract_fn is None:
                    from extractors.base import extract_stream as extract_fn

                result = extract_fn(entry["url"])
                resolved_url = result[0] if result else None
                referer = result[2] if result and len(result) >= 3 and result[2] else entry["url"]
                variants = result[3] if result and len(result) >= 4 and result[3] else []
            else:
                variants = []

            if not resolved_url:
                callInMainThread(self._stopProgress)      # [detail-progress]
                callInMainThread(self["status"].setText, "Could not prepare the link — try another quality")
                return

            label = entry.get("resolution") or entry.get("quality") or "HD"
            server = {"name": entry.get("resolution") or "Download link",
                      "url": entry["url"],
                      "type": "embed",
                      "quality": label}
            callInMainThread(self._onStreamFound, resolved_url, label, referer, server, variants)
        except Exception as e:
            my_log("resolve-and-watch error: {}".format(e))
            callInMainThread(self._stopProgress)          # [detail-progress]
            callInMainThread(self["status"].setText, "Could not prepare the link to watch")

    def _bgResolveAndDownload(self, entry):
        try:
            from plugin_downloads import resolve_download_link, DownloadTask, download_manager
            resolved_url = resolve_download_link(entry["url"])
            referer = entry["url"]

            if not resolved_url:
                extract_fn = None
                try:
                    extractor = _get_extractor(self._site)
                    extract_fn = getattr(extractor, "extract_stream", None)
                except Exception:
                    extract_fn = None
                if extract_fn is None:
                    from extractors.base import extract_stream as extract_fn

                result = extract_fn(entry["url"])
                resolved_url = result[0] if result else None
                referer = result[2] if result and len(result) >= 3 and result[2] else entry["url"]

            if not resolved_url:
                callInMainThread(self._stopProgress)      # [detail-progress]
                callInMainThread(self["status"].setText, "Could not prepare the download link — try another quality")
                return

            title_hint = getattr(self, "_raw_title", None) or self._item.get("title", "video")
            task = DownloadTask(title=title_hint, url=resolved_url, referer=referer,
                                item_url=self._item.get("url", ""))
            self._active_download_task = task
            progress_timer_stop = threading.Event()

            def _progress_loop():
                while not progress_timer_stop.is_set():
                    if task.status == "downloading":
                        pct = task.progress_pct()
                        msg = ("Downloading: {:.0f}%".format(pct) if pct
                            else "Downloading: {:.1f} MB".format(task.bytes_done / 1048576.0))
                        callInMainThread(self["status"].setText, msg)
                    elif task.status in ("done", "error", "cancelled"):
                        break
                    time.sleep(1.5)

            threading.Thread(target=_progress_loop, daemon=True).start()

            try:
                callInMainThread(self._stopProgress)      # [detail-progress]
                dest = download_manager(task, title_hint=title_hint)
                progress_timer_stop.set()
                callInMainThread(self["status"].setText, "Saved: {}".format(os.path.basename(dest)))
            except Exception as e:
                progress_timer_stop.set()
                callInMainThread(self._stopProgress)      # [detail-progress]
                if task.status == "cancelled":
                    return
                my_log("Download failed: {}".format(e))
                callInMainThread(self["status"].setText, "Download failed: {}".format(str(e)[:40]))
        except Exception as e:
            my_log("Download setup error: {}".format(e))
            callInMainThread(self._stopProgress)          # [detail-progress]
            callInMainThread(self["status"].setText, "Download error: {}".format(str(e)[:40]))
        finally:
            self._active_download_task = None
