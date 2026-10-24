import io
import os
import tempfile
import unittest
from unittest import mock

from autocheatdeck import fling, gamepad, startup, steam
from autocheatdeck.watcher import Watcher

GAME = r"C:\Games\Hades"
GAME_EXE = r"C:\Games\Hades\Hades.exe"


def make_watcher(procs, launched, targets=None, changes=None):
    return Watcher(
        targets=lambda: {"1": (GAME, r"C:\t.exe")} if targets is None else targets(),
        list_processes=lambda: procs,
        launch=lambda appid, path: launched.append(appid),
        delay=lambda: 0,
        on_change=None if changes is None else changes.append,
    )


class WatcherPrimingTest(unittest.TestCase):
    def test_game_already_running_at_start_gets_no_second_trainer(self):
        launched: list[str] = []
        w = make_watcher([GAME_EXE], launched)
        w.tick(0)
        w.tick(10)
        self.assertEqual(launched, [])

    def test_priming_waits_for_the_game_list(self):
        launched: list[str] = []
        loaded = {"ready": False}
        w = make_watcher([GAME_EXE], launched,
                         targets=lambda: {"1": (GAME, r"C:\t.exe")} if loaded["ready"] else {})
        w.tick(0)  # list not loaded yet: must not prime with "nothing running"
        loaded["ready"] = True
        w.tick(1)
        w.tick(2)
        self.assertEqual(launched, [])

    def test_reports_running_games(self):
        procs: list[str] = []
        changes: list[set] = []
        w = make_watcher(procs, [], changes=changes)
        w.tick(0)
        procs.append(GAME_EXE)
        w.tick(1)
        procs.clear()
        w.tick(2)
        self.assertEqual(changes, [{"1"}, set()])
        self.assertEqual(w.running, set())


class VdfEscapeTest(unittest.TestCase):
    def test_quotes_and_backslashes(self):
        data = steam.parse_vdf(r'"AppState" { "name" "The \"Quoted\" Game" "path" "D:\\Steam" }')
        self.assertEqual(data["AppState"]["name"], 'The "Quoted" Game')
        self.assertEqual(data["AppState"]["path"], "D:\\Steam")


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self._url = url

    def geturl(self):
        return self._url


class SafeReplaceTest(unittest.TestCase):
    PAGE = "https://flingtrainer.com/trainer/hades-trainer/"
    URL = "https://flingtrainer.com/downloads/abc"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "trainers", "1")
        os.makedirs(self.target)
        with open(os.path.join(self.target, "Old Trainer.exe"), "wb") as f:
            f.write(b"MZ old")

    def tearDown(self):
        self.tmp.cleanup()

    def download(self, payload: bytes):
        final = "https://flingtrainer.com/wp-content/uploads/New%20Trainer.exe"
        with mock.patch.object(fling, "_get", return_value=FakeResponse(payload, final)):
            return fling.download(self.URL, self.PAGE, self.target)

    def test_replaces_and_cleans_up(self):
        result = self.download(b"MZ new")
        self.assertEqual(os.listdir(self.target), ["New Trainer.exe"])
        self.assertEqual(result["path"], os.path.join(self.target, "New Trainer.exe"))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.target))), ["1"])

    def test_running_trainer_is_left_alone(self):
        with mock.patch.object(fling.os, "rename", side_effect=PermissionError("in use")):
            with self.assertRaisesRegex(ValueError, "Close the trainer"):
                self.download(b"MZ new")
        self.assertEqual(os.listdir(self.target), ["Old Trainer.exe"])
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.target))), ["1"])

    def test_unusable_download_restores_old_trainer(self):
        with self.assertRaises(ValueError):
            self.download(b"<html>not a trainer</html>")
        self.assertEqual(os.listdir(self.target), ["Old Trainer.exe"])
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.target))), ["1"])


class GamepadTest(unittest.TestCase):
    def test_stick_acts_as_dpad(self):
        self.assertEqual(gamepad.pressed_names(0x1000, 0, 30000), {"a", "up"})
        self.assertEqual(gamepad.pressed_names(0, -30000, 100), {"left"})
        self.assertEqual(gamepad.pressed_names(0, 5000, -5000), set())

    def test_repeat_only_for_directions(self):
        r = gamepad.Repeater()
        events = sorted(r.update({"down", "a"}, 0.0))
        self.assertEqual(events, ["a", "down"])
        self.assertEqual(r.update({"down", "a"}, 0.2), [])
        self.assertEqual(r.update({"down", "a"}, 0.41), ["down"])
        self.assertEqual(r.update({"down", "a"}, 0.45), [])
        self.assertEqual(r.update({"down", "a"}, 0.53), ["down"])
        self.assertEqual(r.update(set(), 0.6), [])
        self.assertEqual(r.update({"a"}, 0.7), ["a"])


class StartupTest(unittest.TestCase):
    def test_command_quotes_path_and_starts_in_background(self):
        self.assertEqual(startup.command_for(r"C:\Games\Auto Cheat\AutoCheatDeck.exe"),
                         '"C:\\Games\\Auto Cheat\\AutoCheatDeck.exe" --background')


if __name__ == "__main__":
    unittest.main()
