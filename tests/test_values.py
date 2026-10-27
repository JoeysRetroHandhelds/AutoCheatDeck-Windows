import json
import os
import unittest

from autocheatdeck import values

TREE = os.path.join(os.path.dirname(__file__), "fixtures", "fling_trainer_tree.json")


class ValuesTest(unittest.TestCase):
    def test_every_value_row_in_a_real_trainer_is_handled(self):
        with open(TREE, encoding="utf-8-sig") as f:
            tree = json.load(f)["tree"]
        kinds = {}
        for i, element in enumerate(tree):
            skip = ("CheatOptionToolTip", "CheatOptionLabel")
            if element["cls"].startswith("CheatOption") and element["cls"] not in skip:
                below = []
                for child in tree[i + 1:]:
                    if child["depth"] <= element["depth"]:
                        break
                    below.append(child)
                has_box = any(c["id"] in ("m_textbox", "m_box") and "ValuePattern" in c["patterns"] for c in below)
                kinds.setdefault(element["cls"], set()).add(has_box)
        for cls, boxes in kinds.items():
            # Every kind of row that has a value box is one the overlay can change. (Some rows of
            # those kinds keep the box hidden, e.g. "Undo Teleport": the helper reports no value.)
            if True in boxes:
                self.assertNotEqual(values.kind_of(cls), "", cls)
            else:
                self.assertEqual(values.kind_of(cls), "", cls)
        self.assertEqual(kinds["CheatOptionSetValue"], {True, False})

    def test_step(self):
        self.assertEqual(values.step("2.0", 1), "2.5")
        self.assertEqual(values.step("0.5", -1), "0.0")
        self.assertEqual(values.step("0.0", -1), "0.0")
        self.assertEqual(values.step("1", 1), "2")
        self.assertEqual(values.step("1", -1), "0")
        self.assertEqual(values.step("junk", 1), "1")

    def test_check_allows_only_plain_numbers(self):
        self.assertEqual(values.check(" 9999999 "), "9999999")
        self.assertEqual(values.check("2.5"), "2.5")
        for bad in ("", "1e9", "1;2", "abc", "12345678901"):
            with self.assertRaises(ValueError):
                values.check(bad)

    def test_digit_editor(self):
        editor = values.DigitEditor("9999999")
        self.assertEqual(editor.display(), "999999[9]")
        editor.up()  # 9 -> 0, no carry
        self.assertEqual(editor.value, "9999990")
        for _ in range(7):
            editor.left()  # the 7th adds a leading digit
        editor.up()
        self.assertEqual(editor.value, "19999990")
        editor.right()
        editor.down()
        self.assertEqual(editor.value, "18999990")
        self.assertEqual(values.DigitEditor("").value, "0")
        self.assertEqual(values.DigitEditor("50.7").value, "50")
