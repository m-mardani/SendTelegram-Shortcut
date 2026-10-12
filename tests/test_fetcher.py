import os
import unittest
from unittest import mock

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from hold2tg import config as config_mod, fetcher as fetcher_mod  # noqa: E402
from hold2tg.config import Config  # noqa: E402
from hold2tg.fetcher import Fetcher, safe_name  # noqa: E402
from hold2tg.inbox import Inbox, state_path  # noqa: E402
from hold2tg.selection import Snapshot  # noqa: E402
from hold2tg.sender import Sender  # noqa: E402
from tests.fake_telegram import TOKEN, FakeTelegram  # noqa: E402
from tests.test_sender import RecordingNotifier  # noqa: E402

CHAN = {"id": -100777, "type": "channel", "title": "c"}


def doc(mid, name, uid, size, group=None):
    msg = {"message_id": mid, "date": 1760000000, "chat": CHAN,
           "document": {"file_id": "id-" + uid, "file_unique_id": uid,
                        "file_name": name, "file_size": size}}
    if group:
        msg["media_group_id"] = group
    return msg


class SafeNameTests(unittest.TestCase):
    def test_names_cannot_escape_the_folder(self):
        self.assertEqual(safe_name("../../etc/passwd"), ".._.._etc_passwd")
        for bad in ("", ".", "..", " "):
            self.assertEqual(safe_name(bad), "file")

    def test_long_names_are_trimmed_but_keep_the_extension(self):
        name = safe_name("گزارش" * 60 + ".pdf")
        self.assertLessEqual(len(name.encode("utf-8")), 200)
        self.assertTrue(name.endswith(".pdf"))


class FetcherTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.updates = []
        self.fake.methods["getUpdates"] = lambda f: [
            u for u in self.updates if u["update_id"] >= int(f.get("offset", 0))]
        self.fake.methods["getFile"] = lambda f: {"file_id": f["file_id"],
                                                  "file_path": "documents/" + f["file_id"]}
        self.notifier = RecordingNotifier()
        self.cfg = Config(bot_token=TOKEN, chat_id="-100777", api_base=self.fake.base)
        self.fetcher = Fetcher(self.cfg, self.notifier)
        self.copied = []
        for name in ("copy_text", "copy_files"):
            p = mock.patch.object(fetcher_mod.clipboard, name,
                                  side_effect=lambda v, _n=name: self.copied.append((_n, v)))
            p.start()
            self.addCleanup(p.stop)
        for path in (state_path(), state_path() + ".lock"):
            if os.path.exists(path):
                os.remove(path)
        self.addCleanup(self.fetcher._prune, set())

    def posted(self, *messages):
        start = len(self.updates) + 1
        self.updates += [{"update_id": start + i, "channel_post": m} for i, m in enumerate(messages)]

    def test_text_posted_by_someone_else(self):
        self.posted({"message_id": 1, "date": 1, "chat": CHAN, "text": "از گوشی"})
        self.assertTrue(self.fetcher.fetch())
        self.assertEqual(self.copied, [("copy_text", "از گوشی")])
        self.assertEqual(self.notifier.shown[-1][0], self.notifier.t("copied_text"))

    def test_file_is_downloaded_once_and_put_on_the_clipboard(self):
        self.fake.files["documents/id-u1"] = b"%PDF-1.7 data"
        self.posted(doc(2, "گزارش.pdf", "u1", 13))
        self.assertTrue(self.fetcher.fetch())
        kind, paths = self.copied[0]
        self.assertEqual(kind, "copy_files")
        self.assertEqual(os.path.basename(paths[0]), "گزارش.pdf")
        with open(paths[0], "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF-1.7 data")
        self.assertTrue(paths[0].startswith(config_mod.received_dir()))
        self.assertTrue(self.fetcher.fetch())  # the same message again
        self.assertEqual([c[0] for c in self.fake.calls].count("getFile"), 1)

    def test_album_brings_every_file(self):
        self.fake.files.update({"documents/id-a": b"a", "documents/id-b": b"bb"})
        self.posted(doc(3, "a.txt", "a", 1, group="g"), doc(4, "b.txt", "b", 2, group="g"))
        self.assertTrue(self.fetcher.fetch())
        self.assertEqual([os.path.basename(p) for p in self.copied[0][1]], ["a.txt", "b.txt"])

    def test_too_big_file_is_explained_and_clipboard_left_alone(self):
        Inbox(TOKEN, "-100777").record(doc(5, "movie.mkv", "big", 900 * 1024 * 1024))
        self.fake.scripted = [(200, {"ok": True, "result": []}),                  # getUpdates
                              (400, {"ok": False, "error_code": 400,              # getFile
                                     "description": "Bad Request: file is too big"})]
        self.assertFalse(self.fetcher.fetch())
        self.assertEqual(self.copied, [])
        title, body, error = self.notifier.shown[-1]
        self.assertTrue(error)
        self.assertIn(self.notifier.t("dl_too_big"), body)

    def test_own_post_counts_as_the_newest(self):
        self.fake.methods["sendMessage"] = lambda f: {"message_id": 50, "date": 2, "chat": CHAN,
                                                      "text": f["text"]}
        self.posted({"message_id": 49, "date": 1, "chat": CHAN, "text": "older, from phone"})
        self.fetcher.sync()
        Sender(self.cfg, self.notifier).send(Snapshot(kind="text", text="sent from this PC"))
        self.assertTrue(self.fetcher.fetch())
        self.assertEqual(self.copied, [("copy_text", "sent from this PC")])

    def test_nothing_known_yet(self):
        self.assertFalse(self.fetcher.fetch())
        self.assertEqual(self.notifier.shown[-1][0], self.notifier.t("no_message"))
        self.assertEqual(self.copied, [])

    def test_old_downloads_are_removed_once_replaced(self):
        self.fake.files["documents/id-old"] = b"x"
        self.posted(doc(6, "old.bin", "old", 1))
        self.assertTrue(self.fetcher.fetch())
        self.posted({"message_id": 7, "date": 3, "chat": CHAN, "text": "now text"})
        self.assertTrue(self.fetcher.fetch())
        self.assertEqual(os.listdir(config_mod.received_dir()), [])

    def test_background_sync_is_quiet_without_config_or_network(self):
        Fetcher(Config(), self.notifier).sync()
        self.fake.close()
        Fetcher(Config(bot_token=TOKEN, chat_id="-1", api_base="http://127.0.0.1:9"),
                self.notifier).sync()
        self.assertEqual(self.notifier.shown, [])


if __name__ == "__main__":
    unittest.main()
