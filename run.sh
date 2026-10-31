#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1

[ -z "$PYTHON" ] && [ -f .python ] && PYTHON=$(cat .python)
PYTHON="${PYTHON:-python3}"

export MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV=1

exec "$PYTHON" launch.py "$@"
