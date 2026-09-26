from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from normalizer import (  # noqa: E402
    kayle_top_classification,
    normalize_events,
    normalize_frames,
    normalize_match,
    normalize_participant_challenges,
    normalize_participant_items,
    normalize_participant_perk_selections,
    normalize_participant_perk_stats,
    normalize_participant_perk_styles,
    normalize_participants,
    normalize_raw_events,
    normalize_raw_frames,
    normalize_raw_participants,
    normalize_raw_teams,
    normalize_team_bans,
    normalize_team_objectives,
    normalize_teams,
    normalize_timeline_event_assists,
)
from patches import patch_from_version, unique_patches  # noqa: E402
from public_sources import (  # noqa: E402
    PublicCandidate,
    discover_kayle_candidates,
)
from discovery import (  # noqa: E402
    _multiregion_order,
    _round_robin_allocations,
    screen_reference_matches,
    smoke_discovery_flow,
)


def fixture_match():
    participants = []
    for participant_id in range(1, 11):
        participants.append(
            {
                "participantId": participant_id,
                "puuid": f"puuid-{participant_id}",
                "riotIdGameName": f"Player{participant_id}",
                "riotIdTagline": "NA1",
                "teamId": 100 if participant_id <= 5 else 200,
                "championId": 10 if participant_id == 1 else 100 + participant_id,
                "championName": "Kayle" if participant_id == 1 else f"Champion{participant_id}",
                "teamPosition": "TOP" if participant_id in (1, 6) else "MIDDLE",
                "individualPosition": "TOP" if participant_id in (1, 6) else "MIDDLE",
                "win": participant_id <= 5,
                "kills": participant_id,
                "deaths": 1,
                "assists": 2,
                "item0": 1056,
                "perks": {
                    "statPerks": {"offense": 5005, "flex": 5008, "defense": 5002},
                    "styles": [
                        {
                            "description": "primaryStyle",
                            "style": 8000,
                            "selections": [{"perk": 8005, "var1": 1, "var2": 2, "var3": 3}],
                        }
                    ],
                },
                "challenges": {"unknownFutureMetric": 123},
                "futureParticipantField": "preserved",
            }
        )
    return {
        "metadata": {"matchId": "NA1_FIXTURE", "participants": [p["puuid"] for p in participants]},
        "info": {
            "platformId": "NA1",
            "queueId": 420,
            "mapId": 11,
            "gameCreation": 1770000000000,
            "gameStartTimestamp": 1770000001000,
            "gameEndTimestamp": 1770001801000,
            "gameDuration": 1800,
            "gameVersion": "26.18.701.9999",
            "participants": participants,
            "teams": [
                {
                    "teamId": 100,
                    "win": True,
                    "objectives": {
                        "baron": {"first": True, "kills": 1},
                        "champion": {"first": True, "kills": 25},
                        "dragon": {"first": True, "kills": 3},
                        "horde": {"first": True, "kills": 6},
                        "inhibitor": {"first": True, "kills": 2},
                        "riftHerald": {"first": False, "kills": 1},
                        "tower": {"first": True, "kills": 10},
                    },
                    "bans": [
                        {"championId": 1, "pickTurn": 1},
                        {"championId": 2, "pickTurn": 3},
                    ],
                },
                {
                    "teamId": 200,
                    "win": False,
                    "objectives": {
                        "baron": {"first": False, "kills": 0},
                        "champion": {"first": False, "kills": 12},
                        "dragon": {"first": False, "kills": 1},
                        "horde": {"first": False, "kills": 0},
                        "inhibitor": {"first": False, "kills": 0},
                        "riftHerald": {"first": True, "kills": 1},
                        "tower": {"first": False, "kills": 2},
                    },
                    "bans": [
                        {"championId": 3, "pickTurn": 2},
                        {"championId": 4, "pickTurn": 4},
                    ],
                },
            ],
        },
    }


def fixture_timeline():
    frames = []
    for frame_index in range(3):
        participant_frames = {}
        for participant_id in range(1, 11):
            participant_frames[str(participant_id)] = {
                "participantId": participant_id,
                "position": {"x": participant_id * 100, "y": participant_id * 200},
                "level": frame_index + 1,
                "xp": frame_index * 100,
                "currentGold": 500,
                "totalGold": 500 + frame_index * 300,
                "minionsKilled": frame_index * 6,
                "jungleMinionsKilled": 0,
                "championStats": {
                    "health": 600,
                    "healthMax": 700,
                    "attackDamage": 60,
                    "abilityPower": 0,
                    "movementSpeed": 335,
                    "futureChampionStat": 9,
                },
                "damageStats": {"totalDamageDone": frame_index * 100},
                "futureFrameField": "preserved",
            }
        frames.append(
            {
                "timestamp": frame_index * 60000,
                "participantFrames": participant_frames,
                "events": [
                    {
                        "timestamp": frame_index * 60000 + 500,
                        "type": "ITEM_PURCHASED",
                        "participantId": 1,
                        "assistingParticipantIds": [2, 3],
                        "itemId": 1056,
                        "futureEventField": "preserved",
                    }
                ],
            }
        )
    return {"metadata": {"matchId": "NA1_FIXTURE"}, "info": {"frames": frames}}


class AcceptanceTests(unittest.TestCase):
    def test_patch_parsing_and_three_unique_versions(self):
        self.assertEqual(patch_from_version("26.18.701.9999"), "26.18")
        self.assertEqual(unique_patches(["26.18.1", "26.18.2", "26.17.1", "26.16.1"]), ["26.18", "26.17", "26.16"])

    def test_match_normalizes_once_with_all_ten_participants(self):
        match = fixture_match()
        self.assertEqual(normalize_match(match)["match_id"], "NA1_FIXTURE")
        participants = normalize_participants(match)
        self.assertEqual(len(participants), 10)
        self.assertEqual(len({(p["match_id"], p["participant_id"]) for p in participants}), 10)
        teams = normalize_teams(match)
        self.assertEqual(len(teams), 2)
        self.assertEqual(teams[0], {"match_id": "NA1_FIXTURE", "team_id": 100, "win": True})
        objectives = normalize_team_objectives(match)
        self.assertEqual(len(objectives), 14)
        self.assertIn(
            {"match_id": "NA1_FIXTURE", "team_id": 100, "objective_type": "dragon", "first": True, "kills": 3},
            objectives,
        )
        bans = normalize_team_bans(match)
        self.assertEqual(len(bans), 4)
        self.assertEqual(bans[0], {
            "match_id": "NA1_FIXTURE",
            "team_id": 100,
            "pick_turn": 1,
            "champion_id": 1,
        })
        self.assertEqual(len(normalize_participant_items(match)), 70)
        self.assertEqual(normalize_participant_perk_stats(match)[0]["offense_perk_id"], 5005)
        self.assertEqual(normalize_participant_perk_styles(match)[0]["style_id"], 8000)
        self.assertEqual(normalize_participant_perk_selections(match)[0]["perk_id"], 8005)
        self.assertEqual(normalize_participant_challenges(match)[0]["value_numeric"], 123)
        self.assertEqual(normalize_raw_participants(match)[0]["payload"]["futureParticipantField"], "preserved")
        self.assertEqual(normalize_raw_teams(match)[0]["payload"]["teamId"], 100)

    def test_all_frames_all_participants_and_events_are_retained(self):
        timeline = fixture_timeline()
        frames = normalize_frames(timeline)
        events = normalize_events(timeline)
        self.assertEqual(len(frames), 3 * 10)
        self.assertEqual(len({(f["match_id"], f["participant_id"], f["frame_index"]) for f in frames}), 30)
        self.assertEqual(len(events), 3)
        self.assertEqual(normalize_raw_frames(timeline)[0]["payload"]["futureFrameField"], "preserved")
        self.assertEqual(normalize_raw_events(timeline)[0]["payload"]["futureEventField"], "preserved")
        self.assertEqual(len(normalize_timeline_event_assists(timeline)), 6)

    def test_top_rule_uses_documented_fallback_and_flags_conflict(self):
        self.assertTrue(kayle_top_classification({"championId": 10, "teamPosition": "TOP", "individualPosition": "TOP"})["qualifies"])
        fallback = kayle_top_classification({"championId": 10, "teamPosition": "", "individualPosition": "TOP"})
        self.assertTrue(fallback["qualifies"])
        self.assertEqual(fallback["role_source"], "individualPosition_fallback")
        conflict = kayle_top_classification({"championId": 10, "teamPosition": "MIDDLE", "individualPosition": "TOP"})
        self.assertFalse(conflict["qualifies"])
        self.assertTrue(conflict["ambiguous"])

    def test_schema_has_idempotent_keys_and_raw_jsonb(self):
        schema = (ROOT / "sql" / "01_schema.sql").read_text(encoding="utf-8")
        self.assertIn("PRIMARY KEY (match_id, participant_id, frame_index)", schema)
        self.assertIn("PRIMARY KEY (match_id, frame_index, event_index)", schema)
        self.assertIn("PRIMARY KEY (match_id, team_id, pick_turn)", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS team_objectives", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS participant_perk_selections", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS timeline_event_assists", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS raw_archive.raw_participant_frames", schema)
        self.assertIn("payload JSONB NOT NULL", schema)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS raw_archive", schema)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS pipeline_internal", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS candidate_qualifying_matches", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS platform_regions", schema)
        self.assertIn("pipeline_internal.player_scan_checkpoints", schema)
        self.assertIn("platform_code TEXT REFERENCES platform_regions", schema)
        self.assertNotIn("qualifying_match_ids TEXT[]", schema)

    def test_fixed_opgg_snapshot_has_150_unique_multiregion_riot_ids(self):
        candidates = discover_kayle_candidates()
        self.assertEqual(len(candidates), 150)
        self.assertEqual(
            len({(row.platform, row.game_name.casefold(), row.tag_line.casefold()) for row in candidates}),
            150,
        )
        self.assertEqual(
            Counter(row.platform for row in candidates),
            Counter({"na1": 30, "euw1": 30, "kr": 30, "eun1": 30, "br1": 30}),
        )
        self.assertEqual(
            [row.platform for row in candidates[:5]],
            ["na1", "euw1", "kr", "eun1", "br1"],
        )
        self.assertEqual(
            {(row.platform, row.routing) for row in candidates},
            {
                ("na1", "americas"),
                ("br1", "americas"),
                ("euw1", "europe"),
                ("eun1", "europe"),
                ("kr", "asia"),
            },
        )

    def test_multiregion_planning_interleaves_platforms_and_caps_games(self):
        rows = []
        for platform in ("NA1", "EUW1", "KR"):
            for number in range(2):
                rows.append(
                    {
                        "puuid": f"{platform}-{number}",
                        "platform_code": platform,
                        "tier": "MASTER",
                        "division": None,
                        "league_points": 100 + number,
                        "source_position": number + 1,
                        "qualifying_kayle_top_games": 12,
                    }
                )
        ordered = _multiregion_order(rows)
        self.assertEqual(
            [row["platform_code"] for row in ordered[:3]],
            ["EUW1", "KR", "NA1"],
        )
        allocations = _round_robin_allocations(ordered, target=35, per_player_cap=10)
        self.assertEqual(sum(allocations.values()), 35)
        self.assertLessEqual(max(allocations.values()), 10)

    def test_reference_screen_stops_once_minimum_games_are_found(self):
        class FakeAPI:
            def match_ids(self, *args, **kwargs):
                return [f"NA1_{n}" for n in range(1, 6)]

        matches = {
            f"NA1_{n}": {
                "info": {
                    "gameVersion": "26.18.1",
                    "participants": [
                        {
                            "puuid": "target",
                            "championId": 10,
                            "teamPosition": "TOP",
                            "individualPosition": "TOP",
                        }
                    ],
                }
            }
            for n in range(1, 6)
        }
        seen = []

        def fake_cached_match(conn, api, match_id):
            seen.append(match_id)
            return matches[match_id]

        with patch("discovery._cached_match", side_effect=fake_cached_match):
            qualifying, checked = screen_reference_matches(
                object(), FakeAPI(), "target", {"26.18"}, 100, 2
            )
        self.assertEqual(qualifying, ["NA1_1", "NA1_2"])
        self.assertEqual(checked, 2)
        self.assertEqual(seen, ["NA1_1", "NA1_2"])

    def test_smoke_discovery_runs_public_id_through_riot_validation(self):
        candidates = [
            PublicCandidate("Eligible", "NA1", 10),
            PublicCandidate("Too Low", "NA1", 50),
        ]

        class Settings:
            min_kayle_mastery_points = 10000

        class FakeAPI:
            mastery_calls = []

            def for_region(self, platform, routing):
                self.platform = platform
                self.routing = routing
                return self

            def account_by_riot_id(self, game_name, tag_line):
                return {"puuid": game_name}

            def ranked_entries_by_puuid(self, puuid):
                tier = "EMERALD" if puuid == "Eligible" else "PLATINUM"
                return [{"queueType": "RANKED_SOLO_5x5", "tier": tier, "rank": "IV"}]

            def kayle_mastery(self, puuid):
                self.mastery_calls.append(puuid)
                return {"championPoints": 50000}

            def match_ids(self, puuid, **kwargs):
                return ["NA1_SMOKE"]

            def match(self, match_id):
                return {
                    "info": {
                        "gameVersion": "26.18.1",
                        "participants": [{
                            "puuid": "Eligible",
                            "championId": 10,
                            "teamPosition": "TOP",
                            "individualPosition": "TOP",
                        }],
                    }
                }

        api = FakeAPI()
        with patch("discovery.discover_kayle_candidates", return_value=candidates):
            reports = smoke_discovery_flow(
                api, Settings(), ("26.18", "26.17", "26.16"), count=2
            )
        self.assertEqual(reports[0]["result"], "kayle_top_flow_validated")
        self.assertEqual(reports[1]["result"], "below_emerald_iv_or_unranked")
        self.assertEqual(api.mastery_calls, ["Eligible"])
        self.assertEqual(reports[0]["platform"], "NA1")


if __name__ == "__main__":
    unittest.main()
