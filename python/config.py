"""Environment-backed settings. Normal changes never require editing Python."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _accounts(raw: str) -> tuple[tuple[str, str], ...]:
    parsed: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "#" not in item:
            raise ValueError(f"SELF_ACCOUNTS entry must look like GameName#TagLine: {item!r}")
        game_name, tag_line = item.rsplit("#", 1)
        parsed.append((game_name.strip(), tag_line.strip()))
    if not parsed:
        raise ValueError("SELF_ACCOUNTS must contain at least one Riot ID")
    return tuple(parsed)


@dataclass(frozen=True)
class Settings:
    api_key: str
    postgres_dsn: str
    platform: str
    routing: str
    self_accounts: tuple[tuple[str, str], ...]
    reference_candidate_limit: int
    target_reference_players: int
    min_kayle_mastery_points: int
    min_account_experience_games: int
    min_reference_kayle_games: int
    reference_matches_to_screen: int
    reference_use_patch_window: bool
    reference_allow_high_diamond: bool
    self_max_matches: int
    target_reference_player_games: int
    reference_games_per_player: int
    very_short_game_seconds: int
    http_timeout_seconds: int
    max_http_retries: int
    target_champion_key: str = "kayle"


def get_settings(require_api_key: bool = True) -> Settings:
    api_key = os.getenv("RIOT_API_KEY", "").strip()
    if require_api_key and (not api_key or api_key.startswith("RGAPI-your")):
        raise RuntimeError(
            "RIOT_API_KEY is missing. Copy .env.example to .env and add a current key; "
            "do not paste the key into Python or ChatGPT."
        )
    dsn = os.getenv(
        "POSTGRES_DSN",
        "postgresql://postgres:your_password@localhost:5432/kayle_analysis",
    ).strip()
    if "/kayle_analysis" not in dsn.split("?", 1)[0]:
        raise RuntimeError(
            "Safety stop: POSTGRES_DSN must point to the separate kayle_analysis database."
        )
    return Settings(
        api_key=api_key,
        postgres_dsn=dsn,
        platform=os.getenv("RIOT_PLATFORM", "na1").strip().lower(),
        routing=os.getenv("RIOT_ROUTING", "americas").strip().lower(),
        self_accounts=_accounts(
            os.getenv("SELF_ACCOUNTS", "YourGameName#YourTag")
        ),
        reference_candidate_limit=_int("REFERENCE_CANDIDATE_LIMIT", 150, 1),
        target_reference_players=_int("TARGET_REFERENCE_PLAYERS", 150, 1),
        min_kayle_mastery_points=_int("MIN_KAYLE_MASTERY_POINTS", 0),
        min_account_experience_games=_int("MIN_ACCOUNT_EXPERIENCE_GAMES", 0),
        min_reference_kayle_games=_int("MIN_REFERENCE_KAYLE_GAMES", 1, 1),
        reference_matches_to_screen=_int("REFERENCE_MATCHES_TO_SCREEN", 500, 1),
        reference_use_patch_window=_bool("REFERENCE_USE_PATCH_WINDOW", False),
        reference_allow_high_diamond=_bool("REFERENCE_ALLOW_HIGH_DIAMOND", True),
        self_max_matches=_int("SELF_MAX_MATCHES", 10000, 100),
        target_reference_player_games=_int(
            "TARGET_REFERENCE_PLAYER_GAMES", 5000, 1
        ),
        reference_games_per_player=_int("REFERENCE_GAMES_PER_PLAYER", 100, 1),
        very_short_game_seconds=_int("VERY_SHORT_GAME_SECONDS", 300),
        http_timeout_seconds=_int("HTTP_TIMEOUT_SECONDS", 30, 1),
        max_http_retries=_int("MAX_HTTP_RETRIES", 7, 1),
    )

