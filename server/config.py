"""Configuration loader for Buddy voice server."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the server directory
_server_dir = Path(__file__).parent
load_dotenv(_server_dir / ".env", override=True)


def _require_key(name: str, hint: str) -> str:
    """Get a required environment variable or exit with a helpful message."""
    value = os.getenv(name, "").strip()
    if not value or value.startswith("your_"):
        print(f"\n❌  Missing required key: {name}")
        print(f"    → {hint}")
        print(f"    Set it in: {_server_dir / '.env'}\n")
        sys.exit(1)
    return value


# ── LLM (only paid service) ──────────────────────────────
ANTHROPIC_API_KEY = _require_key(
    "ANTHROPIC_API_KEY",
    "Get from https://console.anthropic.com"
)
LLM_MODEL = os.getenv("BUDDY_LLM_MODEL", "claude-sonnet-4-5-20250929")

# ── STT: Whisper.cpp (local) ──────────────────────────────
WHISPER_SERVER_URL = os.getenv("WHISPER_SERVER_URL", "http://127.0.0.1:8178")

# ── TTS: Piper (local, Python library) ────────────────────
_home = Path.home()
PIPER_MODEL = os.getenv("PIPER_MODEL", str(_home / ".local/share/piper/voices/en_US-amy-medium.onnx"))

if not Path(PIPER_MODEL).is_file():
    print(f"\n❌  Piper voice model not found at: {PIPER_MODEL}")
    print(f"    → Run: bash scripts/install-piper.sh")
    print(f"    Or set PIPER_MODEL in {_server_dir / '.env'}\n")
    sys.exit(1)

# ── Server ───────────────────────────────────────────────
SERVER_HOST = os.getenv("BUDDY_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("BUDDY_PORT", "7860"))
