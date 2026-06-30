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


@pytest.mark.asyncio
async def test_get_method_returns_single():
    with respx.mock:
        respx.get(f"{BASE}/verifyer/m2").mock(return_value=Response(200, json={
            "id":"m2","type":"solana","description":"SOL stake","score":3.0
        }))
        async with PohClient(BASE) as poh:
            m = await poh.get_method("m2")
    assert m.id == "m2"
    assert m.type == "solana"


# ── brain verdict ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_brain_verdict_done():
    with respx.mock:
        respx.get(f"{BASE}/checker/brain/bk-1").mock(return_value=Response(200, json={
            "status": "done", "verdict": "HUMAN", "confidence": 0.91, "reasoning": "active"
        }))
        async with PohClient(BASE) as poh:
            v = await poh.get_brain_verdict("bk-1")
    assert v.status == "done"
    assert v.verdict == "HUMAN"
    assert v.confidence == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_poll_brain_verdict_resolves_after_pending():
    call = 0
    def side(_):
        nonlocal call
        snaps = [
            {"status": "pending"},
            {"status": "done", "verdict": "AI", "confidence": 0.7},
        ]
        r = snaps[min(call, len(snaps) - 1)]
        call += 1
        return Response(200, json=r)

    with respx.mock:
        respx.get(f"{BASE}/checker/brain/bk-2").mock(side_effect=side)
        async with PohClient(BASE) as poh:
            from poh_sdk.types import BrainPollOptions
            v = await poh.poll_brain_verdict("bk-2", BrainPollOptions(interval=0.01))
    assert v.status == "done"
    assert v.verdict == "AI"


@pytest.mark.asyncio
async def test_scan_and_verdict_returns_scan_and_verdict():
    with respx.mock:
        respx.post(f"{BASE}/checker").mock(return_value=Response(200, json={
            "result": True, "brainKey": "bk-3", "freeScansLeft": 5
        }))
        respx.get(f"{BASE}/checker/brain/bk-3").mock(return_value=Response(200, json={
            "status": "done", "verdict": "HUMAN", "confidence": 0.99
        }))
        async with PohClient(BASE) as poh:
            from poh_sdk.types import BrainPollOptions
            sv = await poh.scan_and_verdict("0xabc", brain_options=BrainPollOptions(interval=0.01))
    assert sv.scan.result is True
    assert sv.verdict.verdict == "HUMAN"


@pytest.mark.asyncio
async def test_scan_and_verdict_returns_not_found_when_no_brain_key():
    with respx.mock:
        respx.post(f"{BASE}/checker").mock(return_value=Response(200, json={
            "result": False, "freeScansLeft": 2
        }))
        async with PohClient(BASE) as poh:
            sv = await poh.scan_and_verdict("0xabc")
    assert sv.verdict.status == "not_found"


# ── natural language jobs ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_job_routes_and_returns_job_ref():
    # budget=0 (free job) — no signed payment required.
    with respx.mock:
        respx.post(f"{BASE}/chat/route").mock(return_value=Response(200, json={
            "type": "skill", "skillId": "sk-sum", "input": {}
        }))
        respx.post(f"{BASE}/job").mock(return_value=Response(200, json={
            "jobId": "jnl-1", "status": "queued"
        }))
        async with PohClient(BASE) as poh:
            from poh_sdk.types import AskOptions
            ref = await poh.submit_job("Summarise this", AskOptions(budget=0))
    assert ref.job_id == "jnl-1"


@pytest.mark.asyncio
async def test_submit_job_raises_when_budget_positive_without_private_key():
    with respx.mock:
        respx.post(f"{BASE}/chat/route").mock(return_value=Response(200, json={
            "type": "skill", "skillId": "sk-sum", "input": {}
        }))
        async with PohClient(BASE) as poh:
            from poh_sdk.types import AskOptions
            with pytest.raises(PohError) as exc_info:
                await poh.submit_job("Summarise this", AskOptions(budget=0.5, wallet_address="pohAlice"))
    assert exc_info.value.status == 402


@pytest.mark.asyncio
async def test_submit_job_signs_a_nonce_bound_payment_proof_when_budget_positive():
    from poh_sdk.signing import generate_key_pair
    priv, _ = generate_key_pair()
    captured = {}

    def capture_job(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"jobId": "jnl-1", "status": "queued"})

    with respx.mock:
        respx.post(f"{BASE}/chat/route").mock(return_value=Response(200, json={
            "type": "skill", "skillId": "sk-sum", "input": {}
        }))
        respx.get(f"{BASE}/api/miner/info").mock(return_value=Response(200, json={
            "minerAddress": "pohMiner", "gasPrice": 1, "model": "qwen2.5:1.5b",
            "queueLength": 0, "reputation": 1.0,
        }))
        respx.get(f"{BASE}/api/wallet/nonce").mock(return_value=Response(200, json={
            "address": "pohAlice", "nonce": 3,
        }))
        respx.post(f"{BASE}/job").mock(side_effect=capture_job)
        async with PohClient(BASE) as poh:
            from poh_sdk.types import AskOptions
            ref = await poh.submit_job("Summarise this", AskOptions(
                budget=0.5, wallet_address="pohAlice", private_key_pem=priv,
            ))
    assert ref.job_id == "jnl-1"
    body = captured["body"]
    assert body["maxBudget"] == 500_000_000
    assert body["requesterAddress"] == "pohAlice"
    assert body["paymentTx"]["txHash"]
    assert body["paymentTx"]["signature"]


@pytest.mark.asyncio
async def test_submit_job_raises_when_no_skill_matched():
    with respx.mock:
        respx.post(f"{BASE}/chat/route").mock(return_value=Response(200, json={
            "type": "chat", "reason": "No skill matched"
        }))
        async with PohClient(BASE) as poh:
            from poh_sdk import PohError
            with pytest.raises(PohError) as exc_info:
                await poh.submit_job("random question")
    assert exc_info.value.status == 422


@pytest.mark.asyncio
async def test_run_compute_raises_when_budget_not_positive():
    from poh_sdk.signing import generate_key_pair
    from poh_sdk.types import ComputeOptions
    priv, _ = generate_key_pair()
    async with PohClient(BASE) as poh:
        with pytest.raises(PohError) as exc_info:
            await poh.run_compute("hi", ComputeOptions(
                model="qwen2.5:1.5b", budget=0, wallet_address="pohAlice", private_key_pem=priv,
            ))
    assert exc_info.value.status == 402


@pytest.mark.asyncio
async def test_run_compute_signs_payment_and_posts_model_dataset():
    from poh_sdk.signing import generate_key_pair
    from poh_sdk.types import ComputeOptions
    priv, _ = generate_key_pair()
    captured = {}

    def capture_job(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"jobId": "jc-1", "status": "queued"})

    with respx.mock:
        respx.get(f"{BASE}/api/miner/info").mock(return_value=Response(200, json={
            "minerAddress": "pohMiner", "gasPrice": 1, "model": "qwen2.5:1.5b",
            "queueLength": 0, "reputation": 1.0,
        }))
        respx.get(f"{BASE}/api/wallet/nonce").mock(return_value=Response(200, json={
            "address": "pohAlice", "nonce": 7,
        }))
        respx.post(f"{BASE}/job").mock(side_effect=capture_job)
        async with PohClient(BASE) as poh:
            ref = await poh.run_compute("Summarize the top rows", ComputeOptions(
                model="llama3.1:8b", dataset="some-org/some-dataset",
                budget=0.5, wallet_address="pohAlice", private_key_pem=priv,
            ))
    assert ref.job_id == "jc-1"
    body = captured["body"]
    assert body["model"] == "llama3.1:8b"
    assert body["dataset"] == "some-org/some-dataset"
    assert body["maxBudget"] == 500_000_000
    assert body["payload"]["prompt"] == "Summarize the top rows"
    assert body["paymentTx"]["txHash"]
    assert body["paymentTx"]["signature"]


@pytest.mark.asyncio
async def test_get_job_status_returns_status():
    with respx.mock:
        respx.get(f"{BASE}/job/jnl-1/status").mock(return_value=Response(200, json={
            "jobId": "jnl-1", "status": "computing"
        }))
        async with PohClient(BASE) as poh:
            s = await poh.get_job_status("jnl-1")
    assert s.status == "computing"


@pytest.mark.asyncio
async def test_get_job_result_parses_completed_result():
    with respx.mock:
        respx.get(f"{BASE}/job/jnl-1/result").mock(return_value=Response(200, json={
            "jobId": "jnl-1",
            "profile": {"skillOutput": {"answer": 42}, "skillId": "sk-1", "tokensUsed": 10, "nlResponse": "The answer is 42."}
        }))
        async with PohClient(BASE) as poh:
            r = await poh.get_job_result("jnl-1")
    assert r.job_id == "jnl-1"
    assert r.status == "done"
    assert r.nl_response == "The answer is 42."
    assert r.tokens_used == 10


@pytest.mark.asyncio
async def test_poll_job_result_polls_status_then_fetches_result():
    call = 0
    status_snaps = [{"jobId": "jnl-2", "status": "done"}]

    def status_side(_):
        nonlocal call
        r = status_snaps[min(call, len(status_snaps) - 1)]
        call += 1
        return Response(200, json=r)

    with respx.mock:
        respx.get(f"{BASE}/job/jnl-2/status").mock(side_effect=status_side)
        respx.get(f"{BASE}/job/jnl-2/result").mock(return_value=Response(200, json={
            "jobId": "jnl-2",
            "profile": {"nlResponse": "Done!", "skillId": "sk-1", "tokensUsed": 5, "skillOutput": None}
        }))
        async with PohClient(BASE) as poh:
            from poh_sdk.types import PollOptions
            r = await poh.poll_job_result("jnl-2", PollOptions(interval=0.01))
    assert r.nl_response == "Done!"


@pytest.mark.asyncio
async def test_ask_and_wait_routes_submits_and_polls():
    with respx.mock:
        respx.post(f"{BASE}/chat/route").mock(return_value=Response(200, json={
            "type": "skill", "skillId": "sk-1", "input": {}
        }))
        respx.post(f"{BASE}/job").mock(return_value=Response(200, json={"jobId": "jnl-3", "status": "queued"}))
        respx.get(f"{BASE}/job/jnl-3/status").mock(return_value=Response(200, json={"jobId": "jnl-3", "status": "done"}))
        respx.get(f"{BASE}/job/jnl-3/result").mock(return_value=Response(200, json={
            "jobId": "jnl-3",
            "profile": {"nlResponse": "Answer", "skillId": "sk-1", "tokensUsed": 8, "skillOutput": None}
        }))
        async with PohClient(BASE) as poh:
            from poh_sdk.types import AskOptions, PollOptions
            r = await poh.ask_and_wait("What is 2+2?", AskOptions(), PollOptions(interval=0.01))
    assert r.nl_response == "Answer"


# ── node info / miner info / skills ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_node_info_returns_metadata():
    with respx.mock:
        respx.get(f"{BASE}/healthz").mock(return_value=Response(200, json={
            "status": "ok", "nodeId": "node-42", "version": "1.2.0", "peers": 3
        }))
        async with PohClient(BASE) as poh:
            info = await poh.get_node_info()
    assert info.node_id == "node-42"
    assert info.version == "1.2.0"
    assert info.peers == 3


@pytest.mark.asyncio
async def test_list_skills_returns_array():
    with respx.mock:
        respx.get(f"{BASE}/api/skills").mock(return_value=Response(200, json=[
            {"id": "sk-1", "description": "Summariser", "triggers": ["summarise"]}
        ]))
        async with PohClient(BASE) as poh:
            skills = await poh.list_skills()
    assert len(skills) == 1
    assert skills[0].id == "sk-1"


@pytest.mark.asyncio
async def test_get_miner_info_returns_miner_metadata():
    with respx.mock:
        respx.get(f"{BASE}/api/miner/info").mock(return_value=Response(200, json={
            "minerAddress": "poh-miner-1", "gasPrice": 1000, "model": "llama-3",
            "queueLength": 2, "reputation": 4.5
        }))
        async with PohClient(BASE) as poh:
            info = await poh.get_miner_info()
    assert info.miner_address == "poh-miner-1"
    assert info.model == "llama-3"


# ── wallet / blockchain ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_balance_returns_μpoh():
    with respx.mock:
        respx.get(f"{BASE}/api/wallet/balance").mock(return_value=Response(200, json={
            "address": "poh123", "balance": 5_000_000_000
        }))
        async with PohClient(BASE) as poh:
            bal = await poh.get_balance("poh123")
    assert bal.address == "poh123"
    assert bal.balance == 5_000_000_000


@pytest.mark.asyncio
async def test_get_nonce_returns_current_nonce():
    with respx.mock:
        respx.get(f"{BASE}/api/wallet/nonce").mock(return_value=Response(200, json={
            "address": "poh123", "nonce": 7
        }))
        async with PohClient(BASE) as poh:
            n = await poh.get_nonce("poh123")
    assert n.address == "poh123"
    assert n.nonce == 7


@pytest.mark.asyncio
async def test_get_transaction_history_returns_entries():
    with respx.mock:
        respx.get(f"{BASE}/api/wallet/history").mock(return_value=Response(200, json={
            "address": "poh123",
            "entries": [{"height": 100, "delta": 1_000_000_000, "txHash": "abc", "ts": 1700000000, "label": "transfer"}]
        }))
        async with PohClient(BASE) as poh:
            hist = await poh.get_transaction_history("poh123")
    assert hist.address == "poh123"
    assert len(hist.entries) == 1
    assert hist.entries[0].delta == 1_000_000_000
    assert hist.entries[0].label == "transfer"


@pytest.mark.asyncio
async def test_get_pending_transactions_returns_queue():
    with respx.mock:
        respx.get(f"{BASE}/api/tx/pending").mock(return_value=Response(200, json={
            "pending": [], "count": 0
        }))
        async with PohClient(BASE) as poh:
            p = await poh.get_pending_transactions()
    assert p.count == 0


@pytest.mark.asyncio
async def test_submit_transaction_posts_and_returns_tx_hash():
    from poh_sdk.signing import PohTxData
    with respx.mock:
        respx.post(f"{BASE}/api/tx/submit").mock(return_value=Response(200, json={
            "ok": True, "txHash": "cafebabe", "queueSize": 1
        }))
        async with PohClient(BASE) as poh:
            tx = PohTxData(from_addr="pohA", to="pohB", amount=1_000_000_000,
                           fee=0, nonce=1, timestamp=1700000000000, memo="",
                           tx_hash="cafebabe", signature="sig", signing_public_key="pub")
            result = await poh.submit_transaction(tx)
    assert result.tx_hash == "cafebabe"
    assert result.ok is True


@pytest.mark.asyncio
async def test_register_signing_key_posts_key_and_proof():
    with respx.mock:
        respx.post(f"{BASE}/api/wallet/register-key").mock(return_value=Response(200, json={
            "success": True
        }))
        async with PohClient(BASE) as poh:
            res = await poh.register_signing_key("pohA", "pubkey-pem", "proof-b64")
    assert res.get("success") is True


# ── scan_and_wait ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_and_wait_combines_bulk_and_poll():
    call = 0
    def job_side(_):
        nonlocal call
        snaps = [
            {"jobId": "j-sw", "status": "processing", "total": 1, "done": 0, "percent": 0, "results": [], "errors": [], "createdAt": ""},
            {"jobId": "j-sw", "status": "done", "total": 1, "done": 1, "percent": 100, "results": [{"input": "0xabc", "result": True}], "errors": [], "createdAt": ""},
        ]
        r = snaps[min(call, len(snaps) - 1)]
        call += 1
        return Response(200, json=r)

    with respx.mock:
        respx.post(f"{BASE}/checker").mock(return_value=Response(200, json={
            "jobId": "j-sw", "status": "queued", "total": 1, "pollUrl": "/checker/job/j-sw", "freeScansLeft": 4
        }))
        respx.get(f"{BASE}/checker/job/j-sw").mock(side_effect=job_side)
        async with PohClient(BASE) as poh:
            done = await poh.scan_and_wait(["0xabc"], poll_options=PollOptions(interval=0.01))
    assert done.status == "done"
    assert done.percent == 100.0
