import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autocheatdeck import fling, steam, updater

TEST_KEY = Ed25519PrivateKey.generate()
TEST_PUBLIC_KEY = TEST_KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

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

            def add(lib, appid, name, folder, create=True, played=0):
                with open(os.path.join(lib, "steamapps", "appmanifest_%s.acf" % appid), "w") as f:
                    f.write('"AppState" { "appid" "%s" "name" "%s" "installdir" "%s" "LastPlayed" "%d" '
                            '"buildid" "1%s" }' % (appid, name, folder, played, appid))
                if create:
                    os.makedirs(os.path.join(lib, "steamapps", "common", folder))

            add(steam_dir, "1145360", "Hades", "Hades", played=1700000000)
            add(extra, "1245620", "ELDEN RING", "ELDEN RING", played=1790000000)
            add(extra, "620", "Portal 2", "Portal 2")
            add(steam_dir, "400", "Balatro", "Balatro")
            add(extra, "228980", "Steamworks Common Redistributables", "Steamworks Shared")
            add(extra, "999", "Uninstalled", "Gone", create=False)

            games = steam.installed_games(steam_dir)
        # Most recently played first, then never-played games alphabetically.
        self.assertEqual([g.name for g in games], ["ELDEN RING", "Hades", "Balatro", "Portal 2"])
        self.assertEqual(games[1].build, "11145360")  # Steam's build id, for "game updated"


@unittest.skipUnless(sys.platform == "win32", "needs Windows")
class LaunchTest(unittest.TestCase):
    def test_launch_trainer_passes_valid_startfile_arguments(self):
        from autocheatdeck import app

        with mock.patch.object(app.os, "startfile") as startfile:
            app.launch_trainer(r"C:\data\trainers\1\Some Trainer.exe")
        path, operation, arguments, cwd = startfile.call_args.args
        self.assertEqual((operation, arguments, cwd), ("open", "", r"C:\data\trainers\1"))

    def test_launch_trainer_reaches_windows(self):
        from autocheatdeck import app

        with self.assertRaises(FileNotFoundError):
            app.launch_trainer(r"C:\definitely-missing\trainer.exe")


class FlingTest(unittest.TestCase):
    def test_parse_pages(self):
        self.assertTrue(fling.parse_search(read_fixture("fling_search.html")))
        page = fling.parse_trainer_page(read_fixture("fling_trainer.html"))
        self.assertEqual(page["versions"][0]["title"], "Elden.Ring.v1.02-v1.16.1.Plus.35.Trainer-FLiNG")

    def test_search_queries_clean_up_store_names(self):
        cases = {
            "Hades": ["Hades"],
            "DOOM Eternal\u2122": ["DOOM Eternal\u2122", "DOOM Eternal"],
            "The Witcher\u00ae 3: Wild Hunt - Game of the Year Edition":
                ["The Witcher\u00ae 3: Wild Hunt - Game of the Year Edition", "The Witcher 3: Wild Hunt",
                 "The Witcher 3"],
            "Assassin's Creed\u00ae Odyssey (Deluxe Edition)":
                ["Assassin's Creed\u00ae Odyssey (Deluxe Edition)", "Assassin's Creed Odyssey"],
            "Marvel\u2019s Spider-Man Remastered":
                ["Marvel\u2019s Spider-Man Remastered", "Marvel's Spider-Man Remastered"],
            "Special Forces": ["Special Forces"],
        }
        for name, expected in cases.items():
            self.assertEqual(fling.search_queries(name), expected, name)

    def test_search_retries_with_cleaner_names_until_found(self):
        hit = read_fixture("fling_search.html")
        asked = []

        def fetch(url):
            asked.append(url)
            return hit if "Cyberpunk+2077&" in url + "&" else "<html></html>"

        with mock.patch.object(fling, "_fetch_text", side_effect=fetch):
            self.assertTrue(fling.search("Cyberpunk 2077: Ultimate Edition"))
        self.assertEqual(len(asked), 2)


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


def fake_open(payload: bytes, checksum: str | None = None, signed_tag: str = "v1.1.0", key=None):
    checksum = checksum or hashlib.sha256(payload).hexdigest()
    signature = (key or TEST_KEY).sign(updater.signed_message(signed_tag, hashlib.sha256(payload).hexdigest()))
    release = {"tag_name": "v1.1.0", "assets": [
        {"name": "AutoCheatDeck-win64.zip", "browser_download_url": ZIP_URL},
        {"name": "AutoCheatDeck-win64.zip.sha256", "browser_download_url": ZIP_URL + ".sha256"},
        {"name": "AutoCheatDeck-win64.zip.sig", "browser_download_url": ZIP_URL + ".sig"},
    ]}
    responses = {updater.API_URL: json.dumps(release).encode(), ZIP_URL: payload,
                 ZIP_URL + ".sha256": (checksum + " *AutoCheatDeck-win64.zip\n").encode(),
                 ZIP_URL + ".sig": base64.b64encode(signature)}
    return lambda url: FakeResponse(responses[url], url)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        key = mock.patch.object(updater, "PUBLIC_KEY", TEST_PUBLIC_KEY)
        key.start()
        self.addCleanup(key.stop)
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
            self.assertEqual(updater.stage_update(self.app, "1.0.0"), "v1.1.0")
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
                    updater.stage_update(self.app, "1.0.0")
            self.assert_version("1.0.0")
            self.assertFalse(os.path.exists(updater.staging_dir(self.app)))

    def test_unsigned_or_mis_signed_updates_are_refused(self):
        payload = make_zip("1.1.0")
        for opener in (fake_open(payload, key=Ed25519PrivateKey.generate()),  # someone else's key
                       fake_open(payload, signed_tag="v1.0.5")):  # an older release re-tagged as newer
            with mock.patch.object(updater, "_open", opener):
                with self.assertRaisesRegex(ValueError, "signature"):
                    updater.stage_update(self.app, "1.0.0")
            self.assert_version("1.0.0")
            self.assertFalse(os.path.exists(updater.staging_dir(self.app)))

    def test_release_that_is_not_newer_is_refused(self):
        with mock.patch.object(updater, "_open", fake_open(make_zip("1.1.0"))):
            with self.assertRaisesRegex(ValueError, "not newer"):
                updater.stage_update(self.app, "1.1.0")
        self.assertFalse(os.path.exists(updater.staging_dir(self.app)))

    def test_failed_copy_restores_old_files(self):
        with mock.patch.object(updater, "_open", fake_open(make_zip("1.1.0"))):
            updater.stage_update(self.app, "1.0.0")
        with mock.patch.object(updater.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                updater.finish(updater.staging_dir(self.app), self.app)
        self.assert_version("1.0.0")
        self.assertEqual(self.read("README.md"), "1.0.0")


if __name__ == "__main__":
    unittest.main()
