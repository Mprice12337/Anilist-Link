# Anilist-Link — Project Overview

## Summary

Anilist-Link is a self-hosted Docker container that serves as a centralized bridge between AniList and multiple media platforms. It organizes anime file libraries, writes AniList-sourced metadata to Plex and Jellyfin, syncs watch progress from Crunchyroll to AniList, and integrates with Sonarr/Radarr for download management. The project consolidates and expands the existing [Crunchyroll-Anilist-Sync](https://github.com/Mprice12337/Crunchyroll-Anilist-Sync) Docker container into a unified, multi-platform service.

## The 4 Pillars

| Pillar | Summary | Status |
|---|---|---|
| **P2 — File Organization** | Rename/restructure anime files using AniList series data | ✅ Complete |
| **P3 — Metadata** | Write AniList metadata to Plex and Jellyfin libraries | ✅ Complete |
| **P1 — Watch Sync** | Sync Crunchyroll watch progress to AniList | ✅ Crunchyroll done; Plex/Jellyfin planned |
| **P4 — Downloads** | Add anime to Sonarr/Radarr via AniList alt titles + Prowlarr | 🔧 Partially implemented |

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Anilist-Link Service                           │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │  Metadata   │  │   Watch     │  │   Download   │  │  Web UI   │  │
│  │  Scanner    │  │   Syncer    │  │   Manager    │  │ (FastAPI) │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘  └─────┬─────┘  │
│         │                │                │                 │         │
│  ┌──────┴────────────────┴────────────────┴─────────────────┴──────┐  │
│  │                     Title Matching Engine                        │  │
│  │         (rapidfuzz — multi-algorithm fuzzy matching)             │  │
│  └──────┬──────────────┬──────────────┬──────────────┬─────────────┘  │
│         │              │              │              │                 │
│  ┌──────┴──────┐  ┌────┴───┐  ┌──────┴──────┐  ┌───┴──────────────┐  │
│  │   AniList   │  │  Plex  │  │  Jellyfin   │  │   Crunchyroll    │  │
│  │   Client    │  │ Client │  │   Client    │  │     Client       │  │
│  └─────────────┘  └────────┘  └─────────────┘  └──────────────────┘  │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │  Sonarr  │  │  Radarr  │  │ Prowlarr │  │    qBittorrent       │  │
│  │  Client  │  │  Client  │  │  Client  │  │      Client          │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────┐  ┌──────────────────────┐    │
│  │         SQLite Database (v17)      │  │   Sync Scheduler     │    │
│  │  24 tables — mappings, users,      │  │   (APScheduler)      │    │
│  │  cache, series groups, downloads   │  └──────────────────────┘    │
│  └────────────────────────────────────┘                              │
└──────────────────────────────────────────────────────────────────────┘
```

## Core Components

### AniList Client (`src/Clients/AnilistClient.py`)
GraphQL client for all AniList interactions. Handles public queries (search, fetch by ID, relations traversal) and authenticated mutations (watch status updates). Implements OAuth2 flow for per-user token management. Proactive token-bucket rate limiter (90 req/min, 1.5/sec refill). `relationType(version: 2)` used for SEQUEL/PREQUEL graph traversal.

### Plex Client (`src/Clients/PlexClient.py`)
Library enumeration, metadata writing (title, summary, genres, ratings, posters, studio), and per-user watch status reading via Plex.tv API. Supports both webhook-based and polling-based sync.

### Jellyfin Client (`src/Clients/JellyfinClient.py`)
Full Jellyfin API integration: library access, metadata writing, watch status tracking. Auth via `MediaBrowser Client="AnilistLink", Token="{api_key}"` header.

### Crunchyroll Client (`src/Clients/CrunchyrollClient.py`)
Reverse-engineered auth and watch history retrieval using Selenium/undetected-chromedriver. Session persistence via `cr_session_cache` DB table (30-day TTL). All browser ops run via `asyncio.to_thread()`.

### Sonarr / Radarr Clients (`src/Clients/SonarrClient.py`, `RadarrClient.py`)
Sonarr API v3 and Radarr API v3 integration for series/movie lookup, add requests, and monitoring. P4 download management.

### Prowlarr Client (`src/Clients/ProwlarrClient.py`)
Indexer search across configured Prowlarr indexers for manual grab workflow.

### Title Matching Engine (`src/Matching/`)
rapidfuzz-based multi-algorithm fuzzy matching: ratio, partial ratio, token sort ratio, token set ratio — configurable weights. Anime-specific normalization (season number stripping, Unicode transliteration, punctuation removal, bracket tag removal). Multi-pass search with configurable confidence thresholds. Manual override system for entries that can't be auto-matched.

### Series Group Builder (within `src/Clients/AnilistClient.py`)
BFS traversal of AniList SEQUEL/PREQUEL relation graph to build chronologically-ordered series groups. Caches results to avoid re-traversal. Used by both the Metadata Scanner and Library Restructurer.

### Library Restructurer (`src/Scanner/LibraryRestructurer.py`)
Analyzes and reorganizes anime file libraries into a standardized structure. Supports three operation levels: L1 (folder rename only), L2 (folder + file rename), L3 (full restructure). Wizard UI with analyze → preview → execute flow. Auto-detects three Plex/Jellyfin library structures (Structure A, B, C).

### Metadata Scanner (`src/Scanner/MetadataScanner.py`, `JellyfinMetadataScanner.py`)
Orchestrates the scan → match → cache → apply pipeline for Plex and Jellyfin. Enumerates library items, matches to AniList via the matching engine, builds series groups, caches AniList metadata, writes metadata back to the media server.

### Watch Syncer (`src/Sync/WatchSyncer.py`)
Crunchyroll → AniList watch sync. Fetches paginated watch history, matches episodes to AniList entries, updates per-user AniList status with transitions (PLANNING → CURRENT → COMPLETED). Plex/Jellyfin sync is planned (P1).

### Download Manager (`src/Download/DownloadManager.py`)
Orchestrates AniList → Sonarr/Radarr add requests. Resolves AniList IDs to TVDB/TMDB IDs, sends add requests with AniList alternative titles for better indexer matching. P4.

### Web Dashboard (`src/Web/`)
FastAPI + Jinja2 server-rendered dashboard. Routes for all 4 pillars plus onboarding wizard, settings, connection testing, and unified library view.

## Data Model (SQLite v17)

Key tables:

- **media_mappings** — Maps media server library items to AniList IDs with confidence scores and match method
- **users** — Linked AniList accounts with OAuth tokens
- **sync_state** — Per-user, per-item sync tracking (last synced episode, timestamp, status)
- **anilist_cache** — Cached AniList metadata (7-day TTL)
- **manual_overrides** — User-specified title→AniList overrides (priority over fuzzy matching)
- **series_groups / series_group_entries** — AniList SEQUEL/PREQUEL relation groups
- **restructure_log** — File move operation audit trail
- **cr_session_cache** — Crunchyroll auth session persistence (30-day TTL)
- **app_settings** — GUI-managed configuration
- **plex_media / jellyfin_media** — Persistent library snapshots
- **plex_users / jellyfin_users** — Per-user credentials for P1 watch sync
- **anilist_sonarr_mapping / anilist_radarr_mapping** — P4 download mapping tables
- **user_watchlist** — AniList watchlist snapshot per user

Full schema: see `src/Database/Models.py` and `src/Database/Migrations.py` (v1–v17).

## Known Technical Challenges

### Episode Mapping for Multi-Season Shows
AniList treats each season as a separate entry. The Series Group Builder traverses the SEQUEL/PREQUEL graph to build ordered groups and map season numbers to the correct AniList IDs. Three Plex/Jellyfin library structures (split folders, multi-season, absolute numbering) are auto-detected and handled.

### Crunchyroll Integration
No official public API — uses Selenium with undetected-chromedriver. Session persistence reduces browser launches. Subject to breakage when Crunchyroll changes their frontend.

### AniList Token Lifecycle
OAuth tokens are long-lived but expire. The DB schema includes `refresh_token` and `expires_at` — auto-refresh is not yet wired up (known technical debt).

### Title Variability Across Platforms
Anime titles vary significantly across platforms (romanization, English vs. Romaji, season numbers). The multi-algorithm matching engine handles most cases; edge cases use the manual override system.

## Tech Stack

- **Language:** Python 3.12
- **Web Framework:** FastAPI (async)
- **Database:** SQLite via aiosqlite (async)
- **Fuzzy Matching:** rapidfuzz
- **Scheduling:** APScheduler
- **HTTP Client:** httpx (async)
- **Browser Automation:** Selenium + undetected-chromedriver (Crunchyroll only)
- **Containerization:** Docker / Docker Compose (Binhex conventions)
- **Target Deployment:** Unraid or any Docker host

## Repository

- **Repository:** https://github.com/Mprice12337/Anilist-Link
- **Predecessor project:** [Crunchyroll-Anilist-Sync](https://github.com/Mprice12337/Crunchyroll-Anilist-Sync)
- **Full architecture docs:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

**Last Updated:** 2026-03-19
