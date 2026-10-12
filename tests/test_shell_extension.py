"""The clipboard history extension, tested inside a real GNOME Shell.

The shell runs headless, with D-Bus buses (session and system) and XDG folders
of its own, so the desktop the tests run on is not touched: it does not see
the real session (whose lock screen would turn its extensions off), nor GDM,
nor the real runtime folder (where the shell keeps its crash marker). It needs gnome-shell, wl-clipboard
and GTK 4, and takes half a minute, so it only runs when asked:

    HOLD2TG_SHELL_TESTS=1 python3 -m unittest tests.test_shell_extension
"""
import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UUID = "hold2tg-clipboard@m-mardani.github.io"
SOURCE = os.path.join(os.path.dirname(HERE), "gnome-shell", UUID)
BUS = "io.github.m_mardani.hold2tg.Clipboard"
PATH = "/io/github/m_mardani/hold2tg/Clipboard"
SCHEMA = "org.gnome.shell.extensions.hold2tg-clipboard"
# Linux input codes, as the extension's PressKeys takes them.
META, ALT, H, J, ESC, DOWN, ENTER, CAPS, C, SHIFT = 125, 56, 35, 36, 1, 108, 28, 58, 46, 42
LETTERS = {"p": 25, "a": 30, "s": 31, "t": 20, "e": 18}
PNG = base64.b64decode(  # 1x1 red pixel
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")

NEEDED = ("gnome-shell", "dbus-daemon", "gdbus", "gsettings", "glib-compile-schemas", "wl-copy",
          "wl-paste")


@unittest.skipUnless(os.environ.get("HOLD2TG_SHELL_TESTS") == "1",
                     "set HOLD2TG_SHELL_TESTS=1 to run the GNOME Shell tests")
class ExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [t for t in NEEDED if not shutil.which(t)]
        if missing:
            raise unittest.SkipTest("missing: " + ", ".join(missing))
        cls.tmp = tempfile.mkdtemp(prefix="hold2tg-shell-")
        # Cleanups run even when this set-up fails half way: nothing is left running.
        cls.addClassCleanup(cls.remove_tmp)
        home = lambda *p: os.path.join(cls.tmp, *p)  # noqa: E731
        ext = home("data", "gnome-shell", "extensions", UUID)
        shutil.copytree(SOURCE, ext)
        subprocess.run(["glib-compile-schemas", os.path.join(ext, "schemas")], check=True)
        cls.state = home("state", "hold2tg")
        os.makedirs(cls.state)
        with open(os.path.join(cls.state, "clipboard.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "items": [{"text": "from the old box", "at": 1}]}, fh)
        os.makedirs(home("runtime"), mode=0o700)
        cls.display = "hold2tg-test"
        cls.env = dict(os.environ, XDG_CONFIG_HOME=home("config"), XDG_DATA_HOME=home("data"),
                       XDG_STATE_HOME=home("state"), XDG_CACHE_HOME=home("cache"),
                       XDG_RUNTIME_DIR=home("runtime"), WAYLAND_DISPLAY=cls.display,
                       GIO_USE_VFS="local", HOLD2TG_CLIPBOARD_DEBUG="1")
        for name in ("DISPLAY", "XDG_SESSION_ID", "XDG_SEAT", "XDG_VTNR"):
            cls.env.pop(name, None)
        for var in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS"):
            bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=cls.env)
            cls.addClassCleanup(bus.wait)
            cls.addClassCleanup(bus.kill)
            cls.env[var] = bus.stdout.readline().decode().strip()
        for schema, key, value in (
                ("org.gnome.shell", "disable-user-extensions", "false"),
                ("org.gnome.shell", "enabled-extensions", "['%s']" % UUID),
                ("org.gnome.shell", "welcome-dialog-last-shown-version", "'999'"),
                # As hold2tg sets it up for Caps Lock hotkeys, with a Persian layout too.
                ("org.gnome.desktop.input-sources", "sources", "[('xkb', 'us'), ('xkb', 'ir')]"),
                ("org.gnome.desktop.input-sources", "xkb-options", "['caps:hyper']"),
                # A plain background: the shell starts its extensions only once the
                # startup animation has run, and that waits for the wallpaper to load.
                ("org.gnome.desktop.background", "picture-options", "'none'"),
                ("org.gnome.desktop.background", "picture-uri", "''"),
                ("org.gnome.desktop.background", "picture-uri-dark", "''")):
            subprocess.run(["gsettings", "set", schema, key, value], env=cls.env, check=True)
        cls.shell = None
        cls.apps = []
        cls.addClassCleanup(cls.stop_shell)
        cls.addClassCleanup(lambda: [app.kill() for app in cls.apps])
        cls.start_shell()

    @classmethod
    def remove_tmp(cls):
        # Services the bus started (the document portal) may have mounted FUSE file
        # systems in the runtime folder; they let go once the bus is gone.
        for name in ("doc", "gvfs"):
            path = os.path.join(cls.tmp, "runtime", name)
            if os.path.ismount(path):
                subprocess.run(["fusermount3", "-uz", path], stderr=subprocess.DEVNULL)
        for _ in range(20):
            shutil.rmtree(cls.tmp, ignore_errors=True)
            if not os.path.exists(cls.tmp):
                return
            time.sleep(0.25)

    @classmethod
    def start_shell(cls):
        # The first shell on a brand-new profile often starts but never gets to
        # loading its extensions (its log says nothing about it); the next one does.
        for attempt in range(1, 4):
            cls.shell_log = open(os.path.join(cls.tmp, "shell.log"), "ab")
            cls.shell = subprocess.Popen(
                ["gnome-shell", "--headless", "--no-x11", "--virtual-monitor", "1280x800",
                 "--wayland-display", cls.display],
                env=cls.env, stdin=subprocess.DEVNULL, stdout=cls.shell_log,
                stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if "true" in cls.gdbus("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                       "org.freedesktop.DBus.NameHasOwner", BUS):
                    time.sleep(0.5)  # the first copy is read right after
                    return
                time.sleep(0.25)
            if attempt < 3:
                print("\n(the test shell did not load its extensions; starting another)",
                      file=sys.stderr)
                cls.stop_shell()
        cls.shell_log.flush()
        with open(cls.shell_log.name, "rb") as fh:
            tail = fh.read().decode("utf-8", "replace").splitlines()[-25:]
        info = cls.gdbus("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions.GetExtensionInfo",
                         UUID)
        enabled = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                                 stdout=subprocess.PIPE, env=cls.env).stdout.decode()
        enabled += " | mode: " + cls.gdbus("org.gnome.Shell", "/org/gnome/Shell",
                                           "org.freedesktop.DBus.Properties.Get",
                                           "org.gnome.Shell", "Mode").strip()
        for args in (["get", "org.gnome.shell", "disable-user-extensions"],
                     ["writable", "org.gnome.shell", "enabled-extensions"]):
            enabled += " | %s: %s" % (args[-1], subprocess.run(
                ["gsettings", *args], stdout=subprocess.PIPE, env=cls.env).stdout.decode().strip())
        raise RuntimeError("the extension did not come up.\nshell says: %s\nenabled: %s\n%s"
                           % (info.strip()[:1500], enabled.strip(), "\n".join(tail)))

    @classmethod
    def stop_shell(cls):
        if cls.shell is not None:
            cls.shell.send_signal(signal.SIGTERM)
            try:
                cls.shell.wait(10)
            except subprocess.TimeoutExpired:
                cls.shell.kill()
            cls.shell = None
            cls.shell_log.close()

    @classmethod
    def gdbus(cls, dest, path, method, *args):
        out = subprocess.run(["gdbus", "call", "--session", "--dest", dest, "--object-path", path,
                              "--method", method, *args],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=cls.env, timeout=10)
        return out.stdout.decode()

    # -- helpers --
    def call(self, method, *args):
        return self.gdbus(BUS, PATH, "%s.%s" % (BUS, method), *args)

    def history(self):
        out = self.call("GetHistory")
        return json.loads(out[out.index("'") + 1:out.rindex("'")])

    def texts(self):
        return [i.get("text") for i in self.history()]

    def layout(self):
        out = self.call("Layout")
        return json.loads(out[out.index("'") + 1:out.rindex("'")])

    def is_open(self):
        return "true" in self.call("IsOpen")

    def keys(self, *codes):
        self.call("PressKeys", "[%s]" % ", ".join("uint32 %d" % c for c in codes))

    def click(self, box):
        x, y, w, h = box
        self.call("Click", str(x + w / 2), str(y + h / 2))

    def copy(self, data, mime="text/plain;charset=utf-8"):
        subprocess.run(["wl-copy", "--type", mime],
                       input=data if isinstance(data, bytes) else data.encode(),
                       env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, check=True)

    def clipboard(self):
        return subprocess.run(["wl-paste", "--no-newline"], stdout=subprocess.PIPE,
                              env=self.env, timeout=10).stdout.decode()

    def setting(self, key, value):
        subprocess.run(["gsettings", "--schemadir", os.path.join(
            self.env["XDG_DATA_HOME"], "gnome-shell", "extensions", UUID, "schemas"),
            "set", SCHEMA, key, value], env=self.env, check=True)

    def until(self, check, timeout=5.0, message="timed out"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.1)
        self.fail(message)

    def app(self, *args):
        report = os.path.join(self.tmp, "app-%d.txt" % len(self.apps))
        open(report, "w").close()
        env = dict(self.env, GDK_BACKEND="wayland")
        app = subprocess.Popen([sys.executable, os.path.join(HERE, "shell_app.py"), report, *args],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.apps.append(app)
        self.addCleanup(app.wait)
        self.addCleanup(app.kill)  # gone again after the test that started it
        time.sleep(1.5)  # up and focused
        return report

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def setUp(self):
        if self.is_open():
            self.call("Hide")

    # -- the tests (in name order) --
    def test_01_takes_over_the_old_history_once(self):
        self.assertIn("from the old box", self.texts())
        self.until(lambda: os.path.exists(os.path.join(self.state, "shell-clipboard.json")))
        with open(os.path.join(self.state, "clipboard.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], 1)  # the old file is left as it was

    def test_02_records_copies_newest_first_without_repeats(self):
        self.copy("first")
        self.copy("دومی فارسی")
        self.until(lambda: self.texts()[:2] == ["دومی فارسی", "first"])
        self.copy("first")
        self.until(lambda: self.texts()[0] == "first")
        self.assertEqual(self.texts().count("first"), 1)

    def test_03_files_are_not_recorded_but_pictures_are(self):
        before = len(self.history())
        self.copy(b"copy\nfile:///tmp/x", "x-special/gnome-copied-files")
        time.sleep(1)
        self.assertEqual(len(self.history()), before)
        self.copy(PNG, "image/png")
        item = self.until(lambda: self.history()[0] if self.history()[0]["type"] == "image" else None)
        picture = os.path.join(self.state, "shell-clipboard-images", item["hash"] + ".img")
        self.until(lambda: os.path.exists(picture))
        self.assertEqual(os.stat(picture).st_mode & 0o077, 0)

    def test_04_secrets_are_not_recorded(self):
        report = self.app("--secret", "hunter2")
        self.until(lambda: self.read(report) == "secret copied")
        time.sleep(1)
        self.assertNotIn("hunter2", self.texts())

    def test_05_record_off(self):
        self.setting("record", "false")
        try:
            self.copy("not while it is off")
            time.sleep(1)
            self.assertNotIn("not while it is off", self.texts())
        finally:
            self.setting("record", "true")

    def test_06_the_shortcut_opens_and_closes_it_and_follows_the_setting(self):
        self.keys(META, ALT, H)
        self.until(self.is_open, message="Super+Alt+H did not open it")
        self.keys(META, ALT, H)
        self.until(lambda: not self.is_open(), message="Super+Alt+H did not close it")
        self.keys(META, ALT, H)
        self.until(self.is_open)
        self.keys(ESC)
        self.until(lambda: not self.is_open(), message="Escape did not close it")
        self.setting("toggle-history", "['<Super><Alt>j']")
        try:
            time.sleep(0.5)
            self.keys(META, ALT, H)
            time.sleep(0.7)
            self.assertFalse(self.is_open())
            self.keys(META, ALT, J)
            self.until(self.is_open, message="the new shortcut did not open it")
            self.keys(META, ALT, J)
            self.until(lambda: not self.is_open(), message="the new shortcut did not close it")
        finally:
            self.setting("toggle-history", "['<Super><Alt>h']")

    def test_07_picking_an_item_pastes_it_into_the_window(self):
        self.copy("paste me")
        self.copy("something newer")
        self.until(lambda: self.texts()[0] == "something newer")
        report = self.app()
        self.call("Show")
        self.until(self.is_open)
        index = [r["text"] for r in self.layout()].index("paste me")
        self.click(self.layout()[index]["row"])
        self.until(lambda: self.read(report) == "paste me", message="nothing was pasted: %r"
                   % self.read(report))
        self.assertFalse(self.is_open())
        self.assertEqual(self.clipboard(), "paste me")

    def test_08_keyboard_down_and_enter(self):
        self.copy("one")
        self.copy("two")
        self.until(lambda: self.texts()[:2] == ["two", "one"])
        self.call("Show")
        self.until(self.is_open)
        self.keys(DOWN)
        self.keys(ENTER)
        self.until(lambda: self.clipboard() == "one")

    def test_08b_it_is_a_small_box_in_the_middle(self):
        self.call("Show")
        self.until(self.is_open)
        x, y, w, h = self.layout()[0]["row"]
        self.assertLess(w, 600)                      # not stretched over the 1280 px screen
        self.assertLess(abs((x + w / 2) - 640), 40)  # centred

    def test_09_typing_searches(self):
        self.copy("paste me")
        self.call("Show")
        self.until(self.is_open)
        for letter in "paste":
            self.keys(LETTERS[letter])
        rows = self.until(lambda: [r["text"] for r in self.layout()]
                          if len(self.layout()) == 1 else None, message="search did not filter")
        self.assertEqual(rows, ["paste me"])

    def test_10_pin_and_delete_with_the_pointer(self):
        self.copy("keep me")
        self.copy("delete me")
        self.until(lambda: self.texts()[:2] == ["delete me", "keep me"])
        self.copy("on the clipboard")
        self.until(lambda: self.texts()[0] == "on the clipboard")
        self.call("Show")
        self.until(self.is_open)
        rows = {r["text"]: r for r in self.layout()}
        self.click(rows["keep me"]["pin"])
        self.until(lambda: any(i.get("text") == "keep me" and i["pinned"] for i in self.history()),
                   message="the pin button did not pin")
        self.assertTrue(self.is_open(), "the pin button also picked the item")
        # Picking moves an item to the top; the clipboard itself cannot be read while
        # the panel holds the keyboard.
        self.assertEqual(self.texts()[0], "on the clipboard")
        self.assertEqual(self.layout()[0]["text"], "keep me")  # pinned ones come first
        rows = {r["text"]: r for r in self.layout()}
        self.click(rows["delete me"]["remove"])
        self.until(lambda: "delete me" not in self.texts(), message="the delete button did not delete")
        self.assertTrue(self.is_open(), "the delete button also picked an item")

    def test_11_clicking_outside_closes_it(self):
        self.call("Show")
        self.until(self.is_open)
        self.call("Click", "20", "780")
        self.until(lambda: not self.is_open())

    def test_12_clear_all_keeps_the_pinned(self):
        self.call("Clear")
        self.assertEqual(self.texts(), ["keep me"])

    def test_13_kept_across_a_restart(self):
        time.sleep(1)  # written a moment after the last change
        self.stop_shell()
        self.start_shell()
        items = self.history()
        self.assertEqual([(i.get("text"), i["pinned"]) for i in items], [("keep me", True)])


    def test_14_capslock_hotkeys_do_not_reach_the_apps(self):
        self.setting("catch-keys", "['<Mod3>c', '<Mod3>v', '<Mod3>t']")
        report = self.app()
        self.keys(C)
        self.until(lambda: self.read(report) == "c", message="a plain c was not typed")
        self.keys(CAPS, C)
        time.sleep(0.6)
        self.assertEqual(self.read(report), "c", "Caps Lock+C reached the app")
        self.assertNotIn("true", self.call("CapsLock"), "Caps Lock turned capitals on")
        self.keys(SHIFT, C)
        self.until(lambda: self.read(report) == "cC", message="typing is not normal any more")

    def test_15_capslock_h_opens_and_closes_the_history(self):
        self.setting("toggle-history", "['<Mod3>h']")
        try:
            report = self.app()
            self.keys(CAPS, H)
            self.until(self.is_open, message="Caps Lock+H did not open it")
            self.keys(CAPS, H)
            self.until(lambda: not self.is_open(), message="Caps Lock+H did not close it")
            self.assertEqual(self.read(report), "", "Caps Lock+H reached the app")
        finally:
            self.setting("toggle-history", "['<Super><Alt>h']")

    def test_16_the_same_in_the_persian_layout(self):
        self.setting("catch-keys", "['<Mod3>c']")
        report = self.app()
        self.call("UseInputSource", "uint32 1")
        try:
            time.sleep(0.8)
            self.keys(C)
            self.until(lambda: self.read(report) == "ز", message="the Persian layout is not on")
            self.keys(CAPS, C)
            time.sleep(0.6)
            self.assertEqual(self.read(report), "ز", "Caps Lock+C reached the app")
        finally:
            self.call("UseInputSource", "uint32 0")
            time.sleep(0.5)

    def test_17_a_key_alone_is_never_taken(self):
        self.setting("catch-keys", "['c']")
        report = self.app()
        self.keys(C)
        self.until(lambda: self.read(report) == "c", message="a key alone was taken from the apps")
        self.setting("catch-keys", "@as []")


if __name__ == "__main__":
    unittest.main()
