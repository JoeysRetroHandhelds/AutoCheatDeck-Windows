"""Send logs: everything needed to diagnose a problem, in one zip the user can send.

There is no server to upload to, so the zip is saved for the user to attach to a GitHub issue (or
send another way). Their Windows user name is blanked out of every file, so paths like
C:\\Users\\<name>\\... don't identify them.
"""

import datetime
import json
import os
import platform
import re
import sys
import urllib.parse
import zipfile

ISSUES_URL = "https://github.com/JoeysRetroHandhelds/AutoCheatDeck-Windows/issues/new"


def redact(text: str, user: str | None = None, home: str | None = None) -> str:
    """Blank out the user name (in paths and anywhere else it appears as a whole word)."""
    home = home if home is not None else os.path.expanduser("~")
    user = user if user is not None else os.environ.get("USERNAME", "")
    if home and len(home) > 3:
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.I)
        text = re.sub(re.escape(home.replace("\\", "/")), "%USERPROFILE%", text, flags=re.I)
    if user and len(user) > 1:
        text = re.sub(r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])".format(re.escape(user)), "<user>", text, flags=re.I)
    return text


def system_info(version: str, extra: dict | None = None) -> dict:
    info = {
        "app": version,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "windows": platform.version(),
        "release": platform.release(),
        "machine": platform.machine(),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    info.update(extra or {})
    return info


def files_to_include(data_dir: str) -> dict[str, str]:
    """{name in the zip: path on disk} for the files worth sending (those that exist)."""
    candidates = {
        "autocheatdeck.log": os.path.join(data_dir, "autocheatdeck.log"),
        "autocheatdeck.log.1": os.path.join(data_dir, "autocheatdeck.log.1"),
        "config.json": os.path.join(data_dir, "config.json"),
    }
    return {name: path for name, path in candidates.items() if os.path.isfile(path)}


def build_zip(dest: str, data_dir: str, info: dict, user: str | None = None, home: str | None = None) -> str:
    """Write the diagnostics zip to dest and return its path. Every text file is redacted."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("system.json", redact(json.dumps(info, indent=2), user, home))
        for name, path in files_to_include(data_dir).items():
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            archive.writestr(name, redact(text, user, home))
    return dest


def default_destination() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    folder = desktop if os.path.isdir(desktop) else os.path.expanduser("~")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(folder, "AutoCheatDeck-logs-{}.zip".format(stamp))


def issue_url(info: dict) -> str:
    """A new GitHub issue with the version details filled in (the user attaches the zip)."""
    body = ("**What happened?**\n\n\n**What did you expect?**\n\n\n"
            "**Logs:** drag the AutoCheatDeck-logs zip from your Desktop here.\n\n"
            "---\nAutoCheatDeck {app}, Windows {release} ({windows}), {machine}").format(**info)
    return ISSUES_URL + "?" + urllib.parse.urlencode({"body": body})
