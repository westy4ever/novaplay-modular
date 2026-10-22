# -*- coding: utf-8 -*-
"""NovaPlay — search screen (query, scope, suggestions, TMDB async).

[A1] Recent searches are now saved on every submit, and re-appear at
the top of the suggestions list via _library_search_suggestions().
"""

import threading

from Screens.Screen import Screen
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import ePoint, eSize, gFont
try:
    from skin import parseColor
except Exception:
    parseColor = None

from plugin_common import PLUGIN_PATH
from plugin_gridlist import scale_skin_xml, sc
from plugin_rowzoom import RowZoom

# [rows] suggestions as separate rows (zoom like the category rows)
SG_ROWS = 8
_SG_X, _SG_Y0, _SG_STEP, _SG_W, _SG_H = 58, 446, 60, 1804, 54


def _sg_rows_xml():
    out = []
    for i in range(SG_ROWS):
        y = _SG_Y0 + i * _SG_STEP
        out.append(
            '<widget name="sgBg{i}" position="{x},{y}" size="{w},{h}" backgroundColor="#222B3A" cornerRadius="12" zPosition="3" />'
            '<widget name="sgBar{i}" position="{x},{by}" size="5,{bh}" backgroundColor="#F2C230" cornerRadius="2" zPosition="5" />'
            '<widget name="sgText{i}" position="{tx},{y}" size="1200,{h}" font="Regular;30" valign="center" foregroundColor="#D6DCE5" backgroundColor="#121822" transparent="1" zPosition="5" />'
            '<widget name="sgMeta{i}" position="{mx},{y}" size="520,{h}" font="Regular;22" halign="right" valign="center" foregroundColor="#A3ADBB" backgroundColor="#121822" transparent="1" zPosition="5" />'
            .format(i=i, x=_SG_X, y=y, w=_SG_W, h=_SG_H, by=y + 10, bh=_SG_H - 20, tx=_SG_X + 28, mx=_SG_X + _SG_W - 540))
    return "\n        ".join(out)
from plugin_state import (_library_search_suggestions, _save_search_query,
                          _clear_search_history, _remove_search_query)
from novaplay_choice import NovaMessageBox as MessageBox
from plugin_tmdb import _tmdb_enabled, _tmdb_search_suggestions
from plugin_util import (_wrap_ui_text, _single_line_text,
                         _search_scope_label, _normalize_query)
from novaplay_thread import callInMainThread


class NovaPlaySearch(Screen):
    # [panel 2026-10-21] same style as Home / Settings / site screens
    skin = scale_skin_xml("""
    <screen name="NovaPlaySearch" position="center,center" size="1920,1080" flags="wfNoBorder" backgroundColor="#0C1017">
        <widget name="bg" position="0,0" size="1920,1080" backgroundColor="#0C1017" zPosition="1" />
        <ePixmap position="40,30" size="84,84" pixmap="{p}/images/plugin.png" alphatest="blend" scale="1" zPosition="3" />
        <widget name="title" position="146,30" size="1100,52" font="Bold;40" foregroundColor="#F2C230" transparent="1" zPosition="3" />
        <widget name="subtitle" position="146,86" size="1500,32" font="Regular;22" foregroundColor="#A3ADBB" transparent="1" zPosition="3" />

        <eLabel position="40,150" size="1840,124" backgroundColor="#F2C230" cornerRadius="16" zPosition="1" />
        <widget name="query_box" position="43,153" size="1834,118" backgroundColor="#161C26" cornerRadius="14" zPosition="2" />
        <widget name="query_label" position="74,166" size="400,28" font="Regular;20" foregroundColor="#A3ADBB" transparent="1" zPosition="3" />
        <widget name="query" position="74,198" size="1770,60" font="Bold;36" foregroundColor="#FFFFFF" transparent="1" zPosition="3" />

        <widget name="scope_box" position="40,292" size="1840,76" backgroundColor="#121822" cornerRadius="16" zPosition="2" />
        <widget name="scope_label" position="74,292" size="160,76" font="Regular;20" valign="center" foregroundColor="#A3ADBB" transparent="1" zPosition="3" />
        <widget name="scope" position="240,292" size="1600,76" font="Regular;28" valign="center" foregroundColor="#F2C230" transparent="1" zPosition="3" />

        <widget name="suggestions_box" position="40,388" size="1840,572" backgroundColor="#121822" cornerRadius="16" zPosition="2" />
        <widget name="suggestions_title" position="74,404" size="900,30" font="Regular;20" foregroundColor="#A3ADBB" transparent="1" zPosition="3" />
        {rows}

        <eLabel position="40,976" size="1840,76" backgroundColor="#121822" cornerRadius="16" zPosition="2" />
        <eLabel position="72,1003" size="22,22" backgroundColor="#E5484D" cornerRadius="11" zPosition="3" />
        <widget name="key_red" position="106,994" size="200,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="3" />
        <eLabel position="312,1003" size="22,22" backgroundColor="#30A46C" cornerRadius="11" zPosition="3" />
        <widget name="key_green" position="346,994" size="220,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="3" />
        <eLabel position="572,1003" size="22,22" backgroundColor="#F2C230" cornerRadius="11" zPosition="3" />
        <widget name="key_yellow" position="606,994" size="200,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="3" />
        <eLabel position="812,1003" size="22,22" backgroundColor="#3E7BFA" cornerRadius="11" zPosition="3" />
        <widget name="key_blue" position="846,994" size="240,40" font="Regular;26" valign="center" foregroundColor="#FFFFFF" transparent="1" zPosition="3" />
        <widget name="hint" position="1100,994" size="750,40" font="Regular;22" valign="center" foregroundColor="#C9D0DA" transparent="1" zPosition="3" halign="right" />
    </screen>
    """.format(p=PLUGIN_PATH, rows=_sg_rows_xml()))

    def __init__(self, session, current_site="egydead", default_scope="all", query=""):
        Screen.__init__(self, session)
        self._current_site = current_site
        self._query = query or ""
        self._scope = default_scope or "all"
        self["bg"] = Label("")
        self["title"] = Label("Search")
        self["subtitle"] = Label("Type a name, pick a suggestion, or press GREEN to search")
        self["query_box"] = Label("")
        self["query_label"] = Label("SEARCH TEXT")
        self["query"] = Label("")
        self["scope_box"] = Label("")
        self["scope_label"] = Label("SCOPE")
        self["scope"] = Label("")
        self["suggestions_box"] = Label("")
        self["suggestions_title"] = Label("Quick suggestions")
        for _i in range(SG_ROWS):
            for _n in ("sgBg", "sgBar", "sgText", "sgMeta"):
                self["%s%d" % (_n, _i)] = Label("")
        self._sel = 0
        self._top = 0
        self._zoom = RowZoom(self, self._placeRow)
        self._fonts = {}
        self["hint"] = Label("OK  Search / pick    ◀ ▶  Scope    MENU  Remove one")
        self["key_red"] = Label("Clear")
        self["key_green"] = Label("Search now")
        self["key_yellow"] = Label("Type")
        self["key_blue"] = Label("Choose sites")
        self._suggestions = []
        self._suggestion_ticket = 0

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions"], {
            "menu": self._remove_suggestion,
            "ok": self._submit_or_edit,
            "cancel": self.close,
            "up": self._suggestion_up,
            "down": self._suggestion_down,
            "left": lambda: self._step_scope(-1),
            "right": lambda: self._step_scope(1),
            "red": self._clear_query,
            "green": self._submit,
            "yellow": self._edit_query,
            "blue": self._pick_scope,
        }, -1)
        self.onLayoutFinish.append(self._init_search)

    def _init_search(self):
        self._refresh_suggestions()
        self._refresh()

    def _refresh(self):
        self["key_red"].setText("Clear text" if self._query.strip() else "Clear history")
        preview = self._query or "Type a movie, series or actor name"
        self["query"].setText(_wrap_ui_text(preview, width=42, max_lines=2))
        self["scope"].setText(u"\u2039  {}  \u203a".format(_search_scope_label(self._scope if self._scope else "all")))
        self._refresh_suggestion_list()

    def _refresh_suggestion_list(self):
        n = len(self._suggestions)
        self._sel = max(0, min(self._sel, n - 1))
        if self._sel < self._top:
            self._top = self._sel
        elif self._sel >= self._top + SG_ROWS:
            self._top = self._sel - SG_ROWS + 1
        self._top = max(0, min(self._top, max(0, n - SG_ROWS)))
        self["suggestions_title"].setText("QUICK SUGGESTIONS  ·  {}".format(n) if n else "QUICK SUGGESTIONS")
        for i in range(SG_ROWS):
            g = self._top + i
            names = ["%s%d" % (k, i) for k in ("sgBg", "sgBar", "sgText", "sgMeta")]
            if not n and i == 0:
                for k in names:
                    self[k].hide()
                self["sgText0"].setText("No suggestions yet")
                self["sgText0"].show()
                continue
            if g >= n:
                for k in names:
                    self[k].hide()
                continue
            item = self._suggestions[g]
            on = g == self._sel
            meta = [x for x in (item.get("source"), item.get("kind"), item.get("year")) if x]
            self["sgText%d" % i].setText(_single_line_text(item.get("title", ""), width=60, fallback="Suggestion"))
            self["sgMeta%d" % i].setText("  ·  ".join(str(x) for x in meta))
            self["sgText%d" % i].show()
            self["sgMeta%d" % i].show()
            self["sgBg%d" % i].show() if on else self["sgBg%d" % i].hide()
            self["sgBar%d" % i].show() if on else self["sgBar%d" % i].hide()
            self._fg("sgText%d" % i, "#F2C230" if on else "#D6DCE5")
        self._zoom.focus(self._sel - self._top if n else -1, (self._top, n))

    def _fg(self, name, color):
        inst = self[name].instance
        if inst is not None and parseColor is not None:
            try:
                inst.setForegroundColor(parseColor(color))
                inst.invalidate()
            except Exception:
                pass

    def _placeRow(self, i, p):
        """[rows] the selected suggestion grows like a category row"""
        y = _SG_Y0 + i * _SG_STEP
        dw, dh = 20.0 * p, float(_SG_STEP - _SG_H) * p
        x0, y0, w, h = _SG_X - dw / 2.0, y - dh / 2.0, _SG_W + dw, _SG_H + dh
        for name, x, yy, ww, hh in (("sgBg", x0, y0, w, h), ("sgBar", x0, y0 + 10, 5, h - 20),
                                    ("sgText", x0 + 28, y0, 1200, h), ("sgMeta", x0 + w - 540, y0, 520, h)):
            inst = self["%s%d" % (name, i)].instance
            if inst is None:
                continue
            try:
                inst.move(ePoint(sc(x), sc(yy)))
                inst.resize(eSize(max(1, sc(ww)), max(1, sc(hh))))
            except Exception:
                pass
        size = sc(30 + 2 * p)
        inst = self["sgText%d" % i].instance
        if inst is not None and self._fonts.get(i) != size:
            try:
                inst.setFont(gFont("Regular", size))
                self._fonts[i] = size
            except Exception:
                pass

    def _refresh_suggestions(self):
        self._suggestions = _library_search_suggestions(self._query, self._current_site, limit=8)
        self._refresh_suggestion_list()
        ticket = self._suggestion_ticket = self._suggestion_ticket + 1
        if len((self._query or "").strip()) >= 2 and _tmdb_enabled():
            threading.Thread(target=self._bg_tmdb_suggestions, args=(self._query, ticket), daemon=True).start()

    def _bg_tmdb_suggestions(self, query, ticket):
        suggestions = _tmdb_search_suggestions(query, limit=6)
        callInMainThread(self._merge_tmdb_suggestions, query, ticket, suggestions)

    def _merge_tmdb_suggestions(self, query, ticket, suggestions):
        if ticket != self._suggestion_ticket: return
        if (query or "").strip() != (self._query or "").strip(): return
        seen = set(_normalize_query(item.get("query", item.get("title", ""))) for item in self._suggestions)
        for item in suggestions:
            norm = _normalize_query(item.get("query", item.get("title", "")))
            if not norm or norm in seen: continue
            seen.add(norm)
            self._suggestions.append(item)
        self._suggestions = self._suggestions[:10]
        self._refresh_suggestion_list()

    def _scope_keys(self):
        """All sources, this site (if any), then every search site."""
        from plugin_util import active_search_sites
        sites = active_search_sites()
        keys = ["all"]
        if self._current_site:
            keys.append(self._current_site)
        return keys + [k for k in sites if k != self._current_site]

    def _step_scope(self, step):
        """◀ ▶: previous / next scope (wraps around)."""
        keys = self._scope_keys()
        try:
            i = keys.index(self._scope or "all")
        except ValueError:
            i = 0
        self._scope = keys[(i + step) % len(keys)]
        self._refresh_suggestions()
        self._refresh()

    def _toggle_scope(self):
        self._step_scope(1)

    def _pick_scope(self):
        """BLUE: choose All sources or any single site."""
        from plugin_util import _site_label, active_search_sites
        sites = active_search_sites()
        items = [(u"All sources ({})".format(len(sites)), "all")]
        if self._current_site:
            items.append((u"{}  (this site)".format(_site_label(self._current_site)), self._current_site))
        items += [(_site_label(k), k) for k in sites if k != self._current_site]
        try:
            sel = [v for _l, v in items].index(self._scope or "all")
        except ValueError:
            sel = 0

        def _picked(choice):
            if choice:
                self._scope = choice[1]
                self._refresh_suggestions()
                self._refresh()
        from novaplay_choice import NovaChoiceBox
        self.session.openWithCallback(_picked, NovaChoiceBox, title=u"Search in…", list=items, selection=sel)

    def _clear_query(self):
        """RED: clear the typed text; with nothing typed, clear the
        previous searches (after a yes/no)."""
        if self._query.strip():
            self._query = ""
            self._refresh_suggestions()
            self._refresh()
            return

        def _yes(ans):
            if ans:
                _clear_search_history()
                self._refresh_suggestions()
                self._refresh()
        self.session.openWithCallback(_yes, MessageBox, "Remove all previous searches?",
                                      MessageBox.TYPE_YESNO, default=False)

    def _remove_suggestion(self):
        """MENU: remove the selected previous search."""
        idx = self._sel
        if not (self._suggestions and 0 <= idx < len(self._suggestions)):
            return
        it = self._suggestions[idx]
        if it.get("source") != "Previous search":
            return
        _remove_search_query(it.get("query") or it.get("title"))
        self._refresh_suggestions()
        self._refresh()

    def _edit_query(self):
        self.session.openWithCallback(self._onKeyboard, VirtualKeyBoard, title="Search for a movie or series", text=self._query)

    def _onKeyboard(self, result):
        if result is None: return
        self._query = result.strip()
        self._refresh_suggestions()
        self._refresh()

    def _suggestion_up(self):
        if self._suggestions:
            self._sel = (self._sel - 1) % len(self._suggestions)
            self._refresh_suggestion_list()

    def _suggestion_down(self):
        if self._suggestions:
            self._sel = (self._sel + 1) % len(self._suggestions)
            self._refresh_suggestion_list()

    def _submit_or_edit(self):
        idx = self._sel
        if self._suggestions and idx >= 0 and idx < len(self._suggestions):
            chosen = self._suggestions[idx]
            q = (chosen.get("query") or chosen.get("title") or "").strip()
            if q:
                _save_search_query(q)    # [A1] remember picked suggestion too
            self.close((q, self._scope or "all"))
            return
        if self._query.strip():
            self._submit()
        else:
            self._edit_query()

    def _submit(self):
        query = self._query.strip()
        if not query:
            self._edit_query()
            return
        _save_search_query(query)        # [A1] persist the submitted query
        self.close((query, self._scope or "all"))