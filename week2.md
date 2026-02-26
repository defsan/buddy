# Week 2: OpenClaw Integration + iOS Client

**Goal:** Wire Buddy into OpenClaw for personality, memory, and tool access. Build a native-feeling iOS client so you can talk to Buddy from your iPhone.

**Success criteria:** Ask Buddy "What's on my calendar today?" from your iPhone and hear a real answer sourced from your Google Calendar via OpenClaw tools.

---

## Day 1: OpenClaw LLM Processor

### Targets
- [ ] Build a custom Pipecat `FrameProcessor` that routes LLM calls through OpenClaw instead of direct Anthropic
- [ ] Buddy responses now reflect SOUL.md personality and MEMORY.md context
- [ ] Conversation history persists across reconnects (OpenClaw session)

### Steps

**1. Understand the integration point**
Currently `bot.py` uses `AnthropicLLMService` directly — that means:
- No access to SOUL.md, USER.md, MEMORY.md
- No tools (calendar, email, lights, home assistant)
- No conversation persistence
- No personality beyond the hardcoded system prompt

We need a custom processor that sends transcribed text to OpenClaw's gateway and streams back the response.

**2. OpenClaw gateway protocol**
OpenClaw gateway exposes a WebSocket API at `ws://127.0.0.1:18789`. The integration approach:

**Option A: sessions_send via HTTP (simpler, recommended for day 1)**
- Create a dedicated "buddy" session/agent in OpenClaw
- Send user transcriptions as messages via the internal API
- Receive response text, pass to TTS

**Option B: Direct gateway WebSocket (tighter, lower latency)**
- Connect directly to the gateway WebSocket
- Speak the internal session protocol
- Can stream tokens for faster TTS start
- More complex but better latency

Start with Option A today, migrate to Option B on Day 2 if latency is an issue.

**3. Create `server/openclaw_llm.py`**

```python
# openclaw_llm.py — OpenClaw LLM processor for Pipecat
#
# Sends user transcriptions to OpenClaw's session API,
# receives response text, and emits TextFrames for TTS.

import asyncio
import aiohttp
from pipecat.frames.frames import (
    TextFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

class OpenClawLLMProcessor(FrameProcessor):
    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:18789",
        session_label: str = "buddy-voice",
        agent_id: str | None = None,
    ):
        super().__init__()
        self._gateway_url = gateway_url
        self._session_label = session_label
        self._agent_id = agent_id
        self._pending_text = ""
        self._is_speaking = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._is_speaking = True
            await self.push_frame(frame, direction)

        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._is_speaking = False
            await self.push_frame(frame, direction)

        elif isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                # Send to OpenClaw and stream response
                await self._process_with_openclaw(frame.text)

        else:
            await self.push_frame(frame, direction)

    async def _process_with_openclaw(self, text: str):
        """Send text to OpenClaw and emit response as TextFrames."""
        await self.push_frame(LLMFullResponseStartFrame())

        try:
            # Use sessions_send or a direct HTTP endpoint
            # Exact API shape depends on OpenClaw's internal HTTP API
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._gateway_url}/api/sessions/send",
                    json={
                        "message": text,
                        "label": self._session_label,
                        "agentId": self._agent_id,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                    response_text = result.get("response", "")

                    if response_text:
                        await self.push_frame(TextFrame(text=response_text))

        except Exception as e:
            logger.error(f"OpenClaw error: {e}")
            await self.push_frame(
                TextFrame(text="Sorry, I had trouble reaching my brain. Try again?")
            )

        await self.push_frame(LLMFullResponseEndFrame())
```

**4. Update `bot.py` pipeline**
Replace `AnthropicLLMService` + context aggregators with `OpenClawLLMProcessor`:

```python
# Old:
pipeline = Pipeline([
    transport.input(),
    stt,
    user_aggregator,
    llm,
    tts,
    transport.output(),
    assistant_aggregator,
])

# New:
from openclaw_llm import OpenClawLLMProcessor

openclaw = OpenClawLLMProcessor(
    gateway_url="http://127.0.0.1:18789",
    session_label="buddy-voice",
)

pipeline = Pipeline([
    transport.input(),
    stt,
    openclaw,
    tts,
    transport.output(),
])
```

**5. Create a buddy agent/session in OpenClaw**
Either use the main agent with a "buddy-voice" session label, or create a dedicated agent with a voice-optimized system prompt.

### Deliverable
Ask Buddy anything and it uses OpenClaw's full context — personality, memory, the works. "What's the weather?" actually checks the weather tool.

---

## Day 2: Streaming Response + Latency Optimization

### Targets
- [ ] Stream LLM tokens from OpenClaw to TTS (don't wait for full response)
- [ ] Measure and log per-component latency
- [ ] Target: first audio chunk <1.5s after end-of-speech

### Steps

**1. Token streaming from OpenClaw**
The biggest latency win is streaming: start TTS on the first sentence while the LLM is still generating the rest.

Strategy:
- Buffer incoming tokens until you hit a sentence boundary (`.`, `!`, `?`, or `\n`)
- Emit each complete sentence as a `TextFrame` immediately
- TTS starts generating audio for sentence 1 while LLM is generating sentence 2

```python
async def _stream_from_openclaw(self, text: str):
    """Stream response sentence-by-sentence for faster TTS."""
    buffer = ""
    async for chunk in self._openclaw_stream(text):
        buffer += chunk
        # Check for sentence boundaries
        while any(p in buffer for p in ['. ', '! ', '? ', '.\n', '!\n', '?\n']):
            # Find first sentence end
            for punct in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
                idx = buffer.find(punct)
                if idx >= 0:
                    sentence = buffer[:idx + 1].strip()
                    buffer = buffer[idx + 2:].lstrip()
                    if sentence:
                        await self.push_frame(TextFrame(text=sentence))
                    break

    # Emit remaining buffer
    if buffer.strip():
        await self.push_frame(TextFrame(text=buffer.strip()))
```

**2. Add latency metrics**
```python
import time

class LatencyTracker:
    def __init__(self):
        self.speech_end = 0
        self.stt_done = 0
        self.llm_first_token = 0
        self.tts_first_chunk = 0
        self.audio_played = 0

    def report(self):
        return {
            "stt": f"{(self.stt_done - self.speech_end)*1000:.0f}ms",
            "llm_ttft": f"{(self.llm_first_token - self.stt_done)*1000:.0f}ms",
            "tts_ttfa": f"{(self.tts_first_chunk - self.llm_first_token)*1000:.0f}ms",
            "total": f"{(self.audio_played - self.speech_end)*1000:.0f}ms",
        }
```

Log this after every exchange. Identify bottlenecks.

**3. Optimization levers**
- Deepgram `endpointing` param: reduce from 1000ms to 500-700ms (faster end-of-speech detection)
- ElevenLabs `optimize_streaming_latency`: set to 3 or 4 (trades quality for speed)
- Claude model: `claude-sonnet-4-5` is fast; `haiku` is faster if quality is acceptable
- Local Qwen fallback for simple queries: "what time is it?" doesn't need Claude

### Deliverable
Latency metrics logged per turn. Total time consistently <1.5s. Streaming TTS starts before full LLM response is ready.

---

## Day 3: Conversation Memory + Session Persistence

### Targets
- [ ] Buddy remembers what you talked about earlier today
- [ ] Reconnecting from browser doesn't reset conversation
- [ ] Long-term memory updates from voice conversations

### Steps

**1. Session persistence**
Use OpenClaw's session system — the buddy-voice session should persist across WebRTC reconnects:
- On client connect: resume existing session (don't create new)
- On client disconnect: session stays alive
- On reconnect: conversation history is intact

**2. Memory integration**
OpenClaw already handles MEMORY.md. But voice conversations should also:
- Be summarizable (end-of-day: "Buddy, what did we talk about today?")
- Allow explicit memory saves ("Remember that I have a dentist appointment Friday")
- Optionally log voice transcripts to daily memory files

**3. Conversation-aware responses**
Test scenarios:
- "What did I just say?" → should recall last user message
- "Like I mentioned earlier..." → should have context
- Reconnect after 5 min → should remember the conversation

### Deliverable
Buddy has a memory. Conversations feel continuous, not amnesiac.

---

## Day 4: Tool Access via OpenClaw

### Targets
- [ ] "What's on my calendar?" → reads Google Calendar via `gog` skill
- [ ] "Turn off the lights" → controls Home Assistant / Hue
- [ ] "Check my email" → scans Gmail via `gog` skill
- [ ] "What's the weather?" → uses weather skill

### Steps

**1. Voice-optimized tool responses**
Tools return structured data. Need to transform for speech:
- Calendar: "You have three things today. First, standup at 10 AM. Then lunch with Mike at noon. And a dentist appointment at 3."
- Weather: "It's 72 and sunny in LA right now. Should stay nice all day."
- Email: "You have two new emails. One from Sarah about the Q3 report, and one from GitHub about a failing CI run."

**2. System prompt additions for tool usage**
Add to the voice system prompt:
```
When using tools, summarize results conversationally. 
Don't read out URLs, IDs, or technical details unless asked.
For calendar events: mention time, title, and who's involved.
For emails: mention sender, subject, urgency. Don't read full bodies.
For weather: temperature, conditions, and any alerts.
```

**3. Action confirmation**
For destructive/external actions (sending email, turning off lights), Buddy should confirm:
- "Turn off the lights" → "Done, lights are off."
- "Send an email to Mike" → "What should I say to Mike?" (don't just send empty email)
- "Delete that meeting" → "You want me to cancel the 3 PM dentist? Just confirming."

**4. Test matrix**

| Command | Expected Tool | Expected Response |
|---------|--------------|-------------------|
| "What's on my calendar?" | gog calendar | Natural summary of today's events |
| "Turn off the living room lights" | openhue | Confirmation |
| "Check my email" | gog gmail | Summary of recent unread |
| "What's the weather?" | weather | Current conditions + forecast |
| "Set a reminder for 5 PM" | cron | Confirmation with time |
| "What's Bitcoin at?" | web_search | Current price |

### Deliverable
Buddy can actually do things, not just talk. Voice-triggered tools feel natural and responsive.

---

## Day 5: iOS Client (PWA)

### Targets
- [ ] Progressive Web App that works from iPhone home screen
- [ ] Full-screen, no browser chrome
- [ ] Push-to-talk and/or always-listening modes
- [ ] Works over LAN (same WiFi) and optionally via Tailscale

### Steps

**1. PWA manifest and service worker**
Create a proper PWA so it can be added to iPhone home screen:

```
client/
├── index.html          # Main app shell
├── app.js              # WebRTC + audio logic
├── style.css           # Mobile-optimized UI
├── manifest.json       # PWA manifest
├── sw.js               # Service worker (offline shell)
└── icons/
    ├── icon-192.png
    └── icon-512.png
```

**2. UI design — mobile-first**
Keep it dead simple:
- Full screen, dark background
- Animated orb/waveform in center showing Buddy's state:
  - Idle: gentle pulse
  - Listening: expanding rings
  - Thinking: spinning/morphing
  - Speaking: waveform animation
- Status text below: "Listening...", "Thinking...", "Speaking..."
- Settings gear icon: voice selection, server URL
- No text transcript by default (it's voice-first)
- Optional: small transcript toggle for noisy environments

**3. Audio handling on iOS**
iOS Safari quirks:
- Requires user gesture to start AudioContext
- WebRTC mic works on Safari iOS 14.5+
- Need to handle audio session interruptions (phone call, notification)
- Keep audio session active to prevent mic dropout

```javascript
// Must be called from user gesture (button tap)
async function startAudio() {
    const audioContext = new AudioContext();
    await audioContext.resume(); // Required on iOS
    
    const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        }
    });
    
    // Connect to WebRTC...
}
```

**4. Connection modes**
- **LAN mode** (default): connect to `http://<mac-mini-ip>:7860`
- **Tailscale mode**: connect via Tailscale IP if on different network
- Auto-detect: try LAN first, fall back to Tailscale
- Server URL stored in localStorage, configurable in settings

**5. Home screen installation**
Add a banner/prompt: "Add Buddy to your home screen for the best experience"

### Deliverable
Buddy on your iPhone home screen, looking like a native app. Tap, talk, hear response.

---

## Day 6: Mac Client (Menu Bar)

### Targets
- [ ] Menu bar app or keyboard shortcut to activate Buddy on Mac
- [ ] Works alongside existing OpenClaw Mac overlay
- [ ] Option: extend OpenClaw's existing voice-overlay instead of separate app

### Steps

**1. Option A: Extend OpenClaw voice overlay**
OpenClaw already has `voice-overlay.md` and `voicewake.md` docs for Mac. If the existing overlay can connect to the Buddy WebRTC server, this is the simplest path:
- Check if OpenClaw Mac app supports custom voice endpoints
- If yes: configure it to point at `ws://localhost:7860`
- If no: build standalone

**2. Option B: Browser bookmark + global shortcut**
Simplest Mac "client":
- Chrome/Arc app mode: `chrome --app=http://localhost:7860/client`
- Assign global keyboard shortcut (e.g., `⌥Space`) via Raycast or Automator
- Opens/focuses the Buddy tab

**3. Option C: SwiftUI menu bar app (future)**
Full native Mac client:
- Lives in menu bar
- Global hotkey (e.g., `⌥B`) toggles listening
- Status indicator: idle/listening/speaking
- This is a Week 4+ nice-to-have

### Deliverable
Quick way to activate Buddy from Mac. Keyboard shortcut or menu bar click → talking to Buddy.

---

## Day 7: Integration Testing + Polish

### Targets
- [ ] Test all tools from voice on both Mac and iPhone
- [ ] Fix any latency regressions from OpenClaw integration
- [ ] Error handling: graceful degradation if OpenClaw is down
- [ ] Session cleanup: don't leak sessions or connections

### Steps

**1. End-to-end test matrix**

| Test | Device | Expected |
|------|--------|----------|
| Basic conversation | Mac | <1.5s response |
| Basic conversation | iPhone | <2s response |
| Calendar query | iPhone | Real calendar data |
| Light control | Mac | Lights actually change |
| Memory recall | Mac | "What did I say 5 min ago?" works |
| Reconnect | iPhone | Lock phone, unlock, still works |
| Long conversation (10+ turns) | Mac | No memory issues, stays coherent |
| Interruption | Both | Clean cutoff and new response |

**2. Error handling**
- OpenClaw gateway down → fall back to direct Anthropic (Week 1 mode)
- Deepgram API error → graceful error message ("I didn't catch that")
- ElevenLabs API error → fall back to Piper TTS if available
- Network timeout → retry once, then inform user

**3. Cleanup**
- WebRTC connections properly closed on disconnect
- No orphaned OpenClaw sessions
- Memory/CPU stable over hours of use

### Deliverable
Solid, tested voice companion accessible from Mac and iPhone with full tool access.

---

## Week 2 Exit Criteria

| # | Criteria | Target |
|---|----------|--------|
| 1 | OpenClaw integration working | Personality, memory, tools |
| 2 | Tool access from voice | Calendar, weather, lights minimum |
| 3 | Response streaming | TTS starts before full LLM response |
| 4 | iOS client (PWA) | Works from iPhone home screen |
| 5 | Mac activation | Quick shortcut or menu bar |
| 6 | Session persistence | Reconnect without losing context |
| 7 | Latency with OpenClaw | <2s total (was <1.5s direct) |
| 8 | Error handling | Graceful degradation on failures |

## What's NOT in Week 2
- ❌ Wake word detection (Week 3)
- ❌ Dedicated home device setup (Week 3)
- ❌ Local fallback chain (Week 4)
- ❌ Voice cloning (nice-to-have)
- ❌ Multi-room audio (future)
- ❌ Native iOS app (PWA is sufficient for now)

---

## Quick Reference
- **OpenClaw gateway:** `ws://127.0.0.1:18789`
- **Buddy server:** `http://0.0.0.0:7860`
- **OpenClaw docs (voice):** `docs/platforms/mac/voice-overlay.md`, `docs/platforms/mac/voicewake.md`
- **Pipecat custom processors:** https://docs.pipecat.ai/docs/category/processors
- **ElevenLabs streaming:** https://elevenlabs.io/docs/api-reference/text-to-speech-stream
