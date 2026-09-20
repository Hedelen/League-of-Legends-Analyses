"""Patch parsing and live three-patch-window discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
PATCH_RE = re.compile(r"^(\d+)\.(\d+)")


def patch_from_version(version: str | None) -> str | None:
    """Return Riot's major.minor patch from strings such as 26.18.701.1234."""
    if not version:
        return None
    match = PATCH_RE.match(str(version).strip())
    return f"{int(match.group(1))}.{int(match.group(2))}" if match else None


def unique_patches(versions: Iterable[str]) -> list[str]:
    found: list[str] = []
    for version in versions:
        patch = patch_from_version(version)
        if patch and patch not in found:
            found.append(patch)
    return found


@dataclass(frozen=True)
class PatchWindow:
    current: str
    patches: tuple[str, str, str]
    source_versions: tuple[str, ...]


def get_live_patch_window(timeout: int = 30) -> PatchWindow:
    import requests

    response = requests.get(VERSIONS_URL, timeout=timeout)
    response.raise_for_status()
    versions = response.json()
    patches = unique_patches(versions)
    if len(patches) < 3:
        raise RuntimeError("Data Dragon returned fewer than three distinct patch versions")
    return PatchWindow(
        current=patches[0],
        patches=(patches[0], patches[1], patches[2]),
        source_versions=tuple(versions[:10]),
    )
