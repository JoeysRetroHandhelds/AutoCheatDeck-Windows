"""Installed games: Steam, Xbox app / Game Pass, plus manually added ones.

Every game gets an id that is safe as a folder name. Steam keeps its plain app id so existing
settings and trainers carry over; the others are prefixed with their store.
"""

import hashlib
import os
import re
import string
import struct
import xml.etree.ElementTree as ET

from . import steam
from .steam import Game


def _safe_id(prefix: str, value: str) -> str:
    return "{}-{}".format(prefix, re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:80])


# ---------- Xbox app / Game Pass ----------

def gaming_roots(drive_roots: list[str] | None = None) -> list[str]:
    """Folders the Xbox app installs games into, read from each drive's .GamingRoot marker.

    The marker is "RGBX", a uint32 count, then that many NUL-terminated UTF-16 paths relative to
    the drive (usually just "XboxGames").
    """
    if drive_roots is None:
        drive_roots = ["{}:\\".format(letter) for letter in string.ascii_uppercase]
    roots = []
    for drive in drive_roots:
        marker = os.path.join(drive, ".GamingRoot")
        try:
            with open(marker, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if data[:4] != b"RGBX" or len(data) < 8:
            continue
        count = struct.unpack("<I", data[4:8])[0]
        paths = data[8:].decode("utf-16-le", errors="ignore").split("\0")
        for relative in paths[:count]:
            folder = os.path.join(drive, relative)
            if relative and os.path.isdir(folder):
                roots.append(folder)
    return roots


def _xbox_display_name(config_path: str, fallback: str) -> str:
    try:
        tree = ET.parse(config_path)
    except (OSError, ET.ParseError):
        return fallback
    for element in tree.iter():
        if element.tag.split("}")[-1] == "ShellVisuals":
            name = element.get("DefaultDisplayName", "")
            if name and not name.startswith("ms-resource:"):
                return name
    return fallback


def xbox_games(drive_roots: list[str] | None = None) -> list[Game]:
    games = []
    for root in gaming_roots(drive_roots):
        for folder in sorted(os.listdir(root)):
            content = os.path.join(root, folder, "Content")
            config = os.path.join(content, "MicrosoftGame.config")
            if os.path.isfile(config):
                games.append(Game(_safe_id("xbox", folder), _xbox_display_name(config, folder), content,
                                  source="Xbox"))
    return games


# ---------- manual ----------

def manual_id(folder: str) -> str:
    digest = hashlib.sha1(os.path.normcase(os.path.normpath(folder)).encode("utf-8")).hexdigest()[:12]
    return "manual-" + digest


def manual_games(entries: list[dict]) -> list[Game]:
    return [Game(e["id"], e["name"], e["dir"], source="Manual") for e in entries if os.path.isdir(e.get("dir", ""))]


# ---------- everything ----------

def all_games(manual: list[dict], last_played: dict[str, int]) -> list[Game]:
    """Every installed game, most recently played first.

    Steam records when each game was last played; for the other stores AutoCheatDeck records it
    itself (`last_played`, from game detection).
    """
    games: dict[str, Game] = {}
    sources = (steam.installed_games, xbox_games, lambda: manual_games(manual))
    for source in sources:
        try:
            found = source()
        except Exception:  # one broken store must not hide the others
            from . import log

            log.get().exception("library: reading %s failed", getattr(source, "__name__", "manual games"))
            continue
        for game in found:
            games.setdefault(game.appid, game)
    result = []
    for game in games.values():
        seen = last_played.get(game.appid, 0)
        if seen > game.last_played:
            game = Game(game.appid, game.name, game.install_dir, seen, game.source)
        result.append(game)
    return sorted(result, key=lambda g: (-g.last_played, g.name.lower()))
