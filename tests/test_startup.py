import base64
import os
import tempfile
import unittest
from unittest import mock

from autocheatdeck import startup

OLD_EXE = r"C:\A\AutoCheatDeck.exe"
NEW_EXE = r"D:\B\AutoCheatDeck.exe"


class StartupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"LOCALAPPDATA": self.tmp.name, "USERDOMAIN": "PC",
                                           "USERNAME": "O'Brien"})
        env.start()
        self.addCleanup(env.stop)
        self.run_entry = None

        def set_run_entry(enabled):
            self.run_entry = "launcher start.ps1" if enabled else None

        for patch in (mock.patch.object(startup, "_set_run_entry", side_effect=set_run_entry),
                      mock.patch.object(startup, "_run_entry", side_effect=lambda: self.run_entry)):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_encoded_script_sets_quoted_values(self):
        decoded = base64.b64decode(startup.encoded_script("Write-Output $User", User=r"PC\O'Brien"))
        self.assertEqual(decoded.decode("utf-16-le"), "$User = 'PC\\O''Brien'\nWrite-Output $User")

    def test_existing_task_needs_no_prompt(self):
        with mock.patch.object(startup, "task_exists", return_value=True), \
                mock.patch.object(startup, "_run_elevated") as elevated:
            self.assertEqual(startup.enable(OLD_EXE), "task")
        elevated.assert_not_called()
        self.assertTrue(startup.is_enabled())

    def test_approved_prompt_registers_task_for_this_user(self):
        with mock.patch.object(startup, "task_exists", return_value=False), \
                mock.patch.object(startup, "_run_elevated", return_value=True) as elevated:
            self.assertEqual(startup.enable(OLD_EXE), "task")
        self.assertEqual(elevated.call_args.kwargs["User"], r"PC\O'Brien")
        self.assertIsNone(self.run_entry)

    def test_declined_prompt_falls_back_to_run_entry(self):
        with mock.patch.object(startup, "task_exists", return_value=False), \
                mock.patch.object(startup, "_run_elevated", return_value=False):
            self.assertEqual(startup.enable(OLD_EXE), "run")
        self.assertEqual(self.run_entry, "launcher start.ps1")

    def test_disable_needs_no_prompt_and_keeps_path(self):
        with mock.patch.object(startup, "task_exists", return_value=True):
            startup.enable(OLD_EXE)
        with mock.patch.object(startup, "_run_elevated") as elevated:
            startup.disable()
        elevated.assert_not_called()
        self.assertFalse(startup.is_enabled())
        self.assertEqual(startup._read_location()["exe"], OLD_EXE)

    def test_moved_folder_updates_launcher_without_prompt(self):
        with mock.patch.object(startup, "task_exists", return_value=True):
            startup.enable(OLD_EXE)
        startup.refresh(NEW_EXE)
        self.assertEqual(startup._read_location(), {"exe": NEW_EXE, "enabled": True})

    def test_old_style_run_entry_is_migrated_to_the_launcher(self):
        self.run_entry = '"{}" --background'.format(OLD_EXE)
        startup.refresh(OLD_EXE)
        self.assertEqual(self.run_entry, "launcher start.ps1")
        self.assertTrue(startup.is_enabled())
        with open(os.path.join(startup.launcher_dir(), "start.ps1"), encoding="utf-8") as f:
            self.assertIn("--background", f.read())


if __name__ == "__main__":
    unittest.main()
