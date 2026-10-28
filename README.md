# AutoCheatDeck for Windows

A portable app that finds [FLiNG](https://flingtrainer.com) trainers for your installed games (Steam, Xbox / Game Pass, or any game you add), downloads them, and launches them when you ask. Windows counterpart of the [AutoCheatDeck](https://github.com/JoeysRetroHandhelds/AutoCheatDeck) Decky plugin.

## Use

1. Download `AutoCheatDeck-win64.zip` from the [latest release](https://github.com/JoeysRetroHandhelds/AutoCheatDeck-Windows/releases/latest), unzip it anywhere and run `AutoCheatDeck\AutoCheatDeck.exe`. Settings and trainers are stored in the `data` folder inside it, so the whole folder is portable.
2. Pick a game, press **Find trainer online**, choose the trainer and version.
3. Start your game, then press **Launch trainer** (or **X** on a controller). FLiNG trainers are built to run as administrator, so Windows may show a UAC prompt.
4. Closing the window keeps AutoCheatDeck in the system tray; quit from the tray icon's menu. Opening AutoCheatDeck again (shortcut, Start menu, Xbox app) or clicking the tray icon shows the window.

Games are listed most recently played first. The list includes Steam games, Xbox app / Game Pass games (installed in any Xbox games folder, on any drive), and anything you add with **Add game** (pick the game's .exe: DRM-free, GOG, Epic, emulators...). For Xbox and added games, "last played" is when you last launched their trainer.

### Start the trainer with a Steam game

For a Steam game with a trainer, tick **Start the trainer when I play this game in Steam**. From then on, pressing Play in Steam (desktop, Big Picture or Xbox full screen mode) starts the game and, a few seconds later, its trainer; AutoCheatDeck doesn't need to be open. Trainers run as administrator, so Windows asks for approval each time.

This works through the game's Steam launch options. Steam only saves those while it's closed, so ticking or unticking the box closes Steam and opens it again. Launch options you already had are kept, and come back as they were when you untick it. A backup of Steam's settings file is kept next to it (`localconfig.vdf.autocheatdeck-backup`). Prefer to do it yourself? **Copy the Steam launch option** and paste it into the game's Properties > Launch Options. If you move the AutoCheatDeck folder, untick and tick the box again so the option points at the new place.

AutoCheatDeck does nothing in the background: no game detection and no timers. While it sits in the tray or isn't the focused window it uses no CPU.

### Controller

The whole app works with an Xbox-style controller or the keyboard (arrows, Enter, Esc, Page Up/Down): D-pad or left stick to move, **A** select, **B** back, **Y** find a trainer for the selected game, **X** launch its trainer, **LB/RB** page through the list. Controllers have to be polled, so the controller is only read while an AutoCheatDeck window (or one of its dialogs) has focus: it never reacts to buttons pressed in a game.

### Using the trainer

Once a trainer is running, use it as FLiNG intends: its own window, or its hotkeys (usually the number pad, shown next to each cheat). Older versions (2.1.x) had a Game Bar widget and a cheat overlay; FLiNG trainers only accept real clicks and keypresses, so switching cheats from outside the trainer couldn't be done reliably and was removed in 2.2.0. Updating from 2.1.x removes the old widget, and asks once for administrator approval to remove its certificate and network exemption too.

### Start with Windows

Tick **Start with Windows** to launch AutoCheatDeck into the tray at sign-in. The Xbox full screen experience on handhelds skips ordinary startup apps, so this registers a sign-in task instead, which needs one administrator approval when you tick it (the task itself runs as you, not as admin). Decline it and AutoCheatDeck still starts with Windows on the desktop. Moving the folder or turning the option off needs no further approval.

### Updates

**Check for updates** / **Install update** downloads the new version, swaps the app files (your `data` folder is untouched) and restarts; **Restore previous version** swaps back.

Trainers are third-party programs that modify game memory, and Windows Defender often flags them. If Windows Security removes a trainer, its game shows **Missing** with buttons to open Protection history (to restore it), download it again or remove it; if it blocks one from starting, AutoCheatDeck says so. AutoCheatDeck never changes Windows Security's settings itself. Only use trainers you trust, and only in single-player games: using one in an online game with anti-cheat can get your account banned.

If the controller isn't detected, the status line at the bottom says so, and `data\autocheatdeck.log` records what the app saw.

**Reporting a problem:** press **Send logs**. It saves a zip on your Desktop with the logs and settings (your Windows user name is blanked out of all of them) and offers to open a GitHub issue you can attach it to.

## Develop

```
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -t .
python -m PyInstaller --onedir --noconsole --name AutoCheatDeck main.py
```

Releasing: write `docs/releases/vX.Y.Z.md` (the release notes users read), set `autocheatdeck/__init__.py` to the same version, then push a `vX.Y.Z` tag. CI builds, signs and publishes the release. Signing needs the `RELEASE_SIGNING_KEY` repository secret (a tag build fails without it).
