-- Beginner exploration: highlight one statement at a time in pgAdmin/DBeaver.

-- 1) One row in players = one durable PUUID identity.
SELECT * FROM players ORDER BY first_seen_at DESC LIMIT 20;

-- 2) One row in matches = one unique Riot match (never one row per player).
SELECT * FROM matches ORDER BY game_start DESC LIMIT 20;

-- 3) One row in participants = one of the 10 players in a match.
SELECT * FROM participants ORDER BY match_id, participant_id LIMIT 30;

-- 4) One row in participant_frames = one player at one timeline snapshot.
SELECT * FROM participant_frames ORDER BY match_id, frame_index, participant_id LIMIT 50;

-- 5) How many selected independent players and planned games by rank?
SELECT tier,
       COUNT(*) AS independent_players,
       SUM(planned_game_count) AS planned_player_games
FROM reference_cohort
GROUP BY tier
ORDER BY CASE tier
  WHEN 'EMERALD' THEN 1 WHEN 'DIAMOND' THEN 2 WHEN 'MASTER' THEN 3
  WHEN 'GRANDMASTER' THEN 4 WHEN 'CHALLENGER' THEN 5 END;

-- 6) Actual Kayle games and independent players per cohort/rank.
SELECT * FROM v_cohort_summary ORDER BY run_id DESC, cohort_type, cohort_rank;

-- 7) Verify no one player dominates a rank.
SELECT rank_at_collection, puuid, COUNT(*) AS games,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER
             (PARTITION BY rank_at_collection), 1) AS rank_share_pct
FROM target_player_matches
WHERE cohort_type='REFERENCE'
GROUP BY rank_at_collection, puuid
ORDER BY rank_at_collection, games DESC;

-- 8) How many available frame snapshots does each game contain?
SELECT match_id,
       COUNT(DISTINCT frame_index) AS frames_per_participant,
       COUNT(*) AS participant_frame_rows,
       COUNT(DISTINCT participant_id) AS participants
FROM participant_frames
GROUP BY match_id
ORDER BY frames_per_participant DESC
LIMIT 50;

-- 9) Follow one Kayle player through one game.
-- Replace the two placeholders with values from v_kayle_target_games.
SELECT timestamp_ms/60000.0 AS minute, level, xp, total_gold,
       minions_killed, jungle_minions_killed, position_x, position_y,
       health, health_max, attack_damage, ability_power, movement_speed
FROM participant_frames
WHERE match_id = 'NA1_REPLACE_ME'
  AND participant_id = 1
ORDER BY frame_index;

-- 10) Kayle versus the opposing TOP at every frame.
SELECT match_id, timestamp_ms/60000.0 AS minute,
       kayle_gold, opponent_gold, gold_diff,
       kayle_xp, opponent_xp, xp_diff,
       kayle_cs, opponent_cs, cs_diff
FROM v_kayle_lane_frames
ORDER BY match_id, frame_index
LIMIT 200;

-- 11) Inspect common timeline events.
SELECT match_id, timestamp_ms/60000.0 AS minute, event_type,
       participant_id, killer_id, victim_id, assisting_participant_ids,
       item_id, monster_type, building_type, position_x, position_y
FROM timeline_events
WHERE event_type IN ('CHAMPION_KILL','ITEM_PURCHASED','WARD_PLACED',
                     'ELITE_MONSTER_KILL','BUILDING_KILL')
ORDER BY match_id, timestamp_ms
LIMIT 200;

-- 12) JSONB retains fields we did not flatten. See all top-level keys.
SELECT DISTINCT jsonb_object_keys(raw_participant) AS participant_json_key
FROM participants
ORDER BY 1;

-- 13) Read one nested challenge directly from retained JSONB.
SELECT match_id, riot_id_game_name,
       raw_participant #>> '{challenges,soloKills}' AS solo_kills
FROM participants
WHERE champion_id=10
LIMIT 30;

-- 14) Clean observations for future modeling (still do grouped splits by puuid).
SELECT cohort_type, rank_at_collection, COUNT(*) AS games
FROM v_clean_modeling_games
GROUP BY cohort_type, rank_at_collection
ORDER BY cohort_type, rank_at_collection;

