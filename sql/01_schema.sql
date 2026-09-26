-- Idempotent schema and migrations. Legacy nested columns are backfilled into
-- relational/raw child tables before removal, so ordinary reruns preserve data.

CREATE SCHEMA IF NOT EXISTS raw_archive;
CREATE SCHEMA IF NOT EXISTS pipeline_internal;

-- Keep JSON source documents and resumable pipeline state out of the analyst ERD.
DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'raw_player_accounts', 'raw_rank_snapshots', 'raw_mastery_snapshots',
        'raw_teams', 'raw_participants', 'raw_participant_frames',
        'raw_timeline_events', 'raw_payloads'
    ] LOOP
        IF to_regclass('public.' || table_name) IS NOT NULL
           AND to_regclass('raw_archive.' || table_name) IS NULL THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA raw_archive', table_name);
        END IF;
    END LOOP;

    FOREACH table_name IN ARRAY ARRAY['api_cache', 'collection_tasks', 'api_failures'] LOOP
        IF to_regclass('public.' || table_name) IS NOT NULL
           AND to_regclass('pipeline_internal.' || table_name) IS NULL THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA pipeline_internal', table_name);
        END IF;
    END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'STARTED'
        CHECK (status IN ('STARTED','DISCOVERING','DISCOVERED','COLLECTING','COMPLETED','FAILED')),
    current_patch TEXT NOT NULL,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS collection_run_patches (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    patch_order INTEGER NOT NULL,
    patch TEXT NOT NULL,
    PRIMARY KEY (run_id, patch_order),
    UNIQUE (run_id, patch)
);

CREATE TABLE IF NOT EXISTS collection_run_source_versions (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    version_order INTEGER NOT NULL,
    source_version TEXT NOT NULL,
    PRIMARY KEY (run_id, version_order)
);

CREATE TABLE IF NOT EXISTS collection_run_settings (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    setting_name TEXT NOT NULL,
    value_index INTEGER NOT NULL DEFAULT 0,
    setting_value TEXT,
    PRIMARY KEY (run_id, setting_name, value_index)
);

CREATE TABLE IF NOT EXISTS platform_regions (
    platform_code TEXT PRIMARY KEY,
    routing_region TEXT NOT NULL,
    display_name TEXT NOT NULL
);

INSERT INTO platform_regions (platform_code, routing_region, display_name) VALUES
    ('BR1','americas','Brazil'),
    ('EUN1','europe','Europe Nordic & East'),
    ('EUW1','europe','Europe West'),
    ('JP1','asia','Japan'),
    ('KR','asia','Korea'),
    ('LA1','americas','Latin America North'),
    ('LA2','americas','Latin America South'),
    ('ME1','europe','Middle East'),
    ('NA1','americas','North America'),
    ('OC1','sea','Oceania'),
    ('PH2','sea','Philippines'),
    ('RU','europe','Russia'),
    ('SG2','sea','Singapore'),
    ('TH2','sea','Thailand'),
    ('TR1','europe','Turkey'),
    ('TW2','sea','Taiwan'),
    ('VN2','sea','Vietnam')
ON CONFLICT (platform_code) DO UPDATE SET
    routing_region=EXCLUDED.routing_region,
    display_name=EXCLUDED.display_name;

-- Convert existing run arrays/JSON into ordinary child rows before removal.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'collection_runs' AND column_name = 'patch_window'
    ) THEN
        EXECUTE $sql$
            INSERT INTO collection_run_patches (run_id, patch_order, patch)
            SELECT r.run_id, patch.ordinality::integer - 1, patch.value
            FROM collection_runs AS r
            CROSS JOIN LATERAL unnest(r.patch_window) WITH ORDINALITY AS patch(value, ordinality)
            ON CONFLICT (run_id, patch_order) DO UPDATE SET patch = EXCLUDED.patch
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'collection_runs' AND column_name = 'patch_source'
    ) THEN
        EXECUTE $sql$
            INSERT INTO collection_run_source_versions (run_id, version_order, source_version)
            SELECT r.run_id, version.ordinality::integer - 1, version.value #>> '{}'
            FROM collection_runs AS r
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(r.patch_source -> 'data_dragon_versions', '[]'::jsonb)
            ) WITH ORDINALITY AS version(value, ordinality)
            ON CONFLICT (run_id, version_order) DO UPDATE SET source_version = EXCLUDED.source_version
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'collection_runs' AND column_name = 'config'
    ) THEN
        EXECUTE $sql$
            INSERT INTO collection_run_settings (run_id, setting_name, value_index, setting_value)
            SELECT r.run_id, setting.key, 0, setting.value::text
            FROM collection_runs AS r
            CROSS JOIN LATERAL jsonb_each(COALESCE(r.config, '{}'::jsonb)) AS setting(key, value)
            ON CONFLICT (run_id, setting_name, value_index) DO UPDATE SET
                setting_value = EXCLUDED.setting_value
        $sql$;
    END IF;
END $$;

DROP VIEW IF EXISTS v_current_active_reference_window;

ALTER TABLE collection_runs
    DROP COLUMN IF EXISTS patch_window,
    DROP COLUMN IF EXISTS patch_source,
    DROP COLUMN IF EXISTS config;

CREATE TABLE IF NOT EXISTS players (
    puuid TEXT PRIMARY KEY,
    riot_id_game_name TEXT,
    riot_id_tagline TEXT,
    player_class TEXT NOT NULL DEFAULT 'MATCH_CONTEXT'
        CHECK (player_class IN ('SELF','REFERENCE','MATCH_CONTEXT')),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_archive.raw_player_accounts (
    puuid TEXT PRIMARY KEY REFERENCES players(puuid) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Move legacy embedded account JSON into its own archival child table.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'players' AND column_name = 'raw_account'
    ) THEN
        EXECUTE $sql$
            INSERT INTO raw_archive.raw_player_accounts (puuid, payload)
            SELECT puuid, raw_account FROM players
            WHERE raw_account <> '{}'::jsonb
            ON CONFLICT (puuid) DO UPDATE SET payload = EXCLUDED.payload, stored_at = now()
        $sql$;
    END IF;
END $$;

ALTER TABLE players DROP COLUMN IF EXISTS raw_account;

CREATE TABLE IF NOT EXISTS rank_snapshots (
    rank_snapshot_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    puuid TEXT NOT NULL REFERENCES players(puuid),
    queue_type TEXT NOT NULL DEFAULT 'RANKED_SOLO_5x5',
    platform_code TEXT REFERENCES platform_regions(platform_code),
    tier TEXT NOT NULL,
    division TEXT,
    league_points INTEGER,
    wins INTEGER,
    losses INTEGER,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    discovery_source TEXT NOT NULL,
    UNIQUE (run_id, puuid, tier)
);

CREATE TABLE IF NOT EXISTS raw_archive.raw_rank_snapshots (
    rank_snapshot_id BIGINT PRIMARY KEY REFERENCES rank_snapshots(rank_snapshot_id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'rank_snapshots' AND column_name = 'raw_snapshot'
    ) THEN
        EXECUTE $sql$
            INSERT INTO raw_archive.raw_rank_snapshots (rank_snapshot_id, payload)
            SELECT rank_snapshot_id, raw_snapshot FROM rank_snapshots
            ON CONFLICT (rank_snapshot_id) DO UPDATE SET payload = EXCLUDED.payload, stored_at = now()
        $sql$;
    END IF;
END $$;

ALTER TABLE rank_snapshots DROP COLUMN IF EXISTS raw_snapshot;

CREATE TABLE IF NOT EXISTS champion_mastery_snapshots (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    puuid TEXT NOT NULL REFERENCES players(puuid),
    champion_id INTEGER NOT NULL,
    champion_level INTEGER,
    champion_points BIGINT,
    last_play_time_ms BIGINT,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, puuid, champion_id)
);

CREATE TABLE IF NOT EXISTS raw_archive.raw_mastery_snapshots (
    run_id BIGINT NOT NULL,
    puuid TEXT NOT NULL,
    champion_id INTEGER NOT NULL,
    payload JSONB NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, puuid, champion_id),
    FOREIGN KEY (run_id, puuid, champion_id)
        REFERENCES champion_mastery_snapshots(run_id, puuid, champion_id) ON DELETE CASCADE
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'champion_mastery_snapshots' AND column_name = 'raw_mastery'
    ) THEN
        EXECUTE $sql$
            INSERT INTO raw_archive.raw_mastery_snapshots (run_id, puuid, champion_id, payload)
            SELECT run_id, puuid, champion_id, raw_mastery FROM champion_mastery_snapshots
            WHERE raw_mastery <> '{}'::jsonb
            ON CONFLICT (run_id, puuid, champion_id) DO UPDATE SET
                payload = EXCLUDED.payload, stored_at = now()
        $sql$;
    END IF;
END $$;

ALTER TABLE champion_mastery_snapshots DROP COLUMN IF EXISTS raw_mastery;

CREATE TABLE IF NOT EXISTS account_experience_checks (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    puuid TEXT NOT NULL REFERENCES players(puuid),
    passed BOOLEAN NOT NULL,
    evidence_method TEXT NOT NULL,
    observed_game_count INTEGER NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, puuid)
);

CREATE TABLE IF NOT EXISTS account_experience_details (
    run_id BIGINT NOT NULL,
    puuid TEXT NOT NULL,
    detail_name TEXT NOT NULL,
    detail_value TEXT,
    PRIMARY KEY (run_id, puuid, detail_name),
    FOREIGN KEY (run_id, puuid)
        REFERENCES account_experience_checks(run_id, puuid) ON DELETE CASCADE
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'account_experience_checks' AND column_name = 'details'
    ) THEN
        EXECUTE $sql$
            INSERT INTO account_experience_details (run_id, puuid, detail_name, detail_value)
            SELECT e.run_id, e.puuid, detail.key, detail.value::text
            FROM account_experience_checks AS e
            CROSS JOIN LATERAL jsonb_each(COALESCE(e.details, '{}'::jsonb)) AS detail(key, value)
            ON CONFLICT (run_id, puuid, detail_name) DO UPDATE SET detail_value = EXCLUDED.detail_value
        $sql$;
    END IF;
END $$;

ALTER TABLE account_experience_checks DROP COLUMN IF EXISTS details;

CREATE TABLE IF NOT EXISTS candidate_evaluations (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    puuid TEXT NOT NULL REFERENCES players(puuid),
    discovery_source TEXT,
    source_region TEXT,
    platform_code TEXT REFERENCES platform_regions(platform_code),
    routing_region TEXT,
    source_position INTEGER,
    tier TEXT NOT NULL,
    division TEXT,
    league_points INTEGER,
    kayle_mastery_points BIGINT,
    ranked_games_in_window INTEGER NOT NULL DEFAULT 0,
    qualifying_kayle_top_games INTEGER NOT NULL DEFAULT 0,
    kayle_top_play_rate NUMERIC,
    experience_passed BOOLEAN NOT NULL DEFAULT false,
    eligible BOOLEAN NOT NULL DEFAULT false,
    selected BOOLEAN NOT NULL DEFAULT false,
    planned_game_count INTEGER NOT NULL DEFAULT 0,
    selection_reason TEXT,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, puuid)
);

CREATE TABLE IF NOT EXISTS candidate_qualifying_matches (
    run_id BIGINT NOT NULL,
    puuid TEXT NOT NULL,
    match_order INTEGER NOT NULL,
    match_id TEXT NOT NULL,
    PRIMARY KEY (run_id, puuid, match_order),
    UNIQUE (run_id, puuid, match_id),
    FOREIGN KEY (run_id, puuid)
        REFERENCES candidate_evaluations(run_id, puuid) ON DELETE CASCADE
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'candidate_evaluations'
          AND column_name = 'qualifying_match_ids'
    ) THEN
        EXECUTE $sql$
            INSERT INTO candidate_qualifying_matches (run_id, puuid, match_order, match_id)
            SELECT c.run_id, c.puuid, match.ordinality::integer - 1, match.value
            FROM candidate_evaluations AS c
            CROSS JOIN LATERAL unnest(c.qualifying_match_ids) WITH ORDINALITY AS match(value, ordinality)
            ON CONFLICT (run_id, puuid, match_order) DO UPDATE SET match_id = EXCLUDED.match_id
        $sql$;
    END IF;
END $$;

ALTER TABLE candidate_evaluations DROP COLUMN IF EXISTS qualifying_match_ids;

-- Non-destructive upgrade for databases created before Kayle-first discovery.
ALTER TABLE candidate_evaluations
    ADD COLUMN IF NOT EXISTS discovery_source TEXT;
ALTER TABLE candidate_evaluations
    ADD COLUMN IF NOT EXISTS source_position INTEGER;
ALTER TABLE candidate_evaluations
    ADD COLUMN IF NOT EXISTS source_region TEXT;
ALTER TABLE candidate_evaluations
    ADD COLUMN IF NOT EXISTS platform_code TEXT;
ALTER TABLE candidate_evaluations
    ADD COLUMN IF NOT EXISTS routing_region TEXT;

ALTER TABLE rank_snapshots
    ADD COLUMN IF NOT EXISTS platform_code TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_rank_snapshots_platform') THEN
        ALTER TABLE rank_snapshots ADD CONSTRAINT fk_rank_snapshots_platform
            FOREIGN KEY (platform_code) REFERENCES platform_regions(platform_code);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_candidate_evaluations_platform') THEN
        ALTER TABLE candidate_evaluations ADD CONSTRAINT fk_candidate_evaluations_platform
            FOREIGN KEY (platform_code) REFERENCES platform_regions(platform_code);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS reference_cohort (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    puuid TEXT NOT NULL REFERENCES players(puuid),
    tier TEXT NOT NULL,
    division TEXT,
    platform_code TEXT REFERENCES platform_regions(platform_code),
    rank_snapshot_id BIGINT REFERENCES rank_snapshots(rank_snapshot_id),
    planned_game_count INTEGER NOT NULL,
    selection_reason TEXT NOT NULL,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, puuid)
);

ALTER TABLE reference_cohort ADD COLUMN IF NOT EXISTS platform_code TEXT;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_reference_cohort_platform') THEN
        ALTER TABLE reference_cohort ADD CONSTRAINT fk_reference_cohort_platform
            FOREIGN KEY (platform_code) REFERENCES platform_regions(platform_code);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    platform_id TEXT,
    queue_id INTEGER,
    map_id INTEGER,
    game_creation TIMESTAMPTZ,
    game_start TIMESTAMPTZ,
    game_end TIMESTAMPTZ,
    game_duration_seconds INTEGER,
    game_version TEXT,
    patch TEXT,
    game_mode TEXT,
    game_type TEXT,
    end_of_game_result TEXT,
    participant_count INTEGER,
    is_remake_or_short BOOLEAN NOT NULL DEFAULT false,
    unexpected_participant_count BOOLEAN NOT NULL DEFAULT false,
    timeline_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (timeline_status IN ('PENDING','COMPLETE','MISSING','FAILED')),
    first_collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_collected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

UPDATE matches SET platform_id=upper(platform_id) WHERE platform_id IS NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_matches_platform') THEN
        ALTER TABLE matches ADD CONSTRAINT fk_matches_platform
            FOREIGN KEY (platform_id) REFERENCES platform_regions(platform_code);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS collection_run_matches (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    in_active_reference_window BOOLEAN NOT NULL,
    discovered_for TEXT NOT NULL,
    PRIMARY KEY (run_id, match_id)
);

CREATE TABLE IF NOT EXISTS teams (
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL,
    win BOOLEAN,
    PRIMARY KEY (match_id, team_id)
);

CREATE TABLE IF NOT EXISTS team_objectives (
    match_id TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    objective_type TEXT NOT NULL,
    first BOOLEAN,
    kills INTEGER,
    PRIMARY KEY (match_id, team_id, objective_type),
    FOREIGN KEY (match_id, team_id)
        REFERENCES teams(match_id, team_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS team_bans (
    match_id TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    pick_turn INTEGER NOT NULL,
    champion_id INTEGER,
    PRIMARY KEY (match_id, team_id, pick_turn),
    FOREIGN KEY (match_id, team_id)
        REFERENCES teams(match_id, team_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_archive.raw_teams (
    match_id TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    payload JSONB NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, team_id),
    FOREIGN KEY (match_id, team_id)
        REFERENCES teams(match_id, team_id) ON DELETE CASCADE
);

-- Backfill the relational team children before removing legacy embedded columns.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'teams' AND column_name = 'objectives'
    ) THEN
        EXECUTE $sql$
            INSERT INTO team_objectives (match_id, team_id, objective_type, first, kills)
            SELECT t.match_id, t.team_id, objective.key,
                   (objective.value ->> 'first')::boolean,
                   (objective.value ->> 'kills')::integer
            FROM teams AS t
            CROSS JOIN LATERAL jsonb_each(COALESCE(t.objectives, '{}'::jsonb)) AS objective(key, value)
            ON CONFLICT (match_id, team_id, objective_type) DO UPDATE SET
                first = EXCLUDED.first, kills = EXCLUDED.kills
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'teams' AND column_name = 'bans'
    ) THEN
        EXECUTE $sql$
            INSERT INTO team_bans (match_id, team_id, pick_turn, champion_id)
            SELECT t.match_id, t.team_id,
                   COALESCE((ban.value ->> 'pickTurn')::integer, ban.ordinality::integer),
                   (ban.value ->> 'championId')::integer
            FROM teams AS t
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.bans, '[]'::jsonb))
                WITH ORDINALITY AS ban(value, ordinality)
            ON CONFLICT (match_id, team_id, pick_turn) DO UPDATE SET
                champion_id = EXCLUDED.champion_id
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'teams' AND column_name = 'raw_team'
    ) THEN
        EXECUTE $sql$
            INSERT INTO raw_archive.raw_teams (match_id, team_id, payload)
            SELECT match_id, team_id, raw_team FROM teams
            ON CONFLICT (match_id, team_id) DO UPDATE SET payload = EXCLUDED.payload, stored_at = now()
        $sql$;
    END IF;
END $$;

-- Recreate this compatibility view in 02_views.sql after its old column dependencies are removed.
DROP VIEW IF EXISTS v_teams_analysis;

ALTER TABLE teams
    DROP COLUMN IF EXISTS objectives,
    DROP COLUMN IF EXISTS bans,
    DROP COLUMN IF EXISTS raw_team,
    DROP COLUMN IF EXISTS baron_first,
    DROP COLUMN IF EXISTS baron_kills,
    DROP COLUMN IF EXISTS champion_first,
    DROP COLUMN IF EXISTS champion_kills,
    DROP COLUMN IF EXISTS dragon_first,
    DROP COLUMN IF EXISTS dragon_kills,
    DROP COLUMN IF EXISTS horde_first,
    DROP COLUMN IF EXISTS horde_kills,
    DROP COLUMN IF EXISTS inhibitor_first,
    DROP COLUMN IF EXISTS inhibitor_kills,
    DROP COLUMN IF EXISTS rift_herald_first,
    DROP COLUMN IF EXISTS rift_herald_kills,
    DROP COLUMN IF EXISTS tower_first,
    DROP COLUMN IF EXISTS tower_kills;

CREATE TABLE IF NOT EXISTS participants (
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL,
    puuid TEXT,
    riot_id_game_name TEXT,
    riot_id_tagline TEXT,
    summoner_id TEXT,
    team_id INTEGER,
    champion_id INTEGER,
    champion_name TEXT,
    team_position TEXT,
    individual_position TEXT,
    lane TEXT,
    role TEXT,
    win BOOLEAN,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    champ_level INTEGER,
    total_minions_killed INTEGER,
    neutral_minions_killed INTEGER,
    gold_earned INTEGER,
    gold_spent INTEGER,
    total_damage_to_champions INTEGER,
    physical_damage_to_champions INTEGER,
    magic_damage_to_champions INTEGER,
    true_damage_to_champions INTEGER,
    total_damage_taken INTEGER,
    damage_self_mitigated INTEGER,
    vision_score INTEGER,
    wards_placed INTEGER,
    wards_killed INTEGER,
    detector_wards_placed INTEGER,
    time_played INTEGER,
    summoner_spell_1 INTEGER,
    summoner_spell_2 INTEGER,
    PRIMARY KEY (match_id, participant_id),
    FOREIGN KEY (match_id, team_id) REFERENCES teams(match_id, team_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS participant_items (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    item_slot INTEGER NOT NULL CHECK (item_slot BETWEEN 0 AND 6),
    item_id INTEGER,
    PRIMARY KEY (match_id, participant_id, item_slot),
    FOREIGN KEY (match_id, participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS participant_perk_stats (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    offense_perk_id INTEGER,
    flex_perk_id INTEGER,
    defense_perk_id INTEGER,
    PRIMARY KEY (match_id, participant_id),
    FOREIGN KEY (match_id, participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS participant_perk_styles (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    style_index INTEGER NOT NULL,
    style_id INTEGER,
    description TEXT,
    PRIMARY KEY (match_id, participant_id, style_index),
    FOREIGN KEY (match_id, participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS participant_perk_selections (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    style_index INTEGER NOT NULL,
    selection_index INTEGER NOT NULL,
    perk_id INTEGER,
    var1 INTEGER,
    var2 INTEGER,
    var3 INTEGER,
    PRIMARY KEY (match_id, participant_id, style_index, selection_index),
    FOREIGN KEY (match_id, participant_id, style_index)
        REFERENCES participant_perk_styles(match_id, participant_id, style_index) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS participant_challenges (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    challenge_name TEXT NOT NULL,
    value_type TEXT NOT NULL,
    value_numeric NUMERIC,
    value_boolean BOOLEAN,
    value_text TEXT,
    PRIMARY KEY (match_id, participant_id, challenge_name),
    FOREIGN KEY (match_id, participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_archive.raw_participants (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    payload JSONB NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, participant_id),
    FOREIGN KEY (match_id, participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

-- Backfill participant children from already-collected rows; no Riot API calls are needed.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'participants' AND column_name = 'items'
    ) THEN
        EXECUTE $sql$
            INSERT INTO participant_items (match_id, participant_id, item_slot, item_id)
            SELECT p.match_id, p.participant_id, item.ordinality::integer - 1, item.value
            FROM participants AS p
            CROSS JOIN LATERAL unnest(p.items) WITH ORDINALITY AS item(value, ordinality)
            ON CONFLICT (match_id, participant_id, item_slot) DO UPDATE SET item_id = EXCLUDED.item_id
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'participants' AND column_name = 'perks'
    ) THEN
        EXECUTE $sql$
            INSERT INTO participant_perk_stats
                (match_id, participant_id, offense_perk_id, flex_perk_id, defense_perk_id)
            SELECT p.match_id, p.participant_id,
                   (p.perks #>> '{statPerks,offense}')::integer,
                   (p.perks #>> '{statPerks,flex}')::integer,
                   (p.perks #>> '{statPerks,defense}')::integer
            FROM participants AS p
            ON CONFLICT (match_id, participant_id) DO UPDATE SET
                offense_perk_id = EXCLUDED.offense_perk_id,
                flex_perk_id = EXCLUDED.flex_perk_id,
                defense_perk_id = EXCLUDED.defense_perk_id
        $sql$;

        EXECUTE $sql$
            INSERT INTO participant_perk_styles
                (match_id, participant_id, style_index, style_id, description)
            SELECT p.match_id, p.participant_id, style.ordinality::integer - 1,
                   (style.value ->> 'style')::integer,
                   style.value ->> 'description'
            FROM participants AS p
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(p.perks -> 'styles', '[]'::jsonb))
                WITH ORDINALITY AS style(value, ordinality)
            ON CONFLICT (match_id, participant_id, style_index) DO UPDATE SET
                style_id = EXCLUDED.style_id, description = EXCLUDED.description
        $sql$;

        EXECUTE $sql$
            INSERT INTO participant_perk_selections
                (match_id, participant_id, style_index, selection_index, perk_id, var1, var2, var3)
            SELECT p.match_id, p.participant_id,
                   style.ordinality::integer - 1,
                   selection.ordinality::integer - 1,
                   (selection.value ->> 'perk')::integer,
                   (selection.value ->> 'var1')::integer,
                   (selection.value ->> 'var2')::integer,
                   (selection.value ->> 'var3')::integer
            FROM participants AS p
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(p.perks -> 'styles', '[]'::jsonb))
                WITH ORDINALITY AS style(value, ordinality)
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(style.value -> 'selections', '[]'::jsonb))
                WITH ORDINALITY AS selection(value, ordinality)
            ON CONFLICT (match_id, participant_id, style_index, selection_index) DO UPDATE SET
                perk_id = EXCLUDED.perk_id, var1 = EXCLUDED.var1,
                var2 = EXCLUDED.var2, var3 = EXCLUDED.var3
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'participants' AND column_name = 'challenges'
    ) THEN
        EXECUTE $sql$
            INSERT INTO participant_challenges
                (match_id, participant_id, challenge_name, value_type,
                 value_numeric, value_boolean, value_text)
            SELECT p.match_id, p.participant_id, challenge.key,
                   jsonb_typeof(challenge.value),
                   CASE WHEN jsonb_typeof(challenge.value) = 'number'
                        THEN (challenge.value #>> '{}')::numeric END,
                   CASE WHEN jsonb_typeof(challenge.value) = 'boolean'
                        THEN (challenge.value #>> '{}')::boolean END,
                   CASE WHEN jsonb_typeof(challenge.value) IN ('number','boolean','null')
                        THEN NULL ELSE challenge.value #>> '{}' END
            FROM participants AS p
            CROSS JOIN LATERAL jsonb_each(COALESCE(p.challenges, '{}'::jsonb)) AS challenge(key, value)
            ON CONFLICT (match_id, participant_id, challenge_name) DO UPDATE SET
                value_type = EXCLUDED.value_type,
                value_numeric = EXCLUDED.value_numeric,
                value_boolean = EXCLUDED.value_boolean,
                value_text = EXCLUDED.value_text
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'participants' AND column_name = 'raw_participant'
    ) THEN
        EXECUTE $sql$
            INSERT INTO raw_archive.raw_participants (match_id, participant_id, payload)
            SELECT match_id, participant_id, raw_participant FROM participants
            ON CONFLICT (match_id, participant_id) DO UPDATE SET payload = EXCLUDED.payload, stored_at = now()
        $sql$;
    END IF;
END $$;

ALTER TABLE participants
    DROP COLUMN IF EXISTS items,
    DROP COLUMN IF EXISTS perks,
    DROP COLUMN IF EXISTS challenges,
    DROP COLUMN IF EXISTS raw_participant;

CREATE TABLE IF NOT EXISTS target_player_matches (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    puuid TEXT NOT NULL REFERENCES players(puuid),
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL,
    cohort_type TEXT NOT NULL CHECK (cohort_type IN ('SELF','REFERENCE')),
    platform_code TEXT REFERENCES platform_regions(platform_code),
    rank_at_collection TEXT,
    division_at_collection TEXT,
    lp_at_collection INTEGER,
    rank_snapshot_at TIMESTAMPTZ,
    role_source TEXT NOT NULL,
    role_ambiguous BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (run_id, puuid, match_id),
    FOREIGN KEY (match_id, participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

ALTER TABLE target_player_matches ADD COLUMN IF NOT EXISTS platform_code TEXT;
UPDATE target_player_matches AS t
SET platform_code=upper(m.platform_id)
FROM matches AS m
WHERE t.match_id=m.match_id AND t.platform_code IS NULL;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_target_player_matches_platform') THEN
        ALTER TABLE target_player_matches ADD CONSTRAINT fk_target_player_matches_platform
            FOREIGN KEY (platform_code) REFERENCES platform_regions(platform_code);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS participant_frames (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    frame_index INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    position_x INTEGER,
    position_y INTEGER,
    level INTEGER,
    xp INTEGER,
    current_gold INTEGER,
    total_gold INTEGER,
    minions_killed INTEGER,
    jungle_minions_killed INTEGER,
    time_enemy_spent_controlled INTEGER,
    health INTEGER,
    health_max INTEGER,
    health_regen INTEGER,
    resource INTEGER,
    resource_max INTEGER,
    resource_regen INTEGER,
    armor INTEGER,
    magic_resist INTEGER,
    attack_damage INTEGER,
    attack_speed INTEGER,
    ability_power INTEGER,
    ability_haste INTEGER,
    movement_speed INTEGER,
    lifesteal INTEGER,
    omnivamp INTEGER,
    physical_vamp INTEGER,
    spell_vamp INTEGER,
    armor_pen_flat INTEGER,
    armor_pen_percent INTEGER,
    magic_pen_flat INTEGER,
    magic_pen_percent INTEGER,
    total_damage_done INTEGER,
    total_damage_done_to_champions INTEGER,
    total_damage_taken INTEGER,
    physical_damage_done INTEGER,
    physical_damage_done_to_champions INTEGER,
    physical_damage_taken INTEGER,
    magic_damage_done INTEGER,
    magic_damage_done_to_champions INTEGER,
    magic_damage_taken INTEGER,
    true_damage_done INTEGER,
    true_damage_done_to_champions INTEGER,
    true_damage_taken INTEGER,
    PRIMARY KEY (match_id, participant_id, frame_index),
    FOREIGN KEY (match_id, participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_archive.raw_participant_frames (
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    frame_index INTEGER NOT NULL,
    payload JSONB NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, participant_id, frame_index),
    FOREIGN KEY (match_id, participant_id, frame_index)
        REFERENCES participant_frames(match_id, participant_id, frame_index) ON DELETE CASCADE
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'participant_frames' AND column_name = 'raw_frame'
    ) THEN
        EXECUTE $sql$
            INSERT INTO raw_archive.raw_participant_frames (match_id, participant_id, frame_index, payload)
            SELECT match_id, participant_id, frame_index, raw_frame FROM participant_frames
            ON CONFLICT (match_id, participant_id, frame_index) DO UPDATE SET
                payload = EXCLUDED.payload, stored_at = now()
        $sql$;
    END IF;
END $$;

ALTER TABLE participant_frames DROP COLUMN IF EXISTS raw_frame;

CREATE TABLE IF NOT EXISTS timeline_events (
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    event_index INTEGER NOT NULL,
    timestamp_ms INTEGER,
    event_type TEXT,
    participant_id INTEGER,
    killer_id INTEGER,
    victim_id INTEGER,
    creator_id INTEGER,
    position_x INTEGER,
    position_y INTEGER,
    ward_type TEXT,
    monster_type TEXT,
    monster_sub_type TEXT,
    building_type TEXT,
    tower_type TEXT,
    lane_type TEXT,
    item_id INTEGER,
    before_id INTEGER,
    after_id INTEGER,
    skill_slot INTEGER,
    level_up_type TEXT,
    level INTEGER,
    bounty INTEGER,
    kill_streak_length INTEGER,
    team_id INTEGER,
    PRIMARY KEY (match_id, frame_index, event_index)
);

CREATE TABLE IF NOT EXISTS timeline_event_assists (
    match_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    event_index INTEGER NOT NULL,
    assisting_participant_id INTEGER NOT NULL,
    PRIMARY KEY (match_id, frame_index, event_index, assisting_participant_id),
    FOREIGN KEY (match_id, frame_index, event_index)
        REFERENCES timeline_events(match_id, frame_index, event_index) ON DELETE CASCADE,
    FOREIGN KEY (match_id, assisting_participant_id)
        REFERENCES participants(match_id, participant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_archive.raw_timeline_events (
    match_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    event_index INTEGER NOT NULL,
    payload JSONB NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, frame_index, event_index),
    FOREIGN KEY (match_id, frame_index, event_index)
        REFERENCES timeline_events(match_id, frame_index, event_index) ON DELETE CASCADE
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'timeline_events'
          AND column_name = 'assisting_participant_ids'
    ) THEN
        EXECUTE $sql$
            INSERT INTO timeline_event_assists
                (match_id, frame_index, event_index, assisting_participant_id)
            SELECT e.match_id, e.frame_index, e.event_index, assistant.participant_id
            FROM timeline_events AS e
            CROSS JOIN LATERAL unnest(e.assisting_participant_ids) AS assistant(participant_id)
            ON CONFLICT DO NOTHING
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'timeline_events' AND column_name = 'raw_event'
    ) THEN
        EXECUTE $sql$
            INSERT INTO raw_archive.raw_timeline_events (match_id, frame_index, event_index, payload)
            SELECT match_id, frame_index, event_index, raw_event FROM timeline_events
            ON CONFLICT (match_id, frame_index, event_index) DO UPDATE SET
                payload = EXCLUDED.payload, stored_at = now()
        $sql$;
    END IF;
END $$;

ALTER TABLE timeline_events
    DROP COLUMN IF EXISTS assisting_participant_ids,
    DROP COLUMN IF EXISTS raw_event;

CREATE TABLE IF NOT EXISTS raw_archive.raw_payloads (
    resource_type TEXT NOT NULL CHECK (resource_type IN ('MATCH','TIMELINE')),
    resource_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (resource_type, resource_key)
);

CREATE TABLE IF NOT EXISTS pipeline_internal.api_cache (
    endpoint TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (endpoint, cache_key)
);

CREATE TABLE IF NOT EXISTS player_scan_state (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    puuid TEXT NOT NULL REFERENCES players(puuid),
    scan_kind TEXT NOT NULL,
    next_start INTEGER NOT NULL DEFAULT 0,
    stop_match_id TEXT,
    newest_match_id TEXT,
    complete BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, puuid, scan_kind)
);

ALTER TABLE player_scan_state ADD COLUMN IF NOT EXISTS stop_match_id TEXT;
ALTER TABLE player_scan_state ADD COLUMN IF NOT EXISTS newest_match_id TEXT;

CREATE TABLE IF NOT EXISTS pipeline_internal.player_scan_checkpoints (
    puuid TEXT NOT NULL REFERENCES players(puuid) ON DELETE CASCADE,
    scan_kind TEXT NOT NULL,
    newest_match_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (puuid, scan_kind)
);

CREATE TABLE IF NOT EXISTS pipeline_internal.collection_tasks (
    task_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    task_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','RUNNING','COMPLETE','FAILED','SKIPPED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, task_type, task_key)
);

CREATE TABLE IF NOT EXISTS pipeline_internal.api_failures (
    failure_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES collection_runs(run_id) ON DELETE SET NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    context JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_results (
    run_id BIGINT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    failing_count BIGINT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, check_name)
);

CREATE TABLE IF NOT EXISTS quality_result_details (
    run_id BIGINT NOT NULL,
    check_name TEXT NOT NULL,
    detail_name TEXT NOT NULL,
    detail_value TEXT,
    PRIMARY KEY (run_id, check_name, detail_name),
    FOREIGN KEY (run_id, check_name)
        REFERENCES quality_results(run_id, check_name) ON DELETE CASCADE
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'quality_results' AND column_name = 'details'
    ) THEN
        EXECUTE $sql$
            INSERT INTO quality_result_details (run_id, check_name, detail_name, detail_value)
            SELECT q.run_id, q.check_name, detail.key, detail.value::text
            FROM quality_results AS q
            CROSS JOIN LATERAL jsonb_each(COALESCE(q.details, '{}'::jsonb)) AS detail(key, value)
            ON CONFLICT (run_id, check_name, detail_name) DO UPDATE SET
                detail_value = EXCLUDED.detail_value
        $sql$;
    END IF;
END $$;

ALTER TABLE quality_results DROP COLUMN IF EXISTS details;

CREATE INDEX IF NOT EXISTS idx_rank_snapshots_tier ON rank_snapshots (tier, division, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_mastery_puuid ON champion_mastery_snapshots (puuid, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_candidate_rank ON candidate_evaluations (run_id, tier, selected);
CREATE INDEX IF NOT EXISTS idx_matches_patch_queue ON matches (patch, queue_id, game_start);
CREATE INDEX IF NOT EXISTS idx_participants_puuid ON participants (puuid, match_id);
CREATE INDEX IF NOT EXISTS idx_participants_champion_role ON participants (champion_id, team_position, individual_position);
CREATE INDEX IF NOT EXISTS idx_target_cohort ON target_player_matches (cohort_type, rank_at_collection, puuid);
CREATE INDEX IF NOT EXISTS idx_frames_participant_time ON participant_frames (match_id, participant_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON timeline_events (event_type, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON pipeline_internal.collection_tasks (run_id, status, task_type);
