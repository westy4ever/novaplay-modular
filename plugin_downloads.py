# -*- coding: utf-8 -*-
"""
NovaPlay Media Center - download engine
=========================================
Streams a resolved video URL (direct .mp4 or HLS .m3u8) to local storage
in chunks, WITHOUT ever loading the whole file into memory.

CONSOLIDATED FILE — contains every amendment from the whole port
(pipe-guard, encrypted-HLS refusal, fMP4 init segment, segment retries,
.part cleanup, item_url, subtitle sidecar).

Changes in this revision (audit fixes):
  * _write_stream() buffers each HLS segment in memory and only appends
    to the output file after the segment downloaded completely. The old
    version streamed into the shared file, so a retry after a partial
    segment failure spliced a duplicated partial segment into the
    output — corrupting the concatenation at that timestamp. (Freak
    >64MB "segments" fall back to direct streaming without retry.)
  * download_direct() sniffs the first chunk: an expired/intermediate
    link returns an HTML page, which the old code happily saved as
    video.mp4 ("downloads fine, never plays").
  * _resolve_hls_media_playlist() validates #EXTM3U before parsing —
    an HTML error page used to fall through as a "playlist".
  * download_hls() refuses live streams (no #EXT-X-ENDLIST) instead of
    "successfully" saving a truncated live window.
  * fMP4 playlists (#EXT-X-MAP) now switch the destination to .mp4 —
    concatenated fMP4 in a .ts container won't play in most players.
  * Cookie forwarding: DownloadTask carries a cookie; the pipe-guard
    extracts Referer AND Cookie from piped URLs (Cookie used to be
    silently dropped, so cookie-gated CDNs 403'd or returned HTML).

Two download strategies:
  - download_direct(): plain chunked HTTP GET -> disk, for direct files.
  - download_hls():   fetches the .m3u8 (resolving a master playlist to
    its best variant if needed), then downloads every .ts segment in
    order and concatenates them into one .ts file.

download_manager() picks the strategy and is the only entry point
plugin.py needs to call.
"""

from __future__ import absolute_import, print_function

import os
import io
import re
import time
import shutil
import threading
import urllib.request as urllib_request
import urllib.error

try:
    from urllib.parse import urlparse, urljoin
except ImportError:
    from urlparse import urlparse, urljoin

try:
    from extractors.base import log as _log
except Exception:
    def _log(msg):
        try:
            with open("/tmp/arabicplayer.log", "a") as f:
                f.write("[downloads] {}\n".format(msg))
        except Exception:
            pass

try:
    from plugin_state import _get_config
except Exception:
    def _get_config(key, default=""):
        return default

SAFE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
CHUNK_SIZE = 256 * 1024  # keeps RAM flat regardless of file size


def download_dir():
    d = (_get_config("download_dir", "") or "").strip()
    if not d:
        d = "/media/hdd/NovaPlay/downloads" if os.path.exists("/media/hdd") else "/tmp/NovaPlay/downloads"
    try:
        if not os.path.exists(d):
            os.makedirs(d)
    except Exception:
        pass
    return d


def safe_filename(title, ext):
    name = re.sub(r'[\\/:*?"<>|]+', '_', (title or "video").strip())
    name = re.sub(r'\s+', ' ', name).strip(" .")[:120] or "video"
    return "{}.{}".format(name, ext.lstrip("."))


class DownloadCancelled(Exception):
    pass


class DownloadTask(object):
    """Tracks one in-progress download; cancel_flag lets the UI stop it."""

    def __init__(self, title, url, referer="", item_url="", cookie=""):
        self.title = title
        self.url = url
        self.referer = referer
        self.item_url = item_url      # detail-page URL (library/resume key)
        self.cookie = cookie          # scraper-earned cookie (from pipe or caller)
        self.cancel_flag = threading.Event()
        self.status = "queued"        # queued|downloading|done|error|cancelled
        self.error = ""
        self.dest_path = ""
        self.bytes_done = 0
        self.bytes_total = 0
        self.started_at = 0

    def cancel(self):
        self.cancel_flag.set()

    def progress_pct(self):
        if self.bytes_total > 0:
            return min(100.0, (self.bytes_done * 100.0) / self.bytes_total)
        return 0.0


def _open_request(url, referer="", cookie=""):
    headers = {
        "User-Agent": SAFE_UA,
        "Referer": referer or url,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib_request.Request(url, headers=headers)
    return urllib_request.urlopen(req, timeout=20)


def _remove_part(path):
    try:
        if os.path.exists(path + ".part"):
            os.remove(path + ".part")
    except Exception:
        pass


def download_direct(task, dest_path):
    """Chunked GET -> disk for a direct video file (.mp4/.mkv/.avi/...)."""
    tmp_path = dest_path + ".part"
    resp = _open_request(task.url, task.referer, task.cookie)
    try:
        total = None
        try:
            total = resp.headers.get("Content-Length")
        except Exception:
            total = None
        if total is None:
            try:
                total = resp.info().get("Content-Length")
            except Exception:
                total = None
        task.bytes_total = int(total) if total else 0
        task.status = "downloading"
        try:
            with open(tmp_path, "wb") as f:
                first = True
                while True:
                    if task.cancel_flag.is_set():
                        raise DownloadCancelled()
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if first:
                        first = False
                        # An expired/intermediate link returns an HTML
                        # page — detect it BEFORE writing MBs of garbage
                        # that "downloads fine" but never plays.
                        ctype = ""
                        try:
                            ctype = (resp.headers.get("Content-Type") or "").lower()
                        except Exception:
                            pass
                        head = chunk[:512].lstrip().lower()
                        if "text/html" in ctype or head.startswith(b"<!doctype") or head.startswith(b"<html"):
                            raise Exception("الرابط أعاد صفحة HTML وليس ملف فيديو — انتهت صلاحية الرابط، أعد المحاولة")
                    f.write(chunk)
                    task.bytes_done += len(chunk)
        except Exception:
            _remove_part(dest_path)
            raise
    finally:
        try:
            resp.close()
        except Exception:
            pass
    os.rename(tmp_path, dest_path)
    return dest_path


_M3U8_URL_RE = re.compile(r'^https?://', re.I)


def _resolve_hls_media_playlist(m3u8_url, referer):
    """If m3u8_url is a master (multi-variant) playlist, pick the
    highest-bandwidth variant and return its media-playlist URL + body;
    otherwise return (m3u8_url, body)."""
    resp = _open_request(m3u8_url, referer)
    body = resp.read().decode("utf-8", "ignore")
    resp.close()
    if not body.lstrip().startswith("#EXTM3U"):
        # An HTML error page is not a playlist — fail loudly instead of
        # letting the segment loop "succeed" with zero segments later.
        raise Exception("Playlist is not a valid M3U8 (probably an HTML error page)")
    if "#EXT-X-STREAM-INF" not in body:
        return m3u8_url, body
    best_bw, best_url = -1, ""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        bw_m = re.search(r'BANDWIDTH=(\d+)', line)
        bw = int(bw_m.group(1)) if bw_m else 0
        target = ""
        for j in range(i + 1, len(lines)):
            cand = lines[j].strip()
            if cand and not cand.startswith("#"):
                target = cand
                break
        if not target:
            continue
        if not _M3U8_URL_RE.match(target):
            target = urljoin(m3u8_url, target)
        if bw >= best_bw:
            best_bw, best_url = bw, target
    if not best_url:
        return m3u8_url, body
    resp2 = _open_request(best_url, referer)
    media_body = resp2.read().decode("utf-8", "ignore")
    resp2.close()
    return best_url, media_body


def download_hls(task, dest_path):
    """Fetch an HLS stream (resolving a master playlist if needed) and
    concatenate all segments into a single playable .ts file."""
    media_url, body = _resolve_hls_media_playlist(task.url, task.referer)

    # Refuse encrypted HLS — AES segments would concatenate into a
    # file that "downloads fine" and then refuses to play.
    for line in body.splitlines():
        if line.startswith("#EXT-X-KEY") and "METHOD=NONE" not in line:
            raise Exception("Encrypted HLS (#EXT-X-KEY) — the downloaded "
                            "file would be unplayable")

    # Refuse live playlists: without #EXT-X-ENDLIST the playlist is a
    # sliding window — "downloading" it saves a truncated recording
    # and reports success.
    if "#EXT-X-ENDLIST" not in body:
        raise Exception("بث مباشر (Live) — لا يمكن تحميله كملف")

    # fMP4 init segment (must be written before the media segments)
    map_uri = ""
    for line in body.splitlines():
        if line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                cand = m.group(1)
                map_uri = cand if _M3U8_URL_RE.match(cand) else urljoin(media_url, cand)
            break

    # fMP4 (EXT-X-MAP) concatenated into a .ts file won't play in most
    # players — switch the destination to .mp4 now that we know.
    if map_uri and dest_path.lower().endswith(".ts"):
        dest_path = dest_path[:-3] + ".mp4"
        task.dest_path = dest_path

    segments = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        seg_url = line if _M3U8_URL_RE.match(line) else urljoin(media_url, line)
        segments.append(seg_url)

    if not segments and not map_uri:
        raise Exception("No HLS segments found in playlist")

    task.bytes_total = 0  # segment sizes aren't known up-front for HLS
    task.status = "downloading"
    tmp_path = dest_path + ".part"

    def _write_stream(out, url, label):
        """Download one URL fully into memory, THEN append to `out`.

        Retrying after a partial write would splice a duplicated partial
        segment into the output — corrupting the concatenation — so a
        segment only reaches disk after downloading completely. Segments
        are typically 2-10 MB; a freakishly large "segment" (>64 MB)
        falls back to direct streaming (retry disabled for it). Progress
        counts only committed bytes."""
        last_exc = None
        for attempt in (1, 2, 3):
            if task.cancel_flag.is_set():
                raise DownloadCancelled()
            try:
                buf = io.BytesIO()
                resp = _open_request(url, task.referer, task.cookie)
                got = 0
                try:
                    while True:
                        if task.cancel_flag.is_set():
                            raise DownloadCancelled()
                        chunk = resp.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        buf.write(chunk)
                        got += len(chunk)
                        if got > 64 * 1024 * 1024:
                            # whole-file "segment": stream the rest
                            # directly, no retry possible from here
                            out.write(buf.getvalue())
                            while True:
                                chunk = resp.read(CHUNK_SIZE)
                                if not chunk:
                                    break
                                out.write(chunk)
                                got += len(chunk)
                            task.bytes_done += got
                            return
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
                out.write(buf.getvalue())
                task.bytes_done += got
                return
            except DownloadCancelled:
                raise
            except Exception as e:
                last_exc = e
                _log("download_hls: {} attempt {} failed: {}".format(
                    label, attempt, e))
                time.sleep(1.0)
        raise last_exc

    try:
        with open(tmp_path, "wb") as out:
            if map_uri:
                _write_stream(out, map_uri, "init segment")
            for idx, seg_url in enumerate(segments):
                _write_stream(out, seg_url, "segment {}".format(idx + 1))
    except Exception:
        _remove_part(dest_path)
        raise
    os.rename(tmp_path, dest_path)
    return dest_path


# ──────────────────────────────────────────────────────────────────────────
# Host-specific DOWNLOAD page flows
# ──────────────────────────────────────────────────────────────────────────

def resolve_download_link(url, referer=""):
    """
    Given a wecima 'Download--Wecima--Single' entry URL (already decoded
    from base64 by the extractor), follow that specific host's DOWNLOAD
    page flow - which is often completely different from its STREAMING
    embed flow for the exact same host - and return the final,
    directly-downloadable file URL (a raw .mp4/.mkv, or an .m3u8 HLS URL
    which download_manager's HLS strategy will handle).
    """
    try:
        from urllib.parse import urlparse, urljoin
    except ImportError:
        from urlparse import urlparse, urljoin
    import re as _re
    try:
        from extractors.base import fetch as _fetch, resolve_host as _resolve_host
    except Exception:
        return None

    domain = urlparse(url).netloc.lower()

    # ── DoodStream family ──
    if any(k in domain for k in ("dood", "playmogo", "d0o0d", "dsvplay", "doods", "ds2play", "dooood")):
        html, final_url = _fetch(url, referer=referer or url)
        if html:
            m = _re.search(r'href="(/download/[^"]+)"', html, _re.I)
            if m:
                base_url = final_url or url
                base = "{}://{}".format(urlparse(base_url).scheme, urlparse(base_url).netloc)
                return urljoin(base, m.group(1))
        return None

    # ── savefiles.com / abstream.to ──
    if "savefiles.com" in domain or "abstream.to" in domain:
        html, _ = _fetch(url, referer=referer or url)
        if html:
            m = _re.search(r'href="(https?://[^"]+\.(?:mp4|mkv|m3u8)[^"]*)"', html, _re.I)
            if m:
                return m.group(1)
        return None

    # ── MixDrop / MiixDrop ──
    if "mixdrop" in domain or "miixdrop" in domain:
        dl_url = url if url.endswith("?download") else url.rstrip("/") + "?download"
        html, final_url = _fetch(dl_url, referer=referer or url)
        if final_url and final_url != dl_url and any(
            ext in final_url.lower() for ext in (".mp4", ".mkv", ".m3u8")
        ):
            return final_url
        if html:
            m = _re.search(r'(https?://[^\s"\']+mxcontent\.net[^\s"\']+\.mp4[^\s"\']*)', html, _re.I)
            if m:
                return m.group(1)
        return None

    # ── Lulustream / luluvdo.com ──
    if "luluvdo.com" in domain or "lulustream" in domain:
        html, _ = _fetch(url, referer=referer or url)
        if html:
            m = _re.search(r'https?://luluvdo\.com/([A-Za-z0-9]+)(?!["\'/])', html, _re.I)
            if m:
                bare_url = "https://luluvdo.com/{}".format(m.group(1))
                result = _resolve_host(bare_url)
                if result:
                    return result
        return _resolve_host(url)

    # ── byselapuix.com ──
    if "byselapuix.com" in domain:
        html, final_url = _fetch(url, referer=referer or url)
        if html:
            m = _re.search(r'href="(/download/[A-Za-z0-9]+)"', html, _re.I)
            if m:
                base_url = final_url or url
                base = "{}://{}".format(urlparse(base_url).scheme, urlparse(base_url).netloc)
                sub_url = urljoin(base, m.group(1))
                result = _resolve_host(sub_url)
                if result:
                    return result
        return _resolve_host(url)

    # ── vibuxer / hanerix / audinifer / morencius family ──
    if any(k in domain for k in ("vibuxer.com", "hanerix.com", "audinifer.com", "morencius.com")):
        return _resolve_host(url)

    return _resolve_host(url)


# ──────────────────────────────────────────────────────────────────────────
# Subtitle sidecar: save the matched .srt next to the finished video
# ──────────────────────────────────────────────────────────────────────────

def _save_subtitle_sidecar(task, video_path):
    try:
        from novaplay_subtitles import (
            recall_subtitle, find_best_local_subtitle,
            _make_shifted_copy, AUTO_SUB_MIN_SCORE,
        )
    except Exception:
        return
    src, offset = "", 0
    try:
        if getattr(task, "item_url", ""):
            path, offset = recall_subtitle(task.item_url)
            src = path or ""
    except Exception:
        src = ""
    if not src:
        try:
            path, score = find_best_local_subtitle(task.title, "")
            if path and score >= AUTO_SUB_MIN_SCORE:
                src, offset = path, 0
        except Exception:
            src = ""
    if not src or not str(src).lower().endswith(".srt"):
        return
    if not str(video_path).lower().endswith((".mp4", ".mkv", ".avi", ".ts", ".webm")):
        return
    target = _make_shifted_copy(src, offset) if offset else src
    if target and os.path.exists(target):
        side = os.path.splitext(video_path)[0] + ".srt"
        if not os.path.exists(side):
            try:
                shutil.copyfile(target, side)
                _log("sidecar subtitle saved: {}".format(side))
            except Exception as e:
                _log("sidecar copy failed: {}".format(e))


def download_manager(task, title_hint=""):
    """Entry point: pick a strategy based on the resolved URL's
    extension and run it. Returns the final file path or raises."""
    # Pipe-guard: resolvers (streamruby, mixdrop) return piped URLs like
    # "...m3u8|Referer=..." — urlopen can't open a URL containing '|',
    # so strip the pipe and keep the Referer/Cookie context.
    if "|" in task.url:
        task.url, _pipe = task.url.split("|", 1)
        task.url = task.url.strip()
        for _p in _pipe.split("&"):
            if "=" not in _p:
                continue
            _k, _v = _p.split("=", 1)
            _k = _k.strip().lower()
            if _k == "referer" and not task.referer:
                task.referer = _v
            elif _k == "cookie" and not getattr(task, "cookie", ""):
                task.cookie = _v

    lower = task.url.lower().split("?")[0]
    ext = "ts" if ".m3u8" in lower else (lower.rsplit(".", 1)[-1] if "." in lower.rsplit("/", 1)[-1] else "mp4")
    if ext not in ("mp4", "mkv", "avi", "ts", "webm"):
        ext = "mp4"
    dest_path = os.path.join(download_dir(), safe_filename(title_hint or task.title, ext))
    task.dest_path = dest_path
    task.started_at = time.time()
    try:
        if ".m3u8" in lower:
            _log("download_manager: HLS strategy for {}".format(task.url[:100]))
            download_hls(task, dest_path)
        else:
            _log("download_manager: direct strategy for {}".format(task.url[:100]))
            download_direct(task, dest_path)
        task.status = "done"
        try:
            _save_subtitle_sidecar(task, dest_path)
        except Exception as e:
            _log("sidecar subtitle skipped: {}".format(e))
    except DownloadCancelled:
        task.status = "cancelled"
        _remove_part(dest_path)
        raise
    except Exception as e:
        task.status = "error"
        task.error = str(e)
        _remove_part(dest_path)
        _log("download_manager error: {}".format(e))
        raise
    return dest_path