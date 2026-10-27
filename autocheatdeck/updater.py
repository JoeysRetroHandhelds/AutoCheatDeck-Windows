"""Self-update the portable app folder from GitHub releases.

The release is a zip of the whole app folder (AutoCheatDeck.exe + _internal/ + docs). Windows
locks a running app's files, so updating is a hand-off:

1. The running app downloads the zip, checks its SHA-256 and the release key's signature over it,
   and unpacks it to data/update/staging.
2. It starts the staged exe with --finish-update <its pid> <app dir> and exits.
3. The staged copy waits for that pid, moves the old app files to data/update/outgoing, copies
   itself into the app folder, keeps the old files as data/update/previous and relaunches the
   app folder's exe. The relaunched app deletes the staging folder.

Restoring the previous version stages a copy of data/update/previous and runs the same hand-off.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile

from . import ed25519, tls

REPO = "JoeysRetroHandhelds/AutoCheatDeck-Windows"
API_URL = "https://api.github.com/repos/{}/releases/latest".format(REPO)
ASSET_NAME = "AutoCheatDeck-win64.zip"
EXE_NAME = "AutoCheatDeck.exe"
ALLOWED_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
USER_AGENT = "AutoCheatDeck-Windows-Updater"
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
# Releases are signed in CI with the matching private key (tools/sign_release.py). The .sha256 only
# catches a broken download; the signature is what proves the zip came from this project.
PUBLIC_KEY = bytes.fromhex("056b3989bd5f6e3328bb05186cdc755f888961a1c2bde7cfc6445cc6b73afd6a")
# Never touched by an update: the user's settings and trainers (and the update area itself).
KEEP = {"data"}


def parse_version(value: str) -> tuple[int, ...]:
    match = re.match(r"v?(\d+(?:\.\d+)*)", value.strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("URL not allowed: {}".format(url))


def _open(url: str):
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    response = urllib.request.urlopen(request, timeout=30, context=tls.create_context())
    try:
        _check_url(response.geturl())
    except ValueError:
        response.close()
        raise
    return response


def latest_release() -> dict:
    with _open(API_URL) as response:
        return json.load(response)


def pick_assets(release: dict) -> tuple[str, str, str]:
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    names = (ASSET_NAME, ASSET_NAME + ".sha256", ASSET_NAME + ".sig")
    missing = [name for name in names if name not in assets]
    if missing:
        raise ValueError("Release is missing {}".format(", ".join(missing)))
    return assets[names[0]], assets[names[1]], assets[names[2]]


def signed_message(tag: str, sha256_hex: str) -> bytes:
    """What a release signature covers. The tag is included so an older signed zip can't be
    offered as a newer version."""
    return "AutoCheatDeck-Windows release\n{}\n{}\n".format(tag, sha256_hex.lower()).encode("utf-8")


def verify_signature(tag: str, sha256_hex: str, signature_text: str) -> None:
    try:
        signature = base64.b64decode(signature_text.strip(), validate=True)
    except (binascii.Error, ValueError):
        signature = b""
    if not ed25519.verify(PUBLIC_KEY, signed_message(tag, sha256_hex), signature):
        raise ValueError("The update's signature is not valid; not installing it.")


def check(current_version: str) -> dict:
    release = latest_release()
    latest = release.get("tag_name", "")
    return {"current": current_version, "latest": latest, "available": is_newer(latest, current_version),
            "notes": release.get("body") or ""}


# ---------- paths ----------

def update_dir(app_dir: str) -> str:
    return os.path.join(app_dir, "data", "update")


def staging_dir(app_dir: str) -> str:
    return os.path.join(update_dir(app_dir), "staging")


def previous_dir(app_dir: str) -> str:
    return os.path.join(update_dir(app_dir), "previous")


def has_backup(app_dir: str) -> bool:
    return os.path.isfile(os.path.join(previous_dir(app_dir), EXE_NAME))


def _rmtree(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def _retry(action, attempts: int = 20, delay: float = 0.5):
    """Files of a just-exited process (or one antivirus is scanning) can stay locked briefly."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


# ---------- step 1: stage ----------

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


def _extract_app(zip_path: str, dest: str) -> str:
    """Extract the release zip into dest and return the folder that holds the exe."""
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            target = os.path.realpath(os.path.join(dest, member))
            if target != root and not target.startswith(root + os.sep):
                raise ValueError("Unsafe path in archive: {}".format(member))
        archive.extractall(dest)
    for dirpath, _dirs, files in os.walk(dest):
        if EXE_NAME in files:
            with open(os.path.join(dirpath, EXE_NAME), "rb") as f:
                if f.read(2) != b"MZ":
                    raise ValueError("{} in the release is not a Windows program".format(EXE_NAME))
            return dirpath
    raise ValueError("Release zip does not contain {}".format(EXE_NAME))


def stage_update(app_dir: str, release: dict | None = None) -> str:
    """Download, verify and unpack the latest release into the staging folder. Returns its tag."""
    release = release or latest_release()
    zip_url, sha_url, sig_url = pick_assets(release)
    tag = release.get("tag_name", "")
    work = update_dir(app_dir)
    os.makedirs(work, exist_ok=True)
    zip_path = os.path.join(work, "download.zip")
    extract = os.path.join(work, "extract")
    staging = staging_dir(app_dir)
    try:
        with _open(sha_url) as response:
            words = response.read().decode(errors="replace").split()
        if not words:
            raise ValueError("Release checksum file is empty")
        expected = words[0].lower()
        with _open(sig_url) as response:
            signature = response.read(4096).decode("ascii", errors="replace")
        actual = _download(zip_url, zip_path)
        if actual != expected:
            raise ValueError("Checksum mismatch: expected {}, got {}".format(expected, actual))
        verify_signature(tag, actual, signature)
        _rmtree(extract)
        app_root = _extract_app(zip_path, extract)
        _rmtree(staging)
        os.replace(app_root, staging)
    finally:
        _rmtree(zip_path)
        _rmtree(extract)
    return tag


def stage_rollback(app_dir: str) -> None:
    if not has_backup(app_dir):
        raise ValueError("No previous version to restore")
    staging = staging_dir(app_dir)
    _rmtree(staging)
    shutil.copytree(previous_dir(app_dir), staging)


def start_finisher(app_dir: str, wait_pid: int) -> None:
    """Run the staged exe so it can replace the app folder once this process exits."""
    exe = os.path.join(staging_dir(app_dir), EXE_NAME)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe, "--finish-update", str(wait_pid), app_dir], cwd=app_dir, close_fds=True,
                     creationflags=flags)


# ---------- step 2: swap (runs from the staged copy) ----------

def finish(source_dir: str, app_dir: str) -> None:
    """Replace the app files in app_dir with source_dir's, keeping the old ones as `previous`."""
    incoming = [name for name in os.listdir(source_dir) if name not in KEEP]
    if EXE_NAME not in incoming:
        raise ValueError("Staged update is missing {}".format(EXE_NAME))
    outgoing = os.path.join(update_dir(app_dir), "outgoing")
    _rmtree(outgoing)
    os.makedirs(outgoing)

    # Only move what a release owns, so anything else the user keeps in the folder stays put.
    owned = set(incoming) | {EXE_NAME, "_internal"}
    current = [name for name in os.listdir(app_dir) if name in owned]
    moved: list[str] = []
    copied: list[str] = []
    try:
        for name in current:
            _retry(lambda: os.replace(os.path.join(app_dir, name), os.path.join(outgoing, name)))
            moved.append(name)
        for name in incoming:
            src, dst = os.path.join(source_dir, name), os.path.join(app_dir, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            copied.append(name)
    except Exception:
        for name in copied:
            _rmtree(os.path.join(app_dir, name))
        for name in moved:
            os.replace(os.path.join(outgoing, name), os.path.join(app_dir, name))
        raise

    previous = previous_dir(app_dir)
    _rmtree(previous)
    os.replace(outgoing, previous)


def cleanup(app_dir: str) -> None:
    """Remove leftovers once the app runs from its real folder again."""
    for name in ("staging", "outgoing", "extract", "download.zip"):
        try:
            _retry(lambda: _rmtree(os.path.join(update_dir(app_dir), name)), attempts=6)
        except OSError:
            pass


def launch(exe_path: str, *args: str) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe_path, *args], cwd=os.path.dirname(exe_path), close_fds=True, creationflags=flags)
