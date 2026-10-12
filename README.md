# hold2tg

Send the files or text you have selected to Telegram by **holding `Caps Lock + C` for 5 seconds**. Built for Ubuntu (GNOME), on both X11 and Wayland.

- Files or folders selected in **Files (Nautilus)** are sent as they are (folders as `.zip`).
- Text **highlighted** in any other app is sent as a message. No need to press Ctrl+C.
- The hotkeys and the hold times are configurable. Caps Lock works as their modifier: it no longer turns on capitals (Shift still does), and the keys held with it never reach the app you are in.
- Files also gets a **Send To Telegram** entry in its right-click menu.
- **Holding `Caps Lock + V` for 5 seconds** works the other way: it copies the newest message of the channel to your clipboard. Text is copied as text. Files (PDF, zip, photos, anything) are downloaded so that Ctrl+V in Files pastes them.
- **Holding `Caps Lock + T` for 5 seconds** opens a small box with the translation of the highlighted text, through Google Translate. Text in any language is translated into Persian, with a word's other meanings and example sentences (each with its Persian translation). Persian text is translated into English.
- **Pressing `Caps Lock + H`** shows your clipboard history, like `Win + V` on Windows: the texts and pictures you copied lately, newest first, in a panel in the middle of the screen. It opens at once. Click one (or use the arrow keys and Enter) and it is pasted into the window you were in. Type to search, pin the ones to keep, delete single ones, or clear all but the pinned.
- Messages go to a Telegram channel, or to your private chat with the bot.

## How it works

A small background service (a systemd user service) reads raw key events from `/dev/input`. When the hotkey has been held long enough, it decides what "the selection" is and uploads it through the Telegram Bot API:

- **A Files window has focus:** the files selected in that window. The bundled Nautilus extension keeps the service informed about the current selection.
- **Any other app has focus:** the highlighted text (the PRIMARY selection), read with `xclip` or `wl-paste`.

Files left selected in a window that does *not* have focus are never sent.

The fetch hotkey (`Caps Lock + V`) copies the channel's newest message. The Bot API cannot read a channel's history, so hold2tg keeps its own short record of the newest messages. Its own posts are recorded when they are sent. Posts by anyone else, for example from your phone, reach the bot as updates, which the service collects every 10 minutes and again when you press the hotkey. Files are downloaded to `~/.cache/hold2tg/received/` and put on the clipboard the same way Files copies them, then pasted with Ctrl+V. A photo album or a group of files sent together comes back as all of its files.

The translate hotkey (`Caps Lock + T`) reads the highlighted text the moment the keys go down, and after 5 seconds opens the translation box (a small GTK window) in a process of its own. The box asks Google Translate, using the endpoint Google's browser extension uses (no API key needed), and lists other meanings and examples when the text is a single word. Press Escape to close it. The button at the top copies the translation.

The clipboard history (`Caps Lock + H`) is a GNOME Shell extension (`gnome-shell/hold2tg-clipboard@m-mardani.github.io`). Living inside the shell, it sees every copy, opens its panel at once and can paste into the window you were in (with Ctrl+V, or Ctrl+Shift+V in a terminal). It keeps texts and pictures up to 4 MB in `~/.local/state/hold2tg/shell-clipboard.json` and `shell-clipboard-images/`. Each time the service starts, it passes its settings on to the extension: the shortcut (`history_keys`), the language and the size (`[history] size`). While the extension runs, the service leaves the clipboard to it.

**Caps Lock as a modifier.** To the kernel and to the apps, Caps Lock is no modifier: holding Caps Lock + C would type "C" into the focused window over and over. So when a hotkey uses `capslock`, hold2tg turns on GNOME's `caps:hyper` keyboard option, which makes Caps Lock a modifier of its own (Hyper, separate from the Super key, so no clash with GNOME's Super shortcuts). The extension then takes those hotkeys from the apps: GNOME keeps them, nothing is typed, and hold2tg still times the hold by reading the keyboard itself. The keyboard layout does not matter (it works the same with the Persian layout). If no hotkey uses Caps Lock any more, hold2tg turns the option off again, but only if it was hold2tg that turned it on.

Without the extension (GNOME older than 48, or before the first log out and back in after installing) the service falls back on its own, slower history: a helper (`hold2tg clipwatch`, GTK through XWayland) records copied texts in `~/.local/state/hold2tg/clipboard.json`, and the hotkey opens a GTK window that copies the text you pick (paste it yourself). The extension takes over that history the first time it starts.

## Requirements

- Ubuntu or Debian with GNOME. Tested on Ubuntu 24.04 (Nautilus 46) on X11 and Wayland, and runs on Ubuntu 26.04 (Nautilus 50).
- Python 3.8 or newer (the system `python3`).
- For the translation box: GTK 4 and libadwaita 1.4 or newer (Ubuntu 24.04 and later).
- For the clipboard history panel: GNOME 48 or newer (Ubuntu 25.04 and later). On older GNOME the fallback box is used; it needs libadwaita 1.5 and XWayland (standard on GNOME).
- A Telegram bot, plus a channel or a private chat for it to post to.

The installer pulls in the rest from apt: `python3-evdev`, `python3-requests`, `python3-socks`, `xclip`, `wl-clipboard`, `libnotify-bin`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, and `python3-nautilus` when Nautilus is present.

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
- installs and enables the clipboard history extension for GNOME Shell. GNOME only starts a newly installed extension at login, so **log out and back in once** after the first install.
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
| Hold `Caps Lock + V` for 5 seconds | Nothing is sent. The channel's newest message goes to the clipboard: paste text anywhere, or paste files into a Files window or the desktop with Ctrl+V |
| Highlight text and hold `Caps Lock + T` for 5 seconds | Nothing is sent. A box shows the translation: into Persian, or into English when the text is Persian |
| Press `Caps Lock + H` | Nothing is sent. The clipboard history opens: click an item (or arrow keys and Enter) to paste it, type to search, the pin keeps an item, the bin deletes one, **Clear all** keeps only the pinned. Escape, a click outside or the shortcut again closes it |

After 5 seconds a "Sending…" notification appears and you can let go of the keys. The result is reported in a second notification. Releasing the keys earlier sends nothing. `Caps Lock + V` and `Caps Lock + T` work the same way: after 5 seconds the "Fetching…" notification or the translation box appears, and releasing earlier does nothing.

### Command line

```bash
hold2tg send-selection          # send the current selection now, no hotkey
hold2tg send-text "hello"       # or: echo hello | hold2tg send-text
hold2tg send-files a.pdf dir/   # send files or folders
hold2tg fetch                   # copy the channel's newest message to the clipboard
hold2tg translate               # translation box for the highlighted text
hold2tg translate -p "word"     # print the translation of a text instead
hold2tg history                 # open the clipboard history
hold2tg history -p              # print the clipboard history instead
hold2tg history --clear         # clear the clipboard history (pinned items stay)
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
| `[hotkey] keys` | `capslock+c` | e.g. `super+alt+c` or `super+f9` |
| `[hotkey] hold_seconds` | `5` | How long to hold the keys |
| `[hotkey] fetch_keys` | `capslock+v` | Copies the newest channel message to the clipboard. Empty turns it off |
| `[hotkey] fetch_hold_seconds` | `5` | How long to hold the fetch keys. `0` acts on the press itself |
| `[hotkey] translate_keys` | `capslock+t` | Opens the translation box. Empty turns it off |
| `[hotkey] translate_hold_seconds` | `5` | How long to hold the translate keys |
| `[hotkey] history_keys` | `capslock+h` | Shows the clipboard history. Empty turns it off, and nothing is recorded |
| `[hotkey] history_hold_seconds` | `0` | Only for the fallback box; the extension opens on the press |
| `[history] size` | `100` | How many copies to keep, pinned ones besides (the extension keeps 10 to 500) |
| `[translate] language` | `fa` | Text in other languages is translated into this one |
| `[translate] second_language` | `en` | Text already in `language` is translated into this one |
| `[translate] proxy` | (empty) | Proxy for Google Translate. Empty uses the `[telegram]` proxy, `none` connects directly |
| `[behavior] notifications` | `true` | Desktop notifications |
| `[behavior] language` | `en` | Language of the notifications, the boxes and the clipboard panel: `en` or `fa` |
| `[behavior] max_file_mb` | `50` | Larger files are skipped (the Bot API limit is 50 MB) |
| `[behavior] max_files` | `20` | If more are selected, nothing is sent |
| `[behavior] zip_folders` | `true` | Send folders as `.zip` (`false` skips them) |
| `[behavior] text_backend` | `auto` | How to read highlighted text: `auto`, `xclip`, `xsel` or `wl-paste` |

**Hotkey keys.** Modifiers are `ctrl`, `shift`, `alt`, `super` and `capslock` (also `caps`). Keys are `a`–`z`, `0`–`9`, `f1`–`f12`, `space`, `enter`, `tab`, `esc`, `insert`, `delete`, `home`, `end`, `pageup`, `pagedown`, the arrow keys (`up`, `down`, `left`, `right`), `print`, `pause` and common punctuation (`minus`, `equal`, `comma`, `dot`, `slash`, …). A hotkey needs at least one non-modifier key.

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

- **Caps Lock no longer turns on capitals** while a hotkey uses it (Shift still gives capitals). For Caps Lock back, set the hotkeys to other keys, for example `super+alt+c`, and restart the service; hold2tg then turns GNOME's `caps:hyper` option off again. If Caps Lock already has another role in GNOME (say `caps:escape`), hold2tg says so and leaves it alone.
- **Hotkey conflicts.** With Caps Lock hotkeys there are none: GNOME keeps those keys from the apps once the extension runs (until the first log out and back in after installing, they also reach the focused app). With other combinations hold2tg only *listens*: if you pick one an app uses (for example `Ctrl+Shift+S`, "Save As" in LibreOffice and VS Code), that app reacts while you hold the keys. The text is captured the instant the combo goes down, so the right thing is still sent.
- **Keyboard access.** On Wayland, reading `/dev/input` is the only way to detect a held key. The installer's udev rule gives the user sitting at the machine read access to keyboard events. As a consequence, any program running as your user can read them too, the same as membership in the `input` group. If you don't want that, skip the installation and bind `hold2tg send-selection` to a normal shortcut in Settings → Keyboard → Custom Shortcuts instead (no hold detection).
- **Keyboard layout doesn't matter.** The hotkey uses the physical key, so it works with any layout (English, Persian, …).
- **Files (Nautilus) only.** Selections in other file managers or on desktop icons are not detected.
- **Files are sent as documents**, so photos are not recompressed. Empty files and files over the size limit are skipped and listed in the notification.
- **What the fetch hotkey can see.** hold2tg knows posts made by itself on this computer, and posts made by anyone else since it was set up. Telegram keeps the posts of others waiting for the bot for 24 hours only, so a post is missed if this computer stays off (or you stay logged out) for more than a day after it was made. Posts made by the same bot from another computer are not visible at all, because bots receive nothing about their own posts. A deleted post is still copied, since bots are not told about deletions.
- **Downloads are limited to 20 MB** by the Bot API. Larger files are listed in the notification and not copied.
- **One bot, one program.** The service collects the bot's updates and confirms them, so they are gone for any other program reading the same bot with `getUpdates`. Give hold2tg a bot of its own. `hold2tg chats` still lists the chats it has collected.
- **Translations go through Google.** The highlighted text is sent to Google Translate, through the `[telegram]` proxy if you set one. Google sometimes refuses an address it suspects of automated use (often a VPN or proxy exit). The box then says so; try again later or use another proxy.
- **Privacy.** The bot token stays in `config.ini` with mode `600` and is never written to logs. The text you send or translate is not logged either. For the fetch hotkey, the newest 10 messages of the channel (their text and file references) are kept in `~/.local/state/hold2tg/inbox.json` with mode `600`.
- **The clipboard history is kept on disk** in `~/.local/state/hold2tg/` (only you can read it), so it survives a restart. Copies that a password manager marks as secret (KeePassXC and others do) are skipped, but a password copied from an ordinary text is recorded like any other text: delete it in the panel, or run `hold2tg history --clear`. Copied files are not recorded. The extension keeps pictures up to 4 MB and texts up to 200,000 characters; the fallback keeps texts only.

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
| `No message from the channel yet` on `Caps Lock + V` | hold2tg has not seen a post since it was set up. Post something in the channel and press it again. |
| `Conflict: … getUpdates` | Another program is reading the same bot. Use a separate bot for hold2tg. |
| The translation box says Google refuses requests from this network | Google has flagged the address you reach it from. Wait a while, or set `[translate] proxy` (or `none` to go direct). `hold2tg doctor` tests it. |
| Translations come as notifications, not in a box | GTK 4 or libadwaita is missing: `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1` |
| `Caps Lock + H` opens the old, slower box (or nothing), or Caps Lock hotkeys type letters into the app | The extension is not running yet: log out and back in once. `hold2tg doctor` says whether it runs; `gnome-extensions info hold2tg-clipboard@m-mardani.github.io` shows its state. |
| The clipboard history stays empty | With the extension, check it is enabled (above). With the fallback, `hold2tg doctor` must show the X display, and the service log says whether the recorder runs: `journalctl --user -u hold2tg` |
| Config changes have no effect | `systemctl --user restart hold2tg` |

## Uninstall

```bash
./uninstall.sh            # keeps your config and the clipboard history
./uninstall.sh --purge    # also deletes them
```

## Project layout and tests

```text
hold2tg/hotkey.py      hold detection logic (no dependencies)
hold2tg/listener.py    background service; reads the keyboard with evdev
hold2tg/selection.py   reads highlighted text and selected files
hold2tg/telegram.py    Bot API client
hold2tg/sender.py      sends text/files, zips folders, enforces limits
hold2tg/config.py      config file loading and validation
hold2tg/notify.py      desktop notifications and their messages (en/fa)
hold2tg/history.py     clipboard history: the extension's settings, and the fallback's
                       storage, recorder and box processes
hold2tg/clipwatch.py   fallback: records copied texts (GTK through XWayland)
hold2tg/historybox.py  fallback: the clipboard history box (GTK 4)
gnome-shell/           the clipboard history extension for GNOME Shell 48–50
hold2tg/cli.py         the hold2tg command
nautilus/              Files extension (extension API 3.0, 4.0 and 4.1)
systemd/  udev/        user service and keyboard-access rule
```

```bash
python3 -m unittest discover -s tests -t .
```

Automated tests cover the hold logic, the Telegram client (against a fake server), file and folder sending, the service loop, and the clipboard history's storage, recorder and hotkey.

The clipboard extension has tests of its own that run it in a real, headless GNOME Shell with its own D-Bus and folders, so the desktop you run them on is not touched. They copy texts, pictures, files and a password manager's secret, then use the shortcut, the keyboard and the mouse, paste into a window, and restart the shell:

```bash
HOLD2TG_SHELL_TESTS=1 python3 -m unittest tests.test_shell_extension
```
 The Files extension and text reading were tested by hand with real apps on Nautilus 46 and mutter 46 (Ubuntu 24.04), on both X11 and Wayland. Reading a real keyboard from `/dev/input` and sending to the real Telegram are not covered by the automated tests; after installing, check them with `hold2tg doctor` and `hold2tg test`.
