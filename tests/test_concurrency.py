"""Concurrency: several courses in flight at once.

Correctness under concurrency is easy to get wrong in ways that only show up on a real
account -- interleaved writes, lost counters, order-dependent output. These tests use a
multi-course fake instance with artificial latency so the behaviour is observable.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx

from canvas_archive.archiver import Archiver
from canvas_archive.http.client import CanvasClient

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"
LATENCY = 0.05
N_COURSES = 8


def _mount_many(n: int = N_COURSES, *, latency: float = LATENCY, failing: set[int] | None = None):
    """A fake instance with `n` courses, each response costing `latency` seconds."""
    failing = failing or set()
    state = {"peak": 0, "active": 0}

    async def slow(request):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(latency)
        finally:
            state["active"] -= 1

        url = str(request.url)
        if "/users/self/enrollments" in url:
            return httpx.Response(200, json=[])
        if "/users/self" in url:
            return httpx.Response(200, json={"id": 1, "name": "Student"})
        if "/api/v1/courses?" in url:
            if "enrollment_state=active" not in url:
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {"id": i, "name": f"Course {i:02d}", "course_code": f"C{i}"}
                    for i in range(1, n + 1)
                ],
            )
        for cid in failing:
            if f"/courses/{cid}/modules" in url:
                return httpx.Response(500)
        if "/modules" in url:
            return httpx.Response(200, json=[{"id": 1, "name": "Week 1", "items": []}])
        return httpx.Response(200, json=[])

    respx.route().mock(side_effect=slow)
    return state


@pytest.fixture
def client():
    return CanvasClient(BASE, "1~tok", concurrency=16, retries=1)


@respx.mock
async def test_courses_are_archived_concurrently(client, tmp_path):
    """Wall-clock must beat the sequential lower bound by a clear margin."""
    _mount_many()
    started = time.monotonic()
    stats = await Archiver(client, tmp_path, build_html=False, course_workers=4).run()
    elapsed = time.monotonic() - started

    assert stats.courses == N_COURSES
    # Each course makes several sequential round-trips; done one course at a time this
    # could not finish anywhere near this quickly.
    sequential_floor = N_COURSES * LATENCY * 3
    assert elapsed < sequential_floor, f"{elapsed:.2f}s suggests courses ran sequentially"
    await client.aclose()


@respx.mock
async def test_more_workers_is_faster_than_one(client, tmp_path):
    _mount_many(n=6)
    t0 = time.monotonic()
    await Archiver(client, tmp_path / "serial", build_html=False, course_workers=1).run()
    serial = time.monotonic() - t0

    t0 = time.monotonic()
    await Archiver(client, tmp_path / "parallel", build_html=False, course_workers=6).run()
    parallel = time.monotonic() - t0

    assert parallel < serial * 0.7, f"parallel {parallel:.2f}s vs serial {serial:.2f}s"
    await client.aclose()


@respx.mock
async def test_worker_count_is_respected(client, tmp_path):
    """course_workers=1 must genuinely serialise, whatever the HTTP concurrency is."""
    _mount_many(n=4)
    archiver = Archiver(client, tmp_path, build_html=False, course_workers=1)

    in_flight = {"now": 0, "peak": 0}
    original = archiver.archive_course

    async def counted(course, grades, handle=None):
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        try:
            return await original(course, grades, handle)
        finally:
            in_flight["now"] -= 1

    archiver.archive_course = counted
    await archiver.run()
    assert in_flight["peak"] == 1
    await client.aclose()


@respx.mock
async def test_every_course_lands_on_disk(client, tmp_path):
    """Concurrent writers must not lose or collide with one another."""
    _mount_many()
    await Archiver(client, tmp_path, build_html=False, course_workers=5).run()
    dirs = sorted(p.name for p in (tmp_path / "courses").iterdir())
    assert len(dirs) == N_COURSES
    for i in range(1, N_COURSES + 1):
        assert any(d.endswith(f"__{i}") for d in dirs), f"course {i} missing"
    await client.aclose()


@respx.mock
async def test_index_order_is_deterministic(client, tmp_path):
    """Courses finish in arbitrary order; the index must not."""
    _mount_many()
    a = Archiver(client, tmp_path / "a", build_html=False, course_workers=5)
    await a.run()
    b = Archiver(client, tmp_path / "b", build_html=False, course_workers=2)
    await b.run()
    assert [e["name"] for e in a._index] == [e["name"] for e in b._index]
    assert [e["name"] for e in a._index] == sorted((e["name"] for e in a._index), key=str.lower)
    await client.aclose()


@respx.mock
async def test_one_failing_course_does_not_affect_the_others(client, tmp_path):
    _mount_many(failing={3})
    stats = await Archiver(client, tmp_path, build_html=False, course_workers=4).run()
    # Course 3's modules 500. It is still archived -- just with less in it -- and the
    # failure is reported rather than swallowed.
    assert stats.courses == N_COURSES
    assert len(list((tmp_path / "courses").iterdir())) == N_COURSES
    assert any("modules" in e and "course 3" in e for e in stats.errors)
    await client.aclose()


@respx.mock
async def test_counters_survive_concurrent_updates(client, tmp_path):
    _mount_many()
    stats = await Archiver(client, tmp_path, build_html=False, course_workers=8).run()
    assert stats.courses == N_COURSES
    assert stats.json_records["modules"] == N_COURSES


# --- download pool is separate from the API rate-limit governor ---------------


@respx.mock
async def test_a_slow_download_does_not_block_api_calls(client, tmp_path):
    """A big lecture recording must not stall every other course.

    Downloads and API calls previously shared one semaphore, so a single large
    transfer occupied a rate-limit slot for its whole duration.
    """
    api_done = []

    async def slow_file(request):
        await asyncio.sleep(0.4)
        return httpx.Response(200, content=b"x" * 10)

    async def quick_api(request):
        await asyncio.sleep(0.01)
        api_done.append(time.monotonic())
        return httpx.Response(200, json={"ok": True})

    respx.get(url__startswith="https://files.test/big").mock(side_effect=slow_file)
    respx.get(url__startswith=f"{API}/ping").mock(side_effect=quick_api)

    started = time.monotonic()
    download = asyncio.create_task(client.download("https://files.test/big", tmp_path / "big.bin"))
    await asyncio.sleep(0.05)  # let the download take its slot

    # Saturate the API pool while the download is still streaming.
    await asyncio.gather(*(client.get("ping") for _ in range(6)))
    api_elapsed = time.monotonic() - started

    assert len(api_done) == 6
    assert api_elapsed < 0.3, f"API calls waited {api_elapsed:.2f}s behind the download"
    await download
    await client.aclose()


@respx.mock
async def test_download_pool_size_is_respected(client, tmp_path):
    peak = {"now": 0, "max": 0}

    async def tracked(request):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        try:
            await asyncio.sleep(0.05)
            return httpx.Response(200, content=b"z")
        finally:
            peak["now"] -= 1

    respx.get(url__startswith="https://files.test/").mock(side_effect=tracked)
    small = CanvasClient(BASE, "1~tok", concurrency=6, download_concurrency=3, retries=1)
    await asyncio.gather(
        *(small.download(f"https://files.test/{i}", tmp_path / f"f{i}") for i in range(9))
    )
    assert peak["max"] <= 3
    await small.aclose()
    await client.aclose()


@respx.mock
async def test_all_courses_run_at_once_by_default(client, tmp_path):
    """course_workers=0 means every course starts, bounded only by the HTTP pools."""
    _mount_many(n=10)
    archiver = Archiver(client, tmp_path, build_html=False)  # default workers
    assert archiver.course_workers == 0

    in_flight = {"now": 0, "peak": 0}
    original = archiver.archive_course

    async def counted(course, grades, handle=None):
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        try:
            return await original(course, grades, handle)
        finally:
            in_flight["now"] -= 1

    archiver.archive_course = counted
    await archiver.run()
    assert in_flight["peak"] == 10, f"only {in_flight['peak']} courses ran together"
    await client.aclose()
