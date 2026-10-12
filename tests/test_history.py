import json
import os
import stat
import tempfile
import threading
import unittest
from unittest import mock

from hold2tg import history as history_mod
from hold2tg.history import MAX_CHARS, History, HistoryBox, Recorder, ago, keep, preview
from hold2tg.notify import MESSAGES, Notifier


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "clipboard.json")
        self.history = History(self.path, size=3)

    def texts(self, history=None):
        return [e.text for e in (history or self.history).load()]

    def test_newest_first_and_copying_again_moves_it_to_the_top(self):
        self.history.add("a", now=1)
        self.history.add("b", now=2)
        self.history.add("a", now=3)
        self.assertEqual([(e.text, e.at) for e in self.history.load()], [("a", 3.0), ("b", 2.0)])

    def test_only_the_newest_are_kept(self):
        for text in "abcd":
            self.history.add(text)
        self.assertEqual(self.texts(), ["d", "c", "b"])

    def test_blank_and_very_long_copies_are_not_kept(self):
        self.assertFalse(self.history.add(" \n\t "))
        self.assertFalse(self.history.add("x" * (MAX_CHARS + 1)))
        self.assertTrue(self.history.add("x" * MAX_CHARS))
        self.assertEqual(len(self.texts()), 1)

    def test_text_is_kept_exactly(self):
        text = "  سلام دنیا\n\tline two  \n"
        self.history.add(text)
        self.assertEqual(History(self.path).load()[0].text, text)   # another process sees it

    def test_remove_and_clear(self):
        for text in "abc":
            self.history.add(text)
        self.history.remove("b")
        self.history.remove("not there")
        self.assertEqual(self.texts(), ["c", "a"])
        self.history.clear()
        self.assertEqual(self.texts(), [])

    def test_only_the_user_can_read_it(self):
        self.history.add("secret-ish")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path + ".lock").st_mode), 0o600)

    def test_an_unreadable_file_starts_afresh(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(self.texts(), [])
        self.history.add("new")
        self.assertEqual(self.texts(), ["new"])
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["items"][0]["text"], "new")

    def test_writers_at_the_same_time_lose_nothing(self):
        big = History(self.path, size=1000)

        def write(prefix):
            for i in range(25):
                History(self.path, size=1000).add("%s-%d" % (prefix, i))

        threads = [threading.Thread(target=write, args=(p,)) for p in "xyz"]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(big.load()), 75)


class KeepTests(unittest.TestCase):
    def test_secrets_and_files_are_not_recorded(self):
        text = "TARGETS\nUTF8_STRING\ntext/plain;charset=utf-8\n"
        self.assertTrue(keep(text))
        self.assertFalse(keep(text + "x-kde-passwordManagerHint\n"))
        self.assertFalse(keep("x-special/gnome-copied-files\ntext/plain\n"))


class ShownTests(unittest.TestCase):
    def test_preview_is_one_line(self):
        self.assertEqual(preview("  first\n\n  second\tthird  "), "first second third")
        long = preview("word " * 100, limit=30)
        self.assertTrue(long.endswith("…"))
        self.assertLessEqual(len(long), 31)

    def test_ago(self):
        en, fa = MESSAGES["en"], MESSAGES["fa"]
        self.assertEqual(ago(5, en), "just now")
        self.assertEqual(ago(-3, en), "just now")              # a clock moved back
        self.assertEqual(ago(5 * 60 + 9, en), "5 min ago")
        self.assertEqual(ago(2 * 3600, en), "2 h ago")
        self.assertEqual(ago(3 * 86400 + 1, en), "3 days ago")
        self.assertEqual(ago(12 * 60, fa, persian=True), "۱۲ دقیقه پیش")
        self.assertEqual(ago(30, fa, persian=True), "همین الان")


class FakeProc:
    def __init__(self, args, **kwargs):
        self.args = args
        self.env = kwargs.get("env")
        self.code = None
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


class _Spawning(unittest.TestCase):
    """Popen replaced: the helpers it would start are recorded in self.started."""

    def setUp(self):
        self.started = []

        def popen(args, **kwargs):
            proc = FakeProc(args, **kwargs)
            self.started.append(proc)
            return proc

        p = mock.patch.object(history_mod.subprocess, "Popen", side_effect=popen)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"DISPLAY": ":0"})
        env.start()
        self.addCleanup(env.stop)


class RecorderTests(_Spawning):
    def test_runs_the_helper_on_xwayland(self):
        recorder = Recorder(size=100)
        recorder.check(0.0)
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.started[0].args[-4:], ["hold2tg", "clipwatch", "--size", "100"])
        self.assertEqual(self.started[0].env["GDK_BACKEND"], "x11")
        recorder.check(1.0)                                   # still running: left alone
        self.assertEqual(len(self.started), 1)

    def test_restarts_it_after_a_growing_wait(self):
        recorder = Recorder()
        recorder.check(0.0)
        self.started[-1].code = 1                             # it stopped
        recorder.check(10.0)
        recorder.check(11.0)
        self.assertEqual(len(self.started), 1)                # waits 2 s
        recorder.check(12.0)
        self.assertEqual(len(self.started), 2)
        self.started[-1].code = 1
        recorder.check(13.0)
        recorder.check(17.0)
        self.assertEqual(len(self.started), 2)                # then 5 s
        recorder.check(18.0)
        self.assertEqual(len(self.started), 3)
        recorder.check(18.0 + Recorder.STEADY)                # a minute running: back to 2 s
        self.started[-1].code = 1
        recorder.check(100.0)
        recorder.check(102.0)
        self.assertEqual(len(self.started), 4)

    def test_without_an_x_display_nothing_is_started(self):
        del os.environ["DISPLAY"]
        recorder = Recorder()
        with self.assertLogs("hold2tg.history", "WARNING") as logs:
            recorder.check(0.0)
            recorder.check(100.0)
        self.assertEqual(self.started, [])
        self.assertEqual(len(logs.records), 1)                # said once

    def test_stop_ends_the_helper(self):
        recorder = Recorder()
        recorder.check(0.0)
        recorder.stop()
        self.assertTrue(self.started[0].terminated)
        self.assertIsNone(recorder.proc)


class BoxTests(unittest.TestCase):
    def test_the_hotkey_opens_the_box_and_closes_it_again(self):
        started = []
        with mock.patch.object(history_mod.subprocess, "Popen",
                               side_effect=lambda args, **kw: started.append(FakeProc(args)) or started[-1]):
            box = HistoryBox(Notifier(False))
            box.toggle()
            self.assertEqual(started[0].args[-2:], ["hold2tg", "history"])
            box.toggle()
            self.assertTrue(started[0].terminated)
            self.assertEqual(len(started), 1)
            box.toggle()                                      # opens anew
            self.assertEqual(len(started), 2)
            started[1].code = 0                               # closed by the user meanwhile
            box.toggle()
            self.assertEqual(len(started), 3)


class FakeShell:
    def __init__(self, active):
        self.is_active = active

    def active(self):
        return self.is_active


class ExtensionHandoverTests(_Spawning):
    """While the GNOME Shell extension runs, hold2tg's own history stands aside."""

    def test_the_recorder_stops_while_the_extension_runs_and_comes_back_after(self):
        shell = FakeShell(False)
        recorder = Recorder(shell=shell)
        recorder.check(0.0)
        self.assertEqual(len(self.started), 1)
        shell.is_active = True
        recorder.check(5.0)
        self.assertTrue(self.started[0].terminated)
        recorder.check(10.0)
        self.assertEqual(len(self.started), 1)                # not restarted meanwhile
        shell.is_active = False
        recorder.check(15.0)
        self.assertEqual(len(self.started), 2)

    def test_the_box_is_left_to_the_extension(self):
        box = HistoryBox(Notifier(False), FakeShell(True))
        box.toggle()
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
