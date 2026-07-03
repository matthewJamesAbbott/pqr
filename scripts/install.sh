#!/usr/bin/env bash
# Install pqr (Parquet & JSONL viewer/editor)
# Usage: bash scripts/install.sh [--env <conda-env>] [--all] [--base]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV_NAME="ml"
USE_BASE=false
EXTRA=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --env)   ENV_NAME="$2"; shift 2;;
        --all)   EXTRA="all"; shift;;
        --base)  USE_BASE=true; shift;;
        *)       shift;;
    esac
done

# Define the PIP command strategy
if [ "$USE_BASE" = true ]; then
    # Use the base python/pip available in the current shell
    PIP_CMD="python3 -m pip"
else
    # Use conda to run pip inside the target environment
    # This avoids hardcoding paths entirely
    PIP_CMD="conda run -n $ENV_NAME python -m pip"
fi

echo "Installing core deps using: $PIP_CMD"
$PIP_CMD install pandas pyarrow textual rich

if [ -n "$EXTRA" ]; then
    echo "Installing optional deps..."
    $PIP_CMD install duckdb openpyxl zstandard
fi

echo "Installing pqr (editable)..."
$PIP_CMD install -e "$PROJECT_DIR"

echo ""
echo "Done! Run 'pqr' to start."
