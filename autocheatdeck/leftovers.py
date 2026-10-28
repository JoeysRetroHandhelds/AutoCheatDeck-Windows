"""Remove what the Game Bar widget (AutoCheatDeck 2.1.x) left on this PC.

2.2.0 dropped the widget and its cheat overlay. Copies that installed the widget still have:

- as the user: the widget package, the autocheatdeck: link in the registry, the link's command
  file in ~/.autocheatdeck and the widget launcher's log;
- machine-wide (removing them takes one UAC prompt): the widget's signing certificate in Trusted
  People and its loopback exemption.
"""

import os
import shutil
import subprocess
import winreg

from . import elevate

PACKAGE_NAME = "JoeysRetroHandhelds.AutoCheatDeckWidget"
PACKAGE_FAMILY = "JoeysRetroHandhelds.AutoCheatDeckWidget_sbtj8m4gnqc1y"
CERT_THUMBPRINT = "5C77A765E59001854442E91D04D81081825447DC"
LINK_KEY = r"Software\Classes\autocheatdeck"
COMMAND_DIR = os.path.join(os.path.expanduser("~"), ".autocheatdeck")
LAUNCHER_LOG = os.path.join(os.path.expanduser("~"), "AutoCheatDeck-launcher.log")

ADMIN_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$path = "Cert:\LocalMachine\TrustedPeople\$Thumbprint"
if (Test-Path $path) { Remove-Item $path }
& "$env:SystemRoot\System32\CheckNetIsolation.exe" LoopbackExempt -d "-n=$Family" | Out-Null
"""


def _powershell(command: str) -> str:
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, creationflags=elevate.NO_WINDOW, timeout=120)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip() or "PowerShell failed")
    return result.stdout.strip()


def needs_admin() -> bool:
    """Is the certificate still trusted or the loopback exemption still there?"""
    trusted = _powershell("Test-Path 'Cert:\\LocalMachine\\TrustedPeople\\{}'".format(CERT_THUMBPRINT)) == "True"
    exempt = PACKAGE_FAMILY.lower() in _powershell(
        "& \"$env:SystemRoot\\System32\\CheckNetIsolation.exe\" LoopbackExempt -s").lower()
    return trusted or exempt


def remove_user_parts() -> list[str]:
    """Remove everything that needs no admin rights; returns what was removed."""
    removed = []
    if _powershell("(Get-AppxPackage -Name '{}' | Measure-Object).Count".format(PACKAGE_NAME)) not in ("", "0"):
        _powershell("$ErrorActionPreference = 'Stop'; Get-AppxPackage -Name '{}' | Remove-AppxPackage".format(
            PACKAGE_NAME))
        removed.append("widget")
    try:
        _delete_key_tree(winreg.HKEY_CURRENT_USER, LINK_KEY)
        removed.append("link")
    except FileNotFoundError:
        pass
    command_file = os.path.join(COMMAND_DIR, "widget-command.txt")
    if os.path.isfile(command_file):
        os.remove(command_file)
        removed.append("command file")
        if os.path.isdir(COMMAND_DIR) and not os.listdir(COMMAND_DIR):
            shutil.rmtree(COMMAND_DIR, ignore_errors=True)
    if os.path.isfile(LAUNCHER_LOG):
        os.remove(LAUNCHER_LOG)
        removed.append("launcher log")
    return removed


def remove_admin_parts() -> bool:
    """Untrust the certificate and drop the loopback exemption (one UAC prompt). False if declined."""
    return elevate.run_powershell(ADMIN_SCRIPT, Thumbprint=CERT_THUMBPRINT, Family=PACKAGE_FAMILY)


def _delete_key_tree(root, path: str) -> None:
    with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_key_tree(root, path + "\\" + child)
    winreg.DeleteKey(root, path)
