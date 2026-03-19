"""Tests for the RateLimiter class in AnilistClient."""

import asyncio
import time
from unittest.mock import patch

import httpx
import pytest

from src.Clients.AnilistClient import RateLimiter, SCAN_RESERVE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_headers(**kwargs: str) -> httpx.Headers:
    """Build httpx.Headers from keyword arguments."""
    return httpx.Headers(kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRateLimiterInitialState:
    async def test_initial_limit(self) -> None:
        rl = RateLimiter()
        assert rl._limit == 90

    async def test_initial_remaining(self) -> None:
        rl = RateLimiter()
        assert rl._remaining == 90

    async def test_initial_reset_at(self) -> None:
        rl = RateLimiter()
        assert rl._reset_at == 0.0


@pytest.mark.asyncio
class TestAcquire:
    async def test_acquire_decrements_remaining(self) -> None:
        rl = RateLimiter()
        # Set last_response to now so gap enforcement doesn't sleep
        rl._last_response = time.monotonic() - 10
        await rl.acquire()
        assert rl._remaining == 89

    async def test_acquire_multiple_decrements(self) -> None:
        rl = RateLimiter()
        rl._last_response = time.monotonic() - 10
        for _ in range(5):
            await rl.acquire()
        assert rl._remaining == 85

    async def test_acquire_high_priority_respects_lower_threshold(self) -> None:
        """High-priority requests only block when remaining <= 1."""
        rl = RateLimiter()
        rl._last_response = time.monotonic() - 10
        # Set remaining to just above the high-priority threshold (1)
        rl._remaining = 3
        await rl.acquire(high_priority=True)
        assert rl._remaining == 2

    async def test_acquire_normal_pauses_at_scan_reserve(self) -> None:
        """Normal (non-high-priority) requests pause at SCAN_RESERVE."""
        rl = RateLimiter()
        rl._last_response = time.monotonic() - 10
        # Set remaining to exactly at SCAN_RESERVE — should block
        rl._remaining = SCAN_RESERVE
        # Set reset_at in the near past so the window resets immediately
        rl._reset_at = time.monotonic() - 0.01

        # This should succeed because the window reset restores budget
        await asyncio.wait_for(rl.acquire(), timeout=3.0)
        # After window reset, remaining should be limit - 1
        assert rl._remaining == rl._limit - 1

    async def test_acquire_high_priority_can_use_scan_reserve(self) -> None:
        """High-priority requests can use capacity within the SCAN_RESERVE zone."""
        rl = RateLimiter()
        rl._last_response = time.monotonic() - 10
        rl._remaining = SCAN_RESERVE  # 10
        # High priority threshold is 1, so 10 > 1 => should proceed
        await asyncio.wait_for(rl.acquire(high_priority=True), timeout=2.0)
        assert rl._remaining == SCAN_RESERVE - 1


@pytest.mark.asyncio
class TestUpdateFromHeaders:
    async def test_updates_limit(self) -> None:
        rl = RateLimiter()
        headers = _make_headers(**{"X-RateLimit-Limit": "30"})
        rl.update_from_headers(headers)
        assert rl._limit == 30

    async def test_updates_remaining_only_if_lower(self) -> None:
        rl = RateLimiter()
        rl._remaining = 50
        # Server says 40 — lower, so should update
        headers = _make_headers(**{"X-RateLimit-Remaining": "40"})
        rl.update_from_headers(headers)
        assert rl._remaining == 40

    async def test_does_not_inflate_remaining(self) -> None:
        rl = RateLimiter()
        rl._remaining = 30
        # Server says 50 — higher, so should NOT update
        headers = _make_headers(**{"X-RateLimit-Remaining": "50"})
        rl.update_from_headers(headers)
        assert rl._remaining == 30

    async def test_updates_reset_at(self) -> None:
        rl = RateLimiter()
        # Set reset_at to a future epoch time
        future_epoch = int(time.time()) + 60
        headers = _make_headers(**{"X-RateLimit-Reset": str(future_epoch)})
        rl.update_from_headers(headers)
        assert rl._reset_at > 0

    async def test_reset_at_never_moves_backwards(self) -> None:
        rl = RateLimiter()
        # Set to a far future reset
        far_future = int(time.time()) + 600
        headers1 = _make_headers(**{"X-RateLimit-Reset": str(far_future)})
        rl.update_from_headers(headers1)
        saved_reset = rl._reset_at

        # Try to set to an earlier reset
        near_future = int(time.time()) + 10
        headers2 = _make_headers(**{"X-RateLimit-Reset": str(near_future)})
        rl.update_from_headers(headers2)
        assert rl._reset_at == saved_reset

    async def test_updates_last_response_time(self) -> None:
        rl = RateLimiter()
        before = time.monotonic()
        headers = _make_headers()
        rl.update_from_headers(headers)
        after = time.monotonic()
        assert before <= rl._last_response <= after

    async def test_no_headers_is_safe(self) -> None:
        """Calling update_from_headers with empty headers should not crash."""
        rl = RateLimiter()
        rl.update_from_headers(_make_headers())
        # Should still have initial values
        assert rl._limit == 90
        assert rl._remaining == 90


@pytest.mark.asyncio
class TestRefillOverTime:
    async def test_window_reset_restores_budget(self) -> None:
        """When the rate window expires, remaining is restored to limit."""
        rl = RateLimiter()
        rl._last_response = time.monotonic() - 10
        rl._remaining = 5
        # Set reset_at to the past so the window is expired
        rl._reset_at = time.monotonic() - 1.0

        await rl.acquire()
        # After window reset, remaining = limit - 1 (one consumed by acquire)
        assert rl._remaining == rl._limit - 1

    async def test_min_gap_scales_with_limit(self) -> None:
        rl = RateLimiter()
        assert rl._min_gap == pytest.approx(60.0 / 90 * 1.1, abs=0.01)
        rl._limit = 30
        assert rl._min_gap == pytest.approx(60.0 / 30 * 1.1, abs=0.01)


@pytest.mark.asyncio
class TestMinGapEnforcement:
    async def test_gap_enforcement_sleeps_when_too_fast(self) -> None:
        """Phase 2 of acquire() sleeps if called faster than min_gap."""
        rl = RateLimiter()
        rl._remaining = 90
        rl._last_response = time.monotonic()  # "just responded"

        sleep_durations: list[float] = []

        async def fake_sleep(duration: float) -> None:
            sleep_durations.append(duration)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await rl.acquire()
            # Should have slept for the gap enforcement
            assert len(sleep_durations) > 0
            assert sleep_durations[-1] > 0
