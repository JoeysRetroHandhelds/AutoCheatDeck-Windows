import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
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


ZIP_URL = "https://github.com/x/releases/download/v1.1.0/AutoCheatDeck-win64.zip"


def make_zip(version: str, extra: dict | None = None, exe: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("AutoCheatDeck/AutoCheatDeck.exe", exe if exe is not None else b"MZ " + version.encode())
        archive.writestr("AutoCheatDeck/_internal/lib.dll", version)
        archive.writestr("AutoCheatDeck/README.md", version)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def fake_open(payload: bytes, checksum: str | None = None):
    checksum = checksum or hashlib.sha256(payload).hexdigest()
    release = {"tag_name": "v1.1.0", "assets": [
        {"name": "AutoCheatDeck-win64.zip", "browser_download_url": ZIP_URL},
        {"name": "AutoCheatDeck-win64.zip.sha256", "browser_download_url": ZIP_URL + ".sha256"},
    ]}
    responses = {updater.API_URL: json.dumps(release).encode(), ZIP_URL: payload,
                 ZIP_URL + ".sha256": (checksum + " *AutoCheatDeck-win64.zip\n").encode()}
    return lambda url: FakeResponse(responses[url], url)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = os.path.join(self.tmp.name, "AutoCheatDeck")
        for name, data in {"AutoCheatDeck.exe": "MZ 1.0.0", "_internal/lib.dll": "1.0.0", "README.md": "1.0.0",
                           "my notes.txt": "mine", "data/config.json": "{}",
                           "data/trainers/1/t.exe": "trainer"}.items():
            path = os.path.join(self.app, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(data)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, *parts: str) -> str:
        with open(os.path.join(self.app, *parts)) as f:
            return f.read()

    def update_to(self, payload: bytes) -> None:
        with mock.patch.object(updater, "_open", fake_open(payload)):
            self.assertEqual(updater.stage_update(self.app), "v1.1.0")
        updater.finish(updater.staging_dir(self.app), self.app)
        updater.cleanup(self.app)

    def assert_version(self, version: str) -> None:
        self.assertEqual(self.read("AutoCheatDeck.exe"), "MZ " + version)
        self.assertEqual(self.read("_internal", "lib.dll"), version)

    def test_versions(self):
        self.assertTrue(updater.is_newer("v1.0.10", "1.0.9"))
        self.assertFalse(updater.is_newer("v1.0.0", "1.0.0"))

    def test_update_then_rollback_keeps_user_files(self):
        self.update_to(make_zip("1.1.0"))
        self.assert_version("1.1.0")
        self.assertTrue(updater.has_backup(self.app))
        self.assertEqual(sorted(os.listdir(updater.update_dir(self.app))), ["previous"])

        updater.stage_rollback(self.app)
        updater.finish(updater.staging_dir(self.app), self.app)
        updater.cleanup(self.app)
        self.assert_version("1.0.0")
        self.assertEqual(updater.has_backup(self.app), True)
        self.assertEqual(self.read("data", "update", "previous", "_internal", "lib.dll"), "1.1.0")

        self.assertEqual(self.read("my notes.txt"), "mine")
        self.assertEqual(self.read("data", "config.json"), "{}")
        self.assertEqual(self.read("data", "trainers", "1", "t.exe"), "trainer")

    def test_bad_downloads_change_nothing(self):
        bad = [
            (make_zip("1.1.0"), "0" * 64),
            (make_zip("1.1.0", exe=b"<html>"), None),
            (make_zip("1.1.0", extra={"../../escape.txt": "x"}), None),
        ]
        for payload, checksum in bad:
            with mock.patch.object(updater, "_open", fake_open(payload, checksum)):
                with self.assertRaises(ValueError):
                    updater.stage_update(self.app)
            self.assert_version("1.0.0")
            self.assertFalse(os.path.exists(updater.staging_dir(self.app)))

    def test_failed_copy_restores_old_files(self):
        with mock.patch.object(updater, "_open", fake_open(make_zip("1.1.0"))):
            updater.stage_update(self.app)
        with mock.patch.object(updater.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                updater.finish(updater.staging_dir(self.app), self.app)
        self.assert_version("1.0.0")
        self.assertEqual(self.read("README.md"), "1.0.0")


if __name__ == "__main__":
    unittest.main()
