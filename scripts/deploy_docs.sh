#!/usr/bin/env bash
# Build the docs (regenerate + run doc tests, then `mkdocs build`) and publish ./site
# to Cloudflare Pages via wrangler direct-upload. Used both locally (git-bash / WSL /
# macOS / Linux) and by .github/workflows/docs.yml.
#
# Requirements:
#   - Python deps:  pip install -e . && pip install -r docs/requirements-docs.txt
#   - Node/npx (for wrangler; installed on demand via `npx`)
#   - Cloudflare auth in the environment:
#         export CLOUDFLARE_API_TOKEN=...      # token with "Cloudflare Pages: Edit"
#         export CLOUDFLARE_ACCOUNT_ID=...
#   - A Cloudflare Pages project (direct-upload type). Override the name with:
#         export CF_PAGES_PROJECT=oryxflow-docs
#
# Usage:
#   scripts/deploy_docs.sh                 # build + test + deploy (production)
#   scripts/deploy_docs.sh --skip-tests    # skip doc tests (faster)
#   BUILD_ONLY=1 scripts/deploy_docs.sh    # build + test, do NOT deploy
set -euo pipefail
cd "$(dirname "$0")/.."

CF_PAGES_PROJECT="${CF_PAGES_PROJECT:-oryxflow-docs}"
CF_BRANCH="${CF_BRANCH:-main}"

echo "==> Building docs"
python scripts/build_docs.py "$@"

if [[ "${BUILD_ONLY:-0}" == "1" ]]; then
  echo "==> BUILD_ONLY set — skipping Cloudflare deploy. Site is in ./site"
  exit 0
fi

echo "==> Deploying ./site to Cloudflare Pages project '$CF_PAGES_PROJECT' (branch '$CF_BRANCH')"
npx --yes wrangler@3 pages deploy site \
  --project-name "$CF_PAGES_PROJECT" \
  --branch "$CF_BRANCH" \
  --commit-dirty=true
