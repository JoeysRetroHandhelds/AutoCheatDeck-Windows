import os
import sys
import tkinter as tk
from tkinter import messagebox

from autocheatdeck import updater, winapi
from autocheatdeck.app import App


def _take_arg(flag: str, count: int) -> list[str] | None:
    if flag not in sys.argv:
        return None
    index = sys.argv.index(flag)
    values = sys.argv[index + 1:index + 1 + count]
    del sys.argv[index:index + 1 + count]
    return values if len(values) == count else None


def finish_update(wait_pid: str, app_dir: str) -> None:
    """Runs from data/update/staging: swap this copy into app_dir, then start the app from there."""
    winapi.wait_for_pid(int(wait_pid))
    try:
        updater.finish(os.path.dirname(os.path.abspath(sys.executable)), app_dir)
    except Exception as e:  # finish() already put the old files back
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("AutoCheatDeck", "Update failed, keeping the current version.\n\n{}".format(e))
    updater.launch(os.path.join(app_dir, updater.EXE_NAME), "--after-update", str(os.getpid()))


def main() -> None:
    winapi.enable_dpi_awareness()

    finishing = _take_arg("--finish-update", 2)
    if finishing:
        finish_update(*finishing)
        return

    # After an update the app waits for the updater copy to exit before taking the mutex.
    after = _take_arg("--after-update", 1)
    if after:
        try:
            winapi.wait_for_pid(int(after[0]))
        except ValueError:
            pass

    background = _take_arg("--background", 0) is not None
    if not winapi.acquire_single_instance("Local\\AutoCheatDeck-Windows"):
        # Opening the app again is how you get back to it: show the running copy.
        # A startup launch finding it already running stays quiet.
        if not background:
            winapi.ask_running_copy_to_show()
        return

    root = tk.Tk()
    if background:
        root.withdraw()  # started with Windows: straight to the tray, no window flashing up
    App(root, background=background)
    root.mainloop()


if __name__ == "__main__":
    main()
