"""
PoH signing and transaction utilities.

Requires: pip install cryptography
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class PohTxData:
    """A PoH transaction, unsigned or signed."""

    from_addr: str
    to: str
    amount: int          # μPOH (1 POH = 1_000_000_000)
    fee: int
    nonce: int
    timestamp: int       # milliseconds since epoch
    memo: str
    tx_hash: Optional[str] = None
    signature: Optional[str] = None
    signing_public_key: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "from":      self.from_addr,
            "to":        self.to,
            "amount":    self.amount,
            "fee":       self.fee,
            "nonce":     self.nonce,
            "timestamp": self.timestamp,
            "memo":      self.memo,
        }
        if self.tx_hash:
            d["txHash"] = self.tx_hash
        if self.signature:
            d["signature"] = self.signature
        if self.signing_public_key:
            d["signingPublicKey"] = self.signing_public_key
        return d


# ── Crypto helpers ────────────────────────────────────────────────────────────

def _import_crypto():  # type: ignore[return]
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            PublicFormat,
            load_pem_private_key,
        )
        return Ed25519PrivateKey, Encoding, PrivateFormat, PublicFormat, NoEncryption, load_pem_private_key
    except ImportError:
        raise ImportError(
            "Install 'cryptography' to use signing utilities: pip install cryptography"
        )


def derive_address_from_signing_key(signing_public_key: str) -> str:
    """Derive the canonical poh address bound to an ed25519 SPKI PEM public key."""
    digest = hashlib.sha256(signing_public_key.encode()).hexdigest()
    return "poh" + digest[:40]


def generate_key_pair() -> tuple[str, str, str]:
    """Generate a new Ed25519 key pair.

    Returns
    -------
    (signing_private_key_pem, signing_public_key_pem, address)
        PEM-encoded keys plus the canonical address derived from the public key.
    """
    Ed25519PrivateKey, Encoding, PrivateFormat, PublicFormat, NoEncryption, _ = _import_crypto()
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    pub_pem  = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    return priv_pem, pub_pem, derive_address_from_signing_key(pub_pem)


def sign_data(message: str, private_key_pem: str) -> str:
    """Sign *message* with an Ed25519 private key.

    Returns the base64-encoded signature.
    """
    _, Encoding, PrivateFormat, PublicFormat, NoEncryption, load_pem_private_key = _import_crypto()
    priv = load_pem_private_key(private_key_pem.encode(), password=None)
    sig  = priv.sign(message.encode())  # type: ignore[attr-defined]
    return base64.b64encode(sig).decode()


def create_signing_proof(wallet_address: str, private_key_pem: str) -> str:
    """Create the *proof* field required by ``POST /api/wallet/register-key``.

    The proof is a base64 Ed25519 signature of the wallet address string.
    """
    return sign_data(wallet_address, private_key_pem)


def create_rotation_proof(
    address: str,
    new_signing_public_key: str,
    existing_private_key_pem: str,
) -> str:
    """Create the rotation proof required to replace an existing registered key."""
    payload = json.dumps(
        {"action": "rotate-key", "address": address, "newSigningPublicKey": new_signing_public_key},
        separators=(",", ":"),
    )
    return sign_data(payload, existing_private_key_pem)


# ── Transaction helpers ───────────────────────────────────────────────────────

def compute_tx_hash(
    from_addr: str,
    to: str,
    amount: int,
    fee: int,
    nonce: int,
    timestamp: int,
    memo: str,
) -> str:
    """Return the SHA-256 hex digest of the canonical transaction payload.

    Must byte-for-byte match the node's ``JSON.stringify`` output (no whitespace),
    since the node recomputes this hash from the transaction's own fields and
    rejects the transaction if it doesn't match — ``separators=(",", ":")`` is
    required here, not cosmetic.
    """
    payload = json.dumps({
        "from":      from_addr,
        "to":        to,
        "amount":    amount,
        "fee":       fee,
        "nonce":     nonce,
        "timestamp": timestamp,
        "memo":      memo,
    }, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def build_transfer(
    from_addr: str,
    to: str,
    amount_poh: float,
    nonce: int,
    fee: int = 0,
    memo: str = "",
) -> PohTxData:
    """Build an unsigned :class:`PohTxData`.

    Parameters
    ----------
    amount_poh:
        Amount in POH (not μPOH). Converted internally via ``round(amount_poh * 1_000_000_000)``.
    """
    amount    = round(amount_poh * 1_000_000_000)
    timestamp = int(time.time() * 1000)
    tx_hash   = compute_tx_hash(from_addr, to, amount, fee, nonce, timestamp, memo)
    return PohTxData(
        from_addr = from_addr,
        to        = to,
        amount    = amount,
        fee       = fee,
        nonce     = nonce,
        timestamp = timestamp,
        memo      = memo,
        tx_hash   = tx_hash,
    )


def sign_transaction(tx: PohTxData, private_key_pem: str) -> PohTxData:
    """Sign *tx* and return a new :class:`PohTxData` with ``signature`` and
    ``signing_public_key`` populated.

    Raises :class:`ValueError` if ``tx.tx_hash`` is not set (call
    :func:`build_transfer` first).
    """
    if not tx.tx_hash:
        raise ValueError("tx.tx_hash missing — call build_transfer() first")
    _, Encoding, PrivateFormat, PublicFormat, NoEncryption, load_pem_private_key = _import_crypto()
    priv    = load_pem_private_key(private_key_pem.encode(), password=None)
    sig     = priv.sign(tx.tx_hash.encode())  # type: ignore[attr-defined]
    pub_pem = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()  # type: ignore[attr-defined]
    return dataclasses.replace(
        tx,
        signature          = base64.b64encode(sig).decode(),
        signing_public_key = pub_pem,
    )


# ── Job fee payment ──────────────────────────────────────────────────────────

def compute_job_payment_hash(
    job_id: str,
    requester_address: str,
    miner_address: str,
    amount: int,
    nonce: int,
) -> str:
    """Compute the canonical payment hash for a job fee.

    Binds the fee to one specific job + miner + amount + nonce, so a signature
    over it can't be replayed against a different job or a higher budget. Must
    byte-for-byte match the node's own ``computeJobPaymentHash`` — uses compact
    JSON separators (no whitespace), matching JavaScript's ``JSON.stringify``.
    """
    payload = json.dumps({
        "jobId":            job_id,
        "requesterAddress": requester_address,
        "minerAddress":     miner_address,
        "amount":           amount,
        "nonce":            nonce,
    }, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def sign_job_payment(
    job_id: str,
    requester_address: str,
    miner_address: str,
    amount: int,
    nonce: int,
    private_key_pem: str,
) -> dict:
    """Sign a fee payment authorizing a fee-required job (skill execution, or a
    model/dataset compute job).

    The result (``{"txHash": ..., "signature": ...}``) goes in the ``paymentTx``
    field of a ``POST /job`` request — the node verifies the signature and debits
    the requester's balance before it will run the job at all.
    """
    tx_hash = compute_job_payment_hash(job_id, requester_address, miner_address, amount, nonce)
    signature = sign_data(tx_hash, private_key_pem)
    return {"txHash": tx_hash, "signature": signature}
