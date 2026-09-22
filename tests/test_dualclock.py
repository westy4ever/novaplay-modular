# -*- coding: utf-8 -*-
"""Dual-calendar tests (headless). Needs the fixed novaplay_dualclock.py
(format_dual_dates(now, hijri_offset) + fixed-table names)."""

import datetime

import novaplay_dualclock as dc


def _hijri_reference(y, m, d):
    """Independent tabular (civil) Islamic calendar by day counting."""
    jdn = datetime.date(y, m, d).toordinal() + 1721425
    days = jdn - 1948440                       # 0 = 1 Muharram 1 AH (civil epoch)
    cycles, rem = divmod(days, 10631)
    yy = 1
    for yy in range(1, 31):
        ylen = 355 if ((11 * yy + 14) % 30) < 11 else 354
        if rem < ylen:
            break
        rem -= ylen
    mm = 1
    for mm in range(1, 13):
        leap = ((11 * yy + 14) % 30) < 11
        mlen = 30 if mm % 2 == 1 else (30 if (mm == 12 and leap) else 29)
        if rem < mlen:
            break
        rem -= mlen
    return cycles * 30 + yy, mm, rem + 1


def test_hijri_matches_independent_tabular_calendar():
    d, end = datetime.date(1950, 1, 1), datetime.date(2100, 12, 31)
    while d <= end:
        got = dc.gregorian_to_hijri(d.year, d.month, d.day)
        assert got == _hijri_reference(d.year, d.month, d.day), d
        assert 1 <= got[1] <= 12 and 1 <= got[2] <= 30, (d, got)
        d += datetime.timedelta(days=1)


def test_clock_12h_boundaries():
    for hour, minute, want_t, want_p in ((0, 5, "12:05", "AM"), (9, 5, "9:05", "AM"),
                                          (12, 0, "12:00", "PM"), (13, 7, "1:07", "PM"),
                                          (23, 59, "11:59", "PM")):
        _g, _h, t, p = dc.format_dual_dates(datetime.datetime(2026, 1, 5, hour, minute), 0)
        assert (t, p) == (want_t, want_p), (hour, minute, t, p)


def test_english_line_does_not_depend_on_locale():
    class NoStrftime(datetime.datetime):
        def strftime(self, fmt):
            raise ValueError("locale-dependent strftime used")
    g, _h, _t, p = dc.format_dual_dates(NoStrftime(2026, 9, 5, 19, 45), 0)
    assert g == "Saturday, September 5, 2026" and p == "PM"        # no zero-padded day


def test_hijri_offset_shifts_hijri_only():
    base = datetime.datetime(2026, 9, 18, 19, 45)
    g0, h0, _, _ = dc.format_dual_dates(base, 0)
    g1, h1, _, _ = dc.format_dual_dates(base, 1)
    assert g0 == g1 and h0 != h1
    assert h0.split()[0] == h1.split()[0]                         # weekday word is the real weekday


def test_config_offset_is_clamped_and_safe():
    import plugin_state as ps
    orig = ps._get_config
    try:
        for raw, want in (("1", 1), ("-3", -2), ("9", 2), ("abc", 0), ("", 0)):
            ps._get_config = lambda k, d="", raw=raw: raw if k == "hijri_offset" else d
            assert dc._hijri_offset() == want, (raw, dc._hijri_offset())
    finally:
        ps._get_config = orig
