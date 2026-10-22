# -*- coding: utf-8 -*-
"""NovaPlay — Downloads Manager screen [A2].

Lists every DownloadTask created this Enigma2 session (active, done,
failed, cancelled) with live progress. Auto-refreshes every 1s.

Actions:
  OK    = cancel an active download / open file location menu for a
          finished one
  RED   = cancel the selected active task
  BLUE  = clear finished/failed entries from the list
  EXIT  = close
"""

import os
import time

from Screens.Screen import Screen
from novaplay_choice import NovaMessageBox as MessageBox
from novaplay_choice import NovaChoiceBox as ChoiceBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText
from enigma import (eTimer, eListbox, eListboxPythonMultiContent, gFont, ePoint, eSize,
                    RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_HALIGN_RIGHT, RT_VALIGN_CENTER)
try:
    from skin import parseColor
except Exception:
    parseColor = None

from plugin_common import PLUGIN_PATH
from plugin_gridlist import scale_skin_xml, sc
from plugin_rowzoom import RowZoom
import plugin_downloads


_STATUS_LABELS = {
    "queued":      "…  Queued",
    "downloading": "⬇  Downloading",
    "done":        "✓  Done",
    "error":       "✕  Failed",
    "cancelled":   "■  Cancelled",
}


# [panel 2026-10-21] one card-like row per download: status chip, title,
# size / percent, a detail line and a progress bar for active downloads
_CHIP = {
    "queued": ("Queued", 0x2E3A4D, 0xD6DCE5),
    "downloading": ("Downloading", 0x5C4A12, 0xF2C230),
    "done": ("Done", 0x1E6B45, 0xFFFFFF),
    "error": ("Failed", 0x7A1E24, 0xFFFFFF),
    "cancelled": ("Cancelled", 0x3A4252, 0xC9D0DA),
    "saved": ("Saved", 0x1E4E6B, 0xFFFFFF),
}
_ROW_H = 92
_LIST_W = 1804


# [cards] one card per download, the selected one zooms (like the other screens)
DL_ROWS = 6
_DX, _DY0, _DSTEP, _DW, _DH = 58, 170, 120, 1804, 112


def _dl_rows_xml():
    out = []
    for i in range(DL_ROWS):
        y = _DY0 + i * _DSTEP
        out.append(
            '<widget name="dEdge{i}" position="{ex},{ey}" size="{ew},{eh}" backgroundColor="#F2C230" cornerRadius="16" zPosition="2" />'
            '<widget name="dBg{i}" position="{x},{y}" size="{w},{h}" backgroundColor="#161C26" cornerRadius="14" zPosition="3" />'
            '<widget name="dChip{i}" position="{cx},{cy}" size="160,34" font="Regular;19" halign="center" valign="center" foregroundColor="#FFFFFF" backgroundColor="#2E3A4D" cornerRadius="8" zPosition="4" />'
            '<widget name="dTitle{i}" position="{tx},{ty}" size="1290,40" font="Regular;27" valign="center" foregroundColor="#FFFFFF" backgroundColor="#161C26" transparent="1" zPosition="4" />'
            '<widget name="dRight{i}" position="{rx},{ty}" size="250,40" font="Regular;21" halign="right" valign="center" foregroundColor="#C9D0DA" backgroundColor="#161C26" transparent="1" zPosition="4" />'
            '<widget name="dDetail{i}" position="{tx},{dy}" size="1580,28" font="Regular;19" valign="center" foregroundColor="#A3ADBB" backgroundColor="#161C26" transparent="1" zPosition="4" />'
            '<widget name="dTrack{i}" position="{tx},{by}" size="1580,6" backgroundColor="#2A3240" cornerRadius="3" zPosition="4" />'
            '<widget name="dBar{i}" position="{tx},{by}" size="6,6" backgroundColor="#F2C230" cornerRadius="3" zPosition="5" />'
            .format(i=i, x=_DX, y=y, w=_DW, h=_DH, ex=_DX - 3, ey=y - 3, ew=_DW + 6, eh=_DH + 6,
                    cx=_DX + 16, cy=y + 16, tx=_DX + 196, ty=y + 10, rx=_DX + _DW - 266, dy=y + 52, by=y + 90))
    return "\n        ".join(out)


_VIDEO_EXT = (".mp4", ".mkv", ".ts", ".avi", ".m4v", ".webm", ".mov", ".mpg")


class _SavedFile(object):
    """A video already in the download folder (from an earlier session)."""
    status = "saved"

    def __init__(self, path):
        self.dest_path = path
        self.title = os.path.splitext(os.path.basename(path))[0]
        self.error = ""


def _saved_files(skip):
    d = plugin_downloads.download_dir()
    out = []
    try:
        for name in os.listdir(d):
            path = os.path.join(d, name)
            if (name.lower().endswith(_VIDEO_EXT) and os.path.isfile(path)
                    and path not in skip and not name.endswith(".part")):
                out.append(path)
    except Exception:
        return []
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return [_SavedFile(p) for p in out]


class DownloadList(GUIComponent):
    GUI_WIDGET = eListbox

    def __init__(self):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("Regular", 27))
        self.l.setFont(1, gFont("Regular", 21))
        self.l.setFont(2, gFont("Regular", 19))
        self.l.setItemHeight(_ROW_H)

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setItemHeight(_ROW_H)

    def preWidgetDelete(self, instance):
        instance.setContent(None)

    def getSelectedIndex(self):
        return self.l.getCurrentSelectionIndex()

    def moveToIndex(self, i):
        if self.instance:
            self.instance.moveSelectionTo(i)

    def setList(self, rows):
        W = _LIST_W - 20
        out = []
        for r in rows:
            if isinstance(r, str):
                out.append([r, MultiContentEntryText(pos=(20, 0), size=(W - 40, _ROW_H), font=0,
                                                     flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=r, color=0xA3ADBB)])
                continue
            status, title, right, detail, pct = r
            label, bg, fg = _CHIP.get(status, (status, 0x2E3A4D, 0xD6DCE5))
            e = [r,
                 MultiContentEntryText(pos=(14, 16), size=(160, 34), font=2, flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER,
                                       text=label, color=fg, color_sel=fg, backcolor=bg, backcolor_sel=bg),
                 MultiContentEntryText(pos=(196, 10), size=(W - 470, 40), font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                       text=title, color=0xFFFFFF, color_sel=0xF2C230),
                 MultiContentEntryText(pos=(W - 260, 10), size=(250, 40), font=1, flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER,
                                       text=right, color=0xF2C230 if status == "downloading" else 0xC9D0DA),
                 MultiContentEntryText(pos=(196, 50), size=(W - 220, 28), font=2, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                       text=detail, color=0xFF8A8A if status == "error" else 0xA3ADBB)]
            if status == "downloading":
                bw = W - 220
                e.append(MultiContentEntryText(pos=(196, 80), size=(bw, 6), font=2, text="", backcolor=0x2A3240, backcolor_sel=0x2A3240))
                if pct > 0:
                    e.append(MultiContentEntryText(pos=(196, 80), size=(max(6, int(bw * min(100, pct) / 100.0)), 6), font=2,
                                                   text="", backcolor=0xF2C230, backcolor_sel=0xF2C230))
            out.append(e)
        self.l.setList(out)


class NovaPlayDownloads(Screen):
    # [panel 2026-10-21] full-screen, same style as the other screens
    skin = scale_skin_xml("""
    <screen name="NovaPlayDownloads" position="center,center" size="1920,1080" title="Downloads" flags="wfNoBorder" backgroundColor="#0C1017">
        <eLabel position="0,0" size="1920,1080" backgroundColor="#0C1017" zPosition="0" />
        <ePixmap position="40,30" size="84,84" pixmap="{p}/images/plugin.png" alphatest="blend" scale="1" zPosition="2" />
        <eLabel position="146,30" size="800,52" text="Downloads" font="Bold;40" foregroundColor="#F2C230" backgroundColor="#0C1017" transparent="1" zPosition="2" />
        <widget name="header" position="146,86" size="1700,32" font="Regular;22" foregroundColor="#A3ADBB" transparent="1" zPosition="2" />
        <eLabel position="40,150" size="1840,810" backgroundColor="#121822" cornerRadius="16" zPosition="1" />
        <widget name="dEmpty" position="58,170" size="1804,112" font="Regular;27" valign="center" foregroundColor="#A3ADBB" backgroundColor="#161C26" cornerRadius="14" zPosition="2" />
        {rows}
        <eLabel position="58,888" size="1804,1" backgroundColor="#2A3240" zPosition="2" />
        <widget name="footer" position="74,900" size="1770,44" font="Regular;22" valign="center" foregroundColor="#A3ADBB" transparent="1" zPosition="2" />
        <eLabel position="40,976" size="1840,76" backgroundColor="#121822" cornerRadius="16" zPosition="1" />
        <eLabel position="72,1003" size="22,22" backgroundColor="#E5484D" cornerRadius="11" zPosition="2" />
        <widget name="key_red" position="106,994" size="300,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="2" />
        <eLabel position="412,1003" size="22,22" backgroundColor="#3E7BFA" cornerRadius="11" zPosition="2" />
        <widget name="key_blue" position="446,994" size="300,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="2" />
        <widget name="key_exit" position="900,994" size="950,40" font="Regular;24" valign="center" foregroundColor="#C9D0DA" transparent="1" halign="right" zPosition="2" />
    </screen>""".format(p=PLUGIN_PATH, rows=_dl_rows_xml()))

    def __init__(self, session):
        Screen.__init__(self, session)
        self.skinName = ["NovaPlayDownloads"]
        self["header"] = Label("Downloads")
        self["footer"] = Label("")
        self["dEmpty"] = Label("   No downloads yet — files saved by NovaPlay appear here")
        for _i in range(DL_ROWS):
            for _n in ("dEdge", "dBg", "dChip", "dTitle", "dRight", "dDetail", "dTrack", "dBar"):
                self["%s%d" % (_n, _i)] = Label("")
        self._sel = 0
        self._top = 0
        self._rows = []
        self._fonts = {}
        self._zoom = RowZoom(self, self._placeRow)
        self["key_red"] = Label("Cancel / Delete")
        self["key_blue"] = Label("Clear finished")
        self["key_exit"] = Label(u"\u25b2 \u25bc  Select    OK  Options    EXIT  Close")
        self._tasks = []
        self._row_map = []
        self._last_rows = None                     # [FIX-DL]
        self._flash_until = 0

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "up":     lambda: self._step(-1),
            "down":   lambda: self._step(1),
            "left":   lambda: self._step(-DL_ROWS),
            "right":  lambda: self._step(DL_ROWS),
            "ok":     self._onOk,
            "cancel": self.close,
            "red":    self._onCancelActive,
            "blue":   self._onClearFinished,
        }, -1)

        self._refresh_timer = eTimer()
        self._refresh_timer.callback.append(self._refresh)
        self._refresh_timer.start(1000, False)
        self.onClose.append(self._stopTimer)        # [FIX-DL] timer used to outlive the screen
        self.onLayoutFinish.append(self._refresh)

    # ── rendering ─────────────────────────────────────────────────────
    def _refresh(self):
        try:
            self._tasks = plugin_downloads.get_all_tasks()
        except Exception:
            self._tasks = []
        skip = set(getattr(t, "dest_path", "") for t in self._tasks)
        self._tasks = list(self._tasks) + _saved_files(skip)
        rows = []
        self._row_map = []
        for t in self._tasks:
            label = self._format_task(t)
            rows.append(label)
            self._row_map.append(t)
        if not rows:
            rows = ["No downloads yet — files saved by NovaPlay appear here"]
        self._set_rows(rows)
        active = sum(1 for t in self._tasks if t.status in ("queued", "downloading"))
        done = sum(1 for t in self._tasks if t.status in ("done", "saved"))
        failed = sum(1 for t in self._tasks if t.status == "error")
        self["header"].setText("{} active  ·  {} saved  ·  {} failed".format(active, done, failed))
        if time.time() >= self._flash_until:
            self["footer"].setText("Folder: {}".format(plugin_downloads.download_dir()))

    def _format_task(self, t):
        """-> (status, title, right, detail, percent) for DownloadList."""
        title = (t.title or "Untitled")[:70]
        mb = lambda v: v / 1048576.0
        if t.status == "downloading":
            pct = t.progress_pct()
            if pct > 0:
                return (t.status, title, "{:.0f}%".format(pct),
                        "{:.1f} / {:.1f} MB".format(mb(t.bytes_done), mb(t.bytes_total)), pct)
            return (t.status, title, "{:.1f} MB".format(mb(t.bytes_done)), "Starting…", 0)
        if t.status in ("done", "saved") and t.dest_path:
            try:
                size = "{:.1f} MB".format(os.path.getsize(t.dest_path) / 1048576.0)
            except Exception:
                size = ""
            if t.status == "saved":
                try:
                    when = time.strftime("%d %b %Y  %H:%M", time.localtime(os.path.getmtime(t.dest_path)))
                except Exception:
                    when = ""
                return (t.status, title, size, when, 0)
            return (t.status, title, size, os.path.basename(t.dest_path), 0)
        if t.status == "error":
            return (t.status, title, "", (t.error or "")[:120], 0)
        return (t.status, title, "", "", 0)

    # ── actions ───────────────────────────────────────────────────────
    # [FIX-DL] helpers
    def _stopTimer(self):
        try:
            self._refresh_timer.stop()
        except Exception:
            pass
        try:
            self._refresh_timer.callback.remove(self._refresh)
        except Exception:
            pass

    def _say(self, msg, secs=4):
        """Footer message that survives the 1s refresh for a few seconds."""
        self._flash_until = time.time() + secs
        try:
            self["footer"].setText(msg)
        except Exception:
            pass

    def _title(self, t):
        return (getattr(t, "title", "") or "video")

    def _current_index(self):
        return self._sel

    def _step(self, d):
        n = len(self._row_map)
        if not n:
            return
        if abs(d) == 1:
            self._sel = (self._sel + d) % n
        else:
            self._sel = max(0, min(n - 1, self._sel + d))
        self._last_rows = None
        self._set_rows(self._rows)

    def _color(self, name, bg=None, fg=None):
        inst = self[name].instance
        if inst is None or parseColor is None:
            return
        try:
            if bg:
                inst.setBackgroundColor(parseColor(bg))
            if fg:
                inst.setForegroundColor(parseColor(fg))
            inst.invalidate()
        except Exception:
            pass

    def _placeRow(self, i, p):
        """[cards] the selected download card grows a little (smooth)"""
        y = _DY0 + i * _DSTEP
        dw, dh = 20.0 * p, float(_DSTEP - _DH) * p
        x0, y0, w, h = _DX - dw / 2.0, y - dh / 2.0, _DW + dw, _DH + dh
        dy = dh / 2.0
        for name, x, yy, ww, hh in (("dEdge", x0 - 3, y0 - 3, w + 6, h + 6), ("dBg", x0, y0, w, h),
                                    ("dChip", x0 + 16, y0 + 16 + dy / 2, None, None),
                                    ("dTitle", x0 + 196, y0 + 10 + dy / 2, None, None),
                                    ("dRight", x0 + w - 266, y0 + 10 + dy / 2, None, None),
                                    ("dDetail", x0 + 196, y0 + 52 + dy, None, None),
                                    ("dTrack", x0 + 196, y0 + 90 + dy, None, None),
                                    ("dBar", x0 + 196, y0 + 90 + dy, None, None)):
            inst = self["%s%d" % (name, i)].instance
            if inst is None:
                continue
            try:
                inst.move(ePoint(sc(x), sc(yy)))
                if ww is not None:
                    inst.resize(eSize(max(1, sc(ww)), max(1, sc(hh))))
            except Exception:
                pass
        size = sc(27 + 2 * p)
        inst = self["dTitle%d" % i].instance
        if inst is not None and self._fonts.get(i) != size:
            try:
                inst.setFont(gFont("Regular", size))
                self._fonts[i] = size
            except Exception:
                pass

    def _set_rows(self, rows):
        """paint the visible cards (the 1 s refresh only repaints when something changed)"""
        self._rows = rows
        if rows == self._last_rows:
            return
        self._last_rows = list(rows)
        data = [r for r in rows if not isinstance(r, str)]
        n = len(data)
        self._sel = max(0, min(self._sel, n - 1))
        if self._sel < self._top:
            self._top = self._sel
        elif self._sel >= self._top + DL_ROWS:
            self._top = self._sel - DL_ROWS + 1
        self._top = max(0, min(self._top, max(0, n - DL_ROWS)))
        self["dEmpty"].show() if not n else self["dEmpty"].hide()
        for i in range(DL_ROWS):
            g = self._top + i
            names = ["%s%d" % (k, i) for k in ("dEdge", "dBg", "dChip", "dTitle", "dRight", "dDetail", "dTrack", "dBar")]
            if g >= n:
                for k in names:
                    self[k].hide()
                continue
            status, title, right, detail, pct = data[g]
            label, bg, fg = _CHIP.get(status, (status, 0x2E3A4D, 0xD6DCE5))
            sel = g == self._sel
            for k in names[1:6]:
                self[k].show()
            self["dEdge%d" % i].show() if sel else self["dEdge%d" % i].hide()
            self["dChip%d" % i].setText(label)
            self._color("dChip%d" % i, bg="#%06X" % bg, fg="#%06X" % fg)
            self["dTitle%d" % i].setText(title)
            self._color("dTitle%d" % i, fg="#F2C230" if sel else "#FFFFFF")
            self["dRight%d" % i].setText(right)
            self._color("dRight%d" % i, fg="#F2C230" if status == "downloading" else "#C9D0DA")
            self["dDetail%d" % i].setText(detail)
            self._color("dDetail%d" % i, fg="#FF8A8A" if status == "error" else "#A3ADBB")
            if status == "downloading":
                self["dTrack%d" % i].show()
                inst = self["dBar%d" % i].instance
                if inst is not None and pct > 0:
                    try:
                        inst.resize(eSize(max(6, sc(1580 * min(100, pct) / 100.0)), max(1, sc(6))))
                    except Exception:
                        pass
                    self["dBar%d" % i].show()
                else:
                    self["dBar%d" % i].hide()
            else:
                self["dTrack%d" % i].hide()
                self["dBar%d" % i].hide()
        self._zoom.focus(self._sel - self._top if n else -1, (self._top, n))

    def _selected_task(self):
        idx = self._sel
        if 0 <= idx < len(self._row_map):
            return self._row_map[idx]
        return None

    def _onOk(self):
        t = self._selected_task()
        if not t:
            return
        if t.status in ("queued", "downloading"):
            self.session.openWithCallback(
                lambda ans: self._confirm_cancel(ans, t),
                MessageBox, "Cancel download:\n{}".format(self._title(t)[:60]),
                MessageBox.TYPE_YESNO, timeout=8, default=False)
            return
        if t.status in ("done", "saved") and t.dest_path and os.path.exists(t.dest_path):
            entries = [
                (u"▶  Play", "play"),
                (u"✕  Delete file", "delete"),
            ]
            self.session.openWithCallback(
                lambda c: self._onFileAction(c, t),
                ChoiceBox, title=self._title(t)[:60], list=entries)
            return

        if t.status == "error" and t.error:                                  # [FIX-DL]
            self.session.open(MessageBox, t.error[:300], MessageBox.TYPE_ERROR, timeout=10)
        elif t.status == "done":
            self._say(u"File not found")

    def _confirm_cancel(self, ans, t):
        if ans:
            try:
                t.cancel()
                self._say("Cancel requested: {}".format(self._title(t)[:40]))
            except Exception:
                pass

    def _play(self, t):
        try:
            from plugin_screen_player import NovaPlaySimplePlayer, _capture_previous_service
            self.session.open(NovaPlaySimplePlayer, t.title or os.path.basename(t.dest_path),
                              [(4097, t.dest_path, u"local file", False)],
                              previous_service=_capture_previous_service(self.session),
                              resume_pos=0, item_url="",
                              next_episode=None, on_next=None, poster_url="",
                              quality_variants=None, server_url="", is_preview=True)
        except Exception as e:
            self._say("Could not play: {}".format(str(e)[:40]))

    def _onCancelActive(self):
        """RED: cancel the selected download, or delete the selected file."""
        t = self._selected_task()
        if t and t.status in ("done", "saved") and t.dest_path and os.path.exists(t.dest_path):
            self._onFileAction((u"", "delete"), t)
            return
        if not t or t.status not in ("queued", "downloading"):
            self._say("Select a download or a file")
            return
        self.session.openWithCallback(
            lambda ans: self._confirm_cancel(ans, t),
            MessageBox, "Cancel download:\n{}".format(self._title(t)[:60]),
            MessageBox.TYPE_YESNO, timeout=8, default=False)

    def _onClearFinished(self):
        try:
            plugin_downloads.clear_finished_tasks()
            self._refresh()
            self._say("Finished entries cleared")
        except Exception as e:
            self._say("Clear failed: {}".format(str(e)[:40]))

    def _onFileAction(self, choice, t):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "play":
            self._play(t)
        elif action == "delete":
            self.session.openWithCallback(
                lambda ans: self._delete_file(ans, t),
                MessageBox, "Delete file?\n{}".format(os.path.basename(t.dest_path)),
                MessageBox.TYPE_YESNO, timeout=8, default=False)

    def _delete_file(self, ans, t):
        if not ans:
            return
        try:
            if os.path.exists(t.dest_path):
                os.remove(t.dest_path)
            self._say("Deleted: {}".format(os.path.basename(t.dest_path)))
            self._refresh()
        except Exception as e:
            self._say("Delete failed: {}".format(str(e)[:40]))