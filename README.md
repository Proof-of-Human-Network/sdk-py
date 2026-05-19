# poh-sdk

Python SDK for the [Proof of Human](https://proofofhuman.ge) API.

## Install

```bash
pip install poh-sdk
```

## Quick start

```python
import asyncio
from poh_sdk import PohClient

async def main():
    async with PohClient("https://proofofhuman.ge") as poh:
        result = await poh.scan("0xabc...")
        print(result.result)   # True = human, False = bot, None = inconclusive

asyncio.run(main())
```

## Sync usage

```python
from poh_sdk import PohClient

poh = PohClient.sync("https://proofofhuman.ge")
result = poh.scan("0xabc...")
```

## Bulk scanning

```python
async with PohClient("https://proofofhuman.ge") as poh:
    job = await poh.scan_bulk(["0xaaa", "0xbbb", "0xccc"])

    async for snap in poh.watch_job(job.job_id):
        print(f"{snap.percent:.0f}% done")
```
