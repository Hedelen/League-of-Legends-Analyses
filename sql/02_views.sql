CREATE OR REPLACE VIEW v_current_active_reference_window AS
SELECT
    r.run_id,
    r.current_patch,
    max(p.patch) FILTER (WHERE p.patch_order = 0) AS patch_1,
    max(p.patch) FILTER (WHERE p.patch_order = 1) AS patch_2,
    max(p.patch) FILTER (WHERE p.patch_order = 2) AS patch_3,
    r.started_at,
    r.status
FROM collection_runs AS r
LEFT JOIN collection_run_patches AS p USING (run_id)
GROUP BY r.run_id, r.current_patch, r.started_at, r.status
ORDER BY run_id DESC
LIMIT 1;

CREATE OR REPLACE VIEW v_teams_analysis AS
SELECT
    t.match_id,
    t.team_id,
    CASE t.team_id WHEN 100 THEN 'BLUE' WHEN 200 THEN 'RED' ELSE 'UNKNOWN' END AS team_side,
    t.win,
    bool_or(o.first) FILTER (WHERE o.objective_type = 'baron') AS baron_first,
    max(o.kills) FILTER (WHERE o.objective_type = 'baron') AS baron_kills,
    bool_or(o.first) FILTER (WHERE o.objective_type = 'champion') AS champion_first,
    max(o.kills) FILTER (WHERE o.objective_type = 'champion') AS champion_kills,
    bool_or(o.first) FILTER (WHERE o.objective_type = 'dragon') AS dragon_first,
    max(o.kills) FILTER (WHERE o.objective_type = 'dragon') AS dragon_kills,
    bool_or(o.first) FILTER (WHERE o.objective_type = 'horde') AS horde_first,
    max(o.kills) FILTER (WHERE o.objective_type = 'horde') AS horde_kills,
    bool_or(o.first) FILTER (WHERE o.objective_type = 'inhibitor') AS inhibitor_first,
    max(o.kills) FILTER (WHERE o.objective_type = 'inhibitor') AS inhibitor_kills,
    bool_or(o.first) FILTER (WHERE o.objective_type = 'riftHerald') AS rift_herald_first,
    max(o.kills) FILTER (WHERE o.objective_type = 'riftHerald') AS rift_herald_kills,
    bool_or(o.first) FILTER (WHERE o.objective_type = 'tower') AS tower_first,
    max(o.kills) FILTER (WHERE o.objective_type = 'tower') AS tower_kills
FROM teams AS t
LEFT JOIN team_objectives AS o
  ON o.match_id = t.match_id AND o.team_id = t.team_id
GROUP BY t.match_id, t.team_id, t.win;

CREATE OR REPLACE VIEW v_team_bans_analysis AS
SELECT
    b.match_id,
    b.team_id,
    CASE b.team_id WHEN 100 THEN 'BLUE' WHEN 200 THEN 'RED' ELSE 'UNKNOWN' END AS team_side,
    b.pick_turn,
    b.champion_id
FROM team_bans b;

CREATE OR REPLACE VIEW v_kayle_target_games AS
SELECT
    t.run_id, t.cohort_type, t.rank_at_collection, t.division_at_collection,
    t.lp_at_collection, t.rank_snapshot_at, t.puuid, t.match_id,
    t.participant_id, t.role_source, t.role_ambiguous,
    p.riot_id_game_name, p.riot_id_tagline, p.team_id, p.champion_name,
    p.team_position, p.individual_position, p.win, p.kills, p.deaths,
    p.assists, p.total_minions_killed, p.neutral_minions_killed,
    p.gold_earned, p.total_damage_to_champions, p.vision_score,
    m.game_start, m.game_duration_seconds, m.game_version, m.patch,
    m.queue_id, m.timeline_status, m.is_remake_or_short,
    m.unexpected_participant_count
FROM target_player_matches t
JOIN participants p
  ON p.match_id=t.match_id AND p.participant_id=t.participant_id
JOIN matches m
  ON m.match_id=t.match_id;

CREATE OR REPLACE VIEW v_self_kayle_games AS
SELECT * FROM v_kayle_target_games WHERE cohort_type='SELF';

CREATE OR REPLACE VIEW v_reference_kayle_games AS
SELECT * FROM v_kayle_target_games WHERE cohort_type='REFERENCE';

CREATE OR REPLACE VIEW v_kayle_lane_matchups AS
SELECT
    k.*,
    foe.participant_id AS opponent_participant_id,
    foe.puuid AS opponent_puuid,
    foe.champion_id AS opponent_champion_id,
    foe.champion_name AS opponent_champion_name,
    foe.kills AS opponent_kills,
    foe.deaths AS opponent_deaths,
    foe.assists AS opponent_assists,
    foe.total_minions_killed AS opponent_lane_cs,
    foe.neutral_minions_killed AS opponent_jungle_cs,
    foe.gold_earned AS opponent_gold_earned,
    foe.total_damage_to_champions AS opponent_damage_to_champions
FROM v_kayle_target_games k
LEFT JOIN participants foe
  ON foe.match_id=k.match_id
 AND foe.team_id<>k.team_id
 AND COALESCE(NULLIF(foe.team_position,''), foe.individual_position)='TOP';

CREATE OR REPLACE VIEW v_kayle_lane_frames AS
SELECT
    m.run_id, m.cohort_type, m.rank_at_collection, m.puuid,
    m.match_id, m.game_start, m.patch, m.win,
    kf.frame_index, kf.timestamp_ms,
    kf.position_x AS kayle_x, kf.position_y AS kayle_y,
    kf.level AS kayle_level, kf.xp AS kayle_xp,
    kf.total_gold AS kayle_gold,
    kf.minions_killed + kf.jungle_minions_killed AS kayle_cs,
    ofr.position_x AS opponent_x, ofr.position_y AS opponent_y,
    ofr.level AS opponent_level, ofr.xp AS opponent_xp,
    ofr.total_gold AS opponent_gold,
    ofr.minions_killed + ofr.jungle_minions_killed AS opponent_cs,
    kf.total_gold - ofr.total_gold AS gold_diff,
    kf.xp - ofr.xp AS xp_diff,
    (kf.minions_killed + kf.jungle_minions_killed)
      - (ofr.minions_killed + ofr.jungle_minions_killed) AS cs_diff
FROM v_kayle_lane_matchups m
JOIN participant_frames kf
  ON kf.match_id=m.match_id AND kf.participant_id=m.participant_id
LEFT JOIN participant_frames ofr
  ON ofr.match_id=m.match_id
 AND ofr.participant_id=m.opponent_participant_id
 AND ofr.frame_index=kf.frame_index;

CREATE OR REPLACE VIEW v_team_frame_totals AS
SELECT
    f.match_id, f.frame_index, f.timestamp_ms, p.team_id,
    SUM(f.total_gold) AS team_total_gold,
    SUM(f.xp) AS team_total_xp,
    SUM(f.minions_killed + f.jungle_minions_killed) AS team_total_cs
FROM participant_frames f
JOIN participants p
  ON p.match_id=f.match_id AND p.participant_id=f.participant_id
GROUP BY f.match_id, f.frame_index, f.timestamp_ms, p.team_id;

CREATE OR REPLACE VIEW v_cohort_summary AS
SELECT
    t.run_id, t.cohort_type, COALESCE(t.rank_at_collection, 'SELF') AS cohort_rank,
    COUNT(DISTINCT t.puuid) AS independent_players,
    COUNT(DISTINCT t.match_id) AS unique_matches,
    ROUND(COUNT(DISTINCT t.match_id)::numeric
          / NULLIF(COUNT(DISTINCT t.puuid), 0), 1) AS games_per_player
FROM target_player_matches t
GROUP BY t.run_id, t.cohort_type, COALESCE(t.rank_at_collection, 'SELF');

CREATE OR REPLACE VIEW v_discovery_report AS
SELECT
    c.run_id, c.tier, c.division, c.league_points, c.puuid,
    p.riot_id_game_name, p.riot_id_tagline,
    c.kayle_mastery_points, c.ranked_games_in_window,
    c.qualifying_kayle_top_games, c.kayle_top_play_rate,
    e.passed AS account_200_games_proven,
    e.evidence_method, e.observed_game_count,
    c.eligible, c.selected, c.selection_reason, c.planned_game_count,
    c.discovery_source, c.source_position,
    c.source_region, c.platform_code, c.routing_region
FROM candidate_evaluations c
JOIN players p USING (puuid)
LEFT JOIN account_experience_checks e USING (run_id, puuid);

CREATE OR REPLACE VIEW v_player_contribution AS
SELECT
    run_id, cohort_type, COALESCE(rank_at_collection,'SELF') AS cohort_rank,
    puuid, COUNT(*) AS games,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER
          (PARTITION BY run_id, cohort_type,
           COALESCE(rank_at_collection,'SELF')), 1) AS cohort_share_pct
FROM target_player_matches
GROUP BY run_id, cohort_type, COALESCE(rank_at_collection,'SELF'), puuid;

CREATE OR REPLACE VIEW v_region_summary AS
SELECT
    t.run_id,
    t.cohort_type,
    t.platform_code,
    pr.routing_region,
    pr.display_name AS region_name,
    COUNT(DISTINCT t.puuid) AS independent_players,
    COUNT(DISTINCT t.match_id) AS unique_matches,
    COUNT(*) AS player_games
FROM target_player_matches AS t
LEFT JOIN platform_regions AS pr USING (platform_code)
GROUP BY t.run_id, t.cohort_type, t.platform_code,
         pr.routing_region, pr.display_name;

CREATE OR REPLACE VIEW v_clean_modeling_games AS
SELECT k.*
FROM v_kayle_target_games k
JOIN collection_run_matches crm
  ON crm.run_id=k.run_id AND crm.match_id=k.match_id
WHERE k.queue_id=420
  AND k.champion_name='Kayle'
  AND NOT k.role_ambiguous
  AND NOT k.is_remake_or_short
  AND NOT k.unexpected_participant_count
  AND k.timeline_status='COMPLETE'
  AND (k.cohort_type='SELF' OR crm.in_active_reference_window);
