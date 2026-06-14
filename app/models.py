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
