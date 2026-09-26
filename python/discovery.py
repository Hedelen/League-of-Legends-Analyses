"""Kayle-first candidate discovery and Riot-validated cohort sampling."""

from __future__ import annotations

from collections import deque
from typing import Any

from database import (
    add_collection_task,
    add_experience_check,
    add_mastery,
    add_rank_snapshot,
    cache_get,
    cache_put,
    upsert_player,
)
from normalizer import kayle_top_classification, participant_for_puuid
from patches import patch_from_version
from public_sources import SOURCE_NAME, discover_kayle_candidates
from riot_api import RiotNotFound

STANDARD_RANKS = ("EMERALD", "DIAMOND")
APEX_RANKS = ("MASTER", "GRANDMASTER", "CHALLENGER")
ALL_RANKS = STANDARD_RANKS + APEX_RANKS
DIVISIONS = ("I", "II", "III", "IV")
PUBLIC_SOURCE_PREFIX = "kayle_public_leaderboard:"
CURRENT_DISCOVERY_SOURCE = f"{PUBLIC_SOURCE_PREFIX}{SOURCE_NAME}"


def _regional_api(api: Any, platform: str, routing: str) -> Any:
    if hasattr(api, "for_region"):
        return api.for_region(platform, routing)
    return api


def _candidate_source(candidate: Any) -> str:
    return f"{CURRENT_DISCOVERY_SOURCE}:{candidate.source_region}"


def _ranked_solo_entry(api: Any, puuid: str) -> dict[str, Any] | None:
    entries = api.ranked_entries_by_puuid(puuid)
    return next(
        (row for row in entries if row.get("queueType") == "RANKED_SOLO_5x5"),
        None,
    )


def _cached_match(conn: Any, api: Any, match_id: str) -> dict[str, Any]:
    cached = cache_get(conn, "match_v5", match_id)
    if cached is not None:
        return cached
    match = api.match(match_id)
    cache_put(conn, "match_v5", match_id, match, "30 days")
    return match


def establish_experience(
    conn: Any, api: Any, run_id: int, puuid: str, ranked_wins: int, ranked_losses: int
) -> tuple[bool, str, int]:
    ranked_total = ranked_wins + ranked_losses
    if ranked_total >= 200:
        add_experience_check(conn, run_id, puuid, True, "ranked_solo_wins_plus_losses", ranked_total)
        return True, "ranked_solo_wins_plus_losses", ranked_total
    observed: set[str] = set()
    for start in (0, 100):
        observed.update(api.match_ids(puuid, start=start, count=100, queue=None))
        if len(observed) >= 200:
            break
    passed = len(observed) >= 200
    method = "match_v5_200_unique_ids" if passed else "unknown_insufficient_observable_history"
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
) -> tuple[list[str], int]:
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
            if patch not in patch_window:
                outside_window_streak += 1
                if outside_window_streak >= 10:
                    return qualifying, ranked_in_window
                continue
            outside_window_streak = 0
            ranked_in_window += 1
            participant = participant_for_puuid(match, puuid)
            if kayle_top_classification(participant)["qualifies"]:
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
           experience_passed, eligible, selected, planned_game_count,
           selection_reason)
        VALUES
          (%(run_id)s,%(puuid)s,%(discovery_source)s,%(source_region)s,%(source_position)s,
           %(platform_code)s,%(routing_region)s,
           %(tier)s,%(division)s,%(league_points)s,
           %(kayle_mastery_points)s,%(ranked_games_in_window)s,
           %(qualifying_kayle_top_games)s,%(kayle_top_play_rate)s,
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
        conn.executemany(
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
    candidates = discover_kayle_candidates()
    conn.execute(
        """
        UPDATE candidate_evaluations
        SET selected=false, planned_game_count=0
        WHERE run_id=%s AND discovery_source LIKE %s
          AND discovery_source NOT LIKE %s
        """,
        (run_id, f"{PUBLIC_SOURCE_PREFIX}%", f"{CURRENT_DISCOVERY_SOURCE}:%"),
    )
    conn.commit()
    print(
        f"\n[discover] Fixed multi-region OP.GG Kayle snapshot: {len(candidates)} candidates; "
        "screening every Riot ID through Riot"
    )

    for number, candidate in enumerate(candidates, 1):
        riot_id = f"{candidate.game_name}#{candidate.tag_line}"
        candidate_api = _regional_api(api, candidate.platform, candidate.routing)
        source_name = _candidate_source(candidate)
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
        if tier in ALL_RANKS:
            add_rank_snapshot(
                conn, run_id, entry, tier, source_name, candidate.platform
            )

        existing = conn.execute(
            "SELECT * FROM candidate_evaluations WHERE run_id=%s AND puuid=%s",
            (run_id, puuid),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE candidate_evaluations
                SET discovery_source=%s, source_region=%s, source_position=%s,
                    platform_code=%s, routing_region=%s,
                    tier=%s, division=%s, league_points=%s,
                    eligible=CASE WHEN %s THEN eligible ELSE false END,
                    selection_reason=CASE WHEN %s THEN selection_reason
                                          ELSE 'below_emerald_iv_or_unranked' END
                WHERE run_id=%s AND puuid=%s
                """,
                (
                    source_name,
                    candidate.source_region,
                    candidate.source_position,
                    candidate.platform.upper(),
                    candidate.routing,
                    tier,
                    entry.get("rank"),
                    entry.get("leaguePoints"),
                    tier in ALL_RANKS,
                    tier in ALL_RANKS,
                    run_id,
                    puuid,
                ),
            )
            conn.commit()
            print(
                f"  [{number}/{len(candidates)}] {riot_id} [{candidate.platform.upper()}]: reused this run's "
                f"Riot validation ({existing['qualifying_kayle_top_games']} Kayle TOP)"
            )
            continue

        mastery_points = 0
        experience_passed = False
        qualifying: list[str] = []
        ranked_in_window = 0
        reason = "eligible"
        if tier not in ALL_RANKS:
            reason = "below_emerald_iv_or_unranked"
        else:
            mastery = candidate_api.kayle_mastery(puuid)
            mastery_points = add_mastery(conn, run_id, puuid, mastery)
            if mastery_points < settings.min_kayle_mastery_points:
                reason = "below_mastery_screen"
            else:
                wins = int(entry.get("wins") or 0)
                losses = int(entry.get("losses") or 0)
                experience_passed, _, _ = establish_experience(
                    conn, candidate_api, run_id, puuid, wins, losses
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
                    )
                    if len(qualifying) < settings.min_reference_kayle_games:
                        reason = "too_few_recent_kayle_top_games"
        eligible = (
            tier in ALL_RANKS
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
            "kayle_mastery_points": mastery_points,
            "ranked_games_in_window": ranked_in_window,
            "qualifying_kayle_top_games": len(qualifying),
            "kayle_top_play_rate": (
                len(qualifying) / ranked_in_window if ranked_in_window else None
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
            f"Kayle TOP={len(qualifying)}; {reason}"
        )


def smoke_discovery_flow(
    api: Any,
    settings: Any,
    patch_window: tuple[str, str, str],
    count: int = 5,
) -> list[dict[str, Any]]:
    """Read-only public-ID -> Riot rank/mastery/recent-role smoke test."""
    candidates = discover_kayle_candidates(limit=count)
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
        if tier not in ALL_RANKS:
            report["result"] = "below_emerald_iv_or_unranked"
            reports.append(report)
            continue
        mastery = candidate_api.kayle_mastery(puuid) or {}
        report["kayle_mastery_points"] = int(mastery.get("championPoints") or 0)
        if report["kayle_mastery_points"] < settings.min_kayle_mastery_points:
            report["result"] = "below_mastery_screen"
            reports.append(report)
            continue
        report["result"] = "rank_and_mastery_validated"
        report["recent_qualifying_match"] = None
        for match_id in candidate_api.match_ids(puuid, start=0, count=10, queue=420):
            match = candidate_api.match(match_id)
            if patch_from_version(match.get("info", {}).get("gameVersion")) not in patch_window:
                continue
            participant = participant_for_puuid(match, puuid)
            if kayle_top_classification(participant)["qualifies"]:
                report["recent_qualifying_match"] = match_id
                report["result"] = "kayle_top_flow_validated"
                break
        reports.append(report)
    return reports


def _balanced_order(rows: list[dict[str, Any]], tier: str) -> list[dict[str, Any]]:
    if tier in STANDARD_RANKS:
        groups = {
            division: deque(
                sorted(
                    (r for r in rows if r.get("division") == division),
                    key=lambda r: (r.get("source_position") or 10**9, r["puuid"]),
                )
            )
            for division in DIVISIONS
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
            cap = min(per_player_cap, row["qualifying_kayle_top_games"])
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
        tier_queues = {
            tier: deque(_balanced_order([r for r in regional if r["tier"] == tier], tier))
            for tier in ALL_RANKS
        }
        ordered: list[dict[str, Any]] = []
        while any(tier_queues.values()):
            for tier in ALL_RANKS:
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
        (run_id, f"{CURRENT_DISCOVERY_SOURCE}:%"),
    ).fetchall()
    ordered = _multiregion_order(rows)

    selected: list[dict[str, Any]] = []
    capacity = 0
    for row in ordered:
        if (
            len(selected) >= settings.target_reference_players
            and capacity >= settings.target_reference_player_games
        ):
            break
        selected.append(row)
        capacity += min(
            settings.reference_games_per_player,
            row["qualifying_kayle_top_games"],
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
            "selected_multiregion_opgg_eligible"
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
    return report
