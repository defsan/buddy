# Week 1: Voice Pipeline MVP

**Goal:** Get voice in → text → LLM → voice out working end-to-end. Talk to Buddy from your Mac, hear it respond.

**Success criteria:** You say "What's the weather like?" into your mic, and within ~1.5s you hear a spoken answer.

---

## Day 1: Environment + Pipecat Hello World

### Targets
- [ ] Set up Python project with Pipecat
- [ ] Get API keys for Deepgram + ElevenLabs
- [ ] Run Pipecat's built-in example (echo bot or simple LLM bot)

### Steps

**1. Create the project environment**
```bash
cd ~/projects/buddy  # or wherever
python3 -m venv venv
source venv/bin/activate
pip install "pipecat-ai[silero,deepgram,elevenlabs,anthropic,websocket]"
```

**2. Get API keys**
- **Deepgram:** https://console.deepgram.com → free tier gives $200 credit
  - Create API key with "Member" scope
  - Store in `~/.config/buddy/deepgram_key`
  
- **ElevenLabs:** https://elevenlabs.io → free tier gives 10K chars/mo
  - Get API key from Profile settings
  - Store in `~/.config/buddy/elevenlabs_key`
  - Pick a voice (or clone one later) — note the voice ID

**3. Run Pipecat example**
```bash
# Clone pipecat examples for reference
git clone https://github.com/pipecat-ai/pipecat.git /tmp/pipecat-ref
# Look at: /tmp/pipecat-ref/examples/simple-chatbot/
```

**4. Minimal test script**
```python
# buddy_test.py — minimal voice pipeline test
import asyncio
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.transports.services.daily import DailyTransport  # or local
from pipecat.services.deepgram import DeepgramSTTService
from pipecat.services.elevenlabs import ElevenLabsTTSService
from pipecat.services.anthropic import AnthropicLLMService
from pipecat.vad.silero import SileroVADAnalyzer

# ... (fill in with API keys and basic pipeline)
```

### Deliverable
A running Pipecat process that you can talk to via Daily.co's test room (their free transport layer for testing).

---

## Day 2: Local WebSocket Transport

### Targets
- [ ] Replace Daily.co transport with local WebSocket
- [ ] Build a minimal browser client (HTML + JS) that captures mic and plays audio
- [ ] End-to-end: browser mic → server → browser speaker

### Steps

**1. WebSocket server transport**
Pipecat supports WebSocket transport. Build or adapt:

```python
# buddy_server.py
from pipecat.transports.network.websocket_server import WebSocketServerTransport

transport = WebSocketServerTransport(
    host="0.0.0.0",
    port=8765,
    params=WebSocketServerParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    )
)
```

**2. Browser client**
```html
<!-- client/web/index.html -->
<!-- Captures mic audio, sends PCM over WebSocket, plays received audio -->
```

Key browser APIs:
- `navigator.mediaDevices.getUserMedia()` — mic access
- `AudioContext` + `ScriptProcessorNode` / `AudioWorklet` — capture PCM frames
- `WebSocket` — send/receive audio chunks
- `AudioContext.decodeAudioData()` or raw PCM playback

**3. Audio format alignment**
- Pipecat expects 16-bit PCM, 16kHz mono for STT
- TTS output comes as PCM or opus
- Client needs to resample if mic is 44.1kHz/48kHz → 16kHz

### Deliverable
Open `localhost:8080` in browser, speak, hear response. All on LAN.

---

## Day 3: Wire Up Claude via OpenClaw

### Targets
- [ ] Replace Pipecat's built-in Anthropic processor with OpenClaw
- [ ] Buddy responds with personality (SOUL.md) and context (MEMORY.md)
- [ ] Verify tools work ("What's on my calendar?")

### Steps

**1. OpenClaw LLM processor**
Two options:

**Option A: HTTP bridge (recommended for day 3)**
```python
# openclaw_llm.py
class OpenClawLLMProcessor(FrameProcessor):
    """Send transcribed text to OpenClaw, get response."""
    
    async def process_frame(self, frame):
        if isinstance(frame, TranscriptionFrame):
            # Send to OpenClaw via its WebSocket/HTTP API
            response = await self._send_to_openclaw(frame.text)
            # Emit text frames for TTS
            yield TextFrame(text=response)
    
    async def _send_to_openclaw(self, text):
        # Use OpenClaw's session API
        # POST to gateway with message, get response
        pass
```

**Option B: Direct Anthropic with OpenClaw's system prompt**
```python
# Simpler: just use Anthropic directly but load SOUL.md as system prompt
system_prompt = open("/Users/elie/.openclaw/workspace/SOUL.md").read()
system_prompt += "\n\nYou are Buddy, a voice companion. Keep responses to 1-3 sentences. Be warm and conversational."

llm = AnthropicLLMService(
    api_key=anthropic_key,
    model="claude-sonnet-4-20250514",
    system_prompt=system_prompt,
)
```

Start with Option B to get it working, migrate to Option A for full tool access.

**2. Voice-optimized system prompt**
```
You are Buddy, Elie's voice companion. You speak out loud — keep it natural.

Rules for voice:
- 1-3 sentences max unless asked for detail
- No markdown, no bullet points, no code blocks
- Use conversational language ("yeah", "sure", "hmm")
- Numbers: say "about twenty bucks" not "$19.99"
- Don't say "I'd be happy to help" — just help
```

### Deliverable
Talk to Buddy, get responses that sound like a person (not a chatbot). Personality matches SOUL.md.

---

## Day 4: ElevenLabs Voice Selection + Tuning

### Targets
- [ ] Pick or create the right voice
- [ ] Tune TTS settings (stability, similarity, speed)
- [ ] Test streaming TTS latency

### Steps

**1. Voice selection**
- Browse ElevenLabs voice library: https://elevenlabs.io/voice-library
- Or clone a voice: upload 1-5 min of clean audio samples
- Recommendation: pick a warm, slightly casual voice — avoid robotic/formal ones
- Note the `voice_id` for config

**2. TTS configuration**
```python
tts = ElevenLabsTTSService(
    api_key=elevenlabs_key,
    voice_id="your_voice_id",
    model="eleven_turbo_v2_5",  # Fastest model
    params=ElevenLabsTTSParams(
        stability=0.5,           # Lower = more expressive
        similarity_boost=0.75,   # Higher = closer to original voice
        style=0.3,               # Expressiveness
        use_speaker_boost=True,
    ),
    output_format="pcm_16000",   # Match pipeline sample rate
)
```

**3. Streaming test**
- Measure time from text input to first audio chunk
- Target: <500ms
- If too slow: try `eleven_turbo_v2` model, reduce `optimize_streaming_latency`

### Deliverable
Buddy sounds good. Voice feels right. Latency is acceptable.

---

## Day 5: Interruption Handling + VAD Tuning

### Targets
- [ ] User can interrupt Buddy mid-sentence
- [ ] No false triggers (TV, background noise)
- [ ] Clean turn-taking (no awkward pauses or cutoffs)

### Steps

**1. Interruption handling**
Pipecat's pipeline handles this via frame cancellation:
```python
# Pipeline automatically handles:
# - UserStartedSpeakingFrame → cancel in-flight TTS
# - UserStoppedSpeakingFrame → process new utterance
```

Test scenarios:
- Buddy is talking, you say "stop" → should stop immediately
- Buddy is talking, you say "actually..." → should stop and listen
- Background noise → should NOT interrupt

**2. VAD tuning**
```python
vad = SileroVADAnalyzer(
    params=VADParams(
        threshold=0.5,           # Confidence threshold (higher = less sensitive)
        min_speech_duration=0.25, # Min speech length to trigger
        min_silence_duration=0.6, # Silence before endpointing
        padding_duration=0.3,     # Buffer around speech
    )
)
```

Tune iteratively:
- Too many false triggers → raise threshold, increase min_speech_duration
- Cutting off words → increase padding_duration
- Long pauses before response → decrease min_silence_duration

**3. Endpointing strategy**
- Fast endpointing (600ms silence) for quick exchanges
- Longer endpointing (1.5s) when Buddy asks a question (expect thinking time)
- Pipecat can adjust dynamically based on context

### Deliverable
Smooth conversation flow. Can interrupt naturally. No ghost triggers.

---

## Day 6-7: Integration + Polish

### Targets
- [ ] Stable server process (auto-restart on crash)
- [ ] Config file for all API keys and settings
- [ ] README with setup instructions
- [ ] Test from multiple devices on LAN

### Steps

**1. Config management**
```python
# config.py
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "buddy"

def load_config():
    return {
        "deepgram_key": (CONFIG_DIR / "deepgram_key").read_text().strip(),
        "elevenlabs_key": (CONFIG_DIR / "elevenlabs_key").read_text().strip(),
        "elevenlabs_voice_id": "...",
        "anthropic_key": "...",  # or use OpenClaw
        "openclaw_gateway": "ws://127.0.0.1:18789",
        "server_host": "0.0.0.0",
        "server_port": 8765,
        "client_port": 8080,
    }
```

**2. Process management**
```bash
# launchd plist or simple wrapper
# buddy-server.sh
#!/bin/bash
cd /Users/elie/projects/buddy
source venv/bin/activate
python3 buddy_server.py
```

**3. Multi-device test**
- Mac: open browser → `http://mac-mini.local:8080`
- iPhone: open Safari → same URL
- iPad: same URL (this becomes the "home device" later)

### Deliverable
Working voice companion you can talk to from any device on your network.

---

## Week 1 Exit Criteria

| # | Criteria | Target |
|---|----------|--------|
| 1 | End-to-end voice conversation works | ✅ |
| 2 | Latency from end-of-speech to first audio | <1.5s |
| 3 | Personality/tone feels right | Matches SOUL.md |
| 4 | Interruption works cleanly | Can cut off mid-sentence |
| 5 | Works from browser on LAN | Mac + iPhone tested |
| 6 | Server runs stably for 30+ min | No crashes |

## What's NOT in Week 1
- ❌ OpenClaw tool integration (Week 2)
- ❌ Wake word detection (Week 3)
- ❌ Dedicated home device setup (Week 3)
- ❌ Voice cloning (nice-to-have, can do anytime)
- ❌ Local fallback chain (Week 4)
- ❌ Conversation history/memory in voice sessions (Week 2)

---

## Quick Reference: API Docs
- **Pipecat:** https://docs.pipecat.ai
- **Deepgram:** https://developers.deepgram.com/docs/streaming
- **ElevenLabs:** https://elevenlabs.io/docs/api-reference/text-to-speech-stream
- **Anthropic:** https://docs.anthropic.com/en/docs
