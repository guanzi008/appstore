from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

from ui.preferences import PreferenceStore, UIPreferences


class MCPNetworkTests(unittest.TestCase):
    def start_server(self, transport: str, preferences: Path | None = None) -> tuple[int, str]:
        env = os.environ.copy()
        env.pop(f"APPSTORE_MCP_{transport.upper()}_TOKEN", None)
        argv = [sys.executable, "-m", "appstore.mcp_server"]
        if preferences is not None:
            argv = [sys.executable, "-m", "ui.cpp_bridge", "mcp"]
            env["UTPUBLISHER_PREFERENCES_PATH"] = str(preferences)
        process = subprocess.Popen(
            [*argv, "--transport", transport, "--port", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )

        def stop():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)

        self.addCleanup(stop)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=5), "MCP listener did not start")
        line = process.stdout.readline().decode().strip()
        if not line:
            self.fail(process.communicate(timeout=5)[1].decode())
        url = urlsplit(line.rsplit(" ", 1)[1])
        self.assertEqual(url.hostname, "127.0.0.1")
        assert url.port is not None
        return url.port, url.path

    def request(self, port: int, path: str, payload: dict | None = None, headers: dict | None = None):
        with closing(HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
            connection.request(
                "POST" if payload is not None else "GET", path,
                json.dumps(payload).encode() if payload is not None else None,
                {
                    "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                    **(headers or {}),
                },
            )
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None

    def rpc(self, method: str, params: dict | None = None, *, request_id: int = 1) -> dict:
        return {"jsonrpc": "2.0", "method": method, "id": request_id, "params": params or {}}

    def test_streamable_http_legacy_handshake(self) -> None:
        port, path = self.start_server("http")
        self.assertEqual(path, "/mcp")
        status, init = self.request(port, path, self.rpc("initialize", {"protocolVersion": "2025-11-25"}))
        self.assertEqual(status, 200)
        self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
        headers = {"MCP-Protocol-Version": "2025-11-25"}
        self.assertEqual(self.request(port, path, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers), (202, None))
        status, listing = self.request(port, path, self.rpc("tools/list"), headers)
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["result"]["tools"]), 20)
        self.assertEqual(self.request(port, path), (405, None))

    def test_streamable_http_modern_header_validation(self) -> None:
        port, path = self.start_server("http")
        version = "2026-07-28"
        meta = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        headers = {"MCP-Protocol-Version": version, "Mcp-Method": "server/discover"}
        status, discovery = self.request(port, path, self.rpc("server/discover", {"_meta": meta}), headers)
        self.assertEqual(status, 200)
        self.assertIn(version, discovery["result"]["supportedVersions"])
        call = self.rpc("tools/call", {"_meta": meta, "name": "inspect_packages", "arguments": []})
        headers["Mcp-Method"] = "tools/call"
        status, error = self.request(port, path, call, {**headers, "Mcp-Name": "delete_draft"})
        self.assertEqual((status, error["error"]["code"]), (400, -32020))
        status, error = self.request(port, path, call, {**headers, "Mcp-Name": "inspect_packages"})
        self.assertEqual((status, error["error"]["code"]), (200, -32602))

    def test_managed_http_credentials_and_live_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferenceStore(Path(directory) / "preferences.json")
            store.save(UIPreferences(mcp_enabled=True, mcp_http_token="http-test"))
            port, path = self.start_server("http", store.path)
            request = self.rpc("tools/list")
            auth = {"Authorization": "Bearer http-test"}
            self.assertEqual(self.request(port, path, request), (401, None))
            self.assertEqual(self.request(port, path, request, {**auth, "Host": "elsewhere.invalid"}), (403, None))
            self.assertEqual(self.request(port, path, request, {**auth, "Origin": "https://elsewhere.invalid"}), (403, None))
            status, listing = self.request(port, path, request, auth)
            self.assertEqual(status, 200)
            self.assertEqual(len(listing["result"]["tools"]), 12)
            store.save(UIPreferences(mcp_enabled=True, mcp_allow_actions=True, mcp_http_token="http-test"))
            _, listing = self.request(port, path, request, auth)
            self.assertEqual(len(listing["result"]["tools"]), 20)

    def test_sse_endpoint_and_session_handshake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferenceStore(Path(directory) / "preferences.json")
            store.save(UIPreferences(mcp_enabled=True, mcp_sse_token="sse-test"))
            port, path = self.start_server("sse", store.path)
            self.assertEqual(path, "/sse")
            self.assertEqual(self.request(port, path), (401, None))
            auth = {"Authorization": "Bearer sse-test"}
            with closing(HTTPConnection("127.0.0.1", port, timeout=5)) as stream:
                stream.request("GET", path, headers={**auth, "Accept": "text/event-stream"})
                events = stream.getresponse()
                self.assertEqual(events.status, 200)
                self.assertEqual(events.readline().strip(), b"event: endpoint")
                endpoint = events.readline().decode().removeprefix("data: ").strip()
                self.assertEqual(events.readline(), b"\n")
                self.assertTrue(endpoint.startswith("/messages?session_id="))

                def exchange(request):
                    self.assertEqual(self.request(port, endpoint, request, auth), (202, None))
                    self.assertEqual(events.readline().strip(), b"event: message")
                    result = json.loads(events.readline().decode().removeprefix("data: "))
                    self.assertEqual(events.readline(), b"\n")
                    return result["result"]

                init = exchange(self.rpc("initialize", {"protocolVersion": "2024-11-05"}))
                self.assertEqual(init["protocolVersion"], "2024-11-05")
                tools = exchange(self.rpc("tools/list"))["tools"]
                self.assertEqual(len(tools), 12)
                self.assertNotIn("annotations", tools[0])
                self.assertEqual(self.request(port, "/messages?session_id=missing", self.rpc("ping"), auth), (404, None))
