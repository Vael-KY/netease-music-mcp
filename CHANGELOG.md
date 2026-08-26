# Changelog

All notable changes to this project will be documented in this file.

## [3.1.0] - 2026-08-26

### Added
- `get_song_lyrics` tool: Fetch lyrics and translations for any song
- `get_song_details` tool: Batch get song details (album, duration, release date)
- `get_artist_hot_songs` tool: Get an artist's top 20 most popular songs
- `get_personal_fm` tool: Get personalized FM recommendations
- `get_liked_songs` tool: Get full list of user's liked (red heart) song IDs
- `get_user_level` tool: Get user level, listening days, and total play count
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

## [2.0.0] - 2026-07-15

### Changed
- Migrated from stdio transport to Streamable HTTP transport
- Added SSE (Server-Sent Events) support for real-time communication
- Multi-threaded request handling

## [1.0.0] - 2026-06-01

### Added
- Initial release with 10 tools
- Cookie-based authentication
- Playlist CRUD operations
- Music search and playback control
- Play history retrieval
- Daily recommendations
