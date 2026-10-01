import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mobile_remote import MobileRemoteServer


class MobileRemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.actions = []
        self.server = MobileRemoteServer(
            Path(self.temp_dir.name),
            host="127.0.0.1",
            port=0,
            token="phone-pairing-test-token",
            on_action=self.actions.append,
        )
        self.port = self.server.start()
        self.addCleanup(self.server.stop)
        self.base_url = f"http://127.0.0.1:{self.port}"

    def request(self, path, *, token=None, method="GET", body=None):
        headers = {}
        data = None
        if token is not None:
            headers["X-Sky-Token"] = token
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        return urlopen(request, timeout=3)

    def test_api_requires_pairing_token(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/state")
        self.assertEqual(error.exception.code, 401)
        error.exception.close()

        response = self.request("/api/state", token="phone-pairing-test-token")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read().decode("utf-8"))["protocol_version"], 1)

    def test_authenticated_actions_are_forwarded(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/action", method="POST", body={"action": "start"})
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        self.assertEqual(self.actions, [])

        response = self.request(
            "/api/action",
            token="phone-pairing-test-token",
            method="POST",
            body={"action": "select", "filename": "Song.json"},
        )
        self.assertEqual(response.status, 202)
        self.assertEqual(self.actions, [{"action": "select", "filename": "Song.json"}])

    def test_state_score_and_web_fallback_are_available(self):
        Path(self.temp_dir.name, "index.html").write_text("SkyAutoMusic", encoding="utf-8")
        self.server.set_state({
            "song_title": "测试曲",
            "score": {"filename": "test.json", "title": "测试曲", "bpm": 100, "notes": []},
        })
        score = self.request("/api/score", token="phone-pairing-test-token")
        self.assertEqual(json.loads(score.read().decode("utf-8"))["title"], "测试曲")
        page = self.request("/", token=None)
        self.assertEqual(page.status, 200)
        self.assertIn("SkyAutoMusic", page.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
