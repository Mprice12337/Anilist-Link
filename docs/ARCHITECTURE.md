# Architecture Overview

This document defines the architecture of Anilist-Link, organized around its four functional pillars. It serves as the primary reference for understanding the system's design, components, and implementation status. Update this document as the codebase evolves.

**Date of Last Update**: 2026-02-13

---

## 1. Project Vision & The 4 Pillars

Anilist-Link is a self-hosted Docker container that connects AniList with media platforms (Plex, Jellyfin, Crunchyroll) and download managers (Sonarr, Radarr). Rather than being a single-purpose sync tool, it delivers four distinct capabilities:

| # | Pillar | Summary | Priority | Status |
|---|--------|---------|----------|--------|
| 2 | **File Organization** | Rename/reorganize anime files into a standardized folder structure using AniList data | 1st | Partially implemented |
| 3 | **Metadata from AniList** | Write AniList metadata (titles, descriptions, posters, genres, ratings) to Plex/Jellyfin | 2nd | Implemented for Plex |
| 1 | **Watch Status Sync** | Sync watch progress between Crunchyroll/Plex/Jellyfin and AniList | 3rd | Crunchyroll→AniList done |
| 4 | **Download Management** | Send add requests to Sonarr/Radarr with AniList alternative titles for matching | 4th | Not started |

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
  [Browser]        <───> │  │  Management          │  Web UI      │       │           │
                         │  └──────────────────────┤  (FastAPI)   │───────┘           │
                         │                         └──────────────┘                    │
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
- **Public queries**: anime search (by title), fetch by ID, relations traversal with `relationType(version: 2)`
- **Authenticated mutations**: watch status updates (per-user OAuth2 tokens)
- **OAuth2 flow**: authorization URL generation, token exchange, viewer profile fetch
- **Rate limiting**: token-bucket algorithm (90 capacity, 1.5/sec refill rate)
- **Retry logic**: exponential backoff on 429 and 5xx responses

Used by: All 4 pillars

### 3.2. Title Matcher (`src/Matching/TitleMatcher.py`, `src/Matching/Normalizer.py`)

**Status**: Fully implemented

Multi-algorithm fuzzy matching engine:
- Four weighted algorithms via rapidfuzz: ratio, partial ratio, token sort ratio, token set ratio
- Anime-specific normalization: season number stripping, Unicode transliteration, punctuation removal, common prefix/suffix handling
- Multi-pass search with configurable confidence thresholds
- Manual override support (user-specified title→AniList ID mappings)
- `determine_correct_entry_and_episode()` for absolute numbering resolution

Used by: P1, P2, P3

### 3.3. Series Group Builder (`src/Scanner/SeriesGroupBuilder.py`)

**Status**: Fully implemented

Builds series groups by traversing AniList's relation graph:
- BFS traversal following only SEQUEL/PREQUEL edges where `type == ANIME`
- Excludes SIDE_STORY, SPIN_OFF, ALTERNATIVE, etc. (treated as separate groups)
- Sorts entries chronologically by `startDate`
- Persists groups to `series_groups` and `series_group_entries` tables
- Caches with TTL to avoid redundant traversals
- Returns `(group_id, entries_list)` for downstream use

Used by: P2, P3 (and P1 for episode-to-entry resolution)

### 3.4. Database Layer (`src/Database/`)

**Status**: Fully implemented

- **Connection.py**: Async SQLite connection manager via aiosqlite
- **Models.py**: Dataclass definitions for all tables
- **Migrations.py**: 6 versioned migrations (v1–v6), auto-run at startup

Current schema version: **6**

See [Section 10: Data Stores](#10-data-stores) for full table listing.

### 3.5. Web Dashboard (`src/Web/`)

**Status**: Fully implemented (core pages)

FastAPI application with Jinja2 templates:
- **App.py**: Factory pattern (`create_app()`) with lifespan for startup/shutdown
- **Dashboard** (`/`): System status, job triggers, linked accounts
- **Settings** (`/settings`): GUI-based configuration management for all credentials and intervals
- **Auth** (`/auth/anilist/*`): AniList OAuth2 account linking flow

### 3.6. Scheduler (`src/Scheduler/Jobs.py`)

**Status**: Fully implemented

APScheduler integration for periodic background tasks. Jobs are registered as callables during app startup. Currently schedules Crunchyroll watch sync at configurable intervals.

### 3.7. Config (`src/Utils/Config.py`)

**Status**: Fully implemented

Frozen dataclass loaded once via `load_config()`. Reads from environment variables with fallback to DB-stored GUI settings (`app_settings` table). Supports encrypted secret storage.

---

## 4. Pillar 2: File Organization (Priority: 1st)

### 4.1. Overview

Helps users organize their anime files into a standardized folder structure that produces clean 1:1 mappings between Plex shows and AniList entries. This is the **recommended first step** for new users — properly organized files make metadata writing (P3) and watch sync (P1) significantly more reliable.

### 4.2. Implementation Status

| Component | Status |
|-----------|--------|
| LibraryRestructurer (`src/Scanner/LibraryRestructurer.py`) | Implemented — full restructure mode |
| Restructure wizard UI (`src/Web/Routes/Restructure.py`) | Implemented — analyze/preview/execute flow |
| Restructure templates | Implemented — wizard, preview, progress, results pages |
| `restructure_log` DB table | Implemented (migration v6) |
| Rename-only mode | **Not yet implemented** |
| Operation level selection in wizard | **Not yet implemented** |

### 4.3. Current Capabilities (Full Restructure Mode)

The Library Restructurer analyzes a Plex library and generates a plan to reorganize files into Structure A (one folder per AniList entry):

**Workflow**: Wizard → Analyze → Preview → Execute → Log → Plex Refresh

1. **Analyze**: Scan Plex library, match shows to AniList, build series groups
2. **Preview**: Show the user what file moves will happen (source → destination)
3. **Execute**: Perform file moves with season number remapping, update Plex
4. **Log**: Record all operations in `restructure_log` for audit/undo

**Routes**:
- `GET /restructure` — Wizard start page
- `POST /restructure/analyze` — Begin analysis of selected library
- `GET /restructure/progress` — SSE progress stream during analysis
- `GET /restructure/preview` — Show planned file moves for approval
- `POST /restructure/execute` — Execute approved file moves
- `GET /restructure/results` — Show results and any errors

### 4.4. Planned: Rename-Only Mode

A lighter alternative to full restructure that renames folders/files in place without moving them between directories. Three operation levels:

1. **Folder rename**: Rename show folders to match AniList titles
2. **Folder + file rename**: Also rename episode files to standardized format
3. **Full consolidation**: Merge split folders into multi-season structure (current full restructure)

Users will select the operation level in the wizard before analysis begins.

### 4.5. Target File Structure

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

This produces the cleanest 1:1 mapping and avoids the complexity of season-level mapping.

---

## 5. Pillar 3: Metadata from AniList (Priority: 2nd)

### 5.1. Overview

Writes AniList metadata to Plex (and eventually Jellyfin) anime libraries. Transforms Plex's default metadata (often from TVDB/TMDB) into accurate AniList-sourced information including titles, descriptions, cover art, genres, ratings, and studios.

### 5.2. Implementation Status

| Component | Status |
|-----------|--------|
| MetadataScanner (`src/Scanner/MetadataScanner.py`) | Implemented — full scan/match/apply pipeline |
| PlexClient metadata writing (`src/Clients/PlexClient.py`) | Implemented — show + season level |
| Plex library browser (`src/Web/Routes/PlexLibrary.py`) | Implemented — browse, manage mappings |
| Plex scan routes (`src/Web/Routes/PlexScan.py`) | Implemented — preview/live/apply |
| Plex media persistence (`plex_media` table) | Implemented (migration v4) |
| Staff/credits writing | **Not yet implemented** |
| JellyfinClient (`src/Clients/JellyfinClient.py`) | **Stub only** — module docstring only |
| Jellyfin scan routes | **Not yet implemented** |

### 5.3. Metadata Scanner Pipeline

The MetadataScanner orchestrates the full pipeline:

```
Enumerate Plex shows → Match titles (TitleMatcher) → Detect structure (A/B/C)
    → Build series groups → Cache AniList metadata → Write to Plex
```

**Modes**:
- **Preview/dry-run**: Shows what would change without applying
- **Live scan**: Applies metadata changes in real-time with SSE progress

**Metadata written to Plex**:
- Show title (AniList romaji or english title)
- Summary/description
- Genres
- Rating (AniList score → Plex rating scale)
- Cover art/poster
- Season names (each AniList entry's title)

### 5.4. Plex Library Browser

Full-featured web UI for managing the Plex→AniList mapping:

**Routes**:
- `GET /plex` — Browse Plex libraries with mapping status
- `POST /plex/update-match` — Change a show's AniList match
- `POST /plex/remove-match` — Remove a mapping
- `POST /plex/remove-library` — Remove a scanned library
- `POST /plex/scan/preview` — Preview scan for a library
- `POST /plex/scan/live` — Execute live scan
- `GET /plex/scan/progress` — SSE progress stream
- `GET /plex/scan/results` — Scan results page
- `POST /plex/apply-all` — Apply metadata to all matched items
- `POST /plex/apply-single` — Apply metadata to one item
- `GET /api/plex/thumb` — Proxy for Plex thumbnail images

### 5.5. Plex Scan Routes

Additional scan management routes with AniList search integration:

**Routes**:
- `POST /scan/plex/preview` — Preview scan
- `GET /scan/plex/progress` — SSE progress (HTML page)
- `GET /api/scan/plex/progress` — SSE progress (API)
- `GET /scan/plex/results` — Results page
- `POST /scan/plex/apply` — Apply scan results
- `GET /api/scan/plex/search` — AniList title search for manual rematch
- `POST /scan/plex/rematch` — Manually rematch a show

### 5.6. Planned: Staff/Credits Writing

Write staff information (director, writer, voice actors) from AniList to Plex as credits metadata. Requires extending the AniList GraphQL queries to fetch staff data and the PlexClient to write credits.

### 5.7. Planned: Jellyfin Integration

Mirror the Plex metadata pipeline for Jellyfin:
- Implement `JellyfinClient` (currently a stub)
- Add Jellyfin scan and library browser routes
- Jellyfin's open API makes this straightforward — no paid tier required

---

## 6. Pillar 1: Watch Status Sync (Priority: 3rd)

### 6.1. Overview

Syncs watch progress between media platforms and AniList. Detects when a user watches an episode on Plex, Jellyfin, or Crunchyroll, and updates their AniList entry with the correct episode count and status.

### 6.2. Implementation Status

| Component | Status |
|-----------|--------|
| WatchSyncer (`src/Sync/WatchSyncer.py`) | Implemented — Crunchyroll→AniList |
| CrunchyrollClient (`src/Clients/CrunchyrollClient.py`) | Implemented — auth + watch history |
| CR session persistence (`cr_session_cache` table) | Implemented (migration v2) |
| Sync state tracking (`sync_state` table) | Implemented (migration v1) |
| Scheduler integration | Implemented — periodic CR sync |
| Dashboard manual trigger | Implemented — `POST /api/sync` |
| PlexWatchSyncer | **Not yet implemented** |
| JellyfinWatchSyncer | **Not yet implemented** |
| Plex webhook handler | **Not yet implemented** |
| Jellyfin webhook handler | **Not yet implemented** |
| AniList backfill syncer | **Not yet implemented** |
| `plex_users` table | **Not yet implemented** |
| `jellyfin_users` table | **Not yet implemented** |

### 6.3. Current: Crunchyroll → AniList

The WatchSyncer (ported from the original Crunchyroll-Anilist-Sync project) handles:

- Smart pagination of Crunchyroll watch history with early stopping
- Title matching against AniList (fuzzy match + series group resolution)
- Episode-to-entry resolution using cumulative episode counts
- Automatic status transitions: PLANNING → CURRENT → COMPLETED
- Per-user, per-item sync tracking via `sync_state` table
- Dry-run mode for preview without mutations

**CrunchyrollClient** uses Selenium via `asyncio.to_thread()` for browser-based authentication (required due to anti-bot measures). Sessions are cached in `cr_session_cache` with 30-day TTL.

### 6.4. Planned: Plex Watch Sync

- **PlexWatchSyncer**: Poll Plex for watch progress changes per linked user
- **Plex webhook handler**: Real-time sync via Plex webhooks (requires Plex Pass)
- **`plex_users` table**: Store per-user Plex tokens obtained via Plex.tv API (`/api/v2/home/users`)
- Uses existing series group + structure detection to resolve episodes to AniList entries

### 6.5. Planned: Jellyfin Watch Sync

- **JellyfinWatchSyncer**: Poll or receive webhooks for watch progress
- **`jellyfin_users` table**: Store per-user Jellyfin credentials
- Jellyfin webhooks don't require a paid tier (unlike Plex)

### 6.6. Planned: AniList Backfill Syncer

Reverse direction: read AniList watch status and update media server "watched" flags. Useful for marking items as watched in Plex/Jellyfin based on AniList history.

---

## 7. Pillar 4: Download Management (Priority: 4th)

### 7.1. Overview

Integrates with Sonarr and Radarr to send add requests using AniList data. When a user wants to download an anime, Anilist-Link resolves the AniList entry to TVDB/TMDB IDs and sends the request to Sonarr/Radarr with alternative titles from AniList for better matching.

### 7.2. Implementation Status

| Component | Status |
|-----------|--------|
| SonarrClient | **Not yet implemented** |
| RadarrClient | **Not yet implemented** |
| DownloadManager (`src/Downloads/`) | **Not yet implemented** |
| Download management UI routes | **Not yet implemented** |
| `download_requests` table | **Not yet implemented** |

### 7.3. Planned Architecture

**New clients**:
- `src/Clients/SonarrClient.py` — Sonarr API v3 integration (add series, search, status)
- `src/Clients/RadarrClient.py` — Radarr API v3 integration (add movie, search, status)

**New module**:
- `src/Downloads/DownloadManager.py` — Orchestrates AniList→Sonarr/Radarr workflow

**Workflow**:
1. User selects an AniList entry (from search or series group browse)
2. Resolve AniList ID → TVDB/TMDB ID via AniList `externalLinks` field
3. Send add request to Sonarr (TV) or Radarr (movie) with AniList alternative titles
4. Track request status in `download_requests` table
5. Show status in dashboard

**New environment variables**:
- `SONARR_URL` — Sonarr server URL
- `SONARR_API_KEY` — Sonarr API key
- `RADARR_URL` — Radarr server URL
- `RADARR_API_KEY` — Radarr API key

**New DB table**: `download_requests` — Track sent requests with status, timestamps, and AniList/Sonarr/Radarr IDs.

---

## 8. Media Mapping Architecture

This shared architecture underpins Pillars 2 and 3. It solves the structural mismatch between AniList's per-season entries and Plex's show-based organization.

### 8.1. The Structural Mismatch

**AniList**: Every season, part, or cour is a **separate Media entry** with its own unique ID. There is no "parent series" container. Seasons are connected solely through **relation edges** (SEQUEL, PREQUEL, etc.). A show like Demon Slayer is 5+ separate entries chained together, mixing TV seasons and movies in the sequel chain.

**Plex**: A show is a single entity containing Season folders with episodes numbered per season. "Demon Slayer" would typically be one show with Seasons 1-5.

This creates a many-to-many mapping problem that the series group architecture solves.

### 8.2. Series Groups

A **series group** is the core concept that bridges the two models. It represents a collection of AniList entries that together form one logical "show" from the user's perspective.

**Definition**: A series group contains everything reachable by following only `SEQUEL` and `PREQUEL` relation edges where `type == ANIME`. This includes all formats: TV, MOVIE, OVA, ONA, and SPECIAL — if AniList considers it a sequel, it's part of the viewing order.

**Excluded relations**: `SIDE_STORY`, `SPIN_OFF`, `ALTERNATIVE`, `CHARACTER`, `SUMMARY`, `COMPILATION`, `CONTAINS`, `SOURCE`, `ADAPTATION`, and `OTHER` edges are **not** followed. Entries reachable only through these relations are treated as separate series groups.

**Ordering**: Entries within a series group are sorted by `startDate` (chronological). This produces the intended viewing order, naturally interleaving TV seasons, movies, OVAs, and specials.

**Display title**: The series group's display title is derived from the first entry's title (chronologically).

**Season naming**: Each entry within the group uses its own AniList title as its season display name, rather than generic "Season 1", "Season 2" numbering.

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

Note: Both the Mugen Train movie and the Mugen Train TV arc appear as separate seasons since both exist in the SEQUEL chain. Users who have only one version on disk will simply not have files for the other.

#### Duplicate Content Handling

When AniList has both a movie and a TV adaptation of the same arc as separate sequels (e.g., Demon Slayer: Mugen Train), both are included in the series group and sorted chronologically. The system does not attempt to deduplicate — both appear as distinct seasons. Users map whichever version they have on disk.

### 8.3. Building Series Groups via Relation Traversal

The SeriesGroupBuilder (`src/Scanner/SeriesGroupBuilder.py`) implements this process:

1. **Match one entry** — Fuzzy title match, GUID parsing, or manual override identifies a single AniList entry
2. **Walk the relation graph** — BFS traversal of SEQUEL/PREQUEL edges using `relationType(version: 2)` for directional semantics
3. **Filter** — Keep only entries where `type == ANIME` (any format)
4. **Sort** — Order by `startDate` (year, month, day) ascending
5. **Assign season numbers** — Sequential from 1 based on chronological position
6. **Cache** — Store the complete series group in `series_groups` and `series_group_entries` tables with TTL

### 8.4. Plex File Structure Adaptation

The scanner handles three common file organization patterns without requiring the user to restructure their library.

#### Structure A: Split Folders (One Folder Per AniList Entry)

```
/Anime/
  Demon Slayer Kimetsu no Yaiba/          ← Plex show (rating_key: 1001)
  Demon Slayer Mugen Train/               ← Plex show (rating_key: 1002)
  Demon Slayer Entertainment District/    ← Plex show (rating_key: 1003)
```

- Each Plex show is an independent item matched to one AniList entry
- 1:1 mapping — simplest case, works with current `media_mappings` schema
- The scanner can optionally detect these belong to the same series group (for dashboard display) but no season-level mapping is needed
- **This is the recommended structure** for new libraries

#### Structure B: Multi-Season Show (Traditional Plex/TVDB Style)

```
/Anime/
  Demon Slayer (2019)/
    Season 01/  S01E01-E26  (26 eps)
    Season 02/  S02E01      (1 ep — movie)
    Season 03/  S03E01-E11  (11 eps)
```

- One Plex show with multiple seasons, each season corresponding to a different AniList entry
- Match the show title → find one AniList entry → walk the SEQUEL/PREQUEL chain → build series group
- Map Plex season numbers to series group entries positionally: Season 01 → group entry 1, Season 02 → group entry 2, etc.
- Requires **season-level mapping** in the database
- If the user has fewer Plex seasons than group entries: map what exists, leave the rest unmapped
- If the user has more Plex seasons than group entries: extras go unmapped, flagged in dashboard for review

#### Structure C: Absolute Numbering (ASS/HAMA Style)

```
/Anime/
  Demon Slayer/
    Demon Slayer - 001.mkv through Demon Slayer - 057.mkv
```

- Plex sees one show, one season (or no season folders), with absolute episode numbers
- Walk the series group chain, use cumulative episode counts to determine boundaries:
  - Episodes 1-26 → AniList entry 1 (26 eps)
  - Episode 27 → AniList entry 2 (1 ep, movie)
  - Episodes 28-38 → AniList entry 3 (11 eps)
- Reuses the logic from `TitleMatcher.determine_correct_entry_and_episode()`

#### Detection Strategy

The scanner infers which structure is in use rather than requiring user configuration:

```
1. Match show to AniList → build series group

2. If series group has only 1 entry:
     → Simple 1:1 mapping (show → entry)

3. If series group has multiple entries:
     a. If Plex show has multiple seasons:
          → Structure B (map seasons to group entries by position)
     b. If Plex show has 1 season with episode count > first entry's episodes:
          → Structure C (absolute numbering, split by cumulative episode counts)
     c. If Plex show has 1 season with episode count ≈ first entry's episodes:
          → Structure A (this show is just one entry in the group)
```

When the positional mapping in Structure B produces mismatched episode counts, this is flagged for manual review.

### 8.5. Database Schema for Media Mapping

#### `series_groups`
```sql
CREATE TABLE series_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_anilist_id INTEGER NOT NULL,
    display_title TEXT NOT NULL,
    entry_count INTEGER NOT NULL DEFAULT 1,
    last_traversed TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(root_anilist_id)
);
```

#### `series_group_entries`
```sql
CREATE TABLE series_group_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES series_groups(id) ON DELETE CASCADE,
    anilist_id INTEGER NOT NULL,
    season_order INTEGER NOT NULL,
    display_title TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'TV',
    episodes INTEGER,
    start_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(group_id, anilist_id),
    UNIQUE(group_id, season_order)
);
CREATE INDEX idx_sge_anilist_id ON series_group_entries(anilist_id);
```

#### `media_mappings` (extended)
```sql
-- Columns added via migration v5:
ALTER TABLE media_mappings ADD COLUMN series_group_id INTEGER REFERENCES series_groups(id);
ALTER TABLE media_mappings ADD COLUMN season_number INTEGER;
```

For Structure A: `series_group_id` set for dashboard grouping, `season_number` NULL.
For Structure B: Each Plex season gets its own row with `source_id` = `"{rating_key}:S{season_number}"`.
For Structure C: Single row with `series_group_id` set; episode resolution at sync time.

### 8.6. GUID Parsing (Future Enhancement)

Plex items carry GUID metadata that can provide high-confidence mapping without fuzzy title matching:

- **HAMA agent**: `com.plexapp.agents.hama://anidb-4776?lang=en` → extract AniDB ID
- **Plex TV Series agent**: `plex://show/XXXXX` with additional Guid entries like `tvdb://76885` or `tmdb://123456`

Parsing these GUIDs and bridging to AniList IDs (via AniDB→AniList or TVDB→AniList mapping databases) would provide a high-confidence first-pass match before falling back to fuzzy title matching.

---

## 9. Project Structure

```
Anilist-Link/
├── src/                              # Main application source code
│   ├── Clients/                      # External API client modules
│   │   ├── AnilistClient.py          # AniList GraphQL + OAuth2 client [implemented]
│   │   ├── PlexClient.py             # Plex API client [implemented]
│   │   ├── CrunchyrollClient.py      # Crunchyroll reverse-engineered client [implemented]
│   │   ├── JellyfinClient.py         # Jellyfin API client [stub]
│   │   ├── SonarrClient.py           # Sonarr API v3 client [planned — P4]
│   │   └── RadarrClient.py           # Radarr API v3 client [planned — P4]
│   ├── Matching/                     # Title matching engine [implemented]
│   │   ├── TitleMatcher.py           # Multi-algorithm fuzzy matching
│   │   └── Normalizer.py            # Anime-specific title normalization
│   ├── Scanner/                      # Scanning and file organization
│   │   ├── MetadataScanner.py        # Scan → match → cache → apply pipeline [implemented]
│   │   ├── SeriesGroupBuilder.py     # BFS relation traversal [implemented]
│   │   └── LibraryRestructurer.py    # File reorganization tool [implemented]
│   ├── Sync/                         # Watch status synchronization
│   │   └── WatchSyncer.py            # Crunchyroll→AniList sync [implemented]
│   │   # PlexWatchSyncer.py          # [planned — P1]
│   │   # JellyfinWatchSyncer.py      # [planned — P1]
│   ├── Downloads/                    # Download management [planned — P4]
│   │   # DownloadManager.py          # Sonarr/Radarr orchestration
│   ├── Web/                          # FastAPI web dashboard
│   │   ├── App.py                    # Application factory [implemented]
│   │   ├── Routes/
│   │   │   ├── Dashboard.py          # Dashboard and status [implemented]
│   │   │   ├── Settings.py           # GUI configuration [implemented]
│   │   │   ├── Auth.py               # AniList OAuth2 [implemented]
│   │   │   ├── PlexLibrary.py        # Plex library browser [implemented]
│   │   │   ├── PlexScan.py           # Plex scan management [implemented]
│   │   │   ├── Restructure.py        # File restructure wizard [implemented]
│   │   │   └── Mappings.py           # Mapping review/overrides [stub]
│   │   ├── Templates/                # Jinja2 HTML templates
│   │   └── Static/                   # CSS, JS, static assets
│   ├── Database/                     # Database layer [implemented]
│   │   ├── Connection.py             # Async SQLite connection manager
│   │   ├── Models.py                 # Table definitions and dataclasses
│   │   └── Migrations.py            # Versioned schema migrations (v1–v6)
│   ├── Scheduler/                    # Background job scheduling [implemented]
│   │   └── Jobs.py                   # APScheduler job definitions
│   ├── Utils/                        # Shared utilities [implemented]
│   │   ├── Config.py                 # Configuration management
│   │   └── Logging.py               # Logging configuration
│   └── Main.py                       # Application entry point [implemented]
├── tests/
├── scripts/
├── docs/
├── _resources/                       # Development references (NOT in git)
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## 10. Data Stores

### 10.1. SQLite Database (Primary)

**Location**: `/config/anilist_link.db` (Docker) or `./data/anilist_link.db` (local dev)

**Current tables** (schema version 6):

| Table | Purpose | Migration |
|-------|---------|-----------|
| `schema_version` | Migration tracking | v1 |
| `media_mappings` | Plex/Jellyfin → AniList mappings with confidence scores | v1, extended v5 |
| `users` | Linked AniList accounts with OAuth tokens | v1 |
| `sync_state` | Per-user, per-item sync progress tracking | v1 |
| `anilist_cache` | AniList metadata cache (7-day TTL) | v1 |
| `manual_overrides` | User-specified title → AniList ID overrides | v1 |
| `cr_session_cache` | Crunchyroll auth session persistence (30-day TTL) | v2 |
| `app_settings` | GUI-managed configuration (encrypted secrets) | v3 |
| `plex_media` | Persistent Plex library item snapshot | v4 |
| `series_groups` | Series group metadata | v5 |
| `series_group_entries` | Individual entries within a series group | v5 |
| `restructure_log` | File move operation audit trail | v6 |

**Planned tables**:

| Table | Purpose | Pillar |
|-------|---------|--------|
| `plex_users` | Per-user Plex tokens for watch tracking | P1 |
| `jellyfin_users` | Per-user Jellyfin credentials | P1 |
| `download_requests` | Sonarr/Radarr request tracking | P4 |

### 10.2. In-Memory Cache

Short-lived caching of frequently accessed AniList API responses and rate limit state during active scan/sync operations.

---

## 11. External Integrations

| Service | Client | Purpose | Auth Method | Status |
|---------|--------|---------|-------------|--------|
| AniList GraphQL API | `AnilistClient` | Metadata source, watch status target | OAuth2 | Implemented |
| Plex Media Server | `PlexClient` | Library enumeration, metadata writing, watch status | X-Plex-Token | Implemented |
| Crunchyroll | `CrunchyrollClient` | Watch history retrieval | Session-based (Selenium) | Implemented |
| Plex.tv API | (via `PlexClient`) | Per-user token retrieval for multi-user support | X-Plex-Token | Planned (P1) |
| Jellyfin API | `JellyfinClient` | Library access, metadata writing, watch status | API key | Stub only |
| Sonarr API v3 | `SonarrClient` | Add series requests with alt titles | API key | Planned (P4) |
| Radarr API v3 | `RadarrClient` | Add movie requests with alt titles | API key | Planned (P4) |

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
  │  → Sends add requests to Sonarr/Radarr
```

Each pillar can function independently, but the recommended order maximizes data reuse. P2's file organization makes P3's matching more reliable, P3's cached series groups are reused by P1 for episode resolution, and P4 leverages AniList metadata already cached by P3.

---

## 13. Implementation Roadmap

### Current: P2 + P3 Foundation

What's built:
- Full scan/match/apply pipeline for Plex metadata (P3)
- Library restructure wizard with analyze/preview/execute (P2)
- Series group builder with BFS relation traversal
- Crunchyroll→AniList watch sync (P1 partial)
- Web dashboard with settings, library browser, OAuth2

### Next: P2 Enhancements

- Rename-only mode (folder rename without file moves)
- Operation level selection in restructure wizard

### Then: P3 Completion

- Staff/credits writing to Plex
- Jellyfin client implementation
- Jellyfin scan and library browser routes

### Then: P1 Expansion

- Plex watch sync (polling + webhook)
- Jellyfin watch sync
- AniList backfill syncer
- `plex_users` and `jellyfin_users` tables

### Future: P4

- Sonarr/Radarr client implementations
- Download manager module
- Download management UI
- `download_requests` table

---

## 14. Deployment & Infrastructure

**Target**: Self-hosted Docker container (primarily Unraid)

- **Base Image**: `python:3.11-alpine` (multi-stage build)
- **Process Manager**: Supervisord
- **Port**: 9876
- **Volumes**: `/config` (database, logs, config), `/data` (reserved)
- **CI/CD**: GitHub Actions (lint, test, Docker build)

See `docker-compose.yml` and CLAUDE.md for full Docker configuration details.

---

## 15. Security

- **AniList OAuth2**: Per-user tokens, each user only modifies their own AniList
- **Plex/Jellyfin**: Token/API key authentication
- **Dashboard**: Local-only, no built-in auth (relies on network access control)
- **Data**: OAuth tokens stored locally in SQLite, no external data sharing beyond configured platforms
- **Code**: Parameterized queries, input validation, no secrets in committed code

---

## 16. Glossary

- **AniList**: Anime/manga tracking platform with a public GraphQL API
- **Series Group**: Collection of AniList entries connected by SEQUEL/PREQUEL relations forming one logical show (Section 8.2)
- **Structure A/B/C**: Three Plex file organization patterns the scanner adapts to (Section 8.4)
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

---

## 17. Project Identification

**Project Name**: Anilist-Link
**Repository**: https://github.com/Mprice12337/Anilist-Link
**Primary Contact**: Mprice12337
