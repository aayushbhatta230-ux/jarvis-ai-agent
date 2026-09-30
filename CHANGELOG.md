# Changelog

All notable changes to JARVIS.

## [Unreleased]

### Voice System
- Rewrote `voice/listener.py` with singleton `_AudioCapture` class for proper capture thread lifecycle
- Added local Whisper fallback when Google STT is unavailable
- Implemented echo guard using spectral fingerprinting to prevent self-hearing
- Improved barge-in detection with adaptive noise floor and refractory window
- Added automatic recovery from device errors with max restart limit

### Conversation Manager
- Fixed race conditions in pause event handling and auto-listen re-arming
- Added `_voice_response` method for single-sentence voice output truncation
- Made `_quick_response` defensive against partial initialization (supports `__new__` testing)
- Improved confirmation flow with proper state transitions
- Fixed echo guard timing and remote turn detection

### Mobile Gateway (index.html)
- Complete redesign with modern, responsive layout
- Added reliable tunnel detection with exponential backoff polling
- Integrated QR code generation for easy mobile access
- Added toast notification system
- Improved accessibility with ARIA labels and semantic HTML

### Main HUD (interface/index.html)
- Complete redesign with clean semantic HTML5
- Added proper ARIA roles and live regions for accessibility
- Improved touch targets (44px minimum)
- Added settings and confirmation modals
- Added typing indicator and streaming message support

### HUD Styles (interface/style.css)
- Complete redesign with CSS custom properties
- Added dark/light theme support with system preference detection
- Implemented fluid typography with clamp()
- Added reduced motion support
- Created comprehensive button, modal, and toast components
- Added landscape orientation optimizations

### HUD App (interface/app.js)
- Complete rewrite with robust SSE connection and exponential backoff reconnection
- Added toast notification system with multiple types
- Implemented proper error boundaries and memory leak prevention
- Added keyboard shortcuts (M for mic, S for settings, Enter to send)
- Improved state management with defensive coding

### Fixed
- Fixed `max_suggestions_per_hour = 0` meaning disabled (was unlimited)
- Fixed `--once` mode hanging by rendering on UI thread with deadline
- Fixed config coercion for all fields from JSON/env
- Fixed PowerShell BOM config file loading
- Fixed `--doctor` reporting "Ready" when model unreachable
- Fixed auto-exec line inversion in doctor output
- Fixed corrupt audit row crash in `--history`
- Fixed `test_action_pipeline` by adding `_voice_response` and defensive `_quick_response`

## [3.0.0] - Previous Release
- Initial release with voice, web interface, and automation capabilities