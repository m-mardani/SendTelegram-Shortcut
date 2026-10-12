"""Command line interface: `hold2tg <command>`."""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
from typing import List

from . import __version__, config as config_mod, selection
from .config import ConfigError
from .fetcher import Fetcher
from .gnome import CAPS_OPTION, Shell, uses_capslock
from .history import History, HistoryBox, Recorder, history_path, preview, shell_history_path
from .hotkey import describe
from .inbox import Inbox, update_chats
from .notify import LANGUAGE_NAMES, Notifier
from .selection import Snapshot
from .sender import Sender
from .telegram import TelegramClient, TelegramError
from .translate import RTL, Popup, TranslateError, Translator, plain

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
    shell = Shell()
    problem = shell.keyboard(cfg)  # Caps Lock as a hotkey key needs a GNOME keyboard option
    if problem:
        logging.warning("%s", problem)
    if shell.sync(cfg):
        logging.info("settings passed on to the GNOME Shell extension")
    try:
        listener = Listener(cfg, sender, notifier, Fetcher(cfg, notifier), Popup(notifier),
                            HistoryBox(notifier, shell), Recorder(cfg.history_size, shell))
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


def cmd_fetch(args) -> int:
    cfg, notifier, _sender = _load()
    return 0 if Fetcher(cfg, notifier).fetch() else 1


def _translator(cfg) -> Translator:
    return Translator(cfg.translate_language, cfg.translate_second_language, cfg.google_proxy,
                      ui_language=cfg.language)


def cmd_translate(args) -> int:
    cfg, notifier, _sender = _load()
    if args.text == ["-"]:
        text = sys.stdin.read()
    elif args.text:
        text = " ".join(args.text)
    else:
        text = selection.read_primary_text(cfg.text_backend)
    if not text.strip():
        notifier.show(notifier.t("nothing"), error=True)
        return 1
    translator = _translator(cfg)
    if args.print:
        try:
            r = translator.translate(text)
        except TranslateError as e:
            print("✗ %s" % e, file=sys.stderr)
            return 1
        print("%s → %s\n%s" % (r.src_lang, r.tgt_lang, r.text))
        for pos, words in r.meanings:
            print("  %s: %s" % (pos, ", ".join(words)))
        for example in r.examples:
            print("  • %s" % plain(example))
        return 0
    try:
        from . import popup
    except (ImportError, ValueError) as e:  # no GTK 4 / libadwaita: a notification will do
        logging.warning("cannot open the translation box (%s); "
                        "sudo apt install gir1.2-adw-1 gir1.2-gtk-4.0 python3-gi", e)
        try:
            notifier.show(notifier.t("tr_title"), translator.translate(text).text, private=True)
        except TranslateError as e:
            notifier.show(notifier.t("translate_failed"), str(e), error=True)
            return 1
        return 0
    return popup.run(text, translator, notifier.strings,
                     LANGUAGE_NAMES.get(cfg.language, LANGUAGE_NAMES["en"]), cfg.language in RTL)


def _print_shell_history() -> int:
    try:
        with open(shell_history_path(), encoding="utf-8") as fh:
            items = json.load(fh).get("items") or []
    except (OSError, ValueError, AttributeError):
        items = []
    for i, item in enumerate(items, 1):
        text = item.get("text") if item.get("type") == "text" else "[picture]"
        print("%3d %s %s" % (i, "📌" if item.get("pinned") else " ", preview(str(text), 120)))
    return 0


def cmd_history(args) -> int:
    cfg, notifier, _sender = _load()
    shell = Shell()
    if shell.active():  # the GNOME Shell extension keeps the history
        if args.print:
            return _print_shell_history()
        if args.clear:
            ok = shell.call("Clear")
            print("✓ the clipboard history is cleared (pinned items stay)" if ok
                  else "✗ the clipboard extension did not answer")
            return 0 if ok else 1
        return 0 if shell.call("Show") else 1
    history = History(size=cfg.history_size)
    if args.clear:
        history.clear()
        print("✓ the clipboard history is cleared")
        return 0
    if args.print:
        for i, entry in enumerate(history.load(), 1):
            print("%3d  %s" % (i, preview(entry.text, 120)))
        return 0
    try:
        from . import historybox
    except (ImportError, ValueError) as e:
        logging.error("the history box needs GTK 4 and libadwaita (%s): "
                      "sudo apt install gir1.2-adw-1 gir1.2-gtk-4.0 python3-gi", e)
        notifier.show(notifier.t("history_failed"), error=True)
        return 1
    return historybox.run(history, notifier.strings, cfg.language == "fa")


def cmd_clipwatch(args) -> int:
    try:
        from . import clipwatch
    except (ImportError, ValueError) as e:
        logging.error("recording the clipboard needs GTK 4 (%s): "
                      "sudo apt install gir1.2-gtk-4.0 python3-gi", e)
        return 1
    return clipwatch.run(args.size)


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
    if cfg.chat_id:
        # The daemon collects (and so uses up) updates for the fetch hotkey,
        # but it remembers which chats they came from.
        for chat in Inbox(cfg.bot_token, cfg.chat_id).seen_chats():
            seen[chat["id"]] = chat
    for upd in updates:
        for chat in update_chats(upd):
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
        ok("hotkey: %s" % describe(cfg.combo, cfg.hold_seconds))
        if cfg.fetch_combo is not None:
            ok("fetch hotkey: %s" % describe(cfg.fetch_combo, cfg.fetch_hold_seconds))
        else:
            warn("fetch hotkey is off (fetch_keys is empty)")
        if cfg.translate_combo is not None:
            ok("translate hotkey: %s" % describe(cfg.translate_combo, cfg.translate_hold_seconds))
        else:
            warn("translate hotkey is off (translate_keys is empty)")
        if cfg.history_combo is not None:
            ok("clipboard history hotkey: %s (keeps the last %d texts)"
               % (describe(cfg.history_combo, cfg.history_hold_seconds), cfg.history_size))
        else:
            warn("clipboard history is off (history_keys is empty)")
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
    writers = [t for t in ({"x11": ["xclip"]}.get(st) or ["wl-copy", "xclip"]) if shutil.which(t)]
    if writers:
        ok("clipboard (for the fetch hotkey): %s" % writers[0])
    else:
        bad("no tool to write the clipboard (sudo apt install wl-clipboard xclip)")
    if shutil.which("notify-send"):
        ok("notify-send")
    else:
        warn("notify-send not installed (sudo apt install libnotify-bin)")

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        ok("translation box: GTK 4 + libadwaita")
    except (ImportError, ValueError):
        warn("translation box needs: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 "
             "(until then translations show as notifications)")

    if cfg and cfg.history_combo is not None:
        shell = Shell()
        if shell.active():
            ok("clipboard history: the GNOME Shell extension (instant panel, pastes, pins, pictures)")
        else:
            if shell.installed():
                warn("the clipboard extension is installed but not running yet: log out and "
                     "back in once (GNOME starts new extensions at login)")
            else:
                warn("the clipboard extension is not installed (re-run install.sh); "
                     "using hold2tg's own, slower history box")
            if os.environ.get("DISPLAY"):
                ok("fallback clipboard history: recorded through XWayland (DISPLAY=%s), "
                   "%d text(s) kept in %s" % (os.environ["DISPLAY"],
                                              len(History(size=cfg.history_size).load()),
                                              history_path()))
            else:
                bad("clipboard history cannot be recorded: no X display (DISPLAY is not set)")

    if cfg and any(uses_capslock(k) for k in (cfg.hotkey, cfg.fetch_hotkey,
                                              cfg.translate_hotkey, cfg.history_hotkey)):
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.input-sources",
                              "xkb-options"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if CAPS_OPTION in out.stdout.decode():
            ok("Caps Lock works as a hotkey key (GNOME keyboard option %s)" % CAPS_OPTION)
        else:
            bad("Caps Lock hotkeys need GNOME's %s keyboard option; restart the service "
                "(systemctl --user restart hold2tg) to turn it on" % CAPS_OPTION)
        if not Shell().active():
            warn("until the GNOME Shell extension runs, Caps Lock hotkeys also reach the "
                 "focused app")

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

    print("Google Translate")
    if cfg:
        try:
            r = _translator(cfg).translate("hello")
            ok("hello → %s%s" % (r.text, " (through the proxy)" if cfg.google_proxy else ""))
        except TranslateError as e:
            bad(str(e))
    else:
        warn("skipped (config not loaded)")

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
    sub.add_parser("fetch", help="copy the newest message of the channel to the clipboard")
    p = sub.add_parser("translate", help="translate text (argument, stdin as '-', or else the "
                                         "highlighted text) in the translation box")
    p.add_argument("-p", "--print", action="store_true", help="print it instead of the box")
    p.add_argument("text", nargs="*")
    p = sub.add_parser("history", help="show the clipboard history (the texts copied lately)")
    p.add_argument("-p", "--print", action="store_true", help="print it instead of the box")
    p.add_argument("--clear", action="store_true", help="forget the whole history")
    p = sub.add_parser("clipwatch", help="record copied texts for the history "
                                         "(started by the daemon)")
    p.add_argument("--size", type=int, default=100, help="how many texts to keep")
    sub.add_parser("test", help="send a test message to the channel")
    sub.add_parser("chats", help="list chat ids the bot has seen (to find your channel id)")
    sub.add_parser("doctor", help="check the whole setup")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    commands = {
        "run": cmd_run, "send-selection": cmd_send_selection, "send-text": cmd_send_text,
        "send-files": cmd_send_files, "fetch": cmd_fetch, "translate": cmd_translate,
        "history": cmd_history, "clipwatch": cmd_clipwatch,
        "test": cmd_test, "chats": cmd_chats,
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
