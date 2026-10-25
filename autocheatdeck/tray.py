"""System tray icon: the app lives here while its window is hidden."""

from typing import Callable

import pystray
from PIL import Image, ImageDraw, ImageFont


def _icon_image() -> Image.Image:
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=14, fill=(47, 111, 219, 255))
    try:
        font = ImageFont.truetype("segoeuib.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    draw.text((size / 2, size / 2), "A", font=font, fill="white", anchor="mm")
    return image


class Tray:
    """Runs the tray icon on its own thread; callbacks run on that thread."""

    def __init__(self, on_show: Callable[[], None], on_quit: Callable[[], None]):
        menu = pystray.Menu(
            pystray.MenuItem("Show AutoCheatDeck", lambda: on_show(), default=True),
            pystray.MenuItem("Quit", lambda: on_quit()),
        )
        self._icon = pystray.Icon("AutoCheatDeck", _icon_image(), "AutoCheatDeck", menu)
        self._icon.run_detached()

    def notify(self, message: str) -> None:
        try:
            self._icon.notify(message, "AutoCheatDeck")
        except Exception:  # notifications are a nicety; never fail because of one
            pass

    def stop(self) -> None:
        self._icon.stop()
