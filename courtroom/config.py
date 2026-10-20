"""All the knobs in one place. Values come from .env so I don't commit API keys by mistake (again)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"

# --- LLM ---------------------------------------------------------------
# provider: groq | openai | ollama | offline
# "offline" = no LLM at all, the agents fall back to rule-based arguing.
# Useful for demos when the college wifi dies.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "offline").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

DEFAULTS = {
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),  # llama-3.3-70b got retired aug 2026
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
}

# --- Retrieval ---------------------------------------------------------
CHUNK_WORDS = int(os.getenv("CHUNK_WORDS", "110"))
TOP_K = int(os.getenv("TOP_K", "6"))          # evidence each lawyer pulls
MAX_PER_DOC = int(os.getenv("MAX_PER_DOC", "3"))  # so one doc can't hog the whole case
BM25_WEIGHT = float(os.getenv("BM25_WEIGHT", "0.55"))  # rest goes to tf-idf cosine

# small pause between stages in offline mode, otherwise everything appears instantly
# and the UI animation looks broken lol
OFFLINE_STAGE_DELAY = float(os.getenv("OFFLINE_STAGE_DELAY", "0.6"))
