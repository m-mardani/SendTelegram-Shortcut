"""Records every copied text in the clipboard history (``hold2tg clipwatch``).

Started and kept running by the daemon, with GTK on its X11 backend: GNOME
mirrors the Wayland clipboard to XWayland, and there GTK hears of every
change, even with no window of ours on screen. (On Wayland itself only the
focused window may read the clipboard.)
"""
from __future__ import annotations

import collections
import hashlib
import logging
import os
import shutil
import signal

os.environ["GDK_BACKEND"] = "x11"  # before GTK is loaded

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .history import FILES, SECRET, History, keep  # noqa: E402

log = logging.getLogger("hold2tg.clipwatch")

# GTK leaves types without a "/" out of its formats, and the password
# manager's mark is one of them: so the X11 targets are asked with xclip.
TARGETS = ["timeout", "3", "xclip", "-o", "-selection", "clipboard", "-t", "TARGETS"]


def run(size: int) -> int:
    Gtk.init()
    display = Gdk.Display.get_default()
    if display is None:
        log.error("cannot open the X display %r", os.environ.get("DISPLAY"))
        return 1
    history = History(size=size)
    clipboard = display.get_clipboard()
    has_xclip = shutil.which("xclip") is not None
    if not has_xclip:
        log.warning("xclip is missing: copies a password manager marks as secret cannot be "
                    "told apart (sudo apt install xclip)")
    latest = [0]  # numbers the changes; only the latest one's text is kept
    # Fingerprints of the secrets seen, in memory only: when the password
    # manager quits, GNOME keeps its copy without the mark, so the same text
    # comes again looking ordinary.
    secrets = collections.deque(maxlen=50)

    def fingerprint(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

    def remember(cb, result, _number):
        try:
            secrets.append(fingerprint(cb.read_text_finish(result) or ""))
        except GLib.Error:
            pass

    def done(cb, result, number):
        try:
            text = cb.read_text_finish(result)
        except GLib.Error as e:
            log.debug("could not read the clipboard: %s", e.message)
            return
        if number != latest[0] or not text:
            return  # something newer was copied meanwhile
        if fingerprint(text) in secrets:
            log.debug("skipped a secret copied before")
            return
        try:
            if history.add(text):
                log.debug("recorded a copy (%d chars)", len(text))
        except OSError as e:
            log.warning("cannot save the clipboard history: %s", e)

    def checked(proc, result, number):
        try:
            _ok, out, _err = proc.communicate_utf8_finish(result)
        except GLib.Error as e:
            log.debug("could not list the clipboard's types: %s", e.message)
            out = ""
        if number != latest[0]:
            return
        if out and not keep(out):
            log.debug("skipped a copy marked secret, or files")
            if SECRET in out.split():
                clipboard.read_text_async(None, remember, number)
            return
        clipboard.read_text_async(None, done, number)

    def changed(cb):
        latest[0] += 1
        formats = cb.get_formats()
        if not formats.get_mime_types() and not formats.get_gtypes():
            return  # cleared; the new content follows
        if formats.contain_mime_type(FILES):
            return
        if not has_xclip:
            cb.read_text_async(None, done, latest[0])
            return
        proc = Gio.Subprocess.new(TARGETS, Gio.SubprocessFlags.STDOUT_PIPE
                                  | Gio.SubprocessFlags.STDERR_SILENCE)
        proc.communicate_utf8_async(None, None, checked, latest[0])

    clipboard.connect("changed", changed)
    changed(clipboard)  # what is on the clipboard already

    loop = GLib.MainLoop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit)
    log.info("recording copied texts")
    loop.run()
    return 0
