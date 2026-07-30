# poh-sdk

Python SDK for the [Proof of Human](https://proofofhuman.ge) network.

## Install

```bash
pip install poh-sdk

# For transaction signing:
pip install poh-sdk cryptography
```

## Quick start

```python
import asyncio
from poh_sdk import PohClient

async def main():
    async with PohClient("https://miner.poh.ge") as poh:
        result = await poh.scan("0xabc...")
        print(result.result)   # True = human, False = bot, None = inconclusive

asyncio.run(main())
```

## Sync usage

Use `PohClient.sync(...)` to get a synchronous wrapper exposing the same
methods (no `_sync` suffix) without `await`:

```python
from poh_sdk import PohClient

poh = PohClient.sync("https://proofofhuman.ge")
result = poh.scan("0xabc...")
balance = poh.get_balance("poh...")
```

## Natural language jobs

Skill jobs always require a fee — pass `budget` (POH), `wallet_address`, and
`private_key_pem` in `AskOptions` so the SDK can sign the payment. The node
verifies the signature and debits the fee before it will run the job at all;
it rejects the request outright (no job ever runs) without a valid signed
payment. Pass `currency` in `AskOptions` to pay the fee in a stablecoin
(see [Stablecoins](#stablecoins-multi-currency)).

```python
from poh_sdk import AskOptions

async with PohClient("https://proofofhuman.ge") as poh:
    options = AskOptions(budget=0.5, wallet_address="poh...", private_key_pem=my_private_key)

    # Submit a question
    ref = await poh.submit_job(
        "What does vitalik.eth write about on Paragraph?",
        options,
    )

    # Wait for the answer
    result = await poh.poll_job_result(ref.job_id)
    print(result.output)       # skill-specific structured data
    print(result.nl_response)  # LLM natural-language answer

    # One-liner convenience
    result = await poh.ask_and_wait("What NFTs does gmoney.eth hold?", options)
```

## Compute jobs (your own model + dataset)

Run inference with a model of your choice, optionally grounded in a Hugging
Face dataset already installed on the node. Like skill jobs, compute jobs
are never free — `run_compute` always signs a fee payment.

```python
from poh_sdk import ComputeOptions

async with PohClient("https://proofofhuman.ge") as poh:
    ref = await poh.run_compute("Summarize the top 5 rows", ComputeOptions(
        model="llama3.1:8b",
        dataset="some-org/some-dataset",  # optional
        budget=0.5,                       # POH (or `currency` display units)
        wallet_address="poh...",
        private_key_pem=my_private_key,
    ))
    result = await poh.poll_job_result(ref.job_id)
    print(result.output)
```

Before either of these will work, the wallet's signing key must be registered
with the node once via `register_signing_key()` / `register_key_pair()` — the
node has no way to verify a signature for a key it has never seen.

## Wallet / blockchain

```python
async with PohClient("https://proofofhuman.ge") as poh:
    # Balance (μPOH — divide by 1e9 for POH)
    bal = await poh.get_balance("poh...")
    print(bal.balance / 1e9, "POH")

    # Nonce
    nonce = await poh.get_nonce("poh...")

    # Transaction history (balance journal)
    history = await poh.get_transaction_history("poh...", limit=50)
    for entry in history.entries:
        print(entry.tx_hash, entry.delta)

    # Raw transaction records involving an address
    txs = await poh.get_transactions("poh...")

    # Miner info
    info = await poh.get_miner_info()
    print(info.model, info.reputation)
```

## Signing & transactions

```python
from poh_sdk import (
    generate_key_pair,
    build_transfer,
    sign_transaction,
    create_signing_proof,
)

# 1. Generate a keypair — address is derived from the signing public key
private_key_pem, public_key_pem, my_address = generate_key_pair()

# 2. Register the public key with your local node (one-time)
async with PohClient(
    "https://miner.poh.ge",
    local_base_url="http://127.0.0.1:3456",
) as poh:
    await poh.register_signing_key(
        my_address, public_key_pem, create_signing_proof(my_address, private_key_pem)
    )

    # 3. Build, sign, and submit a transfer
    nonce_resp = await poh.get_nonce(my_address)
    tx     = build_transfer(my_address, recipient, amount_poh=5.0, nonce=nonce_resp.nonce + 1)
    signed = sign_transaction(tx, private_key_pem)
    result = await poh.submit_transaction(signed)
    print(result.tx_hash)

    # One-liner convenience (fetches nonce automatically)
    result = await poh.transfer(my_address, recipient, 5.0, private_key_pem)
```

`register_key_pair(KeyPair(...))` does the same registration from a `KeyPair`,
deriving the proof automatically and also publishing the wallet's X25519
encryption key (see [Chat record encryption](#chat-record-encryption)).
To replace an already-registered key, sign a
`create_rotation_proof(address, new_signing_public_key, existing_private_key_pem)`
with the **current** key and pass it as `rotation_proof`.

## Stablecoins (multi-currency)

Five regional stablecoins ride alongside POH: `aiGEL`, `aiKGS`, `aiAMD`,
`aiETB`, `aiBTN` (2 decimals — 1 unit = 100 raw; POH keeps 9).

```python
# Transfer 12.50 aiGEL — build + sign + submit manually
# (transfer() itself is POH-only; it has no currency parameter)
tx = build_transfer(from_addr, to, 12.5, nonce + 1, currency="aiGEL")
signed = sign_transaction(tx, private_key_pem)
await poh.submit_transaction(signed)

# Pay a compute job in aiKGS — the miner receives exactly aiKGS
ref = await poh.run_compute("Summarize…", ComputeOptions(
    model="qwen3-1.7b", budget=5.0, currency="aiKGS",
    wallet_address=addr, private_key_pem=key,
))
```

POH transactions/job payments hash exactly as before (`currency` enters the
signed preimage only when non-POH) — existing integrations are unaffected.

## Chat record encryption

Public-job chat records (`promptCipher` / `replyCipher`) are sealed to the
requester wallet's X25519 key, derived deterministically from its Ed25519
signing key. `register_key_pair()` publishes the encryption key automatically.

```python
from poh_sdk import derive_encryption_keypair, is_envelope, unseal

keys = derive_encryption_keypair(private_key_pem)
# keys["publicKeyB64"], keys["privateScalarB64"]

if is_envelope(record["promptCipher"]):
    prompt = unseal(record["promptCipher"], keys["privateScalarB64"])
```

## Bulk scans

```python
async with PohClient("https://proofofhuman.ge") as poh:
    job = await poh.scan_bulk(["0xaaa", "0xbbb", "0xccc"])

    # Stream progress
    async for snap in poh.watch_job(job.job_id):
        print(f"{snap.percent:.0f}% done")

    # Or wait in one call
    final = await poh.scan_and_wait(["0xaaa", "0xbbb"])
```

## Multi-node

```python
poh = PohClient(nodes=[
    "https://miner.poh.ge",
    "https://proofofhuman.ge",
    "https://poh.assetux.com",
])
# Automatically picks the fastest responding node
```

## API reference

### Scanning

| Method | Description |
|--------|-------------|
| `scan(input, opts?)` | Single-address scan |
| `scan_bulk(inputs, opts?)` | Submit bulk scan job |
| `get_job(job_id)` | Current snapshot of a bulk scan job |
| `poll_job(job_id, opts?)` | Poll until job completes |
| `watch_job(job_id, opts?)` | Async generator of job snapshots |
| `scan_and_wait(inputs, opts?)` | Bulk + poll in one call |
| `get_brain_verdict(brain_key)` | AI verdict |
| `poll_brain_verdict(brain_key, opts?)` | Poll until verdict resolves |
| `scan_and_verdict(input, scan_opts?, brain_opts?)` | Scan + verdict in one call |

### Signal methods

| Method | Description |
|--------|-------------|
| `get_methods(wallet_address?)` | List signal verification methods, ordered by vote score |
| `get_method(method_id)` | Fetch a single signal method by ID |

### Natural language jobs

| Method | Description |
|--------|-------------|
| `submit_job(question, options?)` | Submit NL question (`AskOptions`). Skill jobs always require a fee — pass `budget`, `wallet_address`, `private_key_pem`; optional `currency` (stablecoin fee). |
| `run_compute(prompt, options)` | Submit a job that runs a specific `model` (and optional `dataset`); `ComputeOptions` (optional `currency`, `job_id`). Always requires a fee. |
| `get_job_status(job_id)` | Poll status |
| `get_job_result(job_id)` | Fetch result |
| `poll_job_result(job_id, opts?)` | Poll until result ready |
| `ask_and_wait(question, ask_options?, poll_options?)` | Submit + wait |

### Wallet / blockchain

| Method | Description |
|--------|-------------|
| `get_balance(address)` | Balance in μPOH |
| `get_nonce(address)` | Account nonce (+ `pending_nonce` when mempool txs reserve higher) |
| `get_transaction_history(address, limit)` | Balance journal history |
| `get_transactions(address)` | Raw transaction records involving an address |
| `get_pending_transactions()` | Mempool pending txs |
| `submit_transaction(tx)` | Submit signed tx |
| `register_signing_key(addr, pub_key_pem, proof, rotation_proof?, encryption_public_key?)` | Register signing key (+ optional X25519 encryption key) |
| `register_key_pair(key_pair, rotation_proof?)` | Register a `KeyPair`; auto-derives proof + encryption key |
| `transfer(from, to, amount_poh, private_key_pem, fee?, memo?)` | Full transfer (POH only — use `build_transfer(currency=...)` for stablecoins) |

### Signing utilities

| Function | Description |
|----------|-------------|
| `generate_key_pair()` | Fresh Ed25519 keypair → `(private_pem, public_pem, address)` |
| `derive_address_from_signing_key(public_key_pem)` | Canonical `poh…` address for a signing public key |
| `sign_data(message, private_key_pem)` | Sign arbitrary data → base64 |
| `create_signing_proof(address, private_key_pem)` | Proof for key registration |
| `create_rotation_proof(address, new_signing_public_key, existing_private_key_pem)` | Proof for replacing a registered key |
| `build_transfer(from, to, amount_poh, nonce, fee?, memo?, currency?)` | Build unsigned tx (amount in display units of `currency`) |
| `sign_transaction(tx, private_key_pem)` | Sign a PohTxData |
| `compute_tx_hash(...)` | SHA-256 tx hash hex |
| `compute_job_payment_hash(...)` | Canonical hash for a job fee payment (used internally by `submit_job`/`run_compute`) |
| `sign_job_payment(...)` | Sign a job fee payment proof (used internally by `submit_job`/`run_compute`) |

### Chat encryption utilities

| Function | Description |
|----------|-------------|
| `derive_encryption_keypair(stable_secret)` | X25519 keypair dict (`publicKeyB64`, `privateScalarB64`) derived from the signing key |
| `seal(recipient_pub_b64, plaintext)` | Encrypt to a sealed envelope dict |
| `unseal(envelope, private_scalar_b64)` | Decrypt a sealed envelope |
| `seal_json(recipient_pub_b64, obj)` / `unseal_json(envelope, private_scalar_b64)` | JSON convenience wrappers |
| `is_envelope(x)` | Check whether a value is a sealed envelope |

### Node info

| Method | Description |
|--------|-------------|
| `get_node_info()` | Node metadata |
| `get_miner_info()` | Miner details |
| `list_skills()` | Available skills |

## Differences from the JS SDK

The Python SDK does not (yet) implement some features present in
`@poh_network/sdk`:

- `chat()` — no free-form chat endpoint wrapper
- `submit_feedback()` — no job star-rating
- `get_assets()` — no asset-registry / gas-price endpoint wrapper
- `get_balance()` returns only the μPOH balance (no stablecoin `assets` map)
- `transfer()` has no `currency` parameter — stablecoin transfers go through
  `build_transfer(currency=...)` + `submit_transaction()`
- `TxSubmitResult` has no `idempotent` flag
- No `pick_strategy` option (multi-node always picks the fastest node)

## License

MIT
