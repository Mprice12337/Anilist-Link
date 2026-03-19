# Anilist-Link

A self-hosted Docker container that bridges AniList with Plex, Jellyfin, and Crunchyroll — syncing watch progress and providing AniList-powered metadata.

## Features

- **File Organization** — Rename and restructure anime files using AniList series data (L1/L2/L3 restructure wizard)
- **Metadata** — AniList-powered metadata provider for Plex and Jellyfin anime libraries (titles, posters, summaries, genres, ratings)
- **Watch Sync** — Sync watch progress from Crunchyroll to AniList; Plex/Jellyfin sync planned
- **Download Management** — Add anime to Sonarr/Radarr with AniList alternative titles via Prowlarr
- Per-user AniList account linking via OAuth2
- Web dashboard for configuration, mapping review, sync monitoring, and onboarding

## Quick Start

```bash
docker-compose up -d
```

Then navigate to `http://localhost:9876` for the web dashboard.

## Configuration

Set the following environment variables in `docker-compose.yml`:

| Variable | Description |
|---|---|
| `PLEX_URL` | Plex server URL (e.g., `http://192.168.1.100:32400`) |
| `PLEX_TOKEN` | Plex authentication token |
| `JELLYFIN_URL` | Jellyfin server URL (e.g., `http://192.168.1.100:8096`) |
| `JELLYFIN_API_KEY` | Jellyfin API key |
| `ANILIST_CLIENT_ID` | AniList OAuth2 app client ID |
| `ANILIST_CLIENT_SECRET` | AniList OAuth2 app client secret |
| `SONARR_URL` | Sonarr server URL (optional, P4) |
| `SONARR_API_KEY` | Sonarr API key (optional, P4) |
| `RADARR_URL` | Radarr server URL (optional, P4) |
| `RADARR_API_KEY` | Radarr API key (optional, P4) |
| `PROWLARR_URL` | Prowlarr server URL (optional, P4) |
| `PROWLARR_API_KEY` | Prowlarr API key (optional, P4) |
| `QBITTORRENT_URL` | qBittorrent WebUI URL (optional, P4) |
| `QBITTORRENT_USER` | qBittorrent username (optional, P4) |
| `QBITTORRENT_PASS` | qBittorrent password (optional, P4) |

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and component overview
- [Developer Setup](docs/DEV-SETUP.md) - Development environment setup
- [Quick Reference](docs/QUICK-REFERENCE.md) - Best practices and common commands
- [Project Structure](docs/PROJECT-STRUCTURE.md) - Project organization reference

## Related Projects

- [Crunchyroll-Anilist-Sync](https://github.com/Mprice12337/Crunchyroll-Anilist-Sync) - The predecessor project being merged into Anilist-Link
