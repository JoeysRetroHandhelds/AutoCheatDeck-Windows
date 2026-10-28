"""The cheat overlay: AutoCheatDeck's own always-on-top cheat list over the game.

It replaces both the trainer's window (kept hidden) and toggling from Game Bar (which closes as soon
as the trainer grabs focus). The overlay takes focus while it's open, so the keyboard and mouse go
only to it and the controller drives it; B closes it and focus goes back to the game.

FLiNG trainers make themselves the active window now and then (on launch, and on some toggles), even
hidden. While the overlay is open a focus guard (Windows' foreground-change notification, no
polling) takes focus straight back whenever the trainer grabs it.

Games in exclusive fullscreen would minimize if the overlay took focus, so for those it opens
without taking focus. The controller can't drive it then (the game is in front and would get every
press too), so it says so: touch works (buttons for values, a number pad for amounts, Close), or the
game can be switched to borderless.
"""

import ctypes
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

from . import log, values, winapi

BG = "#14161c"
ROW_BG = "#1d2029"
FG = "#e8eaf0"
DIM = "#8a90a0"
ON = "#5fd38a"
ACCENT = "#2f6fdb"

QUNS_RUNNING_D3D_FULL_SCREEN = 3
# The controller can't drive the overlay over an exclusive fullscreen game: it would need focus
# (which minimizes the game), and the game reads every press too. Touch still works.
EXCLUSIVE_NOTE = ("Exclusive fullscreen: use touch here, or set the game's Display mode to "
                  "Borderless to use the controller.")
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowLongW.restype = ctypes.c_long
_shell32 = ctypes.WinDLL("shell32")


def exclusive_fullscreen() -> bool:
    """True when a Direct3D game is in exclusive fullscreen (taking focus would minimize it)."""
    state = ctypes.c_int(0)
    try:
        if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except (AttributeError, OSError):
        return False
    return state.value == QUNS_RUNNING_D3D_FULL_SCREEN


def window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


class Overlay:
    def __init__(self, app):
        self.app = app
        self.appid: str | None = None
        self.trainer_title = ""
        self.cheats: list[dict] = []
        self.visible = False
        self.editor: values.DigitEditor | None = None  # open while typing an amount (Y)
        self._editing = -1  # index of the cheat being edited
        self.focused_mode = True
        self._previous = None  # window to give focus back to on close (the game)
        self._guard = None

        top = self.top = tk.Toplevel(app.root, bg=BG)
        top.withdraw()
        top.overrideredirect(True)  # no title bar: a panel, not a window
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.95)

        title_font = ("Segoe UI Semibold", 16)
        self.title = tk.Label(top, text="", bg=BG, fg=FG, font=title_font, anchor="w", padx=16, pady=10)
        self.title.pack(fill="x")
        self.status = tk.Label(top, text="", bg=BG, fg=DIM, font=("Segoe UI", 11), anchor="w", padx=16)
        self.status.pack(fill="x")

        frame = tk.Frame(top, bg=BG, padx=12, pady=8)
        frame.pack(fill="both", expand=True)
        self.list = tk.Listbox(frame, bg=ROW_BG, fg=FG, font=("Segoe UI", 14), activestyle="none",
                               selectbackground=ACCENT, selectforeground="white", highlightthickness=0,
                               borderwidth=0, exportselection=False)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.list.bind("<Double-Button-1>", lambda _: self.toggle_selected())

        # Touch / mouse controls for values (over exclusive fullscreen games touch is the only input).
        bar = tk.Frame(top, bg=BG, padx=12)
        bar.pack(fill="x")
        for text, command in (("\u2212", lambda: self.step_selected(-1)), ("+", lambda: self.step_selected(1)),
                              ("Amount", self.type_selected), ("Close", self.close)):
            self._touch_button(bar, text, command).pack(side="left", expand=True, fill="x", padx=3)
        # Number pad for typing an amount by touch (shown while typing).
        self.keypad = tk.Frame(top, bg=BG, padx=12, pady=4)
        keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "\u232b", "0", "Set"]
        for i, key in enumerate(keys):
            special = {"\u232b": self._typed_backspace, "Set": self._typed_set}
            command = special.get(key, lambda k=key: self._typed_add(k))
            self._touch_button(self.keypad, key, command).grid(row=i // 3, column=i % 3, sticky="nsew", padx=3, pady=3)
        for column in range(3):
            self.keypad.columnconfigure(column, weight=1)
        self._typed: str | None = None  # the amount being typed on the number pad

        self.footer = tk.Label(top, text="", bg=BG, fg=DIM, font=("Segoe UI", 11), pady=8)
        self.footer.pack(fill="x")

    @staticmethod
    def _touch_button(parent, text: str, command) -> tk.Button:
        # Plain tk buttons: they work in the no-focus (exclusive fullscreen) window, where ttk's
        # focus-driven styling has nothing to show.
        return tk.Button(parent, text=text, command=command, bg=ROW_BG, fg=FG, activebackground=ACCENT,
                         activeforeground="white", relief="flat", font=("Segoe UI", 14), takefocus=False,
                         pady=6)

    # ---------- open / close ----------

    def _place(self) -> None:
        """Right-hand panel sized for the screen as it is now (it can change: docking, resolution)."""
        top, scale = self.top, self.app.scale
        width = int(460 * scale)
        height = int(top.winfo_screenheight() * 0.82)
        x = top.winfo_screenwidth() - width - int(24 * scale)
        y = int((top.winfo_screenheight() - height) / 2)
        top.geometry("{}x{}+{}+{}".format(width, height, x, y))

    def open(self, appid: str) -> None:
        if self.visible and appid == self.appid:
            # Already open (the widget's button pressed again): just bring it forward.
            self.top.lift()
            if self.focused_mode:
                self.take_focus()
            return
        game = self.app.by_id.get(appid)
        entry = self.app.cfg.game(appid)
        if not entry:
            return
        from . import helper

        self.appid = appid
        self.trainer_title = helper.trainer_title(entry["path"])
        self.title.configure(text=game.name if game else appid)
        self.status.configure(text="Reading cheats...")
        if not self.visible:
            self._previous = _user32.GetForegroundWindow()
        self.focused_mode = not exclusive_fullscreen()
        self._place()
        self.footer.configure(text="A toggle    < > change value    Y type amount    B close" if self.focused_mode
                              else "Double-tap a cheat to toggle it")
        self.top.deiconify()
        self.top.lift()
        self.visible = True
        self._apply_focus_mode()
        self.refresh()
        log.get().info("overlay: open for %s (%s)", appid, "focused" if self.focused_mode else "no-focus mode")

    def _hwnd(self) -> int:
        return int(self.top.wm_frame(), 16)

    def _apply_focus_mode(self) -> None:
        hwnd = self._hwnd()
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if self.focused_mode:
            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_NOACTIVATE)
            self.take_focus()
            if self._guard is None:
                self._guard = winapi.ForegroundWatcher(lambda h: self.app.post(lambda: self._on_foreground(h)))
        else:
            # Exclusive fullscreen: don't take focus (the game would minimize). Touch only.
            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)

    def take_focus(self) -> None:
        hwnd = self._hwnd()
        winapi.force_foreground(hwnd)
        self.top.focus_force()
        self.list.focus_set()
        if self.app.helper.running:
            # The helper (admin) can also take focus from an admin window, e.g. the trainer.
            self.app.run_bg(lambda: self.app.helper.focus(hwnd), lambda _: self.list.focus_set(),
                            on_error=self._error)

    def _on_foreground(self, hwnd) -> None:
        """Focus guard: the trainer grabbed focus (it does on launch and some toggles): take it back.
        The trainer runs as admin, so only the (admin) helper is allowed to take focus from it."""
        log.get().debug("overlay: foreground -> %r", window_title(hwnd) if hwnd else None)
        if self.visible and self.focused_mode and hwnd and window_title(hwnd) == self.trainer_title:
            log.get().info("overlay: trainer took focus, taking it back")
            target = self._hwnd()
            self.app.run_bg(lambda: self.app.helper.focus(target), lambda _: self.list.focus_set(),
                            on_error=self._error)

    def close(self) -> None:
        if not self.visible:
            return
        self._stop_editing()
        self.visible = False
        if self._guard is not None:
            self._guard.stop()
            self._guard = None
        self.top.withdraw()
        if self._previous:
            winapi.force_foreground(self._previous)  # back to the game
        log.get().info("overlay: closed")

    # ---------- cheats ----------

    def refresh(self) -> None:
        appid = self.appid

        def done(cheat_list) -> None:
            if appid != self.appid:
                return
            if cheat_list is None:
                self.status.configure(text="No cheats read yet. Launch the trainer first.")
                return
            self.show(cheat_list)

        self.app.run_bg(lambda: self.app.api_cheats(appid), done, on_error=self._error)

    def show(self, cheat_list: list[dict]) -> None:
        selected = self.list.curselection()
        self.cheats = list(cheat_list)
        self.list.delete(0, "end")
        running = False
        for cheat in self.cheats:
            self.list.insert("end", self._label(cheat))
            self._color(self.list.size() - 1, cheat)
            running |= cheat.get("on") is not None
        if self.cheats:
            index = selected[0] if selected and selected[0] < len(self.cheats) else 0
            self.list.selection_set(index)
            self.list.activate(index)
            self.list.see(index)
        if not running:
            self.status.configure(text="The trainer isn't running. Launch it from the widget.")
        else:
            self.status.configure(text="" if self.focused_mode else EXCLUSIVE_NOTE)

    @staticmethod
    def _label(cheat: dict) -> str:
        state = {True: "ON ", False: "OFF", None: " - "}[cheat.get("on")]
        value = cheat.get("value")
        kind = values.kind_of(cheat.get("kind", ""))
        shown = ""
        if value not in (None, ""):
            shown = "   \u2039 {} \u203a".format(value) if kind == "step" else "   = {}".format(value)
        section = "   ({})".format(cheat["section"]) if cheat.get("section") else ""
        return "  {}    {}{}{}".format(state, cheat["name"], shown, section)

    def _error(self, error: Exception) -> None:
        log.get().warning("overlay: %s", error)
        self.status.configure(text="Error: {}".format(error))

    def _can_type(self, cheat: dict) -> bool:
        if cheat.get("on") is None:
            self.status.configure(text="The trainer isn't running.")
            return False
        # Some rows of an amount kind keep their box hidden (no value): nothing to type there.
        if values.kind_of(cheat.get("kind", "")) != "type" or cheat.get("value") in (None, ""):
            self.status.configure(text="This cheat has no amount to type.")
            return False
        return True

    def _selected(self) -> tuple[int, dict] | None:
        selection = self.list.curselection()
        if not selection or not self.cheats or selection[0] >= len(self.cheats):
            return None
        return selection[0], self.cheats[selection[0]]

    def _update_row(self, index: int, cheat: dict) -> None:
        self.cheats[index] = cheat
        self.list.delete(index)
        self.list.insert(index, self._label(cheat))
        self._color(index, cheat)
        self.list.selection_set(index)
        self.list.activate(index)

    # ---------- values ----------

    def set_value(self, index: int, cheat: dict, value: str) -> None:
        appid = self.appid

        def done(result: dict) -> None:
            if appid != self.appid or index >= len(self.cheats):
                return
            self._update_row(index, dict(self.cheats[index], value=result["value"]))
            self.status.configure(text="{} set to {}".format(cheat["name"], result["value"]))

        def failed(error: Exception) -> None:
            self._error(error)
            self.refresh()  # show what the trainer really holds (a step may already be on screen)

        self.app.run_bg(lambda: self.app.api_set_value(appid, cheat["name"], value, cheat.get("nth", 0)), done,
                        on_error=failed)

    def step_selected(self, direction: int) -> None:
        picked = self._selected()
        if not picked:
            return
        index, cheat = picked
        if values.kind_of(cheat.get("kind", "")) != "step" or cheat.get("value") in (None, ""):
            return
        new = values.step(str(cheat["value"]), direction)
        self._update_row(index, dict(cheat, value=new))  # show it now; the trainer's reply follows
        self.set_value(index, cheat, new)

    def edit_selected(self) -> None:
        picked = self._selected()
        if not picked:
            return
        index, cheat = picked
        if not self._can_type(cheat):
            return
        self.editor = values.DigitEditor(str(cheat.get("value") or "0"))
        self._editing = index
        self._show_editor()

    def _show_editor(self) -> None:
        cheat = self.cheats[self._editing]
        self.status.configure(text="{}:  {}\nUp / Down digit   Left / Right move   A set   B cancel".format(
            cheat["name"], self.editor.display()))

    def _stop_editing(self) -> None:
        self.editor = None
        self._editing = -1
        self._typed = None
        self.keypad.pack_forget()

    # Typing an amount by touch

    def type_selected(self) -> None:
        picked = self._selected()
        if not picked:
            return
        index, cheat = picked
        if not self._can_type(cheat):
            return
        self._stop_editing()
        self._editing = index
        self._typed = ""
        self.keypad.pack(fill="x", before=self.footer)
        self._show_typed()

    def _show_typed(self) -> None:
        self.status.configure(text="{}:  {}_".format(self.cheats[self._editing]["name"], self._typed))

    def _typed_add(self, digit: str) -> None:
        if self._typed is not None and len(self._typed) < values.MAX_DIGITS:
            self._typed = (self._typed + digit).lstrip("0") or "0"
            self._show_typed()

    def _typed_backspace(self) -> None:
        if self._typed:
            self._typed = self._typed[:-1]
            self._show_typed()

    def _typed_set(self) -> None:
        if self._typed is None or self._editing >= len(self.cheats):
            return
        index, value = self._editing, self._typed or "0"
        self._stop_editing()
        self.status.configure(text="Setting...")
        self.set_value(index, self.cheats[index], value)

    def _editor_pad(self, name: str) -> None:
        if name == "b":
            self._stop_editing()
            self.status.configure(text="")
        elif name == "a":
            index, value = self._editing, self.editor.value
            self._stop_editing()
            if index < len(self.cheats):
                self.status.configure(text="Setting...")
                self.set_value(index, self.cheats[index], value)
        elif name in ("up", "down", "left", "right"):
            getattr(self.editor, name)()
            self._show_editor()

    def _color(self, index: int, cheat: dict) -> None:
        self.list.itemconfigure(index, foreground=ON if cheat.get("on") else FG)

    def toggle_selected(self) -> None:
        selection = self.list.curselection()
        if not selection or not self.cheats:
            return
        index = selection[0]
        cheat = self.cheats[index]
        if cheat.get("on") is None:
            self.status.configure(text="The trainer isn't running.")
            return
        appid = self.appid

        def done(result: dict) -> None:
            if appid != self.appid or index >= len(self.cheats):
                return
            self._update_row(index, dict(self.cheats[index], on=result["on"]))
            self.status.configure(text="{} is {}".format(cheat["name"], "on" if result["on"] else "off"))

        self.app.run_bg(lambda: self.app.api_toggle(appid, cheat["name"], cheat.get("nth", 0)), done,
                        on_error=self._error)

    # ---------- controller / keyboard ----------

    def on_pad(self, name: str) -> None:
        if self.editor is not None:
            self._editor_pad(name)
        elif self._typed is not None and name in ("a", "b"):
            # Number pad open (keyboard / controller while typing by touch): A sets, B cancels.
            if name == "a":
                self._typed_set()
            else:
                self._stop_editing()
                self.status.configure(text="")
        elif name == "a":
            self.toggle_selected()
        elif name in ("left", "right"):
            self.step_selected(-1 if name == "left" else 1)
        elif name == "y":
            self.edit_selected()
        elif name == "b":
            self.close()
        elif name in ("up", "down", "lb", "rb"):
            self.list.focus_set()
            key = {"up": "<Up>", "down": "<Down>", "lb": "<Prior>", "rb": "<Next>"}[name]
            self.list.event_generate(key)
            active = self.list.index("active")
            self.list.selection_clear(0, "end")
            self.list.selection_set(active)
            self.list.see(active)
