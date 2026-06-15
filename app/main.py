"""
BloCK backend — FastAPI.

Serves the contracts the Android client speaks:
  * Personal sync  (SyncApiService):  GET/POST /sync/profiles
  * Community catalog (BlockApiService): GET /profiles/search,
                                         GET /profiles/{id}, POST /profiles
  * Friend groups (GroupApiService): /groups, /groups/{id}/..., join/invite

Routes and JSON shapes mirror the client DTOs exactly. Interactive docs are at
/docs (handy to show during the demo). GET / is a health/warm-up ping.

Group endpoints identify the caller via X-User-Id / X-User-Name headers (the
Firebase uid + display name). This is demo-grade: the uid is trusted, not
verified — production would validate a Firebase ID token server-side.
"""

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from . import store
from .models import (
    CreateGroupRequest,
    GroupDetail,
    GroupSummary,
    InviteResponse,
    InvitePreview,
    JoinGroupRequest,
    ProfileDetail,
    ProfileSearchResult,
    PublishProfile,
    ReportFocusRequest,
    ShareProfileRequest,
    SyncPullResponse,
    SyncPushRequest,
)

# Public origin used to build invite links. Override on Render via an env var if
# the host ever changes.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://block-api.onrender.com")

# Android App Links verification: the app's package + signing-cert SHA-256.
ANDROID_PACKAGE = "com.example.block"
ANDROID_SHA256 = (
    "3C:9A:03:57:B6:A9:41:81:BB:64:EC:D2:1B:7D:4F:5F:"
    "6D:A5:3A:46:4F:35:F9:93:9C:49:F0:4E:97:B6:39:81"
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="BloCK API", version="1.0.0", lifespan=lifespan)


class Caller:
    """The identity behind a group request (Firebase uid + display name)."""

    def __init__(self, uid: str, name: str):
        self.uid = uid
        self.name = name


def caller(
    x_user_id: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
) -> Caller:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    return Caller(uid=x_user_id, name=(x_user_name or "Member"))


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


# --- Friend groups ---------------------------------------------------------

def _require_member(group_id: str, uid: str) -> None:
    if not store.is_member(group_id, uid):
        raise HTTPException(status_code=403, detail="Not a member of this group")


def _detail_or_404(group_id: str) -> GroupDetail:
    detail = store.get_group_detail(group_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return detail


@app.post("/groups", response_model=GroupDetail)
def create_group(req: CreateGroupRequest, who: Caller = Depends(caller)) -> GroupDetail:
    group_id = store.create_group(req, who.uid, who.name)
    return _detail_or_404(group_id)


@app.get("/groups", response_model=list[GroupSummary])
def list_groups(who: Caller = Depends(caller)) -> list[GroupSummary]:
    return store.list_groups(who.uid)


@app.get("/groups/{group_id}", response_model=GroupDetail)
def group_detail(group_id: str, who: Caller = Depends(caller)) -> GroupDetail:
    _require_member(group_id, who.uid)
    return _detail_or_404(group_id)


@app.post("/groups/{group_id}/invites", response_model=InviteResponse)
def create_invite(group_id: str, who: Caller = Depends(caller)) -> InviteResponse:
    _require_member(group_id, who.uid)
    _detail_or_404(group_id)
    token = store.create_invite(group_id)
    return InviteResponse(token=token, link=f"{PUBLIC_BASE_URL}/invite/{token}")


@app.get("/groups/invite/{token}", response_model=InvitePreview)
def invite_preview(token: str) -> InvitePreview:
    preview = store.preview_invite(token)
    if preview is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    return preview


@app.post("/groups/join", response_model=GroupDetail)
def join_group(req: JoinGroupRequest, who: Caller = Depends(caller)) -> GroupDetail:
    group_id = store.join_by_token(req.token, who.uid, who.name)
    if group_id is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    return _detail_or_404(group_id)


@app.post("/groups/{group_id}/profiles", response_model=GroupDetail)
def share_profile(
    group_id: str, req: ShareProfileRequest, who: Caller = Depends(caller)
) -> GroupDetail:
    _require_member(group_id, who.uid)
    store.share_profile(group_id, req, who.uid, who.name)
    return _detail_or_404(group_id)


@app.post("/groups/{group_id}/stats")
def report_focus(
    group_id: str, req: ReportFocusRequest, who: Caller = Depends(caller)
) -> dict:
    _require_member(group_id, who.uid)
    store.report_focus(group_id, who.uid, req.focusSeconds)
    return {"status": "ok"}


# --- Android App Links + invite landing ------------------------------------

@app.get("/.well-known/assetlinks.json")
def assetlinks() -> JSONResponse:
    """Digital Asset Links file so https invite URLs open the app directly."""
    return JSONResponse(
        content=[
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": ANDROID_PACKAGE,
                    "sha256_cert_fingerprints": [ANDROID_SHA256],
                },
            }
        ]
    )


@app.get("/invite/{token}", response_class=HTMLResponse)
def invite_landing(token: str) -> HTMLResponse:
    """Human-facing fallback when the App Link isn't intercepted by the app
    (app not installed, or link opened on a desktop). When the app *is* set up,
    Android opens it directly and this page is never shown."""
    preview = store.preview_invite(token)
    group_name = preview.groupName if preview else "a BloCK group"
    intent_url = (
        f"intent://invite/{token}#Intent;scheme=https;"
        f"action=android.intent.action.VIEW;package={ANDROID_PACKAGE};end"
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Join {group_name} on BloCK</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#111; color:#eee;
         display:flex; min-height:100vh; align-items:center; justify-content:center; margin:0; }}
  .card {{ max-width:360px; text-align:center; padding:32px; }}
  h1 {{ font-size:1.4rem; }} .group {{ color:#9ab; }}
  a.btn {{ display:inline-block; margin-top:24px; padding:14px 28px; border-radius:12px;
          background:#3b5bdb; color:#fff; text-decoration:none; font-weight:600; }}
  p.hint {{ color:#888; font-size:.85rem; margin-top:20px; }}
</style></head>
<body><div class="card">
  <h1>You're invited to<br><span class="group">{group_name}</span></h1>
  <a class="btn" href="{intent_url}">Open in BloCK</a>
  <p class="hint">If nothing happens, install the BloCK app and open this link again.</p>
</div></body></html>"""
    return HTMLResponse(content=html)
