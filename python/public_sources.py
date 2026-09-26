"""Curated public Kayle leaderboard snapshot used only to discover Riot IDs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_FILE = PROJECT_ROOT / "data" / "kayle_candidates.csv"
SOURCE_NAME = "opgg_kayle_2026_09_26"


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


def discover_kayle_candidates(limit: int | None = None) -> list[PublicCandidate]:
    """Load the fixed candidate snapshot without requesting a public website."""
    if limit is not None and limit < 1:
        raise ValueError("Candidate limit must be at least 1")
    if not CANDIDATE_FILE.exists():
        raise RuntimeError(f"Kayle candidate file is missing: {CANDIDATE_FILE}")

    rows: list[PublicCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    with CANDIDATE_FILE.open(encoding="utf-8-sig", newline="") as handle:
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
        raise RuntimeError(f"Kayle candidate file is empty: {CANDIDATE_FILE}")
    return rows if limit is None else rows[:limit]
