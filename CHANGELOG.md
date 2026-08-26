# Changelog

All notable changes to this project will be documented in this file.

## [3.0.0] - 2026-08-26

### Added
- `search_song` tool: Search by keyword without triggering playback
- `get_recent_plays` tool: Get actual play events with timestamps (not just rankings)
- `reorder_playlist_tracks` tool: Reorder all tracks in a playlist you own
- Structured logging with configurable LOG_LEVEL
- Error handling with try-except for all tool calls
- CONTRIBUTING.md, CHANGELOG.md, SECURITY.md

### Changed
- Version bumped to 3.0.0
- License changed from CC BY-NC-SA 4.0 to MIT
- `update_playlist_description`: Removed broken fallback path, now uses only the correct `/api/playlist/desc/update` endpoint
- Tool count: 10 -> 13

### Fixed
- Playlist description update now works reliably

## [2.0.0] - 2026-07-09

### Added
- Streamable HTTP transport (replacing stdio)
- `update_playlist_description` tool
- `daily_recommend` tool
- `like_song` tool
- `get_play_history` tool
- SSE endpoint for legacy clients
- Health check endpoint

### Changed
- Migrated from MCP stdio to HTTP server architecture
- Single-file design (331 lines, zero dependencies)

## [1.0.0] - 2026-06-15

### Added
- Initial release
- Basic playlist management (create, add, remove)
- Song search and playback
- Pure Python, zero dependencies
- Cookie-based authentication
