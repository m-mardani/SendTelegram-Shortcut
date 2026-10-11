"""Command line interface: `hold2tg <command>`."""
from __future__ import annotations

import argparse
import glob
import logging
import os
import shutil
import socket
import subprocess
import sys
from typing import List

from . import __version__, config as config_mod, selection
from .config import ConfigError
from .notify import Notifier
from .selection import Snapshot
from .sender import Sender
from .telegram import TelegramClient, TelegramError

NAUTILUS_EXT = "~/.local/share/nautilus-python/extensions/hold2tg_nautilus.py"


def _load():
    cfg = config_mod.load()
    notifier = Notifier(cfg.notifications, cfg.language)
    return cfg, notifier, Sender(cfg, notifier)


def cmd_run(args) -> int:
    from .listener import AlreadyRunning, Listener

    cfg, notifier, sender = _load()
    for w in cfg.warnings:
        logging.warning(w)
    try:
        listener = Listener(cfg, sender, notifier)
    except ImportError:
        logging.error("python3-evdev is missing: sudo apt install python3-evdev")
        return 1
    try:
        listener.acquire_lock()
        listener.run()
    except AlreadyRunning as e:
        logging.error("%s", e)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


def cmd_send_selection(args) -> int:
    cfg, _notifier, sender = _load()
    return 0 if sender.send(selection.capture(cfg.text_backend)) else 1


def cmd_send_text(args) -> int:
    _cfg, _notifier, sender = _load()
    text = " ".join(args.text) if args.text and args.text != ["-"] else sys.stdin.read()
    snap = Snapshot(kind="text", text=text) if text.strip() else Snapshot()
    return 0 if sender.send(snap) else 1


def cmd_send_files(args) -> int:
    _cfg, _notifier, sender = _load()
    files = [os.path.abspath(p) for p in args.paths]
    return 0 if sender.send(Snapshot(kind="files", files=files)) else 1


def _client(cfg) -> TelegramClient:
    cfg.require_telegram()
    return TelegramClient(cfg.bot_token, cfg.chat_id, cfg.api_base, cfg.proxy, cfg.silent)


def cmd_test(args) -> int:
    cfg, _notifier, _sender = _load()
    try:
        _client(cfg).send_text("hold2tg ✓ — test message from %s" % socket.gethostname())
    except TelegramError as e:
        print("✗ Telegram: %s" % e)
        return 1
    print("✓ test message sent to %s" % cfg.chat_id)
    return 0


def cmd_chats(args) -> int:
    """List chats the bot has recently seen — handy to find a private channel's id."""
    cfg, _notifier, _sender = _load()
    if not cfg.bot_token:
        raise ConfigError("bot_token is not set — edit %s" % cfg.path)
    client = TelegramClient(cfg.bot_token, cfg.chat_id or "0", cfg.api_base, cfg.proxy)
    try:
        updates = client.get_updates()
    except TelegramError as e:
        print("✗ Telegram: %s" % e)
        return 1
    seen = {}
    for upd in updates:
        for key in ("channel_post", "edited_channel_post", "message", "my_chat_member"):
            chat = (upd.get(key) or {}).get("chat")
            if chat:
                seen[chat["id"]] = chat
    if not seen:
        print("No chats yet. Add the bot to the channel as an admin, post any message "
              "in the channel, then run this again.")
        return 1
    for chat_id, chat in seen.items():
        name = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        print("%-16s %-10s %s" % (chat_id, chat.get("type", ""), name))
    return 0


def cmd_doctor(args) -> int:
    failures = 0

    def ok(msg):
        print("  ✓ " + msg)

    def warn(msg):
        print("  ! " + msg)

    def bad(msg):
        nonlocal failures
        failures += 1
        print("  ✗ " + msg)

    print("hold2tg %s" % __version__)
    print("Config")
    cfg = None
    try:
        cfg = config_mod.load()
        for w in cfg.warnings:
            warn(w)
        if os.path.exists(cfg.path):
            ok(cfg.path)
        ok("hotkey: hold %s for %g s" % (cfg.combo.label, cfg.hold_seconds))
        if cfg.bot_token:
            ok("bot_token is set")
        else:
            bad("bot_token is empty")
        if cfg.chat_id:
            ok("chat_id = %s" % cfg.chat_id)
        else:
            bad("chat_id is empty")
    except ConfigError as e:
        bad(str(e))

    print("Python modules")
    for mod, pkg, needed in (("evdev", "python3-evdev", True), ("requests", "python3-requests", True),
                             ("socks", "python3-socks",
                              bool(cfg and cfg.proxy.lower().startswith("socks")))):
        try:
            __import__(mod)
            ok(mod)
        except ImportError:
            (bad if needed else warn)("%s missing (sudo apt install %s)" % (mod, pkg))

    print("Desktop session")
    st = selection.session_type()
    (ok if st != "unknown" else bad)(
        "session type: %s%s" % (st, "" if st != "unknown" else
                                " (run this from inside your desktop session)"))
    tools = {"x11": ["xclip"], "wayland": ["wl-paste", "xclip"]}.get(st, ["xclip", "wl-paste"])
    have = [t for t in tools if shutil.which(t)]
    for t in tools:
        if shutil.which(t):
            ok(t)
        elif have or st == "wayland":
            warn("%s not installed (optional fallback)" % t)
    if not have:
        bad("no tool to read the text selection (sudo apt install xclip wl-clipboard)")
    if shutil.which("notify-send"):
        ok("notify-send")
    else:
        warn("notify-send not installed (sudo apt install libnotify-bin)")

    print("Keyboard access")
    try:
        import evdev
        readable, denied = [], 0
        codes = cfg.combo.all_codes if cfg else frozenset({29, 42, 31})
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                dev = evdev.InputDevice(path)
            except PermissionError:
                denied += 1
                continue
            except OSError:
                continue
            if set(dev.capabilities().get(evdev.ecodes.EV_KEY, [])) & codes:
                readable.append("%s (%s)" % (path, dev.name))
            dev.close()
        for r in readable:
            ok(r)
        if not readable:
            bad("no readable keyboard (%d device(s) denied). Re-run install.sh, or: "
                "sudo usermod -aG input $USER  and log out/in" % denied)
    except ImportError:
        bad("cannot check: evdev module missing")

    print("File manager (Nautilus)")
    ext = os.path.expanduser(NAUTILUS_EXT)
    if os.path.exists(ext):
        ok("extension installed")
        state = config_mod.nautilus_state_path()
        if os.path.exists(state):
            ok("extension is reporting selections")
        else:
            warn("no report from Nautilus yet — open a Files window and select a file "
                 "(after install: nautilus -q). Needs: sudo apt install python3-nautilus")
    else:
        warn("extension not installed — sending selected files will not work")

    print("Telegram")
    if cfg and cfg.bot_token:
        try:
            client = TelegramClient(cfg.bot_token, cfg.chat_id or "0", cfg.api_base, cfg.proxy,
                                    retries=1)
            me = client.get_me()
            ok("bot: @%s" % me.get("username"))
            if cfg.chat_id:
                chat = client.get_chat()
                ok("channel: %s (%s)" % (chat.get("title") or chat.get("username"),
                                         chat.get("type")))
        except TelegramError as e:
            bad(str(e) + ("" if cfg.proxy else
                          "  — if Telegram is blocked on your network, set `proxy` in the config"))
        except Exception as e:
            bad("%s: %s" % (type(e).__name__, e))
    else:
        warn("skipped (no bot_token)")

    print("Service")
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", "hold2tg.service"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        status = out.stdout.decode().strip() or "unknown"
        (ok if status == "active" else warn)("hold2tg.service: %s" % status)
    except (OSError, subprocess.TimeoutExpired):
        warn("systemctl --user not available")

    print("\n%s" % ("All good." if not failures else "%d problem(s) found." % failures))
    return 1 if failures else 0


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hold2tg",
        description="Hold a hotkey to send the selected files or text to a Telegram channel.")
    parser.add_argument("--version", action="version", version="hold2tg " + __version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="run the hotkey daemon (normally started by systemd)")
    sub.add_parser("send-selection", help="send whatever is selected right now")
    p = sub.add_parser("send-text", help="send text (argument, or stdin)")
    p.add_argument("text", nargs="*")
    p = sub.add_parser("send-files", help="send files or folders")
    p.add_argument("paths", nargs="+")
    sub.add_parser("test", help="send a test message to the channel")
    sub.add_parser("chats", help="list chat ids the bot has seen (to find your channel id)")
    sub.add_parser("doctor", help="check the whole setup")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    commands = {
        "run": cmd_run, "send-selection": cmd_send_selection, "send-text": cmd_send_text,
        "send-files": cmd_send_files, "test": cmd_test, "chats": cmd_chats,
        "doctor": cmd_doctor,
    }
    if not args.command:
        parser.print_help()
        return 2
    try:
        return commands[args.command](args)
    except ConfigError as e:
        print("hold2tg: %s" % e, file=sys.stderr)
        return 1
