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
        "fetching": "در حال دریافت آخرین پیام کانال…",
        "copied_text": "متن آخرین پیام کپی شد ✓ با Ctrl+V جای‌گذاری کنید",
        "copied_files": "{n} فایل کپی شد ✓ با Ctrl+V جای‌گذاری کنید",
        "fetch_partial": "{n} فایل کپی شد، {m} مورد دریافت نشد",
        "fetch_failed": "دریافت از تلگرام ناموفق بود",
        "no_message": "پیامی از کانال پیدا نشد",
        "no_message_hint": "hold2tg فقط پیام‌هایی را می‌شناسد که از زمان راه‌اندازی‌اش "
                           "در کانال گذاشته شده‌اند. چیزی در کانال بفرستید و دوباره امتحان کنید.",
        "dl_too_big": "بزرگ‌تر از ۲۰ مگابایت است؛ تلگرام اجازهٔ دانلودش را به ربات نمی‌دهد",
        "translate_failed": "ترجمه ناموفق بود",
        "tr_title": "ترجمه",
        "tr_direction": "از {src} به {tgt}",
        "tr_working": "در حال ترجمه…",
        "tr_copy": "کپی ترجمه",
        "tr_copied": "ترجمه کپی شد",
        "tr_meanings": "معانی دیگر",
        "tr_examples": "مثال‌ها",
        "tr_blocked": "گوگل فعلاً درخواست‌های این شبکه را نمی‌پذیرد. کمی بعد دوباره امتحان کنید "
                      "یا proxy دیگری بگذارید.",
        "history_failed": "باز کردن تاریخچهٔ کپی ناموفق بود",
        "hi_title": "تاریخچهٔ کپی",
        "hi_count": "{n} مورد",
        "hi_search": "جست‌وجو…",
        "hi_empty": "هنوز چیزی کپی نشده",
        "hi_empty_hint": "هر متنی که کپی کنید این‌جا می‌آید. روی هر مورد بزنید تا دوباره کپی شود.",
        "hi_no_match": "چیزی پیدا نشد",
        "hi_delete": "حذف از تاریخچه",
        "hi_clear": "پاک کردن همه",
        "hi_clear_q": "همهٔ تاریخچه پاک شود؟",
        "hi_clear_body": "{n} مورد پاک می‌شود و برنمی‌گردد.",
        "hi_cancel": "انصراف",
        "hi_lines": "{n} خط",
        "hi_now": "همین الان",
        "hi_minutes": "{n} دقیقه پیش",
        "hi_hours": "{n} ساعت پیش",
        "hi_days": "{n} روز پیش",
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
        "fetching": "Fetching the newest message from the channel…",
        "copied_text": "Newest message copied ✓ Paste it with Ctrl+V",
        "copied_files": "{n} file(s) copied ✓ Paste with Ctrl+V",
        "fetch_partial": "{n} file(s) copied, {m} could not be fetched",
        "fetch_failed": "Fetching from Telegram failed",
        "no_message": "No message from the channel yet",
        "no_message_hint": "hold2tg only knows messages posted since it was set up. "
                           "Post something in the channel and try again.",
        "dl_too_big": "over 20 MB; Telegram does not let bots download it",
        "translate_failed": "Translation failed",
        "tr_title": "Translation",
        "tr_direction": "{src} to {tgt}",
        "tr_working": "Translating…",
        "tr_copy": "Copy the translation",
        "tr_copied": "Translation copied",
        "tr_meanings": "Other meanings",
        "tr_examples": "Examples",
        "tr_blocked": "Google refuses requests from this network for now. Try again later "
                      "or set another proxy.",
        "history_failed": "Could not open the clipboard history",
        "hi_title": "Clipboard history",
        "hi_count": "{n} items",
        "hi_search": "Search…",
        "hi_empty": "Nothing copied yet",
        "hi_empty_hint": "Every text you copy shows up here. Click one to copy it again.",
        "hi_no_match": "No match",
        "hi_delete": "Remove from the history",
        "hi_clear": "Clear all",
        "hi_clear_q": "Clear the whole history?",
        "hi_clear_body": "{n} items will be removed for good.",
        "hi_cancel": "Cancel",
        "hi_lines": "{n} lines",
        "hi_now": "just now",
        "hi_minutes": "{n} min ago",
        "hi_hours": "{n} h ago",
        "hi_days": "{n} days ago",
    },
}

# Language names for the translation box, by UI language.
LANGUAGE_NAMES = {
    "fa": {"fa": "فارسی", "en": "انگلیسی", "ar": "عربی", "fr": "فرانسوی", "de": "آلمانی",
           "es": "اسپانیایی", "it": "ایتالیایی", "ru": "روسی", "tr": "ترکی", "zh": "چینی",
           "ja": "ژاپنی", "ko": "کره‌ای", "pt": "پرتغالی", "nl": "هلندی", "hi": "هندی",
           "ur": "اردو", "uk": "اوکراینی", "pl": "لهستانی", "sv": "سوئدی", "he": "عبری",
           "iw": "عبری", "ku": "کردی", "ckb": "کردی سورانی", "az": "آذربایجانی",
           "hy": "ارمنی", "ps": "پشتو"},
    "en": {"fa": "Persian", "en": "English", "ar": "Arabic", "fr": "French", "de": "German",
           "es": "Spanish", "it": "Italian", "ru": "Russian", "tr": "Turkish", "zh": "Chinese",
           "ja": "Japanese", "ko": "Korean", "pt": "Portuguese", "nl": "Dutch", "hi": "Hindi",
           "ur": "Urdu", "uk": "Ukrainian", "pl": "Polish", "sv": "Swedish", "he": "Hebrew",
           "iw": "Hebrew", "ku": "Kurdish", "ckb": "Central Kurdish", "az": "Azerbaijani",
           "hy": "Armenian", "ps": "Pashto"},
}


class Notifier:
    def __init__(self, enabled: bool = True, language: str = "fa"):
        self.enabled = enabled
        self.strings = MESSAGES.get(language, MESSAGES["en"])

    def t(self, key: str, **kw) -> str:
        return self.strings[key].format(**kw)

    def show(self, title: str, body: str = "", error: bool = False, private: bool = False) -> None:
        """private: the body is the user's own text, so it stays out of the log."""
        logged = "" if private or not body else " — " + body.replace("\n", " | ")
        log.info("%s%s", title, logged)
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
