"""Quick test: build series groups for Demon Slayer and Attack on Titan."""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.Clients.AnilistClient import AniListClient
from src.Database.Connection import DatabaseManager
from src.Scanner.SeriesGroupBuilder import SeriesGroupBuilder
from src.Utils.Config import load_config, load_config_from_db_settings


async def main() -> None:
    load_dotenv()
    config = load_config()
    db = DatabaseManager(config.database.path)
    await db.initialize()

    # Rebuild config with DB settings
    db_settings = await db.get_all_settings()
    config = load_config_from_db_settings(db_settings)

    anilist = AniListClient(
        client_id=config.anilist.client_id,
        client_secret=config.anilist.client_secret,
    )

    builder = SeriesGroupBuilder(db, anilist, max_age_hours=0)  # force fresh

    # AniList IDs:
    #   Demon Slayer (Kimetsu no Yaiba) S1 = 101922
    #   Attack on Titan (Shingeki no Kyojin) S1 = 16498
    test_cases = [
        ("Demon Slayer", 101922),
        ("Attack on Titan", 16498),
    ]

    for name, anilist_id in test_cases:
        print(f"\n{'='*60}")
        print(f"  {name}  (starting from AniList ID {anilist_id})")
        print(f"{'='*60}")

        group_id, entries = await builder.get_or_build_group(anilist_id)
        print(f"  Group ID: {group_id}")
        print(f"  Total entries: {len(entries)}")

        # Show all entries
        print(f"\n  All entries in group:")
        for e in entries:
            eps = e.get("episodes") or "?"
            fmt = e.get("format") or "?"
            print(
                f"    S{e['season_order']:>2}  "
                f"[{fmt:<8}]  "
                f"{eps:>3} eps  "
                f"{e.get('start_date', ''):>10}  "
                f"AniList {e['anilist_id']:<8}  "
                f"{e['display_title']}"
            )

        # Show TV-only entries (what gets mapped to Plex seasons)
        tv_entries = [
            e for e in entries if e.get("format", "") in ("TV", "TV_SHORT")
        ]
        print(f"\n  TV-only entries (used for Plex season mapping):")
        for i, e in enumerate(tv_entries, start=1):
            eps = e.get("episodes") or "?"
            print(
                f"    Plex Season {i} -> "
                f"{eps:>3} eps  "
                f"AniList {e['anilist_id']:<8}  "
                f"{e['display_title']}"
            )

    await anilist.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
