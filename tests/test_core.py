import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from autocheatdeck import fling, steam, updater
from autocheatdeck.watcher import Watcher, running_games

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class SteamTest(unittest.TestCase):
    def test_parse_vdf(self):
        text = '"libraryfolders"\n{\n "0"\n {\n  "path"  "D:\\\\SteamLibrary"\n  "apps" { "620" "1" }\n }\n}'
        data = steam.parse_vdf(text)
        self.assertEqual(data["libraryfolders"]["0"]["path"], "D:\\SteamLibrary")
        self.assertEqual(data["libraryfolders"]["0"]["apps"], {"620": "1"})

    def test_installed_games_across_libraries(self):
        with tempfile.TemporaryDirectory() as root:
            steam_dir, extra = os.path.join(root, "Steam"), os.path.join(root, "Lib2")
            for lib in (steam_dir, extra):
                os.makedirs(os.path.join(lib, "steamapps", "common"))
            with open(os.path.join(steam_dir, "steamapps", "libraryfolders.vdf"), "w") as f:
                f.write('"libraryfolders" { "0" { "path" "%s" } "1" { "path" "%s" } }'
                        % (steam_dir.replace("\\", "\\\\"), extra.replace("\\", "\\\\")))

            def add(lib, appid, name, folder, create=True):
                with open(os.path.join(lib, "steamapps", "appmanifest_%s.acf" % appid), "w") as f:
                    f.write('"AppState" { "appid" "%s" "name" "%s" "installdir" "%s" }' % (appid, name, folder))
                if create:
                    os.makedirs(os.path.join(lib, "steamapps", "common", folder))

            add(steam_dir, "1145360", "Hades", "Hades")
            add(extra, "1245620", "ELDEN RING", "ELDEN RING")
            add(extra, "228980", "Steamworks Common Redistributables", "Steamworks Shared")
            add(extra, "999", "Uninstalled", "Gone", create=False)

            games = steam.installed_games(steam_dir)
        self.assertEqual([g.name for g in games], ["ELDEN RING", "Hades"])


class WatcherTest(unittest.TestCase):
    def test_running_games_matches_install_folder_and_ignores_trainer(self):
        dirs = {"1": r"C:\Games\Hades", "2": r"C:\Games\Hades II"}
        exes = [r"c:\games\hades\x64\Hades.exe", r"C:\Games\Hades II\trainer.exe"]
        self.assertEqual(running_games(dirs, exes, ignore={r"C:\Games\Hades II\trainer.exe"}), {"1"})

    def test_launches_once_after_delay_and_rearms_when_game_exits(self):
        procs: list[str] = []
        launched: list[str] = []
        w = Watcher(
            targets=lambda: {"1": (r"C:\Games\Hades", r"C:\trainers\1\t.exe")},
            list_processes=lambda: procs,
            launch=lambda appid, path: launched.append(appid),
            delay=lambda: 5,
        )
        w.tick(0)
        procs.append(r"C:\Games\Hades\Hades.exe")
        w.tick(10)
        w.tick(14)
        self.assertEqual(launched, [])
        w.tick(15)
        w.tick(30)
        self.assertEqual(launched, ["1"])
        procs.clear()
        w.tick(40)
        procs.append(r"C:\Games\Hades\Hades.exe")
        w.tick(50)
        w.tick(55)
        self.assertEqual(launched, ["1", "1"])

    def test_game_closing_before_delay_cancels_launch(self):
        procs = [r"C:\Games\Hades\Hades.exe"]
        launched: list[str] = []
        w = Watcher(lambda: {"1": (r"C:\Games\Hades", r"C:\t.exe")}, lambda: procs,
                    lambda a, p: launched.append(a), lambda: 5)
        w.tick(0)
        procs.clear()
        w.tick(3)
        w.tick(10)
        self.assertEqual(launched, [])


class FlingTest(unittest.TestCase):
    def test_parse_pages(self):
        self.assertTrue(fling.parse_search(read_fixture("fling_search.html")))
        page = fling.parse_trainer_page(read_fixture("fling_trainer.html"))
        self.assertEqual(page["versions"][0]["title"], "Elden.Ring.v1.02-v1.16.1.Plus.35.Trainer-FLiNG")


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self._url = url

    def geturl(self):
        return self._url


EXE_URL = "https://github.com/x/releases/download/v1.1.0/AutoCheatDeck.exe"


def fake_open(payload: bytes, checksum: str):
    release = {"tag_name": "v1.1.0", "assets": [
        {"name": "AutoCheatDeck.exe", "browser_download_url": EXE_URL},
        {"name": "AutoCheatDeck.exe.sha256", "browser_download_url": EXE_URL + ".sha256"},
    ]}
    responses = {updater.API_URL: json.dumps(release).encode(), EXE_URL: payload,
                 EXE_URL + ".sha256": (checksum + " *AutoCheatDeck.exe\n").encode()}
    return lambda url: FakeResponse(responses[url], url)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exe = os.path.join(self.tmp.name, "AutoCheatDeck.exe")
        with open(self.exe, "wb") as f:
            f.write(b"MZ old")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, path=None) -> bytes:
        with open(path or self.exe, "rb") as f:
            return f.read()

    def test_versions(self):
        self.assertTrue(updater.is_newer("v1.0.10", "1.0.9"))
        self.assertFalse(updater.is_newer("v1.0.0", "1.0.0"))

    def test_install_then_rollback_swaps_back_and_forth(self):
        payload = b"MZ new"
        with mock.patch.object(updater, "_open", fake_open(payload, hashlib.sha256(payload).hexdigest())):
            self.assertEqual(updater.install(self.exe), "v1.1.0")
        self.assertEqual(self.read(), b"MZ new")
        self.assertEqual(self.read(updater.backup_path(self.exe)), b"MZ old")
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["AutoCheatDeck.exe", "AutoCheatDeck.old.exe"])

        updater.rollback(self.exe)
        self.assertEqual(self.read(), b"MZ old")
        self.assertEqual(self.read(updater.backup_path(self.exe)), b"MZ new")

    def test_bad_checksum_or_non_exe_leaves_exe_alone(self):
        for payload, checksum in ((b"MZ new", "0" * 64), (b"<html>", hashlib.sha256(b"<html>").hexdigest())):
            with mock.patch.object(updater, "_open", fake_open(payload, checksum)):
                with self.assertRaises(ValueError):
                    updater.install(self.exe)
            self.assertEqual(self.read(), b"MZ old")
            self.assertEqual(os.listdir(self.tmp.name), ["AutoCheatDeck.exe"])


if __name__ == "__main__":
    unittest.main()
