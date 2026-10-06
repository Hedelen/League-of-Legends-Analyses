-- I want to know what my cs differential should look like against each champion 
-- at each minute of the game. 

-- Same question but instead of diff, I want to see kayles change in cs over time


-- After calculating these two things for the kayle players that are not me,
	-- I want to calculate these details for only my games as well
	-- Then cross reference the two datasets. If there are significant variances against specific champions
	-- and if theres an indication that those games took longer or were harder to win
	-- then that indicates that the way i played at whatever point in the game was a mistake

	-- i would be able to tell if I was too aggressive or too passive against each champion.




-- i want to get "Percent xp to next level"
	-- where 10% is like i ned 90% more xp to level up



-- CTE 1: top_lane_frame_stats
--     one row per top laner per minute

-- CTE 2: player_opponent_split
--     one row per match/minute
--     Kayle + opponent + Kayle side

-- CTE 3: matchup_minute_stats
--     one row per opponent + Kayle side + minute
--     averages/std dev/win rate



select * FROM
	participant_frames AS pf
	LEFT JOIN participants AS p ON pf.match_id = p.match_id
	AND pf.participant_id = p.participant_id;


-- As I work thru this I decided that I want the analysis to be a lot tighter for now.
 --gonna remove health info, only keep % hp


WITH
	top_lane_frame_stats AS (
		SELECT
			pf.match_id,
			(pf.timestamp_ms / 1000) / 60 AS minute_of_game,
			CASE
				WHEN p.team_id = '100' THEN 'Blue'
				ELSE 'Red'
			END AS side_of_map,
			p.champion_name,
			pf.level,
			pf.minions_killed - LAG(pf.minions_killed) OVER (
				PARTITION BY
					pf.match_id,
					pf.participant_id
				ORDER BY
					timestamp_ms
			) AS cs_this_minute,
			pf.jungle_minions_killed - LAG(pf.jungle_minions_killed) OVER (
				PARTITION BY
					pf.match_id,
					pf.participant_id
				ORDER BY
					timestamp_ms
			) AS jg_cs_this_minute,
			pf.current_gold,
			pf.total_gold,
			ROUND(pf.health / (pf.health_max * 1.0) * 100, 0) AS pct_hp,
			CASE WHEN p.win = TRUE then 1 else 0 end as win,
			pf.position_x,
			pf.position_y,
			pf.timestamp_ms,
			pf.participant_id
		FROM
			participant_frames AS pf
			LEFT JOIN participants AS p ON pf.match_id = p.match_id
			AND pf.participant_id = p.participant_id
		WHERE
			pf.participant_id = 1 or pf.participant_id = 6
		ORDER BY
			pf.match_id,
			pf.participant_id,
			timestamp_ms
	),
	laner_split as (
	SELECT
		match_id,
		minute_of_game,
		MAX(case when 
				champion_name ='Kayle' then side_of_map
				else NULL 
				end) as kayle_side_of_map,
		-- blue side stuff
			MAX(case when 
				champion_name ='Kayle' then level
				else NULL 
				end) as kayle_level,
			MAX(case when 
				champion_name ='Kayle' then cs_this_minute
				else NULL 
				end) as kayle_cs_this_minute,
			MAX(case when 
				champion_name ='Kayle' then jg_cs_this_minute
				else NULL 
				end) as kayle_jg_cs_this_minute,
			MAX(case when 
				champion_name ='Kayle' then current_gold
				else NULL 
				end) as kayle_current_gold,
			MAX(case when 
				champion_name ='Kayle' then total_gold
				else NULL 
				end) as kayle_total_gold,
			MAX(case when 
				champion_name ='Kayle' then pct_hp
				else NULL 
				end) as kayle_pct_hp,
			MAX(case when 
				champion_name ='Kayle' then win
				else NULL 
				end) as kayle_win,
			MAX(case when 
				champion_name ='Kayle' then position_x
				else NULL 
				end) as kayle_position_x,
			MAX(case when 
				champion_name ='Kayle' then position_y
				else NULL 
				end) as kayle_position_y,
			MAX(case when 
				champion_name ='Kayle' then participant_id
				else NULL 
				end) as kayle_participant_id,
			-- red side stuff
			MAX(case when 
				champion_name != 'Kayle' then champion_name
				else NULL 
				end) as opponent_champion,
			MAX(case when 
				champion_name != 'Kayle' then level
				else NULL 
				end) as opponent_level,
			MAX(case when 
				champion_name != 'Kayle' then cs_this_minute
				else NULL 
				end) as opponent_cs_this_minute,
			MAX(case when 
				champion_name != 'Kayle' then jg_cs_this_minute
				else NULL 
				end) as opponent_jg_cs_this_minute,
			MAX(case when 
				champion_name != 'Kayle' then current_gold
				else NULL 
				end) as opponent_current_gold,
			MAX(case when 
				champion_name != 'Kayle' then total_gold
				else NULL 
				end) as opponent_total_gold,
			MAX(case when 
				champion_name != 'Kayle' then pct_hp
				else NULL 
				end) as opponent_pct_hp,
			MAX(case when 
				champion_name != 'Kayle' then win
				else NULL 
				end) as opponent_win,
			MAX(case when 
				champion_name != 'Kayle' then position_x
				else NULL 
				end) as opponent_position_x,
			MAX(case when 
				champion_name != 'Kayle' then position_y
				else NULL 
				end) as opponent_position_y,
			MAX(case when 
				champion_name != 'Kayle' then participant_id
				else NULL 
				end) as opponent_participant_id
	FROM
		top_lane_frame_stats
	group by 
	match_id,
	minute_of_game
	)
select 
	opponent_champion, 
	kayle_side_of_map, 
	minute_of_game,
-- as avg_game_length,
			-- blue side stuff
	ROUND(AVG(kayle_level),0) as avg_kayle_level,
	ROUND(AVG(kayle_cs_this_minute),1) as avg_kayle_cs_this_minute,
	ROUND(AVG(kayle_jg_cs_this_minute),1) as avg_kayle_jg_cs_this_minute,
	ROUND(AVG(kayle_current_gold),1) as avg_kayle_current_gold,
	ROUND(AVG(kayle_total_gold),1) as avg_kayle_total_gold,
	ROUND(AVG(kayle_pct_hp),1) as avg_kayle_pct_hp,
	ROUND(AVG(kayle_win) *100.0,1) as avg_kayle_win,
	ROUND(AVG(kayle_position_x),1) as avg_kayle_position_x,
	ROUND(AVG(kayle_position_y),1) as avg_kayle_position_y,
			-- red side stuff
	ROUND(AVG(opponent_level),0) as avg_opponent_level,
	ROUND(AVG(opponent_cs_this_minute),1) as avg_opponent_cs_this_minute,
	ROUND(AVG(opponent_jg_cs_this_minute),1) as avg_opponent_jg_cs_this_minute,
	ROUND(AVG(opponent_current_gold),1) as avg_opponent_current_gold,
	ROUND(AVG(opponent_total_gold),1) as avg_opponent_total_gold,
	ROUND(AVG(opponent_pct_hp),1) as avg_opponent_pct_hp,
	ROUND(AVG(opponent_position_x),1) as avg_opponent_position_x,
	ROUND(AVG(opponent_position_y),1) as avg_opponent_position_y
from laner_split
group by opponent_champion, kayle_side_of_map, minute_of_game
order by opponent_champion, kayle_side_of_map, minute_of_game 
;


-- i want to look into only including champions where there more than 5 games facing 
-- said opponent

-- i want to take this to r and start visualizing it a bit 
	-- i think this will help me develop hypotheses and also investigate the statistical
	-- nature of this data

-- want to make sure to look at things like volatility
-- and other metrics such as average game length, average win rate by game length
-- want to plot average cs x = time, y = cs in minute
	-- each line is facing a specific opponent
	-- this would be nice to see expectations of how my cs should be tracking minute 
		-- over minute
	-- i can use both cumulative and non cumulative
