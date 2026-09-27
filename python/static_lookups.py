"""Refresh human-readable Riot code dictionaries without fetching any matches."""

from __future__ import annotations

from typing import Any, Iterable

import requests

from patches import VERSIONS_URL, patch_from_version

DD_BASE = "https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/{file_name}"
QUEUES_URL = "https://static.developer.riotgames.com/docs/lol/queues.json"
MAPS_URL = "https://static.developer.riotgames.com/docs/lol/maps.json"


def _get_json(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _database_patches(conn: Any) -> set[str]:
    rows = conn.execute(
        """
        SELECT patch FROM matches WHERE patch IS NOT NULL
        UNION
        SELECT patch FROM collection_run_patches WHERE patch IS NOT NULL
        """
    ).fetchall()
    return {str(row["patch"]) for row in rows if row.get("patch")}


def data_dragon_versions_for_patches(
    available_versions: Iterable[str], patches: set[str]
) -> list[str]:
    """Choose Riot's newest Data Dragon build for each stored major.minor patch."""
    versions = list(available_versions)
    newest_by_patch: dict[str, str] = {}
    for version in versions:
        patch = patch_from_version(version)
        if patch and patch not in newest_by_patch:
            newest_by_patch[patch] = version
    selected = [newest_by_patch[patch] for patch in patches if patch in newest_by_patch]
    if not selected and versions:
        selected = [versions[0]]
    return sorted(
        set(selected),
        key=lambda value: tuple(int(part) for part in value.split(".")[:2]),
    )


def champion_rows(payload: dict[str, Any], version: str) -> list[tuple[Any, ...]]:
    return [
        (
            int(champion["key"]),
            champion.get("id"),
            champion.get("name") or f"Champion {champion['key']}",
            champion.get("title"),
            version,
            "DATA_DRAGON",
        )
        for champion in payload.get("data", {}).values()
    ]


def item_rows(payload: dict[str, Any], version: str) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for item_id, item in payload.get("data", {}).items():
        gold = item.get("gold") or {}
        rows.append(
            (
                int(item_id),
                item.get("name") or f"Item {item_id}",
                item.get("description"),
                item.get("plaintext"),
                gold.get("total"),
                item.get("purchasable", gold.get("purchasable")),
                version,
                "DATA_DRAGON",
            )
        )
    return rows


def summoner_spell_rows(
    payload: dict[str, Any], version: str
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for spell in payload.get("data", {}).values():
        cooldowns = spell.get("cooldown") or []
        cooldown = cooldowns[0] if cooldowns else None
        rows.append(
            (
                int(spell["key"]),
                spell.get("id"),
                spell.get("name") or f"Summoner spell {spell['key']}",
                spell.get("description"),
                cooldown,
                spell.get("summonerLevel"),
                version,
                "DATA_DRAGON",
            )
        )
    return rows


def rune_rows(
    payload: list[dict[str, Any]], version: str
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    styles: list[tuple[Any, ...]] = []
    perks: list[tuple[Any, ...]] = []
    for style in payload:
        style_id = int(style["id"])
        styles.append(
            (
                style_id,
                style.get("key"),
                style.get("name") or f"Rune style {style_id}",
                style.get("icon"),
                version,
                "DATA_DRAGON",
            )
        )
        for slot_index, slot in enumerate(style.get("slots") or []):
            for perk in slot.get("runes") or []:
                perks.append(
                    (
                        int(perk["id"]),
                        perk.get("key"),
                        perk.get("name") or f"Perk {perk['id']}",
                        perk.get("shortDesc"),
                        perk.get("longDesc"),
                        style_id,
                        slot_index,
                        slot_index == 0,
                        perk.get("icon"),
                        version,
                        "DATA_DRAGON",
                    )
                )
    return styles, perks


def _many(conn: Any, query: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(query, rows)


def _refresh_data_dragon_version(
    conn: Any, session: Any, version: str, timeout: int
) -> dict[str, int]:
    champion_payload = _get_json(
        session, DD_BASE.format(version=version, file_name="champion.json"), timeout
    )
    item_payload = _get_json(
        session, DD_BASE.format(version=version, file_name="item.json"), timeout
    )
    spell_payload = _get_json(
        session, DD_BASE.format(version=version, file_name="summoner.json"), timeout
    )
    rune_payload = _get_json(
        session, DD_BASE.format(version=version, file_name="runesReforged.json"), timeout
    )

    champions = champion_rows(champion_payload, version)
    items = item_rows(item_payload, version)
    spells = summoner_spell_rows(spell_payload, version)
    styles, perks = rune_rows(rune_payload, version)

    _many(
        conn,
        """
        INSERT INTO champion_catalog
          (champion_id, champion_key, champion_name, champion_title,
           data_dragon_version, lookup_source)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (champion_id) DO UPDATE SET
          champion_key=EXCLUDED.champion_key,
          champion_name=EXCLUDED.champion_name,
          champion_title=EXCLUDED.champion_title,
          data_dragon_version=EXCLUDED.data_dragon_version,
          lookup_source=EXCLUDED.lookup_source,
          updated_at=now()
        """,
        champions,
    )
    _many(
        conn,
        """
        INSERT INTO item_catalog
          (item_id, item_name, description, plaintext, gold_total, purchasable,
           data_dragon_version, lookup_source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (item_id) DO UPDATE SET
          item_name=EXCLUDED.item_name,
          description=EXCLUDED.description,
          plaintext=EXCLUDED.plaintext,
          gold_total=EXCLUDED.gold_total,
          purchasable=EXCLUDED.purchasable,
          data_dragon_version=EXCLUDED.data_dragon_version,
          lookup_source=EXCLUDED.lookup_source,
          updated_at=now()
        """,
        items,
    )
    _many(
        conn,
        """
        INSERT INTO summoner_spell_catalog
          (summoner_spell_id, spell_key, spell_name, description,
           cooldown_seconds, required_level, data_dragon_version, lookup_source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (summoner_spell_id) DO UPDATE SET
          spell_key=EXCLUDED.spell_key,
          spell_name=EXCLUDED.spell_name,
          description=EXCLUDED.description,
          cooldown_seconds=EXCLUDED.cooldown_seconds,
          required_level=EXCLUDED.required_level,
          data_dragon_version=EXCLUDED.data_dragon_version,
          lookup_source=EXCLUDED.lookup_source,
          updated_at=now()
        """,
        spells,
    )
    _many(
        conn,
        """
        INSERT INTO rune_style_catalog
          (style_id, style_key, style_name, icon_path,
           data_dragon_version, lookup_source)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (style_id) DO UPDATE SET
          style_key=EXCLUDED.style_key,
          style_name=EXCLUDED.style_name,
          icon_path=EXCLUDED.icon_path,
          data_dragon_version=EXCLUDED.data_dragon_version,
          lookup_source=EXCLUDED.lookup_source,
          updated_at=now()
        """,
        styles,
    )
    _many(
        conn,
        """
        INSERT INTO perk_catalog
          (perk_id, perk_key, perk_name, short_description, long_description,
           style_id, slot_index, is_keystone, icon_path,
           data_dragon_version, lookup_source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (perk_id) DO UPDATE SET
          perk_key=EXCLUDED.perk_key,
          perk_name=EXCLUDED.perk_name,
          short_description=EXCLUDED.short_description,
          long_description=EXCLUDED.long_description,
          style_id=EXCLUDED.style_id,
          slot_index=EXCLUDED.slot_index,
          is_keystone=EXCLUDED.is_keystone,
          icon_path=EXCLUDED.icon_path,
          data_dragon_version=EXCLUDED.data_dragon_version,
          lookup_source=EXCLUDED.lookup_source,
          updated_at=now()
        """,
        perks,
    )
    return {
        "champions": len(champions),
        "items": len(items),
        "summoner_spells": len(spells),
        "rune_styles": len(styles),
        "perks": len(perks),
    }


def _refresh_riot_documented_codes(
    conn: Any, session: Any, timeout: int
) -> dict[str, int]:
    queues = _get_json(session, QUEUES_URL, timeout)
    maps = _get_json(session, MAPS_URL, timeout)
    queue_rows = [
        (
            int(row["queueId"]),
            row.get("map"),
            row.get("description") or f"Queue {row['queueId']}",
            row.get("notes"),
            "RIOT_STATIC_DOCS",
        )
        for row in queues
    ]
    map_rows = [
        (
            int(row["mapId"]),
            row.get("mapName") or f"Map {row['mapId']}",
            row.get("notes"),
            "RIOT_STATIC_DOCS",
        )
        for row in maps
    ]
    _many(
        conn,
        """
        INSERT INTO queue_catalog
          (queue_id, map_name, queue_description, notes, lookup_source)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (queue_id) DO UPDATE SET
          map_name=EXCLUDED.map_name,
          queue_description=EXCLUDED.queue_description,
          notes=EXCLUDED.notes,
          lookup_source=EXCLUDED.lookup_source,
          updated_at=now()
        """,
        queue_rows,
    )
    _many(
        conn,
        """
        INSERT INTO map_catalog (map_id, map_name, notes, lookup_source)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (map_id) DO UPDATE SET
          map_name=EXCLUDED.map_name,
          notes=EXCLUDED.notes,
          lookup_source=EXCLUDED.lookup_source,
          updated_at=now()
        """,
        map_rows,
    )
    return {"queues": len(queue_rows), "maps": len(map_rows)}


def _coverage(conn: Any) -> list[dict[str, Any]]:
    tables = (
        ("champion_catalog", "champion_id"),
        ("item_catalog", "item_id"),
        ("summoner_spell_catalog", "summoner_spell_id"),
        ("rune_style_catalog", "style_id"),
        ("perk_catalog", "perk_id"),
        ("stat_perk_catalog", "stat_perk_id"),
        ("queue_catalog", "queue_id"),
        ("map_catalog", "map_id"),
        ("team_side_catalog", "team_id"),
        ("skill_slot_catalog", "skill_slot"),
    )
    report: list[dict[str, Any]] = []
    for table, id_column in tables:
        row = conn.execute(
            f"""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE lookup_source='PLACEHOLDER') AS unmapped
            FROM {table}
            """
        ).fetchone()
        report.append(
            {
                "table": table,
                "id_column": id_column,
                "total": int(row["total"]),
                "unmapped": int(row["unmapped"]),
            }
        )
    return report


def refresh_static_lookups(
    conn: Any, timeout: int = 30, session: Any | None = None
) -> dict[str, Any]:
    """Refresh lookup dimensions only; no Riot API key or match calls are used."""
    http = session or requests.Session()
    versions = _get_json(http, VERSIONS_URL, timeout)
    patches = _database_patches(conn)
    selected_versions = data_dragon_versions_for_patches(versions, patches)
    counts: dict[str, int] = {}
    try:
        for version in selected_versions:
            version_counts = _refresh_data_dragon_version(
                conn, http, version, timeout
            )
            counts.update(version_counts)
        counts.update(_refresh_riot_documented_codes(conn, http, timeout))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    coverage = _coverage(conn)
    return {
        "versions": selected_versions,
        "counts": counts,
        "coverage": coverage,
    }


def print_lookup_report(report: dict[str, Any]) -> None:
    versions = ", ".join(report["versions"]) or "none"
    print(f"[lookups] Data Dragon versions: {versions}")
    for row in report["coverage"]:
        print(
            f"  {row['table']}: {row['total']} IDs; "
            f"{row['unmapped']} still explicitly marked unmapped"
        )
    print("[lookups] Lookup refresh completed; no match or timeline endpoints were called")
