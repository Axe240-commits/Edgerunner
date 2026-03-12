#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/axe240/Projects"
OVERLAY="$ROOT/.venvs/quant-cuda-overlay"
SITE="$OVERLAY/usr/lib/python3.14/site-packages"
LIBS="$OVERLAY/usr/lib"

if [[ ! -d "$SITE/torch" ]]; then
  echo "CUDA torch overlay not found at $SITE" >&2
  exit 1
fi

export PYTHONPATH="$SITE${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec python "$@"
