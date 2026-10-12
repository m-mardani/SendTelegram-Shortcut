#!/usr/bin/env bash
# Removes hold2tg. Your config (with the bot token) is kept unless --purge.
set -uo pipefail
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hold2tg"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hold2tg.service"
RULE=/etc/udev/rules.d/70-hold2tg-input.rules
SHELL_EXT_UUID=hold2tg-clipboard@m-mardani.github.io
SHELL_EXT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$SHELL_EXT_UUID"

systemctl --user disable --now hold2tg.service 2>/dev/null
rm -f "$UNIT"
systemctl --user daemon-reload 2>/dev/null
rm -rf "$HOME/.local/share/hold2tg" "$HOME/.local/bin/hold2tg"
rm -f "$HOME/.local/share/nautilus-python/extensions/hold2tg_nautilus.py"
# The clipboard history extension: off for good, then removed (the running
# shell lets go of it at the next login).
gnome-extensions disable "$SHELL_EXT_UUID" >/dev/null 2>&1
/usr/bin/python3 - "$SHELL_EXT_UUID" <<'PY' 2>/dev/null
import ast, subprocess, sys
uuid = sys.argv[1]
raw = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                     stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
items = [] if raw.startswith("@as") or not raw else list(ast.literal_eval(raw))
if uuid in items:
    items.remove(uuid)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                    str(items) if items else "@as []"])
PY
rm -rf "$SHELL_EXT_DIR"
# Caps Lock back to Caps Lock, if hold2tg made it a hotkey key (and only then).
/usr/bin/python3 - "${XDG_STATE_HOME:-$HOME/.local/state}/hold2tg/gnome.json" <<'PY' 2>/dev/null
import ast, json, subprocess, sys
try:
    added = json.load(open(sys.argv[1])).get("added_xkb_options", [])
except (OSError, ValueError):
    added = []
schema = "org.gnome.desktop.input-sources"
raw = subprocess.run(["gsettings", "get", schema, "xkb-options"], stdout=subprocess.PIPE,
                     universal_newlines=True).stdout.strip()
options = [] if raw.startswith("@as") or not raw else list(ast.literal_eval(raw))
kept = [o for o in options if o not in added]
if kept != options:
    subprocess.run(["gsettings", "set", schema, "xkb-options", str(kept) if kept else "@as []"])
    print("Caps Lock is Caps Lock again.")
PY
command -v nautilus >/dev/null 2>&1 && nautilus -q >/dev/null 2>&1
if [ -f "$RULE" ]; then
  echo "Removing $RULE (sudo)"
  sudo rm -f "$RULE" && sudo udevadm control --reload-rules \
    && sudo udevadm trigger --subsystem-match=input --action=change
fi
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$CONF_DIR" "${XDG_STATE_HOME:-$HOME/.local/state}/hold2tg" \
         "${XDG_CACHE_HOME:-$HOME/.cache}/hold2tg"
  command -v dconf >/dev/null 2>&1 && dconf reset -f /org/gnome/shell/extensions/hold2tg-clipboard/
  echo "hold2tg removed, including its config and the clipboard history."
else
  echo "hold2tg removed. Config and clipboard history kept (use --purge to delete them)."
fi
if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
  echo "Note: $USER is still in the 'input' group. To undo: sudo gpasswd -d $USER input"
fi
