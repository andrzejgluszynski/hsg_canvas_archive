"""Read-only async Canvas API client."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..paths import fs_path
from .pagination import next_url
from .throttle import Throttle

log = logging.getLogger(__name__)

# Statuses that mean "this student may not see this resource". On a locked-down
# instance these are the common case, not an error -- they must never be retried
# and never surfaced as failures.
PERMISSION_STATUSES = frozenset({401, 403, 404})

CHUNK = 1024 * 1024


class ReadOnlyViolation(RuntimeError):
    """Raised if anything ever attempts a mutating request."""


@dataclass(slots=True)
class DownloadResult:
    path: Path
    bytes_written: int
    skipped: bool = False


def _is_rate_limited(response: httpx.Response) -> bool:
    """Canvas is mid-migration between 403-with-body and 429 for throttling."""
    if response.status_code == 429:
        return True
    if response.status_code == 403:
        try:
            return "rate limit exceeded" in response.text.lower()
        except Exception:
            return False
    return False


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        concurrency: int = 6,
        download_concurrency: int = 8,
        timeout: float = 30.0,
        retries: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/api/v1"
        self._token = token
        self.retries = max(1, retries)
        self.throttle = Throttle(concurrency)

        # File transfers get their own pool, deliberately separate from the API
        # rate-limit governor. They are bandwidth-bound, not quota-bound, and they run
        # against the CDN rather than the API. Sharing one semaphore meant a single
        # 400 MB lecture recording occupied an API slot for its entire transfer, which
        # throttled every other course on the account.
        self.download_slots = asyncio.Semaphore(max(1, download_concurrency))

        pool = concurrency + download_concurrency
        limits = httpx.Limits(max_connections=pool * 2, max_keepalive_connections=pool)
        # A separate read timeout is the point: the observed throttling failure is a
        # connection that is accepted and then never answered. Without this the run
        # freezes instead of retrying.
        timeouts = httpx.Timeout(timeout, connect=15.0, read=timeout, write=timeout, pool=15.0)
        common = dict(timeout=timeouts, limits=limits, follow_redirects=True, transport=transport)

        self._api_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            **common,
        )
        # Deliberately unauthenticated: Canvas file URLs carry their own `verifier`
        # capability token and redirect cross-host to S3/CloudFront, which reject or
        # mis-sign a stray Authorization header.
        self._file_client = httpx.AsyncClient(**common)

    async def aclose(self) -> None:
        await self._api_client.aclose()
        await self._file_client.aclose()

    async def __aenter__(self) -> CanvasClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.api}/{path.lstrip('/')}"

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Any = None,
        max_attempts: int | None = None,
    ) -> httpx.Response:
        max_attempts = max_attempts or self.retries
        for attempt in range(max_attempts):
            await self.throttle.acquire()
            try:
                response = await client.get(url, params=params)
            except httpx.TransportError as exc:
                self.throttle.release()
                await self.throttle.record_failure()
                if attempt == max_attempts - 1:
                    raise
                log.debug("transport error on %s (%s), retrying", url, exc)
                await self.throttle.backoff(attempt)
                continue
            else:
                self.throttle.observe(response.headers)
                self.throttle.release()

            if _is_rate_limited(response):
                await self.throttle.record_failure()
                if attempt == max_attempts - 1:
                    response.raise_for_status()
                log.debug("rate limited on %s, backing off", url)
                await self.throttle.backoff(attempt, response.headers.get("retry-after"))
                continue

            if response.status_code >= 500:
                await self.throttle.record_failure()
                if attempt == max_attempts - 1:
                    return response
                await self.throttle.backoff(attempt)
                continue

            self.throttle.record_success()
            return response

        raise RuntimeError(f"exhausted retries for {url}")

    async def get(self, path: str, **params: Any) -> Any:
        response = await self._request(self._api_client, self._url(path), params=params or None)
        response.raise_for_status()
        return response.json()

    async def get_optional(self, path: str, **params: Any) -> Any | None:
        """Return None when the student simply may not see this resource."""
        response = await self._request(self._api_client, self._url(path), params=params or None)
        if response.status_code in PERMISSION_STATUSES:
            log.debug("skip %s -> HTTP %s", path, response.status_code)
            return None
        response.raise_for_status()
        return response.json()

    async def paginate(self, path: str, **params: Any) -> AsyncIterator[dict]:
        """Walk a collection via rel="next". Yields nothing if access is denied."""
        params.setdefault("per_page", 100)
        url: str | None = self._url(path)
        first = True
        # A malformed or looping Link header would otherwise spin forever.
        seen: set[str] = set()

        while url:
            if url in seen:
                log.warning("pagination loop detected at %s, stopping", path)
                return
            seen.add(url)
            response = await self._request(self._api_client, url, params=params if first else None)
            if first and response.status_code in PERMISSION_STATUSES:
                log.debug("skip collection %s -> HTTP %s", path, response.status_code)
                return
            response.raise_for_status()

            payload = response.json()
            if not isinstance(payload, list):
                return
            for item in payload:
                yield item

            url = next_url(response.headers.get("link"))
            first = False

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: int | None = None,
        refresh=None,
        on_bytes=None,
    ) -> DownloadResult:
        """Stream to `<dest>.part`, resuming byte ranges across retries.

        A failed transfer keeps its partial file. At ~5.5 MB average file size on a
        flaky connection, restarting from zero each time is the difference between a
        run that finishes and one that never does.

        `refresh` returns a fresh URL: Canvas file verifiers expire, so a long run hits
        a wave of 403s hours in.
        """
        if (
            expected_size is not None
            and fs_path(dest).exists()
            and fs_path(dest).stat().st_size == expected_size
        ):
            return DownloadResult(dest, expected_size, skipped=True)

        fs_path(dest.parent).mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        refreshed = False
        last_error: Exception | None = None

        for attempt in range(self.retries):
            resume_from = fs_path(part).stat().st_size if fs_path(part).exists() else 0
            # Never trust a partial larger than the target: restart instead.
            if expected_size is not None and resume_from >= expected_size:
                resume_from = 0
                fs_path(part).unlink(missing_ok=True)

            headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}

            try:
                await self.download_slots.acquire()
                try:
                    async with self._file_client.stream("GET", url, headers=headers) as response:
                        if response.status_code in PERMISSION_STATUSES:
                            # Most likely an expired verifier rather than a real denial.
                            if refresh and not refreshed:
                                refreshed = True
                                fresh = await refresh()
                                if fresh:
                                    url = fresh
                                    continue
                            response.raise_for_status()

                        # A server that ignored our Range restarts the file.
                        if resume_from and response.status_code != 206:
                            resume_from = 0
                            part.unlink(missing_ok=True)

                        response.raise_for_status()
                        mode = "ab" if resume_from else "wb"
                        with fs_path(part).open(mode) as handle:
                            async for chunk in response.aiter_bytes(CHUNK):
                                handle.write(chunk)
                                if on_bytes:
                                    on_bytes(len(chunk))
                finally:
                    self.download_slots.release()

            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                await self.throttle.record_failure()
                if attempt == self.retries - 1:
                    break
                log.debug("download retry %d for %s (%s)", attempt + 1, dest.name, exc)
                await self.throttle.backoff(attempt)
                continue

            written = fs_path(part).stat().st_size if fs_path(part).exists() else 0
            if expected_size is not None and written != expected_size:
                # Truncated transfer: keep the partial and let the next attempt resume.
                last_error = RuntimeError(f"got {written} of {expected_size} bytes")
                if attempt == self.retries - 1:
                    break
                await self.throttle.backoff(attempt)
                continue

            os.replace(fs_path(part), fs_path(dest))
            self.throttle.record_success()
            return DownloadResult(dest, written)

        raise RuntimeError(f"failed after {self.retries} attempts: {last_error}")
