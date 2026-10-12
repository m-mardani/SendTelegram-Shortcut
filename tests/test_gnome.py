import json
import os
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  (keeps the real state dir out of reach)
from hold2tg.config import Config
from hold2tg.gnome import CAPS_OPTION, SHELL_BUS, Shell, accelerator, uses_capslock
from hold2tg.hotkey import parse_combo

CAPS = "capslock+c"


class FakeGsettings:
    """gsettings/gdbus as far as Shell uses them, with a store of its own."""

    def __init__(self, xkb="@as []"):
        self.values = {("org.gnome.desktop.input-sources", "xkb-options"): xkb}
        self.calls = []
        self.bus_answer = (0, b"(true,)\n")

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "gdbus":
            code, out = self.bus_answer
            return mock.Mock(returncode=code, stdout=out)
        if "--schemadir" in argv:
            argv = argv[:1] + argv[3:]
        if argv[1] == "get":
            return mock.Mock(returncode=0, stdout=(self.values[(argv[2], argv[3])] + "\n").encode())
        self.values[(argv[2], argv[3])] = argv[4]
        return mock.Mock(returncode=0, stdout=b"")


class AcceleratorTests(unittest.TestCase):
    def test_accelerator(self):
        self.assertEqual(accelerator("super+alt+h"), "<Super><Alt>h")
        self.assertEqual(accelerator("Win + Alt + F9"), "<Super><Alt>F9")
        self.assertEqual(accelerator("ctrl+shift+dot"), "<Control><Shift>period")
        self.assertEqual(accelerator("super+pageup"), "<Super>Page_Up")

    def test_caps_lock_is_the_real_mod3_never_hyper(self):
        # Without caps:hyper Hyper maps to nothing, and <Hyper>h would be a plain h.
        self.assertEqual(accelerator("capslock+h"), "<Mod3>h")
        self.assertEqual(accelerator("Caps+Shift+T"), "<Mod3><Shift>t")

    def test_uses_capslock(self):
        self.assertTrue(uses_capslock("capslock+c"))
        self.assertTrue(uses_capslock("caps + v"))
        self.assertFalse(uses_capslock("super+alt+c"))
        self.assertFalse(uses_capslock(""))

    def test_caps_lock_is_held_like_a_modifier(self):
        combo = parse_combo("CapsLock+C")
        self.assertEqual(combo.all_codes, {58, 46})
        self.assertEqual(combo.main_codes, {46})
        self.assertTrue(combo.matches({58, 46}))
        self.assertFalse(combo.matches({46}))


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.fake = FakeGsettings()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.shell = Shell(run=self.fake, clock=lambda: self.now,
                           marker=os.path.join(self.tmp.name, "gnome.json"))
        env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_active_asks_the_bus_and_trusts_the_answer_for_a_while(self):
        self.assertTrue(self.shell.active())
        self.assertIn(SHELL_BUS, self.fake.calls[0])
        self.fake.bus_answer = (0, b"(false,)\n")
        self.now = 1.0
        self.assertTrue(self.shell.active())                  # cached
        self.now = 10.0
        self.assertFalse(self.shell.active())
        self.fake.bus_answer = (1, b"")                       # no session bus, say
        self.now = 20.0
        self.assertFalse(self.shell.active())

    def test_sync_needs_the_extension_installed(self):
        self.assertFalse(self.shell.sync(Config()))
        self.assertEqual(self.fake.calls, [])

    def install(self):
        folder = self.shell.schema_dir()
        os.makedirs(folder)
        open(os.path.join(folder, "gschemas.compiled"), "w").close()
        return folder

    def setting(self, key):
        return self.fake.values[("org.gnome.shell.extensions.hold2tg-clipboard", key)]

    def test_sync_passes_hold2tgs_settings_on(self):
        self.install()
        self.assertTrue(self.shell.sync(Config(hotkey="super+alt+c", fetch_hotkey="super+alt+v",
                                               translate_hotkey="", history_hotkey="super+alt+j",
                                               language="fa", history_size=5000)))
        self.assertEqual(self.setting("toggle-history"), "['<Super><Alt>j']")
        self.assertEqual(self.setting("record"), "true")
        self.assertEqual(self.setting("language"), "'fa'")
        self.assertEqual(self.setting("history-size"), "500")  # within the extension's range
        self.assertEqual(self.setting("catch-keys"), "@as []")  # nothing Caps Lock

    def test_caps_lock_hotkeys_are_handed_over_to_be_kept_from_the_apps(self):
        self.install()
        self.shell.sync(Config(hotkey="capslock+c", fetch_hotkey="capslock+v",
                               translate_hotkey="super+alt+t", history_hotkey="capslock+h"))
        self.assertEqual(self.setting("catch-keys"), "['<Mod3>c', '<Mod3>v']")
        self.assertEqual(self.setting("toggle-history"), "['<Mod3>h']")

    def test_an_empty_history_hotkey_turns_the_extension_off_too(self):
        self.install()
        self.shell.sync(Config(history_hotkey=""))
        self.assertEqual(self.setting("toggle-history"), "@as []")
        self.assertEqual(self.setting("record"), "false")


class KeyboardOptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.marker = os.path.join(self.tmp.name, "gnome.json")

    def shell(self, xkb):
        self.fake = FakeGsettings(xkb)
        return Shell(run=self.fake, marker=self.marker)

    def options(self):
        return self.fake.values[("org.gnome.desktop.input-sources", "xkb-options")]

    def test_caps_lock_hotkeys_turn_the_option_on_next_to_the_others(self):
        self.assertIsNone(self.shell("['compose:ralt']").keyboard(Config(hotkey=CAPS)))
        self.assertEqual(self.options(), "['compose:ralt', '%s']" % CAPS_OPTION)
        with open(self.marker) as fh:
            self.assertEqual(json.load(fh), {"added_xkb_options": [CAPS_OPTION]})

    def test_and_off_again_once_no_hotkey_uses_caps_lock(self):
        self.shell("@as []").keyboard(Config(hotkey=CAPS))
        shell = self.shell(self.options())
        self.assertIsNone(shell.keyboard(Config(hotkey="super+alt+c", fetch_hotkey="",
                                                translate_hotkey="", history_hotkey="")))
        self.assertEqual(self.options(), "@as []")

    def test_an_option_the_user_set_is_left_alone(self):
        self.shell("['%s']" % CAPS_OPTION).keyboard(Config(hotkey=CAPS))  # already there
        self.assertFalse(os.path.exists(self.marker))
        self.shell("['%s']" % CAPS_OPTION).keyboard(Config(
            hotkey="super+alt+c", fetch_hotkey="", translate_hotkey="", history_hotkey=""))
        self.assertEqual(self.options(), "['%s']" % CAPS_OPTION)  # not ours to remove

    def test_another_caps_lock_role_is_reported_not_overridden(self):
        problem = self.shell("['caps:escape']").keyboard(Config(hotkey=CAPS))
        self.assertIn("caps:escape", problem)
        self.assertEqual(self.options(), "['caps:escape']")

    def test_nothing_happens_without_caps_lock_hotkeys(self):
        self.assertIsNone(self.shell("@as []").keyboard(Config(
            hotkey="super+alt+c", fetch_hotkey="", translate_hotkey="", history_hotkey="")))
        self.assertEqual([c for c in self.fake.calls if c[1] == "set"], [])


if __name__ == "__main__":
    unittest.main()
