"""
Proof of Human Python SDK — async + sync client.

Async usage (recommended):
    import asyncio
    from poh_sdk import PohClient

    async def main():
        async with PohClient("https://proofofhuman.ge", api_key="...") as poh:
            res = await poh.scan("0xabc...")
            print(res.result)

    asyncio.run(main())

Sync usage:
    from poh_sdk import PohClient

    poh = PohClient.sync("https://proofofhuman.ge", api_key="...")
    res = poh.scan_sync("0xabc...")
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, List, Optional
from urllib.parse import quote

import httpx

from .types import (
    BrainPollOptions,
    BrainVerdict,
    BulkScanResult,
    JobStatus,
    Method,
    PollOptions,
    ScanOptions,
    ScanResult,
    ScanWithVerdict,
)


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
        Base URL of the POH API, e.g. ``"https://proofofhuman.ge"``.
    api_key:
        API key for paid tier.
    wallet_address:
        Solana wallet address for free-tier request tracking.
    timeout:
        Per-request timeout in seconds (default: 30).
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key:        Optional[str] = None,
        wallet_address: Optional[str] = None,
        timeout:        float         = 30.0,
    ) -> None:
        self._base_url       = base_url.rstrip("/")
        self._api_key        = api_key
        self._wallet_address = wallet_address
        headers: dict = {"Accept": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.AsyncClient(
            base_url = self._base_url,
            headers  = headers,
            timeout  = timeout,
        )

    async def __aenter__(self) -> "PohClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, **kwargs: object) -> dict:
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

    # ── Sync convenience ──────────────────────────────────────────────────────

    @classmethod
    def sync(
        cls,
        base_url: str,
        *,
        api_key:        Optional[str] = None,
        wallet_address: Optional[str] = None,
        timeout:        float         = 30.0,
    ) -> "_SyncPohClient":
        """Return a synchronous wrapper around the async client.

        Example::

            poh = PohClient.sync("https://proofofhuman.ge")
            res = poh.scan_sync("0xabc...")
        """
        return _SyncPohClient(
            base_url        = base_url,
            api_key         = api_key,
            wallet_address  = wallet_address,
            timeout         = timeout,
        )


class _SyncPohClient:
    """Synchronous wrapper that runs an event loop internally."""

    def __init__(self, **kwargs: object) -> None:
        self._kwargs = kwargs

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
