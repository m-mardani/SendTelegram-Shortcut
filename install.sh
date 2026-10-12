#!/usr/bin/env bash
# hold2tg installer for Ubuntu/Debian (per-user install; asks for sudo only
# for the apt packages and the keyboard-access rule).
#
#   ./install.sh                 normal install
#   ./install.sh --input-group   grant keyboard access through the "input"
#                                group instead of a udev rule (needs re-login)
#   ./install.sh --no-system     skip everything that needs sudo
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/hold2tg"
BIN_DIR="$HOME/.local/bin"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hold2tg"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
EXT_DIR="$HOME/.local/share/nautilus-python/extensions"
SHELL_EXT_UUID=hold2tg-clipboard@m-mardani.github.io
SHELL_EXT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$SHELL_EXT_UUID"
RELOGIN=0
RULE=/etc/udev/rules.d/70-hold2tg-input.rules
PY=/usr/bin/python3

USE_GROUP=0; SYSTEM=1
for arg in "$@"; do
  case "$arg" in
    --input-group) USE_GROUP=1 ;;
    --no-system) SYSTEM=0 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not as root (it will call sudo itself)." >&2
  exit 1
fi

if [ "$SYSTEM" -eq 1 ]; then
  step "Installing system packages (sudo)"
  PKGS="python3 python3-evdev python3-requests python3-socks xclip wl-clipboard libnotify-bin"
  # gsettings, gdbus and glib-compile-schemas: the GNOME Shell extension and Caps Lock hotkeys.
  PKGS="$PKGS libglib2.0-bin"
  # The translation box (GTK 4 + libadwaita).
  PKGS="$PKGS python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
  # The Nautilus extension needs python3-nautilus; only where Nautilus exists.
  if command -v nautilus >/dev/null 2>&1; then PKGS="$PKGS python3-nautilus"; fi
  # shellcheck disable=SC2086
  sudo apt-get install -y --no-install-recommends $PKGS
fi

step "Installing hold2tg into $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$CONF_DIR" "$UNIT_DIR" "$EXT_DIR"
rm -rf "$APP_DIR/hold2tg"
cp -r "$SRC/hold2tg" "$APP_DIR/hold2tg"
find "$APP_DIR/hold2tg" -name '__pycache__' -type d -prune -exec rm -rf {} +
cp "$SRC/README.md" "$SRC/config.example.ini" "$APP_DIR/"

cat > "$BIN_DIR/hold2tg" <<LAUNCHER
#!/bin/sh
# Uses the system Python on purpose, so the apt packages are always found.
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec $PY -m hold2tg "\$@"
LAUNCHER
chmod +x "$BIN_DIR/hold2tg"

if [ ! -f "$CONF_DIR/config.ini" ]; then
  install -m 600 "$SRC/config.example.ini" "$CONF_DIR/config.ini"
  note "created $CONF_DIR/config.ini"
else
  chmod 600 "$CONF_DIR/config.ini"
  note "kept your existing $CONF_DIR/config.ini"
fi

step "Installing the Files (Nautilus) extension"
cp "$SRC/nautilus/hold2tg_nautilus.py" "$EXT_DIR/"
if command -v nautilus >/dev/null 2>&1; then
  nautilus -q >/dev/null 2>&1 || true
  note "done — Files was restarted so it loads the extension"
else
  warn "Nautilus not found; sending selected files needs GNOME Files"
fi

if [ "$SYSTEM" -eq 1 ]; then
  step "Granting access to keyboard events (sudo)"
  if [ "$USE_GROUP" -eq 1 ]; then
    sudo usermod -aG input "$USER"
    warn "added $USER to the 'input' group — log out and back in for it to apply"
  else
    sudo install -m 644 "$SRC/udev/70-hold2tg-input.rules" "$RULE"
    sudo udevadm control --reload-rules || warn "could not reload udev rules"
    sudo udevadm trigger --subsystem-match=input --action=change || warn "udev trigger failed"
    command -v udevadm >/dev/null 2>&1 && udevadm settle --timeout=5 || true
    note "installed $RULE"
  fi
fi

step "Installing the clipboard history (GNOME Shell extension)"
SHELL_MAJOR=$(gnome-shell --version 2>/dev/null | grep -oE '[0-9]+' | head -1)
if [ -z "$SHELL_MAJOR" ]; then
  note "GNOME Shell not found: hold2tg's own clipboard history box is used"
elif [ "$SHELL_MAJOR" -lt 48 ]; then
  note "GNOME $SHELL_MAJOR is older than the extension needs (48): hold2tg's own box is used"
elif ! command -v glib-compile-schemas >/dev/null 2>&1 || ! command -v gsettings >/dev/null 2>&1; then
  warn "glib-compile-schemas/gsettings missing (sudo apt install libglib2.0-bin): extension skipped"
else
  rm -rf "$SHELL_EXT_DIR"
  mkdir -p "$(dirname "$SHELL_EXT_DIR")"
  cp -r "$SRC/gnome-shell/$SHELL_EXT_UUID" "$SHELL_EXT_DIR"
  glib-compile-schemas "$SHELL_EXT_DIR/schemas"
  if gnome-extensions info "$SHELL_EXT_UUID" 2>/dev/null | grep -q "State: ACTIVE"; then
    note "installed; it is running already (an update takes effect at your next login)"
  else
    # A running shell only finds a new extension when it starts: enable it for the next login.
    $PY - "$SHELL_EXT_UUID" <<'PY'
import ast, subprocess, sys
uuid = sys.argv[1]
raw = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                     stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
items = [] if raw.startswith("@as") or not raw else list(ast.literal_eval(raw))
if uuid not in items:
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                    str(items + [uuid])], check=True)
PY
    RELOGIN=1
    note "installed and enabled for your next login (until then hold2tg's own box is used)"
  fi
  if [ "$(gsettings get org.gnome.shell disable-user-extensions 2>/dev/null)" = "true" ]; then
    warn "extensions are turned off in GNOME (Extensions app → Extensions switch)"
  fi
fi

step "Starting the background service"
cp "$SRC/systemd/hold2tg.service" "$UNIT_DIR/hold2tg.service"
if systemctl --user daemon-reload 2>/dev/null; then
  systemctl --user enable hold2tg.service >/dev/null 2>&1 || true
  systemctl --user restart hold2tg.service || warn "service did not start; see: journalctl --user -u hold2tg"
  note "enabled hold2tg.service (starts with your desktop session)"
else
  warn "systemctl --user is not available here; start it manually with: hold2tg run"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not in your PATH yet — log out/in, or use the full path $BIN_DIR/hold2tg" ;;
esac

if [ "$USE_GROUP" -eq 0 ] && [ "$SYSTEM" -eq 1 ]; then
  if ! "$BIN_DIR/hold2tg" doctor 2>/dev/null | grep -q '/dev/input/event'; then
    warn "the keyboard is not readable yet. If 'hold2tg doctor' still says so after"
    warn "a re-login, run:  ./install.sh --input-group   and log out/in."
  fi
fi

cat <<DONE

Installed. What is left:
  1. Put your bot token and channel in:  $CONF_DIR/config.ini
  2. systemctl --user restart hold2tg    # after every config change
  3. hold2tg test      # sends a test message to the channel
  4. hold2tg doctor    # checks everything

The hotkeys (defaults; change them in the config):
  hold Caps Lock + C  (5 s)   send the selected files or highlighted text to Telegram
  hold Caps Lock + V  (5 s)   copy the channel's newest message to the clipboard
  hold Caps Lock + T  (5 s)   translate the highlighted text
  press Caps Lock + H         the clipboard history

Caps Lock now works as the hotkeys' modifier and no longer turns on capitals
(Shift still does). Pick other hotkeys in the config to get it back.
DONE
if [ "$RELOGIN" -eq 1 ]; then
  warn "Log out and back in once, so GNOME starts hold2tg's extension: until then the Caps Lock"
  warn "hotkeys also reach the app you are in, and the clipboard history is the slower box."
fi
