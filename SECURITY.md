# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 3.1.x   | :white_check_mark: |
| 3.0.x   | :white_check_mark: |
| < 3.0   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in JARVIS, please report it responsibly:

1. **Do not** create a public GitHub issue
2. Email the maintainers directly at: security@jarvis.example.com
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fixes

We will acknowledge receipt within 48 hours and provide a timeline for a fix.

## Security Considerations

- JARVIS runs locally - no data leaves your machine without explicit consent
- Voice processing happens locally (with optional cloud STT fallback)
- Web interface uses SSE with CORS protection
- No arbitrary code execution from web UI
- File operations are scoped to workspace directories
- Microphone access requires explicit user permission

## Dependencies

We regularly scan dependencies for vulnerabilities using:
- `pip-audit` for Python packages
- GitHub Dependabot alerts

Please keep dependencies updated in your fork.