"""Tests for the ActivityTracker used by the adaptive watchlist refresh."""

from __future__ import annotations

import time

from src.Web.ActivityTracker import ActivityTracker


class TestActivityTracker:
    def test_initial_state_reports_no_activity(self) -> None:
        tracker = ActivityTracker()
        assert tracker.seconds_since_activity() == float("inf")
        assert tracker.is_recently_active(within_seconds=1.0) is False

    def test_mark_active_resets_timer(self) -> None:
        tracker = ActivityTracker()
        tracker.mark_active()
        assert tracker.seconds_since_activity() < 1.0
        assert tracker.is_recently_active(within_seconds=10.0) is True

    def test_recent_activity_threshold(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        tracker = ActivityTracker()
        # Pin monotonic so we can simulate elapsed time.
        now = [1000.0]

        def fake_monotonic() -> float:
            return now[0]

        monkeypatch.setattr(time, "monotonic", fake_monotonic)

        tracker.mark_active()
        now[0] = 1005.0
        assert tracker.is_recently_active(within_seconds=10.0) is True
        now[0] = 1100.0
        assert tracker.is_recently_active(within_seconds=10.0) is False
