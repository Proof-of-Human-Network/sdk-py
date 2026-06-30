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
    async with PohClient("https://bootnode.proofofhuman.ge") as poh:
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
payment.

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
        budget=0.5,                       # POH
        wallet_address="poh...",
        private_key_pem=my_private_key,
    ))
    result = await poh.poll_job_result(ref.job_id)
    print(result.output)
```

Before either of these will work, the wallet's signing key must be registered
with the node once via `register_signing_key()` — the node has no way to
verify a signature for a key it has never seen.

## Wallet / blockchain

```python
async with PohClient("https://proofofhuman.ge") as poh:
    # Balance (μPOH — divide by 1e9 for POH)
    bal = await poh.get_balance("poh...")
    print(bal.balance / 1e9, "POH")

    # Nonce
    nonce = await poh.get_nonce("poh...")

    # Transaction history
    history = await poh.get_transaction_history("poh...", limit=50)
    for entry in history.entries:
        print(entry.tx_hash, entry.delta)

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
    "https://bootnode.proofofhuman.ge",
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
    "https://bootnode.proofofhuman.ge",
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
| `poll_job(job_id, opts?)` | Poll until job completes |
| `watch_job(job_id)` | Async generator of job snapshots |
| `scan_and_wait(inputs, opts?)` | Bulk + poll in one call |
| `get_brain_verdict(brain_key)` | AI verdict |
| `poll_brain_verdict(brain_key)` | Poll until verdict resolves |

### Natural language jobs

| Method | Description |
|--------|-------------|
| `submit_job(question, options?)` | Submit NL question (`AskOptions`). Skill jobs always require a fee — pass `budget`, `wallet_address`, `private_key_pem`. |
| `run_compute(prompt, options)` | Submit a job that runs a specific `model` (and optional `dataset`); `ComputeOptions`. Always requires a fee. |
| `get_job_status(job_id)` | Poll status |
| `get_job_result(job_id)` | Fetch result |
| `poll_job_result(job_id, opts?)` | Poll until result ready |
| `ask_and_wait(question, ask_options?, poll_options?)` | Submit + wait |

### Wallet / blockchain

| Method | Description |
|--------|-------------|
| `get_balance(address)` | Balance in μPOH |
| `get_nonce(address)` | Account nonce |
| `get_transaction_history(address, limit)` | Tx history |
| `get_pending_transactions()` | Mempool pending txs |
| `submit_transaction(tx)` | Submit signed tx |
| `register_signing_key(addr, pub_key_pem, proof)` | Register key |
| `transfer(from, to, amount_poh, private_key_pem, fee?, memo?)` | Full transfer |

### Signing utilities

| Function | Description |
|----------|-------------|
| `generate_key_pair()` | Fresh Ed25519 keypair (PKCS8 PEM) |
| `sign_data(message, private_key_pem)` | Sign arbitrary data → base64 |
| `create_signing_proof(address, private_key_pem)` | Proof for key registration |
| `build_transfer(from, to, amount_poh, nonce, fee?, memo?)` | Build unsigned tx |
| `sign_transaction(tx, private_key_pem)` | Sign a PohTxData |
| `compute_tx_hash(...)` | SHA-256 tx hash hex |
| `compute_job_payment_hash(...)` | Canonical hash for a job fee payment (used internally by `submit_job`/`run_compute`) |
| `sign_job_payment(...)` | Sign a job fee payment proof (used internally by `submit_job`/`run_compute`) |

### Node info

| Method | Description |
|--------|-------------|
| `get_node_info()` | Node metadata |
| `get_miner_info()` | Miner details |
| `list_skills()` | Available skills |

## License

MIT
