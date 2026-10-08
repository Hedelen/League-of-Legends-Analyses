"""Curated champion leaderboard snapshots used only to discover Riot IDs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from champions import ChampionSpec, champion_spec


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAME = "opgg_kayle_2026_09_26"  # Backward-compatible Kayle export.


@dataclass(frozen=True)
class PublicCandidate:
    game_name: str
    tag_line: str
    source_position: int
    platform: str = "na1"
    routing: str = "americas"
    source_region: str = "na"
    source_snapshot: str = SOURCE_NAME

    @property
    def source(self) -> str:
        return f"{self.source_snapshot}:{self.source_region}"


def discover_candidates(
    champion: str | ChampionSpec = "kayle", limit: int | None = None
) -> list[PublicCandidate]:
    """Load the fixed candidate snapshot without requesting a public website."""
    spec = champion_spec(champion)
    candidate_file = PROJECT_ROOT / "data" / spec.candidate_file
    if limit is not None and limit < 1:
        raise ValueError("Candidate limit must be at least 1")
    if not candidate_file.exists():
        raise RuntimeError(f"{spec.name} candidate file is missing: {candidate_file}")

    rows: list[PublicCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    with candidate_file.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            candidate = PublicCandidate(
                game_name=raw["game_name"].strip(),
                tag_line=raw["tag_line"].strip(),
                source_position=int(raw["source_position"]),
                platform=raw["platform"].strip().lower(),
                routing=raw["routing"].strip().lower(),
                source_region=raw["source_region"].strip().lower(),
                source_snapshot=raw["source_snapshot"].strip(),
            )
            key = (
                candidate.platform,
                candidate.game_name.casefold(),
                candidate.tag_line.casefold(),
            )
            if key in seen:
                raise RuntimeError(
                    "Duplicate Riot ID in candidate snapshot: "
                    f"{candidate.game_name}#{candidate.tag_line} ({candidate.platform})"
                )
            seen.add(key)
            rows.append(candidate)

    if not rows:
        raise RuntimeError(f"{spec.name} candidate file is empty: {candidate_file}")
    return rows if limit is None else rows[:limit]


def discover_kayle_candidates(limit: int | None = None) -> list[PublicCandidate]:
    """Compatibility wrapper for existing imports and tests."""
    return discover_candidates("kayle", limit)
