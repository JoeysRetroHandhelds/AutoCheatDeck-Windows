"""Tkinter UI: Steam game list, trainer finder, auto-launch and self-update."""

import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import __version__, config, fling, log, processes, startup, steam, updater
from .watcher import Watcher
from .winapi import WINDOW_TITLE

FONT = ("Segoe UI", 12)
# Keyboard keys drive the same navigation as the controller. Handhelds such as the ROG Ally send
# these keys from their built-in controls when the controller is in "desktop" mode.
KEYS = {"Up": "up", "Down": "down", "Left": "left", "Right": "right", "Return": "a", "KP_Enter": "a",
        "Escape": "b", "Prior": "lb", "Next": "rb"}
ACCENT = "#2f6fdb"
ACCENT_SOFT = "#dbe7fb"
TITLE_FONT = ("Segoe UI Semibold", 15)


def launch_trainer(path: str) -> None:
    # ShellExecute (not CreateProcess) so trainers that require admin get their UAC prompt.
    os.startfile(path, "open", "", os.path.dirname(path))


class App:
    def __init__(self, root: tk.Tk, background: bool = False):
        self.root = root
        self.cfg = config.Config()
        log.get().info("AutoCheatDeck %s starting (frozen=%s)", __version__, getattr(sys, "frozen", False))
        self.games: list[steam.Game] = []
        self.by_id: dict[str, steam.Game] = {}
        self._events: queue.Queue[Callable[[], None]] = queue.Queue()
        # Updates only apply to the packaged app; None when running from source.
        self.app_dir = config.app_dir() if getattr(sys, "frozen", False) else None

        # Fixed title: a second launch finds this window by it and brings it to the front.
        root.title(WINDOW_TITLE)
        # Tk sizes in pixels but fonts in points; scale pixel sizes so high-DPI screens match.
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        root.geometry("{}x{}".format(self.px(1000), self.px(640)))
        root.minsize(self.px(760), self.px(480))
        root.option_add("*Font", FONT)
        style = ttk.Style(root)
        # "clam" lets the focused control be highlighted clearly, which controller navigation needs.
        style.theme_use("clam")
        style.configure(".", font=FONT)
        style.map("TButton", background=[("focus", ACCENT_SOFT), ("active", ACCENT_SOFT)],
                  bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)])
        style.map("TCheckbutton", background=[("focus", ACCENT_SOFT)])
        style.configure("TCheckbutton", indicatorsize=self.px(14))
        style.map("Treeview", background=[("selected", "focus", ACCENT), ("selected", "#9fb8e6")],
                  foreground=[("selected", "white")])
        style.configure("Treeview", rowheight=self.px(34), font=FONT)
        style.configure("Treeview.Heading", font=FONT)
        style.configure("TButton", padding=(12, 8))
        style.configure("Title.TLabel", font=TITLE_FONT)

        self._build()
        self.refresh_games()

        self.watcher = Watcher(
            targets=self._watch_targets,
            list_processes=lambda: list(processes.running_exe_paths().values()),
            launch=lambda appid, path: self.post(lambda: self._auto_launch(appid, path)),
            delay=lambda: float(self.cfg.data.get("launchDelay", 5)),
            on_change=lambda running: self.post(self._update_game_state),
        )
        self.watcher.start()

        from .gamepad import Gamepad

        self.gamepad = Gamepad(on_press=lambda name: self.post(lambda: self.on_pad(name)))
        for key in KEYS:
            root.bind_all("<{}>".format(key), self._on_key)
        self._refresh_controller_status()

        # A portable folder can move; keep the startup entry pointing at this copy.
        if self.app_dir and startup.is_enabled():
            startup.enable(startup.command_for(sys.executable))
        if background:
            root.iconify()

        if self.app_dir:
            updater.cleanup(self.app_dir)
        self._refresh_update_controls()
        if self.cfg.data.get("checkUpdates", True):
            self.check_app_update(quiet=True)

        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(100, self._drain_events)

    # ---------- plumbing ----------

    def px(self, value: float) -> int:
        return int(value * self.scale)

    def post(self, fn: Callable[[], None]) -> None:
        """Run fn on the Tk thread (safe to call from any thread)."""
        self._events.put(fn)

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                event()
            except Exception as e:  # one failing event must not stop the loop
                log.get().exception("UI event failed")
                self.set_status("Error: {}".format(e))
        self.root.after(100, self._drain_events)

    def run_bg(self, work: Callable[[], Any], done: Callable[[Any], None], status: str = "") -> None:
        if status:
            self.set_status(status)

        def runner() -> None:
            try:
                result = work()
            except Exception as e:  # report every background failure in the UI
                error = e
                self.post(lambda: self._bg_failed(error))
                return
            self.post(lambda: done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _bg_failed(self, error: Exception) -> None:
        self.set_status("")
        messagebox.showerror("AutoCheatDeck", str(error), parent=self.root)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def quit(self) -> None:
        self.watcher.stop()
        self.gamepad.stop()
        self.root.destroy()

    # ---------- layout ----------

    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=(12, 10))
        top.pack(fill="x")
        ttk.Label(top, text="AutoCheatDeck", style="Title.TLabel").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._fill_list())
        ttk.Label(top, text="Filter:").pack(side="left", padx=(24, 6))
        self.filter_entry = ttk.Entry(top, textvariable=self.search, width=28)
        self.filter_entry.pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_games).pack(side="left", padx=6)

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12)

        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, columns=("trainer",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Game")
        self.tree.heading("trainer", text="Trainer")
        self.tree.column("#0", width=self.px(340))
        self.tree.column("trainer", width=self.px(110), anchor="center", stretch=False)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._show_selected(force=False))
        body.add(left, weight=3)

        self.detail = ttk.Frame(body, padding=(16, 4))
        body.add(self.detail, weight=2)
        # ttk.PanedWindow ignores weights for the first layout; place the divider once it has a real size.
        body.bind("<Map>", lambda _: self.root.after(50, lambda: body.sashpos(0, int(body.winfo_width() * 0.6))))

        bottom = ttk.Frame(self.root, padding=(12, 8))
        bottom.pack(fill="x")
        ttk.Label(bottom, text="Launch delay (s):").pack(side="left")
        self.delay = tk.IntVar(value=int(self.cfg.data.get("launchDelay", 5)))
        self.delay.trace_add("write", lambda *_: self._save_delay())
        ttk.Spinbox(bottom, from_=0, to=120, width=5, textvariable=self.delay).pack(side="left", padx=(6, 18))
        self.startup_on = tk.BooleanVar(value=startup.is_enabled())
        ttk.Checkbutton(bottom, text="Start with Windows (minimized)", variable=self.startup_on,
                        command=self._toggle_startup).pack(side="left")

        updates = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        updates.pack(fill="x")
        self.update_frame = ttk.Frame(updates)
        self.update_frame.pack(side="right")

        self.status = ttk.Label(self.root, text="", padding=(12, 0, 12, 8), foreground="#555")
        self.status.pack(fill="x")
        self.controller_hint = ttk.Label(self.root, text="", foreground="#777", padding=(12, 0, 12, 8))
        self.controller_hint.pack(fill="x")

    def _clear_detail(self) -> None:
        for child in self.detail.winfo_children():
            child.destroy()

    # ---------- games ----------

    def refresh_games(self) -> None:
        self.run_bg(steam.installed_games, self._games_loaded, "Reading Steam library...")

    def _games_loaded(self, games: list[steam.Game]) -> None:
        first_load = not self.games
        self.games = games
        self.by_id = {g.appid: g for g in games}
        self.set_status("{} Steam games found, most recently played first".format(len(games))
                        if games else "No Steam games found")
        self._fill_list()
        if first_load:
            self.tree.focus_set()  # start on the list so a controller works straight away

    def _fill_list(self) -> None:
        selected = self.selected_appid()
        needle = self.search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for game in self.games:
            if needle and needle not in game.name.lower():
                continue
            entry = self.cfg.game(game.appid)
            mark = ""
            if entry and os.path.isfile(entry.get("path", "")):
                mark = "Auto" if entry.get("autoLaunch", True) else "Manual"
            self.tree.insert("", "end", iid=game.appid, text=game.name, values=(mark,))
        if not (selected and self.tree.exists(selected)):
            children = self.tree.get_children()
            selected = children[0] if children else None
        if selected:
            self.tree.selection_set(selected)
            self.tree.focus(selected)
            self.tree.see(selected)
        self._show_selected()

    def selected_appid(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _show_selected(self, force: bool = True) -> None:
        appid = self.selected_appid()
        # The list re-announces an unchanged selection on some keys (e.g. Right); rebuilding then
        # would destroy the button that just received focus.
        if not force and appid == getattr(self, "_shown_appid", None):
            return
        self._shown_appid = appid
        had_focus = self.root.focus_get() in self._focusables(self.detail)
        self._clear_detail()
        if had_focus:
            self.tree.focus_set()
        game = self.by_id.get(appid or "")
        if not game:
            ttk.Label(self.detail, text="Select a game", foreground="#777").pack(anchor="w", pady=20)
            return
        ttk.Label(self.detail, text=game.name, style="Title.TLabel", wraplength=self.px(360)).pack(
            anchor="w", pady=(8, 2))
        ttk.Label(self.detail, text=game.install_dir, foreground="#777", wraplength=self.px(360)).pack(anchor="w")

        entry = self.cfg.game(game.appid)
        has_trainer = bool(entry and os.path.isfile(entry.get("path", "")))
        if has_trainer:
            ttk.Label(self.detail, text=entry["title"], wraplength=self.px(360)).pack(anchor="w", pady=(16, 8))
            auto = tk.BooleanVar(value=entry.get("autoLaunch", True))
            ttk.Checkbutton(self.detail, text="Start trainer when the game starts", variable=auto,
                            command=lambda: self._set_auto(game.appid, auto.get())).pack(anchor="w", pady=4)
            self.game_state = ttk.Label(self.detail, text="", foreground="#777", wraplength=self.px(360))
            self.game_state.pack(anchor="w", pady=(0, 8))
            self._update_game_state()
            buttons = [
                ("Launch trainer now", lambda: self._launch_now(entry["path"])),
                ("Check for trainer update", lambda: self.check_trainer_update(game.appid)),
                ("Find a different trainer", self.find_trainer),
                ("Open trainer folder", lambda: os.startfile(os.path.dirname(entry["path"]))),
                ("Remove trainer", lambda: self.remove_trainer(game.appid)),
            ]
        else:
            ttk.Label(self.detail, text="No trainer yet", foreground="#777").pack(anchor="w", pady=(16, 8))
            buttons = [("Find trainer online", self.find_trainer)]
        for label, command in buttons:
            ttk.Button(self.detail, text=label, command=command).pack(fill="x", pady=4)

    def _update_game_state(self) -> None:
        label = getattr(self, "game_state", None)
        appid = self.selected_appid()
        entry = self.cfg.game(appid or "")
        if not label or not label.winfo_exists() or not entry:
            return
        if not entry.get("autoLaunch", True):
            label.configure(text="")
        elif appid in self.watcher.running:
            label.configure(text="Game detected: running")
        else:
            label.configure(text="Game not running. If it is running and this doesn't change, "
                                 "the game may hide its process (anti-cheat); use Launch trainer now.")

    def _set_auto(self, appid: str, value: bool) -> None:
        entry = self.cfg.game(appid)
        if entry:
            entry["autoLaunch"] = value
            self.cfg.set_game(appid, entry)
            self._fill_list()

    def _save_delay(self) -> None:
        try:
            self.cfg.data["launchDelay"] = max(0, int(self.delay.get()))
            self.cfg.save()
        except (tk.TclError, ValueError):
            pass

    # ---------- trainers ----------

    def _watch_targets(self) -> dict[str, tuple[str, str]]:
        targets = {}
        for appid, entry in list(self.cfg.data["games"].items()):
            game = self.by_id.get(appid)
            if game and entry.get("autoLaunch", True) and os.path.isfile(entry.get("path", "")):
                targets[appid] = (game.install_dir, entry["path"])
        return targets

    def _auto_launch(self, appid: str, path: str) -> None:
        game = self.by_id.get(appid)
        try:
            launch_trainer(path)
            self.set_status("Started trainer for {}".format(game.name if game else appid))
        except Exception as e:  # a missed game-start launch must be visible, not just a status line
            self.set_status("Could not start trainer: {}".format(e))
            messagebox.showerror("AutoCheatDeck", "Could not start the trainer:\n\n{}".format(e), parent=self.root)

    def _launch_now(self, path: str) -> None:
        try:
            launch_trainer(path)
        except Exception as e:  # report every launch failure in the UI
            messagebox.showerror("AutoCheatDeck", str(e), parent=self.root)

    def find_trainer(self) -> None:
        game = self.by_id.get(self.selected_appid() or "")
        if game:
            TrainerFinder(self, game)

    def install_trainer(self, game: steam.Game, page: dict, version: dict) -> None:
        target = os.path.join(config.trainers_dir(), game.appid)

        def done(result: dict) -> None:
            self.cfg.set_game(game.appid, {
                "title": version["title"],
                "pageUrl": page["url"],
                "path": result["path"],
                "sha256": result["sha256"],
                "autoLaunch": True,
            })
            self.set_status("Installed {}".format(version["title"]))
            self._fill_list()
            if not self.cfg.data.get("defenderNoticeShown"):
                self.cfg.data["defenderNoticeShown"] = True
                self.cfg.save()
                messagebox.showinfo(
                    "AutoCheatDeck",
                    "Windows Defender often flags game trainers because they modify game memory.\n\n"
                    "If the trainer disappears or won't start, check Windows Security > Protection history. "
                    "Only allow trainers you trust.",
                    parent=self.root,
                )

        self.run_bg(lambda: fling.download(version["url"], page["url"], target), done,
                    "Downloading {}...".format(version["title"]))

    def check_trainer_update(self, appid: str) -> None:
        entry = self.cfg.game(appid)
        game = self.by_id.get(appid)
        if not entry or not game:
            return

        def done(detail: dict) -> None:
            self.set_status("")
            latest = detail["versions"][0] if detail["versions"] else None
            if not latest or latest["title"] == entry["title"]:
                messagebox.showinfo("AutoCheatDeck", "Trainer is up to date.", parent=self.root)
            elif messagebox.askyesno("AutoCheatDeck", "Newer trainer available:\n\n{}\n\nDownload it?".format(
                    latest["title"]), parent=self.root):
                self.install_trainer(game, {"url": entry["pageUrl"]}, latest)

        self.run_bg(lambda: fling.list_versions(entry["pageUrl"]), done, "Checking trainer...")

    def remove_trainer(self, appid: str) -> None:
        if not messagebox.askyesno("AutoCheatDeck", "Remove this game's trainer?", parent=self.root):
            return
        folder = os.path.join(config.trainers_dir(), appid)
        if os.path.isdir(folder):
            # Renaming fails while the trainer is running, so nothing is half-deleted.
            doomed = folder + ".removing"
            try:
                os.rename(folder, doomed)
            except OSError:
                messagebox.showerror("AutoCheatDeck", "Close the trainer before removing it.", parent=self.root)
                return
            shutil.rmtree(doomed, ignore_errors=True)
        self.cfg.set_game(appid, None)
        self._fill_list()

    def _toggle_startup(self) -> None:
        try:
            if self.startup_on.get():
                if not self.app_dir:
                    raise OSError("Start with Windows only works from the packaged app.")
                startup.enable(startup.command_for(sys.executable))
            else:
                startup.disable()
        except OSError as e:
            self.startup_on.set(startup.is_enabled())
            messagebox.showerror("AutoCheatDeck", str(e), parent=self.root)

    # ---------- controller ----------

    def _refresh_controller_status(self) -> None:
        if not self.gamepad.available:
            state = "Controller support unavailable"
        elif self.gamepad.connected:
            state = "Controller connected"
        else:
            state = "No controller detected (on a ROG Ally, switch Control mode to Gamepad)"
        self.controller_hint.configure(
            text="{}.  A / Enter select   B / Esc back   Y find trainer   X launch trainer   "
                 "LB/RB page".format(state))
        self.root.after(2000, self._refresh_controller_status)

    def _on_key(self, event) -> str | None:
        name = KEYS.get(event.keysym)
        widget = event.widget
        if not name or not isinstance(widget, tk.Misc):
            return None
        if isinstance(widget, (ttk.Treeview, tk.Listbox)) and name in ("up", "down", "lb", "rb"):
            return None  # the list's own arrow keys already move the selection
        if isinstance(widget, (ttk.Entry, ttk.Spinbox)) and name in ("left", "right"):
            return None  # moving the text cursor
        self.on_pad(name)
        return "break"

    def _focusables(self, container: tk.Misc) -> list:
        found = []
        for child in container.winfo_children():
            if isinstance(child, (ttk.Button, ttk.Checkbutton)) and child.instate(["!disabled"]):
                found.append(child)
            found.extend(self._focusables(child))
        return found

    def on_pad(self, name: str) -> None:
        widget = self.root.focus_get()
        top = widget.winfo_toplevel() if widget else self.root
        if isinstance(top, TrainerFinder):
            top.on_pad(name, widget)
            return
        if widget is self.filter_entry and name == "a":
            self.tree.focus_set()
            return
        detail = self._focusables(self.detail)
        if name in ("lb", "rb") or (widget is self.tree and name in ("up", "down")):
            self.tree.focus_set()
            key = {"up": "<Up>", "down": "<Down>", "lb": "<Prior>", "rb": "<Next>"}[name]
            self.tree.event_generate(key)
            focused = self.tree.focus()
            if focused:
                self.tree.selection_set(focused)
                self.tree.see(focused)
        elif name == "y":
            self.find_trainer()
        elif name == "x":
            entry = self.cfg.game(self.selected_appid() or "")
            if entry and os.path.isfile(entry.get("path", "")):
                self._launch_now(entry["path"])
        elif widget is self.tree or widget is None:
            if name in ("right", "a") and detail:
                detail[0].focus_set()
            elif widget is None:
                self.tree.focus_set()
        elif widget in detail:
            index = detail.index(widget)
            if name == "a":
                widget.invoke()
            elif name in ("left", "b"):
                self.tree.focus_set()
            elif name == "down" and index + 1 < len(detail):
                detail[index + 1].focus_set()
            elif name == "up":
                (detail[index - 1] if index > 0 else self.tree).focus_set()
            elif name == "right":
                bottom = self._focusables(self.update_frame)
                if bottom:
                    bottom[-1].focus_set()
        else:
            # Filter box, settings row and update buttons: plain focus traversal.
            if name in ("down", "right"):
                widget.tk_focusNext().focus_set()
            elif name in ("up", "left"):
                widget.tk_focusPrev().focus_set()
            elif name == "a" and isinstance(widget, (ttk.Button, ttk.Checkbutton)):
                widget.invoke()
            elif name == "b":
                self.tree.focus_set()

    # ---------- app updates ----------

    def _refresh_update_controls(self, info: dict | None = None) -> None:
        for child in self.update_frame.winfo_children():
            child.destroy()
        if self.app_dir and updater.has_backup(self.app_dir):
            ttk.Button(self.update_frame, text="Restore previous version", command=self.rollback).pack(
                side="right", padx=(6, 0))
        if info and info["available"]:
            ttk.Button(self.update_frame, text="Install update {}".format(info["latest"]),
                       command=lambda: self.install_update(info)).pack(side="right")
        else:
            ttk.Button(self.update_frame, text="Check for updates",
                       command=lambda: self.check_app_update(quiet=False)).pack(side="right")
        ttk.Label(self.update_frame, text="v{}".format(__version__), foreground="#777").pack(side="right", padx=8)

    def check_app_update(self, quiet: bool) -> None:
        def done(info: dict) -> None:
            self.set_status("")
            self._refresh_update_controls(info)
            if not quiet and not info["available"]:
                messagebox.showinfo("AutoCheatDeck", "AutoCheatDeck is up to date.", parent=self.root)

        if quiet:
            def work() -> dict:
                try:
                    return updater.check(__version__)
                except Exception:  # offline at startup is fine
                    return {"available": False}

            self.run_bg(work, done)
        else:
            self.run_bg(lambda: updater.check(__version__), done, "Checking for updates...")

    def install_update(self, info: dict) -> None:
        if not self.app_dir:
            messagebox.showinfo("AutoCheatDeck", "Running from source; updates only apply to the packaged app.",
                                parent=self.root)
            return
        notes = (info.get("notes") or "").strip()
        if not messagebox.askyesno("AutoCheatDeck", "Install {}?\n\n{}\n\nAutoCheatDeck will restart.".format(
                info["latest"], notes[:600]), parent=self.root):
            return

        def done(_: str) -> None:
            updater.start_finisher(self.app_dir, os.getpid())
            self.quit()

        self.run_bg(lambda: updater.stage_update(self.app_dir), done, "Downloading update...")

    def rollback(self) -> None:
        if not messagebox.askyesno("AutoCheatDeck", "Go back to the previous version? AutoCheatDeck will restart.",
                                   parent=self.root):
            return
        try:
            updater.stage_rollback(self.app_dir)
        except (OSError, ValueError) as e:
            messagebox.showerror("AutoCheatDeck", str(e), parent=self.root)
            return
        updater.start_finisher(self.app_dir, os.getpid())
        self.quit()


class TrainerFinder(tk.Toplevel):
    """Search FLiNG, pick a trainer page, pick a version, confirm."""

    def __init__(self, app: App, game: steam.Game):
        super().__init__(app.root)
        self.app = app
        self.game = game
        self.page: dict | None = None
        self.items: list[dict] = []
        self.title("Find trainer - {}".format(game.name))
        self.geometry("{}x{}".format(app.px(720), app.px(520)))
        self.transient(app.root)

        bar = ttk.Frame(self, padding=12)
        bar.pack(fill="x")
        self.query = tk.StringVar(value=game.name)
        self.entry = ttk.Entry(bar, textvariable=self.query)
        self.entry.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Search", command=self.search).pack(side="left", padx=(8, 0))

        self.heading = ttk.Label(self, text="", padding=(12, 0))
        self.heading.pack(fill="x")
        self.listbox = tk.Listbox(self, activestyle="dotbox", font=FONT)
        self.listbox.pack(fill="both", expand=True, padx=12, pady=8)
        self.listbox.bind("<Double-Button-1>", lambda _: self.choose())

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        self.back = ttk.Button(buttons, text="Back", command=self.search)
        ttk.Button(buttons, text="Select", command=self.choose).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side="right", padx=8)

        self.search()

    def close(self) -> None:
        self.destroy()
        self.app.tree.focus_set()

    def go_back(self) -> None:
        if self.page is not None:
            self.search()
        else:
            self.close()

    def on_pad(self, name: str, widget) -> None:
        if name == "b":
            self.go_back()
        elif name == "a":
            if isinstance(widget, ttk.Button):
                widget.invoke()
            elif widget is self.entry:
                self.search()
            else:
                self.choose()
        elif name in ("up", "down", "lb", "rb"):
            self.listbox.focus_set()
            key = {"up": "<Up>", "down": "<Down>", "lb": "<Prior>", "rb": "<Next>"}[name]
            self.listbox.event_generate(key)
        elif name in ("left", "right") and widget is not None:
            (widget.tk_focusNext() if name == "right" else widget.tk_focusPrev()).focus_set()

    def _show(self, heading: str, items: list[dict], empty: str) -> None:
        self.items = items
        self.heading.configure(text=heading)
        self.listbox.delete(0, "end")
        for item in items:
            self.listbox.insert("end", item["title"])
        if not items:
            self.listbox.insert("end", empty)
        else:
            self.listbox.selection_set(0)
        self.listbox.focus_set()

    def search(self) -> None:
        self.page = None
        self.back.pack_forget()
        query = self.query.get().strip()
        if not query:
            return
        self.heading.configure(text="Searching FLiNG...")
        self.app.run_bg(lambda: fling.search(query),
                        lambda results: self._show("Pick the trainer for your game:", results, "No trainers found"))

    def choose(self) -> None:
        selection = self.listbox.curselection()
        if not selection or not self.items:
            return
        item = self.items[selection[0]]
        if self.page is None:
            self.page = item
            self.back.pack(side="left")
            self.heading.configure(text="Loading versions...")

            def shown(detail: dict) -> None:
                updated = " (updated {})".format(detail["lastUpdated"]) if detail["lastUpdated"] else ""
                self._show("Pick a version" + updated + ":", detail["versions"], "No downloads on this page")

            self.app.run_bg(lambda: fling.list_versions(item["url"]), shown)
            return
        if messagebox.askyesno(
            "Download trainer?",
            "{}\n\nThis downloads a third-party Windows program from flingtrainer.com and starts it "
            "whenever {} runs. Only use trainers you trust.".format(item["title"], self.game.name),
            parent=self,
        ):
            self.app.install_trainer(self.game, self.page, item)
            self.destroy()
            self.app.tree.focus_set()
