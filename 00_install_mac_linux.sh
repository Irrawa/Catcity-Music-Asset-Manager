#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=========================================="
echo " Catcity Music Asset Manager - Install"
echo "=========================================="
echo

if [[ -x ".venv/bin/python" ]]; then
  echo "Found existing .venv, skipping venv creation."
else
  echo "Creating venv: .venv"
  python3 -m venv .venv
fi

echo
echo "Installing requirements..."
".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt

echo
echo "Done. Next: ./01_start_mac_linux.sh"
