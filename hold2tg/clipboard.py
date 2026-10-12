"""Putting text or files on the clipboard (the one Ctrl+V pastes).

wl-copy on Wayland, xclip on X11 (and on Wayland through XWayland when
wl-copy is missing). Files go up the way Files (Nautilus) copies them
itself, so Ctrl+V in a Files window or on the desktop pastes them.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
from typing import Dict, List

from .selection import session_type

TEXT_TYPE = "text/plain;charset=utf-8"
FILES_TYPE = "x-special/gnome-copied-files"


class ClipboardError(Exception):
    pass


def _command(mime: str, env: Dict[str, str]) -> List[str]:
    if session_type(env) == "wayland" and shutil.which("wl-copy"):
        # An explicit text type: left to guess, wl-copy may label a text that
        # looks like a script as application/x-shellscript.
        return ["wl-copy", "--type", mime]
    if env.get("DISPLAY") and shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
        return cmd if mime == TEXT_TYPE else cmd + ["-target", mime]
    raise ClipboardError("no tool to write the clipboard (sudo apt install wl-clipboard xclip)")


def _put(data: bytes, mime: str) -> None:
    env = dict(os.environ)
    cmd = _command(mime, env)
    # Both tools leave a child behind that serves the clipboard until
    # something else is copied. It keeps stdout/stderr open, so those must
    # not be pipes we would wait on.
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=err,
                                  timeout=5, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ClipboardError("%s: %s" % (cmd[0], e))
        if proc.returncode != 0:
            err.seek(0)
            detail = err.read().decode("utf-8", "replace").strip()
            raise ClipboardError("%s failed: %s" % (cmd[0], detail or "exit %d" % proc.returncode))


def copy_text(text: str) -> None:
    _put(text.encode("utf-8"), TEXT_TYPE)


def copy_files(paths: List[str]) -> None:
    uris = [pathlib.Path(os.path.abspath(p)).as_uri() for p in paths]
    _put(("copy\n" + "\n".join(uris)).encode("utf-8"), FILES_TYPE)
