"""Download resilience: the behaviour that decides whether a big run finishes."""

from __future__ import annotations

import httpx
import pytest
import respx

from canvas_archive.http.client import CanvasClient

BASE = "https://canvas.test"
FILE_URL = "https://files.test/doc.pdf"
BODY = b"x" * 5000


@pytest.fixture
def client():
    c = CanvasClient(BASE, "1~tok", concurrency=2, retries=4)
    # Backoff is tested separately; keep the suite fast.
    c.throttle.backoff = lambda *a, **k: _noop()
    return c


async def _noop():
    return None


@respx.mock
async def test_retries_through_rate_limiting(client, tmp_path):
    """Two 429s then success must complete with no user intervention."""
    route = respx.get(FILE_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, content=BODY),
        ]
    )
    result = await client.download(FILE_URL, tmp_path / "doc.pdf", expected_size=len(BODY))
    assert route.call_count == 3
    assert result.bytes_written == len(BODY)
    assert (tmp_path / "doc.pdf").read_bytes() == BODY
    await client.aclose()


@respx.mock
async def test_truncated_transfer_resumes_by_range(client, tmp_path):
    """A short first response must be resumed, not restarted."""
    calls: list[str | None] = []

    def handler(request):
        calls.append(request.headers.get("Range"))
        if len(calls) == 1:
            return httpx.Response(200, content=BODY[:2000])  # truncated
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        return httpx.Response(206, content=BODY[start:])

    respx.get(FILE_URL).mock(side_effect=handler)
    result = await client.download(FILE_URL, tmp_path / "doc.pdf", expected_size=len(BODY))

    assert calls[0] is None  # first attempt asks for the whole file
    assert calls[1] == "bytes=2000-"  # second resumes from what landed
    assert result.bytes_written == len(BODY)
    assert (tmp_path / "doc.pdf").read_bytes() == BODY
    await client.aclose()


@respx.mock
async def test_server_ignoring_range_restarts_cleanly(client, tmp_path):
    """A 200 in reply to a Range request means start over, not append."""

    def handler(request):
        if not hasattr(handler, "seen"):
            handler.seen = True
            return httpx.Response(200, content=BODY[:2000])
        return httpx.Response(200, content=BODY)  # ignores Range

    respx.get(FILE_URL).mock(side_effect=handler)
    await client.download(FILE_URL, tmp_path / "doc.pdf", expected_size=len(BODY))
    assert (tmp_path / "doc.pdf").read_bytes() == BODY  # not 2000 bytes of garbage + body
    await client.aclose()


@respx.mock
async def test_expired_verifier_is_refreshed_once(client, tmp_path):
    """Canvas file URLs expire mid-run; a 403 should refresh rather than fail."""
    fresh_url = "https://files.test/fresh-doc.pdf"
    respx.get(FILE_URL).mock(return_value=httpx.Response(403))
    respx.get(fresh_url).mock(return_value=httpx.Response(200, content=BODY))

    async def refresh():
        return fresh_url

    result = await client.download(
        FILE_URL, tmp_path / "doc.pdf", expected_size=len(BODY), refresh=refresh
    )
    assert result.bytes_written == len(BODY)
    await client.aclose()


@respx.mock
async def test_persistent_failure_raises_after_budget(client, tmp_path):
    route = respx.get(FILE_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(RuntimeError):
        await client.download(FILE_URL, tmp_path / "doc.pdf", expected_size=len(BODY))
    assert route.call_count == 4  # honours the retry budget
    assert not (tmp_path / "doc.pdf").exists()
    await client.aclose()


@respx.mock
async def test_partial_bytes_survive_for_a_later_run(client, tmp_path):
    """Bytes that did arrive must be kept, so re-running resumes instead of restarting."""
    respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=BODY[:1500]))
    with pytest.raises(RuntimeError):
        await client.download(FILE_URL, tmp_path / "doc.pdf", expected_size=len(BODY))
    part = tmp_path / "doc.pdf.part"
    assert part.exists() and part.stat().st_size == 1500
    await client.aclose()


@respx.mock
async def test_already_complete_file_is_not_refetched(client, tmp_path):
    dest = tmp_path / "doc.pdf"
    dest.write_bytes(BODY)
    route = respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=BODY))
    result = await client.download(FILE_URL, dest, expected_size=len(BODY))
    assert result.skipped
    assert route.call_count == 0
    await client.aclose()


@respx.mock
async def test_permission_denied_is_not_retried(client):
    """403/404 on the API are student permission limits, not transient faults."""
    route = respx.get(f"{BASE}/api/v1/courses/1/files").mock(return_value=httpx.Response(403))
    assert await client.get_optional("courses/1/files") is None
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_paginate_walks_link_headers(client):
    def handler(request):
        if "page=2" in str(request.url):
            return httpx.Response(200, json=[{"id": 2}])
        return httpx.Response(
            200,
            json=[{"id": 1}],
            headers={"Link": f'<{BASE}/api/v1/things?page=2>; rel="next"'},
        )

    respx.get(url__startswith=f"{BASE}/api/v1/things").mock(side_effect=handler)
    got = [item async for item in client.paginate("things")]
    assert [i["id"] for i in got] == [1, 2]
    await client.aclose()


@respx.mock
async def test_denied_collection_yields_nothing(client):
    respx.get(f"{BASE}/api/v1/courses/1/files").mock(return_value=httpx.Response(403))
    assert [i async for i in client.paginate("courses/1/files")] == []
    await client.aclose()


@respx.mock
async def test_pagination_loop_is_broken(client):
    """A Link header pointing back at itself must not spin forever."""
    respx.get(url__startswith=f"{BASE}/api/v1/loop").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1}],
            headers={"Link": f'<{BASE}/api/v1/loop>; rel="next"'},
        )
    )
    got = [item async for item in client.paginate("loop")]
    assert got == [{"id": 1}]
    await client.aclose()
