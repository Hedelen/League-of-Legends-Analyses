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
        items = [p.get(f"item{i}") for i in range(7)]
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
                "items": items,
                "summoner_spell_1": p.get("summoner1Id"),
                "summoner_spell_2": p.get("summoner2Id"),
                "perks": p.get("perks"),
                "challenges": p.get("challenges"),
                "raw_participant": p,
            }
        )
    return rows


def normalize_teams(match: dict[str, Any]) -> list[dict[str, Any]]:
    match_id = match["metadata"]["matchId"]
    return [
        {
            "match_id": match_id,
            "team_id": team.get("teamId"),
            "win": team.get("win"),
            "objectives": team.get("objectives"),
            "bans": team.get("bans"),
            "raw_team": team,
        }
        for team in match.get("info", {}).get("teams", [])
    ]


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
                    "raw_frame": pf,
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
                    "assisting_participant_ids": event.get("assistingParticipantIds") or [],
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
                    "raw_event": event,
                }
            )
    return rows


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
