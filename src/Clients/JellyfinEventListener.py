"""Persistent WebSocket listener for Jellyfin server events.

Maintains a single WebSocket connection to the Jellyfin server, subscribes
to ``ScheduledTasksInfo`` updates, and detects two types of events:

1. **Library scan completion** (``RefreshLibrary`` task) — fires the
   ``on_scan_complete`` callback and sets an :class:`asyncio.Event` that
   callers can ``await``.
2. **New items added** (``WebhookItemAdded`` task) — fires the same
   callback after a debounce delay, catching auto-detected media that
   bypasses the scheduled task system.

Connection URL: ``ws(s)://JELLYFIN_URL/socket?ApiKey=API_KEY``
No plugin required — this is core Jellyfin server functionality.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

logger = logging.getLogger(__name__)

# Silence the noisy websockets protocol-level debug logs.
logging.getLogger("websockets").setLevel(logging.WARNING)

# How often Jellyfin pushes ScheduledTasksInfo after we subscribe (ms).
_TASK_INFO_INTERVAL_MS = 2000

# Seconds to wait after the last ItemAdded notifier completes before
# running cleanup.  This debounces rapid-fire additions so we only
# clean up once after all items have been processed.
_ITEM_ADDED_DEBOUNCE_S = 15.0


class JellyfinEventListener:
    """Persistent WebSocket connection to Jellyfin for real-time events.

    Always subscribed to ``ScheduledTasksInfo`` so we catch scans
    triggered by Jellyfin's own scheduler, not just ones we initiate.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        on_scan_complete: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        base = url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[8:]
        elif base.startswith("http://"):
            base = "ws://" + base[7:]
        self._ws_url = f"{base}/socket?ApiKey={api_key}"

        self._on_scan_complete = on_scan_complete

        # Scan state (RefreshLibrary task)
        self._scan_complete = asyncio.Event()
        self._scan_complete.set()  # Start as "no scan in progress"
        self._scan_progress: float = 0.0
        self._scan_running: bool = False

        # Item-added state (WebhookItemAdded task)
        self._item_added_running: bool = False
        self._item_added_debounce: asyncio.Task | None = None  # type: ignore[type-arg]

        # Lifecycle
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the listener as a background task."""
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever(), name="jellyfin-ws")
        logger.info("Jellyfin WebSocket listener started")

    async def stop(self) -> None:
        """Shut down the WebSocket connection."""
        self._running = False
        if self._item_added_debounce and not self._item_added_debounce.done():
            self._item_added_debounce.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Jellyfin WebSocket listener stopped")

    async def wait_for_scan_complete(self, timeout: float = 300.0) -> bool:
        """Await scan completion.

        Returns True if a scan completed (or no scan was running),
        False on timeout.
        """
        try:
            await asyncio.wait_for(self._scan_complete.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "Jellyfin scan wait timed out after %.0fs",
                timeout,
            )
            return False

    @property
    def scan_progress(self) -> float:
        """Current scan progress (0-100), updated in real-time."""
        return self._scan_progress

    @property
    def is_scan_running(self) -> bool:
        """Whether a library scan is currently in progress."""
        return self._scan_running

    @property
    def connected(self) -> bool:
        """Whether the WebSocket is currently connected."""
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run_forever(self) -> None:
        """Connect, listen, and auto-reconnect on failure."""
        backoff = 2.0
        max_backoff = 60.0

        while self._running:
            try:
                await self._connect_and_listen()
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._running:
                    break
                logger.warning(
                    "Jellyfin WebSocket disconnected — reconnecting in %.0fs",
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _connect_and_listen(self) -> None:
        """Single WebSocket session: connect, subscribe, process messages."""
        display_url = self._ws_url.split("?")[0] + "?ApiKey=***"
        logger.info("Jellyfin WebSocket connecting to %s", display_url)

        async with websockets.connect(
            self._ws_url,
            ping_interval=None,  # Jellyfin uses its own KeepAlive protocol
        ) as ws:
            logger.info("Jellyfin WebSocket connected")

            # Subscribe to task status updates immediately so we catch
            # scans triggered by Jellyfin's own scheduler.
            await ws.send(
                json.dumps(
                    {
                        "MessageType": "ScheduledTasksInfoStart",
                        "Data": f"0,{_TASK_INFO_INTERVAL_MS}",
                    }
                )
            )

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = msg.get("MessageType", "")

                if msg_type == "ForceKeepAlive":
                    await ws.send(json.dumps({"MessageType": "KeepAlive"}))

                elif msg_type == "ScheduledTasksInfo":
                    await self._handle_task_info(msg.get("Data", []))

    async def _handle_task_info(self, tasks: list) -> None:
        """Process a ScheduledTasksInfo update — detect task transitions."""
        if not isinstance(tasks, list):
            return

        for task in tasks:
            key = task.get("Key", "")
            if key == "RefreshLibrary":
                self._handle_scan_task(task)
            elif key == "WebhookItemAdded":
                self._handle_item_added_task(task)

    def _handle_scan_task(self, task: dict) -> None:
        """Detect RefreshLibrary Running → Idle transitions."""
        state = task.get("State", "Idle")
        progress = task.get("CurrentProgressPercentage") or 0.0
        was_running = self._scan_running

        if state == "Running":
            self._scan_running = True
            self._scan_progress = progress
            if not was_running:
                self._scan_complete.clear()
                logger.info("Jellyfin library scan started (via WebSocket)")
        elif was_running:
            self._scan_running = False
            self._scan_progress = 0.0

            result = task.get("LastExecutionResult") or {}
            status = result.get("Status", "Unknown")
            logger.info(
                "Jellyfin library scan completed: %s (via WebSocket)",
                status,
            )

            self._scan_complete.set()
            self._fire_scan_complete_callback()

    def _handle_item_added_task(self, task: dict) -> None:
        """Detect WebhookItemAdded Running → Idle and debounce cleanup."""
        state = task.get("State", "Idle")
        was_running = self._item_added_running

        if state == "Running":
            self._item_added_running = True
            # Cancel any pending debounce — more items are coming
            if self._item_added_debounce and not self._item_added_debounce.done():
                self._item_added_debounce.cancel()
        elif was_running:
            self._item_added_running = False
            logger.info(
                "Jellyfin item-added notifier finished — "
                "scheduling cleanup in %.0fs",
                _ITEM_ADDED_DEBOUNCE_S,
            )
            # Cancel any prior debounce and start a new one
            if self._item_added_debounce and not self._item_added_debounce.done():
                self._item_added_debounce.cancel()
            self._item_added_debounce = asyncio.create_task(
                self._debounced_item_added_cleanup()
            )

    async def _debounced_item_added_cleanup(self) -> None:
        """Wait for the debounce period, then fire the cleanup callback."""
        try:
            await asyncio.sleep(_ITEM_ADDED_DEBOUNCE_S)
            logger.info("Running virtual cleanup after new items detected")
            self._fire_scan_complete_callback()
        except asyncio.CancelledError:
            pass  # Debounce was reset — another batch of items arrived

    def _fire_scan_complete_callback(self) -> None:
        """Schedule the on_scan_complete callback as a background task."""
        if self._on_scan_complete:
            asyncio.create_task(self._run_callback())

    async def _run_callback(self) -> None:
        """Execute the scan-complete callback with error handling."""
        try:
            await self._on_scan_complete()  # type: ignore[misc]
        except Exception:
            logger.exception("Error in Jellyfin scan-complete callback")
