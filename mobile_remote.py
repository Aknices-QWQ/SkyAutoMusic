"""Small authenticated LAN bridge for the SkyAutoMusic mobile companion.

The desktop player remains the source of truth.  This module only exposes a
read-only state snapshot, the currently selected score, and a small allowlist
of player actions.  It deliberately uses the Python standard library so a
desktop release does not need another server dependency.
"""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import secrets
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse


PROTOCOL_VERSION = 1
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024
ACTION_NAMES = {
    "select",
    "start",
    "stop",
    "preview",
    "seek",
    "set_speed",
    "set_delay",
    "set_wrong",
}


def create_pairing_token() -> str:
    """Return a URL-safe token suitable for pairing one phone with one PC."""

    return secrets.token_urlsafe(24)


def local_ipv4_addresses() -> list[str]:
    """Return usable local IPv4 addresses for the pairing instructions."""

    addresses: set[str] = set()
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = entry[4][0]
            if address and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    if not addresses:
        try:
            address = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            address.connect(("192.0.2.1", 9))
            candidate = address.getsockname()[0]
            address.close()
            if candidate and not candidate.startswith("127."):
                addresses.add(candidate)
        except OSError:
            pass
    return sorted(addresses)


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class MobileRemoteServer:
    """Threaded HTTP server with an in-memory, lock-protected state cache."""

    def __init__(
        self,
        web_root: str | Path,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        token: str | None = None,
        on_action: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.web_root = Path(web_root).resolve()
        self.host = host
        self.port = int(port)
        self.token = str(token or create_pairing_token())
        self.on_action = on_action
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "app_version": "",
            "status": "未连接",
            "selected_file": None,
            "song_title": "",
            "progress": 0,
            "elapsed": "0:00",
            "duration": "0:00",
            "playing": False,
            "previewing": False,
            "speed": 1.0,
            "delay": 0,
            "wrong": 0,
            "current_keys": [],
            "library_count": 0,
            "score": None,
        }
        self._library: list[dict[str, Any]] = []
        self._server: _ReusableServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def bound_port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server else self.port

    def start(self) -> int:
        """Start the server and return the bound port.

        Port ``0`` is accepted for tests and development.  Production pairing
        uses the stable default port so the phone URL can be copied once.
        """

        if self._server is not None:
            return self.bound_port
        handler = self._handler_type()
        server = _ReusableServer((self.host, self.port), handler)
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="SkyAutoMusic-MobileRemote",
            daemon=True,
        )
        self._thread.start()
        return self.port

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def set_state(self, snapshot: dict[str, Any]) -> None:
        """Replace the state cache with JSON-safe values from the Qt thread."""

        if not isinstance(snapshot, dict):
            return
        try:
            # Round-trip through json to detach the HTTP thread from Qt-owned
            # mutable containers and to fail early on an accidental widget.
            detached = json.loads(json.dumps(snapshot, ensure_ascii=False))
        except (TypeError, ValueError):
            return
        library = detached.pop("library", None)
        with self._lock:
            self._state.update(detached)
            self._state["protocol_version"] = PROTOCOL_VERSION
            if isinstance(library, list):
                self._library = [item for item in library if isinstance(item, dict)]
                self._state["library_count"] = len(self._library)

    def state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state, ensure_ascii=False))

    def library_snapshot(self, query: str = "", limit: int = 80) -> dict[str, Any]:
        query = str(query or "").strip().casefold()
        try:
            limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 80
        with self._lock:
            matches = self._library
            if query:
                matches = [
                    item
                    for item in matches
                    if query in str(item.get("title", "")).casefold()
                    or query in str(item.get("filename", "")).casefold()
                ]
            return {
                "query": query,
                "total": len(matches),
                "items": json.loads(json.dumps(matches[:limit], ensure_ascii=False)),
            }

    def pairing_urls(self) -> list[str]:
        port = self.bound_port
        return [f"http://{address}:{port}/" for address in local_ipv4_addresses()]

    def _handler_type(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "SkyAutoMusicMobile/1"

            def log_message(self, *_args):
                return

            def _token(self) -> str:
                parsed = urlparse(self.path)
                query_token = parse_qs(parsed.query).get("token", [""])[0]
                header = self.headers.get("X-Sky-Token", "")
                auth = self.headers.get("Authorization", "")
                bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
                return str(header or bearer or query_token)

            def _authorized(self) -> bool:
                return bool(self._token()) and hmac.compare_digest(self._token(), bridge.token)

            def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
                raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sky-Token, Authorization")
                self.end_headers()
                self.wfile.write(raw)

            def _error(self, message: str, status: int) -> None:
                self._send_json({"error": message}, status)

            def do_OPTIONS(self):  # noqa: N802 - BaseHTTPRequestHandler API
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sky-Token, Authorization")
                self.end_headers()

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/"):
                    if not self._authorized():
                        self._error("需要有效的配对令牌", HTTPStatus.UNAUTHORIZED)
                        return
                    self._get_api(parsed)
                    return
                self._serve_file(parsed.path)

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if not self._authorized():
                    self._error("需要有效的配对令牌", HTTPStatus.UNAUTHORIZED)
                    return
                if parsed.path != "/api/action":
                    self._error("未知的接口", HTTPStatus.NOT_FOUND)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_BODY_BYTES:
                    self._error("请求内容无效", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._error("请求必须是 JSON", HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(payload, dict) or payload.get("action") not in ACTION_NAMES:
                    self._error("不支持的操作", HTTPStatus.BAD_REQUEST)
                    return
                action = dict(payload)
                try:
                    accepted = bridge.on_action(action) if bridge.on_action else False
                except Exception:
                    accepted = False
                if accepted is False:
                    self._error("桌面端未接受操作", HTTPStatus.CONFLICT)
                    return
                self._send_json({"accepted": True, "action": action["action"]}, HTTPStatus.ACCEPTED)

            def _get_api(self, parsed):
                query = parse_qs(parsed.query)
                if parsed.path == "/api/health":
                    self._send_json({"ok": True, "protocol_version": PROTOCOL_VERSION})
                elif parsed.path == "/api/state":
                    self._send_json(bridge.state_snapshot())
                elif parsed.path == "/api/library":
                    self._send_json(bridge.library_snapshot(query.get("q", [""])[0], query.get("limit", [80])[0]))
                elif parsed.path == "/api/score":
                    snapshot = bridge.state_snapshot()
                    requested = query.get("file", [""])[0]
                    score = snapshot.get("score")
                    if not score or (requested and requested != score.get("filename")):
                        self._error("只提供当前选中的曲谱", HTTPStatus.NOT_FOUND)
                    else:
                        self._send_json(score)
                elif parsed.path == "/api/manifest":
                    snapshot = bridge.state_snapshot()
                    self._send_json({
                        "protocol_version": PROTOCOL_VERSION,
                        "app_version": snapshot.get("app_version", ""),
                        "library_count": snapshot.get("library_count", 0),
                    })
                else:
                    self._error("未知的接口", HTTPStatus.NOT_FOUND)

            def _serve_file(self, request_path: str):
                relative = unquote(request_path.lstrip("/")) or "index.html"
                candidate = (bridge.web_root / relative).resolve()
                try:
                    candidate.relative_to(bridge.web_root)
                except ValueError:
                    self._error("资源路径无效", HTTPStatus.NOT_FOUND)
                    return
                if not candidate.is_file():
                    self._error("资源不存在", HTTPStatus.NOT_FOUND)
                    return
                try:
                    raw = candidate.read_bytes()
                except OSError:
                    self._error("资源读取失败", HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                if candidate.suffix.lower() in {".js", ".css", ".json", ".webmanifest"}:
                    content_type += "; charset=utf-8"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

        return Handler


def encode_pairing_payload(url: str, token: str) -> str:
    """Create a compact payload for future QR pairing without a QR dependency."""

    raw = json.dumps({"url": url, "token": token}, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
