import os
import tempfile
import unittest

from autocheatdeck import cheats

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "fling_trainer_ui.txt")


def fixture_lines() -> list[str]:
    with open(FIXTURE, encoding="utf-8-sig") as f:
        return f.read().splitlines()


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.parsed = cheats.parse_trainer_ui(fixture_lines())
        self.by_key = {c["key"]: c for c in self.parsed["cheats"]}

    def test_reads_every_cheat_in_order(self):
        names = [c["name"] for c in self.parsed["cheats"]]
        self.assertEqual(names[:3], ["Infinite HP", "Infinite MP", "Max Limit Gauge"])
        self.assertEqual(names[-1], "AP")
        self.assertEqual(len(names), 50)  # the trainer is "Plus 50"

    def test_hotkeys_and_sections(self):
        self.assertEqual(self.by_key["Ctrl+Num 1"]["name"], "Edit Money")
        self.assertEqual(self.by_key["Alt+Num /"]["name"], "Save Location")
        self.assertEqual(self.by_key["Num 1"]["section"], "")
        self.assertEqual(self.by_key["Ctrl+F1"]["section"], "Selected Character Bonus Stats")

    def test_adjustable_cheats_are_marked(self):
        self.assertTrue(self.by_key["Num -"]["adjustable"])  # Damage Multiplier < >
        self.assertTrue(self.by_key["Alt+Num 5"]["adjustable"])  # Set Game Speed < >
        self.assertFalse(self.by_key["Num 1"]["adjustable"])

    def test_stops_before_notes_and_settings(self):
        names = {c["name"] for c in self.parsed["cheats"]}
        self.assertNotIn("Save Input Values", names)
        self.assertNotIn("Edit Hotkeys", names)

    def test_game_process_names_when_the_trainer_shows_them(self):
        self.assertEqual(self.parsed["processes"], [])  # this capture didn't have them
        lines = fixture_lines()
        lines.insert(12, "Text\tFFRS-Win64-Shipping.exe/FFRS-WinGDK-Shipping.exe")
        self.assertEqual(cheats.parse_trainer_ui(lines)["processes"],
                         ["FFRS-Win64-Shipping.exe", "FFRS-WinGDK-Shipping.exe"])

    def test_not_a_trainer(self):
        self.assertEqual(cheats.parse_trainer_ui(["Text\tHello"]), {"cheats": [], "processes": []})


class HotkeyTest(unittest.TestCase):
    def test_virtual_keys(self):
        cases = {
            "Num 1": [0x61],
            "Num +": [0x6B],
            "Ctrl+Num +": [0x11, 0x6B],
            "Alt+Num /": [0x12, 0x6F],
            "Ctrl+Num .": [0x11, 0x6E],
            "Shift+F12": [0x10, 0x7B],
            "Ctrl+F1": [0x11, 0x70],
            "Ctrl+Shift+Home": [0x11, 0x10, 0x24],
            "F5": [0x74],
            "Ctrl+K": [0x11, ord("K")],
        }
        for hotkey, expected in cases.items():
            with self.subTest(hotkey=hotkey):
                self.assertEqual(cheats.hotkey_vks(hotkey), expected)

    def test_every_fixture_hotkey_is_understood(self):
        for cheat in cheats.parse_trainer_ui(fixture_lines())["cheats"]:
            self.assertTrue(cheats.hotkey_vks(cheat["key"]))

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            cheats.hotkey_vks("Ctrl+Banana")


class CacheTest(unittest.TestCase):
    def test_cache_is_tied_to_the_trainer_build(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "cheats.json")
            parsed = {"cheats": [{"key": "Num 1", "name": "Infinite HP", "section": "", "adjustable": False}],
                      "processes": []}
            cheats.save_cache(path, "abc", parsed)
            self.assertEqual(cheats.load_cache(path, "abc")["cheats"], parsed["cheats"])
            self.assertIsNone(cheats.load_cache(path, "other-build"))
            self.assertIsNone(cheats.load_cache(os.path.join(folder, "missing.json"), "abc"))


if __name__ == "__main__":
    unittest.main()
