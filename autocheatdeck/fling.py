"""Search and download trainers from flingtrainer.com."""

import hashlib
import html
import os
import re
import shutil
import urllib.parse
import urllib.request
import zipfile

from . import tls

BASE_URL = "https://flingtrainer.com"
ALLOWED_HOSTS = {"flingtrainer.com", "www.flingtrainer.com"}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AutoCheatDeck-Windows"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

_TRAINER_LINK = re.compile(r'<a[^>]+href="(https://flingtrainer\.com/trainer/[^"#]+/)"[^>]*>([^<]+)</a>')
_DOWNLOAD_LINK = re.compile(
    r'<a[^>]+href="(https://flingtrainer\.com/downloads/[^"]+)"[^>]*title="([^"]+)"[^>]*class="attachment-link"'
)
_LAST_UPDATED = re.compile(r"Last Updated:\s*([0-9.]+)")


def _check_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("URL not allowed: {}".format(url))
    return url


def _get(url: str, referer: str | None = None) -> urllib.request.addinfourl:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(_check_url(url), headers=headers)
    response = urllib.request.urlopen(request, timeout=30, context=tls.create_context())
    _check_url(response.geturl())
    return response


def _fetch_text(url: str) -> str:
    with _get(url) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_search(page: str) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = set()
    for url, title in _TRAINER_LINK.findall(page):
        title = html.unescape(title).strip()
        if url in seen or not title:
            continue
        seen.add(url)
        results.append({"url": url, "title": title})
    return results


def parse_trainer_page(page: str) -> dict:
    versions = [
        {"url": url, "title": html.unescape(title).strip()} for url, title in _DOWNLOAD_LINK.findall(page)
    ]
    updated = _LAST_UPDATED.search(page)
    return {"versions": versions, "lastUpdated": updated.group(1) if updated else None}


def search(name: str) -> list[dict]:
    query = urllib.parse.urlencode({"s": name})
    return parse_search(_fetch_text("{}/?{}".format(BASE_URL, query)))


def list_versions(page_url: str) -> dict:
    return parse_trainer_page(_fetch_text(page_url))


def _find_exe(directory: str) -> str | None:
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if name.lower().endswith(".exe"):
                return os.path.join(root, name)
    return None


def download(download_url: str, page_url: str, target_dir: str) -> dict:
    """Download a trainer into target_dir (replacing its contents) and return the exe path."""
    _check_url(page_url)
    os.makedirs(os.path.dirname(target_dir), exist_ok=True)
    tmp_path = target_dir + ".part"
    try:
        with _get(download_url, referer=page_url) as response:
            final_name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(response.geturl()).path))
            digest = hashlib.sha256()
            size = 0
            with open(tmp_path, "wb") as out:
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Download too large")
                    digest.update(chunk)
                    out.write(chunk)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    # Set the current trainer aside instead of deleting it: on Windows the rename fails while the
    # trainer is running, and it can be put back if the new download turns out to be unusable.
    previous = target_dir + ".previous"
    if os.path.exists(previous):
        shutil.rmtree(previous)
    if os.path.isdir(target_dir):
        try:
            os.rename(target_dir, previous)
        except OSError:
            os.remove(tmp_path)
            raise ValueError("Close the trainer before replacing it.")

    try:
        with open(tmp_path, "rb") as f:
            magic = f.read(4)
        os.makedirs(target_dir)

        if magic.startswith(b"PK"):
            with zipfile.ZipFile(tmp_path) as archive:
                root = os.path.realpath(target_dir)
                for member in archive.namelist():
                    dest = os.path.realpath(os.path.join(target_dir, member))
                    if not dest.startswith(root + os.sep):
                        raise ValueError("Unsafe path in archive: {}".format(member))
                archive.extractall(target_dir)
            exe_path = _find_exe(target_dir)
            if exe_path is None:
                raise ValueError("No .exe found in archive")
        elif magic.startswith(b"MZ"):
            name = final_name if final_name.lower().endswith(".exe") else "trainer.exe"
            exe_path = os.path.join(target_dir, name)
            shutil.move(tmp_path, exe_path)
        else:
            raise ValueError("Downloaded file is not an exe or zip")
    except BaseException:
        shutil.rmtree(target_dir, ignore_errors=True)
        if os.path.isdir(previous):
            os.rename(previous, target_dir)
        raise
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    shutil.rmtree(previous, ignore_errors=True)

    return {"path": exe_path, "sha256": digest.hexdigest(), "size": size}
