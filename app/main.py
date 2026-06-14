"""
BloCK backend — FastAPI.

Serves the two contracts the Android client already speaks:
  * Personal sync  (SyncApiService):  GET/POST /sync/profiles
  * Community catalog (BlockApiService): GET /profiles/search,
                                         GET /profiles/{id}, POST /profiles

Routes and JSON shapes mirror the client DTOs exactly. Interactive docs are at
/docs (handy to show during the demo). GET / is a health/warm-up ping.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from . import store
from .models import (
    ProfileDetail,
    ProfileSearchResult,
    PublishProfile,
    SyncPullResponse,
    SyncPushRequest,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="BloCK API", version="1.0.0", lifespan=lifespan)


@app.get("/")
def health() -> dict:
    """Health/warm-up endpoint — hit before a demo to wake a cold instance."""
    return {"status": "ok", "service": "block-api"}


# --- Personal sync ---------------------------------------------------------

@app.get("/sync/profiles", response_model=SyncPullResponse)
def pull_profiles(since: int = Query(0)) -> SyncPullResponse:
    records, server_timestamp = store.pull_sync_profiles(since)
    return SyncPullResponse(records=records, serverTimestamp=server_timestamp)


@app.post("/sync/profiles")
def push_profiles(body: SyncPushRequest) -> dict:
    store.upsert_sync_profiles(body.records)
    return {"status": "ok", "received": len(body.records)}


# --- Community catalog -----------------------------------------------------

@app.get("/profiles/search", response_model=list[ProfileSearchResult])
def search_profiles(q: str = Query("")) -> list[ProfileSearchResult]:
    return store.search_catalog(q)


@app.get("/profiles/{profile_id}", response_model=ProfileDetail)
def get_profile(profile_id: str) -> ProfileDetail:
    profile = store.get_catalog_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@app.post("/profiles")
def publish_profile(dto: PublishProfile) -> dict:
    store.publish_catalog_profile(dto)
    return {"status": "ok", "id": dto.id}
