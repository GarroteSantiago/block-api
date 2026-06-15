"""
Wire models for the BloCK backend.

Field names are deliberately camelCase to match the Android client's Gson
serialization exactly (e.g. ProfileSyncDto, BlockApiService). The JSON keys are
the contract; do not rename them without changing the client DTOs in lockstep.
"""

from pydantic import BaseModel


# --- Shared ----------------------------------------------------------------

class Restriction(BaseModel):
    """One restriction on one app. Matches RestrictionSyncDto / RestrictionDto."""
    id: str
    appId: str
    type: str
    config: str


# --- Personal sync (SyncApiService) ----------------------------------------

class ProfileSync(BaseModel):
    """A profile as it travels for personal sync. Matches ProfileSyncDto."""
    id: str
    ownerId: str
    name: str
    description: str
    visibility: str
    state: str
    deleted: bool
    updatedAt: int
    restrictions: list[Restriction]


class SyncPullResponse(BaseModel):
    records: list[ProfileSync]
    serverTimestamp: int


class SyncPushRequest(BaseModel):
    records: list[ProfileSync]


# --- Community catalog (BlockApiService) -----------------------------------

class ProfileSearchResult(BaseModel):
    """A catalog search hit. Matches ProfileSearchResultDto."""
    id: str
    name: str
    description: str
    appCount: int
    authorName: str


class ProfileDetail(BaseModel):
    """A catalog profile with its restrictions. Matches ProfileDetailDto."""
    id: str
    name: str
    description: str
    restrictions: list[Restriction]


class PublishProfile(BaseModel):
    """Body of POST /profiles. Matches PublishProfileDto."""
    id: str
    name: str
    description: str
    restrictions: list[Restriction]


# --- Friend groups (GroupApiService) ---------------------------------------
# The caller's identity travels in the X-User-Id / X-User-Name headers (the
# Firebase uid + display name). Demo-grade: the uid is trusted, not verified.

class GroupMember(BaseModel):
    """A member and their leaderboard value (cumulative focus seconds)."""
    uid: str
    displayName: str
    focusSeconds: int
    isOwner: bool


class SharedProfile(BaseModel):
    """A blocking profile shared into a group (a snapshot, not a live link)."""
    id: str
    name: str
    description: str
    restrictions: list[Restriction]
    sharedByUid: str
    sharedByName: str


class GroupSummary(BaseModel):
    """A group as it appears in the caller's group list."""
    id: str
    name: str
    memberCount: int


class GroupDetail(BaseModel):
    """Full group view: members ranked by focus time, plus shared profiles."""
    id: str
    name: str
    ownerUid: str
    members: list[GroupMember]        # sorted by focusSeconds desc (leaderboard)
    sharedProfiles: list[SharedProfile]


class CreateGroupRequest(BaseModel):
    name: str


class JoinGroupRequest(BaseModel):
    token: str


class InviteResponse(BaseModel):
    token: str
    link: str


class InvitePreview(BaseModel):
    """Shown before joining: which group a token points at."""
    groupId: str
    groupName: str


class ShareProfileRequest(BaseModel):
    id: str
    name: str
    description: str
    restrictions: list[Restriction]


class ReportFocusRequest(BaseModel):
    focusSeconds: int
