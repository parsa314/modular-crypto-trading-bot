"""Pure immutable boundary contracts, independent of V59/strategy imports."""
from dataclasses import dataclass
from datetime import datetime
import math
import json
from .intent import ApprovedOrderIntent, FrozenModelManifest, digest, sha, text, utc


@dataclass(frozen=True)
class ProductionDecisionEnvelope:
    event_id: str
    research_audit_hash: str
    risk_constitution_hash: str
    promotion_manifest_hash: str
    promotion_evidence_hash: str
    portfolio_decision_id: str
    payload_json: str
    created_at: datetime
    purpose: str

    def __post_init__(self):
        text(self.event_id)
        for field in ('research_audit_hash', 'risk_constitution_hash', 'promotion_manifest_hash',
                      'promotion_evidence_hash', 'portfolio_decision_id'):
            sha(getattr(self, field))
        utc(self.created_at)
        payload = json.loads(self.payload_json)
        required = {'SIGNAL_CANDIDATE', 'MODEL_PREDICTION', 'UNCERTAINTY_ASSESSMENT',
                    'ECONOMIC_DECISION', 'FINANCIAL_DECISION', 'FINAL_RESEARCH_DECISION', 'PORTFOLIO_STATE'}
        if not required <= set(payload):
            raise ValueError('INCOMPLETE_PRODUCTION_ENVELOPE')
        if self.purpose != 'ENGINEERING_REPLAY':
            raise ValueError('PHASE1_PRIVATE_EXECUTION_FORBIDDEN')

    @property
    def sha256(self):
        return digest(self.__dict__)


@dataclass(frozen=True)
class ProtectiveOrderIntent:
    entry_client_order_id: str
    symbol: str
    venue: str
    quantity: float
    stop_loss: float
    take_profit: float
    risk_constitution_hash: str

    def __post_init__(self):
        for name in ('entry_client_order_id', 'symbol', 'venue'):
            text(getattr(self, name))
        sha(self.risk_constitution_hash)
        for name in ('quantity', 'stop_loss', 'take_profit'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('INVALID_PROTECTIVE_ORDER')
        if self.stop_loss >= self.take_profit:
            raise ValueError('INVALID_PROTECTIVE_BARRIERS')

    @property
    def protection_id(self):
        return 'P'+digest({'entry': self.entry_client_order_id, 'symbol': self.symbol,
                          'venue': self.venue, 'role': 'PROTECTIVE_PAIR_V1'})[:30]


@dataclass(frozen=True)
class ExecutionReceipt:
    client_order_id: str
    venue_order_id: str
    symbol: str
    side: str
    filled_quantity: float
    filled_cost: float
    fee_quote: float
    state: str
    evidence_class: str

    def __post_init__(self):
        for name in ('client_order_id', 'venue_order_id', 'symbol'):
            text(getattr(self, name))
        if self.side not in {'buy', 'sell'} or self.state not in {'OPEN', 'PARTIAL', 'FILLED', 'CANCELLED', 'REJECTED'}:
            raise ValueError('INVALID_EXECUTION_RECEIPT_STATE')
        if self.evidence_class != 'ENGINEERING_REPLAY':
            raise ValueError('PHASE1_RECEIPT_CLASS_FORBIDDEN')
        for name in ('filled_quantity', 'filled_cost', 'fee_quote'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError('INVALID_EXECUTION_RECEIPT_VALUE')


@dataclass(frozen=True)
class PortfolioDecision:
    account_id: str
    event_id: str
    state_hash: str
    risk_hash: str
    approved_notional: float
    approved: bool
    reason: str

    def __post_init__(self):
        text(self.account_id); text(self.event_id); text(self.reason)
        sha(self.state_hash); sha(self.risk_hash)
        if (not isinstance(self.approved, bool) or isinstance(self.approved_notional, bool)
                or not isinstance(self.approved_notional, (float, int))
                or not math.isfinite(self.approved_notional) or self.approved_notional < 0
                or (self.approved and self.approved_notional == 0)):
            raise ValueError('INVALID_PORTFOLIO_DECISION')

    @property
    def decision_id(self):
        return digest(self.__dict__)
