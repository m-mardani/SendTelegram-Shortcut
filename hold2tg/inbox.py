"""A local record of the newest messages in the channel.

The Bot API cannot read a chat's history, so hold2tg keeps its own short list
of the channel's latest messages, fed from two places:

* what hold2tg posts itself, recorded right after sending (bots never receive
  updates about their own posts);
* what anyone else posts, which Telegram queues for the bot until getUpdates
  collects it. Telegram keeps that queue for 24 hours only, so the daemon
  collects it regularly.
"""
from __future__ import annotations

import fcntl
import json
import logging
import mimetypes
import os
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from . import config as config_mod

log = logging.getLogger("hold2tg.inbox")

KEEP = 10  # enough for a whole album (Telegram groups at most 10 items)
POST_KINDS = ("channel_post", "edited_channel_post", "message", "edited_message")
# Order matters: an animation message also carries a `document` copy of itself.
FILE_KINDS = ("animation", "document", "photo", "video", "audio", "voice", "video_note",
              "sticker")
FIXED_EXT = {"photo": ".jpg", "voice": ".ogg", "video_note": ".mp4"}
DEFAULT_EXT = {"video": ".mp4", "animation": ".mp4", "audio": ".mp3"}


def state_path() -> str:
    return os.path.join(config_mod.state_dir(), "inbox.json")


def same_chat(chat: Dict[str, Any], chat_id: str) -> bool:
    """Whether a Message's chat is the configured one (numeric id or @username)."""
    if str(chat.get("id")) == chat_id:
        return True
    username = chat.get("username") or ""
    return bool(username) and chat_id.startswith("@") and username.lower() == chat_id[1:].lower()


def update_chats(update: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """The chats an update is about."""
    for kind in POST_KINDS + ("my_chat_member",):
        chat = (update.get(kind) or {}).get("chat")
        if chat:
            yield chat


def _file_name(kind: str, obj: Dict[str, Any], date: int) -> str:
    if obj.get("file_name"):
        return obj["file_name"]
    if kind == "sticker":
        ext = ".webm" if obj.get("is_video") else ".tgs" if obj.get("is_animated") else ".webp"
    elif kind in FIXED_EXT:
        ext = FIXED_EXT[kind]
    else:
        mime = obj.get("mime_type") or ""
        ext = (mimetypes.guess_extension(mime) if mime else None) or DEFAULT_EXT.get(kind, "")
    # Named the way Telegram Desktop names unnamed media.
    return "%s_%s%s" % (kind, time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(date)), ext)


def to_entry(message: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a Message that can be copied: its file, or else its text."""
    date = int(message.get("date") or 0)
    file = None
    for kind in FILE_KINDS:
        obj = message.get(kind)
        if kind == "photo" and obj:
            obj = obj[-1]  # the largest size
        if obj:
            file = {"id": obj["file_id"], "uid": obj.get("file_unique_id") or obj["file_id"],
                    "name": _file_name(kind, obj, date), "size": int(obj.get("file_size") or 0)}
            break
    return {"id": int(message["message_id"]), "date": date,
            "group": message.get("media_group_id") or "",
            "text": "" if file else message.get("text") or "", "file": file}


class Inbox:
    def __init__(self, bot_token: str, chat_id: str, path: str = ""):
        # Offsets belong to the bot and messages to the chat: a new bot or a
        # new channel starts from scratch. Only the bot's id, never the secret.
        self.key = "%s|%s" % (bot_token.split(":", 1)[0], chat_id)
        self.chat_id = chat_id
        self.path = path or state_path()

    # -- storage (shared by the daemon and the command line) --
    @contextmanager
    def _lock(self) -> Iterator[None]:
        with open(self.path + ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if (isinstance(data, dict) and data.get("key") == self.key
                    and isinstance(data.get("messages"), list)):
                data.setdefault("offset", 0)
                data.setdefault("chats", {})
                return data
        except (OSError, ValueError):
            pass
        return {"key": self.key, "offset": 0, "messages": [], "chats": {}}

    def _write(self, data: Dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".inbox-")  # mode 600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    @staticmethod
    def _add(data: Dict[str, Any], message: Dict[str, Any]) -> None:
        entry = to_entry(message)
        if not entry["text"] and not entry["file"]:
            return  # service messages, polls, locations…: nothing to copy
        messages = [m for m in data["messages"] if m["id"] != entry["id"]]  # edits replace
        messages.append(entry)
        messages.sort(key=lambda m: m["id"])
        data["messages"] = messages[-KEEP:]

    # -- API --
    def record(self, message: Dict[str, Any]) -> None:
        """Remember a message hold2tg has just posted to the channel."""
        with self._lock():
            data = self._read()
            self._add(data, message)
            self._write(data)

    def sync(self, client) -> None:
        """Collect what was posted since the last time. This confirms the
        updates, so Telegram will not hand them out again."""
        with self._lock():
            data = self._read()
            try:
                for _page in range(50):
                    updates = client.get_updates(data["offset"])
                    for upd in updates:
                        data["offset"] = max(data["offset"], int(upd["update_id"]) + 1)
                        for chat in update_chats(upd):
                            data["chats"][str(chat["id"])] = chat
                        for kind in POST_KINDS:
                            message = upd.get(kind)
                            if message and same_chat(message.get("chat") or {}, self.chat_id):
                                self._add(data, message)
                    if len(updates) < 100:
                        break
            finally:
                # Saved even after an error: updates confirmed so far are gone
                # from Telegram's side.
                self._write(data)

    def latest(self) -> List[Dict[str, Any]]:
        """The newest message, together with the rest of its album if it has one."""
        messages = self._read()["messages"]
        if not messages:
            return []
        last = messages[-1]
        if last["group"]:
            return [m for m in messages if m["group"] == last["group"]]
        return [last]

    def seen_chats(self) -> List[Dict[str, Any]]:
        """Chats that updates collected by :meth:`sync` were about."""
        return list(self._read()["chats"].values())
