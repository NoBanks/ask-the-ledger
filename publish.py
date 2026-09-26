#!/usr/bin/env python3
"""Publish agents/ask-the-ledger.jsonc to your AssemblyAI account.

    python3 publish.py

First run creates the agent (POST /v1/agents) and saves AGENT_ID to .env.
Later runs update it in place (PUT /v1/agents/{id}).
"""

import os
import sys

from aai import ROOT, ApiError, call, load_env, parse_jsonc, save_env, substitute


def main() -> int:
    load_env()
    agent = substitute(parse_jsonc((ROOT / "agents" / "ask-the-ledger.jsonc").read_text()))
    agent_id = os.environ.get("AGENT_ID", "")
    try:
        if agent_id:
            try:
                call(f"/agents/{agent_id}", "PUT", agent)
                print(f"Updated agent {agent_id}")
                return 0
            except ApiError as err:
                if "HTTP 404" not in str(err):
                    raise
        out = call("/agents", "POST", agent)
    except ApiError as err:
        print(err, file=sys.stderr)
        return 1
    save_env("AGENT_ID", out["id"])
    print(f"Created agent {out['id']} (saved to .env)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
