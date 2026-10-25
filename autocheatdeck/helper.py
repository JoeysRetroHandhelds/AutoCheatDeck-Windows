"""The admin helper: everything that has to touch an elevated FLiNG trainer.

FLiNG trainers run as administrator and ignore simulated keystrokes, but every cheat row in their
window has a toggle button that UI Automation can switch and read, from an elevated process. The
helper is a PowerShell script started elevated once per session (one UAC prompt, silent where UAC
auto-approves). It starts trainers (so the trainer needs no second prompt), reads a trainer's text,
and lists / toggles its cheats.

It talks to the (unelevated) app over a named pipe with a random name whose ACL admits only the
signed-in user, one JSON line per request. It blocks on the pipe between requests (no CPU), and
exits as soon as the app closes its end, so it never outlives the app.
"""

import json
import os
import secrets
import subprocess
import threading
import time

from . import elevate, log

HELPER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Core
$security = New-Object System.IO.Pipes.PipeSecurity
$sid = New-Object System.Security.Principal.SecurityIdentifier $UserSid
$security.AddAccessRule((New-Object System.IO.Pipes.PipeAccessRule $sid, 'ReadWrite', 'Allow'))
$size = 65536
$server = New-Object System.IO.Pipes.NamedPipeServerStream $PipeName, 'InOut', 1, 'Byte', 'None', `
    $size, $size, $security
$server.WaitForConnection()
$reader = New-Object System.IO.StreamReader $server
$writer = New-Object System.IO.StreamWriter $server
$writer.AutoFlush = $true
$auto = [Windows.Automation.AutomationElement]
$scope = [Windows.Automation.TreeScope]

function Find-Trainer($title) {
    $cond = New-Object Windows.Automation.PropertyCondition ($auto::NameProperty), $title
    $win = $auto::RootElement.FindFirst($scope::Children, $cond)
    if (-not $win) { throw "The trainer isn't running" }
    return $win
}
function Child-ById($element, $id) {
    $cond = New-Object Windows.Automation.PropertyCondition ($auto::AutomationIdProperty), $id
    return $element.FindFirst($scope::Descendants, $cond)
}
function Get-Rows($win) {
    # Every kind of cheat row (CheatOption, CheatOptionWithInput, CheatOptionSetValue, ...) has an
    # m_toggle_button; its parent is the row.
    $walker = [Windows.Automation.TreeWalker]::ControlViewWalker
    $cond = New-Object Windows.Automation.PropertyCondition ($auto::AutomationIdProperty), 'm_toggle_button'
    return @(foreach ($button in $win.FindAll($scope::Descendants, $cond)) { $walker.GetParent($button) })
}
function Row-State($row) {
    $button = Child-ById $row 'm_toggle_button'
    if (-not $button) { return $null }
    $toggle = $button.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
    return @{ button = $toggle; on = ($toggle.Current.ToggleState -eq 'On') }
}

while ($true) {
    $line = $reader.ReadLine()
    if ($line -eq $null) { break }  # the app closed its end: we're done
    try {
        $req = $line | ConvertFrom-Json
        switch ($req.cmd) {
            'ping' { $res = @{ ok = $true } }
            'launch' {
                Start-Process -FilePath $req.path -WorkingDirectory (Split-Path $req.path)
                $res = @{ ok = $true }
            }
            'dump' {
                $win = Find-Trainer $req.title
                $all = $win.FindAll($scope::Descendants, [Windows.Automation.Condition]::TrueCondition)
                $lines = @(foreach ($e in $all) {
                    $n = $e.Current.Name
                    if ($n -and $n.Trim()) { $e.Current.ControlType.ProgrammaticName + "`t" + $n }
                })
                $res = @{ ok = $true; lines = $lines }
            }
            'states' {
                $win = Find-Trainer $req.title
                $states = @(foreach ($row in (Get-Rows $win)) {
                    $name = Child-ById $row 'm_label_option_name'
                    $state = Row-State $row
                    if ($name -and $state) {
                        @{ name = $name.Current.Name; on = $state.on; kind = $row.Current.ClassName }
                    }
                })
                $res = @{ ok = $true; states = $states }
            }
            'toggle' {
                $win = Find-Trainer $req.title
                $found = $null
                foreach ($row in (Get-Rows $win)) {
                    $name = Child-ById $row 'm_label_option_name'
                    if ($name -and $name.Current.Name -eq $req.name) { $found = $row; break }
                }
                if (-not $found) { throw ("No cheat named " + $req.name) }
                $state = Row-State $found
                if (-not $state) { throw ($req.name + " has no on/off switch") }
                $state.button.Toggle()
                Start-Sleep -Milliseconds 150
                $res = @{ ok = $true; on = ((Row-State $found).on) }
            }
            'quit' { $writer.WriteLine('{"ok":true}'); exit 0 }
            default { throw ("Unknown command " + $req.cmd) }
        }
    } catch {
        $res = @{ ok = $false; error = $_.Exception.Message }
    }
    $writer.WriteLine(($res | ConvertTo-Json -Compress -Depth 6))
}
"""

# Starts the helper elevated and returns once the UAC prompt has been answered (not when it exits).
LAUNCH_OUTER = ("try {{ Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList "
                "'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand {}'; "
                "exit 0 }} catch {{ exit 1223 }}")


def current_user_sid() -> str:
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                            creationflags=elevate.NO_WINDOW, check=True)
    return result.stdout.strip().split(",")[-1].strip().strip('"')


class HelperError(Exception):
    pass


class Helper:
    """Starts the elevated helper on first use and forwards requests to it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pipe = None

    @property
    def running(self) -> bool:
        return self._pipe is not None

    def _start(self) -> None:
        name = "AutoCheatDeck-" + secrets.token_hex(8)
        encoded = elevate.encoded_script(HELPER_SCRIPT, PipeName=name, UserSid=current_user_sid())
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                 LAUNCH_OUTER.format(encoded)],
                                capture_output=True, creationflags=elevate.NO_WINDOW, timeout=300, check=False)
        if result.returncode != 0:
            raise HelperError("Toggling cheats needs administrator approval.")
        path = r"\\.\pipe\{}".format(name)
        deadline = time.monotonic() + 20
        while True:
            try:
                self._pipe = open(path, "r+b", buffering=0)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise HelperError("The admin helper didn't start.")
                time.sleep(0.2)
        log.get().info("helper: started (pipe %s)", name)

    def request(self, cmd: str, **fields) -> dict:
        with self._lock:
            if self._pipe is None:
                self._start()
            message = json.dumps(dict(cmd=cmd, **fields)) + "\n"
            try:
                self._pipe.write(message.encode("utf-8"))
                reply = self._readline()
            except OSError:
                self._close()
                raise HelperError("The admin helper stopped.")
        data = json.loads(reply)
        if not data.get("ok"):
            raise HelperError(data.get("error") or "The admin helper failed.")
        return data

    def _readline(self) -> str:
        chunks = bytearray()
        while not chunks.endswith(b"\n"):
            chunk = self._pipe.read(65536)
            if not chunk:
                raise OSError("pipe closed")
            chunks += chunk
        return chunks.decode("utf-8-sig").strip()

    def _close(self) -> None:
        if self._pipe is not None:
            try:
                self._pipe.close()
            except OSError:
                pass
            self._pipe = None

    def stop(self) -> None:
        """Closing our end makes the helper exit."""
        with self._lock:
            self._close()

    # Convenience wrappers

    def launch(self, trainer_path: str) -> None:
        self.request("launch", path=trainer_path)

    def dump(self, trainer_title: str) -> list[str]:
        return list(self.request("dump", title=trainer_title).get("lines") or [])

    def states(self, trainer_title: str) -> dict[str, dict]:
        """{cheat name: {"on": bool, "kind": row class}} for every cheat row the trainer shows."""
        rows = self.request("states", title=trainer_title).get("states") or []
        return {r["name"]: {"on": bool(r["on"]), "kind": r.get("kind", "")} for r in rows}

    def toggle(self, trainer_title: str, cheat_name: str) -> bool:
        return bool(self.request("toggle", title=trainer_title, name=cheat_name).get("on"))


def trainer_title(trainer_path: str) -> str:
    """FLiNG titles the trainer window after its exe name."""
    return os.path.splitext(os.path.basename(trainer_path))[0]
