# -*- coding: utf-8 -*-
"""Byse / n1mwq captcha proof-of-work, vectorised with numpy.

The scalar port in hosts.py (_n1mwq_pow_hash) is ~2,000 ChaCha-style mixing
rounds per hash in plain Python: 273 hashes/s on a PC and far fewer on the
box, while difficulty 16 needs ~65,000 tries inside the ~20 s the challenge
lives (box 2026-10-21: "PoW not solved within 20000 ms"). Here thousands of
candidate suffixes are hashed at once, one numpy array per state word, so the
Python overhead is paid per batch instead of per hash. Results are identical
to the scalar version (checked in tests and against the real solved challenge
nonce "kkJaZM4KOitHQp8OXdB3MVnC", difficulty 16 -> "4078").
"""
import time

import numpy as np

_BLOCK = 512
_MASK = _BLOCK - 1
_MUL1 = np.uint32(2654435761)
_MUL2 = np.uint32(2246822519)
_IV = (1779033703, 3144134277, 1013904242, 2773480762)


def _rotl(x, n):
    return (x << np.uint32(n)) | (x >> np.uint32(32 - n))


def _mix(e):
    e0, e1, e2, e3 = e
    e0 = e0 + e1
    e3 = _rotl(e3 ^ e0, 16)
    e2 = e2 + e3
    e1 = _rotl(e1 ^ e2, 12)
    e0 = e0 + e1
    e3 = _rotl(e3 ^ e0, 8)
    e2 = e2 + e3
    e1 = _rotl(e1 ^ e2, 7)
    e[0], e[1], e[2], e[3] = e0, e1, e2, e3


def _prefix_state(prefix):
    """The shared "<nonce>:" part, once, in plain Python (same as gr())."""
    def rotl(x, n):
        return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF

    e = list(_IV)
    for b in prefix:
        e[0] = rotl((e[0] + b) & 0xFFFFFFFF, 7)
        e[0] = (e[0] + e[1]) & 0xFFFFFFFF; e[3] = rotl(e[3] ^ e[0], 16)
        e[2] = (e[2] + e[3]) & 0xFFFFFFFF; e[1] = rotl(e[1] ^ e[2], 12)
        e[0] = (e[0] + e[1]) & 0xFFFFFFFF; e[3] = rotl(e[3] ^ e[0], 8)
        e[2] = (e[2] + e[3]) & 0xFFFFFFFF; e[1] = rotl(e[1] ^ e[2], 7)
    return e


def hash_batch(prefix_state, start, count, ndigits):
    """Hashes of prefix + str(n) for n in [start, start+count), all with
    `ndigits` digits. Returns a (count, 8) uint32 array."""
    k = count
    nums = np.arange(start, start + count, dtype=np.int64)
    e = [np.full(k, v, dtype=np.uint32) for v in prefix_state]
    for j in range(ndigits):
        d = ((nums // (10 ** (ndigits - 1 - j))) % 10 + 48).astype(np.uint32)
        e[0] = _rotl(e[0] + d, 7)
        _mix(e)
    for _ in range(8):
        _mix(e)
    r = np.empty((_BLOCK, k), dtype=np.uint32)          # row = position, column = candidate
    for i in range(_BLOCK):
        _mix(e)
        r[i] = e[0] ^ e[2]
    cols = np.arange(k)
    for _ in range(2):
        for s in range(_BLOCK):
            a = (r[s] & np.uint32(_MASK)).astype(np.intp)
            c = r[s] + r[a, cols]
            c = _rotl(c, 13)
            c = c ^ (r[(s + 1) & _MASK] * _MUL1)
            r[s] = c
            e[0] = e[0] ^ c
            _mix(e)
    out = np.empty((8, k), dtype=np.uint32)
    chunk = _BLOCK // 8
    for i in range(8):
        _mix(e)
        s = e[0].copy()
        base = i * chunk
        for c in range(chunk):
            d = r[base + c]
            s = _rotl(s + d, 5)
            s = s ^ (d * _MUL2)
        out[i] = s ^ e[2]
    return out.T


def _leading_zero_bits(words):
    """Leading zero bits across each row of a (k, 8) uint32 array."""
    total = np.zeros(words.shape[0], dtype=np.int64)
    alive = np.ones(words.shape[0], dtype=bool)
    for i in range(words.shape[1]):
        w = words[:, i].astype(np.int64)
        bl = np.zeros_like(w)
        nz = w != 0
        bl[nz] = np.floor(np.log2(w[nz])).astype(np.int64) + 1
        total += np.where(alive, 32 - bl, 0)
        alive &= ~nz
    return total


def solve(nonce, difficulty, timeout_ms=20000, batch=4096, log=None):
    """Smallest-ish integer suffix s with lzb(hash(nonce + ":" + str(s)))
    >= difficulty, searched in order like the page does. None on timeout."""
    if not nonce:
        return None
    if not difficulty or difficulty <= 0:
        return "0"
    prefix = (nonce + ":").encode("ascii", errors="ignore")
    ps = _prefix_state(prefix)
    t0 = time.time()
    s = 0
    while (time.time() - t0) * 1000 < timeout_ms:
        nd = len(str(s))
        end = min(s + batch, 10 ** nd)            # a batch never mixes digit counts
        h = hash_batch(ps, s, end - s, nd)
        ok = np.nonzero(_leading_zero_bits(h) >= difficulty)[0]
        if ok.size:
            sol = s + int(ok[0])
            if log:
                log("byse pow: solved difficulty {} -> {} in {:.1f} s".format(
                    difficulty, sol, time.time() - t0))
            return str(sol)
        s = end
    if log:
        log("byse pow: not solved within {} ms (difficulty {}, tried {})".format(
            timeout_ms, difficulty, s))
    return None


if __name__ == "__main__":
    # speed check on the box:  python3 extractors/byse_pow.py
    t = time.time()
    r = solve("kkJaZM4KOitHQp8OXdB3MVnC", 16, timeout_ms=120000)
    dt = time.time() - t
    ps = _prefix_state(b"bench:")
    t2 = time.time()
    hash_batch(ps, 100000, 4096, 6)
    rate = 4096 / (time.time() - t2)
    print("known challenge: {} (expected 4078) in {:.1f} s".format(r, dt))
    print("speed: {:.0f} hashes/s -> a typical difficulty-16 captcha (~65,000 tries) "
          "takes about {:.0f} s".format(rate, 65536 / rate))
