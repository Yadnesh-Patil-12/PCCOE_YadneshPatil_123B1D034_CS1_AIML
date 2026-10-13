#!/usr/bin/env bash
# One-command start for Linux / macOS:  bash run.sh
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -q -r requirements.txt
python server.py
