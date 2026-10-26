# Contributing to netease-music-mcp

Thank you for your interest in contributing! This project aims to stay lightweight and zero-dependency.

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Copy `.env.example` to `.env` and fill in your NetEase cookie
4. Run the server: `python server/mcp-server/server.py`
5. Check `/health` and run the tests:
   ```bash
   python -m unittest discover -s tests -v
   python tests/live_smoke.py     # anonymous read-only calls to the real API
   ```

## Development Guidelines

- **Single file architecture**: All server logic lives in `server/mcp-server/server.py`. Keep it that way.
- **Zero dependencies**: Only the Python standard library. No pip packages.
- **Keep it simple**: Each tool function should be self-contained and readable.
- **Error handling**: Every tool returns a dict; user errors raise / return `{"error": ...}` instead of crashing. NetEase calls never raise.
- **Credentials**: never log cookies or query strings in full — use `_redact()`.
- **Logging**: use the module `logger`.

## Adding a New Tool

1. Write the implementation function `def my_tool(params): ...`
2. Add a `_tool(...)` entry to the `TOOLS` list (schema, `required`, annotations)
3. Register it in `TOOL_DISPATCH`
4. Add a test in `tests/test_server.py` (with a mock route in `_Upstream`)
5. Update README.md and CHANGELOG.md

The registry is asserted to have 18 entries for v3.2; update that assertion when adding tools.

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes
3. `python -m unittest discover -s tests -v` must pass, and ideally `python tests/live_smoke.py`
4. Submit a PR with a clear description of what changed and why

## Code Style

- Python 3.8+ compatible (no walrus in comprehension-heavy code, no `match`)
- Type annotations optional
- Docstrings for all public functions

## Reporting Issues

- Include your Python version
- Include the error message from logs
- Do NOT include your cookie or any credentials

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
