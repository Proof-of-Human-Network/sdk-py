"""
Proof of Human Python SDK — async + sync client.

Async usage (recommended):
    import asyncio
    from poh_sdk import PohClient

    # Single node (legacy):
    async with PohClient("https://proofofhuman.ge", api_key="...") as poh:
        res = await poh.scan("0xabc...")

    # Network mode — auto-picks fastest responding node:
    async with PohClient(nodes=["https://bootnode.proofofhuman.ge",
                                "https://proofofhuman.ge"]) as poh:
        res = await poh.scan("0xabc...")

Sync usage:
    from poh_sdk import PohClient

    poh = PohClient.sync("https://proofofhuman.ge")
    res = poh.scan("0xabc...")
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncIterator, List, Optional
from urllib.parse import quote

import httpx

from .types import (
    AccountNonce,
    AskJobRef,
    AskJobResult,
    AskJobStatus,
    AskOptions,
    BrainPollOptions,
    BrainVerdict,
    BulkScanResult,
    ComputeOptions,
    JobStatus,
    Method,
    MinerInfo,
    NodeInfo,
    PendingTxResult,
    PollOptions,
    ScanOptions,
    ScanResult,
    ScanWithVerdict,
    Skill,
    TxHistoryEntry,
    TxHistoryResult,
    TxSubmitResult,
    WalletBalance,
)
from .signing import PohTxData, sign_job_payment

# Default network nodes used when neither base_url nor nodes is provided.
DEFAULT_NODES: List[str] = [
    "https://bootnode.proofofhuman.ge",
    "https://proofofhuman.ge",
    "https://poh.assetux.com",
]


async def _probe_node(client: httpx.AsyncClient, url: str) -> str:
    """HEAD /healthz to measure node liveness. Returns url on success."""
    try:
        r = await client.head(f"{url}/healthz", timeout=4.0)
        if r.status_code < 500:
            return url
    except Exception:
        pass
    raise ConnectionError(f"Node unreachable: {url}")


async def _pick_fastest(nodes: List[str]) -> str:
    """Race health-checks against all nodes; return first that responds."""
    if len(nodes) == 1:
        return nodes[0]
    async with httpx.AsyncClient() as client:
        tasks = [asyncio.ensure_future(_probe_node(client, url)) for url in nodes]
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in done:
                if not t.exception():
                    return t.result()
        except Exception:
            pass
    return nodes[0]  # fallback


class PohError(Exception):
    """Raised when the POH API returns a non-2xx response."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status

    def __repr__(self) -> str:
        return f"PohError(status={self.status}, message={str(self)!r})"


class PohClient:
    """
    Async Proof of Human API client.

    Use as an async context manager or call :meth:`aclose` when done.
    For one-off synchronous use, see :meth:`sync`.

    Parameters
    ----------
    base_url:
        Single-node base URL (legacy), e.g. ``"https://proofofhuman.ge"``.
        Takes precedence over *nodes* when provided.
    nodes:
        List of network node URLs to probe. The client races health-checks and
        uses the fastest responding node. Falls back to ``DEFAULT_NODES`` when
        neither *base_url* nor *nodes* is provided.
    api_key:
        API key for paid tier.
    wallet_address:
        Solana wallet address for free-tier request tracking.
    timeout:
        Per-request timeout in seconds (default: 30).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        nodes:          Optional[List[str]] = None,
        api_key:        Optional[str] = None,
        wallet_address: Optional[str] = None,
        timeout:        float         = 30.0,
    ) -> None:
        self._api_key        = api_key
        self._wallet_address = wallet_address
        self._timeout        = timeout
        self._nodes: List[str] = []
        self._resolved_url: Optional[str] = None

        headers: dict = {"Accept": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        self._headers = headers

        if base_url:
            # Legacy single-node path
            self._resolved_url = base_url.rstrip("/")
        else:
            self._nodes = [u.rstrip("/") for u in (nodes or DEFAULT_NODES)]

        # Build initial client — may be recreated after node discovery
        url = self._resolved_url or self._nodes[0]
        self._client = httpx.AsyncClient(
            base_url = url,
            headers  = headers,
            timeout  = timeout,
        )

    async def __aenter__(self) -> "PohClient":
        if not self._resolved_url and self._nodes:
            await self._resolve_node()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _resolve_node(self) -> None:
        """Pick the fastest live node and rebuild the HTTP client pointed at it."""
        url = await _pick_fastest(self._nodes)
        self._resolved_url = url
        await self._client.aclose()
        self._client = httpx.AsyncClient(
            base_url = url,
            headers  = self._headers,
            timeout  = self._timeout,
        )

    @property
    def active_node(self) -> Optional[str]:
        """The URL of the currently selected node (None before context entry)."""
        return self._resolved_url

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, **kwargs: object) -> dict:
        if not self._resolved_url and self._nodes:
            await self._resolve_node()
        try:
            res = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise PohError("Request timed out", 408) from exc

        if res.is_error:
            try:
                msg = res.json().get("error", res.text)
            except Exception:
                msg = res.text or f"HTTP {res.status_code}"
            raise PohError(str(msg), res.status_code)

        return res.json()

    # ── Scan ──────────────────────────────────────────────────────────────────

    async def scan(
        self,
        input: str,
        options: Optional[ScanOptions] = None,
    ) -> ScanResult:
        """Scan a single wallet address.

        Returns ``result=True`` for human, ``False`` for not-human,
        ``None`` for inconclusive.
        """
        body: dict = {"input": input}
        if self._wallet_address:
            body["walletAddress"] = self._wallet_address
        if options:
            body.update(options.to_dict())
        return ScanResult.from_dict(await self._request("POST", "/checker", json=body))

    async def scan_bulk(
        self,
        inputs: List[str],
        options: Optional[ScanOptions] = None,
    ) -> BulkScanResult:
        """Submit a bulk scan.

        Returns a :class:`BulkScanResult` with a ``job_id``.
        Use :meth:`poll_job` or :meth:`watch_job` to retrieve results.
        """
        if not inputs:
            raise ValueError("inputs list must not be empty")
        body: dict = {"input": inputs}
        if self._wallet_address:
            body["walletAddress"] = self._wallet_address
        if options:
            body.update(options.to_dict())
        return BulkScanResult.from_dict(await self._request("POST", "/checker", json=body))

    # ── Job polling ───────────────────────────────────────────────────────────

    async def get_job(self, job_id: str) -> JobStatus:
        """Fetch the current status snapshot of an async scan job."""
        return JobStatus.from_dict(await self._request("GET", f"/checker/job/{quote(job_id)}"))

    async def poll_job(
        self,
        job_id: str,
        options: Optional[PollOptions] = None,
    ) -> JobStatus:
        """Poll a job until it reaches ``done`` or ``error``.

        Raises :class:`asyncio.TimeoutError` if the job does not finish
        within ``options.timeout`` seconds.
        """
        opts     = options or PollOptions()
        deadline = time.monotonic() + opts.timeout

        while True:
            job = await self.get_job(job_id)
            if opts.on_progress:
                opts.on_progress(job)
            if job.is_terminal:
                return job
            if time.monotonic() + opts.interval > deadline:
                raise TimeoutError(
                    f"POH job '{job_id}' did not complete within {opts.timeout}s"
                )
            await asyncio.sleep(opts.interval)

    async def watch_job(
        self,
        job_id: str,
        options: Optional[PollOptions] = None,
    ) -> AsyncIterator[JobStatus]:
        """Async generator that yields a status snapshot on each poll tick.

        Terminates when the job is ``done`` or ``error``.

        Example::

            async for snap in poh.watch_job(job_id):
                print(f"{snap.percent:.0f}% ({snap.done}/{snap.total})")
        """
        opts     = options or PollOptions()
        deadline = time.monotonic() + opts.timeout

        while True:
            job = await self.get_job(job_id)
            yield job
            if job.is_terminal:
                return
            if time.monotonic() + opts.interval > deadline:
                raise TimeoutError(
                    f"POH job '{job_id}' did not complete within {opts.timeout}s"
                )
            await asyncio.sleep(opts.interval)

    async def scan_and_wait(
        self,
        inputs: List[str],
        scan_options:  Optional[ScanOptions]  = None,
        poll_options:  Optional[PollOptions]  = None,
    ) -> JobStatus:
        """Convenience: submit a bulk scan and wait for all results."""
        job = await self.scan_bulk(inputs, scan_options)
        return await self.poll_job(job.job_id, poll_options)

    # ── Brain verdict ──────────────────────────────────────────────────────────

    async def get_brain_verdict(self, brain_key: str) -> BrainVerdict:
        """Retrieve the AI brain verdict for a completed scan."""
        return BrainVerdict.from_dict(
            await self._request("GET", f"/checker/brain/{quote(brain_key)}")
        )

    async def poll_brain_verdict(
        self,
        brain_key: str,
        options: Optional[BrainPollOptions] = None,
    ) -> BrainVerdict:
        """Poll the brain verdict until the status leaves ``pending``.

        Raises :class:`TimeoutError` if the verdict does not resolve within
        ``options.timeout`` seconds.

        Example::

            verdict = await poh.poll_brain_verdict(scan.brain_key)
            print(verdict.verdict, verdict.confidence)
        """
        opts     = options or BrainPollOptions()
        deadline = time.monotonic() + opts.timeout

        while True:
            v = await self.get_brain_verdict(brain_key)
            if v.status != "pending":
                return v
            if time.monotonic() + opts.interval > deadline:
                raise TimeoutError(
                    f"Brain verdict for '{brain_key}' did not resolve within {opts.timeout}s"
                )
            await asyncio.sleep(opts.interval)

    async def scan_and_verdict(
        self,
        input: str,
        scan_options:  Optional[ScanOptions]      = None,
        brain_options: Optional[BrainPollOptions] = None,
    ) -> ScanWithVerdict:
        """Convenience: scan a single address and wait for the AI brain verdict.

        Returns a :class:`ScanWithVerdict` with both the raw scan evidence and
        the resolved AI verdict.

        Example::

            sv = await poh.scan_and_verdict("0xabc...")
            print(sv.verdict.verdict, sv.verdict.confidence)
        """
        scan = await self.scan(input, scan_options)
        if not scan.brain_key:
            return ScanWithVerdict(scan=scan, verdict=BrainVerdict(status="not_found"))
        verdict = await self.poll_brain_verdict(scan.brain_key, brain_options)
        return ScanWithVerdict(scan=scan, verdict=verdict)

    # ── Methods ───────────────────────────────────────────────────────────────

    async def get_methods(self, wallet_address: Optional[str] = None) -> List[Method]:
        """List available signal verification methods."""
        addr = wallet_address or self._wallet_address
        qs   = f"?address={quote(addr)}" if addr else ""
        data = await self._request("GET", f"/verifyer{qs}")
        return [Method.from_dict(m) for m in data]

    async def get_method(self, method_id: str) -> Method:
        """Fetch a single signal method by ID."""
        return Method.from_dict(
            await self._request("GET", f"/verifyer/{quote(method_id)}")
        )

    # ── Natural language jobs ─────────────────────────────────────────────────

    async def submit_job(
        self,
        question: str,
        options: Optional[AskOptions] = None,
    ) -> AskJobRef:
        """Route a natural language question and submit it as a skill job.

        Returns immediately with an :class:`AskJobRef`; use :meth:`poll_job_result`
        or :meth:`ask_and_wait` to get the answer.

        Raises :class:`PohError` (422) if no skill matches the question.

        Skill jobs always require a fee — pass ``budget``, ``wallet_address``, and
        ``private_key_pem`` so the request can be signed. The node verifies the
        signature and debits the fee before it will run the job at all.

        Example::

            ref = await poh.submit_job("What does vitalik.eth write on Paragraph?",
                AskOptions(budget=0.5, wallet_address="poh...", private_key_pem=my_key))
            result = await poh.poll_job_result(ref.job_id)
        """
        opts      = options or AskOptions()
        max_budget = round(opts.budget * 1_000_000_000)

        # 1. Route to a skill
        route = await self._request("POST", "/chat/route", json={
            "message": question,
            "budget":  max_budget,
        })
        if route.get("type") != "skill" or not route.get("skillId"):
            raise PohError(
                route.get("reason") or f"No skill matched: {question!r}", 422
            )

        # 2. Submit job
        requester_address = opts.wallet_address or self._wallet_address
        job_id = f"job-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

        job_body: dict = {
            "id":        job_id,
            "type":      "skill",
            "skillId":   route["skillId"],
            "payload":   route.get("input") or {},
            "maxBudget": max_budget,
        }
        if requester_address:
            job_body["requesterAddress"] = requester_address

        if max_budget > 0:
            if not requester_address or not opts.private_key_pem:
                raise PohError(
                    "submit_job: wallet_address and private_key_pem are required when "
                    "budget > 0 — skill jobs always require a signed fee.",
                    402,
                )
            miner_info = await self.get_miner_info()
            nonce_info = await self.get_nonce(requester_address)
            job_body["paymentTx"] = sign_job_payment(
                job_id, requester_address, miner_info.miner_address,
                max_budget, nonce_info.nonce, opts.private_key_pem,
            )

        return AskJobRef.from_dict(await self._request("POST", "/job", json=job_body))

    async def run_compute(self, prompt: str, options: ComputeOptions) -> AskJobRef:
        """Submit a paid compute job that runs a user-specified model (and,
        optionally, grounds the answer in a Hugging Face dataset already
        installed on the node). Compute jobs are never free — the node rejects
        the request outright unless it carries a valid signed fee payment.

        Example::

            ref = await poh.run_compute("Summarize the top 5 rows", ComputeOptions(
                model="llama3.1:8b", dataset="some-org/some-dataset",
                budget=0.5, wallet_address=my_address, private_key_pem=my_key,
            ))
            result = await poh.poll_job_result(ref.job_id)
        """
        if not (options.budget > 0):
            raise PohError("run_compute: budget must be > 0 — compute jobs always require a fee", 402)

        job_id     = options.job_id or f"job-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        max_budget = round(options.budget * 1_000_000_000)

        miner_info = await self.get_miner_info()
        nonce_info = await self.get_nonce(options.wallet_address)
        payment_tx = sign_job_payment(
            job_id, options.wallet_address, miner_info.miner_address,
            max_budget, nonce_info.nonce, options.private_key_pem,
        )

        job_body: dict = {
            "id":               job_id,
            "type":             "compute",
            "model":            options.model,
            "dataset":          options.dataset,
            "payload":          {"prompt": prompt},
            "maxBudget":        max_budget,
            "requesterAddress": options.wallet_address,
            "paymentTx":        payment_tx,
        }
        return AskJobRef.from_dict(await self._request("POST", "/job", json=job_body))

    async def get_job_status(self, job_id: str) -> AskJobStatus:
        """Fetch the current status of a natural language job (without the full result)."""
        return AskJobStatus.from_dict(
            await self._request("GET", f"/job/{quote(job_id)}/status")
        )

    async def get_job_result(self, job_id: str) -> AskJobResult:
        """Fetch the result of a completed natural language job.

        Returns a result with ``status='computing'`` if the job is not done yet.
        """
        if not self._resolved_url and self._nodes:
            await self._resolve_node()
        try:
            res = await self._client.get(f"/job/{quote(job_id)}/result")
        except httpx.TimeoutException as exc:
            raise PohError("Request timed out", 408) from exc

        if res.status_code == 202:
            return AskJobResult(job_id=job_id, status="computing")

        if res.is_error:
            try:
                msg = res.json().get("error", res.text)
            except Exception:
                msg = res.text or f"HTTP {res.status_code}"
            raise PohError(str(msg), res.status_code)

        return AskJobResult.from_dict(res.json())

    async def poll_job_result(
        self,
        job_id: str,
        options: Optional[PollOptions] = None,
    ) -> AskJobResult:
        """Poll a natural language job until ``done`` or ``error``, then return the result.

        Raises :class:`TimeoutError` if the job does not finish within ``options.timeout`` seconds.
        """
        opts     = options or PollOptions()
        deadline = time.monotonic() + opts.timeout

        while True:
            s = await self.get_job_status(job_id)
            if s.status in ("done", "error"):
                return await self.get_job_result(job_id)
            if time.monotonic() + opts.interval > deadline:
                raise TimeoutError(
                    f"POH job '{job_id}' did not complete within {opts.timeout}s"
                )
            await asyncio.sleep(opts.interval)

    async def ask_and_wait(
        self,
        question: str,
        ask_options:  Optional[AskOptions]  = None,
        poll_options: Optional[PollOptions] = None,
    ) -> AskJobResult:
        """Route, submit, and wait for a natural language job in one call.

        Example::

            result = await poh.ask_and_wait(
                "What does vitalik.eth write about on Paragraph?",
                AskOptions(budget=0.5, wallet_address="poh..."),
            )
            print(result.nl_response or result.output)
        """
        ref = await self.submit_job(question, ask_options)
        return await self.poll_job_result(ref.job_id, poll_options)

    # ── Node info ─────────────────────────────────────────────────────────────

    async def get_node_info(self) -> NodeInfo:
        """Fetch metadata about the currently connected node.

        Returns node ID, version, wallet address, reputation, and peer count.
        """
        return NodeInfo.from_dict(await self._request("GET", "/healthz"))

    async def list_skills(self) -> List[Skill]:
        """List all skills available on the connected node."""
        data = await self._request("GET", "/api/skills")
        if isinstance(data, list):
            return [Skill.from_dict(s) for s in data]
        return []

    # ── Miner info ────────────────────────────────────────────────────────────

    async def get_miner_info(self) -> MinerInfo:
        """Fetch metadata about the connected miner node (address, gas price, model, etc.)."""
        return MinerInfo.from_dict(await self._request("GET", "/api/miner/info"))

    # ── Wallet / blockchain ───────────────────────────────────────────────────

    async def get_balance(self, address: str) -> WalletBalance:
        """Return the POH balance for *address* (balance is in μPOH; divide by 1e9 for POH)."""
        d = await self._request("GET", f"/api/wallet/balance?address={quote(address)}")
        return WalletBalance(address=d["address"], balance=d["balance"])

    async def get_nonce(self, address: str) -> AccountNonce:
        """Return the current nonce for *address*. Use ``nonce + 1`` for the next transaction."""
        d = await self._request("GET", f"/api/wallet/nonce?address={quote(address)}")
        return AccountNonce(address=d["address"], nonce=d["nonce"])

    async def get_transaction_history(self, address: str, limit: int = 30) -> TxHistoryResult:
        """Return the transaction history for *address*."""
        qs = f"address={quote(address)}&limit={limit}"
        d  = await self._request("GET", f"/api/wallet/history?{qs}")
        entries = [
            TxHistoryEntry(
                height  = e["height"],
                delta   = e["delta"],
                tx_hash = e["txHash"],
                ts      = e["ts"],
                label   = e["label"],
            )
            for e in d.get("entries", [])
        ]
        return TxHistoryResult(address=d.get("address", ""), entries=entries)

    async def get_transactions(self, address: str) -> dict:
        """Return the raw transactions dict for *address*."""
        return await self._request("GET", f"/api/wallet/transactions?address={quote(address)}")

    async def get_pending_transactions(self) -> PendingTxResult:
        """Return transactions currently sitting in the miner's queue."""
        return PendingTxResult.from_dict(await self._request("GET", "/api/tx/pending"))

    async def submit_transaction(self, tx: "PohTxData") -> TxSubmitResult:
        """Submit a signed :class:`~poh_sdk.signing.PohTxData` to the network."""
        return TxSubmitResult.from_dict(
            await self._request("POST", "/api/tx/submit", json=tx.to_dict())
        )

    async def register_signing_key(
        self,
        address: str,
        signing_public_key: str,
        proof: str,
    ) -> dict:
        """Register a signing public key for *address*.

        *proof* must be a base64 Ed25519 signature of the wallet address string,
        created with the corresponding private key (see :func:`~poh_sdk.signing.create_signing_proof`).
        """
        return await self._request(
            "POST",
            "/api/wallet/register-key",
            json={
                "address":          address,
                "signingPublicKey":  signing_public_key,
                "proof":             proof,
            },
        )

    async def transfer(
        self,
        from_addr: str,
        to: str,
        amount_poh: float,
        private_key_pem: str,
        fee: int = 0,
        memo: str = "",
    ) -> TxSubmitResult:
        """Build, sign, and submit a POH transfer in one call.

        Parameters
        ----------
        amount_poh:
            Amount in POH (not μPOH). Converted internally.
        private_key_pem:
            PKCS8 PEM-encoded Ed25519 private key used to sign the transaction.
        """
        from .signing import build_transfer, sign_transaction
        nonce_resp = await self.get_nonce(from_addr)
        tx         = build_transfer(from_addr, to, amount_poh, nonce_resp.nonce + 1, fee, memo)
        signed     = sign_transaction(tx, private_key_pem)
        return await self.submit_transaction(signed)

    # ── Sync convenience ──────────────────────────────────────────────────────

    @classmethod
    def sync(
        cls,
        base_url: Optional[str] = None,
        *,
        nodes:          Optional[List[str]] = None,
        api_key:        Optional[str] = None,
        wallet_address: Optional[str] = None,
        timeout:        float         = 30.0,
    ) -> "_SyncPohClient":
        """Return a synchronous wrapper around the async client.

        Example::

            poh = PohClient.sync("https://proofofhuman.ge")
            # or network mode:
            poh = PohClient.sync(nodes=["https://bootnode.proofofhuman.ge"])
            res = poh.scan("0xabc...")
        """
        return _SyncPohClient(
            base_url        = base_url,
            nodes           = nodes,
            api_key         = api_key,
            wallet_address  = wallet_address,
            timeout         = timeout,
        )


class _SyncPohClient:
    """Synchronous wrapper that runs an event loop internally."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        nodes: Optional[List[str]] = None,
        **kwargs: object,
    ) -> None:
        self._kwargs = {"base_url": base_url, "nodes": nodes, **kwargs}

    def _run(self, coro):  # type: ignore[no-untyped-def]
        return asyncio.get_event_loop().run_until_complete(coro)

    def _client(self) -> PohClient:
        return PohClient(**self._kwargs)  # type: ignore[arg-type]

    def scan(self, input: str, options: Optional[ScanOptions] = None) -> ScanResult:
        async def _go():
            async with self._client() as c:
                return await c.scan(input, options)
        return self._run(_go())

    def scan_bulk(self, inputs: List[str], options: Optional[ScanOptions] = None) -> BulkScanResult:
        async def _go():
            async with self._client() as c:
                return await c.scan_bulk(inputs, options)
        return self._run(_go())

    def get_job(self, job_id: str) -> JobStatus:
        async def _go():
            async with self._client() as c:
                return await c.get_job(job_id)
        return self._run(_go())

    def poll_job(self, job_id: str, options: Optional[PollOptions] = None) -> JobStatus:
        async def _go():
            async with self._client() as c:
                return await c.poll_job(job_id, options)
        return self._run(_go())

    def scan_and_wait(
        self,
        inputs: List[str],
        scan_options: Optional[ScanOptions] = None,
        poll_options: Optional[PollOptions] = None,
    ) -> JobStatus:
        async def _go():
            async with self._client() as c:
                return await c.scan_and_wait(inputs, scan_options, poll_options)
        return self._run(_go())

    def get_brain_verdict(self, brain_key: str) -> BrainVerdict:
        async def _go():
            async with self._client() as c:
                return await c.get_brain_verdict(brain_key)
        return self._run(_go())

    def poll_brain_verdict(
        self,
        brain_key: str,
        options: Optional[BrainPollOptions] = None,
    ) -> BrainVerdict:
        async def _go():
            async with self._client() as c:
                return await c.poll_brain_verdict(brain_key, options)
        return self._run(_go())

    def scan_and_verdict(
        self,
        input: str,
        scan_options:  Optional[ScanOptions]      = None,
        brain_options: Optional[BrainPollOptions] = None,
    ) -> ScanWithVerdict:
        async def _go():
            async with self._client() as c:
                return await c.scan_and_verdict(input, scan_options, brain_options)
        return self._run(_go())

    def get_methods(self, wallet_address: Optional[str] = None) -> List[Method]:
        async def _go():
            async with self._client() as c:
                return await c.get_methods(wallet_address)
        return self._run(_go())

    def submit_job(
        self, question: str, options: Optional[AskOptions] = None
    ) -> AskJobRef:
        async def _go():
            async with self._client() as c:
                return await c.submit_job(question, options)
        return self._run(_go())

    def run_compute(self, prompt: str, options: ComputeOptions) -> AskJobRef:
        async def _go():
            async with self._client() as c:
                return await c.run_compute(prompt, options)
        return self._run(_go())

    def get_job_status(self, job_id: str) -> AskJobStatus:
        async def _go():
            async with self._client() as c:
                return await c.get_job_status(job_id)
        return self._run(_go())

    def get_job_result(self, job_id: str) -> AskJobResult:
        async def _go():
            async with self._client() as c:
                return await c.get_job_result(job_id)
        return self._run(_go())

    def poll_job_result(
        self, job_id: str, options: Optional[PollOptions] = None
    ) -> AskJobResult:
        async def _go():
            async with self._client() as c:
                return await c.poll_job_result(job_id, options)
        return self._run(_go())

    def ask_and_wait(
        self,
        question: str,
        ask_options:  Optional[AskOptions]  = None,
        poll_options: Optional[PollOptions] = None,
    ) -> AskJobResult:
        async def _go():
            async with self._client() as c:
                return await c.ask_and_wait(question, ask_options, poll_options)
        return self._run(_go())

    def get_node_info(self) -> NodeInfo:
        async def _go():
            async with self._client() as c:
                return await c.get_node_info()
        return self._run(_go())

    def list_skills(self) -> List[Skill]:
        async def _go():
            async with self._client() as c:
                return await c.list_skills()
        return self._run(_go())

    def get_miner_info(self) -> "MinerInfo":
        async def _go():
            async with self._client() as c:
                return await c.get_miner_info()
        return self._run(_go())

    def get_balance(self, address: str) -> "WalletBalance":
        async def _go():
            async with self._client() as c:
                return await c.get_balance(address)
        return self._run(_go())

    def get_nonce(self, address: str) -> "AccountNonce":
        async def _go():
            async with self._client() as c:
                return await c.get_nonce(address)
        return self._run(_go())

    def get_transaction_history(self, address: str, limit: int = 30) -> "TxHistoryResult":
        async def _go():
            async with self._client() as c:
                return await c.get_transaction_history(address, limit)
        return self._run(_go())

    def get_transactions(self, address: str) -> dict:
        async def _go():
            async with self._client() as c:
                return await c.get_transactions(address)
        return self._run(_go())

    def get_pending_transactions(self) -> "PendingTxResult":
        async def _go():
            async with self._client() as c:
                return await c.get_pending_transactions()
        return self._run(_go())

    def submit_transaction(self, tx: "PohTxData") -> "TxSubmitResult":
        async def _go():
            async with self._client() as c:
                return await c.submit_transaction(tx)
        return self._run(_go())

    def register_signing_key(self, address: str, signing_public_key: str, proof: str) -> dict:
        async def _go():
            async with self._client() as c:
                return await c.register_signing_key(address, signing_public_key, proof)
        return self._run(_go())

    def transfer(
        self,
        from_addr: str,
        to: str,
        amount_poh: float,
        private_key_pem: str,
        fee: int = 0,
        memo: str = "",
    ) -> "TxSubmitResult":
        async def _go():
            async with self._client() as c:
                return await c.transfer(from_addr, to, amount_poh, private_key_pem, fee, memo)
        return self._run(_go())
