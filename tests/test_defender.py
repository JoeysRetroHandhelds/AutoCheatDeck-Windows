import os
import tempfile
import unittest

from autocheatdeck import defender


class DefenderTest(unittest.TestCase):
    def test_missing_trainer(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = os.path.join(folder, "Trainer.exe")
            entry = {"title": "T", "path": exe}
            self.assertTrue(defender.trainer_missing(entry))  # installed, file gone
            open(exe, "wb").close()
            self.assertFalse(defender.trainer_missing(entry))
        self.assertFalse(defender.trainer_missing(None))  # never installed
        self.assertFalse(defender.trainer_missing({}))

    def test_blocked_launch_errors(self):
        virus = OSError("Operation did not complete successfully because the file contains a virus")
        virus.winerror = 225
        self.assertTrue(defender.blocked(virus))
        smart_app_control = OSError("blocked")
        smart_app_control.winerror = 4551
        self.assertTrue(defender.blocked(smart_app_control))
        self.assertTrue(defender.blocked(Exception("Couldn't start the trainer (error 225)")))  # admin helper
        not_found = OSError("missing")
        not_found.winerror = 2
        self.assertFalse(defender.blocked(not_found))
        self.assertFalse(defender.blocked(Exception("Couldn't start the trainer (error 740)")))
        self.assertFalse(defender.blocked(ValueError("something else")))
