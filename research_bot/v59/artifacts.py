from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import json
from typing import Any

from .hashing import canonical_json, stable_hash


@dataclass(frozen=True)
class ArtifactRef:
    relative_path: str
    sha256: str
    size_bytes: int


class ImmutableArtifactStore:
    """Create-only local artifact store used by V59 evidence pipelines."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _target(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("relative_path is required")
        target = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root != target and root not in target.parents:
            raise ValueError("artifact path escapes store root")
        return target

    def write_bytes(self, relative_path: str, payload: bytes) -> ArtifactRef:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        target = self._target(relative_path)
        if target.exists():
            raise FileExistsError(f"refusing to overwrite immutable artifact: {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return ArtifactRef(relative_path, sha256(payload).hexdigest(), len(payload))

    def write_json(self, relative_path: str, payload: Any) -> ArtifactRef:
        return self.write_bytes(relative_path, canonical_json(payload) + b"\n")


def bundle_hash(refs: list[ArtifactRef]) -> str:
    return stable_hash([asdict(ref) for ref in sorted(refs, key=lambda x: x.relative_path)])
