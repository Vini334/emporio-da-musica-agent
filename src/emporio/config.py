"""Paths and environment settings.

Environment variables are read at call time (not import time) so tests can override them.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
STORAGE_DIR = ROOT_DIR / "storage"
DOCS_DIR = ROOT_DIR / "docs"

POLICIES_PDF = RAW_DIR / "politicas_da_loja.pdf"
CHROMA_DIR = STORAGE_DIR / "chroma"
EMBEDDING_CACHE_DIR = STORAGE_DIR / "models"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
HANDOFFS_FILE = STORAGE_DIR / "handoffs.jsonl"
MEMORY_DB = STORAGE_DIR / "memory.sqlite"

TIMEZONE = "America/Campo_Grande"
DEFAULT_LLM_MODEL = "google_genai:gemini-flash-latest"

load_dotenv(ROOT_DIR / ".env")


def llm_model() -> str:
    return os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL


def now_override() -> str | None:
    """Raw value of EMPORIO_NOW (ISO 8601), or None when the real clock should be used."""
    value = os.getenv("EMPORIO_NOW", "").strip()
    return value or None
