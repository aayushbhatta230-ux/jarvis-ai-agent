# Architecture Overview

## System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        JARVIS System                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Web UI     │    │  Voice I/O   │    │   Core       │      │
│  │  (interface) │    │ (listener,   │    │ (conversation│      │
│  │              │    │  speaker)    │    │  manager)    │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         └───────────────────┼───────────────────┘               │
│                             ▼                                   │
│                  ┌──────────────────────┐                        │
│                  │    Event Hub         │                        │
│                  │  (pub/sub messages)  │                        │
│                  └──────────┬───────────┘                        │
│                             │                                     │
│         ┌───────────────────┼───────────────────┐               │
│         ▼                   ▼                   ▼                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │   Brain     │    │   Tools     │    │   Memory    │         │
│  │  (Ollama)   │    │  (screen,   │    │  (neural,   │         │
│  │             │    │   files,    │    │   short-term)│         │
│  └─────────────┘    │   system)   │    └─────────────┘         │
│                     └─────────────┘                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

1. **Voice Input** → Listener → Transcript → Conversation Manager
2. **Text Input** (Web UI) → HTTP API → Conversation Manager
3. **Conversation Manager** → Intent Classification → Action Planning
4. **Action Planner** → Tool Execution → Result
5. **Result** → Brain (LLM) → Response
6. **Response** → Speaker (TTS) + Web UI (SSE)

## Thread Model

- **Main Thread**: Web server, SSE connections
- **Worker Thread**: Conversation manager loop
- **Audio Thread**: Persistent microphone capture
- **TTS Thread**: Speech synthesis queue
- **Barge-in Monitor**: Interrupt detection during TTS

## Key Design Decisions

- **Singleton Audio Capture**: Single `sd.InputStream` reused across turns
- **Event-Driven**: All components communicate via EventHub
- **Graceful Degradation**: Voice optional, text-only fallback
- **Security**: No arbitrary code execution from web UI
- **Mobile-First**: Responsive UI, touch-friendly, PWA-ready