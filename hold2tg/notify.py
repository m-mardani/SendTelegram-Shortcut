"""Desktop notifications (notify-send) and the user-facing message strings."""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("hold2tg.notify")

MESSAGES = {
    "fa": {
        "sending": "در حال ارسال به تلگرام…",
        "sent_text": "متن به تلگرام ارسال شد ✓",
        "sent_files": "{n} فایل به تلگرام ارسال شد ✓",
        "partial": "{n} فایل ارسال شد، {m} مورد ارسال نشد",
        "nothing": "چیزی انتخاب نشده است",
        "failed": "ارسال ناموفق بود",
        "too_many": "{n} فایل انتخاب شده؛ بیشتر از سقف {max} تایی است. چیزی ارسال نشد.",
        "no_config": "توکن ربات یا شناسهٔ کانال تنظیم نشده است",
        "no_keyboard": "به صفحه‌کلید دسترسی ندارم؛ hold2tg doctor را اجرا کنید",
        "skip_big": "بزرگ‌تر از {mb} مگابایت",
        "skip_empty": "فایل خالی است",
        "skip_missing": "فایل پیدا نشد",
        "skip_folder": "پوشه است (zip_folders خاموش است)",
        "skip_special": "فایل معمولی نیست",
    },
    "en": {
        "sending": "Sending to Telegram…",
        "sent_text": "Text sent to Telegram ✓",
        "sent_files": "{n} file(s) sent to Telegram ✓",
        "partial": "{n} file(s) sent, {m} not sent",
        "nothing": "Nothing is selected",
        "failed": "Sending failed",
        "too_many": "{n} files selected; that is over the limit of {max}. Nothing was sent.",
        "no_config": "Bot token or channel id is not configured",
        "no_keyboard": "Cannot read the keyboard; run: hold2tg doctor",
        "skip_big": "larger than {mb} MB",
        "skip_empty": "file is empty",
        "skip_missing": "file not found",
        "skip_folder": "is a folder (zip_folders is off)",
        "skip_special": "not a regular file",
    },
}


class Notifier:
    def __init__(self, enabled: bool = True, language: str = "fa"):
        self.enabled = enabled
        self.strings = MESSAGES.get(language, MESSAGES["en"])

    def t(self, key: str, **kw) -> str:
        return self.strings[key].format(**kw)

    def show(self, title: str, body: str = "", error: bool = False) -> None:
        log.info("%s%s", title, " — " + body.replace("\n", " | ") if body else "")
        if not self.enabled or not shutil.which("notify-send"):
            return
        cmd = ["notify-send", "--app-name=hold2tg",
               "--icon=" + ("dialog-error" if error else "mail-send")]
        if not error:
            # Progress/success banners should not pile up in the notification
            # list; errors stay there so they can be read later.
            cmd.append("--hint=int:transient:1")
        cmd.append(title)
        if body:
            cmd.append(body)
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
