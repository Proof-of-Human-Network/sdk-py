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
