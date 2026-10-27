# AutoCheatDeck for Windows

A portable app that finds [FLiNG](https://flingtrainer.com) trainers for your installed games (Steam, Xbox / Game Pass, or any game you add), downloads them, and launches them when you ask. Windows counterpart of the [AutoCheatDeck](https://github.com/JoeysRetroHandhelds/AutoCheatDeck) Decky plugin.

## Use

1. Download `AutoCheatDeck-win64.zip` from the [latest release](https://github.com/JoeysRetroHandhelds/AutoCheatDeck-Windows/releases/latest), unzip it anywhere and run `AutoCheatDeck\AutoCheatDeck.exe`. Settings and trainers are stored in the `data` folder inside it, so the whole folder is portable.
2. Pick a game, press **Find trainer online**, choose the trainer and version.
3. Start your game, then press **Launch trainer** (or **X** on a controller). FLiNG trainers are built to run as administrator, so Windows may show a UAC prompt.
4. Closing the window keeps AutoCheatDeck in the system tray; quit from the tray icon's menu. Opening AutoCheatDeck again (shortcut, Start menu, Xbox app) or clicking the tray icon shows the window.

Games are listed most recently played first. The list includes Steam games, Xbox app / Game Pass games (installed in any Xbox games folder, on any drive), and anything you add with **Add game** (pick the game's .exe: DRM-free, GOG, Epic, emulators...). For Xbox and added games, "last played" is when you last launched their trainer.

AutoCheatDeck does nothing in the background: no game detection and no timers. While it sits in the tray or isn't the focused window it uses no CPU.

### Controller

The whole app works with an Xbox-style controller or the keyboard (arrows, Enter, Esc, Page Up/Down): D-pad or left stick to move, **A** select, **B** back, **Y** find a trainer for the selected game, **X** launch its trainer, **LB/RB** page through the list. Controllers have to be polled, so the controller is only read while an AutoCheatDeck window (or one of its dialogs) has focus: it never reacts to buttons pressed in a game.

### Cheats over the game (Game Bar widget)

The **AutoCheatDeck** widget in Xbox Game Bar (Win+G, or the Xbox / guide button) lets you start a trainer and switch its cheats without leaving the game:

1. Open Game Bar, open **Widgets > AutoCheatDeck**. It lists the games that have a trainer. If AutoCheatDeck isn't running the widget starts it (in the tray), and it quits again a couple of minutes after you stop using it, unless a trainer it started is still running.
2. Pick the game and press **Launch trainer**. Windows asks once per session for administrator approval: FLiNG trainers run as administrator, so only an administrator helper can start them and press their buttons. The trainer's own window stays hidden.
3. The first launch of a trainer reads its cheat list (it's remembered until the trainer is updated). Then the **cheat overlay** opens over the game:

| Controller | Keyboard | Does |
|---|---|---|
| Up / Down, LB / RB | arrows, Page Up / Down | move, page |
| **A** | Enter | switch the cheat on / off |
| **Left / Right** | Left / Right | change a multiplier, speed or option (`‹ 2.0 ›`) |
| **Y** | | type an amount (money, items) one digit at a time: Up / Down change a digit, Left / Right move, A sets, B cancels |
| **B** | Esc | close; focus goes back to the game |

**Show cheats** in the widget opens the overlay again. Quitting AutoCheatDeck gives the trainer its window back (minimized), so it can be closed normally.

The overlay also has touch / mouse buttons: double-tap a cheat to switch it, **−** / **+** change its value, **Amount** opens a number pad, **Close** closes.

Games in *exclusive* fullscreen would minimize if the overlay took focus, so over those it opens without focus and works by touch only. To use the controller, set the game's display mode to **Borderless** (most recent games already are).

**Installing the widget.** Download `AutoCheatDeckWidget.msixbundle` and `AutoCheatDeckWidget.cer` from the release. Trust the certificate once (as administrator: import the `.cer` into *Local Machine > Trusted People*), then double-click the bundle to install it. Game Bar only lists signed widgets. Open AutoCheatDeck on the desktop once afterwards so the widget can find it.

To build it yourself, open `widget/AutoCheatDeckWidget` in Visual Studio with the UWP workload (the full-trust launcher is compiled automatically) and sign the package with your own certificate.

### Start with Windows

Tick **Start with Windows** to launch AutoCheatDeck into the tray at sign-in. The Xbox full screen experience on handhelds skips ordinary startup apps, so this registers a sign-in task instead, which needs one administrator approval when you tick it (the task itself runs as you, not as admin). Decline it and AutoCheatDeck still starts with Windows on the desktop. Moving the folder or turning the option off needs no further approval.

### Updates

**Check for updates** / **Install update** downloads the new version, swaps the app files (your `data` folder is untouched) and restarts; **Restore previous version** swaps back.

Trainers are third-party programs that modify game memory, and Windows Defender often flags them. If Windows Security removes a trainer, its game shows **Missing** with buttons to open Protection history (to restore it), download it again or remove it; if it blocks one from starting, AutoCheatDeck says so. AutoCheatDeck never changes Windows Security's settings itself. Only use trainers you trust, and only in single-player games: using one in an online game with anti-cheat can get your account banned.

If the controller isn't detected, the status line at the bottom says so, and `data\autocheatdeck.log` records what the app saw.

**Reporting a problem:** press **Send logs**. It saves a zip on your Desktop with the logs, settings and the cheat lists AutoCheatDeck read (your Windows user name is blanked out of all of them) and offers to open a GitHub issue you can attach it to.

## Develop

```
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -t .
python -m PyInstaller --onedir --noconsole --name AutoCheatDeck main.py
```

Releasing: write `docs/releases/vX.Y.Z.md` (the release notes users read), set `autocheatdeck/__init__.py` to the same version, then push a `vX.Y.Z` tag. CI builds, signs and publishes the release. Signing needs these repository secrets: `RELEASE_SIGNING_KEY` (app updates; a tag build fails without it) and `WIDGET_SIGNING_PFX` + `WIDGET_SIGNING_PASSWORD` (the Game Bar widget; without them it's left out of the release).
