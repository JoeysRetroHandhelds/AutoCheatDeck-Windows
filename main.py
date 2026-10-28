import os
import sys
import tkinter as tk
from tkinter import messagebox

from autocheatdeck import launcher, updater, winapi
from autocheatdeck.app import App, launch_trainer


MUTEX = "Local\\AutoCheatDeck-Windows"


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

    # Steam launch option: AutoCheatDeck.exe --run <appid> <the game's command...>. Starts the game,
    # then its trainer, and waits for the game. No window and no single-instance check: it runs
    # alongside the app.
    if len(sys.argv) >= 3 and sys.argv[1] == launcher.RUN_FLAG:
        sys.exit(launcher.run(sys.argv[2], sys.argv[3:], launch_trainer))

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
    if not winapi.acquire_single_instance(MUTEX):
        # Opening the app again is how you get back to it: show the running copy, and stay until
        # it's hidden again (Steam treats a launch that exits at once as a closed game).
        # A startup launch finding it already running stays quiet.
        if not background:
            winapi.show_running_copy_and_wait(MUTEX)
        return

    root = tk.Tk()
    if background:
        root.withdraw()  # started with Windows: straight to the tray, no window flashing up
    App(root, background=background)
    root.mainloop()


if __name__ == "__main__":
    main()
