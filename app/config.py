"""
Application configuration, loaded from environment variables.
Copy .env.example to .env and fill in real values before running.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present (does not override real env vars already set)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    # Auth token required in the "Authorization: Bearer <token>" header
    # for every request except /health. Single-user system, so a single
    # shared secret is enough — no user accounts/passwords needed.
    AUTH_TOKEN: str = os.environ.get("APP_AUTH_TOKEN", "")

    # Anthropic API — needed for the chat pipeline's NLU step.
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

    # SQLite database file location.
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{Path(__file__).resolve().parent.parent / 'data' / 'lifeos.db'}"
    )

    # CORS: which origins may call this API (set to your frontend's URL in production).
    ALLOWED_ORIGINS: list[str] = os.environ.get("ALLOWED_ORIGINS", "*").split(",")


settings = Settings()

if not settings.AUTH_TOKEN:
    raise RuntimeError(
        "APP_AUTH_TOKEN is not set. Create backend/.env from .env.example "
        "and set a secret token before starting the app."
    )
if not settings.ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. Get a key from console.anthropic.com "
        "and set it before starting the app."
    )
