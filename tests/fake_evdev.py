"""A stand-in for the `evdev` module backed by pipes, so the daemon's main
loop can be exercised on machines without /dev/input (CI, containers)."""
import os
import struct
import types

EV_SYN, EV_KEY, SYN_DROPPED = 0, 1, 3
_FMT = "HHi"
_SIZE = struct.calcsize(_FMT)

ecodes = types.SimpleNamespace(EV_SYN=EV_SYN, EV_KEY=EV_KEY, SYN_DROPPED=SYN_DROPPED)
registry = {}   # path -> FakeKeyboard


class FakeKeyboard:
    """The 'hardware' side: tests press and release keys on it."""

    def __init__(self, path, keys, name="Fake keyboard", denied=False):
        self.path, self.keys, self.name, self.denied = path, list(keys), name, denied
        self.r, self.w = os.pipe()
        os.set_blocking(self.r, False)
        self.down = set()
        registry[path] = self

    def emit(self, etype, code, value):
        os.write(self.w, struct.pack(_FMT, etype, code, value))

    def press(self, *codes):
        for c in codes:
            self.down.add(c)
            self.emit(EV_KEY, c, 1)

    def release(self, *codes):
        for c in codes:
            self.down.discard(c)
            self.emit(EV_KEY, c, 0)

    def unplug(self):
        registry.pop(self.path, None)
        os.close(self.w)


class InputDevice:
    def __init__(self, path):
        kb = registry.get(path)
        if kb is None:
            raise FileNotFoundError(path)
        if kb.denied:
            raise PermissionError(13, "Permission denied", path)
        self._kb, self.path, self.name, self.fd = kb, path, kb.name, kb.r

    def capabilities(self):
        return {EV_SYN: [0], EV_KEY: list(self._kb.keys)}

    def active_keys(self):
        return sorted(self._kb.down)

    def read(self):
        data = os.read(self.fd, _SIZE * 64)       # raises BlockingIOError when empty
        if not data:
            raise OSError(19, "No such device")   # writer closed == unplugged
        for i in range(0, len(data), _SIZE):
            etype, code, value = struct.unpack(_FMT, data[i:i + _SIZE])
            yield types.SimpleNamespace(type=etype, code=code, value=value)

    def close(self):
        pass
