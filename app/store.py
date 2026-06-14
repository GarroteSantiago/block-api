"""
SQLite persistence for the BloCK backend.

Two stores:
  * sync_profiles  - the personal-sync mirror (single-tenant/global for the
                     demo). Each row carries a server_updated_at stamp used as
                     the pull watermark, independent of the domain updatedAt.
  * catalog        - the community catalog backing search / detail / publish.

Restrictions are stored as a JSON blob on the owning row; they are small and
always read/written together with their profile.
"""

import json
import sqlite3
import time
from pathlib import Path

from .models import (
    ProfileDetail,
    ProfileSearchResult,
    ProfileSync,
    PublishProfile,
    Restriction,
)

DB_PATH = Path(__file__).resolve().parent.parent / "block.db"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_profiles (
                id                TEXT PRIMARY KEY,
                owner_id          TEXT NOT NULL,
                name              TEXT NOT NULL,
                description       TEXT NOT NULL,
                visibility        TEXT NOT NULL,
                state             TEXT NOT NULL,
                deleted           INTEGER NOT NULL,
                updated_at        INTEGER NOT NULL,
                restrictions      TEXT NOT NULL,
                server_updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalog (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                description  TEXT NOT NULL,
                author_name  TEXT NOT NULL,
                restrictions TEXT NOT NULL
            );
            """
        )
    _seed_catalog()


# --- Personal sync ---------------------------------------------------------

def upsert_sync_profiles(records: list[ProfileSync]) -> None:
    """Store pushed profiles, stamping each with the server's change time."""
    stamp = _now_ms()
    with _connect() as conn:
        for r in records:
            conn.execute(
                """
                INSERT INTO sync_profiles
                    (id, owner_id, name, description, visibility, state,
                     deleted, updated_at, restrictions, server_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    name=excluded.name,
                    description=excluded.description,
                    visibility=excluded.visibility,
                    state=excluded.state,
                    deleted=excluded.deleted,
                    updated_at=excluded.updated_at,
                    restrictions=excluded.restrictions,
                    server_updated_at=excluded.server_updated_at
                """,
                (
                    r.id, r.ownerId, r.name, r.description, r.visibility,
                    r.state, int(r.deleted), r.updatedAt,
                    json.dumps([x.model_dump() for x in r.restrictions]),
                    stamp,
                ),
            )


def pull_sync_profiles(since: int) -> tuple[list[ProfileSync], int]:
    """Profiles whose server stamp is newer than `since`, plus a fresh watermark.

    serverTimestamp is the wall clock at read time: every returned row has a
    server stamp <= it, so the client's next pull (since=this) excludes them,
    while anything pushed afterwards is picked up.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_profiles WHERE server_updated_at > ? "
            "ORDER BY server_updated_at ASC",
            (since,),
        ).fetchall()
    records = [_row_to_sync(row) for row in rows]
    return records, _now_ms()


def _row_to_sync(row: sqlite3.Row) -> ProfileSync:
    return ProfileSync(
        id=row["id"],
        ownerId=row["owner_id"],
        name=row["name"],
        description=row["description"],
        visibility=row["visibility"],
        state=row["state"],
        deleted=bool(row["deleted"]),
        updatedAt=row["updated_at"],
        restrictions=[Restriction(**x) for x in json.loads(row["restrictions"])],
    )


# --- Community catalog -----------------------------------------------------

def search_catalog(query: str) -> list[ProfileSearchResult]:
    q = (query or "").strip().lower()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM catalog").fetchall()
    results = []
    for row in rows:
        if q and q not in row["name"].lower() and q not in row["description"].lower():
            continue
        restrictions = json.loads(row["restrictions"])
        app_count = len({r["appId"] for r in restrictions})
        results.append(
            ProfileSearchResult(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                appCount=app_count,
                authorName=row["author_name"],
            )
        )
    return results


def get_catalog_profile(profile_id: str) -> ProfileDetail | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM catalog WHERE id = ?", (profile_id,)
        ).fetchone()
    if row is None:
        return None
    return ProfileDetail(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        restrictions=[Restriction(**x) for x in json.loads(row["restrictions"])],
    )


def publish_catalog_profile(dto: PublishProfile, author_name: str = "community") -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO catalog (id, name, description, author_name, restrictions)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                restrictions=excluded.restrictions
            """,
            (
                dto.id, dto.name, dto.description, author_name,
                json.dumps([x.model_dump() for x in dto.restrictions]),
            ),
        )


def _seed_catalog() -> None:
    """Seed a few community profiles so search is non-empty on a fresh deploy."""
    seeds = [
        (
            "550e8400-e29b-41d4-a716-446655440001", "Focus Mode",
            "Block social media and entertainment apps",
            [("com.instagram.android", "ALWAYS_ON", "{}"),
             ("com.zhiliaoapp.musically", "ALWAYS_ON", "{}"),
             ("com.google.android.youtube", "ALWAYS_ON", "{}")],
        ),
        (
            "550e8400-e29b-41d4-a716-446655440002", "Study Session",
            "Keep distracting apps away while studying",
            [("com.instagram.android", "USAGE_QUOTA", '{"limitMinutes":15}'),
             ("com.whatsapp", "USAGE_QUOTA", '{"limitMinutes":20}')],
        ),
        (
            "550e8400-e29b-41d4-a716-446655440003", "Bedtime",
            "No screens after bedtime",
            [("com.instagram.android", "TIME_RANGE", '{"start":"22:00","end":"07:00"}'),
             ("com.google.android.youtube", "TIME_RANGE", '{"start":"22:00","end":"07:00"}')],
        ),
        (
            "550e8400-e29b-41d4-a716-446655440004", "Work Only",
            "Allow only productivity apps during work hours",
            [("com.instagram.android", "TIME_RANGE", '{"start":"09:00","end":"17:00"}')],
        ),
    ]
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM catalog").fetchone()["c"]
        if count:
            return
        for idx, (pid, name, desc, restrictions) in enumerate(seeds):
            payload = [
                {"id": f"r-{pid}-{i}", "appId": app, "type": rtype, "config": cfg}
                for i, (app, rtype, cfg) in enumerate(restrictions)
            ]
            conn.execute(
                "INSERT INTO catalog (id, name, description, author_name, restrictions) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, name, desc, "community", json.dumps(payload)),
            )
