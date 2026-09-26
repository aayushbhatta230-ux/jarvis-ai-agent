# Contributing to JARVIS

Thank you for your interest in contributing to JARVIS! We welcome contributions to make JARVIS even more capable, reliable, and intelligent.

## Getting Started

1. **Fork the repository** on GitHub.
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/your-username/jarvis-companion.git
   cd jarvis-companion
   ```
3. **Set up a virtual environment**:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate  # On Windows
   pip install -r requirements.txt
   ```
4. **Pull Ollama Model**:
   ```bash
   ollama pull llama3.2
   ```

## Development Guidelines

- **Code Style**: Follow PEP 8 and maintain modern type annotations (`from __future__ import annotations`).
- **Safety First**: Destructive system actions must always be gated with safety checks and confirmations in `core/capabilities.py`.
- **Testing**: Run test suites before opening a pull request:
  ```bash
  pytest tests/
  ```
- **Zero Secrets**: Never commit tokens, credentials, or personal configuration files. Ensure `.env` is ignored.

## Pull Request Process

1. Create a feature branch (`git checkout -b feat/amazing-feature`).
2. Commit your changes with meaningful commit messages (`git commit -m 'feat: add support for xyz'`).
3. Push to your branch (`git push origin feat/amazing-feature`).
4. Open a Pull Request on GitHub with a clear description of changes.

## License

By contributing, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
