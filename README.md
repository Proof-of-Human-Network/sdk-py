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

All async methods have a `_sync` counterpart:

```python
from poh_sdk import PohClient

poh = PohClient("https://proofofhuman.ge")
result = poh.scan_sync("0xabc...")
balance = poh.get_balance_sync("poh...")
```

## Natural language jobs

```python
async with PohClient("https://proofofhuman.ge") as poh:
    # Submit a question
    ref = await poh.submit_job(
        "What does vitalik.eth write about on Paragraph?",
        budget=0.5,
        wallet_address="poh...",
    )

    # Wait for the answer
    result = await poh.poll_job_result(ref.job_id)
    print(result.output)       # skill-specific structured data
    print(result.nl_response)  # LLM natural-language answer

    # One-liner convenience
    result = await poh.ask_and_wait(
        "What NFTs does gmoney.eth hold?",
        budget=0.5,
        wallet_address="poh...",
    )
```

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

# 1. Generate a keypair
kp = generate_key_pair()  # returns (private_pem, public_pem)
private_key_pem, public_key_pem = kp

# 2. Register the public key with the node (one-time)
proof = create_signing_proof(my_address, private_key_pem)
async with PohClient("https://proofofhuman.ge") as poh:
    await poh.register_signing_key(my_address, public_key_pem, proof)

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
| `submit_job(question, budget, wallet_address)` | Submit NL question |
| `get_job_status(job_id)` | Poll status |
| `get_job_result(job_id)` | Fetch result |
| `poll_job_result(job_id, opts?)` | Poll until result ready |
| `ask_and_wait(question, budget, wallet_address)` | Submit + wait |

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

### Node info

| Method | Description |
|--------|-------------|
| `get_node_info()` | Node metadata |
| `get_miner_info()` | Miner details |
| `list_skills()` | Available skills |

## License

MIT
