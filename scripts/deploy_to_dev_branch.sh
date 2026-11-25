#!/usr/bin/env bash
set -euo pipefail

# Safe deployment helper for azarya78/ai-jobs-aggregator
# Creates dedicated branch 'feat/v2-direct-discovery' without touching 'main' production feed.

REPO_URL="${1:-git@github.com:azarya78/ai-jobs-aggregator.git}"
BRANCH="feat/v2-direct-discovery"
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"

echo "[1/5] Cloning $REPO_URL..."
git clone "$REPO_URL" "$WORK_DIR/repo"
cd "$WORK_DIR/repo"

echo "[2/5] Creating/switching to development branch: $BRANCH (preserving main/jobs_feed.json)..."
git checkout -B "$BRANCH"

echo "[3/5] Copying AI Jobs Hub v2 backend modules, tests, and staging workflow..."
cp -R "$BUNDLE_DIR/config" "$BUNDLE_DIR/src" "$BUNDLE_DIR/tests" "$BUNDLE_DIR/scripts" "$WORK_DIR/repo/"
mkdir -p "$WORK_DIR/repo/.github/workflows" "$WORK_DIR/repo/state" "$WORK_DIR/repo/staging"
cp "$BUNDLE_DIR/collect_jobs.py" "$BUNDLE_DIR/requirements.txt" "$WORK_DIR/repo/"
cp "$BUNDLE_DIR/.github/workflows/collect_v2_staging.yml" "$WORK_DIR/repo/.github/workflows/"

echo "[4/5] Running local pytest verification..."
PYTHONPATH=src pytest tests/ -v

echo "[5/5] Committing to $BRANCH (not main)..."
git add config src tests scripts collect_jobs.py requirements.txt .github/workflows/collect_v2_staging.yml
git commit -m "feat(v2): implement multi-source direct AI evaluation job discovery pipeline"
git push -u origin "$BRANCH"

echo "Done! Open a Pull Request from '$BRANCH' -> 'main' after reviewing staging workflow runs."
