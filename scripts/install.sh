#!/usr/bin/env bash
# Install pqr (Parquet & JSONL viewer/editor)
#
# Works on any system with Python 3.9+ (Debian/Ubuntu, macOS, Arch, ...):
#   - If conda is available, installs into the conda env "ml", creating
#     it if missing (a broken env with no python and no bin/lib dirs is
#     removed and recreated; override the name with --env <name>)
#   - Otherwise, creates a dedicated venv in ~/.local/share/pqr and
#     links the pqr command into ~/.local/bin
#   - --base installs into the system python3 on PATH instead
#
# Usage: bash scripts/install.sh [--env <conda-env>] [--all] [--base] [--venv]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DEFAULT_ENV="ml"
ENV_NAME="$DEFAULT_ENV"
EXTRA=""
EXPLICIT_ENV=false
USE_BASE=false
USE_VENV=false

info() { printf '%s\n' "$*"; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Install pqr (Parquet & JSONL viewer/editor)

Usage: install.sh [options]

  --env <name>   Install into conda environment <name> (created if missing)
  --base         Install into the python3 on PATH (system python)
  --venv         Install into a dedicated venv under ~/.local/share/pqr
  --all          Also install optional deps (duckdb, openpyxl, zstandard)
  -h, --help     Show this help

With no flags: uses conda env "ml" when conda is available
(created automatically if missing or broken), otherwise falls back
to a dedicated venv.
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --env)   EXPLICIT_ENV=true; ENV_NAME="${2:?--env requires a value}"; shift 2;;
        --all)   EXTRA="all"; shift;;
        --base)  USE_BASE=true; shift;;
        --venv)  USE_VENV=true; shift;;
        -h|--help) usage; exit 0;;
        *)       echo "Unknown option: $1" >&2; echo >&2; usage >&2; exit 1;;
    esac
done

# --- interpreter discovery ---------------------------------------------------

PYTHON_BIN="$(command -v python3 || true)"
[[ -n "$PYTHON_BIN" ]] || die "python3 not found on PATH.
  Install Python 3.9+ first:
    Debian/Ubuntu: sudo apt install python3
    macOS:         brew install python"

check_python_version() {
    local py="$1"
    if ! "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
        die "$py is older than 3.9, but pqr requires Python 3.9+."
    fi
}

# conda may only exist as a shell function in interactive shells; source it if needed
conda_available() {
    command -v conda >/dev/null 2>&1 && return 0
    local base
    for base in "$HOME/miniconda3" "$HOME/miniforge3" "$HOME/mambaforge" \
                "$HOME/anaconda3" "$HOME/anaconda" /opt/conda /usr/local/miniconda; do
        if [[ -f "$base/etc/profile.d/conda.sh" ]]; then
            # shellcheck disable=SC1091
            source "$base/etc/profile.d/conda.sh"
            return 0
        fi
    done
    return 1
}

# An env counts as usable only if it actually contains a python interpreter
# (an env can appear in `conda env list` yet be empty/broken)
conda_env_usable() {
    conda run -n "$ENV_NAME" python --version >/dev/null 2>&1
}

# On-disk path of the env, or empty if it does not exist
conda_env_path() {
    conda env list 2>/dev/null | awk -v n="$ENV_NAME" '
        $1==n { for (i=2; i<=NF; i++) if ($i != "*") { print $i; exit } }'
}

# Make sure a usable conda env named $ENV_NAME exists: reuse it, remove and
# recreate it if it is broken but empty, or create it from scratch.
ensure_conda_env() {
    if conda_env_usable; then
        return 0
    fi
    local path
    path="$(conda_env_path)"
    if [[ -n "$path" ]]; then
        if [[ -d "$path/bin" || -d "$path/lib" ]]; then
            die "conda environment '$ENV_NAME' exists but has no usable python,
  and does not look empty, so it will not be removed automatically.
  Repair it, or remove it and re-run: conda env remove -n $ENV_NAME"
        fi
        info "conda environment '$ENV_NAME' is broken (no python) - removing it."
        conda env remove -n "$ENV_NAME" -y \
            || die "could not remove the broken conda environment '$ENV_NAME'."
    fi
    info "Creating conda environment '$ENV_NAME' (python 3.11) ..."
    conda create -n "$ENV_NAME" python=3.11 -y \
        || die "conda create failed for environment '$ENV_NAME'."
}

# --- choose an install strategy ----------------------------------------------
#   base  : system python3 on PATH
#   venv  : dedicated venv under ~/.local/share/pqr
#   conda : conda env named $ENV_NAME
MODE=""
VENV_DIR=""
PIP_CMD=()
PIP_LABEL=""

setup_venv() {
    check_python_version "$PYTHON_BIN"
    VENV_DIR="${PQR_VENV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/pqr}"
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        info "Creating a virtual environment in $VENV_DIR ..."
        "$PYTHON_BIN" -m venv "$VENV_DIR" || die "could not create a venv with '$PYTHON_BIN' -m venv.
  Debian/Ubuntu users: sudo apt install python3-venv, then re-run."
        "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip \
            || info "Warning: could not upgrade pip in the venv (offline?); continuing with the bundled pip."
    fi
    PIP_CMD=("$VENV_DIR/bin/python" -m pip)
    PIP_LABEL="venv ($VENV_DIR)"
}

if [[ "$USE_BASE" == true ]]; then
    MODE="base"
elif [[ "$USE_VENV" == true ]]; then
    MODE="venv"
elif conda_available; then
    MODE="conda"
    ensure_conda_env
else
    if [[ "$EXPLICIT_ENV" == true ]]; then
        die "--env was given but conda is not available (use --venv or --base instead)."
    fi
    MODE="venv"
    info "conda not found - using a dedicated venv."
fi

case "$MODE" in
    base)
        check_python_version "$PYTHON_BIN"
        "$PYTHON_BIN" -m pip --version >/dev/null 2>&1 \
            || die "pip is not available for $PYTHON_BIN.
  Debian/Ubuntu users: sudo apt install python3-pip, then re-run."
        # PEP 668: some distros forbid pip installs into the system python
        if [[ -f "$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')/EXTERNALLY-MANAGED" ]]; then
            info "Note: this system python is externally managed (PEP 668); 'pip install' may be refused."
            info "      If it fails, re-run without --base (a dedicated venv will be used)."
        fi
        PIP_CMD=("$PYTHON_BIN" -m pip)
        PIP_LABEL="system python ($PYTHON_BIN)"
        ;;
    venv)
        setup_venv
        ;;
    conda)
        # PYTHONNOUSERSITE=1 keeps a shared ~/.local (user site) from leaking
        # into the env, so pip installs the deps into the env itself and the
        # env stays self-contained
        PIP_CMD=(env PYTHONNOUSERSITE=1 conda run -n "$ENV_NAME" python -m pip)
        PIP_LABEL="conda env '$ENV_NAME'"
        ;;
esac

# --- install -------------------------------------------------------------------

info "Installing core deps using: $PIP_LABEL"
"${PIP_CMD[@]}" install pandas pyarrow textual rich

if [[ -n "$EXTRA" ]]; then
    info "Installing optional deps..."
    "${PIP_CMD[@]}" install duckdb openpyxl zstandard
fi

info "Installing pqr (editable)..."
"${PIP_CMD[@]}" install -e "$PROJECT_DIR"

# --- done ----------------------------------------------------------------------

link_pqr() {
    local dir="$HOME/.local/bin"
    mkdir -p "$dir" 2>/dev/null || return 1
    ln -sf "$VENV_DIR/bin/pqr" "$dir/pqr"
}

echo ""
echo "Done!"
if [[ "$MODE" == "venv" ]]; then
    echo "Run it with: $VENV_DIR/bin/pqr"
    if link_pqr; then
        echo "Linked pqr -> ~/.local/bin/pqr"
        case ":$PATH:" in
            *":$HOME/.local/bin:"*) ;;
            *) echo "Note: add ~/.local/bin to your PATH so 'pqr' is found directly." ;;
        esac
    else
        echo "Tip: add $VENV_DIR/bin to your PATH to use 'pqr' directly."
    fi
elif [[ "$MODE" == "conda" ]]; then
    echo "Run 'conda activate $ENV_NAME' and then 'pqr' to start."
else
    echo "Run 'pqr' to start."
fi
