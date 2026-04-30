"""Tests for the token-bucket RateLimiter class in AnilistClient."""

import asyncio
import time
from unittest.mock import patch

import httpx
import pytest

from src.Clients.AnilistClient import SCAN_RESERVE_TOKENS, RateLimiter

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

    async def test_initial_tokens(self) -> None:
        rl = RateLimiter()
        assert rl._tokens == 90.0

    async def test_initial_capacity(self) -> None:
        rl = RateLimiter()
        assert rl._capacity == 90.0

    async def test_initial_refill_rate(self) -> None:
        rl = RateLimiter()
        assert rl._refill_rate == pytest.approx(90.0 / 60.0)

    async def test_custom_capacity(self) -> None:
        rl = RateLimiter(capacity=30.0)
        assert rl._limit == 30
        assert rl._tokens == 30.0
        assert rl._refill_rate == pytest.approx(30.0 / 60.0)


@pytest.mark.asyncio
class TestAcquire:
    async def test_acquire_decrements_tokens(self) -> None:
        rl = RateLimiter()
        await rl.acquire()
        assert rl._tokens == pytest.approx(89.0, abs=0.5)

    async def test_acquire_multiple_decrements(self) -> None:
        rl = RateLimiter()
        for _ in range(5):
            await rl.acquire()
        assert rl._tokens == pytest.approx(85.0, abs=0.5)

    async def test_acquire_high_priority_proceeds_with_low_tokens(self) -> None:
        """High-priority requests only need 1 token."""
        rl = RateLimiter()
        rl._tokens = 2.0
        rl._last_refill = time.monotonic()
        await asyncio.wait_for(rl.acquire(high_priority=True), timeout=2.0)
        assert rl._tokens < 2.0

    async def test_acquire_normal_pauses_at_scan_reserve(self) -> None:
        """Normal requests need SCAN_RESERVE_TOKENS + 1 tokens."""
        rl = RateLimiter()
        # Set tokens below the reserve threshold
        rl._tokens = float(SCAN_RESERVE_TOKENS)
        rl._last_refill = time.monotonic()

        sleep_durations: list[float] = []

        async def fake_sleep(duration: float) -> None:
            sleep_durations.append(duration)
            # Simulate time passing so refill works
            rl._tokens += duration * rl._refill_rate

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await asyncio.wait_for(rl.acquire(), timeout=3.0)
            # Should have slept to refill tokens above threshold
            assert len(sleep_durations) > 0

    async def test_acquire_high_priority_can_use_scan_reserve(self) -> None:
        """High-priority requests can use tokens within the reserve zone."""
        rl = RateLimiter()
        rl._tokens = float(SCAN_RESERVE_TOKENS)
        rl._last_refill = time.monotonic()
        # SCAN_RESERVE_TOKENS > 1, so high priority should proceed immediately
        await asyncio.wait_for(rl.acquire(high_priority=True), timeout=2.0)


@pytest.mark.asyncio
class TestUpdateFromHeaders:
    async def test_updates_limit(self) -> None:
        rl = RateLimiter()
        headers = _make_headers(**{"X-RateLimit-Limit": "30"})
        rl.update_from_headers(headers)
        assert rl._limit == 30
        assert rl._capacity == 30.0
        assert rl._refill_rate == pytest.approx(30.0 / 60.0)

    async def test_updates_remaining(self) -> None:
        rl = RateLimiter()
        headers = _make_headers(**{"X-RateLimit-Remaining": "40"})
        rl.update_from_headers(headers)
        assert rl._remaining == 40

    async def test_no_headers_is_safe(self) -> None:
        """Calling update_from_headers with empty headers should not crash."""
        rl = RateLimiter()
        rl.update_from_headers(_make_headers())
        assert rl._limit == 90


@pytest.mark.asyncio
class TestRefillOverTime:
    async def test_refill_adds_tokens_over_time(self) -> None:
        rl = RateLimiter()
        rl._tokens = 0.0
        # Pretend last refill was 1 second ago
        rl._last_refill = time.monotonic() - 1.0
        rl._refill()
        # Should have ~1.5 tokens (90/60 = 1.5/sec)
        assert rl._tokens == pytest.approx(1.5, abs=0.2)

    async def test_refill_caps_at_capacity(self) -> None:
        rl = RateLimiter()
        rl._tokens = 89.0
        rl._last_refill = time.monotonic() - 60.0  # long time ago
        rl._refill()
        assert rl._tokens == 90.0  # capped at capacity

    async def test_limit_change_adjusts_refill_rate(self) -> None:
        rl = RateLimiter()
        headers = _make_headers(**{"X-RateLimit-Limit": "30"})
        rl.update_from_headers(headers)
        assert rl._refill_rate == pytest.approx(30.0 / 60.0)


@pytest.mark.asyncio
class TestSleepOnLowTokens:
    async def test_sleeps_when_tokens_exhausted(self) -> None:
        """acquire() sleeps to wait for token refill when empty."""
        rl = RateLimiter()
        rl._tokens = 0.0
        rl._last_refill = time.monotonic()

        sleep_durations: list[float] = []

        async def fake_sleep(duration: float) -> None:
            sleep_durations.append(duration)
            rl._tokens += duration * rl._refill_rate

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await rl.acquire(high_priority=True)
            assert len(sleep_durations) > 0
            assert sleep_durations[0] > 0
