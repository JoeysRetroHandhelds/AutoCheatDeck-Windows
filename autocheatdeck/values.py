"""Changing a cheat's value from the overlay, without the trainer's window.

FLiNG rows hold their value in a text box that UI Automation can set directly (the trainer reads it
when the cheat applies; there is no Apply button). The trainer's own < > arrows are plain text, not
buttons, so the overlay steps values itself:

- adjustable rows (multipliers, speeds: "CheatOptionWithInputAdjustment") and option lists
  ("CheatOptionSetComboBox", e.g. save slots) step with Left / Right;
- amount rows ("CheatOptionSetValue", "CheatOptionWithInput": money, item counts) open a digit
  editor with Y.

A row of these kinds can still keep its box hidden (no value): then there is nothing to change.
"""

import re

STEPPED = ("CheatOptionWithInputAdjustment", "CheatOptionSetComboBox")
TYPED = ("CheatOptionSetValue", "CheatOptionWithInput")
MAX_DIGITS = 10
_NUMBER = re.compile(r"^-?\d{1,10}(\.\d{1,4})?$")


def kind_of(row_class: str) -> str:
    """"step", "type" or "" for a trainer row class."""
    if row_class.startswith(STEPPED):
        return "step"
    if row_class.startswith(TYPED):
        return "type"
    return ""


def check(value: str) -> str:
    """A value safe to hand to the trainer: a plain number. Raises ValueError otherwise."""
    value = str(value).strip()
    if not _NUMBER.match(value):
        raise ValueError("Not a number: {!r}".format(value))
    return value


def step(value: str, direction: int, minimum: float = 0) -> str:
    """One step left (-1) or right (+1). Decimals (multipliers) move by 0.5, whole numbers by 1."""
    try:
        current = float(value)
    except ValueError:
        current = 0.0
    if "." in value:
        result = max(minimum, round(current + 0.5 * direction, 4))
        return "{:.1f}".format(result)
    return str(int(max(minimum, int(current) + direction)))


class DigitEditor:
    """Edit a whole number one digit at a time with a d-pad: Up / Down change the selected digit,
    Left / Right move between digits (Left past the first digit adds one)."""

    def __init__(self, value: str):
        digits = re.sub(r"\D", "", str(value).split(".")[0]) or "0"
        self.digits = list(digits[-MAX_DIGITS:])
        self.pos = len(self.digits) - 1

    @property
    def value(self) -> str:
        return str(int("".join(self.digits)))

    def up(self) -> None:
        self.digits[self.pos] = str((int(self.digits[self.pos]) + 1) % 10)

    def down(self) -> None:
        self.digits[self.pos] = str((int(self.digits[self.pos]) - 1) % 10)

    def left(self) -> None:
        if self.pos > 0:
            self.pos -= 1
        elif len(self.digits) < MAX_DIGITS:
            self.digits.insert(0, "0")

    def right(self) -> None:
        if self.pos < len(self.digits) - 1:
            self.pos += 1

    def display(self) -> str:
        """The number with the selected digit in brackets, e.g. 99[9]99."""
        return "".join("[{}]".format(d) if i == self.pos else d for i, d in enumerate(self.digits))
