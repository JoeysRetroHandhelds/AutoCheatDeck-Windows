"""List running processes by full exe path (Win32 via ctypes, no dependencies)."""

import ctypes
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)


def _pids() -> list[int]:
    size = 4096
    while True:
        array = (wintypes.DWORD * size)()
        needed = wintypes.DWORD()
        if not _psapi.EnumProcesses(array, ctypes.sizeof(array), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        count = needed.value // ctypes.sizeof(wintypes.DWORD)
        if count < size:
            return list(array[:count])
        size *= 2


def running_exe_paths() -> dict[int, str]:
    """Map pid -> full exe path for every process we're allowed to query."""
    result: dict[int, str] = {}
    buffer = ctypes.create_unicode_buffer(32768)
    for pid in _pids():
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            length = wintypes.DWORD(len(buffer))
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                result[pid] = buffer.value
        finally:
            _kernel32.CloseHandle(handle)
    return result
