"""Champion-first candidate discovery and Riot-validated cohort sampling."""

from __future__ import annotations

from collections import deque
from typing import Any

from champions import ChampionSpec, champion_spec

from database import (
    add_collection_task,
    add_experience_check,
    add_mastery,
    add_rank_snapshot,
    cache_get,
    cache_put,
    upsert_player,
)
from normalizer import participant_for_puuid, target_top_classification
from patches import patch_from_version
from public_sources import discover_candidates
from riot_api import RiotNotFound

APEX_RANKS = ("MASTER", "GRANDMASTER", "CHALLENGER")
HIGH_DIAMOND_DIVISIONS = ("I", "II")
ALL_RANKS = APEX_RANKS + ("DIAMOND",)


def _spec(settings: Any) -> ChampionSpec:
    return champion_spec(getattr(settings, "target_champion_key", "kayle"))


def _source_prefix(spec: ChampionSpec) -> str:
    return f"{spec.key}_public_leaderboard:"


def _current_source(spec: ChampionSpec) -> str:
    return f"{_source_prefix(spec)}{spec.source_name}"


def _regional_api(api: Any, platform: str, routing: str) -> Any:
    if hasattr(api, "for_region"):
        return api.for_region(platform, routing)
    return api


def _candidate_source(candidate: Any, spec: ChampionSpec) -> str:
    return f"{_current_source(spec)}:{candidate.source_region}"


def _ranked_solo_entry(api: Any, puuid: str) -> dict[str, Any] | None:
    entries = api.ranked_entries_by_puuid(puuid)
    return next(
        (row for row in entries if row.get("queueType") == "RANKED_SOLO_5x5"),
        None,
    )


def _champion_mastery(api: Any, puuid: str, spec: ChampionSpec) -> dict[str, Any] | None:
    if hasattr(api, "champion_mastery"):
        return api.champion_mastery(puuid, spec.champion_id)
    if spec.key == "kayle" and hasattr(api, "kayle_mastery"):
        return api.kayle_mastery(puuid)
    raise AttributeError("Riot API client does not support champion mastery lookups")


def _eligible_rank(tier: str, division: str | None, allow_high_diamond: bool) -> bool:
    if tier in APEX_RANKS:
        return True
    return (
        allow_high_diamond
        and tier == "DIAMOND"
        and str(division or "").upper() in HIGH_DIAMOND_DIVISIONS
    )


def _cached_match(conn: Any, api: Any, match_id: str) -> dict[str, Any]:
    cached = cache_get(conn, "match_v5", match_id)
    if cached is not None:
        return cached
    match = api.match(match_id)
    cache_put(conn, "match_v5", match_id, match, "30 days")
    return match


def establish_experience(
    conn: Any,
    api: Any,
    run_id: int,
    puuid: str,
    ranked_wins: int,
    ranked_losses: int,
    minimum_games: int,
) -> tuple[bool, str, int]:
    ranked_total = ranked_wins + ranked_losses
    if minimum_games <= 0:
        add_experience_check(conn, run_id, puuid, True, "not_required", ranked_total)
        return True, "not_required", ranked_total
    if ranked_total >= minimum_games:
        add_experience_check(conn, run_id, puuid, True, "ranked_solo_wins_plus_losses", ranked_total)
        return True, "ranked_solo_wins_plus_losses", ranked_total
    observed: set[str] = set()
    for start in range(0, minimum_games, 100):
        observed.update(api.match_ids(puuid, start=start, count=100, queue=None))
        if len(observed) >= minimum_games:
            break
    passed = len(observed) >= minimum_games
    method = "match_v5_unique_ids" if passed else "unknown_insufficient_observable_history"
    add_experience_check(
        conn,
        run_id,
        puuid,
        passed,
        method,
        len(observed),
        {"ranked_solo_record": ranked_total},
    )
    return passed, method, len(observed)


def screen_reference_matches(
    conn: Any,
    api: Any,
    puuid: str,
    patch_window: set[str],
    maximum: int,
    target_qualifying: int,
    use_patch_window: bool,
    champion: ChampionSpec | None = None,
) -> tuple[list[str], int]:
    champion = champion or champion_spec("kayle")
    qualifying: list[str] = []
    ranked_in_window = 0
    outside_window_streak = 0
    for start in range(0, maximum, 100):
        ids = api.match_ids(puuid, start=start, count=min(100, maximum - start), queue=420)
        if not ids:
            break
        for match_id in ids:
            match = _cached_match(conn, api, match_id)
            patch = patch_from_version(match.get("info", {}).get("gameVersion"))
            if use_patch_window and patch not in patch_window:
                outside_window_streak += 1
                if outside_window_streak >= 10:
                    return qualifying, ranked_in_window
                continue
            outside_window_streak = 0
            ranked_in_window += 1
            participant = participant_for_puuid(match, puuid)
            if target_top_classification(
                participant, champion.champion_id, champion.name
            )["qualifies"]:
                qualifying.append(match_id)
                if len(qualifying) >= target_qualifying:
                    return qualifying, ranked_in_window
        if len(ids) < min(100, maximum - start):
            break
    return qualifying, ranked_in_window


def _save_evaluation(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO candidate_evaluations
          (run_id, puuid, discovery_source, source_region, source_position,
           platform_code, routing_region,
           tier, division, league_points, kayle_mastery_points,
           ranked_games_in_window, qualifying_kayle_top_games, kayle_top_play_rate,
           champion_mastery_points, qualifying_top_games, top_play_rate,
           experience_passed, eligible, selected, planned_game_count,
           selection_reason)
        VALUES
          (%(run_id)s,%(puuid)s,%(discovery_source)s,%(source_region)s,%(source_position)s,
           %(platform_code)s,%(routing_region)s,
           %(tier)s,%(division)s,%(league_points)s,
           %(legacy_kayle_mastery_points)s,%(ranked_games_in_window)s,
           %(legacy_qualifying_kayle_top_games)s,%(legacy_kayle_top_play_rate)s,
           %(champion_mastery_points)s,%(qualifying_top_games)s,%(top_play_rate)s,
           %(experience_passed)s,%(eligible)s,false,0,%(selection_reason)s)
        ON CONFLICT (run_id, puuid) DO UPDATE SET
           discovery_source=EXCLUDED.discovery_source,
           source_region=EXCLUDED.source_region,
           source_position=EXCLUDED.source_position,
           platform_code=EXCLUDED.platform_code,
           routing_region=EXCLUDED.routing_region,
           tier=EXCLUDED.tier, division=EXCLUDED.division,
           league_points=EXCLUDED.league_points,
           kayle_mastery_points=EXCLUDED.kayle_mastery_points,
           ranked_games_in_window=EXCLUDED.ranked_games_in_window,
           qualifying_kayle_top_games=EXCLUDED.qualifying_kayle_top_games,
           kayle_top_play_rate=EXCLUDED.kayle_top_play_rate,
           champion_mastery_points=EXCLUDED.champion_mastery_points,
           qualifying_top_games=EXCLUDED.qualifying_top_games,
           top_play_rate=EXCLUDED.top_play_rate,
           experience_passed=EXCLUDED.experience_passed,
           eligible=EXCLUDED.eligible,
           selection_reason=EXCLUDED.selection_reason,
           evaluated_at=now()
        """,
        row,
    )
    conn.execute(
        "DELETE FROM candidate_qualifying_matches WHERE run_id=%s AND puuid=%s",
        (row["run_id"], row["puuid"]),
    )
    qualifying_match_ids = list(row.get("qualifying_match_ids") or [])
    if qualifying_match_ids:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO candidate_qualifying_matches
                  (run_id, puuid, match_order, match_id)
                VALUES (%s,%s,%s,%s)
                """,
                [
                    (row["run_id"], row["puuid"], index, match_id)
                    for index, match_id in enumerate(qualifying_match_ids)
                ],
            )
    conn.commit()


def evaluate_candidates(
    conn: Any,
    api: Any,
    run_id: int,
    patch_window: tuple[str, str, str],
    settings: Any,
) -> None:
    spec = _spec(settings)
    candidates = discover_candidates(spec, limit=settings.reference_candidate_limit)
    source_prefix = _source_prefix(spec)
    current_source = _current_source(spec)
    conn.execute(
        """
        UPDATE candidate_evaluations
        SET selected=false, planned_game_count=0
        WHERE run_id=%s AND discovery_source LIKE %s
          AND discovery_source NOT LIKE %s
        """,
        (run_id, f"{source_prefix}%", f"{current_source}:%"),
    )
    conn.commit()
    print(
        f"\n[discover] Fixed multi-region OP.GG {spec.name} snapshot: {len(candidates)} candidates; "
        "screening every Riot ID through Riot"
    )

    for number, candidate in enumerate(candidates, 1):
        riot_id = f"{candidate.game_name}#{candidate.tag_line}"
        candidate_api = _regional_api(api, candidate.platform, candidate.routing)
        source_name = _candidate_source(candidate, spec)
        try:
            account = candidate_api.account_by_riot_id(candidate.game_name, candidate.tag_line)
        except RiotNotFound:
            print(f"  [{number}/{len(candidates)}] {riot_id}: Riot ID no longer resolves")
            continue
        puuid = account["puuid"]
        upsert_player(conn, puuid, "REFERENCE", account)

        # Rank is the cheapest decisive Riot-side eligibility check.
        entry = _ranked_solo_entry(candidate_api, puuid)
        tier = str((entry or {}).get("tier") or "UNRANKED").upper()
        entry = {**(entry or {}), "puuid": puuid}
        division = str(entry.get("rank") or "").upper() or None
        rank_eligible = _eligible_rank(
            tier, division, settings.reference_allow_high_diamond
        )
        if rank_eligible:
            add_rank_snapshot(
                conn, run_id, entry, tier, source_name, candidate.platform
            )

        mastery_points = 0
        experience_passed = False
        qualifying: list[str] = []
        ranked_in_window = 0
        reason = "eligible"
        if not rank_eligible:
            reason = "below_master_or_high_diamond"
        else:
            mastery = _champion_mastery(candidate_api, puuid, spec)
            mastery_points = add_mastery(
                conn, run_id, puuid, mastery, spec.champion_id
            )
            if mastery_points < settings.min_kayle_mastery_points:
                reason = "below_mastery_screen"
            else:
                wins = int(entry.get("wins") or 0)
                losses = int(entry.get("losses") or 0)
                experience_passed, _, _ = establish_experience(
                    conn,
                    candidate_api,
                    run_id,
                    puuid,
                    wins,
                    losses,
                    settings.min_account_experience_games,
                )
                if not experience_passed:
                    reason = "account_experience_not_proven"
                else:
                    qualifying, ranked_in_window = screen_reference_matches(
                        conn,
                        candidate_api,
                        puuid,
                        set(patch_window),
                        settings.reference_matches_to_screen,
                        settings.reference_games_per_player,
                        settings.reference_use_patch_window,
                        spec,
                    )
                    if len(qualifying) < settings.min_reference_kayle_games:
                        reason = "too_few_recent_target_top_games"
        eligible = (
            rank_eligible
            and experience_passed
            and mastery_points >= settings.min_kayle_mastery_points
            and len(qualifying) >= settings.min_reference_kayle_games
        )
        row = {
            "run_id": run_id,
            "puuid": puuid,
            "discovery_source": source_name,
            "source_region": candidate.source_region,
            "source_position": candidate.source_position,
            "platform_code": candidate.platform.upper(),
            "routing_region": candidate.routing,
            "tier": tier,
            "division": entry.get("rank"),
            "league_points": entry.get("leaguePoints"),
            "champion_mastery_points": mastery_points,
            "ranked_games_in_window": ranked_in_window,
            "qualifying_top_games": len(qualifying),
            "top_play_rate": (
                len(qualifying) / ranked_in_window if ranked_in_window else None
            ),
            "legacy_kayle_mastery_points": (
                mastery_points if spec.key == "kayle" else None
            ),
            "legacy_qualifying_kayle_top_games": (
                len(qualifying) if spec.key == "kayle" else 0
            ),
            "legacy_kayle_top_play_rate": (
                (len(qualifying) / ranked_in_window if ranked_in_window else None)
                if spec.key == "kayle" else None
            ),
            "experience_passed": experience_passed,
            "eligible": eligible,
            "selection_reason": reason,
            "qualifying_match_ids": qualifying,
        }
        _save_evaluation(conn, row)
        print(
            f"  [{number}/{len(candidates)}] {riot_id} [{candidate.platform.upper()}]: Riot rank={tier} "
            f"{entry.get('rank') or ''}; mastery={mastery_points:,}; "
            f"{spec.name} TOP={len(qualifying)}; {reason}"
        )


def smoke_discovery_flow(
    api: Any,
    settings: Any,
    patch_window: tuple[str, str, str],
    count: int = 5,
) -> list[dict[str, Any]]:
    """Read-only public-ID -> Riot rank/mastery/recent-role smoke test."""
    spec = _spec(settings)
    candidates = discover_candidates(spec, limit=count)
    reports: list[dict[str, Any]] = []
    for candidate in candidates:
        riot_id = f"{candidate.game_name}#{candidate.tag_line}"
        candidate_api = _regional_api(api, candidate.platform, candidate.routing)
        report: dict[str, Any] = {
            "riot_id": riot_id,
            "source_position": candidate.source_position,
            "platform": candidate.platform.upper(),
            "routing": candidate.routing,
        }
        try:
            account = candidate_api.account_by_riot_id(
                candidate.game_name, candidate.tag_line
            )
        except RiotNotFound:
            report["result"] = "riot_id_not_found"
            reports.append(report)
            continue
        puuid = account["puuid"]
        entry = _ranked_solo_entry(candidate_api, puuid) or {}
        tier = str(entry.get("tier") or "UNRANKED").upper()
        report.update({"tier": tier, "division": entry.get("rank")})
        if not _eligible_rank(
            tier, entry.get("rank"), settings.reference_allow_high_diamond
        ):
            report["result"] = "below_master_or_high_diamond"
            reports.append(report)
            continue
        mastery = _champion_mastery(candidate_api, puuid, spec) or {}
        report["champion_mastery_points"] = int(mastery.get("championPoints") or 0)
        if report["champion_mastery_points"] < settings.min_kayle_mastery_points:
            report["result"] = "below_mastery_screen"
            reports.append(report)
            continue
        report["result"] = "rank_and_mastery_validated"
        report["recent_qualifying_match"] = None
        for match_id in candidate_api.match_ids(puuid, start=0, count=10, queue=420):
            match = candidate_api.match(match_id)
            if (
                settings.reference_use_patch_window
                and patch_from_version(match.get("info", {}).get("gameVersion"))
                not in patch_window
            ):
                continue
            participant = participant_for_puuid(match, puuid)
            if target_top_classification(
                participant, spec.champion_id, spec.name
            )["qualifies"]:
                report["recent_qualifying_match"] = match_id
                report["result"] = "target_top_flow_validated"
                break
        reports.append(report)
    return reports


def _balanced_order(rows: list[dict[str, Any]], tier: str) -> list[dict[str, Any]]:
    if tier == "DIAMOND":
        groups = {
            division: deque(
                sorted(
                    (r for r in rows if r.get("division") == division),
                    key=lambda r: (r.get("source_position") or 10**9, r["puuid"]),
                )
            )
            for division in HIGH_DIAMOND_DIVISIONS
        }
    else:
        ordered = sorted(rows, key=lambda r: (r.get("league_points") or 0, r["puuid"]))
        groups = {str(i): deque() for i in range(5)}
        for index, row in enumerate(ordered):
            groups[str(min(4, index * 5 // max(1, len(ordered))))].append(row)
    output: list[dict[str, Any]] = []
    queues = list(groups.values())
    while any(queues):
        for queue in queues:
            if queue:
                output.append(queue.popleft())
    return output


def _round_robin_allocations(
    selected: list[dict[str, Any]], target: int, per_player_cap: int
) -> dict[str, int]:
    allocations = {r["puuid"]: 0 for r in selected}
    total = 0
    while total < target:
        changed = False
        for row in selected:
            available = row.get("qualifying_top_games")
            if available is None:
                available = row.get("qualifying_kayle_top_games", 0)
            cap = min(per_player_cap, available)
            if allocations[row["puuid"]] < cap and total < target:
                allocations[row["puuid"]] += 1
                total += 1
                changed = True
        if not changed:
            break
    return allocations


def _multiregion_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Interleave platforms and ranks; source position is only a tie-breaker."""
    platforms = sorted({str(row.get("platform_code") or "UNKNOWN") for row in rows})
    platform_queues: dict[str, deque[dict[str, Any]]] = {}
    for platform in platforms:
        regional = [row for row in rows if row.get("platform_code") == platform]
        priority = ("CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND")
        tier_queues = {
            tier: deque(_balanced_order([r for r in regional if r["tier"] == tier], tier))
            for tier in priority
        }
        ordered: list[dict[str, Any]] = []
        while any(tier_queues.values()):
            for tier in priority:
                if tier_queues[tier]:
                    ordered.append(tier_queues[tier].popleft())
        platform_queues[platform] = deque(ordered)

    output: list[dict[str, Any]] = []
    while any(platform_queues.values()):
        for platform in platforms:
            if platform_queues[platform]:
                output.append(platform_queues[platform].popleft())
    return output


def select_and_plan(conn: Any, run_id: int, settings: Any) -> dict[str, Any]:
    spec = _spec(settings)
    current_source = _current_source(spec)
    report: dict[str, Any] = {}
    all_tasks: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT c.*,
               ARRAY(
                   SELECT q.match_id FROM candidate_qualifying_matches q
                   WHERE q.run_id=c.run_id AND q.puuid=c.puuid
                   ORDER BY q.match_order
               ) AS qualifying_match_ids
        FROM candidate_evaluations c
        WHERE c.run_id=%s AND c.eligible
          AND c.discovery_source LIKE %s
        ORDER BY c.puuid
        """,
        (run_id, f"{current_source}:%"),
    ).fetchall()
    # Exhaust Master+ capacity before using Diamond I-II as a fallback.
    apex_rows = [row for row in rows if row["tier"] in APEX_RANKS]
    diamond_rows = [row for row in rows if row["tier"] == "DIAMOND"]
    ordered = _multiregion_order(apex_rows) + _multiregion_order(diamond_rows)

    selected: list[dict[str, Any]] = []
    capacity = 0
    for row in ordered:
        if (
            len(selected) >= settings.target_reference_players
            or capacity >= settings.target_reference_player_games
        ):
            break
        selected.append(row)
        capacity += min(
            settings.reference_games_per_player,
            row["qualifying_top_games"],
        )

    allocations = _round_robin_allocations(
        selected,
        settings.target_reference_player_games,
        settings.reference_games_per_player,
    )
    conn.execute("DELETE FROM reference_cohort WHERE run_id=%s", (run_id,))

    for row in rows:
        planned = allocations.get(row["puuid"], 0)
        selected_flag = planned > 0
        reason = (
            (
                "selected_master_plus"
                if row["tier"] in APEX_RANKS
                else "selected_high_diamond_fallback"
            )
            if selected_flag
            else "eligible_not_needed_after_player_and_game_targets"
        )
        conn.execute(
            """
            UPDATE candidate_evaluations
            SET selected=%s, planned_game_count=%s, selection_reason=%s
            WHERE run_id=%s AND puuid=%s
            """,
            (selected_flag, planned, reason, run_id, row["puuid"]),
        )
        if not selected_flag:
            continue
        snapshot = conn.execute(
            "SELECT * FROM rank_snapshots WHERE run_id=%s AND puuid=%s AND tier=%s",
            (run_id, row["puuid"], row["tier"]),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO reference_cohort
              (run_id, puuid, tier, division, platform_code, rank_snapshot_id,
               planned_game_count, selection_reason)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id, puuid) DO UPDATE SET
              tier=EXCLUDED.tier,
              division=EXCLUDED.division,
              platform_code=EXCLUDED.platform_code,
              rank_snapshot_id=EXCLUDED.rank_snapshot_id,
              planned_game_count=EXCLUDED.planned_game_count,
              selection_reason=EXCLUDED.selection_reason
            """,
            (
                run_id,
                row["puuid"],
                row["tier"],
                row.get("division"),
                row.get("platform_code"),
                snapshot["rank_snapshot_id"],
                planned,
                reason,
            ),
        )
        for match_id in list(row["qualifying_match_ids"])[:planned]:
            task = all_tasks.setdefault(
                match_id,
                {
                    "platform": row["platform_code"],
                    "routing": row["routing_region"],
                    "target_champion_id": spec.champion_id,
                    "target_champion_name": spec.name,
                    "targets": [],
                },
            )
            task["targets"].append(
                {
                    "puuid": row["puuid"],
                    "cohort_type": "REFERENCE",
                    "tier": row["tier"],
                    "division": row.get("division"),
                    "league_points": row.get("league_points"),
                    "platform": row["platform_code"],
                    "rank_snapshot_at": snapshot["snapshot_at"].isoformat(),
                }
            )

    planned_total = sum(allocations.values())
    for platform in sorted({row["platform_code"] for row in rows}):
        regional_rows = [row for row in rows if row["platform_code"] == platform]
        regional_selected = [row for row in regional_rows if allocations.get(row["puuid"], 0)]
        regional_games = sum(allocations.get(row["puuid"], 0) for row in regional_rows)
        report[platform] = {
            "eligible_players": len(regional_rows),
            "selected_players": len(regional_selected),
            "planned_player_games": regional_games,
        }
        print(
            f"[plan] {platform}: {len(regional_selected)} players, "
            f"{regional_games} player-games (region/rank diversity first)"
        )

    report["selected_players"] = sum(1 for value in allocations.values() if value)
    report["planned_player_games"] = planned_total
    for match_id, payload in all_tasks.items():
        add_collection_task(conn, run_id, "REFERENCE_MATCH", match_id, payload)
    conn.commit()
    report["unique_reference_matches"] = len(all_tasks)
    print(
        f"[plan] total: {report['selected_players']} players, "
        f"{planned_total} player-games, {len(all_tasks)} unique matches"
    )
    if planned_total < settings.target_reference_player_games:
        print(
            f"[plan] WARNING: candidate history supplied only {planned_total:,} of "
            f"{settings.target_reference_player_games:,} requested games. Add more "
            "leaderboard candidates or increase REFERENCE_MATCHES_TO_SCREEN."
        )
    return report

