import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

from mac_phone_remote.backend import DemoController
from mac_phone_remote.server import Auth, Handler, INPUTS_PER_SECOND, RemoteServer, SESSION_LIFETIME, normalize_origin


IDENTITY = "owner@example.com"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.auth = Auth(clock=lambda: self.now)

    def test_code_has_eight_digits(self):
        self.assertEqual(len(self.auth.code), 8)
        self.assertTrue(self.auth.code.isdigit())

    def test_pair_session_expiry_and_logout(self):
        token, status = self.auth.pair(self.auth.code, IDENTITY)
        self.assertEqual(status, 200)
        self.assertTrue(self.auth.valid(token, IDENTITY))
        self.now += SESSION_LIFETIME
        self.assertFalse(self.auth.valid(token, IDENTITY))

    def test_logout_revokes_token_without_reopening_pairing(self):
        token, _ = self.auth.pair(self.auth.code, IDENTITY)
        self.auth.revoke(token)
        self.assertFalse(self.auth.valid(token, IDENTITY))
        self.assertEqual(self.auth.pair(self.auth.code, IDENTITY), (None, 401))

    def test_wrong_code_and_types_rejected(self):
        wrong = "00000000" if self.auth.code != "00000000" else "11111111"
        for value in (None, 12345678, "x", wrong, "\ud800" * 8, "\uff11" * 8):
            self.now += 60
            with self.subTest(value=value):
                self.assertEqual(self.auth.pair(value, IDENTITY), (None, 401))

    def test_pair_code_expires_at_ten_minutes(self):
        self.now += 600
        self.assertEqual(self.auth.pair(self.auth.code, IDENTITY), (None, 401))

    def test_global_rate_limit_cannot_be_bypassed_by_valid_code(self):
        for _ in range(5):
            self.auth.pair("bad", IDENTITY)
        self.assertEqual(self.auth.pair(self.auth.code, IDENTITY), (None, 429))
        self.now += 60
        self.assertEqual(self.auth.pair(self.auth.code, IDENTITY)[1], 200)

    def test_code_is_single_use_across_identities(self):
        self.assertEqual(self.auth.pair(self.auth.code, IDENTITY)[1], 200)
        self.assertEqual(self.auth.pair(self.auth.code, IDENTITY), (None, 401))
        self.assertEqual(self.auth.pair(self.auth.code, "other@example.com"), (None, 401))
        self.assertEqual(len(self.auth.sessions), 1)

    def test_missing_identity_does_not_consume_code(self):
        self.assertEqual(self.auth.pair(self.auth.code, ""), (None, 401))
        self.assertEqual(self.auth.pair(self.auth.code, IDENTITY)[1], 200)

    def test_session_is_bound_to_tailscale_identity(self):
        token, _ = self.auth.pair(self.auth.code, IDENTITY)
        self.assertFalse(self.auth.valid(token, "other@example.com"))
        self.assertFalse(self.auth.valid(token, ""))
        self.assertTrue(self.auth.valid(token, IDENTITY))

    def test_input_rate_limit_recovers_without_extending_expiry(self):
        token, _ = self.auth.pair(self.auth.code, IDENTITY)
        for _ in range(INPUTS_PER_SECOND):
            self.assertEqual(self.auth.authorize(token, IDENTITY, input_request=True), 200)
        self.assertEqual(self.auth.authorize(token, IDENTITY, input_request=True), 429)
        self.assertTrue(self.auth.valid(token, IDENTITY))
        self.now += 1
        self.assertEqual(self.auth.authorize(token, IDENTITY, input_request=True), 200)
        self.now += SESSION_LIFETIME
        self.assertFalse(self.auth.valid(token, IDENTITY))

    def test_pairing_race_creates_only_one_session(self):
        barrier = threading.Barrier(2)
        results = []

        def pair():
            barrier.wait(timeout=5)
            results.append(self.auth.pair(self.auth.code, IDENTITY)[1])

        threads = [threading.Thread(target=pair) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, [200, 401])
        self.assertEqual(len(self.auth.sessions), 1)

    def test_only_hashed_sessions_stored(self):
        token, _ = self.auth.pair(self.auth.code, IDENTITY)
        self.assertNotIn(token, self.auth.sessions)
        self.assertFalse(self.auth.valid("invalid", IDENTITY))
        self.assertFalse(self.auth.valid("", IDENTITY))
        self.assertFalse(self.auth.valid("\ud800" * 43, IDENTITY))


class ConfigurationTests(unittest.TestCase):
    def test_request_log_does_not_include_client_content(self):
        with patch("mac_phone_remote.server.LOG.info") as log:
            Handler.log_message(Mock(), "code %d, message %s", 400, "/?private-input=example")
        log.assert_called_once_with("HTTP request handled.")

    def test_normalizes_origins(self):
        self.assertEqual(normalize_origin("https://My-Mac.tail.ts.net/"), "https://my-mac.tail.ts.net")
        self.assertEqual(normalize_origin("http://127.0.0.1:8765"), "http://127.0.0.1:8765")
        self.assertEqual(normalize_origin("https://mac.tail.ts.net:443"), "https://mac.tail.ts.net")
        self.assertEqual(normalize_origin("https://mac.tail.ts.net:8443"), "https://mac.tail.ts.net:8443")
        self.assertEqual(normalize_origin("http://localhost:80"), "http://localhost")

    def test_invalid_origins(self):
        for value in (
            "http://192.168.1.1:8765", "http://my-mac.tail.ts.net", "https://example.com/a",
            "https://example.com/?x=1", "https://user:pass@example.com", "ftp://example.com",
            "example.com", "https://example.com:bad", "https://example.com/#fragment",
            "https://public.example.com", "https://mac..tail.ts.net",
            "https://-mac.tail.ts.net", "https://mac.tail.ts.net:0",
            "https://mac.tail.ts.net\n", "https://mac.tail.ts.net/#",
            "https://mac.tail.ts.net/?", "https://mac.tail.ts.net.evil.example",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_origin(value)

    def test_cannot_bind_network_interfaces(self):
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            RemoteServer(("0.0.0.0", 8765), DemoController(), "https://mac.tail.ts.net")

    def test_real_control_cannot_start_over_local_http(self):
        with self.assertRaisesRegex(ValueError, "Real Mac control requires"):
            RemoteServer(("127.0.0.1", 0), Mock(demo=False), "http://127.0.0.1:8765")


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
        self.token, _ = self.server.auth.pair(self.server.auth.code, IDENTITY)

    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        default = {
            "Origin": self.server.origin,
            "X-Remote-Request": "1",
            "Content-Type": "application/json",
            "Cookie": f"{self.server.cookie_name}={self.token}",
            "Tailscale-User-Login": IDENTITY,
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
        self.server.auth = Auth()
        status, headers, _ = self.request("POST", "/api/pair", {"code": self.server.auth.code})
        self.assertEqual(status, 200)
        for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Max-Age=3600"):
            self.assertIn(flag, headers["Set-Cookie"])
        self.assertTrue(headers["Set-Cookie"].startswith("__Host-remote_session="))
        self.assertNotIn("Domain=", headers["Set-Cookie"])

    def test_http_local_cookie_omits_secure_for_preview(self):
        self.server.auth = Auth()
        with patch.object(self.server, "secure", False):
            _, headers, _ = self.request("POST", "/api/pair", {"code": self.server.auth.code})
            self.assertNotIn("; Secure", headers["Set-Cookie"])

    def test_missing_identity_blocks_even_assets_and_pairing(self):
        for method, path, body in (
            ("GET", "/", None), ("GET", "/api/state", None),
            ("POST", "/api/pair", {"code": self.server.auth.code}),
            ("POST", "/api/input", {"action": "key", "key": "space"}),
        ):
            with self.subTest(path=path):
                status, _, _ = self.request(method, path, body, {"Tailscale-User-Login": ""})
                self.assertEqual(status, 403)

    def test_different_identity_cannot_replay_session(self):
        status, _, _ = self.request("GET", "/api/state", headers={"Tailscale-User-Login": "other@example.com"})
        self.assertEqual(status, 401)
        self.assertEqual(self.request("GET", "/api/state")[0], 200)

    def test_identity_matching_preserves_the_proxy_value_exactly(self):
        status, _, _ = self.request("GET", "/api/state", headers={"Tailscale-User-Login": IDENTITY.upper()})
        self.assertEqual(status, 401)

    def test_pair_code_cannot_be_replayed(self):
        self.server.auth = Auth()
        code = self.server.auth.code
        self.assertEqual(self.request("POST", "/api/pair", {"code": code})[0], 200)
        self.assertEqual(self.request("POST", "/api/pair", {"code": code})[0], 401)

    def test_input_rate_limit_is_explicit(self):
        for _ in range(INPUTS_PER_SECOND):
            self.server.auth.authorize(self.token, IDENTITY, input_request=True)
        status, headers, body = self.request("POST", "/api/input", {"action": "key", "key": "space"})
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "1")
        self.assertIn(b"Too many controls", body)
        self.assertEqual(self.request("GET", "/api/state")[0], 200)

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

    def test_oversized_headers_rejected(self):
        self.assertEqual(self.request("GET", "/", headers={"X-Padding": "a" * 17000})[0], 431)

    def test_get_body_and_transfer_encoding_rejected(self):
        self.assertEqual(self.request("GET", "/", raw=b"{}")[0], 400)
        self.assertEqual(self.request("POST", "/api/input", raw=b"{}", headers={"Transfer-Encoding": "chunked"})[0], 400)

    def test_duplicate_security_headers_rejected(self):
        for name, value in (
            ("Host", "mac.tail.ts.net"), ("Origin", self.server.origin),
            ("Content-Length", "2"), ("Cookie", f"{self.server.cookie_name}={self.token}"),
            ("Tailscale-User-Login", IDENTITY), ("X-Remote-Request", "1"),
            ("Content-Type", "application/json"),
        ):
            with self.subTest(name=name):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
                try:
                    connection.putrequest("POST", "/api/input", skip_host=True)
                    for key, val in {
                        "Host": "mac.tail.ts.net", "Origin": self.server.origin,
                        "Content-Length": "2", "Content-Type": "application/json",
                        "Cookie": f"{self.server.cookie_name}={self.token}", "X-Remote-Request": "1",
                        "Tailscale-User-Login": IDENTITY,
                    }.items():
                        connection.putheader(key, val)
                    connection.putheader(name, value)
                    connection.endheaders(b"{}")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    response.read()
                finally:
                    connection.close()

    def test_signed_and_non_numeric_content_lengths_rejected(self):
        for value in ("-1", "+2", "two"):
            with self.subTest(value=value):
                self.assertEqual(self.request(
                    "POST", "/api/input", raw=b"{}", headers={"Content-Length": value},
                )[0], 400)

    def partial_body_request(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.putrequest("POST", "/api/input")
        for name, value in {
            "Origin": self.server.origin, "X-Remote-Request": "1",
            "Content-Type": "application/json", "Content-Length": "10",
            "Cookie": f"{self.server.cookie_name}={self.token}",
            "Tailscale-User-Login": IDENTITY,
        }.items():
            connection.putheader(name, value)
        connection.endheaders(b"{")
        return connection

    def test_incomplete_request_body_is_explicit(self):
        connection = self.partial_body_request()
        try:
            connection.sock.shutdown(socket.SHUT_WR)
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertIn(b"Incomplete", response.read())
        finally:
            connection.close()

    def test_request_body_timeout_is_explicit(self):
        original = Handler.setup

        def short_timeout(handler):
            original(handler)
            handler.connection.settimeout(0.1)

        with patch.object(Handler, "setup", short_timeout):
            connection = self.partial_body_request()
            try:
                response = connection.getresponse()
                self.assertEqual(response.status, 408)
                self.assertIn(b"timed out", response.read())
            finally:
                connection.close()

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


class ConnectionLimitTests(unittest.TestCase):
    def test_overload_closes_connection_without_creating_another_thread(self):
        with patch("mac_phone_remote.server.MAX_CONNECTIONS", 1):
            server = RemoteServer(("127.0.0.1", 0), DemoController(), "http://127.0.0.1:8765")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        slow = None
        try:
            slow = socket.create_connection(server.server_address, timeout=5)
            slow.sendall(b"GET / HTTP/1.1\r\n")
            deadline = time.monotonic() + 3
            while server._slots._value and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(server._slots._value, 0)
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                with self.assertRaises((OSError, http.client.RemoteDisconnected)):
                    connection.request("GET", "/")
                    connection.getresponse()
            finally:
                connection.close()
            slow.sendall(b"Host: 127.0.0.1:8765\r\n\r\n")
            response = http.client.HTTPResponse(slow)
            response.begin()
            self.assertEqual(response.status, 200)
            response.read()
            slow.close()
            slow = None
            deadline = time.monotonic() + 3
            while not server._slots._value and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(server._slots._value, 1)
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request("GET", "/", headers={"Host": "127.0.0.1:8765"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
            finally:
                connection.close()
        finally:
            if slow is not None:
                slow.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
