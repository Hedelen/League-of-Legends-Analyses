"""Small Riot API client with header-aware throttling and bounded retries."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import requests


class RiotAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RiotNotFound(RiotAPIError):
    pass


def _pairs(value: str | None) -> dict[int, int]:
    """Parse Riot rate headers such as '20:1,100:120'."""
    parsed: dict[int, int] = {}
    if not value:
        return parsed
    for item in value.split(","):
        try:
            amount, seconds = item.strip().split(":", 1)
            parsed[int(seconds)] = int(amount)
        except (TypeError, ValueError):
            continue
    return parsed


class RiotAPI:
    def __init__(
        self,
        api_key: str,
        platform: str = "na1",
        routing: str = "americas",
        timeout: int = 30,
        max_retries: int = 7,
        on_retry: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.platform = platform
        self.routing = routing
        self.timeout = timeout
        self.max_retries = max_retries
        self.on_retry = on_retry
        self.session = requests.Session()
        self.session.headers.update(
            {"X-Riot-Token": api_key, "User-Agent": "kayle-analysis/1.0"}
        )
        self._blocked_until: dict[str, float] = {}

    def _record_retry(self, **details: Any) -> None:
        if self.on_retry:
            self.on_retry(details)

    def _honor_rate_headers(self, host: str, response: requests.Response) -> None:
        now = time.monotonic()
        for prefix in ("X-App", "X-Method"):
            limits = _pairs(response.headers.get(f"{prefix}-Rate-Limit"))
            counts = _pairs(response.headers.get(f"{prefix}-Rate-Limit-Count"))
            for seconds, limit in limits.items():
                if counts.get(seconds, 0) >= limit:
                    self._blocked_until[host] = max(
                        self._blocked_until.get(host, 0.0), now + seconds + 0.05
                    )

    def _get(
        self,
        host: str,
        path: str,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        url = f"https://{host}.api.riotgames.com{path}"
        for attempt in range(self.max_retries):
            delay = self._blocked_until.get(host, 0.0) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            try:
                response = self.session.get(
                    url, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if attempt == self.max_retries - 1:
                    raise RiotAPIError(f"Network failure after retries: {exc}") from exc
                wait = min(2**attempt, 30) + random.random()
                self._record_retry(url=url, attempt=attempt + 1, reason=str(exc), wait=wait)
                time.sleep(wait)
                continue

            self._honor_rate_headers(host, response)
            if response.ok:
                return response.json()
            if response.status_code == 404 and allow_404:
                return None
            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", "1")) + random.random()
                self._blocked_until[host] = time.monotonic() + wait
                self._record_retry(
                    url=url, attempt=attempt + 1, status=429, reason="rate_limit", wait=wait
                )
                continue
            if response.status_code >= 500:
                wait = min(2**attempt, 30) + random.random()
                self._record_retry(
                    url=url,
                    attempt=attempt + 1,
                    status=response.status_code,
                    reason="server_error",
                    wait=wait,
                )
                time.sleep(wait)
                continue
            if response.status_code == 403:
                raise RiotAPIError(
                    "Riot returned 403. The development API key is probably expired. "
                    "Update RIOT_API_KEY in .env, then run the same command again; "
                    "completed work is already committed.",
                    403,
                )
            if response.status_code == 404:
                raise RiotNotFound(f"Riot resource not found: {url}", 404)
            body = response.text[:500]
            raise RiotAPIError(
                f"Riot API {response.status_code} for {url}: {body}",
                response.status_code,
            )
        raise RiotAPIError(f"Riot API unavailable after {self.max_retries} attempts: {url}")

    def account_by_riot_id(self, game_name: str, tag_line: str) -> dict[str, Any]:
        return self._get(
            self.routing,
            "/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}",
        )

    def account_by_puuid(self, puuid: str) -> dict[str, Any] | None:
        return self._get(
            self.routing,
            f"/riot/account/v1/accounts/by-puuid/{quote(puuid, safe='')}",
            allow_404=True,
        )

    def summoner_by_id(self, summoner_id: str) -> dict[str, Any] | None:
        return self._get(
            self.platform,
            f"/lol/summoner/v4/summoners/{quote(summoner_id, safe='')}",
            allow_404=True,
        )

    def ranked_entries_by_puuid(self, puuid: str) -> list[dict[str, Any]]:
        return self._get(
            self.platform,
            f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}",
        )

    def ladder_entries(
        self, tier: str, division: str, page: int
    ) -> list[dict[str, Any]]:
        return self._get(
            self.platform,
            f"/lol/league/v4/entries/RANKED_SOLO_5x5/{tier}/{division}",
            {"page": page},
        )

    def apex_league(self, tier: str) -> dict[str, Any]:
        endpoint = {
            "MASTER": "masterleagues",
            "GRANDMASTER": "grandmasterleagues",
            "CHALLENGER": "challengerleagues",
        }[tier]
        return self._get(
            self.platform,
            f"/lol/league/v4/{endpoint}/by-queue/RANKED_SOLO_5x5",
        )

    def kayle_mastery(self, puuid: str) -> dict[str, Any] | None:
        return self._get(
            self.platform,
            "/lol/champion-mastery/v4/champion-masteries/by-puuid/"
            f"{quote(puuid, safe='')}/by-champion/10",
            allow_404=True,
        )

    def match_ids(
        self,
        puuid: str,
        *,
        start: int = 0,
        count: int = 100,
        queue: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[str]:
        params: dict[str, Any] = {"start": start, "count": min(count, 100)}
        if queue is not None:
            params["queue"] = queue
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self._get(
            self.routing,
            f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}/ids",
            params,
        )

    def match(self, match_id: str) -> dict[str, Any]:
        return self._get(
            self.routing, f"/lol/match/v5/matches/{quote(match_id, safe='')}"
        )

    def timeline(self, match_id: str) -> dict[str, Any] | None:
        return self._get(
            self.routing,
            f"/lol/match/v5/matches/{quote(match_id, safe='')}/timeline",
            allow_404=True,
        )
