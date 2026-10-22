# -*- coding: utf-8 -*-
"""NovaPlay — Detail screen (servers / episodes / qualities / chain).
MODULAR EXTRACTION of NovaPlayDetail with:
  * episode chain + auto-server (next-episode support, _playNextEpisode)
  * health hooks on load/extract
  * quality-choices kept populated (variants → Back → servers → grid)
  * watchdog race fix (_done re-check in the timeout callback)
  * OSD poster seeding (P1/P2) for the player
  * magnet prompt + TorrServer + download engine flows
  * [PATCH 46-R2] poster badges: red year box (top-left) + gold-star
    rating box (top-right) — universal TMDB rating source
  * [PATCH 65-B3] per-title quality preference: when a stored quality
    matches an available variant, auto-play it instead of showing the
    quality menu. Saved by plugin_screen_player when the user picks a
    quality from the in-player menu.
  * [detail-reveal] staggered slide-in on screen open: poster from the
    left, info panel from the right, plot + menu from the bottom, each
    eased. Skipped for chained-episode opens (episode_index >= 0).
  * [detail-stagger] menu rows appear one (or a few) at a time on load
    and on quality-choices swap — makes even a slow response feel live.
  * [detail-accent] gold accent bar parked beside the selected server.
    [PATCH 112] For long lists the bar tracks the real visible-window
    row index (native eListbox behaviour) instead of sliding
    proportionally — and never overshoots the menu box on the last item.
  * [detail-progress] indeterminate cyan bar sweeping under the menu
    box while a stream is being extracted, hidden when it resolves.
"""

import os
import re
import time
import threading
import traceback

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from Screens.Console import Console
from novaplay_choice import NovaChoiceBox as ChoiceBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer, ePicLoad, ePoint, eSize

from plugin_common import my_log, PLUGIN_PATH, _TYPE_LABELS
from extractors import get_extractor
from extractors.base import get_curl_failed_needs_proxy
from plugin_state import (_get_config, _entry_from_item, _upsert_library_item,
                          _is_favorite, _get_saved_position, _save_position,
                          _toggle_favorite_entry)
from urllib.parse import urlparse
from plugin_util import (_single_line_text, _wrap_ui_text,
                          _strip_arabic_from_english_title,
                          _pick_plot_text, _pick_plot_text_with_source,
                          _poster_cache_path, _normalize_poster_url,
                          _get_cached_poster, _fetch_poster_bytes)
from plugin_tmdb import _tmdb_enabled, _merge_tmdb_data
from plugin_util import _split_episode_label                    # [PATCH 81]
from plugin_screen_player import NovaPlaySimplePlayer, _play
from plugin_widgets import StreamList
from plugin_toast import TOAST_XML, toast_init, toast
from plugin_gridlist import POP_ANIM_FRAMES, POP_ANIM_INTERVAL, anim_frames
import plugin_health
import plugin_imagecache
from novaplay_thread import callInMainThread

_PLUGIN_VERSION = "4.1.0"

# [detail-tmdb] how far past a plain contain-fit the real TMDb backdrop
# photo is scaled up, to bring it closer to the poster instead of leaving
# a big flat gap. 1.0 = pure contain (no crop at all, biggest gap). Each
# step up trades a little more top/bottom crop for a narrower gap -- see
# _generateBackdrop's is_landscape branch for the actual crop math.
_BACKDROP_WIDTH_BOOST = 1.3

# [detail-trailer] mirrors plugin_tmdb.py's own _CACHE_BASE_HDD/_TMP
# pattern -- confirmed on the user's own device that /media/hdd is
# mounted and already used for the poster/image cache, so resolved
# trailers (an ffmpeg-muxed local mp4, tens of MB each -- categorically
# bigger than a poster thumbnail) go there too rather than risking a
# RAM-backed /tmp filling up and taking the box down. /tmp is only the
# fallback for a device with no HDD mounted at all.
_TRAILER_CACHE_BASE_HDD = "/media/hdd/NovaPlay/cache/trailers"
_TRAILER_CACHE_BASE_TMP = "/tmp/ap_trailers"
_TRAILER_CACHE_MAX_FILES = 8
# [detail-trailer] Trailers are a quick preview, not the main feature, so
# there's no reason to pull full/best resolution and burn data for it.
# Capped at 480p -- plenty sharp on a TV-distance screen, far smaller/
# faster to download than 1080p+.
_TRAILER_MAX_HEIGHT = 480
# [FIX] "audio only, no video" on a real device traced to the old format
# selector's fallback branches accepting ANY bestvideo (no codec filter),
# which for many YouTube titles is VP9/AV1-in-webm once mp4 isn't
# available at the capped height. Set-top boxes almost universally have
# H.264 hardware decode but often lack VP9/AV1 decode, so a VP9 video
# track silently fails to render while the (software-decodable) audio
# track plays fine -- exactly the symptom reported. Every fallback rung
# below now stays pinned to avc1/h264 video; only the very last resort
# drops that constraint, and bumping this each time the selector logic
# changes forces a fresh download instead of replaying an old, possibly
# video-less cached file under the previous format version.
_TRAILER_CACHE_VERSION = 2


def _trailer_cache_dir():
    base = _TRAILER_CACHE_BASE_HDD if os.path.exists("/media/hdd") else _TRAILER_CACHE_BASE_TMP
    try:
        if not os.path.exists(base):
            os.makedirs(base)
    except Exception:
        pass
    return base


def _evict_old_trailers(trailer_dir, keep=_TRAILER_CACHE_MAX_FILES):
    """[detail-trailer] Keeps the trailer cache bounded -- oldest-mtime-
    first eviction, same principle plugin_imagecache.py already uses for
    the image cache. Trailers are kept (not deleted on screen close) so
    re-opening the same title's detail screen plays instantly instead of
    re-downloading, but unbounded growth isn't acceptable for files this
    size."""
    try:
        files = [os.path.join(trailer_dir, f) for f in os.listdir(trailer_dir) if f.endswith(".mp4")]
        if len(files) <= keep:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for f in files[:-keep]:
            try:
                os.remove(f)
            except Exception:
                pass
    except Exception:
        pass


# ── [B3] quality-preference key ─────────────────────────────────────
def _quality_pref_key(title):
    """Normalize a title into a config key for its per-title quality
    preference. Series episode titles reduce to the series base so an
    entire show shares one preference."""
    t = str(title or "").strip()
    t = re.sub(r"\bS\d{1,2}E\d{1,2}\b.*$", "", t, flags=re.I)
    t = re.sub(r"\b\d{1,2}x\d{1,2}\b.*$", "", t)
    t = re.sub(r"\s*\[\d+p\]\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -|:")
    t = t.lower()
    t = re.sub(r"[^a-z0-9\u0600-\u06ff]+", "_", t)[:60].strip("_")
    return ("qpref_" + t) if t else ""


# [row-compact] Site keys (extractors/registry.py) whose server rows carry
# a real torrent seeder count and so need StreamList's taller, two-line
# row. Every other site gets the shorter single-line row -- see
# NovaPlayDetail._applyMenuRowHeight().
_TORRENT_SEEDER_SITES = ("torrentio", "yts")


# [theme] "Modern" screen style (Settings > Screen Style): the TMDb backdrop
# fills the top of the screen (faded into the background on the left and
# at the bottom), poster | title, details, facts, plot over it, and the
# servers / episodes / qualities as a row of cards in a band at the bottom.
SV_CARDS = 6
_SV_W, _SV_H, _SV_GAP = 268, 150, 22
_SV_X0 = (1920 - (SV_CARDS * _SV_W + (SV_CARDS - 1) * _SV_GAP)) // 2
_SV_Y = 758
_SV_GOLD, _SV_CARD, _SV_ON = "#F2C230", "#161C26", "#2A2717"
# [cast] 2 rows x 3 round TMDb photos with names, right of the plot
CAST_N = 6
_CAST_X = (1356, 1526, 1696)
_CAST_Y = (196, 432)


def _detail_skin_modern():
    p = ["""
    <screen name="NovaPlayDetailModern" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="#0C1017">
        <widget name="bg" position="0,0" size="1920,1080" backgroundColor="#0C1017" zPosition="1" />
        <widget name="backdrop" position="0,0" size="1920,690" backgroundColor="#0C1017" zPosition="1" />
        <widget name="backdropImage" position="0,0" size="1920,690" zPosition="2" alphatest="blend" />
        <widget name="siteLogo" position="40,40" size="180,64" zPosition="3" alphatest="blend" scale="1" />
        <widget name="clock_time" position="1380,22" size="500,62" font="Bold;54" halign="right" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="clock_date" position="1280,90" size="600,30" font="Regular;22" halign="right" foregroundColor="#C9D0DA" transparent="1" zPosition="4" />

        <eLabel position="34,134" size="352,520" backgroundColor="#262E3B" cornerRadius="18" zPosition="3" />
        <eLabel position="38,138" size="344,512" backgroundColor="#161C26" cornerRadius="16" zPosition="3" />
        <widget name="poster" position="52,152" size="316,470" zPosition="4" alphatest="blend" />
        <widget name="posterYear" position="62,162" size="86,34" font="Bold;22" foregroundColor="#0C1017" backgroundColor="#F2C230" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="9" />
        <widget name="posterRating" position="272,162" size="88,34" font="Bold;22" foregroundColor="#F2C230" backgroundColor="#0C1017" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="9" />

        <widget name="title" position="420,140" size="900,66" font="Bold;52" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="meta" position="420,212" size="900,34" font="Regular;24" foregroundColor="#F2C230" transparent="1" zPosition="4" />
        <widget name="proxy_warning" position="420,248" size="900,30" font="Regular;21" foregroundColor="#FF8A6B" transparent="1" zPosition="4" halign="left" />
        <widget name="source" position="420,290" size="880,160" font="Regular;22" foregroundColor="#D6DCE5" transparent="1" halign="left" zPosition="4" />
        <widget name="plot_title" position="420,462" size="600,28" font="Bold;19" foregroundColor="#A3ADBB" transparent="1" halign="left" zPosition="4" />
        <widget name="plot" position="420,494" size="880,180" font="Regular;22" foregroundColor="#E6EAF0" transparent="1" halign="left" valign="top" zPosition="4" />

        <eLabel position="1336,140" size="544,532" backgroundColor="#121822" cornerRadius="20" zPosition="2" />
        <widget name="castTitle" position="1364,152" size="480,32" font="Bold;19" foregroundColor="#A3ADBB" backgroundColor="#121822" transparent="1" zPosition="4" />
        <widget name="menu_box" position="40,692" size="1840,272" backgroundColor="#121822" cornerRadius="22" zPosition="2" />
        <widget name="section" position="70,702" size="1150,44" font="Bold;26" foregroundColor="#FFFFFF" backgroundColor="#121822" transparent="1" zPosition="4" />
        <widget name="svPos" position="1230,706" size="620,36" font="Regular;20" halign="right" foregroundColor="#A3ADBB" backgroundColor="#121822" transparent="1" zPosition="4" />
        <widget name="menu" position="440,700" size="930,60" zPosition="0" scrollbarMode="showNever" transparent="1" backgroundColor="#121822" backgroundColorSelected="#121822" />
        <widget name="svLeft" position="48,800" size="40,64" font="Regular;52" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#121822" transparent="1" zPosition="4" />
        <widget name="svRight" position="1832,800" size="40,64" font="Regular;52" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#121822" transparent="1" zPosition="4" />
        <widget name="menuAccent" position="0,0" size="2,2" backgroundColor="#121822" zPosition="0" />
        <widget name="menuProgress" position="{px},936" size="180,6" backgroundColor="#F2C230" transparent="0" cornerRadius="3" zPosition="5" />
    """.format(px=_SV_X0)]
    for i in range(CAST_N):
        x, y = _CAST_X[i % 3], _CAST_Y[i // 3]
        p.append("""
        <widget name="castImg{i}" position="{ix},{y}" size="116,116" alphatest="blend" scale="1" zPosition="4" />
        <widget name="castName{i}" position="{x},{ny}" size="164,54" font="Regular;18" halign="center" valign="top" foregroundColor="#FFFFFF" backgroundColor="#121822" transparent="1" zPosition="4" />
        """.format(i=i, x=x, ix=x + 24, y=y, ny=y + 122))
    for i in range(SV_CARDS):
        x = _SV_X0 + i * (_SV_W + _SV_GAP)
        p.append("""
        <widget name="svEdge{i}" position="{ex},{ey}" size="{ew},{eh}" backgroundColor="{gold}" cornerRadius="18" zPosition="3" />
        <widget name="svBg{i}" position="{x},{y}" size="{w},{h}" backgroundColor="{card}" cornerRadius="16" zPosition="4" />
        <widget name="svNum{i}" position="{nx},{ny}" size="46,46" font="Bold;22" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#262E3B" cornerRadius="12" zPosition="5" />
        <widget name="svQ{i}" position="{qx},{ny}" size="110,30" font="Bold;18" halign="center" valign="center" foregroundColor="#0C1017" backgroundColor="{gold}" cornerRadius="8" zPosition="5" />
        <widget name="svName{i}" position="{nx},{tny}" size="{tw},62" font="Regular;22" halign="left" valign="top" foregroundColor="#FFFFFF" backgroundColor="{card}" transparent="1" zPosition="5" />
        <widget name="svInfo{i}" position="{nx},{iy}" size="{tw},24" font="Regular;17" halign="left" foregroundColor="#A3ADBB" backgroundColor="{card}" transparent="1" zPosition="5" />
        """.format(i=i, x=x, y=_SV_Y, w=_SV_W, h=_SV_H, ex=x - 4, ey=_SV_Y - 4, ew=_SV_W + 8, eh=_SV_H + 8,
                   nx=x + 16, ny=_SV_Y + 14, qx=x + _SV_W - 126, tny=_SV_Y + 68, iy=_SV_Y + 118, tw=_SV_W - 32,
                   gold=_SV_GOLD, card=_SV_CARD))
    p.append("""
        <eLabel position="40,980" size="1840,72" backgroundColor="#121822" cornerRadius="18" zPosition="2" />
        <eLabel position="64,998" size="34,34" backgroundColor="#E5484D" cornerRadius="9" zPosition="3" />
        <widget name="key_red"    position="112,994" size="220,42" font="Regular;24" valign="center" foregroundColor="#D6DCE5" transparent="1" zPosition="4" />
        <widget name="dotGreen" position="350,998" size="34,34" backgroundColor="#30A46C" cornerRadius="9" zPosition="3" />
        <widget name="key_green"  position="398,994" size="200,42" font="Regular;24" valign="center" foregroundColor="#D6DCE5" transparent="1" zPosition="4" />
        <eLabel position="616,998" size="34,34" backgroundColor="#F2C230" cornerRadius="9" zPosition="3" />
        <widget name="key_yellow" position="664,994" size="230,42" font="Regular;24" valign="center" foregroundColor="#D6DCE5" transparent="1" zPosition="4" />
        <widget name="dotBlue" position="912,998" size="34,34" backgroundColor="#3E7BFA" cornerRadius="9" zPosition="3" />
        <widget name="key_blue"   position="960,994" size="230,42" font="Regular;24" valign="center" foregroundColor="#D6DCE5" transparent="1" zPosition="4" />
        <widget name="status"     position="1200,994" size="656,42" font="Regular;21" valign="center" foregroundColor="#C9D0DA" transparent="1" halign="right" zPosition="4" />
    </screen>""")
    return "".join(p)


_DETAIL_SKIN_MODERN = _detail_skin_modern().replace("</screen>", TOAST_XML + "</screen>")


def _trailer_stream_url(yt_dlp, youtube_url):
    """[trailer-stream] the trailer's single combined H.264 + AAC format
    (video and sound in one URL, the only kind the box players can stream)
    -> 'url|User-Agent=...', or '' when YouTube has none for this video."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(youtube_url, download=False) or {}
    except Exception as e:
        my_log("trailer stream lookup: {}".format(e))
        return ""
    best = None
    for f in info.get("formats") or []:
        vc, ac = str(f.get("vcodec") or ""), str(f.get("acodec") or "")
        if not vc.startswith("avc1") or ac in ("", "none") or not f.get("url"):
            continue
        if str(f.get("protocol") or "https") not in ("https", "http"):
            continue
        if (f.get("height") or 0) > _TRAILER_MAX_HEIGHT:
            continue
        if best is None or (f.get("height") or 0) > (best.get("height") or 0):
            best = f
    if not best:
        return ""
    ua = (best.get("http_headers") or {}).get("User-Agent") or ""
    try:
        from urllib.parse import quote
        ua = quote(ua, safe="")
    except Exception:
        pass
    return best["url"] + ("|User-Agent={}".format(ua) if ua else "")


def _round_photo(path, size=116):
    """[cast] TMDb profile photo -> round PNG (transparent corners), cached."""
    if not path:
        return ""
    url = "https://image.tmdb.org/t/p/w185" + path
    try:
        base = _poster_cache_path(url)
        out = os.path.splitext(base)[0] + "_r%d.png" % size
        if os.path.exists(out):
            return out
        data = _fetch_poster_bytes(url, "https://www.themoviedb.org/")
        from PIL import Image, ImageDraw, ImageOps
        import io
        img = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (size, size), Image.LANCZOS,
                           centering=(0.5, 0.3))
        mask = Image.new("L", (size * 3, size * 3), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * 3 - 1, size * 3 - 1), fill=255)
        mask = mask.resize((size, size), Image.LANCZOS)          # smooth edge
        res = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        res.paste(img, (0, 0), mask)
        res.save(out, format="PNG")
        return out
    except Exception as e:
        my_log("cast photo {}: {}".format(path, e))
        return ""


def _modern_backdrop(img, w, h, is_landscape):
    """[theme] full-width header picture: cover-crop (a poster is blurred
    instead), then faded into the screen background on the left (where the
    title / facts / plot sit) and at the bottom (toward the server band)."""
    from PIL import Image, ImageFilter, ImageEnhance, ImageOps
    try:
        fitted = ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.6, 0.3))
    except Exception:
        fitted = img.resize((w, h))
    if not is_landscape:
        try:
            tiny = fitted.resize((max(1, w // 16), max(1, h // 16)), Image.BILINEAR)
            fitted = tiny.filter(ImageFilter.GaussianBlur(radius=3)).resize((w, h), Image.BILINEAR)
            fitted = ImageEnhance.Brightness(fitted).enhance(0.6)
        except Exception:
            pass
    bg = Image.new("RGB", (w, h), (12, 16, 23))           # #0C1017
    # left -> right: solid behind the text, clear over the right third
    row = []
    for x in range(w):
        t = x / float(w)
        if t < 0.42:
            a = 238
        elif t > 0.80:
            a = 40
        else:
            a = int(238 - (t - 0.42) / 0.38 * 198)
        row.append(a)
    hm = Image.new("L", (w, 1))
    hm.putdata(row)
    hm = hm.resize((w, h))
    # top -> bottom: fade out over the lower 40 %
    col = []
    for y in range(h):
        t = y / float(h)
        if t < 0.18:
            col.append(int(150 * (1 - t / 0.18)))       # behind the clock
        else:
            col.append(0 if t < 0.6 else int(min(255, (t - 0.6) / 0.4 * 255)))
    vm = Image.new("L", (1, h))
    vm.putdata(col)
    vm = vm.resize((w, h))
    try:
        from PIL import ImageChops
        mask = ImageChops.lighter(hm, vm)
    except Exception:
        mask = hm
    return Image.composite(bg, fitted, mask)


class NovaPlayDetail(Screen):
    # [panel 2026-10-21] three columns like the other panel screens:
    # poster + facts | servers / episodes | backdrop + plot, header with the
    # site logo, title, details and clock, colour-key bar at the bottom.
    skin = """
    <screen name="NovaPlayDetail" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="#0C1017">
        <widget name="bg" position="0,0" size="1920,1080" backgroundColor="#0C1017" zPosition="1" />
        <widget name="siteLogo" position="40,40" size="180,64" zPosition="3" alphatest="blend" scale="1" />
        <widget name="title" position="240,28" size="1120,56" font="Bold;40" foregroundColor="#F2C230" transparent="1" zPosition="4" />
        <widget name="meta" position="240,86" size="1120,32" font="Regular;22" foregroundColor="#A3ADBB" transparent="1" zPosition="4" />
        <widget name="proxy_warning" position="240,118" size="1120,28" font="Regular;22" foregroundColor="#FF6B6B" transparent="1" zPosition="4" halign="left" />
        <widget name="clock_time" position="1380,22" size="500,62" font="Bold;54" halign="right" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="clock_date" position="1280,90" size="600,30" font="Regular;22" halign="right" foregroundColor="#A3ADBB" transparent="1" zPosition="4" />

        <eLabel position="40,150" size="360,810" backgroundColor="#121822" cornerRadius="16" zPosition="2" />
        <eLabel position="62,172" size="316,470" backgroundColor="#161C26" cornerRadius="10" zPosition="3" />
        <widget name="poster" position="62,172" size="316,470" zPosition="4" alphatest="blend" />
        <widget name="posterYear" position="70,180" size="86,34" font="Bold;22" foregroundColor="#0C1017" backgroundColor="#F2C230" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="8" />
        <widget name="posterRating" position="282,180" size="88,34" font="Bold;22" foregroundColor="#F2C230" backgroundColor="#0C1017" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="8" />
        <widget name="source" position="62,662" size="316,280" font="Regular;22" foregroundColor="#C9D0DA" transparent="1" halign="left" zPosition="4" />

        <widget name="section" position="430,152" size="950,48" font="Bold;30" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="menu_box" position="430,210" size="950,750" backgroundColor="#161C26" cornerRadius="14" zPosition="2" />
        <widget name="menu" position="440,222" size="930,720" zPosition="4"
                scrollbarMode="showNever"
                enableWrapAround="1"
                transparent="1"
                backgroundColor="#161C26"
                backgroundColorSelected="#2B3648" />
        <widget name="menuAccent"   position="434,222"  size="5,60"  backgroundColor="#F2C230" transparent="0" cornerRadius="2" zPosition="5" />
        <widget name="menuProgress" position="440,946" size="180,5" backgroundColor="#F2C230" transparent="0" cornerRadius="2" zPosition="5" />

        <eLabel position="1410,150" size="470,810" backgroundColor="#121822" cornerRadius="16" zPosition="1" />
        <widget name="backdrop" position="1430,170" size="430,242" backgroundColor="#161C26" cornerRadius="12" zPosition="2" />
        <widget name="backdropImage" position="1430,170" size="430,242" zPosition="3" alphatest="blend" />
        <widget name="plot_title" position="1430,428" size="430,32" font="Bold;22" foregroundColor="#F2C230" transparent="1" halign="left" zPosition="4" />
        <widget name="plot" position="1430,466" size="430,476" font="Regular;23" foregroundColor="#D6DCE5" transparent="1" halign="left" valign="top" zPosition="4" />

        <eLabel position="40,976" size="1840,76" backgroundColor="#121822" cornerRadius="16" zPosition="2" />
        <eLabel position="72,1003" size="22,22" backgroundColor="#E5484D" cornerRadius="11" zPosition="3" />
        <widget name="key_red"    position="106,994" size="220,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="dotGreen" position="332,1003" size="22,22" backgroundColor="#30A46C" cornerRadius="11" zPosition="3" />
        <widget name="key_green"  position="366,994" size="180,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <eLabel position="552,1003" size="22,22" backgroundColor="#F2C230" cornerRadius="11" zPosition="3" />
        <widget name="key_yellow" position="586,994" size="230,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="dotBlue" position="822,1003" size="22,22" backgroundColor="#3E7BFA" cornerRadius="11" zPosition="3" />
        <widget name="key_blue"   position="856,994" size="240,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="status"     position="1110,994" size="740,40" font="Regular;22" valign="center" foregroundColor="#C9D0DA" transparent="1" halign="right" zPosition="4" />
    </screen>
    """

    # [detail-reveal] design positions for every widget that participates
    # in the entry animation. Each group slides from an offset toward its
    # design position with an ease-out cubic curve. Groups have staggered
    # start frames so the reveal cascades.
    # (name, ((widget_key, design_x, design_y), ...), off_x, off_y, delay, duration)
    _REVEAL_GROUPS = (
        ("poster", (
            ("poster", 62, 172),
            ("posterYear", 70, 180),
            ("posterRating", 282, 180),
            ("source", 62, 662),
        ), -360, 0, 0, 8),
        ("info", (
            ("title", 240, 28),
            ("meta", 240, 86),
            ("proxy_warning", 240, 118),
        ), 220, 0, 2, 8),
        ("menu", (
            ("menu_box", 430, 210),
            ("section", 430, 152),
            ("menu", 440, 222),
        ), 0, 320, 3, 8),
        ("plot", (
            ("backdrop", 1430, 170),
            ("backdropImage", 1430, 170),
            ("plot_title", 1430, 428),
            ("plot", 1430, 466),
        ), 360, 0, 4, 8),
    )

    _REVEAL_GROUPS_MODERN = (
        ("poster", (
            ("poster", 52, 152),
            ("posterYear", 62, 162),
            ("posterRating", 272, 162),
        ), -400, 0, 0, 8),
        ("info", (
            ("title", 420, 140),
            ("meta", 420, 212),
            ("proxy_warning", 420, 248),
            ("source", 420, 290),
            ("plot_title", 420, 462),
            ("plot", 420, 494),
        ), 260, 0, 2, 8),
        ("band", (
            ("menu_box", 40, 692),
            ("section", 70, 702),
            ("svPos", 1230, 706),
        ), 0, 300, 3, 8),
    )

    # [detail-accent] geometry of the menu widget, used to compute where
    # the accent bar should sit relative to the selected row.
    _MENU_X = 440
    _MENU_Y = 222
    _MENU_W = 930
    # [PATCH 115] confirmed live from screen recording: 4 server rows are
    # actually visible on screen at once (e.g. "fastved / firestream /
    # luluvdoo / rpmvip" all shown together), not 3 -- 320 // 95 was
    # computing visible_rows=3, so the accent bar pinned itself one row
    # too early and could never reach the real 4th row. 380 // 95 = 4,
    # matching the real screen.
    _MENU_H = 720
    _MENU_ROW_H = 95

    # [detail-progress] geometry of the indeterminate bar
    _PROG_X0 = 440
    _PROG_Y  = 946
    _PROG_W  = 180
    _PROG_H  = 5
    _PROG_SPAN = 930 - 180   # bar travels this far before reversing
    _PROG_STEP = 40

    def __init__(self, session, item, site="egydead", m_type="movie",
                 episode_chain=None, episode_index=-1, auto_server_idx=None, resume=False):
        # [theme] Modern: backdrop header + server cards in a bottom band
        self._modern = _get_config("screen_style", "classic") == "modern"
        if self._modern:
            self.skin = _DETAIL_SKIN_MODERN
            self.skinName = "NovaPlayDetailModern"
            self._REVEAL_GROUPS = self._REVEAL_GROUPS_MODERN
            self._PROG_X0 = _SV_X0
            self._PROG_Y = 936
            self._PROG_H = 6
            self._PROG_SPAN = SV_CARDS * _SV_W + (SV_CARDS - 1) * _SV_GAP - self._PROG_W
        else:
            self.skin = NovaPlayDetail.skin.format(plugin_path=PLUGIN_PATH).replace("</screen>", TOAST_XML + "</screen>")
        self._sv_items = []
        self._sv_pop_key = None
        self._sv_pop_slot = -1
        self._sv_pop_f = 0
        self._sv_slide = 0
        self._svPopT = eTimer()
        self._svPopT.callback.append(self._svPopTick)
        Screen.__init__(self, session)
        self.session = session
        self._item   = item
        self._site   = site
        self._m_type = m_type
        self._data   = None
        self._servers = []
        self._episodes = []
        self._tmp_posters = []
        self._poster_loaded = False
        self._raw_title = ""
        self._closed = False
        self._proxy_warning_shown = False

        self._extract_lock = threading.Lock()
        self._extract_token = 0
        self._extracting = False
        self._quality_choices = []

        self._episode_chain = list(episode_chain) if episode_chain else []
        self._episode_index = int(episode_index if episode_index is not None else -1)
        self._auto_server_idx = auto_server_idx
        self._last_server_idx = None
        # [continue] opened from Continue Watching: start the last-used
        # server and resume without the yes/no question
        self._resume_direct = bool(resume)
        self._close_on_return = False
        if self._episode_chain and 0 <= self._episode_index < len(self._episode_chain) - 1:
            self._next_episode = self._episode_chain[self._episode_index + 1]
        else:
            self._next_episode = None
        self._auto_menu_timer = None
        self._osd_poster = (item.get("poster") or item.get("image") or "")

        # [detail-reveal] animation state
        self._reveal_timer = eTimer()
        self._reveal_timer.callback.append(self._revealTick)
        self._reveal_frame = 0
        self._reveal_running = False

        # [detail-stagger] menu rows appearing one step at a time
        self._stagger_timer = eTimer()
        self._stagger_timer.callback.append(self._staggerTick)
        self._stagger_items = []
        self._stagger_idx = 0
        self._stagger_step = 1

        # [detail-accent] tracks the last item count so the accent bar can
        # hide itself when the menu is empty
        self._menu_item_count = 0

        # [detail-progress] indeterminate bar state
        self._progress_timer = eTimer()
        self._progress_timer.callback.append(self._progressTick)
        self._progress_x = self._PROG_X0
        self._progress_dir = 1
        self._progress_active = False

        self["bg"]     = Label("")
        self["backdrop"] = Label("")
        self["backdropImage"] = Pixmap()
        self["menu_box"] = Label("")
        self["poster"] = Pixmap()
        self["posterYear"] = Label("")
        self["posterRating"] = Label("")
        self["title"]  = Label(item.get("title", ""))
        self["meta"]   = Label("")
        self["source"] = Label("")
        self["proxy_warning"] = Label("")
        self["plot_title"] = Label("Plot")
        self["plot"]   = Label("")
        self["section"] = Label("Preparing...")
        self["menu"]   = StreamList(width=930)
        self["siteLogo"] = Pixmap()
        self["clock_time"] = Label("")
        self["clock_date"] = Label("")
        self["dotGreen"] = Label("")
        self["dotBlue"] = Label("")
        self._clockTimer = eTimer()
        self._clockTimer.callback.append(self._tickClock)
        self["menuAccent"]   = Label("")
        self["menuProgress"] = Label("")
        self["menuAccent"].hide()
        self["menuProgress"].hide()
        if self._modern:
            self["castTitle"] = Label("")
            for _i in range(CAST_N):
                self["castImg%d" % _i] = Pixmap()
                self["castName%d" % _i] = Label("")
            self._cast_token = None
            self["svPos"] = Label("")
            self["svLeft"] = Label(u"\u2039")
            self["svRight"] = Label(u"\u203a")
            for _i in range(SV_CARDS):
                for _n in ("svEdge", "svBg", "svNum", "svQ", "svName", "svInfo"):
                    self["%s%d" % (_n, _i)] = Label("")
        self["key_red"] = Label("Favorites")
        self["key_yellow"] = Label("Update TMDb")
        self["key_blue"] = Label("Download")
        self["key_green"] = Label("")
        self["status"] = Label("Loading details...")
        toast_init(self)

        self._downloads = []
        self._active_download_task = None

        # [detail-accent] hook the selection-changed pipeline. If Enigma2
        # wires StreamList.selectionChanged (it does for eListbox parents),
        # this fires on every arrow-key move; otherwise the arrow-key
        # handlers below also call _updateMenuAccent explicitly, so it's
        # covered either way.
        try:
            self["menu"].onSelectionChanged.append(self._updateMenuAccent)
        except Exception:
            pass

        self.picLoad = ePicLoad()
        self.picLoad.PictureData.get().append(self._paintPoster)

        # [detail-tmdb] second, independent native-decode pipeline for the
        # blurred backdrop image -- kept fully separate from self.picLoad
        # (the sharp poster) so the two decodes never race or overwrite
        # each other's PictureData callback.
        self.backdropPicLoad = ePicLoad()
        self.backdropPicLoad.PictureData.get().append(self._paintBackdrop)

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "ok":     self._onOk,
            "cancel": self._onCancel,
            "red":    self._toggleFavorite,
            "yellow": self._onYellow,
            "blue":   self._openDownloads,
            "green":  self._playTrailer,
            "up":     self._navMenuUp,
            "down":   self._navMenuDown,
            "left":   self._navLeft,
            "right":  self._navRight,
        }, -1)
        if self._modern:
            self.onLayoutFinish.append(self._svInit)
        # [2026-10-21] CH+ / CH- step the torrent quality filter (like YTS's tabs)
        self["torFilterActions"] = ActionMap(["ChannelSelectBaseActions"], {
            "nextBouquet": lambda: self._stepTorrentFilter(1),
            "prevBouquet": lambda: self._stepTorrentFilter(-1),
        }, -1)

        self.onLayoutFinish.append(self._load)
        self.onExecBegin.append(self._refreshPoster)
        self.onExecBegin.append(self._maybeCloseAfterResume)
        self.onClose.append(self._onCloseCleanup)

    # ── [detail-accent] navigation helpers ─────────────────────────────
    # [theme] Modern: the cards run left/right, so ◀ ▶ step one card and
    # ▲ ▼ jump a whole row of cards; Classic keeps ▲ ▼ rows, ◀ ▶ pages.
    def _navMenuUp(self):
        if self._modern:
            return self._svStep(-SV_CARDS)
        try:
            self["menu"].up()
        except Exception:
            pass
        self._updateMenuAccent()

    def _navMenuDown(self):
        if self._modern:
            return self._svStep(SV_CARDS)
        try:
            self["menu"].down()
        except Exception:
            pass
        self._updateMenuAccent()

    def _navLeft(self):
        if self._modern:
            return self._svStep(-1)
        self["menu"].pageUp()

    def _navRight(self):
        if self._modern:
            return self._svStep(1)
        self["menu"].pageDown()

    # ── [theme] Modern server cards ───────────────────────────────────
    def _svInit(self):
        try:
            self["menu"].hide()
            self["menuAccent"].hide()
        except Exception:
            pass
        self["castTitle"].setText("CAST")
        for i in range(CAST_N):
            self["castImg%d" % i].hide()
            self["castName%d" % i].hide()
        try:
            # every selection move (incl. the auto-pick of the last server)
            self["menu"].instance.selectionChanged.get().append(self._svPaint)
        except Exception as e:
            my_log("detail modern: selectionChanged hook failed: {}".format(e))
        self._svPaint()

    # [pop-anim] the selected card grows 8 % (same smooth zoom as the
    # posters / cards on the home and site screens)
    _SV_PARTS = (("svEdge", -4, -4, _SV_W + 8, _SV_H + 8, True), ("svBg", 0, 0, _SV_W, _SV_H, True),
                 ("svNum", 16, 14, 46, 46, True), ("svQ", _SV_W - 126, 14, 110, 30, False),
                 ("svName", 16, 68, _SV_W - 32, 62, False), ("svInfo", 16, 118, _SV_W - 32, 24, False))

    def _svPlace(self, i, p, xoff=0.0):
        x0 = _SV_X0 + i * (_SV_W + _SV_GAP) + xoff
        k = 1.0 + 0.08 * p
        cx, cy = x0 - (_SV_W * k - _SV_W) / 2.0, _SV_Y - (_SV_H * k - _SV_H) / 2.0
        geo = self.__dict__.setdefault("_sv_geo", {})
        for name, rx, ry, w, h, rs in self._SV_PARTS:
            key = "%s%d" % (name, i)
            pos = (int(cx + rx * k), int(cy + ry * k))
            size = (max(1, int(w * k)), max(1, int(h * k))) if rs else None
            old = geo.get(key)
            if old == (pos, size):
                continue                      # [perf] nothing would change
            try:
                inst = self[key].instance
                if inst is None:
                    continue
                if not old or old[0] != pos:
                    inst.move(ePoint(*pos))
                if size is not None and (not old or old[1] != size):
                    inst.resize(eSize(*size))
                geo[key] = (pos, size)
            except Exception:
                pass

    def _svPopFocus(self, slot, extra):
        key = (slot,) + tuple(extra) + (id(self._sv_items),) if slot >= 0 else None
        if key == self._sv_pop_key:
            return
        try:
            self._svPopT.stop()
        except Exception:
            pass
        if 0 <= self._sv_pop_slot < SV_CARDS:
            self._svPlace(self._sv_pop_slot, 0.0)
        self._sv_pop_key = key
        self._sv_pop_slot = slot if (slot >= 0 and extra[1]) else -1
        if self._sv_pop_slot < 0 and not self._sv_slide:
            return
        self._sv_pop_f = 0
        self._svFrame(0.0)
        try:
            self._svPopT.start(POP_ANIM_INTERVAL, False)
        except Exception:
            self._svFrame(1.0)

    def _svFrame(self, p):
        """[slide-anim] one step past the edge: the whole row glides one card
        sideways while the newly selected card grows; otherwise just the zoom."""
        d = self._sv_slide
        if d:
            off = d * (_SV_W + _SV_GAP) * (1.0 - p)
            for i in range(SV_CARDS):
                self._svPlace(i, p if i == self._sv_pop_slot else 0.0, off)
        elif self._sv_pop_slot >= 0:
            self._svPlace(self._sv_pop_slot, p)

    def _svPopTick(self):
        import math
        if (self._sv_pop_slot < 0 and not self._sv_slide) or getattr(self, "_closed", False):
            self._svPopT.stop()
            return
        self._sv_pop_f += 1
        t = min(1.0, self._sv_pop_f / float(max(1, anim_frames())))
        self._svFrame(math.sin(t * math.pi / 2))
        if t >= 1.0:
            self._svPopT.stop()
            self._sv_slide = 0

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
                out.append((c.get("name", ""), _round_photo(c.get("photo"))))
            callInMainThread(self._paintCast, key, out)
        threading.Thread(target=_worker, daemon=True).start()

    def _paintCast(self, key, cast):
        if getattr(self, "_closed", False) or key != self._cast_token:
            return
        for i in range(CAST_N):
            if i < len(cast):
                name, path = cast[i]
                self["castName%d" % i].setText(_wrap_ui_text(name, width=16, max_lines=2, fallback=""))
                self["castName%d" % i].show()
                inst = self["castImg%d" % i].instance
                if path and inst is not None:
                    inst.setPixmapFromFile(path)
                    self["castImg%d" % i].show()
                else:
                    self["castImg%d" % i].hide()
            else:
                self["castName%d" % i].hide()
                self["castImg%d" % i].hide()
        self["castTitle"].setText("CAST" if cast else "")

    def _svStep(self, d):
        n = len(self._sv_items)
        if not n:
            return
        idx = self["menu"].getCurrentIndex()
        t = idx + d
        if abs(d) == 1:
            t %= n                       # wrap around at the ends
        else:
            t = max(0, min(n - 1, t))
        try:
            self["menu"].instance.moveSelectionTo(t)
        except Exception:
            pass
        self._svPaint()

    def _svColor(self, name, bg=None, fg=None):
        try:
            from skin import parseColor
            inst = self[name].instance
            if inst is None:
                return
            if bg:
                inst.setBackgroundColor(parseColor(bg))
            if fg:
                inst.setForegroundColor(parseColor(fg))
            inst.invalidate()
        except Exception:
            pass

    def _svPaint(self):
        if not getattr(self, "_modern", False) or getattr(self, "_closed", False):
            return
        items = self._sv_items or []
        n = len(items)
        try:
            idx = self["menu"].getCurrentIndex()
        except Exception:
            idx = 0
        idx = max(0, min(idx, n - 1)) if n else 0
        # [slide-anim] the row scrolls one card at a time: stepping past the
        # last (first) visible card moves the window by one and slides
        old = getattr(self, "_sv_base", 0)
        if id(items) != getattr(self, "_sv_list_id", None):
            self._sv_list_id = id(items)
            base = min((idx // SV_CARDS) * SV_CARDS, max(0, n - SV_CARDS))
            old = base
        else:
            base = old
            if idx < base:
                base = idx
            elif idx >= base + SV_CARDS:
                base = idx - SV_CARDS + 1
            base = max(0, min(base, max(0, n - SV_CARDS)))
        self._sv_base = base
        if abs(base - old) == 1:
            self._sv_slide = base - old
        for i in range(SV_CARDS):
            g = base + i
            names = ["%s%d" % (k, i) for k in ("svEdge", "svBg", "svNum", "svQ", "svName", "svInfo")]
            if g >= n:
                for k in names:
                    self[k].hide()
                continue
            it = tuple(items[g]) + ("", "", "", "")
            q, name, size, extra = (str(x or "") for x in it[:4])
            q = re.sub(r"[\[\]]", "", q).strip()
            sel = g == idx
            if not name:
                # placeholder row ("No servers available")
                for k in names:
                    self[k].hide()
                self["svBg%d" % i].show()
                self["svName%d" % i].setText(_wrap_ui_text(q, width=20, max_lines=2, fallback=""))
                self["svName%d" % i].show()
                continue
            is_ep = bool(re.match(r"^S\d{1,3}E\d{1,4}$", q, re.I))
            self["svEdge%d" % i].show() if sel else self["svEdge%d" % i].hide()
            self["svBg%d" % i].show()
            self._svColor("svBg%d" % i, bg=_SV_ON if sel else _SV_CARD)
            self["svNum%d" % i].setText(str(g + 1))
            self["svNum%d" % i].show()
            badge = q.upper() if q else ""
            if badge in ("", "AUTO"):
                self["svQ%d" % i].hide()
            else:
                gold = badge.startswith("4K") or "2160" in badge
                blue = is_ep or any(t in badge for t in ("HD", "1080", "720", "1440"))
                self["svQ%d" % i].setText(_single_line_text(badge, width=9, fallback=""))
                self._svColor("svQ%d" % i, bg=_SV_GOLD if gold else ("#3E7BFA" if blue else "#566070"),
                              fg="#0C1017" if gold else "#FFFFFF")
                self["svQ%d" % i].show()
            self["svName%d" % i].setText(_wrap_ui_text(name, width=21, max_lines=2, fallback="—"))
            self._svColor("svName%d" % i, fg=_SV_GOLD if sel else "#FFFFFF")
            self["svName%d" % i].show()
            info = " · ".join(x.replace("Size: ", "").replace("Seeders: ", "\u25b2 ") for x in (size, extra) if x)
            if sel and self._last_server_idx == g and not info:
                info = "\u25cf Last watched"
            self["svInfo%d" % i].setText(_single_line_text(info, width=28, fallback=""))
            self["svInfo%d" % i].show()
        self._svPopFocus(idx - base if n else -1, (base, n > 0 and bool(str((tuple(items[idx]) + ("", ""))[1] or ""))))
        pages = (n + SV_CARDS - 1) // SV_CARDS if n else 0
        self["svPos"].setText(u"{} / {}   \u00b7   \u25c0 \u25b6 choose   \u00b7   OK play".format(idx + 1, n) if n else "")
        self["svLeft"].show() if base > 0 else self["svLeft"].hide()
        self["svRight"].show() if base + SV_CARDS < n else self["svRight"].hide()

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

    def _layoutHeader(self):
        """[panel] title, details, facts and plot have fixed places in the
        three-column layout (the skin positions); nothing to stack."""
        self._hdr_y = {}

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
                dy = getattr(self, "_hdr_y", {}).get(key, dy)
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
                dy = getattr(self, "_hdr_y", {}).get(key, dy)
                try:
                    inst = self[key].instance
                    if inst:
                        inst.move(ePoint(dx + xoff, dy + yoff))
                except Exception:
                    pass
        if all_done:
            self._reveal_running = False
            try: self._reveal_timer.stop()
            except Exception: pass

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
            self.session.open(NovaPlayDetail, ep, self._site, ep.get("type", "episode"),
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

    def _initTorrentFilter(self):
        srv = list(self._servers or [])
        self._tor_all = None
        self._tor_filter = "All"
        # any list with 2+ torrents (YTS adds embed servers after its torrents)
        if sum(1 for x in srv if str(x.get("url", "")).startswith("magnet:")) >= 2:
            self._tor_all = srv
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
            return u"Quality: {}".format(self._tor_filter)
        return "Update TMDb"

    def _setServerSection(self):
        if getattr(self, "_tor_all", None):
            c = self._torCounts()
            tabs = u"   ".join((u"[{}]" if b == self._tor_filter else u"{}").format(
                u"{} {}".format(b, c[b]) if b != "All" else u"All")
                for b in self._TOR_BUCKETS if b == "All" or c[b])
            other = u"   + {} other".format(c["_other"]) if (c["_other"] and self._tor_filter == "All") else u""
            self["section"].setText(_single_line_text(
                u"🧲 Torrents {}   {}{}".format(c["All"], tabs, other), width=90))
        else:
            self["section"].setText(_single_line_text("Available servers: {}  |  pick a quality or server".format(len(self._servers)), width=90))

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
        for t in ("_reveal_timer", "_stagger_timer", "_progress_timer", "_clockTimer", "_svPopT"):
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

            img = Image.open(src_path).convert("RGB")

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
                                            "m" if getattr(self, "_modern", False) else ("" if w >= 1000 else "_s"))
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
        self["title"].setText(_single_line_text(current_title, width=46, fallback="Untitled"))

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
        try:
            _rt_min = int(data.get("runtime") or 0)
            if _rt_min > 0:
                _h, _m2 = divmod(_rt_min, 60)
                _rt_fmt = "{}h {}m".format(_h, _m2) if _h else "{}m".format(_m2)
        except Exception:
            pass
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
        if _cert or _rd_fmt or _rt_fmt:
            tmdb_meta = []
            if _cert:               tmdb_meta.append(_cert)
            if _rd_fmt:             tmdb_meta.append(_rd_fmt)
            if data.get("genres"): tmdb_meta.append(data["genres"])
            if _rt_fmt:             tmdb_meta.append(_rt_fmt)
            # [no-repeat] no site name here (logo + "Source" fact show it)
            self["meta"].setText(_single_line_text("  ·  ".join(tmdb_meta), width=96, fallback=""))
        else:
            # [no-repeat] year and rating are on the poster badges already
            meta = []
            if data.get("type") in _TYPE_LABELS: meta.append(_TYPE_LABELS[data["type"]])
            if data.get("quality"): meta.append(data["quality"])          # [PATCH 111] print type e.g. HDCAM
            if data.get("genres"):  meta.append(data["genres"])
            self["meta"].setText(_single_line_text("  ·  ".join(meta), width=96, fallback=""))
        counts = []
        _nav_items = [e for e in data.get("items", []) if e.get("type") in ("episode", "series", "season")]
        has_episodes = bool(_nav_items)
        has_servers = bool([s for s in data.get("servers", []) if s.get("url")])
        is_series_item = (
            data.get("type") in ("series", "show")
            or self._item.get("type") in ("series", "show")
            or has_episodes
        )
        if has_episodes:
            _nav_label = "Seasons" if all(e.get("type") in ("series", "season") for e in _nav_items) else "Episodes"
            counts.append("{}: {}".format(_nav_label, len(_nav_items)))
        else:
            counts.append("Servers: {}".format(len([s for s in data.get("servers", []) if s.get("url")])))
        # [no-repeat] Year is on the poster badge, Runtime in the details line
        # when TMDb gave it -- the facts only keep what is shown nowhere else
        if data.get("country"):                                       # [PATCH 111]
            counts.append("Country: {}".format(data.get("country")))
        if data.get("runtime") and not _rt_fmt:
            counts.append("Runtime: {}".format(data.get("runtime")))
        if data.get("channel"):
            counts.append("Channel: {}".format(data.get("channel")))
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
        plot_text = re.sub(r"^\[.*?\]\s*|^(?:المصدر|Source):\s*.*?\|\s*", "", plot_text)
        _MID_SITES = ("EgyDead", "Wecima", "Akoam", "ArabSeed", "TopCinema", "TopCinemaa", "FaselHD", "Shaheed", "Shaheed4u")
        for _ms in _MID_SITES:
            plot_text = re.sub(r"\s*[|\-]\s*" + re.escape(_ms) + r"[^\u0600-\u06ff\n]{0,25}", " ", plot_text, flags=re.I)
            plot_text = re.sub(r"\u0639\u0644\u0649\s+\u0645\u0648\u0642\u0639\s+" + re.escape(_ms) + r"[^\u0600-\u06ff\n]{0,30}", " ", plot_text, flags=re.I)
        plot_text = re.sub(r"  +", " ", plot_text).strip()
        my_log("Detail plot source: {} | len={}".format(plot_source, len(plot_text)))

        _pt = (plot_text or "").strip()
        if len(_pt) > 500:
            _pt = _pt[:500].rsplit(" ", 1)[0] + "…"
        _ar_count = sum(1 for _c in _pt[:80] if "\u0600" <= _c <= "\u06ff")
        if _ar_count > int(len(_pt[:80]) * 0.3):
            _pt = "\u200f" + _pt
        self["plot"].setText(_pt)
        try:
            self._layoutHeader()      # [plot-low] place the plot for its length
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
            self["section"].setText(_single_line_text("{}: {}  |  {}".format(_list_label, len(self._episodes), _pick_hint), width=90))
            items = [self._format_episode_item(ep) for ep in self._episodes]
            self._setMenuStaggered(items)
            self["status"].setText(self._status_hint(_ok_hint))
        elif self._servers:
            self._setServerSection()
            items = [self._format_server_item(s) for s in self._servers]
            self._setMenuStaggered(items)
            self["status"].setText(self._status_hint("Choose a server — OK"))
        elif is_series_item:
            self["section"].setText("Available episodes: 0")
            self._setMenu([("No episodes available", "", "", "")])
            self["status"].setText("No episodes")
        else:
            self["section"].setText("Available servers: 0")
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
        if self._tmdb_id:
            self["key_green"].setText("Trailer")
        else:
            self["key_green"].setText("")
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
        backdrop_url = data.get("fanart") or ""
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

        self["section"].setText(_single_line_text("Available qualities: {}  |  pick a quality".format(len(choices)), width=90))
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
            NovaPlayDetail, next_ep, self._site,
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
        # screen (__onConfirmed / exhausted in plugin_screen_player.py) --
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
        if not getattr(self, "_tmdb_id", None):
            self["status"].setText("No trailer available for this title")
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
            from plugin_tmdb import _tmdb_fetch_trailer_key
            key = _tmdb_fetch_trailer_key(self._tmdb_kind, self._tmdb_id)
            if getattr(self, "_closed", False):
                return
            if not key:
                callInMainThread(self._onTrailerFailed, "No trailer on YouTube for this title")
                return

            youtube_url = "https://www.youtube.com/watch?v={}".format(key)
            try:
                import yt_dlp
            except ImportError:
                callInMainThread(self._onTrailerFailed, "yt-dlp is not installed on the box")
                return

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
            # what's playing. NovaPlaySimplePlayer's own __onEOF
            # (used for every other stream in this app already) just calls
            # __onExit() and closes -- no movielist, no global setting
            # involved -- and __onCancelKey does the same unless playback is
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
            self.session.open(NovaPlaySimplePlayer, trailer_title, candidates,
                               previous_service=None, resume_pos=0, item_url="",
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


from plugin_util import _site_label, _sort_servers            # noqa: E402
from plugin_assets import placeholder_for_item                # noqa: E402


def _get_extractor(site):
    return get_extractor(site)