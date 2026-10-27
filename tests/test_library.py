import os
import struct
import tempfile
import unittest
from unittest import mock

from autocheatdeck import library
from autocheatdeck.steam import Game

CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<Game configVersion="1">
  <Identity Name="Studio.Replaced" Publisher="CN=x" Version="1.0.0.0"/>
  <ShellVisuals DefaultDisplayName="{name}" PublisherDisplayName="Studio"/>
</Game>
"""


def gaming_root_marker(*paths: str) -> bytes:
    body = "".join(p + "\0" for p in paths).encode("utf-16-le")
    return b"RGBX" + struct.pack("<I", len(paths)) + body


class XboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drive = self.tmp.name + os.sep

    def tearDown(self):
        self.tmp.cleanup()

    def add_game(self, root: str, folder: str, name: str) -> None:
        content = os.path.join(self.drive, root, folder, "Content")
        os.makedirs(content)
        with open(os.path.join(content, "MicrosoftGame.config"), "w", encoding="utf-8") as f:
            f.write(CONFIG.format(name=name))

    def write_marker(self, *paths: str) -> None:
        with open(os.path.join(self.drive, ".GamingRoot"), "wb") as f:
            f.write(gaming_root_marker(*paths))

    def test_reads_custom_install_folder_from_marker(self):
        self.write_marker("WINDOWS BACKUP\\XboxGames")
        self.add_game("WINDOWS BACKUP\\XboxGames", "Replaced", "Replaced")
        os.makedirs(os.path.join(self.drive, "WINDOWS BACKUP", "XboxGames", "GameSave"))  # not a game
        games = library.xbox_games([self.drive])
        self.assertEqual([(g.name, g.source) for g in games], [("Replaced", "Xbox")])
        self.assertTrue(games[0].install_dir.endswith(os.path.join("Replaced", "Content")))
        self.assertEqual(games[0].appid, "xbox-Replaced")
        self.assertEqual(games[0].build, "1.0.0.0")  # the package version, for "game updated"

    def test_localized_name_falls_back_to_folder(self):
        self.write_marker("XboxGames")
        self.add_game("XboxGames", "Call of Duty", "ms-resource:AppDisplayName")
        self.assertEqual([g.name for g in library.xbox_games([self.drive])], ["Call of Duty"])

    def test_no_marker_or_bad_marker_means_no_games(self):
        self.assertEqual(library.xbox_games([self.drive]), [])
        with open(os.path.join(self.drive, ".GamingRoot"), "wb") as f:
            f.write(b"junk")
        self.assertEqual(library.xbox_games([self.drive]), [])

    def test_ids_are_safe_folder_names(self):
        self.write_marker("XboxGames")
        self.add_game("XboxGames", "Denshattack!", "Denshattack!")
        appid = library.xbox_games([self.drive])[0].appid
        self.assertEqual(appid, "xbox-Denshattack_")
        self.assertNotRegex(appid, r'[<>:"/\\|?*]')


class ManualAndMergeTest(unittest.TestCase):
    def test_manual_games_skip_missing_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            entries = [{"id": library.manual_id(folder), "name": "My Game", "dir": folder},
                       {"id": "manual-gone", "name": "Gone", "dir": os.path.join(folder, "missing")}]
            games = library.manual_games(entries)
        self.assertEqual([(g.name, g.source) for g in games], [("My Game", "Manual")])

    def test_manual_id_is_stable_and_case_insensitive(self):
        self.assertEqual(library.manual_id(r"C:\Games\Foo"), library.manual_id(r"c:\games\foo\\"))
        self.assertTrue(library.manual_id(r"C:\Games\Foo").startswith("manual-"))

    def test_merge_sorts_by_last_played_including_recorded_times(self):
        steam_games = [Game("1", "Steam Old", "s1", 100), Game("2", "Steam Never", "s2", 0)]
        xbox = [Game("xbox-A", "Xbox Game", "x1", source="Xbox")]
        with mock.patch.object(library.steam, "installed_games", return_value=steam_games), \
                mock.patch.object(library, "xbox_games", return_value=xbox):
            games = library.all_games([], {"xbox-A": 500})
        self.assertEqual([g.name for g in games], ["Xbox Game", "Steam Old", "Steam Never"])

    def test_game_updated_compares_the_recorded_version(self):
        game = Game("1", "Hades", "d", build="200")
        self.assertFalse(library.game_updated({"gameBuild": "200"}, game))
        self.assertTrue(library.game_updated({"gameBuild": "100"}, game))
        self.assertFalse(library.game_updated({}, game))  # nothing recorded yet
        self.assertFalse(library.game_updated(None, game))
        self.assertFalse(library.game_updated({"gameBuild": "100"}, Game("1", "Hades", "d")))  # version unknown

    def test_recorded_play_time_keeps_the_version(self):
        with mock.patch.object(library.steam, "installed_games", return_value=[Game("1", "S", "s", 1, build="7")]), \
                mock.patch.object(library, "xbox_games", return_value=[]):
            self.assertEqual(library.all_games([], {"1": 99})[0].build, "7")

    def test_one_failing_store_does_not_hide_the_rest(self):
        with mock.patch.object(library.steam, "installed_games", side_effect=OSError("boom")), \
                mock.patch.object(library, "xbox_games", return_value=[Game("xbox-A", "X", "x", source="Xbox")]):
            self.assertEqual([g.name for g in library.all_games([], {})], ["X"])


if __name__ == "__main__":
    unittest.main()
