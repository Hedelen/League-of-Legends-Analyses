"""Supported target champions and their fixed public candidate snapshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChampionSpec:
    key: str
    champion_id: int
    name: str
    candidate_file: str
    source_name: str


CHAMPIONS: dict[str, ChampionSpec] = {
    "kayle": ChampionSpec(
        "kayle", 10, "Kayle", "kayle_candidates.csv", "opgg_kayle_2026_09_26"
    ),
    "urgot": ChampionSpec(
        "urgot", 6, "Urgot", "urgot_candidates.csv", "opgg_urgot_2026_10_08"
    ),
    "mordekaiser": ChampionSpec(
        "mordekaiser",
        82,
        "Mordekaiser",
        "mordekaiser_candidates.csv",
        "opgg_mordekaiser_2026_10_08",
    ),
}

ALIASES = {"morde": "mordekaiser", "mord": "mordekaiser"}


def champion_spec(value: str | ChampionSpec | None = None) -> ChampionSpec:
    if isinstance(value, ChampionSpec):
        return value
    key = (value or "kayle").strip().lower()
    key = ALIASES.get(key, key)
    try:
        return CHAMPIONS[key]
    except KeyError as exc:
        choices = ", ".join(CHAMPIONS)
        raise ValueError(f"Unsupported champion {value!r}; choose one of: {choices}") from exc

