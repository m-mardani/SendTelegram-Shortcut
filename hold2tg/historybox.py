"""The clipboard history box: the texts copied lately, newest first, in a small
window (GNOME opens it in the middle of the screen). Clicking one, or Enter,
copies it again and closes the box; typing searches; Escape closes it.

Runs in a process of its own (``hold2tg history``), never inside the daemon.
Importing it fails with ImportError/ValueError when GTK 4 or libadwaita is
missing.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import clipboard  # noqa: E402
from .history import Entry, History, ago, preview  # noqa: E402
from .translate import looks_like  # noqa: E402

log = logging.getLogger("hold2tg.historybox")

APP_ID = "io.github.m_mardani.hold2tg.History"
CSS = """
.h2h-row { padding: 8px 4px 8px 12px; }
.h2h-meta { font-size: 0.82em; opacity: 0.62; }
"""
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


class EntryRow(Gtk.ListBoxRow):
    def __init__(self, entry: Entry, meta: str, delete_tip: str):
        super().__init__()
        self.entry = entry
        box = Gtk.Box(spacing=8)
        box.add_css_class("h2h-row")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                        valign=Gtk.Align.CENTER)
        shown = preview(entry.text)
        label = Gtk.Label(label=shown, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          lines=2, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        # Each text in its own direction: Persian from the right, English from the left.
        label.set_direction(Gtk.TextDirection.RTL if looks_like("fa", entry.text)
                            else Gtk.TextDirection.LTR)
        texts.append(label)
        info = Gtk.Label(label=meta, xalign=0)
        info.add_css_class("h2h-meta")
        texts.append(info)
        box.append(texts)
        self.delete = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=delete_tip,
                                 valign=Gtk.Align.CENTER)
        self.delete.add_css_class("flat")
        self.delete.add_css_class("circular")
        box.append(self.delete)
        if shown != entry.text.strip():
            self.set_tooltip_text(entry.text[:1500])
        self.set_child(box)


class HistoryWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, history: History, strings: Dict[str, str],
                 persian: bool):
        super().__init__(application=app, title=strings["hi_title"],
                         default_width=460, default_height=560)
        self.s = strings
        self.persian = persian
        self.history = history
        self.copying = False      # while the copy is under way, losing focus is expected
        self.closing = False      # closing also loses the focus: do not close twice
        self.was_active = False
        self.dialog: Optional[Adw.AlertDialog] = None

        self.heading = Adw.WindowTitle(title=strings["hi_title"])
        header = Adw.HeaderBar(title_widget=self.heading)
        self.clear_button = Gtk.Button(icon_name="user-trash-symbolic",
                                       tooltip_text=strings["hi_clear"])
        self.clear_button.connect("clicked", self._ask_clear)
        header.pack_end(self.clear_button)

        self.search = Gtk.SearchEntry(placeholder_text=strings["hi_search"], hexpand=True,
                                      margin_top=6, margin_bottom=6, margin_start=12,
                                      margin_end=12)
        self.search.connect("search-changed", lambda *_: self.list.invalidate_filter())
        self.search.connect("activate", self._activate_first)
        self.search.connect("stop-search", lambda *_: self.close())
        self.search.set_key_capture_widget(self)  # typing anywhere goes to the search
        down = Gtk.EventControllerKey()
        down.connect("key-pressed", self._search_key)
        self.search.add_controller(down)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                activate_on_single_click=True, valign=Gtk.Align.START,
                                margin_bottom=12, margin_start=12, margin_end=12)
        self.list.add_css_class("boxed-list")
        self.list.set_filter_func(self._matches)
        self.list.set_placeholder(Gtk.Label(label=strings["hi_no_match"], margin_top=18,
                                            margin_bottom=18, css_classes=["dim-label"]))
        self.list.connect("row-activated", lambda _list, row: self._copy(row.entry.text))
        scrolled = Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.search)
        page.append(scrolled)

        self.stack = Gtk.Stack()
        self.stack.add_named(page, "list")
        self.stack.add_named(Adw.StatusPage(icon_name="edit-paste-symbolic",
                                            title=strings["hi_empty"],
                                            description=strings["hi_empty_hint"]), "empty")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("notify::is-active", self._active_changed)
        self.connect("close-request", self._close_request)
        self._fill()

    # -- filling --
    def _number(self, n: int) -> str:
        return str(n).translate(_FA_DIGITS) if self.persian else str(n)

    def _fill(self) -> None:
        now = time.time()
        first = None
        for entry in self.history.load():
            meta = ago(now - entry.at, self.s, self.persian)
            lines = entry.text.strip().count("\n") + 1
            if lines > 1:
                meta += " · " + self.s["hi_lines"].format(n=self._number(lines))
            row = EntryRow(entry, meta, self.s["hi_delete"])
            row.delete.connect("clicked", lambda _b, r=row: self._delete(r))
            self.list.append(row)
            first = first or row
        self._update_count()
        if first is not None:
            self.list.select_row(first)
            self.set_focus(first)

    def _rows(self):
        i = 0
        while (row := self.list.get_row_at_index(i)) is not None:
            yield row
            i += 1

    def _update_count(self) -> None:
        count = sum(1 for _ in self._rows())
        self.heading.set_subtitle(self.s["hi_count"].format(n=self._number(count)) if count else "")
        self.clear_button.set_sensitive(count > 0)
        self.stack.set_visible_child_name("list" if count else "empty")

    def _matches(self, row: EntryRow) -> bool:
        query = self.search.get_text().strip().casefold()
        return not query or query in row.entry.text.casefold()

    # -- actions --
    def _copy(self, text: str) -> None:
        if self.copying:
            return
        self.copying = True

        def work():
            try:
                clipboard.copy_text(text)  # outlives this window, unlike GTK's own
            except clipboard.ClipboardError as e:
                log.warning("%s", e)
                GLib.idle_add(self._copy_failed, str(e))
                return
            GLib.idle_add(self.close)

        threading.Thread(target=work, daemon=True).start()

    def _copy_failed(self, message: str) -> bool:
        self.copying = False
        self.toasts.add_toast(Adw.Toast(title=message))
        return GLib.SOURCE_REMOVE

    def _activate_first(self, _entry) -> None:
        for row in self._rows():
            if self._matches(row):
                self._copy(row.entry.text)
                return

    def _delete(self, row: EntryRow) -> None:
        try:
            self.history.remove(row.entry.text)
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=str(e)))
            return
        self.list.remove(row)
        self._update_count()

    def _ask_clear(self, _button) -> None:
        count = sum(1 for _ in self._rows())
        self.dialog = Adw.AlertDialog(heading=self.s["hi_clear_q"],
                                      body=self.s["hi_clear_body"].format(n=self._number(count)))
        self.dialog.add_response("cancel", self.s["hi_cancel"])
        self.dialog.add_response("clear", self.s["hi_clear"])
        self.dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        self.dialog.set_default_response("cancel")
        self.dialog.set_close_response("cancel")
        self.dialog.connect("response", self._cleared)
        self.dialog.present(self)

    def _cleared(self, _dialog, response: str) -> None:
        self.dialog = None
        if response != "clear":
            return
        try:
            self.history.clear()
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=str(e)))
            return
        self.list.remove_all()
        self._update_count()

    # -- keys and focus --
    def _search_key(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            for row in self._rows():
                if self._matches(row):
                    self.list.select_row(row)
                    row.grab_focus()
                    return True
        return False

    def _key(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self.dialog is None:
            self.close()
            return True
        return False

    def _close_request(self, *_args) -> bool:
        self.closing = True
        return False  # go on closing

    def _active_changed(self, *_args) -> None:
        # Like a menu: clicking anywhere else closes it.
        if self.is_active():
            self.was_active = True
        elif self.was_active and not (self.copying or self.closing) and self.dialog is None:
            self.close()


def run(history: History, strings: Dict[str, str], persian: bool) -> int:
    GLib.set_prgname("hold2tg")
    if persian:
        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(app):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        window = HistoryWindow(app, history, strings, persian)
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.present()

    app.connect("activate", activate)
    return app.run([])
