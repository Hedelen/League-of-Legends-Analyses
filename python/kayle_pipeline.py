"""The one user-facing command for the entire Kayle data project."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from collector import collect_tasks, discover_self_matches, print_status, run_quality_checks
from config import get_settings
from database import (
    apply_sql_files,
    connect,
    create_database_if_needed,
    pending_tasks,
    resumable_run,
    set_run_status,
    start_run,
)
from discovery import evaluate_candidates, select_and_plan, smoke_discovery_flow
from patches import get_live_patch_window
from riot_api import RiotAPI


def init_database(settings: Any) -> None:
    print("[setup] Ensuring the separate kayle_analysis database exists")
    create_database_if_needed(settings.postgres_dsn)
    with connect(settings.postgres_dsn) as conn:
        apply_sql_files(conn)
    print("[setup] Schema and views are ready (existing data was preserved)")


def api_client(settings: Any) -> RiotAPI:
    def retry(details: dict[str, Any]) -> None:
        print(
            f"[Riot retry] reason={details.get('reason')} "
            f"wait={details.get('wait', 0):.1f}s attempt={details.get('attempt')}"
        )

    return RiotAPI(
        settings.api_key,
        settings.platform,
        settings.routing,
        settings.http_timeout_seconds,
        settings.max_http_retries,
        retry,
    )


def get_or_create_run(conn: Any, settings: Any) -> dict[str, Any]:
    row = resumable_run(conn)
    if row:
        print(f"[resume] Using unfinished run {row['run_id']} ({row['status']})")
        return row
    window = get_live_patch_window(settings.http_timeout_seconds)
    run_id = start_run(conn, window, settings)
    print(
        f"[run] Started run {run_id}; live reference window: "
        f"{', '.join(window.patches)}"
    )
    return conn.execute(
        "SELECT * FROM collection_runs WHERE run_id=%s", (run_id,)
    ).fetchone()


def discovery_phase(conn: Any, api: RiotAPI, run: dict[str, Any], settings: Any) -> None:
    run_id = int(run["run_id"])
    set_run_status(conn, run_id, "DISCOVERING")
    try:
        evaluate_candidates(
            conn, api, run_id, tuple(run["patch_window"]), settings
        )
        report = select_and_plan(conn, run_id, settings)
        discover_self_matches(conn, api, run_id, settings)
        set_run_status(conn, run_id, "DISCOVERED")
        print(
            f"[discover] Finished. {report['unique_reference_matches']} unique "
            "reference matches planned; SELF matches were planned separately."
        )
    except Exception as exc:
        conn.rollback()
        set_run_status(conn, run_id, "DISCOVERING", str(exc)[:2000])
        raise RuntimeError(f"Discovery failed: {exc}") from exc


def collection_phase(conn: Any, api: RiotAPI, run: dict[str, Any], settings: Any) -> None:
    run_id = int(run["run_id"])
    set_run_status(conn, run_id, "COLLECTING")
    try:
        collect_tasks(conn, api, run_id, tuple(run["patch_window"]), settings)
        remaining = pending_tasks(conn, run_id)
        run_quality_checks(conn, run_id)
        if remaining:
            print(f"[collect] {len(remaining)} tasks remain; run resume after fixing the error")
        else:
            set_run_status(conn, run_id, "COMPLETED")
            print("[done] Collection run completed safely")
    except Exception as exc:
        conn.rollback()
        set_run_status(conn, run_id, "COLLECTING", str(exc)[:2000])
        raise RuntimeError(f"Collection failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect resumable Kayle TOP SELF and high-Elo reference data"
    )
    parser.add_argument(
        "command",
        choices=(
            "init-db",
            "smoke-discovery",
            "discover",
            "collect",
            "run",
            "resume",
            "status",
            "quality",
        ),
    )
    parser.add_argument("--smoke-count", type=int, default=5)
    args = parser.parse_args()
    require_key = args.command not in ("init-db", "status", "quality")
    settings = get_settings(require_api_key=require_key)

    if args.command == "init-db":
        init_database(settings)
        return 0

    if args.command == "smoke-discovery":
        if not 1 <= args.smoke_count <= 10:
            raise RuntimeError("--smoke-count must be between 1 and 10")
        api = api_client(settings)
        window = get_live_patch_window(settings.http_timeout_seconds)
        reports = smoke_discovery_flow(
            api, settings, tuple(window.patches), args.smoke_count
        )
        print(
            f"[smoke] Fixed OP.GG Kayle IDs -> Riot validation; "
            f"patches={', '.join(window.patches)}"
        )
        for report in reports:
            print(
                f"  {report['riot_id']}: {report['result']}; "
                f"Riot rank={report.get('tier', 'n/a')} "
                f"{report.get('division') or ''}; "
                f"Kayle TOP match={report.get('recent_qualifying_match') or 'not found in first 10'}"
            )
        return 0

    if args.command in ("run", "resume"):
        init_database(settings)

    with connect(settings.postgres_dsn) as conn:
        if args.command == "status":
            print_status(conn)
            return 0
        if args.command == "quality":
            row = conn.execute(
                "SELECT run_id FROM collection_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            if not row:
                print("No collection run exists yet.")
                return 0
            run_quality_checks(conn, int(row["run_id"]))
            return 0

        api = api_client(settings)
        run = get_or_create_run(conn, settings)
        if args.command == "discover":
            discovery_phase(conn, api, run, settings)
            print_status(conn, int(run["run_id"]))
            return 0
        if args.command == "collect":
            if run["status"] == "DISCOVERING":
                raise RuntimeError(
                    "This run has not finished discovery. Run the discover command first, "
                    "or use run/resume to continue automatically."
                )
            collection_phase(conn, api, run, settings)
            print_status(conn, int(run["run_id"]))
            return 0

        # `run` and `resume` are intentionally identical and idempotent.
        if run["status"] == "DISCOVERING":
            discovery_phase(conn, api, run, settings)
            run = conn.execute(
                "SELECT * FROM collection_runs WHERE run_id=%s", (run["run_id"],)
            ).fetchone()
        collection_phase(conn, api, run, settings)
        print_status(conn, int(run["run_id"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped safely. Run the resume command later; completed rows are committed.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print("Progress already committed. Fix the issue and run the resume command.", file=sys.stderr)
        raise SystemExit(1)
