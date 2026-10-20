# -*- coding: utf-8 -*-
"""
Cinejoy's own servers (api.wing.st) -- the "lumen-gate-v2" protected API.

Worked out 2026-10-21 from crush.wasm (Rust: p256, hkdf, sha2, aes-gcm) and
the player module of cinejoy.pk, and checked byte for byte against the real
WASM (a request sealed here decrypts with the WASM's keys and vice versa):

  seed (32 random bytes), iv (12 random bytes)
  eph_priv = SHA256("lumen-gate-v2|ephemeral|" + seed + BE32(ctr))
             (ctr = 0, 1, ... until it is a valid P-256 scalar)
  eph_pub  = eph_priv * G                     (65-byte uncompressed point)
  Z        = x-coordinate of eph_priv * SERVER_PUB   (key id 2, in the WASM)
  c2s      = HKDF-SHA256(ikm=Z, salt=eph_pub, info="lumen-gate-v2|c2s", 32)
  s2c      = HKDF-SHA256(ikm=Z, salt=eph_pub, info="lumen-gate-v2|s2c", 32)
  request  = 02 | key_id | eph_pub | iv |
             AES-256-GCM(c2s, iv, aad="lumen-gate-v2" 00 01 key_id eph_pub,
                         JSON {"path": "/<server>/<kind>", "payload": {...}})
  POST https://api.wing.st/g  (text/plain body = request bytes)
  reply    = iv(12) | AES-256-GCM(s2c, iv,
                                  aad="lumen-gate-v2" 00 02 key_id eph_pub)
           -> JSON {"status": 200, "data": {"stream": [ ... ]}}

stream[0] is either {"type": "file", "qualities": {"1080": {"url": ...}}}
or {"playlist": "https://...m3u8", "captions": [...]}.

Pure Python (no compiled packages on the box); `cryptography` is used for
the curve maths when it happens to be installed.
"""
import hashlib
import hmac
import json
import os
import struct

from .net import log

API = "https://api.wing.st"
SITE = "https://cinejoy.pk"
LABEL = b"lumen-gate-v2"
KEY_ID = 2
SERVER_PUB = bytes.fromhex(
    "045c88a0ae33c683a4872590b04b22f4774a4fe1c6ecf74785c74a919a71207ca9"
    "4f9e6b5a7854c3aa3b44ee46bc444fea694b9f23bcd80f864ecbef48c1ef6e14")

# ─── P-256 ────────────────────────────────────────────────────────────────
_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_A = _P - 3
_GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
_GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5


def _jdouble(X, Y, Z):
    if not Y:
        return 0, 1, 0
    YY = Y * Y % _P
    S = 4 * X * YY % _P
    ZZ = Z * Z % _P
    M = 3 * (X - ZZ) * (X + ZZ) % _P
    X3 = (M * M - 2 * S) % _P
    Y3 = (M * (S - X3) - 8 * YY * YY) % _P
    Z3 = 2 * Y * Z % _P
    return X3, Y3, Z3


def _jadd(X1, Y1, Z1, X2, Y2, Z2):
    if not Z1:
        return X2, Y2, Z2
    if not Z2:
        return X1, Y1, Z1
    Z1Z1 = Z1 * Z1 % _P
    Z2Z2 = Z2 * Z2 % _P
    U1 = X1 * Z2Z2 % _P
    U2 = X2 * Z1Z1 % _P
    S1 = Y1 * Z2 * Z2Z2 % _P
    S2 = Y2 * Z1 * Z1Z1 % _P
    if U1 == U2:
        if S1 != S2:
            return 0, 1, 0
        return _jdouble(X1, Y1, Z1)
    H = (U2 - U1) % _P
    R = (S2 - S1) % _P
    HH = H * H % _P
    HHH = H * HH % _P
    V = U1 * HH % _P
    X3 = (R * R - HHH - 2 * V) % _P
    Y3 = (R * (V - X3) - S1 * HHH) % _P
    Z3 = H * Z1 * Z2 % _P
    return X3, Y3, Z3


def _mul(k, x, y):
    """k * (x, y) -> affine (x, y)."""
    RX, RY, RZ = 0, 1, 0
    for bit in bin(k)[2:]:
        RX, RY, RZ = _jdouble(RX, RY, RZ)
        if bit == "1":
            RX, RY, RZ = _jadd(RX, RY, RZ, x, y, 1)
    if not RZ:
        raise ValueError("point at infinity")
    zi = pow(RZ, _P - 2, _P)
    zi2 = zi * zi % _P
    return RX * zi2 % _P, RY * zi2 * zi % _P


def _ecdh(priv_int):
    """-> (eph_pub 65 bytes, shared x 32 bytes)."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.derive_private_key(priv_int, ec.SECP256R1())
        from cryptography.hazmat.primitives import serialization
        pub = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), SERVER_PUB)
        return pub, key.exchange(ec.ECDH(), peer)
    except Exception:
        pass
    px, py = _mul(priv_int, _GX, _GY)
    sx, _sy = _mul(priv_int, int.from_bytes(SERVER_PUB[1:33], "big"),
                   int.from_bytes(SERVER_PUB[33:], "big"))
    return (b"\x04" + px.to_bytes(32, "big") + py.to_bytes(32, "big"),
            sx.to_bytes(32, "big"))


# ─── HKDF / AES-GCM ───────────────────────────────────────────────────────

def _hkdf(ikm, salt, info, length=32):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def _gcm(key, iv, data, aad, encrypt):
    """AES-256-GCM with AAD. encrypt: data=plaintext -> ct+tag;
    decrypt: data=ct+tag -> plaintext (ValueError on a bad tag)."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        g = AESGCM(key)
        return g.encrypt(iv, data, aad) if encrypt else g.decrypt(iv, data, aad)
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES
        c = AES.new(key, AES.MODE_GCM, nonce=iv)
        c.update(aad)
        if encrypt:
            ct, tag = c.encrypt_and_digest(data)
            return ct + tag
        return c.decrypt_and_verify(data[:-16], data[-16:])
    except ImportError:
        pass
    from . import _pure_aes_gcm as P
    rk = P._aes256_key_schedule(key)
    h = int.from_bytes(P._aes256_encrypt_block_ks(rk, b"\x00" * 16), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    if encrypt:
        src, tag_in = data, None
    else:
        src, tag_in = data[:-16], data[-16:]
    out = bytearray(len(src))
    ctr = j0
    for off in range(0, len(src), 16):
        ctr = P._inc32(ctr)
        ks = P._aes256_encrypt_block_ks(rk, ctr)
        for i, b in enumerate(src[off:off + 16]):
            out[off + i] = b ^ ks[i]
    ct = bytes(out) if encrypt else src

    def pad(b):
        return b + b"\x00" * ((-len(b)) % 16)
    s = P._ghash(h, pad(aad) + pad(ct) + struct.pack(">QQ", len(aad) * 8, len(ct) * 8))
    tag = bytes(a ^ b for a, b in zip(s.to_bytes(16, "big"), P._aes256_encrypt_block_ks(rk, j0)))
    if encrypt:
        return ct + tag
    if not hmac.compare_digest(tag, tag_in):
        raise ValueError("GCM tag mismatch")
    return bytes(out)


# ─── seal / open ──────────────────────────────────────────────────────────

def seal(path, payload, seed=None, iv=None):
    """-> (request bytes, s2c key, eph_pub)."""
    seed = seed or os.urandom(32)
    iv = iv or os.urandom(12)
    ctr = 0
    while True:
        k = int.from_bytes(hashlib.sha256(
            LABEL + b"|ephemeral|" + seed + struct.pack(">I", ctr)).digest(), "big")
        if 0 < k < _N:
            break
        ctr += 1
    eph, z = _ecdh(k)
    c2s = _hkdf(z, eph, LABEL + b"|c2s")
    s2c = _hkdf(z, eph, LABEL + b"|s2c")
    msg = json.dumps({"path": path, "payload": payload}, separators=(",", ":")).encode()
    aad = LABEL + bytes([0, 1, KEY_ID]) + eph
    body = bytes([2, KEY_ID]) + eph + iv + _gcm(c2s, iv, msg, aad, True)
    return body, s2c, eph


def open_reply(raw, s2c, eph):
    if len(raw) < 28:
        raise ValueError("reply too short")
    aad = LABEL + bytes([0, 2, KEY_ID]) + eph
    return json.loads(_gcm(s2c, raw[:12], raw[12:], aad, False).decode("utf-8"))


def call(path, payload, timeout=25):
    """POST one sealed request -> decoded envelope {"status", "data"} or None."""
    import urllib.request
    import urllib.error
    body, s2c, eph = seal(path, payload)
    req = urllib.request.Request(API + "/g", data=body, method="POST", headers={
        "Content-Type": "text/plain;charset=UTF-8", "Origin": SITE, "Referer": SITE + "/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        log("wingst: {} -> HTTP {}".format(path, e.code))
        return None
    except Exception as e:
        log("wingst: {} -> {}".format(path, e))
        return None
    try:
        return open_reply(raw, s2c, eph)
    except Exception as e:
        log("wingst: {} reply not readable: {}".format(path, e))
        return None


def servers():
    """[{name, status, 4k}, ...] from api.wing.st/servers."""
    from .net import fetch_json
    d = fetch_json(API + "/servers", referer=SITE + "/", extra_headers={"Origin": SITE}) or {}
    return [s for s in d.get("servers") or [] if isinstance(s, dict) and s.get("name")]


# ─── resolver: https://api.wing.st/play?server=..&kind=..&tmdb=..[&imdb&year&title&season&episode]

_PATH_OK = {}   # (server, is_tv) -> working "/<server>/<kind>" path


def _pick(stream):
    if not isinstance(stream, dict):
        return None
    q = stream.get("qualities")
    if stream.get("type") == "file" and isinstance(q, dict):
        best = None
        for k, v in q.items():
            try:
                h = int(str(k).rstrip("p"))
            except ValueError:
                h = 0
            u = v.get("url") if isinstance(v, dict) else v
            if isinstance(u, str) and u.startswith("http"):
                rank = (h <= 1080, h)
                if best is None or rank > best[0]:
                    best = (rank, u)
        return best[1] if best else None
    pl = stream.get("playlist")
    return pl if isinstance(pl, str) and pl.startswith("http") else None


def resolve(url, referer=None):
    from urllib.parse import urlparse, parse_qs
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    server = q.pop("server", "")
    kind = q.pop("kind", "movie")
    if not server or not q.get("tmdb"):
        return None
    tv = kind != "movie"
    payload = {k: v for k, v in q.items() if v}
    tried = []
    key = (server, tv)
    paths = [_PATH_OK[key]] if key in _PATH_OK else []
    for name in (server, server.lower()):
        for kd in (("tv", "series", "show") if tv else ("movie",)):
            p = "/{}/{}".format(name, kd)
            if p not in paths:
                paths.append(p)
    for path in paths:
        tried.append(path)
        env = call(path, payload)
        if not isinstance(env, dict):
            continue
        status = env.get("status")
        data = env.get("data") or {}
        streams = data.get("stream") if isinstance(data, dict) else None
        if status and 200 <= status < 300 and isinstance(streams, list) and streams:
            u = _pick(streams[0])
            if u:
                _PATH_OK[key] = path
                log("wingst: {} -> {}".format(path, u[:100]))
                hdr = (streams[0].get("headers") or {}) if isinstance(streams[0], dict) else {}
                if isinstance(hdr, dict) and hdr:
                    return u + "|" + "&".join("{}={}".format(k, v) for k, v in hdr.items())
                return u + "|Referer=" + SITE + "/&Origin=" + SITE
        err = data.get("error") if isinstance(data, dict) else None
        log("wingst: {} -> status {} {}".format(path, status, err or ""))
        if status in (200, 204) or (status and status != 404 and status < 500 and err and "path" not in str(err).lower()):
            break      # path accepted but no source: other spellings won't help
    return None
