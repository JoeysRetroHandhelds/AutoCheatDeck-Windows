"""Start a game's trainer when the game's process appears."""

import os
import threading
import time
from typing import Callable


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def running_games(install_dirs: dict[str, str], exe_paths: list[str], ignore: set[str]) -> set[str]:
    """Return the appids whose install folder contains a running exe (other than ignored ones)."""
    dirs = {appid: _norm(d) + os.sep for appid, d in install_dirs.items()}
    ignored = {_norm(p) for p in ignore}
    running: set[str] = set()
    for exe in exe_paths:
        exe = _norm(exe)
        if exe in ignored:
            continue
        for appid, folder in dirs.items():
            if exe.startswith(folder):
                running.add(appid)
    return running


class Watcher:
    """Polls running processes and launches trainers for games that just started.

    `targets()` returns {appid: (install_dir, trainer_path)} for games with auto-launch on.
    """

    def __init__(
        self,
        targets: Callable[[], dict[str, tuple[str, str]]],
        list_processes: Callable[[], list[str]],
        launch: Callable[[str, str], None],
        delay: Callable[[], float],
        interval: float = 3.0,
        on_change: Callable[[set[str]], None] | None = None,
    ):
        self._targets = targets
        self._list_processes = list_processes
        self._launch = launch
        self._delay = delay
        self._interval = interval
        self._seen: set[str] = set()
        # Games already running when watching starts already have their trainer (or the user chose
        # not to start one); only launch for games that start afterwards.
        self._primed = False
        self._on_change = on_change
        self.running: set[str] = set()
        self._pending: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, now: float) -> None:
        targets = self._targets()
        running = running_games(
            {appid: d for appid, (d, _) in targets.items()},
            self._list_processes(),
            ignore={trainer for _, trainer in targets.values()},
        )
        if running != self.running:
            self.running = running
            if self._on_change:
                self._on_change(set(running))
        if not self._primed:
            if not targets:  # the game list hasn't loaded yet; prime once there's something to watch
                return
            self._primed = True
            self._seen = running
            return
        for appid in running - self._seen:
            self._pending[appid] = now + self._delay()
        for appid in self._seen - running:
            self._pending.pop(appid, None)
        self._seen = running
        for appid, due in list(self._pending.items()):
            if now >= due and appid in targets:
                del self._pending[appid]
                self._launch(appid, targets[appid][1])

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick(time.monotonic())
            except Exception:  # keep watching even if one poll fails, but record why
                from . import log

                log.get().exception("watcher: poll failed")

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
