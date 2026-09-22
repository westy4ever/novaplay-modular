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
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from enigma import eTimer

from plugin_common import PLUGIN_PATH
from plugin_gridlist import scale_skin_xml
import plugin_downloads


_STATUS_LABELS = {
    "queued":      "⏳ في الانتظار",
    "downloading": "⬇ جاري التحميل",
    "done":        "✅ تم",
    "error":       "❌ فشل",
    "cancelled":   "⛔ أُلغي",
}


class AdvancedArabicPlayerDownloads(Screen):
    skin = scale_skin_xml("""
    <screen name="AdvancedArabicPlayerDownloads" position="center,center" size="1700,940" title="Downloads — التنزيلات" flags="wfNoBorder">
        <eLabel position="0,0" size="1700,940" backgroundColor="#0D1117" zPosition="0" />
        <eLabel position="0,0" size="1700,90" backgroundColor="#161B22" zPosition="1" />
        <widget name="header" position="50,20" size="1600,50" font="Regular;38" foregroundColor="#00E5FF" transparent="1" zPosition="2" />
        <widget name="list" position="40,110" size="1620,720" scrollbarMode="showOnDemand" foregroundColor="#F0F6FC" foregroundColorSelected="#00E5FF" backgroundColor="#161B22" backgroundColorSelected="#21262D" font="Regular;30" itemHeight="56" zPosition="2" />
        <widget name="footer" position="50,840" size="1600,40" font="Regular;26" foregroundColor="#8B949E" transparent="1" zPosition="2" />
        <widget name="key_red" position="60,895" size="380,36" font="Regular;24" foregroundColor="#FF6B6B" transparent="1" halign="center" zPosition="2" />
        <widget name="key_blue" position="460,895" size="380,36" font="Regular;24" foregroundColor="#58A6FF" transparent="1" halign="center" zPosition="2" />
        <widget name="key_exit" position="860,895" size="780,36" font="Regular;24" foregroundColor="#8B949E" transparent="1" halign="right" zPosition="2" />
    </screen>""")

    def __init__(self, session):
        Screen.__init__(self, session)
        self.skinName = ["AdvancedArabicPlayerDownloads"]
        self["header"] = Label("التنزيلات")
        self["footer"] = Label("")
        self["list"] = MenuList([])
        self["key_red"] = Label("إلغاء التنزيل")
        self["key_blue"] = Label("مسح المكتمل")
        self["key_exit"] = Label("EXIT إغلاق")
        self._tasks = []
        self._row_map = []
        self._last_rows = None                     # [FIX-DL]
        self._flash_until = 0

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "ok":     self._onOk,
            "cancel": self.close,
            "red":    self._onCancelActive,
            "blue":   self._onClearFinished,
        }, -1)

        self._refresh_timer = eTimer()
        self._refresh_timer.callback.append(self._refresh)
        self._refresh_timer.start(1000, False)
        self.onClose.append(self._stopTimer)        # [FIX-DL] timer used to outlive the screen
        self._refresh()

    # ── rendering ─────────────────────────────────────────────────────
    def _refresh(self):
        try:
            self._tasks = plugin_downloads.get_all_tasks()
        except Exception:
            self._tasks = []
        rows = []
        self._row_map = []
        for t in self._tasks:
            label = self._format_task(t)
            rows.append(label)
            self._row_map.append(t)
        if not rows:
            rows = ["لا توجد تنزيلات في هذه الجلسة"]
        self._set_rows(rows)
        active = sum(1 for t in self._tasks if t.status in ("queued", "downloading"))
        done = sum(1 for t in self._tasks if t.status == "done")
        failed = sum(1 for t in self._tasks if t.status == "error")
        self["header"].setText("التنزيلات: {} نشط  |  {} مكتمل  |  {} فشل".format(active, done, failed))
        if time.time() >= self._flash_until:
            self["footer"].setText("المجلد: {}".format(plugin_downloads.download_dir()))

    def _format_task(self, t):
        status = _STATUS_LABELS.get(t.status, t.status)
        title = (t.title or "بدون عنوان")[:60]
        if t.status == "downloading":
            pct = t.progress_pct()
            if pct > 0:
                size = "{:.1f}/{:.1f} MB".format(t.bytes_done / 1048576.0, t.bytes_total / 1048576.0)
                return "{}  {}  {:.0f}%  ({})".format(status, title, pct, size)
            return "{}  {}  {:.1f} MB".format(status, title, t.bytes_done / 1048576.0)
        if t.status == "done" and t.dest_path:
            try:
                sz = os.path.getsize(t.dest_path) / 1048576.0
                return "{}  {}  ({:.1f} MB)".format(status, title, sz)
            except Exception:
                return "{}  {}".format(status, title)
        if t.status == "error" and t.error:
            return "{}  {}  — {}".format(status, title, t.error[:60])
        return "{}  {}".format(status, title)

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
        try:
            return int(self["list"].getSelectedIndex())
        except Exception:
            return -1

    def _set_rows(self, rows):
        """setList() sends the cursor back to the top on Enigma2 and this screen refreshes
        every second - remember the selection and restore it; skip when nothing changed."""
        if rows == self._last_rows:
            return
        idx = self._current_index()
        try:
            self["list"].setList(rows)
        except Exception:
            return
        self._last_rows = list(rows)
        if idx > 0:
            try:
                self["list"].moveToIndex(min(idx, len(rows) - 1))
            except Exception:
                pass

    def _selected_task(self):
        try:
            idx = self["list"].getSelectedIndex()
        except Exception:
            idx = -1
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
                MessageBox, "إلغاء التحميل:\n{}".format(self._title(t)[:60]),
                MessageBox.TYPE_YESNO, timeout=8, default=False)
            return
        if t.status == "done" and t.dest_path and os.path.exists(t.dest_path):
            parent = os.path.dirname(t.dest_path)
            entries = [
                ("▶ شغّل الملف", "play"),
                ("📂 افتح المجلد", "open"),
                ("🗑 احذف الملف", "delete"),
            ]
            self.session.openWithCallback(
                lambda c: self._onFileAction(c, t),
                ChoiceBox, title=self._title(t)[:60], list=entries)
            return

        if t.status == "error" and t.error:                                  # [FIX-DL]
            self.session.open(MessageBox, t.error[:300], MessageBox.TYPE_ERROR, timeout=10)
        elif t.status == "done":
            self._say(u"الملف غير موجود")

    def _confirm_cancel(self, ans, t):
        if ans:
            try:
                t.cancel()
                self._say("تم إرسال طلب الإلغاء: {}".format(self._title(t)[:40]))
            except Exception:
                pass

    def _onCancelActive(self):
        t = self._selected_task()
        if not t or t.status not in ("queued", "downloading"):
            self._say("لا يوجد تحميل نشط مختار")
            return
        self._confirm_cancel(True, t)

    def _onClearFinished(self):
        try:
            plugin_downloads.clear_finished_tasks()
            self._refresh()
            self._say("تم مسح المدخلات المنتهية")
        except Exception as e:
            self._say("فشل المسح: {}".format(str(e)[:40]))

    def _onFileAction(self, choice, t):
        if not choice:
            return
        action = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        if action == "play":
            self.close(("play", t.dest_path))
        elif action == "open":
            self.close(("open", os.path.dirname(t.dest_path)))
        elif action == "delete":
            self.session.openWithCallback(
                lambda ans: self._delete_file(ans, t),
                MessageBox, "حذف الملف؟\n{}".format(os.path.basename(t.dest_path)),
                MessageBox.TYPE_YESNO, timeout=8, default=False)

    def _delete_file(self, ans, t):
        if not ans:
            return
        try:
            if os.path.exists(t.dest_path):
                os.remove(t.dest_path)
            self._say("تم الحذف: {}".format(os.path.basename(t.dest_path)))
            self._refresh()
        except Exception as e:
            self._say("فشل الحذف: {}".format(str(e)[:40]))