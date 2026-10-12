import sys
import threading
import time
import unittest
from unittest import mock

from tests import fake_evdev

from hold2tg import listener as listener_mod  # noqa: E402
from hold2tg.config import Config  # noqa: E402
from hold2tg.notify import Notifier  # noqa: E402
from hold2tg.selection import Snapshot  # noqa: E402

CTRL, SHIFT, ALT, S, A, V, T, H = 29, 42, 56, 31, 30, 47, 20, 35
HOLD = 0.4


class RecordingSender:
    def __init__(self):
        self.sent = []
        self.event = threading.Event()

    def send(self, snap):
        self.sent.append(snap)
        self.event.set()
        return True


class RecordingFetcher:
    def __init__(self):
        self.fetched = 0
        self.synced = 0
        self.event = threading.Event()

    def fetch(self):
        self.fetched += 1
        self.event.set()
        return True

    def sync(self):
        self.synced += 1


class RecordingTranslator:
    def __init__(self):
        self.shown = []
        self.event = threading.Event()

    def show(self, text):
        self.shown.append(text)
        self.event.set()


class RecordingHistoryBox:
    def __init__(self):
        self.toggled = []
        self.event = threading.Event()

    def toggle(self):
        self.toggled.append(time.monotonic())
        self.event.set()


class RecordingRecorder:
    def __init__(self):
        self.checks = 0
        self.stopped = False

    def check(self, now):
        self.checks += 1

    def stop(self):
        self.stopped = True


class _ListenerCase(unittest.TestCase):
    FETCH = ""      # the fetch, translate and history hotkeys are off
    TRANSLATE = ""  # unless a test case turns them on
    HISTORY = ""

    def setUp(self):
        fake_evdev.registry.clear()
        self.kbd = fake_evdev.FakeKeyboard("/dev/input/event3", range(1, 120), "AT keyboard")
        fake_evdev.FakeKeyboard("/dev/input/event0", [116], "Power Button")      # not a keyboard
        fake_evdev.FakeKeyboard("/dev/input/event9", range(1, 120), "Locked", denied=True)
        self.captures = []

        def capture(backend="auto", state_path=""):
            self.captures.append(time.monotonic())
            return Snapshot(kind="text", text="selected at press time")

        patches = [
            mock.patch.object(listener_mod.glob, "glob",
                              side_effect=lambda _p: sorted(fake_evdev.registry)),
            mock.patch.object(listener_mod.selection, "capture", side_effect=capture),
            mock.patch.object(listener_mod.selection, "read_primary_text",
                              side_effect=lambda backend="auto": self.highlighted.pop(0)),
            mock.patch.object(listener_mod, "RESCAN_SECONDS", 0.2),
            # the daemon imports evdev lazily; hand it the simulated one
            mock.patch.dict(sys.modules, {"evdev": fake_evdev}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.sender = RecordingSender()
        self.fetcher = RecordingFetcher()
        self.translator = RecordingTranslator()
        self.highlighted = []
        self.history_box = RecordingHistoryBox()
        self.recorder = RecordingRecorder()
        cfg = Config(hotkey="ctrl+shift+s", hold_seconds=HOLD, notifications=False,
                     fetch_hotkey=self.FETCH, fetch_hold_seconds=HOLD,
                     translate_hotkey=self.TRANSLATE, translate_hold_seconds=HOLD,
                     history_hotkey=self.HISTORY, history_hold_seconds=0)
        self.listener = listener_mod.Listener(cfg, self.sender, Notifier(False), self.fetcher,
                                              self.translator, self.history_box, self.recorder)
        thread = threading.Thread(target=self.listener.run, daemon=True)
        self.thread = thread
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.listener.stop)
        deadline = time.monotonic() + 2
        while "/dev/input/event3" not in self.listener.devices and time.monotonic() < deadline:
            time.sleep(0.01)



class ListenerTests(_ListenerCase):
    def test_only_real_readable_keyboards_are_used(self):
        self.assertEqual(list(self.listener.devices), ["/dev/input/event3"])
        self.assertEqual(self.listener.rejected, {"/dev/input/event0"})
        self.assertEqual(self.listener.denied, {"/dev/input/event9"})

    def test_full_hold_sends_what_was_selected_at_press_time(self):
        t0 = time.monotonic()
        self.kbd.press(CTRL, SHIFT, S)
        self.assertTrue(self.sender.event.wait(3), "nothing was sent")
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, HOLD)
        self.assertLess(elapsed, HOLD + 0.3)
        self.assertEqual(self.sender.sent[0].text, "selected at press time")
        self.assertLess(self.captures[0] - t0, 0.2)       # captured on press, not on fire
        time.sleep(HOLD * 2)                              # keep holding: no repeat send
        self.assertEqual(len(self.sender.sent), 1)
        self.kbd.release(S, SHIFT, CTRL)

    def test_short_press_sends_nothing(self):
        self.kbd.press(CTRL, SHIFT, S)
        time.sleep(HOLD / 2)
        self.kbd.release(S)
        self.assertFalse(self.sender.event.wait(HOLD * 2))
        self.assertEqual(self.sender.sent, [])

    def test_two_holds_send_twice(self):
        for _ in range(2):
            self.sender.event.clear()
            self.kbd.press(CTRL, SHIFT, S)
            self.assertTrue(self.sender.event.wait(3))
            self.kbd.release(S, SHIFT, CTRL)
            time.sleep(0.05)
        self.assertEqual(len(self.sender.sent), 2)

    def test_other_shortcut_is_ignored(self):
        self.kbd.press(CTRL, SHIFT, A)
        self.assertFalse(self.sender.event.wait(HOLD * 2))

    def test_unplug_during_hold_cancels_and_hotplug_is_picked_up(self):
        self.kbd.press(CTRL, SHIFT, S)
        time.sleep(HOLD / 4)
        self.kbd.unplug()
        self.assertFalse(self.sender.event.wait(HOLD * 2))
        new = fake_evdev.FakeKeyboard("/dev/input/event5", range(1, 120), "USB keyboard")
        deadline = time.monotonic() + 2
        while "/dev/input/event5" not in self.listener.devices and time.monotonic() < deadline:
            time.sleep(0.02)
        new.press(CTRL, SHIFT, S)
        self.assertTrue(self.sender.event.wait(3))

    def test_dropped_events_trigger_a_resync(self):
        # The kernel lost the key-up events: SYN_DROPPED must make us re-read
        # the real key state instead of believing the combo is still held.
        self.kbd.press(CTRL, SHIFT, S)
        time.sleep(HOLD / 4)
        self.kbd.down.clear()
        self.kbd.emit(fake_evdev.EV_SYN, fake_evdev.SYN_DROPPED, 0)
        self.assertFalse(self.sender.event.wait(HOLD * 2))


class FetchHotkeyTests(_ListenerCase):
    FETCH = "ctrl+alt+v"

    def test_fetch_needs_the_full_hold_and_fires_once_per_hold(self):
        self.kbd.press(CTRL, ALT, V)
        time.sleep(HOLD / 2)
        self.kbd.release(V)
        self.assertFalse(self.fetcher.event.wait(HOLD * 2))   # let go too early
        self.kbd.release(ALT, CTRL)
        t0 = time.monotonic()
        self.kbd.press(CTRL, ALT, V)
        self.assertTrue(self.fetcher.event.wait(3), "nothing was fetched")
        self.assertGreaterEqual(time.monotonic() - t0, HOLD)
        time.sleep(HOLD * 2)                                  # keep holding: no repeat
        self.kbd.release(V, ALT, CTRL)
        self.assertEqual(self.fetcher.fetched, 1)
        self.assertEqual(self.sender.sent, [])

    def test_send_hotkey_still_works_and_the_channel_is_checked_at_start(self):
        self.kbd.press(CTRL, SHIFT, S)
        self.assertTrue(self.sender.event.wait(3))
        self.assertEqual(self.fetcher.fetched, 0)
        self.assertGreaterEqual(self.fetcher.synced, 1)

    def test_without_a_fetch_hotkey_the_channel_is_left_alone(self):
        self.listener.stop()
        cfg = Config(hotkey="ctrl+shift+s", fetch_hotkey="", notifications=False)
        listener = listener_mod.Listener(cfg, self.sender, Notifier(False), RecordingFetcher())
        self.assertIsNone(listener.fetcher)
        self.assertEqual(len(listener.hotkeys), 1)


class TranslateHotkeyTests(_ListenerCase):
    TRANSLATE = "ctrl+alt+t"

    def test_text_highlighted_at_press_time_is_translated_after_the_hold(self):
        self.highlighted = ["hello at press time", "something else later"]
        t0 = time.monotonic()
        self.kbd.press(CTRL, ALT, T)
        self.assertTrue(self.translator.event.wait(3), "no translation box")
        self.assertGreaterEqual(time.monotonic() - t0, HOLD)
        self.kbd.release(T, ALT, CTRL)
        self.assertEqual(self.translator.shown, ["hello at press time"])
        self.assertEqual(self.sender.sent, [])

    def test_short_press_opens_nothing(self):
        self.highlighted = ["x"]
        self.kbd.press(CTRL, ALT, T)
        time.sleep(HOLD / 2)
        self.kbd.release(T)
        self.assertFalse(self.translator.event.wait(HOLD * 2))


class HistoryHotkeyTests(_ListenerCase):
    HISTORY = "ctrl+alt+h"

    def test_a_press_opens_the_history_at_once_and_only_once(self):
        t0 = time.monotonic()
        self.kbd.press(CTRL, ALT, H)
        self.assertTrue(self.history_box.event.wait(3), "no history box")
        self.assertLess(self.history_box.toggled[0] - t0, 0.2)   # no holding needed
        time.sleep(0.3)                                           # held on: still once
        self.kbd.release(H, ALT, CTRL)
        self.assertEqual(len(self.history_box.toggled), 1)
        self.history_box.event.clear()
        self.kbd.press(CTRL, ALT, H)                              # again: toggles again
        self.assertTrue(self.history_box.event.wait(3))
        self.kbd.release(H, ALT, CTRL)
        self.assertEqual(len(self.history_box.toggled), 2)
        self.assertEqual(self.sender.sent, [])

    def test_the_recorder_runs_with_the_daemon(self):
        deadline = time.monotonic() + 2
        while not self.recorder.checks and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(self.recorder.checks, 1)
        self.listener.stop()
        self.thread.join(2)
        self.assertTrue(self.recorder.stopped)

    def test_turned_off_there_is_no_hotkey_and_nothing_is_recorded(self):
        cfg = Config(hotkey="ctrl+shift+s", history_hotkey="", notifications=False)
        listener = listener_mod.Listener(cfg, self.sender, Notifier(False), None, None,
                                         RecordingHistoryBox(), RecordingRecorder())
        self.assertEqual([h.name for h in listener.hotkeys], ["send"])
        self.assertIsNone(listener.recorder)


if __name__ == "__main__":
    unittest.main()
