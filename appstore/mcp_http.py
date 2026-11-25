from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from secrets import compare_digest, token_urlsafe
from threading import Lock
from urllib.parse import parse_qs


DEFAULT_MCP_HTTP_PORT = 18765
DEFAULT_MCP_SSE_PORT = 18766
_HTTP_ENDPOINT = "/mcp"
_SSE_ENDPOINT = "/sse"
_SSE_MESSAGES = "/messages"
_MAX_REQUEST_BYTES = 1024 * 1024
_Dispatch = Callable[[bytes, HTTPMessage], tuple[HTTPStatus, dict | list | None]]


@dataclass
class _SseSession:
    dispatch: _Dispatch
    messages: Queue[bytes] = field(default_factory=lambda: Queue(maxsize=32))


def _encode_json(payload: dict | list) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class _MCPNetworkServer(ThreadingHTTPServer):
    def __init__(self, transport: str, port: int, token: str, make_dispatch: Callable[[], _Dispatch]) -> None:
        self.authorization = f"Bearer {token}".encode("ascii") if token else b""
        self.make_dispatch = make_dispatch
        self.dispatch_lock = Lock()
        self.sessions: dict[str, _SseSession] = {}
        self.sessions_lock = Lock()
        handler = _MCPSseHandler if transport == "sse" else _MCPHttpHandler
        super().__init__(("127.0.0.1", port), handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


class _MCPNetworkHandler(BaseHTTPRequestHandler):
    server: _MCPNetworkServer
    protocol_version = "HTTP/1.1"

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        self.close_connection = True
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update({"127.0.0.1", "localhost"})
        host = self.headers.get_all("Host", [])
        origin = self.headers.get_all("Origin", [])
        if len(host) != 1 or host[0].lower() not in hosts or (
            origin and (len(origin) != 1 or origin[0].lower() not in {f"http://{item}" for item in hosts})
        ):
            self._reply(HTTPStatus.FORBIDDEN)
            return False
        if self.server.authorization:
            auth = self.headers.get_all("Authorization", [])
            if len(auth) != 1 or not compare_digest(auth[0].encode("latin-1"), self.server.authorization):
                self._reply(HTTPStatus.UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
                return False
        return True

    def _read_json(self) -> bytes | None:
        if self.headers.get_content_type() != "application/json":
            self._reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return None
        lengths = self.headers.get_all("Content-Length", [])
        length_text = lengths[0].strip() if len(lengths) == 1 else ""
        if not length_text.isdecimal() or self.headers.get("Transfer-Encoding") is not None:
            self._reply(HTTPStatus.BAD_REQUEST)
            return None
        if len(length_text) > 7 or int(length_text) > _MAX_REQUEST_BYTES:
            self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        length = int(length_text)
        try:
            body = self.rfile.read(length)
        except TimeoutError:
            self._reply(HTTPStatus.REQUEST_TIMEOUT)
            return None
        if len(body) != length:
            self._reply(HTTPStatus.BAD_REQUEST)
            return None
        return body

    def _reply(self, status: HTTPStatus, payload: dict | list | None = None, *, headers: dict[str, str] | None = None) -> None:
        body = _encode_json(payload) if payload is not None else b""
        try:
            self.send_response(status)
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if body:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def log_message(self, format: str, *args) -> None:
        pass


class _MCPHttpHandler(_MCPNetworkHandler):
    def do_POST(self) -> None:
        if self.path != _HTTP_ENDPOINT:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        body = self._read_json()
        if body is None:
            return
        # Some tools run the CLI in-process and redirect its output.
        with self.server.dispatch_lock:
            status, response = self.server.make_dispatch()(body, self.headers)
        self._reply(status, response)

    def do_GET(self) -> None:
        if self.path == _HTTP_ENDPOINT:
            self._reply(HTTPStatus.METHOD_NOT_ALLOWED, headers={"Allow": "POST"})
        else:
            self._reply(HTTPStatus.NOT_FOUND)

    do_HEAD = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET


class _MCPSseHandler(_MCPNetworkHandler):
    def do_GET(self) -> None:
        if self.path != _SSE_ENDPOINT:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        session_id = token_urlsafe(24)
        session = _SseSession(self.server.make_dispatch())
        with self.server.sessions_lock:
            self.server.sessions[session_id] = session
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            destination = f"{_SSE_MESSAGES}?session_id={session_id}"
            self.wfile.write(f"event: endpoint\ndata: {destination}\n\n".encode("ascii"))
            while True:
                try:
                    message = session.messages.get(timeout=10)
                except Empty:
                    message = b": keep-alive\n\n"
                self.wfile.write(message)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            with self.server.sessions_lock:
                self.server.sessions.pop(session_id, None)

    def do_POST(self) -> None:
        path, _, query = self.path.partition("?")
        if path != _SSE_MESSAGES:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        session_ids = parse_qs(query).get("session_id", [])
        with self.server.sessions_lock:
            session = self.server.sessions.get(session_ids[0]) if len(session_ids) == 1 else None
        if session is None:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        body = self._read_json()
        if body is None:
            return
        with self.server.dispatch_lock:
            with self.server.sessions_lock:
                if session_ids[0] not in self.server.sessions:
                    self._reply(HTTPStatus.NOT_FOUND)
                    return
            if session.messages.full():
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            status, response = session.dispatch(body, self.headers)
            if status == HTTPStatus.OK and response is not None:
                session.messages.put_nowait(b"event: message\ndata: " + _encode_json(response) + b"\n\n")
        self._reply(HTTPStatus.ACCEPTED if status == HTTPStatus.OK else status, response if status >= 400 else None)


def serve_network(transport: str, port: int, token: str, make_dispatch: Callable[[], _Dispatch]) -> int:
    endpoint = _SSE_ENDPOINT if transport == "sse" else _HTTP_ENDPOINT
    with _MCPNetworkServer(transport, port, token, make_dispatch) as server:
        print(f"MCP {transport.upper()} listening on http://127.0.0.1:{server.server_port}{endpoint}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
