"""Translating the highlighted text with Google Translate.

Text in any other language goes into the configured language (Persian by
default), and text already in that language goes into the second one
(English). It uses the endpoint Google's browser extension talks to: no API
key, and besides the translation it returns a word's other meanings and
example sentences. (The better-known ``client=gtx`` is refused with
"unusual traffic" from many VPN and proxy addresses.)

The box itself (popup.py) runs in a process of its own; this module has no
GTK in it.
"""
from __future__ import annotations

import html
import logging
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .notify import Notifier

log = logging.getLogger("hold2tg.translate")

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MAX_CHARS = 5000
MAX_EXAMPLES = 5
RTL = frozenset({"fa", "ar", "he", "iw", "ur", "ps", "ckb", "sd", "ug", "yi", "dv"})
_ARABIC_SCRIPT = frozenset({"fa", "ar", "ur", "ps", "ckb", "sd", "ug"})
_ARABIC_LETTER = re.compile("[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LETTER = re.compile(r"[^\W\d_]")
_BOLD = re.compile(r"(</?b>)")


class TranslateError(Exception):
    def __init__(self, message: str, blocked: bool = False):
        super().__init__(message)
        self.blocked = blocked  # Google refuses this network for now


@dataclass
class Result:
    source: str
    src_lang: str
    tgt_lang: str
    text: str
    pronunciation: str = ""  # of the source, for single words
    meanings: List[Tuple[str, List[str]]] = field(default_factory=list)  # (part of speech, words)
    examples: List[str] = field(default_factory=list)  # in the source language, <b> marks the word


def plain(example: str) -> str:
    return html.unescape(_BOLD.sub("", example))


def markup(example: str) -> str:
    """An example as Pango markup: Google's <b> kept, everything else escaped."""
    return "".join(part if _BOLD.fullmatch(part) else html.escape(html.unescape(part))
                   for part in _BOLD.split(example))


def looks_like(language: str, text: str) -> bool:
    """A cheap guess that saves a round trip: is the text mostly in the
    script of ``language``? Only known for Arabic-script languages."""
    if language not in _ARABIC_SCRIPT:
        return False
    letters = len(_LETTER.findall(text))
    return letters > 0 and len(_ARABIC_LETTER.findall(text)) * 2 > letters


def parse(source: str, data: Dict[str, Any], target: str) -> Result:
    sentences = data.get("sentences") or []
    text = "".join(s.get("trans") or "" for s in sentences).strip()
    pronunciation = next((s["src_translit"] for s in sentences if s.get("src_translit")), "")
    meanings = []
    for entry in data.get("dict") or []:
        words = [w for w in entry.get("terms") or [] if w][:8]
        if words:
            meanings.append((entry.get("pos") or "", words))
    examples = [e["text"] for e in (data.get("examples") or {}).get("example") or []
                if e.get("text")]
    for definition in data.get("definitions") or []:
        examples += [g["example"] for g in definition.get("entry") or [] if g.get("example")]
    seen, unique = set(), []
    for example in examples:
        key = plain(example).strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(example)
    return Result(source, data.get("src") or "", target, text, pronunciation,
                  meanings[:4], unique[:MAX_EXAMPLES])


class Translator:
    def __init__(self, language: str = "fa", second_language: str = "en", proxy: str = "",
                 ui_language: str = "fa", timeout: float = 15.0):
        import requests  # imported lazily so the pure parts stay testable

        self._requests = requests
        self.language = language
        self.second_language = second_language
        self.ui_language = ui_language  # names the parts of speech
        self.timeout = timeout
        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def _query(self, text: str, source: str, target: str, details: bool) -> Dict[str, Any]:
        params = [("client", "dict-chrome-ex"), ("sl", source), ("tl", target),
                  ("hl", self.ui_language), ("dj", "1"), ("dt", "t")]
        if details:
            params += [("dt", d) for d in ("bd", "ex", "md", "rm")]
        try:
            # POST: the text can be longer than a URL may be.
            resp = self.session.post(ENDPOINT, params=params, data={"q": text},
                                     timeout=(10, self.timeout))
        except self._requests.RequestException as e:
            raise TranslateError("network error: %s" % e)
        if resp.status_code == 429:
            raise TranslateError("Google refuses requests from this network for now", blocked=True)
        if resp.status_code != 200:
            raise TranslateError("Google Translate answered HTTP %d" % resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise TranslateError("unexpected answer from Google Translate")
        return data

    def translate(self, text: str) -> Result:
        text = text.strip()[:MAX_CHARS]
        guess = self.second_language if looks_like(self.language, text) else self.language
        data = self._query(text, "auto", guess, details=True)
        want = self.second_language if data.get("src") == self.language else self.language
        if want != guess:
            data = self._query(text, "auto", want, details=True)
        return parse(text, data, want)

    def translate_lines(self, lines: List[str], source: str, target: str) -> List[str]:
        """Translate several lines at once; [] if they do not come back one to one."""
        if not lines:
            return []
        data = self._query("\n".join(lines), source or "auto", target, details=False)
        out = "".join(s.get("trans") or "" for s in data.get("sentences") or []).split("\n")
        out = [line.strip() for line in out]
        return out if len(out) == len(lines) else []


class Popup:
    """Opens the translation box for the daemon, in a process of its own
    (GTK has no place inside the daemon). A new box replaces the last one."""

    def __init__(self, notifier: Notifier):
        self.notifier = notifier
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def show(self, text: str) -> None:
        if not text.strip():
            self.notifier.show(self.notifier.t("nothing"), error=True)
            return
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
            log.info("opening the translation box (%d chars)", len(text))
            # The text goes through stdin: argv is visible to every user.
            self._proc = subprocess.Popen([sys.executable, "-m", "hold2tg", "translate", "-"],
                                          stdin=subprocess.PIPE)
            try:
                self._proc.stdin.write(text.encode("utf-8"))
                self._proc.stdin.close()
            except OSError:
                log.warning("the translation box exited early")
