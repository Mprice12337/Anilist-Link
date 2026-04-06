"""Path translation between different container mount points.

When Jellyfin/Plex and our app mount the same host directory at different
container paths (e.g. Jellyfin uses ``/data`` while we use ``/media``),
paths reported by the media server need to be translated before we can
read or write files at those locations.

Usage::

    translator = PathTranslator.build(
        service_locations=["/data/anime_test"],
        local_library_paths=["/media/anime_test"],
    )
    local_path = translator.translate("/data/anime_test/Show/ep.mkv")
    # → "/media/anime_test/Show/ep.mkv"
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PathTranslator:
    """Translates service-reported filesystem paths to locally accessible paths."""

    def __init__(self, mappings: list[tuple[str, str]]) -> None:
        # List of (service_prefix, local_prefix) pairs, longest first so more
        # specific paths take priority over shorter parent prefixes.
        self._mappings = sorted(
            [(s.rstrip("/"), loc.rstrip("/")) for s, loc in mappings],
            key=lambda p: len(p[0]),
            reverse=True,
        )

    @property
    def has_mappings(self) -> bool:
        return bool(self._mappings)

    def translate(self, path: str) -> str:
        """Return the locally accessible equivalent of *path*.

        If no mapping matches, returns the original path unchanged so callers
        can always use the return value without a None check.
        """
        for svc_prefix, local_prefix in self._mappings:
            if path == svc_prefix or path.startswith(svc_prefix + "/"):
                remainder = path[len(svc_prefix) :]
                return local_prefix + remainder
        return path

    @classmethod
    def build(
        cls,
        service_locations: list[str],
        local_library_paths: list[str],
    ) -> "PathTranslator":
        """Build a translator by matching service locations to local paths.

        Matching uses the longest common path-component suffix (right-to-left).
        For example ``/data/anime_test`` and ``/media/anime_test`` share the
        suffix ``anime_test``, yielding the mapping ``/data`` → ``/media``.
        """
        mappings: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for svc_loc in service_locations:
            pair = _find_best_match(svc_loc, local_library_paths)
            if pair and pair not in seen:
                mappings.append(pair)
                seen.add(pair)
                logger.info(
                    "PathTranslator: mapped service prefix '%s' → local prefix '%s'",
                    pair[0],
                    pair[1],
                )
        if not mappings:
            logger.debug(
                "PathTranslator: no path mappings found " "(service=%s, local=%s)",
                service_locations,
                local_library_paths,
            )
        return cls(mappings)

    @classmethod
    def identity(cls) -> "PathTranslator":
        """Return a no-op translator that leaves all paths unchanged."""
        return cls([])


def _find_best_match(
    svc_loc: str,
    local_paths: list[str],
) -> tuple[str, str] | None:
    """Return (service_prefix, local_prefix) for the best-matching local path.

    "Best" means the greatest number of matching path components counted from
    the right (leaf side).  Returns None if no components match.
    """
    svc_parts = _split_path(svc_loc)

    best_suffix_len = 0
    best_pair: tuple[str, str] | None = None

    for local_path in local_paths:
        local_parts = _split_path(local_path)

        # Count matching components from the right
        suffix_len = 0
        for i in range(1, min(len(svc_parts), len(local_parts)) + 1):
            if svc_parts[-i].lower() == local_parts[-i].lower():
                suffix_len = i
            else:
                break

        if suffix_len > best_suffix_len:
            best_suffix_len = suffix_len
            svc_prefix = _rebuild_prefix(svc_parts, suffix_len)
            local_prefix = _rebuild_prefix(local_parts, suffix_len)
            # Only register a mapping when the prefixes actually differ —
            # identical prefixes mean both containers use the same path.
            if svc_prefix != local_prefix:
                best_pair = (svc_prefix, local_prefix)

    return best_pair if best_suffix_len > 0 and best_pair else None


def _split_path(path: str) -> list[str]:
    """Split a POSIX-style path into non-empty components."""
    return [p for p in path.replace("\\", "/").strip("/").split("/") if p]


def _rebuild_prefix(parts: list[str], suffix_len: int) -> str:
    """Return the path prefix (everything before the last *suffix_len* components)."""
    prefix_parts = parts[:-suffix_len] if suffix_len < len(parts) else []
    return "/" + "/".join(prefix_parts) if prefix_parts else "/"
