"""The clipboard history: the texts copied lately, newest first.

The real thing is the GNOME Shell extension in gnome-shell/: inside the shell
it sees every copy, opens its panel at once and pastes the chosen item. While
it runs, hold2tg only hands it its settings (gnome.py) and stays out of the
way.

Without it (an older GNOME, or not enabled yet) hold2tg falls back on its own:
Wayland lets no background program watch the clipboard, but GNOME mirrors the
clipboard to XWayland, where an X11 client is told about every change. So a
small helper process (clipwatch.py: GTK on its X11 backend, started and kept
running by the daemon) records each copied text here, and the history box
(historybox.py) shows them. This module has no GTK in it.

The history is a JSON file only the user can read. A copy that a password
manager marks as secret is never recorded.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, List, Optional

from . import config as config_mod
from .gnome import Shell
from .notify import Notifier

log = logging.getLogger("hold2tg.history")

DEFAULT_SIZE = 100
MAX_CHARS = 100_000  # a longer copy is not kept
# A password manager (KeePassXC and others) offers this type alongside a
# secret it copies: such copies stay out of the history.
SECRET = "x-kde-passwordManagerHint"
FILES = "x-special/gnome-copied-files"  # files copied in Files: not text to keep
_SPACE = re.compile(r"\s+")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


@dataclass
class Entry:
    text: str
    at: float  # when it was last copied (seconds since the epoch)


def history_path() -> str:
    return os.path.join(config_mod.state_dir(), "clipboard.json")


def shell_history_path() -> str:
    """The extension's own history (texts and pictures, with pins)."""
    return os.path.join(config_mod.state_dir(), "shell-clipboard.json")


class History:
    """The stored history. Writers take a lock; readers need none, since the
    file is only ever replaced whole."""

    def __init__(self, path: str = "", size: int = DEFAULT_SIZE):
        self.path = path or history_path()
        self.size = size

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def load(self) -> List[Entry]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("the clipboard history is unreadable, starting afresh: %s", e)
            return []
        items = raw.get("items") if isinstance(raw, dict) else None
        out = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                at = item.get("at")
                out.append(Entry(item["text"], float(at) if isinstance(at, (int, float)) else 0.0))
        return out

    def _save(self, entries: List[Entry]) -> None:
        folder = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".clipboard-", dir=folder)  # mode 600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "items": [{"text": e.text, "at": e.at} for e in entries]},
                          fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def add(self, text: str, now: Optional[float] = None) -> bool:
        """Puts a copied text on top (moving it there if it is already in the
        history). False if it is not kept: blank, or too long."""
        if not text.strip() or len(text) > MAX_CHARS:
            return False
        with self._locked():
            entries = [e for e in self.load() if e.text != text]
            entries.insert(0, Entry(text, time.time() if now is None else now))
            self._save(entries[:self.size])
        return True

    def remove(self, text: str) -> None:
        with self._locked():
            entries = self.load()
            kept = [e for e in entries if e.text != text]
            if len(kept) != len(entries):
                self._save(kept)

    def clear(self) -> None:
        with self._locked():
            self._save([])


def keep(targets: str) -> bool:
    """Whether a copy offering these clipboard types (one per line) is recorded."""
    names = set(targets.split())
    return SECRET not in names and FILES not in names


# --- how an entry is shown ----------------------------------------------------

def preview(text: str, limit: int = 300) -> str:
    """One line: runs of spaces and line breaks become one space."""
    flat = _SPACE.sub(" ", text).strip()
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def ago(seconds: float, strings: dict, persian: bool = False) -> str:
    """How long ago, e.g. "5 min ago" / "۵ دقیقه پیش"."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return strings["hi_now"]
    for unit, key in ((86400, "hi_days"), (3600, "hi_hours"), (60, "hi_minutes")):
        if seconds >= unit:
            text = strings[key].format(n=seconds // unit)
            return text.translate(_FA_DIGITS) if persian else text
    return strings["hi_now"]


# --- the processes, for the daemon --------------------------------------------

def _command(*args: str) -> List[str]:
    return [sys.executable, "-m", "hold2tg", *args]


class Recorder:
    """Keeps the helper that records copied texts (``hold2tg clipwatch``)
    running while the daemon runs, restarting it if it stops."""

    START_AFTER = (2.0, 5.0, 15.0, 60.0)  # waits after it stops, growing
    STEADY = 60.0                         # running this long resets the waits

    def __init__(self, size: int = DEFAULT_SIZE, shell: Optional[Shell] = None):
        self.size = size
        self.shell = shell
        self.proc: Optional[subprocess.Popen] = None
        self._started = 0.0
        self._next_start = 0.0
        self._failures = 0
        self._warned = False

    def check(self, now: float) -> None:
        """Called from the daemon's loop now and then."""
        if self.shell is not None and self.shell.active():
            if self.proc is not None:
                log.info("the GNOME Shell extension keeps the clipboard history; "
                         "hold2tg's own recorder stops")
                self.stop()
            return
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                if now - self._started >= self.STEADY:
                    self._failures = 0
                return
            log.warning("the clipboard recorder stopped (exit %s); restarting it", code)
            self.proc = None
            self._next_start = now + self.START_AFTER[min(self._failures, len(self.START_AFTER) - 1)]
            self._failures += 1
        if now < self._next_start:
            return
        if not os.environ.get("DISPLAY"):
            if not self._warned:
                self._warned = True
                log.warning("no X display (DISPLAY is not set): the clipboard history "
                            "is not recorded")
            self._next_start = now + 60.0
            return
        env = dict(os.environ, GDK_BACKEND="x11")  # XWayland: see the module docstring
        try:
            self.proc = subprocess.Popen(_command("clipwatch", "--size", str(self.size)),
                                         stdin=subprocess.DEVNULL, env=env)
        except OSError as e:
            log.warning("cannot start the clipboard recorder: %s", e)
            self._next_start = now + 60.0
            return
        self._started = now
        log.info("recording the clipboard history (last %d texts)", self.size)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


class HistoryBox:
    """Opens the history box in a process of its own (GTK has no place inside
    the daemon). Pressing the hotkey while it is open closes it."""

    def __init__(self, notifier: Notifier, shell: Optional[Shell] = None):
        self.notifier = notifier
        self.shell = shell
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def toggle(self) -> None:
        if self.shell is not None and self.shell.active():
            # The extension has the same shortcut and has opened its panel already.
            log.debug("history hotkey: left to the GNOME Shell extension")
            return
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                log.info("closing the clipboard history")
                self._proc.terminate()
                self._proc = None
                return
            log.info("opening the clipboard history")
            self._proc = subprocess.Popen(_command("history"), stdin=subprocess.DEVNULL)
