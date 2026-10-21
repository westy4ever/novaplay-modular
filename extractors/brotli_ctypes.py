# -*- coding: utf-8 -*-
"""Brotli decompression through the system's libbrotlidec (ctypes).

OpenATV 7.6 has no python3-brotli package, only the C library ("opkg install
brotli" -> libbrotlidec.so.1). Sites that answer with Content-Encoding: br
then couldn't be read by urllib. This module gives the same API as the
`brotli` Python package for what NovaPlay uses: decompress(bytes) -> bytes.
"""
import ctypes
import ctypes.util

_lib = None
for _name in ("libbrotlidec.so.1", ctypes.util.find_library("brotlidec") or "", "libbrotlidec.so"):
    if not _name:
        continue
    try:
        _lib = ctypes.CDLL(_name)
        break
    except OSError:
        _lib = None

if _lib is None:
    raise ImportError("libbrotlidec not found (opkg install brotli)")

_size_t = ctypes.c_size_t
_u8p = ctypes.POINTER(ctypes.c_uint8)

_lib.BrotliDecoderCreateInstance.restype = ctypes.c_void_p
_lib.BrotliDecoderCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_lib.BrotliDecoderDestroyInstance.restype = None
_lib.BrotliDecoderDestroyInstance.argtypes = [ctypes.c_void_p]
_lib.BrotliDecoderDecompressStream.restype = ctypes.c_int
_lib.BrotliDecoderDecompressStream.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(_size_t), ctypes.POINTER(_u8p),
    ctypes.POINTER(_size_t), ctypes.POINTER(_u8p),
    ctypes.POINTER(_size_t)]

_SUCCESS, _NEEDS_INPUT, _NEEDS_OUTPUT = 1, 2, 3


class error(Exception):
    pass


def decompress(data):
    data = bytes(data)
    state = _lib.BrotliDecoderCreateInstance(None, None, None)
    if not state:
        raise error("brotli: could not create a decoder")
    try:
        inbuf = (ctypes.c_uint8 * max(1, len(data))).from_buffer_copy(data or b"\0")
        avail_in = _size_t(len(data))
        next_in = ctypes.cast(inbuf, _u8p)
        chunk = 256 * 1024
        outbuf = (ctypes.c_uint8 * chunk)()
        out = []
        while True:
            avail_out = _size_t(chunk)
            next_out = ctypes.cast(outbuf, _u8p)
            res = _lib.BrotliDecoderDecompressStream(
                state, ctypes.byref(avail_in), ctypes.byref(next_in),
                ctypes.byref(avail_out), ctypes.byref(next_out), None)
            produced = chunk - avail_out.value
            if produced:
                out.append(ctypes.string_at(outbuf, produced))
            if res == _SUCCESS:
                return b"".join(out)
            if res == _NEEDS_OUTPUT:
                continue
            if res == _NEEDS_INPUT:
                raise error("brotli: truncated input")
            raise error("brotli: corrupt input")
    finally:
        _lib.BrotliDecoderDestroyInstance(state)
