"""The background daemon: watches keyboards through evdev and fires the send.

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
from typing import Dict, Optional, Set

from . import config as config_mod
from . import selection
from .config import Config
from .hotkey import HoldDetector
from .notify import Notifier
from .sender import Sender

log = logging.getLogger("hold2tg.listener")

RESCAN_SECONDS = 5.0


class AlreadyRunning(Exception):
    pass


class Listener:
    def __init__(self, cfg: Config, sender: Sender, notifier: Notifier):
        import evdev  # lazy: only the daemon needs it

        self.evdev = evdev
        self.cfg = cfg
        self.sender = sender
        self.notifier = notifier
        self.combo = cfg.combo
        self.detector = HoldDetector(self.combo, cfg.hold_seconds)
        self.devices: Dict[str, "evdev.InputDevice"] = {}
        self.rejected: Set[str] = set()   # opened fine, but not a keyboard
        self.denied: Set[str] = set()     # no permission (yet)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hold2tg")
        self.pending: Optional[Future] = None
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
                if not keys & self.combo.all_codes:
                    dev.close()
                    self.rejected.add(path)
                    continue
                self.devices[path] = dev
                self._handle(self.detector.sync_device(path, dev.active_keys(), now))
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
        self._handle(self.detector.drop_device(path, time.monotonic()))

    # -- signals from the detector --
    def _handle(self, signal: Optional[str]) -> None:
        if signal == "arm":
            # Snapshot the selection the instant the combo goes down — before
            # the focused app reacts to the keys (a "Save As" dialog would
            # otherwise steal the focus and sometimes the selection).
            log.debug("armed")
            self.pending = self.pool.submit(selection.capture, self.cfg.text_backend)
        elif signal == "cancel":
            log.debug("cancelled")
            self.pending = None
        elif signal == "fire":
            pending, self.pending = self.pending, None
            log.info("hotkey held for %gs — sending", self.cfg.hold_seconds)
            self.pool.submit(self._send, pending)

    def _send(self, pending: Optional[Future]) -> None:
        try:
            snap = pending.result(timeout=10) if pending is not None else selection.capture(
                self.cfg.text_backend)
            self.sender.send(snap)
        except Exception:
            log.exception("send failed")
            self.notifier.show(self.notifier.t("failed"), error=True)

    # -- main loop --
    def stop(self) -> None:
        self.running = False

    def run(self) -> None:
        ecodes = self.evdev.ecodes
        log.info("hotkey: hold %s for %g seconds", self.combo.label, self.cfg.hold_seconds)
        next_scan = 0.0
        while self.running:
            now = time.monotonic()
            if now >= next_scan:
                self.scan()
                next_scan = now + RESCAN_SECONDS
            wake = next_scan
            if self.detector.deadline is not None:
                wake = min(wake, self.detector.deadline)
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
                            self._handle(self.detector.key_event(path, ev.code, ev.value, now))
                        elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_DROPPED:
                            # The kernel dropped events: resync the key state.
                            self._handle(self.detector.sync_device(path, dev.active_keys(), now))
                except BlockingIOError:
                    pass
                except OSError:
                    self._drop(path)
            self._handle(self.detector.poll(time.monotonic()))
