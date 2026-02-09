#!/usr/bin/env bash
# Anilist-Link initial setup script
set -euo pipefail

echo "Setting up Anilist-Link development environment..."

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    python3.11 -m venv .venv
    echo "Virtual environment created."
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Create data directory for local SQLite database
mkdir -p data

echo "Setup complete. Activate your environment with: source .venv/bin/activate"
