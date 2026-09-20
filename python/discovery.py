"""Kayle-first candidate discovery and Riot-validated cohort sampling."""

from __future__ import annotations

from collections import defaultdict, deque
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
    minimum_qualifying: int,
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
                if len(qualifying) >= minimum_qualifying:
                    return qualifying, ranked_in_window
        if len(ids) < min(100, maximum - start):
            break
    return qualifying, ranked_in_window


def _save_evaluation(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO candidate_evaluations
          (run_id, puuid, discovery_source, source_position,
           tier, division, league_points, kayle_mastery_points,
           ranked_games_in_window, qualifying_kayle_top_games, kayle_top_play_rate,
           experience_passed, eligible, selected, planned_game_count,
           selection_reason, qualifying_match_ids)
        VALUES
          (%(run_id)s,%(puuid)s,%(discovery_source)s,%(source_position)s,
           %(tier)s,%(division)s,%(league_points)s,
           %(kayle_mastery_points)s,%(ranked_games_in_window)s,
           %(qualifying_kayle_top_games)s,%(kayle_top_play_rate)s,
           %(experience_passed)s,%(eligible)s,false,0,%(selection_reason)s,
           %(qualifying_match_ids)s)
        ON CONFLICT (run_id, puuid) DO UPDATE SET
           discovery_source=EXCLUDED.discovery_source,
           source_position=EXCLUDED.source_position,
           tier=EXCLUDED.tier, division=EXCLUDED.division,
           league_points=EXCLUDED.league_points,
           kayle_mastery_points=EXCLUDED.kayle_mastery_points,
           ranked_games_in_window=EXCLUDED.ranked_games_in_window,
           qualifying_kayle_top_games=EXCLUDED.qualifying_kayle_top_games,
           kayle_top_play_rate=EXCLUDED.kayle_top_play_rate,
           experience_passed=EXCLUDED.experience_passed,
           eligible=EXCLUDED.eligible,
           selection_reason=EXCLUDED.selection_reason,
           qualifying_match_ids=EXCLUDED.qualifying_match_ids,
           evaluated_at=now()
        """,
        row,
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
    source_name = CURRENT_DISCOVERY_SOURCE
    conn.execute(
        """
        UPDATE candidate_evaluations
        SET selected=false, planned_game_count=0
        WHERE run_id=%s AND discovery_source LIKE %s
          AND discovery_source<>%s
        """,
        (run_id, f"{PUBLIC_SOURCE_PREFIX}%", source_name),
    )
    conn.commit()
    print(
        f"\n[discover] Fixed OP.GG Kayle top-50 snapshot: {len(candidates)} candidates; "
        "screening every Riot ID"
    )

    for number, candidate in enumerate(candidates, 1):
        riot_id = f"{candidate.game_name}#{candidate.tag_line}"
        try:
            account = api.account_by_riot_id(candidate.game_name, candidate.tag_line)
        except RiotNotFound:
            print(f"  [{number}/{len(candidates)}] {riot_id}: Riot ID no longer resolves")
            continue
        puuid = account["puuid"]
        upsert_player(conn, puuid, "REFERENCE", account)

        # Rank is the cheapest decisive Riot-side eligibility check.
        entry = _ranked_solo_entry(api, puuid)
        tier = str((entry or {}).get("tier") or "UNRANKED").upper()
        entry = {**(entry or {}), "puuid": puuid}
        if tier in ALL_RANKS:
            add_rank_snapshot(conn, run_id, entry, tier, source_name)

        existing = conn.execute(
            "SELECT * FROM candidate_evaluations WHERE run_id=%s AND puuid=%s",
            (run_id, puuid),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE candidate_evaluations
                SET discovery_source=%s, source_position=%s,
                    tier=%s, division=%s, league_points=%s,
                    eligible=CASE WHEN %s THEN eligible ELSE false END,
                    selection_reason=CASE WHEN %s THEN selection_reason
                                          ELSE 'below_emerald_iv_or_unranked' END
                WHERE run_id=%s AND puuid=%s
                """,
                (
                    source_name,
                    candidate.source_position,
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
                f"  [{number}/{len(candidates)}] {riot_id}: reused this run's "
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
            mastery = api.kayle_mastery(puuid)
            mastery_points = add_mastery(conn, run_id, puuid, mastery)
            if mastery_points < settings.min_kayle_mastery_points:
                reason = "below_mastery_screen"
            else:
                wins = int(entry.get("wins") or 0)
                losses = int(entry.get("losses") or 0)
                experience_passed, _, _ = establish_experience(
                    conn, api, run_id, puuid, wins, losses
                )
                if not experience_passed:
                    reason = "account_experience_not_proven"
                else:
                    qualifying, ranked_in_window = screen_reference_matches(
                        conn,
                        api,
                        puuid,
                        set(patch_window),
                        settings.reference_matches_to_screen,
                        settings.min_reference_kayle_games,
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
            "source_position": candidate.source_position,
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
            f"  [{number}/{len(candidates)}] {riot_id}: Riot rank={tier} "
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
        report: dict[str, Any] = {"riot_id": riot_id, "source_position": candidate.source_position}
        try:
            account = api.account_by_riot_id(candidate.game_name, candidate.tag_line)
        except RiotNotFound:
            report["result"] = "riot_id_not_found"
            reports.append(report)
            continue
        puuid = account["puuid"]
        entry = _ranked_solo_entry(api, puuid) or {}
        tier = str(entry.get("tier") or "UNRANKED").upper()
        report.update({"tier": tier, "division": entry.get("rank")})
        if tier not in ALL_RANKS:
            report["result"] = "below_emerald_iv_or_unranked"
            reports.append(report)
            continue
        mastery = api.kayle_mastery(puuid) or {}
        report["kayle_mastery_points"] = int(mastery.get("championPoints") or 0)
        if report["kayle_mastery_points"] < settings.min_kayle_mastery_points:
            report["result"] = "below_mastery_screen"
            reports.append(report)
            continue
        report["result"] = "rank_and_mastery_validated"
        report["recent_qualifying_match"] = None
        for match_id in api.match_ids(puuid, start=0, count=10, queue=420):
            match = api.match(match_id)
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


def select_and_plan(conn: Any, run_id: int, settings: Any) -> dict[str, Any]:
    report: dict[str, Any] = {}
    all_tasks: dict[str, dict[str, Any]] = {}
    rows_by_tier: dict[str, list[dict[str, Any]]] = {}
    ordered_by_tier: dict[str, list[dict[str, Any]]] = {}
    for tier in ALL_RANKS:
        rows_by_tier[tier] = conn.execute(
            """
            SELECT * FROM candidate_evaluations
            WHERE run_id=%s AND tier=%s AND eligible
              AND discovery_source=%s
            ORDER BY puuid
            """,
            (run_id, tier, CURRENT_DISCOVERY_SOURCE),
        ).fetchall()
        ordered_by_tier[tier] = _balanced_order(rows_by_tier[tier], tier)

    selected_puuids: set[str] = set()
    queues = {tier: deque(ordered_by_tier[tier]) for tier in ALL_RANKS}
    while len(selected_puuids) < settings.target_reference_players and any(queues.values()):
        for tier in ALL_RANKS:
            if queues[tier] and len(selected_puuids) < settings.target_reference_players:
                selected_puuids.add(queues[tier].popleft()["puuid"])

    for tier in ALL_RANKS:
        rows = rows_by_tier[tier]
        selected = [row for row in ordered_by_tier[tier] if row["puuid"] in selected_puuids]
        allocations = _round_robin_allocations(
            selected,
            settings.target_games_per_rank,
            settings.max_games_per_reference_player,
        )
        for row in rows:
            planned = allocations.get(row["puuid"], 0)
            selected_flag = planned > 0
            reason = (
                "selected_opgg_top50_eligible"
                if selected_flag
                else "eligible_not_needed_after_configured_player_target"
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
                (run_id, row["puuid"], tier),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO reference_cohort
                  (run_id, puuid, tier, division, rank_snapshot_id,
                   planned_game_count, selection_reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id, puuid) DO UPDATE SET
                  planned_game_count=EXCLUDED.planned_game_count,
                  selection_reason=EXCLUDED.selection_reason
                """,
                (
                    run_id,
                    row["puuid"],
                    tier,
                    row.get("division"),
                    snapshot["rank_snapshot_id"],
                    planned,
                    reason,
                ),
            )
            for match_id in list(row["qualifying_match_ids"])[:planned]:
                task = all_tasks.setdefault(match_id, {"targets": []})
                task["targets"].append(
                    {
                        "puuid": row["puuid"],
                        "cohort_type": "REFERENCE",
                        "tier": tier,
                        "division": row.get("division"),
                        "league_points": row.get("league_points"),
                        "rank_snapshot_at": snapshot["snapshot_at"].isoformat(),
                    }
                )
        planned_games = sum(allocations.values())
        report[tier] = {
            "eligible_players": len(rows),
            "selected_players": sum(1 for n in allocations.values() if n),
            "planned_player_games": planned_games,
            "unique_matches_planned_before_cross_player_dedup": planned_games,
        }
        print(
            f"[plan] {tier}: {report[tier]['selected_players']} players, "
            f"{planned_games} player-games (diversity first)"
        )
    for match_id, payload in all_tasks.items():
        add_collection_task(conn, run_id, "REFERENCE_MATCH", match_id, payload)
    conn.commit()
    report["unique_reference_matches"] = len(all_tasks)
    return report
