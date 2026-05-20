"""Unit tests — no live server required (uses respx to mock httpx)."""
import asyncio
import json
import pytest
import respx
from httpx import Response

from poh_sdk import PohClient, PohError, PollOptions, ScanResult, JobStatus


BASE = "http://mock-poh"

# ── scan ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_returns_result():
    with respx.mock:
        respx.post(f"{BASE}/checker").mock(return_value=Response(200, json={
            "result": True, "brainKey": "bk1", "freeScansLeft": 9
        }))
        async with PohClient(BASE) as poh:
            res = await poh.scan("0xabc")
    assert res.result is True
    assert res.brain_key == "bk1"
    assert res.free_scans_left == 9


@pytest.mark.asyncio
async def test_scan_raises_poh_error_on_4xx():
    with respx.mock:
        respx.post(f"{BASE}/checker").mock(return_value=Response(
            401, json={"error": "unauthorized"}
        ))
        async with PohClient(BASE) as poh:
            with pytest.raises(PohError) as exc_info:
                await poh.scan("0xabc")
    assert exc_info.value.status == 401
    assert "unauthorized" in str(exc_info.value)


# ── bulk + poll ───────────────────────────────────────────────────────────────

SNAPS = [
    {"jobId":"j1","status":"processing","total":2,"done":1,"percent":50,"results":[],"errors":[],"createdAt":""},
    {"jobId":"j1","status":"done","total":2,"done":2,"percent":100,"results":[
        {"input":"0xaaa","result":True},{"input":"0xbbb","result":False}
    ],"errors":[],"createdAt":"","completedAt":"2024-01-01T00:00:00Z"},
]

@pytest.mark.asyncio
async def test_poll_job_resolves_on_done():
    call = 0
    def side_effect(_):
        nonlocal call
        snap = SNAPS[min(call, len(SNAPS)-1)]
        call += 1
        return Response(200, json=snap)

    with respx.mock:
        respx.post(f"{BASE}/checker").mock(return_value=Response(200, json={
            "jobId":"j1","status":"queued","total":2,"pollUrl":"/checker/job/j1","freeScansLeft":5
        }))
        respx.get(f"{BASE}/checker/job/j1").mock(side_effect=side_effect)

        async with PohClient(BASE) as poh:
            bulk = await poh.scan_bulk(["0xaaa","0xbbb"])
            opts = PollOptions(interval=0.01)
            done = await poh.poll_job(bulk.job_id, opts)

    assert done.status == "done"
    assert done.percent == 100
    assert len(done.results) == 2


@pytest.mark.asyncio
async def test_poll_job_raises_on_timeout():
    with respx.mock:
        respx.get(f"{BASE}/checker/job/jx").mock(return_value=Response(200, json={
            "jobId":"jx","status":"processing","total":1,"done":0,"percent":0,
            "results":[],"errors":[],"createdAt":""
        }))
        async with PohClient(BASE) as poh:
            with pytest.raises(TimeoutError):
                await poh.poll_job("jx", PollOptions(interval=0.05, timeout=0.06))


@pytest.mark.asyncio
async def test_watch_job_yields_all_snapshots():
    call = 0
    snaps = [
        {"jobId":"jw","status":"processing","total":3,"done":1,"percent":33,"results":[],"errors":[],"createdAt":""},
        {"jobId":"jw","status":"processing","total":3,"done":2,"percent":66,"results":[],"errors":[],"createdAt":""},
        {"jobId":"jw","status":"done","total":3,"done":3,"percent":100,"results":[],"errors":[],"createdAt":""},
    ]
    def side(_):
        nonlocal call
        s = snaps[min(call, len(snaps)-1)]
        call += 1
        return Response(200, json=s)

    with respx.mock:
        respx.get(f"{BASE}/checker/job/jw").mock(side_effect=side)
        async with PohClient(BASE) as poh:
            seen = [snap.percent async for snap in poh.watch_job("jw", PollOptions(interval=0.01))]

    assert seen == [33.0, 66.0, 100.0]


# ── scan_bulk validation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_bulk_rejects_empty_list():
    async with PohClient(BASE) as poh:
        with pytest.raises(ValueError):
            await poh.scan_bulk([])


# ── methods ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_methods_returns_list():
    with respx.mock:
        respx.get(f"{BASE}/verifyer").mock(return_value=Response(200, json=[
            {"id":"m1","type":"evm","description":"ETH balance","score":42.0}
        ]))
        async with PohClient(BASE) as poh:
            methods = await poh.get_methods()
    assert len(methods) == 1
    assert methods[0].id == "m1"
    assert methods[0].type == "evm"
