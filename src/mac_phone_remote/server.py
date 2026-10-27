"""Loopback-only HTTP service, intended to sit behind Tailscale Serve HTTPS."""

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


LOG = logging.getLogger(__name__)
WEB = Path(__file__).with_name("web")
MAX_BODY = 8192
SESSION_LIFETIME = 12 * 60 * 60


def normalize_origin(value):
    url = urlsplit(value)
    if url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
        raise ValueError("Public URL must be an origin only, e.g. https://my-mac.example.ts.net")
    if not url.hostname or url.scheme not in ("http", "https"):
        raise ValueError("Public URL must use HTTPS (HTTP is only allowed on localhost).")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("Non-local access requires HTTPS. Use Tailscale Serve, not port forwarding.")
    try:
        url.port
    except ValueError as exc:
        raise ValueError("Public URL contains an invalid port.") from exc
    return f"{url.scheme}://{url.netloc.lower()}"


class Auth:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.code = f"{secrets.randbelow(100_000_000):08d}"
        self.code_hash = hashlib.sha256(self.code.encode()).digest()
        self.code_expires = clock() + 600
        self.sessions = {}
        self.attempts = deque()

    def pair(self, code):
        with self.lock:
            now = self.clock()
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 5:
                return None, 429
            self.attempts.append(now)
            if not isinstance(code, str) or len(code) != 8 or not code.isascii() or not code.isdigit() or now >= self.code_expires:
                return None, 401
            if not hmac.compare_digest(hashlib.sha256(code.encode()).digest(), self.code_hash):
                return None, 401
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            if len(self.sessions) >= 8:
                return None, 429
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).digest()] = now + SESSION_LIFETIME
            return token, 200

    def valid(self, token):
        if not token:
            return False
        with self.lock:
            digest = hashlib.sha256(token.encode()).digest()
            expiry = self.sessions.get(digest, 0)
            if expiry <= self.clock():
                self.sessions.pop(digest, None)
                return False
            return True

    def revoke(self, token):
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).digest(), None)


class RemoteServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, controller, origin):
        if address[0] != "127.0.0.1":
            raise ValueError("The server must bind to 127.0.0.1. Use Tailscale Serve for network access.")
        self.origin = normalize_origin(origin)
        self.authority = urlsplit(self.origin).netloc
        self.secure = self.origin.startswith("https:")
        self.controller = controller
        self.auth = Auth()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "MacRemote"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        # Never log cookies, input text, pairing codes, or query strings.
        LOG.info("HTTP response %s", args[1] if len(args) > 1 else "error")

    def reply(self, status, value, headers=None, content_type="application/json"):
        body = json.dumps(value).encode() if content_type == "application/json" else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; manifest-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        if self.server.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def token(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        morsel = cookie.get("remote_session")
        return morsel.value if morsel else ""

    def cookie(self, token, age):
        secure = "; Secure" if self.server.secure else ""
        return f"remote_session={token}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict{secure}"

    def check_host(self):
        host = self.headers.get("Host", "").lower()
        # Some reverse proxies forward the loopback upstream Host, others the public Host.
        local = f"127.0.0.1:{self.server.server_port}"
        if host not in {self.server.authority, local}:
            self.reply(403, {"error": "Unrecognized host."})
            return False
        return True

    def do_GET(self):
        if not self.check_host():
            return
        path = urlsplit(self.path).path
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/icon.svg": ("icon.svg", "image/svg+xml"),
        }
        if path in assets:
            name, mime = assets[path]
            self.reply(200, (WEB / name).read_bytes(), content_type=mime)
        elif path == "/api/state":
            if not self.server.auth.valid(self.token()):
                self.reply(401, {"error": "Pair this phone to continue."})
                return
            try:
                self.reply(200, self.server.controller.state())
            except RuntimeError as exc:
                LOG.error("Display enumeration failed: %s", exc)
                self.reply(503, {"error": str(exc)})
        else:
            self.reply(404, {"error": "Not found."})

    def do_POST(self):
        if not self.check_host():
            return
        if self.headers.get("Origin") != self.server.origin or self.headers.get("X-Remote-Request") != "1":
            self.reply(403, {"error": "Requests must come from the configured remote page."})
            return
        if self.headers.get_content_type() != "application/json":
            self.reply(415, {"error": "JSON is required."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reply(400, {"error": "Invalid content length."})
            return
        if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY:
            self.reply(413, {"error": "Request must contain 1 to 8192 bytes without transfer encoding."})
            return
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self.reply(400, {"error": "Invalid JSON."})
            return
        if not isinstance(data, dict):
            self.reply(400, {"error": "Expected a JSON object."})
            return
        path = urlsplit(self.path).path
        if path == "/api/pair":
            token, status = self.server.auth.pair(data.get("code"))
            if token:
                self.reply(200, {"ok": True}, {"Set-Cookie": self.cookie(token, SESSION_LIFETIME)})
            else:
                message = "Too many pairing attempts or devices. Wait 60 seconds and retry."
                if status != 429:
                    message = "Invalid or expired code. Codes expire after 10 minutes; restart the Mac server for a new one."
                LOG.warning("Pairing rejected (%s)", status)
                self.reply(status, {"error": message}, {"Retry-After": "60"} if status == 429 else None)
            return
        token = self.token()
        if not self.server.auth.valid(token):
            self.reply(401, {"error": "Session expired. Pair this phone again."})
            return
        if path == "/api/logout":
            self.server.auth.revoke(token)
            self.reply(200, {"ok": True}, {"Set-Cookie": self.cookie("", 0)})
        elif path == "/api/input":
            try:
                self.server.controller.execute(data)
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
                return
            except RuntimeError as exc:
                LOG.error("Mac input failed: %s", exc)
                self.reply(503, {"error": str(exc)})
                return
            self.reply(200, {"ok": True})
        else:
            self.reply(404, {"error": "Not found."})
