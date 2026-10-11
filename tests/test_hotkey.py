import unittest

from hold2tg.hotkey import KEYS, MODIFIERS, HoldDetector, HotkeyError, parse_combo

CTRL_L, CTRL_R, SHIFT_L, SHIFT_R, ALT_L, KEY_S, KEY_A = 29, 97, 42, 54, 56, 31, 30


def detector(hold=5.0, combo="ctrl+shift+s"):
    return HoldDetector(parse_combo(combo), hold)


class ParseTests(unittest.TestCase):
    def test_default_combo(self):
        c = parse_combo("Ctrl + Shift + S")
        self.assertEqual(c.label, "ctrl+shift+s")
        self.assertEqual(c.all_codes, {CTRL_L, CTRL_R, SHIFT_L, SHIFT_R, KEY_S})
        self.assertEqual(c.main_codes, {KEY_S})

    def test_aliases_and_errors(self):
        self.assertEqual(parse_combo("control+win+f9").label, "control+win+f9")
        for bad in ("", "ctrl+shift", "ctrl+nope", "ctrl+ctrl+s"):
            with self.assertRaises(HotkeyError):
                parse_combo(bad)

    def test_codes_match_the_kernel_table(self):
        try:
            from evdev import ecodes
        except ImportError:
            self.skipTest("evdev not installed")
        special = {"esc": "ESC", "print": "SYSRQ", "grave": "GRAVE"}
        for name, code in KEYS.items():
            kernel = getattr(ecodes, "KEY_" + special.get(name, name).upper())
            self.assertEqual(code, kernel, name)
        self.assertEqual(MODIFIERS["ctrl"], {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL})
        self.assertEqual(MODIFIERS["shift"], {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT})
        self.assertEqual(MODIFIERS["alt"], {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT})
        self.assertEqual(MODIFIERS["super"], {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA})


class HoldTests(unittest.TestCase):
    def press_combo(self, d, t=0.0, dev="kbd"):
        self.assertIsNone(d.key_event(dev, CTRL_L, 1, t))
        self.assertIsNone(d.key_event(dev, SHIFT_L, 1, t))
        self.assertEqual(d.key_event(dev, KEY_S, 1, t), "arm")

    def test_fires_once_after_full_hold(self):
        d = detector()
        self.press_combo(d, 10.0)
        self.assertEqual(d.deadline, 15.0)
        self.assertIsNone(d.poll(14.99))
        self.assertEqual(d.poll(15.0), "fire")
        self.assertIsNone(d.poll(20.0))            # still held: no second fire
        self.assertIsNone(d.deadline)
        self.assertIsNone(d.key_event("kbd", KEY_S, 0, 21.0))
        self.assertEqual(d.key_event("kbd", KEY_S, 1, 22.0), "arm")   # new hold re-arms

    def test_early_release_cancels(self):
        for released in (CTRL_L, SHIFT_L, KEY_S):
            d = detector()
            self.press_combo(d)
            self.assertEqual(d.key_event("kbd", released, 0, 4.9), "cancel")
            self.assertIsNone(d.poll(100.0))

    def test_auto_repeat_is_ignored(self):
        d = detector()
        self.press_combo(d)
        for i in range(50):
            self.assertIsNone(d.key_event("kbd", KEY_S, 2, 0.5 + i * 0.03))
        self.assertEqual(d.poll(5.0), "fire")

    def test_extra_key_breaks_the_combo(self):
        d = detector()
        self.press_combo(d)
        self.assertEqual(d.key_event("kbd", KEY_A, 1, 1.0), "cancel")
        self.assertIsNone(d.poll(9.0))
        # Ctrl+Shift+Alt+S is a different shortcut and must not arm either.
        d = detector()
        d.key_event("kbd", ALT_L, 1, 0)
        d.key_event("kbd", CTRL_L, 1, 0)
        d.key_event("kbd", SHIFT_L, 1, 0)
        self.assertIsNone(d.key_event("kbd", KEY_S, 1, 0))

    def test_plain_ctrl_s_does_not_arm(self):
        d = detector()
        d.key_event("kbd", CTRL_L, 1, 0)
        self.assertIsNone(d.key_event("kbd", KEY_S, 1, 0))
        self.assertIsNone(d.poll(60))

    def test_right_hand_modifiers_and_swapping_sides(self):
        d = detector()
        d.key_event("kbd", CTRL_R, 1, 0)
        d.key_event("kbd", SHIFT_R, 1, 0)
        self.assertEqual(d.key_event("kbd", KEY_S, 1, 0), "arm")
        # adding the other Shift and letting go of the first keeps the hold alive
        self.assertIsNone(d.key_event("kbd", SHIFT_L, 1, 1))
        self.assertIsNone(d.key_event("kbd", SHIFT_R, 0, 2))
        self.assertEqual(d.poll(5.0), "fire")

    def test_keys_spread_over_two_keyboards(self):
        d = detector()
        d.key_event("a", CTRL_L, 1, 0)
        d.key_event("a", SHIFT_L, 1, 0)
        self.assertEqual(d.key_event("b", KEY_S, 1, 0), "arm")
        self.assertEqual(d.drop_device("b", 1.0), "cancel")   # keyboard unplugged

    def test_sync_device(self):
        d = detector()
        self.assertEqual(d.sync_device("kbd", [CTRL_L, SHIFT_L, KEY_S], 0), "arm")
        self.assertEqual(d.sync_device("kbd", [], 1), "cancel")

    def test_zero_seconds_fires_immediately(self):
        d = detector(hold=0)
        self.press_combo(d, 3.0)
        self.assertEqual(d.poll(3.0), "fire")


if __name__ == "__main__":
    unittest.main()
