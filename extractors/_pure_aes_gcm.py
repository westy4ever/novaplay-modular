# -*- coding: utf-8 -*-
"""
Pure-Python AES-256-GCM (decrypt-only, no AAD)
===============================================
[PATCH N2] Third-tier fallback for resolve_n1mwq()'s playback decrypt,
used only when neither `cryptography` nor `pycryptodome` can be imported
-- confirmed necessary, 2026-10-13, directly from a real install attempt
on the user's actual OpenATV/Enigma2 box (pip install cryptography there
fails building cffi's native extension: "No such file or directory:
'arm-oe-linux-gnueabi-gcc'" -- no C compiler on the device at all, so
ANY package needing a compiled extension is a dead end on that hardware,
not just this one). No third-party dependency, no compiled extension --
just the standard algorithms (FIPS-197 AES, NIST SP 800-38D GCM),
self-verified against:
  * the official NIST FIPS-197 Appendix C.3 AES-256 test vector
    (key 000102...1f, plaintext 00112233...ff -> ciphertext
    8ea2b7ca...6089), and
  * the real captured n1mwq.org playback response this session already
    decrypted successfully with `cryptography` -- this module reproduces
    the exact same plaintext (and its GCM tag verifies), byte for byte.

Speed is not a concern here: the playback payload is only a few hundred
bytes (under 40 AES blocks), so even an order-of-magnitude-slower ARM
box is well under a second -- unlike the PoW solve, which is genuinely
CPU-bound over a much larger search space.
"""

_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]

_RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36, 0x6c, 0xd8, 0xab, 0x4d]


def _gmul(a, b):
    """GF(2^8) multiply, reduction poly x^8+x^4+x^3+x+1 (0x1B) -- used by
    AES MixColumns."""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p & 0xFF


def _aes256_key_schedule(key32):
    Nk, Nb, Nr = 8, 4, 14
    w = [list(key32[4 * i:4 * i + 4]) for i in range(Nk)]
    for i in range(Nk, Nb * (Nr + 1)):
        temp = list(w[i - 1])
        if i % Nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // Nk]
        elif Nk > 6 and i % Nk == 4:
            temp = [_SBOX[b] for b in temp]
        w.append([(w[i - Nk][j] ^ temp[j]) & 0xFF for j in range(4)])
    round_keys = []
    for r in range(Nr + 1):
        rk = []
        for c in range(4):
            rk.extend(w[r * 4 + c])
        round_keys.append(bytes(rk))
    return round_keys


def _add_round_key(state, rk):
    return bytes(s ^ k for s, k in zip(state, rk))


def _sub_bytes(state):
    return bytes(_SBOX[b] for b in state)


def _shift_rows(state):
    out = bytearray(16)
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = state[((col + row) % 4) * 4 + row]
    return bytes(out)


def _mix_columns(state):
    out = bytearray(16)
    for c in range(4):
        a0, a1, a2, a3 = state[c * 4], state[c * 4 + 1], state[c * 4 + 2], state[c * 4 + 3]
        out[c * 4 + 0] = _gmul(a0, 2) ^ _gmul(a1, 3) ^ a2 ^ a3
        out[c * 4 + 1] = a0 ^ _gmul(a1, 2) ^ _gmul(a2, 3) ^ a3
        out[c * 4 + 2] = a0 ^ a1 ^ _gmul(a2, 2) ^ _gmul(a3, 3)
        out[c * 4 + 3] = _gmul(a0, 3) ^ a1 ^ a2 ^ _gmul(a3, 2)
    return bytes(out)


def _aes256_encrypt_block_ks(round_keys, block16):
    state = _add_round_key(block16, round_keys[0])
    for rnd in range(1, 14):
        state = _sub_bytes(state)
        state = _shift_rows(state)
        state = _mix_columns(state)
        state = _add_round_key(state, round_keys[rnd])
    state = _sub_bytes(state)
    state = _shift_rows(state)
    state = _add_round_key(state, round_keys[14])
    return state


def aes256_encrypt_block(key32, block16):
    """Single 16-byte-block AES-256 forward-cipher encrypt (FIPS-197).
    Exposed mainly for the module's own self-test against the NIST test
    vector; GCM below uses the key-schedule-reuse path instead."""
    return _aes256_encrypt_block_ks(_aes256_key_schedule(key32), block16)


# ─── GHASH / GCM (NIST SP 800-38D) ──────────────────────────────────────────

_R128 = 0xE1 << 120  # reduction constant for GF(2^128), poly x^128+x^7+x^2+x+1


def _gf128_mul(x_int, y_int):
    z = 0
    v = y_int
    for i in range(128):
        if (x_int >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ _R128
        else:
            v >>= 1
    return z


def _ghash(h_int, data):
    """data must already be padded to a multiple of 16 bytes."""
    y = 0
    for i in range(0, len(data), 16):
        block = int.from_bytes(data[i:i + 16], "big")
        y = _gf128_mul(y ^ block, h_int)
    return y


def _inc32(block16):
    """Increment only the low 32 bits of a 128-bit block (GCM's counter
    increment for a 96-bit IV)."""
    prefix, counter = block16[:12], block16[12:]
    n = (int.from_bytes(counter, "big") + 1) & 0xFFFFFFFF
    return prefix + n.to_bytes(4, "big")


def aes256_gcm_decrypt(key, iv, ciphertext_and_tag):
    """Decrypt + verify AES-256-GCM with no associated data (AAD), as
    n1mwq.org's playback payload uses (confirmed from its own client JS:
    plain {name:"AES-GCM", iv} importKey/decrypt call, no additionalData).
    `ciphertext_and_tag` is ciphertext with the 16-byte GCM tag appended
    (the standard WebCrypto / cryptography.AESGCM convention). Raises
    ValueError on a bad key/IV/tag; returns the plaintext bytes."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM needs a 32-byte key, got {}".format(len(key)))
    if len(iv) != 12:
        raise ValueError("this implementation only supports a 96-bit (12-byte) IV")
    ciphertext, tag = ciphertext_and_tag[:-16], ciphertext_and_tag[-16:]

    round_keys = _aes256_key_schedule(key)
    h_block = _aes256_encrypt_block_ks(round_keys, b"\x00" * 16)
    h_int = int.from_bytes(h_block, "big")

    j0 = iv + b"\x00\x00\x00\x01"

    plaintext = bytearray(len(ciphertext))
    counter_block = j0
    offset = 0
    while offset < len(ciphertext):
        counter_block = _inc32(counter_block)
        ks = _aes256_encrypt_block_ks(round_keys, counter_block)
        chunk = ciphertext[offset:offset + 16]
        for i, b in enumerate(chunk):
            plaintext[offset + i] = b ^ ks[i]
        offset += 16

    pad_len = (-len(ciphertext)) % 16
    ghash_input = ciphertext + b"\x00" * pad_len
    ghash_input += (0).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big")
    s_bytes = _ghash(h_int, ghash_input).to_bytes(16, "big")

    e_j0 = _aes256_encrypt_block_ks(round_keys, j0)
    computed_tag = bytes(a ^ b for a, b in zip(s_bytes, e_j0))

    if computed_tag != tag:
        raise ValueError("AES-GCM tag verification failed (ciphertext corrupt or key wrong)")

    return bytes(plaintext)


# ─── [2026-10-22] any-size AES key schedule + CBC decrypt (Streamp2p) ─────
# Streamp2p (megamax.strp2p.site, EgyDead's "Streamp2p" MegaMax mirror)
# encrypts its API replies with AES-128-CBC; the box has neither
# `cryptography` nor `pycryptodome`. Checked against `cryptography` on the
# real captured reply and the FIPS-197 Appendix C.1 AES-128 vector.

def aes_key_schedule(key):
    """Round keys (list of 16-byte bytes) for a 16/24/32-byte key."""
    Nk = len(key) // 4
    if Nk not in (4, 6, 8):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    Nb, Nr = 4, Nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(Nk)]
    for i in range(Nk, Nb * (Nr + 1)):
        temp = list(w[i - 1])
        if i % Nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // Nk]
        elif Nk > 6 and i % Nk == 4:
            temp = [_SBOX[b] for b in temp]
        w.append([(w[i - Nk][j] ^ temp[j]) & 0xFF for j in range(4)])
    return [bytes(w[r * 4] + w[r * 4 + 1] + w[r * 4 + 2] + w[r * 4 + 3]) for r in range(Nr + 1)]


_INV_SBOX = None


def _inv_tables():
    global _INV_SBOX
    if _INV_SBOX is None:
        inv = [0] * 256
        for i, v in enumerate(_SBOX):
            inv[v] = i
        _INV_SBOX = inv
    return _INV_SBOX


def aes_decrypt_block(round_keys, block):
    inv = _inv_tables()
    nr = len(round_keys) - 1
    s = bytearray(a ^ b for a, b in zip(block, round_keys[nr]))
    for rnd in range(nr - 1, -1, -1):
        # InvShiftRows (state is column-major: index = row + 4*col)
        t = bytearray(16)
        for c in range(4):
            for r in range(4):
                t[r + 4 * ((c + r) % 4)] = s[r + 4 * c]
        # InvSubBytes + AddRoundKey
        s = bytearray(inv[x] ^ k for x, k in zip(t, round_keys[rnd]))
        if rnd:
            m = bytearray(16)
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                m[4 * c] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
                m[4 * c + 1] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
                m[4 * c + 2] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
                m[4 * c + 3] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)
            s = m
    return bytes(s)


def aes_cbc_decrypt(key, iv, data, unpad=True):
    """AES-CBC decrypt (any key size), PKCS#7 padding removed when unpad."""
    if len(data) % 16:
        raise ValueError("ciphertext is not a multiple of 16 bytes")
    rk = aes_key_schedule(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        dec = aes_decrypt_block(rk, blk)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = blk
    if unpad and out:
        n = out[-1]
        if 1 <= n <= 16 and out[-n:] == bytes([n]) * n:
            out = out[:-n]
    return bytes(out)
