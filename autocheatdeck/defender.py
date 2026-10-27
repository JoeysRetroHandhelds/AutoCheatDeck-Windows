"""Windows Security and trainers: explain when it removed or blocked one.

Defender (and Smart App Control) often flag game trainers, because they change other programs'
memory. It then quarantines the exe, sometimes right after the download, or refuses to start it.
AutoCheatDeck never changes Defender's settings; it says what happened and opens the page where
the user can restore or allow the trainer themselves.
"""

import os
import re

PROTECTION_HISTORY = "windowsdefender://protectionhistory"

# Windows errors meaning "security software stopped this program".
BLOCKED_ERRORS = {
    225,   # ERROR_VIRUS_INFECTED: "the file contains a virus or potentially unwanted software"
    226,   # ERROR_VIRUS_DELETED
    1260,  # ERROR_ACCESS_DISABLED_BY_POLICY
    4551,  # ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION: Smart App Control / App Control
}
_HELPER_ERROR = re.compile(r"\(error (\d+)\)")

MISSING = ("The trainer file is missing. Windows Security probably removed it: trainers change game "
           "memory, so they're often flagged. Restore it in Protection history if you trust it, or "
           "download it again.")
BLOCKED = ("Windows Security blocked this trainer. Trainers change game memory, so they're often "
           "flagged. If you trust it, allow it in Windows Security > Protection history, then try again.")


def trainer_missing(entry: dict | None) -> bool:
    """A trainer was installed for the game, but its exe is gone."""
    return bool(entry and entry.get("path")) and not os.path.isfile(entry["path"])


def blocked(error: BaseException) -> bool:
    """Was this launch error Windows Security refusing to start the trainer?"""
    code = getattr(error, "winerror", None)
    if code is None:  # the admin helper reports "Couldn't start the trainer (error 225)"
        match = _HELPER_ERROR.search(str(error))
        code = int(match.group(1)) if match else None
    return code in BLOCKED_ERRORS


def open_protection_history() -> None:
    os.startfile(PROTECTION_HISTORY)
