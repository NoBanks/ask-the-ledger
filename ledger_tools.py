"""Voice-sized answers about the traide-arc-agents decision ledger.

The ledger is public: every decision three autonomous trading agents make on
Arc testnet is written as a hash-chained receipt, and every trade is anchored
on chain. It is also 35 MB of JSON, and a voice agent's tool result is capped
at 8 KiB. This module is the bridge: it reads the public ledger, answers one
question at a time, and re-proves any receipt from scratch (sha256 of the
canonical JSON, the link to the previous receipt, and eth_calls against the
anchor contract on Arc) so the agent can say "verified" and mean it.

Standard library only. Every number comes from a live request; nothing here
is cached for longer than LEDGER_TTL seconds.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

LEDGER_BASE = os.environ.get("LEDGER_BASE_URL", "https://arc-agents.nohumannearby.com").rstrip("/")
ARC_RPC = os.environ.get("ARC_RPC_URL", "https://rpc.testnet.arc.io")
LEDGER_TTL = 20            # seconds a recent-window read is reused
FULL_TTL = 600             # the whole ledger is 35 MB; only fetched for old ids
RECENT_WINDOW = 900        # rows read for "latest" questions (300 cycles x 3 agents)

AGENTS = ("REBALANCE", "AGGRESSIVE", "PASSIVE")
# From the strategy docstring in traide-arc-agents/arc_agents/agents.py. All
# three hold, with the reason recorded, when The Graph gives no usable signal.
AGENT_STYLE = {
    "REBALANCE": ("keeps a target split of its inventory between LINK and USDC, with the target set from "
                  "The Graph price tier, and trades back when it drifts more than 8 points; it cannot act without that price data"),
    "AGGRESSIVE": "trend follower: buys LINK when activity or price is rising, sells when falling, and sizes up with signal strength",
    "PASSIVE": "contrarian: trades only on a clear signal at the smallest size, buys when the market is quiet or falling, sells into strength",
}

# Function selectors on ArcReceiptAnchor, the first four bytes of
# keccak256 of the signature. hashlib has no keccak (sha3_256 is a different
# padding), so they are written out; tests/test_selectors.py re-derives them
# with web3 when it is installed.
SEL_ATTESTED_AT = "0x02888f23"   # attestedAt(bytes32) -> uint256
SEL_ATTESTED_BY = "0x5d34b11d"   # attestedBy(bytes32) -> address

HEX_ID = re.compile(r"^(0x)?[0-9a-f]{4,64}$")
UA = "ask-the-ledger/1.0 (+https://github.com/NoBanks/ask-the-ledger)"


class LedgerError(Exception):
    """Something upstream failed. The message is safe to speak."""


# --- fetching ---------------------------------------------------------------


def _get_json(url: str, timeout: float = 20) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        raise LedgerError(f"the ledger did not answer ({type(err).__name__})") from err


def _rpc(method: str, params: list) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(ARC_RPC, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            out = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        raise LedgerError(f"the Arc RPC did not answer ({type(err).__name__})") from err
    if "error" in out:
        raise LedgerError(f"the Arc RPC returned an error: {out['error'].get('message', 'unknown')}")
    return out.get("result")


class _Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float, load) -> Any:
        with self._lock:
            hit = self._store.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        value = load()
        with self._lock:
            self._store[key] = (time.time(), value)
        return value


_cache = _Cache()


def ledger(limit: int = RECENT_WINDOW, agent: str = "", traded: bool = False) -> dict:
    """One read of /ledger.json. Rows come oldest first; the last row is newest."""
    q = {"limit": str(limit)}
    if agent:
        q["agent"] = agent
    if traded:
        q["traded"] = "1"
    url = f"{LEDGER_BASE}/ledger.json?{urllib.parse.urlencode(q)}"
    return _cache.get(url, LEDGER_TTL if limit else FULL_TTL, lambda: _get_json(url, 60 if not limit else 20))


def integrity() -> dict:
    return _cache.get("verify", LEDGER_TTL, lambda: _get_json(f"{LEDGER_BASE}/api/verify"))


# --- formatting for speech ----------------------------------------------------


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def ago(ts: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    secs = int((now - _parse_ts(ts)).total_seconds())
    if secs < 90:
        return f"{max(secs, 1)} seconds ago"
    mins = secs // 60
    if mins < 90:
        return f"{mins} minutes ago"
    hours = mins // 60
    if hours < 36:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def short_id(receipt_hash: str) -> str:
    return receipt_hash[:6]


def spell(hex_id: str) -> str:
    """How to say a short id aloud: one character at a time."""
    return " ".join(hex_id.upper())


def _usdc(units6: int) -> float:
    return round(int(units6) / 1e6, 4)


def _link(wei18: int) -> float:
    return round(int(wei18) / 1e18, 4)


def action_words(action: str) -> str:
    return {"BUY_LINK": "bought LINK with USDC", "SELL_LINK": "sold LINK for USDC",
            "HOLD": "held"}.get(action, action.lower().replace("_", " "))


def pool_price(receipt: dict) -> Optional[float]:
    """USDC per LINK in the TRAIDE AMM pool at decision time, from reserves."""
    res = (receipt.get("pool") or {}).get("reserves") or {}
    link, usdc = res.get("link_wei_18"), res.get("usdc_units_6")
    if not link or not usdc:
        return None
    return round(_usdc(usdc) / _link(link), 6)


def summarize(row: dict, now: Optional[datetime] = None) -> dict:
    """One ledger row, reduced to what a person would ask about."""
    r = row["receipt"]
    swap = r.get("swap") or {}
    graph = r.get("graph") or {}
    out = {
        "id": short_id(row["receipt_hash"]),
        "id_spoken": spell(short_id(row["receipt_hash"])),
        "agent": r["agent"],
        "action": r["action"],
        "did": action_words(r["action"]),
        "when": r["timestamp"],
        "when_spoken": ago(r["timestamp"], now),
        "reason": r.get("reason", ""),
        "traded": bool(swap.get("hash")),
        "anchored": bool((row.get("anchor") or {}).get("hash")),
        "balances": {"usdc": _usdc(r["balances"]["usdc_units_6"]),
                     "link": _link(r["balances"]["link_wei_18"])},
    }
    if graph.get("tier"):
        out["graph_tier"] = graph["tier"]
    sig = graph.get("signal") or {}
    if "price_change" in sig:
        out["graph_price_change_pct"] = round(sig["price_change"] * 100, 2)
    if swap.get("hash"):
        out["swap"] = {"amount_in_usdc": _usdc(swap.get("amount_in", 0)),
                       "status": swap.get("status"), "block": swap.get("block")}
    return out


# --- resolving "which receipt" from a spoken request ---------------------------


def resolve(ref: str = "latest", agent: str = "") -> tuple[dict, Optional[dict], dict]:
    """Return (row, previous_row_or_None, ledger_meta) for a spoken reference.

    ref: "latest" (newest decision), "latest_trade" (newest swap), or a hex id
    prefix of at least 4 characters. agent narrows to one agent.
    """
    ref = (ref or "latest").strip().lower().replace(" ", "").replace("-", "")
    agent = (agent or "").strip().upper()
    if agent and agent not in AGENTS:
        raise LedgerError(f"there is no agent called {agent.title()}; the agents are Rebalance, Aggressive and Passive")

    if ref in ("latest", "last", "newest", "latestdecision"):
        doc = ledger(agent=agent)
        if not doc["rows"]:
            raise LedgerError("the ledger has no decisions yet")
        return doc["rows"][-1], _prev_of(doc["rows"][-1]), doc
    if ref in ("latesttrade", "lasttrade", "latest_trade", "trade"):
        doc = ledger(limit=5, agent=agent, traded=True)
        if not doc["rows"]:
            raise LedgerError("that agent has not made a trade yet")
        row = doc["rows"][-1]
        return row, _prev_of(row), doc

    ref = ref.replace("_", "")
    if not HEX_ID.match(ref):
        raise LedgerError("I need a receipt id: the first few letters and digits of its hash, or say latest, or latest trade")
    ref = ref[2:] if ref.startswith("0x") else ref
    if len(ref) == 64:
        try:
            row = _get_json(f"{LEDGER_BASE}/receipt/{ref}.json")
            return row, _prev_of(row), ledger(limit=1)
        except LedgerError:
            pass
    # Recent window first; the full ledger only when the id is old.
    for doc in (ledger(), ledger(limit=0)):
        matches = [row for row in doc["rows"] if row["receipt_hash"].startswith(ref)
                   and (not agent or row["receipt"]["agent"] == agent)]
        if len(matches) == 1:
            return matches[0], _prev_of(matches[0]), doc
        if len(matches) > 1:
            raise LedgerError(f"{len(matches)} receipts start with {spell(ref)}; read me a couple more characters")
    raise LedgerError(f"no receipt starts with {spell(ref)}")


def _prev_of(row: dict) -> Optional[dict]:
    prev = row.get("prev_receipt_hash")
    if not prev:
        return None
    try:
        return _get_json(f"{LEDGER_BASE}/receipt/{prev}.json")
    except LedgerError:
        return None


# --- verification ---------------------------------------------------------------


def canonical_hash(receipt: dict) -> str:
    """The traide-keeper rule, as published in ledger.json's canonical_rule."""
    return hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _call(to: str, selector: str, word_hex: str) -> str:
    return _rpc("eth_call", [{"to": to, "data": selector + word_hex}, "latest"])


def verify(row: dict, prev: Optional[dict], anchor_contract: str) -> dict:
    """Re-prove one receipt. Each check is PASS, FAIL or SKIP with a spoken reason."""
    checks: list[dict] = []

    def add(name: str, status: str, say: str, **extra: Any) -> None:
        checks.append({"check": name, "status": status, "say": say, **extra})

    recorded = row["receipt_hash"]
    recomputed = canonical_hash(row["receipt"])
    if recomputed == recorded:
        add("hash", "PASS", "the sha256 of the receipt matches its recorded hash, so nothing in it was edited")
    else:
        add("hash", "FAIL", "the receipt's contents do not hash to its recorded id")

    if prev is None:
        add("chain", "SKIP", "this is the first receipt, or its predecessor could not be fetched")
    elif prev.get("receipt_hash") == row.get("prev_receipt_hash") and canonical_hash(prev["receipt"]) == prev["receipt_hash"]:
        add("chain", "PASS", "it links to the receipt before it, which also hashes correctly, so no decision was removed in between")
    else:
        add("chain", "FAIL", "the link to the previous receipt is broken")

    anchor = row.get("anchor") or {}
    if not anchor.get("hash"):
        add("anchor", "SKIP", "hold decisions are hash chained but not anchored; only trades are written to the anchor contract")
    else:
        agent_addr = row["receipt"]["agent_address"].lower()
        at = int(_call(anchor_contract, SEL_ATTESTED_AT, recorded) or "0x0", 16)
        by_raw = _call(anchor_contract, SEL_ATTESTED_BY, recorded) or "0x"
        by = "0x" + by_raw[-40:].lower()
        if at and by == agent_addr:
            when = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            add("anchor", "PASS", f"the anchor contract on Arc says this hash was attested {ago(when)} by the agent's own wallet",
                attested_at=when)
        elif at:
            add("anchor", "FAIL", "the hash is on chain but was attested by a different address")
        else:
            add("anchor", "FAIL", "the anchor contract has no record of this hash")

    swap = (row["receipt"].get("swap") or {})
    if swap.get("hash"):
        rc = _rpc("eth_getTransactionReceipt", [swap["hash"]])
        amm = ((row["receipt"].get("pool") or {}).get("amm") or "").lower()
        if rc is None:
            # The public Arc RPC answers null for transactions it no longer
            # indexes (seen on 19-day-old swaps). Unknown is not a failure;
            # the anchor check above already proves the receipt on chain.
            add("swap", "SKIP", "the public Arc RPC no longer serves a transaction that old, so the swap itself was not re-read")
        else:
            ok = (rc.get("status") == "0x1"
                  and (rc.get("from") or "").lower() == row["receipt"]["agent_address"].lower()
                  and (not amm or (rc.get("to") or "").lower() == amm))
            add("swap", "PASS" if ok else "FAIL",
                "the swap transaction succeeded on Arc, sent from the agent's wallet to the TRAIDE AMM" if ok
                else "the swap transaction on Arc does not match the receipt",
                block=int(rc["blockNumber"], 16) if rc.get("blockNumber") else None)

    failed = [c for c in checks if c["status"] == "FAIL"]
    passed = [c for c in checks if c["status"] == "PASS"]
    verdict = "FAILED" if failed else "VERIFIED"
    return {"verdict": verdict, "passed": len(passed), "failed": len(failed), "checks": checks}


# --- the tools the voice agent calls ---------------------------------------------


def tool_status() -> dict:
    doc = ledger()
    ver = integrity()
    latest: dict[str, dict] = {}
    for row in reversed(doc["rows"]):
        a = row["receipt"]["agent"]
        if a not in latest:
            latest[a] = summarize(row)
    last_trade = ledger(limit=1, traded=True)["rows"]
    t = doc["totals"]
    say = (f"The ledger holds {t['receipts']:,} receipts, {t['swaps']:,} of them real swaps on Arc testnet, "
           f"and the hash chain {'checks out end to end' if ver.get('ok') else 'reports a problem'}. "
           f"Last decision {ago(t['last_decision_at'])}.")
    if last_trade:
        lt = summarize(last_trade[0])
        say += f" Last trade: {lt['agent'].title()} {lt['did']} {lt['when_spoken']}."
    # Each agent's OWN logged reason, verbatim. The model once merged two agents' reasons into one
    # ("both holding due to low USDC" when one was an RPC error); spelling each out prevents that.
    for a in AGENTS:
        if a in latest:
            say += f" {a.title()} {latest[a]['did']}: {latest[a]['reason']}."
    return {
        "say": say,
        "totals": t,
        "integrity_ok": ver.get("ok"),
        "chain": "Arc testnet (chain id 5042002)",
        "agents": {a: {"style": AGENT_STYLE[a], "latest": latest.get(a)} for a in AGENTS},
        "dashboard": LEDGER_BASE,
    }


def tool_agent(agent: str, count: int = 3) -> dict:
    agent = (agent or "").strip().upper()
    if agent not in AGENTS:
        raise LedgerError("pick one of Rebalance, Aggressive or Passive")
    count = max(1, min(int(count or 3), 5))
    rows = ledger(agent=agent)["rows"][-count:]
    items = [summarize(r) for r in reversed(rows)]
    trades = ledger(limit=1, agent=agent, traded=True)["rows"]
    out = {"agent": agent, "style": AGENT_STYLE[agent], "latest": items}
    if trades:
        out["last_trade"] = summarize(trades[-1])
    head = items[0]
    out["say"] = f"{agent.title()} {head['did']} {head['when_spoken']}: {head['reason']}."
    return out


def tool_trades(agent: str = "", count: int = 3) -> dict:
    agent = (agent or "").strip().upper()
    if agent and agent not in AGENTS:
        raise LedgerError("pick one of Rebalance, Aggressive or Passive, or leave the agent out")
    count = max(1, min(int(count or 3), 5))
    doc = ledger(limit=count, agent=agent, traded=True)
    items = [summarize(r) for r in reversed(doc["rows"])]
    if not items:
        return {"say": "No trades on record for that agent yet.", "trades": []}
    head = items[0]
    return {"say": f"Most recent trade: {head['agent'].title()} {head['did']} {head['when_spoken']}.",
            "trades": items}


def tool_explain(ref: str = "latest", agent: str = "") -> dict:
    row, _, doc = resolve(ref, agent)
    s = summarize(row)
    s["pool_price_usdc_per_link"] = pool_price(row["receipt"])
    s["agent_style"] = AGENT_STYLE.get(s["agent"], "")
    s["say"] = f"{s['agent'].title()} {s['did']} {s['when_spoken']} because: {s['reason']}."
    return s


def tool_verify(ref: str = "latest_trade", agent: str = "") -> dict:
    row, prev, doc = resolve(ref, agent)
    anchor_contract = doc.get("anchor_contract") or (row.get("anchor") or {}).get("anchor_contract", "")
    result = verify(row, prev, anchor_contract)
    s = summarize(row)
    lines = "; ".join(f"{c['check']} {c['status'].lower()}: {c['say']}" for c in result["checks"])
    result.update({
        "receipt": {k: s[k] for k in ("id", "id_spoken", "agent", "did", "when_spoken", "reason")},
        "say": f"{result['verdict'].title()}. {result['passed']} checks passed, {result['failed']} failed. {lines}.",
        "explorer": ((row.get("anchor") or {}).get("explorer") or ((row["receipt"].get("swap") or {}).get("explorer")) or ""),
    })
    return result


def tool_tamper_test(ref: str = "latest_trade", agent: str = "") -> dict:
    """Show what a forged receipt looks like: change one digit, re-hash, compare.
    Nothing is written anywhere; the edit exists only inside this request."""
    row, _, doc = resolve(ref, agent)
    forged = json.loads(json.dumps(row["receipt"]))
    usdc = int(forged["balances"]["usdc_units_6"])
    forged["balances"]["usdc_units_6"] = usdc + 1
    real, fake = canonical_hash(row["receipt"]), canonical_hash(forged)
    anchor_contract = doc.get("anchor_contract", "")
    forged_on_chain = bool(anchor_contract) and int(_call(anchor_contract, SEL_ATTESTED_AT, fake) or "0x0", 16) > 0
    return {
        "receipt": short_id(row["receipt_hash"]),
        "edit": f"USDC balance {usdc} to {usdc + 1} (one millionth of a dollar)",
        "original_hash_prefix": real[:12],
        "forged_hash_prefix": fake[:12],
        "matches_recorded": fake == row["receipt_hash"],
        "forged_hash_on_chain": forged_on_chain,
        "say": (f"I took receipt {spell(short_id(row['receipt_hash']))} and changed its USDC balance by one millionth of a dollar. "
                f"The real receipt hashes to {spell(real[:4])} and the forged one to {spell(fake[:4])}, so the forgery fails "
                "verification" + ("." if forged_on_chain else ", and the anchor contract on Arc has no record of the forged hash.")),
    }


TOOLS = {
    "tamper_test": lambda q: tool_tamper_test(q.get("ref", "latest_trade"), q.get("agent", "")),
    "status": lambda q: tool_status(),
    "agent": lambda q: tool_agent(q.get("agent", ""), q.get("count", 3)),
    "trades": lambda q: tool_trades(q.get("agent", ""), q.get("count", 3)),
    "explain": lambda q: tool_explain(q.get("ref", "latest"), q.get("agent", "")),
    "verify": lambda q: tool_verify(q.get("ref", "latest_trade"), q.get("agent", "")),
}


def run_tool(name: str, query: dict) -> tuple[int, dict]:
    """Every result stays under the 8 KiB tool-result cap."""
    if name not in TOOLS:
        return 404, {"error": f"no tool named {name}"}
    try:
        out = TOOLS[name](query)
    except LedgerError as err:
        return 200, {"error": str(err), "say": f"I could not get that: {err}."}
    body = json.dumps(out, separators=(",", ":"))
    if len(body) > 7800:
        out = {"say": out.get("say", ""), "truncated": True}
    return 200, out
