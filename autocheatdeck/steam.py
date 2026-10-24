"""Find installed Steam games from the local library files."""

import os
import re
from dataclasses import dataclass

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


@dataclass(frozen=True)
class Game:
    appid: str
    name: str
    install_dir: str


def parse_vdf(text: str) -> dict:
    """Parse Valve's text KeyValues format into nested dicts."""
    stack: list[dict] = [{}]
    key: str | None = None
    for quoted, brace in _TOKEN.findall(text):
        if brace == "{":
            child: dict = {}
            stack[-1][key or ""] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = quoted.replace("\\\\", "\\")
        else:
            stack[-1][key] = quoted.replace("\\\\", "\\")
            key = None
    return stack[0]


def find_steam_path() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            path = winreg.QueryValueEx(key, "SteamPath")[0]
            if os.path.isdir(path):
                return os.path.normpath(path)
    except OSError:
        pass
    default = os.path.expandvars(r"%ProgramFiles(x86)%\Steam")
    return default if os.path.isdir(default) else None


def library_folders(steam_path: str) -> list[str]:
    folders = [steam_path]
    vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8") as f:
            data = parse_vdf(f.read())
    except OSError:
        return folders
    for entry in data.get("libraryfolders", {}).values():
        path = entry.get("path") if isinstance(entry, dict) else None
        if path and os.path.normcase(os.path.normpath(path)) not in {os.path.normcase(p) for p in folders}:
            folders.append(os.path.normpath(path))
    return folders


# Steam tools and runtimes that show up as installed apps but aren't games.
_NOT_GAMES = re.compile(r"^(Steamworks Common Redistributables|Proton|Steam Linux Runtime|SteamVR)", re.I)


def installed_games(steam_path: str | None = None) -> list[Game]:
    steam_path = steam_path or find_steam_path()
    if not steam_path:
        return []
    games: dict[str, Game] = {}
    for library in library_folders(steam_path):
        apps = os.path.join(library, "steamapps")
        try:
            names = os.listdir(apps)
        except OSError:
            continue
        for name in names:
            if not (name.startswith("appmanifest_") and name.endswith(".acf")):
                continue
            try:
                with open(os.path.join(apps, name), encoding="utf-8") as f:
                    state = parse_vdf(f.read()).get("AppState", {})
            except OSError:
                continue
            appid, title, folder = state.get("appid"), state.get("name"), state.get("installdir")
            if not (appid and title and folder) or _NOT_GAMES.match(title):
                continue
            install_dir = os.path.join(apps, "common", folder)
            if os.path.isdir(install_dir):
                games[appid] = Game(appid, title, install_dir)
    return sorted(games.values(), key=lambda g: g.name.lower())
