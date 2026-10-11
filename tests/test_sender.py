import os
import tempfile
import unittest
import zipfile

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from hold2tg.config import Config  # noqa: E402
from hold2tg.notify import Notifier  # noqa: E402
from hold2tg.selection import Snapshot  # noqa: E402
from hold2tg.sender import Sender  # noqa: E402
from tests.fake_telegram import TOKEN, FakeTelegram  # noqa: E402


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__(enabled=False, language="en")
        self.shown = []

    def show(self, title, body="", error=False):
        self.shown.append((title, body, error))


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.notifier = RecordingNotifier()
        self.cfg = Config(bot_token=TOKEN, chat_id="@chan", api_base=self.fake.base,
                          max_file_mb=1, max_files=5)
        self.sender = Sender(self.cfg, self.notifier)

    def make(self, name, data=b"data"):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_text(self):
        self.assertTrue(self.sender.send(Snapshot(kind="text", text="hello")))
        self.assertEqual(self.fake.calls[0][1]["text"], "hello")
        self.assertEqual(self.notifier.shown[-1], ("Text sent to Telegram ✓", "", False))

    def test_nothing_selected(self):
        self.assertFalse(self.sender.send(Snapshot()))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.notifier.shown[-1][0], "Nothing is selected")

    def test_several_files(self):
        files = [self.make("a.txt"), self.make("عکس.png", b"\x89PNG")]
        self.assertTrue(self.sender.send(Snapshot(kind="files", files=files)))
        names = [call[2]["document"][0] for call in self.fake.calls]
        self.assertEqual(names, ["a.txt", "عکس.png"])
        self.assertEqual(self.notifier.shown[-1][0], "2 file(s) sent to Telegram ✓")

    def test_folder_is_zipped_with_its_structure(self):
        self.make("project/readme.md", b"# hi")
        self.make("project/src/main.py", b"print(1)")
        folder = os.path.join(self.tmp.name, "project")
        self.assertTrue(self.sender.send(Snapshot(kind="files", files=[folder])))
        filename, payload = self.fake.calls[0][2]["document"]
        self.assertEqual(filename, "project.zip")
        archive = os.path.join(self.tmp.name, "got.zip")
        with open(archive, "wb") as fh:
            fh.write(payload)
        with zipfile.ZipFile(archive) as zf:
            self.assertEqual(sorted(zf.namelist()), ["project/readme.md", "project/src/main.py"])
            self.assertEqual(zf.read("project/src/main.py"), b"print(1)")

    def test_folder_skipped_when_zipping_is_off(self):
        self.cfg.zip_folders = False
        self.make("d/x", b"1")
        self.assertFalse(self.sender.send(
            Snapshot(kind="files", files=[os.path.join(self.tmp.name, "d")])))
        self.assertEqual(self.fake.calls, [])

    def test_big_empty_and_missing_files_are_skipped_but_the_rest_is_sent(self):
        files = [self.make("big.bin", b"x" * (1024 * 1024 + 1)), self.make("empty.txt", b""),
                 os.path.join(self.tmp.name, "gone.txt"), self.make("ok.txt")]
        self.assertFalse(self.sender.send(Snapshot(kind="files", files=files)))
        self.assertEqual([c[2]["document"][0] for c in self.fake.calls], ["ok.txt"])
        title, body, error = self.notifier.shown[-1]
        self.assertEqual(title, "1 file(s) sent, 3 not sent")
        self.assertIn("big.bin: larger than 1 MB", body)
        self.assertIn("empty.txt: file is empty", body)
        self.assertIn("gone.txt: file not found", body)
        self.assertTrue(error)

    def test_too_many_files_sends_nothing(self):
        files = [self.make("f%d" % i) for i in range(6)]
        self.assertFalse(self.sender.send(Snapshot(kind="files", files=files)))
        self.assertEqual(self.fake.calls, [])
        self.assertIn("over the limit of 5", self.notifier.shown[-1][0])

    def test_one_rejected_file_does_not_stop_the_others(self):
        self.fake.scripted.append((400, {"ok": False, "error_code": 400,
                                         "description": "Bad Request: file rejected"}))
        files = [self.make("first"), self.make("second")]
        self.assertFalse(self.sender.send(Snapshot(kind="files", files=files)))
        self.assertEqual(len(self.fake.calls), 2)
        self.assertIn("first: Bad Request: file rejected", self.notifier.shown[-1][1])

    def test_missing_configuration(self):
        self.cfg.bot_token = ""
        self.assertFalse(self.sender.send(Snapshot(kind="text", text="x")))
        self.assertEqual(self.notifier.shown[-1][0], "Bot token or channel id is not configured")

    def test_temporary_zip_is_cleaned_up(self):
        self.make("p/a", b"1")
        before = set(os.listdir(tempfile.gettempdir()))
        self.sender.send(Snapshot(kind="files", files=[os.path.join(self.tmp.name, "p")]))
        leftovers = [n for n in set(os.listdir(tempfile.gettempdir())) - before
                     if n.startswith("hold2tg-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
