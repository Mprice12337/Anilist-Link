# Anilist-Link

A self-hosted Docker container that bridges AniList with Plex, Jellyfin, and Crunchyroll — syncing watch progress and providing AniList-powered metadata.

## Features

- Sync watch progress from Crunchyroll, Plex, and Jellyfin to AniList
- AniList-powered metadata provider for Plex and Jellyfin anime libraries
- Per-user AniList account linking via OAuth2
- Web dashboard for configuration, mapping review, and sync monitoring

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

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and component overview
- [Developer Setup](docs/DEV-SETUP.md) - Development environment setup
- [Quick Reference](docs/QUICK-REFERENCE.md) - Best practices and common commands
- [Project Structure](docs/PROJECT-STRUCTURE.md) - Project organization reference

## Related Projects

- [Crunchyroll-Anilist-Sync](https://github.com/Mprice12337/Crunchyroll-Anilist-Sync) - The predecessor project being merged into Anilist-Link
