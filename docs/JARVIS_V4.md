# JARVIS v4 — Installation, Usage & Architecture

JARVIS v4 is an autonomous Windows desktop assistant: a holographic humanoid interface, a voice pipeline
built around your headset with real-time recognition of *your* voice, an agent that plans → acts → verifies with
88 tools, persistent memory, screen understanding, a coding agent, research, and a persistent scheduler.

The new code lives in the `jarvis/` package. The previous app is preserved as `legacy_main.py` (+ `core/`,
`interface/`, `voice/`, `tools/` …) but is no longer started by default — see *Security* below.

---

## 1. Install

Prerequisites: Windows 10/11, Python 3.11–3.13, [Ollama](https://ollama.com), [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)
(installed at `C:\Program Files\Tesseract-OCR`).

```powershell
cd C:\Users\joshi\JARVIS
powershell -ExecutionPolicy Bypass -File scripts\install.ps1            # deps, voice models, Ollama models, shortcuts, self-test
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -AutoStart # …and start JARVIS when you sign in
```

The installer pulls these local models (≈7.5 GB):

| Role | Model | Why |
|---|---|---|
| chat / fast / agent / code | `qwen3:4b-instruct` | Fits the RTX 3050 6 GB fully in VRAM, native tool calling, no "thinking" delay |
| vision | `gemma3:4b` | Screenshot understanding |
| embeddings | `nomic-embed-text` | Semantic memory & code search |
| fallback | `llama3.2` | Second local option |

> Do **not** use the plain `qwen3:4b` tag: Ollama maps it to a thinking-only build that ignores tool calling.

## 2. Start

* Start Menu / Desktop shortcut **JARVIS**, or `JARVIS.bat`, or `.venv\Scripts\python.exe -m jarvis`
* `python -m jarvis --selftest` — diagnostics report (ready / degraded / unavailable)
* `python -m jarvis --cli` — text-only console (no GUI, no voice)
* `python -m jarvis --no-voice` — GUI without the microphone pipeline

Only one instance runs at a time; JARVIS lives in the system tray (closing the window hides it).

## 3. Using JARVIS

### Hotkeys (system-wide)
| Keys | Action |
|---|---|
| **Ctrl+Alt+J** | Show / hide JARVIS |
| **Ctrl+Alt+L** | Push-to-talk (also: click the hologram) |
| **Ctrl+Alt+K** | Type a command |
| **Ctrl+Alt+X** | Stop speaking and cancel running tasks (also **Esc** in the window, or say "stop") |

If a combination is taken by another app, JARVIS falls back to Ctrl+Shift+… and tells you.

### Views (buttons top-right)
* **◈ Holo** — command centre: the humanoid hologram on its projector, live system gauges, agent terminal,
  real-time logs, conversation, current task plan, system monitor (CPU/RAM/GPU/network sparklines, processes),
  shortcuts. Default view.
* **◉ Compact** — floating avatar only (stays on top).
* **☰ Conversation** — avatar + chat.
* **▦ Activity** — tabs: Activity (tasks, live steps, history, resume), Schedule (jobs, reminders, to-dos),
  Memory (search/edit/delete/export/import/backup/restore), System (performance, self-test, audit log), Settings.

The hologram reacts in real time: colour per state (listening cyan, thinking violet, executing blue, screen
analysis teal with a scan line, coding green, waiting amber, recovering red, done green flash), the pedestal
ripples with your microphone, and the jaw lip-syncs to JARVIS's voice. Pick the female or male bust (or the
abstract orb) in Settings → Screen & profile → Avatar.

### Voice + headset
* JARVIS automatically prefers a **headset/headphone microphone** (Bluetooth hands-free, USB or the wired jack)
  and **never uses the laptop's built-in microphone array** (Settings → Voice to change). When the headset is
  connected or disconnected, JARVIS switches devices automatically and tells you. Speech output goes to the
  headphones.
* Say **"Hey JARVIS"** (or press Ctrl+Alt+L), then your request. After JARVIS answers it keeps listening for a
  few seconds for a follow-up, without the wake word.
* Interrupt at any time by speaking (barge-in) or saying "Hey JARVIS" — speech stops within ~20 ms.
* **Recognise my voice** (Settings → Voice → *Enrol my voice*): read 5 short phrases. From then on every wake
  word, barge-in and command is verified against your voiceprint in real time (~80 ms). Modes: `enforce`
  (ignore other voices), `tag` (respond to anyone, show who spoke), `off`. The status line shows
  `👤 Aayush (0.86)` or `⛔ unknown speaker`.
* Approvals can be answered by voice ("yes" / "no").

### Things to try
* "Open Notepad and write a short haiku about the monsoon."
* "What's wrong with this window?" / "Explain the error on my screen."
* "In C:\Users\joshi\JARVIS, run the tests and fix anything that fails."
* "Research the best local speech-to-text models for Windows and write a report."
* "Remind me to call the bank tomorrow at 10." / "Every weekday at 9:00 check my disk space and warn me if C: is below 10 GB."
* "Remember that I prefer short answers." / "What do you remember about my projects?"
* "Resume" — continue the last interrupted task.

## 4. Architecture

```
jarvis/
  app.py              Jarvis core: wires services, conversation routing (chat ↔ agent), notifications
  __main__.py         entry point (GUI / --cli / --selftest), single-instance guard
  core/               config (JSON, typed defaults), db (SQLite + migrations + FTS5 + backup/recovery),
                      events (thread-safe bus), permissions (policy, approvals, audit, fs boundaries),
                      secrets (Windows Credential Manager), logging (JSON logs + secret redaction),
                      cancel (cancel/pause tokens), hardware (adapts defaults to RAM/VRAM)
  providers/          Ollama, OpenAI-compatible, Anthropic + ModelRouter (roles, capability probing,
                      health checks, cooldowns, rate limits, fallback, manual override)
  agent/engine.py     plan → act → observe → verify loop; checkpoints every step; retries, failure-streak and
                      repeated-call guards; ask_user; pause/resume/cancel; resume after restart
  tools/              88 tools with JSON schemas + permission categories:
                      files, shell (commands, python, background processes), desktop (apps, windows, UI
                      Automation, typing, keys, media, volume, settings), screen (vision, OCR, find/wait/compare),
                      code (repo overview, search, semantic index, git checkpoints, tests/build/lint,
                      Claude Code delegation), research (search, fetch, documents, downloads, cited reports),
                      productivity (reminders, scheduled tasks, to-dos, focus, drafts, briefing), memory, system
  memory/store.py     long-term memory (hybrid FTS5 + embedding retrieval, dedupe, importance/recency),
                      project memory, conversation log, export/import, legacy import
  services/           screen (capture, OCR, change detection, continuous observation, vision analysis),
                      scheduler (persistent jobs, catch-up after restart, run history), diagnostics (self-test,
                      performance)
  voice/              devices (headset-first selection, hot-plug), capture, vad (Silero), wakeword
                      (openWakeWord "hey jarvis" via ONNX), speaker_id (WeSpeaker ResNet34 voiceprints),
                      stt (faster-whisper + Google fallback), tts (Edge neural + SAPI fallback, sentence
                      streaming, instant stop), pipeline (state machine, barge-in, follow-up, enrolment)
  ui/                 main_window (modes, tray, hotkeys, approval cards), avatar (holographic humanoid),
                      dashboard (holo command centre), orb, chat, panels, theme
```

Threads: Qt UI thread · conversation worker · agent pool · tool pool · scheduler · voice capture/VAD ·
STT worker · TTS synth worker + audio callback · screen observer · hot-plug watcher. Everything reports through
the event bus; the UI receives events via a queued Qt signal so it never blocks.

Data lives in `data/` (git-ignored): `jarvis.db`, `config.json`, `logs/`, `backups/` (daily automatic),
`models/`, `screenshots/`, `snapshots/` (pre-edit file copies), `workspace/` (research reports, drafts, downloads).
Override with the `JARVIS_HOME` environment variable.

## 5. Configuration

Most settings are in the Settings tab; everything is in `data/config.json`:

* `models.roles.<role>` — ordered `provider:model` candidates; `models.override` forces one model.
* Cloud models: Settings → AI models → enable Anthropic / OpenAI-compatible and paste the key (stored in
  Windows Credential Manager as `JARVIS/ANTHROPIC_API_KEY`, `JARVIS/OPENAI_API_KEY`; never in config or logs).
  With a key, the agent and code roles automatically prefer Claude (`claude-sonnet-5-5` / `claude-opus-5-5`).
* Search: `research.search_provider` = `duckduckgo` (default, no key) | `brave` (`BRAVE_API_KEY`) | `tavily`
  (`TAVILY_API_KEY`).
* `permissions.<category>` = `allow` | `ask` | `deny` for `read`, `write`, `execute`, `system`, `external`,
  `destructive`; `permissions.allowed_roots` = folders JARVIS may write to (default: your home folder).
* `screen.observe` = `on_demand` | `continuous` (watches the active window, OCR on change, alerts on visible errors).

## 6. Security model

* Every tool call is validated, permission-checked and written to the audit log (System tab).
* Default policy: reads/writes/commands inside your home folder run autonomously; **system changes, external
  actions (git push, sending) and destructive actions (delete → Recycle Bin, kill process, force operations)
  require approval**. Shell commands are classified — `Remove-Item -Recurse`, `rm -rf`, `git push --force`,
  `reg delete`, `format`, `shutdown` … are escalated to *destructive* automatically.
* File edits keep snapshots (`restore_file`); git work can be checkpointed (`git_checkpoint`) and restored.
* Web pages, documents and OCR text are wrapped as untrusted data; the agent is instructed never to follow
  instructions inside them.
* Secrets: Credential Manager; log redaction for keys/tokens/passwords; the `remember` tool refuses secrets.
* **Legacy warning:** `legacy_main.py` (the old app) binds an unauthenticated web server on `0.0.0.0` and opens a
  public ngrok/Cloudflare tunnel exposing screen streaming and remote mouse/keyboard control. v4 does not start
  it. If you run `python main.py --legacy`, anyone with the tunnel URL can control the PC.

## 7. Tests

```powershell
.venv\Scripts\python.exe -m pytest tests\v4 -q                      # v4 unit/integration/GUI tests
.venv\Scripts\python.exe -m pytest tests\v4\test_e2e_smoke.py -q    # real-model end-to-end smoke (needs Ollama)
.venv\Scripts\python.exe -m pytest tests -q                         # everything incl. legacy tests
```

Coverage: config/db (migrations, BOM, corruption recovery, backup/restore), events, cancellation, redaction,
command classification, permission allow/ask/deny/timeout, filesystem boundaries, tool schema validation,
registry timeouts/crash containment, file/shell/process tools, router fallback/cooldown/rate-limit/capabilities/
override/no-splice, agent planning/verification/unverified/recovery/repeated-call guard/text tool calls/cancel/
ask-user/resume-after-restart/no-model, memory CRUD/dedupe/retrieval/persistence/export/legacy import,
scheduler parsing/next-run/execution/one-shot/catch-up/agent jobs, productivity tools, headset device scoring,
transcript cleanup, TTS streaming & interruption, speaker recognition, voice state machine (wake, segmentation,
barge-in), speaker gate enforcement, conversation routing and chat→agent hand-off, stop, text approvals,
diagnostics, hotkeys, GUI construction / all 11 states / 2,000-event responsiveness, and the e2e startup →
model → memory → tool → shutdown smoke test.

### Manual checks (need your hardware)
1. Connect the YS-511 headset → status line shows `🎧 Headset (YS-511)` (no restart needed).
2. Say "Hey JARVIS, what time is it?" → hologram turns cyan, then answers through the headphones.
3. While JARVIS is talking, start speaking → it stops immediately and listens.
4. Settings → Enrol my voice; then have someone else say "Hey JARVIS" → status shows `⛔ unknown speaker`, no reply.
5. "What's on my screen?" with an error dialog open → it quotes the error.
6. Disconnect the headset mid-session → JARVIS switches mics and shows a notice; reconnect → switches back.

## 8. Known limitations

* 7.6 GB RAM is tight: with Chrome open, Windows reports ~0.5 GB free. Switching between the chat model and the
  vision model takes 10–60 s the first time. A cloud key (Anthropic/OpenAI) makes the agent much smarter and
  faster for complex multi-file coding; the local 4B model handles everyday tasks.
* Windows Smart App Control blocks some native DLLs (SciPy, PyAV, sherpa-onnx, sometimes `tokenizers`).
  JARVIS works around this (ONNX-only wake word/speaker ID, pure-Python tokenizer shim, PyAV stub).
* Edge neural voice needs internet (~1–2.5 s to first audio); offline it falls back to Windows voices.
* Bluetooth headsets switch to hands-free (16 kHz) audio while the microphone is open; that's a Bluetooth limit.
* Wake-word detection uses the generic openWakeWord "hey jarvis" model; enrolment improves who it responds to,
  not how it hears the wake word.
