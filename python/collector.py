"""SELF discovery, match/timeline collection, resume logic, and quality summary."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from database import (
    add_collection_task,
    cache_get,
    cache_put,
    pending_tasks,
    set_task_status,
    store_match_bundle,
    upsert_player,
)
from normalizer import kayle_top_classification, participant_for_puuid
from patches import patch_from_version


PLATFORM_ROUTING = {
    "BR1": "americas",
    "LA1": "americas",
    "LA2": "americas",
    "NA1": "americas",
    "EUN1": "europe",
    "EUW1": "europe",
    "ME1": "europe",
    "RU": "europe",
    "TR1": "europe",
    "JP1": "asia",
    "KR": "asia",
    "OC1": "sea",
    "PH2": "sea",
    "SG2": "sea",
    "TH2": "sea",
    "TW2": "sea",
    "VN2": "sea",
}


def _regional_api(api: Any, platform: str, routing: str) -> Any:
    if hasattr(api, "for_region"):
        return api.for_region(platform.lower(), routing.lower())
    return api


def _cached_match(conn: Any, api: Any, match_id: str) -> dict[str, Any]:
    cached = cache_get(conn, "match_v5", match_id)
    if cached is not None:
        return cached
    match = api.match(match_id)
    cache_put(conn, "match_v5", match_id, match, "30 days")
    return match


def _merge_self_task(
    conn: Any,
    run_id: int,
    match_id: str,
    target: dict[str, Any],
    platform: str,
    routing: str,
) -> None:
    existing = conn.execute(
        """
        SELECT payload FROM pipeline_internal.collection_tasks
        WHERE run_id=%s AND task_type='SELF_MATCH' AND task_key=%s
        """,
        (run_id, match_id),
    ).fetchone()
    payload = existing["payload"] if existing else {
        "platform": platform.upper(),
        "routing": routing.lower(),
        "targets": [],
    }
    payload["platform"] = platform.upper()
    payload["routing"] = routing.lower()
    targets = list(payload.get("targets", []))
    if not any(item.get("puuid") == target["puuid"] for item in targets):
        targets.append(target)
    payload["targets"] = targets
    add_collection_task(conn, run_id, "SELF_MATCH", match_id, payload)


def discover_self_matches(
    conn: Any, api: Any, run_id: int, settings: Any
) -> None:
    self_api = _regional_api(api, settings.platform, settings.routing)
    for game_name, tag_line in settings.self_accounts:
        account = self_api.account_by_riot_id(game_name, tag_line)
        puuid = account["puuid"]
        upsert_player(conn, puuid, "SELF", account)
        state = conn.execute(
            """
            SELECT * FROM player_scan_state
            WHERE run_id=%s AND puuid=%s AND scan_kind='SELF_RANKED_SOLO'
            """,
            (run_id, puuid),
        ).fetchone()
        start = int(state["next_start"]) if state else 0
        if state and state["complete"]:
            print(f"[self] {game_name}#{tag_line}: scan already complete")
            continue
        checkpoint = conn.execute(
            """
            SELECT newest_match_id FROM pipeline_internal.player_scan_checkpoints
            WHERE puuid=%s AND scan_kind='SELF_RANKED_SOLO'
            """,
            (puuid,),
        ).fetchone()
        stop_match_id = (
            state.get("stop_match_id") if state else None
        ) or (checkpoint.get("newest_match_id") if checkpoint else None)
        newest_match_id = state.get("newest_match_id") if state else None
        mode = "new matches" if stop_match_id else "all retrievable matches"
        print(
            f"[self] Scanning {mode} for {game_name}#{tag_line} "
            f"[{settings.platform.upper()}]"
        )
        qualifying_total = 0
        while start < settings.self_max_matches:
            ids = self_api.match_ids(puuid, start=start, count=100, queue=420)
            if start == 0 and ids and not newest_match_id:
                newest_match_id = ids[0]
            reached_checkpoint = False
            for match_id in ids:
                if stop_match_id and match_id == stop_match_id:
                    reached_checkpoint = True
                    break
                match = _cached_match(conn, self_api, match_id)
                participant = participant_for_puuid(match, puuid)
                classification = kayle_top_classification(participant)
                if classification["qualifies"]:
                    _merge_self_task(
                        conn,
                        run_id,
                        match_id,
                        {
                            "puuid": puuid,
                            "cohort_type": "SELF",
                            "platform": settings.platform.upper(),
                            "role_source": classification["role_source"],
                        },
                        settings.platform,
                        settings.routing,
                    )
                    qualifying_total += 1
            start += len(ids)
            complete = reached_checkpoint or len(ids) < 100
            conn.execute(
                """
                INSERT INTO player_scan_state
                  (run_id, puuid, scan_kind, next_start, stop_match_id,
                   newest_match_id, complete)
                VALUES (%s,%s,'SELF_RANKED_SOLO',%s,%s,%s,%s)
                ON CONFLICT (run_id, puuid, scan_kind) DO UPDATE SET
                  next_start=EXCLUDED.next_start,
                  stop_match_id=EXCLUDED.stop_match_id,
                  newest_match_id=EXCLUDED.newest_match_id,
                  complete=EXCLUDED.complete,
                  updated_at=now()
                """,
                (run_id, puuid, start, stop_match_id, newest_match_id, complete),
            )
            if complete and newest_match_id:
                conn.execute(
                    """
                    INSERT INTO pipeline_internal.player_scan_checkpoints
                      (puuid, scan_kind, newest_match_id)
                    VALUES (%s,'SELF_RANKED_SOLO',%s)
                    ON CONFLICT (puuid, scan_kind) DO UPDATE SET
                      newest_match_id=EXCLUDED.newest_match_id,
                      updated_at=now()
                    """,
                    (puuid, newest_match_id),
                )
            conn.commit()
            print(
                f"  inspected {start} match IDs; found {qualifying_total} new Kayle TOP"
            )
            if complete or not ids:
                break
        if start >= settings.self_max_matches:
            print(
                f"  reached SELF_MAX_MATCHES={settings.self_max_matches:,}; "
                "raise it and resume if status shows the scan incomplete"
            )


def _stored_match_payload(conn: Any, match_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload FROM raw_archive.raw_payloads WHERE resource_type='MATCH' AND resource_key=%s",
        (match_id,),
    ).fetchone()
    return row["payload"] if row else None


def collect_tasks(
    conn: Any,
    api: Any,
    run_id: int,
    patch_window: tuple[str, ...],
    settings: Any,
) -> None:
    tasks = pending_tasks(conn, run_id)
    total = len(tasks)
    print(f"[collect] {total} incomplete unique-match tasks")
    for number, task in enumerate(tasks, 1):
        match_id = task["task_key"]
        payload = task["payload"]
        platform = str(payload.get("platform") or match_id.split("_", 1)[0]).upper()
        routing = str(payload.get("routing") or PLATFORM_ROUTING.get(platform) or settings.routing)
        task_api = _regional_api(api, platform, routing)
        set_task_status(conn, task["task_id"], "RUNNING")
        try:
            stored = conn.execute(
                "SELECT timeline_status FROM matches WHERE match_id=%s", (match_id,)
            ).fetchone()
            match = _stored_match_payload(conn, match_id)
            if match is None:
                match = _cached_match(conn, task_api, match_id)
            if not stored or stored["timeline_status"] != "COMPLETE":
                timeline = task_api.timeline(match_id)
                store_match_bundle(
                    conn, match, timeline, settings.very_short_game_seconds
                )
            for target in payload.get("targets", []):
                participant = participant_for_puuid(match, target["puuid"])
                classification = kayle_top_classification(participant)
                if not classification["qualifies"]:
                    raise RuntimeError(
                        f"Task target no longer validates as Kayle TOP: {target['puuid']} "
                        f"({classification['reason']})"
                    )
                conn.execute(
                    """
                    INSERT INTO target_player_matches
                      (run_id, puuid, match_id, participant_id, cohort_type, platform_code,
                       rank_at_collection, division_at_collection, lp_at_collection,
                       rank_snapshot_at, role_source, role_ambiguous)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id, puuid, match_id) DO UPDATE SET
                      participant_id=EXCLUDED.participant_id,
                      platform_code=EXCLUDED.platform_code,
                      role_source=EXCLUDED.role_source,
                      role_ambiguous=EXCLUDED.role_ambiguous
                    """,
                    (
                        run_id,
                        target["puuid"],
                        match_id,
                        participant["participantId"],
                        target["cohort_type"],
                        str(target.get("platform") or platform).upper(),
                        target.get("tier"),
                        target.get("division"),
                        target.get("league_points"),
                        target.get("rank_snapshot_at"),
                        classification["role_source"],
                        classification["ambiguous"],
                    ),
                )
            patch = patch_from_version(match.get("info", {}).get("gameVersion"))
            conn.execute(
                """
                INSERT INTO collection_run_matches
                  (run_id, match_id, in_active_reference_window, discovered_for)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (run_id, match_id) DO UPDATE SET
                  in_active_reference_window=EXCLUDED.in_active_reference_window,
                  discovered_for=EXCLUDED.discovered_for
                """,
                (
                    run_id,
                    match_id,
                    patch in set(patch_window),
                    task["task_type"],
                ),
            )
            conn.commit()
            set_task_status(conn, task["task_id"], "COMPLETE")
            print(f"  [{number}/{total}] {match_id}: match + all participants + timeline saved")
        except Exception as exc:
            conn.rollback()
            set_task_status(conn, task["task_id"], "FAILED", str(exc)[:2000])
            conn.execute(
                "INSERT INTO pipeline_internal.api_failures (run_id, context) VALUES (%s,%s)",
                (
                    run_id,
                    Jsonb({"task_id": task["task_id"], "match_id": match_id, "error": str(exc)}),
                ),
            )
            conn.commit()
            raise


QUALITY_CHECKS: list[tuple[str, str, str]] = [
    ("duplicate_matches", "error", "SELECT COUNT(*) FROM (SELECT match_id FROM matches GROUP BY match_id HAVING COUNT(*)>1) x"),
    ("unexpected_participant_counts", "warning", "SELECT COUNT(*) FROM matches WHERE participant_count<>10"),
    ("duplicate_participants", "error", "SELECT COUNT(*) FROM (SELECT match_id,participant_id FROM participants GROUP BY 1,2 HAVING COUNT(*)>1) x"),
    ("duplicate_frames", "error", "SELECT COUNT(*) FROM (SELECT match_id,participant_id,frame_index FROM participant_frames GROUP BY 1,2,3 HAVING COUNT(*)>1) x"),
    ("duplicate_events", "error", "SELECT COUNT(*) FROM (SELECT match_id,frame_index,event_index FROM timeline_events GROUP BY 1,2,3 HAVING COUNT(*)>1) x"),
    ("reference_outside_window", "error", "SELECT COUNT(*) FROM target_player_matches t JOIN collection_run_matches c USING(run_id,match_id) WHERE t.run_id=%s AND t.cohort_type='REFERENCE' AND NOT c.in_active_reference_window"),
    ("reference_not_kayle", "error", "SELECT COUNT(*) FROM target_player_matches t JOIN participants p USING(match_id,participant_id) WHERE t.run_id=%s AND t.cohort_type='REFERENCE' AND p.champion_id<>10"),
    ("reference_not_top", "error", "SELECT COUNT(*) FROM target_player_matches t JOIN participants p USING(match_id,participant_id) WHERE t.run_id=%s AND t.cohort_type='REFERENCE' AND COALESCE(NULLIF(p.team_position,''),p.individual_position)<>'TOP'"),
    ("wrong_queue", "error", "SELECT COUNT(*) FROM target_player_matches t JOIN matches m USING(match_id) WHERE t.run_id=%s AND m.queue_id<>420"),
    ("missing_timelines", "warning", "SELECT COUNT(*) FROM target_player_matches t JOIN matches m USING(match_id) WHERE t.run_id=%s AND m.timeline_status<>'COMPLETE'"),
    ("matches_without_frames", "warning", "SELECT COUNT(*) FROM target_player_matches t WHERE t.run_id=%s AND NOT EXISTS (SELECT 1 FROM participant_frames f WHERE f.match_id=t.match_id)"),
    ("insufficient_experience_in_cohort", "error", "SELECT COUNT(*) FROM reference_cohort r LEFT JOIN account_experience_checks e USING(run_id,puuid) WHERE r.run_id=%s AND COALESCE(e.passed,false)=false"),
]


def run_quality_checks(conn: Any, run_id: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, severity, query in QUALITY_CHECKS:
        params = (run_id,) if "%s" in query else ()
        count = int(conn.execute(query, params).fetchone()["count"])
        conn.execute(
            """
            INSERT INTO quality_results (run_id, check_name, severity, failing_count)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (run_id, check_name) DO UPDATE SET
              severity=EXCLUDED.severity, failing_count=EXCLUDED.failing_count,
              checked_at=now()
            """,
            (run_id, name, severity, count),
        )
        results.append({"check": name, "severity": severity, "failing_count": count})
    conn.commit()
    print("\n[data quality]")
    for row in results:
        marker = "OK" if row["failing_count"] == 0 else row["severity"].upper()
        print(f"  {marker:7} {row['check']}: {row['failing_count']}")
    contribution = conn.execute(
        """
        SELECT COALESCE(rank_at_collection,'SELF') AS cohort, puuid,
               COUNT(*) AS games,
               ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER
                     (PARTITION BY COALESCE(rank_at_collection,'SELF')),1) AS share_pct
        FROM target_player_matches WHERE run_id=%s
        GROUP BY 1,2 ORDER BY 1,games DESC
        """,
        (run_id,),
    ).fetchall()
    print("\n[player contribution shares]")
    for row in contribution:
        print(f"  {row['cohort']:12} {row['puuid'][:12]}… {row['games']:4} games ({row['share_pct']}%)")
    return results


def print_status(conn: Any, run_id: int | None = None) -> None:
    if run_id is None:
        row = conn.execute(
            """
            SELECT r.*, ARRAY(
                SELECT p.patch FROM collection_run_patches p
                WHERE p.run_id=r.run_id ORDER BY p.patch_order
            ) AS patch_window
            FROM collection_runs r ORDER BY r.run_id DESC LIMIT 1
            """
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT r.*, ARRAY(
                SELECT p.patch FROM collection_run_patches p
                WHERE p.run_id=r.run_id ORDER BY p.patch_order
            ) AS patch_window
            FROM collection_runs r WHERE r.run_id=%s
            """,
            (run_id,),
        ).fetchone()
    if not row:
        print("No collection run exists yet.")
        return
    print(
        f"Run {row['run_id']} | {row['status']} | active patches: "
        f"{', '.join(row['patch_window'])} | started {row['started_at']}"
    )
    counts = conn.execute(
        """
        SELECT status, COUNT(*) AS count FROM pipeline_internal.collection_tasks
        WHERE run_id=%s GROUP BY status ORDER BY status
        """,
        (row["run_id"],),
    ).fetchall()
    print("Tasks: " + (", ".join(f"{x['status']}={x['count']}" for x in counts) or "none"))
    cohorts = conn.execute(
        "SELECT * FROM v_cohort_summary WHERE run_id=%s ORDER BY cohort_type,cohort_rank",
        (row["run_id"],),
    ).fetchall()
    for cohort in cohorts:
        print(
            f"  {cohort['cohort_type']} {cohort.get('platform_code') or 'ALL'} "
            f"{cohort['cohort_rank']}: "
            f"{cohort['independent_players']} players, {cohort['unique_matches']} matches"
        )
