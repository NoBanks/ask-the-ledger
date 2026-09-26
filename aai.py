"""The small slice of the AssemblyAI Voice Agent API this app needs, plus .env
and JSONC loading. Standard library only.

API reference: https://www.assemblyai.com/docs/voice-agents/voice-agent-api
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
AGENTS_API = os.environ.get("AAI_AGENTS_API", "https://agents.assemblyai.com/v1")


class ApiError(Exception):
    pass


def load_env(path: Path = ENV_FILE) -> None:
    """KEY=value lines. Values already in the environment win."""
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if not m or line.lstrip().startswith("#"):
            continue
        os.environ.setdefault(m.group(1), re.sub(r"^(['\"])(.*)\1$", r"\2", m.group(2)))


def save_env(key: str, value: str, path: Path = ENV_FILE) -> None:
    os.environ[key] = value
    text = path.read_text() if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(f"{key}={value}", text, count=1)
    else:
        text += ("" if not text or text.endswith("\n") else "\n") + f"{key}={value}\n"
    path.write_text(text)


def parse_jsonc(text: str) -> Any:
    """JSON with // and /* */ comments. Strings are respected."""
    out, i, n = [], 0, len(text)
    in_str = esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def substitute(value: Any) -> Any:
    """${VAR} from the environment, recursively. An unset variable is an error."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            if m.group(1) not in os.environ:
                raise ApiError(f"{m.group(1)} is not set in .env")
            return os.environ[m.group(1)]
        return re.sub(r"\$\{([A-Z0-9_]+)\}", repl, value)
    if isinstance(value, list):
        return [substitute(v) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v) for k, v in value.items()}
    return value


def call(path: str, method: str = "GET", body: Any = None) -> Any:
    key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not key:
        raise ApiError("ASSEMBLYAI_API_KEY is not set")
    req = urllib.request.Request(
        AGENTS_API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as err:
        # The body explains validation failures; it never contains the key.
        raise ApiError(f"{method} {path}: HTTP {err.code} {err.read().decode()[:600]}") from err
    except urllib.error.URLError as err:
        raise ApiError(f"{method} {path}: {err.reason}") from err
    return json.loads(text) if text else {}


def mint_token(expires_in: int = 60, max_session: int = 600) -> str:
    """A single-use token for one browser session. The API key stays here."""
    out = call(f"/token?expires_in_seconds={expires_in}&max_session_duration_seconds={max_session}")
    return out["token"]
