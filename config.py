"""Central settings for Interview Arena.

Every other module imports settings from here, so there is ONE place
that reads the .env file. API key VALUES are never stored as module
variables or printed — code asks `has_key()` / `get_key()` only when needed.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project folder (not the current working directory),
# so scripts work no matter where they are started from.
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# --- Models (names are not secret, so plain variables are fine) ---
MAIN_MODEL: str = os.getenv("MAIN_MODEL", "gemini-3.5-flash-lite")
BACKUP_MODEL_1: str = os.getenv("BACKUP_MODEL_1", "openai/gpt-oss-120b")
# Optional 2nd Groq model (own free quota). Leave empty in .env to switch it off.
BACKUP_MODEL_1B: str = os.getenv("BACKUP_MODEL_1B", "qwen/qwen3.8-27b")
BACKUP_MODEL_2: str = os.getenv("BACKUP_MODEL_2", "")

# --- App mode: "local" allows Gmail drafts, "cloud" is PDF-only ---
APP_MODE: str = os.getenv("APP_MODE", "local").strip().lower()
if APP_MODE not in ("local", "cloud"):
    APP_MODE = "local"

# --- Input limits (characters), from the PRD ---
MAX_RESUME_CHARS: int = 12_000
MAX_JD_CHARS: int = 8_000
MAX_ANSWER_CHARS: int = 2_000
MIN_PDF_TEXT_CHARS: int = 200  # below this, ask the user to paste text

# --- Requests per minute we allow ourselves, per provider (shared by the whole process). ---
# Google counts Gemini limits per Google Cloud PROJECT (not per key); one process-wide
# limiter is right for one project used by one running app.
# Gemini's free tier is 15 RPM, but Google says limits are not guaranteed, so we use 12
# as a safety margin. Groq 30, OpenRouter 20 (CLAUDE.md §3).
RPM_LIMITS: dict[str, int] = {"gemini": 12, "groq": 30, "groq_b": 30, "openrouter": 20}

# --- Evaluator lock ---
# Gemini 3.5 Flash-Lite ignores temperature, so scores could vary run to run.
# Default "gemini"; if the Phase 9 consistency test fails (> ±1), set
# EVALUATOR_PROVIDER=groq in .env (gpt-oss-120b honours temperature 0).
EVALUATOR_PROVIDER: str = os.getenv("EVALUATOR_PROVIDER", "gemini").strip().lower()
if EVALUATOR_PROVIDER not in ("gemini", "groq"):
    raise ValueError("EVALUATOR_PROVIDER in .env must be 'gemini' or 'groq'.")

# --- RAG (Phase 3) ---
EMBED_MODEL: str = "BAAI/bge-small-en-v1.5"  # FastEmbed, ONNX on CPU, 384 dims, ~65 MB
EMBED_CACHE_DIR: Path = PROJECT_ROOT / ".fastembed_cache"  # git-ignored; downloaded once
CHUNK_SIZE: int = 500  # characters
CHUNK_OVERLAP: int = 50
RUBRIC_PATH: Path = PROJECT_ROOT / "data" / "rubric.md"

# --- Provider endpoints (OpenAI-compatible APIs) ---
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

# --- Reliability: timeouts and retries ---
REQUEST_TIMEOUT_SECONDS: int = 60  # one LLM request (router and CrewAI)
MAX_RETRIES_PER_PROVIDER: int = 2  # 1 first try + 2 retries on 429/5xx, then the next provider
BACKOFF_BASE_SECONDS: float = 2.0  # retry waits ~2 s, then ~4 s
MCP_CALL_TIMEOUT_SECONDS: int = 360  # first Gmail call may wait for the browser OAuth consent

# --- Interview rules ---
MIN_ANSWER_WORDS: int = 3  # fewer words = "no answer" (score 0, no LLM call)
MIN_GAP_QUESTIONS: int = 3  # technical questions that must target a skill gap (PRD FR-4.2)
GAP_EVIDENCE_PER_SKILL: int = 2  # resume chunks shown to the Gap Analyst per JD skill
EVALUATOR_EVIDENCE_CHUNKS: int = 2  # JD/resume chunks given to the Evaluator per answer
SAMPLE_ANSWER_BELOW: float = 6.0  # report: improved sample answer for scores below this (FR-7.1)
REPORT_ANSWER_CHARS: int = 600  # how much of each answer the Report Writer sees

# --- Files ---
MAX_PDF_BYTES: int = 2 * 1024 * 1024  # resume PDF up to 2 MB (PRD FR-1.1)

# --- Web app sessions ---
SESSION_TTL_SECONDS: int = 3 * 60 * 60  # forget idle sessions after 3 hours
MAX_WAIT_PER_RUN_SECONDS: int = 30  # spinner wait per page run, then redraw and keep waiting
GRAPH_WORKER_THREADS: int = 8  # background graph steps running at the same time (all sessions)

# --- LLM call budget per session (decision 21 Nov 2026) ---
# 18 is the TARGET (normal session). 24 is the HARD CAP: headroom so retries and
# fallbacks never stop a session midway. Going over 18 is logged and reported.
LLM_CALL_TARGET: int = 18
LLM_CALL_HARD_CAP: int = 24

# Only these names may be read as secrets.
KEY_NAMES = ("GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")


def has_key(name: str) -> bool:
    """True if the key exists and is not empty. Never reveals the value."""
    return bool(os.getenv(name, "").strip())


def get_key(name: str) -> str:
    """Return a key value for passing straight to an SDK client.

    Only for use inside llm/router.py and crew setup — never print or log it.
    """
    if name not in KEY_NAMES:
        raise ValueError(f"Unknown key name: {name}")
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing. Add it to your .env file.")
    return value
