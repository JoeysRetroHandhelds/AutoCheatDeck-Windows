"""Small Win32 helpers: single-instance mutex, "show yourself" signal, waiting on a pid."""

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

WINDOW_TITLE = "AutoCheatDeck"
SHOW_EVENT = "Local\\AutoCheatDeck-Show"
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
ASFW_ANY = -1
INFINITE = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.OpenProcess.restype = wintypes.HANDLE


def listen_for_show(callback: Callable[[], None]) -> None:
    """Call `callback` (on a background thread) whenever another launch asks this copy to show."""
    handle = _kernel32.CreateEventW(None, False, False, SHOW_EVENT)
    if not handle:
        return

    def wait() -> None:
        while _kernel32.WaitForSingleObject(handle, INFINITE) == 0:
            callback()

    threading.Thread(target=wait, name="show-signal", daemon=True).start()


def ask_running_copy_to_show() -> bool:
    """From a second launch: let the running copy take the foreground and tell it to show."""
    handle = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT)
    if not handle:
        return False
    _user32.AllowSetForegroundWindow(ASFW_ANY)
    _kernel32.SetEvent(handle)
    _kernel32.CloseHandle(handle)
    return True


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
