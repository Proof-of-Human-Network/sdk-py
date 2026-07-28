"""
chat-crypto — portable public-job chat encryption for the POH Python SDK.

Public compute jobs are raced by miners the requester doesn't control, so the on-chain
record of the prompt/reply is sealed to the requester's X25519 key:

    X25519 (ECDH) -> HKDF-SHA256 -> AES-256-GCM

Byte-identical to the node reference (poh-miner ``src/security/chat-crypto.js``,
verified round-trip) and the JS/Rust SDKs. See CHAT-CRYPTO.md for the wire format.

Requires: pip install cryptography
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, Union

_SEAL_INFO = b"poh-chat-seal-v1"
_SCALAR_INFO = b"poh-x25519-v1"


def _import_crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import (
            X25519PrivateKey,
            X25519PublicKey,
        )
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Install 'cryptography' to use chat encryption: pip install cryptography"
        ) from exc
    return (
        X25519PrivateKey,
        X25519PublicKey,
        AESGCM,
        SHA256,
        HKDF,
        Encoding,
        PublicFormat,
    )


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _derive_key(shared: bytes, recipient_pub: bytes, epk: bytes) -> bytes:
    _, _, _, SHA256, HKDF, _, _ = _import_crypto()
    return HKDF(
        algorithm=SHA256(), length=32, salt=recipient_pub + epk, info=_SEAL_INFO
    ).derive(shared)


def derive_encryption_keypair(stable_secret: Union[str, bytes]) -> Dict[str, str]:
    """Deterministically derive the wallet's X25519 keypair from a stable secret (its
    ed25519 signing private key PEM), matching the node. Returns raw 32-byte keys b64."""
    X25519PrivateKey, _, _, SHA256, HKDF, Encoding, PublicFormat = _import_crypto()
    ikm = stable_secret.encode() if isinstance(stable_secret, str) else stable_secret
    scalar = HKDF(algorithm=SHA256(), length=32, salt=b"", info=_SCALAR_INFO).derive(ikm)
    priv = X25519PrivateKey.from_private_bytes(scalar)
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {"publicKeyB64": _b64e(pub), "privateKeyB64": _b64e(scalar)}


def seal(recipient_pub_b64: str, plaintext: Union[str, bytes]) -> Dict[str, Any]:
    """Seal a plaintext to a recipient's raw X25519 public key (base64)."""
    X25519PrivateKey, X25519PublicKey, AESGCM, _, _, Encoding, PublicFormat = (
        _import_crypto()
    )
    recipient_pub_raw = _b64d(recipient_pub_b64)
    if len(recipient_pub_raw) != 32:
        raise ValueError("recipient X25519 pubkey must be 32 bytes")
    esk = X25519PrivateKey.generate()
    epk = esk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    shared = esk.exchange(X25519PublicKey.from_public_bytes(recipient_pub_raw))
    key = _derive_key(shared, recipient_pub_raw, epk)
    iv = os.urandom(12)
    pt = plaintext.encode() if isinstance(plaintext, str) else plaintext
    ct = AESGCM(key).encrypt(iv, pt, None)  # ciphertext || 16-byte tag
    return {
        "v": 1,
        "alg": "x25519-hkdf-sha256-aes256gcm",
        "epk": _b64e(epk),
        "iv": _b64e(iv),
        "ct": _b64e(ct),
    }


def unseal(envelope: Dict[str, Any], private_scalar_b64: str) -> str:
    """Open an envelope with the recipient's raw X25519 private scalar (base64).
    (Named ``unseal`` to avoid shadowing the builtin ``open``; equals JS ``open``.)"""
    X25519PrivateKey, X25519PublicKey, AESGCM, _, _, Encoding, PublicFormat = (
        _import_crypto()
    )
    if not envelope or envelope.get("v") != 1:
        raise ValueError("unsupported chat-crypto envelope")
    priv = X25519PrivateKey.from_private_bytes(_b64d(private_scalar_b64))
    recipient_pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    epk = _b64d(envelope["epk"])
    shared = priv.exchange(X25519PublicKey.from_public_bytes(epk))
    key = _derive_key(shared, recipient_pub, epk)
    pt = AESGCM(key).decrypt(_b64d(envelope["iv"]), _b64d(envelope["ct"]), None)
    return pt.decode()


def seal_json(recipient_pub_b64: str, obj: Any) -> Dict[str, Any]:
    return seal(recipient_pub_b64, json.dumps(obj, separators=(",", ":")))


def unseal_json(envelope: Dict[str, Any], private_scalar_b64: str) -> Any:
    return json.loads(unseal(envelope, private_scalar_b64))


def is_envelope(x: Any) -> bool:
    return (
        isinstance(x, dict)
        and x.get("v") == 1
        and isinstance(x.get("epk"), str)
        and isinstance(x.get("ct"), str)
    )
