import http.client
import json
import unittest

from autocheatdeck import api


class FakeBackend(api.Backend):
    def __init__(self):
        self.toggled: list[str] = []
        self.launched: list[str] = []
        self.activity = 0

    def api_games(self):
        return [{"id": "1", "name": "Hades", "source": "Steam", "trainer": "T", "hasCheats": True}]

    def api_cheats(self, appid):
        return [{"key": "Num 1", "name": "Infinite HP", "section": "", "adjustable": False}] if appid == "1" else None

    def api_launch(self, appid):
        self.launched.append(appid)
        return {"started": True, "readingCheats": False}

    def api_read(self, appid):
        return {"cheats": []}

    def api_toggle(self, appid, name):
        if name != "Infinite HP":
            raise ValueError("No cheat named " + name)
        self.toggled.append(name)
        return {"on": True}

    def api_activity(self):
        self.activity += 1


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.server = api.Server(self.backend, port=0)
        self.addCleanup(self.server.stop)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        all_headers = {api.HEADER: "1", "Content-Type": "application/json"}
        all_headers.update(headers or {})
        all_headers = {k: v for k, v in all_headers.items() if v is not None}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=all_headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b"null")
        conn.close()
        return response.status, data

    def test_games_and_cheats(self):
        self.assertEqual(self.request("GET", "/api/games")[1]["games"][0]["name"], "Hades")
        self.assertEqual(self.request("GET", "/api/games/1/cheats")[1]["cheats"][0]["name"], "Infinite HP")
        self.assertEqual(self.request("GET", "/api/games/2/cheats")[1], {"cheats": None})

    def test_launch_and_toggle(self):
        self.assertEqual(self.request("POST", "/api/games/1/launch")[0], 200)
        self.assertEqual(self.backend.launched, ["1"])
        self.assertEqual(self.request("POST", "/api/games/1/toggle", {"name": "Infinite HP"}), (200, {"on": True}))
        self.assertEqual(self.request("POST", "/api/games/1/toggle", {"name": "Banana"})[0], 400)
        self.assertEqual(self.backend.toggled, ["Infinite HP"])
        self.assertEqual(self.request("POST", "/api/press", {"key": "Num 1"})[0], 404)  # gone: trainers ignore it

    def test_every_request_counts_as_activity(self):
        self.request("GET", "/api/games")
        self.request("GET", "/api/games/1/cheats")
        self.assertEqual(self.backend.activity, 2)

    def test_requests_without_the_header_are_refused(self):
        status, _ = self.request("POST", "/api/games/1/toggle", {"name": "Infinite HP"}, {api.HEADER: None})
        self.assertEqual(status, 403)
        self.assertEqual(self.backend.toggled, [])

    def test_browser_requests_are_refused(self):
        status, _ = self.request("POST", "/api/games/1/toggle", {"name": "Infinite HP"},
                                 {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("OPTIONS", "/api/games/1/toggle")[0], 403)
        self.assertEqual(self.backend.toggled, [])

    def test_unknown_paths(self):
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)
        self.assertEqual(self.request("POST", "/api/games/../../x/launch")[0], 404)

    def test_stop_ends_the_thread(self):
        self.server.stop()
        with self.assertRaises(OSError):
            http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2).request("GET", "/api/games")


class ProtocolTest(unittest.TestCase):
    def test_link_starts_the_app_in_widget_mode(self):
        from autocheatdeck import protocol

        self.assertEqual(protocol.command_for(r"C:\Apps\AutoCheatDeck\AutoCheatDeck.exe"),
                         r'"C:\Apps\AutoCheatDeck\AutoCheatDeck.exe" --widget "%1"')


if __name__ == "__main__":
    unittest.main()
