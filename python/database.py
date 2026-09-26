"""PostgreSQL persistence and idempotent per-match transactions."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from normalizer import (
    normalize_events,
    normalize_frames,
    normalize_match,
    normalize_participant_challenges,
    normalize_participant_items,
    normalize_participant_perk_selections,
    normalize_participant_perk_stats,
    normalize_participant_perk_styles,
    normalize_participants,
    normalize_raw_events,
    normalize_raw_frames,
    normalize_raw_participants,
    normalize_raw_teams,
    normalize_team_bans,
    normalize_team_objectives,
    normalize_teams,
    normalize_timeline_event_assists,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row)


def create_database_if_needed(dsn: str) -> None:
    params = conninfo_to_dict(dsn)
    target = params.get("dbname")
    if target != "kayle_analysis":
        raise RuntimeError("Safety stop: only kayle_analysis may be created by this project")
    admin = dict(params)
    admin["dbname"] = "postgres"
    with psycopg.connect(make_conninfo(**admin), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (target,)
        ).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))


def apply_sql_files(conn: psycopg.Connection) -> None:
    for name in ("01_schema.sql", "02_views.sql"):
        script = (PROJECT_ROOT / "sql" / name).read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(script, prepare=False)
        conn.commit()


def _json(value: Any) -> Jsonb:
    return Jsonb(value if value is not None else {})


def start_run(conn: psycopg.Connection, window: Any, settings: Any) -> int:
    row = conn.execute(
        """
        INSERT INTO collection_runs (status, current_patch)
        VALUES ('DISCOVERING', %s)
        RETURNING run_id
        """,
        (window.current,),
    ).fetchone()
    run_id = int(row["run_id"])
    conn.executemany(
        """
        INSERT INTO collection_run_patches (run_id, patch_order, patch)
        VALUES (%s,%s,%s)
        ON CONFLICT (run_id, patch_order) DO UPDATE SET patch=EXCLUDED.patch
        """,
        [(run_id, index, patch) for index, patch in enumerate(window.patches)],
    )
    conn.executemany(
        """
        INSERT INTO collection_run_source_versions (run_id, version_order, source_version)
        VALUES (%s,%s,%s)
        ON CONFLICT (run_id, version_order) DO UPDATE SET source_version=EXCLUDED.source_version
        """,
        [(run_id, index, version) for index, version in enumerate(window.source_versions)],
    )
    setting_rows: list[tuple[int, str, int, str | None]] = []
    for setting_name, value in asdict(settings).items():
        if setting_name in {"api_key", "postgres_dsn"}:
            continue
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        for value_index, item in enumerate(values):
            if isinstance(item, (list, tuple)):
                item = "#".join(str(part) for part in item)
            setting_rows.append((run_id, setting_name, value_index, None if item is None else str(item)))
    if setting_rows:
        conn.executemany(
            """
            INSERT INTO collection_run_settings
              (run_id, setting_name, value_index, setting_value)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (run_id, setting_name, value_index) DO UPDATE SET
              setting_value=EXCLUDED.setting_value
            """,
            setting_rows,
        )
    conn.commit()
    return run_id


def get_run(conn: psycopg.Connection, run_id: int) -> dict[str, Any] | None:
    return conn.execute(
        """
        SELECT r.*,
               ARRAY(
                   SELECT p.patch FROM collection_run_patches p
                   WHERE p.run_id=r.run_id ORDER BY p.patch_order
               ) AS patch_window
        FROM collection_runs r
        WHERE r.run_id=%s
        """,
        (run_id,),
    ).fetchone()


def resumable_run(conn: psycopg.Connection) -> dict[str, Any] | None:
    return conn.execute(
        """
        SELECT r.*,
               ARRAY(
                   SELECT p.patch FROM collection_run_patches p
                   WHERE p.run_id=r.run_id ORDER BY p.patch_order
               ) AS patch_window
        FROM collection_runs r
        WHERE r.status IN ('DISCOVERING','DISCOVERED','COLLECTING','FAILED')
        ORDER BY r.run_id DESC LIMIT 1
        """
    ).fetchone()


def set_run_status(
    conn: psycopg.Connection, run_id: int, status: str, error: str | None = None
) -> None:
    completed = ", completed_at=now()" if status == "COMPLETED" else ""
    conn.execute(
        f"UPDATE collection_runs SET status=%s, last_error=%s {completed} WHERE run_id=%s",
        (status, error, run_id),
    )
    conn.commit()


def upsert_player(
    conn: psycopg.Connection,
    puuid: str,
    player_class: str = "MATCH_CONTEXT",
    account: dict[str, Any] | None = None,
) -> None:
    account = account or {}
    conn.execute(
        """
        INSERT INTO players
          (puuid, riot_id_game_name, riot_id_tagline, player_class)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (puuid) DO UPDATE SET
          riot_id_game_name=COALESCE(EXCLUDED.riot_id_game_name, players.riot_id_game_name),
          riot_id_tagline=COALESCE(EXCLUDED.riot_id_tagline, players.riot_id_tagline),
          player_class=CASE
            WHEN players.player_class='SELF' OR EXCLUDED.player_class='SELF' THEN 'SELF'
            WHEN players.player_class='REFERENCE' OR EXCLUDED.player_class='REFERENCE' THEN 'REFERENCE'
            ELSE 'MATCH_CONTEXT' END,
          last_seen_at=now()
        """,
        (
            puuid,
            account.get("gameName"),
            account.get("tagLine"),
            player_class,
        ),
    )
    if account:
        conn.execute(
            """
            INSERT INTO raw_archive.raw_player_accounts (puuid, payload)
            VALUES (%s,%s)
            ON CONFLICT (puuid) DO UPDATE SET
              payload=CASE WHEN EXCLUDED.payload ? 'puuid'
                           THEN EXCLUDED.payload ELSE raw_player_accounts.payload END,
              stored_at=CASE WHEN EXCLUDED.payload ? 'puuid'
                             THEN now() ELSE raw_player_accounts.stored_at END
            """,
            (puuid, _json(account)),
        )


def add_rank_snapshot(
    conn: psycopg.Connection,
    run_id: int,
    entry: dict[str, Any],
    tier: str,
    source: str,
    platform: str | None = None,
) -> int:
    puuid = entry.get("puuid")
    row = conn.execute(
        """
        INSERT INTO rank_snapshots
          (run_id, puuid, platform_code, tier, division, league_points, wins, losses,
           discovery_source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (run_id, puuid, tier) DO UPDATE SET
          platform_code=EXCLUDED.platform_code,
          division=EXCLUDED.division, league_points=EXCLUDED.league_points,
          wins=EXCLUDED.wins, losses=EXCLUDED.losses,
          discovery_source=EXCLUDED.discovery_source,
          snapshot_at=now()
        RETURNING rank_snapshot_id
        """,
        (
            run_id,
            puuid,
            platform.upper() if platform else None,
            tier,
            entry.get("rank"),
            entry.get("leaguePoints"),
            entry.get("wins"),
            entry.get("losses"),
            source,
        ),
    ).fetchone()
    rank_snapshot_id = int(row["rank_snapshot_id"])
    conn.execute(
        """
        INSERT INTO raw_archive.raw_rank_snapshots (rank_snapshot_id, payload)
        VALUES (%s,%s)
        ON CONFLICT (rank_snapshot_id) DO UPDATE SET
          payload=EXCLUDED.payload, stored_at=now()
        """,
        (rank_snapshot_id, _json(entry)),
    )
    return rank_snapshot_id


def add_mastery(
    conn: psycopg.Connection, run_id: int, puuid: str, mastery: dict[str, Any] | None
) -> int:
    mastery = mastery or {}
    points = int(mastery.get("championPoints") or 0)
    conn.execute(
        """
        INSERT INTO champion_mastery_snapshots
          (run_id, puuid, champion_id, champion_level, champion_points,
           last_play_time_ms)
        VALUES (%s,%s,10,%s,%s,%s)
        ON CONFLICT (run_id, puuid, champion_id) DO UPDATE SET
          champion_level=EXCLUDED.champion_level,
          champion_points=EXCLUDED.champion_points,
          last_play_time_ms=EXCLUDED.last_play_time_ms,
          snapshot_at=now()
        """,
        (
            run_id,
            puuid,
            mastery.get("championLevel"),
            points,
            mastery.get("lastPlayTime"),
        ),
    )
    if mastery:
        conn.execute(
            """
            INSERT INTO raw_archive.raw_mastery_snapshots
              (run_id, puuid, champion_id, payload)
            VALUES (%s,%s,10,%s)
            ON CONFLICT (run_id, puuid, champion_id) DO UPDATE SET
              payload=EXCLUDED.payload, stored_at=now()
            """,
            (run_id, puuid, _json(mastery)),
        )
    return points


def add_experience_check(
    conn: psycopg.Connection,
    run_id: int,
    puuid: str,
    passed: bool,
    method: str,
    count: int,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO account_experience_checks
          (run_id, puuid, passed, evidence_method, observed_game_count)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (run_id, puuid) DO UPDATE SET
          passed=EXCLUDED.passed, evidence_method=EXCLUDED.evidence_method,
          observed_game_count=EXCLUDED.observed_game_count,
          checked_at=now()
        """,
        (run_id, puuid, passed, method, count),
    )
    conn.execute(
        "DELETE FROM account_experience_details WHERE run_id=%s AND puuid=%s",
        (run_id, puuid),
    )
    if details:
        conn.executemany(
            """
            INSERT INTO account_experience_details
              (run_id, puuid, detail_name, detail_value)
            VALUES (%s,%s,%s,%s)
            """,
            [
                (run_id, puuid, key, None if value is None else str(value))
                for key, value in details.items()
            ],
        )


def cache_get(conn: psycopg.Connection, endpoint: str, key: str) -> Any | None:
    row = conn.execute(
        """
        SELECT payload FROM pipeline_internal.api_cache
        WHERE endpoint=%s AND cache_key=%s
          AND (expires_at IS NULL OR expires_at > now())
        """,
        (endpoint, key),
    ).fetchone()
    return row["payload"] if row else None


def cache_put(
    conn: psycopg.Connection,
    endpoint: str,
    key: str,
    payload: Any,
    ttl_interval: str | None = None,
) -> None:
    if ttl_interval is None:
        conn.execute(
            """
            INSERT INTO pipeline_internal.api_cache (endpoint, cache_key, payload, expires_at)
            VALUES (%s,%s,%s,NULL)
            ON CONFLICT (endpoint, cache_key) DO UPDATE SET
              payload=EXCLUDED.payload, fetched_at=now(), expires_at=NULL
            """,
            (endpoint, key, _json(payload)),
        )
    else:
        conn.execute(
            """
            INSERT INTO pipeline_internal.api_cache (endpoint, cache_key, payload, expires_at)
            VALUES (%s,%s,%s,now() + %s::interval)
            ON CONFLICT (endpoint, cache_key) DO UPDATE SET
              payload=EXCLUDED.payload, fetched_at=now(),
              expires_at=EXCLUDED.expires_at
            """,
            (endpoint, key, _json(payload), ttl_interval),
        )
    conn.commit()


def _bulk_upsert(
    conn: psycopg.Connection,
    table: str,
    rows: list[dict[str, Any]],
    key_columns: tuple[str, ...],
    json_columns: set[str],
) -> None:
    if not rows:
        return
    columns = list(rows[0])
    updates = [c for c in columns if c not in key_columns]
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}").format(
        sql.Identifier(*table.split(".")),
        sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.SQL(",").join(sql.Placeholder() for _ in columns),
        sql.SQL(",").join(map(sql.Identifier, key_columns)),
        sql.SQL(",").join(
            sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
            for c in updates
        ),
    )
    values = [
        tuple(_json(row[c]) if c in json_columns else row[c] for c in columns)
        for row in rows
    ]
    with conn.cursor() as cur:
        cur.executemany(query, values)


def store_match_bundle(
    conn: psycopg.Connection,
    match: dict[str, Any],
    timeline: dict[str, Any] | None,
    very_short_seconds: int,
) -> None:
    """Commit a complete match atomically; rerunning updates rather than duplicates."""
    match_row = normalize_match(match, very_short_seconds)
    match_id = match_row.pop("match_id")
    raw_match = match_row.pop("raw_match")
    with conn.transaction():
        for p in match.get("info", {}).get("participants", []):
            puuid = p.get("puuid")
            if puuid:
                upsert_player(
                    conn,
                    puuid,
                    "MATCH_CONTEXT",
                    {"gameName": p.get("riotIdGameName"), "tagLine": p.get("riotIdTagline")},
                )
        conn.execute(
            """
            INSERT INTO matches
              (match_id, platform_id, queue_id, map_id, game_creation, game_start,
               game_end, game_duration_seconds, game_version, patch, game_mode,
               game_type, end_of_game_result, participant_count,
               is_remake_or_short, unexpected_participant_count, timeline_status)
            VALUES
              (%(match_id)s, %(platform_id)s, %(queue_id)s, %(map_id)s,
               to_timestamp(%(game_creation_ms)s/1000.0),
               to_timestamp(%(game_start_ms)s/1000.0),
               CASE WHEN %(game_end_ms)s IS NULL THEN NULL ELSE to_timestamp(%(game_end_ms)s/1000.0) END,
               %(game_duration_seconds)s, %(game_version)s, %(patch)s, %(game_mode)s,
               %(game_type)s, %(end_of_game_result)s, %(participant_count)s,
               %(is_remake_or_short)s, %(unexpected_participant_count)s, %(timeline_status)s)
            ON CONFLICT (match_id) DO UPDATE SET
               platform_id=EXCLUDED.platform_id, queue_id=EXCLUDED.queue_id,
               map_id=EXCLUDED.map_id, game_creation=EXCLUDED.game_creation,
               game_start=EXCLUDED.game_start, game_end=EXCLUDED.game_end,
               game_duration_seconds=EXCLUDED.game_duration_seconds,
               game_version=EXCLUDED.game_version, patch=EXCLUDED.patch,
               game_mode=EXCLUDED.game_mode, game_type=EXCLUDED.game_type,
               end_of_game_result=EXCLUDED.end_of_game_result,
               participant_count=EXCLUDED.participant_count,
               is_remake_or_short=EXCLUDED.is_remake_or_short,
               unexpected_participant_count=EXCLUDED.unexpected_participant_count,
               timeline_status=EXCLUDED.timeline_status, last_collected_at=now()
            """,
            {
                "match_id": match_id,
                **match_row,
                "timeline_status": "COMPLETE" if timeline else "MISSING",
            },
        )
        _bulk_upsert(
            conn,
            "teams",
            normalize_teams(match),
            ("match_id", "team_id"),
            set(),
        )
        for child_table in ("team_objectives", "team_bans"):
            conn.execute(
                sql.SQL("DELETE FROM {} WHERE match_id=%s").format(sql.Identifier(child_table)),
                (match_id,),
            )
        _bulk_upsert(
            conn,
            "team_objectives",
            normalize_team_objectives(match),
            ("match_id", "team_id", "objective_type"),
            set(),
        )
        _bulk_upsert(
            conn,
            "team_bans",
            normalize_team_bans(match),
            ("match_id", "team_id", "pick_turn"),
            set(),
        )
        _bulk_upsert(
            conn,
            "raw_archive.raw_teams",
            normalize_raw_teams(match),
            ("match_id", "team_id"),
            {"payload"},
        )
        _bulk_upsert(
            conn,
            "participants",
            normalize_participants(match),
            ("match_id", "participant_id"),
            set(),
        )
        participant_children = (
            ("participant_items", normalize_participant_items(match), ("match_id", "participant_id", "item_slot"), set()),
            ("participant_perk_stats", normalize_participant_perk_stats(match), ("match_id", "participant_id"), set()),
            ("participant_perk_styles", normalize_participant_perk_styles(match), ("match_id", "participant_id", "style_index"), set()),
            ("participant_perk_selections", normalize_participant_perk_selections(match), ("match_id", "participant_id", "style_index", "selection_index"), set()),
            ("participant_challenges", normalize_participant_challenges(match), ("match_id", "participant_id", "challenge_name"), set()),
        )
        for child_table in (
            "participant_perk_selections",
            "participant_perk_styles",
            "participant_perk_stats",
            "participant_items",
            "participant_challenges",
        ):
            conn.execute(
                sql.SQL("DELETE FROM {} WHERE match_id=%s").format(sql.Identifier(child_table)),
                (match_id,),
            )
        for child_table, child_rows, child_key, json_columns in participant_children:
            _bulk_upsert(conn, child_table, child_rows, child_key, json_columns)
        _bulk_upsert(
            conn,
            "raw_archive.raw_participants",
            normalize_raw_participants(match),
            ("match_id", "participant_id"),
            {"payload"},
        )
        conn.execute(
            """
            INSERT INTO raw_archive.raw_payloads (resource_type, resource_key, payload)
            VALUES ('MATCH',%s,%s)
            ON CONFLICT (resource_type, resource_key) DO UPDATE SET
              payload=EXCLUDED.payload, fetched_at=now()
            """,
            (match_id, _json(raw_match)),
        )
        if timeline:
            _bulk_upsert(
                conn,
                "participant_frames",
                normalize_frames(timeline),
                ("match_id", "participant_id", "frame_index"),
                set(),
            )
            _bulk_upsert(
                conn,
                "raw_archive.raw_participant_frames",
                normalize_raw_frames(timeline),
                ("match_id", "participant_id", "frame_index"),
                {"payload"},
            )
            _bulk_upsert(
                conn,
                "timeline_events",
                normalize_events(timeline),
                ("match_id", "frame_index", "event_index"),
                set(),
            )
            conn.execute("DELETE FROM timeline_event_assists WHERE match_id=%s", (match_id,))
            _bulk_upsert(
                conn,
                "timeline_event_assists",
                normalize_timeline_event_assists(timeline),
                ("match_id", "frame_index", "event_index", "assisting_participant_id"),
                set(),
            )
            _bulk_upsert(
                conn,
                "raw_archive.raw_timeline_events",
                normalize_raw_events(timeline),
                ("match_id", "frame_index", "event_index"),
                {"payload"},
            )
            conn.execute(
                """
                INSERT INTO raw_archive.raw_payloads (resource_type, resource_key, payload)
                VALUES ('TIMELINE',%s,%s)
                ON CONFLICT (resource_type, resource_key) DO UPDATE SET
                  payload=EXCLUDED.payload, fetched_at=now()
                """,
                (match_id, _json(timeline)),
            )


def add_collection_task(
    conn: psycopg.Connection, run_id: int, task_type: str, key: str, payload: dict[str, Any]
) -> None:
    conn.execute(
        """
        INSERT INTO pipeline_internal.collection_tasks (run_id, task_type, task_key, payload)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (run_id, task_type, task_key) DO UPDATE SET
          payload=EXCLUDED.payload,
          status=CASE WHEN collection_tasks.status='COMPLETE' THEN 'COMPLETE' ELSE 'PENDING' END,
          updated_at=now()
        """,
        (run_id, task_type, key, _json(payload)),
    )


def pending_tasks(conn: psycopg.Connection, run_id: int) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT * FROM pipeline_internal.collection_tasks
        WHERE run_id=%s AND status IN ('PENDING','RUNNING','FAILED')
        ORDER BY CASE task_type WHEN 'REFERENCE_MATCH' THEN 1 ELSE 2 END, task_id
        """,
        (run_id,),
    ).fetchall()


def set_task_status(
    conn: psycopg.Connection, task_id: int, status: str, error: str | None = None
) -> None:
    conn.execute(
        """
        UPDATE pipeline_internal.collection_tasks SET status=%s,
          attempts=attempts + CASE WHEN %s='RUNNING' THEN 1 ELSE 0 END,
          last_error=%s, updated_at=now() WHERE task_id=%s
        """,
        (status, status, error, task_id),
    )
    conn.commit()
