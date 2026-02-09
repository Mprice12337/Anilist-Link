# Developer Setup Guide

> **Purpose**: This guide contains the setup tasks and configurations that human developers should complete before handing off work to Claude Code. Once this setup is complete, Claude can work more autonomously with your codebase.

---

## Table of Contents
1. [Initial Project Setup](#initial-project-setup)
2. [Git Configuration](#git-configuration)
3. [Development Environment](#development-environment)
4. [Claude Code Installation](#claude-code-installation)
5. [Creating Your CLAUDE.md](#creating-your-claudemd)
6. [Testing Setup](#testing-setup)
7. [CI/CD Configuration](#cicd-configuration)
8. [Optional: MCP Servers](#optional-mcp-servers)
9. [Handoff Checklist](#handoff-checklist)

---

## Initial Project Setup

### 1. Clone and Verify Repository
```bash
# Clone the repository
git clone https://github.com/Mprice12337/Anilist-Link.git
cd Anilist-Link

# Verify you're on the correct branch
git branch -a
```

### 2. Create Project Folders
```bash
# Create documentation folder
mkdir -p docs

# Create _resources folder (for development references, NOT in git)
mkdir -p _resources/{Examples,Research,Assets,Notes}

# Verify .gitignore includes _resources
grep "_resources/" .gitignore || echo "_resources/" >> .gitignore
```

**Folder Purposes**:
- **`docs/`**: All project documentation except README.md
- **`_resources/`**: Development reference materials (never committed)
  - `Examples/` - API response samples (AniList GraphQL, Plex, Jellyfin responses)
  - `Research/` - Crunchyroll API reverse-engineering notes, library comparisons
  - `Assets/` - Design mockups, UI wireframes, diagrams
  - `Notes/` - Meeting notes, brainstorming, scratchpad

### 3. Create Initial Documentation

#### Create ARCHITECTURE.md
```bash
# Architecture documentation should already exist
ls docs/ARCHITECTURE.md
```

#### Create/Move CLAUDE.md
```bash
# CLAUDE.md lives in docs/ with a symlink in root
ls docs/CLAUDE.md

# Verify symlink exists in root
ls -la CLAUDE.md
# Should show: CLAUDE.md -> docs/CLAUDE.md
```

### 4. Update .gitignore
The `.gitignore` should already include the following entries:
```
# Development resources (not for version control)
_resources/

# OS files
.DS_Store
Thumbs.db

# IDE
.vscode/
.idea/
*.swp
*.swo
```

### 5. Install Dependencies
```bash
# Create and activate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # macOS/Linux

# Install project dependencies
pip install -e ".[dev]"

# Or install from requirements
pip install -r requirements.txt
```

### 6. Environment Configuration
```bash
# Copy environment template
cp .env.example .env

# Edit .env with your local credentials
```

**Required Environment Variables**:
- [ ] `PLEX_URL` - Your Plex server URL (e.g., `http://192.168.1.100:32400`)
- [ ] `PLEX_TOKEN` - Your Plex authentication token
- [ ] `JELLYFIN_URL` - Your Jellyfin server URL (e.g., `http://192.168.1.100:8096`)
- [ ] `JELLYFIN_API_KEY` - Your Jellyfin API key
- [ ] `ANILIST_CLIENT_ID` - AniList OAuth2 app client ID (register at https://anilist.co/settings/developer)
- [ ] `ANILIST_CLIENT_SECRET` - AniList OAuth2 app client secret

### 7. Database Setup
```bash
# SQLite database auto-creates on first run
# No manual setup required — just ensure /config directory exists for Docker
# or the project directory is writable for local development

# For local development, the database will be created at:
# ./data/anilist_link.db
mkdir -p data
```

---

## Git Configuration

### 1. Set Up Git Aliases for Clean History

Add these aliases to maintain a linear commit history:

```bash
# Preferred: Use git pull --rebase by default
git config --global pull.rebase true

# Or set up custom aliases
git config --global alias.pr 'pull --rebase'
git config --global alias.sync 'pull --rebase origin main'
```

**Why**: This prevents merge commits and keeps history clean.

### 2. Configure Git User Info
```bash
git config user.name "Your Name"
git config user.email "your.email@example.com"
```

### 3. Set Up Git Hooks (Optional)
```bash
# Install pre-commit framework
pip install pre-commit
pre-commit install
```

### 4. Branch Protection (if you have access)
- [ ] Protect main branch
- [ ] Require CI checks to pass
- [ ] Enable status checks

---

## Development Environment

### 1. IDE/Editor Setup

**Recommended Extensions/Plugins**:
- [ ] Python language support (Pylance for VS Code, Python plugin for PyCharm)
- [ ] Ruff linter extension
- [ ] Docker support extension
- [ ] Git integration
- [ ] TOML support (for pyproject.toml)

### 2. Local Services

Anilist-Link requires access to at least one media server for full functionality:

```bash
# Option 1: Run locally with Python
source .venv/bin/activate
python -m src.Main

# Option 2: Run with Docker
docker-compose up -d
```

**Verify Services Are Running**:
- [ ] Application starts without errors
- [ ] Dashboard accessible at `http://localhost:9876`
- [ ] Plex server reachable (if configured)
- [ ] Jellyfin server reachable (if configured)

### 3. Build and Run
```bash
# Activate virtual environment
source .venv/bin/activate

# Run the application locally
python -m src.Main

# Or run with uvicorn for hot-reload during development
uvicorn src.Web.App:app --reload --host 0.0.0.0 --port 9876
```

**Verify Application Works**:
- [ ] Navigate to `http://localhost:9876`
- [ ] Dashboard loads successfully
- [ ] API docs available at `http://localhost:9876/docs`

---

## Claude Code Installation

### 1. Install Claude Code

**Mac/Linux**:
```bash
curl -fsSL https://cli.claude.ai/install.sh | sh
```

**Windows**: Follow instructions at [claude.ai/code](https://docs.claude.com/en/docs/claude-code)

### 2. Verify Installation
```bash
claude --version
```

### 3. Authenticate
```bash
claude auth login
```

### 4. Set Default Model (Optional)
```bash
# Use Sonnet for most tasks (cost-efficient)
claude config set model sonnet

# Or use Opus for complex work
claude config set model opus
```

### 5. Test Claude Code
```bash
# Navigate to your project
cd /path/to/Anilist-Link

# Start Claude Code
claude code

# Test with a simple command
# In Claude prompt: "Explain the project structure"
```

---

## Creating Your CLAUDE.md

### 1. Verify Setup

The CLAUDE.md should already exist in `/docs` with a symlink in the root:

```bash
# Verify actual file exists
cat docs/CLAUDE.md | head -5

# Verify symlink in root
ls -la CLAUDE.md
# Should show: CLAUDE.md -> docs/CLAUDE.md
```

**Why this structure?**
- All documentation (except README.md) lives in `/docs`
- Symlink in root allows Claude Code to auto-detect the file
- Keeps project root clean
- Makes documentation organization consistent

### 2. Customize as Needed

Review and update these sections in `docs/CLAUDE.md`:
- [ ] **Project Overview**: Verify description is accurate
- [ ] **Technology Stack**: Confirm all technologies are listed
- [ ] **Project Structure**: Update if directory layout changes
- [ ] **Claude Code Preferences**: Adjust workflow preferences
- [ ] **Testing Strategy**: Set testing expectations
- [ ] **Code Quality Standards**: Verify linting/formatting configuration

### 3. Let Claude Help

Once you have a basic CLAUDE.md, you can ask Claude to improve it:

```bash
# In Claude Code
"Review our CLAUDE.md file and suggest improvements based on the codebase"
```

### 4. Use `_resources/` for Development

The `_resources/` folder is your workspace for development materials that shouldn't be in git:

**Store Here**:
- AniList GraphQL response examples for testing
- Plex/Jellyfin API response samples
- Crunchyroll reverse-engineering research notes
- Design mockups for the web dashboard
- Meeting notes and brainstorming documents

**Example Usage**:
```bash
# Store example AniList API response
curl -X POST https://graphql.anilist.co \
  -H "Content-Type: application/json" \
  -d '{"query":"{ Media(id: 1) { title { romaji english } } }"}' \
  > _resources/Examples/AnilistMediaResponse.json

# Save research notes
echo "# Crunchyroll API Endpoints" > _resources/Research/CrunchyrollApi.md

# Store design assets
cp ~/Downloads/dashboard-mockup.png _resources/Assets/
```

**Important**:
- This folder is NEVER committed to git
- Claude can read from and write to this folder
- Developers can freely add/modify content

---

## Testing Setup

### 1. Verify Test Framework Works
```bash
# Activate virtual environment
source .venv/bin/activate

# Run all tests
pytest

# Should see results - even if some fail, framework should work
```

### 2. Configure Test Coverage
```bash
# Install coverage tools (should be in dev dependencies)
pip install pytest-cov pytest-asyncio

# Run tests with coverage
pytest --cov=src --cov-report=html

# View coverage report
open htmlcov/index.html  # macOS
```

### 3. Document Testing Preferences

In your `CLAUDE.md`, the following testing preferences are set:
- Write tests after implementation for new features
- Core matching and sync logic must have tests
- 70% minimum coverage goal on core logic
- Use pytest with pytest-asyncio for async tests

---

## CI/CD Configuration

### 1. Set Up GitHub Actions

Create `.github/workflows/CI.yml`:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Run linter
        run: ruff check src/
      - name: Run type checker
        run: mypy src/
      - name: Run tests
        run: pytest --cov=src
```

### 2. Verify CI Pipeline
- [ ] Push a test commit
- [ ] Verify CI runs on GitHub
- [ ] Check that tests execute
- [ ] Confirm status checks work

---

## Docker Optimization

### 1. Implement Binhex Standardization

All Docker containers follow the Binhex standard for consistency:

#### Standard Volume Structure
```yaml
volumes:
  - /mnt/user/appdata/AnilistLink:/config
  - /mnt/user/data:/data
```

**Volume Purposes:**
- `/config` - Configuration files, SQLite database, and logs (including supervisord.log)
- `/data` - Application data (reserved for future use)

#### Standard Environment Variables
```yaml
environment:
  # User/Group Management
  - PUID=1000                    # Your user ID (find with: id -u)
  - PGID=1000                    # Your group ID (find with: id -g)
  - UMASK=002                    # File permissions (002 recommended)

  # System Configuration
  - TZ=America/New_York          # Your timezone
  - DEBUG=false                  # Enable debug logging

  # Application-specific variables
  - PLEX_URL=http://192.168.1.100:32400
  - PLEX_TOKEN=your-plex-token
  - JELLYFIN_URL=http://192.168.1.100:8096
  - JELLYFIN_API_KEY=your-jellyfin-api-key
  - ANILIST_CLIENT_ID=your-client-id
  - ANILIST_CLIENT_SECRET=your-client-secret
```

#### Setting Up PUID/PGID
```bash
# Find your user and group IDs
id -u        # Returns your PUID (e.g., 1000)
id -g        # Returns your PGID (e.g., 1000)
```

**UMASK Values:**
- `000` - Full permissions (777 folders, 666 files) - most permissive
- `002` - Group writable (775 folders, 664 files) - **recommended**
- `022` - User writable only (755 folders, 644 files) - more restrictive

### 2. Test Docker Build
```bash
# Build image
docker build -t anilist-link .

# Check image size
docker images anilist-link

# Run container with Binhex standards
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

# Or use docker-compose
docker-compose up -d

# Check logs
docker logs AnilistLink

# Check detailed logs (Binhex standard)
docker exec AnilistLink cat /config/supervisord.log
```

---

## Optional: MCP Servers

MCP (Model Context Protocol) servers extend Claude's capabilities.

### Playwright MCP (for browser automation)
```bash
# Useful for testing the web dashboard
npm install -g @anthropic-ai/mcp-server-playwright

# Enable in Claude Code
claude config set mcp.playwright.enabled true
```

---

## Handoff Checklist

Before handing off work to Claude, ensure:

### Environment Setup
- [ ] Python 3.11+ installed
- [ ] Virtual environment created and dependencies installed
- [ ] Application starts successfully (`python -m src.Main`)
- [ ] Environment variables set (at minimum ANILIST_CLIENT_ID/SECRET)

### Git Configuration
- [ ] Git aliases configured (`git pr` for pull --rebase)
- [ ] Branch strategy documented (feature/*, bugfix/*, hotfix/*)
- [ ] Pre-commit hooks working (if used)

### Claude Code Ready
- [ ] Claude Code installed and authenticated
- [ ] `docs/CLAUDE.md` file exists and is customized
- [ ] Root `CLAUDE.md` symlink works
- [ ] Tested with simple prompt

### Documentation
- [ ] `docs/` folder created with all documentation files
- [ ] `_resources/` folder created (added to .gitignore)
- [ ] `docs/ARCHITECTURE.md` created with system overview
- [ ] `docs/CLAUDE.md` in docs/ with symlink in root
- [ ] `README.md` updated with project overview

### Optional Enhancements
- [ ] CI/CD pipeline configured (GitHub Actions)
- [ ] Docker build tested
- [ ] MCP servers installed (if needed)

---

## Testing Claude Code

### 1. Simple Test

Start Claude Code and try:
```
"Create a to-do list of tasks needed to implement the Plex metadata scanner"
```

Claude should generate a structured task list based on the project architecture.

### 2. Code Generation Test
```
"Write a simple unit test for the TitleMatcher class"
```

Verify Claude:
- Understands pytest as the test framework
- Follows the PascalCase file naming convention
- Generates runnable async-compatible tests
- Uses the project's directory structure (tests/Unit/)

### 3. Codebase Understanding Test
```
"Explain how the metadata scanning pipeline works in this application"
```

Claude should demonstrate understanding of the scan → match → cache → apply architecture.

### 4. If Issues Occur

**Claude seems confused about the project**:
- Add more detail to your CLAUDE.md
- Provide explicit examples of conventions
- Use planning mode: "think hard about the architecture"

**Claude makes syntax errors**:
- Verify Python version (3.11+) is documented in CLAUDE.md
- Ensure linting rules are documented
- Ask Claude to read the pyproject.toml

**Claude violates conventions**:
- Explicitly document the convention in CLAUDE.md
- Provide before/after examples
- Set it as a hard rule in "Coding Conventions" section

---

## Next Steps

Once setup is complete:

1. **Start Small**: Give Claude simple, well-defined tasks (e.g., "Create the AniList GraphQL query for searching by title")
2. **Iterate on CLAUDE.md**: Update it as you discover what Claude needs to know
3. **Use Planning Mode**: For complex tasks like implementing the full Plex integration
4. **Leverage Subagents**: For exploration or parallel work across multiple clients
5. **Enable Auto-Accept**: Once confident in Claude's output, to speed up workflows

### Resources

- [Claude Code Documentation](https://docs.claude.com/en/docs/claude-code)
- [Prompt Engineering Guide](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)
- [MCP Servers](https://github.com/anthropics/mcp-servers)
- Your `CLAUDE.md` file

---

## Common Issues and Solutions

### "Module not found" errors
- Ensure virtual environment is activated: `source .venv/bin/activate`
- Verify dependencies installed: `pip install -e ".[dev]"`
- Check Python version: `python --version` (must be 3.11+)

### Tests fail on first run
- Ensure test dependencies are installed: `pip install pytest pytest-asyncio pytest-cov`
- Verify data directory exists: `mkdir -p data`

### Claude Code authentication fails
- Run `claude auth logout` then `claude auth login`
- Check internet connection
- Verify Claude.ai account is active

### Git conflicts during pull --rebase
- Use `git rebase --abort` to undo
- Then use `git pull` (without --rebase) to merge
- Resolve conflicts in the merge commit
- Future pulls: continue using `git pull --rebase`

### Docker permission issues
- Set correct `PUID`/`PGID` environment variables matching your host user
- Find your IDs: `id -u` (PUID) and `id -g` (PGID)
- Use `UMASK=002` for shared group access
- Check container file ownership: `docker exec AnilistLink ls -la /config`
- Fix host permissions if needed: `sudo chown -R $(id -u):$(id -g) /path/to/volume`

### Docker container keeps restarting
- Check supervisord log: `docker exec AnilistLink cat /config/supervisord.log`
- View container logs: `docker logs AnilistLink`
- Verify volumes are properly mounted
- Ensure `PUID`/`PGID` have write access to `/config`
- Check if required environment variables are set

### AniList OAuth2 not working
- Verify `ANILIST_CLIENT_ID` and `ANILIST_CLIENT_SECRET` are set correctly
- Check redirect URI matches what's registered on AniList developer settings
- Ensure the application is accessible at the configured callback URL

---

## Maintenance

### Weekly Tasks
- [ ] Update dependencies: `pip install --upgrade -e ".[dev]"`
- [ ] Review and update CLAUDE.md if needed
- [ ] Check CI/CD pipeline is working

### Monthly Tasks
- [ ] Review test coverage: `pytest --cov=src`
- [ ] Update documentation
- [ ] Check for security updates via Dependabot
- [ ] Review Crunchyroll API compatibility (may break without notice)

---

**Questions or Issues?**

- Check the [Claude Code documentation](https://docs.claude.com)
- Ask Claude: "Help me troubleshoot my development environment"
- File issues at: https://github.com/Mprice12337/Anilist-Link/issues
