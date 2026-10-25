# AutoCheatDeck for Windows

A portable app that finds [FLiNG](https://flingtrainer.com) trainers for your installed Steam games, downloads them, and starts the trainer automatically when the game launches. Windows counterpart of the [AutoCheatDeck](https://github.com/JoeysRetroHandhelds/AutoCheatDeck) Decky plugin.

## Use

1. Download `AutoCheatDeck-win64.zip` from the [latest release](https://github.com/JoeysRetroHandhelds/AutoCheatDeck-Windows/releases/latest), unzip it anywhere and run `AutoCheatDeck\AutoCheatDeck.exe`. Settings and trainers are stored in the `data` folder inside it, so the whole folder is portable.
2. Pick a game, press **Find trainer online**, choose the trainer and version.
3. Start the game. The trainer launches after the delay set at the bottom (default 5 s). Trainers that need admin rights show a UAC prompt.
4. Closing the window keeps AutoCheatDeck running in the system tray, so trainers still start with your games; quit from the tray icon's menu. Opening AutoCheatDeck again (shortcut, Start menu, Xbox app) or clicking the tray icon shows the window.

Games are listed most recently played first.

### Steam launch option (most reliable)

For a game with a trainer, **Use Steam launch option** copies a line like `"...\AutoCheatDeck.exe" --run 3259780 %command%` to the clipboard; paste it into the game's Steam launch options (right-click the game > Properties > General). Steam then starts the trainer with the game every time, whether you launch it from Steam, Big Picture, the Xbox app or the Xbox full screen experience, and AutoCheatDeck doesn't need to be running. Games set up this way are skipped by the background game detection, so the trainer never starts twice.

### Controller

The whole app works with an Xbox-style controller or the keyboard (arrows, Enter, Esc, Page Up/Down): D-pad or left stick to move, **A** select, **B** back, **Y** find a trainer for the selected game, **X** launch its trainer, **LB/RB** page through the list. The controller is only read while AutoCheatDeck is the active window, so it never reacts to buttons pressed in a game and uses no CPU in the background.

### Start with Windows

Tick **Start with Windows** to launch AutoCheatDeck into the tray at sign-in, so trainers start with your games without opening it first. This uses your user's startup list (no admin needed) and also works with the Xbox full screen experience on handhelds; switch to it there with Windows' task switcher, or add AutoCheatDeck.exe to the Xbox app as a game and open it from there. If you move the folder, open the app once from its new place and it updates the startup entry.

Updates: **Check for updates** / **Install update** downloads the new version, swaps the app files (your `data` folder is untouched) and restarts; **Restore previous version** swaps back.

Trainers are third-party programs that modify game memory, and Windows Defender often flags them. Only use trainers you trust.

If the controller isn't detected, the status line at the bottom says so, and `datautocheatdeck.log` records what the app saw (useful when reporting a problem).

## Develop

```
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -t .
python -m PyInstaller --onedir --noconsole --name AutoCheatDeck main.py
```

Pushing a `vX.Y.Z` tag that matches `autocheatdeck/__init__.py` builds and publishes a release.
