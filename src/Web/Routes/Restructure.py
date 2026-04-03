"""Library Restructuring Wizard routes."""

from __future__ import annotations

import json
import logging
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from src.Clients.JellyfinClient import JellyfinClient
from src.Clients.PlexClient import PlexClient
from src.Matching.TitleMatcher import TitleMatcher
from src.Scanner.LibraryRestructurer import (
    LibraryRestructurer,
    RestructurePlan,
    RestructureProgress,
)
from src.Scanner.LocalDirectoryScanner import LocalDirectoryScanner
from src.Web.App import spawn_background_task

logger = logging.getLogger(__name__)


async def _seed_library_from_plan(app_state: object, plan: RestructurePlan) -> int:
    """Seed library_items from a restructure plan's scan data.

    Used when the user cancels or skips the restructure so the local
    library still has all the AniList matches and cover images that
    were discovered during the analyze phase.
    """
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    library_id: int | None = getattr(app_state, "restructure_library_id", None)
    if not library_id:
        library_id = getattr(app_state, "onboarding_library_id", None)
    if not library_id:
        # Try to find or create a library
        libraries = await db.get_all_libraries()
        if libraries:
            library_id = libraries[0]["id"]
        else:
            return 0

    restructurer = await LibraryRestructurer.from_settings(db, anilist_client)

    # Clear stale rows before seeding
    await db.execute("DELETE FROM library_items WHERE library_id = ?", (library_id,))
    seeded = await restructurer.seed_library_items(plan, library_id, from_source=True)
    logger.info("Seeded %d library items from restructure plan (no execute)", seeded)
    return seeded


router = APIRouter(tags=["restructure"])


def _is_restructure_busy(app_state: object) -> bool:
    """Return True if an analysis or execution is currently in-flight."""
    progress = getattr(app_state, "restructure_progress", None)
    if progress and progress.status not in ("", "pending", "complete", "error"):
        return True
    exec_progress = getattr(app_state, "restructure_exec_progress", None)
    if exec_progress and exec_progress.status not in (
        "",
        "pending",
        "complete",
        "error",
    ):
        return True
    return False


async def _run_analysis_background(
    app_state: object,
    source_dirs: list[str],
    output_dir: str,
    level: str,
    force_rescan: bool,
    templates: dict[str, str] | None = None,
) -> None:
    """Background coroutine: run restructure analysis without blocking the request.

    Updates ``app_state.restructure_progress`` for the floating widget and
    creates a notification with a link to ``/restructure/preview`` on success.

    When *templates* is provided, the restructurer is built with those values
    instead of reading from the database (so cancelling analysis doesn't
    overwrite the last applied naming settings).
    """
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress: RestructureProgress = app_state.restructure_progress  # type: ignore[attr-defined]

    try:
        if templates:
            from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder

            group_builder = SeriesGroupBuilder(db, anilist_client)
            restructurer = LibraryRestructurer(
                db=db,
                group_builder=group_builder,
                file_template=templates.get("naming.file_template", ""),
                folder_template=templates.get("naming.folder_template", ""),
                season_folder_template=templates.get(
                    "naming.season_folder_template", ""
                ),
                movie_file_template=templates.get("naming.movie_file_template", ""),
                title_pref=templates.get("app.title_display", "romaji"),
                illegal_char_replacement=templates.get(
                    "naming.illegal_char_replacement", ""
                ),
            )
        else:
            restructurer = await LibraryRestructurer.from_settings(db, anilist_client)
        title_matcher = TitleMatcher(similarity_threshold=0.75)
        scanner = LocalDirectoryScanner(
            db=db, anilist_client=anilist_client, title_matcher=title_matcher
        )

        all_shows = []
        for src_dir in source_dirs:
            progress.phase = f"Scanning {src_dir}"
            logger.info("Restructure analyze (bg): scanning %r", src_dir)
            shows = await scanner.scan_directory(
                src_dir, progress, force_rescan=force_rescan
            )
            matched = sum(1 for s in shows if s.anilist_id)
            logger.info(
                "Restructure analyze (bg): %r -> %d folders (%d matched, %d unmatched)",
                src_dir,
                len(shows),
                matched,
                len(shows) - matched,
            )
            all_shows.extend(shows)

        logger.info(
            "Restructure analyze (bg): total=%d, calling analyze level=%r",
            len(all_shows),
            level,
        )

        plan = await restructurer.analyze(
            all_shows, progress, level=level, output_dir=output_dir or None
        )
        app_state.onboarding_restructure_plan = plan  # type: ignore[attr-defined]

        # Create/reuse a library for post-restructure seeding
        lib_paths = [output_dir] if output_dir else list(source_dirs)
        libraries = await db.get_all_libraries()
        if libraries:
            lib_id = libraries[0]["id"]
            await db.update_library(lib_id, libraries[0]["name"], json.dumps(lib_paths))
        else:
            lib_id = await db.create_library("My Library", json.dumps(lib_paths))
        app_state.onboarding_library_id = lib_id  # type: ignore[attr-defined]

        if plan.total_groups > 0:
            app_state.restructure_plan = plan  # type: ignore[attr-defined]
            app_state.restructure_source_mode = "local"  # type: ignore[attr-defined]
            app_state.restructure_library_keys = []  # type: ignore[attr-defined]
            app_state.restructure_library_id = lib_id  # type: ignore[attr-defined]

        app_state.onboarding_restructure_conflicts = (  # type: ignore[attr-defined]
            LibraryRestructurer.detect_conflicts(plan)
        )
        app_state.onboarding_source_dirs = source_dirs  # type: ignore[attr-defined]
        app_state.onboarding_output_dir = output_dir  # type: ignore[attr-defined]

        progress.status = "complete"
        progress.phase = (
            f"Analysis complete: {plan.total_groups} shows,"
            f" {plan.total_files} files"
        )

        if plan.total_groups > 0:
            await db.add_notification(
                notification_type="success",
                message=(
                    f"Library scan complete — {plan.total_groups} shows,"
                    f" {plan.total_files} files ready to organize."
                    " Review before applying."
                ),
                action_url="/restructure/preview",
                action_label="Review Plan",
            )
        else:
            matched = sum(1 for s in all_shows if s.anilist_id)
            await db.add_notification(
                notification_type="info",
                message=(
                    f"Analysis complete — scanned {len(all_shows)} folders"
                    f" ({matched} matched). No changes needed; all folders"
                    " already match the target naming convention."
                ),
                action_url="/",
                action_label="Dashboard",
            )

        logger.info(
            "Restructure analyze (bg) complete: %d groups, %d files",
            plan.total_groups,
            plan.total_files,
        )
    except Exception as exc:
        logger.exception("Restructure background analysis failed")
        progress.status = "error"
        progress.error_message = str(exc)


async def _run_execution(app_state: object) -> None:
    """Background coroutine: execute restructuring, seed library, then hand off.

    The user-facing progress page tracks only file moves + library seeding.
    Once seeding is done, progress is marked "complete" and the client
    redirects to the unified library.  Media server refresh continues in a
    separate background task shown in the floating progress widget.
    """
    config = app_state.config  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]
    anilist_client = app_state.anilist_client  # type: ignore[attr-defined]
    progress: RestructureProgress = app_state.restructure_exec_progress  # type: ignore[attr-defined]
    plan: RestructurePlan = app_state.restructure_plan  # type: ignore[attr-defined]
    source_mode: str = app_state.restructure_source_mode  # type: ignore[attr-defined]

    restructurer = await LibraryRestructurer.from_settings(db, anilist_client)

    try:
        # Phase 1: Move/rename files
        stats = await restructurer.execute(plan, progress)
        app_state.restructure_stats = stats  # type: ignore[attr-defined]

        # Clear restructure state so _auto_scan_media_servers proceeds
        app_state.restructure_plan = None  # type: ignore[attr-defined]
        app_state.onboarding_restructure_plan = None  # type: ignore[attr-defined]
        app_state.restructure_progress = None  # type: ignore[attr-defined]

        # Phase 2: Pre-seed library_items for ALL groups.
        # Enabled groups moved → seed from target paths.
        # Disabled groups untouched → seed from source paths.
        library_id: int | None = getattr(app_state, "restructure_library_id", None)
        if library_id:
            progress.phase = "Indexing library"
            await db.execute(
                "DELETE FROM library_items WHERE library_id = ?", (library_id,)
            )

            enabled_groups = [g for g in plan.groups if g.enabled]
            disabled_groups = [g for g in plan.groups if not g.enabled]

            if enabled_groups:
                enabled_plan = RestructurePlan(
                    groups=enabled_groups,
                    operation_level=plan.operation_level,
                )
                await restructurer.seed_library_items(
                    enabled_plan, library_id, from_source=False
                )

            # Seed disabled groups from their original (source) paths,
            # and include unchanged/unmatched shows so every scanned
            # folder appears in the library.
            disabled_plan = RestructurePlan(
                groups=disabled_groups,
                operation_level=plan.operation_level,
                unchanged_shows=plan.unchanged_shows,
                unchanged_group_ids=plan.unchanged_group_ids,
                unmatched_shows=plan.unmatched_shows,
            )
            await restructurer.seed_library_items(
                disabled_plan, library_id, from_source=True
            )

        # Prevent _auto_scan_media_servers from re-indexing — we just seeded.
        app_state.library_already_seeded = True  # type: ignore[attr-defined]

        # ---- User-facing work is done — release the progress page ----
        progress.status = "complete"
        progress.phase = "Operation complete"

        # Phase 3: Media server refresh runs in background, tracked by the
        # floating progress widget (not the restructure progress page).
        spawn_background_task(
            app_state,
            _post_restructure_refresh(app_state, config, plan, source_mode, library_id),
        )
    except Exception:
        logger.exception("Restructuring execution failed")
        progress.status = "error"
        progress.error_message = "Execution failed unexpectedly"


async def _post_restructure_refresh(
    app_state: object,
    config: object,
    plan: RestructurePlan,
    source_mode: str,
    library_id: int | None,
) -> None:
    """Background: refresh media servers after restructure.

    Tracked by the floating progress widget via ``app_state.media_refresh_progress``.
    """
    refresh_progress = RestructureProgress(
        status="running", phase="Refreshing media server…"
    )
    app_state.media_refresh_progress = refresh_progress  # type: ignore[attr-defined]
    db = app_state.db  # type: ignore[attr-defined]

    plex_client: PlexClient | None = None
    jellyfin_client: JellyfinClient | None = None

    try:
        if source_mode == "plex":
            library_keys: list[str] = app_state.restructure_library_keys  # type: ignore[attr-defined]
            plex_client = PlexClient(url=config.plex.url, token=config.plex.token)  # type: ignore[attr-defined]

            refresh_progress.phase = "Waiting for Plex to index"
            for key in library_keys:
                try:
                    await plex_client.refresh_library_and_wait(key, poll_interval=2.0)
                except Exception:
                    logger.exception("Failed to refresh Plex library %s", key)

            if plan.operation_level == "full_restructure":
                refresh_progress.phase = "Cleaning up old entries"
                for group in plan.groups:
                    if not group.enabled:
                        continue
                    for rk in group.source_rating_keys:
                        await db.delete_plex_media_by_rating_key(rk)

        elif (
            source_mode == "jellyfin"
            and config.jellyfin.url  # type: ignore[attr-defined]
            and config.jellyfin.api_key  # type: ignore[attr-defined]
        ):
            jellyfin_client = JellyfinClient(
                url=config.jellyfin.url, api_key=config.jellyfin.api_key  # type: ignore[attr-defined]
            )
            refresh_progress.phase = "Waiting for Jellyfin to index"
            try:
                await jellyfin_client.refresh_library_and_wait(
                    poll_interval=5.0, inactivity_timeout=120.0
                )
            except Exception:
                logger.exception("Failed to refresh Jellyfin library")

        if source_mode == "local":
            if config.plex.url and config.plex.token:  # type: ignore[attr-defined]
                refresh_progress.phase = "Waiting for Plex to re-index"
                plex_client = PlexClient(url=config.plex.url, token=config.plex.token)  # type: ignore[attr-defined]
                try:
                    keys = (
                        list(config.plex.anime_library_keys)  # type: ignore[attr-defined]
                        if config.plex.anime_library_keys  # type: ignore[attr-defined]
                        else None
                    )
                    if keys:
                        for key in keys:
                            await plex_client.refresh_library_and_wait(
                                key, poll_interval=3.0
                            )
                    else:
                        libs = await plex_client.get_libraries()
                        for lib in libs:
                            await plex_client.refresh_library_and_wait(
                                lib.key, poll_interval=3.0
                            )
                except Exception:
                    logger.exception("Local restructure: Plex refresh failed")
                finally:
                    await plex_client.close()
                    plex_client = None

            if config.jellyfin.url and config.jellyfin.api_key:  # type: ignore[attr-defined]
                refresh_progress.phase = "Waiting for Jellyfin to re-index"
                jellyfin_client = JellyfinClient(
                    url=config.jellyfin.url, api_key=config.jellyfin.api_key  # type: ignore[attr-defined]
                )
                try:
                    await jellyfin_client.refresh_library_and_wait(
                        poll_interval=5.0, inactivity_timeout=120.0
                    )
                except Exception:
                    logger.exception("Local restructure: Jellyfin refresh failed")
                finally:
                    await jellyfin_client.close()
                    jellyfin_client = None

            refresh_progress.phase = "Running metadata scans"
            from src.Web.Routes.Onboarding import _auto_scan_media_servers

            await _auto_scan_media_servers(app_state)

        refresh_progress.status = "complete"
        refresh_progress.phase = "Media server refresh complete"
        logger.info("Post-restructure media server refresh complete")
    except Exception:
        logger.exception("Post-restructure media server refresh failed")
        refresh_progress.status = "error"
        refresh_progress.phase = "Media server refresh failed"
    finally:
        if plex_client:
            await plex_client.close()
        if jellyfin_client:
            await jellyfin_client.close()


@router.post("/api/restructure/analyze")
async def restructure_analyze_async(request: Request) -> JSONResponse:
    """Start restructure analysis as a background task and return immediately.

    The floating progress widget tracks progress via ``/api/progress``.
    A notification with a link to ``/restructure/preview`` appears on completion.
    """
    if _is_restructure_busy(request.app.state):
        return JSONResponse(
            {"ok": False, "error": "A restructure operation is already running"},
            status_code=409,
        )

    body = await request.json()
    source_dirs: list[str] = body.get("source_dirs") or []
    output_dir: str = (body.get("output_dir") or "").strip()
    force_rescan: bool = bool(body.get("force_rescan", False))

    _level_map = {"full": "full_restructure", "quick": "folder_file_rename"}
    level_raw: str = body.get("level") or "full"
    level: str = _level_map.get(level_raw, level_raw)

    db = request.app.state.db

    # Collect template overrides — saved to DB only on successful execution,
    # not during analysis, so cancelling preserves the last applied settings.
    templates_data: dict = body.get("templates") or {}
    pending_templates: dict[str, str] = {}
    for key, setting_key in [
        ("episode", "naming.file_template"),
        ("folder", "naming.folder_template"),
        ("season", "naming.season_folder_template"),
        ("movie", "naming.movie_file_template"),
        ("illegal_char_replacement", "naming.illegal_char_replacement"),
        ("title_pref", "app.title_display"),
    ]:
        val = (templates_data.get(key) or "").strip()
        if val:
            pending_templates[setting_key] = val

    if not source_dirs:
        return JSONResponse(
            {"ok": False, "error": "At least one source directory is required"},
            status_code=400,
        )

    # Clear previous plan / notification
    request.app.state.restructure_plan = None
    request.app.state.onboarding_restructure_plan = None
    await db.dismiss_notifications_by_url("/restructure/preview")
    await db.clear_dismissed_notifications()

    # Store pending templates so they can be applied on execution
    request.app.state.pending_naming_templates = pending_templates

    # Set up progress and launch background task
    progress = RestructureProgress(status="running")
    request.app.state.restructure_progress = progress

    spawn_background_task(
        request.app.state,
        _run_analysis_background(
            request.app.state,
            source_dirs=source_dirs,
            output_dir=output_dir,
            level=level,
            force_rescan=force_rescan,
            templates=pending_templates,
        ),
    )

    logger.info(
        "Restructure analyze started in background — level=%r, sources=%s",
        level,
        source_dirs,
    )

    # Determine redirect target
    library_id = body.get("library_id")
    if library_id:
        redirect = f"/library/{library_id}"
    else:
        redirect = "/"

    return JSONResponse({"ok": True, "redirect": redirect})


@router.get("/restructure", response_class=HTMLResponse)
async def restructure_wizard(request: Request) -> Response:
    """Render the restructure wizard landing page."""
    # If an analysis is already running, redirect to dashboard so the user
    # watches progress via the floating widget instead of re-submitting.
    if _is_restructure_busy(request.app.state):
        return RedirectResponse(
            url="/?message=Restructure+analysis+already+running", status_code=303
        )

    db = request.app.state.db
    templates = request.app.state.templates

    # Check if coming from a library context
    library_context = None
    library_id_str = request.query_params.get("library_id", "")
    if library_id_str:
        try:
            library_id = int(library_id_str)
            library = await db.get_library(library_id)
            if library:
                library_context = {
                    "id": library["id"],
                    "name": library["name"],
                    "paths": json.loads(library["paths"]) if library["paths"] else [],
                }
        except (ValueError, TypeError):
            pass

    # Load current naming settings
    naming_values: dict[str, str] = {}
    for key in [
        "naming.file_template",
        "naming.movie_file_template",
        "naming.folder_template",
        "naming.season_folder_template",
        "naming.illegal_char_replacement",
        "app.title_display",
    ]:
        naming_values[key] = await db.get_setting(key) or ""

    # Default browse path: first library path, or saved library paths, or /media
    initial_browse_path = "/media"
    saved_library_paths: list[str] = []

    if library_context and library_context["paths"]:
        initial_browse_path = library_context["paths"][0]
    else:
        # No explicit library context — check for any saved library
        all_libs = await db.get_all_libraries()
        if all_libs:
            try:
                paths = json.loads(all_libs[0]["paths"]) if all_libs[0]["paths"] else []
                if paths:
                    initial_browse_path = paths[0]
                    saved_library_paths = paths
            except (json.JSONDecodeError, TypeError):
                pass

    return templates.TemplateResponse(
        "restructure_wizard.html",
        {
            "request": request,
            "library_context": library_context,
            "naming_values": naming_values,
            "initial_browse_path": initial_browse_path,
            "saved_library_paths": saved_library_paths,
        },
    )


@router.get("/restructure/progress", response_class=HTMLResponse)
async def restructure_progress_page(request: Request) -> HTMLResponse:
    """Render progress page that polls for status."""
    templates = request.app.state.templates

    # Determine which progress to track (analysis or execution)
    exec_progress = getattr(request.app.state, "restructure_exec_progress", None)
    is_executing = exec_progress is not None and exec_progress.status not in (
        "complete",
        "error",
    )

    return templates.TemplateResponse(
        "restructure_progress.html",
        {
            "request": request,
            "is_executing": is_executing,
        },
    )


@router.get("/api/restructure/progress")
async def restructure_progress_api(request: Request) -> JSONResponse:
    """Return current progress as JSON."""
    # Check execution progress first
    exec_progress: RestructureProgress | None = getattr(
        request.app.state, "restructure_exec_progress", None
    )
    if exec_progress and exec_progress.status not in ("pending",):
        elapsed = (
            time.monotonic() - exec_progress.started_at
            if exec_progress.started_at > 0
            else 0
        )
        result_url = "/library?source=local"
        return JSONResponse(
            {
                "status": exec_progress.status,
                "phase": exec_progress.phase,
                "processed": exec_progress.processed,
                "total": exec_progress.total,
                "current_item": exec_progress.current_item,
                "error_message": exec_progress.error_message,
                "elapsed_seconds": round(elapsed, 1),
                "result_url": result_url,
            }
        )

    # Analysis progress
    progress: RestructureProgress | None = getattr(
        request.app.state, "restructure_progress", None
    )
    if not progress:
        return JSONResponse({"status": "idle"})

    elapsed = time.monotonic() - progress.started_at if progress.started_at > 0 else 0
    result_url = "/restructure/preview"

    return JSONResponse(
        {
            "status": progress.status,
            "phase": progress.phase,
            "processed": progress.processed,
            "total": progress.total,
            "current_item": progress.current_item,
            "error_message": progress.error_message,
            "elapsed_seconds": round(elapsed, 1),
            "result_url": result_url,
        }
    )


def _build_grouped_moves(
    plan: RestructurePlan,
) -> dict[str, list[dict]]:
    """Pre-process file_moves into subfolder-grouped structures for preview.

    Returns a dict keyed by group_key.  Each value is a list of subfolder
    dicts::

        {
          "name": str,      # current subfolder name (or "" for root)
          "renamed": str,   # new subfolder name (same as name if unchanged)
          "source": str,    # source folder basename (restructure: where from)
          "files": [FileMove, ...]
        }

    Works for all operation levels:
    - **Rename (L1/L2)**: groups by source subdir, detects dir renames.
    - **Full restructure**: groups by *destination* season folder, tracks
      which source folder each file comes from.
    """
    result: dict[str, list[dict]] = {}

    for group in plan.groups:
        if group.operation_type in ("rename_folder", "rename_file"):
            result[group.group_key] = _group_rename_moves(group)
        elif group.operation_type == "move":
            result[group.group_key] = _group_restructure_moves(group)
        # standalones handled by _group_restructure_moves too
        elif group.operation_type.startswith("standalone"):
            result[group.group_key] = _group_restructure_moves(group)

    return result


def _group_rename_moves(group: object) -> list[dict]:
    """Group file_moves for L1/L2 rename operations by source subfolder."""
    src_root = group.source_folders[0] if group.source_folders else ""  # type: ignore[attr-defined]
    dir_renames: dict[str, str] = {}
    file_by_subdir: dict[str, list] = {}

    for fm in group.file_moves:  # type: ignore[attr-defined]
        if fm.is_dir:
            dir_renames[fm.original_filename] = fm.renamed_filename
        else:
            # Group files by their parent subdirectory
            rel = os.path.relpath(fm.source, src_root) if src_root else ""
            parts = rel.replace("\\", "/").split("/")
            subdir = parts[0] if len(parts) > 1 else ""
            file_by_subdir.setdefault(subdir, []).append(fm)

    subfolders: list[dict] = []
    seen: set[str] = set()

    for orig, renamed in sorted(dir_renames.items()):
        files = file_by_subdir.pop(orig, [])
        subfolders.append(
            {"name": orig, "renamed": renamed, "source": "", "files": files}
        )
        seen.add(orig)

    for subdir, files in sorted(file_by_subdir.items()):
        if subdir and subdir not in seen:
            subfolders.append(
                {"name": subdir, "renamed": subdir, "source": "", "files": files}
            )
        elif not subdir and files:
            subfolders.insert(
                0, {"name": "", "renamed": "", "source": "", "files": files}
            )

    return subfolders


def _group_restructure_moves(group: object) -> list[dict]:
    """Group file_moves for full restructure by destination season folder.

    Each subfolder entry shows the target season folder name and which
    source folder(s) the files come from.
    """
    target = group.target_folder.rstrip("/")

    # dest_subdir_name → {source_basename → [FileMove]}
    by_dest: dict[str, dict[str, list]] = {}

    for fm in group.file_moves:
        # Extract the season folder from the destination path
        dest_rel = os.path.relpath(fm.destination, target).replace("\\", "/")
        parts = dest_rel.split("/")
        season_folder = parts[0] if len(parts) > 1 else ""

        # Identify which source folder this file came from
        src_basename = ""
        for sf in group.source_folders:
            if fm.source.startswith(sf.rstrip("/") + "/") or fm.source.startswith(
                sf.rstrip("/") + os.sep
            ):
                src_basename = os.path.basename(sf.rstrip("/"))
                break

        by_dest.setdefault(season_folder, {}).setdefault(src_basename, []).append(fm)

    subfolders: list[dict] = []
    for season_name in sorted(by_dest.keys()):
        sources = by_dest[season_name]
        # Merge all source files into one list, track the primary source
        all_files: list = []
        source_names: list[str] = []
        for src_name, files in sorted(sources.items()):
            all_files.extend(files)
            if src_name:
                source_names.append(src_name)

        source_label = ", ".join(dict.fromkeys(source_names)) if source_names else ""
        subfolders.append(
            {
                "name": season_name,
                "renamed": season_name,
                "source": source_label,
                "files": all_files,
            }
        )

    return subfolders


@router.get("/restructure/preview", response_class=HTMLResponse)
async def restructure_preview(request: Request) -> Response:
    """Render the preview page showing the restructure plan."""
    templates = request.app.state.templates
    plan: RestructurePlan | None = getattr(request.app.state, "restructure_plan", None)

    if not plan:
        return RedirectResponse(
            url="/restructure?error=No+analysis+results", status_code=303
        )

    # Pre-process file_moves into subfolder-grouped structures for the template
    grouped_moves = _build_grouped_moves(plan)

    return templates.TemplateResponse(
        "restructure_preview.html",
        {
            "request": request,
            "plan": plan,
            "operation_level": plan.operation_level,
            "grouped_moves": grouped_moves,
        },
    )


@router.post("/restructure/cancel")
async def restructure_cancel(request: Request) -> RedirectResponse:
    """Cancel a pending restructure plan and trigger deferred scans."""
    db = request.app.state.db
    plan = getattr(request.app.state, "restructure_plan", None)

    # Seed library from the plan's scan data before discarding it
    if plan:
        await _seed_library_from_plan(request.app.state, plan)
        # Flag so _auto_scan_media_servers skips redundant local index
        request.app.state.library_already_seeded = True  # type: ignore[attr-defined]

    # Clear all restructure state (including pending templates)
    request.app.state.restructure_plan = None
    request.app.state.onboarding_restructure_plan = None
    request.app.state.restructure_progress = None
    request.app.state.pending_naming_templates = None

    # Dismiss the notification
    await db.dismiss_notifications_by_url("/restructure/preview")
    await db.clear_dismissed_notifications()

    # Trigger the deferred media server scans now that the restructure
    # is no longer blocking them.
    from src.Web.Routes.Onboarding import _auto_scan_media_servers

    spawn_background_task(
        request.app.state, _auto_scan_media_servers(request.app.state)
    )

    return RedirectResponse(
        url="/library?message=Restructure+skipped.+Media+server+scans+started.",
        status_code=303,
    )


@router.post("/restructure/execute")
async def restructure_execute(request: Request) -> RedirectResponse:
    """Execute selected restructure groups."""
    if _is_restructure_busy(request.app.state):
        return RedirectResponse(
            url="/restructure?error=A+restructure+operation+is+already+running",
            status_code=303,
        )

    plan: RestructurePlan | None = getattr(request.app.state, "restructure_plan", None)

    if not plan:
        return RedirectResponse(
            url="/restructure?error=No+plan+available", status_code=303
        )

    db = request.app.state.db
    form = await request.form()
    enabled_keys = {str(v) for v in form.getlist("group_key")}

    # Update enabled state on groups
    for group in plan.groups:
        group.enabled = group.group_key in enabled_keys

    if not any(g.enabled for g in plan.groups):
        # No groups selected — seed library from plan data, then trigger scans
        await _seed_library_from_plan(request.app.state, plan)
        request.app.state.library_already_seeded = True  # type: ignore[attr-defined]

        request.app.state.restructure_plan = None
        request.app.state.onboarding_restructure_plan = None
        request.app.state.restructure_progress = None
        await db.dismiss_notifications_by_url("/restructure/preview")
        await db.clear_dismissed_notifications()

        from src.Web.Routes.Onboarding import _auto_scan_media_servers

        spawn_background_task(
            request.app.state,
            _auto_scan_media_servers(request.app.state),
        )
        return RedirectResponse(
            url="/library?message=No+changes+applied.+Library+indexed.+Media+server+scans+started.",
            status_code=303,
        )

    # Persist the pending naming templates now that the user has confirmed.
    # This ensures cancelled analyses don't overwrite the last applied settings.
    pending_tpl = getattr(request.app.state, "pending_naming_templates", None)
    if pending_tpl:
        for setting_key, val in pending_tpl.items():
            await db.set_setting(setting_key, val)
        request.app.state.pending_naming_templates = None

    request.app.state.restructure_exec_progress = RestructureProgress(
        status="running", phase="Starting restructure…"
    )
    request.app.state.restructure_stats = None

    # Dismiss the "review plan" notification now that user has approved
    await db.dismiss_notifications_by_url("/restructure/preview")
    await db.clear_dismissed_notifications()

    spawn_background_task(request.app.state, _run_execution(request.app.state))

    return RedirectResponse(url="/restructure/progress", status_code=303)


@router.get("/restructure/report", response_class=HTMLResponse)
async def restructure_report_page(request: Request) -> HTMLResponse:
    """Persistent restructure operation log — browseable at any time."""
    db = request.app.state.db
    templates = request.app.state.templates

    limit = int(request.query_params.get("limit", "1000"))
    status_filter = request.query_params.get("status", "")  # "", "success", "error"

    entries = await db.get_restructure_log(limit=limit)
    if status_filter:
        entries = [e for e in entries if e["status"] == status_filter]

    moved = sum(1 for e in entries if e["status"] == "success")
    errors = sum(1 for e in entries if e["status"] == "error")

    return templates.TemplateResponse(
        "restructure_report.html",
        {
            "request": request,
            "entries": entries,
            "moved": moved,
            "errors": errors,
            "total": len(entries),
            "status_filter": status_filter,
        },
    )


@router.get("/restructure/results", response_class=HTMLResponse)
async def restructure_results(request: Request) -> Response:
    """Render the results page after execution."""
    templates = request.app.state.templates
    stats: dict[str, int] | None = getattr(request.app.state, "restructure_stats", None)
    plan: RestructurePlan | None = getattr(request.app.state, "restructure_plan", None)
    source_mode: str = getattr(request.app.state, "restructure_source_mode", "plex")

    if not stats:
        return RedirectResponse(
            url="/restructure?error=No+results+available", status_code=303
        )

    return templates.TemplateResponse(
        "restructure_results.html",
        {
            "request": request,
            "stats": stats,
            "plan": plan,
            "operation_level": plan.operation_level if plan else "full_restructure",
            "source_mode": source_mode,
        },
    )
