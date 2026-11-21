# EzMyGo sync API

FastAPI service for account sessions and per-user, versioned planner snapshots. SQLite is used by default; `DATABASE_URL` can point SQLAlchemy at another database.

```powershell
uv sync
uv run uvicorn main:app --reload
```

The API listens on `http://localhost:8000`. Configure `CORS_ORIGINS` as a comma-separated list when the frontend is served from another origin. Set `TRAVEL_PLANNER_SECRET_KEY` to a unique, secret value before deploying; the built-in key is only for local development.

- `POST /api/auth/register` and `POST /api/auth/login` create bearer-token sessions.
- `POST /api/auth/logout` revokes the active session; `GET /api/auth/me` returns the signed-in user and latest snapshot.
- `GET /api/sync/latest` fetches the latest snapshot.
- `GET /api/sync/versions` and `GET /api/sync/versions/{version}` inspect snapshot history.
- `POST /api/sync` stores a new immutable version when `base_version` matches the current server version. Stale writes return `409 Conflict`.
- `GET /health` is a liveness check.