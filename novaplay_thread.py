# -*- coding: utf-8 -*-
"""
novaplay_thread.py — shared main-thread dispatcher.
==================================================================
Leaf module: imports ONLY from enigma, never from other plugin modules,
so plugin.py / novaplay_subtitles.py / anything else can import it with
zero circular-import risk. Replaces the private copies of
callInMainThread() (novaplay_subtitles' MAIN queue — and plugin.py's
CMIT queue if you ever choose to switch it over too).

Why this exists: two modules each carried their own identical
callInMainThread implementation, duplicated specifically to avoid
circular imports. A leaf module that nobody imports *from* makes the
duplication unnecessary.

Changes in this revision (audit fixes):
  * callInMainThread() now ALWAYS (re)arms the timer after enqueueing.
    The old version armed only when the timer was freshly created — an
    item enqueued while the drain loop was mid-callback depended on the
    drain's own re-check, which doesn't cover every eTimer re-entrant
    start() behavior across images. One-shot start on an idle timer is
    idempotent; on a pending one it just resets the 50ms.
  * Timer-construction failure now drains via the twisted reactor —
    items can't sit in the queue forever if eTimer is unavailable.
  * Callback exceptions are logged to /tmp/arabicplayer.log instead of
    vanishing. A swallowed NameError from a half-applied edit used to
    look like "the screen just didn't update" with zero trace.
"""

import threading

try:
    from enigma import eTimer
except Exception:
    eTimer = None

_MAIN_LOCK = threading.Lock()
_MAIN_QUEUE = []
_MAIN_TIMER = None


def _log_crash(exc):
    # Direct file write, NOT extractors.base.log — this is a leaf
    # module; importing extractors would break the design contract
    # the docstring above advertises.
    try:
        import traceback
        with open("/tmp/arabicplayer.log", "a") as f:
            f.write("[thread] callback crashed: {} {}\n".format(
                exc, traceback.format_exc()[-400:]))
    except Exception:
        pass


def _drain_main_queue():
    global _MAIN_TIMER
    with _MAIN_LOCK:
        items = list(_MAIN_QUEUE)
        del _MAIN_QUEUE[:]
    for _f, _a, _kw in items:
        try:
            _f(*_a, **_kw)
        except Exception as _e:
            _log_crash(_e)
    with _MAIN_LOCK:
        pending = bool(_MAIN_QUEUE)
    if pending and _MAIN_TIMER is not None:
        try:
            _MAIN_TIMER.start(50, True)
        except Exception:
            pass


def callInMainThread(func, *args, **kwargs):
    """Run func(args, kwargs) on the main (UI) thread.
    Safe to call from any thread; also safe if the timer infra is
    unavailable (falls back to twisted reactor, then runs inline)."""
    global _MAIN_TIMER
    if eTimer is None:
        # headless / import-test environment — run inline
        try:
            func(*args, **kwargs)
        except Exception:
            pass
        return
    with _MAIN_LOCK:
        _MAIN_QUEUE.append((func, args, kwargs))
        need_timer = (_MAIN_TIMER is None)
        if need_timer:
            try:
                _MAIN_TIMER = eTimer()
                _MAIN_TIMER.callback.append(_drain_main_queue)
            except Exception:
                _MAIN_TIMER = None
                # timer unavailable: drain inline via the reactor so
                # items can't sit in the queue forever
                try:
                    from twisted.internet import reactor
                    reactor.callFromThread(_drain_main_queue)
                except Exception:
                    pass
    # Always (re)arm after enqueueing: the drain loop's own re-check only
    # covers items added *while callbacks are executing*. If the timer
    # fires between our append and this start(), or eTimer.start() was
    # swallowed mid-callback on this image, an unconditional arm is the
    # only guarantee the new item ever runs.
    if _MAIN_TIMER is not None:
        try:
            _MAIN_TIMER.start(50, True)
        except Exception:
            try:
                from twisted.internet import reactor
                reactor.callFromThread(_drain_main_queue)
            except Exception:
                pass
    else:
        try:
            from twisted.internet import reactor
            reactor.callFromThread(_drain_main_queue)
        except Exception:
            pass