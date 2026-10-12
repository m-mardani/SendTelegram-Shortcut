"""Configuration: ~/.config/hold2tg/config.ini (INI so it works on any Python 3.8+)."""
from __future__ import annotations

import configparser
import os
import stat
from dataclasses import dataclass, field
from typing import List, Optional

from .hotkey import Combo, HotkeyError, parse_combo


class ConfigError(Exception):
    pass


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "hold2tg")


def config_path() -> str:
    return os.environ.get("HOLD2TG_CONFIG") or os.path.join(config_dir(), "config.ini")


def runtime_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    if not os.path.isdir(base):
        # Never a shared, guessable place like /tmp: this directory tells the
        # daemon which files to send. (Keep in sync with the Nautilus extension.)
        base = os.path.expanduser("~/.cache")
    path = os.path.join(base, "hold2tg")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def nautilus_state_path() -> str:
    return os.path.join(runtime_dir(), "nautilus.json")


def state_dir() -> str:
    """Kept across reboots: what hold2tg knows about the channel's messages."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    path = os.path.join(base, "hold2tg")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def received_dir() -> str:
    """Where files fetched from the channel wait to be pasted."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, "hold2tg", "received")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


@dataclass
class Config:
    bot_token: str = ""
    chat_id: str = ""
    proxy: str = ""
    api_base: str = "https://api.telegram.org"
    silent: bool = False
    hotkey: str = "capslock+c"
    hold_seconds: float = 5.0
    fetch_hotkey: str = "capslock+v"
    fetch_hold_seconds: float = 5.0
    translate_hotkey: str = "capslock+t"
    translate_hold_seconds: float = 5.0
    history_hotkey: str = "capslock+h"
    history_hold_seconds: float = 0.0
    history_size: int = 100
    translate_language: str = "fa"
    translate_second_language: str = "en"
    translate_proxy: str = ""  # empty = the [telegram] proxy, "none" = direct
    notifications: bool = True
    language: str = "en"
    max_file_mb: float = 50.0
    max_files: int = 20
    zip_folders: bool = True
    text_backend: str = "auto"
    path: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def combo(self) -> Combo:
        return parse_combo(self.hotkey)

    @property
    def fetch_combo(self) -> Optional[Combo]:
        """The hotkey that copies the newest channel message; None = disabled."""
        return parse_combo(self.fetch_hotkey) if self.fetch_hotkey else None

    @property
    def translate_combo(self) -> Optional[Combo]:
        """The hotkey that opens the translation box; None = disabled."""
        return parse_combo(self.translate_hotkey) if self.translate_hotkey else None

    @property
    def history_combo(self) -> Optional[Combo]:
        """The hotkey that shows the clipboard history; None = the history is off."""
        return parse_combo(self.history_hotkey) if self.history_hotkey else None

    @property
    def google_proxy(self) -> str:
        if self.translate_proxy.lower() in ("none", "direct", "off"):
            return ""
        return self.translate_proxy or self.proxy

    def require_telegram(self) -> None:
        if not self.bot_token or not self.chat_id:
            raise ConfigError(
                "bot_token / chat_id are not set — edit %s" % (self.path or config_path()))


def load(path: str = "") -> Config:
    path = path or config_path()
    cfg = Config(path=path)
    cp = configparser.ConfigParser(interpolation=None)
    if os.path.exists(path):
        try:
            cp.read(path, encoding="utf-8")
        except configparser.Error as e:
            raise ConfigError("cannot parse %s: %s" % (path, e))
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            cfg.warnings.append(
                "%s is readable by other users (mode %o); run: chmod 600 %s" % (path, mode, path))
    else:
        cfg.warnings.append("config file %s does not exist yet" % path)

    def get(section: str, key: str, default: str) -> str:
        return cp.get(section, key, fallback=default).strip()

    def get_bool(section: str, key: str, default: bool) -> bool:
        raw = get(section, key, "")
        if not raw:
            return default
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() in ("0", "false", "no", "off"):
            return False
        raise ConfigError("[%s] %s must be true or false, got %r" % (section, key, raw))

    def get_num(section: str, key: str, default: float) -> float:
        raw = get(section, key, "")
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            raise ConfigError("[%s] %s must be a number, got %r" % (section, key, raw))

    cfg.bot_token = os.environ.get("HOLD2TG_BOT_TOKEN") or get("telegram", "bot_token", "")
    cfg.chat_id = os.environ.get("HOLD2TG_CHAT_ID") or get("telegram", "chat_id", "")
    cfg.proxy = get("telegram", "proxy", "")
    cfg.api_base = get("telegram", "api_base", cfg.api_base).rstrip("/") or cfg.api_base
    cfg.silent = get_bool("telegram", "silent", cfg.silent)

    cfg.hotkey = get("hotkey", "keys", cfg.hotkey)
    cfg.hold_seconds = get_num("hotkey", "hold_seconds", cfg.hold_seconds)
    cfg.fetch_hotkey = get("hotkey", "fetch_keys", cfg.fetch_hotkey)
    cfg.fetch_hold_seconds = get_num("hotkey", "fetch_hold_seconds", cfg.fetch_hold_seconds)
    cfg.translate_hotkey = get("hotkey", "translate_keys", cfg.translate_hotkey)
    cfg.translate_hold_seconds = get_num("hotkey", "translate_hold_seconds",
                                         cfg.translate_hold_seconds)

    cfg.history_hotkey = get("hotkey", "history_keys", cfg.history_hotkey)
    cfg.history_hold_seconds = get_num("hotkey", "history_hold_seconds", cfg.history_hold_seconds)
    cfg.history_size = int(get_num("history", "size", cfg.history_size))

    cfg.translate_language = get("translate", "language", cfg.translate_language)
    cfg.translate_second_language = get("translate", "second_language",
                                        cfg.translate_second_language)
    cfg.translate_proxy = get("translate", "proxy", cfg.translate_proxy)

    cfg.notifications = get_bool("behavior", "notifications", cfg.notifications)
    cfg.language = get("behavior", "language", cfg.language).lower()
    cfg.max_file_mb = get_num("behavior", "max_file_mb", cfg.max_file_mb)
    cfg.max_files = int(get_num("behavior", "max_files", cfg.max_files))
    cfg.zip_folders = get_bool("behavior", "zip_folders", cfg.zip_folders)
    cfg.text_backend = get("behavior", "text_backend", cfg.text_backend).lower()

    try:
        cfg.combo
    except HotkeyError as e:
        raise ConfigError("[hotkey] keys: %s" % e)
    combos = {"keys": cfg.combo.groups}
    for key, prop in (("fetch_keys", "fetch_combo"), ("translate_keys", "translate_combo"),
                      ("history_keys", "history_combo")):
        try:
            combo = getattr(cfg, prop)
        except HotkeyError as e:
            raise ConfigError("[hotkey] %s: %s" % (key, e))
        if combo is None:
            continue
        for other, groups in combos.items():
            if set(combo.groups) == set(groups):
                raise ConfigError("[hotkey] %s must differ from %s" % (key, other))
        combos[key] = combo.groups
    if min(cfg.hold_seconds, cfg.fetch_hold_seconds, cfg.translate_hold_seconds,
           cfg.history_hold_seconds) < 0:
        raise ConfigError("[hotkey] hold_seconds cannot be negative")
    if not cfg.translate_language or not cfg.translate_second_language:
        raise ConfigError("[translate] language and second_language cannot be empty")
    if cfg.translate_language == cfg.translate_second_language:
        raise ConfigError("[translate] language and second_language must differ")
    if not 1 <= cfg.history_size <= 1000:
        raise ConfigError("[history] size must be between 1 and 1000")
    if cfg.text_backend not in ("auto", "xclip", "xsel", "wl-paste"):
        raise ConfigError("[behavior] text_backend must be auto, xclip, xsel or wl-paste")
    if cfg.language not in ("fa", "en"):
        raise ConfigError("[behavior] language must be fa or en")
    if cfg.max_files < 1:
        raise ConfigError("[behavior] max_files must be at least 1")
    return cfg
