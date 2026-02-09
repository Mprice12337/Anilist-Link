# Anilist-Link — Project Overview

## Summary

Anilist-Link is a self-hosted Docker container that serves as a centralized bridge between AniList and multiple media platforms. It syncs watch progress from Crunchyroll, Plex, and Jellyfin to AniList, and acts as an AniList-powered metadata provider for Plex and Jellyfin. The project consolidates and expands the existing [Crunchyroll-Anilist-Sync](https://github.com/Mprice12337/Crunchyroll-Anilist-Sync) Docker container into a unified, multi-platform service.

## Goals

- Sync watch progress from Crunchyroll → AniList (existing functionality, to be merged)
- Sync watch progress from Plex → AniList
- Sync watch progress from Jellyfin → AniList
- Serve as a metadata provider for Plex anime libraries using AniList as the data source (titles, descriptions, cover art, genres, ratings, studios, staff)
- Serve as a metadata provider for Jellyfin anime libraries using AniList as the data source
- Support per-user AniList account linking via OAuth2 (each Plex/Jellyfin user can map to their own AniList account)
- Provide a web-based configuration dashboard for managing connections, mappings, and sync status

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Anilist-Link Service                         │
│                                                                  │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐               │
│  │  Metadata   │   │   Watch    │   │   Web UI   │               │
│  │  Scanner    │   │   Syncer   │   │  (FastAPI)  │               │
│  └──────┬─────┘   └─────┬──────┘   └──────┬─────┘               │
│         │                │                  │                     │
│  ┌──────┴────────────────┴──────────────────┴──────┐             │
│  │              Title Matching Engine               │             │
│  │   (rapidfuzz — multi-algorithm fuzzy matching)   │             │
│  └──────┬────────────┬──────────────┬──────────────┘             │
│         │            │              │                             │
│  ┌──────┴──────┐ ┌───┴────┐ ┌──────┴──────┐ ┌──────────────┐    │
│  │   AniList   │ │  Plex  │ │  Jellyfin   │ │ Crunchyroll  │    │
│  │   Client    │ │ Client │ │   Client    │ │   Client     │    │
│  └─────────────┘ └────────┘ └─────────────┘ └──────────────┘    │
│                                                                  │
│  ┌──────────────────────────────────┐  ┌──────────────────────┐  │
│  │        SQLite Database           │  │   Sync Scheduler     │  │
│  │ (mappings, users, tokens, cache) │  │   (APScheduler)      │  │
│  └──────────────────────────────────┘  └──────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## Core Components

### AniList Client (`clients/anilist.py`)
GraphQL client for all AniList interactions. Handles public queries (search, fetch by ID, relations traversal) and authenticated mutations (watch status updates). Implements OAuth2 flow for per-user token management. No API key is required for public queries; authenticated operations use per-user tokens. AniList enforces a rate limit of 90 requests per minute — the client handles 429 responses reactively and should implement proactive throttling (token bucket / semaphore) for large libraries.

### Plex Client (`clients/plex.py`)
Interfaces with the Plex API for library enumeration, item listing, metadata writing (summary, genres, ratings, posters, studio), and per-user watch status reading. For true per-user tracking with Plex Home, individual user tokens are obtained via the Plex.tv API (`/api/v2/home/users`). Supports both webhook-based real-time sync (requires Plex Pass) and polling-based scheduled sync.

### Jellyfin Client (`clients/jellyfin.py`)
Interfaces with the Jellyfin API for library access, metadata writing, and watch status tracking. Jellyfin's API is fully open and does not require a paid tier for webhook support.

### Crunchyroll Client (`clients/crunchyroll.py`)
Handles authentication and watch history retrieval from Crunchyroll. No official public API exists — this requires reverse-engineering the web/mobile API. Must handle session persistence, token refresh, and rate limiting to avoid detection. This is a merge of the existing Crunchyroll-Anilist-Sync project.

### Title Matching Engine (`matching/`)
Fuzzy matching engine using `rapidfuzz` with four weighted algorithms: ratio, partial ratio, token sort ratio, and token set ratio. Includes anime-specific normalization (season number stripping, Unicode transliteration, punctuation removal). Supports multi-pass search (original title → base title fallback) with configurable confidence thresholds. Manual override capability for entries that can't be auto-matched.

### Metadata Scanner (`scanner/`)
Orchestrates the scan → match → cache → apply pipeline. Scans configured media libraries, matches items to AniList entries via the matching engine, caches AniList data in SQLite, and writes metadata (titles, descriptions, cover art, genres, ratings, studios) back to Plex/Jellyfin via their respective APIs. Tracks all mappings in the database with timestamps for incremental rescans.

### Watch Syncer (`sync/`)
Handles bidirectional-ish watch status synchronization. Detects watch progress from Plex (webhooks or polling), Jellyfin (webhooks or polling), and Crunchyroll (polling), then updates the corresponding AniList entry status per linked user. Automatically determines status transitions: PLANNING → CURRENT → COMPLETED. Tracks sync state per user to avoid redundant API calls.

### Web Dashboard (`web/`)
FastAPI-based single-page web UI for configuration and monitoring. Provides library statistics, linked user management, AniList OAuth2 account linking, item mapping review (with inline search/override for unmatched items), sync status and logs, and manual trigger buttons for scans and syncs.

### Scheduler
APScheduler instance running periodic metadata scans and watch syncs at user-configurable intervals.

## Data Model (SQLite)

Key tables:

- **media_mappings** — Maps media server library items to AniList IDs, with match confidence scores, match method (auto/manual), and cached AniList metadata
- **users** — Plex/Jellyfin users linked to AniList accounts, storing OAuth tokens (access token, refresh token, expiry)
- **sync_state** — Per-user, per-item sync tracking to prevent duplicate API calls (last synced episode, timestamp, status)
- **anilist_cache** — Cached AniList metadata with TTL for reducing API calls during rescans
- **manual_overrides** — User-specified title-to-AniList-ID overrides that take priority over fuzzy matching

## Key Technical Challenges

### Episode Mapping for Multi-Season Shows
AniList treats each season as a separate entry (e.g., "Attack on Titan Season 2" is a different AniList ID from Season 1). Plex and Jellyfin may represent these as a single show with multiple seasons or as separate entries depending on the library configuration. The matching engine supports season-aware searching, but a relations-based lookup that traverses AniList's sequel/prequel graph edges should be implemented to auto-map season numbers to the correct AniList entries.

### Crunchyroll Integration
No official public API — requires reverse-engineering authentication and watch history endpoints. Session management, token refresh, and anti-bot mitigation are ongoing concerns. The existing Crunchyroll-Anilist-Sync project handles the current approach and will be merged in.

### AniList Token Lifecycle
AniList OAuth tokens are long-lived but do expire. The database schema includes refresh_token and expires_at fields — the refresh logic needs to be wired up to handle transparent token renewal.

### Title Variability Across Platforms
Anime titles vary significantly across platforms due to romanization differences, English vs. Japanese vs. Romaji naming, inclusion/exclusion of season numbers, and special characters. The matching engine's multi-algorithm approach with anime-specific normalization handles most cases, but edge cases will always require the manual override system.

## Development Phases

### Phase 1 — Foundation & Crunchyroll Merge
- Set up project structure, Docker configuration, and CI
- Merge existing Crunchyroll-Anilist-Sync functionality
- Implement AniList GraphQL client with OAuth2
- Establish SQLite data model
- Basic configuration via environment variables

### Phase 2 — Plex Integration
- Plex API client (library scan, metadata write, watch status read)
- Title matching engine with fuzzy matching
- Metadata scanner pipeline (scan → match → cache → apply)
- Plex → AniList watch sync (polling-based)
- Plex webhook support for real-time sync

### Phase 3 — Jellyfin Integration
- Jellyfin API client (mirror Plex client capabilities)
- Adapt metadata scanner and watch syncer for Jellyfin
- Jellyfin webhook support

### Phase 4 — Web Dashboard & Multi-User
- FastAPI web UI for configuration and monitoring
- Per-user AniList OAuth2 account linking
- Manual mapping review and override interface
- Sync status dashboard and logs

### Phase 5 — Polish & Advanced Features
- Season-to-AniList-ID relation graph traversal
- AniList token auto-refresh
- Proactive rate limiting (token bucket)
- Notification support (webhook callbacks, Discord, etc.)
- Comprehensive logging and health checks

## Tech Stack

- **Language:** Python 3.11+
- **Web Framework:** FastAPI (async, modern, built-in OpenAPI docs)
- **Database:** SQLite (via aiosqlite for async access)
- **Fuzzy Matching:** rapidfuzz
- **Scheduling:** APScheduler
- **HTTP Client:** httpx (async)
- **Containerization:** Docker / Docker Compose
- **Target Deployment:** Unraid (or any Docker host)

## Docker Deployment

Designed to run as a single container alongside Plex/Jellyfin. Configuration via environment variables and/or a mounted config file. Persistent data (SQLite DB, config) stored in a mounted volume.

```yaml
services:
  anilist-link:
    build: .
    container_name: anilist-link
    ports:
      - "9876:9876"
    environment:
      - PLEX_URL=http://<plex-ip>:32400
      - PLEX_TOKEN=<your-plex-token>
      - JELLYFIN_URL=http://<jellyfin-ip>:8096
      - JELLYFIN_API_KEY=<your-jellyfin-api-key>
      - ANILIST_CLIENT_ID=<your-anilist-oauth-client-id>
      - ANILIST_CLIENT_SECRET=<your-anilist-oauth-client-secret>
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

## Repository

- **Existing codebase to merge:** [Crunchyroll-Anilist-Sync](https://github.com/Mprice12337/Crunchyroll-Anilist-Sync)
- **New repository:** TBD (Anilist-Link)
