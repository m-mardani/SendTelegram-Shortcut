"""Copies the newest message of the channel to the clipboard (the reverse of
the sender): its text as text, its file(s) as files to paste in Files."""
from __future__ import annotations

import logging
import os
import shutil
import threading
from typing import Any, Dict, List, Set, Tuple

from . import clipboard
from . import config as config_mod
from .clipboard import ClipboardError
from .config import Config, ConfigError
from .inbox import Inbox
from .notify import Notifier
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("hold2tg.fetcher")


def safe_name(name: str) -> str:
    """A name that came from Telegram, made safe as a single path component."""
    name = name.replace("/", "_").replace("\0", "").strip()
    if name in ("", ".", ".."):
        return "file"
    root, ext = os.path.splitext(name)
    if len(ext.encode("utf-8")) > 20:
        root, ext = name, ""
    while len((root + ext).encode("utf-8")) > 200:  # file systems allow 255 bytes
        root = root[:-1]
    return root + ext


class Fetcher:
    def __init__(self, cfg: Config, notifier: Notifier):
        self.cfg = cfg
        self.notifier = notifier
        self._lock = threading.Lock()  # one fetch at a time
        self._sync_failing = False

    def _client(self, retries: int = 3) -> TelegramClient:
        self.cfg.require_telegram()
        return TelegramClient(self.cfg.bot_token, self.cfg.chat_id, self.cfg.api_base,
                              self.cfg.proxy, retries=retries)

    def _inbox(self) -> Inbox:
        return Inbox(self.cfg.bot_token, self.cfg.chat_id)

    def sync(self) -> None:
        """Collect what others posted, quietly. The daemon calls this
        regularly, because Telegram keeps those posts for 24 hours only."""
        try:
            self._inbox().sync(self._client(retries=1))
        except ConfigError:
            return
        except Exception as e:
            if not self._sync_failing:  # say it once per outage, not every time
                log.warning("could not check the channel for new posts: %s", e)
            self._sync_failing = True
            return
        if self._sync_failing:
            log.info("checking the channel works again")
        self._sync_failing = False

    def fetch(self) -> bool:
        """Copy the newest message; reports the outcome to the user. True on full success."""
        n = self.notifier
        with self._lock:
            try:
                client = self._client()
            except ConfigError as e:
                n.show(n.t("no_config"), str(e), error=True)
                return False
            try:
                n.show(n.t("fetching"))
                inbox = self._inbox()
                inbox.sync(client)
                entries = inbox.latest()
                if not entries:
                    n.show(n.t("no_message"), n.t("no_message_hint"), error=True)
                    return False
                files = [e["file"] for e in entries if e["file"]]
                if files:
                    return self._copy_files(client, files)
                text = entries[-1]["text"]
                clipboard.copy_text(text)
                self._prune(set())
                log.info("copied the newest message (text, %d chars)", len(text))
                n.show(n.t("copied_text"))
                return True
            except (TelegramError, ClipboardError) as e:
                n.show(n.t("fetch_failed"), str(e), error=True)
                return False
            except Exception as e:  # never let a worker thread die silently
                log.exception("unexpected error while fetching")
                n.show(n.t("fetch_failed"), "%s: %s" % (type(e).__name__, e), error=True)
                return False

    def _copy_files(self, client: TelegramClient, files: List[Dict[str, Any]]) -> bool:
        n = self.notifier
        root = config_mod.received_dir()
        paths: List[str] = []
        failed: List[Tuple[str, str]] = []
        for f in files:
            folder = os.path.join(root, safe_name(f["uid"]))
            target = os.path.join(folder, safe_name(f["name"]))
            # Fetching the same message again reuses the earlier download.
            if not (os.path.isfile(target) and f["size"] in (0, os.path.getsize(target))):
                os.makedirs(folder, mode=0o700, exist_ok=True)
                try:
                    client.download(f["id"], target)
                except TelegramError as e:
                    too_big = "too big" in str(e).lower()
                    failed.append((f["name"], n.t("dl_too_big") if too_big else str(e)))
                    continue
            log.info("fetched file %s (%d bytes)", f["name"], os.path.getsize(target))
            paths.append(target)
        if paths:
            clipboard.copy_files(paths)
            self._prune(set(os.path.dirname(p) for p in paths))
        names = "\n".join(os.path.basename(p) for p in paths[:8])
        if not failed:
            n.show(n.t("copied_files", n=len(paths)), names)
            return True
        detail = "\n".join("%s: %s" % item for item in failed[:8])
        title = n.t("fetch_partial", n=len(paths), m=len(failed)) if paths else n.t("fetch_failed")
        n.show(title, detail, error=True)
        return False

    @staticmethod
    def _prune(keep: Set[str]) -> None:
        """Delete earlier downloads once the clipboard no longer points at them."""
        root = config_mod.received_dir()
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if path not in keep:
                shutil.rmtree(path, ignore_errors=True)
