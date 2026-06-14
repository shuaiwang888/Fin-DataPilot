#!/usr/bin/env bash
# Deploy Fin-DataPilot backend to HuggingFace Spaces.
#
# Usage:
#   ./deploy-to-hf.sh [--message "msg"] [--dry-run]
#
# What it does:
#   1. Builds a clean working tree containing only the backend + Skills + manifests
#   2. Initializes a separate git repo in that temp dir
#   3. Pushes it to the `hf` git remote
#
# The HF Space root must contain: Dockerfile, app/, requirements.txt, start.sh
# (Skills/ is mounted at /Skills inside the container — agent reads it from there.)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
TMP="${TMPDIR:-/tmp}/findatapilot-hf-deploy"
HF_REMOTE="hf"
HF_BRANCH="${HF_BRANCH:-main}"
MESSAGE="deploy: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --message) MESSAGE="$2"; shift 2 ;;
    --remote)  HF_REMOTE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

echo "▶ Source root: $ROOT"
echo "▶ Deploy dir:  $TMP"
echo "▶ Remote:      $HF_REMOTE ($HF_BRANCH)"
echo "▶ Message:     $MESSAGE"
echo

# 1. Clean previous deploy
rm -rf "$TMP"
mkdir -p "$TMP"

# 2. Copy backend -> deploy root
cp -R "$ROOT/backend/app"       "$TMP/app"
cp    "$ROOT/backend/requirements.txt" "$TMP/requirements.txt"
cp    "$ROOT/backend/Dockerfile" "$TMP/Dockerfile"
cp    "$ROOT/backend/start.sh"   "$TMP/start.sh"
cp    "$ROOT/backend/pyproject.toml" "$TMP/pyproject.toml"
chmod +x "$TMP/start.sh"

# 3. Copy Skills alongside (the app's data dir points to ./Skills)
cp -R "$ROOT/Skills"            "$TMP/Skills"

# 4. .env.example (for local config)
cp    "$ROOT/.env.example"      "$TMP/.env.example"

# 5. README for the Space (HF requires metadata header)
cat > "$TMP/README.md" <<'EOF'
---
title: Fin-DataPilot
emoji: 📊
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: true
license: mit
short_description: Natural-language financial data agent (LangGraph + 4 iWencai skills)
---

# Fin-DataPilot Backend

Agent-based natural-language financial data platform. See root `README.md` for the full design.

- Health: `GET /api/health`
- Skills: `GET /api/skills` (and `PATCH /api/skills/{name}` to enable/disable)
- Sessions: `POST /api/sessions`, `GET /api/sessions`, `GET /api/sessions/{id}`
- Chat (SSE): `POST /api/agent/chat/stream`

Configure secrets in the Space's "Variables and secrets" tab:
- `LLM_BASE_URL` (default `https://api.minimaxi.com/v1`)
- `LLM_API_KEY`
- `IWENCAI_API_KEY`
- `CORS_ALLOW_ORIGINS` (your GitHub Pages / Vercel frontend origin)
EOF

# 6. .gitignore inside deploy dir
cat > "$TMP/.gitignore" <<'EOF'
__pycache__/
*.pyc
.env
data/
logs/
.venv/
.DS_Store
EOF

if [[ $DRY_RUN -eq 1 ]]; then
  echo "✓ Dry run — deploy tree ready at $TMP"
  echo "  Contents:"
  ls -la "$TMP" | sed 's/^/    /'
  exit 0
fi

# 7. Init git + push
cd "$TMP"
git init -q -b "$HF_BRANCH"
git config user.email "deploy@findatapilot.local"
git config user.name "Fin-DataPilot Deploy Bot"
git add -A
git commit -q -m "$MESSAGE"

# Verify the hf remote exists
if ! git remote get-url "$HF_REMOTE" >/dev/null 2>&1; then
  echo "✗ Remote '$HF_REMOTE' is not configured. Add it with:" >&2
  echo "  git remote add $HF_REMOTE git@hf.co:spaces/appQQQ/FinDataPilot" >&2
  exit 1
fi

echo "▶ Pushing to $HF_REMOTE/$HF_BRANCH ..."
git push -f "$HF_REMOTE" "$HF_BRANCH"

echo
echo "✓ Deployed. HF Space will rebuild (≈2-3 min)."
echo "  URL: https://huggingface.co/spaces/appQQQ/FinDataPilot"
