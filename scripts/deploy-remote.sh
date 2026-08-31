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
echo "$GHCR_PULL_TOKEN" | docker login ghcr.io -u "$GHCR_OWNER" --password-stdin >/dev/null
BACKEND_IMAGE="$BACKEND_IMAGE" FRONTEND_IMAGE="$FRONTEND_IMAGE" \
  docker compose pull api worker frontend
BACKEND_IMAGE="$BACKEND_IMAGE" FRONTEND_IMAGE="$FRONTEND_IMAGE" \
  docker compose up -d --remove-orphans api worker frontend
