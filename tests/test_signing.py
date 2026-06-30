"""Tests for poh_sdk.signing utilities — no network required."""
import base64
import hashlib
import json
import pytest

cryptography = pytest.importorskip("cryptography")

from poh_sdk.signing import (
    generate_key_pair,
    sign_data,
    create_signing_proof,
    compute_tx_hash,
    compute_job_payment_hash,
    sign_job_payment,
    build_transfer,
    sign_transaction,
    PohTxData,
)


# ── generate_key_pair ─────────────────────────────────────────────────────────

def test_generate_key_pair_returns_pem_strings():
    priv_pem, pub_pem = generate_key_pair()
    assert "-----BEGIN PRIVATE KEY-----" in priv_pem
    assert "-----END PRIVATE KEY-----" in priv_pem
    assert "-----BEGIN PUBLIC KEY-----" in pub_pem
    assert "-----END PUBLIC KEY-----" in pub_pem


def test_generate_key_pair_produces_different_keys_each_call():
    priv1, pub1 = generate_key_pair()
    priv2, pub2 = generate_key_pair()
    assert priv1 != priv2
    assert pub1 != pub2


# ── sign_data / create_signing_proof ──────────────────────────────────────────

def test_sign_data_returns_base64_string():
    priv_pem, _ = generate_key_pair()
    sig = sign_data("hello world", priv_pem)
    assert isinstance(sig, str)
    # Ed25519 sig = 64 bytes → standard base64 = 88 chars
    assert len(base64.b64decode(sig)) == 64


def test_sign_data_is_deterministic():
    priv_pem, _ = generate_key_pair()
    sig1 = sign_data("same message", priv_pem)
    sig2 = sign_data("same message", priv_pem)
    assert sig1 == sig2


def test_sign_data_differs_for_different_messages():
    priv_pem, _ = generate_key_pair()
    sig1 = sign_data("message-A", priv_pem)
    sig2 = sign_data("message-B", priv_pem)
    assert sig1 != sig2


def test_create_signing_proof_equals_sign_data_of_address():
    priv_pem, _ = generate_key_pair()
    address = "poh_test_address"
    proof = create_signing_proof(address, priv_pem)
    direct = sign_data(address, priv_pem)
    assert proof == direct


def test_sign_data_can_be_verified_with_public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    priv_pem, pub_pem = generate_key_pair()
    message = "verify me"
    sig_b64 = sign_data(message, priv_pem)
    sig_bytes = base64.b64decode(sig_b64)

    pub_key = load_pem_public_key(pub_pem.encode())
    # Should NOT raise — raises InvalidSignature on failure
    pub_key.verify(sig_bytes, message.encode())


# ── compute_tx_hash ───────────────────────────────────────────────────────────

def test_compute_tx_hash_returns_64_char_hex():
    h = compute_tx_hash("pohA", "pohB", 1_000_000_000, 0, 1, 1700000000000, "")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_tx_hash_is_deterministic():
    args = ("pohA", "pohB", 5_000_000_000, 1000, 3, 1700000000000, "test")
    h1 = compute_tx_hash(*args)
    h2 = compute_tx_hash(*args)
    assert h1 == h2


def test_compute_tx_hash_differs_for_different_amounts():
    base = ("pohA", "pohB", 0, 0, 1, 1700000000000, "")
    h1 = compute_tx_hash("pohA", "pohB", 1_000_000_000, 0, 1, 1700000000000, "")
    h2 = compute_tx_hash("pohA", "pohB", 2_000_000_000, 0, 1, 1700000000000, "")
    assert h1 != h2


def test_compute_tx_hash_matches_sha256_of_canonical_json():
    # Must use compact separators (no whitespace) — this must byte-for-byte match the
    # node's JSON.stringify() output, since the node recomputes and verifies this hash
    # server-side (see WalletManager.applyTransaction in miner/node).
    from_addr, to, amount, fee, nonce, timestamp, memo = "pohA", "pohB", 1_000_000_000, 0, 1, 1700000000000, ""
    canonical = json.dumps(
        {"from": from_addr, "to": to, "amount": amount, "fee": fee,
         "nonce": nonce, "timestamp": timestamp, "memo": memo},
        separators=(",", ":"),
    )
    expected = hashlib.sha256(canonical.encode()).hexdigest()
    assert compute_tx_hash(from_addr, to, amount, fee, nonce, timestamp, memo) == expected


# ── build_transfer ────────────────────────────────────────────────────────────

def test_build_transfer_converts_poh_to_μpoh():
    tx = build_transfer("pohA", "pohB", 1.5, 3)
    assert tx.amount == 1_500_000_000


def test_build_transfer_sets_all_fields():
    tx = build_transfer("pohA", "pohB", 0.001, 5, fee=100, memo="memo text")
    assert tx.from_addr == "pohA"
    assert tx.to == "pohB"
    assert tx.amount == 1_000_000
    assert tx.fee == 100
    assert tx.nonce == 5
    assert tx.memo == "memo text"
    assert isinstance(tx.timestamp, int) and tx.timestamp > 0
    assert isinstance(tx.tx_hash, str) and len(tx.tx_hash) == 64


def test_build_transfer_tx_hash_is_correct():
    tx = build_transfer("pohA", "pohB", 1.0, 1)
    expected = compute_tx_hash(tx.from_addr, tx.to, tx.amount, tx.fee, tx.nonce, tx.timestamp, tx.memo)
    assert tx.tx_hash == expected


# ── sign_transaction ──────────────────────────────────────────────────────────

def test_sign_transaction_fills_in_signature_and_public_key():
    priv_pem, pub_pem = generate_key_pair()
    tx = build_transfer("pohA", "pohB", 2.0, 1)
    signed = sign_transaction(tx, priv_pem)
    assert signed.signature is not None and len(signed.signature) > 0
    assert "-----BEGIN PUBLIC KEY-----" in (signed.signing_public_key or "")
    assert signed.tx_hash == tx.tx_hash


def test_sign_transaction_raises_when_tx_hash_missing():
    priv_pem, _ = generate_key_pair()
    tx = PohTxData(from_addr="pohA", to="pohB", amount=1_000_000_000,
                   fee=0, nonce=1, timestamp=1700000000000, memo="")
    with pytest.raises(ValueError, match="tx.tx_hash missing"):
        sign_transaction(tx, priv_pem)


def test_sign_transaction_preserves_original_fields():
    priv_pem, _ = generate_key_pair()
    tx = build_transfer("pohA", "pohB", 3.0, 7, fee=500, memo="hello")
    signed = sign_transaction(tx, priv_pem)
    assert signed.from_addr == tx.from_addr
    assert signed.to == tx.to
    assert signed.amount == tx.amount
    assert signed.nonce == tx.nonce
    assert signed.memo == tx.memo


def test_sign_transaction_signature_verifies_with_matching_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    priv_pem, pub_pem = generate_key_pair()
    tx = build_transfer("pohA", "pohB", 1.0, 1)
    signed = sign_transaction(tx, priv_pem)

    sig_bytes = base64.b64decode(signed.signature)
    pub_key = load_pem_public_key(pub_pem.encode())
    # Verifying the tx_hash bytes — should not raise
    pub_key.verify(sig_bytes, signed.tx_hash.encode())


def test_to_dict_includes_all_fields_when_signed():
    priv_pem, _ = generate_key_pair()
    tx = build_transfer("pohA", "pohB", 1.0, 1)
    signed = sign_transaction(tx, priv_pem)
    d = signed.to_dict()
    assert d["from"] == "pohA"
    assert d["to"] == "pohB"
    assert "txHash" in d
    assert "signature" in d
    assert "signingPublicKey" in d


# ── job payment ────────────────────────────────────────────────────────────────

def test_compute_job_payment_hash_returns_64_char_hex():
    h = compute_job_payment_hash("job-1", "pohA", "pohMiner", 500_000_000, 0)
    assert len(h) == 64
    int(h, 16)  # raises if not valid hex


def test_compute_job_payment_hash_is_deterministic():
    args = ("job-1", "pohA", "pohMiner", 500_000_000, 0)
    assert compute_job_payment_hash(*args) == compute_job_payment_hash(*args)


def test_compute_job_payment_hash_differs_for_different_amounts():
    h1 = compute_job_payment_hash("job-1", "pohA", "pohMiner", 500_000_000, 0)
    h2 = compute_job_payment_hash("job-1", "pohA", "pohMiner", 999_000_000, 0)
    assert h1 != h2


def test_compute_job_payment_hash_matches_node_reference_value():
    # Fixed value computed by the node's own algorithm — crypto.createHash('sha256')
    # .update(JSON.stringify({jobId,requesterAddress,minerAddress,amount,nonce}))
    # .digest('hex') — for these exact inputs. The node recomputes and verifies this
    # hash server-side, so any mismatch here means real jobs submitted by this SDK
    # would be rejected outright.
    h = compute_job_payment_hash("job-abc", "pohAlice", "pohMiner", 500_000_000, 3)
    assert h == "1ed86280c1ab64d60d55a232a1c339299d32d8bd45e5f2bf26ff72b26d8908c0"


def test_sign_job_payment_returns_tx_hash_and_signature():
    priv_pem, pub_pem = generate_key_pair()
    proof = sign_job_payment("job-1", "pohA", "pohMiner", 500_000_000, 0, priv_pem)
    assert proof["txHash"] == compute_job_payment_hash("job-1", "pohA", "pohMiner", 500_000_000, 0)
    assert proof["signature"]

    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    pub_key = load_pem_public_key(pub_pem.encode())
    pub_key.verify(base64.b64decode(proof["signature"]), proof["txHash"].encode())
