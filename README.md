# BloCK backend

A small FastAPI service implementing the two REST contracts the BloCK Android
client speaks. It is intentionally minimal — a demo backend, not production.
The Android app lives in a separate repository; this repo is just the backend.

| Contract | Endpoints | Client interface |
|---|---|---|
| Personal sync | `GET /sync/profiles?since=` · `POST /sync/profiles` | `SyncApiService` |
| Community catalog | `GET /profiles/search?q=` · `GET /profiles/{id}` · `POST /profiles` | `BlockApiService` |

JSON shapes mirror the client DTOs exactly (camelCase keys). Interactive docs at
`/docs`. `GET /` is a health/warm-up ping.

## Layout
```
app/
  main.py     FastAPI app + routes
  models.py   Pydantic models (== client DTOs)
  store.py    SQLite persistence + catalog seeds
requirements.txt
render.yaml   optional Render Blueprint (reference)
```

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # serves http://localhost:8000
```
Smoke test:
```bash
curl localhost:8000/                      # {"status":"ok",...}
curl "localhost:8000/profiles/search?q="  # 4 seeded community profiles
```

## Deploy to Render (dashboard — recommended)
1. Render → **New** → **Web Service** → connect this repo.
2. Settings (the server is at the repo root, so no Root Directory override):
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** Free
3. Create. When it's live, copy the URL (e.g. `https://block-api-xxxx.onrender.com`).
4. **Give that URL to whoever wires the app** — it becomes `BASE_URL` in the
   client's `ApiModule` (the two Hilt bindings are already pointed at the real API).

## Demo runbook
1. **Warm up** (free instances sleep when idle, ~30–60 s cold start):
   `curl https://<your-url>/` once, a minute before presenting.
2. **Personal sync round-trip:** create/edit a profile in the app → it pushes
   (or tap *Sync now* in Settings). Reinstall the app or use a second device →
   *Sync now* → the profile reappears (pulled from the server).
3. **Catalog:** open *Descubrir* → the 4 seeded profiles load from the server;
   download one. Publish one of your profiles → it appears in search.

## Known limits (talking points, not bugs)
- **Single-tenant sync:** the server stores profiles globally; it does not scope
  by account. True multi-account sync would add a Firebase ID-token auth header
  (`SyncApiService`) verified server-side. Out of scope for the demo.
- **Persistence:** SQLite on Render's free tier lives on ephemeral disk — data
  survives while the instance is up, but a cold redeploy starts fresh. Fine for a
  live demo; attach a persistent disk (paid) or a managed Postgres for permanence.
