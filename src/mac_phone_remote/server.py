"""Loopback-only HTTP service, intended to sit behind Tailscale Serve HTTPS."""

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


LOG = logging.getLogger(__name__)
WEB = Path(__file__).with_name("web")
MAX_BODY = 8192
MAX_HEADERS = 16384
MAX_CONNECTIONS = 16
INPUTS_PER_SECOND = 120
SESSION_LIFETIME = 60 * 60


def normalize_origin(value):
    if not isinstance(value, str) or not value.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError("Public URL must be an ASCII origin without whitespace or control characters.")
    url = urlsplit(value)
    if url.username or url.password or url.path not in ("", "/") or "?" in value or "#" in value:
        raise ValueError("Public URL must be an origin only, e.g. https://my-mac.example.ts.net")
    if not url.hostname or url.scheme not in ("http", "https"):
        raise ValueError("Public URL must use HTTPS (HTTP is only allowed on localhost).")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("Non-local access requires HTTPS. Use Tailscale Serve, not port forwarding.")
    try:
        port = url.port
    except ValueError as exc:
        raise ValueError("Public URL contains an invalid port.") from exc
    if port == 0:
        raise ValueError("Public URL must use a nonzero port.")
    if url.scheme == "https":
        labels = url.hostname.split(".")
        if not url.hostname.endswith(".ts.net") or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("HTTPS access must use your Tailscale Serve .ts.net hostname.")
    default_port = 443 if url.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{url.scheme}://{url.hostname}{suffix}"


@dataclass
class Session:
    expires: float
    identity: str
    inputs: deque = field(default_factory=deque)


class Auth:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.code = f"{secrets.randbelow(100_000_000):08d}"
        self.code_hash = hashlib.sha256(self.code.encode()).digest()
        self.code_expires = clock() + 600
        self.pairing_open = True
        self.sessions = {}
        self.attempts = deque()

    def pair(self, code, identity):
        with self.lock:
            now = self.clock()
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 5:
                return None, 429
            self.attempts.append(now)
            if not self.pairing_open or not identity:
                return None, 401
            if not isinstance(code, str) or len(code) != 8 or not code.isascii() or not code.isdigit() or now >= self.code_expires:
                return None, 401
            if not hmac.compare_digest(hashlib.sha256(code.encode()).digest(), self.code_hash):
                return None, 401
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).digest()] = Session(now + SESSION_LIFETIME, identity)
            self.pairing_open = False
            return token, 200

    def authorize(self, token, identity, *, input_request=False):
        if not token or len(token) != 43 or not token.isascii():
            return 401
        with self.lock:
            digest = hashlib.sha256(token.encode()).digest()
            session = self.sessions.get(digest)
            now = self.clock()
            if session is None:
                return 401
            if session.expires <= now:
                self.sessions.pop(digest, None)
                return 401
            if session.identity != identity:
                return 401
            if input_request:
                while session.inputs and session.inputs[0] <= now - 1:
                    session.inputs.popleft()
                if len(session.inputs) >= INPUTS_PER_SECOND:
                    return 429
                session.inputs.append(now)
            return 200

    def valid(self, token, identity):
        return self.authorize(token, identity) == 200

    def revoke(self, token):
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).digest(), None)


class RemoteServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    @property
    def cookie_name(self):
        return "__Host-remote_session" if self.secure else "remote_session"

    def __init__(self, address, controller, origin):
        if address[0] != "127.0.0.1":
            raise ValueError("The server must bind to 127.0.0.1. Use Tailscale Serve for network access.")
        self.origin = normalize_origin(origin)
        self.authority = urlsplit(self.origin).netloc
        self.secure = self.origin.startswith("https:")
        if not self.secure and not controller.demo:
            raise ValueError("Real Mac control requires a Tailscale HTTPS URL. Local HTTP is only available with --demo.")
        self.controller = controller
        self.auth = Auth()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            LOG.warning("Connection limit reached; request rejected.")
            # Do not parse or spawn another handler once all bounded slots are occupied.
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except (RuntimeError, OSError):
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "MacRemote"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def parse_request(self):
        if not super().parse_request():
            return False
        if sum(len(key) + len(value) + 4 for key, value in self.headers.raw_items()) > MAX_HEADERS:
            self.reply(431, {"error": "Request headers are too large."})
            return False
        for name in (
            "Host", "Origin", "Content-Length", "Content-Type", "Cookie",
            "X-Remote-Request", "Tailscale-User-Login", "Transfer-Encoding",
        ):
            if len(self.headers.get_all(name, [])) > 1:
                self.reply(400, {"error": f"Duplicate {name} headers are not allowed."})
                return False
        if self.headers.get("Transfer-Encoding") is not None:
            self.reply(400, {"error": "Transfer encoding is not supported."})
            return False
        if not self.path.startswith("/") or self.path.startswith("//") or "#" in self.path:
            self.reply(400, {"error": "An origin-form request target is required."})
            return False
        return True

    def log_message(self, fmt, *args):
        # Never log cookies, input text, pairing codes, or query strings.
        LOG.info("HTTP request handled.")

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
        morsel = cookie.get(self.server.cookie_name)
        return morsel.value if morsel else ""

    def cookie(self, token, age):
        secure = "; Secure" if self.server.secure else ""
        return f"{self.server.cookie_name}={token}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict{secure}"

    def check_host(self):
        host = self.headers.get("Host", "").lower()
        # Some reverse proxies forward the loopback upstream Host, others the public Host.
        local = f"127.0.0.1:{self.server.server_port}"
        if host not in {self.server.authority, local}:
            self.reply(403, {"error": "Unrecognized host."})
            return False
        if self.server.secure:
            identity = self.headers.get("Tailscale-User-Login", "")
            if not identity or len(identity) > 256 or not identity.isascii() or any(
                ord(c) < 33 or ord(c) == 127 for c in identity
            ):
                self.reply(403, {"error": "Tailscale user identity required. Use Serve, not Funnel or a public proxy."})
                return False
        return True

    def identity(self):
        if self.server.secure:
            return self.headers["Tailscale-User-Login"]
        return "local-demo"

    def do_GET(self):
        if not self.check_host():
            return
        if self.headers.get("Content-Length", "0") != "0":
            self.reply(400, {"error": "GET requests must not contain a body."})
            return
        path = self.path.partition("?")[0]
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
            if not self.server.auth.valid(self.token(), self.identity()):
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
        path = self.path.partition("?")[0]
        if path not in {"/api/pair", "/api/input", "/api/logout"}:
            self.reply(404, {"error": "Not found."})
            return
        token = self.token()
        if path != "/api/pair":
            status = self.server.auth.authorize(token, self.identity(), input_request=path == "/api/input")
            if status != 200:
                message = "Too many controls. Wait one second; the last action was not sent."
                if status == 401:
                    message = "Session expired or belongs to a different Tailscale user. Restart the Mac server and pair again."
                self.reply(status, {"error": message}, {"Retry-After": "1"} if status == 429 else None)
                return
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length or not raw_length.isascii() or not raw_length.isdigit():
            self.reply(400, {"error": "Invalid content length."})
            return
        if len(raw_length) > 4 or not 0 < int(raw_length) <= MAX_BODY:
            self.reply(413, {"error": "Request must contain 1 to 8192 bytes without transfer encoding."})
            return
        length = int(raw_length)
        try:
            raw_body = self.rfile.read(length)
        except TimeoutError:
            self.reply(408, {"error": "Request body timed out."})
            return
        if len(raw_body) != length:
            self.reply(400, {"error": "Incomplete request body."})
            return
        try:
            data = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            self.reply(400, {"error": "Invalid JSON."})
            return
        if not isinstance(data, dict):
            self.reply(400, {"error": "Expected a JSON object."})
            return
        if path == "/api/pair":
            token, status = self.server.auth.pair(data.get("code"), self.identity())
            if token:
                self.reply(200, {"ok": True}, {"Set-Cookie": self.cookie(token, SESSION_LIFETIME)})
            else:
                message = "Too many pairing attempts. Wait 60 seconds and retry."
                if status != 429:
                    message = "Invalid, used, or expired code. Restart the Mac server for a new single-use code."
                LOG.warning("Pairing rejected (%s)", status)
                self.reply(status, {"error": message}, {"Retry-After": "60"} if status == 429 else None)
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
