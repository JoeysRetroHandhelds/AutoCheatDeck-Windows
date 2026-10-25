import os
import sys
import tempfile
import unittest
from unittest import mock

from autocheatdeck import config, launcher


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.trainer = os.path.join(self.tmp.name, "Trainer.exe")
        with open(self.trainer, "wb") as f:
            f.write(b"MZ")
        self.cfg_path = os.path.join(self.tmp.name, "config.json")
        cfg = config.Config(self.cfg_path)
        cfg.data["launchDelay"] = 3
        cfg.set_game("42", {"title": "T", "pageUrl": "", "path": self.trainer})
        real_config = config.Config
        patcher = mock.patch.object(launcher.config, "Config", lambda: real_config(self.cfg_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_game_then_trainer_and_returns_game_exit_code(self):
        started, slept = [], []
        code = launcher.run("42", [sys.executable, "-c", "import sys; sys.exit(7)"], started.append, slept.append)
        self.assertEqual(code, 7)
        self.assertEqual(slept, [3.0])
        self.assertEqual(started, [self.trainer])  # even though the game's first process already exited

    def test_game_without_trainer_still_runs(self):
        started = []
        code = launcher.run("99", [sys.executable, "-c", "pass"], started.append, lambda _: None)
        self.assertEqual((code, started), (0, []))

    def test_trainer_failure_does_not_stop_the_game(self):
        def broken(_):
            raise OSError("blocked")

        self.assertEqual(launcher.run("42", [sys.executable, "-c", "pass"], broken, lambda _: None), 0)

    def test_launch_option_text(self):
        self.assertEqual(launcher.launch_option(r"C:\Apps\AutoCheatDeck\AutoCheatDeck.exe", "42"),
                         r'"C:\Apps\AutoCheatDeck\AutoCheatDeck.exe" --run 42 %command%')


if __name__ == "__main__":
    unittest.main()
