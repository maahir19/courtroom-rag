@echo off
if not exist venv (
  python -m venv venv
  call venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call venv\Scripts\activate
)
if not exist .env copy .env.example .env
start http://localhost:8000
python app.py
