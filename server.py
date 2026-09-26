#!/usr/bin/env python3
"""Ask The Ledger: the page, the session tokens, and the voice tools.

    python3 server.py            # PORT from .env, default 3021

  GET /                 the voice UI
  GET /token            a 60-second, single-use AssemblyAI session token
  GET /config           the published agent id
  GET /tools/<name>     the http tools AssemblyAI calls mid-conversation;
                        the page calls the same URLs to show the evidence
  GET /health           liveness
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aai import ROOT, ApiError, load_env, mint_token
from ledger_tools import run_tool

WEB = ROOT / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript"),
          "/mic-worklet.js": ("mic-worklet.js", "text/javascript"),
          "/og.png": ("og.png", "image/png")}

# Every session bills the key that minted its token, so tokens are rationed
# per visitor: TOKENS_PER_HOUR per IP, and each session capped at 10 minutes.
TOKENS_PER_HOUR = int(os.environ.get("TOKENS_PER_HOUR", "12"))
_minted: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _allow(ip: str) -> bool:
    now = time.time()
    with _lock:
        q = _minted[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= TOKENS_PER_HOUR:
            return False
        q.append(now)
        return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AskTheLedger/1.0"

    def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

    def _ip(self) -> str:
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path in STATIC:
            name, ctype = STATIC[path]
            f = WEB / name
            if f.exists():
                self._send(200, f.read_bytes(), ctype, "no-cache")
            else:
                self._send(404, b"not found", "text/plain")
            return
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path == "/config":
            self._json(200, {"agent_id": os.environ.get("AGENT_ID", "")})
            return
        if path == "/token":
            if not _allow(self._ip()):
                self._json(429, {"error": "Too many sessions from this address. Try again in a while."})
                return
            try:
                self._json(200, {"token": mint_token()})
            except ApiError as err:
                print("token:", err, flush=True)
                self._json(502, {"error": "Could not start a voice session."})
            return
        if path.startswith("/tools/"):
            query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            status, out = run_tool(path[len("/tools/"):], query)
            print(f"tool {path} {query} -> {status} {out.get('say', out.get('error', ''))[:120]}", flush=True)
            self._json(status, out)
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    load_env()
    port = int(os.environ.get("PORT", "3021"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Ask The Ledger on http://127.0.0.1:{port} (agent {os.environ.get('AGENT_ID') or 'not published'})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
