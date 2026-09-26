-- Zero-row/error checks. The CLI stores a compact version in quality_results.

-- Matches whose stored participant count is not 10.
SELECT match_id, participant_count
FROM matches
WHERE participant_count <> 10;

-- Database-enforced uniqueness checks (should return no rows).
SELECT match_id, participant_id, COUNT(*)
FROM participants GROUP BY 1,2 HAVING COUNT(*) > 1;

SELECT match_id, participant_id, frame_index, COUNT(*)
FROM participant_frames GROUP BY 1,2,3 HAVING COUNT(*) > 1;

SELECT match_id, frame_index, event_index, COUNT(*)
FROM timeline_events GROUP BY 1,2,3 HAVING COUNT(*) > 1;

-- Reference observations must meet patch, champion, TOP, queue, and experience rules.
SELECT t.run_id, t.puuid, t.match_id, m.patch, r.patch_window
FROM target_player_matches t
JOIN matches m USING (match_id)
JOIN collection_runs r USING (run_id)
WHERE t.cohort_type='REFERENCE' AND NOT (m.patch = ANY(r.patch_window));

SELECT t.run_id, t.puuid, t.match_id, p.champion_id,
       p.team_position, p.individual_position, m.queue_id
FROM target_player_matches t
JOIN participants p USING (match_id, participant_id)
JOIN matches m USING (match_id)
WHERE t.cohort_type='REFERENCE'
  AND (p.champion_id<>10 OR m.queue_id<>420
       OR COALESCE(NULLIF(p.team_position,''),p.individual_position)<>'TOP');

SELECT r.run_id, r.tier, r.puuid, e.evidence_method, e.observed_game_count
FROM reference_cohort r
LEFT JOIN account_experience_checks e USING (run_id, puuid)
WHERE COALESCE(e.passed,false)=false;

-- Missing timeline material remains stored and visible rather than deleted.
SELECT m.match_id, m.timeline_status,
       COUNT(DISTINCT f.frame_index) AS frame_count
FROM matches m
JOIN target_player_matches t USING (match_id)
LEFT JOIN participant_frames f USING (match_id)
GROUP BY m.match_id, m.timeline_status
HAVING m.timeline_status<>'COMPLETE' OR COUNT(DISTINCT f.frame_index)=0;

-- Rank diversity and contribution balance.
SELECT rank_at_collection,
       COUNT(DISTINCT puuid) AS independent_players,
       COUNT(*) AS player_games,
       MAX(player_games) AS largest_player_contribution,
       ROUND(100.0*MAX(player_games)/SUM(player_games),1) AS largest_share_pct
FROM (
  SELECT rank_at_collection, puuid, COUNT(*) AS player_games
  FROM target_player_matches
  WHERE cohort_type='REFERENCE'
  GROUP BY rank_at_collection, puuid
) x
GROUP BY rank_at_collection
ORDER BY rank_at_collection;

-- Orphan checks (foreign keys should make every count zero).
SELECT COUNT(*) AS orphan_participants
FROM participants p LEFT JOIN matches m USING (match_id)
WHERE m.match_id IS NULL;

SELECT COUNT(*) AS orphan_frames
FROM participant_frames f
LEFT JOIN participants p USING (match_id, participant_id)
WHERE p.match_id IS NULL;

