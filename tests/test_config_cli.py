import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from hold2tg import cli, config as config_mod  # noqa: E402
from hold2tg.config import ConfigError  # noqa: E402
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
        self.assertEqual((cfg.hotkey, cfg.hold_seconds), ("ctrl+shift+s", 5.0))
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

    def test_bad_values_are_explained(self):
        for body, needle in (("[hotkey]\nkeys = ctrl+shift\n", "non-modifier"),
                             ("[hotkey]\nhold_seconds = five\n", "must be a number"),
                             ("[behavior]\nzip_folders = maybe\n", "true or false"),
                             ("[behavior]\ntext_backend = magic\n", "text_backend")):
            self.write(body)
            with self.assertRaises(ConfigError) as cm:
                config_mod.load(self.path)
            self.assertIn(needle, str(cm.exception))

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
