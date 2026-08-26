# Contributing to netease-music-mcp

Thank you for your interest in contributing! This project aims to stay lightweight and zero-dependency.

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Copy `.env.example` to `.env` and fill in your NetEase cookie
4. Run the server: `python server/mcp-server/server.py`
5. Test with the `/health` endpoint

## Development Guidelines

- **Single file architecture**: All server logic lives in `server.py`. Keep it that way.
- **Zero dependencies**: Only Python standard library. No pip packages.
- **Keep it simple**: Each tool function should be self-contained and readable.
- **Error handling**: All NetEase API calls should handle failures gracefully.
- **Logging**: Use the `LOG` logger for important events and errors.

## Adding a New Tool

1. Write the implementation function
2. Add the tool definition to the `TOOLS` list
3. Add the dispatch case in `handle_jsonrpc`
4. Update CHANGELOG.md

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes
3. Test locally with a real NetEase cookie
4. Submit a PR with a clear description of what changed and why

## Code Style

- Python 3.8+ compatible
- No type annotations required (keeping it lightweight)
- Docstrings for all public functions
- Consistent string formatting (string concatenation, not f-strings, for broader compatibility)

## Reporting Issues

- Include your Python version
- Include the error message from logs
- Do NOT include your cookie or any credentials

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
