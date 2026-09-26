"""Offline tests for the parts that must never drift: the canonical hash rule,
the selectors, id resolution rules, and the tool result size cap."""

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger_tools as L  # noqa: E402
from aai import parse_jsonc  # noqa: E402

SAMPLE = {"agent": "REBALANCE", "action": "HOLD", "balances": {"usdc_units_6": 5, "link_wei_18": 7},
          "timestamp": "2026-09-26T21:54:52Z", "reason": "inside band"}


class CanonicalHash(unittest.TestCase):
    def test_matches_published_rule(self):
        rule = hashlib.sha256(json.dumps(SAMPLE, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(L.canonical_hash(SAMPLE), rule)

    def test_one_unit_changes_hash(self):
        forged = json.loads(json.dumps(SAMPLE))
        forged["balances"]["usdc_units_6"] += 1
        self.assertNotEqual(L.canonical_hash(SAMPLE), L.canonical_hash(forged))

    def test_key_order_does_not_matter(self):
        self.assertEqual(L.canonical_hash(dict(reversed(list(SAMPLE.items())))), L.canonical_hash(SAMPLE))


class Selectors(unittest.TestCase):
    def test_selectors_are_keccak(self):
        try:
            from web3 import Web3
        except ImportError:
            self.skipTest("web3 not installed; selectors are fixed constants")
        self.assertEqual(L.SEL_ATTESTED_AT, "0x" + Web3.keccak(text="attestedAt(bytes32)").hex().removeprefix("0x")[:8])
        self.assertEqual(L.SEL_ATTESTED_BY, "0x" + Web3.keccak(text="attestedBy(bytes32)").hex().removeprefix("0x")[:8])


class Resolve(unittest.TestCase):
    def test_rejects_unknown_agent(self):
        with self.assertRaises(L.LedgerError):
            L.resolve("latest", "YOLO")

    def test_rejects_non_hex(self):
        with self.assertRaises(L.LedgerError):
            L.resolve("zz")

    def test_spell(self):
        self.assertEqual(L.spell("ec4850"), "E C 4 8 5 0")


class ToolCap(unittest.TestCase):
    def test_unknown_tool_404(self):
        self.assertEqual(L.run_tool("nope", {})[0], 404)

    def test_oversized_result_is_trimmed(self):
        L.TOOLS["_big"] = lambda q: {"say": "short", "blob": "x" * 20000}
        try:
            status, out = L.run_tool("_big", {})
            self.assertEqual(status, 200)
            self.assertLess(len(json.dumps(out)), 8192)
            self.assertTrue(out["truncated"])
        finally:
            del L.TOOLS["_big"]


class AgentFile(unittest.TestCase):
    def test_agent_file_parses_and_has_all_tools(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents", "ask-the-ledger.jsonc")
        agent = parse_jsonc(open(path).read())
        names = {t["name"] for t in agent["tools"]}
        self.assertEqual(names, {"ledger_status", "agent_activity", "recent_trades", "explain_decision", "verify_receipt", "tamper_test"})
        for t in agent["tools"]:
            self.assertTrue(t["http"]["url"].startswith("${PUBLIC_BASE_URL}/tools/"))
            self.assertIn(t["http"]["url"].rsplit("/", 1)[1], L.TOOLS)


if __name__ == "__main__":
    unittest.main()
