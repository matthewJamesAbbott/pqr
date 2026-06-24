#!/usr/bin/env bash
# Install pqr (Parquet & JSONL viewer/editor)
# Usage: bash scripts/install.sh [--env <conda-env>]
#
#   --env ml    Install into named conda env (default: ml)
#   --all       Also install optional deps (duckdb, openpyxl, zstandard)
#   --base      Install into base Python env instead of conda env

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV_NAME="${1:-}"
ENV=""
EXTRA=""
BASE=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --env)   ENV_NAME="$2"; shift 2;;
        --all)   EXTRA="all"; shift;;
        --base)  BASE=1; shift;;
        *)       shift;;
    esac
done

if [ "$BASE" -eq 1 ]; then
    PIP="pip"
else
    CONDA_ENV="${ENV_NAME:-ml}"
    PIP="/home/matt/miniconda3/envs/$CONDA_ENV/bin/pip"
    if [ ! -x "$PIP" ]; then
        echo "Error: pip not found at $PIP"
        echo "Create the conda env first: conda create -n $CONDA_ENV python=3.11"
        exit 1
    fi
fi

echo "Installing core deps (pandas, pyarrow, textual, rich)..."
$PIP install pandas pyarrow textual rich

if [ -n "$EXTRA" ]; then
    echo "Installing optional deps (duckdb, openpyxl, zstandard)..."
    $PIP install duckdb openpyxl zstandard
fi

echo "Installing pqr (editable)..."
$PIP install -e "$PROJECT_DIR"

echo ""
echo "Done! Run 'pqr' to start."
