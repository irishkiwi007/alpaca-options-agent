#!/bin/bash
# Regenerates logs/reasoning_export.json from the local events.jsonl and
# pushes it to GitHub so the hosted Streamlit dashboard can read it.
#
# Intended to run on a cron schedule on the VM (e.g. every 15 minutes).
# Idempotent and safe to run repeatedly: does nothing if the export is
# unchanged since last run.
#
# One-time setup this script assumes is already done:
#   1. git remote set to a URL with PUSH access (a fine-grained PAT).
#   2. git config user.email / user.name set for this user.
#
# This script never touches the trading process itself — it only reads
# the log file and pushes a derived artifact. It cannot affect what the
# agent does next.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

python3 scripts/export_reasoning.py

# Empty output = file unchanged (or identical to what's tracked) —
# nothing to commit. Non-empty = new or modified file.
if [ -z "$(git status --porcelain -- logs/reasoning_export.json)" ]; then
    exit 0
fi

git add logs/reasoning_export.json
git commit -m "Sync trade reasoning export [automated]" --quiet
git push origin main --quiet

echo "$(date -u +%FT%TZ) reasoning_export.json synced"
