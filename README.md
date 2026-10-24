# AutoCheatDeck for Windows

A portable app that finds [FLiNG](https://flingtrainer.com) trainers for your installed Steam games, downloads them, and starts the trainer automatically when the game launches. Windows counterpart of the [AutoCheatDeck](https://github.com/JoeysRetroHandhelds/AutoCheatDeck) Decky plugin.

## Use

1. Download `AutoCheatDeck.exe` from the [latest release](https://github.com/JoeysRetroHandhelds/AutoCheatDeck-Windows/releases/latest) and put it in its own folder. Settings and trainers are stored in a `data` folder next to it.
2. Pick a game, press **Find trainer online**, choose the trainer and version.
3. Start the game. The trainer launches after the delay set at the bottom (default 5 s). Trainers that need admin rights show a UAC prompt.
4. **Ctrl+Alt+T** brings the window to the front, even over a game.

Updates: **Check for updates** / **Install update** replaces the exe and restarts; **Restore previous version** swaps back.

Trainers are third-party programs that modify game memory, and Windows Defender often flags them. Only use trainers you trust.

## Develop

```
python main.py
python -m unittest discover -s tests -t .
python -m PyInstaller --onefile --noconsole --name AutoCheatDeck main.py
```

Pushing a `vX.Y.Z` tag that matches `autocheatdeck/__init__.py` builds and publishes a release.
