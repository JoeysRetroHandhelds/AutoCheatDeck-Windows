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


def force_foreground(hwnd) -> None:
    """Bring a window to the front even from the background (a tap of Alt lets SetForegroundWindow
    through Windows' focus-stealing rules)."""
    _user32.keybd_event(0x12, 0, 0, 0)
    _user32.keybd_event(0x12, 0, 2, 0)  # KEYEVENTF_KEYUP
    _user32.SetForegroundWindow(wintypes.HWND(hwnd))


EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WM_QUIT = 0x0012
_WinEventProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                   wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
_user32.SetWinEventHook.restype = wintypes.HANDLE
_user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WinEventProc,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]


class ForegroundWatcher:
    """Calls callback(hwnd) whenever another window comes to the front. Windows notifies us (no
    polling); the thread just sleeps in GetMessage between notifications. stop() ends it."""

    def __init__(self, callback: Callable[[int], None]):
        self._callback = callback
        self._thread_id = 0
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="foreground-watch", daemon=True).start()
        self._ready.wait(2)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        proc = _WinEventProc(lambda _h, _e, hwnd, *_: self._callback(hwnd or 0))
        hook = _user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, proc,
                                       0, 0, WINEVENT_OUTOFCONTEXT)
        self._ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        if hook:
            _user32.UnhookWinEvent(hook)

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
