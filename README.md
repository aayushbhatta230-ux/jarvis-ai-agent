<div align="center">

# ⚡ JARVIS
### Autonomous Local-First AI Computer Companion & Remote Control Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI: Passing](https://img.shields.io/badge/CI-Passing-10b981?style=flat&logo=githubactions&logoColor=white)](https://github.com/aayushbhatta230-ux/jarvis-ai-agent/actions)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2011%20%7C%2010-0078D6.svg)](https://microsoft.com/windows)
[![LLM: Ollama](https://img.shields.io/badge/LLM-Llama%203.2%20(Local)-orange.svg)](https://ollama.com)
[![Tunnel: Cloudflare](https://img.shields.io/badge/Tunnel-Cloudflare%20Zero--Trust-F38020.svg)](https://cloudflare.com)

*JARVIS is an autonomous, privacy-first computer companion built for Windows. It fuses local LLM reasoning with real-world OS automation, voice perception, an interactive 3D WebGL interface, and a seamless iPhone/mobile remote companion.*

---

</div>

## 🌟 Key Highlights

- 🧠 **100% Local Intelligence**: Powered by Ollama (`llama3.2`) and Neural Vector Memory — your conversations, code, and system commands never leave your personal machine.
- ⏰ **Smart Alarms & Countdown Timers**: Background thread engine managing precision timers, reminders, and voice notifications.
- 🐙 **Git Intelligence & Remote Sync**: Conversational git status auditor, commit log explorer, and automated one-shot commit/push pipeline.
- 🔍 **Resilient Web Researcher**: DuckDuckGo instant knowledge retrieval with automatic offline LLM fallback.
- 📋 **Windows Clipboard & Code Intelligence**: Native OS clipboard reading/writing and deep workspace source code inspection.
- 📱 **Mobile Remote & PWA Companion**:
  - Full-screen real-time **laptop screen mirroring** streamed straight to your phone.
  - **Remote Touchpad & Navigation**: Scroll, tap, drag, select text, and send keystrokes from your phone.
  - Dedicated **iPhone 15 & Safari PWA** optimization with Add-to-Home-Screen app mode.
- 🎙️ **Natural Voice Interaction**:
  - High-fidelity British Neural voice synthesis (`en-GB-RyanNeural`) powered by Edge TTS.
  - Dual-mic anti-echo architecture: your PC hardware speakers stay muted during remote mobile sessions.
  - Sub-second streaming speech response and natural conversational barge-in.
- 🌐 **Instant Cloudflare Quick Tunnel**:
  - Unlimited bandwidth tunneling with zero monthly quotas and no login required.
  - Remote access anywhere in the world with automatic HTTPS provisioning.
- 🎵 **Autonomous Media & YouTube Music Agency**:
  - Deep song resolution via `yt_dlp` without relying on search listings.
  - Autonomous cursor gliding and physical click execution on player controls.
- 🛡️ **Gated Safety Execution Model**:
  - 40+ capabilities categorized by risk levels (`low`, `medium`, `high`).
  - Gated confirmation before any destructive file or system modifications.
- ✨ **Celestial Hologram Visualizer**:
  - Interactive 3D WebGL particle hologram that reacts dynamically to agent thinking and speech states.

---

## 🏛️ System Architecture

```text
               +---------------------------------------------------+
               |             Remote Companion (iPhone/Web)         |
               |   [Screen Mirror] [Touchpad Control] [Neural TTS]  |
               +-------------------------+-------------------------+
                                         |
                       Cloudflare HTTPS / Local LAN HTTP
                                         |
                                         v
+-----------------------------------------------------------------------------+
|                           JARVIS Core Orchestrator                          |
|                                                                             |
|  [Voice Stream]  -->  [Intent Engine]  -->  [Task Planner]  -->  [Ollama]   |
|        ^                     |                      |                 |     |
|        |                     v                      v                 v     |
|  [Audio I/O]          [Local Memory]         [Capabilities]     [Llama 3.2] |
|                              |                      |                       |
+------------------------------|----------------------|-----------------------+
                               |                      |
                               v                      v
               +-----------------------------------------------+
               |                 Host OS (Windows)             |
               |  [Mouse/Keyboard]  [Chrome/Media]  [Terminal] |
               |  [Screen Capture]  [File System]   [Registry] |
               +-----------------------------------------------+
```

---

## 🚀 Quickstart

### 1. Prerequisites
- **Operating System**: Windows 10 or Windows 11 (64-bit)
- **Python**: Version 3.10, 3.11, or 3.12
- **Ollama**: Download and install from [ollama.com](https://ollama.com)

### 2. Installation
```powershell
# Clone the repository
git clone https://github.com/aayushbhatta230-ux/jarvis-companion.git
cd jarvis-companion

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Pull the lightweight local language model
ollama pull llama3.2
```

### 3. Launching JARVIS
```powershell
python main.py
```

Once running:
- **Local PC Interface**: Open `http://127.0.0.1:8765/` in your browser.
- **iPhone / Mobile Companion**: JARVIS prints your local network IP and a live Cloudflare Tunnel URL in the terminal (e.g. `https://xxxx.trycloudflare.com`). Scan the QR code or open the link on your mobile browser!

---

## 💬 Natural Language Examples

JARVIS understands messy, human conversational language without rigid syntax:

| You Say | JARVIS Does |
|---------|-------------|
| *"Play Starboy on YouTube Music"* | Resolves official track ID, opens Chrome, glides cursor to play button, and begins playback |
| *"What's on my screen right now?"* | Captures display buffer, analyzes open windows, and describes current activity |
| *"Find all PDF notes from last week in Downloads"* | Performs filesystem scan, indexes relevant documents, and provides an organized summary |
| *"Switch to VS Code and show me recent commits"* | Focuses editor, executes git log query via terminal capability, and displays results |
| *"Turn down the volume and record my screen for 30s"* | Controls hardware volume and triggers high-fps background screen recording |

---

## 🛠️ Capability Registry

All agent actions are governed by declarative capabilities in `core/capabilities.py`:

| Category | Available Tools | Safety Level |
|----------|-----------------|:------------:|
| **Media & Audio** | `play_music`, `stop_music`, `pause_music`, `volume_control` | Low |
| **Browser & Web** | `open_url`, `search_web`, `resolve_site` | Low |
| **System & Desktop** | `launch`, `close_app`, `focus_window`, `get_system_info` | Low |
| **Screen Perception** | `capture_screen`, `start_recording`, `stop_recording`, `screen_stream` | Low |
| **File Automation** | `search_files`, `read_file`, `create_folder`, `organize_files` | Low / Med |
| **Destructive Files** | `delete_file`, `overwrite_file`, `rename_file` | **High (Confirmed)** |
| **Terminal & Shell** | `execute_command`, `run_python` | **High (Confirmed)** |

---

## 🔒 Privacy & Security

1. **Zero Data Telemetry**: Your queries, audio input, system files, and screen frames are never uploaded to any remote server or third-party AI provider.
2. **Local Memory Isolation**: Preferences and conversation history are persisted in local JSON stores that are never committed to version control.
3. **Hardware Isolation**: PC microphones and speakers automatically coordinate to prevent audio feedback or conflicting playback during remote turns.

---

## 🤝 Contributing

Contributions are welcomed! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for local development workflows and pull request guidelines.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).