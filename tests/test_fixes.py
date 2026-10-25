import io
import os
import tempfile
import unittest
from unittest import mock

from autocheatdeck import fling, gamepad, steam


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


class GamepadFocusTest(unittest.TestCase):
    def make(self):
        with mock.patch.object(gamepad, "_load_xinput", return_value=None):  # no polling thread
            return gamepad.Gamepad(on_press=lambda name: None)

    def test_only_wanted_while_focused_or_a_dialog_is_open(self):
        pad = self.make()
        self.assertFalse(pad._wanted.is_set())
        pad.set_active(True)
        self.assertTrue(pad._wanted.is_set())
        pad.set_active(False)
        self.assertFalse(pad._wanted.is_set())

    def test_dialog_keeps_it_on_after_the_window_loses_focus(self):
        pad = self.make()
        pad.set_active(True)
        pad.hold()
        pad.set_active(False)  # the native dialog took focus
        self.assertTrue(pad._wanted.is_set())
        pad.release()
        self.assertFalse(pad._wanted.is_set())

    def test_stop_wakes_the_sleeping_thread(self):
        pad = self.make()
        pad.stop()
        self.assertTrue(pad._wanted.is_set() and pad._stop.is_set())


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


if __name__ == "__main__":
    unittest.main()
