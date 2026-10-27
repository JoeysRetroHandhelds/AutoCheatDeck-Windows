"""Tkinter UI: game list, trainer finder, manual trainer launching and self-update.

Nothing runs on a timer: background threads hand work to the window through a queue and wake it
with a Tk event, and the controller is only read while one of the app's windows is focused.
"""

import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import __version__, api, cheats, values, config, fling, helper, library, log, protocol, startup, steam, updater
from .winapi import WINDOW_TITLE, listen_for_show

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


class Dialogs:
    """messagebox, but the controller keeps working while the (native) dialog has focus."""

    def __init__(self, gamepad):
        self._gamepad = gamepad

    def __getattr__(self, name: str):
        show = getattr(messagebox, name)

        def wrapped(*args, **kwargs):
            self._gamepad.hold()
            try:
                return show(*args, **kwargs)
            finally:
                self._gamepad.release()

        return wrapped


class App(api.Backend):
    def __init__(self, root: tk.Tk, background: bool = False, idle_quit: int = 0):
        self.root = root
        self.hidden = False
        # Started by the widget: quit this many seconds after its last request, unless the user
        # opens the window (then it behaves like a normal start). 0 = never.
        self.idle_quit = idle_quit
        self._idle_job = None
        self.cfg = config.Config()
        log.get().info("AutoCheatDeck %s starting (frozen=%s)", __version__, getattr(sys, "frozen", False))
        self.games: list[steam.Game] = []
        # Set once the game library has been read; the widget's first request waits for it.
        self._games_ready = threading.Event()
        self.by_id: dict[str, steam.Game] = {}
        self._events: queue.Queue[Callable[[], None]] = queue.Queue()
        # Updates only apply to the packaged app; None when running from source.
        self.app_dir = config.app_dir() if getattr(sys, "frozen", False) else None
        self._updating = False
        self._downloading: set[str] = set()  # games with a trainer download in progress

        # Fixed title: a second launch finds this window by it and brings it to the front.
        root.title(WINDOW_TITLE)
        # Tk sizes in pixels but fonts in points; scale pixel sizes so high-DPI screens match.
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        root.geometry("{}x{}".format(self.px(1000), self.px(640)))
        root.minsize(self.px(760), self.px(480))
        # Handheld screens (e.g. 1920x1080 at 150% = 1280x720 usable) are barely bigger than the
        # window: fill them instead of leaving a sliver of desktop around it.
        self._fill_screen = (root.winfo_screenwidth() / self.scale <= 1440
                             or root.winfo_screenheight() / self.scale <= 900)
        if self._fill_screen and not background:  # zooming shows the window: not while starting hidden
            root.state("zoomed")
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

        from .gamepad import Gamepad

        self.gamepad = Gamepad(on_press=lambda name: self.post(lambda: self.on_pad(name)),
                               on_change=lambda: self.post(self._refresh_controller_status))
        self.msg = Dialogs(self.gamepad)
        from .overlay import Overlay

        self.overlay = Overlay(self)
        for key in KEYS:
            root.bind_all("<{}>".format(key), self._on_key)
        # Read the controller only while one of our windows is focused. (Tk's <Activate> never fires
        # here; FocusIn/FocusOut do. Focus moving between our own widgets is a FocusOut followed by
        # a FocusIn, so whether the app lost focus is decided once things settle.)
        root.bind_all("<FocusIn>", lambda _: self._settle_focus())
        root.bind_all("<FocusOut>", lambda _: root.after_idle(self._check_focus))
        self._refresh_controller_status()

        # A portable folder can move; keep the startup entry pointing at this copy.
        if self.app_dir:
            try:
                protocol.register(sys.executable)  # lets the widget start us
            except OSError:
                log.get().exception("protocol: could not register the autocheatdeck: link")
            try:
                startup.refresh(sys.executable)
            except OSError:
                log.get().exception("startup: could not refresh the sign-in launcher")
        from .tray import Tray

        self.tray = Tray(on_show=lambda: self.post(self.show_window), on_quit=lambda: self.post(self.quit))
        listen_for_show(lambda: self.post(self.show_window))
        if background:
            self._set_hidden(True)  # started with Windows: live in the tray until asked for

        if self.app_dir:
            updater.cleanup(self.app_dir)
        self._refresh_update_controls()
        if self.cfg.data.get("checkUpdates", True):
            self.check_app_update(quiet=True)

        # Local API for the Game Bar widget (sleeps in accept() until a request arrives).
        # Admin helper for launching trainers and switching cheats (started on first use).
        self.helper = helper.Helper()
        self.api = api.Server(self, on_error=lambda text: self.post(lambda: self.set_status(text)))

        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        root.bind("<<Wake>>", lambda _: self._drain_events())
        root.after(0, self._drain_events)  # anything posted before the main loop started

    # ---------- plumbing ----------

    def px(self, value: float) -> int:
        return int(value * self.scale)

    def post(self, fn: Callable[[], None]) -> None:
        """Run fn on the Tk thread (safe to call from any thread): queue it and wake the window."""
        self._events.put(fn)
        try:
            self.root.event_generate("<<Wake>>", when="tail")
        except (RuntimeError, tk.TclError):
            pass  # main loop not running yet (drained once it starts) or the window is gone

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                event()
            except Exception as e:  # one failing event must not stop the others
                log.get().exception("UI event failed")
                self.set_status("Error: {}".format(e))

    def run_bg(self, work: Callable[[], Any], done: Callable[[Any], None], status: str = "",
               failed: Callable[[], None] | None = None,
               on_error: Callable[[Exception], None] | None = None) -> None:
        """Run work() on a thread, then done(result) on the Tk thread. On an exception: failed(),
        then on_error(e) if given, else an error dialog."""
        if status:
            self.set_status(status)

        def runner() -> None:
            try:
                result = work()
            except Exception as e:  # report every background failure in the UI
                error = e
                if failed:
                    self.post(failed)
                self.post(lambda: (on_error or self._bg_failed)(error))
                return
            self.post(lambda: done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _bg_failed(self, error: Exception) -> None:
        self.set_status("")
        self.msg.showerror("AutoCheatDeck", str(error), parent=self.root)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _set_hidden(self, hidden: bool) -> None:
        self.hidden = hidden
        if hidden:
            self.root.withdraw()
            self.gamepad.set_active(False)  # withdrawing sends no FocusOut

    def hide_to_tray(self) -> None:
        self._set_hidden(True)
        if not self.cfg.data.get("trayNoticeShown"):
            self.cfg.data["trayNoticeShown"] = True
            self.cfg.save()
            self.tray.notify("Still running in the tray. Right-click the tray icon to quit.")

    def show_window(self) -> None:
        self._stop_idle_quit()  # the user is here: stay like a normal start
        self._set_hidden(False)
        if self._fill_screen and self.root.state() != "zoomed":
            self.root.state("zoomed")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.tree.focus_set()

    def quit(self) -> None:
        self.api.stop()
        self.helper.stop()
        self.gamepad.stop()
        self.tray.stop()
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
        ttk.Button(top, text="Add game", command=self.add_game).pack(side="left")

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12)

        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, columns=("store", "trainer"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Game")
        self.tree.heading("store", text="Store")
        self.tree.heading("trainer", text="Trainer")
        self.tree.column("#0", width=self.px(300))
        self.tree.column("store", width=self.px(80), anchor="center", stretch=False)
        self.tree.column("trainer", width=self.px(130), anchor="center", stretch=False)
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
        self.startup_on = tk.BooleanVar(value=startup.is_enabled())
        ttk.Checkbutton(bottom, text="Start with Windows (in tray)", variable=self.startup_on,
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
        manual = list(self.cfg.data.get("manualGames", []))
        seen = dict(self.cfg.data.get("lastPlayed", {}))
        self.run_bg(lambda: library.all_games(manual, seen), self._games_loaded, "Reading game libraries...")

    def _games_loaded(self, games: list[steam.Game]) -> None:
        first_load = not self.games
        self.games = games
        self.by_id = {g.appid: g for g in games}
        self._games_ready.set()
        # Trainers installed before game versions were recorded: take the current version as theirs.
        changed = False
        for game in games:
            entry = self.cfg.game(game.appid)
            if entry and game.build and not entry.get("gameBuild"):
                entry["gameBuild"] = game.build
                changed = True
        if changed:
            self.cfg.save()
        self.set_status("{} games found, most recently played first".format(len(games))
                        if games else "No games found. Use Add game to add one by hand.")
        self._fill_list()
        if first_load:
            # Start on the list so a controller works straight away, but only when the window is
            # showing: asking for focus while hidden (tray / widget start) still activates the app,
            # which closes Game Bar's overlay. show_window() focuses the list when it appears.
            if not self.hidden:
                self.tree.focus_set()

    def _fill_list(self) -> None:
        # A game that was just added by hand gets selected once it appears.
        selected = getattr(self, "_select_after_load", None) or self.selected_appid()
        self._select_after_load = None
        needle = self.search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for game in self.games:
            if needle and needle not in game.name.lower():
                continue
            entry = self.cfg.game(game.appid)
            mark = ""
            if entry and os.path.isfile(entry.get("path", "")):
                mark = "Game updated" if library.game_updated(entry, game) else "Ready"
            self.tree.insert("", "end", iid=game.appid, text=game.name, values=(game.source, mark))
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
            if library.game_updated(entry, game):
                ttk.Label(self.detail, foreground="#b35900", wraplength=self.px(360),
                          text="The game was updated since this trainer was installed. Trainers are made for "
                               "specific game versions: if cheats stop working, check for a trainer update.").pack(
                    anchor="w", pady=(0, 8))
            buttons = [
                ("Launch trainer", lambda: self._launch_now(game.appid, entry["path"])),
                ("Check for trainer update", lambda: self.check_trainer_update(game.appid)),
                ("Find a different trainer", self.find_trainer),
                ("Open trainer folder", lambda: os.startfile(os.path.dirname(entry["path"]))),
                ("Remove trainer", lambda: self.remove_trainer(game.appid)),
            ]
        else:
            ttk.Label(self.detail, text="No trainer yet", foreground="#777").pack(anchor="w", pady=(16, 8))
            buttons = [("Find trainer online", self.find_trainer)]
        if game.source == "Manual":
            buttons.append(("Remove this game from the list", lambda: self.remove_manual_game(game)))
        for label, command in buttons:
            ttk.Button(self.detail, text=label, command=command).pack(fill="x", pady=4)

    def _record_played(self, appids: set[str], when: int) -> None:
        played = self.cfg.data.setdefault("lastPlayed", {})
        for appid in appids:
            played[appid] = when
        self.cfg.save()

    def add_game(self) -> None:
        from tkinter import filedialog, simpledialog

        exe = filedialog.askopenfilename(parent=self.root, title="Pick the game's .exe",
                                         filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
        if not exe:
            return
        folder = os.path.dirname(exe)
        default = os.path.splitext(os.path.basename(exe))[0]
        name = simpledialog.askstring("Add game", "Name to show (used to search FLiNG):", initialvalue=default,
                                      parent=self.root)
        if not name:
            return
        appid = library.manual_id(folder)
        manual = [e for e in self.cfg.data.get("manualGames", []) if e["id"] != appid]
        manual.append({"id": appid, "name": name.strip(), "dir": folder})
        self.cfg.data["manualGames"] = manual
        self.cfg.save()
        self._select_after_load = appid
        self.refresh_games()

    def remove_manual_game(self, game: steam.Game) -> None:
        if not self.msg.askyesno("AutoCheatDeck", "Remove {} from the list? Its trainer is removed too.".format(
                game.name), parent=self.root):
            return
        folder = os.path.join(config.trainers_dir(), game.appid)
        if os.path.isdir(folder):
            shutil.rmtree(folder + ".removing", ignore_errors=True)
            try:
                os.rename(folder, folder + ".removing")
            except OSError:
                self.msg.showerror("AutoCheatDeck", "Close the trainer before removing the game.", parent=self.root)
                return
            shutil.rmtree(folder + ".removing", ignore_errors=True)
        self.cfg.data["manualGames"] = [e for e in self.cfg.data.get("manualGames", []) if e["id"] != game.appid]
        self.cfg.data["games"].pop(game.appid, None)
        self.cfg.save()
        self.refresh_games()

    # ---------- trainers ----------

    def _launch_now(self, appid: str, path: str) -> None:
        try:
            launch_trainer(path)
        except Exception as e:  # report every launch failure in the UI
            log.get().exception("could not start trainer %s", path)
            self.msg.showerror("AutoCheatDeck", str(e), parent=self.root)
            return
        log.get().info("started trainer %s", path)
        game = self.by_id.get(appid)
        self.set_status("Started trainer for {}".format(game.name if game else appid))
        # Only Steam records play times; launching a trainer counts as playing for the others.
        self._record_played({appid}, int(time.time()))

    # ---------- cheats (shared by the Game Bar API) ----------

    def _cheat_cache(self, appid: str) -> str:
        folder = os.path.join(config.data_dir(), "cheats")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, appid + ".json")

    def cached_cheats(self, appid: str) -> dict | None:
        entry = self.cfg.game(appid)
        if not entry or not entry.get("sha256"):
            return None
        return cheats.load_cache(self._cheat_cache(appid), entry["sha256"])

    def _trainer(self, appid: str) -> dict:
        entry = self.cfg.game(appid)
        if not entry or not os.path.isfile(entry.get("path", "")):
            raise ValueError("No trainer installed for this game")
        return entry

    def read_cheats(self, appid: str, wait_seconds: float = 0) -> dict:
        """Read the running trainer's cheats through the helper and cache them (off the UI thread)."""
        entry = self._trainer(appid)
        deadline = time.monotonic() + wait_seconds
        while not cheats.trainer_window(entry["path"]) and time.monotonic() < deadline:
            time.sleep(0.5)  # bounded wait for a trainer that was just started
        if not cheats.trainer_window(entry["path"]):
            raise ValueError("The trainer isn't running")
        parsed = cheats.parse_trainer_ui(self.helper.dump(helper.trainer_title(entry["path"])))
        if not parsed["cheats"]:
            raise ValueError("No cheats found in the trainer window yet")
        cheats.save_cache(self._cheat_cache(appid), entry["sha256"], parsed)
        log.get().info("cheats: read %d cheats for %s", len(parsed["cheats"]), appid)
        return parsed

    # Game Bar API (called on the API server's threads)

    def _restart_idle_timer(self) -> None:
        if not self.idle_quit:
            return
        if self._idle_job is not None:
            self.root.after_cancel(self._idle_job)
        self._idle_job = self.root.after(self.idle_quit * 1000, self._idle_expired)

    def _stop_idle_quit(self) -> None:
        self.idle_quit = 0
        if self._idle_job is not None:
            self.root.after_cancel(self._idle_job)
            self._idle_job = None

    def _idle_expired(self) -> None:
        self._idle_job = None
        running = [t for t in self.helper.trainers if cheats.trainer_window_by_title(t)]
        if running:
            self._restart_idle_timer()  # keep the helper (and its one UAC approval) while playing
            return
        if self.idle_quit and self.hidden and not self.overlay.visible:
            log.get().info("widget mode: no requests for %ss, quitting", self.idle_quit)
            self.quit()

    def api_activity(self) -> None:
        if self.idle_quit:
            self.post(self._restart_idle_timer)

    def api_games(self) -> list[dict]:
        self._games_ready.wait(15)  # a just-started app is still reading the game library
        if not self.helper.running and not self.helper.declined:
            # The widget is open: start the admin helper now, so Launch doesn't wait for it.
            threading.Thread(target=self._warm_helper, name="helper-start", daemon=True).start()
        result = []
        for game in self.games:
            entry = self.cfg.game(game.appid)
            if entry and os.path.isfile(entry.get("path", "")):
                result.append({"id": game.appid, "name": game.name, "source": game.source,
                               "trainer": entry["title"], "hasCheats": self.cached_cheats(game.appid) is not None,
                               "running": bool(cheats.trainer_window(entry["path"])),
                               "gameUpdated": library.game_updated(entry, game)})
        return result

    def _warm_helper(self) -> None:
        try:
            self.helper.request("ping")
        except Exception:  # Launch tries again (and reports the error)
            log.get().exception("helper: early start failed")

    def api_cheats(self, appid: str) -> list[dict] | None:
        """Cached cheats, with live on/off state when the trainer is running."""
        cached = self.cached_cheats(appid)
        if not cached:
            return None
        entry = self._trainer(appid)
        states: dict[str, dict] = {}
        if cheats.trainer_window(entry["path"]):
            states = self.helper.states(helper.trainer_title(entry["path"]))
        return cheats.with_states(cached["cheats"], states)

    def api_launch(self, appid: str) -> dict:
        entry = self._trainer(appid)
        if not cheats.trainer_window(entry["path"]):
            actual = fling.check_trainer(entry["path"], os.path.join(config.trainers_dir(), appid),
                                         entry.get("exeSha256"))
            if not entry.get("exeSha256"):  # installed before fingerprints were recorded
                self.post(lambda: self.cfg.set_game(appid, dict(entry, exeSha256=actual)))
            self.helper.launch(entry["path"])  # the helper is elevated: no second prompt for the trainer
            log.get().info("started trainer %s (via helper)", entry["path"])
            self.post(lambda: self._record_played({appid}, int(time.time())))
        reading = self.cached_cheats(appid) is None
        if reading:
            def work() -> None:
                try:
                    self.read_cheats(appid, wait_seconds=30)
                except Exception:  # the widget sees "no cheats yet" and can ask again
                    log.get().exception("cheats: reading after launch failed")

            threading.Thread(target=work, name="read-cheats", daemon=True).start()
        return {"started": True, "readingCheats": reading}

    def api_overlay(self, appid: str) -> dict:
        self._trainer(appid)  # errors (no trainer) go back to the widget
        from .overlay import exclusive_fullscreen

        self.post(lambda: self.overlay.open(appid))
        return {"open": True, "exclusiveFullscreen": exclusive_fullscreen()}

    def api_read(self, appid: str) -> dict:
        return {"cheats": self.read_cheats(appid)["cheats"]}

    def api_toggle(self, appid: str, name: str, nth: int = 0) -> dict:
        entry = self._trainer(appid)
        if not name:
            raise ValueError("No cheat given")
        on = self.helper.toggle(helper.trainer_title(entry["path"]), name, nth)
        log.get().info("cheats: %s -> %s", name, "on" if on else "off")
        return {"on": on}

    def api_set_value(self, appid: str, name: str, value: str, nth: int = 0) -> dict:
        entry = self._trainer(appid)
        if not name:
            raise ValueError("No cheat given")
        now = self.helper.set_value(helper.trainer_title(entry["path"]), name, values.check(value), nth)
        log.get().info("cheats: %s = %s", name, now)
        return {"value": now}

    def find_trainer(self) -> None:
        game = self.by_id.get(self.selected_appid() or "")
        if game:
            TrainerFinder(self, game)

    def install_trainer(self, game: steam.Game, page: dict, version: dict) -> None:
        target = os.path.join(config.trainers_dir(), game.appid)
        if game.appid in self._downloading:
            self.set_status("Already downloading a trainer for {}".format(game.name))
            return
        self._downloading.add(game.appid)

        def done(result: dict) -> None:
            self._downloading.discard(game.appid)
            self.cfg.set_game(game.appid, {
                "title": version["title"],
                "pageUrl": page["url"],
                "path": result["path"],
                "sha256": result["sha256"],
                "exeSha256": result["exeSha256"],  # checked before the admin helper starts it
                "gameBuild": game.build,  # the game version this trainer was installed for
            })
            self.set_status("Installed {}".format(version["title"]))
            self._fill_list()
            if not self.cfg.data.get("defenderNoticeShown"):
                self.cfg.data["defenderNoticeShown"] = True
                self.cfg.save()
                self.msg.showinfo(
                    "AutoCheatDeck",
                    "Windows Defender often flags game trainers because they modify game memory.\n\n"
                    "If the trainer disappears or won't start, check Windows Security > Protection history. "
                    "Only allow trainers you trust.\n\n"
                    "Use trainers in single-player games only: in an online game with anti-cheat, a trainer "
                    "can get your account banned.",
                    parent=self.root,
                )

        self.run_bg(lambda: fling.download(version["url"], page["url"], target), done,
                    "Downloading {}...".format(version["title"]),
                    failed=lambda: self._downloading.discard(game.appid))

    def check_trainer_update(self, appid: str) -> None:
        entry = self.cfg.game(appid)
        game = self.by_id.get(appid)
        if not entry or not game:
            return

        def done(detail: dict) -> None:
            self.set_status("")
            latest = detail["versions"][0] if detail["versions"] else None
            if not latest or latest["title"] == entry["title"]:
                # Nothing newer: accept this game version for the current trainer (clears the warning).
                if library.game_updated(entry, game):
                    self.cfg.set_game(appid, dict(entry, gameBuild=game.build))
                    self._fill_list()
                self.msg.showinfo("AutoCheatDeck", "Trainer is up to date.", parent=self.root)
            elif self.msg.askyesno("AutoCheatDeck", "Newer trainer available:\n\n{}\n\nDownload it?".format(
                    latest["title"]), parent=self.root):
                self.install_trainer(game, {"url": entry["pageUrl"]}, latest)

        self.run_bg(lambda: fling.list_versions(entry["pageUrl"]), done, "Checking trainer...")

    def remove_trainer(self, appid: str) -> None:
        if not self.msg.askyesno("AutoCheatDeck", "Remove this game's trainer?", parent=self.root):
            return
        folder = os.path.join(config.trainers_dir(), appid)
        if os.path.isdir(folder):
            # Renaming fails while the trainer is running, so nothing is half-deleted.
            doomed = folder + ".removing"
            shutil.rmtree(doomed, ignore_errors=True)
            try:
                os.rename(folder, doomed)
            except OSError:
                self.msg.showerror("AutoCheatDeck", "Close the trainer before removing it.", parent=self.root)
                return
            shutil.rmtree(doomed, ignore_errors=True)
        self.cfg.set_game(appid, None)
        self._fill_list()

    def _toggle_startup(self) -> None:
        if not self.startup_on.get():
            try:
                startup.disable()
            except OSError as e:
                self.msg.showerror("AutoCheatDeck", str(e), parent=self.root)
            self.startup_on.set(startup.is_enabled())
            return
        if not self.app_dir:
            self.startup_on.set(False)
            self.msg.showinfo("AutoCheatDeck", "Start with Windows only works from the packaged app.",
                              parent=self.root)
            return

        def done(mode: str) -> None:
            self.set_status("")
            self.startup_on.set(startup.is_enabled())
            log.get().info("startup: enabled via %s", mode)
            if mode == "run":
                self.msg.showinfo(
                    "AutoCheatDeck",
                    "AutoCheatDeck will start with Windows on the desktop.\n\nThe Xbox full screen "
                    "experience only runs startup tasks, which needs one administrator approval. Untick "
                    "and tick Start with Windows again to approve it.",
                    parent=self.root,
                )

        self.run_bg(lambda: startup.enable(sys.executable), done, "Setting up Start with Windows...",
                    failed=lambda: self.startup_on.set(startup.is_enabled()))

    # ---------- controller ----------

    def _settle_focus(self) -> None:
        """Tk can report FocusIn just before Windows finishes bringing the window to the front, so
        check now and a few times over the next second (then stop: nothing keeps running)."""
        for delay in (0, 150, 400, 1000):
            self.root.after(delay, self._check_focus)

    def _check_focus(self) -> None:
        # Tk's focus_get() keeps naming its last widget after another app takes over, so ask
        # Windows which process owns the foreground window (one call, only on focus events).
        from .gamepad import foreground_window

        self.gamepad.set_active((not self.hidden or self.overlay.visible) and foreground_window()[0])

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
        if self.overlay.visible:
            self.overlay.on_pad(name)
            return
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
            appid = self.selected_appid() or ""
            entry = self.cfg.game(appid)
            if entry and os.path.isfile(entry.get("path", "")):
                self._launch_now(appid, entry["path"])
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
                self.msg.showinfo("AutoCheatDeck", "AutoCheatDeck is up to date.", parent=self.root)

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
            self.msg.showinfo("AutoCheatDeck", "Running from source; updates only apply to the packaged app.",
                              parent=self.root)
            return
        notes = (info.get("notes") or "").strip()
        if not self.msg.askyesno("AutoCheatDeck", "Install {}?\n\n{}\n\nAutoCheatDeck will restart.".format(
                info["latest"], notes[:600]), parent=self.root):
            return

        if self._updating:
            return
        self._updating = True

        def done(_: str) -> None:
            updater.start_finisher(self.app_dir, os.getpid())
            self.quit()

        def failed() -> None:
            self._updating = False

        self.run_bg(lambda: updater.stage_update(self.app_dir), done, "Downloading update...", failed=failed)

    def rollback(self) -> None:
        if self._updating:
            return
        if not self.msg.askyesno("AutoCheatDeck", "Go back to the previous version? AutoCheatDeck will restart.",
                                 parent=self.root):
            return
        self._updating = True

        def done(_: None) -> None:
            updater.start_finisher(self.app_dir, os.getpid())
            self.quit()

        def failed() -> None:
            self._updating = False

        self.run_bg(lambda: updater.stage_rollback(self.app_dir), done, "Preparing the previous version...",
                    failed=failed)


class TrainerFinder(tk.Toplevel):
    """Search FLiNG, pick a trainer page, pick a version, confirm."""

    def __init__(self, app: App, game: steam.Game):
        super().__init__(app.root)
        self.app = app
        self.game = game
        self.page: dict | None = None
        self.items: list[dict] = []
        self._request = 0  # only the latest search / version lookup may update the list
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

    def _latest(self, show: Callable[[Any], None]) -> Callable[[Any], None]:
        """Wrap a result handler so it is dropped if the window closed or a newer request started."""
        self._request += 1
        request = self._request

        def handler(result: Any) -> None:
            if request == self._request and self.winfo_exists():
                show(result)

        return handler

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
        self.items = []
        self.app.run_bg(lambda: fling.search(query), self._latest(
            lambda results: self._show("Pick the trainer for your game:", results, "No trainers found")))

    def choose(self) -> None:
        selection = self.listbox.curselection()
        if not selection or not self.items:
            return
        item = self.items[selection[0]]
        if self.page is None:
            self.page = item
            self.back.pack(side="left")
            self.heading.configure(text="Loading versions...")
            self.items = []

            def shown(detail: dict) -> None:
                updated = " (updated {})".format(detail["lastUpdated"]) if detail["lastUpdated"] else ""
                self._show("Pick a version" + updated + ":", detail["versions"], "No downloads on this page")

            self.app.run_bg(lambda: fling.list_versions(item["url"]), self._latest(shown))
            return
        if self.app.msg.askyesno(
            "Download trainer?",
            "{}\n\nThis downloads a third-party Windows program from flingtrainer.com for {}. "
            "Only use trainers you trust.".format(item["title"], self.game.name),
            parent=self,
        ):
            self.app.install_trainer(self.game, self.page, item)
            self.destroy()
            self.app.tree.focus_set()
