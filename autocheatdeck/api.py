"""Local API for the Xbox Game Bar widget.

Listens on 127.0.0.1 only. Every request must carry the X-AutoCheatDeck header and no Origin
header: browsers can't add a custom header to a cross-site request without a CORS preflight (which
is never answered), so a web page can't drive it. The thread sleeps in accept() between requests,
so the API costs nothing while unused.

  GET  /api/games                    games that have a trainer
  GET  /api/games/<id>/cheats        cheats with live on/off state ({"cheats": [...]} or {"cheats": null})
  POST /api/games/<id>/launch        start the trainer (reads its cheats if not cached yet)
  POST /api/games/<id>/read          (re)read the running trainer's cheats
  POST /api/games/<id>/overlay       open AutoCheatDeck's cheat overlay over the game
  POST /api/games/<id>/toggle        {"name": "Infinite HP"} switch a cheat; returns {"on": bool}
  POST /api/games/<id>/value         {"name": "Edit Money", "value": "5000"}; returns {"value": str}

Toggle and value take an optional "nth" (default 0) for the nth cheat of that name: trainers can
repeat a name in different sections, and the cheats list says each cheat's nth.

Launching, reading and toggling go through the admin helper (one UAC prompt per session).
"""

import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from . import log

PORT = 47120
HEADER = "X-AutoCheatDeck"
_GAME = re.compile(r"^/api/games/([A-Za-z0-9._-]+)/(cheats|launch|read|toggle|overlay|value)$")


class Backend:
    """What the API needs from the app; App implements these."""

    def api_games(self) -> list[dict]:
        raise NotImplementedError

    def api_cheats(self, appid: str) -> dict | None:
        raise NotImplementedError

    def api_launch(self, appid: str) -> dict:
        raise NotImplementedError

    def api_overlay(self, appid: str) -> dict:
        raise NotImplementedError

    def api_read(self, appid: str) -> dict:
        raise NotImplementedError

    def api_toggle(self, appid: str, name: str, nth: int = 0) -> dict:
        raise NotImplementedError

    def api_set_value(self, appid: str, name: str, value: str, nth: int = 0) -> dict:
        raise NotImplementedError

    def api_activity(self) -> None:
        """Called on every request (e.g. to postpone an idle shutdown)."""


def _nth(body: dict) -> int:
    """Which of the cheats sharing a name (0 = the first); ValueError (400) if it isn't a small number."""
    nth = body.get("nth", 0)
    if not isinstance(nth, int) or isinstance(nth, bool) or not 0 <= nth < 1000:
        raise ValueError("Bad nth: {!r}".format(nth))
    return nth


def _handler(backend: Backend) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutoCheatDeck"

        def log_message(self, fmt, *args):  # keep stderr quiet; errors go to our log
            pass

        def _reply(self, status: int, body: object) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self) -> bool:
            log.get().info("api: %s %s", self.command, self.path)
            backend.api_activity()
            if self.headers.get(HEADER) != "1" or self.headers.get("Origin") is not None:
                self._reply(403, {"error": "forbidden"})
                return False
            return True

        def _body(self) -> dict:
            length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw or b"{}")

        def do_OPTIONS(self):  # never answer CORS preflights
            self._reply(403, {"error": "forbidden"})

        def do_GET(self):
            if not self._allowed():
                return
            try:
                if self.path == "/api/games":
                    return self._reply(200, {"games": backend.api_games()})
                match = _GAME.match(self.path)
                if match and match.group(2) == "cheats":
                    return self._reply(200, {"cheats": backend.api_cheats(match.group(1))})
                self._reply(404, {"error": "not found"})
            except Exception as e:  # report, don't kill the server
                log.get().exception("api: GET %s failed", self.path)
                self._reply(500, {"error": str(e)})

        def do_POST(self):
            if not self._allowed():
                return
            try:
                match = _GAME.match(self.path)
                if match and match.group(2) == "launch":
                    return self._reply(200, backend.api_launch(match.group(1)))
                if match and match.group(2) == "read":
                    return self._reply(200, backend.api_read(match.group(1)))
                if match and match.group(2) == "overlay":
                    return self._reply(200, backend.api_overlay(match.group(1)))
                if match and match.group(2) == "value":
                    body = self._body()
                    return self._reply(200, backend.api_set_value(match.group(1), str(body.get("name", "")),
                                                                  str(body.get("value", "")), _nth(body)))
                if match and match.group(2) == "toggle":
                    body = self._body()
                    return self._reply(200, backend.api_toggle(match.group(1), str(body.get("name", "")), _nth(body)))
                self._reply(404, {"error": "not found"})
            except ValueError as e:
                self._reply(400, {"error": str(e)})
            except Exception as e:  # report, don't kill the server
                log.get().exception("api: POST %s failed", self.path)
                self._reply(500, {"error": str(e)})

    return Handler


class Server:
    """Runs the API on its own thread; handle_request() blocks in accept() between requests."""

    def __init__(self, backend: Backend, port: int = PORT, on_error: Callable[[str], None] | None = None):
        self._stopping = False
        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(backend))
        except OSError as e:
            self._httpd = None
            log.get().warning("api: could not listen on 127.0.0.1:%s: %s", port, e)
            if on_error:
                on_error("Game Bar connection unavailable (port {} in use)".format(port))
            return
        self._httpd.timeout = None  # block until a connection arrives: no wakeups while idle
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._run, name="api", daemon=True).start()
        log.get().info("api: listening on 127.0.0.1:%s", self.port)

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def _run(self) -> None:
        while not self._stopping:
            self._httpd.handle_request()

    def stop(self) -> None:
        if not self._httpd:
            return
        self._stopping = True
        try:  # wake the blocked accept() so the thread can exit
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            pass
        self._httpd.server_close()
