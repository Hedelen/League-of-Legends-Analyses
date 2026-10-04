-- Kayle keystone selection by opposing top-lane champion
-- Goal: measure the percentage of games in which each Kayle keystone is selected
-- against each opposing top-lane champion.

-- Exploratory checkpoint: put blue-side and red-side top laners on one row per match.
SELECT 
    p.match_id,
    MAX(
        CASE 
            WHEN team_id = '100' THEN champion_name
            ELSE ''
        END
    ) AS blue_side,
    MAX(
        CASE 
            WHEN team_id = '200' THEN champion_name
            ELSE ''
        END
    ) AS red_side
FROM participants AS p 
LEFT JOIN participant_perk_styles AS ppst
    ON p.match_id = ppst.match_id
   AND p.participant_id = ppst.participant_id
LEFT JOIN participant_perk_selections AS ppse
    ON ppst.match_id = ppse.match_id
   AND ppst.participant_id = ppse.participant_id
   AND ppst.style_index = ppse.style_index
LEFT JOIN perk_catalog AS pc
    ON pc.perk_id = ppse.perk_id
WHERE individual_position = 'TOP'
GROUP BY p.match_id;


-- Intermediate analytical view.
CREATE OR REPLACE VIEW games_with_keystone_and_pct_used AS
WITH keystone_against AS (
    SELECT
        p.match_id,
        MAX(
            CASE
                WHEN champion_name != 'Kayle' THEN champion_name
                ELSE ''
            END
        ) AS enemy_champ,
        MAX(
            CASE
                WHEN champion_name = 'Kayle' THEN perk_name
                ELSE ''
            END
        ) AS kayles_keystone
    FROM participants AS p
    LEFT JOIN participant_perk_styles AS ppst
        ON p.match_id = ppst.match_id
       AND p.participant_id = ppst.participant_id
    LEFT JOIN participant_perk_selections AS ppse
        ON ppst.match_id = ppse.match_id
       AND ppst.participant_id = ppse.participant_id
       AND ppst.style_index = ppse.style_index
    LEFT JOIN perk_catalog AS pc
        ON pc.perk_id = ppse.perk_id
    WHERE individual_position = 'TOP'
      AND is_keystone = TRUE
    GROUP BY p.match_id
),
games_with_keystone AS (
    SELECT
        enemy_champ,
        kayles_keystone,
        COUNT(match_id) AS num_games_rune_used,
        SUM(COUNT(*)) OVER (
            PARTITION BY enemy_champ
        ) AS tot_games_against_champ
    FROM keystone_against
    GROUP BY
        enemy_champ,
        kayles_keystone
),
games_with_keystone_and_pct_used AS (
    SELECT
        *,
        ROUND(
            (1.0 * num_games_rune_used)
            / (1.0 * tot_games_against_champ)
            * 100.0,
            1
        ) AS pct_used,
        CASE
            WHEN num_games_rune_used = MAX(num_games_rune_used) OVER (
                PARTITION BY enemy_champ
            )
            THEN kayles_keystone
            ELSE ''
        END AS most_used_keystone
    FROM games_with_keystone
)
SELECT *
FROM games_with_keystone_and_pct_used;


-- Final wide-format view: one row per enemy champion.
CREATE OR REPLACE VIEW keystone_for_enemy AS
WITH keystone_against AS (
    SELECT
        p.match_id,
        MAX(
            CASE
                WHEN champion_name != 'Kayle' THEN champion_name
                ELSE ''
            END
        ) AS enemy_champ,
        MAX(
            CASE
                WHEN champion_name = 'Kayle' THEN perk_name
                ELSE ''
            END
        ) AS kayles_keystone
    FROM participants AS p
    LEFT JOIN participant_perk_styles AS ppst
        ON p.match_id = ppst.match_id
       AND p.participant_id = ppst.participant_id
    LEFT JOIN participant_perk_selections AS ppse
        ON ppst.match_id = ppse.match_id
       AND ppst.participant_id = ppse.participant_id
       AND ppst.style_index = ppse.style_index
    LEFT JOIN perk_catalog AS pc
        ON pc.perk_id = ppse.perk_id
    WHERE individual_position = 'TOP'
      AND is_keystone = TRUE
    GROUP BY p.match_id
),
games_with_keystone AS (
    SELECT
        enemy_champ,
        kayles_keystone,
        COUNT(match_id) AS num_games_rune_used,
        SUM(COUNT(*)) OVER (
            PARTITION BY enemy_champ
        ) AS tot_games_against_champ
    FROM keystone_against
    GROUP BY
        enemy_champ,
        kayles_keystone
),
games_with_keystone_and_pct_used AS (
    SELECT
        *,
        ROUND(
            (1.0 * num_games_rune_used)
            / (1.0 * tot_games_against_champ)
            * 100.0,
            1
        ) AS pct_used,
        CASE
            WHEN num_games_rune_used = MAX(num_games_rune_used) OVER (
                PARTITION BY enemy_champ
            )
            THEN kayles_keystone
            ELSE ''
        END AS most_used_keystone
    FROM games_with_keystone
)
SELECT
    enemy_champ,
    MAX(most_used_keystone) AS typical_keystone,
    MAX(
        CASE
            WHEN kayles_keystone = 'Press the Attack' THEN pct_used
            ELSE 0
        END
    ) AS pct_pta,
    MAX(
        CASE
            WHEN kayles_keystone = 'Lethal Tempo' THEN pct_used
            ELSE 0
        END
    ) AS pct_leth_temp,
    MAX(
        CASE
            WHEN kayles_keystone = 'Fleet Footwork' THEN pct_used
            ELSE 0
        END
    ) AS pct_fleet,
    MAX(tot_games_against_champ) AS tot_games_against_champ
FROM games_with_keystone_and_pct_used
GROUP BY enemy_champ
ORDER BY
    tot_games_against_champ DESC,
    pct_pta DESC;


-- Quick inspection queries.
SELECT *
FROM games_with_keystone_and_pct_used;

SELECT *
FROM keystone_for_enemy
ORDER BY typical_keystone;
