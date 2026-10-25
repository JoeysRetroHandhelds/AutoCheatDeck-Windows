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

Add-Type -Namespace Acd -Name Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr hWnd, int index);
[DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr hWnd, int index, int value);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, int flags, IntPtr extra);
[DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr hWnd, out int pid);
[DllImport("user32.dll", CharSet = CharSet.Unicode)]
public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int max);
[DllImport("user32.dll", CharSet = CharSet.Unicode)]
public static extern int GetClassName(IntPtr hWnd, System.Text.StringBuilder text, int max);
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
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string title);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
[DllImport("user32.dll", SetLastError = true)] public static extern bool LockSetForegroundWindow(int code);
'@
$GWL_EXSTYLE = -20
$WS_EX_NOACTIVATE = 0x08000000
$cache = @{}  # trainer title -> @{ hwnd; rows = list of @{ name; kind; toggle } }

function Find-Trainer($title) {
    $cond = New-Object Windows.Automation.PropertyCondition ($auto::NameProperty), $title
    $win = $auto::RootElement.FindFirst($scope::Children, $cond)
    if (-not $win) { throw "The trainer isn't running" }
    return $win
}
# Every kind of cheat row (CheatOption, CheatOptionWithInput, CheatOptionSetValue, ...) has an
# m_toggle_button and an m_label_option_name. Looking them up is slow (a cross-process search per
# element), so it's done once per trainer window and reused; toggles and state reads then talk to
# the cached toggle buttons directly.
function Get-Rows($title, [bool]$refresh) {
    $entry = $cache[$title]
    if ($refresh -or -not $entry -or -not [Acd.Native]::IsWindow($entry.hwnd)) {
        $win = Find-Trainer $title
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
        $entry = @{ hwnd = [IntPtr]$win.Current.NativeWindowHandle; rows = $rows }
        $cache[$title] = $entry
    }
    return $entry
}
function Describe-Window($hwnd) {
    $text = New-Object System.Text.StringBuilder 200
    $class = New-Object System.Text.StringBuilder 200
    [void][Acd.Native]::GetWindowText($hwnd, $text, 200)
    [void][Acd.Native]::GetClassName($hwnd, $class, 200)
    $windowPid = 0
    [void][Acd.Native]::GetWindowThreadProcessId($hwnd, [ref]$windowPid)
    $name = try { (Get-Process -Id $windowPid -ErrorAction Stop).ProcessName } catch { '?' }
    return ("{0} [{1}] '{2}'" -f $name, $class.ToString(), $text.ToString())
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
    # Pressing a WPF button can activate its window, which would close Game Bar's overlay. Make the
    # trainer window non-activating for the moment of the toggle, and give focus back if it moved.
    $foreground = [Acd.Native]::GetForegroundWindow()
    $style = [Acd.Native]::GetWindowLong($entry.hwnd, $GWL_EXSTYLE)
    [void][Acd.Native]::SetWindowLong($entry.hwnd, $GWL_EXSTYLE, ($style -bor $WS_EX_NOACTIVATE))
    # The trainer brings its own window to the front when an option changes; lock foreground
    # changes for the moment of the toggle so Game Bar keeps focus.
    $locked = [Acd.Native]::LockSetForegroundWindow(1)   # LSFW_LOCK
    $lockError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    $script:lockReport = "lock: " + $locked + $(if (-not $locked) { " (error " + $lockError + ")" })
    try {
        $row.toggle.Toggle()
        # The trainer applies the change asynchronously: wait (briefly) until the state flips.
        $deadline = (Get-Date).AddMilliseconds(600)
        while ((Is-On $row.toggle) -eq $before -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 25 }
    } finally {
        [void][Acd.Native]::SetWindowLong($entry.hwnd, $GWL_EXSTYLE, $style)
        if ($locked) { [void][Acd.Native]::LockSetForegroundWindow(2) }   # LSFW_UNLOCK
    }
    $afterToggle = [Acd.Native]::GetForegroundWindow()
    $script:focusReport = "before: " + (Describe-Window $foreground) + `
        " | after toggle: " + (Describe-Window $afterToggle) + " | " + $script:lockReport
    if ($afterToggle -ne $foreground) {
        [Acd.Native]::keybd_event(0x12, 0, 0, [IntPtr]::Zero)  # Alt tap: lets us set the foreground
        [Acd.Native]::keybd_event(0x12, 0, 2, [IntPtr]::Zero)
        [void][Acd.Native]::SetForegroundWindow($foreground)
        $script:focusReport += " | after restore: " + (Describe-Window ([Acd.Native]::GetForegroundWindow()))
    }
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
                # Start the trainer minimized and without taking focus: a new window in front would
                # close Game Bar's overlay, and the widget is how the cheats get used anyway.
                $si = New-Object Acd.Native+STARTUPINFO
                $si.cb = [Runtime.InteropServices.Marshal]::SizeOf($si)
                $si.dwFlags = 1          # STARTF_USESHOWWINDOW
                $si.wShowWindow = 7      # SW_SHOWMINNOACTIVE
                $pi = New-Object Acd.Native+PROCESS_INFORMATION
                $cmdLine = '"' + $req.path + '"'
                # [NullString]::Value: PowerShell would pass $null as an empty string.
                $none = [IntPtr]::Zero
                if (-not [Acd.Native]::CreateProcess([NullString]::Value, $cmdLine, $none, $none, $false, 0,
                        $none, (Split-Path $req.path), [ref]$si, [ref]$pi)) {
                    $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                    throw ("Couldn't start the trainer (error " + $err + ")")
                }
                [void][Acd.Native]::CloseHandle($pi.hProcess)
                [void][Acd.Native]::CloseHandle($pi.hThread)
                # FLiNG trainers ignore that and open in front anyway (which closes Game Bar). Once the
                # window shows up, minimize it so focus falls back to the game.
                $deadline = (Get-Date).AddSeconds(20)
                $hwnd = [IntPtr]::Zero
                while ($hwnd -eq [IntPtr]::Zero -and (Get-Date) -lt $deadline) {
                    Start-Sleep -Milliseconds 100
                    $hwnd = [Acd.Native]::FindWindow([NullString]::Value, $req.title)
                }
                if ($hwnd -ne [IntPtr]::Zero) {
                    Start-Sleep -Milliseconds 300   # let it finish showing before minimizing
                    [void][Acd.Native]::ShowWindow($hwnd, 6)   # SW_MINIMIZE: activates the next window
                }
                $res = @{ ok = $true; minimized = ($hwnd -ne [IntPtr]::Zero) }
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
                # A trainer restart or a stale element: rebuild the cache once and retry.
                try { $states = Read-States $req.title $false } catch { $states = Read-States $req.title $true }
                $res = @{ ok = $true; states = $states }
            }
            'toggle' {
                try { $on = Toggle-Cheat $req.title $req.name $false }
                catch [Windows.Automation.ElementNotAvailableException] {
                    $on = Toggle-Cheat $req.title $req.name $true
                }
                $res = @{ ok = $true; on = $on; focus = $script:focusReport }
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
        self.request("launch", path=trainer_path, title=trainer_title(trainer_path))

    def dump(self, trainer_title: str) -> list[str]:
        return list(self.request("dump", title=trainer_title).get("lines") or [])

    def states(self, trainer_title: str) -> dict[str, dict]:
        """{cheat name: {"on": bool, "kind": row class}} for every cheat row the trainer shows."""
        rows = self.request("states", title=trainer_title).get("states") or []
        return {r["name"]: {"on": bool(r["on"]), "kind": r.get("kind", "")} for r in rows}

    def toggle(self, trainer_title: str, cheat_name: str) -> bool:
        reply = self.request("toggle", title=trainer_title, name=cheat_name)
        log.get().info("helper: toggle %s, focus %s", cheat_name, reply.get("focus"))
        return bool(reply.get("on"))


def trainer_title(trainer_path: str) -> str:
    """FLiNG titles the trainer window after its exe name."""
    return os.path.splitext(os.path.basename(trainer_path))[0]
