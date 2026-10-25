"""The autocheatdeck: link, so the Game Bar widget can start AutoCheatDeck when it isn't running.

Widgets are sandboxed and can't start programs, but they can open a link. Registered for the
current user only (no admin rights); every start of the packaged app refreshes it, so it always
points at wherever the portable folder is now.
"""

import winreg

SCHEME = "autocheatdeck"
WIDGET_FLAG = "--widget"
KEY = r"Software\Classes\{}".format(SCHEME)


def command_for(exe_path: str) -> str:
    return '"{}" {} "%1"'.format(exe_path, WIDGET_FLAG)


def register(exe_path: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:AutoCheatDeck")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, '"{}",0'.format(exe_path))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command_for(exe_path))


def registered_command() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY + r"\shell\open\command") as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None
