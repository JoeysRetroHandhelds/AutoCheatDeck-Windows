"""Self-update the portable exe from GitHub releases.

A running exe can't be overwritten on Windows but it can be renamed, so an update downloads
the new exe beside the current one, verifies its SHA-256, renames the running exe to the
backup name and moves the new one into place. The caller then relaunches.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from . import tls

REPO = "JoeysRetroHandhelds/AutoCheatDeck-Windows"
API_URL = "https://api.github.com/repos/{}/releases/latest".format(REPO)
ASSET_NAME = "AutoCheatDeck.exe"
ALLOWED_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
USER_AGENT = "AutoCheatDeck-Windows-Updater"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


def parse_version(value: str) -> tuple[int, ...]:
    match = re.match(r"v?(\d+(?:\.\d+)*)", value.strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _open(url: str):
    if urllib.parse.urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError("URL not allowed: {}".format(url))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    response = urllib.request.urlopen(request, timeout=30, context=tls.create_context())
    if urllib.parse.urlparse(response.geturl()).hostname not in ALLOWED_HOSTS:
        raise ValueError("Redirected to disallowed host")
    return response


def latest_release() -> dict:
    with _open(API_URL) as response:
        return json.load(response)


def pick_assets(release: dict) -> tuple[str, str]:
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    if ASSET_NAME not in assets or ASSET_NAME + ".sha256" not in assets:
        raise ValueError("Release is missing {} or its .sha256".format(ASSET_NAME))
    return assets[ASSET_NAME], assets[ASSET_NAME + ".sha256"]


def check(current_version: str) -> dict:
    release = latest_release()
    latest = release.get("tag_name", "")
    return {"current": current_version, "latest": latest, "available": is_newer(latest, current_version),
            "notes": release.get("body") or ""}


def backup_path(exe_path: str) -> str:
    root, ext = os.path.splitext(exe_path)
    return root + ".old" + ext


def _download(url: str, path: str) -> str:
    digest = hashlib.sha256()
    size = 0
    with _open(url) as response, open(path, "wb") as out:
        while chunk := response.read(64 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise ValueError("Download too large")
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def install(exe_path: str, release: dict | None = None) -> str:
    """Replace exe_path with the latest release's exe, keeping the old one as a backup."""
    release = release or latest_release()
    exe_url, sha_url = pick_assets(release)
    new_path = exe_path + ".new"
    try:
        with _open(sha_url) as response:
            expected = response.read().decode().split()[0].lower()
        actual = _download(exe_url, new_path)
        if actual != expected:
            raise ValueError("Checksum mismatch: expected {}, got {}".format(expected, actual))
        with open(new_path, "rb") as f:
            if f.read(2) != b"MZ":
                raise ValueError("Downloaded file is not a Windows program")
        _swap(exe_path, new_path)
    finally:
        if os.path.exists(new_path):
            os.remove(new_path)
    return release.get("tag_name", "")


def _swap(exe_path: str, replacement: str) -> None:
    """Move the running exe to the backup name and put replacement in its place."""
    backup = backup_path(exe_path)
    if os.path.exists(backup):
        os.remove(backup)
    os.replace(exe_path, backup)
    try:
        os.replace(replacement, exe_path)
    except OSError:
        os.replace(backup, exe_path)
        raise


def has_backup(exe_path: str) -> bool:
    return os.path.isfile(backup_path(exe_path))


def rollback(exe_path: str) -> None:
    """Swap the backup back in; the version being replaced becomes the new backup."""
    backup = backup_path(exe_path)
    if not os.path.isfile(backup):
        raise ValueError("No previous version to restore")
    staged = exe_path + ".restore"
    os.replace(backup, staged)
    try:
        _swap(exe_path, staged)
    except OSError:
        if os.path.exists(staged):
            os.replace(staged, backup)
        raise


def cleanup(exe_path: str) -> None:
    for leftover in (exe_path + ".new", exe_path + ".restore"):
        try:
            os.remove(leftover)
        except OSError:
            pass


def relaunch(exe_path: str) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    args = [exe_path, "--after-update", str(os.getpid()), *sys.argv[1:]]
    subprocess.Popen(args, close_fds=True, creationflags=flags)
