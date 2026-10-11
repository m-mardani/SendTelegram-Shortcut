# hold2tg

Send the files or text you have selected to Telegram by **holding `Ctrl + Shift + S` for 5 seconds**. Built for Ubuntu (GNOME), on both X11 and Wayland.

- Files or folders selected in **Files (Nautilus)** are sent as they are (folders as `.zip`).
- Text **highlighted** in any other app is sent as a message. No need to press Ctrl+C.
- The hotkey and the hold time are configurable.
- Files also gets a **Send To Telegram** entry in its right-click menu.
- Messages go to a Telegram channel, or to your private chat with the bot.

## How it works

A small background service (a systemd user service) reads raw key events from `/dev/input`. When the hotkey has been held long enough, it decides what "the selection" is and uploads it through the Telegram Bot API:

- **A Files window has focus:** the files selected in that window. The bundled Nautilus extension keeps the service informed about the current selection.
- **Any other app has focus:** the highlighted text (the PRIMARY selection), read with `xclip` or `wl-paste`.

Files left selected in a window that does *not* have focus are never sent.

## Requirements

- Ubuntu or Debian with GNOME. Tested on Ubuntu 24.04 (Nautilus 46) on X11 and Wayland, and runs on Ubuntu 26.04 (Nautilus 50).
- Python 3.8 or newer (the system `python3`).
- A Telegram bot, plus a channel or a private chat for it to post to.

The installer pulls in the rest from apt: `python3-evdev`, `python3-requests`, `python3-socks`, `xclip`, `wl-clipboard`, `libnotify-bin`, and `python3-nautilus` when Nautilus is present.

## Installation

### 1. Create the bot

1. In Telegram, send `/newbot` to [@BotFather](https://t.me/BotFather) and copy the **token** it gives you.
2. Choose where the files should go:
   - **A channel:** add the bot to the channel as an **admin** with the "Post Messages" permission.
   - **Your private chat with the bot:** open the bot (`t.me/<your_bot>`) and press **Start**. A bot can only message you after you have started it.

### 2. Install

Run this as your **normal user, not as root**. The script calls `sudo` itself, and only for the apt packages and the keyboard-access rule.

```bash
git clone https://github.com/m-mardani/SendTelegram-Shortcut.git
cd SendTelegram-Shortcut
./install.sh
```

The installer:

- copies the app to `~/.local/share/hold2tg` and a `hold2tg` launcher to `~/.local/bin`
- creates `~/.config/hold2tg/config.ini` with mode `600` (an existing one is kept)
- installs the Nautilus extension and restarts Files (open Files windows will close)
- adds a udev rule that lets the logged-in user read keyboard events
- enables and starts `hold2tg.service`

| Option | Effect |
| --- | --- |
| `--input-group` | Grant keyboard access by adding you to the `input` group instead of the udev rule (needs a log out and back in). |
| `--no-system` | Skip everything that needs `sudo`. You then install the packages and grant keyboard access yourself. |

### 3. Configure

```bash
nano ~/.config/hold2tg/config.ini
```

```ini
[telegram]
bot_token = 123456789:AAE...
chat_id = -1001234567890
```

To find `chat_id`, post a message in the channel (or send one to the bot), then run:

```bash
hold2tg chats
```

```text
-1001234567890   channel    My Files
123456789        private    Your Name
```

| Destination | `chat_id` |
| --- | --- |
| Public channel | `@channel_username`, or its numeric id |
| Private channel | The negative number starting with `-100` |
| Your private chat with the bot | Your positive user id |

`hold2tg chats` only shows chats with recent activity, since Telegram keeps updates for 24 hours. If the list is empty, post a new message and run it again.

If Telegram is blocked on your network, route it through a proxy, for example a local v2ray/xray inbound:

```ini
proxy = socks5h://127.0.0.1:10808
```

Then check the setup and apply the config:

```bash
hold2tg test                        # sends a test message to the chat
hold2tg doctor                      # checks the whole setup
systemctl --user restart hold2tg    # needed after every config change
```

## Usage

| What you do | What is sent |
| --- | --- |
| Select files/folders in Files and hold the hotkey for 5 seconds | Those files, one message each (folders zipped) |
| Highlight text in a browser, terminal, editor… and hold the hotkey | That text (long text is split into several messages) |
| Right-click selected files in Files → **Send To Telegram** | Those files, immediately |

After 5 seconds a "Sending…" notification appears and you can let go of the keys. The result is reported in a second notification. Releasing the keys earlier sends nothing.

### Command line

```bash
hold2tg send-selection          # send the current selection now, no hotkey
hold2tg send-text "hello"       # or: echo hello | hold2tg send-text
hold2tg send-files a.pdf dir/   # send files or folders
hold2tg chats                   # list the chat ids the bot has seen
hold2tg test                    # send a test message
hold2tg doctor                  # diagnose the setup
hold2tg run                     # run the daemon in the foreground (normally done by systemd)
hold2tg -v <command>            # debug logging
```

## Configuration

Everything lives in `~/.config/hold2tg/config.ini`. Each option is also explained inside the file.

| Option | Default | Description |
| --- | --- | --- |
| `[telegram] bot_token` | (empty) | Token from @BotFather |
| `[telegram] chat_id` | (empty) | Where to send (see above) |
| `[telegram] proxy` | (empty) | `socks5h://…` or `http://…` |
| `[telegram] api_base` | `https://api.telegram.org` | Only for a self-hosted Bot API server or a reverse proxy |
| `[telegram] silent` | `false` | Post without a notification sound for members |
| `[hotkey] keys` | `ctrl+shift+s` | e.g. `ctrl+alt+t` or `super+f9` |
| `[hotkey] hold_seconds` | `5` | How long to hold the keys |
| `[behavior] notifications` | `true` | Desktop notifications |
| `[behavior] language` | `fa` | Notification language: `fa` or `en` |
| `[behavior] max_file_mb` | `50` | Larger files are skipped (the Bot API limit is 50 MB) |
| `[behavior] max_files` | `20` | If more are selected, nothing is sent |
| `[behavior] zip_folders` | `true` | Send folders as `.zip` (`false` skips them) |
| `[behavior] text_backend` | `auto` | How to read highlighted text: `auto`, `xclip`, `xsel` or `wl-paste` |

**Hotkey keys.** Modifiers are `ctrl`, `shift`, `alt` and `super`. Keys are `a`–`z`, `0`–`9`, `f1`–`f12`, `space`, `enter`, `tab`, `esc`, `insert`, `delete`, `home`, `end`, `pageup`, `pagedown`, the arrow keys (`up`, `down`, `left`, `right`), `print`, `pause` and common punctuation (`minus`, `equal`, `comma`, `dot`, `slash`, …). A hotkey needs at least one non-modifier key.

**Environment variables.**

| Variable | Effect |
| --- | --- |
| `HOLD2TG_CONFIG` | Use a different config file |
| `HOLD2TG_BOT_TOKEN` | Override `bot_token` |
| `HOLD2TG_CHAT_ID` | Override `chat_id` |

## Running in the background

`hold2tg.service` is a systemd **user** service tied to your desktop session:

- **After a reboot:** it starts automatically as soon as you log in to the desktop. It does not run before login, because it needs your session to read selections and show notifications.
- **If it crashes:** systemd restarts it after 3 seconds.
- **When you log out:** it stops, and starts again at the next login.

```bash
systemctl --user status hold2tg     # current state
journalctl --user -u hold2tg -f     # live log
systemctl --user restart hold2tg    # after a config change
```

## Things to know

- **Hotkey conflicts.** hold2tg only *listens* to the keys; it does not stop them from reaching the focused app. `Ctrl+Shift+S` is "Save As" in many apps (LibreOffice, VS Code, gedit) and the screenshot tool in Firefox, so that window opens while you hold the keys. The text is captured the instant the combo goes down, so the right thing is still sent. If this bothers you, choose a free combination such as `ctrl+alt+t` or `super+f9`.
- **Keyboard access.** On Wayland, reading `/dev/input` is the only way to detect a held key. The installer's udev rule gives the user sitting at the machine read access to keyboard events. As a consequence, any program running as your user can read them too, the same as membership in the `input` group. If you don't want that, skip the installation and bind `hold2tg send-selection` to a normal shortcut in Settings → Keyboard → Custom Shortcuts instead (no hold detection).
- **Keyboard layout doesn't matter.** The hotkey uses the physical key, so it works with any layout (English, Persian, …).
- **Files (Nautilus) only.** Selections in other file managers or on desktop icons are not detected.
- **Files are sent as documents**, so photos are not recompressed. Empty files and files over the size limit are skipped and listed in the notification.
- **Privacy.** The bot token stays in `config.ini` with mode `600` and is never written to logs. The text you send is not logged either.

## Troubleshooting

Start with:

```bash
hold2tg doctor
```

| Problem | Fix |
| --- | --- |
| `no readable keyboard` | Log out and back in. If that doesn't help: `./install.sh --input-group`, then log out and in again. |
| Text is sent but files are not | Run `nautilus -q` and reopen Files. `python3-nautilus` must be installed. |
| No **Send To Telegram** in the right-click menu | Same as above: `nautilus -q`. Files loads extensions only when it starts. |
| `chat not found` | The bot is not an admin of the channel, you haven't pressed Start in the private chat, or `chat_id` is wrong. |
| Network error / timeout | Set `proxy` in the config. |
| `Error writing /root/.config/hold2tg/config.ini` | The editor is running as root (for example after `sudo su`), so `~` means `/root`. Leave the root shell and edit the file as your normal user. |
| Highlighted text comes back empty (Wayland) | Try `text_backend = wl-paste`. |
| Config changes have no effect | `systemctl --user restart hold2tg` |

## Uninstall

```bash
./uninstall.sh            # keeps your config
./uninstall.sh --purge    # also deletes the config
```

## Project layout and tests

```text
hold2tg/hotkey.py      hold detection logic (no dependencies)
hold2tg/listener.py    background service; reads the keyboard with evdev
hold2tg/selection.py   reads highlighted text and selected files
hold2tg/telegram.py    Bot API client
hold2tg/sender.py      sends text/files, zips folders, enforces limits
hold2tg/config.py      config file loading and validation
hold2tg/notify.py      desktop notifications and their messages (fa/en)
hold2tg/cli.py         the hold2tg command
nautilus/              Files extension (extension API 3.0, 4.0 and 4.1)
systemd/  udev/        user service and keyboard-access rule
```

```bash
python3 -m unittest discover -s tests -t .
```

Automated tests cover the hold logic, the Telegram client (against a fake server), file and folder sending, and the service loop. The Files extension and text reading were tested by hand with real apps on Nautilus 46 and mutter 46 (Ubuntu 24.04), on both X11 and Wayland. Reading a real keyboard from `/dev/input` and sending to the real Telegram are not covered by the automated tests; after installing, check them with `hold2tg doctor` and `hold2tg test`.
