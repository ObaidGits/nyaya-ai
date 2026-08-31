#!/usr/bin/env bash
# Full local Docker deployment: build + start the whole Nyaya stack,
# pull the LLM into the Ollama container, and wait until healthy.
#
# Usage (from repo root):   ./scripts/docker-up.sh
# Tear down (keeps data):   docker compose --profile llm down
# Wipe data too:            docker compose --profile llm down -v
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${LLM_MODEL:-qwen2.5:3b}"

echo "==> Building + starting stack (api, worker, frontend, redis, qdrant, postgres, ollama)"
docker compose --profile llm up -d --build

echo "==> Waiting for Ollama, then pulling model ${MODEL} (first pull ~2 GB, be patient)"
for i in $(seq 1 60); do
  if docker compose exec -T ollama ollama list >/dev/null 2>&1; then break; fi
  sleep 2
done
docker compose exec -T ollama ollama pull "${MODEL}"

echo "==> Waiting for API health"
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    echo "API healthy"; break
  fi
  sleep 2
  if [[ "$i" == 60 ]]; then echo "API not healthy yet — check: docker compose logs api" >&2; exit 1; fi
done

echo "==> Waiting for frontend"
for i in $(seq 1 30); do
  if curl -fsS "http://localhost:${FRONTEND_PORT:-3000}/" >/dev/null 2>&1; then
    echo "Frontend healthy"; break
  fi
  sleep 2
done

echo
echo "Nyaya stack running:"
echo "  Frontend : http://localhost:${FRONTEND_PORT:-3000}"
echo "  API      : http://localhost:8000/api/v1/health"
echo "  Qdrant   : http://localhost:6333/dashboard  (not exposed; internal only)"
echo "  Logs     : docker compose logs -f api worker"
