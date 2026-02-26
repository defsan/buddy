# 🐾 Buddy — Voice Companion (Local-First)

Talk to Buddy from any browser. Built with [Pipecat](https://pipecat.ai).

**Zero cloud STT/TTS costs.** Uses whisper.cpp and Piper TTS locally. Only paid service is Anthropic Claude for the brain.

## Quick Start

### 1. Prerequisites

| Component | Install |
|-----------|---------|
| whisper.cpp | `bash scripts/install-whisper.sh` |
| Piper TTS | `bash scripts/install-piper.sh` |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |

### 2. Start whisper.cpp server

```bash
cd ~/whisper.cpp
./build/bin/whisper-server -m models/ggml-large-v3.bin --host 127.0.0.1 --port 8178
```

### 3. Configure

```bash
cd server
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

### 4. Install & Run

```bash
cd server
uv sync
uv run bot.py
```

### 5. Connect

Open `http://localhost:7860/client` in your browser. Click Connect. Talk.

## Architecture

```
Browser Mic → WebRTC → Silero VAD → whisper.cpp (local) → Claude → Piper TTS (local) → WebRTC → Browser Speaker
```

## Cost

| Service | Monthly |
|---------|---------|
| whisper.cpp (local) | $0 |
| Piper TTS (local) | $0 |
| Claude API | ~$3-5 (varies with usage) |
| **Total** | **~$3-5/mo** |

## Configuration

Edit `server/.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | (required) |
| `WHISPER_SERVER_URL` | whisper.cpp server URL | `http://127.0.0.1:8178` |
| `PIPER_BINARY` | Path to Piper binary | auto-detected |
| `PIPER_MODEL` | Path to Piper voice model | auto-detected |
| `BUDDY_HOST` | Server bind address | `0.0.0.0` |
| `BUDDY_PORT` | Server port | `7860` |
| `BUDDY_LLM_MODEL` | Claude model | `claude-sonnet-4-5-20250929` |

## Changing Buddy's Voice

Browse voices at https://rhasspy.github.io/piper-samples/. Download the `.onnx` and `.onnx.json` files, save to `~/.local/share/piper/voices/`, and set `PIPER_MODEL` in `.env`.

## Changing Buddy's Personality

Edit the `SYSTEM_PROMPT` in `server/bot.py`.
