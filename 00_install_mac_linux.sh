#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Use Tsinghua PyPI mirror by default (works well behind GFW)
export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
export PIP_TRUSTED_HOST="pypi.tuna.tsinghua.edu.cn"

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
