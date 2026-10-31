#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1

CONFIG_ONLY=0
DEV=0
for arg in "$@"; do
    case "$arg" in
        --config) CONFIG_ONLY=1 ;;
        --dev) DEV=1 ;;
    esac
done

copy_configs() {
    echo "Copying configuration templates..."
    for pair in ".env.dist|.env" "config.dist.toml|config.toml" "tasks.dist.json|tasks.json" "manifests/unlock-manifest.dist.json|manifests/unlock-manifest.json" "manifests/idle-manifest.dist.json|manifests/idle-manifest.json"; do
        src=${pair%%|*}
        dst=${pair#*|}
        if [ -f "$src" ] && [ ! -e "$dst" ]; then
            cp "$src" "$dst"
            echo "[+] Created $dst from template."
        fi
    done
}

if [ "$CONFIG_ONLY" = "1" ]; then
    copy_configs
    echo ""
    echo "[OK] Setup complete."
    echo "Next: review the generated config files, then launch with run.sh (see README)."
    exit 0
fi

[ -z "$PYTHON" ] && [ -f .python ] && PYTHON=$(cat .python)
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" --version >/dev/null 2>&1; then
    echo "[ERROR] Python not found (set PYTHON or add python3 to PATH)."
    exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)' >/dev/null 2>&1; then
    echo "[ERROR] Python 3.11-3.14 is required."
    exit 1
fi

echo "Creating virtual environment..."
if [ ! -x "env/bin/python" ]; then
    "$PYTHON" -m venv env || { echo "[ERROR] Failed to create the virtual environment."; exit 1; }
fi

echo "Installing dependencies..."
if [ "$DEV" = "1" ]; then
    env/bin/pip install -r requirements-dev.txt || { echo "[ERROR] Failed to install dependencies."; exit 1; }
else
    env/bin/pip install -r requirements.txt || { echo "[ERROR] Failed to install dependencies."; exit 1; }
fi

echo "Registering package in editable mode..."
env/bin/pip install -e . || { echo "[ERROR] Failed to register package."; exit 1; }

copy_configs
echo ""
echo "[OK] Setup complete."
echo "Next: review the generated config files, then launch with run.sh (see README)."
