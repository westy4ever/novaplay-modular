# -*- coding: utf-8 -*-
"""NovaPlay detail screen -- shared module level: imports, skins, trailer
cache, picture helpers (round cast photos, Modern backdrop), runtime parser.
Re-exported to plugin_screen_detail and the detail_* parts.
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
                          _toggle_favorite_entry, _history_items)
from urllib.parse import urlparse
from plugin_util import (_strip_shown_year, _single_line_text, _wrap_ui_text,
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
from novaplay_ui import set_color                       # [ui-helpers]
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
# [cast] one row of 6 round photos (no panel) under the details, over the backdrop
_CAST_PHOTO, _CAST_W = 90, 150
_CAST_X = tuple(420 + c * _CAST_W for c in range(3))  # 2 rows x 3 under the details
_CAST_Y = (318, 424)


def _detail_skin_modern():
    p = ["""
    <screen name="NovaPlayDetailModern" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="#0C1017">
        <widget name="bg" position="0,0" size="1920,1080" backgroundColor="#0C1017" zPosition="1" />
        <widget name="backdrop" position="0,0" size="1920,690" backgroundColor="#0C1017" zPosition="1" />
        <widget name="backdropImage" position="0,0" size="1920,690" zPosition="2" scale="1" />
        <widget name="siteLogo" position="40,40" size="180,64" zPosition="3" alphatest="blend" scale="1" />
        <widget name="clock_time" position="1380,22" size="500,62" font="Bold;54" halign="right" foregroundColor="#FFFFFF" transparent="1" zPosition="4" />
        <widget name="clock_date" position="1280,90" size="600,30" font="Regular;22" halign="right" foregroundColor="#C9D0DA" transparent="1" zPosition="4" />

        <eLabel position="34,134" size="352,520" backgroundColor="#262E3B" cornerRadius="18" zPosition="3" />
        <eLabel position="38,138" size="344,512" backgroundColor="#161C26" cornerRadius="16" zPosition="3" />
        <widget name="poster" position="52,152" size="316,470" zPosition="4" alphatest="blend" />
        <widget name="posterYear" position="62,162" size="86,34" font="Bold;22" foregroundColor="#0C1017" backgroundColor="#F2C230" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="9" />
        <widget name="posterRating" position="272,162" size="88,34" font="Bold;22" foregroundColor="#F2C230" backgroundColor="#0C1017" transparent="0" zPosition="5" halign="center" valign="center" cornerRadius="9" />
        <widget name="posStrip" position="52,578" size="316,44" backgroundColor="#0C1017" zPosition="6" />
        <widget name="posStatus" position="64,578" size="250,40" font="Bold;19" valign="center" foregroundColor="#FFFFFF" backgroundColor="#0C1017" transparent="1" zPosition="7" />
        <widget name="posFav" position="318,578" size="44,40" font="Regular;26" halign="center" valign="center" foregroundColor="#F2C230" backgroundColor="#0C1017" transparent="1" zPosition="7" />
        <widget name="posBarBg" position="52,618" size="316,4" backgroundColor="#3A4352" zPosition="7" />
        <widget name="posBar" position="52,618" size="1,4" backgroundColor="#30A46C" zPosition="8" />

        <widget name="title" position="420,138" size="900,68" font="Bold;52" valign="center" foregroundColor="#FFFFFF" transparent="1" shadowColor="#000000" shadowOffset="-2,-2" zPosition="4" />
        <widget name="tagline" position="420,208" size="900,30" font="Regular;22" foregroundColor="#C9D0DA" transparent="1" shadowColor="#000000" shadowOffset="-2,-2" zPosition="4" />
        <widget name="metaCert" position="420,242" size="80,32" font="Bold;20" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#1A2130" borderWidth="2" borderColor="#C9D0DA" cornerRadius="6" transparent="0" zPosition="4" />
        <widget name="meta" position="420,242" size="900,32" font="Regular;24" valign="center" foregroundColor="#F2C230" transparent="1" shadowColor="#000000" shadowOffset="-2,-2" zPosition="4" />
        <widget name="source" position="420,280" size="900,30" font="Regular;21" foregroundColor="#C9D0DA" transparent="1" halign="left" shadowColor="#000000" shadowOffset="-2,-2" zPosition="4" />
        <widget name="proxy_warning" position="1320,140" size="560,30" font="Regular;21" foregroundColor="#FF8A6B" transparent="1" zPosition="4" halign="right" shadowColor="#000000" shadowOffset="-2,-2" />
        <widget name="plot_title" position="420,548" size="600,26" font="Bold;19" foregroundColor="#A3ADBB" transparent="1" halign="left" shadowColor="#000000" shadowOffset="-2,-2" zPosition="4" />
        <widget name="plot" position="420,576" size="1460,110" font="Regular;22" foregroundColor="#E6EAF0" transparent="1" halign="left" valign="top" shadowColor="#000000" shadowOffset="-2,-2" zPosition="4" />

        <widget name="menu_box" position="40,692" size="1840,272" backgroundColor="#121822" cornerRadius="22" zPosition="2" />
        <widget name="section" position="70,702" size="1150,44" font="Bold;26" foregroundColor="#FFFFFF" backgroundColor="#121822" transparent="1" zPosition="4" />
        <widget name="torLead" position="-400,-100" size="200,40" font="Bold;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="6" />
        <widget name="tq0" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq1" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq2" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq3" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="tq4" position="-400,-100" size="120,40" font="Bold;22" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" transparent="0" cornerRadius="20" zPosition="6" />
        <widget name="torHint" position="-400,-100" size="200,40" font="Regular;20" valign="center" foregroundColor="#8C96A5" transparent="1" zPosition="6" />
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
        <widget name="castInit{i}" position="{ix},{y}" size="{ps},{ps}" font="Bold;30" halign="center" valign="center" foregroundColor="#D6DCE5" backgroundColor="#2A3342" cornerRadius="{cr}" transparent="0" zPosition="4" />
        <widget name="castImg{i}" position="{ix},{y}" size="{ps},{ps}" alphatest="blend" scale="1" zPosition="5" />
        <widget name="castName{i}" position="{x},{ny}" size="{w},26" font="Regular;16" halign="center" valign="top" foregroundColor="#FFFFFF" backgroundColor="#0C1017" transparent="1" shadowColor="#000000" shadowOffset="-2,-2" zPosition="4" />
        """.format(i=i, x=x, ix=x + (_CAST_W - _CAST_PHOTO) // 2, y=y, ny=y + _CAST_PHOTO + 4,
                   ps=_CAST_PHOTO, w=_CAST_W, cr=_CAST_PHOTO // 2))
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


def _runtime_minutes(v):
    """[FIX runtime] minutes from TMDb's int or a site's text:
    130 / "130" / "130 min" / "130 دقيقة" / "2h 10m" / "02:10:00" / "2:10" /
    "ساعتان و 10 دقائق" -> 130; 0 when it can't tell."""
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v) if 0 < v < 1000 else 0
    t = str(v).strip().translate({ord(a): ord("0") + i for i, a in enumerate(u"\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669")})
    m = re.search(r"(\d{1,2}):(\d{2})(?::\d{2})?", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    hours = 0
    mh = re.search(r"(\d+)\s*(?:h|hr|hour|hours|\u0633\u0627\u0639)", t, re.I)
    if mh:
        hours = int(mh.group(1))
    elif re.search(u"\u0633\u0627\u0639\u062a\u0627\u0646|\u0633\u0627\u0639\u062a\u064a\u0646", t):
        hours = 2
    elif re.search(u"\u0633\u0627\u0639\u0629", t):
        hours = 1
    mm = re.search(r"(\d+)\s*(?:m\b|min|mins|minutes|\u062f\u0642)", t, re.I)
    mins = int(mm.group(1)) if mm else 0
    if hours or mins:
        return hours * 60 + mins
    m = re.search(r"\d+", t)
    n = int(m.group(0)) if m else 0
    return n if 0 < n < 1000 else 0


def _youtube_search_trailer(yt_dlp, query):
    """[trailers] first YouTube search result for `query` -> watch URL, ''
    when nothing (used only when TMDb lists no trailer)."""
    if not query:
        return ""
    try:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("ytsearch3:" + query, download=False) or {}
        for e in info.get("entries") or []:
            vid = e.get("id")
            dur = e.get("duration") or 0
            if vid and (not dur or dur <= 600):        # a trailer, not a full film / review
                my_log("trailer search '{}' -> {}".format(query[:60], vid))
                return "https://www.youtube.com/watch?v={}".format(vid)
    except Exception as e:
        my_log("trailer search: {}".format(e))
    return ""


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


def _initials(name):
    """[hdr-ui] "Tom Hardy" -> "TH", "Zendaya" -> "Z" (cast without a photo)"""
    words = [w for w in re.split(r"[\s\-]+", str(name or "").strip()) if w and w[0].isalnum()]
    if not words:
        return u"?"
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[-1][0]).upper()


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


from plugin_util import _site_label, _sort_servers            # noqa: E402
from plugin_assets import placeholder_for_item                # noqa: E402


def _get_extractor(site):
    return get_extractor(site)


# every name (also the _private ones) for "from detail_base import *"
__all__ = [_n for _n in list(globals()) if not _n.startswith("__")]
