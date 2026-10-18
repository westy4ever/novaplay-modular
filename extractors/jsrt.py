# -*- coding: utf-8 -*-
"""
Tiny JavaScript runtime for jsvm.py
===================================
Just enough of JavaScript's value model and built-ins to run the
bytecode programs that VidCore / VidFast hide inside their player
pages (see jsvm.py).  Everything is synchronous: promises are settled
immediately, so `await` just unwraps them.

Value mapping
  undefined -> UNDEF        null    -> None      boolean -> bool
  number    -> int/float    string  -> str       bigint  -> JSBigInt
  Array     -> JSArray      object  -> JSObject  function-> JSFunction
  Buffer / Uint8Array / Uint16Array / Uint32Array ... -> TypedArray
"""

import base64
import binascii
import hashlib
import hmac as _hmac
import json
import math
import os
import re
import struct
import time


# ─── basic values ────────────────────────────────────────────────────────

class _Undef(object):
    __slots__ = ()

    def __repr__(self):
        return "undefined"

    def __bool__(self):
        return False


UNDEF = _Undef()


class JSBigInt(int):
    pass


class JSThrow(Exception):
    """A JavaScript `throw` carrying any JS value."""

    def __init__(self, value):
        Exception.__init__(self, value)
        self.value = value

    def __str__(self):
        v = self.value
        if isinstance(v, JSObject):
            return "{}: {}".format(v.get("name", "Error"), v.get("message", ""))
        return to_str(v) if v is not UNDEF else "undefined"


class JSObject(dict):
    __slots__ = ("proto", "cls")

    def __init__(self, *a, **k):
        dict.__init__(self, *a, **k)
        self.proto = None
        self.cls = None

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


class JSArray(list):
    __slots__ = ("extra",)

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


class JSFunction(object):
    """fn(this, args) -> value.  `ctor(args)` builds an instance for `new`."""
    __slots__ = ("fn", "name", "props", "ctor", "pycls")

    def __init__(self, fn, name="", ctor=None, pycls=None):
        self.fn = fn
        self.name = name
        self.props = None
        self.ctor = ctor
        self.pycls = pycls

    def call(self, this, args):
        return self.fn(this, args)

    def __call__(self, *args):
        return self.fn(UNDEF, list(args))

    def __repr__(self):
        return "fn:" + (self.name or "?")


class JSPromise(object):
    __slots__ = ("ok", "value")

    def __init__(self, ok, value):
        self.ok = ok
        self.value = value


class JSRegExp(object):
    __slots__ = ("source", "flags", "rx", "lastIndex")

    def __init__(self, source, flags=""):
        self.source = source
        self.flags = flags
        f = 0
        if "i" in flags:
            f |= re.I
        if "m" in flags:
            f |= re.M
        if "s" in flags:
            f |= re.S
        self.rx = re.compile(_js_regex_to_py(source), f)
        self.lastIndex = 0


def _js_regex_to_py(src):
    # the few differences that matter: named groups, \d inside [] is fine,
    # `$` / `^` same.  (?<name> -> (?P<name>
    return re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", src)


class JSError(JSObject):
    pass


def make_error(msg, name="Error"):
    e = JSError()
    e["name"] = name
    e["message"] = msg
    e["stack"] = "{}: {}".format(name, msg)
    return e


def js_throw(msg, name="TypeError"):
    raise JSThrow(make_error(msg, name))


def fnative(name, ctor=None, pycls=None):
    def deco(f):
        return JSFunction(f, name, ctor, pycls)
    return deco


def set_static(fobj, name, f):
    if fobj.props is None:
        fobj.props = JSObject()
    fobj.props[name] = f if isinstance(f, JSFunction) else JSFunction(f, name)
    return fobj


# ─── typed arrays / Buffer ───────────────────────────────────────────────

_TA_INFO = {
    # kind: (bytes per element, signed, float)
    "Uint8Array": (1, False, False),
    "Uint8ClampedArray": (1, False, False),
    "Int8Array": (1, True, False),
    "Uint16Array": (2, False, False),
    "Int16Array": (2, True, False),
    "Uint32Array": (4, False, False),
    "Int32Array": (4, True, False),
    "Float32Array": (4, False, True),
    "Float64Array": (8, False, True),
}


class TypedArray(object):
    """Uint8Array/Buffer share one bytearray with views (subarray);
    wider arrays keep a python list (they are only ever built empty)."""
    __slots__ = ("kind", "buf", "off", "n", "is_buffer")

    def __init__(self, kind, buf, off=0, n=None, is_buffer=False):
        self.kind = kind
        self.buf = buf
        self.off = off
        self.n = (len(buf) - off) if n is None else n
        self.is_buffer = is_buffer

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    def __len__(self):
        return self.n

    @property
    def bytes1(self):
        return self.kind in ("Uint8Array", "Int8Array", "Uint8ClampedArray")

    def get(self, i):
        if 0 <= i < self.n:
            v = self.buf[self.off + i]
            if self.kind == "Int8Array" and v > 127:
                v -= 256
            return v
        return UNDEF

    def set(self, i, v):
        if not (0 <= i < self.n):
            return
        k = self.kind
        if k in ("Float32Array", "Float64Array"):
            self.buf[self.off + i] = float(to_number(v))
            return
        x = to_number(v)
        if isinstance(x, float):
            if x != x or x in (float("inf"), float("-inf")):
                x = 0
            else:
                x = int(x)
        bits = _TA_INFO[k][0] * 8
        x &= (1 << bits) - 1
        if self.bytes1:
            self.buf[self.off + i] = x if k != "Int8Array" else x
            return
        if _TA_INFO[k][1] and x >= (1 << (bits - 1)):
            x -= 1 << bits
        self.buf[self.off + i] = x

    def tobytes(self):
        if self.bytes1:
            return bytes(self.buf[self.off:self.off + self.n])
        size = _TA_INFO[self.kind][0]
        out = bytearray()
        for v in self.buf[self.off:self.off + self.n]:
            out += (v & ((1 << (size * 8)) - 1)).to_bytes(size, "little")
        return bytes(out)

    def tolist(self):
        return [self.get(i) for i in range(self.n)]


def new_buffer(data=b"", is_buffer=True):
    return TypedArray("Uint8Array", bytearray(data), 0, None, is_buffer)


def new_typed(kind, length):
    if kind in ("Uint8Array", "Int8Array", "Uint8ClampedArray"):
        return TypedArray(kind, bytearray(length))
    zero = 0.0 if _TA_INFO[kind][2] else 0
    return TypedArray(kind, [zero] * length)


def as_bytes(v):
    """Bytes of a Buffer / typed array / array / str(utf8)."""
    if isinstance(v, TypedArray):
        return v.tobytes()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        return v.encode("utf-8", "surrogatepass")
    if isinstance(v, list):
        return bytes((to_int32(x) & 255) for x in v)
    js_throw("expected bytes, got " + type_of(v))


# ─── conversions ─────────────────────────────────────────────────────────

_INF = float("inf")
_NAN = float("nan")


def type_of(v):
    if v is UNDEF:
        return "undefined"
    if v is None:
        return "object"
    if v is True or v is False:
        return "boolean"
    if isinstance(v, JSBigInt):
        return "bigint"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, JSFunction):
        return "function"
    return "object"


def truthy(v):
    if v is UNDEF or v is None or v is False:
        return False
    if v is True:
        return True
    if isinstance(v, (int, float)):
        return v == v and v != 0
    if isinstance(v, str):
        return len(v) > 0
    return True


def num_to_str(x, radix=10):
    if isinstance(x, bool):
        return "true" if x else "false"
    if radix != 10:
        if isinstance(x, float):
            if x != x:
                return "NaN"
            if x.is_integer():
                x = int(x)
            else:
                # fractional radix output is rare; good enough
                ip = int(x)
                return _int_radix(ip, radix) + "." + _frac_radix(abs(x - ip), radix)
        return _int_radix(x, radix)
    if isinstance(x, int):
        return str(x)
    if x != x:
        return "NaN"
    if x == _INF:
        return "Infinity"
    if x == -_INF:
        return "-Infinity"
    if x == 0:
        return "0"
    if x.is_integer() and abs(x) < 1e21:
        return str(int(x))
    r = repr(x)
    if "e" in r:
        m, e = r.split("e")
        if m.endswith(".0"):
            m = m[:-2]
        ei = int(e)
        if -7 < ei < 21:
            # python switched to exponent earlier than JS does
            return format(x, "f").rstrip("0").rstrip(".")
        return "{}e{}{}".format(m, "+" if ei > 0 else "-", abs(ei))
    return r


def _int_radix(n, radix):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    neg = n < 0
    n = abs(n)
    out = []
    while n:
        n, r = divmod(n, radix)
        out.append(digits[r])
    return ("-" if neg else "") + "".join(reversed(out))


def _frac_radix(f, radix, max_digits=20):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    while f and len(out) < max_digits:
        f *= radix
        d = int(f)
        out.append(digits[d])
        f -= d
    return "".join(out) or "0"


def to_str(v):
    if isinstance(v, str):
        return v
    if v is UNDEF:
        return "undefined"
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, JSBigInt):
        return str(int(v))
    if isinstance(v, (int, float)):
        return num_to_str(v)
    if isinstance(v, list):
        return ",".join("" if (x is UNDEF or x is None) else to_str(x) for x in v)
    if isinstance(v, TypedArray):
        if v.is_buffer:
            return buffer_tostring(v, "utf8")
        return ",".join(num_to_str(x) for x in v.tolist())
    if isinstance(v, JSFunction):
        return "function {}() {{ [native code] }}".format(v.name)
    if isinstance(v, JSRegExp):
        return "/{}/{}".format(v.source, v.flags)
    if isinstance(v, JSError):
        return "{}: {}".format(to_str(v.get("name", "Error")), to_str(v.get("message", "")))
    if isinstance(v, JSObject):
        ts = obj_lookup(v, "toString")
        if isinstance(ts, JSFunction) and ts.name != "toString#obj":
            return to_str(ts.call(v, []))
        return "[object Object]"
    return str(v)


def str_to_num(s):
    s = s.strip(" \t\n\r\v\f ﻿  ")
    if s == "":
        return 0
    try:
        if s[:2] in ("0x", "0X"):
            return int(s[2:], 16)
        if s[:2] in ("0o", "0O"):
            return int(s[2:], 8)
        if s[:2] in ("0b", "0B"):
            return int(s[2:], 2)
        if s in ("Infinity", "+Infinity"):
            return _INF
        if s == "-Infinity":
            return -_INF
        if re.match(r"^[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)$", s):
            f = float(s)
            if f.is_integer() and abs(f) <= 2 ** 53 and "e" not in s.lower() and "." not in s:
                return int(s)
            return norm_num(f)
    except ValueError:
        pass
    return _NAN


def norm_num(f):
    """Store integral doubles as int (fast paths); keep -0/NaN/inf as float."""
    if isinstance(f, float) and f.is_integer() and abs(f) <= 9007199254740992.0 and not (f == 0 and math.copysign(1, f) < 0):
        return int(f)
    return f


def to_number(v):
    if v is True:
        return 1
    if v is False:
        return 0
    if isinstance(v, JSBigInt):
        js_throw("Cannot convert a BigInt value to a number")
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return str_to_num(v)
    if v is None:
        return 0
    if v is UNDEF:
        return _NAN
    if isinstance(v, (list, TypedArray)):
        return str_to_num(to_str(v))
    if isinstance(v, JSObject):
        vo = obj_lookup(v, "valueOf")
        if isinstance(vo, JSFunction):
            r = vo.call(v, [])
            if not isinstance(r, (JSObject, list)):
                return to_number(r)
        return str_to_num(to_str(v))
    return _NAN


def to_numeric(v):
    if isinstance(v, JSBigInt):
        return v
    return to_number(to_primitive(v))


def to_int32(v):
    if type(v) is int:
        if -2147483648 <= v <= 2147483647:
            return v
        v &= 0xFFFFFFFF
        return v - 0x100000000 if v & 0x80000000 else v
    x = to_number(v)
    if isinstance(x, float):
        if x != x or x in (_INF, -_INF):
            return 0
        x = int(x)
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def to_uint32(v):
    if type(v) is int and 0 <= v <= 0xFFFFFFFF:
        return v
    x = to_number(v)
    if isinstance(x, float):
        if x != x or x in (_INF, -_INF):
            return 0
        x = int(x)
    return x & 0xFFFFFFFF


def to_integer(v):
    x = to_number(v)
    if isinstance(x, float):
        if x != x:
            return 0
        if x in (_INF, -_INF):
            return x
        return int(x)
    return x


def to_primitive(v, hint="default"):
    if isinstance(v, (JSObject, list, TypedArray, JSFunction, JSRegExp)):
        if isinstance(v, JSObject) and not isinstance(v, JSError):
            vo = obj_lookup(v, "valueOf")
            if isinstance(vo, JSFunction) and vo.name != "valueOf#obj":
                r = vo.call(v, [])
                if not isinstance(r, (JSObject, list)):
                    return r
        return to_str(v)
    return v


def to_property_key(k):
    if isinstance(k, str):
        return k
    if type(k) is int:
        return k
    if isinstance(k, float) and k.is_integer() and abs(k) < 2 ** 53 and not (k == 0 and math.copysign(1, k) < 0):
        return int(k)
    return to_str(k)


def _index_of_key(k):
    """int index for array-ish access, or None."""
    if type(k) is int:
        return k if k >= 0 else None
    if isinstance(k, float):
        if k.is_integer() and k >= 0:
            return int(k)
        return None
    if isinstance(k, str) and k.isdigit() and (k == "0" or k[0] != "0"):
        return int(k)
    return None


def _norm_obj_key(k):
    """Object keys are strings in JS; we store canonical strings."""
    if isinstance(k, str):
        return k
    return to_str(k)


# ─── arithmetic ──────────────────────────────────────────────────────────

_MAXSAFE = 9007199254740992


def _numres(r):
    if type(r) is int and -_MAXSAFE <= r <= _MAXSAFE:
        return r
    if type(r) is int:
        return float(r)
    return norm_num(r)


def op_add(a, b):
    ta = type(a)
    tb = type(b)
    if ta is int and tb is int:
        r = a + b
        return r if -_MAXSAFE <= r <= _MAXSAFE else float(a) + float(b)
    if ta is str and tb is str:
        return a + b
    a = to_primitive(a)
    b = to_primitive(b)
    if isinstance(a, str) or isinstance(b, str):
        return to_str(a) + to_str(b)
    if isinstance(a, JSBigInt) and isinstance(b, JSBigInt):
        return JSBigInt(a + b)
    a = to_number(a)
    b = to_number(b)
    if type(a) is int and type(b) is int:
        r = a + b
        return r if -_MAXSAFE <= r <= _MAXSAFE else float(a) + float(b)
    return norm_num(float(a) + float(b))


def _arith(a, b, op):
    a = to_numeric(a)
    b = to_numeric(b)
    if isinstance(a, JSBigInt) or isinstance(b, JSBigInt):
        if not (isinstance(a, JSBigInt) and isinstance(b, JSBigInt)):
            js_throw("Cannot mix BigInt and other types, use explicit conversions")
        a = int(a)
        b = int(b)
        if op == "-":
            return JSBigInt(a - b)
        if op == "*":
            return JSBigInt(a * b)
        if op == "/":
            if b == 0:
                js_throw("Division by zero", "RangeError")
            q = abs(a) // abs(b)
            return JSBigInt(q if (a >= 0) == (b >= 0) else -q)
        if op == "%":
            if b == 0:
                js_throw("Division by zero", "RangeError")
            r = abs(a) % abs(b)
            return JSBigInt(r if a >= 0 else -r)
        if op == "**":
            return JSBigInt(a ** b)
    if op == "-":
        if type(a) is int and type(b) is int:
            return _numres(a - b) if abs(a - b) <= _MAXSAFE else float(a) - float(b)
        return norm_num(float(a) - float(b))
    if op == "*":
        if type(a) is int and type(b) is int:
            r = a * b
            if -_MAXSAFE <= r <= _MAXSAFE and not (r == 0 and (a < 0) != (b < 0) and (a != 0 or b != 0)):
                return r
            return norm_num(float(a) * float(b))
        return norm_num(float(a) * float(b))
    if op == "/":
        a = float(a)
        b = float(b)
        if b == 0:
            if a != a or a == 0:
                return _NAN
            neg = (a < 0) != (math.copysign(1, b) < 0)
            return -_INF if neg else _INF
        return norm_num(a / b)
    if op == "%":
        if type(a) is int and type(b) is int and b != 0:
            r = abs(a) % abs(b)
            return r if a >= 0 else -r
        a = float(a)
        b = float(b)
        if b == 0 or a != a or b != b or a in (_INF, -_INF):
            return _NAN
        if b in (_INF, -_INF):
            return norm_num(a)
        return norm_num(math.fmod(a, b))
    if op == "**":
        try:
            return norm_num(float(a) ** float(b))
        except (OverflowError, ZeroDivisionError):
            return _INF
    raise ValueError(op)


def _bitop(a, b, op):
    a = to_numeric(a)
    b = to_numeric(b)
    if isinstance(a, JSBigInt) or isinstance(b, JSBigInt):
        if not (isinstance(a, JSBigInt) and isinstance(b, JSBigInt)):
            js_throw("Cannot mix BigInt and other types, use explicit conversions")
        a = int(a)
        b = int(b)
        if op == "&":
            return JSBigInt(a & b)
        if op == "|":
            return JSBigInt(a | b)
        if op == "^":
            return JSBigInt(a ^ b)
        if op == "<<":
            return JSBigInt(a << b if b >= 0 else a >> -b)
        if op == ">>":
            return JSBigInt(a >> b if b >= 0 else a << -b)
        js_throw("BigInts have no unsigned right shift, use >> instead")
    if op == "&":
        return to_int32(to_int32(a) & to_int32(b))
    if op == "|":
        return to_int32(to_int32(a) | to_int32(b))
    if op == "^":
        return to_int32(to_int32(a) ^ to_int32(b))
    s = to_uint32(b) & 31
    if op == "<<":
        return to_int32((to_int32(a) << s) & 0xFFFFFFFF)
    if op == ">>":
        return to_int32(a) >> s
    if op == ">>>":
        return to_uint32(a) >> s
    raise ValueError(op)


def strict_eq(a, b):
    if a is b:
        return not (isinstance(a, float) and a != a)
    ta = type_of(a)
    if ta != type_of(b):
        return False
    if ta in ("number", "bigint"):
        return a == b
    if ta == "string":
        return a == b
    if ta == "boolean":
        return a is b
    return False


def loose_eq(a, b):
    if (a is None or a is UNDEF) and (b is None or b is UNDEF):
        return True
    if a is None or a is UNDEF or b is None or b is UNDEF:
        return False
    ta = type_of(a)
    tb = type_of(b)
    if ta == tb:
        return strict_eq(a, b)
    if ta == "boolean":
        return loose_eq(to_number(a), b)
    if tb == "boolean":
        return loose_eq(a, to_number(b))
    if ta == "number" and tb == "string":
        return a == str_to_num(b)
    if ta == "string" and tb == "number":
        return str_to_num(a) == b
    if ta == "bigint" and tb == "number":
        return int(a) == b
    if ta == "number" and tb == "bigint":
        return a == int(b)
    if ta == "bigint" and tb == "string":
        try:
            return int(a) == int(b.strip())
        except ValueError:
            return False
    if ta == "string" and tb == "bigint":
        return loose_eq(b, a)
    if ta == "object" and tb != "object":
        return loose_eq(to_primitive(a), b)
    if tb == "object" and ta != "object":
        return loose_eq(a, to_primitive(b))
    return False


def _compare(a, b):
    """returns -1/0/1 or None (undefined, e.g. NaN)."""
    a = to_primitive(a)
    b = to_primitive(b)
    if isinstance(a, str) and isinstance(b, str):
        ua = a.encode("utf-16-be")
        ub = b.encode("utf-16-be")
        return (ua > ub) - (ua < ub)
    if isinstance(a, JSBigInt) and isinstance(b, str):
        try:
            b = JSBigInt(int(b))
        except ValueError:
            return None
    if isinstance(b, JSBigInt) and isinstance(a, str):
        try:
            a = JSBigInt(int(a))
        except ValueError:
            return None
    if not isinstance(a, JSBigInt):
        a = to_number(a)
    if not isinstance(b, JSBigInt):
        b = to_number(b)
    if (isinstance(a, float) and a != a) or (isinstance(b, float) and b != b):
        return None
    return (a > b) - (a < b)


def op_instanceof(a, b):
    if not isinstance(b, JSFunction):
        js_throw("Right-hand side of 'instanceof' is not callable")
    cls = b.pycls
    if cls is None:
        return False
    if isinstance(cls, str):
        if isinstance(a, TypedArray):
            if cls == "Buffer":
                return a.is_buffer
            return a.kind == cls or (cls == "Uint8Array" and a.is_buffer)
        return False
    return isinstance(a, cls)


def op_in(a, b):
    if not isinstance(b, (JSObject, list, TypedArray, JSFunction)):
        js_throw("Cannot use 'in' operator to search for '{}' in {}".format(to_str(a), to_str(b)))
    return has_property(b, a)


def binop(name, a, b):
    if name == "+":
        return op_add(a, b)
    if name in ("-", "*", "/", "%", "**"):
        return _arith(a, b, name)
    if name in ("&", "|", "^", "<<", ">>", ">>>"):
        return _bitop(a, b, name)
    if name == "===":
        return strict_eq(a, b)
    if name == "!==":
        return not strict_eq(a, b)
    if name == "==":
        return loose_eq(a, b)
    if name == "!=":
        return not loose_eq(a, b)
    if name == "<":
        c = _compare(a, b)
        return c is not None and c < 0
    if name == ">":
        c = _compare(a, b)
        return c is not None and c > 0
    if name == "<=":
        c = _compare(a, b)
        return c is not None and c <= 0
    if name == ">=":
        c = _compare(a, b)
        return c is not None and c >= 0
    if name == "in":
        return op_in(a, b)
    if name == "instanceof":
        return op_instanceof(a, b)
    raise ValueError("unknown operator " + name)


def unop(name, a):
    if name == "-":
        a = to_numeric(a)
        if isinstance(a, JSBigInt):
            return JSBigInt(-a)
        if a == 0 and type(a) is int:
            return -0.0
        return norm_num(-a) if isinstance(a, float) else -a
    if name == "+":
        return to_number(a)
    if name == "!":
        return not truthy(a)
    if name == "~":
        a = to_numeric(a)
        if isinstance(a, JSBigInt):
            return JSBigInt(~int(a))
        return to_int32(~to_int32(a))
    if name == "typeof":
        return type_of(a)
    if name == "void":
        return UNDEF
    raise ValueError("unknown unary " + name)


# ─── property access ─────────────────────────────────────────────────────

def obj_lookup(o, key):
    k = _norm_obj_key(key)
    cur = o
    while cur is not None:
        if k in cur:
            return dict.__getitem__(cur, k)
        cur = cur.proto
    m = OBJECT_PROTO_METHODS.get(k)
    if m is not None:
        return m
    return UNDEF


def has_property(o, key):
    if isinstance(o, JSObject):
        k = _norm_obj_key(key)
        cur = o
        while cur is not None:
            if k in cur:
                return True
            cur = cur.proto
        return k in OBJECT_PROTO_METHODS
    if isinstance(o, list):
        i = _index_of_key(key)
        if i is not None:
            return i < len(o)
        return to_str(key) in ARRAY_METHODS or to_str(key) == "length"
    if isinstance(o, TypedArray):
        i = _index_of_key(key)
        if i is not None:
            return i < o.n
        return to_str(key) in TA_METHODS or to_str(key) == "length"
    if isinstance(o, JSFunction):
        k = to_str(key)
        return (o.props is not None and k in o.props) or k in ("name", "length", "prototype")
    return False


def get_prop(o, key):
    t = type(o)
    if t is str:
        i = _index_of_key(key)
        if i is not None:
            return o[i] if i < len(o) else UNDEF
        if key == "length":
            return len(o)
        m = STRING_METHODS.get(key)
        return m if m is not None else UNDEF
    if t is JSArray or t is list:
        i = _index_of_key(key)
        if i is not None:
            if i < len(o):
                return o[i]
            return UNDEF
        if key == "length":
            return len(o)
        m = ARRAY_METHODS.get(key)
        return m if m is not None else UNDEF
    if t is TypedArray:
        i = _index_of_key(key)
        if i is not None:
            return o.get(i)
        if key == "length":
            return o.n
        if key == "byteLength":
            return o.n * _TA_INFO[o.kind][0]
        if key == "byteOffset":
            return o.off * _TA_INFO[o.kind][0]
        if key == "buffer":
            return o
        m = TA_METHODS.get(key)
        return m if m is not None else UNDEF
    if isinstance(o, JSObject):
        getter = getattr(o, "cls", None)
        v = obj_lookup(o, key)
        if v is UNDEF and getter is not None and hasattr(getter, "get_prop"):
            return getter.get_prop(o, key)
        return v
    if o is UNDEF or o is None:
        js_throw("Cannot read properties of {} (reading '{}')".format(to_str(o), to_str(key)))
    if isinstance(o, JSFunction):
        k = to_str(key)
        if o.props is not None and k in o.props:
            return dict.__getitem__(o.props, k)
        if k == "name":
            return o.name
        if k == "length":
            return 0
        m = FUNCTION_METHODS.get(k)
        return m if m is not None else UNDEF
    if isinstance(o, bool):
        return BOOL_METHODS.get(key, UNDEF)
    if isinstance(o, JSBigInt):
        return BIGINT_METHODS.get(key, UNDEF)
    if isinstance(o, (int, float)):
        return NUMBER_METHODS.get(key, UNDEF)
    if isinstance(o, JSRegExp):
        if key == "lastIndex":
            return o.lastIndex
        if key == "source":
            return o.source
        if key == "flags":
            return o.flags
        if key == "global":
            return "g" in o.flags
        return REGEXP_METHODS.get(key, UNDEF)
    if isinstance(o, JSPromise):
        return PROMISE_METHODS.get(key, UNDEF)
    hook = getattr(o, "js_get", None)
    if hook is not None:
        return hook(key)
    return UNDEF


def set_prop(o, key, v):
    t = type(o)
    if t is JSArray or t is list:
        i = _index_of_key(key)
        if i is not None:
            n = len(o)
            if i < n:
                o[i] = v
            else:
                if i > n:
                    o.extend([UNDEF] * (i - n))
                o.append(v)
            return
        if key == "length":
            n = to_integer(v)
            if n < len(o):
                del o[n:]
            else:
                o.extend([UNDEF] * (n - len(o)))
            return
        return  # named props on arrays: ignored
    if t is TypedArray:
        i = _index_of_key(key)
        if i is not None:
            o.set(i, v)
        return
    if isinstance(o, JSObject):
        dict.__setitem__(o, _norm_obj_key(key), v)
        return
    if isinstance(o, JSFunction):
        if o.props is None:
            o.props = JSObject()
        o.props[to_str(key)] = v
        return
    if isinstance(o, JSRegExp) and key == "lastIndex":
        o.lastIndex = to_integer(v)
        return
    if o is UNDEF or o is None:
        js_throw("Cannot set properties of {} (setting '{}')".format(to_str(o), to_str(key)))
    hook = getattr(o, "js_set", None)
    if hook is not None:
        hook(key, v)


def call_function(f, this, args):
    if isinstance(f, JSFunction):
        return f.fn(this, args)
    js_throw("{} is not a function".format(type_of(f) if f is UNDEF else to_str(f)[:60]))


def call_method(o, name, args):
    f = get_prop(o, name)
    if isinstance(f, JSFunction):
        return f.fn(o, args)
    js_throw("{}.{} is not a function".format(type_of(o), to_str(name)))


def construct(f, args):
    if not isinstance(f, JSFunction) or f.ctor is None:
        js_throw("{} is not a constructor".format(to_str(f)[:60]))
    return f.ctor(args)


def own_keys(o):
    if isinstance(o, JSObject):
        ks = list(dict.keys(o))
        ints = sorted((k for k in ks if k.isdigit() and (k == "0" or k[0] != "0")), key=int)
        rest = [k for k in ks if not (k.isdigit() and (k == "0" or k[0] != "0"))]
        return ints + rest
    if isinstance(o, (list, str)):
        return [str(i) for i in range(len(o))]
    if isinstance(o, TypedArray):
        return [str(i) for i in range(o.n)]
    return []


def arg(args, i):
    return args[i] if i < len(args) else UNDEF


def promise_resolve(v):
    if isinstance(v, JSPromise):
        return v
    return JSPromise(True, v)


def await_value(v):
    if isinstance(v, JSPromise):
        if v.ok:
            return v.value
        raise JSThrow(v.value)
    if isinstance(v, JSObject):
        th = obj_lookup(v, "then")
        if isinstance(th, JSFunction):
            box = {}

            def ok(this, a):
                box["ok"] = arg(a, 0)
                return UNDEF

            def bad(this, a):
                box["err"] = arg(a, 0)
                return UNDEF
            th.fn(v, [JSFunction(ok), JSFunction(bad)])
            if "err" in box:
                raise JSThrow(box["err"])
            return box.get("ok", UNDEF)
    return v


def iterate(v):
    """for spread `...v`"""
    if isinstance(v, (list, JSArray)):
        return list(v)
    if isinstance(v, TypedArray):
        return v.tolist()
    if isinstance(v, str):
        return list(v)
    if isinstance(v, JSMapLike):
        return v.iter_values()
    if v is UNDEF or v is None:
        js_throw("{} is not iterable".format(to_str(v)))
    js_throw("object is not iterable")


class JSMapLike(JSObject):
    def iter_values(self):
        return []


# ─── string methods ──────────────────────────────────────────────────────

def _m(table, name):
    def deco(f):
        table[name] = JSFunction(f, name)
        return f
    return deco


STRING_METHODS = {}
ARRAY_METHODS = {}
TA_METHODS = {}
NUMBER_METHODS = {}
BIGINT_METHODS = {}
BOOL_METHODS = {}
FUNCTION_METHODS = {}
REGEXP_METHODS = {}
PROMISE_METHODS = {}
OBJECT_PROTO_METHODS = {}


def _this_str(this):
    if isinstance(this, str):
        return this
    if this is UNDEF or this is None:
        js_throw("String.prototype method called on null or undefined")
    return to_str(this)


def _rel_index(v, n, default):
    if v is UNDEF:
        return default
    i = to_integer(v)
    if i == _INF:
        return n
    if i == -_INF:
        return 0
    if i < 0:
        return max(0, n + i)
    return min(i, n)


@_m(STRING_METHODS, "charCodeAt")
def _s_charCodeAt(this, a):
    s = _this_str(this)
    i = 0 if not a else to_integer(a[0])
    if 0 <= i < len(s):
        c = ord(s[i])
        return c if c < 0x10000 else c  # (no surrogate splitting needed here)
    return _NAN


@_m(STRING_METHODS, "codePointAt")
def _s_codePointAt(this, a):
    s = _this_str(this)
    i = 0 if not a else to_integer(a[0])
    return ord(s[i]) if 0 <= i < len(s) else UNDEF


@_m(STRING_METHODS, "charAt")
def _s_charAt(this, a):
    s = _this_str(this)
    i = 0 if not a else to_integer(a[0])
    return s[i] if 0 <= i < len(s) else ""


@_m(STRING_METHODS, "at")
def _s_at(this, a):
    s = _this_str(this)
    i = to_integer(arg(a, 0))
    if i < 0:
        i += len(s)
    return s[i] if 0 <= i < len(s) else UNDEF


@_m(STRING_METHODS, "slice")
def _s_slice(this, a):
    s = _this_str(this)
    n = len(s)
    b = _rel_index(arg(a, 0), n, 0)
    e = _rel_index(arg(a, 1), n, n)
    return s[b:e] if b < e else ""


@_m(STRING_METHODS, "substring")
def _s_substring(this, a):
    s = _this_str(this)
    n = len(s)

    def cl(v, d):
        if v is UNDEF:
            return d
        i = to_integer(v)
        if isinstance(i, float):
            return 0 if i < 0 else n
        return min(max(i, 0), n)
    b = cl(arg(a, 0), 0)
    e = cl(arg(a, 1), n)
    if b > e:
        b, e = e, b
    return s[b:e]


@_m(STRING_METHODS, "substr")
def _s_substr(this, a):
    s = _this_str(this)
    n = len(s)
    b = _rel_index(arg(a, 0), n, 0)
    ln = n - b if arg(a, 1) is UNDEF else max(0, to_integer(arg(a, 1)))
    return s[b:b + ln]


@_m(STRING_METHODS, "indexOf")
def _s_indexOf(this, a):
    s = _this_str(this)
    return s.find(to_str(arg(a, 0)), _rel_index(arg(a, 1), len(s), 0) if len(a) > 1 else 0)


@_m(STRING_METHODS, "lastIndexOf")
def _s_lastIndexOf(this, a):
    return _this_str(this).rfind(to_str(arg(a, 0)))


@_m(STRING_METHODS, "includes")
def _s_includes(this, a):
    return to_str(arg(a, 0)) in _this_str(this)


@_m(STRING_METHODS, "startsWith")
def _s_startsWith(this, a):
    s = _this_str(this)
    p = 0 if arg(a, 1) is UNDEF else to_integer(a[1])
    return s.startswith(to_str(arg(a, 0)), p)


@_m(STRING_METHODS, "endsWith")
def _s_endsWith(this, a):
    s = _this_str(this)
    if arg(a, 1) is not UNDEF:
        s = s[:to_integer(a[1])]
    return s.endswith(to_str(arg(a, 0)))


@_m(STRING_METHODS, "toLowerCase")
def _s_lower(this, a):
    return _this_str(this).lower()


@_m(STRING_METHODS, "toUpperCase")
def _s_upper(this, a):
    return _this_str(this).upper()


@_m(STRING_METHODS, "trim")
def _s_trim(this, a):
    return _this_str(this).strip(" \t\n\r\v\f ﻿")


@_m(STRING_METHODS, "trimStart")
def _s_trims(this, a):
    return _this_str(this).lstrip(" \t\n\r\v\f ﻿")


@_m(STRING_METHODS, "trimEnd")
def _s_trime(this, a):
    return _this_str(this).rstrip(" \t\n\r\v\f ﻿")


@_m(STRING_METHODS, "toString")
def _s_tostring(this, a):
    return _this_str(this)


@_m(STRING_METHODS, "valueOf")
def _s_valueof(this, a):
    return _this_str(this)


@_m(STRING_METHODS, "concat")
def _s_concat(this, a):
    return _this_str(this) + "".join(to_str(x) for x in a)


@_m(STRING_METHODS, "repeat")
def _s_repeat(this, a):
    return _this_str(this) * to_integer(arg(a, 0))


@_m(STRING_METHODS, "padStart")
def _s_padStart(this, a):
    s = _this_str(this)
    n = to_integer(arg(a, 0))
    f = " " if arg(a, 1) is UNDEF else to_str(a[1])
    if len(s) >= n or not f:
        return s
    pad = (f * (n // len(f) + 1))[:n - len(s)]
    return pad + s


@_m(STRING_METHODS, "padEnd")
def _s_padEnd(this, a):
    s = _this_str(this)
    n = to_integer(arg(a, 0))
    f = " " if arg(a, 1) is UNDEF else to_str(a[1])
    if len(s) >= n or not f:
        return s
    return s + (f * (n // len(f) + 1))[:n - len(s)]


@_m(STRING_METHODS, "split")
def _s_split(this, a):
    s = _this_str(this)
    sep = arg(a, 0)
    lim = arg(a, 1)
    if sep is UNDEF:
        out = [s]
    elif isinstance(sep, JSRegExp):
        out = sep.rx.split(s)
        out = [x if x is not None else UNDEF for x in out]
    else:
        sep = to_str(sep)
        out = list(s) if sep == "" else s.split(sep)
    if lim is not UNDEF:
        out = out[:to_uint32(lim)]
    return JSArray(out)


def _expand_replacement(rep, m, s):
    out = []
    i = 0
    while i < len(rep):
        c = rep[i]
        if c == "$" and i + 1 < len(rep):
            d = rep[i + 1]
            if d == "$":
                out.append("$")
                i += 2
                continue
            if d == "&":
                out.append(m.group(0))
                i += 2
                continue
            if d == "`":
                out.append(s[:m.start()])
                i += 2
                continue
            if d == "'":
                out.append(s[m.end():])
                i += 2
                continue
            if d.isdigit():
                j = i + 2
                if j < len(rep) and rep[j].isdigit() and int(rep[i + 1:j + 1]) <= (m.re.groups if hasattr(m, "re") else 0):
                    j += 1
                g = int(rep[i + 1:j])
                if 0 < g <= (m.re.groups if hasattr(m, "re") else 0):
                    out.append(m.group(g) or "")
                    i = j
                    continue
        out.append(c)
        i += 1
    return "".join(out)


class _LitMatch(object):
    def __init__(self, s, start, sub):
        self._s = s
        self._st = start
        self._sub = sub
        self.re = None

    def group(self, i=0):
        return self._sub

    def start(self):
        return self._st

    def end(self):
        return self._st + len(self._sub)

    def groups(self):
        return ()


def _do_replace(s, pat, rep, all_=False):
    def repl_for(m):
        if isinstance(rep, JSFunction):
            groups = list(m.groups()) if not isinstance(m, _LitMatch) else []
            args = [m.group(0)] + [g if g is not None else UNDEF for g in groups] + [m.start(), s]
            return to_str(rep.fn(UNDEF, args))
        return _expand_replacement(to_str(rep), m, s)
    if isinstance(pat, JSRegExp):
        glob = "g" in pat.flags or all_
        out = []
        last = 0
        for m in pat.rx.finditer(s):
            out.append(s[last:m.start()])
            out.append(repl_for(m))
            last = m.end()
            if not glob:
                break
        out.append(s[last:])
        return "".join(out)
    p = to_str(pat)
    out = []
    last = 0
    pos = s.find(p)
    while pos >= 0:
        out.append(s[last:pos])
        out.append(repl_for(_LitMatch(s, pos, p)))
        last = pos + len(p)
        if not all_:
            break
        pos = s.find(p, last + (0 if p else 1))
        if not p and pos > len(s):
            break
    out.append(s[last:])
    return "".join(out)


@_m(STRING_METHODS, "replace")
def _s_replace(this, a):
    return _do_replace(_this_str(this), arg(a, 0), arg(a, 1))


@_m(STRING_METHODS, "replaceAll")
def _s_replaceAll(this, a):
    return _do_replace(_this_str(this), arg(a, 0), arg(a, 1), True)


def _match_to_array(m, s):
    arr = JSArray([m.group(0)] + [g if g is not None else UNDEF for g in m.groups()])
    return arr


@_m(STRING_METHODS, "match")
def _s_match(this, a):
    s = _this_str(this)
    r = arg(a, 0)
    if not isinstance(r, JSRegExp):
        r = JSRegExp(to_str(r))
    if "g" in r.flags:
        ms = [m.group(0) for m in r.rx.finditer(s)]
        return JSArray(ms) if ms else None
    m = r.rx.search(s)
    return _match_to_array(m, s) if m else None


@_m(STRING_METHODS, "search")
def _s_search(this, a):
    s = _this_str(this)
    r = arg(a, 0)
    if not isinstance(r, JSRegExp):
        r = JSRegExp(to_str(r))
    m = r.rx.search(s)
    return m.start() if m else -1


@_m(STRING_METHODS, "normalize")
def _s_normalize(this, a):
    return _this_str(this)


@_m(STRING_METHODS, "localeCompare")
def _s_localeCompare(this, a):
    s = _this_str(this)
    t = to_str(arg(a, 0))
    return (s > t) - (s < t)


# ─── array methods ───────────────────────────────────────────────────────

def _this_list(this):
    if isinstance(this, list):
        return this
    if isinstance(this, TypedArray):
        return this
    js_throw("Array method on non-array")


def _call_cb(cb, args):
    if not isinstance(cb, JSFunction):
        js_throw("{} is not a function".format(to_str(cb)))
    return cb.fn(UNDEF, args)


def _seq(this):
    return this.tolist() if isinstance(this, TypedArray) else this


@_m(ARRAY_METHODS, "push")
def _a_push(this, a):
    this.extend(a)
    return len(this)


@_m(ARRAY_METHODS, "pop")
def _a_pop(this, a):
    return this.pop() if this else UNDEF


@_m(ARRAY_METHODS, "shift")
def _a_shift(this, a):
    return this.pop(0) if this else UNDEF


@_m(ARRAY_METHODS, "unshift")
def _a_unshift(this, a):
    this[0:0] = a
    return len(this)


@_m(ARRAY_METHODS, "join")
def _a_join(this, a):
    sep = "," if arg(a, 0) is UNDEF else to_str(a[0])
    return sep.join("" if (x is UNDEF or x is None) else to_str(x) for x in _seq(this))


@_m(ARRAY_METHODS, "reverse")
def _a_reverse(this, a):
    if isinstance(this, TypedArray):
        vals = this.tolist()[::-1]
        for i, v in enumerate(vals):
            this.set(i, v)
        return this
    this.reverse()
    return this


@_m(ARRAY_METHODS, "slice")
def _a_slice(this, a):
    n = len(this)
    b = _rel_index(arg(a, 0), n, 0)
    e = _rel_index(arg(a, 1), n, n)
    if isinstance(this, TypedArray):
        out = new_typed(this.kind, max(0, e - b))
        for i in range(max(0, e - b)):
            out.set(i, this.get(b + i))
        out.is_buffer = False
        return out
    return JSArray(this[b:e])


@_m(ARRAY_METHODS, "splice")
def _a_splice(this, a):
    n = len(this)
    b = _rel_index(arg(a, 0), n, 0)
    if len(a) < 2:
        cnt = n - b
    else:
        cnt = max(0, min(to_integer(a[1]), n - b))
    removed = JSArray(this[b:b + cnt])
    this[b:b + cnt] = a[2:]
    return removed


@_m(ARRAY_METHODS, "concat")
def _a_concat(this, a):
    out = JSArray(_seq(this))
    for x in a:
        if isinstance(x, list):
            out.extend(x)
        else:
            out.append(x)
    return out


@_m(ARRAY_METHODS, "indexOf")
def _a_indexOf(this, a):
    v = arg(a, 0)
    for i, x in enumerate(_seq(this)):
        if strict_eq(x, v):
            return i
    return -1


@_m(ARRAY_METHODS, "lastIndexOf")
def _a_lastIndexOf(this, a):
    v = arg(a, 0)
    s = _seq(this)
    for i in range(len(s) - 1, -1, -1):
        if strict_eq(s[i], v):
            return i
    return -1


@_m(ARRAY_METHODS, "includes")
def _a_includes(this, a):
    v = arg(a, 0)
    for x in _seq(this):
        if strict_eq(x, v) or (isinstance(x, float) and x != x and isinstance(v, float) and v != v):
            return True
    return False


@_m(ARRAY_METHODS, "map")
def _a_map(this, a):
    cb = arg(a, 0)
    return JSArray(_call_cb(cb, [x, i, this]) for i, x in enumerate(_seq(this)))


@_m(ARRAY_METHODS, "forEach")
def _a_forEach(this, a):
    cb = arg(a, 0)
    for i, x in enumerate(list(_seq(this))):
        _call_cb(cb, [x, i, this])
    return UNDEF


@_m(ARRAY_METHODS, "filter")
def _a_filter(this, a):
    cb = arg(a, 0)
    return JSArray(x for i, x in enumerate(_seq(this)) if truthy(_call_cb(cb, [x, i, this])))


@_m(ARRAY_METHODS, "some")
def _a_some(this, a):
    cb = arg(a, 0)
    for i, x in enumerate(_seq(this)):
        if truthy(_call_cb(cb, [x, i, this])):
            return True
    return False


@_m(ARRAY_METHODS, "every")
def _a_every(this, a):
    cb = arg(a, 0)
    for i, x in enumerate(_seq(this)):
        if not truthy(_call_cb(cb, [x, i, this])):
            return False
    return True


@_m(ARRAY_METHODS, "find")
def _a_find(this, a):
    cb = arg(a, 0)
    for i, x in enumerate(_seq(this)):
        if truthy(_call_cb(cb, [x, i, this])):
            return x
    return UNDEF


@_m(ARRAY_METHODS, "findIndex")
def _a_findIndex(this, a):
    cb = arg(a, 0)
    for i, x in enumerate(_seq(this)):
        if truthy(_call_cb(cb, [x, i, this])):
            return i
    return -1


@_m(ARRAY_METHODS, "reduce")
def _a_reduce(this, a):
    cb = arg(a, 0)
    s = list(_seq(this))
    if len(a) > 1:
        acc = a[1]
        start = 0
    else:
        if not s:
            js_throw("Reduce of empty array with no initial value")
        acc = s[0]
        start = 1
    for i in range(start, len(s)):
        acc = _call_cb(cb, [acc, s[i], i, this])
    return acc


@_m(ARRAY_METHODS, "fill")
def _a_fill(this, a):
    v = arg(a, 0)
    n = len(this)
    b = _rel_index(arg(a, 1), n, 0)
    e = _rel_index(arg(a, 2), n, n)
    if isinstance(this, TypedArray):
        for i in range(b, e):
            this.set(i, v)
    else:
        for i in range(b, e):
            this[i] = v
    return this


def _sort_key_default(x):
    return to_str(x)


@_m(ARRAY_METHODS, "sort")
def _a_sort(this, a):
    cmp = arg(a, 0)
    import functools
    vals = _seq(this)
    defined = [x for x in vals if x is not UNDEF]
    undef = [x for x in vals if x is UNDEF]
    if isinstance(cmp, JSFunction):
        def c(x, y):
            r = to_number(cmp.fn(UNDEF, [x, y]))
            if r != r:
                return 0
            return (r > 0) - (r < 0)
        defined.sort(key=functools.cmp_to_key(c))
    elif isinstance(this, TypedArray):
        defined.sort()
    else:
        defined.sort(key=lambda x: to_str(x).encode("utf-16-be"))
    res = defined + undef
    if isinstance(this, TypedArray):
        for i, v in enumerate(res):
            this.set(i, v)
    else:
        this[:] = res
    return this


@_m(ARRAY_METHODS, "keys")
def _a_keys(this, a):
    return JSArray(range(len(this)))


@_m(ARRAY_METHODS, "flat")
def _a_flat(this, a):
    out = JSArray()
    for x in this:
        if isinstance(x, list):
            out.extend(x)
        else:
            out.append(x)
    return out


@_m(ARRAY_METHODS, "at")
def _a_at(this, a):
    i = to_integer(arg(a, 0))
    s = _seq(this)
    if i < 0:
        i += len(s)
    return s[i] if 0 <= i < len(s) else UNDEF


@_m(ARRAY_METHODS, "toString")
def _a_toString(this, a):
    return to_str(this)


# ─── Buffer / typed array methods ───────────────────────────────────────

for _k in ("join", "reverse", "slice", "indexOf", "lastIndexOf", "includes", "map", "forEach",
           "filter", "some", "every", "find", "findIndex", "reduce", "fill", "sort", "at", "keys"):
    TA_METHODS[_k] = ARRAY_METHODS[_k]


def _ta_map(this, a):
    cb = arg(a, 0)
    out = new_typed(this.kind, this.n)
    for i, x in enumerate(this.tolist()):
        out.set(i, _call_cb(cb, [x, i, this]))
    return out


TA_METHODS["map"] = JSFunction(_ta_map, "map")


def _ta_slice(this, a):
    n = this.n
    b = _rel_index(arg(a, 0), n, 0)
    e = _rel_index(arg(a, 1), n, n)
    if this.bytes1:
        # Node: Buffer.slice is a view; Uint8Array.slice is a copy
        if this.is_buffer:
            return TypedArray(this.kind, this.buf, this.off + b, max(0, e - b), True)
        return TypedArray(this.kind, bytearray(this.buf[this.off + b:this.off + max(b, e)]))
    return ARRAY_METHODS["slice"].fn(this, a)


TA_METHODS["slice"] = JSFunction(_ta_slice, "slice")


@_m(TA_METHODS, "subarray")
def _ta_subarray(this, a):
    n = this.n
    b = _rel_index(arg(a, 0), n, 0)
    e = _rel_index(arg(a, 1), n, n)
    return TypedArray(this.kind, this.buf, this.off + b, max(0, e - b), this.is_buffer)


@_m(TA_METHODS, "set")
def _ta_set(this, a):
    src = arg(a, 0)
    off = 0 if arg(a, 1) is UNDEF else to_integer(a[1])
    vals = src.tolist() if isinstance(src, TypedArray) else list(src)
    if off + len(vals) > this.n:
        js_throw("offset is out of bounds", "RangeError")
    for i, v in enumerate(vals):
        this.set(off + i, v)
    return UNDEF


@_m(TA_METHODS, "copy")
def _ta_copy(this, a):
    target = arg(a, 0)
    ts = 0 if arg(a, 1) is UNDEF else to_integer(a[1])
    ss = 0 if arg(a, 2) is UNDEF else to_integer(a[2])
    se = this.n if arg(a, 3) is UNDEF else min(to_integer(a[3]), this.n)
    data = bytes(this.buf[this.off + ss:this.off + se])
    n = max(0, min(len(data), target.n - ts))
    target.buf[target.off + ts:target.off + ts + n] = data[:n]
    return n


@_m(TA_METHODS, "equals")
def _ta_equals(this, a):
    return this.tobytes() == as_bytes(arg(a, 0))


@_m(TA_METHODS, "compare")
def _ta_compare(this, a):
    x = this.tobytes()
    y = as_bytes(arg(a, 0))
    return (x > y) - (x < y)


def _rw(fmt, size):
    def reader(this, a):
        off = 0 if arg(a, 0) is UNDEF else to_integer(a[0])
        if off < 0 or off + size > this.n:
            js_throw("The value of \"offset\" is out of range.", "RangeError")
        b = bytes(this.buf[this.off + off:this.off + off + size])
        return struct.unpack(fmt, b)[0]

    def writer(this, a):
        v = to_number(arg(a, 0))
        off = 0 if arg(a, 1) is UNDEF else to_integer(a[1])
        if off < 0 or off + size > this.n:
            js_throw("The value of \"offset\" is out of range.", "RangeError")
        if fmt[-1] in "fd":
            b = struct.pack(fmt, float(v))
        else:
            iv = int(v) if v == v else 0
            bits = size * 8
            iv &= (1 << bits) - 1
            if fmt[-1].islower() and iv >= (1 << (bits - 1)):
                iv -= 1 << bits
            b = struct.pack(fmt, iv)
        this.buf[this.off + off:this.off + off + size] = b
        return off + size
    return reader, writer


for _name, _fmt, _sz in (("UInt8", "<B", 1), ("Int8", "<b", 1),
                         ("UInt16LE", "<H", 2), ("UInt16BE", ">H", 2),
                         ("Int16LE", "<h", 2), ("Int16BE", ">h", 2),
                         ("UInt32LE", "<I", 4), ("UInt32BE", ">I", 4),
                         ("Int32LE", "<i", 4), ("Int32BE", ">i", 4),
                         ("FloatLE", "<f", 4), ("FloatBE", ">f", 4),
                         ("DoubleLE", "<d", 8), ("DoubleBE", ">d", 8)):
    _r, _w = _rw(_fmt, _sz)
    TA_METHODS["read" + _name] = JSFunction(_r, "read" + _name)
    TA_METHODS["write" + _name] = JSFunction(_w, "write" + _name)
    if "UInt" in _name:
        TA_METHODS["readUint" + _name[4:]] = TA_METHODS["read" + _name]
        TA_METHODS["writeUint" + _name[4:]] = TA_METHODS["write" + _name]


def _big_rw(signed, little):
    def reader(this, a):
        off = 0 if arg(a, 0) is UNDEF else to_integer(a[0])
        b = bytes(this.buf[this.off + off:this.off + off + 8])
        return JSBigInt(int.from_bytes(b, "little" if little else "big", signed=signed))

    def writer(this, a):
        v = int(arg(a, 0))
        off = 0 if arg(a, 1) is UNDEF else to_integer(a[1])
        v &= (1 << 64) - 1
        this.buf[this.off + off:this.off + off + 8] = v.to_bytes(8, "little" if little else "big")
        return off + 8
    return reader, writer


for _name, _sg, _le in (("BigUInt64LE", False, True), ("BigUInt64BE", False, False),
                        ("BigInt64LE", True, True), ("BigInt64BE", True, False)):
    _r, _w = _big_rw(_sg, _le)
    TA_METHODS["read" + _name] = JSFunction(_r, "read" + _name)
    TA_METHODS["write" + _name] = JSFunction(_w, "write" + _name)


def buffer_tostring(buf, enc="utf8", start=0, end=None):
    b = buf.tobytes() if isinstance(buf, TypedArray) else bytes(buf)
    b = b[start:end]
    enc = (enc or "utf8").lower()
    if enc in ("utf8", "utf-8"):
        return b.decode("utf-8", "replace")
    if enc == "hex":
        return binascii.hexlify(b).decode("ascii")
    if enc == "base64":
        return base64.b64encode(b).decode("ascii")
    if enc == "base64url":
        return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")
    if enc in ("latin1", "binary"):
        return b.decode("latin-1")
    if enc == "ascii":
        return "".join(chr(x & 0x7F) for x in b)
    if enc in ("utf16le", "ucs2", "ucs-2", "utf-16le"):
        return b[:len(b) // 2 * 2].decode("utf-16-le", "replace")
    js_throw("Unknown encoding: " + enc)


@_m(TA_METHODS, "toString")
def _ta_toString(this, a):
    if this.is_buffer:
        enc = "utf8" if arg(a, 0) is UNDEF else to_str(a[0])
        st = 0 if arg(a, 1) is UNDEF else to_integer(a[1])
        en = None if arg(a, 2) is UNDEF else to_integer(a[2])
        return buffer_tostring(this, enc, st, en)
    return to_str(this)


@_m(TA_METHODS, "toJSON")
def _ta_toJSON(this, a):
    o = JSObject()
    o["type"] = "Buffer"
    o["data"] = JSArray(this.tolist())
    return o


@_m(TA_METHODS, "write")
def _ta_write(this, a):
    s = to_str(arg(a, 0))
    off = 0
    enc = "utf8"
    rest = a[1:]
    if rest and isinstance(rest[0], str):
        enc = rest[0]
    elif rest:
        off = to_integer(rest[0])
        if len(rest) > 1 and isinstance(rest[-1], str):
            enc = rest[-1]
    data = buffer_from_string(s, enc)
    n = max(0, min(len(data), this.n - off))
    this.buf[this.off + off:this.off + off + n] = data[:n]
    return n


def buffer_from_string(s, enc="utf8"):
    enc = (enc or "utf8").lower()
    if enc in ("utf8", "utf-8"):
        return s.encode("utf-8", "surrogatepass")
    if enc == "hex":
        good = []
        for i in range(0, len(s) - 1, 2):
            pair = s[i:i + 2]
            try:
                good.append(int(pair, 16))
            except ValueError:
                break
        return bytes(good)
    if enc in ("base64", "base64url"):
        t = re.sub(r"[^A-Za-z0-9+/\-_]", "", s).replace("-", "+").replace("_", "/")
        t = t[:len(t) - (len(t) % 4 == 1)]
        t += "=" * (-len(t) % 4)
        return base64.b64decode(t)
    if enc in ("latin1", "binary", "ascii"):
        return bytes(ord(c) & 0xFF for c in s)
    if enc in ("utf16le", "ucs2", "ucs-2", "utf-16le"):
        return s.encode("utf-16-le", "surrogatepass")
    js_throw("Unknown encoding: " + enc)


# ─── number / misc prototype methods ────────────────────────────────────

@_m(NUMBER_METHODS, "toString")
def _n_toString(this, a):
    r = 10 if arg(a, 0) is UNDEF else to_integer(a[0])
    return num_to_str(this, r)


@_m(NUMBER_METHODS, "toFixed")
def _n_toFixed(this, a):
    d = 0 if arg(a, 0) is UNDEF else to_integer(a[0])
    return "{:.{}f}".format(this, d)


@_m(NUMBER_METHODS, "valueOf")
def _n_valueOf(this, a):
    return this


@_m(BIGINT_METHODS, "toString")
def _b_toString(this, a):
    r = 10 if arg(a, 0) is UNDEF else to_integer(a[0])
    return _int_radix(int(this), r)


@_m(BOOL_METHODS, "toString")
def _bo_toString(this, a):
    return to_str(this)


@_m(FUNCTION_METHODS, "call")
def _f_call(this, a):
    return call_function(this, arg(a, 0), list(a[1:]))


@_m(FUNCTION_METHODS, "apply")
def _f_apply(this, a):
    args = arg(a, 1)
    return call_function(this, arg(a, 0), [] if args is UNDEF or args is None else iterate(args))


@_m(FUNCTION_METHODS, "bind")
def _f_bind(this, a):
    target = this
    bthis = arg(a, 0)
    pre = list(a[1:])
    return JSFunction(lambda t, args: call_function(target, bthis, pre + list(args)), "bound " + target.name)


@_m(REGEXP_METHODS, "test")
def _r_test(this, a):
    s = to_str(arg(a, 0))
    if "g" in this.flags or "y" in this.flags:
        m = this.rx.search(s, this.lastIndex)
        if m:
            this.lastIndex = m.end()
            return True
        this.lastIndex = 0
        return False
    return this.rx.search(s) is not None


@_m(REGEXP_METHODS, "exec")
def _r_exec(this, a):
    s = to_str(arg(a, 0))
    start = this.lastIndex if ("g" in this.flags or "y" in this.flags) else 0
    m = this.rx.search(s, start)
    if not m:
        this.lastIndex = 0
        return None
    if "g" in this.flags or "y" in this.flags:
        this.lastIndex = m.end()
    return _match_to_array(m, s)


@_m(PROMISE_METHODS, "then")
def _p_then(this, a):
    ok = arg(a, 0)
    bad = arg(a, 1)
    try:
        if this.ok:
            if isinstance(ok, JSFunction):
                return promise_resolve(_settle(ok.fn(UNDEF, [this.value])))
            return this
        if isinstance(bad, JSFunction):
            return promise_resolve(_settle(bad.fn(UNDEF, [this.value])))
        return this
    except JSThrow as e:
        return JSPromise(False, e.value)


def _settle(v):
    if isinstance(v, JSPromise):
        return v
    return v


@_m(PROMISE_METHODS, "catch")
def _p_catch(this, a):
    bad = arg(a, 0)
    if this.ok or not isinstance(bad, JSFunction):
        return this
    try:
        return promise_resolve(bad.fn(UNDEF, [this.value]))
    except JSThrow as e:
        return JSPromise(False, e.value)


@_m(PROMISE_METHODS, "finally")
def _p_finally(this, a):
    f = arg(a, 0)
    if isinstance(f, JSFunction):
        f.fn(UNDEF, [])
    return this


@_m(OBJECT_PROTO_METHODS, "hasOwnProperty")
def _o_hasOwn(this, a):
    if isinstance(this, JSObject):
        return _norm_obj_key(arg(a, 0)) in dict.keys(this)
    return has_property(this, arg(a, 0))


OBJECT_PROTO_METHODS["toString"] = JSFunction(lambda t, a: "[object Object]", "toString#obj")
OBJECT_PROTO_METHODS["valueOf"] = JSFunction(lambda t, a: t, "valueOf#obj")


# ─── JSON ───────────────────────────────────────────────────────────────

def from_py(v):
    if isinstance(v, dict):
        o = JSObject()
        for k, x in v.items():
            o[str(k)] = from_py(x)
        return o
    if isinstance(v, (list, tuple)):
        return JSArray(from_py(x) for x in v)
    if isinstance(v, float):
        return norm_num(v)
    return v


def to_py(v, depth=0):
    if depth > 60:
        return None
    if v is UNDEF:
        return None
    if isinstance(v, JSObject):
        return {k: to_py(x, depth + 1) for k, x in v.items() if x is not UNDEF and not isinstance(x, JSFunction)}
    if isinstance(v, list):
        return [to_py(x, depth + 1) for x in v]
    if isinstance(v, TypedArray):
        return v.tolist()
    if isinstance(v, JSBigInt):
        return int(v)
    return v


def json_stringify(v, indent=None):
    def conv(x):
        if isinstance(x, JSObject):
            tj = obj_lookup(x, "toJSON")
            if isinstance(tj, JSFunction):
                x = tj.fn(x, [])
                return conv(x)
            out = {}
            for k in own_keys(x):
                val = dict.__getitem__(x, k)
                if val is UNDEF or isinstance(val, JSFunction):
                    continue
                out[k] = conv(val)
            return out
        if isinstance(x, list):
            return [None if (e is UNDEF or isinstance(e, JSFunction)) else conv(e) for e in x]
        if isinstance(x, TypedArray):
            if x.is_buffer:
                return {"type": "Buffer", "data": x.tolist()}
            return {str(i): v for i, v in enumerate(x.tolist())}
        if isinstance(x, JSBigInt):
            js_throw("Do not know how to serialize a BigInt")
        if isinstance(x, float):
            if x != x or x in (_INF, -_INF):
                return None
            return norm_num(x)
        return x
    if v is UNDEF or isinstance(v, JSFunction):
        return UNDEF
    s = json.dumps(conv(v), ensure_ascii=False, separators=(",", ":") if indent is None else None,
                   indent=indent)
    return s


def json_parse(s):
    try:
        return from_py(json.loads(s))
    except ValueError as e:
        js_throw("Unexpected token in JSON: " + str(e)[:80], "SyntaxError")


# ─── crypto (Node's `crypto` as bundled by crypto-browserify) ───────────

def _aes_backend():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        return "cryptography"
    except Exception:
        pass
    try:
        from Crypto.Cipher import AES  # noqa
        return "pycryptodome"
    except Exception:
        pass
    return "pure"


_AES = None


def _aes_ecb_encrypt_blocks(key, data):
    """AES-ECB encrypt (data multiple of 16) using the best backend."""
    global _AES
    if _AES is None:
        _AES = _aes_backend()
    if _AES == "cryptography":
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return e.update(data) + e.finalize()
    if _AES == "pycryptodome":
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_ECB).encrypt(data)
    from . import _pure_aes_gcm as P
    if len(key) != 32:
        raise ValueError("pure AES only supports 256-bit keys")
    rk = P._aes256_key_schedule(key)
    out = bytearray()
    for i in range(0, len(data), 16):
        out += P._aes256_encrypt_block_ks(rk, data[i:i + 16])
    return bytes(out)


def _aes_ecb_decrypt_blocks(key, data):
    global _AES
    if _AES is None:
        _AES = _aes_backend()
    if _AES == "cryptography":
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        return d.update(data) + d.finalize()
    if _AES == "pycryptodome":
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_ECB).decrypt(data)
    return _pure_aes_decrypt(key, data)


def _pure_aes_decrypt(key, data):
    from . import _pure_aes_gcm as P
    inv = [0] * 256
    for i, v in enumerate(P._SBOX):
        inv[v] = i
    rk = P._aes256_key_schedule(key)
    # rk: list of 15 round keys (16 bytes each) as produced by the module
    rks = [bytes(r) for r in rk]
    out = bytearray()

    def xor(a, b):
        return bytes(x ^ y for x, y in zip(a, b))

    def inv_shift(s):
        r = bytearray(16)
        for c in range(4):
            for row in range(4):
                r[row + 4 * ((c + row) % 4)] = s[row + 4 * c]
        return r

    def inv_mix(s):
        g = P._gmul
        r = bytearray(16)
        for c in range(4):
            a0, a1, a2, a3 = s[4 * c:4 * c + 4]
            r[4 * c] = g(a0, 14) ^ g(a1, 11) ^ g(a2, 13) ^ g(a3, 9)
            r[4 * c + 1] = g(a0, 9) ^ g(a1, 14) ^ g(a2, 11) ^ g(a3, 13)
            r[4 * c + 2] = g(a0, 13) ^ g(a1, 9) ^ g(a2, 14) ^ g(a3, 11)
            r[4 * c + 3] = g(a0, 11) ^ g(a1, 13) ^ g(a2, 9) ^ g(a3, 14)
        return r
    nr = len(rks) - 1
    for i in range(0, len(data), 16):
        s = bytearray(xor(data[i:i + 16], rks[nr]))
        for rnd in range(nr - 1, 0, -1):
            s = inv_shift(s)
            s = bytearray(inv[x] for x in s)
            s = bytearray(xor(s, rks[rnd]))
            s = inv_mix(s)
        s = inv_shift(s)
        s = bytearray(inv[x] for x in s)
        out += xor(s, rks[0])
    return bytes(out)


def _ctr_stream(key, j, n):
    """AES-CTR keystream with a 16-byte counter block, inc32."""
    blocks = bytearray()
    ctr = bytearray(j)
    cnt = (n + 15) // 16
    for _ in range(cnt):
        blocks += ctr
        c = (int.from_bytes(ctr[12:], "big") + 1) & 0xFFFFFFFF
        ctr[12:] = c.to_bytes(4, "big")
    return _aes_ecb_encrypt_blocks(key, bytes(blocks))[:n]


def _gf_mul(x, y):
    R = 0xE1000000000000000000000000000000
    z = 0
    v = x
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            z ^= v
        v = (v >> 1) ^ R if v & 1 else v >> 1
    return z


def _ghash(h, aad, ct):
    def blocks(b):
        b = b + b"\x00" * (-len(b) % 16)
        for i in range(0, len(b), 16):
            yield int.from_bytes(b[i:i + 16], "big")
    y = 0
    for blk in blocks(aad):
        y = _gf_mul(y ^ blk, h)
    for blk in blocks(ct):
        y = _gf_mul(y ^ blk, h)
    lens = ((len(aad) * 8) << 64) | (len(ct) * 8)
    return _gf_mul(y ^ lens, h)


class _CipherObj(JSObject):
    pass


def _make_cipher(alg, key, iv, decrypt):
    alg = alg.lower()
    key = as_bytes(key)
    iv = as_bytes(iv) if iv is not None and iv is not UNDEF else b""
    st = {"buf": bytearray(), "pad": True, "tag": None, "aad": b"", "prev": iv, "out_tag": None}
    obj = _CipherObj()

    def enc_out(b, enc):
        if enc is UNDEF or enc is None:
            return new_buffer(b)
        return buffer_tostring(b, to_str(enc))

    def to_in(data, enc):
        if isinstance(data, str):
            return buffer_from_string(data, "utf8" if enc is UNDEF else to_str(enc))
        return as_bytes(data)

    def cbc_process(final):
        b = st["buf"]
        if decrypt:
            keep = 16 if (st["pad"] or final) else 0
            if final:
                keep = 0
            n = (len(b) - (16 if not final and st["pad"] else 0)) // 16 * 16
            if not final and st["pad"] and len(b) % 16 == 0 and len(b) >= 16:
                n = len(b) - 16
            elif not final:
                n = len(b) // 16 * 16
                if st["pad"] and n == len(b):
                    n -= 16
            chunk = bytes(b[:n])
            del b[:n]
            if not chunk:
                return b""
            if alg.endswith("-ecb"):
                return _aes_ecb_decrypt_blocks(key, chunk)
            dec = _aes_ecb_decrypt_blocks(key, chunk)
            out = bytearray()
            prev = st["prev"]
            for i in range(0, len(chunk), 16):
                out += bytes(x ^ y for x, y in zip(dec[i:i + 16], prev))
                prev = chunk[i:i + 16]
            st["prev"] = prev
            return bytes(out)
        n = len(b) // 16 * 16
        chunk = bytes(b[:n])
        del b[:n]
        if not chunk:
            return b""
        out = bytearray()
        prev = st["prev"]
        for i in range(0, len(chunk), 16):
            blk = chunk[i:i + 16]
            if not alg.endswith("-ecb"):
                blk = bytes(x ^ y for x, y in zip(blk, prev))
            c = _aes_ecb_encrypt_blocks(key, blk)
            out += c
            prev = c
        st["prev"] = prev
        return bytes(out)

    is_gcm = alg.endswith("-gcm")
    is_ctr = alg.endswith("-ctr")
    if is_gcm:
        h = int.from_bytes(_aes_ecb_encrypt_blocks(key, b"\x00" * 16), "big")
        if len(iv) == 12:
            j0 = iv + b"\x00\x00\x00\x01"
        else:
            j0 = _ghash(h, b"", iv).to_bytes(16, "big")
        st["j0"] = j0
        st["h"] = h
        st["all"] = bytearray()
    if is_ctr:
        st["pos"] = 0
        st["all"] = bytearray()

    def update(this, a):
        data = to_in(arg(a, 0), arg(a, 1))
        out_enc = arg(a, 2)
        if is_gcm or is_ctr:
            st["all"] += data
            return enc_out(b"", out_enc) if False else enc_out(_stream_part(len(data)), out_enc)
        st["buf"] += data
        return enc_out(cbc_process(False), out_enc)

    def _stream_part(n):
        allb = st["all"]
        pos = st.get("pos", 0)
        if is_gcm:
            j = bytearray(st["j0"])
            c = (int.from_bytes(j[12:], "big") + 1) & 0xFFFFFFFF
            j[12:] = c.to_bytes(4, "big")
            ks = _ctr_stream(key, bytes(j), len(allb))
        else:
            ks = _ctr_stream(key, iv, len(allb))
        st["pos"] = len(allb)
        return bytes(x ^ y for x, y in zip(allb[pos:], ks[pos:]))

    def final(this, a):
        out_enc = arg(a, 0)
        if is_gcm:
            allb = bytes(st["all"])
            if decrypt:
                ct = allb
            else:
                j = bytearray(st["j0"])
                c = (int.from_bytes(j[12:], "big") + 1) & 0xFFFFFFFF
                j[12:] = c.to_bytes(4, "big")
                ks = _ctr_stream(key, bytes(j), len(allb))
                ct = bytes(x ^ y for x, y in zip(allb, ks))
            s = _ghash(st["h"], st["aad"], ct)
            ek = int.from_bytes(_aes_ecb_encrypt_blocks(key, st["j0"]), "big")
            tag = (s ^ ek).to_bytes(16, "big")
            if decrypt:
                if st["tag"] is None or not _hmac.compare_digest(tag[:len(st["tag"])], st["tag"]):
                    js_throw("Unsupported state or unable to authenticate data", "Error")
            else:
                st["out_tag"] = tag
            return enc_out(b"", out_enc)
        if is_ctr:
            return enc_out(b"", out_enc)
        if decrypt:
            rest = cbc_process(True)
            if st["pad"]:
                if not rest:
                    js_throw("bad decrypt", "Error")
                p = rest[-1]
                if p < 1 or p > 16 or rest[-p:] != bytes([p]) * p:
                    js_throw("bad decrypt", "Error")
                rest = rest[:-p]
            return enc_out(rest, out_enc)
        if st["pad"]:
            p = 16 - len(st["buf"]) % 16
            st["buf"] += bytes([p]) * p
        elif len(st["buf"]) % 16:
            js_throw("data not multiple of block length", "Error")
        return enc_out(cbc_process(True), out_enc)

    def set_auto_padding(this, a):
        st["pad"] = truthy(arg(a, 0)) if a else True
        return obj

    def set_auth_tag(this, a):
        st["tag"] = as_bytes(arg(a, 0))
        return UNDEF

    def get_auth_tag(this, a):
        return new_buffer(st["out_tag"] or b"")

    def set_aad(this, a):
        st["aad"] = as_bytes(arg(a, 0))
        return obj
    obj["update"] = JSFunction(update, "update")
    obj["final"] = JSFunction(final, "final")
    obj["setAutoPadding"] = JSFunction(set_auto_padding, "setAutoPadding")
    obj["setAuthTag"] = JSFunction(set_auth_tag, "setAuthTag")
    obj["getAuthTag"] = JSFunction(get_auth_tag, "getAuthTag")
    obj["setAAD"] = JSFunction(set_aad, "setAAD")
    return obj


def _make_hash(alg, key=None):
    alg = alg.lower().replace("-", "")
    if key is None:
        h = hashlib.new(alg)
    else:
        h = _hmac.new(key, digestmod=alg)
    obj = JSObject()

    def update(this, a):
        d = arg(a, 0)
        if isinstance(d, str):
            d = buffer_from_string(d, "utf8" if arg(a, 1) is UNDEF else to_str(a[1]))
        else:
            d = as_bytes(d)
        h.update(d)
        return obj

    def digest(this, a):
        r = h.digest()
        if arg(a, 0) is UNDEF:
            return new_buffer(r)
        return buffer_tostring(r, to_str(a[0]))
    obj["update"] = JSFunction(update, "update")
    obj["digest"] = JSFunction(digest, "digest")
    return obj


def make_crypto(random_bytes=None):
    rb = random_bytes or os.urandom
    c = JSObject()
    c["randomBytes"] = JSFunction(lambda t, a: new_buffer(rb(to_integer(arg(a, 0)))), "randomBytes")
    c["pseudoRandomBytes"] = c["randomBytes"]
    c["rng"] = c["randomBytes"]
    c["prng"] = c["randomBytes"]
    c["createHash"] = JSFunction(lambda t, a: _make_hash(to_str(arg(a, 0))), "createHash")
    c["Hash"] = c["createHash"]
    c["createHmac"] = JSFunction(lambda t, a: _make_hash(to_str(arg(a, 0)), as_bytes(arg(a, 1)) if not isinstance(arg(a, 1), str) else arg(a, 1).encode()), "createHmac")
    c["Hmac"] = c["createHmac"]
    c["createCipheriv"] = JSFunction(lambda t, a: _make_cipher(to_str(arg(a, 0)), arg(a, 1), arg(a, 2), False), "createCipheriv")
    c["Cipheriv"] = c["createCipheriv"]
    c["createDecipheriv"] = JSFunction(lambda t, a: _make_cipher(to_str(arg(a, 0)), arg(a, 1), arg(a, 2), True), "createDecipheriv")
    c["Decipheriv"] = c["createDecipheriv"]

    def gv(t, a):
        x = arg(a, 0)
        if isinstance(x, TypedArray):
            data = rb(x.n * _TA_INFO[x.kind][0])
            if x.bytes1:
                x.buf[x.off:x.off + x.n] = data
            else:
                size = _TA_INFO[x.kind][0]
                for i in range(x.n):
                    x.set(i, int.from_bytes(data[i * size:(i + 1) * size], "little"))
        return x
    c["getRandomValues"] = JSFunction(gv, "getRandomValues")
    c["randomUUID"] = JSFunction(lambda t, a: str(__import__("uuid").uuid4()), "randomUUID")
    return c
