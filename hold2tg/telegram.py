"""Minimal Telegram Bot API client (sendMessage / sendDocument)."""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("hold2tg.telegram")

TEXT_LIMIT = 4096  # Telegram counts UTF-16 code units


class TelegramError(Exception):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def split_text(text: str, limit: int = TEXT_LIMIT) -> List[str]:
    """Split text into chunks of at most ``limit`` UTF-16 units, preferring to
    break at a newline, then at a space."""
    chunks: List[str] = []
    rest = text
    while rest:
        if utf16_len(rest) <= limit:
            chunks.append(rest)
            break
        # Largest prefix that fits (a prefix of n chars has >= n UTF-16 units).
        end = min(len(rest), limit)
        while utf16_len(rest[:end]) > limit:
            end -= 1
        cut = rest.rfind("\n", 0, end)
        if cut < end // 2:
            cut = rest.rfind(" ", 0, end)
        if cut < end // 2:
            cut = end
        chunk, rest = rest[:cut], rest[cut:]
        if cut != end:
            rest = rest[1:]  # drop the separator we split on
        if chunk.strip():
            chunks.append(chunk)
    return [c for c in chunks if c.strip()]


class _MultipartStream:
    """Streams a multipart/form-data body with one file part, so large files
    are not loaded into memory. Built by hand because Telegram ignores the
    RFC 2231 ``filename*=`` form older urllib3 emits for non-ASCII names."""

    def __init__(self, fields: Dict[str, str], file_field: str, filename: str, path: str):
        self.boundary = "hold2tg" + uuid.uuid4().hex
        b = self.boundary
        head = []
        for k, v in fields.items():
            head.append('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v))
        # Undecodable bytes in a filename become "?" instead of crashing.
        safe = filename.encode("utf-8", "replace").decode("utf-8")
        safe = safe.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
        head.append(
            '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n" % (b, file_field, safe))
        self._head = "".join(head).encode("utf-8")
        self._tail = ("\r\n--%s--\r\n" % b).encode("ascii")
        self._file = open(path, "rb")
        self._size = os.fstat(self._file.fileno()).st_size
        self._gen = self._chunks()
        self._buf = b""

    @property
    def content_type(self) -> str:
        return "multipart/form-data; boundary=" + self.boundary

    def __len__(self) -> int:
        return len(self._head) + self._size + len(self._tail)

    def _chunks(self) -> Iterator[bytes]:
        yield self._head
        remaining = self._size
        while remaining > 0:
            data = self._file.read(min(256 * 1024, remaining))
            if not data:
                raise IOError("file shrank while it was being uploaded")
            remaining -= len(data)
            yield data
        yield self._tail

    def __iter__(self) -> Iterator[bytes]:
        return self._gen

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._gen)
            except StopIteration:
                break
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        self._file.close()


class TelegramClient:
    def __init__(self, token: str, chat_id: str, api_base: str = "https://api.telegram.org",
                 proxy: str = "", silent: bool = False, retries: int = 3,
                 max_wait: float = 60.0):
        import requests  # imported lazily so the pure modules stay testable

        self._requests = requests
        self.token = token
        self.chat_id = chat_id
        self.api_base = api_base.rstrip("/")
        self.silent = silent
        self.retries = retries
        self.max_wait = max_wait
        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    # -- plumbing --
    def _scrub(self, text: Any) -> str:
        """Never let the bot token leak into logs or notifications."""
        return str(text).replace(self.token, "<token>") if self.token else str(text)

    def _call(self, method: str, data: Optional[Dict[str, Any]] = None,
              upload: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> Any:
        url = "%s/bot%s/%s" % (self.api_base, self.token, method)
        last = "unknown error"
        for attempt in range(1, self.retries + 1):
            stream = None
            try:
                if upload:
                    fields = dict((k, str(v)) for k, v in (data or {}).items())
                    stream = _MultipartStream(fields, upload["field"], upload["filename"], upload["path"])
                    resp = self.session.post(
                        url, data=stream, headers={"Content-Type": stream.content_type},
                        timeout=(15, max(timeout, 600)))
                else:
                    resp = self.session.post(url, data=data or {}, timeout=(15, timeout))
            except self._requests.RequestException as e:
                last = "network error: %s" % self._scrub(e)
                log.warning("%s (attempt %d/%d)", last, attempt, self.retries)
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, self.max_wait))
                continue
            finally:
                if stream is not None:
                    stream.close()
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code == 200 and body.get("ok"):
                return body.get("result")
            desc = body.get("description") or "HTTP %d" % resp.status_code
            code = int(body.get("error_code") or resp.status_code)
            last = self._scrub(desc)
            retry_after = (body.get("parameters") or {}).get("retry_after")
            if code == 429 and retry_after is not None and attempt < self.retries:
                wait = min(float(retry_after) + 0.5, self.max_wait)
                log.warning("rate limited by Telegram, waiting %.1fs", wait)
                time.sleep(wait)
                continue
            if code >= 500 and attempt < self.retries:
                time.sleep(min(2 ** attempt, self.max_wait))
                continue
            raise TelegramError(last, code)
        raise TelegramError(last)

    # -- API --
    def get_me(self) -> Dict[str, Any]:
        return self._call("getMe")

    def get_chat(self) -> Dict[str, Any]:
        return self._call("getChat", {"chat_id": self.chat_id})

    def get_updates(self) -> List[Dict[str, Any]]:
        return self._call("getUpdates", {"timeout": 0, "limit": 100})

    def send_text(self, text: str) -> int:
        """Send text (split into several messages if long). Returns the count."""
        chunks = split_text(text)
        for chunk in chunks:
            self._call("sendMessage", {
                "chat_id": self.chat_id,
                "text": chunk,
                "disable_notification": "true" if self.silent else "false",
            })
        return len(chunks)

    def send_document(self, path: str, filename: str = "") -> None:
        self._call(
            "sendDocument",
            {"chat_id": self.chat_id,
             "disable_notification": "true" if self.silent else "false"},
            upload={"field": "document", "path": path,
                    "filename": filename or os.path.basename(path)})
