#!/usr/bin/env bash
# Removes hold2tg. Your config (with the bot token) is kept unless --purge.
set -uo pipefail
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hold2tg"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hold2tg.service"
RULE=/etc/udev/rules.d/70-hold2tg-input.rules

systemctl --user disable --now hold2tg.service 2>/dev/null
rm -f "$UNIT"
systemctl --user daemon-reload 2>/dev/null
rm -rf "$HOME/.local/share/hold2tg" "$HOME/.local/bin/hold2tg"
rm -f "$HOME/.local/share/nautilus-python/extensions/hold2tg_nautilus.py"
command -v nautilus >/dev/null 2>&1 && nautilus -q >/dev/null 2>&1
if [ -f "$RULE" ]; then
  echo "Removing $RULE (sudo)"
  sudo rm -f "$RULE" && sudo udevadm control --reload-rules \
    && sudo udevadm trigger --subsystem-match=input --action=change
fi
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$CONF_DIR"
  echo "hold2tg removed, including its config."
else
  echo "hold2tg removed. Config kept in $CONF_DIR (use --purge to delete it)."
fi
if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
  echo "Note: $USER is still in the 'input' group. To undo: sudo gpasswd -d $USER input"
fi
