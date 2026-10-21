# -*- coding: utf-8 -*-
"""
Transmission on demand for NovaPlay.

transmission-daemon is a separate background service. Left running all the
time it kept retrying dead torrents and seeding finished ones from the same
HDD that holds the swap file and the movies being played, which froze
Enigma2 (iowait ~23%, blocked GUI threads). NovaPlay now uses it only on
demand:

  * add(magnet)        -- starts the daemon if needed (with safe settings
                          written first) and adds the download.
  * pause_all()        -- the player calls this when playback starts:
                          every active torrent is stopped (ids remembered).
  * resume_later()     -- the player calls this on exit: after a short delay
                          (so the next episode can take over without a
                          resume/pause blip) the paused torrents are started
                          again, then stop_if_idle() runs.
  * stop_if_idle()     -- closes the daemon when nothing is left to download
                          (finished torrents do not seed: ratio limit 0).
    The daemon also calls it itself when a download completes, through
    settings.json's script-torrent-done (this file run with --stop-if-idle).

Standalone (no Enigma2 imports) so the done-script can run it with python3.
"""
import json
import os
import subprocess
import threading
import time
import urllib.request

RPC = "http://127.0.0.1:9091/transmission/rpc"
DAEMON = "/usr/bin/transmission-daemon"
BASE = "/media/hdd/transmission"
DEFAULT_ARGS = ["-c", BASE + "/watch", "-g", BASE + "/config",
                "-w", BASE + "/download", "-p", "9091", "-T"]
_STATE = "/etc/enigma2/novaplay_transmission.json"   # last seen daemon args
_PAUSED = "/tmp/novaplay_tr_paused.json"              # ids paused by the player
_HERE = os.path.abspath(__file__)

SAFE_SETTINGS = {
    "download-queue-enabled": True, "download-queue-size": 2,
    "seed-queue-enabled": True, "seed-queue-size": 0,
    "peer-limit-global": 50, "peer-limit-per-torrent": 20,
    "cache-size-mb": 16,
    "speed-limit-down-enabled": True, "speed-limit-down": 800,
    "speed-limit-up-enabled": True, "speed-limit-up": 50,
    "ratio-limit-enabled": True, "ratio-limit": 0,
    "idle-seeding-limit-enabled": True, "idle-seeding-limit": 1,
    "port-forwarding-enabled": False,
}

_lock = threading.Lock()
_sid = [""]
_resume_timer = [None]


def _log(msg):
    try:
        from plugin_common import my_log
        my_log("[Transmission] " + msg)
    except Exception:
        pass


# ─── RPC ────────────────────────────────────────────────────────────────

def rpc(method, args=None, timeout=8):
    body = json.dumps({"method": method, "arguments": args or {}}).encode()
    for _ in range(2):
        req = urllib.request.Request(RPC, data=body, headers={
            "Content-Type": "application/json", "X-Transmission-Session-Id": _sid[0]})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 409:
                _sid[0] = e.headers.get("X-Transmission-Session-Id", "")
                continue
            raise
    raise RuntimeError("no session id")


def is_running():
    try:
        rpc("session-get", {"fields": ["version"]}, timeout=3)
        return True
    except Exception:
        return False


# ─── Daemon start / stop ────────────────────────────────────────────────

def _running_args():
    """argv of a running transmission-daemon (to restart it the same way)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except Exception:
            continue
        if argv and os.path.basename(argv[0]) == "transmission-daemon":
            return argv[1:]
    return None


def _remember_args():
    args = _running_args()
    if args:
        try:
            with open(_STATE, "w") as f:
                json.dump({"args": args}, f)
        except Exception:
            pass
    return args


def _daemon_args():
    try:
        with open(_STATE) as f:
            args = json.load(f).get("args")
        if args:
            return [a for a in args if a not in ("-f", "--foreground")]
    except Exception:
        pass
    return list(DEFAULT_ARGS)


def _config_dir(args):
    for i, a in enumerate(args):
        if a in ("-g", "--config-dir") and i + 1 < len(args):
            return args[i + 1]
    return BASE + "/config"


def _done_script(cfg):
    path = os.path.join(cfg, "novaplay_done.sh")
    text = ("#!/bin/sh\n# Written by NovaPlay: stop transmission-daemon once nothing is left to download.\n"
            "( sleep 15; python3 '%s' --stop-if-idle ) >/dev/null 2>&1 &\n" % _HERE)
    try:
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
        return path
    except Exception:
        return None


def apply_safe_settings(args=None):
    """Write SAFE_SETTINGS into settings.json. Only while the daemon is
    stopped: it rewrites the file on exit."""
    cfg = _config_dir(args or _daemon_args())
    path = os.path.join(cfg, "settings.json")
    try:
        os.makedirs(cfg, exist_ok=True)
        try:
            with open(path) as f:
                s = json.load(f)
        except Exception:
            s = {}
        s.update(SAFE_SETTINGS)
        script = _done_script(cfg)
        if script:
            s["script-torrent-done-enabled"] = True
            s["script-torrent-done-filename"] = script
        with open(path, "w") as f:
            json.dump(s, f, indent=4, sort_keys=True)
        return True
    except Exception as e:
        _log("settings: {}".format(e))
        return False


def ensure_started(wait=15):
    if is_running():
        _remember_args()
        return True
    if not os.path.exists(DAEMON):
        _log("not installed")
        return False
    args = _daemon_args()
    for i, a in enumerate(args):
        if a in ("-c", "-w", "--watch-dir", "--download-dir") and i + 1 < len(args):
            try:
                os.makedirs(args[i + 1], exist_ok=True)
            except Exception:
                pass
    apply_safe_settings(args)
    try:
        subprocess.Popen(["nice", "-n", "10", DAEMON] + args,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, start_new_session=True)
    except Exception as e:
        _log("start failed: {}".format(e))
        return False
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1)
        if is_running():
            _log("started " + " ".join(args))
            return True
    return False


def stop(wait=20):
    """Graceful close (the daemon saves its state and settings)."""
    _remember_args()
    try:
        rpc("session-close", timeout=5)
    except Exception:
        pass
    end = time.time() + wait
    while time.time() < end and _running_args() is not None:
        time.sleep(1)
    if _running_args() is not None:
        subprocess.call(["killall", "transmission-daemon"])
    # The daemon is down: settings.json can be made safe for its next start
    # (also covers a daemon that was started at boot by something else).
    apply_safe_settings()


# ─── Torrents ───────────────────────────────────────────────────────────

def _torrents():
    r = rpc("torrent-get", {"fields": ["id", "name", "status", "leftUntilDone", "error"]})
    return (r.get("arguments") or {}).get("torrents") or []


def add(magnet, download_dir=None):
    """-> (ok, message)"""
    if not ensure_started():
        return False, "Transmission is not installed or did not start"
    args = {"filename": magnet}
    if download_dir:
        args["download-dir"] = download_dir
    try:
        r = rpc("torrent-add", args)
    except Exception as e:
        return False, "Transmission error: {}".format(e)
    if r.get("result") != "success":
        return False, "Transmission: {}".format(r.get("result"))
    a = r.get("arguments") or {}
    t = a.get("torrent-added") or a.get("torrent-duplicate") or {}
    return True, ("Already in Transmission: " if "torrent-duplicate" in a else "Downloading: ") + (t.get("name") or "")


def _active(t):
    # status: 0 stopped, 1/2 check, 3/4 download (queued/active), 5/6 seed
    return t.get("status") in (1, 2, 3, 4, 5, 6)


def pause_all():
    """Player started: stop every active torrent, remember which."""
    with _lock:
        if _resume_timer[0]:
            _resume_timer[0].cancel()
            _resume_timer[0] = None
    if not is_running():
        return
    try:
        ids = [t["id"] for t in _torrents() if _active(t)]
        try:
            with open(_PAUSED) as f:
                ids = sorted(set(ids) | set(json.load(f)))
        except Exception:
            pass
        if ids:
            rpc("torrent-stop", {"ids": ids})
        with open(_PAUSED, "w") as f:
            json.dump(ids, f)
        _log("paused {} torrent(s) for playback".format(len(ids)))
    except Exception as e:
        _log("pause: {}".format(e))


def resume_paused():
    try:
        with open(_PAUSED) as f:
            ids = json.load(f)
        os.remove(_PAUSED)
    except Exception:
        ids = []
    if ids and is_running():
        try:
            rpc("torrent-start", {"ids": ids})
            _log("resumed {} torrent(s)".format(len(ids)))
        except Exception as e:
            _log("resume: {}".format(e))
    stop_if_idle()


def resume_later(delay=10):
    """Player closed: resume after `delay` s unless another player starts."""
    with _lock:
        if _resume_timer[0]:
            _resume_timer[0].cancel()
        t = threading.Timer(delay, resume_paused)
        t.daemon = True
        _resume_timer[0] = t
        t.start()


def stop_if_idle():
    """Close the daemon when no torrent still needs downloading."""
    if not is_running():
        return False
    try:
        pending = [t for t in _torrents() if t.get("leftUntilDone", 0) > 0 and t.get("status") != 0]
    except Exception:
        return False
    if os.path.exists(_PAUSED) or pending:
        return False
    _log("idle -> stopping transmission-daemon")
    stop()
    return True


if __name__ == "__main__":
    import sys
    if "--stop-if-idle" in sys.argv:
        stop_if_idle()
    elif "--status" in sys.argv:
        print("running" if is_running() else "stopped")
        if is_running():
            for t in _torrents():
                print(t.get("id"), t.get("status"), t.get("leftUntilDone"), t.get("name"))
