"""The background daemon: watches keyboards through evdev and fires the
hotkeys: send, fetch (copies the channel's newest message to the clipboard),
translate (opens the translation box) and history (shows the texts copied
lately). It also keeps the clipboard recorder running for the history.

evdev reads raw key events from /dev/input, so it works the same on X11 and
Wayland and independently of the active keyboard layout. It only *listens*:
the key presses still reach the focused application as usual.
"""
from __future__ import annotations

import fcntl
import glob
import logging
import os
import select
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

from . import config as config_mod
from . import selection
from .config import Config
from .fetcher import Fetcher
from .history import HistoryBox, Recorder
from .hotkey import HoldDetector, describe
from .notify import Notifier
from .sender import Sender
from .translate import Popup

log = logging.getLogger("hold2tg.listener")

RESCAN_SECONDS = 5.0
SYNC_SECONDS = 600.0  # collect channel posts well within Telegram's 24-hour window


class AlreadyRunning(Exception):
    pass


@dataclass
class Hotkey:
    name: str
    detector: HoldDetector
    action: Callable[[Any], Any]            # gets what `capture` returned
    failed: str                             # message key shown if the action crashes
    capture: Optional[Callable[[], Any]] = None  # runs the moment the combo goes down
    pending: Optional[Future] = None


class Listener:
    def __init__(self, cfg: Config, sender: Sender, notifier: Notifier,
                 fetcher: Optional[Fetcher] = None, translator: Optional[Popup] = None,
                 history_box: Optional[HistoryBox] = None, recorder: Optional[Recorder] = None):
        import evdev  # lazy: only the daemon needs it

        self.evdev = evdev
        self.cfg = cfg
        self.notifier = notifier
        backend = cfg.text_backend
        self.hotkeys: List[Hotkey] = [Hotkey(
            "send", HoldDetector(cfg.combo, cfg.hold_seconds), sender.send, "failed",
            # Snapshot the selection the instant the combo goes down — before
            # the focused app reacts to the keys (a "Save As" dialog would
            # otherwise steal the focus and sometimes the selection).
            capture=lambda: selection.capture(backend))]
        self.fetcher = fetcher if cfg.fetch_combo is not None else None
        if self.fetcher is not None:
            self.hotkeys.append(Hotkey(
                "fetch", HoldDetector(cfg.fetch_combo, cfg.fetch_hold_seconds),
                lambda _: fetcher.fetch(), "fetch_failed"))
        if translator is not None and cfg.translate_combo is not None:
            self.hotkeys.append(Hotkey(
                "translate", HoldDetector(cfg.translate_combo, cfg.translate_hold_seconds),
                translator.show, "translate_failed",
                capture=lambda: selection.read_primary_text(backend)))
        self.recorder = None
        if history_box is not None and cfg.history_combo is not None:
            self.hotkeys.append(Hotkey(
                "history", HoldDetector(cfg.history_combo, cfg.history_hold_seconds),
                lambda _: history_box.toggle(), "history_failed"))
            self.recorder = recorder
        self.codes = frozenset().union(*(h.detector.combo.all_codes for h in self.hotkeys))
        self.devices: Dict[str, "evdev.InputDevice"] = {}
        self.rejected: Set[str] = set()   # opened fine, but not a keyboard
        self.denied: Set[str] = set()     # no permission (yet)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hold2tg")
        self._warned_no_keyboard = False
        self._lock_fh = None
        self.running = True

    # -- single instance --
    def acquire_lock(self) -> None:
        path = os.path.join(config_mod.runtime_dir(), "daemon.lock")
        self._lock_fh = open(path, "w")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning("hold2tg is already running")

    # -- devices --
    def scan(self) -> None:
        now = time.monotonic()
        paths = set(glob.glob("/dev/input/event*"))
        self.rejected &= paths
        self.denied &= paths
        for path in sorted(paths - set(self.devices) - self.rejected):
            try:
                dev = self.evdev.InputDevice(path)
            except PermissionError:
                self.denied.add(path)
                continue
            except OSError:
                continue
            self.denied.discard(path)
            try:
                keys = set(dev.capabilities().get(self.evdev.ecodes.EV_KEY, []))
                if not keys & self.codes:
                    dev.close()
                    self.rejected.add(path)
                    continue
                self.devices[path] = dev
                active = dev.active_keys()
                self._feed(lambda d: d.sync_device(path, active, now))
                log.info("listening on %s (%s)", path, dev.name)
            except OSError:
                self._drop(path)
        if self.devices:
            self._warned_no_keyboard = False
        elif self.denied and not self._warned_no_keyboard:
            # Keyboards exist but we may not read them: a setup problem the
            # user has to fix, so say it once (and again only if it recurs).
            self._warned_no_keyboard = True
            log.error("no readable keyboard device (%d denied by permissions); "
                      "run `hold2tg doctor`", len(self.denied))
            self.notifier.show("hold2tg", self.notifier.t("no_keyboard"), error=True)

    def _drop(self, path: str) -> None:
        dev = self.devices.pop(path, None)
        if dev is not None:
            log.info("device removed: %s", path)
            try:
                dev.close()
            except OSError:
                pass
        now = time.monotonic()
        self._feed(lambda d: d.drop_device(path, now))

    # -- signals from the detectors --
    def _feed(self, step: Callable[[HoldDetector], Optional[str]]) -> None:
        for hotkey in self.hotkeys:
            self._handle(hotkey, step(hotkey.detector))

    def _handle(self, hotkey: Hotkey, signal: Optional[str]) -> None:
        if signal == "arm":
            log.debug("%s armed", hotkey.name)
            if hotkey.capture is not None:
                hotkey.pending = self.pool.submit(hotkey.capture)
        elif signal == "cancel":
            log.debug("%s cancelled", hotkey.name)
            hotkey.pending = None
        elif signal == "fire":
            pending, hotkey.pending = hotkey.pending, None
            log.info("%s hotkey held for %gs", hotkey.name, hotkey.detector.hold_seconds)
            self.pool.submit(self._run, hotkey, pending)

    def _run(self, hotkey: Hotkey, pending: Optional[Future]) -> None:
        try:
            if pending is not None:
                captured = pending.result(timeout=10)
            else:
                captured = hotkey.capture() if hotkey.capture is not None else None
            hotkey.action(captured)
        except Exception:
            log.exception("%s failed", hotkey.name)
            self.notifier.show(self.notifier.t(hotkey.failed), error=True)

    # -- main loop --
    def stop(self) -> None:
        self.running = False

    def run(self) -> None:
        for hotkey in self.hotkeys:
            log.info("%s hotkey: %s", hotkey.name,
                     describe(hotkey.detector.combo, hotkey.detector.hold_seconds))
        try:
            self._loop()
        finally:
            if self.recorder is not None:
                self.recorder.stop()

    def _loop(self) -> None:
        ecodes = self.evdev.ecodes
        next_scan = next_sync = 0.0
        while self.running:
            now = time.monotonic()
            if now >= next_scan:
                self.scan()
                if self.recorder is not None:
                    self.recorder.check(now)
                next_scan = now + RESCAN_SECONDS
            wake = next_scan
            if self.fetcher is not None:
                if now >= next_sync:
                    self.pool.submit(self.fetcher.sync)
                    next_sync = now + SYNC_SECONDS
                wake = min(wake, next_sync)
            for hotkey in self.hotkeys:
                if hotkey.detector.deadline is not None:
                    wake = min(wake, hotkey.detector.deadline)
            by_fd = dict((dev.fd, path) for path, dev in self.devices.items())
            try:
                ready, _, _ = select.select(list(by_fd), [], [], max(0.0, wake - time.monotonic()))
            except (OSError, ValueError):
                ready = []
                next_scan = 0.0
            for fd in ready:
                path = by_fd[fd]
                dev = self.devices.get(path)
                if dev is None:
                    continue
                try:
                    for ev in dev.read():
                        now = time.monotonic()
                        if ev.type == ecodes.EV_KEY:
                            self._feed(lambda d: d.key_event(path, ev.code, ev.value, now))
                        elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_DROPPED:
                            # The kernel dropped events: resync the key state.
                            active = dev.active_keys()
                            self._feed(lambda d: d.sync_device(path, active, now))
                except BlockingIOError:
                    pass
                except OSError:
                    self._drop(path)
            now = time.monotonic()
            self._feed(lambda d: d.poll(now))
