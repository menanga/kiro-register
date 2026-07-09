#!/bin/bash
# Kiro Register launcher — activates venv + runs main.py
# Usage: ./run.sh
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "ERROR: .venv not found. Run setup first:"
  echo "  /opt/homebrew/bin/python3.12 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  echo "  playwright install chromium"
  exit 1
fi
source .venv/bin/activate
exec python3 main.py
