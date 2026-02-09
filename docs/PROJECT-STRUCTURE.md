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
│   │   ├── AnilistMediaResponse.json             # AniList GraphQL response example
│   │   ├── PlexLibraryResponse.json              # Plex library API response
│   │   └── JellyfinItemsResponse.json            # Jellyfin items API response
│   ├── Research/                                 # Technology research, API notes
│   │   ├── CrunchyrollApi.md                     # Reverse-engineered API documentation
│   │   └── TitleMatchingAlgorithms.md            # Matching algorithm comparisons
│   ├── Assets/                                   # Design files, mockups
│   │   └── DashboardMockup.png                   # Web dashboard design
│   └── Notes/                                    # Development notes, scratchpad
│       └── TechDebtIdeas.md                      # Technical debt tracking
│
├── docs/                                         # All documentation
│   ├── ARCHITECTURE.md                           # Required - system design
│   ├── CLAUDE.md                                 # Actual file location
│   ├── DEV-SETUP.md                              # Developer setup guide
│   ├── QUICK-REFERENCE.md                        # Best practices quick reference
│   └── PROJECT-STRUCTURE.md                      # This document
│
├── src/                                          # Main application source code
│   ├── __init__.py
│   ├── Main.py                                   # Application entry point
│   ├── Clients/                                  # External API client modules
│   │   ├── __init__.py
│   │   ├── AnilistClient.py                      # AniList GraphQL + OAuth2 client
│   │   ├── PlexClient.py                         # Plex API client
│   │   ├── JellyfinClient.py                     # Jellyfin API client
│   │   └── CrunchyrollClient.py                  # Crunchyroll reverse-engineered client
│   ├── Matching/                                 # Title matching engine
│   │   ├── __init__.py
│   │   ├── TitleMatcher.py                       # Multi-algorithm fuzzy matching
│   │   └── Normalizer.py                         # Anime-specific title normalization
│   ├── Scanner/                                  # Metadata scanning pipeline
│   │   ├── __init__.py
│   │   └── MetadataScanner.py                    # Scan → match → cache → apply
│   ├── Sync/                                     # Watch status synchronization
│   │   ├── __init__.py
│   │   └── WatchSyncer.py                        # Bidirectional watch progress sync
│   ├── Web/                                      # FastAPI web dashboard
│   │   ├── __init__.py
│   │   ├── App.py                                # FastAPI application factory
│   │   ├── Routes/                               # API route handlers
│   │   │   ├── __init__.py
│   │   │   ├── Dashboard.py                      # Dashboard and stats endpoints
│   │   │   ├── Auth.py                           # OAuth2 account linking endpoints
│   │   │   └── Mappings.py                       # Mapping review/override endpoints
│   │   ├── Templates/                            # Jinja2 HTML templates
│   │   └── Static/                               # CSS, JS, static assets
│   ├── Database/                                 # Database layer
│   │   ├── __init__.py
│   │   ├── Connection.py                         # SQLite/aiosqlite connection management
│   │   ├── Models.py                             # Table definitions and data models
│   │   └── Migrations.py                         # Schema migration utilities
│   ├── Scheduler/                                # Background job scheduling
│   │   ├── __init__.py
│   │   └── Jobs.py                               # APScheduler job definitions
│   └── Utils/                                    # Shared utility functions
│       ├── __init__.py
│       ├── Config.py                             # Configuration management
│       └── Logging.py                            # Logging configuration
│
├── tests/                                        # Test suite
│   ├── __init__.py
│   ├── Unit/                                     # Unit tests
│   │   ├── __init__.py
│   │   ├── TestTitleMatcher.py                   # Title matching engine tests
│   │   ├── TestAnilistClient.py                  # AniList client tests
│   │   └── TestWatchSyncer.py                    # Watch syncer tests
│   └── Integration/                              # Integration tests
│       ├── __init__.py
│       ├── TestApiClients.py                     # Client integration tests
│       └── TestScannerPipeline.py                # Scanner pipeline tests
│
├── scripts/                                      # Automation scripts
│   └── Setup.sh                                  # Initial setup script
│
└── .github/                                      # GitHub CI/CD
    └── workflows/
        └── CI.yml                                # Continuous integration pipeline
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

    ports:
      - "9876:9876"

    networks:
      - AnilistNetwork

networks:
  AnilistNetwork:
    driver: bridge
```

### Dockerfile (Optimized)
```dockerfile
# Multi-stage build for smaller image
FROM python:3.11-alpine AS BuildStage

WORKDIR /app

# Dependencies first (layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Then application code
COPY . .

# Final stage
FROM python:3.11-alpine

WORKDIR /app

# Binhex standard volumes
VOLUME ["/config", "/data"]

# Binhex standard environment variables with defaults
ENV PUID=99 \
    PGID=100 \
    UMASK=000 \
    TZ=UTC \
    DEBUG=false

# Copy only necessary files
COPY --from=BuildStage /app .

# Create standard directories
RUN mkdir -p /config /data

EXPOSE 9876

CMD ["python", "-m", "src.Main"]
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
```

---

## Initial Setup Commands

### 1. Create Project Structure
```bash
# Navigate to project
cd Anilist-Link

# Create source code structure
mkdir -p src/{Clients,Matching,Scanner,Sync,Web/Routes,Web/Templates,Web/Static,Database,Scheduler,Utils}

# Create test structure
mkdir -p tests/{Unit,Integration}

# Create other directories
mkdir -p scripts docs _resources/{Examples,Research,Assets,Notes}
mkdir -p .github/workflows

# Create __init__.py files for all Python packages
touch src/__init__.py
touch src/{Clients,Matching,Scanner,Sync,Web,Web/Routes,Database,Scheduler,Utils}/__init__.py
touch tests/__init__.py
touch tests/{Unit,Integration}/__init__.py
```

### 2. Set Up Documentation
```bash
# Documentation files should already exist in docs/
ls docs/

# Verify CLAUDE.md symlink
ls -la CLAUDE.md
# Output: CLAUDE.md -> docs/CLAUDE.md
```

### 3. Initialize Docker with Binhex Standards
```bash
# Create local volume directories for development
mkdir -p config data

# Set permissions (match PUID/PGID)
sudo chown -R $(id -u):$(id -g) config data
chmod -R 775 config data
```

### 4. Initialize Git
```bash
# Add all files (excluding _resources due to .gitignore)
git add .

# Initial commit
git commit -m "Initial project setup with Binhex standards and documentation structure"

# Verify _resources is ignored
git status
# Should not show _resources/
```

---

## Documentation File Templates

### README.md (Root Level)
```markdown
# Anilist-Link

A self-hosted Docker container that bridges AniList with Plex, Jellyfin, and Crunchyroll — syncing watch progress and providing AniList-powered metadata.

## Quick Start

```bash
docker-compose up -d
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and architecture
- [Developer Setup](docs/DEV-SETUP.md) - Development environment setup
- [Quick Reference](docs/QUICK-REFERENCE.md) - Best practices and common commands
- [Project Structure](docs/PROJECT-STRUCTURE.md) - Project organization reference

## License

[Your License]
```

### docs/ARCHITECTURE.md (Required)
```markdown
# Architecture Overview
[See docs/ARCHITECTURE.md for the complete filled-out version]

Minimum content:
- System Overview with component diagram
- Technology Stack with rationale
- Design Decisions (Context → Decision → Rationale → Consequences)
- Data Flow description
- Security Architecture
- Scalability Considerations
```

### docs/CLAUDE.md (Symlinked to Root)
```markdown
# CLAUDE.md - Anilist-Link
[See docs/CLAUDE.md for the complete filled-out version]

Minimum content:
- Project Overview and Key Features
- Claude Code Preferences (model, planning, testing)
- Technology Stack
- Project Structure with special directories
- Coding Conventions
- Common Commands
```

**Symlink Setup**:
```bash
# Create symlink so Claude auto-detects it
ln -s docs/CLAUDE.md CLAUDE.md
```

---

## File Naming Examples

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

## Using _resources/ Folder

### Example Workflow

**1. Research Phase**
```bash
# Store AniList API research
echo "# AniList GraphQL Queries" > _resources/Research/AnilistQueries.md

# Save example API responses
curl -X POST https://graphql.anilist.co \
  -H "Content-Type: application/json" \
  -d '{"query":"{ Media(id: 1) { title { romaji english } } }"}' \
  > _resources/Examples/AnilistMediaResponse.json
```

**2. Development Phase**
```bash
# Reference in code or with Claude
"Check the AniList API example in _resources/Examples/AnilistMediaResponse.json"

# Store Crunchyroll API findings
echo "# Crunchyroll API Endpoints" > _resources/Research/CrunchyrollApi.md
```

**3. Documentation Phase**
```bash
# Draft documentation in _resources
nano _resources/Notes/ApiDocDraft.md

# Once finalized, create in docs
mv _resources/Notes/ApiDocDraft.md docs/API.md
git add docs/API.md
```

---

## Verification Checklist

After setup, verify everything is correct:

### Project Structure
- [ ] `docs/` folder exists with ARCHITECTURE.md, CLAUDE.md, DEV-SETUP.md, QUICK-REFERENCE.md
- [ ] `_resources/` folder exists with Examples/, Research/, Assets/, Notes/
- [ ] CLAUDE.md symlink in root points to docs/CLAUDE.md
- [ ] README.md in root (not in docs)
- [ ] All `src/` subdirectories have `__init__.py` files

### Git Configuration
- [ ] `.gitignore` includes `_resources/`
- [ ] `git status` does not show _resources/
- [ ] Symlink is committed: `git ls-files | grep CLAUDE.md`

### Docker Configuration
- [ ] docker-compose.yml uses Binhex standard volumes (`/config`, `/data`)
- [ ] Environment variables include PUID, PGID, UMASK, TZ
- [ ] .dockerignore exists and excludes unnecessary files

### Documentation
- [ ] ARCHITECTURE.md has system overview with component diagram
- [ ] CLAUDE.md configured with project-specific details
- [ ] README.md links to docs/ files

### Naming Conventions
- [ ] All Python source files use PascalCase (e.g., `AnilistClient.py`)
- [ ] All directories use PascalCase (e.g., `Clients/`, `Matching/`)
- [ ] Environment variables use UPPER_SNAKE_CASE
- [ ] Python variables and functions use snake_case (PEP 8)
- [ ] Python classes use PascalCase

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
ruff check src/                                   # Lint
black src/                                        # Format
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

# Check PUID/PGID
id -u                                             # Your PUID
id -g                                             # Your PGID
docker exec AnilistLink id                        # Container's PUID/PGID
```

---

## Additional Resources

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture and design
- [CLAUDE.md](CLAUDE.md) - Claude Code configuration
- [DEV-SETUP.md](DEV-SETUP.md) - Detailed developer setup guide
- [QUICK-REFERENCE.md](QUICK-REFERENCE.md) - Quick reference and best practices

---

**Last Updated**: 2026-02-09
**Standards Version**: 1.0
