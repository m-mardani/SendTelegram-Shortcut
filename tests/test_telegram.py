import hashlib
import os
import tempfile
import unittest

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from hold2tg.telegram import TelegramClient, TelegramError, split_text, utf16_len  # noqa: E402
from tests.fake_telegram import TOKEN, FakeTelegram  # noqa: E402


class SplitTests(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(split_text("سلام\nدنیا"), ["سلام\nدنیا"])

    def test_long_text_is_split_within_the_limit_and_lossless(self):
        words = ["کلمه%d" % i for i in range(3000)]
        text = " ".join(words)
        chunks = split_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(utf16_len(c) <= 4096 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), words)

    def test_prefers_newlines(self):
        text = ("a" * 3000 + "\n") * 3
        chunks = split_text(text)
        self.assertEqual([c.rstrip("\n") for c in chunks], ["a" * 3000] * 3)

    def test_emoji_count_as_two_units(self):
        text = "😀" * 3000            # 6000 UTF-16 units, no spaces to break at
        chunks = split_text(text)
        self.assertTrue(all(utf16_len(c) <= 4096 for c in chunks))
        self.assertEqual("".join(chunks), text)

    def test_unbroken_text(self):
        chunks = split_text("x" * 10000)
        self.assertEqual([len(c) for c in chunks], [4096, 4096, 1808])


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.client = TelegramClient(TOKEN, "@my_channel", api_base=self.fake.base, max_wait=0.2)

    def test_send_text(self):
        self.assertEqual(self.client.send_text("متن انتخاب‌شده & <b>raw</b> 100%"), 1)
        method, fields, _ = self.fake.calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(fields["chat_id"], "@my_channel")
        self.assertEqual(fields["text"], "متن انتخاب‌شده & <b>raw</b> 100%")
        self.assertNotIn("parse_mode", fields)      # sent literally, no markup surprises

    def test_long_text_becomes_several_messages(self):
        self.assertEqual(self.client.send_text("line\n" * 2500), 4)
        self.assertEqual(len(self.fake.calls), 4)

    def test_send_document_streams_the_exact_bytes_and_keeps_unicode_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'گزارش "نهایی".bin')
            data = os.urandom(5 * 1024 * 1024 + 123)
            with open(path, "wb") as fh:
                fh.write(data)
            self.client.send_document(path)
        method, fields, files = self.fake.calls[0]
        self.assertEqual(method, "sendDocument")
        self.assertEqual(fields["chat_id"], "@my_channel")
        filename, payload = files["document"]
        self.assertEqual(filename, "گزارش %22نهایی%22.bin")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), hashlib.sha256(data).hexdigest())

    def test_binary_file_containing_boundary_like_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tricky.dat")
            data = b"\r\n--hold2tg\r\n" * 1000 + b"\x00\xff"
            with open(path, "wb") as fh:
                fh.write(data)
            self.client.send_document(path)
        self.assertEqual(self.fake.calls[0][2]["document"][1], data)

    def test_rate_limit_is_retried(self):
        self.fake.scripted.append((429, {"ok": False, "error_code": 429,
                                         "description": "Too Many Requests: retry after 0",
                                         "parameters": {"retry_after": 0}}))
        self.assertEqual(self.client.send_text("hi"), 1)
        self.assertEqual(len(self.fake.calls), 2)

    def test_api_error_is_reported_with_telegram_description(self):
        self.fake.scripted.append((400, {"ok": False, "error_code": 400,
                                         "description": "Bad Request: chat not found"}))
        with self.assertRaises(TelegramError) as cm:
            self.client.send_text("hi")
        self.assertEqual(str(cm.exception), "Bad Request: chat not found")
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(len(self.fake.calls), 1)       # a 400 is not retried

    def test_network_error_never_leaks_the_token(self):
        self.fake.close()
        client = TelegramClient(TOKEN, "@c", api_base="http://127.0.0.1:9", retries=2, max_wait=0)
        with self.assertRaises(TelegramError) as cm:
            client.send_text("hi")
        self.assertIn("network error", str(cm.exception))
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertIn("<token>", str(cm.exception))

    def test_get_updates_confirms_only_when_given_an_offset(self):
        self.client.get_updates()
        self.client.get_updates(42)
        self.assertNotIn("offset", self.fake.calls[0][1])
        self.assertEqual(self.fake.calls[1][1]["offset"], "42")

    def test_every_sent_message_is_reported_and_a_broken_hook_is_harmless(self):
        seen = []
        self.client.on_sent = seen.append
        self.client.send_text("line\n" * 2500)
        self.assertEqual(len(seen), 4)
        self.client.on_sent = lambda message: 1 / 0
        self.assertEqual(self.client.send_text("hi"), 1)

    def test_download_writes_the_exact_bytes(self):
        self.fake.methods["getFile"] = lambda f: {"file_path": "documents/file_1.pdf"}
        self.fake.files["documents/file_1.pdf"] = data = os.urandom(700 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "x.pdf")
            self.client.download("FID", dest)
            with open(dest, "rb") as fh:
                self.assertEqual(fh.read(), data)
            self.assertEqual(os.listdir(tmp), ["x.pdf"])
        self.assertEqual(self.fake.calls[0][1]["file_id"], "FID")

    def test_failed_download_leaves_nothing_behind(self):
        self.fake.methods["getFile"] = lambda f: {"file_path": "gone"}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TelegramError) as cm:
                self.client.download("FID", os.path.join(tmp, "x"))
            self.assertEqual(os.listdir(tmp), [])
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_silent_flag(self):
        TelegramClient(TOKEN, "-1001", api_base=self.fake.base, silent=True).send_text("x")
        self.assertEqual(self.fake.calls[0][1]["disable_notification"], "true")
        self.assertEqual(self.fake.calls[0][1]["chat_id"], "-1001")


if __name__ == "__main__":
    unittest.main()
