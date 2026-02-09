# Quick Reference: Best Practices

> One-page guide to Git workflow, Claude Code usage, and Docker optimization for Anilist-Link

---

## Git Workflow Best Practices

### DO: Use `git pull --rebase`
```bash
# Set up alias for convenience
git config --global alias.pr 'pull --rebase'

# Use it when your push is rejected
git pr
# or
git pull --rebase
```

**Why**: Maintains linear commit history, avoids messy merge commits

### DO: Handle rebase conflicts properly
```bash
# If conflicts occur during rebase, you can:

# Option 1: Abort and use regular merge
git rebase --abort
git pull  # Creates merge commit but easier to resolve

# Option 2: Fix conflicts during rebase
# [fix conflicts in files]
git add .
git rebase --continue
```

### DON'T: Use `git pull` alone
Avoid `git pull` by itself when remote is ahead - it creates unnecessary merge commits

---

## Claude Code: Essential Commands

### Bash Mode
```bash
# Run any bash command directly
"run pytest"
"check the logs in /config"
```

### Model Switching
```bash
/model opus    # Powerful, for complex tasks
/model sonnet  # Fast and efficient, for daily work
```

### Auto-Accept Mode
```bash
/auto-accept on   # Claude makes changes without prompting
/auto-accept off  # Review each change
```

### Interrupt Claude
Press `ESC` to interrupt and redirect Claude's actions

### Documentation
```bash
"Explore the app architecture and update ARCHITECTURE.md"
```

---

## Claude Code: Workflow by Level

### Level 1: Beginner

**Essential Setup**:
- Install Claude Code (local or remote)
- Verify `CLAUDE.md` exists for project memory
- Use to-do lists for task tracking

**Basic Commands**:
```bash
"Create a to-do list for adding Jellyfin webhook support"
"Write unit tests for the TitleMatcher class"
"Debug this AniList API error [paste error]"
```

**Best Practices**:
- Use markdown files for long prompts (reference with `@filename.md`)
- Let Claude generate and maintain `CLAUDE.md`
- Add tasks to message queue while Claude works

---

### Level 2: Intermediate

**Planning & Strategy**:
```bash
# Use planning mode
/plan "How should we implement the Plex metadata scanner?"

# Control thinking depth
"think about the best approach for title matching"
"think hard about edge cases in season mapping"
"ultra think about OAuth2 token refresh flow"
```

**Beyond Code**:
- Research: "Research AniList GraphQL API and create integration plan"
- Documents: "Generate API documentation for the mapping endpoints"
- Changelogs: "Update CHANGELOG.md with recent changes"

**GitHub Integration**:
- Install GitHub Actions integration
- Tag issues with `@claude` for automatic fixing

**Mindset Shift**:
- Think like a PM: Give context and constraints
- Verify at high level (app works, tests pass)
- Not line-by-line code review

---

### Level 3: Master

**Parallel Work**:
```bash
# Multiple plans simultaneously
/subagents parallel "Explore 3 approaches to rate limiting"

# Multi-Claude with Git worktrees
git worktree add ../feature-plex feature/plex-integration
git worktree add ../feature-jellyfin feature/jellyfin-integration
# Run separate Claude instances in each
```

**MCP Servers**:
- Playwright MCP: Browser automation for testing the web dashboard

---

## Docker: Binhex Standardization

### Standard Volume Structure
All containers follow consistent paths:
```yaml
volumes:
  - /host/path/appdata/AnilistLink:/config    # Config, database, logs
  - /host/path/data:/data                      # Application data
```

### Standard Environment Variables
```yaml
environment:
  # User/Group Management (prevents permission issues)
  - PUID=1000              # Your user ID: id -u
  - PGID=1000              # Your group ID: id -g
  - UMASK=002              # File permissions (002 = group writable)

  # System Configuration
  - TZ=America/New_York    # Timezone
  - DEBUG=false            # Enable debug logging

  # Application-Specific
  - PLEX_URL=http://192.168.1.100:32400
  - PLEX_TOKEN=your-plex-token
  - JELLYFIN_URL=http://192.168.1.100:8096
  - JELLYFIN_API_KEY=your-jellyfin-api-key
  - ANILIST_CLIENT_ID=your-client-id
  - ANILIST_CLIENT_SECRET=your-client-secret
```

**UMASK Values:**
- `000` = 777/666 (most permissive)
- `002` = 775/664 (recommended - group writable)
- `022` = 755/644 (user only)

### Standard Logging
- **All logs**: `/config/supervisord.log`
- **App logs**: `/config/anilist_link.log`
- **View logs**: `docker exec AnilistLink cat /config/supervisord.log`
- Supervisord manages all container processes

### Quick Setup
```bash
# Find your PUID/PGID
id -u    # Returns PUID (e.g., 1000)
id -g    # Returns PGID (e.g., 1000)

# Docker run with Binhex standards
docker run -d \
  --name AnilistLink \
  -v $(pwd)/config:/config \
  -v $(pwd)/data:/data \
  -e PUID=$(id -u) \
  -e PGID=$(id -g) \
  -e UMASK=002 \
  -e TZ=America/New_York \
  -p 9876:9876 \
  anilist-link

# Docker Compose
docker-compose up -d
```

---

## Docker Optimization Checklist

### 1. Minimal Base Image
```dockerfile
# Good
FROM python:3.11-alpine

# Avoid
FROM python:latest
FROM ubuntu:latest
```

### 2. Layer Caching
```dockerfile
# Good - dependencies first (change less often)
COPY pyproject.toml ./
RUN pip install .
COPY . .

# Avoid - code copied before dependencies
COPY . .
RUN pip install .
```

### 3. .dockerignore File
```
.git
.venv
__pycache__
*.pyc
.env
*.log
coverage
.DS_Store
.idea
_resources
tests
docs
```

### 4. Combined RUN Commands
```dockerfile
# Good - single layer, cleanup included
RUN apk add --no-cache gcc musl-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del gcc musl-dev

# Avoid - multiple layers
RUN apk add gcc musl-dev
RUN pip install -r requirements.txt
RUN apk del gcc musl-dev
```

### 5. Multi-Stage Builds
```dockerfile
# Build stage
FROM python:3.11-alpine AS build
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .

# Production stage
FROM python:3.11-alpine
WORKDIR /app
COPY --from=build /app .
CMD ["python", "-m", "src.Main"]
```

---

## Testing Best Practices

### Running Tests
```bash
# All tests
pytest

# Unit tests only
pytest tests/Unit/

# Integration tests only
pytest tests/Integration/

# With coverage
pytest --cov=src --cov-report=html

# Stop on first failure
pytest -x

# Verbose output
pytest -v
```

### Test Generation with Claude
```bash
"Write unit tests for the AnilistClient class"
"Generate integration tests for the metadata scanner pipeline"
"Add test coverage for title matching edge cases"
```

### Debugging with Tests
```bash
"Write a test that reproduces this AniList rate limiting bug"
"Add test coverage for the OAuth2 token refresh flow"
```

---

## Common Claude Code Prompts for Anilist-Link

### Architecture & Planning
```
"Explain how the title matching engine works"
"Create a technical design doc for the Jellyfin integration"
"What's the best approach to implement season-to-AniList-ID mapping?"
"think hard about the architecture before implementing"
```

### Code Generation
```
"Implement the Plex webhook handler with tests"
"Add the metadata scanner pipeline for Jellyfin"
"Create the AniList OAuth2 flow endpoints"
"Implement rate limiting for the AniList client"
```

### Debugging
```
"Debug this AniList API error: [paste error]"
"Why is the title matching returning low confidence scores?"
"Add logging to help debug the Plex sync issue"
```

### Documentation
```
"Generate API documentation for the dashboard endpoints"
"Update ARCHITECTURE.md with the new Jellyfin client"
"Document the manual override workflow"
```

---

## Project Organization

### Folder Structure
```
├── README.md              # Main project docs (root only)
├── CLAUDE.md              # Symlink to docs/CLAUDE.md
├── _resources/            # NOT in git - dev references
│   ├── Examples/          # API response samples
│   ├── Research/          # Crunchyroll API notes, comparisons
│   ├── Assets/            # Design mockups, diagrams
│   └── Notes/             # Dev notes, scratchpad
└── docs/                  # All other documentation
    ├── ARCHITECTURE.md    # Required - system design
    ├── CLAUDE.md          # Actual file location
    ├── DEV-SETUP.md       # Developer setup guide
    ├── QUICK-REFERENCE.md # This file
    └── PROJECT-STRUCTURE.md # Project structure reference
```

### Documentation Rules
1. **README.md stays in root** - Main project overview only
2. **All other docs in `/docs`** - Keeps root clean
3. **ARCHITECTURE.md required** - Document system design from start
4. **CLAUDE.md in docs/** - Symlinked to root for auto-detection
5. **`_resources/` NOT in git** - Add to .gitignore

---

## Naming Conventions

### Code Naming (Python with PascalCase Files)
- **Files**: `PascalCase` (e.g., `AnilistClient.py`, `TitleMatcher.py`)
- **Directories**: `PascalCase` (e.g., `Clients/`, `Matching/`, `Scanner/`)
- **Variables**: `snake_case` (e.g., `user_data`, `match_score`) — PEP 8 standard
- **Functions**: `snake_case` (e.g., `get_user_by_id`, `match_title`) — PEP 8 standard
- **Classes**: `PascalCase` (e.g., `AnilistClient`, `TitleMatcher`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `MAX_RETRIES`, `API_BASE_URL`)

### Environment Variables
Always use `UPPER_SNAKE_CASE`:
- `PUID`, `PGID`, `UMASK`, `TZ` (Binhex standards)
- `PLEX_URL`, `PLEX_TOKEN`, `JELLYFIN_URL`, `JELLYFIN_API_KEY`
- `ANILIST_CLIENT_ID`, `ANILIST_CLIENT_SECRET`
- `DEBUG`

### Docker Names
- **Images**: `lowercase-with-dashes` (e.g., `anilist-link:latest`)
- **Containers**: `PascalCase` (e.g., `AnilistLink`)
- **Networks**: `PascalCase` (e.g., `AnilistNetwork`)

---

## Configuration Files Priority

Create these files/folders for optimal Claude Code experience:

1. **docs/CLAUDE.md** (Required)
   - Project memory and rules
   - Symlink to root for Claude auto-detection

2. **docs/ARCHITECTURE.md** (Required)
   - System architecture documentation
   - Design decisions and rationale

3. **_resources/** (Recommended)
   - Development reference materials
   - NOT in git (add to .gitignore)

4. **.dockerignore** (Required for Docker)
   - Reduces build context
   - Speeds up builds

5. **.gitignore**
   - Must include `_resources/`
   - Exclude generated files

---

## Troubleshooting

### Claude doesn't follow project conventions
- Add explicit rules to `CLAUDE.md` with examples

### Tests keep failing
- Verify `pytest-asyncio` is installed for async test support
- Check that test fixtures match current database schema

### Code quality issues
- Run `ruff check --fix src/` to auto-fix common issues
- Run `mypy src/` for type errors

### Git history getting messy
- Configure `git pull --rebase` as default
- Use `git pr` alias consistently

### Docker builds are slow
- Review layer caching order (dependencies before code)
- Add comprehensive `.dockerignore`
- Implement multi-stage builds

### Docker permission errors
- Set `PUID` and `PGID` to match your user: `id -u` and `id -g`
- Use `UMASK=002` for shared group access
- Check ownership: `docker exec AnilistLink ls -la /config`

### AniList rate limiting (429 errors)
- Check that the rate limiter in `AnilistClient.py` is active
- Reduce scan frequency in scheduler configuration
- Use cached responses where possible

### Crunchyroll API breaking changes
- Check `_resources/Research/CrunchyrollApi.md` for latest notes
- Test authentication flow manually
- Compare against Crunchyroll-Anilist-Sync reference implementation

---

## Quick Wins

1. **Set up git alias**: `git config --global alias.pr 'pull --rebase'`
2. **Create docs structure**: `mkdir -p docs _resources/{Examples,Research,Assets,Notes}`
3. **Verify ARCHITECTURE.md**: Ensure system design is documented
4. **Set up _resources/**: Add to .gitignore, store API samples
5. **Verify CLAUDE.md symlink**: `ls -la CLAUDE.md` → `docs/CLAUDE.md`
6. **Implement Binhex Docker standards**: Use `/config`, `/data` + `PUID`/`PGID`
7. **Enable auto-accept**: Speed up workflow once confident
8. **Use planning mode**: For complex features like new platform integrations
9. **Add .dockerignore**: Instant build time improvement
10. **Multi-stage Docker**: Smaller images for production
11. **Set UMASK=002**: Prevent permission issues
12. **Check supervisord.log**: First place to look when debugging containers

---

## Resources

- Claude Code Docs: https://docs.claude.com/en/docs/claude-code
- AniList API Docs: https://anilist.gitbook.io/anilist-apiv2-docs
- Plex API Wiki: https://github.com/Arcanemagus/plex-api/wiki
- Jellyfin API Docs: https://api.jellyfin.org/
- rapidfuzz Docs: https://rapidfuzz.github.io/RapidFuzz/
- Existing Crunchyroll Sync: https://github.com/Mprice12337/Crunchyroll-Anilist-Sync

---

**Pro Tip**: The most important thing is your `CLAUDE.md` file. Invest time in making it comprehensive, and Claude will work much more effectively with your codebase.
