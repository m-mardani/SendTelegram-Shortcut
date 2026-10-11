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

CTRL, SHIFT, S, A = 29, 42, 31, 30
HOLD = 0.4


class RecordingSender:
    def __init__(self):
        self.sent = []
        self.event = threading.Event()

    def send(self, snap):
        self.sent.append(snap)
        self.event.set()
        return True


class ListenerTests(unittest.TestCase):
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
            mock.patch.object(listener_mod, "RESCAN_SECONDS", 0.2),
            # the daemon imports evdev lazily; hand it the simulated one
            mock.patch.dict(sys.modules, {"evdev": fake_evdev}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.sender = RecordingSender()
        cfg = Config(hold_seconds=HOLD, notifications=False)
        self.listener = listener_mod.Listener(cfg, self.sender, Notifier(False))
        thread = threading.Thread(target=self.listener.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.listener.stop)
        deadline = time.monotonic() + 2
        while "/dev/input/event3" not in self.listener.devices and time.monotonic() < deadline:
            time.sleep(0.01)

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


if __name__ == "__main__":
    unittest.main()
