"""Tracks the timestamp of the last user-driven web request.

The dashboard's adaptive watchlist refresh uses this to skip polling
AniList while no one is watching the UI. HTTP middleware updates the
tracker on every non-noise request; background loops read it to decide
whether to do work.
"""

from __future__ import annotations

import time


class ActivityTracker:
    """Records the last time a user-driven HTTP request hit the app."""

    def __init__(self) -> None:
        self._last_activity: float = 0.0

    def mark_active(self) -> None:
        """Update the last-activity timestamp to now."""
        self._last_activity = time.monotonic()

    def seconds_since_activity(self) -> float:
        """Return seconds since the last activity, or +inf if never seen."""
        if self._last_activity == 0.0:
            return float("inf")
        return time.monotonic() - self._last_activity

    def is_recently_active(self, within_seconds: float) -> bool:
        """True if there was activity within the past *within_seconds* seconds."""
        return self.seconds_since_activity() <= within_seconds
