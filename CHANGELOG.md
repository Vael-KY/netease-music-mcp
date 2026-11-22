# Changelog

All notable changes to this project will be documented in this file.

## [3.3.0] - 2026-11-22

### Fixed
- Transport now follows the MCP Streamable HTTP spec: only `POST /mcp` is the endpoint (any path used to be accepted), `initialize` issues an `Mcp-Session-Id`, the protocol version is negotiated, notifications get `202`, JSON-RPC batches work, `GET /mcp` answers `405`, `DELETE /mcp` ends a session, unknown tools are a `-32602` error. The legacy `GET /sse` + `POST /message` pair now actually delivers responses over the stream.
- Security: CORS no longer allows every origin. Browser requests are accepted only from localhost or `MCP_ALLOWED_ORIGINS`, so a web page cannot drive the logged-in account.
- `get_play_history` showed NetEase's ranking score as the play count; it now shows `playCount`.
- `reorder_playlist_tracks` wrote whatever list it got. It now refuses a list with missing, extra or duplicate songs.
- `get_playlist_songs` returned only the first part of big playlists; it now reads the full `trackIds`.
- Error responses close the connection so an unread request body cannot corrupt the next keep-alive request.

### Added
- Optional `MCP_AUTH_TOKEN` (Bearer), `MCP_HOST`, `MCP_ALLOWED_ORIGINS`.
- Tools: `account_status`, `get_listening_profile`, `get_song_comments`, `get_similar_songs`, `get_private_messages`, `send_private_message`.
- Pure-Python eapi encryption (AES-128-ECB), checked byte-for-byte against Node's crypto.
- Optional Listen Together extension (`TOGETHER_ENABLED=true`, see `server/mcp-server/together/README.md`): accepts invitations, follows playback and lyrics, room chat through a small Node process around the official NetEase IM SDK, playback control with confirmation, and a bridge to a Kael-companion relay. With `TOGETHER_COOKIE` it runs as a separate AI account while the normal tools stay on `NETEASE_COOKIE`.
- Test suite (`python -m unittest discover -s tests -t tests`), no real account needed.

### Acknowledgment
- The Listen Together extension's approach (joining flow, room message formats, fetching the chat-room ticket in a throwaway child process) follows [yanceydaisy/ncm-resonance](https://github.com/yanceydaisy/ncm-resonance) (MIT). The code here is a separate Python implementation.
- Thanks to [@Hoshi4qwq](https://github.com/Hoshi4qwq) for the issues reported in PR #2 (transport not following Streamable HTTP, CORS open to every origin, `get_play_history` play count, `reorder_playlist_tracks` validation). They are fixed here; the PR itself was not merged.


## [3.2.1] - 2026-10-12

### Fixed
- `create_playlist` / `update_playlist_description`: requests now fill missing device cookies and send `__csrf` as a cookie as well as `csrf_token`. This addresses incomplete request credentials; recovery from the reported 403 still needs a live authenticated test.
- `get_play_history` returned HTTP 400: the endpoint needs the user id and the encrypted weapi channel.
- `get_recent_plays` ignored `limit` (always returned 300) and showed `?` for every timestamp: `limit` now goes in the encrypted body and is also applied locally, the play-time field is looked up under several names, and the real field names are returned if none match.
- `get_personal_fm` could list the same track twice; results are now de-duplicated.

### Changed
- Write tools (create, add, remove, reorder, description) use weapi first and fall back to the plain route only for gateway-type failures (400/403/404/405).
- Writes do not fall back after transport errors, because their outcome may be unknown. Error logs omit query strings and upstream response bodies to protect credentials.
- Errors keep NetEase's own response body (including HTTP 4xx) and add a `hint` for 403 / 301 / -460.
- The server reads `.env` itself (variables already exported take precedence), which fixes `export $(cat .env | xargs)` dropping `__csrf`.
- New optional settings: `NETEASE_TZ_OFFSET`, `NETEASE_UID`.
- The startup log lists which cookie keys were found (names only) and warns if `MUSIC_U` or `__csrf` is missing.

## [3.2.0] - 2026-10-08

### Fixed
- `create_playlist` returned `403 illegal request` after NetEase tightened the plain `/api/playlist/create` endpoint. It now uses the encrypted weapi channel, with automatic fallback to the legacy endpoint.

### Added
- Zero-dependency weapi encryption: pure-Python AES-128-CBC + RSA (`pow`), no `pycryptodome` required. The project remains a single file using only the Python standard library.

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
