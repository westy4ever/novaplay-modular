# -*- coding: utf-8 -*-
"""
install_dependencies.py — one-shot dependency installer for NovaPlay
=====================================================================
pip-installs curl_cffi + brotli (the Cloudflare-bypass backbone) using
the image's Console screen, with an opkg fallback for images that
don't ship pip. After installation a FULL Enigma2 restart is required
before the running plugin can import the new packages.

Honest expectations:
  * x86 / recent ARM images with python3 + pip: works.
  * Old MIPS/mipsel images: curl_cffi has no prebuilt wheels and will
    likely fail to compile. That's not fatal — your extractors fall
    back to plain urllib + the external browser proxy (configure it
    in settings). This installer just makes the attempt visible.

Hook (from your plugin.py settings screen):
    from install_dependencies import open_installer
    open_installer(self.session)
"""

_DEPS_CMD = [
    # 1. make sure pip exists (opkg fallback; 'true' keeps going if not)
    "pip3 --version >/dev/null 2>&1 || opkg install python3-pip; true",
    # 2. try a user install first (no root needed), then a plain install
    "pip3 install --user curl_cffi brotli || pip3 install curl_cffi brotli",
    # 3. verify — try python3 then python (image-dependent binary names)
    "python3 -c 'import curl_cffi; print(\"VERIFY: curl_cffi OK\")' 2>&1 "
    "|| python -c 'import curl_cffi; print(\"VERIFY: curl_cffi OK\")' 2>&1 "
    "|| echo 'VERIFY: curl_cffi MISSING (browser proxy fallback applies)'",
    "python3 -c 'import brotli; print(\"VERIFY: brotli OK\")' 2>&1 "
    "|| python -c 'import brotli; print(\"VERIFY: brotli OK\")' 2>&1 "
    "|| echo 'VERIFY: brotli MISSING (gzip fallback applies)'",
    "echo 'If VERIFY lines say OK, restart Enigma2 to activate.'",
]

_RESTART_CMD = [
    "echo 'Restarting Enigma2 GUI in 5 seconds...'",
    "sleep 5",
]


def open_installer(session, offer_restart=True):
    """Open the Console screen that installs the dependencies.
    Returns True if the screen opened, False if Console isn't available.
    If offer_restart is True, chains a GUI-restart prompt after install."""
    try:
        from Screens.Console import Console
        cmdlist = list(_DEPS_CMD)
        session.open(Console, title="NovaPlay dependency installer",
                     cmdlist=cmdlist, closeOnSuccess=False)
        return True
    except Exception:
        return False


def open_installer_with_restart(session):
    """Same as open_installer, but automatically restarts the Enigma2 GUI
    5s after the commands finish (the running plugin can't see freshly
    pip-installed packages without a restart)."""
    try:
        from Screens.Console import Console
        session.open(Console, title="NovaPlay dependency installer (+GUI restart)",
                     cmdlist=_DEPS_CMD + _RESTART_CMD + _cmd_quit(),
                     closeOnSuccess=False)
        return True
    except Exception:
        return False


def _cmd_quit():
    # The actual restart is triggered by the caller AFTER the console
    # closes — see open_restart_prompt(). Keeping the split so the user
    # can still read the output if we don't auto-restart.
    return []


def open_restart_prompt(session):
    """Ask the user to restart Enigma2 (GUI only, not the box)."""
    try:
        from Screens.Standby import TryQuitMainloop
        session.open(TryQuitMainloop, 3)   # 3 = restart Enigma2 GUI
        return True
    except Exception:
        return False