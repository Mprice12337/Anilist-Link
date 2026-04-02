# Architecture Overview

This document defines the architecture of Anilist-Link, organized around its four functional pillars. It serves as the primary reference for understanding the system's design, components, and implementation status. Update this document as the codebase evolves.

**Date of Last Update**: 2026-04-01

---

## 1. Project Vision & The 4 Pillars

Anilist-Link is a self-hosted Docker container that connects AniList with media platforms (Plex, Jellyfin, Crunchyroll) and download managers (Sonarr, Radarr). Rather than being a single-purpose sync tool, it delivers four distinct capabilities:

| # | Pillar | Summary | Priority | Status |
|---|--------|---------|----------|--------|
| 2 | **File Organization** | Rename/reorganize anime files into a standardized folder structure using AniList data | 1st | ✅ Complete (L1/L2/L3) |
| 3 | **Metadata from AniList** | Write AniList metadata (titles, descriptions, posters, genres, ratings) to Plex/Jellyfin | 2nd | ✅ Complete (Plex + Jellyfin) |
| 1 | **Watch Status Sync** | Sync watch progress between Crunchyroll/Plex/Jellyfin and AniList | 3rd | Crunchyroll→AniList done; Plex/Jellyfin planned |
| 4 | **Download Management** | Send add requests to Sonarr/Radarr with AniList alternative titles for matching | 4th | Partially implemented (clients, routes, DB) |

**Implementation order**: P2 → P3 → P1 → P4

All four pillars share a common foundation: the AniList Client, Title Matcher, Series Group Builder, Database layer, and Web Dashboard.

---

## 2. High-Level System Diagram

```
                         ┌──────────────────────────────────────────────────────────────┐
                         │                     Anilist-Link Service                      │
                         │                                                              │
                         │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
                         │  │  P2: File   │  │  P3: Meta-  │  │  P1: Watch Status   │  │
                         │  │  Organize   │  │  data Write │  │  Sync               │  │
                         │  └──────┬──────┘  └──────┬──────┘  └──────┬──────────────┘  │
                         │         │                │                │                  │
  [Plex Server]    <───> │  ┌──────┴────────────────┴────────────────┴──────┐          │
                         │  │              Shared Foundation                 │          │
  [Jellyfin]       <───> │  │  AniList Client · Title Matcher · Series      │          │
                         │  │  Group Builder · Database · Scheduler         │          │
  [Crunchyroll]    ───>  │  └──────────────────────┬────────────────────────┘          │
                         │                         │                                    │
  [Sonarr/Radarr]  <───  │  ┌──────────────────────┴──────────────────────┐           │
                         │  │  P4: Download        ┌──────────────┐       │           │
                         │  │  Management          │  Web UI      │       │           │
                         │  └──────────────────────┤  (FastAPI)   │───────┘           │
  [Browser]        <───> │                         └──────────────┘                    │
                         └──────────────────────────────────────────────────────────────┘
                                                   │
                                                   ▼
                                          [AniList GraphQL API]
```

---

## 3. Shared Foundation

These components are used across multiple pillars and form the core infrastructure of the application.

### 3.1. AniList Client (`src/Clients/AnilistClient.py`)

**Status**: Fully implemented

GraphQL client handling all AniList interactions:
- **Public queries**: anime search (by title), fetch by ID, relations traversal with `relationType(version: 2)`, external links (TVDB/TMDB ID extraction)
- **Authenticated mutations**: watch status updates (per-user OAuth2 tokens)
- **OAuth2 flow**: authorization URL generation, token exchange, viewer profile fetch
- **Rate limiting**: token-bucket algorithm (90 capacity, 1.5/sec refill rate) — see `RateLimiter` class
- **Retry logic**: exponential backoff on 429 and 5xx responses

Used by: All 4 pillars

### 3.2. Title Matcher (`src/Matching/TitleMatcher.py`, `src/Matching/Normalizer.py`)

**Status**: Fully implemented

Multi-algorithm fuzzy matching engine:
- `difflib.SequenceMatcher`-based similarity with configurable threshold (default 0.75)
- Anime-specific normalization via `Normalizer.py`: season number stripping, punctuation removal, common prefix/suffix handling, year/tag extraction
- Season-aware matching: `find_best_match_with_season()` detects season indicators in titles (ordinal, roman numeral, "Part N", "Season N")
- Movie-specific matching path via `_find_best_movie_match()`
- Format filtering: excludes MOVIE/OVA/SPECIAL by default, overrideable per call
- `get_primary_title()` helper to extract best available title from AniList data

Used by: P1, P2, P3

### 3.3. Naming Template & Translator (`src/Utils/NamingTemplate.py`, `src/Utils/NamingTranslator.py`)

**Status**: Fully implemented

- **`NamingTemplate.py`**: Template rendering engine with token substitution. `parse_quality()` extracts resolution/source/codec from filenames. File and folder naming templates used by P2 restructurer.
- **`NamingTranslator.py`**: Translates between services. `resolve_tvdb_id()` and `resolve_tmdb_id()` extract IDs from AniList external links. `is_movie_format()` determines Sonarr vs Radarr routing. `get_preferred_title()` selects best title for a given AniList entry.

Used by: P2, P4

### 3.4. Series Group Builder (`src/Scanner/SeriesGroupBuilder.py`)

**Status**: Fully implemented

Builds series groups by traversing AniList's relation graph:
- BFS traversal following only SEQUEL/PREQUEL edges where `type == ANIME`
- Excludes SIDE_STORY, SPIN_OFF, ALTERNATIVE, etc. (treated as separate groups)
- Sorts entries chronologically by `startDate`
- Persists groups to `series_groups` and `series_group_entries` tables
- Caches with 168-hour TTL to avoid redundant traversals
- Returns `(group_id, entries_list)` for downstream use

Used by: P2, P3 (and P1 for episode-to-entry resolution)

### 3.5. Database Layer (`src/Database/`)

**Status**: Fully implemented

- **`Connection.py`**: Async SQLite connection manager via aiosqlite with WAL mode, foreign key enforcement, and full CRUD for all tables
- **`Models.py`**: Dataclass definitions for all tables plus `TABLES` dict (SQL DDL) and `INDEXES` list
- **`Migrations.py`**: 17 versioned migrations (v1–v17), auto-run at startup

Current schema version: **17**

See [Section 10: Data Stores](#10-data-stores) for full table listing.

### 3.6. Web Dashboard (`src/Web/`)

**Status**: Fully implemented (all core pages)

FastAPI application with Jinja2 templates:
- **`App.py`**: Factory pattern (`create_app()`) with lifespan for startup/shutdown; registers all routers
- **Dashboard** (`/`): System status, job triggers, linked accounts
- **Settings** (`/settings`): GUI-based configuration management for all credentials and intervals
- **Auth** (`/auth/anilist/*`): AniList OAuth2 account linking flow
- **Onboarding** (`/onboarding`): 4-step setup wizard for new users
- **Connection Tests** (`/api/test/*`): Live connection validation for all services
- **Floating progress widget**: In `base.html`, polls `GET /api/progress` every 2s for background task feedback

### 3.7. Scheduler (`src/Scheduler/Jobs.py`)

**Status**: Fully implemented

APScheduler integration for periodic background tasks. `JobScheduler` class wraps APScheduler with:
- Cron and interval trigger support via `_cr_trigger()`
- Manual job trigger via `trigger_job(job_id)`
- Job status query via `get_job_status()`
- Registered jobs: Crunchyroll watch sync, Plex/Jellyfin metadata scan, watch sync, download sync

### 3.8. Config (`src/Utils/Config.py`)

**Status**: Fully implemented

Frozen dataclasses loaded once via `load_config()`. Reads from environment variables with fallback to DB-stored GUI settings (`app_settings` table). Supports encrypted secret storage. Config sections: `AniListConfig`, `CrunchyrollConfig`, `PlexConfig`, `JellyfinConfig`, `SonarrConfig`, `RadarrConfig`, `DatabaseConfig`, `SchedulerConfig`, `DownloadSyncConfig`, `AppConfig`.

---

## 4. Pillar 2: File Organization (Priority: 1st)

### 4.1. Overview

Helps users organize their anime files into a standardized folder structure that produces clean 1:1 mappings between Plex/Jellyfin shows and AniList entries. Recommended as the first step for new users.

### 4.2. Implementation Status

| Component | Status |
|-----------|--------|
| `LibraryRestructurer` (`src/Scanner/LibraryRestructurer.py`) | ✅ Complete — all 3 levels |
| Folder rename only (L1) | ✅ Complete |
| Folder + file rename (L2) | ✅ Complete |
| Full restructure with moves (L3) | ✅ Complete |
| Restructure wizard UI (`src/Web/Routes/Restructure.py`) | ✅ Complete — analyze/preview/execute |
| Multi-source restructure with conflict detection | ✅ Complete |
| Restructure templates (wizard, preview, progress, results, report) | ✅ Complete |
| `restructure_log` DB table | ✅ Complete |
| Plex auto-refresh post-execute | ✅ Complete |
| Jellyfin auto-refresh post-execute | ✅ Complete |
| `PlexShowProvider` / `JellyfinShowProvider` | ✅ Complete |

### 4.3. Operation Levels

The Library Restructurer supports three levels of operation, selectable in the wizard:

1. **L1 — Folder rename**: Rename show folders to match AniList titles (no file moves)
2. **L2 — Folder + file rename**: Also rename episode files to standardized format using naming templates
3. **L3 — Full restructure**: Move files between directories, reorganize into series group structure with season subfolders

**Workflow**: Wizard → Select source → Analyze → Preview → Resolve conflicts → Execute → Log → Refresh

### 4.4. Target File Structure

The recommended output structure (Structure A) is one folder per AniList entry:

```
/Anime/
  Demon Slayer Kimetsu no Yaiba/
    Demon Slayer - S01E01.mkv
    ...
  Kimetsu no Yaiba Mugen Train/
    Kimetsu no Yaiba Mugen Train.mkv
  Kimetsu no Yaiba Entertainment District Arc/
    Kimetsu no Yaiba Entertainment District - S01E01.mkv
    ...
```

### 4.5. Routes

- `GET /restructure` — Wizard start page
- `POST /restructure/analyze` — Begin analysis of selected library
- `GET /restructure/progress` — SSE progress stream during analysis
- `GET /restructure/preview` — Show planned file moves for approval
- `POST /restructure/execute` — Execute approved file moves
- `GET /restructure/results` — Show results and any errors
- `GET /restructure/report` — Audit log of past restructures

---

## 5. Pillar 3: Metadata from AniList (Priority: 2nd)

### 5.1. Overview

Writes AniList metadata to Plex and Jellyfin anime libraries. Transforms default metadata (often from TVDB/TMDB) into accurate AniList-sourced information including titles, descriptions, cover art, genres, ratings, and studios.

### 5.2. Implementation Status

| Component | Status |
|-----------|--------|
| `MetadataScanner` (`src/Scanner/MetadataScanner.py`) | ✅ Complete — Plex scan/match/apply |
| `JellyfinMetadataScanner` (`src/Scanner/JellyfinMetadataScanner.py`) | ✅ Complete — Jellyfin scan/match/apply |
| `PlexClient` metadata writing | ✅ Complete — show + season level |
| `JellyfinClient` metadata writing | ✅ Complete — item + season level |
| Plex library browser (`/plex`) | ✅ Complete |
| Jellyfin library browser (`/jellyfin`) | ✅ Complete |
| Plex scan routes (`/scan/plex/*`) | ✅ Complete |
| Jellyfin scan routes (`/scan/jellyfin/*`) | ✅ Complete |
| Unified library browser (`/library`) | ✅ Complete — platform-agnostic view |
| `plex_media` / `jellyfin_media` snapshot tables | ✅ Complete |
| Manual override UI (`/mappings`) | ✅ Complete — list, add, delete |
| Staff/credits writing to Plex | Deferred (non-blocking) |
| GUID-based high-confidence matching | Deferred (non-blocking) |

### 5.3. Metadata Scanner Pipeline

Both `MetadataScanner` (Plex) and `JellyfinMetadataScanner` (Jellyfin) follow the same pipeline:

```
Enumerate shows from server → Match titles (TitleMatcher) → Detect structure (A/B/C)
    → Build series groups → Cache AniList metadata → Write to server
```

**Modes**:
- **Preview/dry-run**: Shows what would change without applying
- **Live scan**: Applies metadata changes in real-time with SSE progress

**Metadata written**:
- Show title (AniList romaji or english)
- Summary/description
- Genres
- Rating (AniList score scaled to platform)
- Cover art/poster (uploaded from AniList CDN URL)
- Season names (each AniList entry's title as the season display name)

### 5.4. Library Routes

**Plex** (`/plex/*`):
- `GET /plex` — Library browser
- `POST /plex/update-match`, `POST /plex/remove-match` — Mapping management
- `POST /plex/scan/preview`, `POST /plex/scan/live` — Scan modes
- `GET /plex/scan/progress`, `GET /plex/scan/results` — Progress/results
- `POST /plex/apply-all`, `POST /plex/apply-single` — Apply metadata

**Jellyfin** (`/jellyfin/*`): Mirrors Plex routes exactly.

**Unified** (`/library/*`): Platform-agnostic library manager for both Plex and Jellyfin items, with series group detail view.

---

## 6. Pillar 1: Watch Status Sync (Priority: 3rd)

### 6.1. Overview

Syncs watch progress between media platforms and AniList. Detects when a user watches an episode and updates their AniList entry with the correct episode count and status.

### 6.2. Implementation Status

| Component | Status |
|-----------|--------|
| `WatchSyncer` (`src/Sync/WatchSyncer.py`) | ✅ Implemented — Crunchyroll→AniList |
| `CrunchyrollClient` | ✅ Implemented — auth + watch history |
| `CrunchyrollPreviewRunner` (`src/Sync/CrunchyrollPreviewRunner.py`) | ✅ Implemented — preview/undo pipeline |
| CR session persistence (`cr_session_cache`) | ✅ Implemented |
| CR sync preview (`cr_sync_preview`, `cr_sync_log`) | ✅ Implemented |
| Sync state tracking (`sync_state`) | ✅ Implemented |
| Scheduler integration | ✅ Implemented |
| Dashboard manual trigger | ✅ Implemented |
| `PlexWatchSyncer` | Not yet implemented |
| `JellyfinWatchSyncer` | Not yet implemented |
| Plex webhook handler | Not yet implemented |
| Jellyfin webhook handler | Not yet implemented |
| AniList backfill syncer | Not yet implemented |
| AniList token auto-refresh | Not yet wired up |

### 6.3. Current: Crunchyroll → AniList

The `WatchSyncer` handles:
- Smart pagination of Crunchyroll watch history with early stopping
- Title matching against AniList (fuzzy match + series group resolution)
- Episode-to-entry resolution using cumulative episode counts
- Automatic status transitions: PLANNING → CURRENT → COMPLETED
- Per-user, per-item sync tracking via `sync_state` table
- Dry-run mode for preview without mutations

**CrunchyrollPreviewRunner** adds a preview/approve/undo layer:
- Proposes changes in `cr_sync_preview` table before applying
- Records applied changes in `cr_sync_log` with undo support
- Routes at `/crunchyroll/*` including history, preview, and undo

**CrunchyrollClient** uses Selenium via `asyncio.to_thread()` for browser-based authentication. Sessions are cached in `cr_session_cache` with 30-day TTL.

### 6.4. Planned: Plex & Jellyfin Watch Sync

- **PlexWatchSyncer**: Poll Plex for watch progress per linked user; per-user tokens via `plex_users` table
- **JellyfinWatchSyncer**: Poll or receive webhooks for watch progress; per-user tokens via `jellyfin_users` table
- Both tables are already in the schema (v10)
- Plex webhooks require Plex Pass; Jellyfin webhooks are free

---

## 7. Pillar 4: Download Management (Priority: 4th)

### 7.1. Overview

Integrates with Sonarr and Radarr to send add/search requests using AniList data. Resolves AniList entries to TVDB/TMDB IDs, routes TV series to Sonarr and movies to Radarr, and pushes AniList alternative titles for better indexer matching.

### 7.2. Implementation Status

| Component | Status |
|-----------|--------|
| `SonarrClient` (`src/Clients/SonarrClient.py`) | ✅ Implemented — full API v3 |
| `RadarrClient` (`src/Clients/RadarrClient.py`) | ✅ Implemented — full API v3 |
| `DownloadManager` (`src/Download/DownloadManager.py`) | ✅ Implemented |
| `MappingResolver` (`src/Download/MappingResolver.py`) | ✅ Implemented |
| `ArrPostProcessor` (`src/Download/ArrPostProcessor.py`) | ✅ Implemented |
| `DownloadSyncer` (`src/Sync/DownloadSyncer.py`) | ✅ Implemented |
| Download management UI (`/downloads`) | ✅ Implemented |
| Manual grab UI (`/manual-grab`) | ✅ Implemented |
| Arr webhook receiver (`/arr-webhook`) | ✅ Implemented |
| Watchlist library view (`/watchlist`) | ✅ Implemented |
| `download_requests` table | ✅ Implemented |
| `anilist_sonarr_mapping` table | ✅ Implemented |
| `anilist_radarr_mapping` table | ✅ Implemented |
| `sonarr_series_cache` / `radarr_movie_cache` tables | ✅ Implemented |
| Full automation (auto-search on new CURRENT status) | Partial — `DownloadSyncer` exists |

### 7.3. Architecture

**ID Resolution** (`src/Utils/NamingTranslator.py`):
1. Fetch AniList external links for the entry
2. Extract TVDB ID (for Sonarr) or TMDB ID (for Radarr)
3. If no ID found, fall back to Sonarr/Radarr title search (`lookup_series` / `lookup_movie`)
4. `is_movie_format()` routes MOVIE/ONA/SPECIAL/MUSIC to Radarr; everything else to Sonarr

**`MappingResolver`** (`src/Download/MappingResolver.py`):
- Persists AniList↔Sonarr/Radarr mappings in `anilist_sonarr_mapping` / `anilist_radarr_mapping`
- Tracks `in_sonarr`/`in_radarr` flag, monitor status, confidence level

**`ArrPostProcessor`** (`src/Download/ArrPostProcessor.py`):
- Handles Sonarr/Radarr webhook events (on-download, on-upgrade)
- Post-processing: file path translation, episode file updates

**`DownloadSyncer`** (`src/Sync/DownloadSyncer.py`):
- Periodic sync: checks AniList watchlist for CURRENT entries not yet in Sonarr/Radarr
- Auto-adds entries based on configured `auto_statuses`

**Environment variables** (P4):
- `SONARR_URL`, `SONARR_API_KEY`
- `RADARR_URL`, `RADARR_API_KEY`

---

## 8. Media Mapping Architecture

This shared architecture underpins Pillars 2 and 3. It solves the structural mismatch between AniList's per-season entries and Plex's show-based organization.

### 8.1. The Structural Mismatch

**AniList**: Every season, part, or cour is a **separate Media entry** with its own unique ID. There is no "parent series" container. Seasons are connected solely through **relation edges** (SEQUEL, PREQUEL, etc.). A show like Demon Slayer is 5+ separate entries chained together, mixing TV seasons and movies in the sequel chain.

**Plex/Jellyfin**: A show is a single entity containing Season folders with episodes numbered per season. "Demon Slayer" would typically be one show with Seasons 1-5.

This creates a many-to-many mapping problem that the series group architecture solves.

### 8.2. Series Groups

A **series group** is the core concept that bridges the two models. It represents a collection of AniList entries that together form one logical "show" from the user's perspective.

**Definition**: A series group contains everything reachable by following only `SEQUEL` and `PREQUEL` relation edges where `type == ANIME`. This includes all formats: TV, MOVIE, OVA, ONA, and SPECIAL.

**Excluded relations**: `SIDE_STORY`, `SPIN_OFF`, `ALTERNATIVE`, `CHARACTER`, `SUMMARY`, `COMPILATION`, `CONTAINS`, `SOURCE`, `ADAPTATION`, and `OTHER` edges are **not** followed.

**Ordering**: Entries within a series group are sorted by `startDate` (chronological).

#### Example: Demon Slayer

```
Series Group: "Demon Slayer: Kimetsu no Yaiba"
  ├── Season 1: Demon Slayer: Kimetsu no Yaiba          (TV, 26 eps, Apr 2019)  → AniList 101922
  ├── Season 2: Kimetsu no Yaiba: Mugen Train            (MOVIE, 1 ep, Oct 2020) → AniList 112151
  ├── Season 3: Kimetsu no Yaiba: Mugen Train Arc        (TV, 7 eps, Oct 2021)   → AniList 129874
  ├── Season 4: Kimetsu no Yaiba: Entertainment District (TV, 11 eps, Dec 2021)  → AniList 142329
  ├── Season 5: Kimetsu no Yaiba: Swordsmith Village     (TV, 11 eps, Apr 2023)  → AniList 145139
  └── Season 6: Kimetsu no Yaiba: Hashira Training       (TV, 8 eps, May 2024)   → AniList 166240
```

### 8.3. Building Series Groups via Relation Traversal

The `SeriesGroupBuilder` (`src/Scanner/SeriesGroupBuilder.py`) implements BFS traversal:

1. **Match one entry** — Fuzzy title match or manual override identifies a single AniList entry
2. **Walk the relation graph** — BFS traversal of SEQUEL/PREQUEL edges using `relationType(version: 2)`
3. **Filter** — Keep only entries where `type == ANIME` (any format)
4. **Sort** — Order by `startDate` (year, month, day) ascending
5. **Assign season numbers** — Sequential from 1 based on chronological position
6. **Cache** — Store in `series_groups` and `series_group_entries` with 168-hour TTL

### 8.4. Plex/Jellyfin File Structure Adaptation

The scanner handles three common file organization patterns:

#### Structure A: Split Folders (One Folder Per AniList Entry)

```
/Anime/
  Demon Slayer Kimetsu no Yaiba/          ← Plex show (rating_key: 1001)
  Demon Slayer Mugen Train/               ← Plex show (rating_key: 1002)
  Demon Slayer Entertainment District/    ← Plex show (rating_key: 1003)
```

- Each server item maps to one AniList entry (1:1 mapping)
- **Recommended structure** for new libraries

#### Structure B: Multi-Season Show (Traditional Plex/TVDB Style)

```
/Anime/
  Demon Slayer (2019)/
    Season 01/  S01E01-E26  (26 eps)
    Season 02/  S02E01      (1 ep — movie)
    Season 03/  S03E01-E11  (11 eps)
```

- One server item with multiple seasons
- Match show title → walk SEQUEL/PREQUEL chain → map seasons positionally

#### Structure C: Absolute Numbering

```
/Anime/
  Demon Slayer/
    Demon Slayer - 001.mkv through Demon Slayer - 057.mkv
```

- Walk series group chain, use cumulative episode counts to determine boundaries

#### Detection Strategy

```
1. Match show to AniList → build series group

2. If series group has only 1 entry:
     → Simple 1:1 mapping (show → entry)

3. If series group has multiple entries:
     a. If server show has multiple seasons:
          → Structure B (map seasons to group entries by position)
     b. If server show has 1 season with episode count > first entry's episodes:
          → Structure C (absolute numbering)
     c. If server show has 1 season with episode count ≈ first entry's episodes:
          → Structure A (this show is just one entry in the group)
```

---

## 9. Project Structure

```
Anilist-Link/
├── src/                                          # Main application source code
│   ├── Clients/                                  # External API client modules
│   │   ├── AnilistClient.py                      # AniList GraphQL + OAuth2 + rate limiter [✅]
│   │   ├── PlexClient.py                         # Plex API client [✅]
│   │   ├── JellyfinClient.py                     # Jellyfin API client [✅]
│   │   ├── CrunchyrollClient.py                  # Crunchyroll reverse-engineered client [✅]
│   │   ├── SonarrClient.py                       # Sonarr API v3 client [✅]
│   │   ├── RadarrClient.py                       # Radarr API v3 client [✅]
│   ├── Matching/                                 # Title matching engine
│   │   ├── TitleMatcher.py                       # Multi-algorithm fuzzy matching [✅]
│   │   └── Normalizer.py                         # Anime-specific title normalization [✅]
│   ├── Scanner/                                  # Scanning, metadata, and file organization
│   │   ├── MetadataScanner.py                    # Plex scan → match → cache → apply [✅]
│   │   ├── JellyfinMetadataScanner.py            # Jellyfin scan → match → cache → apply [✅]
│   │   ├── SeriesGroupBuilder.py                 # BFS relation traversal [✅]
│   │   ├── LibraryRestructurer.py                # File reorganization (L1/L2/L3) [✅]
│   │   ├── LibraryScanner.py                     # Generic library scanner [✅]
│   │   ├── LocalDirectoryScanner.py              # Local filesystem scanner [✅]
│   │   ├── PlexShowProvider.py                   # Fetches Plex shows → ShowInput [✅]
│   │   └── JellyfinShowProvider.py               # Fetches Jellyfin shows → ShowInput [✅]
│   ├── Sync/                                     # Watch status and download synchronization
│   │   ├── WatchSyncer.py                        # Crunchyroll→AniList watch sync [✅]
│   │   ├── CrunchyrollPreviewRunner.py           # CR sync preview/approve/undo pipeline [✅]
│   │   └── DownloadSyncer.py                     # AniList watchlist→Sonarr/Radarr sync [✅]
│   ├── Download/                                 # Download management (P4)
│   │   ├── DownloadManager.py                    # Orchestrates AniList→Sonarr/Radarr [✅]
│   │   ├── MappingResolver.py                    # AniList↔Arr mapping persistence [✅]
│   │   └── ArrPostProcessor.py                   # Sonarr/Radarr webhook post-processing [✅]
│   ├── Web/                                      # FastAPI web dashboard
│   │   ├── App.py                                # Application factory [✅]
│   │   ├── Routes/
│   │   │   ├── Dashboard.py                      # Dashboard and status [✅]
│   │   │   ├── Settings.py                       # GUI configuration [✅]
│   │   │   ├── Auth.py                           # AniList OAuth2 [✅]
│   │   │   ├── Onboarding.py                     # 4-step setup wizard [✅]
│   │   │   ├── ConnectionTest.py                 # Live connection test endpoints [✅]
│   │   │   ├── PlexLibrary.py                    # Plex library browser [✅]
│   │   │   ├── PlexScan.py                       # Plex scan management [✅]
│   │   │   ├── JellyfinLibrary.py                # Jellyfin library browser [✅]
│   │   │   ├── JellyfinScan.py                   # Jellyfin scan management [✅]
│   │   │   ├── Library.py                        # Unified library manager [✅]
│   │   │   ├── UnifiedLibrary.py                 # Unified library view (Plex + Jellyfin) [✅]
│   │   │   ├── Restructure.py                    # File restructure wizard [✅]
│   │   │   ├── Mappings.py                       # Manual override management [✅]
│   │   │   ├── CrunchyrollSync.py                # Crunchyroll sync + history/undo [✅]
│   │   │   ├── Downloads.py                      # Download management UI [✅]
│   │   │   ├── ManualGrab.py                     # Manual release grab UI [✅]
│   │   │   ├── WatchlistLibrary.py               # AniList watchlist browser [✅]
│   │   │   ├── SonarrSync.py                     # Sonarr sync management [✅]
│   │   │   ├── ArrWebhook.py                     # Sonarr/Radarr webhook receiver [✅]
│   │   │   └── Tools.py                          # Admin tools [✅]
│   │   ├── Templates/                            # Jinja2 HTML templates (26 files)
│   │   └── Static/                               # CSS, JS modules, images
│   │       ├── style.css                         # Application stylesheet [✅]
│   │       ├── file-browser.js                   # Shared dual-pane file browser [✅]
│   │       └── naming-templates.js               # Shared naming template presets/preview [✅]
│   ├── Database/                                 # Database layer
│   │   ├── Connection.py                         # Async SQLite connection manager [✅]
│   │   ├── Models.py                             # Table definitions and dataclasses [✅]
│   │   └── Migrations.py                         # Versioned schema migrations (v1–v17) [✅]
│   ├── Scheduler/                                # Background job scheduling
│   │   └── Jobs.py                               # APScheduler job definitions [✅]
│   ├── Utils/                                    # Shared utilities
│   │   ├── Config.py                             # Configuration management [✅]
│   │   ├── Logging.py                            # Logging configuration [✅]
│   │   ├── NamingTemplate.py                     # File/folder naming templates [✅]
│   │   └── NamingTranslator.py                   # ID resolution + title helpers [✅]
│   └── Main.py                                   # Application entry point [✅]
├── tests/
│   ├── conftest.py                               # Shared fixtures (DB, config, mocks)
│   └── Unit/
│       ├── test_arr_post_processor.py            # ArrPostProcessor tests (25+ cases)
│       ├── test_config.py                        # Config loading tests (30 cases)
│       ├── test_database.py                      # DatabaseManager CRUD tests (31 cases)
│       ├── test_naming_template.py               # Quality parsing tests (21 cases)
│       ├── test_normalizer.py                    # Title normalizer tests (68 cases)
│       ├── test_plex_client.py                   # PlexClient helper tests (33 cases)
│       ├── test_rate_limiter.py                  # RateLimiter tests (18 cases)
│       ├── test_series_group_builder.py          # SeriesGroupBuilder tests (22 cases)
│       └── test_title_matcher.py                 # TitleMatcher tests (55 cases)
├── scripts/                                      # Utility scripts (not production code)
├── docs/                                         # All documentation
└── pyproject.toml                                # Python project configuration
```

---

## 10. Data Stores

### 10.1. SQLite Database (Primary)

**Location**: `/config/anilist_link.db` (Docker) or `./anilist_link.db` (local dev)

**Current schema version**: **17**

| Table | Purpose | Added |
|-------|---------|-------|
| `schema_version` | Migration tracking | v1 |
| `media_mappings` | Plex/Jellyfin → AniList mappings with confidence scores | v1 |
| `users` | Linked AniList accounts with OAuth tokens | v1 |
| `sync_state` | Per-user, per-item sync progress tracking | v1 |
| `anilist_cache` | AniList metadata cache (7-day TTL) | v1 |
| `manual_overrides` | User-specified title → AniList ID overrides | v1 |
| `cr_session_cache` | Crunchyroll auth session persistence (30-day TTL) | v2 |
| `app_settings` | GUI-managed configuration (encrypted secrets) | v3 |
| `plex_media` | Persistent Plex library item snapshot | v4 |
| `series_groups` | Series group metadata (root entry, display title) | v5 |
| `series_group_entries` | Individual entries within a series group, ordered | v5 |
| `restructure_log` | File move operation audit trail | v6 |
| `jellyfin_media` | Persistent Jellyfin library item snapshot | v7 |
| `libraries` | Local library definitions (name, paths) | v8 |
| `library_items` | Items in a local library with match data | v8 |
| `plex_users` | Per-user Plex tokens for watch tracking | v10 |
| `jellyfin_users` | Per-user Jellyfin credentials | v10 |
| `cr_sync_preview` | Pending Crunchyroll sync changes awaiting approval | v10 |
| `cr_sync_log` | Applied CR sync changes with undo support | v10 |
| `download_requests` | Sonarr/Radarr add request tracking | v11 |
| `anilist_sonarr_mapping` | AniList↔Sonarr series mappings | v13 |
| `anilist_radarr_mapping` | AniList↔Radarr movie mappings | v13 |
| `sonarr_series_cache` | Cached Sonarr series data (by TVDB ID) | v13 |
| `radarr_movie_cache` | Cached Radarr movie data (by TMDB ID) | v13 |
| `user_watchlist` | Cached AniList watchlist per linked user | v14+ |

### 10.2. In-Memory Cache

Short-lived caching of frequently accessed data during active scan/sync operations. Rate limit state is maintained in the `RateLimiter` instance on the `AniListClient`.

---

## 11. External Integrations

| Service | Client | Purpose | Auth Method | Status |
|---------|--------|---------|-------------|--------|
| AniList GraphQL API | `AnilistClient` | Metadata source, watch status target | OAuth2 | ✅ Implemented |
| Plex Media Server | `PlexClient` | Library enumeration, metadata writing, watch status | X-Plex-Token | ✅ Implemented |
| Crunchyroll | `CrunchyrollClient` | Watch history retrieval | Session-based (Selenium) | ✅ Implemented |
| Jellyfin | `JellyfinClient` | Library access, metadata writing, watch status | API key | ✅ Implemented |
| Sonarr API v3 | `SonarrClient` | Add/search TV series with alt titles | API key | ✅ Implemented |
| Radarr API v3 | `RadarrClient` | Add/search movies with alt titles | API key | ✅ Implemented |
| Plex.tv API | (via `PlexClient`) | Per-user token retrieval | X-Plex-Token | Planned (P1) |

---

## 12. Pillar Interactions

The pillars are designed to build on each other in implementation order:

```
P2 (File Organization)
  │  Organizes files into clean Structure A
  │  → Produces reliable 1:1 show↔AniList mappings
  ▼
P3 (Metadata from AniList)
  │  Uses those mappings to write metadata to Plex/Jellyfin
  │  → Builds series groups, caches AniList data
  ▼
P1 (Watch Status Sync)
  │  Uses series groups + mappings to resolve episodes to AniList entries
  │  → Syncs watch progress per linked user
  ▼
P4 (Download Management)
  │  Uses AniList data to resolve TVDB/TMDB IDs
  │  → Sends add requests to Sonarr/Radarr with alt titles
```

Each pillar can function independently, but the recommended order maximizes data reuse.

---

## 13. Implementation Roadmap

### Completed

- **P2 (File Organization)**: All 3 levels (folder rename, file rename, full restructure). Multi-source, conflict detection, Jellyfin support, restructure log.
- **P3 (Metadata)**: Full scan/match/apply for both Plex and Jellyfin. Unified library browser. Manual overrides.
- **P1 (Crunchyroll)**: WatchSyncer, CrunchyrollPreviewRunner with preview/approve/undo.
- **P4 (Clients)**: SonarrClient, RadarrClient fully implemented.
- **P4 (Manager)**: DownloadManager, MappingResolver, ArrPostProcessor, DownloadSyncer implemented.
- **P4 (UI)**: Downloads page, manual grab, watchlist browser, Sonarr sync, webhook receiver.
- **Infrastructure**: Onboarding wizard, connection test endpoints, floating progress widget.

### In Progress / Next

- **P1 (Plex watch sync)**: PlexWatchSyncer — polling + webhook handler. `plex_users` table exists.
- **P1 (Jellyfin watch sync)**: JellyfinWatchSyncer. `jellyfin_users` table exists.
- **P4 (full automation)**: DownloadSyncer auto-search polish; status feedback loop.

### Future / Deferred

- AniList token auto-refresh
- AniList backfill syncer (AniList → Plex/Jellyfin watched flags)
- GUID-based high-confidence Plex matching
- Staff/credits writing to Plex
- Plex multi-user support (Plex.tv per-user token flow)

---

## 14. Deployment & Infrastructure

**Target**: Self-hosted Docker container (primarily Unraid)

- **Base Image**: `python:3.11-alpine` (multi-stage build)
- **Process Manager**: Supervisord
- **Port**: 9876
- **Volumes**: `/config` (database, logs, config), `/data` (reserved)
- **CI/CD**: GitHub Actions (lint, type check, test, Docker build)

See `docker-compose.yml` and `docs/CLAUDE.md` for full Docker configuration.

---

## 15. Security

- **AniList OAuth2**: Per-user tokens; each user only modifies their own AniList
- **Plex/Jellyfin**: Token/API key authentication
- **Arr services**: API key authentication
- **Dashboard**: Local-only, no built-in auth (relies on network access control)
- **Data**: OAuth tokens stored locally in SQLite; no external data sharing beyond configured platforms
- **Code**: Parameterized queries throughout, input validation, no secrets in committed code

---

## 16. Testing

### Test Suite

315 unit tests across 10 test files. All pass. Run in 0.98s (in-memory SQLite, no external calls).

| File | Tests | Focus |
|------|-------|-------|
| `test_normalizer.py` | 68 | All Normalizer pure functions (100% coverage) |
| `test_title_matcher.py` | 55 | Matching algorithms, season detection, format filtering |
| `test_config.py` | 30 | Config dataclass construction, env var loading |
| `test_database.py` | 31 | DatabaseManager CRUD via in-memory SQLite |
| `test_plex_client.py` | 33 | PlexClient helpers, metadata building, HTML stripping |
| `test_series_group_builder.py` | 22 | BFS traversal, caching, sorting, format filtering |
| `test_naming_template.py` | 21 | Quality parsing from filenames |
| `test_rate_limiter.py` | 18 | Token bucket acquire/refill, high-priority bypass |
| `test_arr_post_processor.py` | 8 | Path translation, folder naming, dry-run |

### Commands

```bash
pytest                          # Run all tests
pytest tests/Unit/              # Unit tests only
pytest --cov=src                # With coverage report
pytest -x                       # Stop on first failure
```

### Quality Tools

```bash
ruff check src/                 # Lint (passes clean)
black --check src/              # Format check (passes clean)
mypy src/                       # Type check (0 errors)
```

---

## 17. Glossary

- **AniList**: Anime/manga tracking platform with a public GraphQL API
- **Series Group**: Collection of AniList entries connected by SEQUEL/PREQUEL relations forming one logical show (Section 8.2)
- **Structure A/B/C**: Three Plex/Jellyfin file organization patterns the scanner adapts to (Section 8.4)
- **Pillar**: One of the four major functional areas of Anilist-Link (Section 1)
- **HAMA**: HTTP AniDB Metadata Agent — community Plex agent using AniDB for anime metadata
- **ASS**: Absolute Series Scanner — community Plex scanner supporting absolute episode numbering
- **AniDB**: Anime database; entries can be cross-referenced to AniList IDs
- **Sonarr**: PVR for Usenet and BitTorrent TV show downloads
- **Radarr**: PVR for Usenet and BitTorrent movie downloads (Sonarr fork)
- **Binhex**: Docker container standardization conventions for volume paths and env vars
- **PUID/PGID**: Process User ID / Process Group ID for Docker file permission management
- **TTL**: Time To Live — duration before cached data expires
- **BFS**: Breadth-First Search — graph traversal algorithm used for relation walking
- **SSE**: Server-Sent Events — used for real-time progress streaming in scan/restructure operations
- **L1/L2/L3**: Library Restructurer operation levels (folder rename / +file rename / full restructure)

---

## 18. Project Identification

**Project Name**: Anilist-Link
**Repository**: https://github.com/Mprice12337/Anilist-Link
**Primary Contact**: Mprice12337
