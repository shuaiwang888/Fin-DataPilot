# Fin-DataPilot Backend

FastAPI + LangGraph agent for the Fin-DataPilot platform.

## Quick start (local dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env       # fill in LLM_API_KEY and IWENCAI_API_KEY
./start.sh                 # http://localhost:7860
```

Tests: `python -m pytest tests/ -q`

## Deployment to HuggingFace Spaces

This backend is deployed to a HF Space via `.github/workflows/deploy-backend-hf.yml`.

### ⚠️ Required: enable Persistent Storage

The Space's `findatapilot.db` lives in `/data/findatapilot.db`. That path is only
persistent across rebuilds **if the Space has Persistent Storage enabled**.

Without it, every Space rebuild / restart wipes the database and you lose
all chat history.

**How to enable:**

1. Open the Space page on huggingface.co (e.g. `huggingface.co/spaces/<owner>/<space>`).
2. Click the **Settings** tab (gear icon, top right).
3. Scroll to the **Persistent Storage** section.
4. Toggle **Enable Persistent Storage** → ON.
5. Optionally change the storage tier (paid Spaces get more space).
6. Click **Save**.

> On free-tier CPU Spaces, Persistent Storage is available with 50 GB.
> On paid hardware, up to 200 GB.

### Verify it's working

After enabling, restart the Space and check:

```bash
curl https://<owner>-<space>.hf.space/api/diag
```

Expected response:

```json
{
  "is_hf_space": true,
  "space_id": "owner/space",
  "turso_configured": false,
  "db_path": "/data/findatapilot.db",
  "db_exists": true,
  "db_size_bytes": 32768,
  "data_dir_is_separate_mount": true,
  "data_dir_mount_info": "device=/dev/sdb fs=ext4 opts=rw",
  "database_url": "sqlite+aiosqlite:////data/findatapilot.db"
}
```

**`data_dir_is_separate_mount: true`** is the key field — it means
`/data` is its own filesystem (a persistent volume) instead of a
directory on the root FS (which would be wiped on rebuild).

If you see `data_dir_is_separate_mount: false`, Persistent Storage is
**not** enabled — go back to step 1.

### Startup logs

The backend logs its DB location on startup. Look for one of these:

- `DB backend: SQLite at /data/findatapilot.db (HF Space, persistent=True)` ✅
- `DB backend: SQLite at /data/findatapilot.db (HF Space, persistent=False)` ⚠️
  Persistent storage NOT enabled; will lose data on restart.
- `DB backend: SQLite at /data/...` then `RuntimeError: /data is not writable` ❌
  Persistent storage path exists but is read-only — check Space tier.

### Switching to Turso (libSQL) — optional, for multi-instance setups

If you ever outgrow a single Space (e.g. running multiple replicas), set:

```bash
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=...
```

The backend will use the remote Turso database instead of `/data`.
No code changes needed.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness + LLM / iWencai key config status |
| GET | `/api/diag` | DB location, persistence mount check, file size |
| GET | `/api/skills` | List registered Skills |
| PATCH | `/api/skills/{name}` | Enable/disable a Skill |
| GET | `/api/sessions` | List chat sessions (cap 50/user) |
| POST | `/api/sessions` | Create a new session |
| DELETE | `/api/sessions/{id}` | Delete a session |
| DELETE | `/api/sessions` | Delete **all** sessions (clear history) |
| GET | `/api/sessions/{id}/messages` | List messages in a session |
| POST | `/api/agent/chat/stream` | Stream an agent run (SSE) |
| POST | `/api/agent/chat/stop` | Abort an in-flight run |

## Project layout

```
backend/
├── app/
│   ├── main.py                # FastAPI app + lifespan
│   ├── config.py              # pydantic-settings + /data resolution
│   ├── db_init.py             # startup: create tables + log DB location
│   ├── agent/                 # LangGraph state machine
│   │   ├── graph.py
│   │   ├── state.py
│   │   └── nodes/             # planner / skill_router / executor / reflector / synthesizer
│   ├── api/                   # FastAPI routers
│   │   ├── agent.py
│   │   ├── health.py          # /api/health, /api/diag, /api/echo
│   │   ├── sessions.py
│   │   └── skills.py
│   ├── skills/                # ToolSpec / ToolRegistry + 4 handlers
│   ├── storage/               # SQLAlchemy 2.0 async
│   │   ├── db.py
│   │   ├── models.py
│   │   └── repository.py
│   ├── llm/                   # OpenAI-compatible LLM factory
│   └── utils/
├── tests/
│   ├── unit/                  # pytest, 24 tests
│   └── eval/                  # agent eval cases
├── Dockerfile
├── start.sh
├── requirements.txt
└── requirements-dev.txt
```
