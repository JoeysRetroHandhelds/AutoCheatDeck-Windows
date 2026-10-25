"""Start with Windows, including under the Xbox full screen experience.

That mode skips the per-user Run list's startup apps but still runs logon scheduled tasks, so
turning the option on registers a logon task (one UAC prompt; creating logon-triggered tasks needs
admin rights). The task itself runs as the signed-in user, unelevated, with no time limit and on
battery. If the prompt is declined, the Run entry is used instead (desktop only).

Neither entry holds the app's path. Both run a small launcher in %LOCALAPPDATA% that reads
location.json, which every app start rewrites, so moving the portable folder needs no new admin
prompt. Turning the option off just marks location.json disabled (no prompt either); the launcher
then does nothing. Approach ported from RAOfflineProxy's Windows build.
"""

import json
import os
import subprocess
import sys
import winreg

from . import elevate

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AutoCheatDeck"
TASK_NAME = "AutoCheatDeck"
BACKGROUND_FLAG = "--background"
# Windows 10 2004 and later have conhost --headless (keeps PowerShell's console from flashing).
HEADLESS_CONHOST_BUILD = 19041
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LAUNCHER_SCRIPT = r"""
# AutoCheatDeck sign-in launcher. Starts the app from wherever its folder is now.
# Written by AutoCheatDeck; safe to delete.
$state = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'location.json') | ConvertFrom-Json
if ($state.enabled -and (Test-Path -LiteralPath $state.exe)) {
    Start-Process -FilePath $state.exe -ArgumentList '--background' -WorkingDirectory (Split-Path $state.exe)
}
"""

REGISTER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'AutoCheatDeck' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
"""


def launcher_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "AutoCheatDeck-autostart")


def _location_path() -> str:
    return os.path.join(launcher_dir(), "location.json")


def _read_location() -> dict:
    try:
        with open(_location_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_launcher(exe_path: str, enabled: bool) -> None:
    os.makedirs(launcher_dir(), exist_ok=True)
    with open(_location_path(), "w", encoding="utf-8") as f:
        json.dump({"exe": exe_path, "enabled": enabled}, f)
    with open(os.path.join(launcher_dir(), "start.ps1"), "w", encoding="utf-8") as f:
        f.write(LAUNCHER_SCRIPT.lstrip())


def launcher_command() -> list[str]:
    script = os.path.join(launcher_dir(), "start.ps1")
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
               "-File", script]
    if sys.getwindowsversion().build >= HEADLESS_CONHOST_BUILD:
        command = ["conhost.exe", "--headless", *command]
    return command


def _run_elevated(script: str, **values: str) -> bool:
    return elevate.run_powershell(script, **values)


encoded_script = elevate.encoded_script


def task_exists() -> bool:
    result = subprocess.run(["schtasks.exe", "/Query", "/TN", TASK_NAME], capture_output=True,
                            creationflags=NO_WINDOW, check=False)
    return result.returncode == 0


def _run_entry() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_VALUE)[0]
    except OSError:
        return None


def _set_run_entry(enabled: bool) -> None:
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(launcher_command()))
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass


def is_enabled() -> bool:
    return bool(_read_location().get("enabled"))


def enable(exe_path: str) -> str:
    """Turn Start with Windows on. Returns "task" (works in Xbox mode) or "run" (desktop only)."""
    _write_launcher(exe_path, True)
    if task_exists():
        _set_run_entry(False)
        return "task"
    domain, name = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    user = "{}\\{}".format(domain, name) if domain else name
    command = launcher_command()
    # The user is passed in, not read inside: a prompt answered with another admin's password would
    # otherwise register the task for that account.
    if _run_elevated(REGISTER_SCRIPT, User=user, Execute=command[0],
                     Arguments=subprocess.list2cmdline(command[1:])):
        _set_run_entry(False)  # one way to start, not two
        return "task"
    _set_run_entry(True)
    return "run"


def disable() -> None:
    """Off without an admin prompt: the task/entry stay, the launcher just does nothing."""
    location = _read_location()
    _write_launcher(location.get("exe", ""), False)


def refresh(exe_path: str) -> None:
    """Every start: point the launcher at where this copy is now, and drop an old-style Run entry
    (from 1.2.x/1.3.x, which held the exe path directly) in favour of the launcher."""
    location = _read_location()
    old_entry = _run_entry()
    if old_entry and "start.ps1" not in old_entry:
        _write_launcher(exe_path, True)
        _set_run_entry(True)
        return
    if location and location.get("exe") != exe_path:
        _write_launcher(exe_path, bool(location.get("enabled")))
