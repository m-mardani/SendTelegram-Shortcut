import os
import tempfile
import unittest

import tests  # noqa: F401  (keeps the real state dir out of reach)
from hold2tg.inbox import KEEP, Inbox, same_chat, to_entry

CHAN = {"id": -1001234, "type": "channel", "title": "کانال", "username": "MyChan"}


def post(mid, chat=CHAN, **fields):
    return dict({"message_id": mid, "date": 1760000000 + mid, "chat": chat}, **fields)


class FakeClient:
    """get_updates() over a list of updates, honouring offsets like Telegram."""

    def __init__(self, updates, fail_on_page=None):
        self.updates = list(updates)
        self.offsets = []
        self.fail_on_page = fail_on_page

    def get_updates(self, offset=0):
        self.offsets.append(offset)
        if self.fail_on_page is not None and len(self.offsets) == self.fail_on_page:
            raise RuntimeError("network down")
        return [u for u in self.updates if u["update_id"] >= offset][:100]


class EntryTests(unittest.TestCase):
    def test_text(self):
        e = to_entry(post(5, text="سلام"))
        self.assertEqual((e["id"], e["text"], e["file"], e["group"]), (5, "سلام", None, ""))

    def test_document_keeps_its_name_and_drops_the_caption(self):
        e = to_entry(post(6, caption="c", document={
            "file_id": "F", "file_unique_id": "U", "file_name": "report.pdf", "file_size": 9}))
        self.assertEqual(e["file"], {"id": "F", "uid": "U", "name": "report.pdf", "size": 9})
        self.assertEqual(e["text"], "")

    def test_photo_uses_the_largest_size_and_gets_a_name(self):
        e = to_entry(post(7, media_group_id="g", photo=[
            {"file_id": "small", "file_unique_id": "s", "file_size": 1},
            {"file_id": "big", "file_unique_id": "b", "file_size": 100}]))
        self.assertEqual(e["file"]["id"], "big")
        self.assertRegex(e["file"]["name"], r"^photo_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.jpg$")
        self.assertEqual(e["group"], "g")

    def test_animation_wins_over_its_document_copy(self):
        e = to_entry(post(8, animation={"file_id": "A", "file_name": "cat.mp4"},
                          document={"file_id": "D", "file_name": "cat.mp4"}))
        self.assertEqual(e["file"]["id"], "A")

    def test_unnamed_media_gets_an_extension(self):
        voice = to_entry(post(9, voice={"file_id": "V", "mime_type": "audio/ogg"}))
        self.assertTrue(voice["file"]["name"].endswith(".ogg"))
        sticker = to_entry(post(10, sticker={"file_id": "S", "is_animated": True}))
        self.assertTrue(sticker["file"]["name"].endswith(".tgs"))

    def test_same_chat_by_id_or_username(self):
        self.assertTrue(same_chat(CHAN, "-1001234"))
        self.assertTrue(same_chat(CHAN, "@mychan"))
        self.assertFalse(same_chat(CHAN, "@other"))
        self.assertFalse(same_chat({"id": 1}, "@x"))


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "inbox.json")
        self.inbox = Inbox("123:secret", "-1001234", self.path)

    def test_empty(self):
        self.assertEqual(self.inbox.latest(), [])

    def test_own_posts_are_recorded_and_the_newest_wins(self):
        self.inbox.record(post(3, text="old"))
        self.inbox.record(post(4, text="new"))
        self.assertEqual([e["text"] for e in self.inbox.latest()], ["new"])

    def test_the_secret_part_of_the_token_is_never_stored(self):
        self.inbox.record(post(1, text="x"))
        with open(self.path, encoding="utf-8") as fh:
            self.assertNotIn("secret", fh.read())
        self.assertEqual(os.stat(self.path).st_mode & 0o077, 0)

    def test_nothing_to_copy_is_skipped(self):
        self.inbox.record(post(1, text="keep me"))
        self.inbox.record(post(2, poll={"question": "?"}))
        self.inbox.record(post(3, pinned_message={"message_id": 1}))
        self.assertEqual(self.inbox.latest()[0]["text"], "keep me")

    def test_sync_takes_this_chat_only_and_confirms_what_it_read(self):
        other = {"id": 99, "type": "private", "first_name": "x"}
        client = FakeClient([
            {"update_id": 10, "channel_post": post(20, text="first")},
            {"update_id": 11, "message": post(5, chat=other, text="not ours")},
            {"update_id": 12, "edited_channel_post": post(20, text="first, edited")},
        ])
        self.inbox.sync(client)
        self.assertEqual([e["text"] for e in self.inbox.latest()], ["first, edited"])
        self.inbox.sync(client)
        self.assertEqual(client.offsets, [0, 13])
        self.assertEqual({c["id"] for c in self.inbox.seen_chats()}, {-1001234, 99})

    def test_sync_pages_through_a_long_queue(self):
        client = FakeClient([{"update_id": i, "channel_post": post(i, text=str(i))}
                             for i in range(1, 251)])
        self.inbox.sync(client)
        self.assertEqual(client.offsets, [0, 101, 201])
        self.assertEqual(self.inbox.latest()[0]["text"], "250")

    def test_progress_survives_an_error_halfway(self):
        client = FakeClient([{"update_id": i, "channel_post": post(i, text=str(i))}
                             for i in range(1, 151)], fail_on_page=2)
        with self.assertRaises(RuntimeError):
            self.inbox.sync(client)
        # Page 1 was confirmed by asking for page 2, so it must not be lost.
        self.assertEqual(self.inbox.latest()[0]["text"], "100")
        client.fail_on_page = None
        self.inbox.sync(client)
        self.assertEqual(client.offsets[-1], 101)

    def test_album_comes_back_whole(self):
        self.inbox.record(post(1, text="before"))
        for mid in (2, 3, 4):
            self.inbox.record(post(mid, media_group_id="alb", document={
                "file_id": "f%d" % mid, "file_name": "%d.pdf" % mid}))
        self.assertEqual([e["file"]["name"] for e in self.inbox.latest()],
                         ["2.pdf", "3.pdf", "4.pdf"])

    def test_only_the_newest_few_are_kept(self):
        for mid in range(1, KEEP + 6):
            self.inbox.record(post(mid, text=str(mid)))
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read().count('"text"'), KEEP)

    def test_another_bot_or_channel_starts_afresh(self):
        self.inbox.record(post(1, text="x"))
        self.assertEqual(Inbox("123:secret", "@elsewhere", self.path).latest(), [])
        self.assertEqual(Inbox("456:other", "-1001234", self.path).latest(), [])

    def test_corrupt_state_is_ignored(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(self.inbox.latest(), [])
        self.inbox.record(post(1, text="ok"))
        self.assertEqual(self.inbox.latest()[0]["text"], "ok")


if __name__ == "__main__":
    unittest.main()
