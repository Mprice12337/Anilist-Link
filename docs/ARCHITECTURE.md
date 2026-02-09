# Architecture Overview
This document serves as a critical, living reference designed to equip agents with a rapid and comprehensive understanding of the Anilist-Link codebase's architecture, enabling efficient navigation and effective contribution from day one. Update this document as the codebase evolves.

## 1. Project Structure
This section provides a high-level overview of the project's directory and file structure, categorised by architectural layer or major functional area. It is essential for quickly navigating the codebase, locating relevant files, and understanding the overall organization and separation of concerns.

```
Anilist-Link/
├── src/                         # Main application source code
│   ├── Clients/                 # External API client modules
│   │   ├── __init__.py
│   │   ├── AnilistClient.py     # AniList GraphQL + OAuth2 client
│   │   ├── PlexClient.py        # Plex API client
│   │   ├── JellyfinClient.py    # Jellyfin API client
│   │   └── CrunchyrollClient.py # Crunchyroll reverse-engineered API client
│   ├── Matching/                # Title matching engine
│   │   ├── __init__.py
│   │   ├── TitleMatcher.py      # Multi-algorithm fuzzy matching with rapidfuzz
│   │   └── Normalizer.py        # Anime-specific title normalization
│   ├── Scanner/                 # Metadata scanning pipeline
│   │   ├── __init__.py
│   │   └── MetadataScanner.py   # Scan → match → cache → apply pipeline
│   ├── Sync/                    # Watch status synchronization
│   │   ├── __init__.py
│   │   └── WatchSyncer.py       # Bidirectional watch progress sync
│   ├── Web/                     # FastAPI web dashboard
│   │   ├── __init__.py
│   │   ├── App.py               # FastAPI application factory
│   │   ├── Routes/              # API route handlers
│   │   │   ├── __init__.py
│   │   │   ├── Dashboard.py     # Dashboard and stats endpoints
│   │   │   ├── Auth.py          # OAuth2 account linking endpoints
│   │   │   └── Mappings.py      # Mapping review and override endpoints
│   │   ├── Templates/           # Jinja2 HTML templates
│   │   └── Static/              # CSS, JS, and static assets
│   ├── Database/                # Database layer
│   │   ├── __init__.py
│   │   ├── Connection.py        # SQLite/aiosqlite connection management
│   │   ├── Models.py            # Table definitions and data models
│   │   └── Migrations.py        # Schema migration utilities
│   ├── Scheduler/               # Background job scheduling
│   │   ├── __init__.py
│   │   └── Jobs.py              # APScheduler job definitions
│   ├── Utils/                   # Shared utility functions
│   │   ├── __init__.py
│   │   ├── Config.py            # Configuration management (env vars + config file)
│   │   └── Logging.py           # Logging configuration
│   ├── __init__.py
│   └── Main.py                  # Application entry point
├── tests/                       # Test suite
│   ├── Unit/                    # Unit tests
│   │   ├── __init__.py
│   │   ├── TestTitleMatcher.py  # Title matching engine tests
│   │   ├── TestAnilistClient.py # AniList client tests
│   │   └── TestWatchSyncer.py   # Watch syncer tests
│   ├── Integration/             # Integration tests
│   │   ├── __init__.py
│   │   ├── TestApiClients.py    # Client integration tests
│   │   └── TestScannerPipeline.py # Scanner pipeline tests
│   └── __init__.py
├── scripts/                     # Automation and utility scripts
│   └── Setup.sh                 # Initial setup script
├── docs/                        # Project documentation
│   ├── ARCHITECTURE.md          # This document
│   ├── CLAUDE.md                # Claude Code configuration (symlinked to root)
│   ├── DEV-SETUP.md             # Developer setup guide
│   ├── QUICK-REFERENCE.md       # Best practices quick reference
│   └── PROJECT-STRUCTURE.md     # Project organization reference
├── _resources/                  # Development references (NOT in git)
│   ├── Examples/                # API response samples, code templates
│   ├── Research/                # Research docs, comparisons
│   ├── Assets/                  # Design files, mockups, diagrams
│   └── Notes/                   # Development notes, scratchpad
├── .github/                     # GitHub Actions CI/CD configurations
│   └── workflows/
│       └── CI.yml               # Continuous integration pipeline
├── README.md                    # Project overview and quick start
├── CLAUDE.md                    # Symlink → docs/CLAUDE.md
├── .gitignore                   # Git ignore rules (includes _resources/)
├── .dockerignore                # Docker build context exclusions
├── Dockerfile                   # Multi-stage Docker build
├── docker-compose.yml           # Binhex-compliant Docker Compose config
└── pyproject.toml               # Python project configuration and dependencies
```

## 2. High-Level System Diagram
The Anilist-Link service acts as a centralized bridge between AniList and multiple media platforms. Data flows from media servers (Plex, Jellyfin, Crunchyroll) through the matching engine to AniList, and metadata flows from AniList back to the media servers.

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                  Anilist-Link Service                    │
                    │                                                         │
[Plex Server] <──> │  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐    │
                    │  │  Metadata    │  │   Watch     │  │   Web UI     │    │
[Jellyfin    ] <──> │  │  Scanner    │  │   Syncer    │  │  (FastAPI)   │    │ <──> [Browser]
                    │  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘    │
[Crunchyroll ] ──>  │         │                │                │            │
                    │  ┌──────┴────────────────┴────────────────┴──────┐     │
                    │  │           Title Matching Engine                │     │
                    │  │      (rapidfuzz — multi-algorithm fuzzy)       │     │
                    │  └──────────────────────┬────────────────────────┘     │
                    │                         │                              │
                    │  ┌──────────────────────┴──────────────────────┐      │
                    │  │         SQLite Database                      │      │
                    │  │  (mappings, users, tokens, cache, overrides) │      │
                    │  └─────────────────────────────────────────────┘      │
                    │                                                         │
                    │  ┌──────────────────────────────────────────┐          │
                    │  │        Sync Scheduler (APScheduler)       │          │
                    │  └──────────────────────────────────────────┘          │
                    └─────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                                     [AniList GraphQL API]
```

## 3. Core Components

### 3.1. Frontend

Name: Web Dashboard

Description: FastAPI-served single-page web UI for configuration and monitoring. Provides library statistics, linked user management, AniList OAuth2 account linking, item mapping review with inline search/override for unmatched items, sync status and logs, and manual trigger buttons for scans and syncs.

Technologies: FastAPI (Jinja2 templates), HTML/CSS/JS

Deployment: Embedded within the Docker container, served on port 9876

### 3.2. Backend Services

#### 3.2.1. AniList Client

Name: AniList GraphQL Client

Description: Handles all AniList interactions including public queries (search, fetch by ID, relations traversal) and authenticated mutations (watch status updates). Implements OAuth2 flow for per-user token management. Manages proactive rate limiting (token bucket / semaphore) to stay within AniList's 90 requests/minute limit.

Technologies: Python (httpx async), GraphQL

Deployment: Runs within the main Docker container

#### 3.2.2. Plex Client

Name: Plex Media Server Client

Description: Interfaces with the Plex API for library enumeration, item listing, metadata writing (summary, genres, ratings, posters, studio), and per-user watch status reading. Supports both webhook-based real-time sync (requires Plex Pass) and polling-based scheduled sync. Per-user tracking via Plex.tv API (`/api/v2/home/users`).

Technologies: Python (httpx async)

Deployment: Runs within the main Docker container

#### 3.2.3. Jellyfin Client

Name: Jellyfin Media Server Client

Description: Interfaces with the Jellyfin API for library access, metadata writing, and watch status tracking. Jellyfin's API is fully open and does not require a paid tier for webhook support.

Technologies: Python (httpx async)

Deployment: Runs within the main Docker container

#### 3.2.4. Crunchyroll Client

Name: Crunchyroll Watch History Client

Description: Handles authentication and watch history retrieval from Crunchyroll via reverse-engineered web/mobile API. Manages session persistence, token refresh, and rate limiting. Merged from the existing Crunchyroll-Anilist-Sync project.

Technologies: Python (httpx async)

Deployment: Runs within the main Docker container

#### 3.2.5. Title Matching Engine

Name: Fuzzy Title Matching Engine

Description: Multi-algorithm fuzzy matching engine using rapidfuzz with four weighted algorithms: ratio, partial ratio, token sort ratio, and token set ratio. Includes anime-specific normalization (season number stripping, Unicode transliteration, punctuation removal). Supports multi-pass search with configurable confidence thresholds and manual override capability.

Technologies: Python (rapidfuzz)

Deployment: Runs within the main Docker container

#### 3.2.6. Metadata Scanner

Name: Library Metadata Scanner

Description: Orchestrates the scan → match → cache → apply pipeline. Scans configured media libraries, matches items to AniList entries via the matching engine, caches AniList data in SQLite, and writes metadata back to Plex/Jellyfin. Tracks all mappings with timestamps for incremental rescans.

Technologies: Python (async)

Deployment: Runs within the main Docker container

#### 3.2.7. Watch Syncer

Name: Watch Status Synchronizer

Description: Handles bidirectional watch status synchronization. Detects watch progress from Plex (webhooks or polling), Jellyfin (webhooks or polling), and Crunchyroll (polling), then updates the corresponding AniList entry per linked user. Automatically determines status transitions: PLANNING → CURRENT → COMPLETED.

Technologies: Python (async)

Deployment: Runs within the main Docker container

## 4. Data Stores

### 4.1. Primary Application Database

Name: Anilist-Link SQLite Database

Type: SQLite (via aiosqlite for async access)

Purpose: Stores all application state including media-to-AniList mappings, user account links, OAuth tokens, sync state tracking, cached AniList metadata, and manual override entries.

Key Schemas/Collections:
- `media_mappings` — Maps media server library items to AniList IDs with confidence scores, match method (auto/manual), and cached metadata
- `users` — Plex/Jellyfin users linked to AniList accounts with OAuth tokens (access token, refresh token, expiry)
- `sync_state` — Per-user, per-item sync tracking (last synced episode, timestamp, status)
- `anilist_cache` — Cached AniList metadata with TTL for reducing API calls during rescans
- `manual_overrides` — User-specified title-to-AniList-ID overrides that override fuzzy matching

### 4.2. In-Memory Cache

Name: Application-level Cache

Type: In-memory (Python dict / TTL-based)

Purpose: Short-lived caching of frequently accessed AniList API responses and rate limit state to reduce external API calls during active scan/sync operations.

## 5. External Integrations / APIs

AniList GraphQL API:
- Purpose: Primary metadata source and watch status target. Used for anime search, metadata retrieval, relations traversal, and authenticated watch status updates.
- Integration Method: GraphQL over HTTPS, OAuth2 for user authentication

Plex Media Server API:
- Purpose: Library enumeration, metadata writing, watch status reading, webhook reception for real-time sync.
- Integration Method: REST API with X-Plex-Token authentication

Jellyfin API:
- Purpose: Library access, metadata writing, watch status tracking, webhook reception.
- Integration Method: REST API with API key authentication

Crunchyroll API (Reverse-Engineered):
- Purpose: Watch history retrieval for syncing to AniList.
- Integration Method: Reverse-engineered REST API with session-based authentication

Plex.tv API:
- Purpose: Obtaining per-user tokens for Plex Home multi-user support.
- Integration Method: REST API (`/api/v2/home/users`)

## 6. Deployment & Infrastructure

Cloud Provider: Self-hosted (Docker on any host, primarily targeting Unraid)

Key Services Used: Docker, Docker Compose, SQLite (file-based, no external DB service required)

CI/CD Pipeline: GitHub Actions (lint, test, build Docker image)

Monitoring & Logging: Python logging module with configurable levels, supervisord process logging to `/config/supervisord.log`

## 7. Security Considerations

Authentication:
- AniList OAuth2 for per-user account linking
- Plex token-based authentication (X-Plex-Token)
- Jellyfin API key authentication
- Crunchyroll session-based authentication with token refresh

Authorization:
- Per-user AniList token scoping (each user's token only modifies their own AniList)
- Admin-level access for web dashboard configuration

Data Encryption:
- OAuth tokens stored in SQLite (local file, encrypted at rest via host filesystem if configured)
- All external API communication over HTTPS/TLS

Key Security Tools/Practices:
- No secrets in environment variable defaults or committed code
- Token refresh logic for expired OAuth credentials
- Rate limiting to prevent API abuse / bans
- Input validation on all user-provided configuration

## 8. Development & Testing Environment

Local Setup Instructions: See [DEV-SETUP.md](DEV-SETUP.md) for detailed steps

Testing Frameworks:
- Unit Tests: pytest
- Integration Tests: pytest with httpx test client
- Async Testing: pytest-asyncio

Code Quality Tools:
- Linter: Ruff
- Formatter: Black
- Type Checking: mypy
- Pre-commit Hooks: pre-commit framework

## 9. Future Considerations / Roadmap

- Season-to-AniList-ID relation graph traversal for automatic multi-season mapping
- AniList token auto-refresh for transparent renewal of expired OAuth tokens
- Proactive rate limiting via token bucket algorithm for large library scans
- Notification support (webhook callbacks, Discord integration)
- Comprehensive health check endpoint for container monitoring
- Potential migration from SQLite to PostgreSQL for multi-instance deployments

## 10. Project Identification

Project Name: Anilist-Link

Repository URL: https://github.com/Mprice12337/Anilist-Link

Primary Contact/Team: Mprice12337

Date of Last Update: 2026-02-09

## 11. Glossary / Acronyms

- **AniList**: Anime/manga tracking and social platform with a public GraphQL API
- **GraphQL**: Query language for APIs; used by AniList for all data operations
- **OAuth2**: Authorization framework used for per-user AniList account linking
- **rapidfuzz**: High-performance fuzzy string matching library for Python
- **APScheduler**: Advanced Python Scheduler for running periodic background jobs
- **aiosqlite**: Async wrapper for Python's sqlite3 module
- **httpx**: Modern async-capable HTTP client for Python
- **FastAPI**: Modern async Python web framework with automatic OpenAPI docs
- **Binhex**: Docker container standardization conventions for volume paths and env vars
- **PUID/PGID**: Process User ID / Process Group ID for Docker file permission management
- **TTL**: Time To Live — duration before cached data expires
- **Webhook**: HTTP callback for real-time event notification from media servers
