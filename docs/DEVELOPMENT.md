# Development Guide

## Setup

```bash
# Clone repository
git clone https://github.com/yourusername/jarvis-ai-agent
cd jarvis-ai-agent

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Ollama (for local LLM)
# Visit https://ollama.com/download
ollama serve
ollama pull llama3.2

# Run JARVIS
python main.py
```

## Project Structure

```
jarvis-ai-agent/
├── main.py                 # Entry point
├── core/                   # Core modules
│   ├── brain.py           # LLM interface
│   ├── conversation.py    # Conversation manager
│   ├── events.py          # Event hub
│   ├── settings.py        # Configuration
│   ├── history.py         # History store
│   ├── intent.py          # Intent classification
│   ├── planner.py         # Action planning
│   ├── memory.py          # Memory systems
│   ├── neural_memory.py   # Vector memory
│   └── knowledge_graph.py # Knowledge graph
├── voice/                 # Voice I/O
│   ├── listener.py        # Speech-to-text
│   ├── speaker.py         # Text-to-speech
│   ├── interpretation.py  # Transcript interpretation
│   └── text.py            # Text cleaning
├── interface/             # Web interface
│   ├── server.py          # HTTP/SSE server
│   ├── index.html         # Main HUD
│   ├── style.css          # Styles
│   ├── app.js             # Frontend logic
│   └── events.js          # SSE client (legacy)
├── tools/                 # System tools
│   ├── screen.py          # Screen capture
│   ├── files.py           # File operations
│   ├── browser.py         # Browser control
│   ├── remote_control.py  # Mouse/keyboard
│   └── ...
├── vision/                # Computer vision
│   ├── analyze.py         # Screen analysis
│   └── neural_vision.py   # UI grounding
├── tests/                 # Test suite
├── config/                # Configuration
└── docs/                  # Documentation
```

## Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_action_pipeline.py -v

# Run with coverage
pytest --cov=core --cov=voice --cov=interface
```

## Code Style

- Type hints required for public APIs
- Docstrings for all public functions/classes
- Line length: 100 characters
- Run `ruff check .` before committing

## Adding New Tools

1. Create tool module in `tools/`
2. Add function with clear signature and docstring
3. Register in `core/planner.py` capabilities
4. Add tests in `tests/`

## Voice Development

- Listener uses persistent `sd.InputStream`
- Speaker uses SAPI5 (Windows) with chunked queue
- Barge-in uses WebRTC VAD when available
- Whisper fallback for offline STT

## Web Development

- Server: stdlib `http.server` with SSE
- Frontend: Vanilla ES modules, no build step
- CSS: Custom properties, no framework
- Icons: Inline SVG

## Debugging

```bash
# Verbose logging
python main.py -v

# Test voice only
python -m voice.listener

# Test TTS
python -m voice.speaker "Hello world"

# Check server health
curl http://localhost:8765/api/health
```

## Common Issues

| Issue | Solution |
|-------|----------|
| Mic not working | Check Windows privacy settings, run as admin |
| Ollama connection failed | Ensure `ollama serve` is running |
| TTS not speaking | Check SAPI5 voices, try `speaker.speak()` directly |
| SSE disconnects | Check firewall, try localhost vs LAN IP |
| High CPU | Reduce poll interval, disable auto-listen |