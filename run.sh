#!/usr/bin/env bash
set -e
if [ ! -d venv ]; then
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
else
  source venv/bin/activate
fi
[ -f .env ] || cp .env.example .env
echo "open http://localhost:8000"
python app.py
