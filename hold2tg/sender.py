"""Turns a selection snapshot into Telegram messages."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import zipfile
from typing import List, Tuple

from .config import Config, ConfigError
from .notify import Notifier
from .selection import Snapshot
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("hold2tg.sender")


def zip_folder(folder: str, dest_dir: str) -> str:
    folder = os.path.abspath(folder)
    name = os.path.basename(folder.rstrip(os.sep)) or "folder"
    target = os.path.join(dest_dir, name + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for root, _dirs, files in os.walk(folder, followlinks=False):
            for fname in sorted(files):
                full = os.path.join(root, fname)
                if os.path.isfile(full):
                    zf.write(full, os.path.join(name, os.path.relpath(full, folder)))
    return target


class Sender:
    def __init__(self, cfg: Config, notifier: Notifier):
        self.cfg = cfg
        self.notifier = notifier
        self._lock = threading.Lock()  # one send at a time

    def _client(self) -> TelegramClient:
        self.cfg.require_telegram()
        return TelegramClient(self.cfg.bot_token, self.cfg.chat_id, self.cfg.api_base,
                              self.cfg.proxy, self.cfg.silent)

    def send(self, snap: Snapshot) -> bool:
        """Send a snapshot; reports the outcome to the user. True on full success."""
        n = self.notifier
        if snap.kind == "none":
            n.show(n.t("nothing"), error=True)
            return False
        with self._lock:
            try:
                client = self._client()
            except ConfigError as e:
                n.show(n.t("no_config"), str(e), error=True)
                return False
            try:
                if snap.kind == "text":
                    n.show(n.t("sending"))
                    count = client.send_text(snap.text)
                    log.info("sent text (%d chars, %d message(s))", len(snap.text), count)
                    n.show(n.t("sent_text"))
                    return True
                return self._send_files(client, snap.files)
            except TelegramError as e:
                n.show(n.t("failed"), str(e), error=True)
                return False
            except Exception as e:  # never let a worker thread die silently
                log.exception("unexpected error while sending")
                n.show(n.t("failed"), "%s: %s" % (type(e).__name__, e), error=True)
                return False

    def _send_files(self, client: TelegramClient, paths: List[str]) -> bool:
        n = self.notifier
        if len(paths) > self.cfg.max_files:
            n.show(n.t("too_many", n=len(paths), max=self.cfg.max_files), error=True)
            return False
        limit = int(self.cfg.max_file_mb * 1024 * 1024)
        skipped: List[Tuple[str, str]] = []
        sent = 0
        tmp = tempfile.mkdtemp(prefix="hold2tg-")
        n.show(n.t("sending"))
        try:
            for path in paths:
                label = os.path.basename(path.rstrip(os.sep)) or path
                label = label.encode("utf-8", "replace").decode("utf-8")
                upload = path
                if not os.path.exists(path):
                    skipped.append((label, n.t("skip_missing")))
                    continue
                if os.path.isdir(path):
                    if not self.cfg.zip_folders:
                        skipped.append((label, n.t("skip_folder")))
                        continue
                    upload = zip_folder(path, tmp)
                elif not os.path.isfile(path):
                    skipped.append((label, n.t("skip_special")))
                    continue
                size = os.path.getsize(upload)
                if size == 0:
                    skipped.append((label, n.t("skip_empty")))
                elif size > limit:
                    skipped.append((label, n.t("skip_big", mb="%g" % self.cfg.max_file_mb)))
                else:
                    try:
                        client.send_document(upload)
                        sent += 1
                        log.info("sent file %s (%d bytes)", label, size)
                    except TelegramError as e:
                        skipped.append((label, str(e)))
                if upload != path:
                    os.remove(upload)  # free the temporary zip right away
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not skipped:
            n.show(n.t("sent_files", n=sent))
            return True
        detail = "\n".join("%s: %s" % item for item in skipped[:8])
        if len(skipped) > 8:
            detail += "\n…"
        n.show(n.t("partial", n=sent, m=len(skipped)), detail, error=True)
        return False
