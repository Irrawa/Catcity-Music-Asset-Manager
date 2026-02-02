#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=========================================="
echo " Catcity Music Asset Manager - Start"
echo "=========================================="
echo

if [[ -x ".venv/bin/python" ]]; then
  ".venv/bin/python" start_app.py
else
  echo "[WARN] .venv not found. Trying system python3."
  echo "       If this fails, run ./00_install_mac_linux.sh first."
  python3 start_app.py
fi
