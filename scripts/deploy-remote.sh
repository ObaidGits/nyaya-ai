#!/usr/bin/env bash
# Remote half of the CI deploy job (.github/workflows/ci.yml).
# Executed on the production VM over SSH ("ssh ... 'bash -s' < this file")
# with these variables set on the remote command line:
#   GHCR_PULL_TOKEN  PAT with read:packages (and contents read for git fetch)
#   GHCR_OWNER       repository owner (docker login username)
#   GITHUB_REPO      owner/repo, used for the git fetch URL
#   BACKEND_IMAGE    full GHCR ref for this commit's backend image
#   FRONTEND_IMAGE   full GHCR ref for this commit's frontend image
set -euo pipefail

cd ~/nyaya-ai

# Sync the deployment repo (compose file, monitoring config) to this commit.
git fetch "https://x-access-token:${GHCR_PULL_TOKEN}@github.com/${GITHUB_REPO}.git" main
git reset --hard FETCH_HEAD
git config remote.origin.url "https://github.com/${GITHUB_REPO}.git"

# Pull this commit's images from GHCR and roll them out.
# Lifecycle is Compose-native and idempotent: `up -d` recreates ONLY services
# whose image or configuration changed and reuses everything else;
# --remove-orphans drops containers this project no longer declares.
# --wait gates the deploy on healthchecks, so a rollout whose API never
# turns healthy FAILS here instead of silently leaving a broken service.
echo "$GHCR_PULL_TOKEN" | docker login ghcr.io -u "$GHCR_OWNER" --password-stdin >/dev/null
BACKEND_IMAGE="$BACKEND_IMAGE" FRONTEND_IMAGE="$FRONTEND_IMAGE" \
  docker compose pull api worker frontend
BACKEND_IMAGE="$BACKEND_IMAGE" FRONTEND_IMAGE="$FRONTEND_IMAGE" \
  docker compose up -d --remove-orphans --wait --wait-timeout 300 api worker frontend

# Stray-stack audit (non-destructive): --remove-orphans only covers THIS
# compose project. A Nyaya stack started from a DIFFERENT checkout gets a
# different project name and would never be cleaned automatically — exactly
# how duplicate containers appear. Surface such containers loudly; a human
# decides what to do (never auto-delete, they may hold data).
strays="$(docker ps -a --format '{{.Names}}\t{{.Image}}' \
  | grep -i nyaya | grep -v '^nyaya-ai-' || true)"
if [ -n "$strays" ]; then
  echo "WARNING: Nyaya-named containers OUTSIDE this compose project found:" >&2
  echo "$strays" >&2
  echo "These belong to another checkout/project and are never touched by this deploy." >&2
fi

# Small disk (29 GB): every rollout leaves the previous SHA-tagged images
# (backend ~6 GB) behind and the disk fills within a few deploys. Drop
# dangling layers and any nyaya-ai registry image no container references.
docker image prune -f >/dev/null 2>&1 || true
docker images --format '{{.Repository}}:{{.Tag}}' \
  | grep '^ghcr.io/obaidgits/nyaya-ai/' \
  | while read -r image; do
      if [ -z "$(docker ps --format '{{.Image}}' | grep -F "$image")" ]; then
        docker rmi "$image" >/dev/null 2>&1 || true
      fi
    done
