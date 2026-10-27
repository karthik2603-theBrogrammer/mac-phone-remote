import http.client
import json
import threading
import unittest
from unittest.mock import patch

from mac_phone_remote.backend import DemoController
from mac_phone_remote.server import Auth, RemoteServer, SESSION_LIFETIME, normalize_origin


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.auth = Auth(clock=lambda: self.now)

    def test_code_has_eight_digits(self):
        self.assertEqual(len(self.auth.code), 8)
        self.assertTrue(self.auth.code.isdigit())

    def test_pair_session_expiry_and_logout(self):
        token, status = self.auth.pair(self.auth.code)
        self.assertEqual(status, 200)
        self.assertTrue(self.auth.valid(token))
        self.auth.revoke(token)
        self.assertFalse(self.auth.valid(token))
        token, _ = self.auth.pair(self.auth.code)
        self.now += SESSION_LIFETIME
        self.assertFalse(self.auth.valid(token))

    def test_wrong_code_and_types_rejected(self):
        wrong = "00000000" if self.auth.code != "00000000" else "11111111"
        for value in (None, 12345678, "x", wrong, "\ud800" * 8, "\uff11" * 8):
            self.now += 60
            with self.subTest(value=value):
                self.assertEqual(self.auth.pair(value), (None, 401))

    def test_pair_code_expires_at_ten_minutes(self):
        self.now += 600
        self.assertEqual(self.auth.pair(self.auth.code), (None, 401))

    def test_global_rate_limit_cannot_be_bypassed_by_valid_code(self):
        for _ in range(5):
            self.auth.pair("bad")
        self.assertEqual(self.auth.pair(self.auth.code), (None, 429))
        self.now += 60
        self.assertEqual(self.auth.pair(self.auth.code)[1], 200)

    def test_eight_device_limit(self):
        for _ in range(8):
            self.now += 15
            self.assertEqual(self.auth.pair(self.auth.code)[1], 200)
        self.assertEqual(self.auth.pair(self.auth.code), (None, 429))

    def test_only_hashed_sessions_stored(self):
        token, _ = self.auth.pair(self.auth.code)
        self.assertNotIn(token, self.auth.sessions)
        self.assertFalse(self.auth.valid("invalid"))
        self.assertFalse(self.auth.valid(""))


class ConfigurationTests(unittest.TestCase):
    def test_normalizes_origins(self):
        self.assertEqual(normalize_origin("https://My-Mac.tail.ts.net/"), "https://my-mac.tail.ts.net")
        self.assertEqual(normalize_origin("http://127.0.0.1:8765"), "http://127.0.0.1:8765")

    def test_invalid_origins(self):
        for value in (
            "http://192.168.1.1:8765", "http://my-mac.tail.ts.net", "https://example.com/a",
            "https://example.com/?x=1", "https://user:pass@example.com", "ftp://example.com",
            "example.com", "https://example.com:bad", "https://example.com/#fragment",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_origin(value)

    def test_cannot_bind_network_interfaces(self):
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            RemoteServer(("0.0.0.0", 8765), DemoController(), "https://mac.tail.ts.net")


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = DemoController()
        cls.server = RemoteServer(("127.0.0.1", 0), cls.backend, "https://mac.tail.ts.net")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.server.auth = Auth()
        self.token, _ = self.server.auth.pair(self.server.auth.code)

    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        default = {
            "Origin": self.server.origin,
            "X-Remote-Request": "1",
            "Content-Type": "application/json",
            "Cookie": f"remote_session={self.token}",
        }
        default.update(headers or {})
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        try:
            connection.request(method, path, body=data, headers=default)
            response = connection.getresponse()
            status, received = response.status, dict(response.getheaders())
            payload = response.read()
            return status, received, payload
        finally:
            connection.close()

    def test_assets_and_headers(self):
        for path, mime in (("/", "text/html"), ("/app.css", "text/css"), ("/app.js", "text/javascript"), ("/icon.svg", "image/svg+xml")):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertTrue(headers["Content-Type"].startswith(mime))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_unpaired_phone_cannot_read_state_or_control(self):
        for method, path, body in (("GET", "/api/state", None), ("POST", "/api/input", {"action": "key", "key": "space"})):
            with self.subTest(path=path):
                status, _, _ = self.request(method, path, body, {"Cookie": ""})
                self.assertEqual(status, 401)

    def test_pair_sets_secure_httponly_strict_cookie(self):
        status, headers, _ = self.request("POST", "/api/pair", {"code": self.server.auth.code})
        self.assertEqual(status, 200)
        for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Max-Age=43200"):
            self.assertIn(flag, headers["Set-Cookie"])

    def test_http_local_cookie_omits_secure_for_preview(self):
        with patch.object(self.server, "secure", False):
            _, headers, _ = self.request("POST", "/api/pair", {"code": self.server.auth.code})
            self.assertNotIn("; Secure", headers["Set-Cookie"])

    def test_state_and_all_input_actions(self):
        status, _, payload = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(payload)["displays"]), 2)
        for action in (
            {"action": "focus"}, {"action": "move", "dx": 2, "dy": 3},
            {"action": "click", "button": "left"},
            {"action": "scroll", "dx": 0, "dy": 10},
            {"action": "text", "text": "hello"},
            {"action": "key", "key": "space"},
            {"action": "shortcut", "key": "copy"},
        ):
            with self.subTest(action=action):
                status, _, payload = self.request("POST", "/api/input", {"display": "2", **action})
                self.assertEqual(status, 200, payload)

    def test_cross_origin_and_missing_request_header_rejected(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": ""}, {"X-Remote-Request": ""}):
            with self.subTest(headers=headers):
                status, _, _ = self.request("POST", "/api/input", {"action": "key", "key": "space"}, headers)
                self.assertEqual(status, 403)

    def test_host_validation_and_public_proxy_host(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": "mac.tail.ts.net"})[0], 200)

    def test_invalid_requests(self):
        for raw, headers, expected in (
            (b"{", {}, 400), (b"[]", {}, 400), (b'{"x":1}', {"Content-Type": "text/plain"}, 415),
            (b"x" * 8193, {}, 413), (b'{"action":"unknown"}', {}, 400),
            (b'{"action":"move","display":"1","dx":NaN,"dy":0}', {}, 400),
            (b'{"action":[]}', {}, 400),
            (b"[" * 1500 + b"]" * 1500, {}, 400),
        ):
            with self.subTest(raw=raw[:50], headers=headers):
                self.assertEqual(self.request("POST", "/api/input", raw=raw, headers=headers)[0], expected)

    def test_logout_revokes_session(self):
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/state")[0], 401)

    def test_errors_are_explicit(self):
        with patch.object(self.backend, "state", side_effect=RuntimeError("Display unavailable")):
            status, _, body = self.request("GET", "/api/state")
            self.assertEqual(status, 503)
            self.assertIn(b"Display unavailable", body)
        with patch.object(self.backend, "execute", side_effect=RuntimeError("Permission revoked")):
            status, _, body = self.request("POST", "/api/input", {"action": "click"})
            self.assertEqual(status, 503)
            self.assertIn(b"Permission revoked", body)

    def test_no_file_traversal_or_arbitrary_endpoint(self):
        for path in ("/../pyproject.toml", "/api/screenshot", "/api/shell", "/unknown"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
