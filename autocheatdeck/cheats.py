"""A running trainer's cheats: read them, and press their hotkeys.

FLiNG trainers are WPF apps that run as administrator, so their window can only be read through UI
Automation from an elevated process (an unelevated one sees nothing). Reading happens once per
trainer version, through one UAC prompt, and is cached. Pressing a cheat needs no elevation: the
trainer listens for its hotkeys globally, and injected keystrokes reach it while a normal
(unelevated) window such as the game is in front.
"""

import ctypes
import json
import os
import re
from ctypes import wintypes

from . import config, elevate

# ---------- parsing the trainer's window text ----------

_MODIFIER = r"(?:(?:Ctrl|Alt|Shift)\+)"
_KEY = r"(?:Num [0-9./*+\-]|F(?:[1-9]|1[0-2])|[A-Z0-9]|Home|End|Insert|Delete|PageUp|PageDown|Up|Down|Left|Right|Space)"
HOTKEY = re.compile(r"^{}*{}$".format(_MODIFIER, _KEY))
_STOP = ("General Notes", "Trainer Settings")


def parse_trainer_ui(lines: list[str]) -> dict:
    """UI Automation text of a FLiNG trainer -> {"cheats": [...], "processes": [...]}.

    Each cheat is {"key": "Ctrl+Num 1", "name": "Edit Money", "section": "", "adjustable": bool};
    "adjustable" cheats have a < > value control that only the trainer window can change.
    """
    texts = [line.split("\t", 1)[-1].strip() for line in lines if line.strip()]
    # The trainer fills in the game's exe names ("A.exe/B.exe") only some of the time, and not
    # always right after its label, so take any text made of .exe names.
    processes: list[str] = []
    for text in texts:
        names = [p.strip() for p in text.split("/")]
        if names and all(n.lower().endswith(".exe") and " " not in n for n in names):
            processes = names
            break
    try:
        start = texts.index("Options") + 1
    except ValueError:
        return {"cheats": [], "processes": processes}

    cheats: list[dict] = []
    section = ""
    pending: str | None = None
    for text in texts[start:]:
        if text.startswith(_STOP):
            break
        if text in ("<", ">"):
            if cheats and pending is None:
                cheats[-1]["adjustable"] = True
            continue
        if HOTKEY.match(text):
            pending = text
        elif pending is not None:
            cheats.append({"key": pending, "name": text, "section": section, "adjustable": False})
            pending = None
        else:
            section = text  # a heading with no hotkey, e.g. "Selected Character Bonus Stats"
    return {"cheats": cheats, "processes": processes}


# ---------- reading a running trainer (elevated) ----------

DUMP_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$hwnd = [IntPtr][long]$Window
$root = [Windows.Automation.AutomationElement]::FromHandle($hwnd)
$all = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
$lines = foreach ($e in $all) {
    $n = $e.Current.Name
    if ($n -and $n.Trim()) { $e.Current.ControlType.ProgrammaticName + "`t" + $n }
}
[IO.File]::WriteAllLines($Out, [string[]]$lines, [Text.UTF8Encoding]::new($false))
"""

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.FindWindowW.restype = wintypes.HWND


def trainer_window(trainer_path: str) -> int:
    """The trainer's main window: FLiNG titles it after the exe ("<Game> v1.0 Plus 50 Trainer")."""
    return trainer_window_by_title(os.path.splitext(os.path.basename(trainer_path))[0])


def trainer_window_by_title(title: str) -> int:
    return _user32.FindWindowW(None, title) or 0


def read_running_trainer(hwnd: int) -> dict:
    """Read the cheats of the trainer window `hwnd` (one UAC prompt). Raises if declined/failed."""
    # Not a tempfile folder: Python 3.13 makes those owner-only, and the elevated process's file
    # would be owned by Administrators. The data folder has ordinary inherited permissions.
    folder = os.path.join(config.data_dir(), "tmp")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "trainer_ui_{}.txt".format(os.getpid()))
    try:
        if not elevate.run_powershell(DUMP_SCRIPT, Window=str(int(hwnd)), Out=out):
            raise OSError("Reading the trainer's cheats needs administrator approval.")
        with open(out, encoding="utf-8") as f:
            return parse_trainer_ui(f.read().splitlines())
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def save_cache(path: str, sha256: str, parsed: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"sha256": sha256, **parsed}, f, indent=2)


def load_cache(path: str, sha256: str) -> dict | None:
    """Cached cheats for this exact trainer build (a new download invalidates them)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if data.get("sha256") == sha256 else None


# ---------- pressing hotkeys ----------

VK_MODIFIERS = {"Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10}
VK_NAMED = {
    "Num 0": 0x60, "Num 1": 0x61, "Num 2": 0x62, "Num 3": 0x63, "Num 4": 0x64, "Num 5": 0x65,
    "Num 6": 0x66, "Num 7": 0x67, "Num 8": 0x68, "Num 9": 0x69, "Num *": 0x6A, "Num +": 0x6B,
    "Num -": 0x6D, "Num .": 0x6E, "Num /": 0x6F, "Home": 0x24, "End": 0x23, "Insert": 0x2D,
    "Delete": 0x2E, "PageUp": 0x21, "PageDown": 0x22, "Up": 0x26, "Down": 0x28, "Left": 0x25,
    "Right": 0x27, "Space": 0x20,
}
EXTENDED = {0x6F, 0x24, 0x23, 0x2D, 0x2E, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27}


def hotkey_vks(hotkey: str) -> list[int]:
    """"Ctrl+Num 1" -> [VK_CONTROL, VK_NUMPAD1] (modifiers first). "Num +" keeps its own "+"."""
    if not HOTKEY.match(hotkey):
        raise ValueError("Unrecognised hotkey: {}".format(hotkey))
    vks, key = [], hotkey
    while True:
        match = re.match(r"(Ctrl|Alt|Shift)\+(?=.)", key)
        if not match:
            break
        vks.append(VK_MODIFIERS[match.group(1)])
        key = key[match.end():]
    if key in VK_NAMED:
        vks.append(VK_NAMED[key])
    elif re.fullmatch(r"F([1-9]|1[0-2])", key):
        vks.append(0x6F + int(key[1:]))
    else:
        vks.append(ord(key))
    return vks


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def press(hotkey: str) -> None:
    """Press and release a trainer hotkey through SendInput."""
    vks = hotkey_vks(hotkey)
    events = [(vk, False) for vk in vks] + [(vk, True) for vk in reversed(vks)]
    inputs = (INPUT * len(events))()
    for i, (vk, up) in enumerate(events):
        flags = (0x0002 if up else 0) | (0x0001 if vk in EXTENDED else 0)
        inputs[i].type = 1
        inputs[i].ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    if _user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT)) != len(events):
        raise OSError(ctypes.get_last_error(), "SendInput was blocked")
