# -*- coding: utf-8 -*-
"""
Browser-ish global objects for jsvm.py (String, Math, Buffer, crypto,
window, navigator, ...).  Only what the VidCore / VidFast programs touch,
plus the obvious neighbours.
"""

import base64
import json
import math
import os
import random
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, parse_qsl, urlencode

from .jsrt import (
    UNDEF, JSArray, JSObject, JSFunction, JSBigInt, JSPromise, JSRegExp, JSThrow,
    JSError, TypedArray, _TA_INFO, arg, to_str, to_number, to_integer, to_int32,
    to_uint32, num_to_str, str_to_num, norm_num, truthy, js_throw, make_error,
    new_buffer, new_typed, as_bytes, buffer_from_string, buffer_tostring,
    iterate, own_keys, json_parse, json_stringify, get_prop, set_prop,
    call_function, construct, make_crypto, promise_resolve, await_value,
    has_property, set_static, strict_eq,
)


def _fn(name, f, ctor=None, pycls=None):
    return JSFunction(f, name, ctor, pycls)


def _obj(**kw):
    o = JSObject()
    for k, v in kw.items():
        o[k] = v
    return o


# ─── String / Number / BigInt / Boolean ─────────────────────────────────

def _string_call(t, a):
    if not a:
        return ""
    v = a[0]
    if isinstance(v, JSObject) and v.cls == "Symbol":
        return "Symbol({})".format(to_str(v.get("description", "")))
    return to_str(v)


def _from_char_code(t, a):
    return "".join(chr(to_uint32(x) & 0xFFFF) for x in a)


def _from_code_point(t, a):
    return "".join(chr(to_integer(x)) for x in a)


def _number_call(t, a):
    if not a:
        return 0
    v = a[0]
    if isinstance(v, JSBigInt):
        return norm_num(float(int(v))) if abs(int(v)) > 2 ** 53 else int(v)
    return to_number(v)


def _parse_int(t, a):
    s = to_str(arg(a, 0)).strip(" \t\n\r\v\f ﻿")
    r = to_int32(arg(a, 1)) if arg(a, 1) is not UNDEF else 0
    sign = 1
    if s[:1] in ("-", "+"):
        if s[0] == "-":
            sign = -1
        s = s[1:]
    if r == 0:
        r = 10
        if s[:2].lower() == "0x":
            r = 16
            s = s[2:]
    elif r == 16 and s[:2].lower() == "0x":
        s = s[2:]
    if r < 2 or r > 36:
        return float("nan")
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"[:r]
    i = 0
    while i < len(s) and s[i].lower() in digits:
        i += 1
    if i == 0:
        return float("nan")
    v = int(s[:i], r) * sign
    if abs(v) > 2 ** 53:
        return float(v)
    if v == 0 and sign < 0:
        return -0.0
    return v


def _parse_float(t, a):
    import re
    s = to_str(arg(a, 0)).strip()
    m = re.match(r"[+-]?(Infinity|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)", s)
    if not m:
        return float("nan")
    if "Infinity" in m.group(0):
        return float("-inf") if m.group(0).startswith("-") else float("inf")
    return norm_num(float(m.group(0)))


def _is_nan(t, a):
    x = to_number(arg(a, 0))
    return isinstance(x, float) and x != x


def _is_finite(t, a):
    x = to_number(arg(a, 0))
    return not (isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))))


def _bigint_call(t, a):
    v = arg(a, 0)
    if isinstance(v, JSBigInt):
        return v
    if v is True or v is False:
        return JSBigInt(int(v))
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not v.is_integer():
            js_throw("The number {} cannot be converted to a BigInt because it is not an integer".format(num_to_str(v)), "RangeError")
        return JSBigInt(int(v))
    if isinstance(v, str):
        s = v.strip()
        try:
            if s[:2].lower() == "0x":
                return JSBigInt(int(s[2:], 16))
            if s[:2].lower() == "0o":
                return JSBigInt(int(s[2:], 8))
            if s[:2].lower() == "0b":
                return JSBigInt(int(s[2:], 2))
            return JSBigInt(int(s or "0"))
        except ValueError:
            js_throw("Cannot convert {} to a BigInt".format(v), "SyntaxError")
    js_throw("Cannot convert {} to a BigInt".format(to_str(v)))


# ─── Math ───────────────────────────────────────────────────────────────

def _imul(t, a):
    x = to_uint32(arg(a, 0))
    y = to_uint32(arg(a, 1))
    r = (x * y) & 0xFFFFFFFF
    return r - 0x100000000 if r & 0x80000000 else r


def _m1(f):
    def g(t, a):
        x = to_number(arg(a, 0))
        try:
            return norm_num(f(x))
        except (ValueError, OverflowError):
            return float("nan")
    return g


def _round(x):
    if x != x or x in (float("inf"), float("-inf")):
        return x
    return math.floor(x + 0.5)


def _max(t, a):
    if not a:
        return float("-inf")
    vals = [to_number(x) for x in a]
    if any(isinstance(v, float) and v != v for v in vals):
        return float("nan")
    return max(vals)


def _min(t, a):
    if not a:
        return float("inf")
    vals = [to_number(x) for x in a]
    if any(isinstance(v, float) and v != v for v in vals):
        return float("nan")
    return min(vals)


def _pow(t, a):
    try:
        return norm_num(float(to_number(arg(a, 0))) ** float(to_number(arg(a, 1))))
    except (OverflowError, ZeroDivisionError):
        return float("inf")


def _trunc(x):
    if x != x or x in (float("inf"), float("-inf")):
        return x
    return int(x)


def _clz32(t, a):
    x = to_uint32(arg(a, 0))
    return 32 - x.bit_length()


def _sign(x):
    if x != x:
        return x
    return (x > 0) - (x < 0)


def make_math(rand):
    return _obj(
        imul=_fn("imul", _imul), ceil=_fn("ceil", _m1(lambda x: x if (x != x or abs(x) == float("inf")) else math.ceil(x))),
        floor=_fn("floor", _m1(lambda x: x if (x != x or abs(x) == float("inf")) else math.floor(x))),
        round=_fn("round", _m1(_round)), abs=_fn("abs", _m1(abs)), max=_fn("max", _max),
        min=_fn("min", _min), pow=_fn("pow", _pow), trunc=_fn("trunc", _m1(_trunc)),
        sqrt=_fn("sqrt", _m1(math.sqrt)), sign=_fn("sign", _m1(_sign)),
        log=_fn("log", _m1(math.log)), log2=_fn("log2", _m1(math.log2)),
        log10=_fn("log10", _m1(math.log10)), exp=_fn("exp", _m1(math.exp)),
        sin=_fn("sin", _m1(math.sin)), cos=_fn("cos", _m1(math.cos)),
        atan2=_fn("atan2", lambda t, a: math.atan2(to_number(arg(a, 0)), to_number(arg(a, 1)))),
        clz32=_fn("clz32", _clz32), fround=_fn("fround", _m1(lambda x: x)),
        random=_fn("random", lambda t, a: rand()),
        PI=math.pi, E=math.e, LN2=math.log(2), LN10=math.log(10),
    )


# ─── Array / Object / JSON ──────────────────────────────────────────────

def _array_ctor(a):
    if len(a) == 1 and isinstance(a[0], (int, float)) and not isinstance(a[0], bool):
        return JSArray([UNDEF] * to_integer(a[0]))
    return JSArray(a)


def _array_from(t, a):
    src = arg(a, 0)
    mf = arg(a, 1)
    if isinstance(src, JSObject) and "length" in src and not isinstance(src, JSMap):
        items = [src.get(str(i), UNDEF) for i in range(to_integer(src["length"]))]
    elif src is UNDEF or src is None:
        js_throw("undefined is not iterable")
    else:
        items = iterate(src)
    if isinstance(mf, JSFunction):
        return JSArray(mf.fn(UNDEF, [x, i]) for i, x in enumerate(items))
    return JSArray(items)


def _object_keys(t, a):
    return JSArray(own_keys(arg(a, 0)))


def _object_values(t, a):
    o = arg(a, 0)
    return JSArray(get_prop(o, k) for k in own_keys(o))


def _object_entries(t, a):
    o = arg(a, 0)
    return JSArray(JSArray([k, get_prop(o, k)]) for k in own_keys(o))


def _object_assign(t, a):
    target = arg(a, 0)
    for src in a[1:]:
        if src is UNDEF or src is None:
            continue
        for k in own_keys(src):
            set_prop(target, k, get_prop(src, k))
    return target


def _object_create(t, a):
    o = JSObject()
    p = arg(a, 0)
    if isinstance(p, JSObject):
        o.proto = p
    return o


def _object_define_property(t, a):
    o = arg(a, 0)
    k = arg(a, 1)
    d = arg(a, 2)
    if isinstance(d, JSObject):
        if "value" in d:
            set_prop(o, k, d["value"])
        elif "get" in d and isinstance(d["get"], JSFunction):
            set_prop(o, k, d["get"].fn(o, []))
    return o


def _object_from_entries(t, a):
    o = JSObject()
    for e in iterate(arg(a, 0)):
        set_prop(o, get_prop(e, 0), get_prop(e, 1))
    return o


def _get_proto(t, a):
    o = arg(a, 0)
    if isinstance(o, JSObject):
        return o.proto
    return None


def _object_call(a):
    v = arg(a, 0)
    if v is UNDEF or v is None:
        return JSObject()
    return v


def make_object_ctor():
    f = JSFunction(lambda t, a: _object_call(a), "Object", lambda a: _object_call(a), JSObject)
    for name, fn in (("keys", _object_keys), ("values", _object_values), ("entries", _object_entries),
                     ("assign", _object_assign), ("create", _object_create),
                     ("defineProperty", _object_define_property), ("fromEntries", _object_from_entries),
                     ("getPrototypeOf", _get_proto),
                     ("freeze", lambda t, a: arg(a, 0)), ("seal", lambda t, a: arg(a, 0)),
                     ("isFrozen", lambda t, a: False),
                     ("getOwnPropertyNames", _object_keys),
                     ("is", lambda t, a: strict_eq(arg(a, 0), arg(a, 1)))):
        set_static(f, name, JSFunction(fn, name))
    protos = JSObject()
    protos["hasOwnProperty"] = JSFunction(
        lambda t, a: isinstance(t, JSObject) and to_str(arg(a, 0)) in dict.keys(t), "hasOwnProperty")
    f.props["prototype"] = protos
    return f


# ─── Map / Set / WeakMap ────────────────────────────────────────────────

def _mkey(k):
    if isinstance(k, (JSObject, JSArray, TypedArray, JSFunction)):
        return ("o", id(k))
    if isinstance(k, float) and k != k:
        return ("nan",)
    if isinstance(k, bool):
        return ("b", k)
    if isinstance(k, (int, float)) and not isinstance(k, JSBigInt):
        return ("n", k)
    return (type(k).__name__, k)


class JSMap(JSObject):
    pass


def _new_map(a, is_set=False, weak=False):
    m = JSMap()
    m.cls = "Set" if is_set else "Map"
    store = {}

    def size_get():
        return len(store)

    def get(t, x):
        e = store.get(_mkey(arg(x, 0)))
        return e[1] if e else UNDEF

    def set_(t, x):
        store[_mkey(arg(x, 0))] = (arg(x, 0), arg(x, 1))
        return m

    def add(t, x):
        store[_mkey(arg(x, 0))] = (arg(x, 0), arg(x, 0))
        return m

    def has(t, x):
        return _mkey(arg(x, 0)) in store

    def delete(t, x):
        return store.pop(_mkey(arg(x, 0)), None) is not None

    def clear(t, x):
        store.clear()
        return UNDEF

    def for_each(t, x):
        cb = arg(x, 0)
        for k, v in list(store.values()):
            cb.fn(UNDEF, [v, k, m])
        return UNDEF
    dict.__setitem__(m, "get", JSFunction(get, "get"))
    dict.__setitem__(m, "set", JSFunction(set_, "set"))
    dict.__setitem__(m, "add", JSFunction(add, "add"))
    dict.__setitem__(m, "has", JSFunction(has, "has"))
    dict.__setitem__(m, "delete", JSFunction(delete, "delete"))
    dict.__setitem__(m, "clear", JSFunction(clear, "clear"))
    dict.__setitem__(m, "forEach", JSFunction(for_each, "forEach"))
    dict.__setitem__(m, "keys", JSFunction(lambda t, x: JSArray(k for k, v in store.values()), "keys"))
    dict.__setitem__(m, "values", JSFunction(lambda t, x: JSArray(v for k, v in store.values()), "values"))
    dict.__setitem__(m, "entries", JSFunction(lambda t, x: JSArray(JSArray([k, v]) for k, v in store.values()), "entries"))
    m.iter_values = (lambda: [v for k, v in store.values()]) if is_set else (
        lambda: [JSArray([k, v]) for k, v in store.values()])
    m._size = size_get
    init = arg(a, 0)
    if init is not UNDEF and init is not None:
        for e in iterate(init):
            if is_set:
                add(None, [e])
            else:
                set_(None, [get_prop(e, 0), get_prop(e, 1)])
    return m


class _SizeHook(object):
    @staticmethod
    def get_prop(o, key):
        if key == "size":
            return o._size()
        return UNDEF


def _map_ctor(is_set=False):
    def c(a):
        m = _new_map(a, is_set)
        m.cls = _SizeHook
        return m
    return c


# patch iterate() to know Map/Set
from . import jsrt as _jsrt  # noqa: E402
_orig_iterate = _jsrt.iterate


def _iterate2(v):
    if isinstance(v, JSMap):
        return v.iter_values()
    return _orig_iterate(v)


_jsrt.iterate = _iterate2
iterate = _iterate2


# ─── typed array constructors / Buffer ──────────────────────────────────

def _ta_ctor(kind):
    def ctor(a):
        x = arg(a, 0)
        if x is UNDEF:
            return new_typed(kind, 0)
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            return new_typed(kind, to_integer(x))
        if isinstance(x, TypedArray) and len(a) > 1 and kind in ("Uint8Array", "Int8Array"):
            # new Uint8Array(buffer, byteOffset, length) -> view
            off = to_integer(arg(a, 1))
            n = (x.n - off) if arg(a, 2) is UNDEF else to_integer(a[2])
            return TypedArray(kind, x.buf, x.off + off, n)
        vals = x.tolist() if isinstance(x, TypedArray) else iterate(x)
        t = new_typed(kind, len(vals))
        for i, v in enumerate(vals):
            t.set(i, v)
        return t
    return ctor


def make_typed_ctor(kind):
    c = _ta_ctor(kind)
    f = JSFunction(lambda t, a: js_throw("Constructor {} requires 'new'".format(kind)), kind, c, kind)
    set_static(f, "from", JSFunction(lambda t, a: c([JSArray(_array_from(t, a))]), "from"))
    set_static(f, "of", JSFunction(lambda t, a: c([JSArray(a)]), "of"))
    f.props["BYTES_PER_ELEMENT"] = _TA_INFO[kind][0]
    return f


def _buffer_from(t, a):
    x = arg(a, 0)
    enc = arg(a, 1)
    if isinstance(x, str):
        return new_buffer(buffer_from_string(x, "utf8" if enc is UNDEF else to_str(enc)))
    if isinstance(x, TypedArray):
        if x.bytes1:
            if len(a) > 1 and isinstance(enc, (int, float)):
                off = to_integer(enc)
                n = (x.n - off) if arg(a, 2) is UNDEF else to_integer(a[2])
                return TypedArray("Uint8Array", x.buf, x.off + off, n, True)
            return new_buffer(x.tobytes())
        return new_buffer(bytes(to_int32(v) & 255 for v in x.tolist()))
    if isinstance(x, list):
        return new_buffer(bytes(to_int32(v) & 255 for v in x))
    if isinstance(x, JSObject) and x.get("type") == "Buffer":
        return new_buffer(bytes(to_int32(v) & 255 for v in x.get("data", [])))
    js_throw("The first argument must be of type string or an instance of Buffer, ArrayBuffer, or Array")


def _buffer_alloc(t, a):
    n = to_integer(arg(a, 0))
    b = new_buffer(bytes(n))
    f = arg(a, 1)
    if f is not UNDEF:
        if isinstance(f, str):
            pat = buffer_from_string(f, "utf8" if arg(a, 2) is UNDEF else to_str(a[2]))
        elif isinstance(f, TypedArray):
            pat = f.tobytes()
        else:
            pat = bytes([to_int32(f) & 255])
        if pat:
            b.buf[:] = (pat * (n // len(pat) + 1))[:n]
    return b


def _buffer_concat(t, a):
    lst = iterate(arg(a, 0))
    data = b"".join(as_bytes(x) for x in lst)
    if arg(a, 1) is not UNDEF:
        n = to_integer(a[1])
        data = (data + bytes(max(0, n - len(data))))[:n]
    return new_buffer(data)


def make_buffer():
    ctor = lambda a: _buffer_from(None, a) if not isinstance(arg(a, 0), (int, float)) else _buffer_alloc(None, a)
    f = JSFunction(lambda t, a: ctor(a), "u", ctor, "Buffer")
    set_static(f, "from", JSFunction(_buffer_from, "from"))
    set_static(f, "alloc", JSFunction(_buffer_alloc, "alloc"))
    set_static(f, "allocUnsafe", JSFunction(_buffer_alloc, "allocUnsafe"))
    set_static(f, "allocUnsafeSlow", JSFunction(_buffer_alloc, "allocUnsafeSlow"))
    set_static(f, "concat", JSFunction(_buffer_concat, "concat"))
    set_static(f, "isBuffer", JSFunction(lambda t, a: isinstance(arg(a, 0), TypedArray) and arg(a, 0).is_buffer, "isBuffer"))
    set_static(f, "byteLength", JSFunction(
        lambda t, a: len(buffer_from_string(arg(a, 0), "utf8" if arg(a, 1) is UNDEF else to_str(a[1])))
        if isinstance(arg(a, 0), str) else len(as_bytes(arg(a, 0))), "byteLength"))
    set_static(f, "isEncoding", JSFunction(lambda t, a: to_str(arg(a, 0)).lower() in (
        "utf8", "utf-8", "hex", "base64", "base64url", "latin1", "binary", "ascii", "ucs2", "utf16le"), "isEncoding"))
    return f


# ─── errors, RegExp, Symbol, Promise ────────────────────────────────────

def make_error_ctor(name):
    def c(a):
        e = make_error("" if arg(a, 0) is UNDEF else to_str(a[0]), name)
        return e
    return JSFunction(lambda t, a: c(a), name, c, JSError)


def _regexp_ctor(a):
    p = arg(a, 0)
    if isinstance(p, JSRegExp):
        return JSRegExp(p.source, p.flags if arg(a, 1) is UNDEF else to_str(a[1]))
    return JSRegExp("(?:)" if p is UNDEF else to_str(p), "" if arg(a, 1) is UNDEF else to_str(a[1]))


_SYMBOLS = {}


def make_symbol():
    def mk(desc):
        s = JSObject()
        s.cls = "Symbol"
        s["description"] = desc
        return s
    f = JSFunction(lambda t, a: mk(UNDEF if not a else to_str(a[0])), "Symbol")
    for wk in ("iterator", "asyncIterator", "toPrimitive", "toStringTag", "hasInstance"):
        set_static(f, wk, mk("Symbol." + wk))

    def sym_for(t, a):
        k = to_str(arg(a, 0))
        if k not in _SYMBOLS:
            _SYMBOLS[k] = mk(k)
        return _SYMBOLS[k]
    set_static(f, "for", JSFunction(sym_for, "for"))
    return f


def make_promise():
    def ctor(a):
        ex = arg(a, 0)
        box = {}

        def res(t, x):
            if "v" not in box:
                box["v"] = (True, arg(x, 0))
            return UNDEF

        def rej(t, x):
            if "v" not in box:
                box["v"] = (False, arg(x, 0))
            return UNDEF
        try:
            ex.fn(UNDEF, [JSFunction(res, "resolve"), JSFunction(rej, "reject")])
        except JSThrow as e:
            box.setdefault("v", (False, e.value))
        ok, v = box.get("v", (True, UNDEF))
        if ok and isinstance(v, JSPromise):
            return v
        return JSPromise(ok, v)
    f = JSFunction(lambda t, a: js_throw("Promise requires new"), "Promise", ctor, JSPromise)
    set_static(f, "resolve", JSFunction(lambda t, a: promise_resolve(arg(a, 0)), "resolve"))
    set_static(f, "reject", JSFunction(lambda t, a: JSPromise(False, arg(a, 0)), "reject"))

    def p_all(t, a):
        out = JSArray()
        for p in iterate(arg(a, 0)):
            try:
                out.append(await_value(p))
            except JSThrow as e:
                return JSPromise(False, e.value)
        return JSPromise(True, out)

    def p_all_settled(t, a):
        out = JSArray()
        for p in iterate(arg(a, 0)):
            o = JSObject()
            try:
                o["status"] = "fulfilled"
                o["value"] = await_value(p)
            except JSThrow as e:
                o["status"] = "rejected"
                o["reason"] = e.value
            out.append(o)
        return JSPromise(True, out)

    def p_race(t, a):
        for p in iterate(arg(a, 0)):
            return promise_resolve(p)
        return JSPromise(True, UNDEF)
    set_static(f, "all", JSFunction(p_all, "all"))
    set_static(f, "allSettled", JSFunction(p_all_settled, "allSettled"))
    set_static(f, "race", JSFunction(p_race, "race"))
    set_static(f, "any", JSFunction(p_race, "any"))
    return f


# ─── URL / encoders / misc ──────────────────────────────────────────────

def _enc_uri_component(t, a):
    return quote(to_str(arg(a, 0)), safe="-_.!~*'()")


def _enc_uri(t, a):
    return quote(to_str(arg(a, 0)), safe="-_.!~*'();/?:@&=+$,#")


def _dec_uri_component(t, a):
    try:
        return unquote(to_str(arg(a, 0)), errors="strict")
    except UnicodeDecodeError:
        js_throw("URI malformed", "URIError")


def _atob(t, a):
    s = "".join(to_str(arg(a, 0)).split())
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=False).decode("latin-1")
    except Exception:
        js_throw("The string to be decoded is not correctly encoded.", "InvalidCharacterError")


def _btoa(t, a):
    s = to_str(arg(a, 0))
    try:
        return base64.b64encode(s.encode("latin-1")).decode("ascii")
    except UnicodeEncodeError:
        js_throw("The string to be encoded contains characters outside of the Latin1 range.", "InvalidCharacterError")


def make_search_params(init=""):
    sp = JSObject()
    sp.cls = "URLSearchParams"
    items = []
    if isinstance(init, str):
        items = parse_qsl(init.lstrip("?"), keep_blank_values=True)
    elif isinstance(init, JSObject):
        items = [(k, to_str(v)) for k, v in init.items()]
    store = [list(x) for x in items]

    def get(t, a):
        k = to_str(arg(a, 0))
        for x in store:
            if x[0] == k:
                return x[1]
        return None

    def set_(t, a):
        k = to_str(arg(a, 0))
        v = to_str(arg(a, 1))
        for x in store:
            if x[0] == k:
                x[1] = v
                rest = [y for y in store if y[0] != k]
                store[:] = [x] + rest if False else [y for y in store if y[0] != k or y is x]
                return UNDEF
        store.append([k, v])
        return UNDEF

    def append(t, a):
        store.append([to_str(arg(a, 0)), to_str(arg(a, 1))])
        return UNDEF

    def has(t, a):
        return any(x[0] == to_str(arg(a, 0)) for x in store)

    def tostring(t, a):
        return urlencode([tuple(x) for x in store])
    dict.__setitem__(sp, "get", JSFunction(get, "get"))
    dict.__setitem__(sp, "set", JSFunction(set_, "set"))
    dict.__setitem__(sp, "append", JSFunction(append, "append"))
    dict.__setitem__(sp, "has", JSFunction(has, "has"))
    dict.__setitem__(sp, "toString", JSFunction(tostring, "toString"))
    return sp


def make_url(href, base=None):
    if base:
        href = urljoin(base, href)
    p = urlsplit(href)
    if not p.scheme:
        js_throw("Invalid URL", "TypeError")
    u = JSObject()
    u["href"] = href
    u["protocol"] = p.scheme + ":"
    u["host"] = p.netloc
    u["hostname"] = p.hostname or ""
    u["port"] = str(p.port) if p.port else ""
    u["pathname"] = p.path or "/"
    u["search"] = ("?" + p.query) if p.query else ""
    u["hash"] = ("#" + p.fragment) if p.fragment else ""
    u["origin"] = p.scheme + "://" + p.netloc
    u["searchParams"] = make_search_params(p.query)
    dict.__setitem__(u, "toString", JSFunction(lambda t, a: href, "toString"))
    return u


def make_text_encoder():
    def c(a):
        o = JSObject()
        o["encoding"] = "utf-8"
        o["encode"] = JSFunction(lambda t, x: TypedArray("Uint8Array", bytearray(
            to_str(arg(x, 0)).encode("utf-8", "surrogatepass"))), "encode")
        return o
    return JSFunction(lambda t, a: js_throw("TextEncoder requires new"), "TextEncoder", c)


def make_text_decoder():
    def c(a):
        o = JSObject()
        o["encoding"] = "utf-8"
        o["decode"] = JSFunction(lambda t, x: as_bytes(arg(x, 0)).decode("utf-8", "replace")
                                 if arg(x, 0) is not UNDEF else "", "decode")
        return o
    return JSFunction(lambda t, a: js_throw("TextDecoder requires new"), "TextDecoder", c)


def make_abort():
    def signal():
        s = JSObject()
        s["aborted"] = False
        s["addEventListener"] = JSFunction(lambda t, a: UNDEF, "addEventListener")
        return s
    sig = JSFunction(lambda t, a: js_throw("Illegal constructor"), "AbortSignal")
    set_static(sig, "timeout", JSFunction(lambda t, a: signal(), "timeout"))
    set_static(sig, "any", JSFunction(lambda t, a: signal(), "any"))

    def ctl(a):
        o = JSObject()
        o["signal"] = signal()
        o["abort"] = JSFunction(lambda t, x: UNDEF, "abort")
        return o
    ac = JSFunction(lambda t, a: js_throw("requires new"), "AbortController", ctl)
    return sig, ac


def make_reflect():
    r = JSObject()
    r["construct"] = JSFunction(lambda t, a: construct(arg(a, 0), iterate(arg(a, 1))), "construct")
    r["apply"] = JSFunction(lambda t, a: call_function(arg(a, 0), arg(a, 1), iterate(arg(a, 2))), "apply")
    r["get"] = JSFunction(lambda t, a: get_prop(arg(a, 0), arg(a, 1)), "get")
    r["set"] = JSFunction(lambda t, a: (set_prop(arg(a, 0), arg(a, 1), arg(a, 2)), True)[1], "set")
    r["has"] = JSFunction(lambda t, a: has_property(arg(a, 0), arg(a, 1)), "has")
    r["ownKeys"] = JSFunction(lambda t, a: JSArray(own_keys(arg(a, 0))), "ownKeys")
    r["getPrototypeOf"] = JSFunction(_get_proto, "getPrototypeOf")
    return r


def make_storage():
    store = {}
    s = JSObject()
    s["getItem"] = JSFunction(lambda t, a: store.get(to_str(arg(a, 0)), None), "getItem")
    s["setItem"] = JSFunction(lambda t, a: store.__setitem__(to_str(arg(a, 0)), to_str(arg(a, 1))) or UNDEF, "setItem")
    s["removeItem"] = JSFunction(lambda t, a: store.pop(to_str(arg(a, 0)), None) and UNDEF, "removeItem")
    s["clear"] = JSFunction(lambda t, a: store.clear() or UNDEF, "clear")
    return s


def _date_ctor_factory(now_ms):
    def c(a):
        ms = now_ms() if not a else to_number(a[0])
        d = JSObject()
        d.cls = "Date"
        d["getTime"] = JSFunction(lambda t, x: ms, "getTime")
        d["valueOf"] = JSFunction(lambda t, x: ms, "valueOf")
        d["getTimezoneOffset"] = JSFunction(lambda t, x: 0, "getTimezoneOffset")
        d["toISOString"] = JSFunction(lambda t, x: time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000.0)) + ".%03dZ" % (int(ms) % 1000), "toISOString")
        d["getFullYear"] = JSFunction(lambda t, x: time.gmtime(ms / 1000.0).tm_year, "getFullYear")
        return d
    return c


def _make_element(tag):
    el = JSObject()
    el["tagName"] = to_str(tag).upper()
    el["style"] = JSObject()
    el["setAttribute"] = JSFunction(lambda t, a: UNDEF, "setAttribute")
    el["appendChild"] = JSFunction(lambda t, a: arg(a, 0), "appendChild")
    el["remove"] = JSFunction(lambda t, a: UNDEF, "remove")
    el["getContext"] = JSFunction(lambda t, a: None, "getContext")
    el["canPlayType"] = JSFunction(lambda t, a: "maybe", "canPlayType")
    return el


FIREFOX_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"


def make_globals(opts=None):
    """Returns a dict name -> JS value.  opts:
        now_ms() / random_bytes(n) / random() hooks, fetch (JSFunction),
        user_agent, page_url."""
    opts = opts or {}
    now_ms = opts.get("now_ms") or (lambda: int(time.time() * 1000))
    rnd = opts.get("random") or random.random
    ua = opts.get("user_agent") or FIREFOX_UA
    page_url = opts.get("page_url") or "https://example.com/"
    g = {}

    S = JSFunction(_string_call, "String", lambda a: _string_call(None, a), str)
    set_static(S, "fromCharCode", JSFunction(_from_char_code, "fromCharCode"))
    set_static(S, "fromCodePoint", JSFunction(_from_code_point, "fromCodePoint"))
    g["String"] = S

    N = JSFunction(_number_call, "Number", lambda a: _number_call(None, a))
    for k, f in (("isInteger", lambda t, a: isinstance(arg(a, 0), (int, float)) and not isinstance(arg(a, 0), bool)
                  and float(arg(a, 0)).is_integer()),
                 ("isFinite", lambda t, a: isinstance(arg(a, 0), (int, float)) and _is_finite(t, a)),
                 ("isNaN", lambda t, a: isinstance(arg(a, 0), float) and arg(a, 0) != arg(a, 0)),
                 ("isSafeInteger", lambda t, a: isinstance(arg(a, 0), int) and abs(arg(a, 0)) <= 2 ** 53 - 1),
                 ("parseInt", _parse_int), ("parseFloat", _parse_float)):
        set_static(N, k, JSFunction(f, k))
    N.props["MAX_SAFE_INTEGER"] = 2 ** 53 - 1
    N.props["MIN_SAFE_INTEGER"] = -(2 ** 53 - 1)
    N.props["EPSILON"] = 2.0 ** -52
    N.props["MAX_VALUE"] = 1.7976931348623157e308
    g["Number"] = N
    g["BigInt"] = JSFunction(_bigint_call, "BigInt")
    g["Boolean"] = JSFunction(lambda t, a: truthy(arg(a, 0)), "Boolean")
    g["parseInt"] = JSFunction(_parse_int, "parseInt")
    g["parseFloat"] = JSFunction(_parse_float, "parseFloat")
    g["isNaN"] = JSFunction(_is_nan, "isNaN")
    g["isFinite"] = JSFunction(_is_finite, "isFinite")
    g["NaN"] = float("nan")
    g["Infinity"] = float("inf")
    g["undefined"] = UNDEF
    g["Math"] = make_math(rnd)

    A = JSFunction(lambda t, a: _array_ctor(a), "Array", _array_ctor, list)
    set_static(A, "from", JSFunction(_array_from, "from"))
    set_static(A, "isArray", JSFunction(lambda t, a: isinstance(arg(a, 0), list), "isArray"))
    set_static(A, "of", JSFunction(lambda t, a: JSArray(a), "of"))
    g["Array"] = A
    g["Object"] = make_object_ctor()
    g["JSON"] = _obj(parse=_fn("parse", lambda t, a: json_parse(to_str(arg(a, 0)))),
                     stringify=_fn("stringify", lambda t, a: json_stringify(arg(a, 0))))
    D = JSFunction(lambda t, a: time.strftime("%a %b %d %Y %H:%M:%S GMT+0000"), "Date",
                   _date_ctor_factory(now_ms))
    set_static(D, "now", JSFunction(lambda t, a: now_ms(), "now"))
    g["Date"] = D
    g["RegExp"] = JSFunction(lambda t, a: _regexp_ctor(a), "RegExp", _regexp_ctor, JSRegExp)
    for en in ("Error", "TypeError", "RangeError", "SyntaxError", "ReferenceError", "URIError", "EvalError"):
        g[en] = make_error_ctor(en)
    for kind in _TA_INFO:
        g[kind] = make_typed_ctor(kind)
    g["Buffer"] = make_buffer()
    g["Map"] = JSFunction(lambda t, a: js_throw("Map requires new"), "Map", _map_ctor(False), JSMap)
    g["Set"] = JSFunction(lambda t, a: js_throw("Set requires new"), "Set", _map_ctor(True), JSMap)
    g["WeakMap"] = JSFunction(lambda t, a: js_throw("WeakMap requires new"), "WeakMap", _map_ctor(False), JSMap)
    g["WeakSet"] = JSFunction(lambda t, a: js_throw("WeakSet requires new"), "WeakSet", _map_ctor(True), JSMap)
    g["Proxy"] = JSFunction(lambda t, a: js_throw("Proxy requires new"), "Proxy", lambda a: arg(a, 0))
    g["Symbol"] = make_symbol()
    g["Promise"] = make_promise()
    g["Reflect"] = make_reflect()
    g["Function"] = JSFunction(lambda t, a: js_throw("Function constructor not supported"), "Function")
    g["encodeURIComponent"] = JSFunction(_enc_uri_component, "encodeURIComponent")
    g["encodeURI"] = JSFunction(_enc_uri, "encodeURI")
    g["decodeURIComponent"] = JSFunction(_dec_uri_component, "decodeURIComponent")
    g["decodeURI"] = JSFunction(_dec_uri_component, "decodeURI")
    g["escape"] = JSFunction(lambda t, a: quote(to_str(arg(a, 0)), safe="@*_+-./"), "escape")
    g["unescape"] = JSFunction(lambda t, a: unquote(to_str(arg(a, 0))), "unescape")
    g["atob"] = JSFunction(_atob, "atob")
    g["btoa"] = JSFunction(_btoa, "btoa")
    g["TextEncoder"] = make_text_encoder()
    g["TextDecoder"] = make_text_decoder()
    g["URL"] = JSFunction(lambda t, a: js_throw("URL requires new"), "URL",
                          lambda a: make_url(to_str(arg(a, 0)), None if arg(a, 1) is UNDEF else to_str(get_prop(a[1], "href") if isinstance(a[1], JSObject) else a[1])))
    g["URLSearchParams"] = JSFunction(lambda t, a: js_throw("requires new"), "URLSearchParams",
                                      lambda a: make_search_params("" if arg(a, 0) is UNDEF else arg(a, 0)))
    g["AbortSignal"], g["AbortController"] = make_abort()
    noop = JSFunction(lambda t, a: UNDEF, "log")
    g["console"] = _obj(log=noop, warn=noop, error=noop, info=noop, debug=noop, table=noop, trace=noop, clear=noop)
    g["crypto_node"] = make_crypto(opts.get("random_bytes"))
    g["localStorage"] = make_storage()
    g["sessionStorage"] = make_storage()
    g["setTimeout"] = JSFunction(lambda t, a: 1, "setTimeout")
    g["clearTimeout"] = JSFunction(lambda t, a: UNDEF, "clearTimeout")
    g["setInterval"] = JSFunction(lambda t, a: 1, "setInterval")
    g["clearInterval"] = JSFunction(lambda t, a: UNDEF, "clearInterval")
    g["queueMicrotask"] = JSFunction(lambda t, a: call_function(arg(a, 0), UNDEF, []), "queueMicrotask")

    nav = JSObject()
    nav["userAgent"] = ua
    nav["webdriver"] = False
    nav["language"] = "en-US"
    nav["languages"] = JSArray(["en-US", "en"])
    nav["platform"] = "Win32"
    nav["vendor"] = ""
    nav["hardwareConcurrency"] = 8
    nav["maxTouchPoints"] = 0
    nav["cookieEnabled"] = True
    nav["onLine"] = True
    nav["plugins"] = JSArray()
    nav["mimeTypes"] = JSArray()
    g["navigator"] = nav
    scr = _obj(width=1280, height=720, availWidth=1280, availHeight=720, colorDepth=24, pixelDepth=24)
    g["screen"] = scr
    pu = urlsplit(page_url)
    loc = _obj(href=page_url, protocol=pu.scheme + ":", host=pu.netloc, hostname=pu.hostname or "",
               pathname=pu.path, search=("?" + pu.query) if pu.query else "", hash="",
               origin=pu.scheme + "://" + pu.netloc, port="")
    g["location"] = loc
    doc = JSObject()
    doc["location"] = loc
    doc["createElement"] = JSFunction(lambda t, a: _make_element(arg(a, 0)), "createElement")
    doc["referrer"] = ""
    doc["cookie"] = ""
    doc["hidden"] = False
    doc["visibilityState"] = "visible"
    doc["documentElement"] = _make_element("html")
    doc["body"] = _make_element("body")
    doc["querySelector"] = JSFunction(lambda t, a: None, "querySelector")
    doc["getElementById"] = JSFunction(lambda t, a: None, "getElementById")
    doc["addEventListener"] = JSFunction(lambda t, a: UNDEF, "addEventListener")
    g["document"] = doc
    g["history"] = _obj(length=1, state=None)
    t0 = time.time()
    g["performance"] = _obj(now=_fn("now", lambda t, a: norm_num(round((time.time() - t0) * 1000, 3))),
                            timeOrigin=now_ms())
    wcrypto = JSObject()
    wcrypto["getRandomValues"] = g["crypto_node"]["getRandomValues"]
    wcrypto["randomUUID"] = g["crypto_node"]["randomUUID"]
    g["crypto_web"] = wcrypto

    win = JSObject()
    g["window"] = win
    g["self"] = win
    g["globalThis"] = win
    win["window"] = win
    win["self"] = win
    win["document"] = doc
    win["name"] = ""
    win["location"] = loc
    win["customElements"] = JSObject()
    win["history"] = g["history"]
    win["navigation"] = JSObject()
    win["navigator"] = nav
    win["screen"] = scr
    win["crypto"] = wcrypto
    win["localStorage"] = g["localStorage"]
    win["performance"] = g["performance"]
    win["innerWidth"] = 1280
    win["innerHeight"] = 720
    win["outerWidth"] = 1280
    win["outerHeight"] = 800
    win["devicePixelRatio"] = 1
    win["requestAnimationFrame"] = JSFunction(lambda t, a: 1, "requestAnimationFrame")
    win["addEventListener"] = JSFunction(lambda t, a: UNDEF, "addEventListener")
    win["removeEventListener"] = JSFunction(lambda t, a: UNDEF, "removeEventListener")
    win["postMessage"] = JSFunction(lambda t, a: UNDEF, "postMessage")
    win["setTimeout"] = g["setTimeout"]
    win["parent"] = win
    win["top"] = win
    if opts.get("fetch") is not None:
        g["fetch"] = opts["fetch"]
        win["fetch"] = opts["fetch"]
    return g
