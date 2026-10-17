# -*- coding: utf-8 -*-
"""XOR-obfuscated HLS playlists (EmbedMaster "obfHosts").

From embdmstrplayer.com/v2/assets/js/player/em-hls-obf.min.js, verified by
running it: for a host in the player's obfHosts list, every playlist URL
"<name>.m3u8" is fetched as "<name>.txt" and the bytes are XORed with a
fixed 32-byte key (repeating). Segments are fetched normally.

Streams that need this carry the pipe header "X-NP-Xor=<hex key>"; the local
proxy (novaplay_proxy) decodes them, and the player routes them through it.
"""
import re

EM_OBF_KEY = "5d62d1c8759975d6ed1c1387b4d87224c49694b9a24f8217c9efc2bf27f05bc3"
XOR_HEADER = "X-NP-Xor"
_M3U8_RE = re.compile(r"\.m3u8(\?|#|$)", re.I)


def is_playlist_url(url):
    return bool(_M3U8_RE.search(url or ""))


def playlist_fetch_url(url):
    """The URL the player really requests for an obfuscated playlist."""
    return _M3U8_RE.sub(r".txt\1", url, count=1)


def xor_decode(data, key_hex):
    key = bytes.fromhex(key_hex)
    if not key:
        return bytes(data)
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(bytearray(data)))
