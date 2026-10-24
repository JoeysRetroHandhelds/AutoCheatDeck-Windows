import sys
import tkinter as tk
from tkinter import messagebox

from autocheatdeck import winapi
from autocheatdeck.app import App


def main() -> None:
    winapi.enable_dpi_awareness()

    # After a self-update the new exe waits for the old one to exit before taking the mutex.
    if "--after-update" in sys.argv:
        index = sys.argv.index("--after-update")
        try:
            winapi.wait_for_pid(int(sys.argv[index + 1]))
        except (IndexError, ValueError):
            pass
        del sys.argv[index:index + 2]

    if not winapi.acquire_single_instance("Local\\AutoCheatDeck-Windows"):
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("AutoCheatDeck", "AutoCheatDeck is already running. Press Ctrl+Alt+T to show it.")
        return

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
