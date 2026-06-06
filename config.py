"""
Finley — central configuration.

All configuration is loaded from environment variables (via a local `.env` file
in development). Critical values raise a clear ``ValueError`` at import time if
they are missing, rather than surfacing a cryptic ``KeyError`` deep inside a
request. Optional values fall back to sensible defaults and log a warning.

Import this module anywhere with ``import config`` (or ``from config import ...``).
Call :func:`log_config_summary` once on startup to print a masked summary.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("finley.config")


# ── Helpers ──────────────────────────────────────────────────────────────────
def _require(name: str, *fallback_names: str) -> str:
    """Return the first non-empty env var among ``name`` + fallbacks, else raise."""
    for candidate in (name, *fallback_names):
        value = os.getenv(candidate)
        if value:
            return value.strip().strip('"').strip("'")
    names = " / ".join((name, *fallback_names))
    raise ValueError(
        f"Required environment variable {names} is not set. "
        "Copy .env.example to .env and fill it in."
    )


def _optional(name: str, default: str = "", *fallback_names: str) -> str:
    """Return the first non-empty env var among ``name`` + fallbacks, else default."""
    for candidate in (name, *fallback_names):
        value = os.getenv(candidate)
        if value:
            return value.strip().strip('"').strip("'")
    if not default:
        logger.warning("Optional env var %s is not set; using empty default.", name)
    return default


def _mask(secret: str) -> str:
    if not secret:
        return "<unset>"
    if len(secret) <= 4:
        return "****"
    return f"****{secret[-4:]}"


# ── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL: str = _require("SUPABASE_URL")
SUPABASE_ANON_KEY: str = _require("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY: str = _require(
    "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY"
)

# ── Pinecone ─────────────────────────────────────────────────────────────────
PINECONE_API_KEY: str = _require("PINECONE_API_KEY")
PINECONE_INDEX_NAME: str = _optional("PINECONE_INDEX_NAME", "finley-market")

# ── Gemini ───────────────────────────────────────────────────────────────────
# Accept either GEMINI_API_KEY or the AI Studio key name used in this repo.
GEMINI_API_KEY: str = _require("GEMINI_API_KEY", "AI_STUDIO_API_KEY", "GOOGLE_API_KEY")

# gemini-embedding-001 is requested at 768 dims (Matryoshka truncation) to match
# the Pinecone index. Keep these in lockstep via this single constant.
EMBED_DIM: int = int(_optional("EMBED_DIM", "768"))

# ── SendBlue ─────────────────────────────────────────────────────────────────
SENDBLUE_API_KEY: str = _require("SENDBLUE_API_KEY")
# Your SendBlue-registered number, e.g. "+15551234567". Optional so imports work
# before it is configured; sending will no-op with a warning until it is set.
SENDBLUE_FROM_NUMBER: str = _optional("SENDBLUE_FROM_NUMBER")

# ── Kraken / Broker ──────────────────────────────────────────────────────────
SCREENSHOT_BUCKET: str = _optional("SCREENSHOT_BUCKET", "trade-screenshots")

# ── Demo / runtime ───────────────────────────────────────────────────────────
DEMO_MODE: bool = _optional("DEMO_MODE", "false").lower() == "true"
PORT: int = int(_optional("PORT", "8000"))


def log_config_summary() -> None:
    """Log the active configuration with all secrets masked to their last 4 chars."""
    logger.info("── Finley configuration ──────────────────────────────")
    logger.info("SUPABASE_URL              = %s", SUPABASE_URL)
    logger.info("SUPABASE_ANON_KEY         = %s", _mask(SUPABASE_ANON_KEY))
    logger.info("SUPABASE_SERVICE_ROLE_KEY = %s", _mask(SUPABASE_SERVICE_ROLE_KEY))
    logger.info("PINECONE_API_KEY          = %s", _mask(PINECONE_API_KEY))
    logger.info("PINECONE_INDEX_NAME       = %s", PINECONE_INDEX_NAME)
    logger.info("GEMINI_API_KEY            = %s", _mask(GEMINI_API_KEY))
    logger.info("EMBED_DIM                 = %d", EMBED_DIM)
    logger.info("SENDBLUE_API_KEY          = %s", _mask(SENDBLUE_API_KEY))
    logger.info("SENDBLUE_FROM_NUMBER      = %s", SENDBLUE_FROM_NUMBER or "<unset>")
    logger.info("SCREENSHOT_BUCKET         = %s", SCREENSHOT_BUCKET)
    logger.info("DEMO_MODE                 = %s", DEMO_MODE)
    logger.info("PORT                      = %d", PORT)
    logger.info("──────────────────────────────────────────────────────")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s %(message)s")
    log_config_summary()
