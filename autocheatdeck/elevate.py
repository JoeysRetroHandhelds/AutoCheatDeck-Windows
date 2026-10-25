"""Run a PowerShell script elevated (one UAC prompt) and wait for it.

The script goes inline as -EncodedCommand, never through a file: a file in a folder the user can
write to could be swapped before the elevated PowerShell reads it. Values are passed as $variables
set ahead of the script, quoted as PowerShell literals.
"""

import base64
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def encoded_script(script: str, **values: str) -> str:
    """The script with `values` set as $variables first, base64 UTF-16 for -EncodedCommand."""
    prelude = "".join("${} = {}\n".format(name, _ps_literal(value)) for name, value in values.items())
    return base64.b64encode((prelude + script.lstrip()).encode("utf-16-le")).decode("ascii")


def run_powershell(script: str, **values: str) -> bool:
    """False if the UAC prompt was declined or the script failed."""
    encoded = encoded_script(script, **values)
    outer = ("try { $p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
             "-ArgumentList '-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + encoded + "'; "
             "exit $p.ExitCode } catch { exit 1223 }")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", outer],
                            capture_output=True, creationflags=NO_WINDOW, timeout=300, check=False)
    return result.returncode == 0
