# Contributing to JARVIS

Thank you for your interest in contributing to JARVIS! This document provides guidelines for contributing to the project.

## Code of Conduct

By participating in this project, you agree to abide by our Code of Conduct. Please be respectful and constructive in all interactions.

## How to Contribute

### Reporting Bugs

1. Check if the bug has already been reported in [Issues](https://github.com/aayushbhatta230-ux/jarvis-ai-agent/issues)
2. If not, create a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Python version, etc.)
   - Logs or screenshots if applicable

### Suggesting Features

1. Check existing issues and discussions
2. Create a feature request issue with:
   - Clear description of the feature
   - Use cases and benefits
   - Potential implementation approach

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Run tests: `pytest`
5. Run linter: `ruff check .`
6. Commit with clear messages
7. Push to your fork
8. Create a Pull Request

## Development Setup

See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed setup instructions.

## Code Style

- Python 3.10+ type hints required
- Line length: 100 characters
- Run `ruff check .` before committing
- Run `ruff format .` to auto-format

## Testing

- Write tests for new functionality
- Run `pytest` before submitting
- Aim for >80% coverage on new code

## Documentation

- Update relevant docs for changes
- Add docstrings to public APIs
- Update CHANGELOG.md for notable changes

## Commit Messages

Use conventional commits format:
- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `test:` test additions
- `refactor:` code restructuring
- `perf:` performance improvement
- `chore:` maintenance tasks

Example: `feat: add Whisper STT fallback for offline voice`

## License

By contributing, you agree that your contributions will be licensed under the MIT License.