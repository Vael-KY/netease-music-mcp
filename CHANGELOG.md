# Changelog

All notable changes to this project will be documented in this file.

## [3.2.1] - 2026-10-20

### Fixed
- `play_music` raised an exception whenever the search returned no song (`{"error": "No songs found"}` was missing its closing brace, so the tool function crashed).
- `get_artist_hot_songs` raised `UnboundLocalError` when the encrypted endpoint failed and the legacy fallbacks were used: the error branch referenced `result` before it existed.
- `get_play_history` always reported `plays: 0`: the real field is `playCount`, not `score`.
- `get_liked_songs` returned only raw ids (capped at 200). It now resolves names/artists in batches of 50 and accepts a `limit`.
- `search_song` ignored the encrypted search channel that recently answers `50000005`; it now tries `/search/get/web`, then `/cloudsearch/get/web`, then the legacy `/api/search/get`.
- `get_playlist_songs` only read the first page (limit 1000 requested explicitly now) and now reports when the track list is truncated.
- `reorder_playlist_tracks` could corrupt a playlist with an incomplete id list. The full track set is now validated against the live playlist and the missing/unknown ids are returned.

### Added
- Streamable HTTP transport as specified: `POST /mcp` (single + batch), `GET /mcp` (SSE stream), `DELETE /mcp` (session end), `Mcp-Session-Id` sessions with 24 h TTL, `MCP-Protocol-Version` negotiation, `202 Accepted` for notifications.
- Legacy HTTP+SSE transport (`GET /sse` + `POST /message?sessionId=`) works again.
- stdio transport (`--transport stdio` / `MCP_TRANSPORT=stdio`).
- Tool annotations: Chinese `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`.
- `GET /health` reports `version`, tool count and `logged_in`.
- `tests/test_server.py` (42 offline tests: FIPS-197 AES vectors, weapi, all 18 tools against a mock upstream, JSON-RPC, HTTP transport) and `tests/live_smoke.py` (anonymous read-only checks against the real API).
- `Dockerfile` with a health check.

### Changed
- Default `NETEASE_TZ_OFFSET` is now `8` (Beijing time) instead of UTC.
- `search_song` results include album and duration; `get_recent_plays` and `daily_recommend` include the recommendation reason when NetEase provides one.
- Lyrics are returned without LRC timestamps (and capped at 120 lines by default).
- Errors keep NetEase's own response body (including HTTP 4xx) and add a `hint` for 403 / 301 / -460 / -462.
- Error logs and error strings strip query strings and cookie values.

### Security
- `/mcp` rejects browser requests from origins other than `localhost`/`127.0.0.1` by default (DNS-rebinding protection); `MCP_ALLOW_ORIGINS` configures the allow list.

## [3.2.0] - 2026-10-08

### Fixed
- `create_playlist` returned `403 illegal request` after NetEase tightened the plain `/api/playlist/create` endpoint. It now uses the encrypted weapi channel, with automatic fallback to the legacy endpoint.

### Added
- Zero-dependency weapi encryption: pure-Python AES-128-CBC + RSA (`pow`), no `pycryptodome` required.

## [3.1.0] - 2026-08-26

### Added
- `get_song_lyrics`, `get_song_details`, `get_artist_hot_songs`, `get_personal_fm`, `get_liked_songs` tools
- Independent `NETEASE_CSRF` environment variable support (fallback to cookie extraction)

### Changed
- Tool dispatch moved to a dictionary; unified tool signatures accept a `params` dict.

## [3.0.0] - 2026-08-26

### Added
- `search_song`, `get_recent_plays`, `reorder_playlist_tracks` tools
- Structured logging with `LOG_LEVEL`, try/except error handling for every API call
- CONTRIBUTING.md, SECURITY.md, this CHANGELOG.md

### Changed
- License changed from CC BY-NC-SA 4.0 to MIT

## [2.0.0] - 2026-07-10

### Changed
- Rewrite from Cheiineeey/netease-music-mcp: from 3 to 9 tools in one Python file
- Playlist operations use the real NetEase API instead of a local database
- Removed the Node.js proxy dependency

## [1.0.0] - Cheiineeey/netease-music-mcp

### Credit
- Original project by Elle & Matt: 3 tools, SSE transport, Python + Node.js proxy, local database
