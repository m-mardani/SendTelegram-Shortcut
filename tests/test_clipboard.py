import os
import signal
import tempfile
import time
import unittest
from unittest import mock

from hold2tg import clipboard
from hold2tg.clipboard import ClipboardError


def fake_tool(folder, name, body):
    path = os.path.join(folder, name)
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = self.tmp.name

    def session(self, **env):
        p = mock.patch.dict(os.environ, dict(env, PATH=self.bin), clear=True)
        p.start()
        self.addCleanup(p.stop)

    def recording_tool(self, name, exit_code=0):
        fake_tool(self.bin, name, 'printf "%%s\\n" "$@" > "%s/args"\n/bin/cat > "%s/input"\n'
                  'echo "it broke" >&2\nexit %d\n' % (self.bin, self.bin, exit_code))

    def recorded(self):
        with open(os.path.join(self.bin, "args")) as fh:
            args = fh.read().split("\n")[:-1]
        with open(os.path.join(self.bin, "input"), "rb") as fh:
            return args, fh.read()

    def test_files_on_wayland_go_up_the_way_files_copies_them(self):
        self.session(WAYLAND_DISPLAY="wayland-0", DISPLAY=":0")
        self.recording_tool("wl-copy")
        self.recording_tool("xclip")
        clipboard.copy_files(["/tmp/a b/گزارش.pdf", "/tmp/x.zip"])
        args, data = self.recorded()
        self.assertEqual(args, ["--type", "x-special/gnome-copied-files"])
        self.assertEqual(data, b"copy\nfile:///tmp/a%20b/%DA%AF%D8%B2%D8%A7%D8%B1%D8%B4.pdf\n"
                               b"file:///tmp/x.zip")

    def test_text_gets_an_explicit_text_type(self):
        self.session(WAYLAND_DISPLAY="wayland-0")
        self.recording_tool("wl-copy")
        clipboard.copy_text("#!/bin/sh\nسلام")
        self.assertEqual(self.recorded(), (["--type", "text/plain;charset=utf-8"],
                                           "#!/bin/sh\nسلام".encode("utf-8")))

    def test_x11_uses_xclip(self):
        self.session(DISPLAY=":0")
        self.recording_tool("wl-copy")
        self.recording_tool("xclip")
        clipboard.copy_text("hi")
        self.assertEqual(self.recorded(), (["-selection", "clipboard"], b"hi"))
        clipboard.copy_files(["/f"])
        self.assertEqual(self.recorded()[0], ["-selection", "clipboard", "-target",
                                              "x-special/gnome-copied-files"])

    def test_wayland_without_wl_copy_falls_back_to_xclip(self):
        self.session(WAYLAND_DISPLAY="wayland-0", DISPLAY=":0")
        self.recording_tool("xclip")
        clipboard.copy_text("hi")
        self.assertEqual(self.recorded()[1], b"hi")

    def test_no_tool_or_a_failing_tool_is_reported(self):
        self.session(WAYLAND_DISPLAY="wayland-0")
        with self.assertRaises(ClipboardError) as cm:
            clipboard.copy_text("x")
        self.assertIn("wl-clipboard", str(cm.exception))
        self.recording_tool("wl-copy", exit_code=1)
        with self.assertRaises(ClipboardError) as cm:
            clipboard.copy_text("x")
        self.assertIn("it broke", str(cm.exception))

    def test_does_not_wait_for_the_child_that_serves_the_clipboard(self):
        # wl-copy and xclip fork a child that keeps stdout/stderr open for as
        # long as it owns the clipboard.
        self.session(WAYLAND_DISPLAY="wayland-0")
        fake_tool(self.bin, "wl-copy", '/bin/cat > /dev/null\n/bin/sleep 10 &\necho $! > "%s/pid"\n'
                  % self.bin)
        t0 = time.monotonic()
        clipboard.copy_text("x")
        self.assertLess(time.monotonic() - t0, 3)
        with open(os.path.join(self.bin, "pid")) as fh:
            os.kill(int(fh.read()), signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
