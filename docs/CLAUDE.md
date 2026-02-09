# CLAUDE.md - Anilist-Link

> **Purpose**: This file serves as your project's memory for Claude Code. It defines rules, workflows, and preferences that Claude will automatically follow when working on your codebase.

## Project Overview

**Anilist-Link** is a self-hosted Docker container that serves as a centralized bridge between AniList and multiple media platforms (Crunchyroll, Plex, Jellyfin). It syncs watch progress to AniList and acts as an AniList-powered metadata provider for Plex and Jellyfin libraries. The project consolidates and expands the existing Crunchyroll-Anilist-Sync container into a unified, multi-platform service.

### Key Features
- Sync watch progress from Crunchyroll, Plex, and Jellyfin to AniList
- Serve as an AniList-powered metadata provider for Plex and Jellyfin anime libraries (titles, descriptions, cover art, genres, ratings, studios, staff)
- Per-user AniList account linking via OAuth2
- Web-based configuration dashboard for managing connections, mappings, and sync status

### Project Context
- **Stage**: MVP (Phase 1 — Foundation & Crunchyroll Merge)
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
- **AniList Client**: GraphQL client with OAuth2 flow, rate limiting (90 req/min), public queries, and authenticated mutations
- **Plex Client**: Library enumeration, metadata writing, per-user watch tracking via Plex.tv API
- **Jellyfin Client**: Library access, metadata writing, watch status tracking via open API
- **Crunchyroll Client**: Reverse-engineered auth + watch history retrieval with session persistence
- **Title Matching Engine**: rapidfuzz-based multi-algorithm fuzzy matching with anime-specific normalization
- **Metadata Scanner**: Orchestrates scan → match → cache → apply pipeline across media libraries
- **Watch Syncer**: Bidirectional watch sync with automatic status transitions (PLANNING → CURRENT → COMPLETED)

### Design Patterns Used
- **Pipeline Pattern**: Metadata Scanner uses scan → match → cache → apply pipeline
- **Strategy Pattern**: Multiple fuzzy matching algorithms (ratio, partial ratio, token sort, token set) with configurable weights
- **Observer Pattern**: Webhook handlers for real-time sync from Plex/Jellyfin
- **Repository Pattern**: Database layer abstracts SQLite operations behind clean interfaces

### Data Flow
User configures connections via Web Dashboard → Scheduler triggers periodic scan → Metadata Scanner enumerates media library items → Title Matching Engine matches items to AniList entries → AniList Client fetches metadata → Scanner writes metadata back to Plex/Jellyfin → Watch Syncer detects progress changes → AniList Client updates watch status per linked user

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

---

## Database

### Schema Overview
- `media_mappings` - Maps media server library items to AniList IDs with confidence scores, match method, and cached metadata
- `users` - Plex/Jellyfin users linked to AniList accounts with OAuth tokens
- `sync_state` - Per-user, per-item sync tracking (last synced episode, timestamp, status)
- `anilist_cache` - Cached AniList metadata with TTL for reducing API calls
- `manual_overrides` - User-specified title-to-AniList-ID overrides

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
  - `POST /api/scan` - Trigger manual metadata scan
  - `POST /api/sync` - Trigger manual watch sync
  - `GET /api/mappings` - List all media-to-AniList mappings
  - `PUT /api/mappings/{id}` - Override a mapping manually
  - `GET /auth/anilist` - Initiate AniList OAuth2 flow
  - `GET /auth/anilist/callback` - AniList OAuth2 callback handler

---

## Background Jobs

### Job System: APScheduler

#### Key Job Categories
- **Metadata Scan**: Periodic scan of configured media libraries, matching to AniList entries, and metadata application
- **Watch Sync**: Periodic check for watch progress changes and sync to AniList

#### Important Job Classes
- `MetadataScanJob` - Runs at configurable interval (default: daily), scans all configured libraries
- `WatchSyncJob` - Runs at configurable interval (default: every 15 minutes), checks for watch progress updates

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

#### Standard Logging
- **Process Manager**: Supervisord (manages all container processes)
- **Main Log Location**: `/config/supervisord.log`
- Application logs also write to `/config/anilist_link.log`

#### Example Docker Compose Configuration
```yaml
services:
  AnilistLink:
    build: .
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
docker build -t anilist-link .                    # Build image
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
- **Logs Location**: `/config/anilist_link.log` (application) and `/config/supervisord.log` (container)
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
1. Build Docker image: `docker build -t anilist-link .`
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
- **Log Location**: `/config/anilist_link.log` (application), `/config/supervisord.log` (container process)
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
- Crunchyroll client needs ongoing maintenance as the unofficial API changes
- Season-to-AniList-ID relation graph traversal not yet implemented
- AniList token auto-refresh not yet wired up

---

## Resources & References

- **Repository**: https://github.com/Mprice12337/Anilist-Link
- **Existing Codebase to Merge**: https://github.com/Mprice12337/Crunchyroll-Anilist-Sync
- **AniList API Docs**: https://anilist.gitbook.io/anilist-apiv2-docs
- **Plex API**: https://github.com/Arcanemagus/plex-api/wiki
- **Jellyfin API**: https://api.jellyfin.org/
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
- Application Logs: `/config/anilist_link.log`
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
