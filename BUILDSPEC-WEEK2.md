# BUILDSPEC — Week 2: OpenClaw Integration + iOS Client

> **Audience:** AI coding agent (Claude Code, Cursor, Copilot, etc.)
> **Prerequisite:** Week 1 complete — Pipecat voice pipeline running with whisper.cpp STT, Piper TTS, and direct Anthropic LLM.
> **Do not skip steps.** Each phase builds on the previous one. Verify each phase works before moving to the next.
> **Repo:** `git@github.com:defsan/buddy.git`
> **Working directory:** `/Users/elie/.openclaw/workspace/projects/buddy`
> **Host machine:** Mac Mini M4 (16GB), macOS, Python 3.12+, `uv` package manager
> **Inference machine:** Mac Studio Ultra M4 (128GB) at `192.168.68.99` — whisper.cpp STT
> **OpenClaw gateway:** `ws://127.0.0.1:18789` (HTTP: `http://127.0.0.1:18789`)

---

## GOAL

Upgrade Buddy from a standalone voice bot to an OpenClaw-integrated voice companion that:
1. Routes LLM calls through OpenClaw (personality, memory, tools)
2. Streams responses sentence-by-sentence for fast TTS
3. Accesses tools via voice (calendar, weather, lights, email)
4. Has a mobile-friendly PWA client for iPhone
5. Persists conversation context across reconnects
6. Falls back to direct Anthropic when OpenClaw is unavailable

**STT and TTS remain local** (whisper.cpp + Piper from Week 1). No cloud STT/TTS needed.

**Success metric:** Ask "What's on my calendar today?" from iPhone Safari, hear a real answer from Google Calendar within 2 seconds.

---

## FILE STRUCTURE (additions to Week 1)

```
projects/buddy/
├── server/
│   ├── bot.py                  # Updated: use OpenClaw LLM instead of direct Anthropic
│   ├── openclaw_llm.py         # NEW: OpenClaw LLM processor for Pipecat
│   ├── latency.py              # NEW: Per-turn latency tracker
│   ├── stt_whisper.py          # Unchanged from Week 1
│   ├── tts_piper.py            # Unchanged from Week 1
│   ├── config.py               # Updated: add OpenClaw settings
│   ├── pyproject.toml          # Unchanged (aiohttp already included)
│   └── .env                    # Updated: add OPENCLAW_GATEWAY_URL
├── client/
│   └── web/                    # NEW: PWA client for iPhone
│       ├── index.html          # App shell + WebRTC client
│       ├── app.js              # Audio capture, WebRTC, UI state
│       ├── style.css           # Mobile-first dark UI
│       ├── manifest.json       # PWA manifest for home screen
│       ├── sw.js               # Service worker
│       └── icons/
│           ├── icon-192.png    # PWA icon
│           └── icon-512.png    # PWA icon
└── README.md                   # Updated with Week 2 features
```

---

## PHASE 1: OpenClaw LLM Processor

### 1.1 Update `server/.env`

Add OpenClaw configuration:

```ini
# Existing (from Week 1)
ANTHROPIC_API_KEY=...
WHISPER_SERVER_URL=http://192.168.68.99:8178

# OpenClaw integration (NEW)
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
OPENCLAW_SESSION_LABEL=buddy-voice
# Optional: set to a specific agent ID if using multi-agent
# OPENCLAW_AGENT_ID=
```

### 1.2 Update `server/config.py`

Append OpenClaw settings to the existing file:

```python
# OpenClaw integration
OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
OPENCLAW_SESSION_LABEL = os.getenv("OPENCLAW_SESSION_LABEL", "buddy-voice")
OPENCLAW_AGENT_ID = os.getenv("OPENCLAW_AGENT_ID", None)

# Fallback: if OpenClaw is down, use direct Anthropic
FALLBACK_TO_DIRECT = os.getenv("BUDDY_FALLBACK_DIRECT", "true").lower() == "true"
```

### 1.3 Create `server/openclaw_llm.py`

This replaces direct Anthropic calls with OpenClaw gateway calls, giving Buddy personality (SOUL.md), memory (MEMORY.md), and tools (calendar, email, lights, etc.).

```python
"""OpenClaw LLM Processor for Pipecat.

Routes voice transcriptions through OpenClaw gateway for full
personality, memory, and tool access. Falls back to direct Anthropic
if OpenClaw is unreachable.
"""

import asyncio
import re
import time
from typing import AsyncGenerator

import aiohttp
from loguru import logger

from pipecat.frames.frames import (
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


# Sentence boundary pattern: split on . ! ? followed by space or end
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')


class OpenClawLLMProcessor(FrameProcessor):
    """Sends transcribed text to OpenClaw, emits response as TextFrames.
    
    Integrates with OpenClaw's session API to leverage:
    - SOUL.md personality
    - MEMORY.md long-term context
    - Full tool ecosystem (calendar, email, lights, etc.)
    - Conversation history persistence
    """

    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:18789",
        session_label: str = "buddy-voice",
        agent_id: str | None = None,
        timeout_seconds: float = 30.0,
        fallback_enabled: bool = True,
        fallback_anthropic_key: str | None = None,
        fallback_model: str = "claude-sonnet-4-5-20250929",
    ):
        super().__init__()
        self._gateway_url = gateway_url.rstrip("/")
        self._session_label = session_label
        self._agent_id = agent_id
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._fallback_enabled = fallback_enabled
        self._fallback_key = fallback_anthropic_key
        self._fallback_model = fallback_model
        self._http_session: aiohttp.ClientSession | None = None
        self._openclaw_healthy = True

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(timeout=self._timeout)
        return self._http_session

    async def cleanup(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        await super().cleanup()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                logger.info(f"🎤 → OpenClaw: {text}")
                t0 = time.monotonic()
                await self._respond(text)
                elapsed = time.monotonic() - t0
                logger.info(f"⏱️  LLM round-trip: {elapsed*1000:.0f}ms")
        elif isinstance(frame, EndFrame):
            await self.cleanup()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def _respond(self, user_text: str):
        """Send user text to OpenClaw and stream response to TTS."""
        await self.push_frame(LLMFullResponseStartFrame())

        success = False
        if self._openclaw_healthy:
            success = await self._respond_via_openclaw(user_text)

        if not success and self._fallback_enabled and self._fallback_key:
            logger.warning("⚠️  Falling back to direct Anthropic")
            await self._respond_via_anthropic(user_text)

        await self.push_frame(LLMFullResponseEndFrame())

    async def _respond_via_openclaw(self, user_text: str) -> bool:
        """Send to OpenClaw gateway. Returns True on success."""
        try:
            session = await self._get_http_session()
            payload = {
                "message": user_text,
                "label": self._session_label,
            }
            if self._agent_id:
                payload["agentId"] = self._agent_id

            async with session.post(
                f"{self._gateway_url}/api/sessions/send",
                json=payload,
            ) as resp:
                if resp.status != 200:
                    logger.error(f"OpenClaw returned {resp.status}: {await resp.text()}")
                    self._openclaw_healthy = False
                    return False

                result = await resp.json()
                response_text = result.get("response", "").strip()

                if not response_text:
                    logger.warning("OpenClaw returned empty response")
                    return False

                # Emit response sentence-by-sentence for faster TTS
                await self._emit_sentences(response_text)
                self._openclaw_healthy = True
                return True

        except asyncio.TimeoutError:
            logger.error("OpenClaw timeout")
            self._openclaw_healthy = False
            return False
        except aiohttp.ClientError as e:
            logger.error(f"OpenClaw connection error: {e}")
            self._openclaw_healthy = False
            return False
        except Exception as e:
            logger.error(f"OpenClaw unexpected error: {e}")
            self._openclaw_healthy = False
            return False

    async def _respond_via_anthropic(self, user_text: str):
        """Direct Anthropic fallback — no tools, no memory, basic personality."""
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self._fallback_key)
            response = await client.messages.create(
                model=self._fallback_model,
                max_tokens=256,
                system="You are Buddy, a voice companion. Be brief and conversational. 1-3 sentences max. No formatting.",
                messages=[{"role": "user", "content": user_text}],
            )
            text = response.content[0].text.strip()
            if text:
                await self._emit_sentences(text)

        except Exception as e:
            logger.error(f"Anthropic fallback error: {e}")
            await self.push_frame(
                TextFrame(text="Sorry, I'm having trouble thinking right now. Try again in a sec.")
            )

    async def _emit_sentences(self, text: str):
        """Split text at sentence boundaries and emit as separate TextFrames.
        
        This lets Piper TTS start generating audio for sentence 1
        while the rest of the text is still being processed.
        """
        sentences = _SENTENCE_RE.split(text)
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence:
                logger.debug(f"💬 Emitting: {sentence}")
                await self.push_frame(TextFrame(text=sentence))
```

**Implementation notes:**

1. The exact OpenClaw HTTP API endpoint may differ. Check the gateway's actual routes. Common patterns:
   - `POST /api/sessions/send` with `{message, label, agentId}`
   - The gateway may use WebSocket only — in that case, use `aiohttp.ClientSession.ws_connect()`

2. The sentence splitting enables Piper to start on the first sentence while later sentences arrive. Since Piper is ~50ms per sentence, this barely matters for short responses, but helps a lot for longer ones.

### 1.4 Create `server/latency.py`

```python
"""Per-turn latency tracking for voice pipeline optimization."""

import time
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class TurnMetrics:
    speech_end: float = 0.0
    stt_complete: float = 0.0
    llm_first_token: float = 0.0
    tts_first_chunk: float = 0.0

    def report(self) -> dict[str, str]:
        metrics = {}
        if self.stt_complete and self.speech_end:
            metrics["stt"] = f"{(self.stt_complete - self.speech_end)*1000:.0f}ms"
        if self.llm_first_token and self.stt_complete:
            metrics["llm_ttft"] = f"{(self.llm_first_token - self.stt_complete)*1000:.0f}ms"
        if self.tts_first_chunk and self.llm_first_token:
            metrics["tts_ttfa"] = f"{(self.tts_first_chunk - self.llm_first_token)*1000:.0f}ms"
        if self.tts_first_chunk and self.speech_end:
            metrics["total"] = f"{(self.tts_first_chunk - self.speech_end)*1000:.0f}ms"
        return metrics

    def log(self):
        r = self.report()
        if r:
            parts = " | ".join(f"{k}={v}" for k, v in r.items())
            logger.info(f"📊 Latency: {parts}")


class LatencyTracker:
    def __init__(self):
        self._current = TurnMetrics()
        self._history: list[dict[str, str]] = []

    def mark_speech_end(self):
        self._current = TurnMetrics(speech_end=time.monotonic())

    def mark_stt_complete(self):
        self._current.stt_complete = time.monotonic()

    def mark_llm_first_token(self):
        if not self._current.llm_first_token:
            self._current.llm_first_token = time.monotonic()

    def mark_tts_first_chunk(self):
        if not self._current.tts_first_chunk:
            self._current.tts_first_chunk = time.monotonic()
            self._current.log()
            self._history.append(self._current.report())

    @property
    def average_total_ms(self) -> float:
        totals = []
        for h in self._history:
            if "total" in h:
                totals.append(float(h["total"].rstrip("ms")))
        return sum(totals) / len(totals) if totals else 0.0
```

### 1.5 Update `server/bot.py`

Replace the direct Anthropic pipeline with OpenClaw:

```python
# In bot.py, replace the LLM section:

# ── Option 1: OpenClaw integration (default) ──────────────
from openclaw_llm import OpenClawLLMProcessor

openclaw_llm = OpenClawLLMProcessor(
    gateway_url=config.OPENCLAW_GATEWAY_URL,
    session_label=config.OPENCLAW_SESSION_LABEL,
    agent_id=config.OPENCLAW_AGENT_ID,
    fallback_enabled=config.FALLBACK_TO_DIRECT,
    fallback_anthropic_key=config.ANTHROPIC_API_KEY,
)

pipeline = Pipeline([
    transport.input(),       # Receive audio from browser via WebRTC
    stt,                     # whisper.cpp: audio → text (batch, ~300ms)
    openclaw_llm,            # OpenClaw: full personality + tools
    tts,                     # Piper: text → audio (batch, ~50ms)
    transport.output(),      # Send audio back to browser via WebRTC
])

# NOTE: LLMContextAggregatorPair is no longer needed — OpenClaw manages
# conversation history internally via the buddy-voice session.
```

**Keep the original direct-Anthropic pipeline code commented out** for easy A/B testing.

### 1.6 Verify OpenClaw integration

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy/server
uv run bot.py
```

Test:
1. Open `http://localhost:7860/client`
2. Say "What's the weather like?"
3. Buddy should respond with actual weather data (from OpenClaw's weather tool)
4. Check server logs for `🎤 → OpenClaw:` and `⏱️ LLM round-trip:` lines

---

## PHASE 2: PWA Client for iPhone

### 2.1 Client serving strategy

Pipecat's SmallWebRTCTransport dev runner serves a basic client at `/client`. For the PWA:

**Option A: Replace the built-in client** — serve custom HTML from Pipecat server
**Option B: Separate static file server** — Python `http.server` on port 8080

**Recommended:** Option A if Pipecat supports a custom static dir, else Option B.

### 2.2 Create `client/web/manifest.json`

```json
{
  "name": "Buddy",
  "short_name": "Buddy",
  "description": "Voice companion",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0a0a0a",
  "theme_color": "#0a0a0a",
  "orientation": "portrait",
  "icons": [
    { "src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

### 2.3 Create `client/web/sw.js`

```javascript
const CACHE_NAME = 'buddy-v1';
const STATIC_ASSETS = ['/', '/style.css', '/app.js', '/manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  if (event.request.url.includes('/api/') || event.request.url.includes('/offer')) {
    event.respondWith(fetch(event.request));
  } else {
    event.respondWith(
      caches.match(event.request).then(cached => cached || fetch(event.request))
    );
  }
});
```

### 2.4 Create `client/web/style.css`

```css
:root {
  --bg: #0a0a0a;
  --surface: #1a1a1a;
  --text: #e0e0e0;
  --text-dim: #666;
  --accent: #4fc3f7;
  --accent-glow: rgba(79, 195, 247, 0.3);
  --danger: #ef5350;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

html, body {
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', sans-serif;
  background: var(--bg);
  color: var(--text);
  overflow: hidden;
  -webkit-tap-highlight-color: transparent;
  user-select: none;
}

.app {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 2rem;
  position: relative;
}

.orb-container { position: relative; width: 200px; height: 200px; margin-bottom: 3rem; }

.orb {
  width: 100%; height: 100%; border-radius: 50%;
  background: radial-gradient(circle at 40% 40%, var(--accent), #1565c0);
  box-shadow: 0 0 60px var(--accent-glow);
  transition: transform 0.3s ease, box-shadow 0.3s ease;
}

.orb.idle { animation: pulse 3s ease-in-out infinite; }
.orb.listening { animation: listen 1s ease-in-out infinite; box-shadow: 0 0 80px var(--accent-glow), 0 0 120px var(--accent-glow); }
.orb.thinking { animation: think 0.8s linear infinite; }
.orb.speaking { animation: speak 0.4s ease-in-out infinite alternate; }

@keyframes pulse { 0%, 100% { transform: scale(1); opacity: 0.8; } 50% { transform: scale(1.05); opacity: 1; } }
@keyframes listen { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }
@keyframes think { 0% { transform: rotate(0deg) scale(0.95); } 100% { transform: rotate(360deg) scale(0.95); } }
@keyframes speak { 0% { transform: scale(1); } 100% { transform: scale(1.08); } }

.status {
  font-size: 1.1rem; color: var(--text-dim);
  text-transform: uppercase; letter-spacing: 0.15em;
  margin-bottom: 1rem; min-height: 1.5em;
}

.connect-btn {
  padding: 1rem 3rem; font-size: 1.1rem; font-weight: 600;
  border: 2px solid var(--accent); border-radius: 2rem;
  background: transparent; color: var(--accent); cursor: pointer;
  transition: all 0.2s;
}
.connect-btn:hover, .connect-btn:active { background: var(--accent); color: var(--bg); }
.connect-btn.connected { border-color: var(--danger); color: var(--danger); }
.connect-btn.connected:hover { background: var(--danger); color: white; }

.settings-btn {
  position: absolute; top: 1rem; right: 1rem;
  background: none; border: none; color: var(--text-dim);
  font-size: 1.5rem; cursor: pointer; padding: 0.5rem;
}

@supports (padding-top: env(safe-area-inset-top)) {
  .app {
    padding-top: calc(2rem + env(safe-area-inset-top));
    padding-bottom: calc(2rem + env(safe-area-inset-bottom));
  }
}
```

### 2.5 Create `client/web/index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, user-scalable=no">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="theme-color" content="#0a0a0a">
  <title>Buddy</title>
  <link rel="manifest" href="manifest.json">
  <link rel="apple-touch-icon" href="icons/icon-192.png">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div class="app">
    <button class="settings-btn" id="settingsBtn" title="Settings">⚙️</button>
    <div class="orb-container"><div class="orb idle" id="orb"></div></div>
    <div class="status" id="status">tap to connect</div>
    <button class="connect-btn" id="connectBtn">Connect</button>
  </div>
  <script src="app.js"></script>
</body>
</html>
```

### 2.6 Create `client/web/app.js`

```javascript
/**
 * Buddy PWA Client
 *
 * Connects to Buddy server via WebRTC.
 * Pipecat's SmallWebRTCTransport exposes /offer for signaling.
 */

const STATE = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
};

class BuddyClient {
  constructor() {
    this.orb = document.getElementById('orb');
    this.status = document.getElementById('status');
    this.connectBtn = document.getElementById('connectBtn');
    this.state = STATE.DISCONNECTED;
    this.pc = null;
    this.localStream = null;
    this.serverUrl = localStorage.getItem('buddy_server_url') || window.location.origin;

    this.connectBtn.addEventListener('click', () => this._toggle());
    document.getElementById('settingsBtn').addEventListener('click', () => this._showSettings());

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    }
  }

  _setState(state) {
    this.state = state;
    this.orb.className = 'orb ' + ({
      disconnected: 'idle', connecting: 'thinking', connected: 'idle',
    }[state] || 'idle');
    this.status.textContent = ({
      disconnected: 'tap to connect', connecting: 'connecting...', connected: 'connected',
    }[state] || '');
    if (state === STATE.DISCONNECTED) {
      this.connectBtn.textContent = 'Connect';
      this.connectBtn.classList.remove('connected');
    } else {
      this.connectBtn.textContent = 'Disconnect';
      this.connectBtn.classList.add('connected');
    }
  }

  async _toggle() {
    if (this.state === STATE.DISCONNECTED) await this._connect();
    else this._disconnect();
  }

  async _connect() {
    this._setState(STATE.CONNECTING);
    try {
      this.localStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
      });
      this.pc = new RTCPeerConnection({ iceServers: [] });
      this.localStream.getTracks().forEach(t => this.pc.addTrack(t, this.localStream));
      this.pc.addTransceiver('audio', { direction: 'recvonly' });

      this.pc.ontrack = (event) => {
        const audio = new Audio();
        audio.srcObject = event.streams[0];
        audio.play().catch(() => {
          document.addEventListener('click', () => audio.play(), { once: true });
        });
      };
      this.pc.onconnectionstatechange = () => {
        if (this.pc.connectionState === 'connected') this._setState(STATE.CONNECTED);
        else if (['disconnected', 'failed', 'closed'].includes(this.pc.connectionState))
          this._setState(STATE.DISCONNECTED);
      };

      const offer = await this.pc.createOffer();
      await this.pc.setLocalDescription(offer);
      await new Promise(r => {
        if (this.pc.iceGatheringState === 'complete') r();
        else { this.pc.onicegatheringstatechange = () => { if (this.pc.iceGatheringState === 'complete') r(); }; setTimeout(r, 3000); }
      });

      const resp = await fetch(`${this.serverUrl}/offer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp: this.pc.localDescription.sdp, type: this.pc.localDescription.type }),
      });
      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      await this.pc.setRemoteDescription(await resp.json());
      this._setState(STATE.CONNECTED);
    } catch (err) {
      console.error('Connection failed:', err);
      this._disconnect();
      this.status.textContent = `Error: ${err.message}`;
    }
  }

  _disconnect() {
    if (this.pc) { this.pc.close(); this.pc = null; }
    if (this.localStream) { this.localStream.getTracks().forEach(t => t.stop()); this.localStream = null; }
    this._setState(STATE.DISCONNECTED);
  }

  _showSettings() {
    const url = prompt('Server URL:', this.serverUrl);
    if (url !== null) { localStorage.setItem('buddy_server_url', url); this.serverUrl = url; if (this.state !== STATE.DISCONNECTED) this._disconnect(); }
  }
}

const buddy = new BuddyClient();
```

### 2.7 Create PWA icons

```bash
mkdir -p client/web/icons
# Generate placeholder icons
python3 -c "
from PIL import Image, ImageDraw
for size in [192, 512]:
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    m = size // 10
    draw.ellipse([m, m, size-m, size-m], fill='#4fc3f7')
    img.save(f'client/web/icons/icon-{size}.png')
print('Icons generated')
"
```

If Pillow unavailable, any small PNG works.

### 2.8 Test from iPhone

1. Mac Mini and iPhone on same WiFi
2. `ipconfig getifaddr en0` for Mac Mini IP
3. iPhone Safari: `http://<ip>:7860/client` (or :8080 for separate server)
4. Tap Connect, allow microphone, speak
5. Add to Home Screen: Share → Add to Home Screen

---

## PHASE 3: Testing + Hardening

### 3.1 Test matrix

| Test | Device | Expected |
|------|--------|----------|
| Basic conversation | Mac browser | Works, <2s latency |
| Basic conversation | iPhone Safari | Works, <2.5s latency |
| Calendar query | Either | Real calendar data via OpenClaw |
| Weather query | Either | Real weather data |
| Memory recall | Mac | Recalls last message |
| Reconnect | iPhone | Session context preserved |
| OpenClaw down | Mac | Falls back to direct Anthropic |
| Long conversation | Mac | Stable 10+ turns |
| Interruption | Either | Buddy stops, listens |
| PWA home screen | iPhone | Full-screen, works |

### 3.2 Validation checklist

- [ ] OpenClaw integration works (personality, memory, tools)
- [ ] Voice tool access works (calendar, weather minimum)
- [ ] Sentence-by-sentence streaming to Piper TTS
- [ ] iPhone PWA works from home screen
- [ ] Session persists across reconnects
- [ ] Latency with OpenClaw <2.5s total
- [ ] Graceful fallback when OpenClaw is down
- [ ] All files committed and pushed

---

## PHASE 4: Git

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy
git add -A
git commit -m "Week 2: OpenClaw integration + PWA client

- OpenClaw LLM processor (personality, memory, tools)
- Sentence-by-sentence streaming for fast Piper TTS
- Direct Anthropic fallback when OpenClaw unavailable
- Per-turn latency tracking
- PWA client for iPhone (installable, full-screen)
- Dark orb UI with state animations"
git push origin main
```

---

## KNOWN ISSUES & EDGE CASES

1. **OpenClaw HTTP API**: The gateway may not expose REST at `/api/sessions/send`. Check actual routes or use WebSocket.

2. **iPhone mic over HTTP**: Safari may block mic on non-HTTPS non-localhost. Workarounds: access via `*.local` Bonjour name, use mkcert for self-signed cert, or use Tailscale.

3. **WebRTC offer format**: Pipecat's `/offer` endpoint may expect different JSON shape. Check the built-in client source.

4. **Pipecat frame API changes**: `TranscriptionFrame` import location may vary. Search the installed package.

---

## WHAT COMES AFTER WEEK 2

- **Week 3:** iPad home station, "Hey Buddy" wake word, multi-device handoff
- **Week 4:** Cloud upgrade option (Deepgram + ElevenLabs), monitoring, launchd service
