import os
import tempfile
import unittest
from unittest import mock

from autocheatdeck import steamcfg

EXE = r"C:\AutoCheatDeck\AutoCheatDeck.exe"
CONFIG = '''"UserLocalConfigStore"
{
\t"Software"
\t{
\t\t"Valve"
\t\t{
\t\t\t"Steam"
\t\t\t{
\t\t\t\t"Apps"
\t\t\t\t{
\t\t\t\t\t"100"
\t\t\t\t\t{
\t\t\t\t\t\t"LastPlayed"\t\t"1"
\t\t\t\t\t\t"LaunchOptions"\t\t"-dx11 -windowed"
\t\t\t\t\t}
\t\t\t\t\t"200"
\t\t\t\t\t{
\t\t\t\t\t\t"LaunchOptions"\t\t"PROTON_LOG=1 %command% -novid"
\t\t\t\t\t}
\t\t\t\t\t"300"
\t\t\t\t\t{
\t\t\t\t\t\t"cloud"\t\t"1"
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t}
\t}
\t"friends"
\t{
\t\t"dup"\t\t"a"
\t\t"dup"\t\t"b"
\t}
}
'''


class LaunchOptionTextTest(unittest.TestCase):
    def test_plain_game_arguments_go_after_the_command(self):
        on = steamcfg.with_trainer("-dx11 -windowed", EXE, "100")
        self.assertEqual(on, '"{}" --run 100 %command% -dx11 -windowed'.format(EXE))
        self.assertEqual(steamcfg.without_trainer(on), "-dx11 -windowed")

    def test_options_with_command_keep_their_shape(self):
        on = steamcfg.with_trainer("PROTON_LOG=1 %command% -novid", EXE, "200")
        self.assertEqual(on, '"{}" --run 200 PROTON_LOG=1 %command% -novid'.format(EXE))
        self.assertEqual(steamcfg.without_trainer(on), "PROTON_LOG=1 %command% -novid")

    def test_empty_round_trip_and_no_doubling(self):
        on = steamcfg.with_trainer("", EXE, "300")
        self.assertEqual(on, '"{}" --run 300 %command%'.format(EXE))
        self.assertEqual(steamcfg.with_trainer(on, r"D:\Moved\AutoCheatDeck.exe", "300"),
                         r'"D:\Moved\AutoCheatDeck.exe" --run 300 %command%')  # replaced, not added twice
        self.assertEqual(steamcfg.without_trainer(on), "")
        self.assertTrue(steamcfg.is_ours(on, "300"))
        self.assertFalse(steamcfg.is_ours(on, "30"))
        self.assertFalse(steamcfg.is_ours("-dx11", "300"))


class FileEditTest(unittest.TestCase):
    def test_only_the_one_value_changes(self):
        new = steamcfg.set_launch_options(CONFIG, "100", "X")
        self.assertEqual(steamcfg.get_launch_options(new, "100"), "X")
        self.assertEqual(new.replace('"X"', '"-dx11 -windowed"'), CONFIG)  # everything else byte for byte

    def test_adds_the_key_or_the_game(self):
        with_key = steamcfg.set_launch_options(CONFIG, "300", steamcfg.with_trainer("", EXE, "300"))
        self.assertTrue(steamcfg.is_ours(steamcfg.get_launch_options(with_key, "300"), "300"))
        self.assertIn('"cloud"', with_key)
        with_game = steamcfg.set_launch_options(CONFIG, "400", "Y")
        self.assertEqual(steamcfg.get_launch_options(with_game, "400"), "Y")
        self.assertEqual(steamcfg.get_launch_options(with_game, "100"), "-dx11 -windowed")
        self.assertEqual(with_game.count('"dup"'), 2)

    def test_quotes_and_backslashes_survive(self):
        value = steamcfg.with_trainer('-name "My Save"', EXE, "100")
        new = steamcfg.set_launch_options(CONFIG, "100", value)
        self.assertEqual(steamcfg.get_launch_options(new, "100"), value)

    def test_unexpected_file_is_left_alone(self):
        with self.assertRaises(OSError):
            steamcfg.set_launch_options('"Something" { }', "100", "X")


class SteamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.steam = self.tmp.name
        folder = os.path.join(self.steam, "userdata", "1234", "config")
        os.makedirs(folder)
        self.path = os.path.join(folder, "localconfig.vdf")
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(CONFIG)
        for patch in (mock.patch.object(steamcfg.steam, "find_steam_path", return_value=self.steam),
                      mock.patch.object(steamcfg, "active_account", return_value="1234")):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_turn_on_and_off_while_steam_is_closed(self):
        with mock.patch.object(steamcfg, "steam_running", return_value=False), \
                mock.patch.object(steamcfg, "quit_steam") as quit_steam:
            steamcfg.set_trainer_with_game("100", True, EXE)
            self.assertEqual(steamcfg.games_with_trainer(), {"100"})
            steamcfg.set_trainer_with_game("100", False, EXE)
        quit_steam.assert_not_called()
        with open(self.path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), CONFIG)  # back to exactly what it was
        self.assertTrue(os.path.isfile(self.path + ".autocheatdeck-backup"))
        self.assertEqual(steamcfg.games_with_trainer(), set())

    def test_steam_is_closed_and_started_again(self):
        with mock.patch.object(steamcfg, "steam_running", return_value=True), \
                mock.patch.object(steamcfg, "quit_steam") as quit_steam, \
                mock.patch.object(steamcfg, "start_steam") as start_steam:
            steamcfg.set_trainer_with_game("200", True, EXE)
        quit_steam.assert_called_once()
        start_steam.assert_called_once()
        self.assertEqual(steamcfg.games_with_trainer(), {"200"})

    def test_steam_restarts_even_if_the_edit_fails(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('"Something" { }')
        with mock.patch.object(steamcfg, "steam_running", return_value=True), \
                mock.patch.object(steamcfg, "quit_steam"), \
                mock.patch.object(steamcfg, "start_steam") as start_steam:
            with self.assertRaises(OSError):
                steamcfg.set_trainer_with_game("100", True, EXE)
        start_steam.assert_called_once()

    def test_account_from_login_users(self):
        with open(os.path.join(self.steam, "config.vdf"), "w"):
            pass
        os.makedirs(os.path.join(self.steam, "config"))
        with open(os.path.join(self.steam, "config", "loginusers.vdf"), "w", encoding="utf-8") as f:
            f.write('"users" { "76561197960266962" { "MostRecent" "1" } "76561197960265729" { "MostRecent" "0" } }')
        mock.patch.stopall()
        with mock.patch.object(steamcfg.steam, "find_steam_path", return_value=self.steam):
            self.assertEqual(steamcfg.active_account(self.steam), "1234")
