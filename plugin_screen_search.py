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
from Components.MenuList import MenuList

from plugin_common import PLUGIN_PATH
from plugin_gridlist import scale_skin_xml
from plugin_state import (_library_search_suggestions, _save_search_query)
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
        <widget name="suggestions" position="58,446" size="1804,500" zPosition="3" scrollbarMode="showOnDemand" foregroundColor="#D6DCE5" foregroundColorSelected="#F2C230" backgroundColor="#121822" backgroundColorSelected="#2B3648" font="Regular;30" itemHeight="50" />

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
    """.format(p=PLUGIN_PATH))

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
        self["suggestions"] = MenuList([])
        self["hint"] = Label("OK  Search / pick    ▲ ▼  Suggestions    EXIT  Back")
        self["key_red"] = Label("Clear")
        self["key_green"] = Label("Search now")
        self["key_yellow"] = Label("Type")
        self["key_blue"] = Label("Choose sites")
        self._suggestions = []
        self._suggestion_ticket = 0

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self._submit_or_edit,
            "cancel": self.close,
            "up": self._suggestion_up,
            "down": self._suggestion_down,
            "left": self._toggle_scope,
            "right": self._toggle_scope,
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
        preview = self._query or "Type a movie, series or actor name"
        self["query"].setText(_wrap_ui_text(preview, width=42, max_lines=2))
        self["scope"].setText(_search_scope_label(self._scope if self._scope else "all"))
        self._refresh_suggestion_list()

    def _refresh_suggestion_list(self):
        if not self._suggestions:
            self["suggestions_title"].setText("QUICK SUGGESTIONS")
            self["suggestions"].setList(["No suggestions yet"])
            return
        self["suggestions_title"].setText("QUICK SUGGESTIONS  ·  {}".format(len(self._suggestions)))
        rows = []
        for item in self._suggestions:
            meta = []
            if item.get("source"): meta.append(item.get("source"))
            if item.get("kind"): meta.append(item.get("kind"))
            if item.get("year"): meta.append(item.get("year"))
            label = _single_line_text(item.get("title", ""), width=60, fallback="Suggestion")
            meta_text = "  ·  ".join([x for x in meta if x])
            if meta_text:
                label = "{}      {}".format(label, meta_text)
            rows.append(label)
        self["suggestions"].setList(rows)

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

    def _toggle_scope(self):
        """◀ ▶: flip between all sources and the current site (if any)."""
        if not self._current_site:
            return
        self._scope = self._current_site if self._scope == "all" else "all"
        self._refresh_suggestions()
        self._refresh()

    def _pick_scope(self):
        """BLUE: choose All sources or any single site."""
        try:
            from extractors import get_search_site_order
            from plugin_util import _site_label
            sites = list(get_search_site_order())
        except Exception:
            sites = []
            _site_label = str
        items = [(u"All sources ({})".format(len(sites)), "all")]
        if self._current_site and self._current_site in sites:
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
        self._query = ""
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
        if self._suggestions: self["suggestions"].up()

    def _suggestion_down(self):
        if self._suggestions: self["suggestions"].down()

    def _submit_or_edit(self):
        idx = self["suggestions"].getSelectedIndex()
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