import json
import os
import tempfile
import unittest
import urllib.parse
import zipfile

from autocheatdeck import diagnostics

HOME = r"C:\Users\Joey"


class DiagnosticsTest(unittest.TestCase):
    def test_redact_blanks_the_user_name(self):
        text = r"started C:\Users\Joey\Games\AutoCheatDeck\AutoCheatDeck.exe as PC\Joey; c:/users/joey/x; Joeyland"
        out = diagnostics.redact(text, user="Joey", home=HOME)
        self.assertNotIn("Joey\\", out)
        self.assertIn(r"%USERPROFILE%\Games", out)
        self.assertIn("%USERPROFILE%/x", out)
        self.assertIn(r"PC\<user>", out)
        self.assertIn("Joeyland", out)  # only whole words

    def test_zip_holds_redacted_logs_and_settings(self):
        with tempfile.TemporaryDirectory() as data:
            with open(os.path.join(data, "autocheatdeck.log"), "w", encoding="utf-8") as f:
                f.write("INFO started trainer C:\\Users\\Joey\\x.exe\n")
            with open(os.path.join(data, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"games": {"1": {"path": "C:\\Users\\Joey\\t.exe"}}}, f)
            dest = os.path.join(data, "out.zip")
            diagnostics.build_zip(dest, data, {"app": "2.1.0", "user": "Joey"}, user="Joey", home=HOME)
            with zipfile.ZipFile(dest) as archive:
                names = set(archive.namelist())
                self.assertTrue({"system.json", "autocheatdeck.log", "config.json"} <= names)
                for name in names:
                    self.assertNotIn("Joey", archive.read(name).decode("utf-8"), name)

    def test_issue_url_prefills_the_version(self):
        info = {"app": "2.1.0", "release": "11", "windows": "10.0.26100", "machine": "AMD64"}
        url = diagnostics.issue_url(info)
        self.assertTrue(url.startswith(diagnostics.ISSUES_URL + "?"))
        body = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["body"][0]
        self.assertIn("AutoCheatDeck 2.1.0, Windows 11 (10.0.26100), AMD64", body)
