"""Nautilus (GNOME Files) extension for hold2tg.

Installed to ~/.local/share/nautilus-python/extensions/ and loaded by
python3-nautilus. It does two things:

1. Keeps $XDG_RUNTIME_DIR/hold2tg/nautilus.json up to date with the currently
   selected files and whether a Files window has keyboard focus, so the
   hold2tg daemon knows what to send when the hotkey is held.
2. Adds a "Send to Telegram" entry to the right-click menu.

Works with every extension API: 4.1 (Nautilus 49+), 4.0 (Nautilus 43+, both
GTK 4) and 3.0 (older, GTK 3).
Everything is wrapped defensively: an error here must never break Nautilus.
"""
import json
import os
import shutil
import subprocess
import time

import gi

# Nautilus 49+ ships the 4.1 API (same menu/file calls as 4.0), 43-48 ship
# 4.0, and older releases 3.0 with GTK 3.
for _nautilus, _gtk in (("4.1", "4.0"), ("4.0", "4.0"), ("3.0", "3.0")):
    try:
        gi.require_version("Nautilus", _nautilus)
        gi.require_version("Gtk", _gtk)
        break
    except ValueError:
        continue
else:
    raise ImportError("no supported Nautilus extension API (python3-nautilus)")

from gi.repository import GLib, GObject, Gtk, Nautilus  # noqa: E402


def _state_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    if not os.path.isdir(base):
        base = os.path.expanduser("~/.cache")  # same fallback as hold2tg/config.py
    folder = os.path.join(base, "hold2tg")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    return os.path.join(folder, "nautilus.json")


def _launcher():
    return shutil.which("hold2tg") or os.path.expanduser("~/.local/bin/hold2tg")


class Hold2TgExtension(GObject.GObject, Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()
        self._files = []
        self._round = None
        self._flush_scheduled = False
        self._watched = set()

    # -- state file --
    def _windows(self):
        app = Gtk.Application.get_default()
        return list(app.get_windows()) if app is not None else []

    def _watch_windows(self):
        """Get told whenever a Files window gains or loses focus."""
        for win in self._windows():
            if id(win) not in self._watched:
                self._watched.add(id(win))
                win.connect("notify::is-active", self._on_focus_changed)
                win.connect("destroy", lambda w: self._watched.discard(id(w)))

    def _on_focus_changed(self, *_args):
        self._write()

    def _write(self):
        try:
            active = any(w.is_active() for w in self._windows())
            path = _state_path()
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "active": active,
                           "files": self._files, "updated": time.time()}, fh)
            os.replace(tmp, path)
        except Exception:
            pass

    @staticmethod
    def _paths(files):
        paths = []
        for f in files:
            location = f.get_location()
            path = location.get_path() if location is not None else None
            if path:
                # Normalise to real UTF-8 text even if Nautilus runs in a
                # non-UTF-8 locale (GLib hands us the raw filename bytes).
                paths.append(os.fsencode(path).decode("utf-8", "surrogateescape"))
        return paths

    def _selection_changed(self, paths):
        """Collect the callbacks of one selection change, then write once.

        Depending on the Nautilus version, a selection change calls
        get_file_items() and/or get_background_items(), in either order, and
        get_file_items() may be skipped when nothing is selected. So: remember
        what get_file_items() reported (if it ran) and settle in an idle
        callback, when all calls of this round are done.
        """
        if paths is not None:
            self._round = paths
        if not self._flush_scheduled:
            self._flush_scheduled = True
            GLib.idle_add(self._flush)

    def _flush(self):
        try:
            self._files = self._round or []
            self._round = None
            self._flush_scheduled = False
            self._watch_windows()
            self._write()
        except Exception:
            pass
        return False  # run once

    # -- Nautilus.MenuProvider --
    # Nautilus 43+: get_file_items(files); older: get_file_items(window, files).
    # Nautilus calls these whenever the selection changes, which is what lets
    # us track it.
    def get_file_items(self, *args):
        try:
            paths = self._paths(args[-1])
        except Exception:
            paths = []
        self._selection_changed(paths)
        if not paths:
            return []
        item = Nautilus.MenuItem(
            name="Hold2Tg::send",
            label="Send To Telegram",
            tip="Send the selected items to the Telegram channel (hold2tg)")
        item.connect("activate", self._on_send, paths)
        return [item]

    def get_background_items(self, *args):
        self._selection_changed(None)
        return []

    def _on_send(self, _item, paths):
        try:
            # Bytes arguments: correct in every locale, including LANG=C.
            argv = [os.fsencode(_launcher()), b"send-files", b"--"]
            argv += [p.encode("utf-8", "surrogateescape") for p in paths]
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            pass
