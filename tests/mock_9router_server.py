#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mock 9router server for the test suite.

Mirrors the endpoints and payload shapes that 9router-manager uses, including the
auth rules of the real server:

  * x-9r-cli-token  -> sha256(machine-id + "9r-cli-auth" + auth/cli-secret)[:16]
  * cookie auth_token -> after POST /api/auth/login {"password": ...}
  * x-9r-peer-token -> compared against --peer-token

Run standalone:

    python3 tests/mock_9router_server.py --data-dir /tmp/x --port 0
"""

import argparse
import hashlib
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_PASSWORD = "mock-password"
PEER_TOKEN = "mock-peer-token"


def derive_token(data_dir: Path):
    machine_id_file = data_dir / "machine-id"
    cli_secret_file = data_dir / "auth" / "cli-secret"
    try:
        machine_id = machine_id_file.read_text(encoding="utf-8").strip()
        cli_secret = cli_secret_file.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not machine_id or not cli_secret:
        return None
    digest = hashlib.sha256((machine_id + "9r-cli-auth" + cli_secret).encode("utf-8")).hexdigest()
    return digest[:16]


class State:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.password = DEFAULT_PASSWORD
        self.nodes = []
        self.connections = []
        self.custom_models = []
        self.disabled = {}
        self.aliases = {}
        self.keys = []
        self.combos = []
        self.settings = {"cloudEnabled": False}
        self.imports = 0
        self.test_calls = []
        self.lock = threading.Lock()

    # -- helpers --------------------------------------------------------
    def node(self, node_id):
        for node in self.nodes:
            if node["id"] == node_id:
                return node
        return None

    def connection(self, connection_id):
        for connection in self.connections:
            if connection["id"] == connection_id:
                return connection
        return None

    def new_id(self, prefix):
        return "%s-%s" % (prefix, uuid.uuid4().hex[:8])

    def sanitized_connection(self, connection):
        return {k: v for k, v in connection.items() if k != "apiKey"}

    def model_catalog(self):
        catalog = []
        providers = {}
        for connection in self.connections:
            providers.setdefault(connection["provider"], True)
        for provider in providers:
            catalog.append({"provider": provider, "model": "mock-model", "name": "Mock Model",
                            "fullModel": "%s/mock-model" % provider,
                            "routedModel": "%s/mock-model" % provider, "alias": "mock-model",
                            "caps": {"vision": True, "reasoning": True}})
        for model in self.custom_models:
            provider = model["providerAlias"]
            catalog.append({"provider": provider, "model": model["id"], "name": model.get("name"),
                            "fullModel": "%s/%s" % (provider, model["id"]),
                            "routedModel": "%s/%s" % (provider, model["id"]),
                            "alias": model["id"], "caps": model.get("caps") or {}})
        return catalog

    def export(self):
        return {
            "settings": self.settings,
            "providerConnections": list(self.connections),
            "providerNodes": list(self.nodes),
            "proxyPools": [],
            "apiKeys": list(self.keys),
            "combos": list(self.combos),
            "modelAliases": dict(self.aliases),
            "customModels": list(self.custom_models),
            "mitmAlias": {},
            "pricing": {},
        }

    def import_payload(self, payload):
        self.settings = payload.get("settings", self.settings)
        self.connections = list(payload.get("providerConnections") or [])
        self.nodes = list(payload.get("providerNodes") or [])
        self.keys = list(payload.get("apiKeys") or [])
        self.combos = list(payload.get("combos") or [])
        self.aliases = dict(payload.get("modelAliases") or {})
        self.custom_models = list(payload.get("customModels") or [])
        self.imports += 1


class Handler(BaseHTTPRequestHandler):
    server_version = "mock-9router/1.0"
    state = None  # type: State
    verbose = False

    # -- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):
        if self.verbose:
            sys.stderr.write("[mock] " + (fmt % args) + "\n")

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _send(self, status, payload, cookies=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not self.state.require_auth:
            return True
        token = self.headers.get("x-9r-cli-token")
        expected = derive_token(self.state.data_dir)
        if token and expected and token == expected:
            return True
        if self.headers.get("x-9r-peer-token") == PEER_TOKEN:
            return True
        cookie = self.headers.get("Cookie") or ""
        if "auth_token=mock-session" in cookie:
            return True
        return False

    # -- routing --------------------------------------------------------
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        state = self.state

        # public endpoints
        if path == "/api/health":
            return self._send(200, {"ok": True})
        if path == "/api/version":
            return self._send(200, {"currentVersion": "0.0.0-mock", "latestVersion": "0.0.0-mock",
                                    "hasUpdate": False})
        if path == "/api/auth/status":
            return self._send(200, {"requireLogin": True, "authMode": "password"})
        if path == "/api/auth/login" and method == "POST":
            body = self._read_json()
            if body.get("password") == state.password:
                return self._send(200, {"success": True},
                                  cookies=["auth_token=mock-session; Path=/; HttpOnly"])
            return self._send(401, {"error": "Invalid password"})

        # test-only introspection
        if path == "/__state":
            with state.lock:
                return self._send(200, {
                    "nodes": state.nodes,
                    "connections": state.connections,
                    "customModels": state.custom_models,
                    "disabled": state.disabled,
                    "aliases": state.aliases,
                    "keys": state.keys,
                    "combos": state.combos,
                    "imports": state.imports,
                    "testCalls": state.test_calls,
                })

        if not self._authorized():
            return self._send(401, {"error": "Unauthorized"})

        with state.lock:
            # provider nodes -------------------------------------------------
            if path == "/api/provider-nodes" and method == "GET":
                return self._send(200, {"nodes": state.nodes})
            if path == "/api/provider-nodes" and method == "POST":
                body = self._read_json()
                if not (body.get("name") or "").strip():
                    return self._send(400, {"error": "Name is required"})
                if not (body.get("prefix") or "").strip():
                    return self._send(400, {"error": "Prefix is required"})
                node_type = body.get("type") or "openai-compatible"
                if node_type == "openai-compatible" and body.get("apiType") not in ("chat", "responses"):
                    return self._send(400, {"error": "Invalid OpenAI compatible API type"})
                node = {
                    "id": state.new_id("openai-compatible-chat" if node_type == "openai-compatible"
                                       else node_type),
                    "type": node_type,
                    "name": body["name"].strip(),
                    "prefix": body["prefix"].strip(),
                    "baseUrl": (body.get("baseUrl") or "https://api.openai.com/v1").strip(),
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                }
                if node_type == "openai-compatible":
                    node["apiType"] = body["apiType"]
                else:
                    base_url = node["baseUrl"].rstrip("/")
                    if node_type == "anthropic-compatible" and base_url.endswith("/messages"):
                        base_url = base_url[:-len("/messages")]
                    if node_type == "custom-embedding" and base_url.endswith("/embeddings"):
                        base_url = base_url[:-len("/embeddings")]
                    node["baseUrl"] = base_url
                state.nodes.append(node)
                return self._send(201, {"node": node})
            if path.startswith("/api/provider-nodes/"):
                node_id = path.rsplit("/", 1)[-1]
                node = state.node(node_id)
                if node is None:
                    return self._send(404, {"error": "Provider node not found"})
                if method == "PUT":
                    body = self._read_json()
                    # the real route validates all three fields together
                    if not (body.get("name") or "").strip():
                        return self._send(400, {"error": "Name is required"})
                    if not (body.get("prefix") or "").strip():
                        return self._send(400, {"error": "Prefix is required"})
                    if not (body.get("baseUrl") or "").strip():
                        return self._send(400, {"error": "Base URL is required"})
                    if node.get("type") == "openai-compatible":
                        if body.get("apiType") not in ("chat", "responses"):
                            return self._send(400, {"error": "Invalid OpenAI compatible API type"})
                        node["apiType"] = body["apiType"]
                    node["name"] = body["name"].strip()
                    node["prefix"] = body["prefix"].strip()
                    base_url = body["baseUrl"].strip()
                    if node.get("type") == "anthropic-compatible":
                        base_url = base_url.rstrip("/")
                        if base_url.endswith("/messages"):
                            base_url = base_url[:-len("/messages")]
                    if node.get("type") == "custom-embedding":
                        base_url = base_url.rstrip("/")
                        if base_url.endswith("/embeddings"):
                            base_url = base_url[:-len("/embeddings")]
                    node["baseUrl"] = base_url
                    node["updatedAt"] = "2026-01-01T00:00:01.000Z"
                    for connection in state.connections:
                        if connection["provider"] == node_id:
                            connection.setdefault("providerSpecificData", {})
                            connection["providerSpecificData"].update({
                                "prefix": node["prefix"],
                                "baseUrl": node["baseUrl"],
                                "apiType": node.get("apiType"),
                                "nodeName": node["name"],
                            })
                    return self._send(200, {"node": node})
                if method == "DELETE":
                    state.nodes = [n for n in state.nodes if n["id"] != node_id]
                    state.connections = [c for c in state.connections if c["provider"] != node_id]
                    state.custom_models = [m for m in state.custom_models
                                           if m["providerAlias"] != node_id]
                    return self._send(200, {"success": True})

            # provider connections ------------------------------------------
            if path == "/api/providers" and method == "GET":
                return self._send(200, {"connections": [state.sanitized_connection(c)
                                                        for c in state.connections]})
            if path == "/api/providers" and method == "POST":
                body = self._read_json()
                provider = body.get("provider")
                if not provider:
                    return self._send(400, {"error": "Invalid provider"})
                if body.get("apiKey") is None:
                    return self._send(400, {"error": "API Key is required"})
                node = state.node(provider)
                connection = {
                    "id": state.new_id("conn"),
                    "provider": provider,
                    "authType": "apikey",
                    "name": body.get("name") or provider,
                    "email": None,
                    "priority": body.get("priority", 1),
                    "isActive": bool(body.get("isActive", True)),
                    "apiKey": body.get("apiKey"),
                    "testStatus": None,
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                }
                if node is not None:
                    connection["providerSpecificData"] = {
                        "prefix": node.get("prefix"),
                        "baseUrl": node.get("baseUrl"),
                        "nodeName": node.get("name"),
                    }
                state.connections.append(connection)
                return self._send(201, {"success": True,
                                        "connection": state.sanitized_connection(connection)})
            if path.startswith("/api/providers/"):
                parts = path.split("/")
                connection_id = parts[3]
                connection = state.connection(connection_id)
                if connection is None:
                    return self._send(404, {"error": "Connection not found"})
                if len(parts) == 5 and parts[4] == "test" and method == "POST":
                    state.test_calls.append(connection_id)
                    connection["testStatus"] = "available"
                    return self._send(200, {"ok": True, "provider": connection["provider"],
                                            "status": 200, "latencyMs": 12})
                if len(parts) == 5 and parts[4] == "models" and method == "GET":
                    return self._send(200, {"provider": connection["provider"],
                                            "connectionId": connection_id,
                                            "models": [{"id": "mock-model-1", "name": "Mock 1"},
                                                       {"id": "mock-model-2", "name": "Mock 2"}]})
                if len(parts) == 4 and method == "PUT":
                    body = self._read_json()
                    for field in ("name", "priority", "isActive", "apiKey", "defaultModel"):
                        if field in body:
                            connection[field] = body[field]
                    connection["updatedAt"] = "2026-01-01T00:00:02.000Z"
                    return self._send(200, {"connection": state.sanitized_connection(connection)})
                if len(parts) == 4 and method == "DELETE":
                    state.connections = [c for c in state.connections if c["id"] != connection_id]
                    return self._send(200, {"message": "Connection deleted successfully"})

            # models --------------------------------------------------------
            if path == "/api/models" and method == "GET":
                return self._send(200, {"models": state.model_catalog()})
            if path == "/api/models/custom":
                if method == "GET":
                    return self._send(200, {"models": state.custom_models})
                if method == "POST":
                    body = self._read_json()
                    alias = body.get("providerAlias")
                    model_id = body.get("id")
                    if not alias or not model_id:
                        return self._send(400, {"error": "providerAlias and id required"})
                    kind = body.get("type") or "llm"
                    existing = None
                    for model in state.custom_models:
                        if (model["providerAlias"], model["id"], model.get("type", "llm")) == \
                                (alias, model_id, kind):
                            existing = model
                            break
                    if existing is not None:
                        if body.get("name"):
                            existing["name"] = body["name"]
                        if body.get("caps"):
                            existing["caps"] = body["caps"]
                        return self._send(200, {"success": True, "added": False})
                    state.custom_models.append({
                        "providerAlias": alias, "id": model_id, "type": kind,
                        "name": body.get("name") or model_id, "caps": body.get("caps"),
                    })
                    return self._send(200, {"success": True, "added": True})
                if method == "DELETE":
                    alias = query.get("providerAlias")
                    model_id = query.get("id")
                    kind = query.get("type") or "llm"
                    state.custom_models = [
                        m for m in state.custom_models
                        if not (m["providerAlias"] == alias and m["id"] == model_id
                                and m.get("type", "llm") == kind)]
                    return self._send(200, {"success": True})
            if path == "/api/models/disabled":
                if method == "GET":
                    return self._send(200, {"disabled": state.disabled})
                if method == "POST":
                    body = self._read_json()
                    alias = body.get("providerAlias")
                    ids = body.get("ids")
                    if not alias or not isinstance(ids, list):
                        return self._send(400, {"error": "providerAlias and ids[] required"})
                    state.disabled.setdefault(alias, [])
                    for model_id in ids:
                        if model_id not in state.disabled[alias]:
                            state.disabled[alias].append(model_id)
                    return self._send(200, {"success": True})
                if method == "DELETE":
                    alias = query.get("providerAlias")
                    model_id = query.get("id")
                    if not alias:
                        return self._send(400, {"error": "providerAlias required"})
                    if model_id:
                        state.disabled[alias] = [m for m in state.disabled.get(alias, [])
                                                 if m != model_id]
                    else:
                        state.disabled.pop(alias, None)
                    return self._send(200, {"success": True})
            if path == "/api/models/alias":
                if method == "GET":
                    return self._send(200, {"aliases": state.aliases})
                if method == "PUT":
                    body = self._read_json()
                    if not body.get("model") or not body.get("alias"):
                        return self._send(400, {"error": "Model and alias required"})
                    state.aliases[body["alias"]] = body["model"]
                    return self._send(200, {"success": True, "model": body["model"],
                                            "alias": body["alias"]})
                if method == "DELETE":
                    alias = query.get("alias")
                    if not alias:
                        return self._send(400, {"error": "Alias required"})
                    state.aliases.pop(alias, None)
                    return self._send(200, {"success": True})
            if path == "/api/models/test" and method == "POST":
                body = self._read_json()
                if not body.get("model"):
                    return self._send(400, {"error": "Model required"})
                return self._send(200, {"ok": True, "model": body["model"], "latencyMs": 7})

            # api keys ------------------------------------------------------
            if path == "/api/keys" and method == "GET":
                return self._send(200, {"keys": state.keys})
            if path == "/api/keys" and method == "POST":
                body = self._read_json()
                if not body.get("name"):
                    return self._send(400, {"error": "Name is required"})
                key = {"id": state.new_id("key"), "name": body["name"],
                       "key": "sk-mock-%s" % uuid.uuid4().hex[:12], "machineId": "mock-machine",
                       "isActive": True, "createdAt": "2026-01-01T00:00:00.000Z"}
                state.keys.append(key)
                return self._send(201, {"key": key["key"], "name": key["name"], "id": key["id"],
                                        "machineId": key["machineId"]})
            if path.startswith("/api/keys/"):
                key_id = path.rsplit("/", 1)[-1]
                target = None
                for key in state.keys:
                    if key["id"] == key_id:
                        target = key
                        break
                if target is None:
                    return self._send(404, {"error": "Key not found"})
                if method == "PUT":
                    body = self._read_json()
                    if "isActive" in body:
                        target["isActive"] = bool(body["isActive"])
                    return self._send(200, {"key": target})
                if method == "DELETE":
                    state.keys = [k for k in state.keys if k["id"] != key_id]
                    return self._send(200, {"success": True})

            # combos --------------------------------------------------------
            if path == "/api/combos" and method == "GET":
                return self._send(200, {"combos": state.combos})
            if path == "/api/combos" and method == "POST":
                body = self._read_json()
                if not body.get("name"):
                    return self._send(400, {"error": "Name is required"})
                combo = {"id": state.new_id("combo"), "name": body["name"],
                         "kind": body.get("kind"), "models": body.get("models") or [],
                         "createdAt": "2026-01-01T00:00:00.000Z",
                         "updatedAt": "2026-01-01T00:00:00.000Z"}
                state.combos.append(combo)
                return self._send(201, combo)
            if path.startswith("/api/combos/"):
                combo_id = path.rsplit("/", 1)[-1]
                if method == "DELETE":
                    state.combos = [c for c in state.combos if c["id"] != combo_id]
                    return self._send(200, {"success": True})

            # settings / database -------------------------------------------
            if path == "/api/settings/database":
                if method == "GET":
                    return self._send(200, state.export())
                if method == "POST":
                    body = self._read_json()
                    state.import_payload(body)
                    return self._send(200, {"success": True})
            if path == "/api/settings" and method == "GET":
                return self._send(200, state.settings)

        return self._send(404, {"error": "Not found: %s %s" % (method, path)})


class MockNineRouter(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, data_dir, require_auth=True, verbose=False):
        self.state = State(Path(data_dir))
        self.state.require_auth = require_auth
        handler = type("BoundHandler", (Handler,), {"state": self.state, "verbose": verbose})
        ThreadingHTTPServer.__init__(self, address, handler)

    @property
    def port(self):
        return self.server_address[1]


def seed_data_dir(data_dir: Path, with_secrets=True):
    data_dir = Path(data_dir)
    (data_dir / "auth").mkdir(parents=True, exist_ok=True)
    (data_dir / "machine-id").write_text("mock-machine-id\n", encoding="utf-8")
    if with_secrets:
        (data_dir / "auth" / "cli-secret").write_text("a" * 64 + "\n", encoding="utf-8")
    return data_dir


def main():
    parser = argparse.ArgumentParser(description="mock 9router server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20128)
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR") or ".mock-9router")
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    seed_data_dir(Path(args.data_dir))
    server = MockNineRouter((args.host, args.port), args.data_dir,
                            require_auth=not args.no_auth, verbose=args.verbose)
    print("mock 9router listening on http://%s:%d" % (args.host, server.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
