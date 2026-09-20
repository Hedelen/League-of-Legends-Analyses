"""Fixed OP.GG Kayle leaderboard snapshot used only to discover Riot IDs."""

from __future__ import annotations

from dataclasses import dataclass


SOURCE_NAME = "opgg_kayle_top50_2026_09_20"


@dataclass(frozen=True)
class PublicCandidate:
    game_name: str
    tag_line: str
    source_position: int
    source: str = SOURCE_NAME


# Manually transcribed from the user-provided OP.GG NA Kayle leaderboard
# screenshots on 2026-09-20. Only the Riot ID and leaderboard position are
# retained. OP.GG rank, games, win rate, and KDA are intentionally not used.
OPGG_KAYLE_TOP_50: tuple[tuple[str, str], ...] = (
    ("Saneryus", "TTV"),
    ("Holy Tetra 33 十", "Isho"),
    ("bear", "34753"),
    ("Kayzoq", "NA1"),
    ("Saneryus", "TWTCH"),
    ("Stepmommy Kayle", "3636"),
    ("Perplexii", "4239"),
    ("Killer qm", "NA1"),
    ("Dynamikz21", "NA1"),
    ("Stevem", "Stvm"),
    ("Let Kayle Scale", "NA1"),
    ("IcarusFA", "Kayle"),
    ("Exälted", "2323"),
    ("MarktheWildBoar", "Hog"),
    ("Ascenndedd", "Lvl16"),
    ("ThePhilipino", "NA1"),
    ("4oureal", "7766"),
    ("SOMATIC", "KORR"),
    ("dwoplet", "pink"),
    ("relapsed", "1972"),
    ("never streSS", "IZAN"),
    ("You Lose 1v1", "NA1"),
    ("Draigoon", "slayr"),
    ("TDiddy", "5710"),
    ("Cubs", "AZ69"),
    ("Emma Moonstone", "NA1"),
    ("Assassin King", "Soma"),
    ("lana del rey fan", "5829"),
    ("Purt", "666"),
    ("Sod1umNaCl", "NA1"),
    ("XD sendrope", "sendr"),
    ("Blade Saint", "YWKM"),
    ("AylinNightsong", "BG3"),
    ("ttv sendrope", "sendr"),
    ("dTian", "NA1"),
    ("LANA DEL REY", "999"),
    ("Radicalstorm", "Rad"),
    ("Cadence", "cycle"),
    ("mawny", "NA1"),
    ("Wheezy", "1v9"),
    ("Joaquin1774", "5186"),
    ("brrdabree", "NA1"),
    ("Sanity", "Evil"),
    ("tttrends", "SubYT"),
    ("RealSchon", "11A"),
    ("Lueedith", "Kevin"),
    ("HwangYeji", "NA69"),
    ("Zefferson", "NA1"),
    ("Invincible246", "NA1"),
    ("zqmdfg", "IGM"),
)


def discover_kayle_candidates(limit: int | None = None) -> list[PublicCandidate]:
    """Return the fixed OP.GG snapshot; no public-site request is performed."""
    rows = [
        PublicCandidate(game_name, tag_line, position)
        for position, (game_name, tag_line) in enumerate(OPGG_KAYLE_TOP_50, 1)
    ]
    if limit is None:
        return rows
    if limit < 1:
        raise ValueError("Candidate limit must be at least 1")
    return rows[:limit]
