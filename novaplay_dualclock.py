# -*- coding: utf-8 -*-
"""novaplay_dualclock.py — dual-calendar (Gregorian + Hijri) helpers.

Layout produced:
    Friday, September 18, 2026
    الجمعة ٦ ربيع الآخر، ١٤٤٨ هـ

Self-contained: no pip deps, works on any Python 2.7 / 3.x image.
Hijri uses the tabular Islamic calendar (Kuwaiti algorithm).
"""

import datetime

_HIJRI_MONTHS_AR = (
    "محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى",
    "جمادى الآخرة", "رجب", "شعبان", "رمضان", "شوال",
    "ذو القعدة", "ذو الحجة",
)

# Python weekday() : Monday=0 … Sunday=6
_WEEKDAYS_AR = (
    "الاثنين", "الثلاثاء", "الأربعاء", "الخميس",
    "الجمعة", "السبت", "الأحد",
)

_ARABIC_DIGITS = u"٠١٢٣٤٥٦٧٨٩"


def _arabic_numerals(n):
    return u"".join(_ARABIC_DIGITS[int(d)] for d in str(int(n)))


def gregorian_to_hijri(gy, gm, gd):
    if gm < 3:
        gy -= 1
        gm += 12
    a = gy // 100
    b = 2 - a + a // 4
    if gy < 1583:
        b = 0
    jd = int(365.25 * (gy + 4716)) + int(30.6001 * (gm + 1)) + gd + b - 1524
    l = jd - 1948440 + 10632
    n = (l - 1) // 10631
    l = l - 10631 * n + 354
    j = ((10985 - l) // 5316) * ((50 * l) // 17719) + \
        (l // 5670) * ((43 * l) // 15238)
    l = l - ((30 - j) // 15) * ((17719 * j) // 50) - \
        (j // 16) * ((15238 * j) // 43) + 29
    hm = (24 * l) // 709
    hd = l - (709 * hm) // 24
    hy = 30 * n + j - 30
    return hy, hm, hd


_MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
_WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _hijri_offset():
    """[FIX-DC] Days added before the Hijri conversion (config key 'hijri_offset',
    clamped to -2..+2). The tabular calendar can differ by a day or two from the
    calendar an authority announces; this lets you line it up."""
    try:
        from plugin_state import _get_config
        v = int(str(_get_config("hijri_offset", "0")).strip() or 0)
    except Exception:
        v = 0
    return max(-2, min(2, v))


def format_dual_dates(now=None, hijri_offset=None):
    """Return (gregorian_en, hijri_ar, time_12h, period).

    [FIX-DC] Names and AM/PM come from fixed tables, not strftime(), so they do not
    change with the box locale. The Arabic weekday stays the real weekday; only the
    Hijri day/month/year use the optional offset.
    """
    now = now or datetime.datetime.now()
    off = _hijri_offset() if hijri_offset is None else int(hijri_offset)

    greg = u"{}, {} {}, {}".format(_WEEKDAYS_EN[now.weekday()], _MONTHS_EN[now.month - 1], now.day, now.year)

    hdate = now + datetime.timedelta(days=off)
    hy, hm, hd = gregorian_to_hijri(hdate.year, hdate.month, hdate.day)
    hijri = u"{} {} {}\u060c {} \u0647\u0640".format(
        _WEEKDAYS_AR[now.weekday()],
        _arabic_numerals(hd),
        _HIJRI_MONTHS_AR[hm - 1],
        _arabic_numerals(hy))

    t = u"{}:{:02d}".format(now.hour % 12 or 12, now.minute)
    period = u"AM" if now.hour < 12 else u"PM"
    return greg, hijri, t, period
