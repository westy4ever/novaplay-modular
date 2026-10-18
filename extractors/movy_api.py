# -*- coding: utf-8 -*-
"""
Movy's own servers (Miami, Boise, Vegas ... Cancun) -- api.wecollege.net.

From a Stream Recorder Pro capture of www.movy.sx playing "Matchbox the
Movie" (TMDB 1148365) on Miami, plus the player chunk that builds the calls:

  1. GET https://api.wecollege.net/seed?mediaId=<tmdb_id>
        -> {"seed": "59743888.5dtytfXE4E_SIcT-QhtELq", "ttlMs": 30000}
  2. GET https://api.wecollege.net/<city>/sources?title=<enc(enc(title))>
        &mediaType=movie|tv&year=..&[totalSeasons=..&]episodeId=..&seasonId=..
        &tmdbId=..&imdbId=..&enc=2&seed=<seed>
        -> base64url text; decrypt() below gives
        {"sources": [{"quality": "1080p", "url": ".../index-s1080p-v1-a1.m3u8"}, ...],
         "subtitles": [...], "thumbnail": "...", "playlist": ".../master.m3u8"}
     (a 401 means the seed expired: fetch a new one and ask again)

The cipher is a small custom stream cipher keyed on (seed, tmdb id); the
plaintext starts with the magic "mvm1". The streams are fMP4 HLS on
moon.zenoak.top / tinyeagle.top (the same CDN as VidCore/VidFast), fetched
with Referer/Origin https://www.movy.sx.

Server URLs handed to NovaPlay are the real sources URL without enc/seed,
so resolve_movy() only has to add a fresh seed.
"""
import base64
import json
import re
import time
from urllib.parse import urlparse, parse_qs, quote, urlencode

from .net import log

API = "https://api.wecollege.net"
SITE = "https://www.movy.sx"

# (name, path, note, keep only sources whose "quality" equals this)
SERVERS = (
    ("Miami", "miami", "may have 4K", None),
    ("Boise", "boise", "may have 4K", None),
    ("Vegas", "vegas", "", None),
    ("Reno", "reno", "", None),
    ("Oakland", "oakland", "", None),
    ("Seattle", "seattle", "", None),
    ("Phoenix", "phoenix", "", None),
    ("Atlanta", "atlanta", "", None),
    ("Portland", "portland", "", None),
    ("Austin", "austin", "", "English"),
    ("Dallas", "dallas", "", None),
    ("Tampa", "tampa", "", None),
    ("Orlando", "orlando", "", None),
    ("Munich", "munich", "German audio", None),
    ("Berlin", "berlin", "German audio", None),
    ("Paris", "paris", "French audio", None),
    ("Delhi", "delhi", "Hindi audio", "Hindi"),
    ("Cancun", "cancun", "Spanish audio", None),
)
_FILTER = {p: f for _n, p, _note, f in SERVERS}

# ─── cipher (port of the player's decoder) ──────────────────────────────

_M = 0xFFFFFFFF
_G = 0x9E3779B9
_MAGIC = b"mvm1"


def _mix(e):
    e &= _M
    e ^= e >> 16
    e = (e * 0x85EBCA6B) & _M
    e ^= e >> 13
    e = (e * 0xC2B2AE35) & _M
    e ^= e >> 16
    return e


def _rotl(e, a):
    e &= _M
    a &= 31
    return e if a == 0 else ((e << a) | (e >> (32 - a))) & _M


def _u32(x):
    try:
        return int(float(x)) & _M
    except (TypeError, ValueError):
        return 0


def decrypt(payload, seed, media_id):
    s = (payload or "").strip().replace("-", "+").replace("_", "/")
    data = base64.b64decode(s + "=" * (-len(s) % 4))
    h = 0x811C9DC5
    for ch in seed:
        h = ((h ^ ord(ch)) * 0x1000193) & _M
    n = _mix(_mix(h) ^ _mix(_u32(media_id) ^ _G))
    S = {}
    for e in range(8):
        a = n % 61
        n = _rotl(n + _G, 7 + (7 & e))
        S[a] = (n ^ _mix(n)) & _M
        n = _mix(n + a)
    acc = _mix(0xA5A5A5A5 ^ n)
    ks = bytearray()
    k = 0
    while len(ks) < len(data):
        l = acc
        r = l % 61
        d = _M if r in S else 0
        sv = (S.get(r, 0) ^ ((_G * (k + 1)) & _M)) & _M
        b = ((l ^ sv) | (l & sv & d)) & _M
        b = (_rotl(b + l, r & 31) ^ _rotl(l, (r * 7) & 31)) & _M
        l = _mix(b + _G)
        S[r] = l
        acc = l
        k += 1
        ks += l.to_bytes(4, "little")
    out = bytes(x ^ y for x, y in zip(data, ks))
    if out[:4] != _MAGIC:
        raise ValueError("decrypt failed: bad seed or tampered payload")
    return out[4:].decode("utf-8")


# ─── server list ────────────────────────────────────────────────────────

def server_urls(tmdb_id, title, year, imdb_id="", season=None, episode=None, total_seasons=None):
    """[{name, url, quality}] -- one per Movy city server."""
    params = [("title", quote(title or "", safe="")),
              ("mediaType", "tv" if season else "movie"),
              ("year", str(year or ""))]
    if season and total_seasons:
        params.append(("totalSeasons", str(total_seasons)))
    params += [("episodeId", str(episode or 1)), ("seasonId", str(season or 1)),
               ("tmdbId", str(tmdb_id)), ("imdbId", imdb_id or "")]
    q = urlencode(params)
    out = []
    for name, path, note, _f in SERVERS:
        label = "Movy {}".format(name) + (" ({})".format(note) if note else "")
        out.append({"name": label, "url": "{}/{}/sources?{}".format(API, path, q),
                    "quality": "4K" if "4K" in note else "HD"})
    return out


# ─── resolver ───────────────────────────────────────────────────────────

_SEEDS = {}


def _hdrs():
    return {"Referer": SITE + "/", "Origin": SITE, "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}


def _seed(media_id, fresh=False):
    from .vidvm import _http
    now = time.time()
    c = _SEEDS.get(media_id)
    if c and not fresh and c[1] - 5 > now:
        return c[0]
    st, text, _ = _http("{}/seed?mediaId={}".format(API, media_id), headers=_hdrs(), timeout=10)
    if st != 200:
        log("movy: seed HTTP {} {!r}".format(st, (text or "")[:80]))
        return None
    j = json.loads(text)
    _SEEDS[media_id] = (j["seed"], now + (j.get("ttlMs") or 30000) / 1000.0)
    return j["seed"]


def _sources(url):
    from .vidvm import _http
    qs = parse_qs(urlparse(url).query)
    media_id = (qs.get("tmdbId") or [""])[0]
    for attempt in (0, 1):
        seed = _seed(media_id, fresh=bool(attempt))
        if not seed:
            return None
        st, text, _ = _http(url + "&enc=2&seed=" + quote(seed, safe=""), headers=_hdrs(), timeout=20)
        if st == 401 and not attempt:
            continue
        if st != 200 or not text:
            log("movy: sources HTTP {} {!r}".format(st, (text or "")[:80]))
            return None
        t = text.strip()
        if t.startswith("{"):
            return json.loads(t)
        return json.loads(decrypt(t, seed, media_id))
    return None


def _height(label):
    m = re.search(r"(\d{3,4})p", label or "")
    return int(m.group(1)) if m else 0


def resolve_movy(url, referer=None):
    """api.wecollege.net/<city>/sources?... -> 'stream|Referer=..&Origin=..&User-Agent=..'"""
    try:
        return _resolve(url)
    except Exception as e:
        log("movy: {} failed: {}".format(url[:80], e))
        return None


def _resolve(url):
    from .vidvm import _probe, UA
    city = urlparse(url).path.strip("/").split("/")[0]
    data = _sources(url) or {}
    srcs = [s for s in (data.get("sources") or []) if isinstance(s, dict) and s.get("url")]
    want = _FILTER.get(city)
    if want:
        srcs = [s for s in srcs if s.get("quality") == want] or srcs
    log("movy: {} -> {} source(s): {}".format(city, len(srcs), ", ".join(str(s.get("quality")) for s in srcs)))
    if not srcs:
        log("movy: {} has no playable source (reply keys: {}, {!r})".format(
            city, list(data.keys()), json.dumps(data)[:200]))
        return None
    hdrs = {"Referer": SITE + "/", "Origin": SITE, "User-Agent": UA}
    suffix = "|" + "&".join("{}={}".format(k, v) for k, v in hdrs.items())
    # highest first; the box plays up to 1080p by default, bigger ones are offered
    srcs.sort(key=lambda s: -_height(s.get("quality")))
    fits = [s for s in srcs if 0 < _height(s.get("quality")) <= 1080] or srcs
    chosen, text = None, ""
    for s_ in fits + [s for s in srcs if s not in fits]:
        ok, text = _probe(s_["url"], hdrs)
        if ok:
            chosen = s_["url"]
            break
    if not chosen:
        log("movy: {} - no playlist loaded".format(city))
        return None
    try:
        import novaplay_proxy
        novaplay_proxy.remember_playlist(chosen, text, chosen, keep=3 * 3600)
    except Exception as e:
        log("movy: could not hand the playlist to the proxy: {}".format(e))
    others = [s["url"] for s in srcs if s["url"] != chosen and s["url"].endswith(".m3u8")]
    if others:
        _offer(chosen + suffix, others, suffix, hdrs)
    log("movy: stream {} [{}]".format(chosen[-60:], city))
    return chosen + suffix


def _offer(full, others, suffix, hdrs):
    """Fill NovaPlay's quality menu and pre-load those playlists into the proxy
    (the CDN refuses the proxy's own playlist requests on the box)."""
    import threading
    from .vidvm import _probe
    try:
        from .htmlmedia import _quality_tls
        _quality_tls.variants = [full] + [u + suffix for u in others]
    except Exception as e:
        log("movy: could not list qualities: {}".format(e))
        return
    try:
        import novaplay_proxy
    except Exception:
        return

    def _one(u):
        ok, t = _probe(u, hdrs)
        if ok:
            novaplay_proxy.remember_playlist(u, t, u, keep=3 * 3600)
    ts = [threading.Thread(target=_one, args=(u,)) for u in others]
    for t in ts:
        t.start()
    for t in ts:
        t.join(12)
