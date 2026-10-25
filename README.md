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

### Start with Windows

Tick **Start with Windows** to launch AutoCheatDeck into the tray at sign-in. The Xbox full screen experience on handhelds skips ordinary startup apps, so this registers a sign-in task instead, which needs one administrator approval when you tick it (the task itself runs as you, not as admin). Decline it and AutoCheatDeck still starts with Windows on the desktop. Moving the folder or turning the option off needs no further approval.

### Updates

**Check for updates** / **Install update** downloads the new version, swaps the app files (your `data` folder is untouched) and restarts; **Restore previous version** swaps back.

Trainers are third-party programs that modify game memory, and Windows Defender often flags them. Only use trainers you trust.

If the controller isn't detected, the status line at the bottom says so, and `data\autocheatdeck.log` records what the app saw (useful when reporting a problem).

## Develop

```
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -t .
python -m PyInstaller --onedir --noconsole --name AutoCheatDeck main.py
```

Pushing a `vX.Y.Z` tag that matches `autocheatdeck/__init__.py` builds and publishes a release.
