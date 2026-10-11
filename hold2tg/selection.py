"""Reading "what is selected right now".

* Text  — the PRIMARY selection (whatever is highlighted with the mouse or
  keyboard, no Ctrl+C needed). Read with xclip/xsel on X11, and on Wayland
  with xclip through XWayland first, then wl-paste.
* Files — the Nautilus extension shipped with this project keeps a small JSON
  state file up to date: which files are selected and whether a Nautilus
  window currently has keyboard focus.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config as config_mod

log = logging.getLogger("hold2tg.selection")

@dataclass
class Snapshot:
    kind: str = "none"              # "files" | "text" | "none"
    files: List[str] = field(default_factory=list)
    text: str = ""


@dataclass
class NautilusState:
    active: bool = False
    files: List[str] = field(default_factory=list)
    updated: float = 0.0


def session_type(env: Optional[Dict[str, str]] = None) -> str:
    env = os.environ if env is None else env
    if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "unknown"


def _commands(backend: str, env: Dict[str, str]) -> List[List[str]]:
    table = {
        "xclip": ["xclip", "-o", "-selection", "primary"],
        "xsel": ["xsel", "--primary", "--output"],
        "wl-paste": ["wl-paste", "--primary", "--no-newline"],
    }
    if backend != "auto":
        return [table[backend]]
    order: List[str] = []
    if session_type(env) == "wayland":
        # xclip via XWayland does not need to grab focus, so it goes first.
        if env.get("DISPLAY"):
            order += ["xclip"]
        order += ["wl-paste"]
    else:
        order += ["xclip", "xsel"]
    return [table[name] for name in order if shutil.which(table[name][0])]


def read_primary_text(backend: str = "auto", timeout: float = 2.0) -> str:
    env = dict(os.environ)
    for cmd in _commands(backend, env):
        try:
            out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=timeout, env=env)
        except FileNotFoundError:
            log.warning("%s is not installed", cmd[0])
            continue
        except subprocess.TimeoutExpired:
            log.warning("%s timed out", cmd[0])
            continue
        if out.returncode == 0 and out.stdout:
            text = out.stdout.decode("utf-8", errors="replace")
            if text.strip():
                return text
    return ""


def read_nautilus_state(path: str = "") -> NautilusState:
    path = path or config_mod.nautilus_state_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return NautilusState()
    pid = raw.get("pid")
    if isinstance(pid, int) and not os.path.exists("/proc/%d" % pid):
        return NautilusState()  # Nautilus is gone; its last report is stale
    files = [f for f in raw.get("files", []) if isinstance(f, str) and os.path.exists(f)]
    return NautilusState(active=bool(raw.get("active")), files=files,
                         updated=float(raw.get("updated") or 0))


def capture(backend: str = "auto", state_path: str = "") -> Snapshot:
    """Decide what the user means by "the selection" right now.

    Files win only while a Files (Nautilus) window has the keyboard focus;
    anywhere else the highlighted text is used. A files selection left behind
    in an unfocused window is deliberately never sent.
    """
    nautilus = read_nautilus_state(state_path)
    if nautilus.active and nautilus.files:
        return Snapshot(kind="files", files=nautilus.files)
    text = read_primary_text(backend)
    if text.strip():
        return Snapshot(kind="text", text=text)
    return Snapshot()
