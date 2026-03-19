"""
Integration tests for Plex and Jellyfin connector functionality.

Usage:
    python scripts/test_connector_integration.py [OPTIONS]

Options:
    --plex-library-key KEY    Plex library section key to use as test target
    --jellyfin-library-id ID  Jellyfin library ID to use as test target
    --skip-plex               Skip all Plex tests
    --skip-jellyfin           Skip all Jellyfin tests
    --no-metadata-write       Skip metadata write tests (read-only)
    --verbose                 Enable INFO logging from scanners and clients
    --log-file PATH           Write full output to a file (default: connector_test_<ts>.log)
    --no-log-file             Disable file logging entirely

Tests:
    T1  Library enumeration        - Can we fetch available libraries?
    T2  Path prefix translation    - Do path prefixes translate correctly to local paths?
    T3  Rescan trigger (Plex)      - Can we trigger a Plex library rescan?
    T4  Rescan completion detect   - Does refresh_library_and_wait() detect completion?
    T5  Metadata from DB cache     - Can we apply metadata without new AniList API calls?

Configuration:
    Reads Plex/Jellyfin credentials from env vars (same as the app):
        PLEX_URL, PLEX_TOKEN, JELLYFIN_URL, JELLYFIN_API_KEY
    Falls back to DB-stored settings if env vars are not set.

    Optional path prefix overrides (separate from production DB settings):
        TEST_PLEX_PATH_PREFIX    Path Plex uses (e.g. /media/anime)
        TEST_LOCAL_PATH_PREFIX   Corresponding local filesystem path
        TEST_JELLYFIN_PATH_PREFIX
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import io
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# Make sure src/ is importable from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.Clients.AnilistClient import AniListClient
from src.Clients.JellyfinClient import JellyfinClient
from src.Clients.PlexClient import PlexClient
from src.Database.Connection import DatabaseManager
from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.JellyfinMetadataScanner import JellyfinMetadataScanner
from src.Scanner.JellyfinShowProvider import JellyfinShowProvider
from src.Scanner.MetadataScanner import MetadataScanner, ScanProgress
from src.Scanner.PlexShowProvider import PlexShowProvider
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.Config import load_config

log = logging.getLogger("connector_test")

# Loggers that emit useful INFO/DEBUG messages during tests
_VERBOSE_LOGGERS = [
    "src.Scanner.MetadataScanner",
    "src.Scanner.JellyfinMetadataScanner",
    "src.Clients.PlexClient",
    "src.Clients.JellyfinClient",
    "src.Clients.AnilistClient",
    "connector_test",
]


class _Tee(io.TextIOBase):
    """Write to both a stream and a file simultaneously."""

    def __init__(self, stream: io.TextIOBase, file_path: Path) -> None:
        self._stream = stream
        self._file = open(file_path, "w", encoding="utf-8")  # noqa: SIM115

    def write(self, s: str) -> int:
        self._stream.write(s)
        self._stream.flush()
        self._file.write(_strip_ansi(s))
        self._file.flush()
        return len(s)

    def flush(self) -> None:
        self._stream.flush()
        self._file.flush()

    def close_file(self) -> None:
        self._file.close()


def _strip_ansi(s: str) -> str:
    """Remove ANSI colour codes for the plain-text log file."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _setup_logging(verbose: bool, log_file: Path | None) -> _Tee | None:
    """Configure logging and optionally start teeing stdout to a file."""
    tee: _Tee | None = None

    if log_file is not None:
        tee = _Tee(sys.stdout, log_file)  # type: ignore[arg-type]
        sys.stdout = tee  # type: ignore[assignment]

    # Root handler — plain format, writes to (possibly tee'd) stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)

    # Also attach the handler to a file handler if teeing (so log records go there too)
    if log_file is not None:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
        root.addHandler(fh)

    level = logging.INFO if verbose else logging.WARNING
    for name in _VERBOSE_LOGGERS:
        logging.getLogger(name).setLevel(level)

    return tee

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"
INFO = "\033[36mINFO\033[0m"

_results: list[tuple[str, str, str]] = []  # (test_id, label, status)


def result(test_id: str, label: str, ok: bool, note: str = "") -> None:
    status = PASS if ok else FAIL
    suffix = f"  ({note})" if note else ""
    print(f"  [{status}] {test_id}: {label}{suffix}")
    _results.append((test_id, label, "PASS" if ok else "FAIL"))


def skip(test_id: str, label: str, reason: str = "") -> None:
    suffix = f"  ({reason})" if reason else ""
    print(f"  [{SKIP}] {test_id}: {label}{suffix}")
    _results.append((test_id, label, "SKIP"))


def info(msg: str) -> None:
    print(f"  [{INFO}] {msg}")


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ---------------------------------------------------------------------------
# DB + config helpers
# ---------------------------------------------------------------------------


async def load_db_settings(db: DatabaseManager) -> dict[str, dict[str, Any]]:
    """Load app_settings rows into a key→row dict."""
    rows = await db.fetch_all("SELECT key, value FROM app_settings")
    return {row["key"]: dict(row) for row in rows}


async def get_db_setting(db: DatabaseManager, key: str) -> str:
    """Return a single app_settings value or empty string."""
    row = await db.fetch_one(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    )
    return str(row["value"]) if row and row["value"] else ""


# ---------------------------------------------------------------------------
# T1: Library enumeration
# ---------------------------------------------------------------------------


async def test_plex_library_enumeration(client: PlexClient) -> None:
    section("T1a — Plex: Library enumeration")
    try:
        libraries = await client.get_libraries()
        ok = len(libraries) > 0
        result("T1a", "Fetch Plex libraries", ok, f"{len(libraries)} found")
        for lib in libraries:
            info(f"  key={lib.key}  type={lib.type}  title={lib.title!r}  items={lib.item_count}")
    except Exception as exc:
        result("T1a", "Fetch Plex libraries", False, str(exc))


async def test_jellyfin_library_enumeration(client: JellyfinClient) -> None:
    section("T1b — Jellyfin: Library enumeration")
    try:
        libraries = await client.get_libraries()
        ok = len(libraries) > 0
        result("T1b", "Fetch Jellyfin libraries", ok, f"{len(libraries)} found")
        for lib in libraries:
            info(f"  id={lib.id}  type={lib.type}  name={lib.name!r}")
    except Exception as exc:
        result("T1b", "Fetch Jellyfin libraries", False, str(exc))


# ---------------------------------------------------------------------------
# T2: Path prefix translation
# ---------------------------------------------------------------------------


async def test_plex_path_prefix(
    client: PlexClient,
    db: DatabaseManager,
    library_key: str | None,
    plex_prefix: str,
    local_prefix: str,
) -> None:
    section("T2a — Plex: Path prefix translation")

    if not library_key:
        skip("T2a", "Path prefix translation", "no --plex-library-key supplied")
        return

    try:
        provider = PlexShowProvider(
            plex_client=client,
            db=db,
            plex_path_prefix=plex_prefix,
            local_path_prefix=local_prefix,
        )
        from src.Scanner.LibraryRestructurer import RestructureProgress

        progress = RestructureProgress()
        shows = await provider.get_shows([library_key], progress)

        result(
            "T2a",
            "PlexShowProvider returns shows",
            len(shows) > 0,
            f"{len(shows)} shows with library_key={library_key!r}",
        )

        if shows and plex_prefix and local_prefix:
            # Verify path translation was applied
            translated = [
                s for s in shows if s.local_path and s.local_path.startswith(local_prefix)
            ]
            result(
                "T2a-paths",
                "Paths translated to local prefix",
                len(translated) > 0,
                f"{len(translated)}/{len(shows)} shows have local prefix",
            )
            for s in shows[:3]:
                info(f"  {s.title!r}: local_path={s.local_path!r}")
        elif shows:
            info("  No prefix configured — raw paths from Plex:")
            for s in shows[:3]:
                info(f"  {s.title!r}: local_path={s.local_path!r}")

    except Exception as exc:
        result("T2a", "PlexShowProvider returns shows", False, str(exc))


async def test_jellyfin_path_prefix(
    client: JellyfinClient,
    db: DatabaseManager,
    library_id: str | None,
    jellyfin_prefix: str,
    local_prefix: str,
) -> None:
    section("T2b — Jellyfin: Path prefix translation")

    if not library_id:
        skip("T2b", "Path prefix translation", "no --jellyfin-library-id supplied")
        return

    try:
        provider = JellyfinShowProvider(
            jellyfin_client=client,
            db=db,
            jellyfin_path_prefix=jellyfin_prefix,
            local_path_prefix=local_prefix,
        )
        from src.Scanner.LibraryRestructurer import RestructureProgress

        progress = RestructureProgress()
        shows = await provider.get_shows([library_id], progress)

        result(
            "T2b",
            "JellyfinShowProvider returns shows",
            len(shows) > 0,
            f"{len(shows)} shows with library_id={library_id!r}",
        )

        if shows:
            for s in shows[:3]:
                info(f"  {s.title!r}: local_path={s.local_path!r}")

    except Exception as exc:
        result("T2b", "JellyfinShowProvider returns shows", False, str(exc))


# ---------------------------------------------------------------------------
# T3: Rescan trigger
# ---------------------------------------------------------------------------


async def test_plex_rescan_trigger(client: PlexClient, library_key: str | None) -> None:
    section("T3 — Plex: Rescan trigger")

    if not library_key:
        skip("T3", "Trigger Plex library rescan", "no --plex-library-key supplied")
        return

    try:
        await client.refresh_library(library_key)
        result("T3", "refresh_library() succeeded (no exception)", True)
    except Exception as exc:
        result("T3", "refresh_library() succeeded (no exception)", False, str(exc))


# ---------------------------------------------------------------------------
# T4: Rescan completion detection
# ---------------------------------------------------------------------------


async def test_plex_rescan_completion(
    client: PlexClient, library_key: str | None
) -> None:
    section("T4 — Plex: Rescan completion detection")

    if not library_key:
        skip("T4", "refresh_library_and_wait()", "no --plex-library-key supplied")
        return

    try:
        info("Triggering refresh and waiting for completion (timeout=60s)…")
        start = time.monotonic()
        completed = await client.refresh_library_and_wait(
            library_key, poll_interval=2.0, timeout=60.0
        )
        elapsed = time.monotonic() - start
        result(
            "T4",
            "refresh_library_and_wait() reports completion",
            completed,
            f"completed={completed}, elapsed={elapsed:.1f}s",
        )
    except Exception as exc:
        result("T4", "refresh_library_and_wait() reports completion", False, str(exc))


async def test_jellyfin_rescan_trigger(
    client: JellyfinClient, library_id: str | None
) -> None:
    section("T4b — Jellyfin: Rescan trigger + completion detection")

    # Test task ID lookup
    try:
        task_id = await client._get_scan_task_id()
        result("T4b-task", "Found RefreshLibrary scheduled task", task_id is not None,
               f"task_id={task_id}")
    except Exception as exc:
        result("T4b-task", "Found RefreshLibrary scheduled task", False, str(exc))
        return

    # Test that triggering causes a transition to Running state
    # (Full completion can take minutes on large libraries — not practical to wait here)
    try:
        info("Triggering refresh and checking for Running state (up to 30s)…")
        await client.refresh_library()
        running = False
        for _ in range(6):
            await asyncio.sleep(5)
            if await client.is_library_scanning():
                running = True
                break
        result(
            "T4b-wait",
            "Scan transitions to Running state after trigger",
            running,
            "(full completion polling uses 600s timeout in production)",
        )
    except Exception as exc:
        result("T4b-wait", "Scan transitions to Running state after trigger", False, str(exc))


# ---------------------------------------------------------------------------
# T5: Metadata write from DB cache (no new AniList API calls)
# ---------------------------------------------------------------------------


async def test_metadata_from_cache_plex(
    plex_client: PlexClient,
    anilist_client: AniListClient,
    db: DatabaseManager,
    config: Any,
    library_key: str | None,
    dry_run: bool,
) -> None:
    section("T5a — Plex: Metadata applies from cache (no AniList API calls)")

    if not library_key:
        skip("T5a", "Metadata from cache", "no --plex-library-key supplied")
        return

    # Check if any cached entries exist — T5 only makes sense with a warm cache
    cached_count = await db.fetch_one(
        "SELECT COUNT(*) as cnt FROM anilist_cache WHERE expires_at > datetime('now')"
    )
    cache_size = cached_count["cnt"] if cached_count else 0
    info(f"Current anilist_cache: {cache_size} valid entries")

    if cache_size == 0:
        skip(
            "T5a",
            "Metadata from cache",
            "cache is empty — run a full metadata scan first via the app, then re-run T5",
        )
        return

    # Wrap get_anime_by_id to count calls
    api_calls: list[int] = []
    original_get = anilist_client.get_anime_by_id

    async def counting_get(anilist_id: int) -> Any:
        api_calls.append(anilist_id)
        return await original_get(anilist_id)

    anilist_client.get_anime_by_id = counting_get  # type: ignore[method-assign]

    try:
        title_matcher = TitleMatcher(similarity_threshold=0.75)
        group_builder = SeriesGroupBuilder(db, anilist_client)
        scanner = MetadataScanner(
            db, anilist_client, title_matcher, plex_client, config,
            group_builder=group_builder,
        )
        progress = ScanProgress()
        scan_results = await scanner.run_scan(
            dry_run=dry_run,
            library_keys=[library_key],
            preview=False,
            progress=progress,
        )

        result(
            "T5a-scan",
            "Scan completed without error",
            progress.status != "error",
            f"status={progress.status}, matched={scan_results.matched}, "
            f"skipped={scan_results.skipped}, failed={scan_results.failed}",
        )
        result(
            "T5a-cache",
            "No new AniList API calls (all metadata from cache)",
            len(api_calls) == 0,
            f"{len(api_calls)} API calls made"
            + (f" for AniList IDs: {api_calls[:5]}" if api_calls else ""),
        )
        if api_calls:
            info(
                "  These IDs were fetched from AniList (not in cache before scan):"
                f" {api_calls[:10]}"
            )
    except Exception as exc:
        result("T5a-scan", "Scan completed without error", False, str(exc))
    finally:
        anilist_client.get_anime_by_id = original_get  # type: ignore[method-assign]


async def test_metadata_from_cache_jellyfin(
    jellyfin_client: JellyfinClient,
    anilist_client: AniListClient,
    db: DatabaseManager,
    config: Any,
    library_id: str | None,
    dry_run: bool,
) -> None:
    section("T5b — Jellyfin: Metadata applies from cache (no AniList API calls)")

    if not library_id:
        skip("T5b", "Metadata from cache", "no --jellyfin-library-id supplied")
        return

    cached_count = await db.fetch_one(
        "SELECT COUNT(*) as cnt FROM anilist_cache WHERE expires_at > datetime('now')"
    )
    cache_size = cached_count["cnt"] if cached_count else 0
    info(f"Current anilist_cache: {cache_size} valid entries")

    if cache_size == 0:
        skip(
            "T5b",
            "Metadata from cache",
            "cache is empty — run a full metadata scan first via the app, then re-run T5",
        )
        return

    api_calls: list[int] = []
    original_get = anilist_client.get_anime_by_id

    async def counting_get(anilist_id: int) -> Any:
        api_calls.append(anilist_id)
        return await original_get(anilist_id)

    anilist_client.get_anime_by_id = counting_get  # type: ignore[method-assign]

    try:
        title_matcher = TitleMatcher(similarity_threshold=0.75)
        group_builder = SeriesGroupBuilder(db, anilist_client)
        scanner = JellyfinMetadataScanner(
            db, anilist_client, title_matcher, jellyfin_client, config,
            group_builder=group_builder,
        )
        progress = ScanProgress()
        scan_results = await scanner.run_scan(
            dry_run=dry_run,
            library_ids=[library_id],
            preview=False,
            progress=progress,
        )

        result(
            "T5b-scan",
            "Scan completed without error",
            progress.status != "error",
            f"status={progress.status}, matched={scan_results.matched}, "
            f"skipped={scan_results.skipped}, failed={scan_results.failed}",
        )
        result(
            "T5b-cache",
            "No new AniList API calls (all metadata from cache)",
            len(api_calls) == 0,
            f"{len(api_calls)} API calls made",
        )
    except Exception as exc:
        result("T5b-scan", "Scan completed without error", False, str(exc))
    finally:
        anilist_client.get_anime_by_id = original_get  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary() -> int:
    """Print test summary. Returns exit code (0 if all pass/skip, 1 if any fail)."""
    section("Summary")
    failed = [r for r in _results if r[2] == "FAIL"]
    passed = [r for r in _results if r[2] == "PASS"]
    skipped = [r for r in _results if r[2] == "SKIP"]

    print(
        f"  {len(passed)} passed  |  {len(failed)} failed  |  {len(skipped)} skipped\n"
    )

    if failed:
        print("  Failed tests:")
        for tid, label, _ in failed:
            print(f"    ✗ {tid}: {label}")

    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> int:
    # Resolve log file path
    log_file: Path | None = None
    if not args.no_log_file:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"connector_test_{ts}.log"
        log_file = Path(args.log_file) if args.log_file else Path(default_name)

    tee = _setup_logging(verbose=args.verbose, log_file=log_file)

    config = load_config()

    # Resolve credentials (env var takes priority, same as app behaviour)
    plex_url = os.environ.get("PLEX_URL") or config.plex.url
    plex_token = os.environ.get("PLEX_TOKEN") or config.plex.token
    jellyfin_url = os.environ.get("JELLYFIN_URL") or config.jellyfin.url
    jellyfin_api_key = os.environ.get("JELLYFIN_API_KEY") or config.jellyfin.api_key

    # Path prefixes for testing (separate from production DB settings)
    plex_prefix = os.environ.get("TEST_PLEX_PATH_PREFIX", "")
    local_prefix = os.environ.get("TEST_LOCAL_PATH_PREFIX", "")
    jellyfin_prefix = os.environ.get("TEST_JELLYFIN_PATH_PREFIX", "")

    db = DatabaseManager(config.database.path)
    await db.initialize()

    # Build clients — AniList client uses no auth for metadata lookups
    plex_client = PlexClient(url=plex_url, token=plex_token) if plex_url else None
    jellyfin_client = (
        JellyfinClient(url=jellyfin_url, api_key=jellyfin_api_key)
        if jellyfin_url
        else None
    )
    anilist_client = AniListClient(
        client_id=config.anilist.client_id or "0",
        client_secret=config.anilist.client_secret or "",
        redirect_uri="",
    )

    print("\nAnilist-Link — Connector Integration Tests")
    print(f"DB:          {config.database.path}")
    print(f"Plex URL:    {plex_url or '(not configured)'}")
    print(f"Jellyfin URL:{jellyfin_url or '(not configured)'}")
    if log_file:
        print(f"Log file:    {log_file.resolve()}")
    print(f"Verbose:     {'yes' if args.verbose else 'no (use --verbose for scanner/client logs)'}")

    try:
        # ---- PLEX tests ----
        if args.skip_plex or not plex_client:
            reason = "skipped via --skip-plex" if args.skip_plex else "PLEX_URL not set"
            for tid, label in [
                ("T1a", "Fetch Plex libraries"),
                ("T2a", "Path prefix translation"),
                ("T3", "Trigger Plex library rescan"),
                ("T4", "Rescan completion detection"),
                ("T5a", "Metadata from cache"),
            ]:
                skip(tid, label, reason)
        else:
            await test_plex_library_enumeration(plex_client)
            await test_plex_path_prefix(
                plex_client, db, args.plex_library_key, plex_prefix, local_prefix
            )
            await test_plex_rescan_trigger(plex_client, args.plex_library_key)
            await test_plex_rescan_completion(plex_client, args.plex_library_key)
            await test_metadata_from_cache_plex(
                plex_client,
                anilist_client,
                db,
                config,
                args.plex_library_key,
                dry_run=args.no_metadata_write,
            )

        # ---- Jellyfin tests ----
        if args.skip_jellyfin or not jellyfin_client:
            reason = (
                "skipped via --skip-jellyfin"
                if args.skip_jellyfin
                else "JELLYFIN_URL not set"
            )
            for tid, label in [
                ("T1b", "Fetch Jellyfin libraries"),
                ("T2b", "Path prefix translation"),
                ("T4b", "Rescan trigger"),
                ("T5b", "Metadata from cache"),
            ]:
                skip(tid, label, reason)
        else:
            await test_jellyfin_library_enumeration(jellyfin_client)
            await test_jellyfin_path_prefix(
                jellyfin_client, db, args.jellyfin_library_id, jellyfin_prefix, local_prefix
            )
            await test_jellyfin_rescan_trigger(jellyfin_client, args.jellyfin_library_id)
            await test_metadata_from_cache_jellyfin(
                jellyfin_client,
                anilist_client,
                db,
                config,
                args.jellyfin_library_id,
                dry_run=args.no_metadata_write,
            )

    finally:
        if plex_client:
            await plex_client.close()
        if jellyfin_client:
            await jellyfin_client.close()
        await anilist_client.close()
        await db.close()

    exit_code = print_summary()
    if log_file:
        print(f"\nFull log written to: {log_file.resolve()}")
    if tee:
        tee.close_file()
        sys.stdout = tee._stream  # restore original stdout
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Integration tests for Plex and Jellyfin connectors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--plex-library-key",
        metavar="KEY",
        help="Plex library section key for T2/T3/T4/T5 (e.g. '3')",
    )
    parser.add_argument(
        "--jellyfin-library-id",
        metavar="ID",
        help="Jellyfin library ID for T2/T4b/T5b",
    )
    parser.add_argument(
        "--skip-plex",
        action="store_true",
        help="Skip all Plex tests",
    )
    parser.add_argument(
        "--skip-jellyfin",
        action="store_true",
        help="Skip all Jellyfin tests",
    )
    parser.add_argument(
        "--no-metadata-write",
        action="store_true",
        help="Run T5 as dry_run=True (no metadata written to media servers)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable INFO logging from scanners and clients (shows per-show detail)",
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help="Write full output to PATH (default: connector_test_<timestamp>.log)",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="Disable file logging entirely (console only)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    exit_code = asyncio.run(main(args))
    sys.exit(exit_code)
