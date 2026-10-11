"""Canonical import location for the existing verified public Bitpin adapter.

No duplicate HTTP client or private endpoint implementation. Simulation is an
explicit offline collaborator, separate from the hard-locked execution sink.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..course.bitpin import BitpinReadOnlyAdapter, ResearchDataPort
from ..course.simulation import OfflineOrderSimulator
from ..execution import NoOrderTransport

__all__ = ["BitpinReadOnlyAdapter", "BitpinResearchGateway", "ResearchDataPort"]


@dataclass(frozen=True)
class BitpinResearchGateway:
    data: BitpinReadOnlyAdapter
    simulator: OfflineOrderSimulator | None = None
    execution: NoOrderTransport = field(default_factory=NoOrderTransport, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.data, BitpinReadOnlyAdapter):
            raise TypeError("VERIFIED_PUBLIC_BITPIN_ADAPTER_REQUIRED")
        if self.simulator is not None and not isinstance(
            self.simulator, OfflineOrderSimulator
        ):
            raise TypeError("OFFLINE_SIMULATOR_REQUIRED")

    @property
    def execution_authorization(self) -> str:
        return "DENIED_RESEARCH_ONLY"
