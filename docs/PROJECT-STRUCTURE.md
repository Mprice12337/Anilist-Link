# Anilist-Link Project Structure Reference

This document provides the complete project organization for Anilist-Link, following all established standards: Binhex Docker conventions, PascalCase naming for files/directories, and documentation organization.

---

## Visual Project Structure

```
Anilist-Link/                                     # Project root
│
├── README.md                                     # Main project overview (root only)
├── CLAUDE.md                                     # Symlink → docs/CLAUDE.md
├── .gitignore                                    # Includes _resources/
├── .dockerignore                                 # Docker build context exclusions
├── docker-compose.yml                            # Binhex-compliant configuration
├── Dockerfile                                    # Multi-stage, optimized for Python
├── pyproject.toml                                # Python project configuration
│
├── _resources/                                   # NOT IN GIT - Development references
│   ├── Examples/                                 # API response samples
│   ├── Research/                                 # Technology research, API notes
│   ├── Assets/                                   # Design files, mockups
│   └── Notes/                                    # Development notes, scratchpad
│
├── docs/                                         # All documentation
│   ├── ARCHITECTURE.md                           # Required - system design
│   ├── CLAUDE.md                                 # Actual file location
│   ├── DEV-SETUP.md                              # Developer setup guide
│   ├── QUICK-REFERENCE.md                        # Best practices quick reference
│   └── PROJECT-STRUCTURE.md                      # This document
│
├── scripts/                                      # Automation and testing scripts
│   ├── reset_for_testing.py                      # Reset DB state for manual testing
│   ├── test_connector_integration.py             # Connector integration smoke tests
│   └── test_series_groups.py                     # Series group builder smoke tests
│
├── src/                                          # Main application source code
│   ├── __init__.py
│   ├── Main.py                                   # Application entry point
│   │
│   ├── Clients/                                  # External API client modules
│   │   ├── __init__.py
│   │   ├── AnilistClient.py                      # AniList GraphQL + OAuth2 + rate limiter
│   │   ├── PlexClient.py                         # Plex API — library, metadata, watch
│   │   ├── JellyfinClient.py                     # Jellyfin API — library, metadata, watch
│   │   ├── CrunchyrollClient.py                  # Crunchyroll — reverse-engineered auth
│   │   ├���─ SonarrClient.py                       # Sonarr API v3 — series add/lookup [P4]
│   │   ├── RadarrClient.py                       # Radarr API v3 — movie add/lookup [P4]
│   │   ├── ServarrBaseClient.py                  # Shared base for Sonarr/Radarr clients
│   │   ├── ProwlarrClient.py                     # Prowlarr indexer manager integration
│   │   ├── QBittorrentClient.py                  # qBittorrent torrent client
│   │   ├── TVMazeClient.py                       # TVMaze API for provider ID lookups
│   │   ├─��� JellyfinEventListener.py              # Jellyfin WebSocket event listener
│   │   ├── NamingTranslator.py                   # AniList→TVDB/TMDB ID translation
│   │
│   ├── Matching/                                 # Title matching engine
│   │   ├── __init__.py
│   │   ├── TitleMatcher.py                       # Multi-algorithm fuzzy matching
│   │   └── Normalizer.py                         # Anime-specific title normalization
│   │
│   ├── Scanner/                                  # Metadata scanning pipeline
│   │   ├── __init__.py
│   │   ├── MetadataScanner.py                    # Plex: scan → match → cache → apply
│   │   ├── JellyfinMetadataScanner.py            # Jellyfin: scan → match → cache → apply
│   │   ├── LibraryRestructurer.py                # File organization engine (L1/L2/L3)
│   │   ├── LibraryScanner.py                     # Shared library scan utilities
│   │   ├── LocalDirectoryScanner.py              # Local filesystem directory scanner
│   │   ├── SeriesGroupBuilder.py                 # BFS relation traversal for series groups
│   │   ├── PlexShowProvider.py                   # Plex → ShowInput adapter
│   │   └── JellyfinShowProvider.py               # Jellyfin → ShowInput adapter
│   │
│   ├── Sync/                                     # Watch status synchronization
│   │   ├── __init__.py
│   │   ├── WatchSyncer.py                        # Crunchyroll→AniList watch sync
│   │   ├── PlexWatchSyncer.py                    # Plex↔AniList bidirectional watch sync
│   │   ├── JellyfinWatchSyncer.py                # Jellyfin↔AniList bidirectional watch sync
│   │   ├── DownloadSyncer.py                     # Download status sync [P4]
│   │   └── CrunchyrollPreviewRunner.py           # CR sync preview pipeline
│   │
│   ├── Download/                                 # Download management [P4]
│   │   ├── __init__.py
│   │   ├── DownloadManager.py                    # Orchestrate Sonarr/Radarr add requests
│   │   ├── MappingResolver.py                    # AniList → TVDB/TMDB ID resolution
│   │   └── ArrPostProcessor.py                   # Webhook post-processing for *arr
│   │
│   ├── Web/                                      # FastAPI web dashboard
│   │   ├── __init__.py
│   │   ├── App.py                                # FastAPI application factory
│   │   │
│   │   ├── Routes/                               # API route handlers
│   │   │   ├── __init__.py
│   │   │   ├── Dashboard.py                      # Dashboard + stats endpoints
│   │   │   ├── Auth.py                           # AniList OAuth2 account linking
│   │   │   ├── Settings.py                       # GUI settings management
│   │   │   ├── Mappings.py                       # Manual override management
│   │   │   ├── PlexLibrary.py                    # Plex library browser
│   │   │   ├── PlexScan.py                       # Plex scan pipeline endpoints
│   │   │   ├── JellyfinLibrary.py                # Jellyfin library browser
│   │   │   ├── JellyfinScan.py                   # Jellyfin scan pipeline endpoints
│   │   │   ├── Restructure.py                    # File restructure wizard
│   │   │   ├── Library.py                        # Unified library manager
│   │   │   ├── UnifiedLibrary.py                 # Unified library view
│   │   │   ├── WatchlistLibrary.py               # AniList watchlist library
│   │   │   ├── ManualGrab.py                     # Manual torrent grab
│   │   │   ├── Downloads.py                      # Download manager UI [P4]
│   │   │   ├── CrunchyrollSync.py                # Crunchyroll sync UI
│   │   │   ├── Onboarding.py                     # First-run onboarding wizard
│   │   │   ├── ConnectionTest.py                 # Service connection test endpoints
│   │   │   ├── SonarrSync.py                     # Sonarr sync management [P4]
│   │   │   ├── ArrWebhook.py                     # Sonarr/Radarr webhook handlers [P4]
│   │   │   └── Tools.py                          # Developer/admin tools
│   │   │
│   │   ├── Templates/                            # Jinja2 HTML templates
│   │   │   ├── base.html                         # Base layout with nav + progress widget
│   │   │   ├── dashboard.html                    # Main dashboard
│   │   │   ├── settings.html                     # Settings page
│   │   │   ├── mappings.html                     # Manual override management
│   │   │   ├── scan_preview.html                 # Scan preview (Plex + Jellyfin)
│   │   │   ├── scan_progress.html                # Live scan progress (Plex + Jellyfin)
│   │   │   ├── restructure_wizard.html           # Restructure wizard step 1
│   │   │   ├── restructure_preview.html          # Restructure preview/confirm
│   │   │   ├── restructure_progress.html         # Restructure execution progress
│   │   │   ├── restructure_results.html          # Restructure execution results
│   │   │   ├── restructure_report.html           # Restructure audit log report
│   │   │   ├── watchlist_library.html            # AniList watchlist view
│   │   │   ├── library_detail.html               # Library item detail
│   │   │   ├── library_scan_progress.html        # Library scan progress
│   │   │   ├── library_scan_results.html         # Library scan results
│   │   │   ├── unified_library.html              # Unified cross-platform library
│   │   │   ├── plex_library.html                  # Plex library browser
│   │   │   ├── jellyfin_library.html             # Jellyfin library browser
│   │   │   ├── jellyfin_scan.html                # Jellyfin scan trigger
│   │   │   ├── manual_grab.html                  # Manual torrent grab
│   │   │   ├── download_manager.html             # Download manager [P4]
│   │   │   ├── crunchyroll.html                  # Crunchyroll sync page
│   │   │   ├── crunchyroll_preview.html          # CR sync preview
│   │   │   ├── crunchyroll_history.html          # CR sync history
│   │   │   ├── onboarding.html                   # 4-step onboarding wizard
│   │   │   └── tools.html                        # Developer tools page
│   │   │
│   │   └── Static/                               # Static assets
│   │       ├── style.css                         # Application stylesheet
│   │       ├── file-browser.js                   # Shared dual-pane file browser module
│   │       ├── naming-templates.js               # Shared naming template presets/preview
│   │       └── img/                              # Image assets
│   │
│   ├── Database/                                 # Database layer
│   │   ├── __init__.py
│   │   ├── Connection.py                         # SQLite/aiosqlite connection management
│   │   ├── Models.py                             # Table DDL definitions (TABLES dict)
│   │   └── Migrations.py                         # Consolidated v1 schema baseline (29 tables)
│   │
│   ├── Scheduler/                                # Background job scheduling
│   │   ├── __init__.py
│   │   └── Jobs.py                               # APScheduler job definitions
│   │
│   └── Utils/                                    # Shared utilities
│       ├── __init__.py
│       ├── Config.py                             # Config dataclass + env var loading
│       ├── Logging.py                            # Logging configuration
│       ├── NamingTemplate.py                     # File naming template engine
│       ├── NamingTranslator.py                   # AniList → filename translation
│       └── PathTranslator.py                     # Path translation utilities
│
├── tests/                                        # Test suite
│   ├── conftest.py                               # Shared fixtures (DB, config, clients)
│   ├── Unit/                                     # Unit tests
│   │   ├── test_arr_post_processor.py            # ArrPostProcessor unit tests
│   │   ├── test_config.py                        # Config loading and validation
│   │   ├── test_database.py                      # Database CRUD operations
│   │   ├── test_naming_template.py               # NamingTemplate parsing tests
│   │   ├── test_normalizer.py                    # Title normalization (68 tests)
│   │   ├── test_plex_client.py                   # PlexClient methods (33 tests)
│   │   ├── test_rate_limiter.py                  # Token-bucket rate limiter (18 tests)
│   │   ├── test_series_group_builder.py          # BFS group builder (22 tests)
│   │   └── test_title_matcher.py                 # Title matching engine (55 tests)
│   └── Integration/                              # Integration tests (placeholder)
│
└── .github/                                      # GitHub CI/CD
    └── workflows/
        └── CI.yml                                # Lint, type-check, test pipeline
```

---

## Docker Setup (Binhex Standard)

### docker-compose.yml
```yaml
services:
  AnilistLink:
    build: .
    container_name: AnilistLink
    restart: unless-stopped

    # Binhex standard volume mappings
    volumes:
      - ./config:/config                  # Config, logs, SQLite database
      - ./data:/data                      # Application data

    # Binhex standard environment variables
    environment:
      # User/Group Management
      - PUID=1000                         # Your user ID (id -u)
      - PGID=1000                         # Your group ID (id -g)
      - UMASK=002                         # Group writable (recommended)

      # System Configuration
      - TZ=America/New_York               # Your timezone
      - DEBUG=false                       # Debug mode

      # Application Variables (UPPER_SNAKE_CASE)
      - PLEX_URL=http://192.168.1.100:32400
      - PLEX_TOKEN=your-plex-token
      - JELLYFIN_URL=http://192.168.1.100:8096
      - JELLYFIN_API_KEY=your-jellyfin-api-key
      - ANILIST_CLIENT_ID=your-anilist-client-id
      - ANILIST_CLIENT_SECRET=your-anilist-client-secret
      # P4 Download Management (optional)
      - SONARR_URL=http://192.168.1.100:8989
      - SONARR_API_KEY=your-sonarr-api-key
      - RADARR_URL=http://192.168.1.100:7878
      - RADARR_API_KEY=your-radarr-api-key

    ports:
      - "9876:9876"

    networks:
      - AnilistNetwork

networks:
  AnilistNetwork:
    driver: bridge
```

---

## .gitignore (Complete)

```gitignore
# Development resources (NOT in version control)
_resources/

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
*.egg-info/
dist/
build/
*.egg

# Virtual environments
.venv/
venv/
ENV/

# Environment files
.env
.env.local
.env.*.local

# Logs
logs/
*.log

# OS files
.DS_Store
Thumbs.db
desktop.ini

# IDE
.vscode/
.idea/
*.swp
*.swo
*.sublime-*

# Test coverage
coverage/
htmlcov/
.coverage
.pytest_cache/

# Type checking
.mypy_cache/

# Docker volumes (local development)
config/
data/

# Temporary files
tmp/
temp/
*.tmp

# Ruff cache
.ruff_cache/

# SQLite database (local dev)
*.db
```

---

## File Naming Conventions

### PascalCase (Code Files & Directories)
```
Correct:
- AnilistClient.py
- TitleMatcher.py
- MetadataScanner.py
- WatchSyncer.py
- Clients/
- Matching/
- Scanner/

Incorrect:
- anilist_client.py  (snake_case)
- titleMatcher.py    (camelCase)
- metadata-scanner.py (kebab-case)
```

### snake_case (Python Variables & Functions — PEP 8)
```
Correct:
- user_data = get_user_by_id(123)
- match_score = calculate_confidence(title)
- sync_state = get_sync_status(user_id, media_id)

Incorrect:
- UserData = GetUserById(123)  (PascalCase for variables/functions)
```

### UPPER_SNAKE_CASE (Environment Variables & Constants)
```
Correct:
- PUID
- PGID
- PLEX_URL
- ANILIST_CLIENT_ID
- MAX_RETRY_COUNT
- API_BASE_URL

Incorrect:
- Puid  (PascalCase)
- plexUrl  (camelCase)
- anilist-client-id  (kebab-case)
```

---

## Database Schema (v1 — consolidated 1.0 baseline)

Current tables (24 total):

| Table | Purpose |
|---|---|
| `schema_version` | Migration version tracking |
| `users` | Linked AniList accounts with OAuth tokens |
| `sync_state` | Per-user, per-item watch sync tracking |
| `anilist_cache` | Cached AniList metadata (7-day TTL) |
| `media_mappings` | Media server item → AniList ID mappings |
| `manual_overrides` | User-specified title→AniList overrides |
| `cr_session_cache` | Crunchyroll auth session (30-day TTL) |
| `app_settings` | GUI-managed configuration |
| `plex_media` | Persistent Plex library snapshot |
| `series_groups` | AniList SEQUEL/PREQUEL relation groups |
| `series_group_entries` | Entries within a series group |
| `restructure_log` | File move operation audit trail |
| `libraries` | Library manager library definitions |
| `library_items` | Items within managed libraries |
| `jellyfin_media` | Persistent Jellyfin library snapshot |
| `plex_users` | Per-user Plex tokens (P1) |
| `jellyfin_users` | Per-user Jellyfin credentials (P1) |
| `cr_sync_preview` | Crunchyroll sync preview runs |
| `cr_sync_log` | Crunchyroll sync operation history |
| `download_requests` | Sonarr/Radarr request tracking (P4) |
| `anilist_sonarr_mapping` | AniList → Sonarr series mapping (P4) |
| `anilist_radarr_mapping` | AniList → Radarr movie mapping (P4) |
| `sonarr_series_cache` | Sonarr series metadata cache (P4) |
| `radarr_movie_cache` | Radarr movie metadata cache (P4) |
| `anilist_sonarr_season_mapping` | Per-season AniList title resolution (P4) |
| `anilist_arr_skip` | Cache of auto-sync resolution failures (P4) |
| `user_watchlist` | AniList watchlist snapshot per user |

---

## Test Suite Summary

Total: 318 tests across 11 files

| Test File | Coverage Focus | Tests |
|---|---|---|
| `test_normalizer.py` | All 6 normalization functions | 68 |
| `test_title_matcher.py` | Similarity, season detection, best-match | 55 |
| `test_database.py` | CRUD operations for all core tables | 31 |
| `test_config.py` | Dataclass loading, env vars, frozen | 30 |
| `test_plex_client.py` | PlexShow properties, metadata params | 33 |
| `test_series_group_builder.py` | BFS traversal, caching, filtering | 22 |
| `test_naming_template.py` | Quality parsing, template rendering | 21 |
| `test_rate_limiter.py` | Token bucket, refill, high-priority | 18 |
| `test_arr_post_processor.py` | Webhook processing | (varies) |

---

## Quick Commands Reference

```bash
# Development
source .venv/bin/activate                         # Activate virtual environment
python -m src.Main                                # Run application
uvicorn src.Web.App:app --reload --port 9876      # Run with hot reload

# Testing
pytest                                            # Run all tests
pytest tests/Unit/                                # Unit tests only
pytest --cov=src                                  # With coverage

# Code Quality
black src/                                        # Format (run first)
ruff check src/                                   # Lint
ruff check --fix src/                             # Auto-fix lint issues
mypy src/                                         # Type check

# Docker commands (Binhex style)
docker-compose up -d                              # Start container
docker-compose down                               # Stop container
docker logs AnilistLink                           # View logs
docker exec AnilistLink cat /config/supervisord.log  # Detailed logs
docker exec -it AnilistLink sh                    # Shell into container

# Git commands
git pull --rebase                                 # Pull with rebase
git status                                        # Check status
ls -la CLAUDE.md                                  # Verify symlink
```

---

**Last Updated**: 2026-04-29
**Schema Version**: 17
**Test Count**: 318
