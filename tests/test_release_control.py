from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

from research_bot.release_control import REQUIRED_GATES, assess_release, rollback_plan


class ReleaseControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'proof.json').write_text('{"fixture": true}')
        self.sha = 'a' * 40
        self.item = dict(status='PASS', source_sha=self.sha, evidence_path='proof.json',
                         sha256=hashlib.sha256((self.root/'proof.json').read_bytes()).hexdigest(),
                         observed_at=datetime.now(timezone.utc).isoformat())

    def all_gates(self) -> dict:
        return {'gates': {name: {**self.item, 'kind': 'INDEPENDENT_RESEARCH' if name in {'scientific','independent_economics'} else 'OBSERVED_OPERATIONAL'} for name in REQUIRED_GATES}}

    def test_missing_dependencies_cannot_pass(self) -> None:
        result = assess_release({'gates': {'engineering': self.item}}, self.root, source_sha=self.sha)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertFalse(result['deployment_allowed'])

    def test_uncommitted_sources_are_not_a_release_revision(self) -> None:
        data = self.all_gates()
        data['working_tree_dirty'] = True
        self.assertEqual(assess_release(data, self.root, source_sha=self.sha)['decision'], 'INCONCLUSIVE')

    def test_complete_metadata_cannot_hide_fixture_external_evidence(self) -> None:
        result = assess_release(self.all_gates(), self.root, source_sha=self.sha)
        self.assertEqual(result['decision'], 'INCONCLUSIVE')
        self.assertEqual(result['gates']['engineering']['status'], 'PASS')
        self.assertFalse(result['deployment_allowed'])
        self.assertFalse(result['live_allowed'])

    def test_changed_hash_revision_and_future_time_block_acceptance(self) -> None:
        for field, value in [('sha256','0'*64), ('source_sha','b'*40), ('observed_at','2099-01-01T00:00:00+00:00')]:
            data = self.all_gates()
            data['gates']['engineering'][field] = value
            self.assertEqual(assess_release(data, self.root, source_sha=self.sha)['decision'], 'INCONCLUSIVE')

    def test_fixture_is_not_operational_or_independent_evidence(self) -> None:
        for gate in ['broker_demo', 'independent_economics']:
            data = self.all_gates()
            data['gates'][gate]['kind'] = 'SYNTHETIC_UNIT_TEST'
            self.assertEqual(assess_release(data, self.root, source_sha=self.sha)['decision'], 'INCONCLUSIVE')

    def test_incident_requires_rollback_even_when_other_gates_pass(self) -> None:
        data = self.all_gates()
        data['incidents'] = ['DUPLICATE_ORDER']
        result = assess_release(data, self.root, source_sha=self.sha)
        self.assertEqual(result['decision'], 'ROLLBACK_REQUIRED')
        self.assertTrue(result['requires_existing_position_reconciliation'])
        self.assertFalse(result['runtime_state_changed'])

    def test_code_rollback_needs_actual_healthy_target_and_reconciliation(self) -> None:
        for sha, reconciled in [(None, True), (self.sha, False)]:
            self.assertEqual(rollback_plan('ENGINEERING', last_healthy_sha=sha, positions_reconciled=reconciled)['status'], 'BLOCKED')

    def test_scientific_and_architectural_rollback_preserve_scope_and_failures(self) -> None:
        scientific = rollback_plan('SCIENTIFIC', last_healthy_sha=None, positions_reconciled=False, scientific_candidate_failed=True)
        self.assertFalse(scientific['candidate_promotion_allowed'])
        self.assertFalse(scientific['reuse_spent_test_sample'])
        architecture = rollback_plan('ARCHITECTURE', last_healthy_sha=None, positions_reconciled=False)
        self.assertEqual(architecture['fallback_scope']['symbols'], ['BTCUSDT','ETHUSDT'])


if __name__ == '__main__':
    unittest.main()
