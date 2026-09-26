#!/usr/bin/env python3
"""Live probe (needs .env + network, not part of the offline suite): open a Voice Agent API
session bound to the published agent, inject a user message as text, ask for a reply, and print
every non-audio event. Shows whether a question produces the expected tool.call.

    python3.11 tests/live_tool_probe.py "Now prove the last trade is real."
"""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import websockets
from aai import load_env, mint_token


async def main(question: str) -> None:
    load_env()
    url = f"wss://agents.assemblyai.com/v1/ws?token={mint_token()}"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {"agent_id": os.environ["AGENT_ID"]}}))
        t0, asked = time.time(), False
        while time.time() - t0 < 60:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 20))
            except asyncio.TimeoutError:
                print("   (20 s with no event)"); break
            ty = m.get("type")
            if ty in ("reply.audio", "transcript.agent.delta"):
                continue
            detail = {k: v for k, v in m.items() if k not in ("type", "config")}
            print(f"{time.time() - t0:6.2f} {ty} {json.dumps(detail)[:300]}")
            if ty == "reply.done" and not asked:
                asked = True
                await ws.send(json.dumps({"type": "conversation.message", "role": "user", "content": question}))
                await ws.send(json.dumps({"type": "reply.create"}))
            elif ty == "transcript.agent" and asked and m.get("text"):
                break
        await ws.send(json.dumps({"type": "session.end"}))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Now prove the last trade is real."))
