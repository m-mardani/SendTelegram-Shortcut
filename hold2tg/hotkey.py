"""Hotkey parsing and the hold-detection state machine.

This module is deliberately free of I/O and of the evdev dependency so it can
be unit-tested anywhere. Key codes are the Linux input-event codes (a stable
kernel ABI), i.e. *physical* keys: the hotkey keeps working no matter which
keyboard layout (English, Persian, ...) is active.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List, Optional, Set

# --- key names -> Linux input event codes -----------------------------------

MODIFIERS: Dict[str, FrozenSet[int]] = {
    "ctrl": frozenset({29, 97}),      # KEY_LEFTCTRL, KEY_RIGHTCTRL
    "shift": frozenset({42, 54}),     # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
    "alt": frozenset({56, 100}),      # KEY_LEFTALT, KEY_RIGHTALT
    "super": frozenset({125, 126}),   # KEY_LEFTMETA, KEY_RIGHTMETA
    # Not a modifier to the kernel; hold2tg holds it like one. On GNOME the
    # caps:hyper keyboard option makes it one for the apps (see gnome.py).
    "capslock": frozenset({58}),      # KEY_CAPSLOCK
}
MODIFIER_ALIASES = {"control": "ctrl", "meta": "super", "win": "super", "cmd": "super",
                    "caps": "capslock"}

KEYS: Dict[str, int] = {}
for _row, _start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for _i, _ch in enumerate(_row):
        KEYS[_ch] = _start + _i
for _i, _ch in enumerate("1234567890"):
    KEYS[_ch] = 2 + _i
for _i in range(1, 11):
    KEYS["f%d" % _i] = 58 + _i
KEYS.update({
    "f11": 87, "f12": 88,
    "esc": 1, "tab": 15, "enter": 28, "space": 57, "backspace": 14,
    "insert": 110, "delete": 111, "home": 102, "end": 107,
    "pageup": 104, "pagedown": 109,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "print": 99, "pause": 119, "scrolllock": 70,
    "grave": 41, "minus": 12, "equal": 13, "backslash": 43,
    "semicolon": 39, "apostrophe": 40, "comma": 51, "dot": 52, "slash": 53,
    "leftbrace": 26, "rightbrace": 27,
})


class HotkeyError(ValueError):
    pass


class Combo:
    """A set of key *groups*; the combo is held when every group has at least
    one key down and no key outside the groups is down."""

    def __init__(self, groups: Iterable[FrozenSet[int]], label: str = ""):
        self.groups: List[FrozenSet[int]] = list(groups)
        self.label = label
        self.all_codes: FrozenSet[int] = frozenset().union(*self.groups)

    def matches(self, pressed: Set[int]) -> bool:
        if not pressed or not pressed <= self.all_codes:
            return False
        return all(pressed & g for g in self.groups)

    @property
    def main_codes(self) -> FrozenSet[int]:
        """Codes of the non-modifier keys (used to recognise keyboards)."""
        mods = frozenset().union(*MODIFIERS.values())
        return frozenset(c for c in self.all_codes if c not in mods)


def parse_combo(text: str) -> Combo:
    """Parse e.g. ``ctrl+shift+s`` into a :class:`Combo`."""
    parts = [p.strip().lower() for p in text.replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("empty hotkey")
    groups: List[FrozenSet[int]] = []
    has_main = False
    for p in parts:
        p = MODIFIER_ALIASES.get(p, p)
        if p in MODIFIERS:
            groups.append(MODIFIERS[p])
        elif p in KEYS:
            groups.append(frozenset({KEYS[p]}))
            has_main = True
        else:
            raise HotkeyError("unknown key %r in hotkey %r" % (p, text))
    if not has_main:
        raise HotkeyError("hotkey %r needs at least one non-modifier key" % text)
    if len(set(groups)) != len(groups):
        raise HotkeyError("duplicate key in hotkey %r" % text)
    return Combo(groups, "+".join(parts))


def describe(combo: Combo, hold_seconds: float) -> str:
    """How to trigger a hotkey, for logs: "press …" or "hold … for N seconds"."""
    if not hold_seconds:
        return "press %s" % combo.label
    return "hold %s for %g seconds" % (combo.label, hold_seconds)


# --- hold detection ----------------------------------------------------------

IDLE, ARMED, FIRED = "idle", "armed", "fired"


class HoldDetector:
    """Turns raw key events into ``arm`` / ``cancel`` / ``fire`` signals.

    * ``arm``    — the combo has just been fully pressed (timer starts)
    * ``cancel`` — it was released (or another key was pressed) too early
    * ``fire``   — it has been held for ``hold_seconds``; emitted once per hold
    """

    def __init__(self, combo: Combo, hold_seconds: float):
        self.combo = combo
        self.hold_seconds = float(hold_seconds)
        self.state = IDLE
        self._armed_at = 0.0
        self._pressed: Dict[str, Set[int]] = {}

    # -- inputs --
    def key_event(self, device: str, code: int, value: int, now: float) -> Optional[str]:
        """value: 1 = press, 0 = release, 2 = auto-repeat (ignored)."""
        if value == 2:
            return None
        keys = self._pressed.setdefault(device, set())
        if value:
            keys.add(code)
        else:
            keys.discard(code)
        return self._update(now)

    def sync_device(self, device: str, codes: Iterable[int], now: float) -> Optional[str]:
        """Replace the known key state of a device (startup, SYN_DROPPED)."""
        self._pressed[device] = set(codes)
        return self._update(now)

    def drop_device(self, device: str, now: float) -> Optional[str]:
        self._pressed.pop(device, None)
        return self._update(now)

    def poll(self, now: float) -> Optional[str]:
        if self.state == ARMED and now - self._armed_at >= self.hold_seconds:
            self.state = FIRED
            return "fire"
        return None

    # -- outputs --
    @property
    def deadline(self) -> Optional[float]:
        """Monotonic time at which :meth:`poll` will fire, if armed."""
        return self._armed_at + self.hold_seconds if self.state == ARMED else None

    def _update(self, now: float) -> Optional[str]:
        pressed: Set[int] = set()
        for keys in self._pressed.values():
            pressed |= keys
        held = self.combo.matches(pressed)
        if self.state == IDLE:
            if held:
                self.state = ARMED
                self._armed_at = now
                return "arm"
        elif self.state == ARMED:
            if not held:
                self.state = IDLE
                return "cancel"
        elif self.state == FIRED:
            if not held:
                self.state = IDLE
        return None
