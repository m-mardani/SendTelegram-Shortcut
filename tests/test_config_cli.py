import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from hold2tg import cli, config as config_mod  # noqa: E402
from hold2tg.config import ConfigError  # noqa: E402
from hold2tg.inbox import Inbox, state_path  # noqa: E402
from hold2tg.telegram import TelegramClient  # noqa: E402
from hold2tg.translate import Result  # noqa: E402
from tests.fake_telegram import TOKEN, FakeTelegram  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(os.path.dirname(HERE), "config.example.ini")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "config.ini")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HOLD2TG_BOT_TOKEN", None)
        os.environ.pop("HOLD2TG_CHAT_ID", None)

    def write(self, text, mode=0o600):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(self.path, mode)

    def test_the_shipped_example_parses_to_the_documented_defaults(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            self.write(fh.read())
        cfg = config_mod.load(self.path)
        self.assertEqual((cfg.hotkey, cfg.hold_seconds), ("capslock+c", 5.0))
        self.assertEqual((cfg.fetch_hotkey, cfg.fetch_hold_seconds), ("capslock+v", 5.0))
        self.assertEqual((cfg.translate_hotkey, cfg.translate_hold_seconds), ("capslock+t", 5.0))
        self.assertEqual((cfg.translate_language, cfg.translate_second_language), ("fa", "en"))
        self.assertEqual((cfg.history_hotkey, cfg.history_hold_seconds, cfg.history_size),
                         ("capslock+h", 0.0, 100))
        self.assertEqual(cfg.google_proxy, "")
        self.assertEqual((cfg.max_file_mb, cfg.max_files, cfg.zip_folders), (50.0, 20, True))
        self.assertEqual((cfg.bot_token, cfg.chat_id, cfg.proxy), ("", "", ""))
        self.assertEqual(cfg.api_base, "https://api.telegram.org")
        self.assertEqual(cfg.warnings, [])
        with self.assertRaises(ConfigError):
            cfg.require_telegram()

    def test_values_and_token_with_special_characters(self):
        self.write("[telegram]\nbot_token = 1:AA%bb-c_d\nchat_id = -1001234\n"
                   "proxy = socks5h://127.0.0.1:10808\n"
                   "[hotkey]\nkeys = Super+F9\nhold_seconds = 2.5\n"
                   "[behavior]\nzip_folders = no\nlanguage = EN\n")
        cfg = config_mod.load(self.path)
        self.assertEqual(cfg.bot_token, "1:AA%bb-c_d")
        self.assertEqual(cfg.chat_id, "-1001234")
        self.assertEqual(cfg.proxy, "socks5h://127.0.0.1:10808")
        self.assertEqual((cfg.combo.label, cfg.hold_seconds), ("super+f9", 2.5))
        self.assertEqual((cfg.zip_folders, cfg.language), (False, "en"))

    def test_fetch_hotkey_can_be_changed_or_turned_off(self):
        self.write("[hotkey]\nfetch_keys = Win+Alt+F12\nfetch_hold_seconds = 1\n")
        cfg = config_mod.load(self.path)
        self.assertEqual((cfg.fetch_combo.all_codes, cfg.fetch_hold_seconds),
                         (frozenset({125, 126, 56, 100, 88}), 1.0))
        self.write("[hotkey]\nfetch_keys =\n")
        self.assertIsNone(config_mod.load(self.path).fetch_combo)

    def test_google_uses_the_telegram_proxy_unless_told_otherwise(self):
        self.write("[telegram]\nproxy = socks5h://127.0.0.1:1\n")
        self.assertEqual(config_mod.load(self.path).google_proxy, "socks5h://127.0.0.1:1")
        self.write("[telegram]\nproxy = socks5h://127.0.0.1:1\n[translate]\nproxy = none\n")
        self.assertEqual(config_mod.load(self.path).google_proxy, "")
        self.write("[translate]\nproxy = http://127.0.0.1:2\n")
        self.assertEqual(config_mod.load(self.path).google_proxy, "http://127.0.0.1:2")

    def test_bad_values_are_explained(self):
        for body, needle in (("[hotkey]\nkeys = ctrl+shift\n", "non-modifier"),
                             ("[hotkey]\nfetch_keys = super+nope\n", "fetch_keys"),
                             ("[hotkey]\nkeys = super+alt+c\nfetch_keys = Alt+Win+C\n",
                              "must differ"),
                             ("[hotkey]\nfetch_hold_seconds = -1\n", "negative"),
                             ("[hotkey]\nfetch_keys = super+f1\ntranslate_keys = super+F1\n",
                              "translate_keys must differ from fetch_keys"),
                             ("[translate]\nlanguage = en\n", "must differ"),
                             ("[hotkey]\nhistory_keys = capslock+t\n",
                              "history_keys must differ from translate_keys"),
                             ("[hotkey]\nhistory_hold_seconds = -0.5\n", "negative"),
                             ("[history]\nsize = 0\n", "between 1 and 1000"),
                             ("[hotkey]\nhold_seconds = five\n", "must be a number"),
                             ("[behavior]\nzip_folders = maybe\n", "true or false"),
                             ("[behavior]\ntext_backend = magic\n", "text_backend")):
            self.write(body)
            with self.assertRaises(ConfigError) as cm:
                config_mod.load(self.path)
            self.assertIn(needle, str(cm.exception))

    def test_the_clipboard_history_can_be_set_or_turned_off(self):
        self.write("[hotkey]\nhistory_keys = ctrl+alt+h\nhistory_hold_seconds = 0.5\n"
                   "[history]\nsize = 250\n")
        cfg = config_mod.load(self.path)
        self.assertEqual((cfg.history_combo.label, cfg.history_hold_seconds, cfg.history_size),
                         ("ctrl+alt+h", 0.5, 250))
        self.write("[hotkey]\nhistory_keys =\n")
        self.assertIsNone(config_mod.load(self.path).history_combo)

    def test_world_readable_config_is_flagged(self):
        self.write("[telegram]\nbot_token = x\n", mode=0o644)
        self.assertIn("chmod 600", config_mod.load(self.path).warnings[0])

    def test_missing_file_gives_defaults_and_env_overrides(self):
        os.environ["HOLD2TG_BOT_TOKEN"] = "from-env"
        cfg = config_mod.load(self.path)
        self.assertEqual(cfg.bot_token, "from-env")
        self.assertIn("does not exist", cfg.warnings[0])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "config.ini")
        with open(path, "w") as fh:
            fh.write("[telegram]\nbot_token = %s\nchat_id = @c\napi_base = %s\n"
                     "[behavior]\nnotifications = false\n" % (TOKEN, self.fake.base))
        os.chmod(path, 0o600)
        env = mock.patch.dict(os.environ, {"HOLD2TG_CONFIG": path})
        env.start()
        self.addCleanup(env.stop)
        for stale in (state_path(), state_path() + ".lock"):
            if os.path.exists(stale):
                os.remove(stale)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_chats_lists_the_channel_id(self):
        self.fake.scripted.append((200, {"ok": True, "result": [
            {"update_id": 1, "channel_post": {"chat": {"id": -1001234567890, "type": "channel",
                                                       "title": "کانال من"}}},
            {"update_id": 2, "message": {"chat": {"id": 42, "type": "private",
                                                  "first_name": "Me"}}}]}))
        code, out = self.run_cli("chats")
        self.assertEqual(code, 0)
        self.assertIn("-1001234567890", out)
        self.assertIn("کانال من", out)

    def test_chats_also_lists_chats_the_daemon_already_collected(self):
        self.fake.scripted.append((200, {"ok": True, "result": [
            {"update_id": 7, "channel_post": {"message_id": 1, "text": "x", "chat": {
                "id": -1009, "type": "channel", "title": "قبلی"}}}]}))
        Inbox(TOKEN, "@c").sync(TelegramClient(TOKEN, "@c", api_base=self.fake.base))
        self.fake.scripted.append((200, {"ok": True, "result": []}))
        code, out = self.run_cli("chats")
        self.assertEqual(code, 0)
        self.assertIn("-1009", out)

    def test_fetch_with_nothing_posted_yet_fails_politely(self):
        self.fake.scripted.append((200, {"ok": True, "result": []}))
        self.assertEqual(self.run_cli("fetch")[0], 1)

    def test_translate_print(self):
        result = Result("hi", "en", "fa", "سلام", meanings=[("اسم", ["درود"])],
                        examples=["say <b>hi</b>"])
        with mock.patch.object(cli, "Translator") as translator:
            translator.return_value.translate.return_value = result
            code, out = self.run_cli("translate", "--print", "hi")
        self.assertEqual(code, 0)
        self.assertEqual(out, "en → fa\nسلام\n  اسم: درود\n  • say hi\n")
        translator.return_value.translate.assert_called_once_with("hi")

    def test_history_print_and_clear(self):
        from hold2tg.history import History
        history = History()
        history.clear()
        history.add("first", now=1)
        history.add("second\n  line", now=2)
        code, out = self.run_cli("history", "--print")
        self.assertEqual(code, 0)
        self.assertEqual(out, "  1  second line\n  2  first\n")
        self.assertEqual(self.run_cli("history", "--clear")[0], 0)
        self.assertEqual(history.load(), [])

    def test_translate_with_nothing_selected(self):
        with mock.patch.object(cli.selection, "read_primary_text", return_value=" "):
            self.assertEqual(self.run_cli("translate")[0], 1)

    def test_chats_with_no_updates_explains_what_to_do(self):
        self.fake.scripted.append((200, {"ok": True, "result": []}))
        code, out = self.run_cli("chats")
        self.assertEqual(code, 1)
        self.assertIn("post any message", out)

    def test_test_command_and_send_files_with_dashes(self):
        self.assertEqual(self.run_cli("test")[0], 0)
        path = os.path.join(self.tmp.name, "-weird name.txt")
        with open(path, "w") as fh:
            fh.write("x")
        with mock.patch("os.getcwd", return_value=self.tmp.name):
            self.assertEqual(self.run_cli("send-files", "--", path)[0], 0)
        self.assertEqual(self.fake.calls[-1][2]["document"][0], "-weird name.txt")

    def test_telegram_failure_gives_exit_code_1(self):
        self.fake.scripted.append((403, {"ok": False, "error_code": 403,
                                         "description": "Forbidden: bot is not a member"}))
        code, out = self.run_cli("test")
        self.assertEqual(code, 1)
        self.assertIn("Forbidden: bot is not a member", out)


if __name__ == "__main__":
    unittest.main()
