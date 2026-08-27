# Changelog

All notable changes to this project will be documented in this file.

## [3.1.0] - 2026-08-26

### Added
- `get_song_lyrics` tool: Fetch lyrics and translations for any song
- `get_song_details` tool: Batch get song details (album, duration, release date)
- `get_artist_hot_songs` tool: Get an artist's top 20 most popular songs
- `get_personal_fm` tool: Get personalized FM recommendations
- `get_liked_songs` tool: Get full list of user's liked (red heart) song IDs
- Independent `NETEASE_CSRF` environment variable support (fallback to cookie extraction)

### Changed
- Refactored tool dispatch from if-elif chain to dictionary pattern
- Unified tool function signatures to accept `params` dict

## [3.0.0] - 2026-08-26

### Added
- `search_song` tool: Search by keyword without triggering playback
- `get_recent_plays` tool: Get actual play events with timestamps (not aggregated rankings)
- `reorder_playlist_tracks` tool: Reorder all tracks in a playlist
- Structured logging with configurable `LOG_LEVEL` environment variable
- try-except error handling for all NetEase API calls
- CONTRIBUTING.md (contribution guidelines)
- SECURITY.md (security policy and vulnerability reporting)
- This CHANGELOG.md

### Fixed
- `update_playlist_description` removed broken fallback endpoint

### Changed
- License changed from CC BY-NC-SA 4.0 to MIT
- Version bump to 3.0.0

### Acknowledgment
- Tool coverage planning for v3.0/v3.1 was inspired in part by community implementations, including [@Rainlxyl/netease-music-mcp-safe](https://github.com/Rainlxyl/netease-music-mcp-safe).

## [2.0.0] - 2026-07-10

### Changed
- Forked from Cheiineeey/netease-music-mcp and rewrote from scratch in one afternoon
- From 3 tools to 9 tools (331 lines, single file)
- Playlist operations from local database to real NetEase Cloud API
- Transport from SSE to Streamable HTTP (compatible with more clients)
- Removed Node.js proxy dependency, pure Python standard library

## [1.0.0] - Cheiineeey/netease-music-mcp

### Credit
- Original project by Elle & Matt
- 3 tools, SSE transport, Python + Node.js proxy, local database
