"""Pure transformation helpers from Riot JSON into relational row dictionaries."""

from __future__ import annotations

from typing import Any, Iterable

from patches import patch_from_version

KAYLE_CHAMPION_ID = 10
RANKED_SOLO_QUEUE_ID = 420


def participant_for_puuid(match: dict[str, Any], puuid: str) -> dict[str, Any] | None:
    return next(
        (p for p in match.get("info", {}).get("participants", []) if p.get("puuid") == puuid),
        None,
    )


def kayle_top_classification(participant: dict[str, Any] | None) -> dict[str, Any]:
    """Apply the documented TOP rule and expose conflicts instead of hiding them."""
    if not participant:
        return {"qualifies": False, "role_source": None, "ambiguous": True, "reason": "target_missing"}
    if participant.get("championId") != KAYLE_CHAMPION_ID:
        return {"qualifies": False, "role_source": None, "ambiguous": False, "reason": "not_kayle"}
    team = (participant.get("teamPosition") or "").upper()
    individual = (participant.get("individualPosition") or "").upper()
    if team == "TOP":
        return {"qualifies": True, "role_source": "teamPosition", "ambiguous": False, "reason": "team_position_top"}
    if not team and individual == "TOP":
        return {"qualifies": True, "role_source": "individualPosition_fallback", "ambiguous": False, "reason": "individual_position_fallback"}
    if individual == "TOP" and team not in ("", "TOP"):
        return {"qualifies": False, "role_source": None, "ambiguous": True, "reason": "role_conflict"}
    if not team and not individual:
        return {"qualifies": False, "role_source": None, "ambiguous": True, "reason": "role_missing"}
    return {"qualifies": False, "role_source": None, "ambiguous": False, "reason": "not_top"}


def _duration_seconds(info: dict[str, Any]) -> int | None:
    value = info.get("gameDuration")
    if value is None:
        return None
    # Old payloads occasionally represented duration in milliseconds.
    return int(value / 1000) if value > 100000 else int(value)


def normalize_match(match: dict[str, Any], very_short_seconds: int = 300) -> dict[str, Any]:
    info = match["info"]
    metadata = match["metadata"]
    participants = info.get("participants", [])
    duration = _duration_seconds(info)
    return {
        "match_id": metadata["matchId"],
        "platform_id": info.get("platformId"),
        "queue_id": info.get("queueId"),
        "map_id": info.get("mapId"),
        "game_creation_ms": info.get("gameCreation"),
        "game_start_ms": info.get("gameStartTimestamp"),
        "game_end_ms": info.get("gameEndTimestamp"),
        "game_duration_seconds": duration,
        "game_version": info.get("gameVersion"),
        "patch": patch_from_version(info.get("gameVersion")),
        "game_mode": info.get("gameMode"),
        "game_type": info.get("gameType"),
        "end_of_game_result": info.get("endOfGameResult"),
        "participant_count": len(participants),
        "is_remake_or_short": duration is not None and duration < very_short_seconds,
        "unexpected_participant_count": len(participants) != 10,
        "raw_match": match,
    }


def normalize_participants(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for p in match.get("info", {}).get("participants", []):
        rows.append(
            {
                "match_id": match_id,
                "participant_id": p.get("participantId"),
                "puuid": p.get("puuid") or None,
                "riot_id_game_name": p.get("riotIdGameName") or None,
                "riot_id_tagline": p.get("riotIdTagline") or None,
                "summoner_id": p.get("summonerId") or None,
                "team_id": p.get("teamId"),
                "champion_id": p.get("championId"),
                "champion_name": p.get("championName"),
                "team_position": p.get("teamPosition") or None,
                "individual_position": p.get("individualPosition") or None,
                "lane": p.get("lane") or None,
                "role": p.get("role") or None,
                "win": p.get("win"),
                "kills": p.get("kills"),
                "deaths": p.get("deaths"),
                "assists": p.get("assists"),
                "champ_level": p.get("champLevel"),
                "total_minions_killed": p.get("totalMinionsKilled"),
                "neutral_minions_killed": p.get("neutralMinionsKilled"),
                "gold_earned": p.get("goldEarned"),
                "gold_spent": p.get("goldSpent"),
                "total_damage_to_champions": p.get("totalDamageDealtToChampions"),
                "physical_damage_to_champions": p.get("physicalDamageDealtToChampions"),
                "magic_damage_to_champions": p.get("magicDamageDealtToChampions"),
                "true_damage_to_champions": p.get("trueDamageDealtToChampions"),
                "total_damage_taken": p.get("totalDamageTaken"),
                "damage_self_mitigated": p.get("damageSelfMitigated"),
                "vision_score": p.get("visionScore"),
                "wards_placed": p.get("wardsPlaced"),
                "wards_killed": p.get("wardsKilled"),
                "detector_wards_placed": p.get("detectorWardsPlaced"),
                "time_played": p.get("timePlayed"),
                "summoner_spell_1": p.get("summoner1Id"),
                "summoner_spell_2": p.get("summoner2Id"),
            }
        )
    return rows


def normalize_participant_items(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    return [
        {
            "match_id": match_id,
            "participant_id": p.get("participantId"),
            "item_slot": slot,
            "item_id": p.get(f"item{slot}"),
        }
        for p in match.get("info", {}).get("participants", [])
        for slot in range(7)
    ]


def normalize_participant_perk_stats(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for p in match.get("info", {}).get("participants", []):
        stats = (p.get("perks") or {}).get("statPerks") or {}
        rows.append(
            {
                "match_id": match_id,
                "participant_id": p.get("participantId"),
                "offense_perk_id": stats.get("offense"),
                "flex_perk_id": stats.get("flex"),
                "defense_perk_id": stats.get("defense"),
            }
        )
    return rows


def normalize_participant_perk_styles(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for p in match.get("info", {}).get("participants", []):
        for style_index, style in enumerate((p.get("perks") or {}).get("styles") or []):
            rows.append(
                {
                    "match_id": match_id,
                    "participant_id": p.get("participantId"),
                    "style_index": style_index,
                    "style_id": style.get("style"),
                    "description": style.get("description"),
                }
            )
    return rows


def normalize_participant_perk_selections(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for p in match.get("info", {}).get("participants", []):
        for style_index, style in enumerate((p.get("perks") or {}).get("styles") or []):
            for selection_index, selection in enumerate(style.get("selections") or []):
                rows.append(
                    {
                        "match_id": match_id,
                        "participant_id": p.get("participantId"),
                        "style_index": style_index,
                        "selection_index": selection_index,
                        "perk_id": selection.get("perk"),
                        "var1": selection.get("var1"),
                        "var2": selection.get("var2"),
                        "var3": selection.get("var3"),
                    }
                )
    return rows


def normalize_participant_challenges(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for p in match.get("info", {}).get("participants", []):
        for challenge_name, value in (p.get("challenges") or {}).items():
            if isinstance(value, bool):
                value_type, value_numeric, value_boolean, value_text = "boolean", None, value, None
            elif isinstance(value, (int, float)):
                value_type, value_numeric, value_boolean, value_text = "number", value, None, None
            elif isinstance(value, str):
                value_type, value_numeric, value_boolean, value_text = "string", None, None, value
            elif value is None:
                value_type, value_numeric, value_boolean, value_text = "null", None, None, None
            else:
                # Unusual future structures remain lossless in raw_participants.
                value_type, value_numeric, value_boolean, value_text = type(value).__name__, None, None, str(value)
            rows.append(
                {
                    "match_id": match_id,
                    "participant_id": p.get("participantId"),
                    "challenge_name": challenge_name,
                    "value_type": value_type,
                    "value_numeric": value_numeric,
                    "value_boolean": value_boolean,
                    "value_text": value_text,
                }
            )
    return rows


def normalize_raw_participants(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    return [
        {"match_id": match_id, "participant_id": p.get("participantId"), "payload": p}
        for p in match.get("info", {}).get("participants", [])
    ]


def normalize_teams(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the small team entity; repeating structures live in child tables."""
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for team in match.get("info", {}).get("teams", []):
        rows.append(
            {
                "match_id": match_id,
                "team_id": team.get("teamId"),
                "win": team.get("win"),
            }
        )
    return rows


def normalize_team_objectives(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for team in match.get("info", {}).get("teams", []):
        for objective_type, objective in (team.get("objectives") or {}).items():
            rows.append(
                {
                    "match_id": match_id,
                    "team_id": team.get("teamId"),
                    "objective_type": objective_type,
                    "first": (objective or {}).get("first"),
                    "kills": (objective or {}).get("kills"),
                }
            )
    return rows


def normalize_raw_teams(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    return [
        {"match_id": match_id, "team_id": team.get("teamId"), "payload": team}
        for team in match.get("info", {}).get("teams", [])
    ]


def normalize_team_bans(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one relational row per team ban instead of one nested JSON array."""
    match_id = match["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for team in match.get("info", {}).get("teams", []):
        for ban_index, ban in enumerate(team.get("bans") or [], start=1):
            rows.append(
                {
                    "match_id": match_id,
                    "team_id": team.get("teamId"),
                    "pick_turn": ban.get("pickTurn") or ban_index,
                    "champion_id": ban.get("championId"),
                }
            )
    return rows


def normalize_frames(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = timeline["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(timeline.get("info", {}).get("frames", [])):
        for key, pf in frame.get("participantFrames", {}).items():
            stats = pf.get("championStats", {})
            damage = pf.get("damageStats", {})
            position = pf.get("position", {})
            rows.append(
                {
                    "match_id": match_id,
                    "participant_id": int(pf.get("participantId") or key),
                    "frame_index": frame_index,
                    "timestamp_ms": frame.get("timestamp"),
                    "position_x": position.get("x"),
                    "position_y": position.get("y"),
                    "level": pf.get("level"),
                    "xp": pf.get("xp"),
                    "current_gold": pf.get("currentGold"),
                    "total_gold": pf.get("totalGold"),
                    "minions_killed": pf.get("minionsKilled"),
                    "jungle_minions_killed": pf.get("jungleMinionsKilled"),
                    "time_enemy_spent_controlled": pf.get("timeEnemySpentControlled"),
                    "health": stats.get("health"),
                    "health_max": stats.get("healthMax"),
                    "health_regen": stats.get("healthRegen"),
                    "resource": stats.get("power"),
                    "resource_max": stats.get("powerMax"),
                    "resource_regen": stats.get("powerRegen"),
                    "armor": stats.get("armor"),
                    "magic_resist": stats.get("magicResist"),
                    "attack_damage": stats.get("attackDamage"),
                    "attack_speed": stats.get("attackSpeed"),
                    "ability_power": stats.get("abilityPower"),
                    "ability_haste": stats.get("abilityHaste"),
                    "movement_speed": stats.get("movementSpeed"),
                    "lifesteal": stats.get("lifesteal"),
                    "omnivamp": stats.get("omnivamp"),
                    "physical_vamp": stats.get("physicalVamp"),
                    "spell_vamp": stats.get("spellVamp"),
                    "armor_pen_flat": stats.get("armorPen"),
                    "armor_pen_percent": stats.get("armorPenPercent"),
                    "magic_pen_flat": stats.get("magicPen"),
                    "magic_pen_percent": stats.get("magicPenPercent"),
                    "total_damage_done": damage.get("totalDamageDone"),
                    "total_damage_done_to_champions": damage.get("totalDamageDoneToChampions"),
                    "total_damage_taken": damage.get("totalDamageTaken"),
                    "physical_damage_done": damage.get("physicalDamageDone"),
                    "physical_damage_done_to_champions": damage.get("physicalDamageDoneToChampions"),
                    "physical_damage_taken": damage.get("physicalDamageTaken"),
                    "magic_damage_done": damage.get("magicDamageDone"),
                    "magic_damage_done_to_champions": damage.get("magicDamageDoneToChampions"),
                    "magic_damage_taken": damage.get("magicDamageTaken"),
                    "true_damage_done": damage.get("trueDamageDone"),
                    "true_damage_done_to_champions": damage.get("trueDamageDoneToChampions"),
                    "true_damage_taken": damage.get("trueDamageTaken"),
                }
            )
    return rows


def normalize_raw_frames(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = timeline["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(timeline.get("info", {}).get("frames", [])):
        for key, participant_frame in frame.get("participantFrames", {}).items():
            rows.append(
                {
                    "match_id": match_id,
                    "participant_id": int(participant_frame.get("participantId") or key),
                    "frame_index": frame_index,
                    "payload": participant_frame,
                }
            )
    return rows


def normalize_events(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = timeline["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(timeline.get("info", {}).get("frames", [])):
        for event_index, event in enumerate(frame.get("events", [])):
            position = event.get("position", {})
            rows.append(
                {
                    "match_id": match_id,
                    "frame_index": frame_index,
                    "event_index": event_index,
                    "timestamp_ms": event.get("timestamp"),
                    "event_type": event.get("type"),
                    "participant_id": event.get("participantId"),
                    "killer_id": event.get("killerId"),
                    "victim_id": event.get("victimId"),
                    "creator_id": event.get("creatorId"),
                    "position_x": position.get("x"),
                    "position_y": position.get("y"),
                    "ward_type": event.get("wardType"),
                    "monster_type": event.get("monsterType"),
                    "monster_sub_type": event.get("monsterSubType"),
                    "building_type": event.get("buildingType"),
                    "tower_type": event.get("towerType"),
                    "lane_type": event.get("laneType"),
                    "item_id": event.get("itemId"),
                    "before_id": event.get("beforeId"),
                    "after_id": event.get("afterId"),
                    "skill_slot": event.get("skillSlot"),
                    "level_up_type": event.get("levelUpType"),
                    "level": event.get("level"),
                    "bounty": event.get("bounty"),
                    "kill_streak_length": event.get("killStreakLength"),
                    "team_id": event.get("teamId"),
                }
            )
    return rows


def normalize_timeline_event_assists(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = timeline["metadata"]["matchId"]
    rows: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(timeline.get("info", {}).get("frames", [])):
        for event_index, event in enumerate(frame.get("events", [])):
            for assisting_participant_id in event.get("assistingParticipantIds") or []:
                rows.append(
                    {
                        "match_id": match_id,
                        "frame_index": frame_index,
                        "event_index": event_index,
                        "assisting_participant_id": assisting_participant_id,
                    }
                )
    return rows


def normalize_raw_events(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = timeline["metadata"]["matchId"]
    return [
        {
            "match_id": match_id,
            "frame_index": frame_index,
            "event_index": event_index,
            "payload": event,
        }
        for frame_index, frame in enumerate(timeline.get("info", {}).get("frames", []))
        for event_index, event in enumerate(frame.get("events", []))
    ]


def target_rows_for_match(
    match: dict[str, Any], targets: Iterable[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Return qualifying target participants for (puuid, cohort_type) pairs."""
    rows: list[dict[str, Any]] = []
    for puuid, cohort_type in targets:
        participant = participant_for_puuid(match, puuid)
        classification = kayle_top_classification(participant)
        if classification["qualifies"]:
            rows.append(
                {
                    "puuid": puuid,
                    "cohort_type": cohort_type,
                    "participant_id": participant["participantId"],
                    **classification,
                }
            )
    return rows
