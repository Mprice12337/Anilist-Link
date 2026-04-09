# CLAUDE.md - Anilist-Link

> **Purpose**: This file serves as your project's memory for Claude Code. It defines rules, workflows, and preferences that Claude will automatically follow when working on your codebase.

## Project Overview

**Anilist-Link** is a self-hosted Docker container that connects AniList with media platforms (Plex, Jellyfin, Crunchyroll) and download managers (Sonarr, Radarr). It delivers four distinct functional pillars, each addressing a different aspect of anime library management. The project consolidates and expands the existing Crunchyroll-Anilist-Sync container into a unified, multi-platform service.

### The 4 Pillars

| # | Pillar | Summary | Priority |
|---|--------|---------|----------|
| 2 | **File Organization** | Rename/reorganize anime files into standardized structure using AniList data | 1st |
| 3 | **Metadata from AniList** | Write AniList metadata (titles, descriptions, posters, genres, ratings) to Plex/Jellyfin | 2nd |
| 1 | **Watch Status Sync** | Sync watch progress between Crunchyroll/Plex/Jellyfin and AniList | 3rd |
| 4 | **Download Management** | Send add requests to Sonarr/Radarr with AniList alternative titles | 4th |

Implementation order: **P2 → P3 → P1 → P4**

### Key Features
- **P2 — File Organization**: Library restructure wizard (analyze → preview → execute), series group-aware file renaming
- **P3 — Metadata**: Full scan/match/apply pipeline for Plex, library browser with mapping management, AniList metadata writing (titles, summaries, posters, genres, ratings)
- **P1 — Watch Sync**: Crunchyroll→AniList sync with smart pagination and status transitions (Plex/Jellyfin sync planned)
- **P4 — Downloads**: Sonarr/Radarr integration with add requests, alt titles, post-download file organization, series path sync, and webhook automation
- **Shared**: AniList OAuth2 account linking, series group builder, fuzzy title matching, web dashboard with GUI settings

### Project Context
- **Stage**: Pillar-based development — P2 and P3 partially implemented, P1 Crunchyroll sync done, P4 planned
- **Team Size**: Solo
- **Priority Focus**: Functionality first, then polish

---

## Claude Code Preferences

### Workflow Mode
- **Default Model**: Sonnet for daily work / Opus for complex planning and architecture
- **Planning Strategy**: Plan for complex tasks only (multi-file changes, new components)
- **Testing Approach**: Write tests after implementation, aim for coverage on core logic
- **Auto-Accept**: Disabled (review changes before applying)

### Communication Style
- **Verbosity**: Concise — brief explanations unless asked for detail
- **Progress Updates**: Yes, keep me informed of progress on multi-step tasks
- **Error Handling**: Explain the issue then fix it

### Task Management
- **To-Do Lists**: Auto-generate for multi-step tasks
- **Subagents**: Use for exploration and parallel work
- **Research**: Proactive web search when needed for API documentation or library usage

---

## Technology Stack

### Backend
- **Language**: Python 3.11+
- **Framework**: FastAPI (async, modern, built-in OpenAPI docs)
- **Database**: SQLite (via aiosqlite for async access)
- **Background Jobs**: APScheduler (periodic metadata scans and watch syncs)
- **Authentication**: AniList OAuth2 for per-user account linking
- **HTTP Client**: httpx (async)
- **Fuzzy Matching**: rapidfuzz

### Frontend
- **Framework/Library**: FastAPI with Jinja2 templates (server-rendered)
- **CSS Framework**: Minimal/custom CSS (single-page dashboard)
- **State Management**: N/A (server-rendered pages)

### Infrastructure
- **Containerization**: Docker, Docker Compose
- **CI/CD**: GitHub Actions
- **Hosting**: Self-hosted (Unraid or any Docker host)

### Key Dependencies
> Packages Claude should be aware of with brief descriptions
- `fastapi` - Async web framework for the dashboard and API endpoints
- `uvicorn` - ASGI server to run the FastAPI application
- `httpx` - Async HTTP client for all external API calls (AniList, Plex, Jellyfin, Crunchyroll)
- `aiosqlite` - Async SQLite driver for non-blocking database access
- `rapidfuzz` - High-performance fuzzy string matching for title matching engine
- `apscheduler` - Background job scheduling for periodic scans and syncs
- `jinja2` - Template engine for the web dashboard
- `python-multipart` - Required by FastAPI for form data handling

---

## Project Structure

```
├── _resources/             # Reference files for development (NOT in git)
│   ├── Examples/           # API response samples, code templates
│   ├── Research/           # Research documents, comparisons
│   ├── Assets/             # Design files, mockups, diagrams
│   └── Notes/              # Development notes, ideas, scratchpad
├── docs/                   # Project documentation
│   ├── ARCHITECTURE.md     # System architecture and design decisions
│   ├── CLAUDE.md           # Claude Code configuration (symlinked to root)
│   ├── DEV-SETUP.md        # Developer setup guide
│   ├── QUICK-REFERENCE.md  # Best practices quick reference
│   └── PROJECT-STRUCTURE.md # Project structure reference
├── src/                    # Main application source code
│   ├── Clients/            # External API client modules
│   ├── Matching/           # Fuzzy title matching engine
│   ├── Scanner/            # Metadata scanning pipeline
│   ├── Sync/               # Watch status synchronization
│   ├── Web/                # FastAPI web dashboard
│   ├── Database/           # SQLite database layer
│   ├── Scheduler/          # APScheduler job definitions
│   ├── Utils/              # Shared utilities (config, logging)
│   └── Main.py             # Application entry point
├── tests/                  # Test suite
│   ├── Unit/               # Unit tests
│   └── Integration/        # Integration tests
├── scripts/                # Automation scripts
├── README.md               # Main project documentation (root level only)
├── CLAUDE.md               # Symlink to docs/CLAUDE.md
└── .gitignore              # Must include _resources/
```

### Special Directories

#### `_resources/` (Not in Git)
**Purpose**: Development reference materials for both human developers and AI assistants

**Contains**:
- Example code snippets and templates
- API response samples for testing (AniList GraphQL responses, Plex/Jellyfin API responses)
- Design mockups and diagrams
- Research documents on reverse-engineered APIs (Crunchyroll)
- Any reference material that helps development but shouldn't be in version control

**Important**:
- This folder is **NEVER committed to git**
- Add `_resources/` to `.gitignore`
- Developers and Claude can freely add/reference files here
- Perfect for storing AniList API response examples, Plex metadata samples, etc.

#### `docs/` (Documentation Repository)
**Purpose**: All project documentation except README.md

**Required Files**:
- **`ARCHITECTURE.md`**: System architecture, design patterns, technical decisions
- **`CLAUDE.md`**: Claude Code configuration (symlinked to root for auto-detection)
- **`DEV-SETUP.md`**: Developer environment setup procedures
- **`QUICK-REFERENCE.md`**: Best practices and common commands

### Key Directories
- **`src/Clients/`**: All external API client modules (AniList, Plex, Jellyfin, Crunchyroll)
- **`src/Matching/`**: Title matching engine with fuzzy algorithms and normalization
- **`src/Scanner/`**: Metadata scanning pipeline (scan → match → cache → apply)
- **`src/Sync/`**: Watch status synchronization from media platforms to AniList
- **`src/Web/`**: FastAPI dashboard with routes, templates, and static assets
- **`src/Database/`**: SQLite connection management, models, and migrations

---

## Documentation Organization

### File Structure
All documentation files (except `README.md`) should be in the `/docs` folder:

```
├── README.md                    # Root level - main project overview
├── CLAUDE.md                    # Symlink to docs/CLAUDE.md
├── _resources/                  # NOT in git - development references
└── docs/
    ├── ARCHITECTURE.md          # Required - system architecture
    ├── CLAUDE.md                # Actual file location
    ├── DEV-SETUP.md             # Developer setup guide
    ├── QUICK-REFERENCE.md       # Best practices quick reference
    └── PROJECT-STRUCTURE.md     # Project structure reference
```

### Documentation Best Practices

1. **Keep README.md concise** - Link to detailed docs in `/docs`
2. **Update ARCHITECTURE.md** when making significant design changes
3. **Document decisions** - Explain *why*, not just *what*
4. **Include diagrams** - Visual representations in ARCHITECTURE.md
5. **Version documentation** - Keep docs in sync with code changes
6. **Use consistent formatting** - Follow project markdown standards

### Using `_resources/` for Documentation Development

Store in `_resources/` (not git):
- Draft documentation
- Research notes for docs
- API response examples for reference
- Crunchyroll API reverse-engineering notes

Move to `/docs` when:
- Documentation is complete and reviewed
- Content is stable and accurate
- Ready for team consumption

---

## Core Architecture

### Primary Models/Components
- **AniList Client**: GraphQL client with OAuth2 flow, rate limiting (90 req/min), public queries, and authenticated mutations [implemented]
- **Plex Client**: Library enumeration, metadata writing, per-user watch tracking via Plex.tv API [implemented]
- **Jellyfin Client**: Library access, metadata writing, watch status tracking via open API [implemented]
- **Crunchyroll Client**: Reverse-engineered auth + watch history retrieval with session persistence [implemented]
- **Sonarr Client**: Sonarr API v3 integration for series add/lookup [implemented]
- **Radarr Client**: Radarr API v3 integration for movie add/lookup [implemented]
- **Title Matching Engine**: rapidfuzz-based multi-algorithm fuzzy matching with anime-specific normalization [implemented]
- **Metadata Scanner**: Orchestrates scan → match → cache → apply pipeline across Plex libraries [implemented]
- **Jellyfin Metadata Scanner**: Parallel scanner for Jellyfin libraries [implemented]
- **Series Group Builder**: BFS traversal of AniList SEQUEL/PREQUEL graph to build series groups [implemented]
- **Library Restructurer**: Analyzes and reorganizes anime files into Structure A [implemented]
- **Watch Syncer**: Crunchyroll→AniList watch sync with status transitions (PLANNING → CURRENT → COMPLETED) [implemented]
- **Crunchyroll Preview Runner**: Preview/approve/undo pipeline for CR sync [implemented]
- **Download Manager**: Orchestrates AniList→Sonarr/Radarr add requests [implemented]
- **Download Syncer**: Periodic AniList watchlist → Sonarr/Radarr auto-add [implemented]
- **Plex Watch Syncer**: Plex↔AniList bidirectional watch sync with COMPLETED guard, circular sync fix, and undo log [implemented]
- **Jellyfin Watch Syncer**: Jellyfin↔AniList bidirectional watch sync (same feature set as Plex) [implemented]
- **Onboarding Wizard**: 4-step first-run setup wizard with service configuration [implemented]
- **Floating Progress Widget**: In-page background task monitor polling `/api/progress` [implemented]

### Design Patterns Used
- **Pipeline Pattern**: Metadata Scanner uses scan → match → cache → apply pipeline
- **Strategy Pattern**: Multiple fuzzy matching algorithms (ratio, partial ratio, token sort, token set) with configurable weights
- **Observer Pattern**: Webhook handlers for real-time sync from Plex/Jellyfin
- **Repository Pattern**: Database layer abstracts SQLite operations behind clean interfaces

### Data Flow (Per Pillar)
- **P2 (File Organization)**: User selects Plex library → Restructurer analyzes shows → matches to AniList → builds series groups → generates move plan → user previews → executes file moves → triggers Plex refresh
- **P3 (Metadata)**: Scanner enumerates Plex shows → Title Matcher finds AniList entries → Series Group Builder walks relation graph → AniList metadata cached → metadata written to Plex (show + season level)
- **P1 (Watch Sync)**: Scheduler triggers periodic sync → Crunchyroll watch history fetched → episodes matched to AniList entries → status updated per linked user (Plex/Jellyfin polling + webhooks planned)
- **P4 (Downloads)**: User selects AniList entry → resolve to TVDB/TMDB IDs via NamingTranslator → MappingResolver persists AniList↔Arr mappings → DownloadManager sends add request to Sonarr/Radarr with alt titles

See `ARCHITECTURE.md` for detailed per-pillar architecture.

### Media Mapping Model
- **Series Group**: Collection of AniList entries linked by SEQUEL/PREQUEL relations, sorted chronologically. Represents one logical "show."
- **Season Mapping**: Each entry in a series group maps to a Plex season, using the entry's AniList title as the season display name.
- **Structure Adaptation**: Scanner auto-detects three Plex file structures (split folders, multi-season, absolute numbering) and maps accordingly. See `ARCHITECTURE.md` Section 8 for details.

---

## Development Workflow

### Git Strategy
- **Main Branch**: `main` (protected, production-ready)
- **Branch Naming**: `feature/*`, `bugfix/*`, `hotfix/*`
- **Commit Convention**: Descriptive imperative messages (e.g., "Add Plex webhook handler")

#### Git Best Practices
- **Always use `git pull --rebase`** (or alias `git pr`) to maintain linear history
- Avoid merge commits when syncing with remote
- If rebase conflicts occur: use `git rebase --abort` to undo, then resolve conflicts
- Keep commits atomic and well-described

### Code Review Process
- Solo project — self-review before merging
- CI checks must pass (lint, test, type check)

---

## Testing Strategy

### Test Framework
- **Unit Tests**: pytest
- **Integration Tests**: pytest with httpx test client
- **Async Testing**: pytest-asyncio
- **Test Coverage Goal**: 70% minimum on core logic (Matching, Sync, Scanner)

### Testing Commands
```bash
pytest                           # Run all tests
pytest tests/Unit/               # Run unit tests only
pytest tests/Integration/        # Run integration tests only
pytest --cov=src                 # Run with coverage report
pytest -x                        # Stop on first failure
```

### Testing Preferences
- **TDD**: Optional — write tests after for new features
- **Test Generation**: Collaborative — Claude writes tests, developer reviews
- **Coverage Requirements**: Core matching and sync logic must have tests

---

## Code Quality Standards

### Linting & Formatting
- **Linter**: Ruff
- **Formatter**: Black
- **Type Checker**: mypy
- **Pre-commit Hooks**: Yes (pre-commit framework)

### Commands
```bash
ruff check src/                  # Run linter
ruff check --fix src/            # Auto-fix linting issues
black src/                       # Format all files
mypy src/                        # Type check
```

### Style Guidelines
- **Indentation**: 4 spaces (Python standard)
- **Line Length**: 88 characters (Black default)
- **Naming Conventions**:
  - Files & Directories: `PascalCase` (e.g., `AnilistClient.py`, `TitleMatcher.py`)
  - Variables: `snake_case` (Python standard, e.g., `user_data`, `config_options`)
  - Functions: `snake_case` (Python standard, e.g., `get_user_by_id`, `process_payment`)
  - Classes: `PascalCase` (e.g., `AnilistClient`, `TitleMatcher`)
  - Constants: `UPPER_SNAKE_CASE` (e.g., `MAX_RETRY_COUNT`, `API_BASE_URL`)
  - Environment Variables: `UPPER_SNAKE_CASE` (e.g., `PUID`, `PGID`, `PLEX_URL`)
- **File Naming**: `PascalCase` (e.g., `AnilistClient.py`, `MetadataScanner.py`)

**Note**: While the template standard specifies PascalCase for variables and functions, this project follows Python PEP 8 conventions for variables (`snake_case`) and functions (`snake_case`), as this is the universal Python standard. PascalCase is used for file names, directory names, and class names per the template standard.

---

## Environment Setup

### Docker Volume Paths
> Following Binhex standardization - all configuration, data, and media use consistent paths

**Container Paths** (these are fixed in the container):
- `/config` - Application configuration, SQLite database, and logs
- `/data` - Application data (not heavily used for this project)

**Host Paths** (customize these for your system):
```bash
# Example mappings
/mnt/user/appdata/AnilistLink:/config    # Configuration, database, logs
/mnt/user/data:/data                      # Application data
```

### Required Environment Variables
Standard Binhex environment variables (set these in Docker Compose or docker run):
- `PUID` - User ID for file ownership (e.g., `1000`)
- `PGID` - Group ID for file ownership (e.g., `1000`)
- `UMASK` - File permission mask (recommended: `002`)
- `TZ` - Timezone (e.g., `America/New_York`)

Application-specific variables:
- `PLEX_URL` - Plex server URL (e.g., `http://192.168.1.100:32400`)
- `PLEX_TOKEN` - Plex authentication token
- `JELLYFIN_URL` - Jellyfin server URL (e.g., `http://192.168.1.100:8096`)
- `JELLYFIN_API_KEY` - Jellyfin API key
- `ANILIST_CLIENT_ID` - AniList OAuth2 application client ID
- `ANILIST_CLIENT_SECRET` - AniList OAuth2 application client secret
- `SONARR_URL` - Sonarr server URL (e.g., `http://192.168.1.100:8989`) [P4]
- `SONARR_API_KEY` - Sonarr API key [P4]
- `RADARR_URL` - Radarr server URL (e.g., `http://192.168.1.100:7878`) [P4]
- `RADARR_API_KEY` - Radarr API key [P4]
- `WATCHLIST_REFRESH_INTERVAL` - Minutes between scheduled AniList watchlist refreshes (default: `30`)

---

## Database

### Schema Overview (v4)
Current tables:
- `media_mappings` - Maps media server library items to AniList IDs with confidence scores, match method, and optional series group reference
- `users` - Linked AniList accounts with OAuth tokens
- `sync_state` - Per-user, per-item sync tracking (last synced episode, timestamp, status)
- `anilist_cache` - Cached AniList metadata with 7-day TTL
- `manual_overrides` - User-specified title-to-AniList-ID overrides
- `cr_session_cache` - Crunchyroll auth session persistence (30-day TTL)
- `app_settings` - GUI-managed configuration (encrypted secrets in DB)
- `plex_media` - Persistent Plex library item snapshot
- `series_groups` - Groups of AniList entries connected by SEQUEL/PREQUEL relations
- `series_group_entries` - Individual entries within a series group, ordered chronologically
- `restructure_log` - File move operation audit trail
- `restructure_plans` - Saved restructure plans with summary and status (v3)
- `jellyfin_media` - Persistent Jellyfin library item snapshot
- `libraries` - Local library definitions (name, paths)
- `library_items` - Items in a local library with match data
- `plex_users` - Per-user Plex tokens for watch tracking
- `jellyfin_users` - Per-user Jellyfin credentials
- `cr_sync_preview` - Pending Crunchyroll sync changes awaiting approval
- `cr_sync_log` - Applied CR sync changes with undo support
- `watch_sync_log` - Plex/Jellyfin sync audit trail with undo support (v4)
- `download_requests` - Sonarr/Radarr add request tracking
- `anilist_sonarr_mapping` - AniList↔Sonarr series mappings
- `anilist_radarr_mapping` - AniList↔Radarr movie mappings
- `anilist_sonarr_season_mapping` - Per-season Sonarr series mappings
- `anilist_arr_skip` - Entries skipped from auto-download
- `sonarr_series_cache` - Cached Sonarr series data (by TVDB ID)
- `radarr_movie_cache` - Cached Radarr movie data (by TMDB ID)
- `user_watchlist` - Cached AniList watchlist per linked user

### Migration Strategy
- Schema migrations handled via versioned SQL scripts in `src/Database/Migrations.py`
- Database auto-creates on first run if not present
- Migrations run automatically at startup

### Important Indexes/Constraints
- `media_mappings`: Unique constraint on (source, source_id) to prevent duplicate mappings
- `sync_state`: Composite index on (user_id, media_mapping_id) for fast per-user lookups
- `anilist_cache`: Index on expires_at for efficient TTL cleanup

---

## API Documentation

- **Location**: Auto-generated at `http://localhost:9876/docs` (FastAPI OpenAPI)
- **Authentication**: AniList OAuth2 for user-facing operations; no auth for local dashboard
- **Rate Limiting**: AniList enforces 90 requests/minute; proactive throttling implemented
- **Key Endpoints**:
  - `GET /` - Dashboard home page
  - `GET /api/status` - System status and sync statistics
  - `GET /api/progress` - Background task progress (floating widget)
  - `GET /api/fs/browse` - File system browser for restructure/onboarding
  - `GET /settings` - GUI configuration page
  - `GET /onboarding` - First-run setup wizard
  - `POST /api/test/{service}` - Connection test endpoints (plex, jellyfin, anilist, etc.)
  - `POST /api/sync` - Trigger manual Crunchyroll watch sync
  - `GET /auth/anilist` - Initiate AniList OAuth2 flow
  - `GET /auth/anilist/callback` - AniList OAuth2 callback handler
  - `GET /plex` - Plex library browser with mapping management
  - `GET /jellyfin` - Jellyfin library browser with mapping management
  - `POST /plex/scan/preview`, `POST /plex/scan/live` - Plex scan modes
  - `POST /scan/jellyfin/preview`, `POST /scan/jellyfin/live` - Jellyfin scan modes
  - `POST /plex/apply-all` - Apply AniList metadata to all matched Plex items
  - `GET /library/{id}` - Unified library detail view
  - `GET /restructure` - File restructure wizard
  - `POST /onboarding/restructure/analyze` - Shared restructure analysis endpoint
  - `POST /restructure/execute` - Execute approved file moves
  - `GET /crunchyroll` - Crunchyroll sync page with preview/history/undo
  - `GET /downloads` - Download manager UI
  - `GET /manual-grab` - Manual release grab
  - `GET /watchlist` - AniList watchlist browser
  - `POST /arr-webhook` - Sonarr/Radarr webhook receiver
  - `GET /tools` - Admin tools
  - `GET /api/scan/plex/search` - AniList title search for manual rematch

---

## Background Jobs

### Job System: APScheduler

#### Key Job Categories
- **Crunchyroll Watch Sync**: Periodic Crunchyroll→AniList watch sync [implemented]
- **Plex Metadata Scan**: Periodic scan of Plex libraries, matching to AniList, metadata application [triggered manually via UI]
- **Jellyfin Metadata Scan**: Periodic scan of Jellyfin libraries [triggered manually via UI]
- **Download Sync**: Periodic AniList watchlist → Sonarr/Radarr auto-add [implemented]
- **Watchlist Refresh**: Periodic refresh of `user_watchlist` cache from AniList for all linked users [implemented]
- **Plex Watch Sync**: Periodic Plex↔AniList watch sync [implemented — default disabled]
- **Jellyfin Watch Sync**: Periodic Jellyfin↔AniList watch sync [implemented — default disabled]

#### Important Job Classes
- `crunchyroll_sync` - Scheduled Crunchyroll watch sync at configurable interval [implemented]
- `download_sync` - Periodic AniList watchlist → Sonarr/Radarr sync [implemented]
- `watchlist_refresh` - Refreshes `user_watchlist` for all linked AniList users; every 30 min, on startup, and post-CR-sync [implemented]
- `plex_metadata_scan` - Plex library scan and metadata application [planned for scheduling]
- `plex_watch_sync` - Plex watch progress polling [implemented — default disabled]
- `jellyfin_watch_sync` - Jellyfin watch progress polling [implemented — default disabled]

---

## Docker Configuration

### Images Used
- **Base Image**: `python:3.11-alpine` (minimal Python image)
- **Multi-stage Builds**: Yes — build dependencies in first stage, slim runtime in second

### Binhex-Style Standardization
> Following Binhex's container conventions for consistency across all Docker images

#### Standard Volume Mappings
- **`/config`** - Configuration files, SQLite database, supervisord logs
  - Contains `supervisord.log` for container process logging
  - Contains `anilist_link.db` SQLite database
  - Example host mapping: `/mnt/user/appdata/AnilistLink:/config`

- **`/data`** - Application data (reserved for future use)
  - Example host mapping: `/mnt/user/data:/data`

#### Standard Environment Variables
All containers support these consistent environment variables:

**User/Group Management:**
- `PUID` - Process User ID (default: `99`)
- `PGID` - Process Group ID (default: `100`)
- `UMASK` - File creation permission mask (default: `000`, recommended: `002`)

**System Configuration:**
- `TZ` - Timezone (e.g., `America/New_York`)
- `DEBUG` - Enable debug logging (`true`/`false`, default: `false`)

**Application-Specific Variables:**
- `PLEX_URL` - Plex server URL
- `PLEX_TOKEN` - Plex authentication token
- `JELLYFIN_URL` - Jellyfin server URL
- `JELLYFIN_API_KEY` - Jellyfin API key
- `ANILIST_CLIENT_ID` - AniList OAuth2 client ID
- `ANILIST_CLIENT_SECRET` - AniList OAuth2 client secret
- `SONARR_URL` - Sonarr server URL [P4]
- `SONARR_API_KEY` - Sonarr API key [P4]
- `RADARR_URL` - Radarr server URL [P4]
- `RADARR_API_KEY` - Radarr API key [P4]

#### Standard Logging
- **Process Manager**: Supervisord (manages all container processes)
- **Main Log Location**: `/config/supervisord.log`
- Application logs also write to `/config/logs/anilist_link.log`

#### Example Docker Compose Configuration
```yaml
services:
  AnilistLink:
    image: dogberttech/anilist-link:latest
    container_name: AnilistLink
    restart: unless-stopped
    volumes:
      - /mnt/user/appdata/AnilistLink:/config
      - /mnt/user/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
      - TZ=America/New_York
      - DEBUG=false
      - PLEX_URL=http://192.168.1.100:32400
      - PLEX_TOKEN=your-plex-token
      - JELLYFIN_URL=http://192.168.1.100:8096
      - JELLYFIN_API_KEY=your-jellyfin-api-key
      - ANILIST_CLIENT_ID=your-anilist-client-id
      - ANILIST_CLIENT_SECRET=your-anilist-client-secret
    ports:
      - 9876:9876
```

### Optimization Notes
> These are applied in our Dockerfiles
- Using minimal base images (python:3.11-alpine)
- Layer caching optimized (dependencies before code)
- `.dockerignore` configured to exclude unnecessary files
- Combined RUN commands to reduce layers
- Multi-stage builds to minimize final image size

### Docker Commands
```bash
docker pull dogberttech/anilist-link:latest       # Pull latest image
docker build -t dogberttech/anilist-link:latest . # Build image locally (dev)
docker-compose up -d                              # Run container
docker logs AnilistLink                           # View container logs
docker exec -it AnilistLink cat /config/supervisord.log  # View detailed logs
docker-compose down                               # Stop container
```

---

## Coding Conventions

### General Principles
1. **Async-first**: Use async/await for all I/O operations (API calls, database, file I/O)
2. **Keep functions focused**: Each function should do one thing well
3. **Type hints everywhere**: All function signatures must include type annotations

### Project-Specific Rules
1. **All external API calls go through Client classes**: Never make raw HTTP requests outside of `src/Clients/`
2. **All database access goes through the Database layer**: Never import sqlite3/aiosqlite outside of `src/Database/`
3. **Configuration via environment variables**: Use `src/Utils/Config.py` for all config access, never read env vars directly in business logic

### Error Handling
- Use specific exception classes for different failure modes (e.g., `RateLimitError`, `AuthenticationError`)
- Log errors with context (which client, which operation, which item)
- Never silently swallow exceptions — at minimum, log them
- Graceful degradation: if one platform fails, others should continue operating

### Performance Considerations
- Respect AniList rate limits (90 req/min) — use proactive throttling
- Cache AniList API responses with TTL to reduce redundant calls
- Use incremental rescans (only process items changed since last scan)
- Batch database operations where possible

---

## Security & Privacy

### Security Best Practices
1. **Never log OAuth tokens or API keys** — mask them in log output
2. **Validate all user input** from the web dashboard before processing
3. **Use parameterized queries** for all SQLite operations (prevent SQL injection)

### Authentication/Authorization
- AniList OAuth2 flow for user account linking (each user gets their own token)
- Plex token authentication for server access
- Jellyfin API key for server access
- Web dashboard is local-only (no built-in auth — relies on network-level access control)

### Data Privacy
- OAuth tokens stored locally in SQLite database
- No data sent to external services other than the configured platforms
- Users can unlink their accounts and delete their tokens via the dashboard

---

## Common Tasks

### Adding a New Feature
1. Create or modify files in the appropriate `src/` subdirectory
2. Update `docs/ARCHITECTURE.md` if the change affects system design
3. Write tests in `tests/Unit/` or `tests/Integration/`
4. Run `ruff check src/` and `mypy src/` before committing
5. Update this file if new conventions or patterns are introduced

### Debugging
- **Logs Location**: `/config/logs/anilist_link.log` (application) and `/config/supervisord.log` (container)
- **Debug Mode**: Set `DEBUG=true` environment variable for verbose logging
- **Common Issues**: See [QUICK-REFERENCE.md](QUICK-REFERENCE.md) troubleshooting section

### Database Changes
1. Add migration logic to `src/Database/Migrations.py`
2. Update `src/Database/Models.py` with new/changed table definitions
3. Test migration on a copy of the database before applying
4. Migrations auto-run on container startup

---

## Custom Commands & Aliases

### Shell Aliases (for developers)
```bash
alias alrun='docker-compose up -d'           # Start Anilist-Link
alias allogs='docker logs -f AnilistLink'    # Follow container logs
alias alstop='docker-compose down'           # Stop Anilist-Link
```

---

## Deployment

### Environments
- **Development**: Local Python venv or Docker container, SQLite in project directory
- **Production**: Docker container on Unraid/Docker host, SQLite in mounted `/config` volume

### Deployment Process
1. Pull the published image: `docker pull dogberttech/anilist-link:latest`
2. Update `docker-compose.yml` with correct environment variables
3. Deploy: `docker-compose up -d`
4. Verify: Check `http://localhost:9876` for dashboard

### CI/CD Pipeline
- GitHub Actions runs on push and pull request
- Automated checks: Ruff lint, mypy type check, pytest test suite
- Docker image build verification

---

## Monitoring & Logging

### Application Monitoring
- **Dashboard**: Built-in web dashboard at `http://localhost:9876`
- **Key Metrics**: Sync status per user, mapping success rate, last scan timestamp, AniList API rate limit usage

### Logging Strategy
- **Log Levels**: DEBUG (verbose), INFO (normal operations), WARNING (non-critical issues), ERROR (failures)
- **Log Location**: `/config/logs/anilist_link.log` (application), `/config/supervisord.log` (container process)
- **Retention**: Log rotation configured to prevent unbounded growth

---

## Dependencies & Updates

### Dependency Management
- **Update Frequency**: Monthly or as needed for security patches
- **Security Updates**: Monitor via GitHub Dependabot
- **Major Version Updates**: Test in development before deploying

### Important Version Constraints
- Python >= 3.11 (required for modern async features and type syntax)
- FastAPI >= 0.100 (for modern Pydantic v2 support)
- rapidfuzz >= 3.0 (for current API compatibility)

---

## Known Issues & Gotchas

### Common Pitfalls
1. **AniList rate limiting**: Exceeding 90 req/min triggers 429 responses with exponential backoff. Always use the throttled client.
2. **Crunchyroll API instability**: The reverse-engineered API may break without notice. Check `_resources/Research/` for latest findings.
3. **Plex multi-user tokens**: Per-user tracking requires obtaining individual tokens via Plex.tv API, not just the server admin token.

### Technical Debt
**P2 — File Organization**: ✅ Complete
- All 3 operation levels implemented: folder rename (L1), folder+file rename (L2), full restructure (L3)
- Wizard UI with shared file browser and naming template modules, analyze, execute, and auto-rescan all working
- Multi-source restructure with conflict detection and resolution

**P3 — Metadata**: ✅ Complete (Plex + Jellyfin)
- MetadataScanner (Plex) and JellyfinMetadataScanner, PlexClient + JellyfinClient metadata writing, structure A/B/C detection, series groups all working
- Manual overrides UI at `/mappings` (list, add, delete)
- Deferred (non-blocking): staff/credits writing to Plex, GUID-based high-confidence matching

**P1 — Watch Sync**: Crunchyroll, Plex, and Jellyfin all implemented
- Crunchyroll→AniList sync with preview/approve/undo pipeline (CrunchyrollPreviewRunner) ✅
- PlexWatchSyncer and JellyfinWatchSyncer — bidirectional sync (polling) ✅
- Enable/disable toggles per source, default disabled (`plex.watch_sync_enabled`, `jellyfin.watch_sync_enabled`) ✅
- COMPLETED status protection: never downgrades AniList entries already marked COMPLETED ✅
- Circular sync fix: backfill always writes `sync_state` to prevent false forward-sync updates ✅
- `watch_sync_log` table (v4): full audit trail with per-entry undo from the Watch Sync UI ✅
- Plex/Jellyfin webhook handler (real-time sync) not yet implemented
- AniList token auto-refresh not yet wired up

**P4 — Downloads**: ✅ Complete
- SonarrClient, RadarrClient, DownloadManager, MappingResolver, ArrPostProcessor, DownloadSyncer all implemented
- Download management UI, manual grab, watchlist browser, Sonarr sync, webhook receiver all working
- Post-processor: naming templates, series groups, season mappings, file renaming, Sonarr path sync + rescan
- Webhook auto-registration (schema-based), SSE resolve with live progress, S1 title variants for sequel search
- Full automation (auto-search on new CURRENT status) partial — DownloadSyncer exists

**General**:
- Crunchyroll client needs ongoing maintenance as the unofficial API changes

---

## Resources & References

- **Repository**: https://github.com/Mprice12337/Anilist-Link
- **Existing Codebase to Merge**: https://github.com/Mprice12337/Crunchyroll-Anilist-Sync
- **AniList API Docs**: https://anilist.gitbook.io/anilist-apiv2-docs
- **Plex API**: https://github.com/Arcanemagus/plex-api/wiki
- **Jellyfin API**: https://api.jellyfin.org/
- **Sonarr API**: https://sonarr.tv/docs/api/
- **Radarr API**: https://radarr.video/docs/api/
- **rapidfuzz Docs**: https://rapidfuzz.github.io/RapidFuzz/

---

## Quick Reference

### Most Common Commands
```bash
# Development
python -m src.Main                               # Run locally
uvicorn src.Web.App:app --reload --port 9876     # Run with hot reload

# Testing
pytest                                            # Run all tests
pytest tests/Unit/                                # Run unit tests
pytest --cov=src                                  # Run with coverage

# Code Quality
ruff check src/                                   # Lint
black src/                                        # Format
mypy src/                                         # Type check

# Docker
docker-compose up -d                              # Start containers
docker-compose down                               # Stop containers
docker logs AnilistLink                           # View container logs
docker exec -it AnilistLink cat /config/supervisord.log  # View detailed process logs
docker ps                                         # List running containers
```

### File Locations
- Config: `/config` (in container) - Maps to host path defined in docker-compose
- Logs: `/config/supervisord.log` (main container log)
- Application Logs: `/config/logs/anilist_link.log`
- Database: `/config/anilist_link.db`
- Tests: `tests/`
- **Documentation**: `/docs` folder (all docs except README.md)
- **ARCHITECTURE.md**: `/docs/ARCHITECTURE.md` (required)
- **CLAUDE.md**: `/docs/CLAUDE.md` (symlinked to root)
- **Reference Materials**: `/_resources` (NOT in git - for dev use only)

### Troubleshooting Docker Permission Issues
```bash
# Check current PUID/PGID
docker exec AnilistLink id

# View file ownership in container
docker exec AnilistLink ls -la /config

# Fix permissions on host (if needed)
sudo chown -R 1000:1000 /mnt/user/appdata/AnilistLink
sudo chmod -R 775 /mnt/user/appdata/AnilistLink
```

---

## MCP Servers (if applicable)

> Claude Code can use MCP servers to extend capabilities

### Configured Servers
- **Playwright MCP**: Browser automation for testing the web dashboard UI and OAuth flows

### Usage Notes
- Use Playwright MCP for visual testing of the dashboard at `http://localhost:9876`
- Not required for core development — primarily useful for E2E testing of the web interface

---

## Notes for Maintaining This File

**When to Update**:
- Major architectural changes (also update ARCHITECTURE.md)
- New development workflows
- Security policy changes
- New dependencies or technology additions
- Project structure changes

**What Not to Include**:
- Frequently changing data (current sprint goals)
- Duplicate information from README
- Overly detailed API specs (link instead)
- Temporary development notes (use `_resources/` instead)

**Tips**:
- Keep explanations concise but complete
- Use examples for complex concepts
- Update when Claude repeatedly makes the same mistakes
- Store draft updates in `_resources/` before committing to docs
- Keep ARCHITECTURE.md in sync with this file
