"""Small Win32 helpers: global hotkey, single-instance mutex, waiting on a pid."""

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
SYNCHRONIZE = 0x00100000
ERROR_ALREADY_EXISTS = 183

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.OpenProcess.restype = wintypes.HANDLE


class GlobalHotkey:
    """Registers Ctrl+Alt+<key> on its own thread and calls `callback` (from that thread)."""

    def __init__(self, key: str, callback: Callable[[], None]):
        self._vk = ord(key.upper())
        self._callback = callback
        self._thread_id = 0
        self.registered = False
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="hotkey", daemon=True).start()
        self._ready.wait(2)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        self.registered = bool(_user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, self._vk))
        self._ready.set()
        if not self.registered:
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self._callback()
        _user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


def enable_dpi_awareness() -> None:
    """Render at native resolution instead of being bitmap-scaled (blurry) on high-DPI screens."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def acquire_single_instance(name: str) -> bool:
    """True if this is the only running instance (the mutex is held until exit)."""
    handle = _kernel32.CreateMutexW(None, False, name)
    return bool(handle) and ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def wait_for_pid(pid: int, timeout_ms: int = 15000) -> None:
    handle = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        _kernel32.WaitForSingleObject(handle, timeout_ms)
        _kernel32.CloseHandle(handle)
