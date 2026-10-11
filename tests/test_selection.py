import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from hold2tg import config as config_mod
from hold2tg import selection


class NautilusStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "nautilus.json")
        self.file = os.path.join(self.tmp.name, "گزارش.pdf")
        open(self.file, "w").close()

    def write(self, **kw):
        data = {"pid": os.getpid(), "active": True, "files": [self.file], "updated": time.time()}
        data.update(kw)
        with open(self.state, "w") as fh:
            json.dump(data, fh)

    def capture(self, text=""):
        with mock.patch.object(selection, "read_primary_text", return_value=text):
            return selection.capture(state_path=self.state)

    def test_focused_file_manager_wins_over_text(self):
        self.write()
        snap = self.capture(text="some highlighted text")
        self.assertEqual((snap.kind, snap.files), ("files", [self.file]))

    def test_unfocused_file_manager_means_text(self):
        self.write(active=False)
        snap = self.capture(text="some highlighted text")
        self.assertEqual((snap.kind, snap.text), ("text", "some highlighted text"))

    def test_files_left_selected_in_an_unfocused_window_are_never_sent(self):
        self.write(active=False)
        self.assertEqual(self.capture(text="").kind, "none")

    def test_state_of_a_dead_file_manager_is_ignored(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        self.write(pid=proc.pid)
        self.assertEqual(self.capture(text="").kind, "none")

    def test_deleted_files_are_dropped(self):
        self.write(files=[self.file, os.path.join(self.tmp.name, "deleted.txt")])
        self.assertEqual(self.capture().files, [self.file])

    def test_missing_or_corrupt_state_file(self):
        self.assertEqual(self.capture(text="t").kind, "text")
        with open(self.state, "w") as fh:
            fh.write("{not json")
        self.assertEqual(self.capture(text="t").kind, "text")

    def test_whitespace_only_text_is_nothing(self):
        self.assertEqual(self.capture(text="  \n ").kind, "none")


class BackendOrderTests(unittest.TestCase):
    def order(self, env):
        with mock.patch.object(selection.shutil, "which", return_value="/usr/bin/x"):
            return [cmd[0] for cmd in selection._commands("auto", env)]

    def test_x11(self):
        self.assertEqual(self.order({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}),
                         ["xclip", "xsel"])

    def test_wayland(self):
        self.assertEqual(self.order({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}),
                         ["xclip", "wl-paste"])
        self.assertEqual(self.order({"WAYLAND_DISPLAY": "wayland-0"}), ["wl-paste"])

    def test_explicit_backend(self):
        self.assertEqual(selection._commands("wl-paste", {})[0][0], "wl-paste")


@unittest.skipUnless(os.environ.get("DISPLAY") and shutil.which("xclip"),
                     "needs an X display and xclip")
class RealXSelectionTests(unittest.TestCase):
    """Uses the real PRIMARY selection of the X server in $DISPLAY."""

    def own_primary(self, text):
        # xclip stays in the background to serve the selection, so it must not
        # inherit our stdout/stderr.
        subprocess.run(["xclip", "-selection", "primary", "-i"], input=text.encode(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    def test_reads_highlighted_text(self):
        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11"}):
            os.environ.pop("WAYLAND_DISPLAY", None)
            for text in ("plain", "متن فارسی با ایموجی 🙂\nخط دوم\n"):
                self.own_primary(text)
                self.assertEqual(selection.read_primary_text(), text)
                self.assertEqual(selection.read_primary_text("xsel"), text)

    def test_capture_end_to_end(self):
        self.own_primary("from the real selection")
        missing = os.path.join(tempfile.gettempdir(), "hold2tg-no-such-state.json")
        snap = selection.capture(state_path=missing)
        self.assertEqual((snap.kind, snap.text), ("text", "from the real selection"))


class RuntimeDirTests(unittest.TestCase):
    def test_runtime_dir_is_private(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            path = config_mod.runtime_dir()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)
            self.assertEqual(config_mod.nautilus_state_path(),
                             os.path.join(tmp, "hold2tg", "nautilus.json"))


if __name__ == "__main__":
    unittest.main()
