"""Evidence must remain stable after caller mutation and fail closed on export."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from research_bot.v58.ledger import EvidenceLedger


class LedgerIntegrityTests(unittest.TestCase):
    def append(self, ledger, payload):
        return ledger.append(
            event_id="event-1", stage="SNAPSHOT", status="OK", payload=payload,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_append_detaches_nested_caller_payload(self):
        ledger = EvidenceLedger()
        payload = {"nested": {"values": [1, {"price": 100.0}]}}
        self.append(ledger, payload)
        payload["nested"]["values"][1]["price"] = 999.0
        self.assertTrue(ledger.verify())
        self.assertEqual(ledger.entries[0].payload["nested"]["values"][1]["price"], 100.0)

    def test_returned_entry_cannot_mutate_stored_ledger(self):
        ledger = EvidenceLedger()
        entry = self.append(ledger, {"nested": {"values": [1, 2]}})
        entry.payload["nested"]["values"].append(3)
        self.assertTrue(ledger.verify())
        self.assertEqual(ledger.entries[0].payload["nested"]["values"], [1, 2])

    def test_entries_view_cannot_mutate_stored_ledger(self):
        ledger = EvidenceLedger()
        self.append(ledger, {"nested": {"values": [1, 2]}})
        ledger.entries[0].payload["nested"]["values"][0] = 999
        self.assertTrue(ledger.verify())
        self.assertEqual(ledger.entries[0].payload["nested"]["values"], [1, 2])

    def test_invalid_json_payload_does_not_append_or_break_chain(self):
        for invalid in (float("nan"), float("inf"), -float("inf"), {1, 2}, object()):
            with self.subTest(invalid=type(invalid).__name__):
                ledger = EvidenceLedger()
                self.append(ledger, {"valid": 1})
                with self.assertRaises(ValueError):
                    self.append(ledger, {"nested": [invalid]})
                self.assertEqual(len(ledger.entries), 1)
                self.assertTrue(ledger.verify())
        ledger = EvidenceLedger()
        cyclic = {}
        cyclic["self"] = cyclic
        with self.assertRaises(ValueError):
            self.append(ledger, cyclic)
        self.assertEqual(ledger.entries, ())

    def test_corrupted_ledger_cannot_replace_existing_export(self):
        ledger = EvidenceLedger()
        self.append(ledger, {"price": 100.0})
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence.jsonl"
            ledger.write_jsonl(target)
            original = target.read_bytes()
            ledger._entries[0].payload["price"] = 999.0
            self.assertFalse(ledger.verify())
            with self.assertRaisesRegex(ValueError, "integrity"):
                ledger.write_jsonl(target)
            self.assertEqual(target.read_bytes(), original)
            missing = Path(directory) / "new" / "evidence.jsonl"
            with self.assertRaisesRegex(ValueError, "integrity"):
                ledger.write_jsonl(missing)
            self.assertFalse(missing.parent.exists())

    def test_detached_ledger_exports_strict_reproducible_json(self):
        ledger = EvidenceLedger()
        payload = {"unicode": "قیمت", "nested": [None, True, 100.0]}
        entry = self.append(ledger, payload)
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "first.jsonl", Path(directory) / "second.jsonl"
            ledger.write_jsonl(first)
            payload["nested"].append(999)
            entry.payload["unicode"] = "changed"
            ledger.write_jsonl(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            exported = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(exported["payload"], {"unicode": "قیمت", "nested": [None, True, 100.0]})


if __name__ == "__main__":
    unittest.main()
