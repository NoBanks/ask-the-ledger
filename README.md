<!--
# CITATION: every ledger number this app speaks is read live at question time from
https://arc-agents.nohumannearby.com/ledger.json and /api/verify (public, read only), and every
on-chain check is a live eth_call or eth_getTransactionReceipt against https://rpc.testnet.arc.io
(Arc testnet, chain id 5042002, checkable on https://testnet.arcscan.app). Nothing is simulated,
projected or precomputed. Cache lifetime: 20 seconds for recent reads.
-->

# Ask The Ledger

**Live: https://ask-the-ledger.nohumannearby.com**

A voice agent that answers for autonomous trading agents, and proves every answer on chain while you listen.

Three autonomous agents (Rebalance, Aggressive, Passive) trade LINK against USDC on Arc testnet using live data from The Graph. Every decision they make, trade or hold, is written as a hash-chained receipt, and every trade is anchored on chain. The public ledger is at https://arc-agents.nohumannearby.com (source: https://github.com/NoBanks/traide-arc-agents).

That ledger is honest, but it is 35 MB of JSON. Nobody reads it. Ask The Ledger makes it something you can talk to:

- "What are the agents doing?" It reads the live ledger and answers with totals, integrity, and each agent's latest decision with its reason.
- "Why isn't Aggressive trading?" It reads that agent's recent receipts and says the reason it logged, in its own words.
- "Prove the last trade." It re-derives the receipt from scratch: it recomputes the sha256, checks the link to the previous receipt, calls the anchor contract on Arc to confirm the hash was attested by the agent's own wallet, and re-reads the swap transaction. It says the verdict, and the page shows every check.
- "Could someone fake one of these?" It changes one digit of a real receipt in memory, re-hashes it, and asks the anchor contract about the forged hash. The contract has no record of it.

The answers are not generated from memory. Every fact the agent speaks comes from a tool result, and every tool reads public data live.

## Why voice

Autonomous agents that move money need to be auditable by people who will not open a JSON file: a compliance lead, a fund partner, the founder on a walk. A receipt only builds trust if someone checks it. Voice turns "check the receipts" from a script you run into a question you ask.

## How it works

```
 browser mic ──PCM16 24 kHz──► AssemblyAI Voice Agent API ◄── stored agent (agents/ask-the-ledger.jsonc)
      ▲                         Universal-3 Pro STT, LLM,        system prompt, voice, keyterms,
      │                         turn taking, TTS                 six HTTP tools
      │ reply.audio, transcripts,          │
      │ tool.call events                   │ HTTP tool calls (server-side, by AssemblyAI)
      │                                    ▼
      └──── server.py (this repo) ── /tools/* ──► arc-agents.nohumannearby.com/ledger.json
            /token mints 60 s tokens               Arc testnet RPC: eth_call attestedAt / attestedBy,
            page calls /tools/* too                eth_getTransactionReceipt
            to render the evidence
```

- **Voice Agent API, one connection.** Speech-to-text, the model, turn taking, interruptions and the voice all come from AssemblyAI. The browser streams mic audio and plays `reply.audio`.
- **HTTP tools.** Six tools in the stored agent. AssemblyAI calls them server-side, so the same agent would work on a phone number. Each returns a compact JSON answer under the 8 KiB tool result cap, with a `say` field written for speech and short receipt ids spelled for reading aloud.
- **Keyterms** bias transcription toward the vocabulary: agent names, LINK, USDC, Arc, The Graph, receipt, anchor.
- **Evidence board.** The page listens for `tool.call` events and fetches the same tool URL, so what the agent heard is what you see: verdicts, PASS/FAIL/SKIP chips per check, and Arcscan links.
- **The API key never reaches the browser.** `server.py` mints single-use 60 second tokens, rationed per visitor, and each session is capped at 10 minutes.

## Verification, exactly

For a receipt, `verify_receipt` runs these checks and reports each one:

| check | what it does |
| --- | --- |
| hash | `sha256(json.dumps(receipt, sort_keys=True, separators=(',',':')))` equals the recorded receipt hash (the rule the ledger publishes) |
| chain | the receipt's `prev_receipt_hash` is the previous receipt, which also hashes correctly |
| anchor | `attestedAt(hash)` on the ArcReceiptAnchor contract is nonzero and `attestedBy(hash)` is the agent's own wallet (trades only; holds are hash chained, not anchored) |
| swap | the swap transaction succeeded, was sent by the agent, to the TRAIDE AMM. SKIP when the public RPC no longer serves a transaction that old |

A SKIP is never counted as a pass. It is said out loud with its reason.

## Run it

Python 3.9+, standard library only.

```sh
cp .env.example .env          # add ASSEMBLYAI_API_KEY and your public https URL
python3 publish.py            # creates the agent on your AssemblyAI account, saves AGENT_ID
python3 server.py             # http://127.0.0.1:3021
python3 -m unittest discover -s tests
```

HTTP tools must be reachable on a public https host, so expose `server.py` (the live deployment uses a Cloudflare tunnel).

## Honest scope

- Arc testnet only. No real funds, no users, no mainnet.
- The agents and the ledger are a separate project (traide-arc-agents); this repo only reads their public data.
- If the agents are holding, the voice agent says so and reads out why. On 2026-09-26 the tools reported that the last trade was two days earlier and that Aggressive had no spendable USDC, because that is what the receipts said.

## Files

```
agents/ask-the-ledger.jsonc   the stored agent: prompt, voice, keyterms, tools
ledger_tools.py               the six tools and the verifier
server.py                     page, tokens, tool endpoints
aai.py                        AssemblyAI Voice Agent API calls, .env, JSONC
publish.py                    POST or PUT the agent
web/                          UI, mic capture worklet (24 kHz PCM16)
tests/                        offline tests
```

MIT licensed.
