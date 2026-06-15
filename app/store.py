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
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from .models import (
    CreateGroupRequest,
    GroupDetail,
    GroupMember,
    GroupSummary,
    InvitePreview,
    ProfileDetail,
    ProfileSearchResult,
    ProfileSync,
    PublishProfile,
    Restriction,
    ShareProfileRequest,
    SharedProfile,
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

            CREATE TABLE IF NOT EXISTS groups (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                owner_uid  TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS group_members (
                group_id      TEXT NOT NULL,
                uid           TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                focus_seconds INTEGER NOT NULL DEFAULT 0,
                joined_at     INTEGER NOT NULL,
                PRIMARY KEY (group_id, uid)
            );

            CREATE TABLE IF NOT EXISTS group_invites (
                token      TEXT PRIMARY KEY,
                group_id   TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS group_profiles (
                group_id       TEXT NOT NULL,
                profile_id     TEXT NOT NULL,
                name           TEXT NOT NULL,
                description    TEXT NOT NULL,
                restrictions   TEXT NOT NULL,
                shared_by_uid  TEXT NOT NULL,
                shared_by_name TEXT NOT NULL,
                shared_at      INTEGER NOT NULL,
                PRIMARY KEY (group_id, profile_id)
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


# --- Friend groups ---------------------------------------------------------

def create_group(req: CreateGroupRequest, uid: str, display_name: str) -> str:
    """Create a group and auto-join the creator as owner. Returns the group id."""
    group_id = uuid.uuid4().hex
    now = _now_ms()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO groups (id, name, owner_uid, created_at) VALUES (?, ?, ?, ?)",
            (group_id, req.name, uid, now),
        )
        conn.execute(
            "INSERT INTO group_members (group_id, uid, display_name, focus_seconds, joined_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (group_id, uid, display_name, now),
        )
    return group_id


def list_groups(uid: str) -> list[GroupSummary]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT g.id, g.name, COUNT(m2.uid) AS member_count
            FROM groups g
            JOIN group_members me ON me.group_id = g.id AND me.uid = ?
            JOIN group_members m2 ON m2.group_id = g.id
            GROUP BY g.id, g.name
            ORDER BY g.created_at DESC
            """,
            (uid,),
        ).fetchall()
    return [
        GroupSummary(id=r["id"], name=r["name"], memberCount=r["member_count"])
        for r in rows
    ]


def is_member(group_id: str, uid: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND uid = ?",
            (group_id, uid),
        ).fetchone()
    return row is not None


def get_group_detail(group_id: str) -> GroupDetail | None:
    with _connect() as conn:
        group = conn.execute(
            "SELECT * FROM groups WHERE id = ?", (group_id,)
        ).fetchone()
        if group is None:
            return None
        member_rows = conn.execute(
            "SELECT * FROM group_members WHERE group_id = ? "
            "ORDER BY focus_seconds DESC, display_name ASC",
            (group_id,),
        ).fetchall()
        profile_rows = conn.execute(
            "SELECT * FROM group_profiles WHERE group_id = ? ORDER BY shared_at DESC",
            (group_id,),
        ).fetchall()

    members = [
        GroupMember(
            uid=r["uid"],
            displayName=r["display_name"],
            focusSeconds=r["focus_seconds"],
            isOwner=(r["uid"] == group["owner_uid"]),
        )
        for r in member_rows
    ]
    shared = [
        SharedProfile(
            id=r["profile_id"],
            name=r["name"],
            description=r["description"],
            restrictions=[Restriction(**x) for x in json.loads(r["restrictions"])],
            sharedByUid=r["shared_by_uid"],
            sharedByName=r["shared_by_name"],
        )
        for r in profile_rows
    ]
    return GroupDetail(
        id=group["id"],
        name=group["name"],
        ownerUid=group["owner_uid"],
        members=members,
        sharedProfiles=shared,
    )


def create_invite(group_id: str) -> str:
    """Mint an unguessable invite token for a group. Returns the token."""
    token = secrets.token_urlsafe(16)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO group_invites (token, group_id, created_at) VALUES (?, ?, ?)",
            (token, group_id, _now_ms()),
        )
    return token


def preview_invite(token: str) -> InvitePreview | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT g.id, g.name FROM group_invites i
            JOIN groups g ON g.id = i.group_id
            WHERE i.token = ?
            """,
            (token,),
        ).fetchone()
    if row is None:
        return None
    return InvitePreview(groupId=row["id"], groupName=row["name"])


def join_by_token(token: str, uid: str, display_name: str) -> str | None:
    """Add the caller to the group the token points at. Returns the group id."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT group_id FROM group_invites WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        group_id = row["group_id"]
        conn.execute(
            "INSERT INTO group_members (group_id, uid, display_name, focus_seconds, joined_at) "
            "VALUES (?, ?, ?, 0, ?) "
            "ON CONFLICT(group_id, uid) DO UPDATE SET display_name=excluded.display_name",
            (group_id, uid, display_name, _now_ms()),
        )
    return group_id


def share_profile(group_id: str, req: ShareProfileRequest, uid: str, display_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO group_profiles
                (group_id, profile_id, name, description, restrictions,
                 shared_by_uid, shared_by_name, shared_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, profile_id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                restrictions=excluded.restrictions,
                shared_by_uid=excluded.shared_by_uid,
                shared_by_name=excluded.shared_by_name,
                shared_at=excluded.shared_at
            """,
            (
                group_id, req.id, req.name, req.description,
                json.dumps([x.model_dump() for x in req.restrictions]),
                uid, display_name, _now_ms(),
            ),
        )


def report_focus(group_id: str, uid: str, focus_seconds: int) -> None:
    """Set the caller's leaderboard value for a group (idempotent, absolute)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE group_members SET focus_seconds = ? WHERE group_id = ? AND uid = ?",
            (focus_seconds, group_id, uid),
        )


def leave_group(group_id: str, uid: str) -> None:
    """Remove the caller from a group. If it empties out, delete the group."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND uid = ?",
            (group_id, uid),
        )
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM group_members WHERE group_id = ?",
            (group_id,),
        ).fetchone()["c"]
        if remaining == 0:
            conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
            conn.execute("DELETE FROM group_invites WHERE group_id = ?", (group_id,))
            conn.execute("DELETE FROM group_profiles WHERE group_id = ?", (group_id,))
