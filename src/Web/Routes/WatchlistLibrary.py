"""Watchlist library view — browse AniList watchlist with local/*arr status."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse

from src.Clients.RadarrClient import RadarrClient
from src.Clients.SonarrClient import SonarrClient
from src.Download.MappingResolver import MappingResolver
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.NamingTranslator import (
    GET_FULL_MEDIA_QUERY,
    build_title_chain,
    collect_series_chain,
    is_movie_format,
)
from src.Web.App import spawn_background_task
from src.Web.Routes.Helpers import enrich_watchlist_entries

logger = logging.getLogger(__name__)

router = APIRouter(tags=["library"])


@router.get("/watchlist", response_class=HTMLResponse)
async def library_page(request: Request) -> HTMLResponse:
    """Render the AniList watchlist library view."""
    db = request.app.state.db
    templates = request.app.state.templates

    users = await db.get_users_by_service("anilist")
    user = users[0] if users else None
    user_id: str = user["user_id"] if user else ""

    entries: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    if user_id:
        raw_entries = await db.get_watchlist(user_id)
        entries = await enrich_watchlist_entries(db, raw_entries)

        for entry in entries:
            s = entry.get("list_status", "")
            status_counts[s] = status_counts.get(s, 0) + 1

    cfg = request.app.state.config
    arr_enabled = bool(cfg.sonarr.url and cfg.sonarr.api_key) or bool(
        cfg.radarr.url and cfg.radarr.api_key
    )
    title_display = await db.get_setting("app.title_display") or "romaji"

    # Fetch Sonarr/Radarr file stats to show local availability from *arr
    sonarr_stats: dict[int, dict] = {}  # sonarr_id -> {0: all, N: season}
    radarr_has_file: dict[int, bool] = {}  # radarr_id -> has_file

    if entries:
        unique_sonarr_ids = {e["sonarr_id"] for e in entries if e.get("sonarr_id")}
        unique_radarr_ids = {e["radarr_id"] for e in entries if e.get("radarr_id")}

        if unique_sonarr_ids and cfg.sonarr.url and cfg.sonarr.api_key:
            try:
                sc = SonarrClient(url=cfg.sonarr.url, api_key=cfg.sonarr.api_key)
                all_series = await sc.get_all_series()
                for s in all_series:
                    sid = s.get("id")
                    if sid is None or sid not in unique_sonarr_ids:
                        continue
                    top = s.get("statistics") or {}
                    entry_stats: dict[int | str, dict] = {
                        "all": {
                            "files": top.get("episodeFileCount", 0),
                            "total": top.get("totalEpisodeCount", 0),
                        }
                    }
                    for season in s.get("seasons") or []:
                        sn = season.get("seasonNumber", 0)
                        if sn == 0:
                            continue
                        ss = season.get("statistics") or {}
                        entry_stats[sn] = {
                            "files": ss.get("episodeFileCount", 0),
                            "total": ss.get("totalEpisodeCount", 0),
                        }
                    sonarr_stats[sid] = entry_stats
            except Exception as exc:
                logger.warning("Could not fetch Sonarr series stats: %s", exc)

        if unique_radarr_ids and cfg.radarr.url and cfg.radarr.api_key:
            try:
                rc = RadarrClient(url=cfg.radarr.url, api_key=cfg.radarr.api_key)
                all_movies = await rc.get_all_movies()
                for m in all_movies:
                    mid = m.get("id")
                    if mid is not None and mid in unique_radarr_ids:
                        radarr_has_file[mid] = bool(m.get("hasFile"))
            except Exception as exc:
                logger.warning("Could not fetch Radarr movie stats: %s", exc)

    # Attach file stats to each entry
    for entry in entries:
        arr_files = arr_total = 0
        arr_has_file = False

        sid = entry.get("sonarr_id")
        rid = entry.get("radarr_id")
        season = entry.get("sonarr_season")
        if sid and sid in sonarr_stats:
            if season and season in sonarr_stats[sid]:
                bucket = sonarr_stats[sid][season]
                arr_files = bucket.get("files", 0)
                arr_total = bucket.get("total", 0)
                arr_has_file = arr_files > 0
            else:
                all_bucket = sonarr_stats[sid].get("all", {})
                arr_has_file = all_bucket.get("files", 0) > 0
                arr_files = 0
                arr_total = 0
        elif rid:
            arr_has_file = radarr_has_file.get(rid, False)
            arr_files = 1 if arr_has_file else 0
            arr_total = 1

        entry["arr_files"] = arr_files
        entry["arr_total"] = arr_total
        entry["arr_has_file"] = arr_has_file

        # Elevate local_status if *arr has files
        if arr_has_file and entry.get("local_status") == "missing":
            entry["local_status"] = "arr"

    return templates.TemplateResponse(
        "watchlist_library.html",
        {
            "request": request,
            "entries": entries,
            "total_count": len(entries),
            "status_counts": status_counts,
            "user": user,
            "user_id": user_id,
            "arr_enabled": arr_enabled,
            "title_display": title_display,
        },
    )


_ANILIST_LINK_TAG = "anilist-link"


async def _build_arr_clients(
    config: Any, is_movie: bool
) -> tuple[SonarrClient | None, RadarrClient | None, str]:
    """Return (sonarr_client, radarr_client, error). Only one will be non-None."""
    if not is_movie:
        if config.sonarr.url and config.sonarr.api_key:
            return (
                SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key),
                None,
                "",
            )
        return None, None, "Sonarr not configured"
    if config.radarr.url and config.radarr.api_key:
        return (
            None,
            RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key),
            "",
        )
    return None, None, "Radarr not configured"


async def _fetch_arr_defaults(
    sonarr_client: SonarrClient | None,
    radarr_client: RadarrClient | None,
    config: Any = None,
) -> tuple[int, str]:
    """Return (quality_profile_id, root_folder_path) from arr client.

    Uses sonarr.anime_root_folder / radarr.anime_root_folder from config when
    set; otherwise falls back to the first root folder returned by the service.
    """
    quality_id = 1
    root_folder = ""

    # Prefer the configured anime root folder
    if config:
        if sonarr_client and config.sonarr.anime_root_folder:
            root_folder = config.sonarr.anime_root_folder
        elif radarr_client and config.radarr.anime_root_folder:
            root_folder = config.radarr.anime_root_folder

    try:
        client = sonarr_client or radarr_client
        if client:
            profiles = await client.get_quality_profiles()
            if profiles:
                quality_id = profiles[0].get("id", 1)
            if not root_folder:
                roots = await client.get_root_folders()
                if roots:
                    root_folder = roots[0].get("path", "")
    except Exception as exc:
        logger.warning("Could not fetch arr root/quality: %s", exc)
    return quality_id, root_folder


async def _get_entry_info(db: Any, anilist_id: int) -> tuple[str, str]:
    """Return (anilist_format, anilist_title) from watchlist or cache."""
    anilist_format = ""
    anilist_title = ""
    users = await db.get_users_by_service("anilist")
    if users:
        entry = await db.get_watchlist_entry(users[0]["user_id"], anilist_id)
        if entry:
            anilist_format = entry.get("anilist_format", "") or ""
            anilist_title = entry.get("anilist_title", "") or ""
    if not anilist_format:
        cached = await db.get_cached_metadata(anilist_id)
        if cached:
            anilist_format = cached.get("format", "") or ""
            anilist_title = (
                cached.get("title_romaji") or cached.get("title_english") or ""
            )
    return anilist_format, anilist_title


@router.get("/api/watchlist/sonarr-lookup")
async def sonarr_lookup(request: Request) -> JSONResponse:
    """Search Sonarr's series lookup for disambiguation candidates.

    Query params: q (title to search)
    """
    config = request.app.state.config
    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "q is required"}, status_code=400)
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=503)

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        results = await client.lookup_series(q)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()

    candidates = [
        {
            "tvdb_id": r.get("tvdbId"),
            "title": r.get("title", ""),
            "year": r.get("year"),
            "status": r.get("status", ""),
            "overview": (r.get("overview") or "")[:200],
            "remote_poster": (
                r.get("remotePoster") or r.get("images", [{}])[0].get("remoteUrl", "")
                if r.get("images")
                else ""
            ),
        }
        for r in results
        if r.get("tvdbId")
    ]
    return JSONResponse({"candidates": candidates[:8]})


async def _auto_link_sonarr_siblings(
    db: Any,
    anilist_client: Any,
    root_anilist_id: int,
    tvdb_id: int,
    sonarr_id: int,
    sonarr_url: str,
    sonarr_api_key: str,
) -> None:
    """Background: BFS-traverse the full SEQUEL/PREQUEL chain and auto-link siblings.

    For multi-season shows (e.g. AoT, Demon Slayer, 86), discovers ALL AniList
    entries sharing the same TVDB series, fetches Sonarr's season list, and
    assigns sonarr_season numbers when the chain length matches the season count
    (1:1 chronological assignment).
    """
    try:
        # Full BFS chain in chronological order (all entries sharing this TVDB ID)
        chain = await collect_series_chain(root_anilist_id, tvdb_id, anilist_client)
        siblings = [aid for aid in chain if aid != root_anilist_id]

        # Build series group so post-processor can resolve root entry for folder naming
        try:
            builder = SeriesGroupBuilder(db=db, anilist_client=anilist_client)
            await builder.get_or_build_group(root_anilist_id)
            logger.info("Built series group for anilist_id=%d", root_anilist_id)
        except Exception as exc:
            logger.warning(
                "Failed to build series group for anilist_id=%d: %s",
                root_anilist_id,
                exc,
            )

        # Load watchlist and cache for title lookups
        users = await db.get_users_by_service("anilist")
        if not users:
            return
        user_id: str = users[0]["user_id"]
        watchlist_rows = await db.fetch_all(
            "SELECT anilist_id, anilist_title FROM user_watchlist WHERE user_id=?",
            (user_id,),
        )
        watchlist_titles: dict[int, str] = {
            row["anilist_id"]: (row["anilist_title"] or "") for row in watchlist_rows
        }
        # Cache titles as fallback for chain entries not in the user's watchlist
        cache_rows = await db.fetch_all(
            "SELECT anilist_id, title_romaji, title_english FROM anilist_cache"
        )
        cache_titles: dict[int, str] = {
            row["anilist_id"]: (row["title_romaji"] or row["title_english"] or "")
            for row in cache_rows
        }

        # Fetch episode counts for each chain entry (used for cumulative matching).
        # Primary: watchlist. Supplement with anilist_cache for chain entries
        # (e.g. S1 of a show) that may not be in the user's watchlist.
        episode_counts: dict[int, int | None] = {}
        ep_rows = await db.fetch_all(
            "SELECT anilist_id, anilist_episodes FROM user_watchlist WHERE user_id=?",
            (user_id,),
        )
        for row in ep_rows:
            episode_counts[row["anilist_id"]] = row["anilist_episodes"]
        cache_rows = await db.fetch_all(
            "SELECT anilist_id, episodes FROM anilist_cache"
        )
        for row in cache_rows:
            if row["anilist_id"] not in episode_counts:
                episode_counts[row["anilist_id"]] = row["episodes"]

        # Fetch Sonarr season list and per-season episode totals
        sonarr_seasons: list[int] = []
        sonarr_season_totals: dict[int, int] = {}  # season_num -> totalEpisodeCount
        try:
            client = SonarrClient(url=sonarr_url, api_key=sonarr_api_key)
            series_data = await client.get_series_by_id(sonarr_id)
            if series_data:
                for s in series_data.get("seasons") or []:
                    sn = s.get("seasonNumber", 0)
                    if sn == 0:
                        continue
                    sonarr_seasons.append(sn)
                    ss = s.get("statistics") or {}
                    sonarr_season_totals[sn] = ss.get("totalEpisodeCount", 0)
                sonarr_seasons.sort()
        except Exception as exc:
            logger.warning(
                "Could not fetch Sonarr seasons for series_id=%d: %s", sonarr_id, exc
            )

        # Season assignment: prefer 1:1 when counts match, else try cumulative
        season_map: dict[int, int | None] = {}
        if sonarr_seasons and len(chain) == len(sonarr_seasons):
            # Perfect 1:1 chronological assignment
            for idx, aid in enumerate(chain):
                season_map[aid] = sonarr_seasons[idx]
            logger.info(
                "Season assignment (1:1) for tvdb_id=%d: %s",
                tvdb_id,
                {aid: season_map[aid] for aid in chain},
            )
        elif sonarr_seasons and all(episode_counts.get(aid) for aid in chain):
            # Cumulative episode-range assignment: map each AniList entry to
            # whichever Sonarr season contains its starting episode.
            # Multiple AniList parts can map to the same Sonarr season.
            sonarr_ranges: list[tuple[int, int, int]] = []  # (start, end, season_num)
            cum = 1
            for sn in sonarr_seasons:
                total = sonarr_season_totals.get(sn, 0)
                if total > 0:
                    sonarr_ranges.append((cum, cum + total - 1, sn))
                    cum += total

            anilist_start = 1
            for aid in chain:
                eps = episode_counts.get(aid) or 0
                assigned: int | None = None
                for s_start, s_end, sn in sonarr_ranges:
                    if anilist_start <= s_end:
                        assigned = sn
                        break
                season_map[aid] = assigned
                anilist_start += eps

            logger.info(
                "Season assignment (cumulative) for tvdb_id=%d: %s",
                tvdb_id,
                {aid: season_map[aid] for aid in chain},
            )
        else:
            for aid in chain:
                season_map[aid] = None
            if sonarr_seasons:
                logger.debug(
                    "Season assignment skipped: chain_len=%d sonarr_seasons=%d"
                    " known_eps=%d for tvdb_id=%d",
                    len(chain),
                    len(sonarr_seasons),
                    sum(1 for aid in chain if episode_counts.get(aid)),
                    tvdb_id,
                )

        # Update root entry's sonarr_season if we now have an assignment
        root_season = season_map.get(root_anilist_id)
        if root_season is not None:
            await db.execute(
                "UPDATE anilist_sonarr_mapping SET sonarr_season=? WHERE anilist_id=?",
                (root_season, root_anilist_id),
            )
            logger.info(
                "Assigned sonarr_season=%d to root anilist_id=%d",
                root_season,
                root_anilist_id,
            )

        # Process siblings
        for related_id in siblings:
            sibling_season = season_map.get(related_id)

            existing = await db.fetch_one(
                "SELECT sonarr_id, sonarr_season FROM anilist_sonarr_mapping"
                " WHERE anilist_id=? AND in_sonarr=1",
                (related_id,),
            )
            if existing:
                # Update sonarr_season if we now have an assignment they're missing
                if sibling_season is not None and existing["sonarr_season"] is None:
                    await db.execute(
                        "UPDATE anilist_sonarr_mapping SET sonarr_season=?"
                        " WHERE anilist_id=?",
                        (sibling_season, related_id),
                    )
                    logger.info(
                        "Updated sonarr_season=%d for existing sibling anilist_id=%d",
                        sibling_season,
                        related_id,
                    )
                continue

            # Link all chain siblings regardless of watchlist membership so every
            # season shows as "in Sonarr" in the UI and post-processor can route them.
            title = (
                watchlist_titles.get(related_id) or cache_titles.get(related_id) or ""
            )
            await db.execute(
                """INSERT INTO anilist_sonarr_mapping
                       (anilist_id, tvdb_id, sonarr_id, sonarr_title, sonarr_season,
                        in_sonarr, sonarr_monitored, monitor_type)
                   VALUES (?, ?, ?, ?, ?, 1, 1, 'future')
                   ON CONFLICT(anilist_id) DO UPDATE SET
                       tvdb_id=excluded.tvdb_id,
                       sonarr_id=excluded.sonarr_id,
                       sonarr_title=excluded.sonarr_title,
                       sonarr_season=excluded.sonarr_season,
                       in_sonarr=1,
                       sonarr_monitored=1,
                       monitor_type='future',
                       updated_at=datetime('now')
                """,
                (related_id, tvdb_id, sonarr_id, title, sibling_season),
            )
            logger.info(
                "Auto-linked sibling anilist_id=%d → sonarr_id=%d season=%s"
                " (tvdb_id=%d, chain_root=%d)",
                related_id,
                sonarr_id,
                sibling_season,
                tvdb_id,
                root_anilist_id,
            )

        # Populate anilist_sonarr_season_mapping for the post-processor.
        # Use INSERT OR REPLACE so re-adding a series always corrects stale mappings.
        mapped_count = 0
        for aid in chain:
            s_num = season_map.get(aid)
            if s_num is not None:
                await db.execute(
                    """INSERT OR REPLACE INTO anilist_sonarr_season_mapping
                       (sonarr_id, season_number, anilist_id)
                       VALUES (?, ?, ?)""",
                    (sonarr_id, s_num, aid),
                )
                mapped_count += 1
        if mapped_count:
            logger.info(
                "Populated %d season mappings for sonarr_id=%d",
                mapped_count,
                sonarr_id,
            )
    except Exception as exc:
        logger.warning(
            "auto_link_sonarr_siblings failed for anilist_id=%d: %s",
            root_anilist_id,
            exc,
        )


@router.post("/api/library/add-to-arr")
async def add_to_arr(request: Request) -> JSONResponse:
    """Add an AniList entry to Sonarr or Radarr.

    Body JSON: { anilist_id, tvdb_id? }
    If tvdb_id is supplied it skips TVDB resolution (user confirmed via picker).
    On success returns { ok, service, arr_id }.
    If TVDB can't be resolved automatically returns { needs_disambiguation: true }.
    """
    db = request.app.state.db
    config = request.app.state.config
    anilist_client = request.app.state.anilist_client

    if not (config.sonarr.url and config.sonarr.api_key) and not (
        config.radarr.url and config.radarr.api_key
    ):
        return JSONResponse(
            {"error": "Sonarr/Radarr is not configured."},
            status_code=503,
        )

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    anilist_id: int = int(body.get("anilist_id", 0))
    if not anilist_id:
        return JSONResponse({"error": "anilist_id required"}, status_code=400)

    tvdb_id_override: int | None = int(body["tvdb_id"]) if body.get("tvdb_id") else None
    monitor_strategy: str = str(body.get("monitor_strategy") or "future")
    valid_strategies = {
        "future",
        "all",
        "firstSeason",
        "latestSeason",
        "none",
        "missing",
    }
    if monitor_strategy not in valid_strategies:
        monitor_strategy = "future"

    anilist_format, anilist_title = await _get_entry_info(db, anilist_id)
    is_movie = is_movie_format(anilist_format)

    sonarr_client, radarr_client, err = await _build_arr_clients(config, is_movie)
    if err:
        return JSONResponse({"error": err}, status_code=503)

    quality_id, root_folder = await _fetch_arr_defaults(
        sonarr_client, radarr_client, config
    )

    # Get or create the anilist-link tag in Sonarr
    tags: list[int] = []
    if sonarr_client:
        try:
            tag_id = await sonarr_client.get_or_create_tag(_ANILIST_LINK_TAG)
            tags = [tag_id]
        except Exception as exc:
            logger.warning("Could not get/create Sonarr tag: %s", exc)

    resolver = MappingResolver(
        db=db,
        anilist_client=anilist_client,
        sonarr_client=sonarr_client,
        radarr_client=radarr_client,
    )

    media: dict[str, Any] = {"title": {"romaji": anilist_title}, "synonyms": []}
    try:
        result = await resolver.resolve_and_add(
            anilist_id=anilist_id,
            anilist_format=anilist_format,
            anilist_media=media,
            quality_profile_id=quality_id,
            root_folder_path=root_folder,
            monitored=monitor_strategy != "none",
            monitor_strategy=monitor_strategy,
            search_immediately=True,
            tags=tags,
            tvdb_id_override=tvdb_id_override,
        )

        # Auto-link sequels/prequels that share the same Sonarr series
        if (
            result.ok
            and result.arr_id
            and result.external_id
            and result.service == "sonarr"
        ):
            spawn_background_task(
                request.app.state,
                _auto_link_sonarr_siblings(
                    db=db,
                    anilist_client=anilist_client,
                    root_anilist_id=anilist_id,
                    tvdb_id=result.external_id,
                    sonarr_id=result.arr_id,
                    sonarr_url=config.sonarr.url,
                    sonarr_api_key=config.sonarr.api_key,
                ),
            )

        # Push all AniList title variants to Sonarr so it can find releases
        if result.ok and result.arr_id and sonarr_client:
            try:
                media_data = await anilist_client._execute_query(
                    GET_FULL_MEDIA_QUERY, {"id": anilist_id}
                )
                full_media = media_data.get("Media", {})
                if full_media:
                    alt_titles = [
                        t for t in build_title_chain(full_media) if t and len(t) > 2
                    ]
                    if alt_titles:
                        await sonarr_client.push_alt_titles(result.arr_id, alt_titles)
                        logger.info(
                            "Pushed %d alt titles to Sonarr for anilist_id=%d",
                            len(alt_titles),
                            anilist_id,
                        )
            except Exception as exc:
                logger.warning("Could not push alt titles to Sonarr: %s", exc)
    finally:
        if sonarr_client:
            await sonarr_client.close()
        if radarr_client:
            await radarr_client.close()

    if result.ok:
        logger.info(
            "Added anilist_id=%d to %s (arr_id=%s)",
            anilist_id,
            result.service,
            result.arr_id,
        )
        return JSONResponse(
            {"ok": True, "service": result.service, "arr_id": result.arr_id}
        )

    if result.needs_disambiguation:
        return JSONResponse(
            {
                "needs_disambiguation": True,
                "title": anilist_title,
                "service": result.service,
                "candidates": result.disambiguation_candidates,
            }
        )

    logger.error("Failed to add anilist_id=%d to *arr: %s", anilist_id, result.error)
    return JSONResponse({"error": result.error}, status_code=500)


@router.post("/api/watchlist/refresh")
async def refresh_watchlist(request: Request) -> JSONResponse:
    """Fetch user's AniList list and bulk-upsert into user_watchlist.

    Accepts optional ``user_id`` in JSON body; defaults to first linked user.
    """
    db = request.app.state.db
    anilist_client = request.app.state.anilist_client

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    user_id: str = body.get("user_id", "")
    if not user_id:
        users = await db.get_users_by_service("anilist")
        if not users:
            return JSONResponse({"error": "No linked AniList user"}, status_code=400)
        user_id = users[0]["user_id"]
        user_row = users[0]
    else:
        user_row = await db.get_user(user_id)
        if not user_row:
            return JSONResponse({"error": "User not found"}, status_code=404)

    anilist_user_id: int = user_row.get("anilist_id", 0)
    access_token: str = user_row.get("access_token", "")

    if not anilist_user_id:
        return JSONResponse({"error": "No AniList user ID on record"}, status_code=400)

    try:
        entries = await anilist_client.get_user_watchlist(
            anilist_user_id, access_token or None
        )
        count = await db.bulk_upsert_watchlist(user_id, entries)
        logger.info("Refreshed watchlist for user_id=%s: %d entries", user_id, count)
        return JSONResponse({"ok": True, "count": count})
    except Exception as exc:
        logger.exception("Watchlist refresh failed for user_id=%s", user_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/watchlist")
async def get_watchlist_json(request: Request) -> JSONResponse:
    """Return watchlist entries as JSON.

    Accepts ``?status=CURRENT,PLANNING`` for filtering (comma-separated).
    """
    db = request.app.state.db

    users = await db.get_users_by_service("anilist")
    if not users:
        return JSONResponse({"entries": [], "total": 0})

    user_id = users[0]["user_id"]

    status_param = request.query_params.get("status", "")
    list_statuses: list[str] | None = None
    if status_param:
        list_statuses = [s.strip() for s in status_param.split(",") if s.strip()]

    entries = await db.get_watchlist(user_id, list_statuses=list_statuses)
    return JSONResponse({"entries": entries, "total": len(entries)})


# ---------------------------------------------------------------------------
# Release search / grab
# ---------------------------------------------------------------------------


def _format_releases(releases: Any) -> list[dict[str, Any]]:
    """Normalize raw Sonarr/Radarr release objects for the UI."""
    if not isinstance(releases, list):
        return []
    out = []
    for r in releases:
        if not isinstance(r, dict):
            continue

        size_bytes: int = r.get("size", 0) or 0
        if size_bytes >= 1_073_741_824:
            size_str = f"{size_bytes / 1_073_741_824:.2f} GB"
        else:
            size_str = f"{size_bytes / 1_048_576:.0f} MB"

        age_hours: float = r.get("ageHours") or (r.get("age", 0) * 24)
        if age_hours >= 48:
            age_str = f"{int(age_hours / 24)}d"
        elif age_hours >= 1:
            age_str = f"{int(age_hours)}h"
        else:
            age_str = "< 1h"

        quality_obj = r.get("quality") or {}
        if isinstance(quality_obj, dict):
            inner = quality_obj.get("quality") or {}
            quality_name = (
                inner.get("name", "?") if isinstance(inner, dict) else str(inner)
            )
        else:
            quality_name = "?"

        raw_rj = r.get("rejections") or []
        rejections = [
            rj if isinstance(rj, str) else rj.get("reason") or rj.get("message", "")
            for rj in raw_rj
            if rj
        ]

        out.append(
            {
                "guid": r.get("guid", ""),
                "indexer_id": r.get("indexerId", 0),
                "title": r.get("title", ""),
                "quality": quality_name,
                "size": size_str,
                "seeders": r.get("seeders"),
                "age": age_str,
                "indexer": r.get("indexer", ""),
                "rejected": bool(r.get("rejected", False)),
                "rejections": rejections,
            }
        )
    return out


@router.get("/api/watchlist/releases")
async def get_releases(request: Request) -> JSONResponse:
    """Fetch available releases for an entry from Sonarr or Radarr.

    Query params: anilist_id
    Triggers a live indexer search — may take up to 90s.
    """
    db = request.app.state.db
    config = request.app.state.config

    anilist_id_str = request.query_params.get("anilist_id", "")
    if not anilist_id_str:
        return JSONResponse({"error": "anilist_id required"}, status_code=400)
    anilist_id = int(anilist_id_str)

    sonarr_row = await db.fetch_one(
        "SELECT sonarr_id FROM anilist_sonarr_mapping"
        " WHERE anilist_id=? AND in_sonarr=1",
        (anilist_id,),
    )
    if sonarr_row and config.sonarr.url and config.sonarr.api_key:
        sonarr_id: int = sonarr_row["sonarr_id"]
        client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
        try:
            releases = await client.search_releases_long(sonarr_id)
            return JSONResponse(
                {
                    "service": "sonarr",
                    "arr_id": sonarr_id,
                    "releases": _format_releases(releases),
                }
            )
        except Exception as exc:
            logger.warning("Sonarr release search failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            await client.close()

    radarr_row = await db.fetch_one(
        "SELECT radarr_id FROM anilist_radarr_mapping"
        " WHERE anilist_id=? AND in_radarr=1",
        (anilist_id,),
    )
    if radarr_row and config.radarr.url and config.radarr.api_key:
        radarr_id: int = radarr_row["radarr_id"]
        client_r = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
        try:
            releases = await client_r.search_releases_long(radarr_id)
            return JSONResponse(
                {
                    "service": "radarr",
                    "arr_id": radarr_id,
                    "releases": _format_releases(releases),
                }
            )
        except Exception as exc:
            logger.warning("Radarr release search failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            await client_r.close()

    return JSONResponse(
        {"error": "Entry not tracked in Sonarr or Radarr"}, status_code=404
    )


def _check_push_response(push_resp: Any) -> JSONResponse:
    """Interpret Sonarr/Radarr release/push response.

    The endpoint returns a list of result objects, each with ``approved`` and
    ``rejections``.  A 200 OK does NOT mean the release was actually queued —
    check the payload.
    """
    items = push_resp if isinstance(push_resp, list) else [push_resp]
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("approved") is False or item.get("rejected") is True:
            raw_rj = item.get("rejections") or []
            reasons = []
            for rj in raw_rj:
                if isinstance(rj, str):
                    reasons.append(rj)
                elif isinstance(rj, dict):
                    reasons.append(rj.get("reason") or rj.get("message", "unknown"))
            msg = "; ".join(reasons) if reasons else "Release rejected by Sonarr/Radarr"
            logger.warning("Push release rejected: %s", msg)
            return JSONResponse({"error": msg}, status_code=200)
    return JSONResponse({"ok": True})


@router.post("/api/watchlist/grab")
async def grab_release(request: Request) -> JSONResponse:
    """Instruct Sonarr/Radarr to grab a specific release by GUID + indexer_id.

    Body JSON: { guid, indexer_id, service }
    """
    config = request.app.state.config

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    service: str = body.get("service", "sonarr")

    # Standard arr grab by GUID + indexer_id
    guid: str = body.get("guid", "")
    indexer_id: int = int(body.get("indexer_id", 0))
    if not guid or not indexer_id:
        return JSONResponse({"error": "guid and indexer_id required"}, status_code=400)

    if service == "sonarr":
        if not config.sonarr.url or not config.sonarr.api_key:
            return JSONResponse({"error": "Sonarr not configured"}, status_code=503)
        client_s2 = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
        try:
            await client_s2.grab_release(guid, indexer_id)
            return JSONResponse({"ok": True})
        except Exception as exc:
            logger.warning("Sonarr grab failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            await client_s2.close()
    else:
        if not config.radarr.url or not config.radarr.api_key:
            return JSONResponse({"error": "Radarr not configured"}, status_code=503)
        client_r2 = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
        try:
            await client_r2.grab_release(guid, indexer_id)
            return JSONResponse({"ok": True})
        except Exception as exc:
            logger.warning("Radarr grab failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            await client_r2.close()


@router.get("/api/watchlist/resolve")
async def resolve_arr_match(request: Request) -> JSONResponse:
    """Preview the Sonarr/Radarr match for an AniList entry without adding it.

    Returns the resolved series info plus any existing siblings that share the
    same Sonarr series (multi-season awareness).

    Query params: anilist_id
    """
    db = request.app.state.db
    config = request.app.state.config
    anilist_client = request.app.state.anilist_client

    anilist_id_str = request.query_params.get("anilist_id", "")
    if not anilist_id_str:
        return JSONResponse({"error": "anilist_id required"}, status_code=400)
    anilist_id = int(anilist_id_str)

    anilist_format, anilist_title = await _get_entry_info(db, anilist_id)
    is_movie = is_movie_format(anilist_format)

    if is_movie:
        if not config.radarr.url or not config.radarr.api_key:
            return JSONResponse({"error": "Radarr not configured"}, status_code=503)

        from src.Utils.NamingTranslator import resolve_tmdb_id

        tmdb_id = await resolve_tmdb_id(anilist_id, anilist_client)
        if not tmdb_id:
            return JSONResponse(
                {
                    "resolved": False,
                    "error": f"Could not resolve TMDB ID for {anilist_title!r}",
                }
            )

        client_r = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
        try:
            result = await client_r.lookup_movie_by_tmdb(tmdb_id)
            if not result:
                return JSONResponse(
                    {
                        "resolved": False,
                        "error": f"TMDB ID {tmdb_id} not found in Radarr lookup",
                    }
                )
            existing = await db.fetch_one(
                "SELECT radarr_id FROM anilist_radarr_mapping"
                " WHERE anilist_id=? AND in_radarr=1",
                (anilist_id,),
            )
            poster = result.get("remotePoster", "")
            if not poster:
                for img in result.get("images", []):
                    if img.get("coverType") == "poster":
                        poster = img.get("remoteUrl", "")
                        break
            return JSONResponse(
                {
                    "resolved": True,
                    "service": "radarr",
                    "tmdb_id": tmdb_id,
                    "arr_title": result.get("title", ""),
                    "arr_year": result.get("year"),
                    "overview": (result.get("overview") or "")[:300],
                    "poster": poster,
                    "already_in_arr": bool(existing),
                    "siblings": [],
                    "suggested_season": None,
                }
            )
        finally:
            await client_r.close()

    # Sonarr path
    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=503)

    from src.Utils.NamingTranslator import (
        resolve_tvdb_id,
        resolve_tvdb_via_prequel_chain,
        resolve_tvdb_via_title_chain,
    )

    client_s = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        tvdb_id = await resolve_tvdb_id(anilist_id, anilist_client)
        candidates: list[dict] = []

        if not tvdb_id:
            # Try walking PREQUEL relations to find root entry with TVDB link
            tvdb_id, _root_id = await resolve_tvdb_via_prequel_chain(
                anilist_id, anilist_client
            )

        if not tvdb_id:
            tvdb_id, candidates = await resolve_tvdb_via_title_chain(
                anilist_id, anilist_client, client_s
            )

        if not tvdb_id:
            return JSONResponse(
                {
                    "resolved": False,
                    "needs_disambiguation": True,
                    "service": "sonarr",
                    "candidates": candidates,
                }
            )

        result = await client_s.lookup_series_by_tvdb(tvdb_id)
        if not result:
            return JSONResponse(
                {
                    "resolved": False,
                    "needs_disambiguation": True,
                    "service": "sonarr",
                    "candidates": candidates,
                    "error": f"TVDB ID {tvdb_id} not found in Sonarr lookup",
                }
            )

        existing = await db.fetch_one(
            "SELECT sonarr_id FROM anilist_sonarr_mapping"
            " WHERE anilist_id=? AND in_sonarr=1",
            (anilist_id,),
        )
        sonarr_id: int | None = result.get("id")  # set only if already in Sonarr

        # Find siblings: other AniList entries that already map to this Sonarr series
        siblings: list[dict] = []
        if sonarr_id:
            sib_rows = await db.fetch_all(
                """SELECT m.anilist_id, m.sonarr_season, w.anilist_title
                   FROM anilist_sonarr_mapping m
                   LEFT JOIN user_watchlist w ON w.anilist_id = m.anilist_id
                   WHERE m.sonarr_id = ? AND m.anilist_id != ? AND m.in_sonarr = 1""",
                (sonarr_id, anilist_id),
            )
        else:
            sib_rows = await db.fetch_all(
                """SELECT m.anilist_id, m.sonarr_season, w.anilist_title
                   FROM anilist_sonarr_mapping m
                   LEFT JOIN user_watchlist w ON w.anilist_id = m.anilist_id
                   WHERE m.tvdb_id = ? AND m.anilist_id != ? AND m.in_sonarr = 1""",
                (tvdb_id, anilist_id),
            )
        for sib in sib_rows:
            siblings.append(
                {
                    "anilist_id": sib["anilist_id"],
                    "anilist_title": sib["anilist_title"] or "",
                    "sonarr_season": sib["sonarr_season"],
                }
            )

        suggested_season: int | None = None
        if siblings:
            max_season = max((s["sonarr_season"] or 1) for s in siblings)
            suggested_season = max_season + 1

        poster = result.get("remotePoster", "")
        if not poster:
            for img in result.get("images", []):
                if img.get("coverType") == "poster":
                    poster = img.get("remoteUrl", "") or img.get("url", "")
                    break

        seasons = result.get("seasons", [])
        season_count = len([s for s in seasons if s.get("seasonNumber", 0) > 0])

        return JSONResponse(
            {
                "resolved": True,
                "service": "sonarr",
                "tvdb_id": tvdb_id,
                "sonarr_id": sonarr_id,
                "arr_title": result.get("title", ""),
                "arr_year": result.get("year"),
                "season_count": season_count,
                "overview": (result.get("overview") or "")[:300],
                "poster": poster,
                "already_in_arr": bool(existing),
                "in_sonarr": sonarr_id is not None,
                "siblings": siblings,
                "suggested_season": suggested_season,
            }
        )
    finally:
        await client_s.close()


@router.get("/api/watchlist/resolve-stream")
async def resolve_arr_match_stream(request: Request) -> StreamingResponse:
    """SSE version of resolve — streams progress events to the UI.

    Events:
      event: status   data: {"text": "..."}       — progress update
      event: result   data: {<full JSON result>}  — final result
      event: error    data: {"error": "..."}       — error
    """
    db = request.app.state.db
    config = request.app.state.config
    anilist_client = request.app.state.anilist_client

    anilist_id_str = request.query_params.get("anilist_id", "")
    if not anilist_id_str:

        async def _err():
            yield _sse("error", {"error": "anilist_id required"})

        return StreamingResponse(_err(), media_type="text/event-stream")

    anilist_id = int(anilist_id_str)

    async def _generate():
        try:
            anilist_format, anilist_title = await _get_entry_info(db, anilist_id)
            is_movie = is_movie_format(anilist_format)

            if is_movie:
                yield _sse("status", {"text": "Resolving TMDB ID\u2026"})
                if not config.radarr.url or not config.radarr.api_key:
                    yield _sse("error", {"error": "Radarr not configured"})
                    return

                from src.Utils.NamingTranslator import resolve_tmdb_id

                tmdb_id = await resolve_tmdb_id(anilist_id, anilist_client)
                if not tmdb_id:
                    yield _sse(
                        "result",
                        {
                            "resolved": False,
                            "error": f"Could not resolve TMDB ID for {anilist_title!r}",
                        },
                    )
                    return

                yield _sse("status", {"text": "Looking up in Radarr\u2026"})
                client_r = RadarrClient(
                    url=config.radarr.url, api_key=config.radarr.api_key
                )
                try:
                    result = await client_r.lookup_movie_by_tmdb(tmdb_id)
                    if not result:
                        yield _sse(
                            "result",
                            {
                                "resolved": False,
                                "error": f"TMDB ID {tmdb_id} not found in Radarr",
                            },
                        )
                        return
                    existing = await db.fetch_one(
                        "SELECT radarr_id FROM anilist_radarr_mapping"
                        " WHERE anilist_id=? AND in_radarr=1",
                        (anilist_id,),
                    )
                    poster = result.get("remotePoster", "")
                    if not poster:
                        for img in result.get("images", []):
                            if img.get("coverType") == "poster":
                                poster = img.get("remoteUrl", "")
                                break
                    yield _sse(
                        "result",
                        {
                            "resolved": True,
                            "service": "radarr",
                            "tmdb_id": tmdb_id,
                            "arr_title": result.get("title", ""),
                            "arr_year": result.get("year"),
                            "overview": (result.get("overview") or "")[:300],
                            "poster": poster,
                            "already_in_arr": bool(existing),
                            "siblings": [],
                            "suggested_season": None,
                        },
                    )
                finally:
                    await client_r.close()
                return

            # Sonarr path
            if not config.sonarr.url or not config.sonarr.api_key:
                yield _sse("error", {"error": "Sonarr not configured"})
                return

            from src.Utils.NamingTranslator import (
                resolve_tvdb_id,
                resolve_tvdb_via_prequel_chain,
                resolve_tvdb_via_title_chain,
            )

            yield _sse("status", {"text": "Checking AniList for TVDB link\u2026"})
            client_s = SonarrClient(
                url=config.sonarr.url, api_key=config.sonarr.api_key
            )
            try:
                tvdb_id = await resolve_tvdb_id(anilist_id, anilist_client)
                candidates: list[dict] = []

                if not tvdb_id:
                    yield _sse(
                        "status",
                        {
                            "text": "Walking prequel chain for TVDB ID\u2026",
                        },
                    )
                    tvdb_id, _root_id = await resolve_tvdb_via_prequel_chain(
                        anilist_id, anilist_client
                    )

                if not tvdb_id:
                    yield _sse(
                        "status",
                        {
                            "text": "Searching Sonarr by title variants\u2026",
                        },
                    )
                    tvdb_id, candidates = await resolve_tvdb_via_title_chain(
                        anilist_id, anilist_client, client_s
                    )

                if not tvdb_id:
                    yield _sse(
                        "result",
                        {
                            "resolved": False,
                            "needs_disambiguation": True,
                            "service": "sonarr",
                            "candidates": candidates,
                        },
                    )
                    return

                yield _sse("status", {"text": "Fetching series details\u2026"})
                result = await client_s.lookup_series_by_tvdb(tvdb_id)
                if not result:
                    yield _sse(
                        "result",
                        {
                            "resolved": False,
                            "needs_disambiguation": True,
                            "service": "sonarr",
                            "candidates": candidates,
                            "error": f"TVDB {tvdb_id} not found in Sonarr",
                        },
                    )
                    return

                existing = await db.fetch_one(
                    "SELECT sonarr_id FROM anilist_sonarr_mapping"
                    " WHERE anilist_id=? AND in_sonarr=1",
                    (anilist_id,),
                )
                sonarr_id: int | None = result.get("id")

                siblings: list[dict] = []
                if sonarr_id:
                    sib_rows = await db.fetch_all(
                        """SELECT m.anilist_id, m.sonarr_season, w.anilist_title
                           FROM anilist_sonarr_mapping m
                           LEFT JOIN user_watchlist w
                               ON w.anilist_id = m.anilist_id
                           WHERE m.sonarr_id = ? AND m.anilist_id != ?
                               AND m.in_sonarr = 1""",
                        (sonarr_id, anilist_id),
                    )
                else:
                    sib_rows = await db.fetch_all(
                        """SELECT m.anilist_id, m.sonarr_season, w.anilist_title
                           FROM anilist_sonarr_mapping m
                           LEFT JOIN user_watchlist w
                               ON w.anilist_id = m.anilist_id
                           WHERE m.tvdb_id = ? AND m.anilist_id != ?
                               AND m.in_sonarr = 1""",
                        (tvdb_id, anilist_id),
                    )
                for sib in sib_rows:
                    siblings.append(
                        {
                            "anilist_id": sib["anilist_id"],
                            "anilist_title": sib["anilist_title"] or "",
                            "sonarr_season": sib["sonarr_season"],
                        }
                    )

                suggested_season: int | None = None
                if siblings:
                    max_season = max((s["sonarr_season"] or 1) for s in siblings)
                    suggested_season = max_season + 1

                poster = result.get("remotePoster", "")
                if not poster:
                    for img in result.get("images", []):
                        if img.get("coverType") == "poster":
                            poster = img.get("remoteUrl", "") or img.get("url", "")
                            break

                seasons = result.get("seasons", [])
                season_count = len([s for s in seasons if s.get("seasonNumber", 0) > 0])

                yield _sse(
                    "result",
                    {
                        "resolved": True,
                        "service": "sonarr",
                        "tvdb_id": tvdb_id,
                        "sonarr_id": sonarr_id,
                        "arr_title": result.get("title", ""),
                        "arr_year": result.get("year"),
                        "season_count": season_count,
                        "overview": (result.get("overview") or "")[:300],
                        "poster": poster,
                        "already_in_arr": bool(existing),
                        "in_sonarr": sonarr_id is not None,
                        "siblings": siblings,
                        "suggested_season": suggested_season,
                    },
                )
            finally:
                await client_s.close()
        except Exception as exc:
            logger.error("resolve-stream error: %s", exc, exc_info=True)
            yield _sse("error", {"error": str(exc)})

    return StreamingResponse(_generate(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    """Format a single SSE message."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/api/watchlist/update-monitor")
async def update_monitor(request: Request) -> JSONResponse:
    """Update monitoring mode for a Sonarr/Radarr entry.

    Body JSON: { anilist_id, monitor_type: "all" | "future" | "none" }

    - "all"    — monitor all seasons + episodes, then trigger a series search
    - "future" — monitor series for new episodes only
    - "none"   — unmonitor series entirely
    """
    db = request.app.state.db
    config = request.app.state.config

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    anilist_id: int = int(body.get("anilist_id", 0))
    monitor_type: str = str(body.get("monitor_type") or "future")

    if not anilist_id:
        return JSONResponse({"error": "anilist_id required"}, status_code=400)
    if monitor_type not in ("all", "future", "none"):
        return JSONResponse(
            {"error": "monitor_type must be all, future, or none"}, status_code=400
        )

    monitored = monitor_type != "none"

    sonarr_row = await db.fetch_one(
        "SELECT sonarr_id, sonarr_season FROM anilist_sonarr_mapping"
        " WHERE anilist_id=? AND in_sonarr=1",
        (anilist_id,),
    )
    if sonarr_row and config.sonarr.url and config.sonarr.api_key:
        sonarr_id = int(sonarr_row["sonarr_id"])
        sonarr_season: int | None = sonarr_row["sonarr_season"]
        client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
        try:
            if monitor_type == "all":
                if sonarr_season is not None:
                    # Multi-season entry: scope to this season only
                    await client.monitor_season_episodes(sonarr_id, sonarr_season)
                    await client.trigger_season_search(sonarr_id, sonarr_season)
                else:
                    await client.monitor_all_episodes(sonarr_id)
                    await client.trigger_series_search(sonarr_id)
            elif sonarr_season is not None and monitor_type == "future":
                # Season-scoped future: ensure series + season are monitored
                await client.update_season_monitor(sonarr_id, sonarr_season, True)
            elif sonarr_season is not None and monitor_type == "none":
                # Season-scoped unmonitor: only unmonitor this season
                await client.update_season_monitor(sonarr_id, sonarr_season, False)
            else:
                await client.update_series_monitor(sonarr_id, monitored)
            await db.execute(
                "UPDATE anilist_sonarr_mapping"
                " SET sonarr_monitored=?, monitor_type=?, updated_at=datetime('now')"
                " WHERE anilist_id=?",
                (int(monitored), monitor_type, anilist_id),
            )
            return JSONResponse(
                {"ok": True, "service": "sonarr", "monitor_type": monitor_type}
            )
        except Exception as exc:
            logger.warning("update_monitor failed sonarr_id=%s: %s", sonarr_id, exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            await client.close()

    radarr_row = await db.fetch_one(
        "SELECT radarr_id FROM anilist_radarr_mapping"
        " WHERE anilist_id=? AND in_radarr=1",
        (anilist_id,),
    )
    if radarr_row and config.radarr.url and config.radarr.api_key:
        radarr_id = int(radarr_row["radarr_id"])
        client_r = RadarrClient(url=config.radarr.url, api_key=config.radarr.api_key)
        try:
            await client_r.update_movie_monitor(radarr_id, monitored)
            await db.execute(
                "UPDATE anilist_radarr_mapping"
                " SET radarr_monitored=?, monitor_type=?, updated_at=datetime('now')"
                " WHERE anilist_id=?",
                (int(monitored), monitor_type, anilist_id),
            )
            return JSONResponse(
                {"ok": True, "service": "radarr", "monitor_type": monitor_type}
            )
        except Exception as exc:
            logger.warning("update_monitor failed radarr_id=%s: %s", radarr_id, exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            await client_r.close()

    return JSONResponse(
        {"error": "Entry not tracked in Sonarr or Radarr"}, status_code=404
    )


@router.post("/api/library/add-by-tvdb")
async def add_by_tvdb(request: Request) -> JSONResponse:
    """Add a series directly to Sonarr by TVDB ID, no AniList entry required.

    Body JSON: { tvdb_id, monitor_strategy? }
    Returns { ok, service, arr_id, arr_title, already_existed? }.
    """
    config = request.app.state.config

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    tvdb_id: int = int(body.get("tvdb_id", 0))
    if not tvdb_id:
        return JSONResponse({"error": "tvdb_id required"}, status_code=400)

    monitor_strategy: str = str(body.get("monitor_strategy") or "future")
    valid_strategies = {
        "future",
        "all",
        "firstSeason",
        "latestSeason",
        "none",
        "missing",
    }
    if monitor_strategy not in valid_strategies:
        monitor_strategy = "future"

    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=503)

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        # Already in Sonarr?
        existing = await client.get_series_by_tvdb_id(tvdb_id)
        if existing:
            return JSONResponse(
                {
                    "ok": True,
                    "service": "sonarr",
                    "arr_id": existing.get("id"),
                    "arr_title": existing.get("title", ""),
                    "already_existed": True,
                }
            )

        # Lookup to get title and confirm TVDB ID is valid
        lookup = await client.lookup_series_by_tvdb(tvdb_id)
        if not lookup:
            return JSONResponse(
                {"error": f"TVDB ID {tvdb_id} not found in Sonarr lookup"},
                status_code=404,
            )
        title: str = lookup.get("title", "")

        quality_id, root_folder = await _fetch_arr_defaults(client, None, config)

        tags: list[int] = []
        try:
            tag_id = await client.get_or_create_tag(_ANILIST_LINK_TAG)
            tags = [tag_id]
        except Exception as exc:
            logger.warning("Could not get/create Sonarr tag: %s", exc)

        result = await client.add_series(
            title=title,
            tvdb_id=tvdb_id,
            quality_profile_id=quality_id,
            root_folder_path=root_folder,
            monitored=monitor_strategy != "none",
            monitor_strategy=monitor_strategy,
            search_immediately=False,
            series_type="anime",
            tags=tags,
        )
        arr_id = result.get("id")
        logger.info(
            "Quick-added tvdb_id=%d (%r) to Sonarr as arr_id=%s", tvdb_id, title, arr_id
        )
        return JSONResponse(
            {
                "ok": True,
                "service": "sonarr",
                "arr_id": arr_id,
                "arr_title": title,
                "already_existed": False,
            }
        )
    except Exception as exc:
        logger.error("add_by_tvdb failed tvdb_id=%d: %s", tvdb_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()


@router.post("/api/watchlist/push-alt-titles")
async def push_alt_titles_endpoint(request: Request) -> JSONResponse:
    """Add extra search titles to a Sonarr series' alternateTitles.

    Body JSON: { anilist_id, titles: ["DanDaDan", "Dan Da Dan", ...] }
    Sonarr will use these titles when querying indexers for releases.
    """
    db = request.app.state.db
    config = request.app.state.config

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    anilist_id: int = int(body.get("anilist_id", 0))
    titles: list[str] = [str(t) for t in (body.get("titles") or []) if t]

    if not anilist_id or not titles:
        return JSONResponse(
            {"error": "anilist_id and titles required"}, status_code=400
        )

    sonarr_row = await db.fetch_one(
        "SELECT sonarr_id FROM anilist_sonarr_mapping"
        " WHERE anilist_id=? AND in_sonarr=1",
        (anilist_id,),
    )
    if not sonarr_row or not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse(
            {"error": "Entry not in Sonarr or Sonarr not configured"}, status_code=404
        )

    client = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
    try:
        await client.push_alt_titles(sonarr_row["sonarr_id"], titles)
        return JSONResponse({"ok": True, "pushed": len(titles)})
    except Exception as exc:
        logger.warning("push_alt_titles failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        await client.close()


@router.post("/api/library/unlink-from-sonarr")
async def unlink_from_sonarr(request: Request) -> JSONResponse:
    """Remove our DB mapping for an AniList entry regardless of Sonarr state.

    Use when the Sonarr series was deleted externally and the entry is stuck
    showing as 'in Sonarr' with no way to re-add it.  Does NOT call the
    Sonarr API — purely a local DB cleanup.

    Body JSON: { anilist_id }
    """
    db = request.app.state.db
    body = await request.json()
    anilist_id: int | None = body.get("anilist_id")
    if not anilist_id:
        return JSONResponse({"error": "anilist_id required"}, status_code=400)

    # Look up sonarr_id before deleting so we can clean season mappings too
    row = await db.fetch_one(
        "SELECT sonarr_id FROM anilist_sonarr_mapping WHERE anilist_id=?",
        (anilist_id,),
    )
    sonarr_id = row["sonarr_id"] if row else None

    await db.execute(
        "DELETE FROM anilist_sonarr_mapping WHERE anilist_id=?", (anilist_id,)
    )
    if sonarr_id:
        await db.execute(
            "DELETE FROM anilist_sonarr_season_mapping"
            " WHERE sonarr_id=? AND anilist_id=?",
            (sonarr_id, anilist_id),
        )
    logger.info(
        "Unlinked anilist_id=%d from Sonarr (sonarr_id=%s)", anilist_id, sonarr_id
    )
    return JSONResponse({"ok": True, "anilist_id": anilist_id})


@router.post("/api/library/backfill-sonarr-siblings")
async def backfill_sonarr_siblings(request: Request) -> JSONResponse:
    """One-shot backfill: walk every mapped Sonarr entry, run full BFS chain
    traversal, and link any watchlist siblings that share the same TVDB series
    but are missing from anilist_sonarr_mapping.

    Safe to run multiple times — uses ON CONFLICT DO UPDATE.
    Returns a summary of entries linked per series.
    """
    db = request.app.state.db
    config = request.app.state.config
    anilist_client = request.app.state.anilist_client

    if not config.sonarr.url or not config.sonarr.api_key:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=503)

    # Load user watchlist for title lookups and membership checks
    users = await db.get_users_by_service("anilist")
    if not users:
        return JSONResponse({"error": "No AniList account linked"}, status_code=400)
    user_id: str = users[0]["user_id"]
    watchlist_rows = await db.fetch_all(
        "SELECT anilist_id, anilist_title FROM user_watchlist WHERE user_id=?",
        (user_id,),
    )
    watchlist_titles: dict[int, str] = {
        row["anilist_id"]: (row["anilist_title"] or "") for row in watchlist_rows
    }

    # All existing sonarr mappings grouped by sonarr_id
    mapped_rows = await db.fetch_all(
        "SELECT anilist_id, tvdb_id, sonarr_id, sonarr_title, sonarr_season"
        " FROM anilist_sonarr_mapping WHERE in_sonarr=1"
    )

    # Group by sonarr_id; track which anilist_ids are already mapped
    from collections import defaultdict

    groups: dict[int, list[dict]] = defaultdict(list)
    already_mapped: set[int] = set()
    for row in mapped_rows:
        groups[row["sonarr_id"]].append(dict(row))
        already_mapped.add(row["anilist_id"])

    # Fetch episode counts for cumulative season matching.
    # Primary source: user watchlist. Supplement with anilist_cache for entries
    # (e.g. S1 of a show) that appear in the BFS chain but aren't in the watchlist.
    ep_rows = await db.fetch_all(
        "SELECT anilist_id, anilist_episodes FROM user_watchlist WHERE user_id=?",
        (user_id,),
    )
    episode_counts: dict[int, int | None] = {
        row["anilist_id"]: row["anilist_episodes"] for row in ep_rows
    }
    cache_rows = await db.fetch_all("SELECT anilist_id, episodes FROM anilist_cache")
    for row in cache_rows:
        if row["anilist_id"] not in episode_counts:
            episode_counts[row["anilist_id"]] = row["episodes"]

    # Fetch Sonarr season counts and per-season totals once
    sonarr_seasons_map: dict[int, list[int]] = {}
    sonarr_season_totals_map: dict[int, dict[int, int]] = {}  # sid -> {sn -> total}
    try:
        sc = SonarrClient(url=config.sonarr.url, api_key=config.sonarr.api_key)
        all_series = await sc.get_all_series()
        for s in all_series:
            sid = s.get("id")
            if sid not in groups:
                continue
            seasons_sorted: list[int] = []
            totals: dict[int, int] = {}
            for sn_data in s.get("seasons") or []:
                sn = sn_data.get("seasonNumber", 0)
                if sn == 0:
                    continue
                seasons_sorted.append(sn)
                ss = sn_data.get("statistics") or {}
                totals[sn] = ss.get("totalEpisodeCount", 0)
            sonarr_seasons_map[sid] = sorted(seasons_sorted)
            sonarr_season_totals_map[sid] = totals
    except Exception as exc:
        logger.warning("backfill: could not fetch Sonarr series list: %s", exc)

    linked: list[dict] = []
    skipped_no_tvdb: list[int] = []

    for sonarr_id, members in groups.items():
        # Use the first member's anilist_id and tvdb_id as chain root
        root = members[0]
        tvdb_id: int | None = root["tvdb_id"]
        if not tvdb_id:
            skipped_no_tvdb.append(sonarr_id)
            continue

        # BFS chain
        try:
            chain = await collect_series_chain(
                root["anilist_id"], tvdb_id, anilist_client
            )
        except Exception as exc:
            logger.warning(
                "backfill: chain traversal failed for sonarr_id=%d: %s",
                sonarr_id,
                exc,
            )
            continue

        # Season assignment: 1:1 when counts match, else cumulative episode-range
        sonarr_seasons = sonarr_seasons_map.get(sonarr_id, [])
        sonarr_season_totals = sonarr_season_totals_map.get(sonarr_id, {})
        season_map: dict[int, int | None] = {}
        if sonarr_seasons and len(chain) == len(sonarr_seasons):
            for idx, aid in enumerate(chain):
                season_map[aid] = sonarr_seasons[idx]
        elif sonarr_seasons and all(episode_counts.get(aid) for aid in chain):
            sonarr_ranges: list[tuple[int, int, int]] = []
            cum = 1
            for sn in sonarr_seasons:
                total = sonarr_season_totals.get(sn, 0)
                if total > 0:
                    sonarr_ranges.append((cum, cum + total - 1, sn))
                    cum += total
            anilist_start = 1
            for aid in chain:
                eps = episode_counts.get(aid) or 0
                assigned: int | None = None
                for s_start, s_end, sn in sonarr_ranges:
                    if anilist_start <= s_end:
                        assigned = sn
                        break
                season_map[aid] = assigned
                anilist_start += eps
        else:
            for aid in chain:
                season_map[aid] = None

        # Update season on already-mapped entries that lack it
        for aid in chain:
            new_season = season_map.get(aid)
            if aid in already_mapped and new_season is not None:
                existing = next((m for m in members if m["anilist_id"] == aid), None)
                if existing and existing["sonarr_season"] is None:
                    await db.execute(
                        "UPDATE anilist_sonarr_mapping SET sonarr_season=?"
                        " WHERE anilist_id=?",
                        (new_season, aid),
                    )
                    linked.append(
                        {
                            "action": "season_updated",
                            "anilist_id": aid,
                            "sonarr_id": sonarr_id,
                            "sonarr_season": new_season,
                        }
                    )

        # Insert new siblings found in watchlist but not yet mapped
        root_member = members[0]
        for aid in chain:
            if aid in already_mapped:
                continue
            if aid not in watchlist_titles:
                continue
            title = watchlist_titles[aid]
            sibling_season = season_map.get(aid)
            await db.execute(
                """INSERT INTO anilist_sonarr_mapping
                       (anilist_id, tvdb_id, sonarr_id, sonarr_title, sonarr_season,
                        in_sonarr, sonarr_monitored, monitor_type)
                   VALUES (?, ?, ?, ?, ?, 1, 1, 'future')
                   ON CONFLICT(anilist_id) DO UPDATE SET
                       tvdb_id=excluded.tvdb_id,
                       sonarr_id=excluded.sonarr_id,
                       sonarr_title=excluded.sonarr_title,
                       sonarr_season=excluded.sonarr_season,
                       in_sonarr=1,
                       sonarr_monitored=1,
                       updated_at=datetime('now')
                """,
                (
                    aid,
                    tvdb_id,
                    sonarr_id,
                    root_member["sonarr_title"],
                    sibling_season,
                ),
            )
            already_mapped.add(aid)
            linked.append(
                {
                    "action": "linked",
                    "anilist_id": aid,
                    "title": title,
                    "sonarr_id": sonarr_id,
                    "sonarr_season": sibling_season,
                }
            )
            logger.info(
                "backfill: linked anilist_id=%d %r → sonarr_id=%d season=%s",
                aid,
                title,
                sonarr_id,
                sibling_season,
            )

    return JSONResponse(
        {
            "ok": True,
            "linked": linked,
            "total_linked": len([x for x in linked if x["action"] == "linked"]),
            "total_season_updates": len(
                [x for x in linked if x["action"] == "season_updated"]
            ),
            "skipped_no_tvdb": skipped_no_tvdb,
        }
    )
