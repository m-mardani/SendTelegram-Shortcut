"""The translation box: a small GTK window with the translation of the
highlighted text, its other meanings and example sentences.

Runs in a process of its own (``hold2tg translate``), never inside the daemon.
Importing it fails with ImportError/ValueError when GTK 4 or libadwaita is
missing.
"""
from __future__ import annotations

import html
import logging
import re
import threading
from typing import Dict, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import clipboard  # noqa: E402
from .translate import RTL, Result, TranslateError, Translator, markup, plain  # noqa: E402

log = logging.getLogger("hold2tg.popup")

APP_ID = "io.github.m_mardani.hold2tg.Translate"
# Colours with transparency, so the cards suit both the light and dark styles.
CSS = """
.h2t-card { border-radius: 14px; padding: 12px 14px; }
.h2t-source { background-color: alpha(currentColor, 0.06); }
.h2t-translation { background-image: linear-gradient(135deg, alpha(#3584e4, 0.32),
                                                     alpha(#9141ac, 0.32)); }
.h2t-meanings { background-color: alpha(currentColor, 0.04); }
.h2t-example { background-color: alpha(#ff7800, 0.12); }
.h2t-badge { font-size: 0.72em; font-weight: 800; padding: 1px 7px; border-radius: 999px;
             background-color: alpha(currentColor, 0.14); }
.h2t-chip { font-weight: bold; font-size: 0.9em; padding: 2px 10px; border-radius: 999px; }
.h2t-chip-0 { background-color: alpha(#2ec27e, 0.30); }
.h2t-chip-1 { background-color: alpha(#9141ac, 0.35); }
.h2t-chip-2 { background-color: alpha(#e66100, 0.30); }
.h2t-chip-3 { background-color: alpha(#3584e4, 0.32); }
.h2t-big { font-size: 1.6em; font-weight: bold; }
.h2t-dim { opacity: 0.72; }
.h2t-section { font-weight: bold; }
.h2t-icon-meanings { color: #2ec27e; }
.h2t-icon-examples { color: #ff7800; }
"""


def _box(vertical: bool = False, spacing: int = 8, *css: str, **props) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical
                  else Gtk.Orientation.HORIZONTAL, spacing=spacing, **props)
    for name in css:
        box.add_css_class(name)
    return box


class TranslationWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, text: str, translator: Translator,
                 strings: Dict[str, str], names: Dict[str, str]):
        super().__init__(application=app, title=strings["tr_title"],
                         default_width=480, default_height=540)
        self.s = strings
        self.names = names
        self.translator = translator
        self.result = None
        self.example_labels: List[Gtk.Label] = []
        # The word in each example, in a colour that reads well on this style.
        self.highlight = "#ffa348" if Adw.StyleManager.get_default().get_dark() else "#c64600"

        self.heading = Adw.WindowTitle(title=strings["tr_title"])
        header = Adw.HeaderBar(title_widget=self.heading)

        self.stack = Gtk.Stack()
        working = _box(True, 12, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        working.append(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        working.append(self._label(strings["tr_working"], "", "h2t-dim", selectable=False))
        self.stack.add_named(working, "working")
        self.body = _box(True, 10, margin_top=6, margin_bottom=16, margin_start=14,
                         margin_end=14)
        # No scrolling to the focused button: the box must open at its top.
        viewport = Gtk.Viewport(child=self.body, scroll_to_focus=False)
        self.stack.add_named(Gtk.ScrolledWindow(child=viewport,
                                                hscrollbar_policy=Gtk.PolicyType.NEVER),
                             "result")
        self.error = Adw.StatusPage(icon_name="dialog-error-symbolic",
                                    title=strings["translate_failed"])
        self.stack.add_named(self.error, "error")

        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("notify::is-active",
                     lambda *_: log.debug("window active: %s", self.is_active()))
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

    # -- work (in a thread) --
    def _work(self, text: str) -> None:
        try:
            result = self.translator.translate(text)
        except TranslateError as e:
            GLib.idle_add(self._show_error, e)
            return
        GLib.idle_add(self._show, result)
        if result.examples:
            try:
                lines = self.translator.translate_lines(
                    [plain(e) for e in result.examples], result.src_lang, result.tgt_lang)
            except TranslateError as e:
                log.warning("could not translate the examples: %s", e)
                return
            GLib.idle_add(self._show_example_translations, lines)

    # -- building blocks --
    def _label(self, text: str, language: str, *css: str, use_markup: bool = False,
               selectable: bool = True) -> Gtk.Label:
        """language: the label runs in its direction; "" = the UI's direction."""
        label = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0,
                          selectable=selectable, hexpand=True)
        if language:
            label.set_direction(Gtk.TextDirection.RTL if language.split("-")[0] in RTL
                                else Gtk.TextDirection.LTR)
        if use_markup:
            label.set_markup(text)
        else:
            label.set_text(text)
        for name in css:
            label.add_css_class(name)
        return label

    def _badge(self, language: str) -> Gtk.Label:
        badge = Gtk.Label(label=language.split("-")[0].upper(), valign=Gtk.Align.START,
                          tooltip_text=self._name(language))
        badge.add_css_class("h2t-badge")
        return badge

    def _section(self, title: str, icon: str, colour: str) -> None:
        row = _box(False, 8, margin_top=8, margin_start=4, margin_end=4)
        image = Gtk.Image(icon_name=icon)
        image.add_css_class(colour)
        row.append(image)
        row.append(self._label(title, "", "h2t-section", selectable=False))
        self.body.append(row)

    def _name(self, code: str) -> str:
        return self.names.get(code) or self.names.get(code.split("-")[0]) or code

    def _example_markup(self, example: str, word: str) -> str:
        text = markup(example)
        if "<b>" not in text and word and " " not in word:
            # Google marks the word in its own examples only; mark it in the rest.
            text = re.sub(r"(?i)(?<![&\w])(%s\w*)" % re.escape(html.escape(word)),
                          r"<b>\1</b>", text)
        return text.replace("<b>", '<span weight="bold" foreground="%s">' % self.highlight) \
                   .replace("</b>", "</span>")

    # -- showing (in the GTK thread) --
    def _show(self, r: Result) -> bool:
        self.result = r
        self.heading.set_subtitle(self.s["tr_direction"].format(
            src=self._name(r.src_lang), tgt=self._name(r.tgt_lang)))

        source = r.source if len(r.source) <= 300 else r.source[:300] + "…"
        foreign = r.src_lang != self.translator.language
        if r.pronunciation and foreign and len(r.source.split()) <= 2:
            source += "  ·  " + r.pronunciation
        card = _box(False, 10, "h2t-card", "h2t-source")
        card.append(self._label(source, r.src_lang, "h2t-dim"))
        card.append(self._badge(r.src_lang))
        self.body.append(card)

        card = _box(True, 6, "h2t-card", "h2t-translation")
        top = _box(False, 6)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=self.s["tr_copy"],
                          valign=Gtk.Align.START)
        copy.add_css_class("flat")
        copy.add_css_class("circular")
        copy.connect("clicked", self._copy)
        top.append(copy)
        top.append(Gtk.Box(hexpand=True))
        top.append(self._badge(r.tgt_lang))
        card.append(top)
        card.append(self._label(r.text, r.tgt_lang, "h2t-big" if len(r.text) < 120 else "body"))
        self.body.append(card)

        if r.meanings:
            self._section(self.s["tr_meanings"], "accessories-dictionary-symbolic",
                          "h2t-icon-meanings")
            card = _box(True, 10, "h2t-card", "h2t-meanings")
            comma = "، " if r.tgt_lang in RTL else ", "
            for i, (pos, words) in enumerate(r.meanings):
                row = _box(False, 10)
                if pos:
                    chip = Gtk.Label(label=pos, valign=Gtk.Align.START)
                    chip.add_css_class("h2t-chip")
                    chip.add_css_class("h2t-chip-%d" % (i % 4))
                    row.append(chip)
                # In the UI's direction, so the words sit next to their chip.
                row.append(self._label(comma.join(words), ""))
                card.append(row)
            self.body.append(card)

        if r.examples:
            self._section(self.s["tr_examples"], "format-justify-left-symbolic",
                          "h2t-icon-examples")
            word = r.source.strip() if foreign else ""
            for example in r.examples:
                card = _box(True, 4, "h2t-card", "h2t-example")
                card.append(self._label(self._example_markup(example, word), r.src_lang,
                                        use_markup=True))
                translation = self._label("", r.tgt_lang, "h2t-dim")
                translation.set_visible(False)  # until its translation arrives
                card.append(translation)
                self.example_labels.append(translation)
                self.body.append(card)

        self.stack.set_visible_child_name("result")
        self.set_focus(copy)  # not a label: it would select all its text
        return GLib.SOURCE_REMOVE

    def _show_example_translations(self, lines: List[str]) -> bool:
        for label, line in zip(self.example_labels, lines):
            if line:
                label.set_text(line)
                label.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _show_error(self, e: TranslateError) -> bool:
        self.error.set_description(self.s["tr_blocked"] if e.blocked else str(e))
        self.stack.set_visible_child_name("error")
        return GLib.SOURCE_REMOVE

    # -- actions --
    def _copy(self, _button) -> None:
        text = self.result.text if self.result else ""

        def work():
            try:
                clipboard.copy_text(text)  # outlives this window, unlike GTK's own
                GLib.idle_add(self.toasts.add_toast, Adw.Toast(title=self.s["tr_copied"]))
            except clipboard.ClipboardError as e:
                log.warning("%s", e)
                GLib.idle_add(self.toasts.add_toast, Adw.Toast(title=str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _key(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == 0xff1b:  # Escape
            self.close()
            return True
        return False


def run(text: str, translator: Translator, strings: Dict[str, str],
        names: Dict[str, str], rtl: bool) -> int:
    GLib.set_prgname("hold2tg")
    if rtl:
        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(app):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        window = TranslationWindow(app, text, translator, strings, names)
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.present()

    app.connect("activate", activate)
    return app.run([])
