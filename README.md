# Kayle TOP longitudinal analysis

This is a separate, direct-to-PostgreSQL collector for learning Kayle TOP. It does **not** read from or write to the existing `riot_analysis` database.

It collects two populations:

- **SELF:** Ranked Solo/Duo Kayle TOP matches for the Riot IDs configured in `SELF_ACCOUNTS`, up to the configured `SELF_MAX_MATCHES` scan limit, then appends new games on later runs.
- **REFERENCE:** NA Emerald, Diamond, Master, Grandmaster, and Challenger Kayle TOP players from the live patch plus the previous two patches.

Every qualifying match stores all 10 participants, every timeline frame for all 10 players, timeline events, normalized analytical columns, and the complete Match-V5 and timeline payloads as JSONB.

## 1. One-time Windows / VS Code setup

Open this `kayle-analysis` folder in VS Code. In **Terminal -> New Terminal**, confirm the terminal says PowerShell, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, run this once in that terminal and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Open `.env` and replace only the placeholders you need:

```dotenv
RIOT_API_KEY=RGAPI-your-key-here
POSTGRES_DSN=postgresql://postgres:your_password@localhost:5432/kayle_analysis
```

Do not paste the API key into Python, SQL, Git, or ChatGPT. Riot development keys expire about every 24 hours; refreshing the value in `.env` is normal.

## 2. Smoke-test discovery, then begin collection

Before the first full run, validate a handful of public Kayle IDs through Riot without writing collection data:

```powershell
py python\kayle_pipeline.py smoke-discovery --smoke-count 5
```

The smoke test resolves each public candidate to a Riot account, checks the current Ranked Solo tier and Kayle mastery, and looks for a recent Riot-confirmed Kayle TOP match. Public leaderboard values are not accepted as analysis data.

This one command creates the **new** `kayle_analysis` database, discovers the cohort, collects the matches/timelines, and runs quality checks:

```powershell
py python\kayle_pipeline.py run
```

Discovery uses a fixed snapshot of the 50 OP.GG NA Kayle leaderboard Riot IDs supplied for this project. It does not scrape additional players. Riot then validates every candidate and supplies every fact used by the database and analysis. The command prints candidate progress, Riot-validated rank, eligible Kayle games, unique-match progress, retries, and the final quality summary. You may stop it with `Ctrl+C` without losing already committed work.

Check progress at any time:

```powershell
py python\kayle_pipeline.py status
```

Resume after interruption or after replacing an expired API key:

```powershell
py python\kayle_pipeline.py resume
```

`run` and `resume` are idempotent: existing matches, participants, frames, and events are updated through stable primary keys rather than duplicated.

Optional phase-by-phase commands:

```powershell
py python\kayle_pipeline.py init-db
py python\kayle_pipeline.py discover
py python\kayle_pipeline.py collect
py python\kayle_pipeline.py quality
```

## 3. How discovery and validation work

The code makes the decision automatically from the data available when you run it:

1. Reads Riot Data Dragon's live version list and converts versions such as `26.18.701...` to patch `26.18`. The first three distinct major/minor patches become the reference window.
2. Reads the fixed top-50 OP.GG Kayle Riot ID snapshot stored in `python/public_sources.py`. No live public-site scrape is performed. OP.GG position, win rate, KDA, games, and displayed rank are not used as analysis facts.
3. Resolves each Riot ID with Account-V1, then uses League-V4 as the source of truth for current Ranked Solo rank. Anyone below Emerald IV or unranked stops here.
4. Applies the remaining cheap-to-expensive checks in order: Riot Kayle mastery, the existing 200-game experience proof, then Match-V5 role/patch screening. Screening stops immediately on a decisive failure or once the minimum qualifying Kayle TOP games is reached.
5. Verifies actual current-window Ranked Solo Kayle TOP games from official Match-V5 payloads. `teamPosition=TOP` is preferred; `individualPosition=TOP` is used only when team position is missing. Conflicts remain flagged and excluded.
6. Screens all 50 listed Riot IDs. Every candidate who passes the existing rules may enter the reference cohort; games are allocated one at a time up to the existing per-rank target and per-player cap.

Default rank target: 300 player-games per rank. The collector may choose fewer when legitimate eligible players/games are unavailable. `candidate_evaluations` records every eligibility and selection reason, and `reference_cohort` records the resulting plan.

This is the evidence-based decision rule; the actual tier mix depends on Riot's live validation and eligible-player availability. The CLI prints the realized plan and stores it in PostgreSQL. Existing rows and resumable run state are preserved when the schema upgrade is applied.

## 4. PostgreSQL table map

| Table | One row represents |
|---|---|
| `collection_runs` | One collection run and its status/current patch |
| `collection_run_patches` | One ordered patch in a run's three-patch window |
| `collection_run_source_versions` | One Data Dragon source version used by a run |
| `collection_run_settings` | One saved non-secret collection setting value |
| `players` | One durable player identity (PUUID) |
| `rank_snapshots` | One player's rank at collection time—not historical match-time rank |
| `champion_mastery_snapshots` | One Kayle mastery observation at collection time |
| `account_experience_checks` | Auditable evidence for the 200-game requirement |
| `account_experience_details` | One named piece of supporting experience evidence |
| `candidate_evaluations` | One discovered candidate's screening and selection result |
| `candidate_qualifying_matches` | One Riot-validated qualifying match found for a candidate |
| `reference_cohort` | One selected reference player and planned contribution |
| `matches` | One unique match |
| `teams` | One team in one match: identity plus win/loss only |
| `team_objectives` | One objective type for one team (`dragon`, `tower`, `baron`, and so on) |
| `team_bans` | One champion ban made by one team in one match |
| `participants` | One of the 10 participants in one match |
| `participant_items` | One inventory slot for one participant |
| `participant_perk_stats` | One participant's three stat-shard choices |
| `participant_perk_styles` | One primary or secondary rune style for one participant |
| `participant_perk_selections` | One selected rune inside a participant's rune style |
| `participant_challenges` | One named Riot challenge metric for one participant |
| `target_player_matches` | One SELF/REFERENCE target associated with a qualifying match |
| `participant_frames` | One match x one participant x one timeline frame |
| `timeline_events` | One event at its stable frame/event position |
| `timeline_event_assists` | One assisting participant attached to one timeline event |
| `quality_results` | One automated quality check result per run |
| `quality_result_details` | One named detail attached to a quality check |

Useful views include `v_teams_analysis`, `v_team_bans_analysis`, `v_self_kayle_games`, `v_reference_kayle_games`, `v_kayle_lane_matchups`, `v_kayle_lane_frames`, `v_team_frame_totals`, `v_cohort_summary`, `v_discovery_report`, `v_player_contribution`, and `v_clean_modeling_games`. They remain optional conveniences; the actual stored data is relational and can be queried directly from the tables above.

The `public` schema is the complete analyst-facing ERD. It contains no JSON or array columns: objectives, bans, items, perks, challenges, qualifying match IDs, patch windows, event assistants, and supporting details are all ordinary child rows connected by foreign keys.

Lossless Riot source documents live separately under `raw_archive`, and resumable API/cache machinery lives under `pipeline_internal`. Those two technical schemas may contain JSON because their job is preserving changing API documents, not analysis. Keeping them separate prevents raw infrastructure from looking like a table-inside-a-table in the `public` ERD.

After receiving this schema update, run the migration once from the project root:

```powershell
py python\kayle_pipeline.py init-db
```

Then refresh the `public` schema in DBeaver with `F5` and reopen the ER diagram. The migration copies existing nested values into their child tables before removing the old embedded columns.

## 5. Open the database in DBeaver and practice SQL

### What is already done

The Python collector writes **directly into PostgreSQL** through `POSTGRES_DSN`. When the terminal says `[done] Collection run completed safely`, the data is already inside the `kayle_analysis` SQL database. There is no CSV import, restore, or second loading step.

PostgreSQL is the database server that stores the data. DBeaver is the desktop client used to browse the relational structure, draw an ER diagram, and run SQL against that same database.

### Connect DBeaver to `kayle_analysis`

1. Make sure the PostgreSQL Windows service is running.
2. Open DBeaver and choose **Database -> New Database Connection**.
3. Select **PostgreSQL**.
4. Enter these settings:

| DBeaver field | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `kayle_analysis` |
| Username | `postgres` |
| Password | The PostgreSQL password used in `POSTGRES_DSN` |

5. Select **Test Connection**. Allow DBeaver to download its PostgreSQL driver if prompted.
6. Select **Finish**.

Do not place the Riot API key in DBeaver. DBeaver needs only the PostgreSQL connection information.

In DBeaver's Database Navigator, expand:

```text
kayle_analysis
└── Databases
    └── kayle_analysis
        └── Schemas
            └── public
                ├── Tables
                └── Views
```

If new tables or rows do not appear, right-click `public`, `Tables`, or the connection and select **Refresh** (or press `F5`).

### Create the relational diagram

1. Expand `Schemas -> public -> Tables`.
2. Select the tables you want, or select the `Tables` folder.
3. Right-click and choose **View Diagram**. Depending on the DBeaver version, this may appear as **ER Diagram**.
4. Start with these core relational tables. DBeaver will draw every listed branch from its declared foreign keys:

```text
players
rank_snapshots
champion_mastery_snapshots
account_experience_checks
account_experience_details
candidate_evaluations
candidate_qualifying_matches
matches
teams
team_objectives
team_bans
participants
participant_items
participant_perk_stats
participant_perk_styles
participant_perk_selections
participant_challenges
target_player_matches
participant_frames
timeline_events
timeline_event_assists
collection_runs
collection_run_patches
collection_run_source_versions
collection_run_settings
quality_results
quality_result_details
```

The main relationships to notice are:

- `matches` -> `teams` -> `team_objectives` and `team_bans`
- `teams` -> `participants` -> items, perks, and challenges
- `participants` -> `participant_frames`
- `matches` -> `timeline_events` -> event assistants
- `players` + `matches` -> `target_player_matches`
- `collection_runs` -> `target_player_matches`

DBeaver reads these relationships from the primary and foreign keys already created by `sql/01_schema.sql`; you do not need to create the relationships manually.

### Open a SQL practice script

Right-click the `kayle_analysis` connection and choose **SQL Editor -> New SQL Script**. Run only the highlighted statement with `Ctrl+Enter`. Keep each exercise in the same script and comment your notes with `--`.

Confirm that the editor is connected to the correct database:

```sql
SELECT current_database(), current_schema();
```

The result should be `kayle_analysis` and `public`.

Confirm that the collector loaded data:

```sql
SELECT 'matches' AS table_name, COUNT(*) AS rows FROM matches
UNION ALL
SELECT 'participants', COUNT(*) FROM participants
UNION ALL
SELECT 'participant_frames', COUNT(*) FROM participant_frames
UNION ALL
SELECT 'timeline_events', COUNT(*) FROM timeline_events
UNION ALL
SELECT 'target_player_matches', COUNT(*) FROM target_player_matches;
```

Check that normal matches have ten participant rows:

```sql
SELECT
    match_id,
    COUNT(*) AS participant_count
FROM participants
GROUP BY match_id
ORDER BY participant_count, match_id;
```

### Beginner SQL practice path

Work through Questions 1–6 before scrolling to the answer key. Write and run your own query for each question, then compare your approach with the corresponding answer.

#### Questions

##### Question 1 — `SELECT`, `WHERE`, `ORDER BY`, and `LIMIT`

Show matches from patch `16.18`, with the longest games first. Return the match ID, start time, patch, and duration, and limit the output to 20 games.

##### Question 2 — Aggregate one table

For Kayle participants, calculate the number of games and the average kills, deaths, assists, total CS, gold earned, and damage to champions.

##### Question 3 — Join tables

Join `target_player_matches` to `participants` and compare the SELF and REFERENCE cohorts by games played and average KDA. Treat zero deaths as one when calculating KDA so the query does not divide by zero.

##### Question 4 — Build a CTE analysis pipeline

Build a CTE containing Kayle game-level statistics. Then compare each cohort and rank by game count, win rate, average KDA, and average CS per minute. Label the SELF rank as `SELF`.

##### Question 5 — Timeline and lane analysis

Use a CTE and `ROW_NUMBER()` to select the timeline frame closest to 10 minutes for each Kayle game. Then compare SELF against REFERENCE average gold, XP, and CS difference at that point.

##### Question 6 — Window functions

Select the most recent SELF Kayle game, then use `LAG()` to calculate how much gold was gained between each pair of timeline frames.

#### Answer key

Try all six questions before using this section. Different valid SQL approaches may produce the same result.

##### Answer 1

```sql
SELECT
    match_id,
    game_start,
    patch,
    game_duration_seconds
FROM matches
WHERE patch = '16.18'
ORDER BY game_duration_seconds DESC
LIMIT 20;
```

##### Answer 2

```sql
SELECT
    COUNT(*) AS games,
    ROUND(AVG(kills), 2) AS avg_kills,
    ROUND(AVG(deaths), 2) AS avg_deaths,
    ROUND(AVG(assists), 2) AS avg_assists,
    ROUND(AVG(total_minions_killed + neutral_minions_killed), 2) AS avg_total_cs,
    ROUND(AVG(gold_earned), 2) AS avg_gold,
    ROUND(AVG(total_damage_to_champions), 2) AS avg_champion_damage
FROM participants
WHERE champion_name = 'Kayle';
```

##### Answer 3

```sql
SELECT
    t.cohort_type,
    COUNT(*) AS games,
    ROUND(
        AVG((p.kills + p.assists)::numeric / GREATEST(p.deaths, 1)),
        2
    ) AS avg_kda
FROM target_player_matches AS t
JOIN participants AS p
    ON p.match_id = t.match_id
   AND p.participant_id = t.participant_id
GROUP BY t.cohort_type
ORDER BY t.cohort_type;
```

##### Answer 4

```sql
WITH kayle_games AS (
    SELECT
        cohort_type,
        rank_at_collection,
        win,
        kills,
        deaths,
        assists,
        total_minions_killed + neutral_minions_killed AS total_cs,
        game_duration_seconds / 60.0 AS game_minutes
    FROM v_kayle_target_games
)
SELECT
    cohort_type,
    COALESCE(rank_at_collection, 'SELF') AS cohort_rank,
    COUNT(*) AS games,
    ROUND(100.0 * AVG(win::int), 1) AS win_rate_pct,
    ROUND(AVG((kills + assists)::numeric / GREATEST(deaths, 1)), 2) AS avg_kda,
    ROUND(AVG(total_cs / NULLIF(game_minutes, 0)), 2) AS avg_cs_per_minute
FROM kayle_games
GROUP BY cohort_type, COALESCE(rank_at_collection, 'SELF')
ORDER BY cohort_type, cohort_rank;
```

##### Answer 5

```sql
WITH ranked_frames AS (
    SELECT
        run_id,
        cohort_type,
        puuid,
        match_id,
        timestamp_ms,
        gold_diff,
        xp_diff,
        cs_diff,
        ROW_NUMBER() OVER (
            PARTITION BY run_id, puuid, match_id
            ORDER BY ABS(timestamp_ms - 600000)
        ) AS frame_distance_rank
    FROM v_kayle_lane_frames
)
SELECT
    cohort_type,
    COUNT(*) AS games,
    ROUND(AVG(gold_diff), 2) AS avg_gold_diff_at_10,
    ROUND(AVG(xp_diff), 2) AS avg_xp_diff_at_10,
    ROUND(AVG(cs_diff), 2) AS avg_cs_diff_at_10
FROM ranked_frames
WHERE frame_distance_rank = 1
GROUP BY cohort_type
ORDER BY cohort_type;
```

##### Answer 6

```sql
WITH target_game AS (
    SELECT
        match_id,
        participant_id
    FROM v_self_kayle_games
    ORDER BY game_start DESC
    LIMIT 1
),
frame_changes AS (
    SELECT
        f.match_id,
        f.participant_id,
        f.timestamp_ms,
        f.total_gold,
        LAG(f.total_gold) OVER (
            PARTITION BY f.match_id, f.participant_id
            ORDER BY f.timestamp_ms
        ) AS previous_total_gold
    FROM participant_frames AS f
    JOIN target_game AS t
        ON t.match_id = f.match_id
       AND t.participant_id = f.participant_id
)
SELECT
    match_id,
    ROUND(timestamp_ms / 60000.0, 1) AS minute,
    total_gold,
    previous_total_gold,
    total_gold - previous_total_gold AS gold_gained_since_prior_frame
FROM frame_changes
ORDER BY timestamp_ms;
```

### Useful starting views

Use the normalized views when practicing analysis; use the base tables when practicing joins and database structure.

| View | Best use |
|---|---|
| `v_teams_analysis` | Flat team wins and objective outcomes with no JSON columns |
| `v_team_bans_analysis` | One flat row per team ban |
| `v_self_kayle_games` | Your Kayle games only |
| `v_reference_kayle_games` | Reference-player Kayle games only |
| `v_kayle_target_games` | SELF and REFERENCE games together |
| `v_kayle_lane_matchups` | Kayle plus the opposing TOP participant |
| `v_kayle_lane_frames` | Kayle/opponent gold, XP, and CS by timeline frame |
| `v_team_frame_totals` | Team-level gold, XP, and CS by frame |
| `v_discovery_report` | Candidate eligibility and selection results |
| `v_clean_modeling_games` | Analysis-ready games after quality exclusions |

`sql/03_explore.sql` contains additional commented examples. `sql/04_quality_checks.sql` contains the detailed validation queries.

### Safe practice rule

While learning, stay with `SELECT` statements and CTEs beginning with `WITH`. Do not run `DROP`, `TRUNCATE`, `DELETE`, `UPDATE`, `INSERT`, or `ALTER` against this database unless you deliberately intend to change stored data. If you make a mistake in a practice query, PostgreSQL normally returns an error without changing anything.

### Add future games

You do not import future data through DBeaver. Update the Riot key in `.env` when needed and run:

```powershell
py python\kayle_pipeline.py resume
```

The collector writes new rows into the same `kayle_analysis` database and preserves existing rows. Refresh the DBeaver connection afterward to see them.

## 6. Keep the GitHub portfolio current

The public repository contains only the portfolio-safe files you have chosen to publish. Your SQL practice is currently kept private, along with your live `.env`, Riot API key, PostgreSQL password, virtual environment, database contents, Python caches, and private raw data.

After your local project folder is connected to the GitHub repository, use this routine whenever you finish a meaningful piece of work:

```powershell
git status
git add .
git commit -m "Describe the SQL, analysis, or model change"
git push
```

Good commit checkpoints include:

- completing a group of SQL practice exercises;
- adding a documented analytical view or KPI;
- adding a quality check or test;
- finishing a visualization or predictive-modeling stage;
- improving the README with findings and methodology.

Before every push, read the `git status` output and confirm `.env`, `.venv`, `__pycache__`, and local data files are not staged. Never use `git add -f .env`.

The PostgreSQL database stays local. GitHub stores the instructions and code needed to reproduce it, which is the appropriate portfolio format and avoids publishing player data dumps or secrets.

## 7. Reliability and interpretation

- Match and timeline payloads are immutable and cached; a match shared by multiple targets is normalized once and associated many-to-many.
- Each match is committed atomically. Interruption cannot leave half of its participants or frames committed.
- `429` honors `Retry-After`; application/method rate headers are monitored; temporary network/5xx failures use bounded exponential backoff with jitter.
- A `403` stops clearly. Update only `RIOT_API_KEY` and use `resume`.
- Short games, missing timelines, role ambiguity, and unexpected participant counts are retained and flagged. The clean view excludes them; raw tables do not.
- Riot exposes recent retrievable match history, not a guaranteed lifetime archive.
- Rank is measured at collection time and must not be interpreted as the player's rank on the historical match date.
- Timeline frames are periodic snapshots. They show state at each supplied timestamp, not every action between snapshots; discrete actions come from `timeline_events` when Riot emits them.
- Later modeling should split/cross-validate by `puuid`, not randomly by game, to prevent the same player leaking into train and test sets.

## 8. Validation performed without secrets

Run the local sanitized fixture tests:

```powershell
py -m unittest discover -s tests -v
```

They verify 10-participant normalization, all frames for all participants, event retention, raw future-field preservation, patch parsing, TOP fallback/conflict behavior, and idempotent schema keys. A live API/database acceptance test still requires your local Riot key and PostgreSQL service.
