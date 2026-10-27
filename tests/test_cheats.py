import json
import os
import sys
import tempfile
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("cheats uses the Windows API")

from autocheatdeck import cheats  # noqa: E402

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


def helper_states(tree: list[dict]) -> dict[str, list[dict]]:
    """What the helper's Get-Rows / Read-States would report for a dumped trainer tree (all off)."""
    states: dict[str, list[dict]] = {}
    for i, element in enumerate(tree):
        if element["id"] != "m_toggle_button":
            continue
        start = next(j for j in range(i - 1, -1, -1) if tree[j]["depth"] < element["depth"])
        row = tree[start]
        below = []
        for child in tree[start + 1:]:
            if child["depth"] <= row["depth"]:
                break
            below.append(child)
        # Like the helper: the label must be a direct child of the row, else it isn't a cheat row.
        label = next((c for c in below if c["id"] == "m_label_option_name" and c["depth"] == row["depth"] + 1), None)
        if label is None:
            continue
        name = label["name"]
        box = next((c for c in below if c["id"] == "m_box"), None) or next(
            (c for c in below if c["id"] == "m_textbox" and c["type"] == "Edit"), None)
        value = box["value"] if box and "ValuePattern" in box["patterns"] else None
        states.setdefault(name, []).append({"on": False, "kind": row["cls"], "value": value})
    return states


class WithStatesTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "fling_trainer_tree.json"),
                  encoding="utf-8-sig") as f:
            self.states = helper_states(json.load(f)["tree"])
        self.cheats = cheats.parse_trainer_ui(fixture_lines())["cheats"]

    def test_every_cheat_of_a_real_trainer_gets_its_row(self):
        result = {c["name"]: c for c in cheats.with_states(self.cheats, self.states)}
        self.assertEqual(len(result), 50)
        self.assertTrue(all(c["on"] is False for c in result.values()))
        self.assertEqual(result["Edit Money"]["value"], "9999999")
        self.assertEqual(result["Stagger Multiplier"]["kind"], "CheatOptionWithInputAdjustment")
        self.assertEqual(result["Stagger Multiplier"]["value"], "2.0")
        self.assertIsNone(result["Undo Teleport"]["value"])  # its box is hidden
        self.assertIsNone(result["Infinite HP"]["value"])

    def test_not_running_means_no_state(self):
        result = cheats.with_states(self.cheats, {})
        self.assertTrue(all(c["on"] is None and c["value"] is None for c in result))

    def test_repeated_names_map_in_order(self):
        cheat_list = [{"name": "Max HP", "section": "A"}, {"name": "Max HP", "section": "B"}, {"name": "Gone"}]
        states = {"Max HP": [{"on": True, "kind": "k", "value": "1"}, {"on": False, "kind": "k", "value": "2"}]}
        result = cheats.with_states(cheat_list, states)
        self.assertEqual([(c["nth"], c["on"], c["value"]) for c in result],
                         [(0, True, "1"), (1, False, "2"), (0, None, None)])
