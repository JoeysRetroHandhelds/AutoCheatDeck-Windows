"""'Start with Windows' via the current user's Run key (no admin rights needed).

Run-key apps also start under the Xbox full screen experience on handhelds.
"""

import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "AutoCheatDeck"
BACKGROUND_FLAG = "--background"


def command_for(exe_path: str) -> str:
    return '"{}" {}'.format(exe_path, BACKGROUND_FLAG)


def current() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE)[0]
    except OSError:
        return None


def is_enabled() -> bool:
    return current() is not None


def enable(command: str) -> None:
    if current() == command:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command)


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE)
    except FileNotFoundError:
        pass
