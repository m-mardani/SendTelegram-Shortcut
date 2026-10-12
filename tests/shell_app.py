"""A tiny window for the extension tests: a text field that reports what it
holds (pasted text ends up there), and that can copy a "secret" the way a
password manager does.

    python3 tests/shell_app.py REPORT_FILE [--secret TEXT]
"""
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

report = sys.argv[1]
secret = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--secret" else None


def activate(app):
    window = Gtk.ApplicationWindow(application=app, title="hold2tg test app")
    entry = Gtk.Entry()
    window.set_child(entry)

    def changed(_entry):
        with open(report, "w", encoding="utf-8") as fh:
            fh.write(entry.get_text())

    entry.connect("changed", changed)
    window.present()
    if secret is not None:
        def copy_secret():
            data = GLib.Bytes.new(secret.encode("utf-8"))
            window.get_clipboard().set_content(Gdk.ContentProvider.new_union([
                Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", data),
                Gdk.ContentProvider.new_for_bytes("x-kde-passwordManagerHint",
                                                  GLib.Bytes.new(b"secret")),
            ]))
            with open(report, "w", encoding="utf-8") as fh:
                fh.write("secret copied")
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(500, copy_secret)


# Non-unique: a second one must open its own window, not wake the first.
app = Gtk.Application(application_id="io.github.m_mardani.hold2tg.TestApp",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([])
