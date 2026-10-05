"""Hash-bound artifact verification and an explicit trusted review registry.

The canonical registry is empty: no scientific model has been promoted. Tests
inject a separate registry whose engineering-only purpose cannot authorize live.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path

from .intent import FrozenModelManifest, digest


CANONICAL_APPROVED_MANIFESTS = frozenset()


@dataclass(frozen=True)
class PromotionApproval:
    manifest_hash: str
    evidence_hash: str
    risk_hash: str
    purpose: str
    approved: bool
    private_execution_authorized: bool = False


class PromotionGate:
    def __init__(self, approved_manifest_hashes=CANONICAL_APPROVED_MANIFESTS, *, purpose='PRODUCTION', verified_artifacts=()):
        if not isinstance(approved_manifest_hashes, frozenset) or purpose not in {'PRODUCTION', 'ENGINEERING_REPLAY'}:
            raise ValueError('IMMUTABLE_PROMOTION_REGISTRY_REQUIRED')
        self.registry, self.purpose = approved_manifest_hashes, purpose
        self.artifacts = tuple(verified_artifacts)

    def evaluate(self, manifest: FrozenModelManifest, *, risk_hash):
        approved = (manifest.sha256 in self.registry and manifest.promotion_decision == 'PROMOTED'
                    and manifest.risk_constitution_hash == risk_hash and manifest.purpose == self.purpose)
        expected = {'model.bin': manifest.model_sha256, 'scaler.json': manifest.scaler_sha256,
                    'feature_schema.json': manifest.feature_schema_sha256}
        if approved:
            actual = {name: hashlib.sha256(payload).hexdigest() for name, payload in self.artifacts}
            approved = actual == expected and len(self.artifacts) == 3
        return PromotionApproval(manifest.sha256, manifest.promotion_evidence_hash, risk_hash,
                                 self.purpose, approved, False)


def verify_model_artifacts(root, manifest: FrozenModelManifest, *, expected_feature_schema_hash):
    """Return exact verified bytes; never deserialize pickle or run model code."""
    if manifest.promotion_decision != 'PROMOTED' or manifest.feature_schema_sha256 != expected_feature_schema_hash:
        raise ValueError('MODEL_NOT_PROMOTED_OR_SCHEMA_MISMATCH')
    root = Path(root).resolve()
    fields = {'model.bin': manifest.model_sha256, 'scaler.json': manifest.scaler_sha256,
              'feature_schema.json': manifest.feature_schema_sha256}
    verified = []
    for name, expected in fields.items():
        file = root/name
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 32*1024*1024:
            raise ValueError('MODEL_ARTIFACT_UNAVAILABLE_OR_UNSAFE')
        payload = file.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError('MODEL_ARTIFACT_HASH_MISMATCH')
        verified.append((name, payload))
    return tuple(verified)
