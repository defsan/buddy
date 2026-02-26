# BUILDSPEC — Week 1: Voice Pipeline MVP

> **Audience:** AI coding agent (Claude Code, Cursor, Copilot, etc.)
> **Do not skip steps.** Each phase builds on the previous one. Verify each phase works before moving to the next.
> **Repo:** `git@github.com:defsan/buddy.git` (already cloned)
> **Working directory:** `/Users/elie/.openclaw/workspace/projects/buddy`
> **Host machine:** Mac Mini M4 (16GB), macOS, Python 3.12+, `uv` package manager available
> **Secondary machine:** Mac Studio Ultra M4 (128GB) at `192.168.68.99` — available for local model inference if needed

---

## GOAL

Build a voice companion server that:
1. Accepts audio input from a browser client over WebRTC
2. Transcribes speech to text using Deepgram
3. Generates a response using Anthropic Claude (claude-sonnet-4-5)
4. Synthesizes speech using ElevenLabs
5. Streams audio back to the browser client
6. Supports natural interruption (user can talk while bot is speaking)
7. Accessible from any device on the LAN (Mac, iPhone, iPad)

**Success metric:** User opens `http://<mac-mini-ip>:7860/client` in a browser, speaks, and hears a conversational response within ~1.5 seconds.

---

## FILE STRUCTURE

Create this exact structure. Do not deviate.

```
projects/buddy/
├── BUILDSPEC-WEEK1.md          # This file (already exists)
├── plan.md                     # Already exists
├── scenario-c.md               # Already exists
├── week1.md                    # Already exists
├── server/
│   ├── pyproject.toml          # Python project config (uv-compatible)
│   ├── .env.example            # Template for API keys
│   ├── .env                    # Actual API keys (gitignored)
│   ├── bot.py                  # Main entry point — Pipecat pipeline
│   └── config.py               # Configuration loader
├── .gitignore                  # Standard Python + .env
└── README.md                   # Setup and run instructions
```

---

## PHASE 1: Project Setup

### 1.1 Create `.gitignore`

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
*.egg-info/
dist/
build/

# Environment
.env
.env.local

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db
```

### 1.2 Create `server/pyproject.toml`

Use `uv` as the package manager. The project uses Pipecat with these specific integrations:
- **Transport:** SmallWebRTCTransport (peer-to-peer WebRTC, no third-party service needed)
- **STT:** Deepgram
- **LLM:** Anthropic Claude
- **TTS:** ElevenLabs
- **VAD:** Silero (bundled with Pipecat)

```toml
[project]
name = "buddy"
version = "0.1.0"
description = "Voice companion — Pipecat voice AI agent"
requires-python = ">=3.10"
dependencies = [
    "pipecat-ai[webrtc,deepgram,anthropic,elevenlabs,silero]",
    "python-dotenv",
    "loguru",
]

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.backends._legacy:_Backend"
```

### 1.3 Create `server/.env.example`

```ini
# Deepgram — https://console.deepgram.com (free $200 credit on signup)
DEEPGRAM_API_KEY=your_deepgram_api_key_here

# Anthropic — https://console.anthropic.com
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# ElevenLabs — https://elevenlabs.io (free 10K chars/mo)
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
ELEVENLABS_VOICE_ID=your_voice_id_here
```

### 1.4 Create `server/.env`

Populate with real API keys. Read them from these locations if they exist:

| Key | Source |
|-----|--------|
| `ANTHROPIC_API_KEY` | Read from `~/.config/anthropic/api_key` if it exists, OR from the OpenClaw keychain. The Anthropic key used by OpenClaw is stored in the OpenClaw config. You can find it by running: `cat /Users/elie/.openclaw/openclaw.json` and looking at the `auth.profiles` section for `anthropic:rellie`. Alternatively, check if there's a key file at `~/.config/anthropic/`. |
| `DEEPGRAM_API_KEY` | Elie needs to create this. Leave a placeholder and print a clear message at startup if missing. |
| `ELEVENLABS_API_KEY` | Elie needs to create this. Leave a placeholder and print a clear message at startup if missing. |
| `ELEVENLABS_VOICE_ID` | Default to `21m00Tcm4TlvDq8ikWAM` (Rachel — a good default female voice). Can be changed later. |

**IMPORTANT:** Do NOT hardcode any API keys in source code. Always read from `.env`.

### 1.5 Create `server/config.py`

```python
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


# Required API keys
DEEPGRAM_API_KEY = _require_key(
    "DEEPGRAM_API_KEY",
    "Sign up at https://console.deepgram.com — free $200 credit"
)
ANTHROPIC_API_KEY = _require_key(
    "ANTHROPIC_API_KEY",
    "Get from https://console.anthropic.com"
)
ELEVENLABS_API_KEY = _require_key(
    "ELEVENLABS_API_KEY",
    "Sign up at https://elevenlabs.io — free 10K chars/mo"
)
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# Server settings
SERVER_HOST = os.getenv("BUDDY_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("BUDDY_PORT", "7860"))

# LLM settings
LLM_MODEL = os.getenv("BUDDY_LLM_MODEL", "claude-sonnet-4-5-20250929")
```

### 1.6 Verify setup

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy/server
uv sync
```

This must complete without errors. If `uv` is not available, fall back to:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "pipecat-ai[webrtc,deepgram,anthropic,elevenlabs,silero]" python-dotenv loguru
```

---

## PHASE 2: Bot Server

### 2.1 Create `server/bot.py`

This is the main entry point. It creates a Pipecat pipeline with:
- **SmallWebRTCTransport** — peer-to-peer WebRTC (no Daily.co or other third-party needed). Includes a built-in development runner that serves a web client at `http://host:port/client`.
- **SileroVADAnalyzer** — voice activity detection for turn-taking
- **DeepgramSTTService** — streaming speech-to-text
- **AnthropicLLMService** — Claude for conversation
- **ElevenLabsTTSService** — streaming text-to-speech
- **LLMContextAggregatorPair** — manages conversation history automatically

**Key architecture notes for the implementing agent:**

1. Pipecat uses a frame-based pipeline. Data flows as typed frames: `AudioRawFrame`, `TranscriptionFrame`, `TextFrame`, etc.
2. The `LLMContextAggregatorPair` creates two processors: `user_aggregator` (collects user transcriptions into context) and `assistant_aggregator` (collects bot responses into context). These maintain the conversation history.
3. SmallWebRTCTransport bundles a development web server. When you run `uv run bot.py`, it serves a client page at `http://0.0.0.0:7860/client` with a Connect button, microphone access, and audio playback.
4. Interruption handling is automatic: when the VAD detects user speech while TTS is playing, Pipecat cancels the in-flight TTS and processes the new input.

**Implementation:**

```python
"""Buddy — Voice Companion Server.

Run with: uv run bot.py
Then open http://localhost:7860/client in your browser.
"""

import os
import sys

# Ensure server directory is in path for config import
sys.path.insert(0, os.path.dirname(__file__))

from loguru import logger

print("🐾 Starting Buddy voice companion...")
print("⏳ Loading models (may take ~20s on first run)\n")

logger.info("Loading Silero VAD model...")
from pipecat.audio.vad.silero import SileroVADAnalyzer
logger.info("✅ Silero VAD loaded")

from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams

logger.info("✅ All components loaded")

import config  # Our config module — validates API keys on import

# ── System Prompt ──────────────────────────────────────────────
# This defines Buddy's personality for voice conversations.
# Keep it concise — voice responses should be short and natural.
SYSTEM_PROMPT = """You are Buddy, a warm and witty voice companion. You speak out loud to Elie.

Voice rules (CRITICAL — you are being spoken aloud via TTS):
- Keep responses to 1-3 sentences unless the user asks for detail.
- Never use markdown, bullet points, asterisks, code blocks, or any formatting.
- Never use emojis or special characters.
- Use natural conversational language. Say "about twenty bucks" not "$19.99".
- Use contractions: "I'm", "you're", "that's", "don't".
- Don't start with "I'd be happy to help" or "Great question" — just answer.
- You can say "hmm", "yeah", "well", "so", "anyway" — be human.
- If you don't know something, say so briefly. Don't ramble.
- Match the user's energy: casual question gets casual answer, serious gets serious.

About Elie:
- Engineer and product person, sharp, values directness.
- Timezone: US Pacific (PST/PDT).
- Prefers no fluff — get to the point.

You are NOT a generic assistant. You're Buddy — a companion. Be yourself."""


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    """Configure and run the voice pipeline."""
    logger.info("Configuring pipeline...")

    # ── Services ───────────────────────────────────────────────
    stt = DeepgramSTTService(api_key=config.DEEPGRAM_API_KEY)

    llm = AnthropicLLMService(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.LLM_MODEL,
    )

    tts = ElevenLabsTTSService(
        api_key=config.ELEVENLABS_API_KEY,
        voice_id=config.ELEVENLABS_VOICE_ID,
        model="eleven_turbo_v2_5",
        output_format="pcm_16000",
    )

    # ── Conversation Context ───────────────────────────────────
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    context = LLMContext(messages)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # ── Pipeline ───────────────────────────────────────────────
    # Data flows left-to-right through these processors:
    #   audio in → STT → user context → LLM → TTS → audio out → assistant context
    pipeline = Pipeline([
        transport.input(),       # Receive audio from browser via WebRTC
        stt,                     # Deepgram: audio → text (streaming)
        user_aggregator,         # Collect user text into conversation context
        llm,                     # Claude: generate response (streaming)
        tts,                     # ElevenLabs: text → audio (streaming)
        transport.output(),      # Send audio back to browser via WebRTC
        assistant_aggregator,    # Collect bot response into conversation context
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    # ── Events ─────────────────────────────────────────────────
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("🔗 Client connected")
        # Buddy greets the user when they connect
        messages.append({
            "role": "system",
            "content": "The user just connected. Greet them warmly but briefly — one sentence max. Be natural, like picking up a conversation with a friend.",
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("👋 Client disconnected")
        await task.cancel()

    # ── Run ─────────────────────────────────────────────────────
    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    logger.info(f"🐾 Buddy is ready! Open http://{config.SERVER_HOST}:{config.SERVER_PORT}/client")
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main entry point for Pipecat's runner."""
    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
```

**IMPORTANT implementation notes:**
- The `pipecat.runner.run.main()` function handles argument parsing and sets up the SmallWebRTCTransport development runner automatically. It serves a web client at `/client`.
- Do NOT create a manual FastAPI/aiohttp server. Pipecat's `main()` does this.
- The `transport_params` dict uses `"webrtc"` key — this selects SmallWebRTCTransport.
- If the ElevenLabs TTS constructor signature differs from what's shown (e.g., different param names for model or output format), check the installed version's API. The key params are: `api_key`, `voice_id`. The `model` and `output_format` may vary by pipecat version.

### 2.2 Verify bot starts

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy/server
uv run bot.py
```

Expected output (approximately):
```
🐾 Starting Buddy voice companion...
⏳ Loading models (may take ~20s on first run)
... Loading Silero VAD model...
... ✅ Silero VAD loaded
... ✅ All components loaded
... Configuring pipeline...
... 🐾 Buddy is ready! Open http://0.0.0.0:7860/client
🚀 WebRTC server starting at http://localhost:7860/client
   Open this URL in your browser to connect!
```

If API keys are missing, you'll see a clear error message from `config.py` telling you which key is missing and where to get it.

### 2.3 Test from browser

1. Open `http://localhost:7860/client` (or `http://<mac-mini-ip>:7860/client` from another device)
2. Click "Connect"
3. Allow microphone access
4. Speak — you should hear Buddy respond

**Troubleshooting:**
- If no audio: check browser console for WebRTC errors
- If STT not working: verify Deepgram key at https://console.deepgram.com
- If TTS not working: verify ElevenLabs key and voice_id
- If LLM not responding: verify Anthropic key

---

## PHASE 3: README

### 3.1 Create `README.md`

```markdown
# 🐾 Buddy — Voice Companion

Talk to Buddy from any browser on your network. Built with [Pipecat](https://pipecat.ai).

## Quick Start

### 1. Get API Keys

| Service | Sign Up | Free Tier |
|---------|---------|-----------|
| [Deepgram](https://console.deepgram.com) | STT (speech-to-text) | $200 credit |
| [Anthropic](https://console.anthropic.com) | LLM (Claude) | Pay-as-you-go |
| [ElevenLabs](https://elevenlabs.io) | TTS (text-to-speech) | 10K chars/mo |

### 2. Configure

```bash
cd server
cp .env.example .env
# Edit .env with your API keys
```

### 3. Install & Run

```bash
cd server
uv sync
uv run bot.py
```

### 4. Connect

Open `http://localhost:7860/client` in your browser. Click Connect. Talk.

From other devices on your LAN: `http://<your-mac-ip>:7860/client`

## Architecture

```
Browser Mic → WebRTC → Silero VAD → Deepgram STT → Claude → ElevenLabs TTS → WebRTC → Browser Speaker
```

## Configuration

Edit `server/.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEEPGRAM_API_KEY` | Deepgram API key | (required) |
| `ANTHROPIC_API_KEY` | Anthropic API key | (required) |
| `ELEVENLABS_API_KEY` | ElevenLabs API key | (required) |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice | Rachel |
| `BUDDY_HOST` | Server bind address | `0.0.0.0` |
| `BUDDY_PORT` | Server port | `7860` |
| `BUDDY_LLM_MODEL` | Claude model | `claude-sonnet-4-5-20250929` |

## Changing Buddy's Voice

Browse voices at https://elevenlabs.io/voice-library, copy the voice ID, and set `ELEVENLABS_VOICE_ID` in `.env`.

## Changing Buddy's Personality

Edit the `SYSTEM_PROMPT` in `server/bot.py`.
```

---

## PHASE 4: Validation Checklist

Run through each item. All must pass before considering Week 1 complete.

- [ ] `uv sync` completes without errors in `server/`
- [ ] `uv run bot.py` starts without errors (given valid API keys)
- [ ] Browser at `http://localhost:7860/client` shows a Connect button
- [ ] Clicking Connect establishes WebRTC connection (check server logs for "Client connected")
- [ ] Speaking into mic produces transcription (check server logs for STT output)
- [ ] Claude generates a response (check server logs)
- [ ] ElevenLabs TTS plays audio back through browser
- [ ] Full round-trip: speak → hear response within ~2 seconds
- [ ] Interruption works: speak while Buddy is talking → Buddy stops and listens
- [ ] Accessible from iPhone Safari on same LAN: `http://<mac-mini-ip>:7860/client`
- [ ] Server runs stable for 10+ minutes of conversation without crashes
- [ ] `.env` is gitignored, no keys in committed code
- [ ] All files committed and pushed to `git@github.com:defsan/buddy.git`

---

## PHASE 5: Git

After all phases complete and validation passes:

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy
git add -A
git commit -m "Week 1: Voice pipeline MVP — Pipecat + Deepgram + Claude + ElevenLabs"
git push origin main
```

---

## KNOWN ISSUES & EDGE CASES

1. **Pipecat API changes:** Pipecat is actively developed. If import paths don't match (e.g., `pipecat.audio.vad.silero` vs `pipecat.vad.silero`), check the installed version's actual module structure with `python -c "import pipecat; print(pipecat.__version__)"` and adjust imports.

2. **ElevenLabs TTS params:** The `ElevenLabsTTSService` constructor may differ between pipecat versions. Core params are always `api_key` and `voice_id`. If `model` or `output_format` cause errors, remove them — Pipecat may handle defaults internally.

3. **SmallWebRTCTransport binding:** It binds to `0.0.0.0` by default via the development runner. If you need to customize the host/port, pass them via CLI args to `main()` or check Pipecat's runner documentation.

4. **macOS firewall:** If other LAN devices can't connect, check System Settings → Network → Firewall. The Python process needs to accept incoming connections on port 7860.

5. **iPhone Safari WebRTC:** Safari supports WebRTC but may require HTTPS for microphone access when not on localhost. If mic doesn't work from iPhone, try accessing via the Mac's Bonjour name (e.g., `http://mac-mini.local:7860/client`) or set up a local HTTPS proxy.

6. **Anthropic model name:** The model string `claude-sonnet-4-5-20250929` is used as of Feb 2026. If it errors with "model not found", check https://docs.anthropic.com/en/docs/about-claude/models for current model names and update `config.py`.

---

## WHAT COMES AFTER WEEK 1

These are NOT in scope for this buildspec. Do not implement them.

- **Week 2:** Wire OpenClaw for memory/personality/tools, build iOS client
- **Week 3:** iPad home station, wake word detection ("Hey Buddy")
- **Week 4:** Local fallback chain (Whisper + Piper + Qwen), latency optimization
