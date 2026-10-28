"""Small Win32 helpers: single-instance mutex, "show yourself" signal, waiting on a pid."""

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

WINDOW_TITLE = "AutoCheatDeck"
SHOW_EVENT = "Local\\AutoCheatDeck-Show"
# Set while the running copy's window is hidden (in the tray) or it has quit.
HIDDEN_EVENT = "Local\\AutoCheatDeck-Hidden"
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
_kernel32.OpenMutexW.restype = wintypes.HANDLE
WAIT_ABANDONED = 0x80
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


class HiddenSignal:
    """The running copy's side of HIDDEN_EVENT: set while its window is hidden."""

    def __init__(self):
        self._handle = _kernel32.CreateEventW(None, True, True, HIDDEN_EVENT)

    def set(self, hidden: bool) -> None:
        if self._handle:
            (_kernel32.SetEvent if hidden else _kernel32.ResetEvent)(self._handle)


def show_running_copy_and_wait(mutex_name: str) -> bool:
    """From a second launch: show the running copy, then wait (sleeping, no CPU) until its window is
    hidden again or it quits. Whatever started this launch (Steam, say) then sees it running for as
    long as AutoCheatDeck is on screen, instead of a process that exits at once (Steam took that
    as the game closing and covered the window with its own). False if no copy answered."""
    hidden = _kernel32.OpenEventW(SYNCHRONIZE | EVENT_MODIFY_STATE, False, HIDDEN_EVENT)
    if hidden:
        _kernel32.ResetEvent(hidden)  # the running copy sets it again once its window is hidden
    if not ask_running_copy_to_show():
        if hidden:
            _kernel32.CloseHandle(hidden)
        return False
    if not hidden:  # an older copy without the signal: nothing to wait for
        return True
    # Also wait on the single-instance mutex: if the running copy dies, it is abandoned.
    mutex = _kernel32.OpenMutexW(SYNCHRONIZE, False, mutex_name)
    handles = (wintypes.HANDLE * 2)(hidden, mutex) if mutex else (wintypes.HANDLE * 1)(hidden)
    result = _kernel32.WaitForMultipleObjects(len(handles), handles, False, INFINITE)
    if mutex:
        if result in (1, WAIT_ABANDONED + 1):  # we now own the mutex: let it go
            _kernel32.ReleaseMutex(mutex)
        _kernel32.CloseHandle(mutex)
    _kernel32.CloseHandle(hidden)
    return True


def enable_dpi_awareness() -> None:
    """Render at native resolution instead of being bitmap-scaled (blurry) on high-DPI screens."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def acquire_single_instance(name: str) -> bool:
    """True if this is the only running instance (the mutex is held until exit). It is owned by the
    calling thread, so it's abandoned (and a waiting second launch wakes) if this copy crashes."""
    handle = _kernel32.CreateMutexW(None, True, name)
    return bool(handle) and ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def wait_for_pid(pid: int, timeout_ms: int = 15000) -> None:
    handle = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        _kernel32.WaitForSingleObject(handle, timeout_ms)
        _kernel32.CloseHandle(handle)
