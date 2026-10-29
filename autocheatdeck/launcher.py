"""Steam launch-option mode: `AutoCheatDeck.exe --run <appid> %command%`.

Steam replaces %command% with the game's own command line, so Steam starts AutoCheatDeck, which
starts the game, waits the configured delay, starts that game's trainer and then waits for the
game to exit (so Steam still sees the game as running). No window, no background watching, and it
works however the game is launched from Steam: the desktop client, Big Picture or the Xbox full
screen experience. steamcfg.py sets the option up.
"""

import os
import subprocess
import time

from . import config, log

RUN_FLAG = "--run"


def launch_option(exe_path: str, appid: str) -> str:
    """The text to paste into the game's Steam launch options."""
    return '"{}" {} {} %command%'.format(exe_path, RUN_FLAG, appid)


def run(appid: str, command: list[str], start_trainer, sleep=time.sleep) -> int:
    """Start the game, then its trainer after the launch delay. Returns the game's exit code."""
    logger = log.get()
    if not command:
        logger.error("launch option: no game command for %s (is %%command%% missing?)", appid)
        return 1
    cfg = config.Config()
    logger.info("launch option: starting %s: %s", appid, command)
    try:
        game = subprocess.Popen(command, cwd=os.path.dirname(command[0]) or None)
    except OSError:
        logger.exception("launch option: could not start the game for %s", appid)
        return 1

    entry = cfg.game(appid)
    path = entry.get("path", "") if entry else ""
    if not os.path.isfile(path):
        logger.warning("launch option: no trainer installed for %s", appid)
    else:
        # Start the trainer even if the first process already exited: many games go through a small
        # launcher that hands off to the real exe, and FLiNG trainers wait for the game themselves.
        try:
            delay = float(cfg.data.get("launchDelay", 5))
        except (TypeError, ValueError):
            logger.warning("launch option: launchDelay %r is not a number; using 5", cfg.data.get("launchDelay"))
            delay = 5.0
        sleep(delay)
        try:
            start_trainer(path)
            logger.info("launch option: started trainer %s", path)
        except Exception:  # the game keeps running; just record why the trainer didn't
            logger.exception("launch option: could not start trainer %s", path)
    return game.wait()
