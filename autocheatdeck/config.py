"""Portable storage: everything lives in a `data` folder next to the exe."""

import json
import os
import sys
import threading


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    path = os.path.join(app_dir(), "data")
    os.makedirs(path, exist_ok=True)
    return path


def trainers_dir() -> str:
    return os.path.join(data_dir(), "trainers")


DEFAULTS = {"games": {}, "launchDelay": 5, "checkUpdates": True}


class Config:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(data_dir(), "config.json")
        self._lock = threading.Lock()
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            self.data.update(saved)
        if not isinstance(self.data.get("games"), dict):
            self.data["games"] = {}

    def save(self) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def game(self, appid: str) -> dict | None:
        return self.data["games"].get(appid)

    def set_game(self, appid: str, entry: dict | None) -> None:
        if entry is None:
            self.data["games"].pop(appid, None)
        else:
            self.data["games"][appid] = entry
        self.save()
