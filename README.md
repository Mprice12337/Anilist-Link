# Anilist-Link

A self-hosted Docker container that bridges AniList with Plex, Jellyfin, and Crunchyroll — syncing watch progress and providing AniList-powered metadata.

## Features

- **File Organization** — Rename and restructure anime files using AniList series data (L1/L2/L3 restructure wizard)
- **Metadata** — AniList-powered metadata provider for Plex and Jellyfin anime libraries (titles, posters, summaries, genres, ratings)
- **Watch Sync** — Sync watch progress from Crunchyroll to AniList; Plex/Jellyfin sync planned
- **Download Management** — Add anime to Sonarr/Radarr with AniList alternative titles
- Per-user AniList account linking via OAuth2
- Web dashboard for configuration, mapping review, sync monitoring, and onboarding

## Quick Start

```bash
docker pull dogberttech/anilist-link:latest
docker compose up -d
```

Then navigate to `http://localhost:9876` for the web dashboard.

---

## Docker Deployment

### Docker Compose

```yaml
services:
  AnilistLink:
    image: dogberttech/anilist-link:latest
    container_name: AnilistLink
    restart: unless-stopped
    shm_size: "2g"
    volumes:
      - /mnt/user/appdata/AnilistLink:/config
      - /mnt/user/media/anime:/media/anime    # mount to the same path your media server uses
    environment:
      # User/Group Management
      - PUID=99
      - PGID=100
      - UMASK=002
      # System
      - TZ=America/New_York
      - DEBUG=false
      # All other settings (AniList, Plex, Jellyfin, Crunchyroll, Sonarr, Radarr)
      # are configured via the onboarding wizard at http://localhost:9876
    ports:
      - "9876:9876"
```

### Volumes

| Container Path | Description | Example Host Path |
|---|---|---|
| `/config` | Config, SQLite database (`anilist_link.db`), and app logs (`anilist_link.log`) | `/mnt/user/appdata/AnilistLink` |
| `/media/anime` | Your anime library — must be writable; used for all rename/restructure operations | `/mnt/user/media/anime` |

> The media volume name is flexible — use any container path that matches how Plex/Jellyfin mount the same files. See **Media Path Alignment** below.

### Port

| Port | Protocol | Description |
|---|---|---|
| `9876` | TCP | Web dashboard |

### Environment Variables

Service credentials (AniList, Plex, Jellyfin, Crunchyroll, Sonarr, Radarr) are configured through the onboarding wizard at `http://localhost:9876` — no env vars needed for those. Only system-level vars need to be set at container start.

| Variable | Default | Description |
|---|---|---|
| `PUID` | `99` | User ID for file ownership (`99` = Unraid `nobody`) |
| `PGID` | `100` | Group ID for file ownership (`100` = Unraid `users`) |
| `UMASK` | `002` | File creation mask |
| `TZ` | `UTC` | Timezone (e.g., `America/New_York`) |
| `DEBUG` | `false` | Enable verbose debug logging |

### Media Path Alignment

The file restructure and rename features read file paths from Plex or Jellyfin and move those files on disk. For this to work, **mount your anime library to the same container path in Anilist-Link as you use in Plex/Jellyfin**:

```
Host:          /mnt/user/media/anime
Plex:          /mnt/user/media/anime → /media/anime
Jellyfin:      /mnt/user/media/anime → /media/anime
Anilist-Link:  /mnt/user/media/anime → /media/anime   ← must match
```

If you have an existing setup where your media server uses a different internal path than Anilist-Link (e.g., Plex reports `/data/anime` but your container mounts it at `/media/anime`), you can configure path prefix translation under **Settings → Library Restructuring** in the web dashboard.

### Notes

- **`shm_size: "2g"`** — Required for Chromium (used by the Crunchyroll client). Reduce to `512m` or remove if not using Crunchyroll.
- **AniList OAuth2** — Register your app at [anilist.co/settings/developer](https://anilist.co/settings/developer) to obtain `ANILIST_CLIENT_ID` and `ANILIST_CLIENT_SECRET`.
- **Logs** — View with `docker logs AnilistLink` or tail the file at `/config/logs/anilist_link.log` inside the container.
- **Permissions** — `PUID`/`PGID` must match the owner of your media files on the host, otherwise renames will fail with permission errors.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and component overview
- [Developer Setup](docs/DEV-SETUP.md) - Development environment setup
- [Quick Reference](docs/QUICK-REFERENCE.md) - Best practices and common commands
- [Project Structure](docs/PROJECT-STRUCTURE.md) - Project organization reference

## Related Projects

- [Crunchyroll-Anilist-Sync](https://github.com/Mprice12337/Crunchyroll-Anilist-Sync) - The predecessor project being merged into Anilist-Link
