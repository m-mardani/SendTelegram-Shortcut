"""hold2tg and GNOME: the Shell extension, and Caps Lock as a hotkey key.

The extension (gnome-shell/) lives inside GNOME Shell. hold2tg's daemon hands
it its settings each time it starts; the extension then

* keeps the clipboard history and opens its panel on ``history_keys``;
* takes the Caps Lock hotkeys away from the apps. Caps Lock is no modifier, so
  without that, holding Caps Lock + C would type "cccc…" into the focused
  window. (hold2tg itself only *listens* to the keyboard.)

For Caps Lock to be usable in a shortcut at all, GNOME's ``caps:hyper`` keyboard
option makes it the Hyper modifier, on a modifier of its own (Mod3), so it
does not clash with Super shortcuts. Caps Lock then no longer turns on capitals.
hold2tg turns the option on while a hotkey uses Caps Lock, and off again if it
was hold2tg that turned it on.

Everything goes through gdbus and gsettings: no GTK in the daemon.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import time
from typing import List, Optional

from . import config as config_mod
from .hotkey import MODIFIERS, parse_combo

log = logging.getLogger("hold2tg.gnome")

SHELL_UUID = "hold2tg-clipboard@m-mardani.github.io"
SHELL_BUS = "io.github.m_mardani.hold2tg.Clipboard"
SHELL_PATH = "/io/github/m_mardani/hold2tg/Clipboard"
SHELL_SCHEMA = "org.gnome.shell.extensions.hold2tg-clipboard"
XKB_SCHEMA = "org.gnome.desktop.input-sources"
CAPS_OPTION = "caps:hyper"  # Caps Lock becomes Hyper, on Mod3 of its own

_MODIFIERS = {"ctrl": "<Control>", "control": "<Control>", "shift": "<Shift>", "alt": "<Alt>",
              "super": "<Super>", "win": "<Super>", "meta": "<Super>", "cmd": "<Super>",
              # The real modifier, not <Hyper>: should caps:hyper be missing, Hyper
              # maps to nothing and <Hyper>c would take the plain c key from you.
              "capslock": "<Mod3>", "caps": "<Mod3>"}
_KEY_NAMES = {
    "esc": "Escape", "tab": "Tab", "enter": "Return", "space": "space", "backspace": "BackSpace",
    "insert": "Insert", "delete": "Delete", "home": "Home", "end": "End", "pageup": "Page_Up",
    "pagedown": "Page_Down", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "print": "Print", "pause": "Pause", "scrolllock": "Scroll_Lock", "grave": "grave",
    "minus": "minus", "equal": "equal", "backslash": "backslash", "semicolon": "semicolon",
    "apostrophe": "apostrophe", "comma": "comma", "dot": "period", "slash": "slash",
    "leftbrace": "bracketleft", "rightbrace": "bracketright",
}


def accelerator(hotkey: str) -> str:
    """hold2tg's ``super+alt+h`` as GNOME writes it: ``<Super><Alt>h``
    (``capslock+h`` is ``<Mod3>h``). The hotkey has been checked already."""
    mods, key = "", ""
    for part in (p.strip().lower() for p in hotkey.replace("-", "+").split("+")):
        if part in _MODIFIERS:
            mods += _MODIFIERS[part]
        elif part:
            key = _KEY_NAMES.get(part) or (part.upper() if part[0] == "f" and part[1:].isdigit()
                                           else part)
    return mods + key


def uses_capslock(hotkey: str) -> bool:
    return bool(hotkey) and MODIFIERS["capslock"] in parse_combo(hotkey).groups


def _strv(text: str) -> List[str]:
    """A gsettings string list as printed: ``['a', 'b']`` or ``@as []``."""
    text = text.strip()
    if not text or text.startswith("@as"):
        return []
    value = ast.literal_eval(text)
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


class Shell:
    """The extension and the GNOME settings hold2tg looks after."""

    CACHE = 5.0  # seconds to trust the last answer to "is it running?"

    def __init__(self, run=subprocess.run, clock=time.monotonic, marker: str = ""):
        self._run = run
        self._clock = clock
        self._checked = -1e9
        self._active = False
        self._marker = marker

    def _command(self, argv: List[str]) -> Optional[str]:
        try:
            out = self._run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None

    # -- the extension --
    def active(self) -> bool:
        """Whether the extension is running in this session right now."""
        now = self._clock()
        if now - self._checked >= self.CACHE:
            out = self._command(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                                 "--object-path", "/org/freedesktop/DBus", "--method",
                                 "org.freedesktop.DBus.NameHasOwner", SHELL_BUS])
            self._active = bool(out and "true" in out)
            self._checked = now
        return self._active

    def call(self, method: str) -> bool:
        return self._command(["gdbus", "call", "--session", "--dest", SHELL_BUS, "--object-path",
                              SHELL_PATH, "--method", "%s.%s" % (SHELL_BUS, method)]) is not None

    @staticmethod
    def schema_dir() -> str:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return os.path.join(base, "gnome-shell", "extensions", SHELL_UUID, "schemas")

    def installed(self) -> bool:
        return os.path.isfile(os.path.join(self.schema_dir(), "gschemas.compiled"))

    def sync(self, cfg) -> bool:
        """Hands hold2tg's settings to the extension: the history's shortcut,
        language and size, whether to record at all, and the Caps Lock hotkeys
        to keep from the apps. False if it is not installed."""
        if not self.installed():
            return False
        on = cfg.history_combo is not None
        caught = [accelerator(k) for k in (cfg.hotkey, cfg.fetch_hotkey, cfg.translate_hotkey)
                  if uses_capslock(k)]
        values = [
            ("toggle-history", "[%r]" % accelerator(cfg.history_hotkey) if on else "@as []"),
            ("record", "true" if on else "false"),
            ("language", "'fa'" if cfg.language == "fa" else "'en'"),
            ("history-size", str(max(10, min(500, cfg.history_size)))),
            ("catch-keys", repr(caught) if caught else "@as []"),
        ]
        ok = True
        for key, value in values:
            if self._command(["gsettings", "--schemadir", self.schema_dir(), "set",
                              SHELL_SCHEMA, key, value]) is None:
                log.warning("could not pass %s on to the GNOME Shell extension", key)
                ok = False
        return ok

    # -- Caps Lock as a modifier --
    def _marker_path(self) -> str:
        return self._marker or os.path.join(config_mod.state_dir(), "gnome.json")

    def _added(self) -> bool:
        try:
            with open(self._marker_path(), encoding="utf-8") as fh:
                return CAPS_OPTION in json.load(fh).get("added_xkb_options", [])
        except (OSError, ValueError, AttributeError):
            return False

    def _remember(self, added: bool) -> None:
        with open(self._marker_path(), "w", encoding="utf-8") as fh:
            json.dump({"added_xkb_options": [CAPS_OPTION] if added else []}, fh)

    def keyboard(self, cfg) -> Optional[str]:
        """Turns caps:hyper on while a hotkey uses Caps Lock, and off when none
        does (only if hold2tg turned it on). Returns a problem to report."""
        need = any(uses_capslock(k) for k in (cfg.hotkey, cfg.fetch_hotkey,
                                              cfg.translate_hotkey, cfg.history_hotkey))
        raw = self._command(["gsettings", "get", XKB_SCHEMA, "xkb-options"])
        if raw is None:
            return ("cannot read GNOME's keyboard options: Caps Lock hotkeys need the "
                    "%s option (Settings → Keyboard)" % CAPS_OPTION) if need else None
        options = _strv(raw)
        if need:
            if CAPS_OPTION in options:
                return None
            other = [o for o in options if o.startswith("caps:")]
            if other:
                return ("Caps Lock is set up as %s in GNOME, so it cannot be a hotkey key; "
                        "remove that option, or pick other hotkeys" % ", ".join(other))
            options.append(CAPS_OPTION)
        elif self._added() and CAPS_OPTION in options:
            options.remove(CAPS_OPTION)
        else:
            return None
        value = repr(options) if options else "@as []"
        if self._command(["gsettings", "set", XKB_SCHEMA, "xkb-options", value]) is None:
            return "could not change GNOME's keyboard options"
        self._remember(need)
        log.info("Caps Lock %s (GNOME keyboard option %s)",
                 "is now a hotkey key" if need else "is Caps Lock again", CAPS_OPTION)
        return None
