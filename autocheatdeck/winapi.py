"""Small Win32 helpers: single-instance mutex, showing the running copy, waiting on a pid."""

import ctypes
from ctypes import wintypes

WINDOW_TITLE = "AutoCheatDeck"
SYNCHRONIZE = 0x00100000
ERROR_ALREADY_EXISTS = 183

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.OpenProcess.restype = wintypes.HANDLE


def show_running_copy() -> bool:
    """Bring the already-running app's window to the front (restoring it if minimized).

    Called from a fresh launch, which Windows lets take the foreground, so this works even
    over a game or the Xbox full screen experience.
    """
    hwnd = _user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        return False
    SW_RESTORE = 9
    _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
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
