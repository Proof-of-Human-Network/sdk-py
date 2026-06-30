from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


# ── Scan ──────────────────────────────────────────────────────────────────────

@dataclass
class ScanOptions:
    chain_ids: Optional[List[str]] = None
    tx_hash:   Optional[str]       = None

    def to_dict(self) -> dict:
        d = {}
        if self.chain_ids is not None:
            d["chainIds"] = self.chain_ids
        if self.tx_hash is not None:
            d["txHash"] = self.tx_hash
        return d


@dataclass
class OfacMatch:
    """Present in ScanResult.ofac when the address is on the OFAC SDN list."""
    name:            str
    program:         str
    chain_code:      str
    type:            str   # "direct" | "counterparty"
    matched_address: str

    @classmethod
    def from_dict(cls, d: dict) -> "OfacMatch":
        return cls(
            name            = d.get("name", ""),
            program         = d.get("program", ""),
            chain_code      = d.get("chainCode", ""),
            type            = d.get("type", "direct"),
            matched_address = d.get("matchedAddress", ""),
        )


@dataclass
class ScanResult:
    result:          Optional[bool]
    brain_key:       Optional[str]       = None
    free_scans_left: Optional[int]       = None
    source:          Optional[str]       = None
    count:           Optional[int]       = None
    ofac:            Optional[OfacMatch] = None  # set if address is OFAC-sanctioned

    @classmethod
    def from_dict(cls, d: dict) -> "ScanResult":
        ofac_raw = d.get("ofac")
        return cls(
            result          = d.get("result"),
            brain_key       = d.get("brainKey"),
            free_scans_left = d.get("freeScansLeft"),
            source          = d.get("source"),
            count           = d.get("count"),
            ofac            = OfacMatch.from_dict(ofac_raw) if ofac_raw and ofac_raw.get("sanctioned") else None,
        )


@dataclass
class BulkScanResult:
    job_id:          str
    status:          str
    total:           int
    poll_url:        str
    free_scans_left: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "BulkScanResult":
        return cls(
            job_id          = d["jobId"],
            status          = d["status"],
            total           = d["total"],
            poll_url        = d["pollUrl"],
            free_scans_left = d.get("freeScansLeft"),
        )


# ── Jobs ──────────────────────────────────────────────────────────────────────

@dataclass
class ScanResultItem:
    input:  str
    result: Optional[bool]
    error:  Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "ScanResultItem":
        return cls(input=d["input"], result=d.get("result"), error=d.get("error"))


@dataclass
class JobStatus:
    job_id:       str
    status:       str
    total:        int
    done:         int
    percent:      float
    results:      List[ScanResultItem]
    errors:       List[str]
    created_at:   str
    completed_at: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("done", "error")

    @classmethod
    def from_dict(cls, d: dict) -> "JobStatus":
        return cls(
            job_id       = d["jobId"],
            status       = d["status"],
            total        = d["total"],
            done         = d["done"],
            percent      = d["percent"],
            results      = [ScanResultItem.from_dict(r) for r in d.get("results", [])],
            errors       = d.get("errors", []),
            created_at   = d["createdAt"],
            completed_at = d.get("completedAt"),
        )


# ── Poll options ──────────────────────────────────────────────────────────────

@dataclass
class PollOptions:
    interval:    float                              = 1.5
    """Seconds between status checks."""
    timeout:     float                              = 120.0
    """Maximum total wait time in seconds."""
    on_progress: Optional[Callable[[JobStatus], None]] = field(default=None, repr=False)


# ── Brain poll options ────────────────────────────────────────────────────────

@dataclass
class BrainPollOptions:
    interval: float = 1.5
    """Seconds between brain verdict checks."""
    timeout: float = 30.0
    """Maximum total wait time in seconds."""


# ── Scan + verdict combined ───────────────────────────────────────────────────

@dataclass
class ScanWithVerdict:
    """Combined result of :meth:`PohClient.scan_and_verdict`."""
    scan: "ScanResult"
    verdict: "BrainVerdict"


# ── Brain verdict ──────────────────────────────────────────────────────────────

@dataclass
class BrainVerdict:
    status:     str
    verdict:    Optional[str]              = None  # "HUMAN" | "AI" | "UNCERTAIN"
    confidence: Optional[float]            = None
    signals:    Optional[Dict[str, float]] = None
    reasoning:  Optional[str]              = None

    @classmethod
    def from_dict(cls, d: dict) -> "BrainVerdict":
        return cls(
            status     = d["status"],
            verdict    = d.get("verdict"),
            confidence = d.get("confidence"),
            signals    = d.get("signals"),
            reasoning  = d.get("reasoning"),
        )


# ── Methods ───────────────────────────────────────────────────────────────────

@dataclass
class Method:
    id:          str
    type:        str
    description: str
    score:       float
    address:     Optional[str] = None
    method:      Optional[str] = None
    vote_count:  Optional[int] = None
    chain_id:    Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Method":
        return cls(
            id          = d["id"],
            type        = d["type"],
            description = d["description"],
            score       = d["score"],
            address     = d.get("address"),
            method      = d.get("method"),
            vote_count  = d.get("voteCount"),
            chain_id    = d.get("chainId"),
        )


# ── Natural language jobs ─────────────────────────────────────────────────────

@dataclass
class AskOptions:
    """Options for submitting a natural language job to the network."""
    budget:         float        = 0.0
    """Budget in POH units (e.g. 0.5 = 0.5 POH). Required for skill jobs."""
    wallet_address: Optional[str] = None
    """Wallet address to charge the budget from. Required when budget > 0."""
    private_key_pem: Optional[str] = None
    """PKCS8 PEM Ed25519 private key used to sign the fee payment. Required when
    budget > 0 — skill jobs always require a fee, and the node rejects the job
    outright without a valid signed payment proof."""


@dataclass
class ComputeOptions:
    """Options for submitting a paid compute job (user-specified model + dataset)."""
    model: str
    """Which model to run, e.g. 'qwen2.5:1.5b', 'llama3.1:8b'."""
    budget: float
    """Fee in POH (e.g. 0.5 = 0.5 POH). Required — compute jobs are never free."""
    wallet_address: str
    """Wallet address paying the fee."""
    private_key_pem: str
    """PKCS8 PEM Ed25519 private key used to sign the fee payment."""
    dataset: Optional[str] = None
    """Optional Hugging Face dataset id to ground the answer in (must be installed on the node)."""
    job_id: Optional[str] = None
    """Optional explicit job id. Auto-generated if omitted."""


@dataclass
class AskJobRef:
    """Reference returned immediately after submitting a job."""
    job_id:     str
    status:     str
    status_url: Optional[str] = None
    result_url: Optional[str] = None
    message:    Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AskJobRef":
        return cls(
            job_id     = d["jobId"],
            status     = d["status"],
            status_url = d.get("statusUrl"),
            result_url = d.get("resultUrl"),
            message    = d.get("message"),
        )


@dataclass
class AskJobStatus:
    """Lightweight status snapshot for a natural language job."""
    job_id:     str
    status:     str   # "queued" | "computing" | "done" | "error"
    error:      Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AskJobStatus":
        return cls(
            job_id     = d["jobId"],
            status     = d["status"],
            error      = d.get("error"),
            updated_at = d.get("updatedAt"),
        )


@dataclass
class AskJobResult:
    """Final result returned after a natural language job completes."""
    job_id:      str
    status:      str   # "done" | "error"
    output:      Optional[object] = None
    """Raw skill output. Shape depends on the skill (e.g. read_paragraph returns author + posts + analysis)."""
    nl_response: Optional[str]    = None
    """Natural language answer generated by the miner's LLM. Present when the job included a question."""
    skill_id:    Optional[str]    = None
    tokens_used: Optional[int]    = None
    error:       Optional[str]    = None

    @classmethod
    def from_dict(cls, d: dict) -> "AskJobResult":
        profile = d.get("profile") or {}
        return cls(
            job_id      = d.get("jobId", ""),
            status      = d.get("status", "done"),
            output      = profile.get("skillOutput"),
            nl_response = profile.get("nlResponse"),
            skill_id    = profile.get("skillId"),
            tokens_used = profile.get("tokensUsed"),
            error       = d.get("error"),
        )


# ── Node info ─────────────────────────────────────────────────────────────────

@dataclass
class NodeInfo:
    """Metadata about a PoH miner node."""
    status:     str
    node_id:    Optional[str]   = None
    version:    Optional[str]   = None
    wallet:     Optional[str]   = None
    reputation: Optional[float] = None
    uptime:     Optional[int]   = None
    peers:      Optional[int]   = None

    @classmethod
    def from_dict(cls, d: dict) -> "NodeInfo":
        return cls(
            status     = d.get("status", "ok"),
            node_id    = d.get("nodeId"),
            version    = d.get("version"),
            wallet     = d.get("wallet"),
            reputation = d.get("reputation"),
            uptime     = d.get("uptime"),
            peers      = d.get("peers"),
        )


# ── Skills ────────────────────────────────────────────────────────────────────

@dataclass
class Skill:
    """A skill available on the network."""
    id:          str
    version:     Optional[str]       = None
    description: Optional[str]       = None
    triggers:    Optional[List[str]] = None
    fee_min:     Optional[int]       = None

    @classmethod
    def from_dict(cls, d: dict) -> "Skill":
        return cls(
            id          = d["id"],
            version     = d.get("version"),
            description = d.get("description"),
            triggers    = d.get("triggers"),
            fee_min     = d.get("feeMin"),
        )


# ── Wallet / blockchain ───────────────────────────────────────────────────────

@dataclass
class WalletBalance:
    address: str
    balance: int  # μPOH (1 POH = 1e9)


@dataclass
class AccountNonce:
    address: str
    nonce: int


@dataclass
class TxHistoryEntry:
    height: int
    delta: int
    tx_hash: str
    ts: int
    label: str


@dataclass
class TxHistoryResult:
    address: str
    entries: List[TxHistoryEntry]


@dataclass
class TxSubmitResult:
    ok: bool
    tx_hash: str
    queue_size: int

    @classmethod
    def from_dict(cls, d: dict) -> "TxSubmitResult":
        return cls(
            ok         = d.get("ok", False),
            tx_hash    = d.get("txHash", ""),
            queue_size = d.get("queueSize", 0),
        )


@dataclass
class PendingTxResult:
    txs: List[dict]
    count: int

    @classmethod
    def from_dict(cls, d: dict) -> "PendingTxResult":
        return cls(txs=d.get("txs", []), count=d.get("count", 0))


@dataclass
class MinerInfo:
    miner_address: str
    gas_price: int
    model: str
    queue_length: int
    reputation: float

    @classmethod
    def from_dict(cls, d: dict) -> "MinerInfo":
        return cls(
            miner_address = d.get("minerAddress", ""),
            gas_price     = d.get("gasPrice", 0),
            model         = d.get("model", ""),
            queue_length  = d.get("queueLength", 0),
            reputation    = d.get("reputation", 0.0),
        )


@dataclass
class KeyPair:
    signing_private_key: str  # PKCS8 PEM
    signing_public_key: str   # SPKI PEM
