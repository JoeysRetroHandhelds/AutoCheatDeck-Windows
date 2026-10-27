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

import base64
import gzip
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

Add-Type -Namespace Acd -Name Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
[DllImport("user32.dll")] public static extern bool EnableWindow(IntPtr hWnd, bool enable);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, int flags, IntPtr extra);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string title);
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct STARTUPINFO {
    public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
    public int dwX; public int dwY; public int dwXSize; public int dwYSize; public int dwXCountChars;
    public int dwYCountChars; public int dwFillAttribute; public int dwFlags; public short wShowWindow;
    public short cbReserved2; public IntPtr lpReserved2; public IntPtr hStdInput; public IntPtr hStdOutput;
    public IntPtr hStdError;
}
[StructLayout(LayoutKind.Sequential)]
public struct PROCESS_INFORMATION {
    public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId;
}
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool CreateProcess(string app, string cmd, IntPtr pa, IntPtr ta, bool inherit, int flags,
    IntPtr env, string dir, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
[DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
'@
$cache = @{}  # trainer title -> @{ hwnd; rows = list of @{ name; kind; toggle } }

# The trainer's own window is never shown: AutoCheatDeck's overlay replaces it. Hidden and disabled,
# it has nothing it can activate, so it can't take focus from the game or the overlay.
function Hide-Trainer($hwnd) {
    if ([Acd.Native]::IsWindowVisible($hwnd)) { [void][Acd.Native]::ShowWindow($hwnd, 0) }  # SW_HIDE
    [void][Acd.Native]::EnableWindow($hwnd, $false)
}
function Trainer-Window($title) {
    # FindWindow also finds hidden windows (UI Automation's window list can leave them out).
    $hwnd = [Acd.Native]::FindWindow([NullString]::Value, $title)
    if ($hwnd -eq [IntPtr]::Zero) { throw "The trainer isn't running" }
    return $hwnd
}
function Find-Trainer($title) { return $auto::FromHandle((Trainer-Window $title)) }

# Every kind of cheat row (CheatOption, CheatOptionWithInput, CheatOptionSetValue, ...) has an
# m_toggle_button and an m_label_option_name. Looking them up is slow (a cross-process search per
# element), so it's done once per trainer window and reused; toggles and state reads then talk to
# the cached toggle buttons directly.
function Get-Rows($title, [bool]$refresh) {
    $entry = $cache[$title]
    if ($refresh -or -not $entry -or -not [Acd.Native]::IsWindow($entry.hwnd)) {
        $hwnd = Trainer-Window $title
        $win = $auto::FromHandle($hwnd)
        $walker = [Windows.Automation.TreeWalker]::ControlViewWalker
        $idProp = $auto::AutomationIdProperty
        $buttonCond = New-Object Windows.Automation.PropertyCondition $idProp, 'm_toggle_button'
        $nameCond = New-Object Windows.Automation.PropertyCondition $idProp, 'm_label_option_name'
        $rows = New-Object System.Collections.ArrayList
        foreach ($button in $win.FindAll($scope::Descendants, $buttonCond)) {
            $row = $walker.GetParent($button)
            $label = $row.FindFirst($scope::Children, $nameCond)
            if (-not $label) { continue }
            $toggle = $button.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
            [void]$rows.Add(@{ name = $label.Current.Name; kind = $row.Current.ClassName; toggle = $toggle })
        }
        $entry = @{ hwnd = $hwnd; rows = $rows }
        $cache[$title] = $entry
    }
    return $entry
}
function Is-On($toggle) { return ($toggle.Current.ToggleState -eq 'On') }
function Read-States($title, [bool]$refresh) {
    $entry = Get-Rows $title $refresh
    return @(foreach ($r in $entry.rows) { @{ name = $r.name; on = (Is-On $r.toggle); kind = $r.kind } })
}
function Toggle-Cheat($title, $name, [bool]$refresh) {
    $entry = Get-Rows $title $refresh
    $row = $null
    foreach ($r in $entry.rows) { if ($r.name -eq $name) { $row = $r; break } }
    if (-not $row) { throw ("No cheat named " + $name) }
    $before = Is-On $row.toggle
    $row.toggle.Toggle()
    # The trainer applies the change asynchronously: wait (briefly) until the state flips.
    $deadline = (Get-Date).AddMilliseconds(600)
    while ((Is-On $row.toggle) -eq $before -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 25 }
    Hide-Trainer $entry.hwnd   # in case changing an option made it show itself again
    return (Is-On $row.toggle)
}

while ($true) {
    $line = $reader.ReadLine()
    if ($line -eq $null) { break }  # the app closed its end: we're done
    try {
        $req = $line | ConvertFrom-Json
        switch ($req.cmd) {
            'ping' { $res = @{ ok = $true } }
            'launch' {
                # Start the trainer hidden (FLiNG trainers may ignore that, so hide and disable the
                # window as soon as it exists).
                $si = New-Object Acd.Native+STARTUPINFO
                $si.cb = [Runtime.InteropServices.Marshal]::SizeOf($si)
                $si.dwFlags = 1          # STARTF_USESHOWWINDOW
                $si.wShowWindow = 0      # SW_HIDE
                $pi = New-Object Acd.Native+PROCESS_INFORMATION
                $none = [IntPtr]::Zero
                # [NullString]::Value: PowerShell would pass $null as an empty string.
                if (-not [Acd.Native]::CreateProcess([NullString]::Value, ('"' + $req.path + '"'), $none, $none,
                        $false, 0, $none, (Split-Path $req.path), [ref]$si, [ref]$pi)) {
                    $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                    throw ("Couldn't start the trainer (error " + $err + ")")
                }
                [void][Acd.Native]::CloseHandle($pi.hProcess)
                [void][Acd.Native]::CloseHandle($pi.hThread)
                $deadline = (Get-Date).AddSeconds(20)
                $hwnd = [IntPtr]::Zero
                while ($hwnd -eq [IntPtr]::Zero -and (Get-Date) -lt $deadline) {
                    Start-Sleep -Milliseconds 25
                    $hwnd = [Acd.Native]::FindWindow([NullString]::Value, $req.title)
                }
                # Hide it as soon as it exists; if it shows itself or takes focus later, the overlay's
                # focus guard takes focus back and toggles hide it again.
                if ($hwnd -ne [IntPtr]::Zero) { Hide-Trainer $hwnd }
                $res = @{ ok = $true; hidden = ($hwnd -ne [IntPtr]::Zero) }
            }
            'show-trainer' {
                $hwnd = Trainer-Window $req.title
                [void][Acd.Native]::EnableWindow($hwnd, $true)
                [void][Acd.Native]::ShowWindow($hwnd, 9)   # SW_RESTORE
                [Acd.Native]::keybd_event(0x12, 0, 0, [IntPtr]::Zero)  # Alt tap: lets us set the foreground
                [Acd.Native]::keybd_event(0x12, 0, 2, [IntPtr]::Zero)
                [void][Acd.Native]::SetForegroundWindow($hwnd)
                $res = @{ ok = $true }
            }
            'focus' {
                # Give focus to one of the app's windows (the overlay). The trainer is elevated, so
                # when it has taken focus only an elevated process can take it back.
                [Acd.Native]::keybd_event(0x12, 0, 0, [IntPtr]::Zero)  # Alt tap: lets us set the foreground
                [Acd.Native]::keybd_event(0x12, 0, 2, [IntPtr]::Zero)
                $res = @{ ok = [Acd.Native]::SetForegroundWindow([IntPtr][long]$req.hwnd) }
            }
            'release-trainer' {
                # Give the trainer its window back without taking focus (the app is quitting).
                $hwnd = [Acd.Native]::FindWindow([NullString]::Value, $req.title)
                if ($hwnd -ne [IntPtr]::Zero) {
                    [void][Acd.Native]::EnableWindow($hwnd, $true)
                    [void][Acd.Native]::ShowWindow($hwnd, 7)   # SW_SHOWMINNOACTIVE
                }
                $res = @{ ok = $true }
            }
            'hide-trainer' {
                Hide-Trainer (Trainer-Window $req.title)
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
            'dump-tree' {
                # The whole control tree with each element's depth, type, class, AutomationId,
                # name, supported patterns and (for value boxes) current value: enough to find the
                # internal IDs of every control in a cheat row (buttons, number boxes, Apply).
                $win = Find-Trainer $req.title
                $walker = [Windows.Automation.TreeWalker]::ControlViewWalker
                $rows = New-Object System.Collections.ArrayList
                $stack = New-Object System.Collections.Stack
                $stack.Push(@($win, 0))
                while ($stack.Count -gt 0) {
                    $item = $stack.Pop(); $e = $item[0]; $depth = $item[1]
                    $c = $e.Current
                    $patterns = @($e.GetSupportedPatterns() | ForEach-Object {
                        $_.ProgrammaticName -replace 'Identifiers\.Pattern$', '' })
                    $value = ''
                    $vp = $null
                    if ($e.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
                        $value = $vp.Current.Value
                    }
                    $type = $c.ControlType.ProgrammaticName -replace '^ControlType\.', ''
                    [void]$rows.Add(@{ depth = $depth; type = $type;
                        cls = $c.ClassName; id = $c.AutomationId; name = $c.Name; patterns = $patterns; value = $value })
                    $children = New-Object System.Collections.ArrayList
                    $child = $walker.GetFirstChild($e)
                    while ($child) { [void]$children.Add($child); $child = $walker.GetNextSibling($child) }
                    for ($i = $children.Count - 1; $i -ge 0; $i--) {
                        $stack.Push(@($children[$i], ($depth + 1)))
                    }
                }
                $res = @{ ok = $true; tree = $rows }
            }
            'states' {
                # A trainer restart or a stale element: rebuild the cache once and retry.
                try { $states = Read-States $req.title $false } catch { $states = Read-States $req.title $true }
                $res = @{ ok = $true; states = $states }
            }
            'toggle' {
                try { $on = Toggle-Cheat $req.title $req.name $false }
                catch [Windows.Automation.ElementNotAvailableException] {
                    $on = Toggle-Cheat $req.title $req.name $true
                }
                $res = @{ ok = $true; on = $on }
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

# The helper script travels inline (never via a file the user could swap), but encoded as UTF-16
# base64 it would exceed Windows' 32,767-character command line. So the command line carries this
# small loader plus the gzip-compressed script, which the loader unpacks and runs.
LOADER = r"""
$packed = [Convert]::FromBase64String($Packed)
$mode = [IO.Compression.CompressionMode]::Decompress
$stream = New-Object IO.Compression.GZipStream (New-Object IO.MemoryStream (,$packed)), $mode
$code = (New-Object IO.StreamReader $stream).ReadToEnd()
. ([ScriptBlock]::Create($code))
"""


def packed_script() -> str:
    return base64.b64encode(gzip.compress(HELPER_SCRIPT.encode("utf-8"))).decode("ascii")


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
        self.trainers: set[str] = set()  # titles of trainers the helper started (kept hidden)
        self.declined = False  # the UAC prompt was declined: don't ask again unprompted

    @property
    def running(self) -> bool:
        return self._pipe is not None

    def _start(self) -> None:
        name = "AutoCheatDeck-" + secrets.token_hex(8)
        encoded = elevate.encoded_script(LOADER, Packed=packed_script(), PipeName=name,
                                         UserSid=current_user_sid())
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                 LAUNCH_OUTER.format(encoded)],
                                capture_output=True, creationflags=elevate.NO_WINDOW, timeout=300, check=False)
        if result.returncode != 0:
            self.declined = True
            raise HelperError("Toggling cheats needs administrator approval.")
        self.declined = False
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
        """Hand hidden trainers back, then close our end (which makes the helper exit)."""
        if self._pipe is not None:
            for title in list(self.trainers):
                if self._pipe is None:
                    break  # the helper is gone; never start a new one (and a UAC prompt) to quit
                try:
                    self.request("release-trainer", title=title)
                except (HelperError, ValueError):
                    pass
        self.trainers.clear()
        with self._lock:
            self._close()

    # Convenience wrappers

    def launch(self, trainer_path: str) -> None:
        self.request("launch", path=trainer_path, title=trainer_title(trainer_path))
        self.trainers.add(trainer_title(trainer_path))

    def dump(self, trainer_title: str) -> list[str]:
        return list(self.request("dump", title=trainer_title).get("lines") or [])

    def dump_tree(self, trainer_title: str) -> list[dict]:
        """Every control in the trainer window, in order: depth, type, cls, id, name, patterns, value."""
        return list(self.request("dump-tree", title=trainer_title).get("tree") or [])

    def states(self, trainer_title: str) -> dict[str, dict]:
        """{cheat name: {"on": bool, "kind": row class}} for every cheat row the trainer shows."""
        rows = self.request("states", title=trainer_title).get("states") or []
        return {r["name"]: {"on": bool(r["on"]), "kind": r.get("kind", "")} for r in rows}

    def toggle(self, trainer_title: str, cheat_name: str) -> bool:
        return bool(self.request("toggle", title=trainer_title, name=cheat_name).get("on"))

    def show_trainer(self, trainer_title: str) -> None:
        self.request("show-trainer", title=trainer_title)

    def focus(self, hwnd: int) -> bool:
        return bool(self.request("focus", hwnd=int(hwnd)).get("ok"))

    def hide_trainer(self, trainer_title: str) -> None:
        self.request("hide-trainer", title=trainer_title)


def trainer_title(trainer_path: str) -> str:
    """FLiNG titles the trainer window after its exe name."""
    return os.path.splitext(os.path.basename(trainer_path))[0]
