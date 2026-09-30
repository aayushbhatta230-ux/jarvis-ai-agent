# JARVIS Configuration

This directory contains configuration files for JARVIS.

## Settings

The main settings are stored in `settings.json` and managed by `core/settings.py`. They can be overridden by:

1. Environment variables (e.g., `JARVIS_VOICE`, `JARVIS_MIC_SENSITIVITY`)
2. The JSON config file
3. Runtime changes via the web UI

## Available Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `voice` | string | "" | SAPI voice name (empty = default) |
| `mic_sensitivity` | float | 1.0 | Microphone sensitivity (0.4-2.5) |
| `response_verbosity` | string | "concise" | Response length: concise, normal, detailed |
| `visual_intensity` | float | 0.7 | UI visual intensity (0.0-1.0) |
| `theme` | string | "dark" | UI theme: dark, light, system |
| `auto_listen` | boolean | true | Enable continuous listening |
| `barge_in` | boolean | false | Allow interruption while speaking |
| `permission_level` | string | "FULL_CONTROL" | Permission level |
| `pc_speaker_enabled` | boolean | false | Enable PC speaker output |

## Environment Variables

All settings can be overridden via `JARVIS_<SETTING_NAME>` environment variables (uppercase, underscores).

Example:
```bash
set JARVIS_MIC_SENSITIVITY=1.5
set JARVIS_AUTO_LISTEN=true
```