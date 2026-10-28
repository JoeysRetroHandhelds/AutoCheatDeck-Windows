"""Turn "start the trainer with the game" on or off in Steam, by editing the game's launch options.

The launch option makes Steam run `AutoCheatDeck.exe --run <appid> %command%` instead of the game
(see launcher.py). Steam keeps launch options in userdata/<account>/config/localconfig.vdf, but it
holds them in memory while it runs and rewrites that file on exit, so the file is only changed
while Steam is closed: AutoCheatDeck asks Steam to quit, edits the file (after a backup), and
starts Steam again. Options the user already had are kept and come back when it's turned off.
"""

import os
import re
import subprocess
import time

from . import launcher, steam

STEAM_ID64_BASE = 76561197960265728
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_OURS = re.compile(r'^"[^"]*"\s+{}\s+\d+\s+'.format(re.escape(launcher.RUN_FLAG)))


# ---------- editing localconfig.vdf in place ----------
# Only the one LaunchOptions value is changed; everything else in the file stays byte for byte as
# Steam wrote it (a full parse and rewrite could lose things such as repeated keys).

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*|\S+', re.S)
_PATH = ("userlocalconfigstore", "software", "valve", "steam", "apps")


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def locate(text: str, appid: str) -> dict:
    """Where things are: {"apps": index after its "{", "app": index after the game's "{" (or None),
    "value": (start, end) of the LaunchOptions value token (or None), "current": its text}."""
    found = {"apps": None, "app": None, "value": None, "current": ""}
    stack: list[str] = []  # keys of the open blocks, lowercase
    key: str | None = None
    for match in _TOKEN.finditer(text):
        quoted, brace = match.group(1), match.group(2)
        if quoted is None and brace is None:
            continue  # comments and conditionals such as [$WIN32]
        if brace == "{":
            stack.append((key or "").lower())
            if tuple(stack) == _PATH:
                found["apps"] = match.end()
            elif tuple(stack) == _PATH + (appid,):
                found["app"] = match.end()
            key = None
        elif brace == "}":
            if stack:
                stack.pop()
            key = None
        elif key is None:
            key = _unquote(quoted)
        else:
            if tuple(stack) == _PATH + (appid,) and key.lower() == "launchoptions":
                found["value"] = (match.start(), match.end())
                found["current"] = _unquote(quoted)
            key = None
    return found


def get_launch_options(text: str, appid: str) -> str:
    return locate(text, appid)["current"]


def set_launch_options(text: str, appid: str, options: str) -> str:
    """The file's text with the game's launch options set to `options`."""
    where = locate(text, appid)
    if where["value"]:
        start, end = where["value"]
        return text[:start] + _quote(options) + text[end:]
    line = '"LaunchOptions"\t\t{}'.format(_quote(options))
    if where["app"] is not None:
        at = where["app"]
        return text[:at] + "\n" + _indent(text, at, 1) + line + text[at:]
    if where["apps"] is not None:
        at = where["apps"]
        tabs = _indent(text, at, 1)
        block = "\n{t}{id}\n{t}{{\n{t}\t{line}\n{t}}}".format(t=tabs, id=_quote(appid), line=line)
        return text[:at] + block + text[at:]
    raise OSError("Steam's settings file looks different than expected; launch options weren't changed.")


def _indent(text: str, brace_end: int, extra: int) -> str:
    """The tabs used inside the block whose "{" ends at brace_end."""
    line_start = text.rfind("\n", 0, brace_end - 1) + 1
    tabs = len(text[line_start:brace_end - 1]) - len(text[line_start:brace_end - 1].lstrip("\t"))
    return "\t" * (tabs + extra)


def is_ours(options: str, appid: str) -> bool:
    match = _OURS.match(options)
    return bool(match) and options[:match.end()].split()[-1] == appid


def with_trainer(options: str, exe_path: str, appid: str) -> str:
    """The user's options with ours in front. Steam swaps %command% for the game; options without
    it are arguments for the game, so they go after %command%."""
    options = without_trainer(options)
    ours = '"{}" {} {}'.format(exe_path, launcher.RUN_FLAG, appid)
    if "%command%" in options:
        return "{} {}".format(ours, options)
    return "{} %command% {}".format(ours, options).strip()


def without_trainer(options: str) -> str:
    """The user's options with ours taken out again."""
    match = _OURS.match(options)
    if not match:
        return options
    rest = options[match.end():].strip()
    if rest == "%command%":
        return ""
    if rest.startswith("%command% ") and rest.count("%command%") == 1:
        return rest[len("%command% "):].strip()  # we put %command% in front of plain game arguments
    return rest


# ---------- the file ----------

def active_account(steam_path: str) -> str | None:
    """The signed-in Steam account's folder name in userdata (its 32-bit account id)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
            account = int(winreg.QueryValueEx(key, "ActiveUser")[0])
        if account:
            return str(account)
    except (OSError, ValueError, ImportError):
        pass
    # Not signed in right now: the most recent login, else the most recently used userdata folder.
    try:
        with open(os.path.join(steam_path, "config", "loginusers.vdf"), encoding="utf-8", errors="replace") as f:
            users = steam.parse_vdf(f.read()).get("users", {})
        for steam_id, user in users.items():
            if isinstance(user, dict) and user.get("MostRecent") == "1":
                return str(int(steam_id) - STEAM_ID64_BASE)
    except (OSError, ValueError):
        pass
    folders = [p for p in _userdata_configs(steam_path)]
    return os.path.basename(os.path.dirname(os.path.dirname(max(folders, key=os.path.getmtime)))) if folders else None


def _userdata_configs(steam_path: str) -> list[str]:
    root = os.path.join(steam_path, "userdata")
    try:
        names = os.listdir(root)
    except OSError:
        return []
    paths = [os.path.join(root, n, "config", "localconfig.vdf") for n in names if n.isdigit()]
    return [p for p in paths if os.path.isfile(p)]


def config_path(steam_path: str) -> str:
    account = active_account(steam_path)
    if not account:
        raise OSError("Couldn't find your Steam account's settings. Sign in to Steam once, then try again.")
    return os.path.join(steam_path, "userdata", account, "config", "localconfig.vdf")


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def write(path: str, text: str) -> None:
    """Back up the current file, then replace it atomically."""
    backup = path + ".autocheatdeck-backup"
    with open(path, "rb") as src, open(backup, "wb") as dst:
        dst.write(src.read())
    tmp = path + ".autocheatdeck-tmp"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------- Steam itself ----------

def steam_running() -> bool:
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"], capture_output=True, text=True,
                            creationflags=NO_WINDOW, check=False)
    return "steam.exe" in result.stdout.lower()


def quit_steam(steam_path: str, timeout: float = 60) -> None:
    subprocess.run([os.path.join(steam_path, "steam.exe"), "-shutdown"], creationflags=NO_WINDOW, check=False)
    deadline = time.monotonic() + timeout
    while steam_running():
        if time.monotonic() > deadline:
            raise OSError("Steam didn't close. Close it yourself (Steam menu > Exit), then try again.")
        time.sleep(1)
    time.sleep(1)  # let it finish writing its files


def start_steam(steam_path: str) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([os.path.join(steam_path, "steam.exe")], cwd=steam_path, close_fds=True, creationflags=flags)


def games_with_trainer() -> set[str]:
    """The Steam games whose launch options start their trainer. Read-only, so it works while
    Steam runs (what Steam has in memory matches the file for options it didn't change itself)."""
    steam_path = steam.find_steam_path()
    if not steam_path:
        return set()
    try:
        apps = _app_root(steam.parse_vdf(read(config_path(steam_path))))
    except OSError:
        return set()
    found = set()
    for appid, app in apps.items():
        if isinstance(app, dict):
            options = next((v for k, v in app.items() if k.lower() == "launchoptions" and isinstance(v, str)), "")
            if is_ours(options, appid):
                found.add(appid)
    return found


def _app_root(data: dict) -> dict:
    node = data
    for name in _PATH:
        node = next((v for k, v in node.items() if k.lower() == name and isinstance(v, dict)), {})
    return node


def set_trainer_with_game(appid: str, on: bool, exe_path: str) -> None:
    """Turn it on or off: closes Steam if it's running, edits the launch options, starts Steam again."""
    steam_path = steam.find_steam_path()
    if not steam_path:
        raise OSError("Steam isn't installed.")
    path = config_path(steam_path)
    was_running = steam_running()
    if was_running:
        quit_steam(steam_path)
    try:
        text = read(path)
        current = get_launch_options(text, appid)
        new = with_trainer(current, exe_path, appid) if on else without_trainer(current)
        if new != current:
            write(path, set_launch_options(text, appid, new))
    finally:
        if was_running:
            start_steam(steam_path)
