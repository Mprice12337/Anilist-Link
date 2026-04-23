#!/usr/bin/env python3
"""Test the overhauled TVMaze multi-title matching against known failures.

Run from the project root:
    python -m scripts.test_tvmaze_matching
"""

import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.Clients.TVMazeClient import TVMazeClient  # noqa: E402

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-5s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Each test case: (description, titles_to_try, expected_match_name_substring)
# Titles simulate what the scanner would pass: english, romaji, synonyms.
TEST_CASES = [
    (
        "AJIN: Demi-Human (English only used to score 40)",
        ["AJIN: Demi-Human", "Ajin", "Ajin: Demi-Human"],
        "Ajin",
    ),
    (
        "Oshi No Ko (brackets caused score 45)",
        ["Oshi No Ko", "[Oshi no Ko]", "Oshi no Ko"],
        "Oshi no Ko",
    ),
    (
        "Blast of Tempest (English vs Japanese name, score 52)",
        ["Blast of Tempest", "Zetsuen no Tempest", "Zetsuen no Tempest: Civilization Blaster"],
        "Tempest",
    ),
    (
        "Terror in Resonance (English vs Japanese, score 50)",
        ["Terror in Resonance", "Zankyou no Terror"],
        "Terror",
    ),
    (
        "Re:ZERO Season 2 Part 2 (no results then score 41)",
        [
            "Re:ZERO -Starting Life in Another World- Season 2 Part 2",
            "Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season Part 2",
            "Re:ZERO -Starting Life in Another World-",
            "Re:Zero kara Hajimeru Isekai Seikatsu",
        ],
        "Re:Zero",
    ),
]


async def main() -> None:
    passed = 0
    failed = 0

    async with TVMazeClient() as client:
        for desc, titles, expected_substr in TEST_CASES:
            print(f"\n{'='*70}")
            print(f"TEST: {desc}")
            print(f"  Titles: {titles}")

            result = await client.search_show_multi(titles)

            if result is None:
                print(f"  RESULT: None (no match)")
                print(f"  STATUS: FAIL - expected match containing '{expected_substr}'")
                failed += 1
            else:
                print(f"  RESULT: imdb={result['imdb_id']} tvdb={result['tvdb_id']} tvmaze={result['tvmaze_id']}")
                has_ids = any(result.get(k) for k in ("imdb_id", "tvdb_id", "tvmaze_id"))
                print(f"  STATUS: {'PASS' if has_ids else 'FAIL'} - got provider IDs: {has_ids}")
                if has_ids:
                    passed += 1
                else:
                    failed += 1

            # Small delay to respect rate limits
            await asyncio.sleep(0.6)

    print(f"\n{'='*70}")
    print(f"Results: {passed} passed, {failed} failed out of {len(TEST_CASES)}")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
