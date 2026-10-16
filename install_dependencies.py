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

# [UPDATE] Everything NovaPlay uses that isn't part of Enigma2 itself. Each item is
# optional -- the plugin degrades gracefully without it -- but each one switches a
# real feature on:
#   module / program   what it enables                                   how
#   curl_cffi          Cloudflare-protected sites (browser fingerprint)  pip (no opkg pkg)
#   brotli             'br'-compressed pages                              opkg, else pip
#   PIL (Pillow)       poster scaling, ribbons, watched (grey) posters    opkg
#   cryptography       fast AES for player decryption (pure-Python
#                      fallback works, just slower)                       opkg
#   requests           Shahid4u's own session path                        opkg
#   yt_dlp + ffmpeg    trailers (fetch + mux into one file)               opkg
#   ServiceApp         player modes 5001/5002 (more streams play)         opkg
#   exteplayer3        the exteplayer3 player mode                        opkg
# Package names differ between images, so every item tries its known names in
# turn, skips anything already installed, and never stops the run. TorrServer and
# Transmission are separate services you set up yourself -- not installed here.


def _py_item(label, module, opkg_names, pip_name=None):
    """Shell line: skip if `module` imports; else opkg (each name), else pip."""
    tries = " || ".join("opkg install {} >/dev/null 2>&1".format(n) for n in opkg_names)
    if pip_name:
        tries = (tries + " || " if tries else "") + \
            "pip3 install {0} >/dev/null 2>&1 || pip3 install --user {0} >/dev/null 2>&1".format(pip_name)
    check = "python3 -c 'import {}' >/dev/null 2>&1".format(module)
    return ("if {c}; then echo 'OK    {l} (already installed)'; "
            "elif {t}; then {c} && echo 'OK    {l} (installed)' || echo 'FAIL  {l} (installed but cannot import)'; "
            "else echo 'SKIP  {l} (not available for this image)'; fi; true").format(c=check, t=tries, l=label)


def _bin_item(label, check, opkg_names):
    """Shell line for a program/plugin: skip if `check` passes, else opkg."""
    tries = " || ".join("opkg install {} >/dev/null 2>&1".format(n) for n in opkg_names)
    return ("if {c}; then echo 'OK    {l} (already installed)'; "
            "elif {t}; then echo 'OK    {l} (installed)'; "
            "else echo 'SKIP  {l} (not available for this image)'; fi; true").format(c=check, t=tries, l=label)


_DEPS_CMD = [
    "echo 'NovaPlay dependency installer -- updating package lists...'",
    "opkg update >/dev/null 2>&1 && echo 'opkg update: OK' || echo 'opkg update: FAILED (no internet?) -- continuing'; true",
    # pip is needed only for curl_cffi (and brotli where opkg has none)
    "pip3 --version >/dev/null 2>&1 || opkg install python3-pip >/dev/null 2>&1; true",
    _py_item("curl_cffi    (Cloudflare sites)", "curl_cffi", [], pip_name="curl_cffi"),
    _py_item("brotli       (compressed pages)", "brotli", ["python3-brotli"], pip_name="brotli"),
    _py_item("Pillow       (posters, ribbons)", "PIL", ["python3-pillow", "python3-imaging"]),
    _py_item("cryptography (fast decryption)", "cryptography", ["python3-cryptography"]),
    _py_item("requests     (Shahid4u)", "requests", ["python3-requests"]),
    _py_item("yt-dlp       (trailers)", "yt_dlp", ["python3-yt-dlp", "yt-dlp"], pip_name="yt-dlp"),
    _bin_item("ffmpeg       (trailers)", "command -v ffmpeg >/dev/null 2>&1", ["ffmpeg"]),
    _bin_item("ServiceApp   (player 5001/5002)",
              "[ -d /usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp ]",
              ["enigma2-plugin-systemplugins-serviceapp"]),
    _bin_item("exteplayer3  (player)", "[ -x /usr/bin/exteplayer3 ]", ["exteplayer3"]),
    "echo ''",
    "echo 'OK = ready.  SKIP = this image has no package for it; NovaPlay still works without it.'",
    "echo 'Restart Enigma2 so NovaPlay can use what was installed.'",
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
    """[PATCH 92] Run the installer, then open the Enigma2 restart screen when
    the console is closed (a running plugin can't see freshly pip-installed
    packages without a restart)."""
    try:
        from Screens.Console import Console

        def _after(*args):
            open_restart_prompt(session)

        session.openWithCallback(
            _after, Console,
            title="NovaPlay dependency installer",
            cmdlist=list(_DEPS_CMD), closeOnSuccess=False)
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