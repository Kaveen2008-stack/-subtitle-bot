import os
from dotenv import load_dotenv

load_dotenv(override=True)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}. Copy .env.example to .env.")
    return value


TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = _require("GITHUB_TOKEN")          # needs repo + gist scopes
GITHUB_REPO = _require("GITHUB_REPO")            # "owner/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

MAX_SRT_MB = 2  # sanity limit for subtitle uploads
