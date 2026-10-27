"""A running trainer's cheats: parse the text of its window, find the window, cache the result.

FLiNG trainers are WPF apps that run as administrator, so their window can only be read through UI
Automation from an elevated process: the admin helper (helper.py) does the reading and switching.
The parsed cheat list is cached per trainer build.
"""

import ctypes
import json
import os
import re
from ctypes import wintypes


# ---------- parsing the trainer's window text ----------

_MODIFIER = r"(?:(?:Ctrl|Alt|Shift)\+)"
_KEY = r"(?:Num [0-9./*+\-]|F(?:[1-9]|1[0-2])|[A-Z0-9]|Home|End|Insert|Delete|PageUp|PageDown|Up|Down|Left|Right|Space)"
HOTKEY = re.compile(r"^{}*{}$".format(_MODIFIER, _KEY))
_STOP = ("General Notes", "Trainer Settings")


def parse_trainer_ui(lines: list[str]) -> dict:
    """UI Automation text of a FLiNG trainer -> {"cheats": [...], "processes": [...]}.

    Each cheat is {"key": "Ctrl+Num 1", "name": "Edit Money", "section": "", "adjustable": bool};
    "adjustable" cheats have a < > value control (the overlay changes it, see values.py).
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


# ---------- the trainer's window ----------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.FindWindowW.restype = wintypes.HWND


def trainer_window(trainer_path: str) -> int:
    """The trainer's main window: FLiNG titles it after the exe ("<Game> v1.0 Plus 50 Trainer")."""
    return trainer_window_by_title(os.path.splitext(os.path.basename(trainer_path))[0])


def trainer_window_by_title(title: str) -> int:
    return _user32.FindWindowW(None, title) or 0


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
