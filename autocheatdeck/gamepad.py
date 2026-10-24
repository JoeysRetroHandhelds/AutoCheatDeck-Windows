"""Read Xbox-style controllers through XInput (ctypes, no dependencies).

Emits named button presses with key-repeat for directions. The controller is only read while
one of the app's windows is in the foreground, so a game being played never drives the UI and the
app costs nothing (no 60 Hz wakeups) while it sits in the background.
"""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Callable

from . import log

BUTTONS = {
    0x0001: "up",
    0x0002: "down",
    0x0004: "left",
    0x0008: "right",
    0x0010: "start",
    0x0020: "back",
    0x0100: "lb",
    0x0200: "rb",
    0x1000: "a",
    0x2000: "b",
    0x4000: "x",
    0x8000: "y",
}
REPEATING = {"up", "down", "left", "right", "lb", "rb"}
STICK_DEADZONE = 16000
REPEAT_DELAY = 0.4
REPEAT_RATE = 0.11
POLL = 1 / 60
# While another app is in front, only check (cheaply) whether we've come back to the front.
IDLE_POLL = 0.5
# XInputGetState is slow for empty slots, so look for newly connected controllers only this often.
RESCAN = 2.0


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


def _load_xinput():
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            dll = ctypes.WinDLL(name)
            log.get().info("controller: loaded %s", name)
            return dll
        except OSError:
            continue
    log.get().warning("controller: no XInput library found")
    return None


def pressed_names(buttons: int, lx: int, ly: int) -> set[str]:
    """Button bitmask + left stick -> set of names (the stick acts as a d-pad)."""
    names = {name for bit, name in BUTTONS.items() if buttons & bit}
    if ly > STICK_DEADZONE:
        names.add("up")
    elif ly < -STICK_DEADZONE:
        names.add("down")
    if lx > STICK_DEADZONE:
        names.add("right")
    elif lx < -STICK_DEADZONE:
        names.add("left")
    return names


class Repeater:
    """Turns held-button snapshots into press events: once on press, then repeating for directions."""

    def __init__(self):
        self._down: dict[str, float] = {}

    def update(self, held: set[str], now: float) -> list[str]:
        events = []
        for name in held:
            if name not in self._down:
                self._down[name] = now + REPEAT_DELAY
                events.append(name)
            elif name in REPEATING and now >= self._down[name]:
                self._down[name] = now + REPEAT_RATE
                events.append(name)
        for name in list(self._down):
            if name not in held:
                del self._down[name]
        return events


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
VK = {"tab": 0x09, "shift": 0x10, "space": 0x20, "escape": 0x1B}
DIALOG_CLASS = "#32770"


def foreground_window() -> tuple[bool, str]:
    """(is it one of this process's windows, its window class)."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False, ""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, name, 64)
    return pid.value == os.getpid(), name.value


def tap(*keys: str) -> None:
    """Press and release keys (modifiers first) via keybd_event."""
    codes = [VK[k] for k in keys]
    for code in codes:
        _user32.keybd_event(code, 0, 0, 0)
    for code in reversed(codes):
        _user32.keybd_event(code, 0, 2, 0)


# Native message boxes run their own modal loop, so drive them with keystrokes.
DIALOG_KEYS = {
    "up": ("shift", "tab"),
    "left": ("shift", "tab"),
    "down": ("tab",),
    "right": ("tab",),
    "a": ("space",),
    "b": ("escape",),
}


class Gamepad:
    """Polls XInput on a thread while the app is in front; `on_press(name)` runs on that thread."""

    def __init__(self, on_press: Callable[[str], None]):
        self._xinput = _load_xinput()
        self._on_press = on_press
        self._connected: set[int] = set()
        self.connected = False  # any controller seen on the last scan (read from the UI thread)
        self._last_scan = 0.0
        self._stop = threading.Event()
        self.available = self._xinput is not None
        if self.available:
            threading.Thread(target=self._run, name="gamepad", daemon=True).start()

    def _held(self, now: float) -> set[str]:
        slots = range(4) if now - self._last_scan >= RESCAN else sorted(self._connected)
        if len(slots) == 4:
            self._last_scan = now
        held: set[str] = set()
        state = XINPUT_STATE()
        for index in slots:
            if self._xinput.XInputGetState(index, ctypes.byref(state)) == 0:
                self._connected.add(index)
                pad = state.Gamepad
                held |= pressed_names(pad.wButtons, pad.sThumbLX, pad.sThumbLY)
            else:
                self._connected.discard(index)
        connected = bool(self._connected)
        if sorted(self._connected) != getattr(self, "_logged_slots", None):
            self._logged_slots = sorted(self._connected)
            log.get().info("controller: connected slots %s", self._logged_slots)
        self.connected = connected
        return held

    def _run(self) -> None:
        repeater = Repeater()
        delay = IDLE_POLL
        logged_presses = 0
        was_ours = None
        while not self._stop.wait(delay):
            try:
                ours, window_class = foreground_window()
                if ours != was_ours:
                    was_ours = ours
                    log.get().info("controller: app %s (window class %r)",
                                   "in front" if ours else "in background", window_class)
                if not ours:
                    delay = IDLE_POLL
                    now = time.monotonic()
                    if now - self._last_scan >= RESCAN * 5:
                        self._held(now)  # keep the "controller connected" status current, rarely
                    repeater.update(set(), now)  # forget buttons held when we lost focus
                    continue
                now = time.monotonic()
                held = self._held(now)
                if delay == IDLE_POLL:
                    # Just came to the front: a button still held from switching here (e.g. A in the
                    # Xbox shell) must not also act on the app.
                    delay = POLL
                    repeater.update(held, now)
                    continue
                for name in repeater.update(held, now):
                    if logged_presses < 20:
                        logged_presses += 1
                        log.get().info("controller: press %s (window class %r)", name, window_class)
                    if window_class == DIALOG_CLASS:
                        if name in DIALOG_KEYS:
                            tap(*DIALOG_KEYS[name])
                    else:
                        self._on_press(name)
            except Exception:  # never let a bad poll kill controller support, but record why
                log.get().exception("controller: poll failed")
                time.sleep(1)

    def stop(self) -> None:
        self._stop.set()
